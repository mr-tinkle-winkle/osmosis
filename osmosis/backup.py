"""Export / import of RPCS3 user data.

Archive layout (a plain .zip, so it is just as easy to open by hand):
  manifest.json
  dev_hdd0/home/<user>/savedata/<SAVE_DIR>/...     part "saves"
  dev_hdd0/home/<user>/exdata/...                  part "saves" (PSN licences: tiny, needed to play)
  dev_hdd0/savedata/vmc/...                        part "saves" (PS1/PS2 memory cards)
  dev_hdd0/home/<user>/trophy/<NPCOMMID>/...       part "trophies"
  config/<files from the RPCS3 config dir>          part "configs" (settings, per-game configs, input, patches)
  osmosis/history.sqlite3                           part "history" (play sessions recorded by osmosis)

Never included: installed games (dev_hdd0/game, games/), firmware (dev_flash*),
caches, logs.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from . import __version__, config, db
from .rpcs3 import Rpcs3

KINDS = {
    "all": ("saves", "configs", "trophies", "history"),
    "data": ("saves", "configs", "history"),
    "trophies": ("trophies",),
}

# Top-level entries of the RPCS3 config dir that are never part of "configs".
CONFIG_EXCLUDE_TOP = {
    "dev_hdd0", "dev_hdd1", "dev_flash", "dev_flash2", "dev_flash3", "dev_bdvd",
    "dev_usb000", "dev_usb001", "dev_usb002", "dev_usb003", "dev_usb004", "dev_usb005", "dev_usb006", "dev_usb007",
    "cache", "logs", "games", "captures", "screenshots", "shaderlog", "spu_progs", "test",
}
CONFIG_EXCLUDE_GLOBS = ("*.log", "*.log.gz", "*.log.old", "*.tmp", "*.lock", "*.buf", "*.obj")
MAX_CONFIG_FILE = 64 * 1024 * 1024  # anything bigger under the config dir is not a config


class Rpcs3RunningError(RuntimeError):
    pass


def rpcs3_running() -> bool:
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            name = Path(f"/proc/{pid}/comm").read_text().strip()
        except OSError:
            continue
        if name.lower() in ("rpcs3", ".rpcs3-wrapped", "rpcs3-wrapped"):
            return True
    return False


# -- collecting ---------------------------------------------------------------
@dataclass
class Entry:
    arc: str          # path inside the archive
    src: Path         # file on disk


def _walk(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for f in sorted(filenames):
            p = Path(dirpath) / f
            if p.is_file() and not p.is_symlink():
                yield p


def _under(root: Path, prefix: str) -> list[Entry]:
    return [Entry(f"{prefix}/{p.relative_to(root).as_posix()}", p) for p in _walk(root)]


def collect(rp: Rpcs3, parts: Iterable[str]) -> list[Entry]:
    parts = set(parts)
    hdd = rp.dev_hdd0
    out: list[Entry] = []
    home = hdd / "home"
    users = sorted(p for p in home.iterdir() if p.is_dir()) if home.is_dir() else []
    if "saves" in parts:
        for u in users:
            for sub in ("savedata", "exdata"):
                out += _under(u / sub, f"dev_hdd0/home/{u.name}/{sub}")
            lu = u / "localusername"
            if lu.is_file():
                out.append(Entry(f"dev_hdd0/home/{u.name}/localusername", lu))
        out += _under(hdd / "savedata" / "vmc", "dev_hdd0/savedata/vmc")
    if "trophies" in parts:
        for u in users:
            out += _under(u / "trophy", f"dev_hdd0/home/{u.name}/trophy")
    if "configs" in parts:
        root = rp.config_dir
        hdd_res = hdd.resolve()
        for p in _walk(root):
            rel = p.relative_to(root)
            if rel.parts[0] in CONFIG_EXCLUDE_TOP:
                continue
            if any(fnmatch.fnmatch(rel.name, g) for g in CONFIG_EXCLUDE_GLOBS):
                continue
            try:
                if hdd_res in p.resolve().parents or p.stat().st_size > MAX_CONFIG_FILE:
                    continue
            except OSError:
                continue
            out.append(Entry(f"config/{rel.as_posix()}", p))
        osm = config.config_path()
        if osm.is_file():
            out.append(Entry("osmosis/config.toml", osm))
    return out


# -- export ---------------------------------------------------------------------
def default_export_dir() -> Path:
    return config.data_dir() / "exports"


def _snapshot_history(tmpdir: Path) -> Path | None:
    src = db.db_path()
    if not src.is_file():
        return None
    dst = tmpdir / "history.sqlite3"
    s = sqlite3.connect(src)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        s.close()
        d.close()
    return dst


def _strip_secrets(toml_path: Path, tmpdir: Path) -> Path:
    s = config.load(toml_path)
    s.steamgriddb_api_key = ""
    out = tmpdir / "config.toml"
    config.save(s, out)
    return out


def export(rp: Rpcs3, kind: str = "all", out_dir: Path | None = None, parts: Iterable[str] | None = None,
           name_prefix: str = "osmosis", titles: dict[str, str] | None = None,
           progress: Callable[[int, int], None] | None = None) -> Path:
    """Write a backup zip and return its path. `kind` is all|data|trophies, or pass `parts`."""
    parts = tuple(parts) if parts else KINDS[kind]
    out_dir = Path(out_dir or default_export_dir()).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    label = kind if not parts or tuple(parts) == KINDS.get(kind) else "-".join(parts)
    final = out_dir / f"{name_prefix}-{label}-{stamp}.zip"

    entries = collect(rp, parts)
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        entries = [e if e.arc != "osmosis/config.toml" else Entry(e.arc, _strip_secrets(e.src, tdp)) for e in entries]
        if "history" in parts:
            snap = _snapshot_history(tdp)
            if snap:
                entries.append(Entry("osmosis/history.sqlite3", snap))
        manifest = {
            "format": "osmosis-backup",
            "version": 1,
            "osmosis": __version__,
            "kind": kind,
            "parts": list(parts),
            "created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "host": socket.gethostname(),
            "rpcs3_user": rp.active_user,
            "titles": titles or {},
            "files": len(entries),
        }
        tmp = final.with_suffix(".zip.part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=2))
            for i, e in enumerate(entries):
                z.write(e.src, e.arc)
                if progress:
                    progress(i + 1, len(entries))
        tmp.replace(final)
    return final


def auto_export(rp: Rpcs3, settings: config.Settings, titles: dict[str, str] | None = None) -> list[Path]:
    """Run the user's auto-export rule: one zip, copied to every configured folder,
    keeping the newest `keep` auto-exports in each."""
    ae = settings.auto_export
    folders = [Path(f).expanduser() for f in ae.folders if f.strip()]
    if not folders or not ae.parts:
        return []
    made = export(rp, parts=ae.parts, kind="auto", name_prefix="osmosis-auto", titles=titles)
    written = []
    for f in folders:
        try:
            f.mkdir(parents=True, exist_ok=True)
            dst = f / made.name
            shutil.copy2(made, dst)
            written.append(dst)
            if ae.keep > 0:
                olds = sorted(f.glob("osmosis-auto-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
                for old in olds[ae.keep:]:
                    old.unlink(missing_ok=True)
        except OSError:
            continue
    made.unlink(missing_ok=True)
    return written


# -- import ---------------------------------------------------------------------
@dataclass
class ImportItem:
    key: str                 # "game:BLUS30443" | "configs" | "licences" | "history" | "other"
    title: str
    members: list[str] = field(default_factory=list)   # archive paths
    roots: list[str] = field(default_factory=list)     # folders replaced wholesale (save dirs, trophy sets)
    conflict: bool = False   # something different already exists on disk
    newer_in_backup: bool | None = None
    detail: str = ""


Decision = str  # "replace" | "skip" | "replace_all" | "skip_all"


def read_manifest(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as z:
        try:
            m = json.loads(z.read("manifest.json"))
        except KeyError:
            raise ValueError("not an osmosis backup (no manifest.json)")
    if m.get("format") != "osmosis-backup":
        raise ValueError("not an osmosis backup")
    return m


def _target(rp: Rpcs3, arc: str) -> Path | None:
    p = PurePosixPath(arc)
    if ".." in p.parts or p.is_absolute():
        return None
    if p.parts[0] == "dev_hdd0":
        return rp.dev_hdd0.joinpath(*p.parts[1:])
    if p.parts[0] == "config":
        return rp.config_dir.joinpath(*p.parts[1:])
    if arc == "osmosis/config.toml":
        return config.config_path()
    return None


def _serial_of_save_dir(name: str) -> str:
    return name[:9].upper()


def plan(rp: Rpcs3, zip_path: Path, titles: dict[str, str] | None = None) -> list[ImportItem]:
    m = read_manifest(zip_path)
    titles = {**m.get("titles", {}), **(titles or {})}
    items: dict[str, ImportItem] = {}

    def item(key: str, title: str) -> ImportItem:
        if key not in items:
            items[key] = ImportItem(key, title)
        return items[key]

    with zipfile.ZipFile(zip_path) as z:
        infos = {i.filename: i for i in z.infolist() if not i.is_dir()}
        trophy_titles = _trophy_titles_in_zip(z, infos)
        for name in infos:
            if name == "manifest.json":
                continue
            p = PurePosixPath(name)
            parts = p.parts
            if parts[0] == "dev_hdd0" and len(parts) >= 5 and parts[1] == "home" and parts[3] == "savedata":
                serial = _serial_of_save_dir(parts[4])
                it = item(f"game:{serial}", titles.get(serial, serial))
                root = "/".join(parts[:5])
            elif parts[0] == "dev_hdd0" and len(parts) >= 5 and parts[1] == "home" and parts[3] == "trophy":
                npid = parts[4]
                t = trophy_titles.get(npid, npid)
                serial = next((s for s, tt in titles.items() if _same(tt, t)), None)
                it = item(f"game:{serial}" if serial else f"trophy:{npid}", titles.get(serial or "", t))
                root = "/".join(parts[:5])
            elif parts[0] == "dev_hdd0" and len(parts) >= 4 and parts[1] == "home" and parts[3] == "exdata":
                it, root = item("licences", "PSN licences"), None
            elif parts[0] == "dev_hdd0":
                it, root = item("other", "Memory cards & user info"), None
            elif parts[0] == "config" or name == "osmosis/config.toml":
                it, root = item("configs", "Settings & per-game configs"), None
            elif name == "osmosis/history.sqlite3":
                it, root = item("history", "Play history"), None
            else:
                continue
            it.members.append(name)
            if root and root not in it.roots:
                it.roots.append(root)

        for it in items.values():
            if it.key == "history":
                it.conflict = False  # always merged, never overwritten
                it.detail = "merged with existing history"
                continue
            differs, newer = _compare(rp, z, infos, it)
            it.conflict = differs
            it.newer_in_backup = newer
    order = {"configs": 0, "licences": 1, "other": 2, "history": 9}
    return sorted(items.values(), key=lambda i: (order.get(i.key, 5), i.title.lower()))


def _same(a: str, b: str) -> bool:
    n = lambda s: "".join(c for c in s.lower() if c.isalnum())
    return bool(a) and n(a) == n(b)


def _trophy_titles_in_zip(z: zipfile.ZipFile, infos) -> dict[str, str]:
    from .trophies import parse_tropconf
    out = {}
    for name in infos:
        if name.endswith("/TROPCONF.SFM"):
            try:
                npid, title, _ = parse_tropconf(z.read(name))
                out[PurePosixPath(name).parts[4]] = title
            except Exception:
                pass
    return out


def _compare(rp: Rpcs3, z, infos, it: ImportItem) -> tuple[bool, bool | None]:
    differs = False
    newest_backup = 0.0
    newest_disk = 0.0
    for name in it.members:
        tgt = _target(rp, name)
        if tgt is None:
            continue
        info = infos[name]
        newest_backup = max(newest_backup, time.mktime(info.date_time + (0, 0, -1)))
        if tgt.is_file():
            newest_disk = max(newest_disk, tgt.stat().st_mtime)
            if tgt.stat().st_size != info.file_size or _sha(tgt) != hashlib.sha256(z.read(name)).hexdigest():
                differs = True
    # Files on disk in a replaced root that the backup doesn't have also count.
    for root in it.roots:
        tgt_root = _target(rp, root)
        if tgt_root and tgt_root.is_dir():
            on_disk = {f"{root}/{p.relative_to(tgt_root).as_posix()}" for p in _walk(tgt_root)}
            for p in _walk(tgt_root):
                newest_disk = max(newest_disk, p.stat().st_mtime)
            if on_disk - set(it.members):
                differs = True
    if not differs:
        return False, None
    return True, newest_backup > newest_disk + 2


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class ImportResult:
    restored: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    safety_copy: Path | None = None
    history_added: int = 0


def apply(rp: Rpcs3, zip_path: Path, items: list[ImportItem],
          resolve: Callable[[ImportItem], Decision] | None = None,
          default: Decision | None = None, check_running: bool = True) -> ImportResult:
    """Restore `items` from the archive. For each conflicting item, `resolve` is
    asked (replace / skip / replace_all / skip_all); `default` pre-answers all.
    Anything overwritten is first moved to a timestamped safety folder."""
    if check_running and rpcs3_running():
        raise Rpcs3RunningError("Close RPCS3 before restoring a backup.")
    res = ImportResult()
    sticky: Decision | None = {"replace_all": "replace", "skip_all": "skip"}.get(default or "", default)
    safety = config.data_dir() / "pre-import" / datetime.now().strftime("%Y-%m-%d_%H%M%S")

    with zipfile.ZipFile(zip_path) as z:
        for it in items:
            if it.key == "history":
                with tempfile.TemporaryDirectory() as td:
                    p = Path(td) / "h.sqlite3"
                    p.write_bytes(z.read(it.members[0]))
                    con = db.connect()
                    try:
                        res.history_added = db.merge_from(con, p)
                    finally:
                        con.close()
                res.restored.append(it.title)
                continue
            if not it.conflict:
                if not _anything_missing(rp, it):
                    res.unchanged.append(it.title)
                    continue
                decision = "replace"
            elif sticky:
                decision = sticky
            else:
                d = resolve(it) if resolve else "skip"
                if d in ("replace_all", "skip_all"):
                    sticky = "replace" if d == "replace_all" else "skip"
                    d = sticky
                decision = d
            if decision != "replace":
                res.skipped.append(it.title)
                continue
            # Safety copy, then replace whole roots so stale files don't linger.
            for root in it.roots:
                tgt = _target(rp, root)
                if tgt and tgt.exists():
                    dst = safety / root
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(tgt), str(dst))
                    res.safety_copy = safety
            for name in it.members:
                tgt = _target(rp, name)
                if tgt is None:
                    continue
                if tgt.is_file():
                    dst = safety / name
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(tgt, dst)
                    res.safety_copy = safety
                tgt.parent.mkdir(parents=True, exist_ok=True)
                keep_key = config.load(tgt).steamgriddb_api_key if name == "osmosis/config.toml" and tgt.is_file() else ""
                with z.open(name) as src, open(tgt, "wb") as out:
                    shutil.copyfileobj(src, out)
                if keep_key:  # backups never carry the API key; don't let a restore erase it
                    s = config.load(tgt)
                    s.steamgriddb_api_key = keep_key
                    config.save(s, tgt)
                info = z.getinfo(name)
                ts = time.mktime(info.date_time + (0, 0, -1))
                os.utime(tgt, (ts, ts))
            res.restored.append(it.title)
    return res


def _anything_missing(rp: Rpcs3, it: ImportItem) -> bool:
    for name in it.members:
        tgt = _target(rp, name)
        if tgt is not None and not tgt.is_file():
            return True
    return False
