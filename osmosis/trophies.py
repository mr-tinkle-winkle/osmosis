"""Reading RPCS3's installed trophy sets.

Each set lives in dev_hdd0/home/<user>/trophy/<NPCOMMID>/:
  TROPCONF.SFM  XML: <trophyconf><npcommid/><title-name/><trophy id ttype pid hidden><name/><detail/></trophy>...
  TROPUSR.DAT   big-endian binary, layout from RPCS3's Loader/TROPUSR.h:
      header  (0x30): magic u32 = 0x818F54AD, unk u32, tables_count u32, unk u32, reserved[32]
      tables  (0x20 each): type u32, entries_size u32, unk u32, entries_count u32, offset u64, reserved u64
      type 4 entries (0x60): type, size, entry_id, unk, trophy_id, grade, pid, pad[68]
      type 6 entries (0x70): type, size, entry_id, unk, trophy_id, state, unk, unk, ts1 u64, ts2 u64, pad[64]
  Timestamps are CellRtcTick: microseconds since 0001-01-01 00:00 UTC.
  ICON0.PNG / TROPnnn.PNG  set + per-trophy icons.
"""
from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

TROPUSR_MAGIC = 0x818F54AD
GRADES = {1: "platinum", 2: "gold", 3: "silver", 4: "bronze"}
TTYPE = {"P": "platinum", "G": "gold", "S": "silver", "B": "bronze"}
# Points the PS3 itself awards per grade (used for the profile's level/points line).
POINTS = {"platinum": 180, "gold": 90, "silver": 30, "bronze": 15}
_RTC_EPOCH = datetime(1, 1, 1, tzinfo=timezone.utc)


def rtc_tick_to_datetime(tick: int) -> datetime | None:
    if not tick:
        return None
    try:
        return _RTC_EPOCH + timedelta(microseconds=tick)
    except OverflowError:
        return None


def datetime_to_rtc_tick(d: datetime) -> int:
    delta = d.astimezone(timezone.utc) - _RTC_EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


@dataclass
class Trophy:
    id: int
    name: str
    detail: str
    grade: str
    hidden: bool
    unlocked: bool = False
    unlocked_at: datetime | None = None
    icon: Path | None = None


@dataclass
class TrophySet:
    np_comm_id: str
    title: str
    path: Path
    trophies: list[Trophy] = field(default_factory=list)

    @property
    def icon(self) -> Path | None:
        p = self.path / "ICON0.PNG"
        return p if p.is_file() else None

    @property
    def total(self) -> int:
        return len(self.trophies)

    @property
    def unlocked(self) -> int:
        return sum(t.unlocked for t in self.trophies)

    @property
    def percent(self) -> int:
        """Weighted by points, like the PS3's own progress percentage."""
        total = sum(POINTS.get(t.grade, 0) for t in self.trophies if t.grade != "platinum")
        got = sum(POINTS.get(t.grade, 0) for t in self.trophies if t.grade != "platinum" and t.unlocked)
        if not total:
            return 100 if self.trophies and all(t.unlocked for t in self.trophies) else 0
        return int(got * 100 / total)

    def count_by_grade(self, unlocked_only: bool = True) -> dict[str, int]:
        out = {g: 0 for g in POINTS}
        for t in self.trophies:
            if t.unlocked or not unlocked_only:
                out[t.grade] = out.get(t.grade, 0) + 1
        return out

    @property
    def last_unlock(self) -> datetime | None:
        ds = [t.unlocked_at for t in self.trophies if t.unlocked_at]
        return max(ds) if ds else None


# -- TROPCONF.SFM ------------------------------------------------------------
_XML_START = re.compile(rb"<\?xml|<trophyconf", re.I)


def parse_tropconf(data: bytes) -> tuple[str, str, list[Trophy]]:
    m = _XML_START.search(data)
    if not m:
        raise ValueError("no trophyconf XML found")
    body = data[m.start():]
    end = body.rfind(b"</trophyconf>")
    if end >= 0:
        body = body[: end + len(b"</trophyconf>")]
    root = ET.fromstring(body.decode("utf-8", "replace"))
    if root.tag != "trophyconf":
        found = root.find(".//trophyconf")
        if found is None:
            raise ValueError("root is not trophyconf")
        root = found
    npid = (root.findtext("npcommid") or "").strip()
    title = (root.findtext("title-name") or "").strip()
    trophies = []
    for t in root.findall("trophy"):
        try:
            tid = int(t.get("id", "0"))
        except ValueError:
            continue
        trophies.append(Trophy(
            id=tid,
            name=(t.findtext("name") or "").strip(),
            detail=(t.findtext("detail") or "").strip(),
            grade=TTYPE.get((t.get("ttype") or "B")[:1].upper(), "bronze"),
            hidden=(t.get("hidden") or "no").lower() == "yes",
        ))
    return npid, title, trophies


# -- TROPUSR.DAT -------------------------------------------------------------
def parse_tropusr(data: bytes) -> dict[int, tuple[bool, int]]:
    """trophy_id -> (unlocked, rtc_tick)."""
    if len(data) < 0x30:
        raise ValueError("TROPUSR.DAT too short")
    magic, _u1, tables, _u2 = struct.unpack_from(">IIII", data, 0)
    if magic != TROPUSR_MAGIC:
        raise ValueError("bad TROPUSR.DAT magic")
    out: dict[int, tuple[bool, int]] = {}
    for i in range(tables):
        hoff = 0x30 + i * 0x20
        ttype, _esize, _unk, count, offset = struct.unpack_from(">IIIIQ", data, hoff)
        if ttype != 6:
            continue
        for e in range(count):
            off = offset + e * 0x70
            if off + 0x30 > len(data):
                break
            trophy_id, state = struct.unpack_from(">II", data, off + 0x10)
            ts1 = struct.unpack_from(">Q", data, off + 0x20)[0]
            out[trophy_id] = (bool(state), ts1)
    return out


def build_tropusr(entries: list[tuple[int, int, bool, int]]) -> bytes:
    """(trophy_id, grade, unlocked, tick) -> bytes. Fixture writer for tests."""
    n = len(entries)
    t4_off = 0x30 + 2 * 0x20
    t6_off = t4_off + n * 0x60
    out = struct.pack(">IIII", TROPUSR_MAGIC, 1, 2, 0) + b"\0" * 32
    out += struct.pack(">IIIIQQ", 4, 0x50, 1, n, t4_off, 0)
    out += struct.pack(">IIIIQQ", 6, 0x60, 1, n, t6_off, 0)
    for i, (tid, grade, _u, _t) in enumerate(entries):
        out += struct.pack(">IIIIIII", 4, 0x50, i, 0, tid, grade, 0xFFFFFFFF) + b"\0" * 68
    for i, (tid, _g, unlocked, tick) in enumerate(entries):
        out += struct.pack(">IIIIIIIIQQ", 6, 0x60, i, 0, tid, int(unlocked), 0, 0, tick, tick) + b"\0" * 64
    return out


def load_set(path: Path) -> TrophySet | None:
    conf = path / "TROPCONF.SFM"
    if not conf.is_file():
        return None
    try:
        npid, title, trophies = parse_tropconf(conf.read_bytes())
    except (ValueError, ET.ParseError, OSError):
        return None
    state: dict[int, tuple[bool, int]] = {}
    usr = path / "TROPUSR.DAT"
    if usr.is_file():
        try:
            state = parse_tropusr(usr.read_bytes())
        except (ValueError, struct.error, OSError):
            state = {}
    for t in trophies:
        unlocked, tick = state.get(t.id, (False, 0))
        t.unlocked = unlocked
        t.unlocked_at = rtc_tick_to_datetime(tick) if unlocked else None
        icon = path / f"TROP{t.id:03d}.PNG"
        t.icon = icon if icon.is_file() else None
    return TrophySet(npid or path.name, title or path.name, path, trophies)


def load_all(trophy_dir: Path) -> list[TrophySet]:
    if not trophy_dir.is_dir():
        return []
    sets = []
    for d in sorted(trophy_dir.iterdir()):
        if d.is_dir():
            s = load_set(d)
            if s:
                sets.append(s)
    return sets


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("™", "").replace("®", ""))


def match_sets(games, sets: list[TrophySet]) -> dict[str, TrophySet]:
    """serial -> trophy set. By NP comm id (from the game's TROPDIR) first, then title."""
    by_id = {s.np_comm_id: s for s in sets}
    by_title = {_norm(s.title): s for s in sets}
    out = {}
    for g in games:
        s = by_id.get(g.np_comm_id or "") or by_title.get(_norm(g.title))
        if s:
            out[g.serial] = s
    return out
