"""Locating an RPCS3 install and reading its game library.

Directory facts (from RPCS3's own source, rpcs3qt/ + Emu/):
  * config dir: $XDG_CONFIG_HOME/rpcs3/ (nixpkgs build); newer builds keep their
    yml files in a `config/` subfolder of it, older ones in the root -- both handled.
  * dev_hdd0 is `$(EmulatorDir)dev_hdd0/` unless vfs.yml overrides it; EmulatorDir
    defaults to the config dir.
  * games.yml maps SERIAL -> disc game folder (or .iso) for games booted from outside dev_hdd0.
  * PSN/HDD games live in dev_hdd0/game/<SERIAL>/ (category HG); patches/updates for
    disc games also live there (category GD) and are not games of their own.
  * GuiConfigs/persistent_settings.dat is a QSettings INI file: [Playtime] SERIAL=<ms>,
    [LastPlayed] SERIAL=<ISO date>, [Users] active_user=<00000001>.
"""
from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

from .sfo import SfoError, read_sfo

GAME_CATEGORIES = {"DG", "HG", "AP", "AM", "AV", "AT", "CB", "HM", "AS", "2P", "1P"}
SERIAL_RE = re.compile(r"^[A-Z]{4}\d{5}$")


def xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def candidate_config_dirs() -> list[Path]:
    home = Path.home()
    return [
        xdg_config_home() / "rpcs3",
        home / ".var/app/net.rpcs3.RPCS3/config/rpcs3",  # Flatpak
        home / ".config/rpcs3",
    ]


def detect_config_dir(override: str | Path | None = None) -> Path | None:
    if override:
        p = Path(override).expanduser()
        return p if p.is_dir() else None
    for c in candidate_config_dirs():
        if c.is_dir():
            return c
    return None


def _load_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class Game:
    serial: str
    title: str
    path: Path | None              # folder holding PARAM.SFO's parent (or .iso)
    kind: str                      # "disc" | "hdd" | "iso"
    sfo_dir: Path | None = None    # folder containing PARAM.SFO / ICON0.PNG / PIC1.PNG
    np_comm_id: str | None = None  # e.g. NPWR00145_00 (from TROPDIR)
    playtime_ms: int = 0
    last_played: datetime | None = None
    sfo: dict = field(default_factory=dict)
    # Other folders that may hold the menu files: the disc folder itself, an
    # extracted .iso, data/install folders under dev_hdd0/game named after the serial.
    extra_dirs: list = field(default_factory=list)

    @property
    def icon(self) -> Path | None:
        return self._asset("ICON0.PNG")

    @property
    def background(self) -> Path | None:
        # Same preference order RPCS3 uses for its own Steam banner.
        for n in ("PIC1.PNG", "PIC3.PNG", "PIC0.PNG", "PIC2.PNG"):
            p = self._asset(n)
            if p:
                return p
        return None

    @property
    def music(self) -> Path | None:
        """SND0.AT3 (or a language variant, SND0_xx.AT3): the music the PS3 menu plays
        while the game is highlighted."""
        return self._find_media("SND0", ".AT3")

    @property
    def preview(self) -> Path | None:
        """ICON1.PAM (or ICON1_xx.PAM): the animated icon / mini trailer the PS3 menu shows.
        Not every game has one."""
        return self._find_media("ICON1", ".PAM")

    @property
    def media_dirs(self) -> list[Path]:
        """Every folder searched for SND0/ICON1, best first."""
        out: list[Path] = []
        for d in (self.sfo_dir, self.update_dir, *self.extra_dirs):
            if d and d not in out and d.is_dir():
                out.append(d)
        return out

    def _find_media(self, stem: str, ext: str) -> Path | None:
        try:
            dirs = self.media_dirs
        except OSError:
            return None
        for d in dirs:
            exact = [f for f in d.iterdir() if f.is_file() and f.name.upper() == stem + ext]
            if exact:
                return exact[0]
        for d in dirs:
            for f in sorted(d.iterdir()):
                n = f.name.upper()
                if f.is_file() and n.startswith(stem + "_") and n.endswith(ext):
                    return f
        return None

    @property
    def update_dir(self) -> Path | None:
        """dev_hdd0/game/<serial> for disc games (where their updates live)."""
        return getattr(self, "_update_dir", None)

    def _asset(self, name: str) -> Path | None:
        return _ci_file(self.sfo_dir, name)


class Rpcs3:
    def __init__(self, config_dir: Path):
        self.config_dir = Path(config_dir)

    # -- locations ---------------------------------------------------------
    def yml(self, name: str) -> Path:
        """Newer builds: <config>/config/<name>; older: <config>/<name>."""
        new = self.config_dir / "config" / name
        return new if new.exists() or not (self.config_dir / name).exists() else self.config_dir / name

    @property
    def emulator_dir(self) -> Path:
        vfs = _load_yaml(self.yml("vfs.yml"))
        emu = str(vfs.get("$(EmulatorDir)") or "").strip()
        return Path(emu).expanduser() if emu else self.config_dir

    def _vfs_path(self, key: str, default: str) -> Path:
        vfs = _load_yaml(self.yml("vfs.yml"))
        raw = str(vfs.get(key) or default)
        raw = raw.replace("$(EmulatorDir)", str(self.emulator_dir).rstrip("/") + "/")
        return Path(raw).expanduser()

    @property
    def dev_hdd0(self) -> Path:
        return self._vfs_path("/dev_hdd0/", "$(EmulatorDir)dev_hdd0/")

    @property
    def gui_configs(self) -> Path:
        return self.config_dir / "GuiConfigs"

    @property
    def persistent_settings_path(self) -> Path:
        return self.gui_configs / "persistent_settings.dat"

    def _persistent(self) -> configparser.ConfigParser:
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.optionxform = str  # keep serial case
        try:
            cp.read(self.persistent_settings_path, encoding="utf-8")
        except (configparser.Error, OSError):
            pass
        return cp

    @property
    def active_user(self) -> str:
        cp = self._persistent()
        user = cp.get("Users", "active_user", fallback="").strip()
        if user and (self.dev_hdd0 / "home" / user).is_dir():
            return user
        home = self.dev_hdd0 / "home"
        users = sorted(p.name for p in home.iterdir() if p.is_dir()) if home.is_dir() else []
        return "00000001" if "00000001" in users or not users else users[0]

    @property
    def user_home(self) -> Path:
        return self.dev_hdd0 / "home" / self.active_user

    @property
    def trophy_dir(self) -> Path:
        return self.user_home / "trophy"

    @property
    def savedata_dir(self) -> Path:
        return self.user_home / "savedata"

    # -- playtime ------------------------------------------------------------
    def playtimes(self) -> dict[str, int]:
        cp = self._persistent()
        out: dict[str, int] = {}
        if cp.has_section("Playtime"):
            for k, v in cp.items("Playtime"):
                try:
                    out[k] = int(v)
                except ValueError:
                    pass
        return out

    def last_played(self) -> dict[str, datetime]:
        cp = self._persistent()
        out: dict[str, datetime] = {}
        if cp.has_section("LastPlayed"):
            for k, v in cp.items("LastPlayed"):
                d = parse_last_played(v)
                if d:
                    out[k] = d
        return out

    # -- library -------------------------------------------------------------
    def games(self) -> list[Game]:
        found: dict[str, Game] = {}

        # Disc games / ISOs registered in games.yml.
        for serial, path in _load_yaml(self.yml("games.yml")).items():
            serial = str(serial)
            if not path:
                continue
            p = Path(str(path)).expanduser()
            if p.suffix.lower() == ".iso":
                g = Game(serial, serial, p, "iso", _iso_dir(serial, p))
                _fill_from_sfo(g)
                found[serial] = g
                continue
            sfo_dir = _sfo_dir_for(p)
            g = Game(serial, serial, p, "disc", sfo_dir)
            if p.is_dir() and p != sfo_dir:
                g.extra_dirs.append(p)
            _fill_from_sfo(g)
            found[serial] = g

        # HDD games.
        game_root = self.dev_hdd0 / "game"
        if game_root.is_dir():
            for d in sorted(game_root.iterdir()):
                sfo_path = d / "PARAM.SFO"
                if not d.is_dir() or not sfo_path.is_file():
                    continue
                try:
                    sfo = read_sfo(sfo_path)
                except (SfoError, OSError):
                    continue
                cat = str(sfo.get("CATEGORY", ""))
                serial = str(sfo.get("TITLE_ID") or d.name)
                if cat not in GAME_CATEGORIES:
                    # A disc game's update: borrow nicer data if the game exists.
                    continue
                if serial in found:
                    continue
                g = Game(serial, str(sfo.get("TITLE") or serial), d, "hdd", d, sfo=sfo)
                g.np_comm_id = _np_comm_id(d)
                found[serial] = g

        extra: dict[str, list[Path]] = {}
        if game_root.is_dir():
            for d in sorted(game_root.iterdir()):
                # e.g. BCUS98119_INSTALL / BCUS98119DATA: the game's own HDD folders.
                if d.is_dir() and len(d.name) > 9 and SERIAL_RE.match(d.name[:9]):
                    extra.setdefault(d.name[:9], []).append(d)
        for g in found.values():
            if g.kind in ("disc", "iso"):
                g._update_dir = self.dev_hdd0 / "game" / g.serial
            g.extra_dirs.extend(extra.get(g.serial, []))
        times = self.playtimes()
        last = self.last_played()
        for g in found.values():
            g.playtime_ms = times.get(g.serial, 0)
            g.last_played = last.get(g.serial)
        return sorted(found.values(), key=lambda g: g.title.lower())


def _ci_file(d: Path | None, name: str) -> Path | None:
    """d/name, matching the name in any letter case."""
    if not d:
        return None
    p = d / name
    if p.is_file():
        return p
    try:
        for f in d.iterdir():
            if f.name.upper() == name.upper() and f.is_file():
                return f
    except OSError:
        pass
    return None


def _sfo_dir_for(game_path: Path) -> Path | None:
    cands = [game_path / "PS3_GAME", game_path]
    try:  # some dumps use a lowercase ps3_game / param.sfo
        cands[1:1] = [d for d in game_path.iterdir() if d.is_dir() and d.name.upper() == "PS3_GAME"]
    except OSError:
        pass
    for c in cands:
        try:
            if any(f.name.upper() == "PARAM.SFO" and f.is_file() for f in c.iterdir()):
                return c
        except OSError:
            continue
    return None


def _iso_dir(serial: str, iso: Path) -> Path | None:
    """The image's menu files, copied into osmosis' cache (see iso.py)."""
    from .config import data_dir
    from .iso import IsoError, extract_menu_files
    try:
        return extract_menu_files(iso, data_dir() / "iso" / serial)
    except (IsoError, OSError):
        return None


def _np_comm_id(sfo_dir: Path) -> str | None:
    trop = sfo_dir / "TROPDIR"
    if trop.is_dir():
        for d in sorted(trop.iterdir()):
            if d.is_dir() and d.name.startswith("NP"):
                return d.name
    return None


def _fill_from_sfo(g: Game) -> None:
    if not g.sfo_dir:
        return
    try:
        g.sfo = read_sfo(_ci_file(g.sfo_dir, "PARAM.SFO") or g.sfo_dir / "PARAM.SFO")
    except (SfoError, OSError):
        return
    g.title = str(g.sfo.get("TITLE") or g.serial)
    g.np_comm_id = _np_comm_id(g.sfo_dir)


_OLD_FORMATS = ("%B %d, %Y %H:%M", "%B %d, %Y", "%B %d %Y")


def parse_last_played(v: str) -> datetime | None:
    v = v.strip().strip('"')
    if not v or v == "0":
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        pass
    for f in _OLD_FORMATS:
        try:
            return datetime.strptime(v, f)
        except ValueError:
            continue
    return None


def find_rpcs3_binary() -> str | None:
    """A launch path that survives nixpkgs updates: prefer profile symlinks over
    /nix/store paths, which change on every rebuild (a Steam shortcut pointing into
    the store breaks the next time RPCS3 is updated)."""
    user = os.environ.get("USER", "")
    stable = [
        Path(f"/etc/profiles/per-user/{user}/bin/rpcs3"),
        Path("/run/current-system/sw/bin/rpcs3"),
        Path.home() / ".nix-profile/bin/rpcs3",
        Path("/usr/bin/rpcs3"),
        Path("/usr/local/bin/rpcs3"),
    ]
    for p in stable:
        if p.exists():
            return str(p)
    import shutil
    return shutil.which("rpcs3")
