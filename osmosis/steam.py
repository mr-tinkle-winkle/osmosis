"""Steam library sync: RPCS3 games as non-Steam shortcuts, with full library art.

* Shortcuts use the exact launch form RPCS3's own "Create Steam Shortcut" writes
  (`--no-gui "%RPCS3_GAMEID%:SERIAL"`), so shortcuts RPCS3 made are recognised and
  adopted instead of duplicated.
* The appid is crc32(quoted_exe + name) | 0x80000000 (Steam's / RPCS3's formula) for new
  entries; existing entries keep theirs, so their art keeps working.
* Art goes in userdata/<id>/config/grid/: <appid>p (cover), <appid> (wide capsule),
  <appid>_hero (the banner above the title on the game page), <appid>_logo, <appid>_icon.
* Steam overwrites shortcuts.vdf on exit, so it must be closed while we write.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import config, vdf
from .rpcs3 import Game
from .steamgriddb import SgdbError, SteamGridDB

STEAMID64_BASE = 76561197960265728
GAMEID_RE = re.compile(r"%RPCS3_GAMEID%:([A-Z]{4}\d{5})")
OSMOSIS_TAG = "osmosis"
ART_SLOTS = {"cover": "p", "wide": "", "hero": "_hero", "logo": "_logo", "icon": "_icon"}


def candidate_roots() -> list[Path]:
    h = Path.home()
    return [h / ".local/share/Steam", h / ".steam/steam", h / ".steam/root",
            h / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]


def detect_root(override: str = "") -> Path | None:
    if override:
        p = Path(override).expanduser()
        return p if (p / "userdata").is_dir() else None
    for c in candidate_roots():
        if (c / "userdata").is_dir():
            return c.resolve()
    return None


def detect_user(root: Path) -> str | None:
    """Account id (userdata folder name) of the most recent Steam login."""
    lu = root / "config" / "loginusers.vdf"
    best = None
    if lu.is_file():
        try:
            users = vdf.loads_text(lu.read_text(encoding="utf-8", errors="replace")).get("users", {})
        except vdf.VdfError:
            users = {}
        ranked = []
        for sid, info in users.items():
            if not sid.isdigit() or not isinstance(info, dict):
                continue
            acct = str(int(sid) - STEAMID64_BASE)
            recent = str(info.get("MostRecent", info.get("mostrecent", "0"))) == "1"
            ts = int(info.get("Timestamp", "0") or 0) if str(info.get("Timestamp", "0")).isdigit() else 0
            ranked.append((recent, ts, acct))
        for _r, _t, acct in sorted(ranked, reverse=True):
            if (root / "userdata" / acct).is_dir():
                best = acct
                break
    if best:
        return best
    ids = [p.name for p in (root / "userdata").iterdir() if p.is_dir() and p.name.isdigit() and p.name != "0"]
    return ids[0] if len(ids) == 1 else None


def steam_appid(exe_quoted: str, name: str) -> int:
    return (zlib.crc32((exe_quoted + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def launch_options(serial: str) -> str:
    return f'--no-gui "%RPCS3_GAMEID%:{serial}"'


def steam_running() -> bool:
    pidfile = Path.home() / ".steam" / "steam.pid"
    try:
        pid = int(pidfile.read_text().strip())
        if Path(f"/proc/{pid}/comm").read_text().strip().startswith("steam"):
            return True
    except (OSError, ValueError):
        pass
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                if Path(f"/proc/{pid}/comm").read_text().strip() in ("steam", "steamwebhelper"):
                    return True
            except OSError:
                continue
    return False


def shutdown_steam(timeout: float = 40.0) -> bool:
    exe = shutil.which("steam")
    if not exe:
        return False
    subprocess.run([exe, "-shutdown"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    end = time.time() + timeout
    while time.time() < end:
        if not steam_running():
            time.sleep(1.0)  # let it finish flushing shortcuts.vdf before we touch it
            return True
        time.sleep(0.5)
    return False


def start_steam() -> None:
    exe = shutil.which("steam")
    if exe:
        subprocess.Popen([exe, "-silent"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


@dataclass
class SyncReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)      # made by RPCS3's own button
    removed: list[str] = field(default_factory=list)
    art: dict[str, list[str]] = field(default_factory=dict)  # title -> slots written
    warnings: list[str] = field(default_factory=list)
    shortcuts_path: Path | None = None
    backup_path: Path | None = None
    dry_run: bool = False


class SteamLibrary:
    def __init__(self, root: Path, user: str):
        self.root = root
        self.user = user

    @classmethod
    def detect(cls, settings: config.Settings) -> "SteamLibrary | None":
        root = detect_root(settings.steam.root)
        if not root:
            return None
        user = detect_user(root)
        return cls(root, user) if user else None

    @property
    def config_dir(self) -> Path:
        return self.root / "userdata" / self.user / "config"

    @property
    def shortcuts_path(self) -> Path:
        return self.config_dir / "shortcuts.vdf"

    @property
    def grid_dir(self) -> Path:
        return self.config_dir / "grid"

    def load(self) -> dict:
        try:
            data = vdf.loads_binary(self.shortcuts_path.read_bytes())
        except FileNotFoundError:
            data = {}
        data.setdefault("shortcuts", {})
        return data

    def entries_by_serial(self, data: dict | None = None) -> dict[str, dict]:
        data = data or self.load()
        out = {}
        for e in data["shortcuts"].values():
            m = GAMEID_RE.search(str(e.get("LaunchOptions", "")))
            if m:
                out.setdefault(m.group(1), e)
        return out

    def art_path(self, appid: int, slot: str) -> Path | None:
        stem = f"{appid & 0xFFFFFFFF}{ART_SLOTS[slot]}"
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            p = self.grid_dir / f"{stem}{ext}"
            if p.is_file():
                return p
        return None

    def _write_art(self, appid: int, slot: str, data: bytes, ext: str) -> Path:
        self.grid_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{appid & 0xFFFFFFFF}{ART_SLOTS[slot]}"
        for old in self.grid_dir.glob(f"{stem}.*"):
            old.unlink(missing_ok=True)
        p = self.grid_dir / f"{stem}{ext}"
        p.write_bytes(data)
        return p

    # ------------------------------------------------------------------------
    def sync(self, games: list[Game], rpcs3_exe: str, settings: config.Settings,
             dry_run: bool = False, refresh_art: bool = False,
             progress: Callable[[str], None] | None = None,
             sgdb: SteamGridDB | None = None) -> SyncReport:
        rep = SyncReport(shortcuts_path=self.shortcuts_path, dry_run=dry_run)
        say = progress or (lambda _m: None)
        exe_q = f'"{rpcs3_exe}"'
        start_dir = f'"{str(Path(rpcs3_exe).parent)}"'
        tag = settings.steam.collection_tag.strip()

        data = self.load()
        entries = list(data["shortcuts"].values())
        by_serial: dict[str, dict] = {}
        for e in entries:
            m = GAMEID_RE.search(str(e.get("LaunchOptions", "")))
            if m and m.group(1) not in by_serial:
                by_serial[m.group(1)] = e
        wanted = {g.serial: g for g in games if g.kind != "iso" or g.path}
        adopted: set[str] = set()

        for serial, g in wanted.items():
            e = by_serial.get(serial)
            if e is None:
                appid = steam_appid(exe_q, g.title)
                e = {
                    "appid": vdf.Int32(appid - 2**32 if appid >= 2**31 else appid),
                    "AppName": g.title, "Exe": exe_q, "StartDir": start_dir, "icon": "",
                    "ShortcutPath": "", "LaunchOptions": launch_options(serial), "IsHidden": vdf.Int32(0),
                    "AllowDesktopConfig": vdf.Int32(1), "AllowOverlay": vdf.Int32(1), "OpenVR": vdf.Int32(0),
                    "Devkit": vdf.Int32(0), "DevkitGameID": "", "DevkitOverrideAppID": vdf.Int32(0),
                    "LastPlayTime": vdf.Int32(0), "FlatpakAppID": "", "sortas": "", "tags": {},
                }
                entries.append(e)
                by_serial[serial] = e
                rep.added.append(g.title)
            else:
                tags = e.get("tags") if isinstance(e.get("tags"), dict) else {}
                ours = OSMOSIS_TAG in tags.values()
                changed = (e.get("Exe") != exe_q or e.get("AppName") != g.title
                           or e.get("LaunchOptions") != launch_options(serial))
                e["Exe"], e["StartDir"], e["AppName"] = exe_q, start_dir, g.title
                e["LaunchOptions"] = launch_options(serial)
                if not ours:
                    rep.adopted.append(g.title)
                    adopted.add(serial)
                elif changed:
                    rep.updated.append(g.title)
            tags = e.get("tags") if isinstance(e.get("tags"), dict) else {}
            vals = [v for v in tags.values() if isinstance(v, str)]
            for t in (tag, OSMOSIS_TAG):
                if t and t not in vals:
                    vals.append(t)
            e["tags"] = {str(i): v for i, v in enumerate(vals)}

        if settings.steam.remove_missing:
            keep = []
            for e in entries:
                m = GAMEID_RE.search(str(e.get("LaunchOptions", "")))
                tags = e.get("tags") if isinstance(e.get("tags"), dict) else {}
                if m and OSMOSIS_TAG in tags.values() and m.group(1) not in wanted:
                    rep.removed.append(str(e.get("AppName", m.group(1))))
                    if not dry_run:
                        for slot in ART_SLOTS:
                            p = self.art_path(int(e.get("appid", 0)), slot)
                            if p:
                                p.unlink(missing_ok=True)
                    continue
                keep.append(e)
            entries = keep

        data["shortcuts"] = {str(i): e for i, e in enumerate(entries)}

        if not dry_run:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            if self.shortcuts_path.is_file():
                bdir = config.data_dir() / "steam-backups"
                bdir.mkdir(parents=True, exist_ok=True)
                rep.backup_path = bdir / f"shortcuts-{datetime.now():%Y-%m-%d_%H%M%S}.vdf"
                shutil.copy2(self.shortcuts_path, rep.backup_path)
                for old in sorted(bdir.glob("shortcuts-*.vdf"))[:-15]:
                    old.unlink(missing_ok=True)
            tmp = self.shortcuts_path.with_suffix(".vdf.osmosis-tmp")
            tmp.write_bytes(vdf.dumps_binary(data))
            tmp.replace(self.shortcuts_path)

        # Art -------------------------------------------------------------------
        if not dry_run and settings.steam.fetch_art:
            client = sgdb or (SteamGridDB(settings.steamgriddb_api_key) if settings.steamgriddb_api_key else None)
            cache_path = config.data_dir() / "sgdb-cache.json"
            try:
                cache = json.loads(cache_path.read_text())
            except (OSError, ValueError):
                cache = {}
            icon_changed = False
            for serial, g in wanted.items():
                e = by_serial[serial]
                appid = int(e["appid"]) & 0xFFFFFFFF
                # Shortcuts adopted from RPCS3's own button only carry the game's small PS3
                # art; replace it once with proper library art when a key is available.
                upgrade = serial in adopted and client is not None
                missing = [s for s in ART_SLOTS if refresh_art or upgrade or not self.art_path(appid, s)]
                written: list[str] = []
                if missing and client:
                    say(f"Fetching art for {g.title}")
                    try:
                        gid = cache.get(serial)
                        if gid is None:
                            gid = client.find_game(g.title)
                            cache[serial] = gid
                        if gid:
                            for slot in list(missing):
                                url = client.best(gid, slot)
                                if url:
                                    blob, ext = client.download(url)
                                    self._write_art(appid, slot, blob, ext)
                                    written.append(slot)
                                    missing.remove(slot)
                    except SgdbError as err:
                        rep.warnings.append(f"{g.title}: {err}")
                        if "API key" in str(err):
                            client = None
                # Fallback: the game's own PS3 art, like RPCS3's button uses.
                for slot in list(missing):
                    src = g.background if slot in ("hero", "wide") else g.icon
                    if src:
                        self._write_art(appid, slot, src.read_bytes(), ".png")
                        written.append(slot + " (game art)")
                if written:
                    rep.art[g.title] = written
                icon = self.art_path(appid, "icon")
                if icon and e.get("icon") != str(icon):
                    e["icon"] = str(icon)
                    icon_changed = True
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(cache, indent=1))
            except OSError:
                pass
            if icon_changed:
                data["shortcuts"] = {str(i): e for i, e in enumerate(entries)}
                tmp = self.shortcuts_path.with_suffix(".vdf.osmosis-tmp")
                tmp.write_bytes(vdf.dumps_binary(data))
                tmp.replace(self.shortcuts_path)
        return rep


def sync_with_steam_closed(lib: SteamLibrary, games, rpcs3_exe: str, settings: config.Settings,
                           progress=None, refresh_art: bool = False) -> SyncReport:
    """Closes Steam if needed (and allowed), syncs, reopens it if it was running."""
    was_running = steam_running()
    if was_running:
        if not settings.steam.close_steam:
            raise RuntimeError("Steam is running. Close it (or allow osmosis to close it) to sync.")
        if progress:
            progress("Closing Steam…")
        if not shutdown_steam():
            raise RuntimeError("Steam didn't close in time; try again.")
    try:
        return lib.sync(games, rpcs3_exe, settings, progress=progress, refresh_art=refresh_art)
    finally:
        if was_running and settings.steam.reopen_steam:
            if progress:
                progress("Reopening Steam…")
            start_steam()
