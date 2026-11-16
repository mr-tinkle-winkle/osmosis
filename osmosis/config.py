"""osmosis settings (~/.config/osmosis/config.toml) and data locations."""
from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import tomli_w

PARTS = ("saves", "configs", "trophies", "history")
TRIGGERS = ("never", "session", "daily", "weekly")
THEMES = ("ribbon", "general", "boilerworks", "trophy_hall", "shelf", "summit", "card_catalog", "terminal")


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "osmosis"


def data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "osmosis"


def config_path() -> Path:
    return config_dir() / "config.toml"


@dataclass
class AutoExport:
    trigger: str = "never"                  # never | session | daily | weekly
    parts: list[str] = field(default_factory=lambda: ["saves", "configs", "trophies", "history"])
    folders: list[str] = field(default_factory=list)
    keep: int = 10                          # newest N auto-exports kept per folder; 0 = keep all
    last_run: float = 0.0                   # unix time of the last auto-export (daily/weekly bookkeeping)


@dataclass
class SteamSettings:
    root: str = ""                          # override; empty = autodetect
    close_steam: bool = True                # Steam must be closed to edit shortcuts.vdf
    reopen_steam: bool = True
    remove_missing: bool = True             # drop shortcuts for games no longer in RPCS3
    fetch_art: bool = True
    art_source: str = "game"                # "game" = the game's own art, exactly like RPCS3's Create Steam Shortcut;
                                            # "steamgriddb" = SteamGridDB (falls back to the game's art)
    collection_tag: str = "PS3"


@dataclass
class Settings:
    rpcs3_config_dir: str = ""              # override; empty = autodetect
    rpcs3_binary: str = ""                  # override; empty = autodetect (stable profile path)
    steamgriddb_api_key: str = ""
    theme: str = "ribbon"
    dark: bool = True                       # for themes that have both variants
    sounds: bool = True
    ribbon_month_colors: bool = True
    ribbon_media: bool = True               # game music (SND0) + animated icon (ICON1) when an item is held
    ribbon_trailer_bg: bool = True          # ICON1 plays across the background (True) or inside the icon like the PS3
    ribbon_color: str = "#1d4fa3"           # used when ribbon_month_colors is off
    ribbon_daylight: bool = True            # dim by time of day, like the PS3
    sound_choices: dict = field(default_factory=dict)   # "set:event" -> variant name
    profile_name: str = ""                  # name shown in osmosis; empty = RPCS3's user name
    profiles_dir: str = ""                  # your public profile + friends' (sync it!); empty = data dir
    own_profile_file: str = ""              # which file in profiles_dir is you
    profile_id: str = ""                    # random id in your public profile, so osmosis never lists you as a friend
    mirror_saves: bool = True               # keep osmosis' own copy of saves (see mirror.py)
    own_frame: bool = True                  # rounded window with osmosis' own border (no system title bar)
    frame_border: int = 2                   # px, 0 = none
    frame_radius: int = 16                  # px
    ribbon_opacity: int = 100               # % strength of the Ribbon's waves
    refresher_path: str = ""                # Refresher executable; empty = look on PATH / ~/Applications
    steam: SteamSettings = field(default_factory=SteamSettings)
    auto_export: AutoExport = field(default_factory=AutoExport)


def _merge(cls, raw: dict):
    kwargs = {}
    for f in fields(cls):
        if f.name not in raw:
            continue
        v = raw[f.name]
        if f.name == "steam" and isinstance(v, dict):
            v = _merge(SteamSettings, v)
        elif f.name == "auto_export" and isinstance(v, dict):
            v = _merge(AutoExport, v)
        kwargs[f.name] = v
    return cls(**kwargs)


def load(path: Path | None = None) -> Settings:
    path = path or config_path()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return Settings()
    s = _merge(Settings, raw)
    if s.theme not in THEMES:
        s.theme = "ribbon"
    if s.auto_export.trigger not in TRIGGERS:
        s.auto_export.trigger = "never"
    s.auto_export.parts = [p for p in s.auto_export.parts if p in PARTS]
    return s


def save(s: Settings, path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(tomli_w.dumps(asdict(s)), encoding="utf-8")
    tmp.replace(path)
