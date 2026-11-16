"""osmosis' own record of your progress, which wins over RPCS3's when they disagree.

osmosis keeps a mirror of:
  * playtime and last-played per game (from persistent_settings.dat),
  * every trophy set (the small TROPCONF/TROPUSR files),
  * save folders (optional, `mirror_saves`).

`refresh()` folds RPCS3's data into the mirror whenever RPCS3 has *more* (you played,
unlocked, saved): progress only ever moves forward. When RPCS3 has *less* than the mirror
-- a reset playtime, a trophy set with fewer unlocks or gone, a save folder missing or
older -- that's a `Difference`. The window lists them and asks; `restore()` puts osmosis'
copy back into RPCS3, `accept()` takes RPCS3's version as the new record.

Nothing here touches RPCS3 while it is running.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config
from .rpcs3 import Rpcs3
from .trophies import load_set

PLAYTIME_SLACK_MS = 60_000


def root() -> Path:
    return config.data_dir() / "mirror"


@dataclass
class Difference:
    kind: str          # "playtime" | "trophies" | "save"
    key: str           # serial | "<user>/<npid>" | "<user>/<save dir>"
    title: str
    detail: str


# -- helpers ---------------------------------------------------------------------------------
def _ledger_path() -> Path:
    return root() / "playtime.json"


def _load_ledger() -> dict:
    try:
        return json.loads(_ledger_path().read_text())
    except (OSError, ValueError):
        return {}


def _save_ledger(d: dict):
    p = _ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
    tmp.replace(p)


def _files(d: Path) -> dict[str, tuple[int, float]]:
    out = {}
    if d.is_dir():
        for dirpath, _dirs, files in os.walk(d):
            for f in files:
                p = Path(dirpath) / f
                st = p.stat()
                out[p.relative_to(d).as_posix()] = (st.st_size, st.st_mtime)
    return out


def _same(a: Path, b: Path) -> bool:
    fa, fb = _files(a), _files(b)
    if fa.keys() != fb.keys():
        return False
    for k in fa:
        if fa[k][0] != fb[k][0] or (a / k).read_bytes() != (b / k).read_bytes():
            return False
    return True


def _newest(d: Path) -> float:
    return max((m for _s, m in _files(d).values()), default=0.0)


def _copy_dir(src: Path, dst: Path):
    tmp = dst.with_name(dst.name + ".osmosis-tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src, tmp)
    if dst.exists():
        shutil.rmtree(dst)
    tmp.rename(dst)


def _unlocked(path: Path) -> int:
    s = load_set(path)
    return s.unlocked if s else 0


def _users(rp: Rpcs3) -> list[Path]:
    home = rp.dev_hdd0 / "home"
    return sorted(p for p in home.iterdir() if p.is_dir()) if home.is_dir() else []


# -- refresh / compare ---------------------------------------------------------------------------
def refresh(rp: Rpcs3, settings: config.Settings, titles: dict[str, str] | None = None) -> list[Difference]:
    """Fold forward progress from RPCS3 into the mirror; return what RPCS3 is behind on."""
    titles = titles or {}
    diffs: list[Difference] = []

    # playtime
    ledger = _load_ledger()
    times, last = rp.playtimes(), rp.last_played()
    changed = False
    for serial, ms in times.items():
        rec = ledger.get(serial)
        if rec is None or ms >= rec["ms"]:
            lp = last.get(serial)
            ledger[serial] = {"ms": ms, "last": lp.isoformat() if lp else (rec or {}).get("last")}
            changed = changed or rec is None or rec["ms"] != ms
    for serial, rec in ledger.items():
        have = times.get(serial, 0)
        if rec["ms"] - have > PLAYTIME_SLACK_MS:
            diffs.append(Difference("playtime", serial, titles.get(serial, serial),
                                    f"RPCS3 shows {have / 3.6e6:.1f} h played, osmosis has {rec['ms'] / 3.6e6:.1f} h"))
    if changed:
        _save_ledger(ledger)

    # trophies
    mtro = root() / "trophy"
    for u in _users(rp):
        src_root = u / "trophy"
        if src_root.is_dir():
            for s in sorted(src_root.iterdir()):
                if not s.is_dir():
                    continue
                m = mtro / u.name / s.name
                if not m.is_dir():
                    _copy_dir(s, m)
                    continue
                if _same(s, m):
                    continue
                have, rec = _unlocked(s), _unlocked(m)
                if have >= rec:
                    _copy_dir(s, m)            # progress (or same count, newer files): new record
                else:
                    t = load_set(m)
                    diffs.append(Difference("trophies", f"{u.name}/{s.name}", t.title if t else s.name,
                                            f"RPCS3 has {have} trophies unlocked, osmosis has {rec}"))
        mu = mtro / u.name
        if mu.is_dir():
            for m in sorted(mu.iterdir()):
                if m.is_dir() and not (src_root / m.name).is_dir():
                    t = load_set(m)
                    diffs.append(Difference("trophies", f"{u.name}/{m.name}", t.title if t else m.name,
                                            f"missing from RPCS3 ({t.unlocked if t else '?'} trophies unlocked in osmosis)"))

    # saves
    if settings.mirror_saves:
        msav = root() / "savedata"
        for u in _users(rp):
            src_root = u / "savedata"
            if src_root.is_dir():
                for s in sorted(src_root.iterdir()):
                    if not s.is_dir():
                        continue
                    m = msav / u.name / s.name
                    if not m.is_dir():
                        _copy_dir(s, m)
                        continue
                    if _same(s, m):
                        continue
                    if _newest(s) >= _newest(m):
                        _copy_dir(s, m)
                    else:
                        serial = s.name[:9].upper()
                        diffs.append(Difference("save", f"{u.name}/{s.name}", titles.get(serial, s.name),
                                                f"save {s.name} in RPCS3 is older than osmosis' copy "
                                                f"({datetime.fromtimestamp(_newest(s)):%b %d %H:%M} vs "
                                                f"{datetime.fromtimestamp(_newest(m)):%b %d %H:%M})"))
            mu = msav / u.name
            if mu.is_dir():
                for m in sorted(mu.iterdir()):
                    if m.is_dir() and not (src_root / m.name).is_dir():
                        serial = m.name[:9].upper()
                        diffs.append(Difference("save", f"{u.name}/{m.name}", titles.get(serial, m.name),
                                                f"save {m.name} is missing from RPCS3"))
    return diffs


# -- resolving ----------------------------------------------------------------------------------
def _set_ini_values(path: Path, section: str, values: dict[str, str]):
    """Edit keys of one [section] of a QSettings INI in place, keeping everything else."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    start = next((i for i, l in enumerate(lines) if l.strip() == f"[{section}]"), None)
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"[{section}]")
        lines += [f"{k}={v}" for k, v in values.items()]
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("[")), len(lines))
        done = set()
        for i in range(start + 1, end):
            m = re.match(r"^([^=]+)=", lines[i])
            if m and m.group(1).strip() in values:
                k = m.group(1).strip()
                lines[i] = f"{k}={values[k]}"
                done.add(k)
        add = [f"{k}={v}" for k, v in values.items() if k not in done]
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines[end:end] = add
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def restore(rp: Rpcs3, diffs: list[Difference]) -> Path:
    """Write osmosis' record back into RPCS3. RPCS3's current files are kept in a safety
    folder first. Returns that folder."""
    safety = config.data_dir() / "pre-restore" / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    ledger = _load_ledger()
    pt = {d.key: str(ledger[d.key]["ms"]) for d in diffs if d.kind == "playtime" and d.key in ledger}
    if pt:
        ps = rp.persistent_settings_path
        if ps.is_file():
            safety.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ps, safety / ps.name)
        _set_ini_values(ps, "Playtime", pt)
        lp = {k: ledger[k]["last"] for k in pt if ledger[k].get("last")}
        if lp:
            _set_ini_values(ps, "LastPlayed", lp)
    for d in diffs:
        if d.kind == "playtime":
            continue
        sub = "trophy" if d.kind == "trophies" else "savedata"
        user, name = d.key.split("/", 1)
        src = root() / sub / user / name
        dst = rp.dev_hdd0 / "home" / user / sub / name
        if dst.exists():
            (safety / sub).mkdir(parents=True, exist_ok=True)
            shutil.copytree(dst, safety / sub / name, dirs_exist_ok=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _copy_dir(src, dst)
    return safety


def accept(rp: Rpcs3, diffs: list[Difference]) -> None:
    """Take RPCS3's version as the new record (forget what osmosis had)."""
    ledger = _load_ledger()
    times = rp.playtimes()
    for d in diffs:
        if d.kind == "playtime":
            ledger[d.key] = {"ms": times.get(d.key, 0), "last": ledger.get(d.key, {}).get("last")}
            continue
        sub = "trophy" if d.kind == "trophies" else "savedata"
        user, name = d.key.split("/", 1)
        src = rp.dev_hdd0 / "home" / user / sub / name
        dst = root() / sub / user / name
        if src.is_dir():
            _copy_dir(src, dst)
        elif dst.exists():
            shutil.rmtree(dst)
    _save_ledger(ledger)
