"""Public profiles: what you share with friends, and reading theirs.

A public profile holds a display name, a small picture, totals, and per-game playtime and
trophy progress -- nothing else (no saves, paths, Steam ids or RPCS3 user data).

Two ways to share:
  * "Copy Public Info" puts a text code on the clipboard ("osmosis-profile:1:<data>");
    "View Copied Public Info" reads one back.
  * The profiles folder: osmosis keeps your profile there as <your name>.osmosis.json, and
    every other *.osmosis.json in it is shown as a friend. Sync the folder with friends
    (Syncthing, a shared drive...) and everyone sees everyone.
"""
from __future__ import annotations

import base64
import json
import re
import zlib
from datetime import datetime
from pathlib import Path

from . import config

PREFIX = "osmosis-profile:1:"
SUFFIX = ".osmosis.json"


def profiles_dir(settings: config.Settings) -> Path:
    d = Path(settings.profiles_dir).expanduser() if settings.profiles_dir else config.data_dir() / "profiles"
    return d


def avatar_path() -> Path:
    return config.data_dir() / "profile" / "avatar.png"


def display_name(settings: config.Settings, profile=None) -> str:
    if settings.profile_name.strip():
        return settings.profile_name.strip()
    try:
        n = (profile.rpcs3.user_home / "localusername").read_text(encoding="utf-8").strip()
        if n:
            return n
    except (OSError, AttributeError):
        pass
    return "Player"


def build(profile, settings: config.Settings) -> dict:
    av = avatar_path()
    games = []
    for e in profile.entries:
        tr = e.trophies
        games.append({
            "serial": e.serial,
            "title": e.title,
            "playtime_ms": e.game.playtime_ms,
            "last_played": e.game.last_played.isoformat(timespec="minutes") if e.game.last_played else None,
            "percent": e.percent,
            "unlocked": tr.unlocked if tr else 0,
            "total": tr.total if tr else 0,
            "grades": tr.count_by_grade() if tr else {},
        })
    return {
        "format": "osmosis-profile",
        "version": 1,
        "name": display_name(settings, profile),
        "avatar": base64.b64encode(av.read_bytes()).decode() if av.is_file() else "",
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "totals": {
            "games": len(profile.entries),
            "playtime_ms": profile.total_playtime_ms,
            "unlocked": profile.trophies_unlocked,
            "total": profile.trophies_total,
            "grades": profile.trophy_counts,
            "points": profile.points,
        },
        "games": games,
    }


def encode(d: dict) -> str:
    raw = zlib.compress(json.dumps(d, separators=(",", ":")).encode(), 9)
    return PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith(PREFIX):
        b = text[len(PREFIX):].strip()
        b += "=" * (-len(b) % 4)
        d = json.loads(zlib.decompress(base64.urlsafe_b64decode(b)))
    elif text.startswith("{"):
        d = json.loads(text)
    else:
        raise ValueError("That isn't osmosis public info.")
    if d.get("format") != "osmosis-profile" or not isinstance(d.get("games"), list):
        raise ValueError("That isn't osmosis public info.")
    return d


def safe_name(name: str) -> str:
    s = re.sub(r"[^\w .-]", "_", name).strip(" .") or "Player"
    return s[:60]


def write_own(settings: config.Settings, d: dict) -> Path | None:
    folder = profiles_dir(settings)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (safe_name(d["name"]) + SUFFIX)
        old = settings.own_profile_file
        if old and old != path.name:
            (folder / old).unlink(missing_ok=True)       # renamed: don't leave yourself behind as a "friend"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return None
    if settings.own_profile_file != path.name:
        s = config.load()
        s.own_profile_file = path.name
        config.save(s)
        settings.own_profile_file = path.name
    return path


def save_friend(settings: config.Settings, d: dict) -> Path:
    folder = profiles_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (safe_name(d["name"]) + SUFFIX)
    if path.name == settings.own_profile_file:
        path = folder / (safe_name(d["name"]) + " (friend)" + SUFFIX)
    path.write_text(json.dumps(d, indent=1), encoding="utf-8")
    return path


def friends(settings: config.Settings) -> list[dict]:
    folder = profiles_dir(settings)
    out = []
    if not folder.is_dir():
        return out
    for f in sorted(folder.glob("*" + SUFFIX)):
        if f.name == settings.own_profile_file:
            continue
        try:
            d = decode(f.read_text(encoding="utf-8"))
        except (OSError, ValueError, zlib.error):
            continue
        d["_file"] = str(f)
        out.append(d)
    return sorted(out, key=lambda d: d.get("updated", ""), reverse=True)
