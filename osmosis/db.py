"""Session history (SQLite). RPCS3 only stores a running total per game, so
individual play sessions are recorded by osmosis itself from the moment it starts
watching; trophy history needs no database (TROPUSR.DAT keeps unlock times)."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    serial TEXT NOT NULL,
    started REAL NOT NULL,      -- unix seconds
    ended REAL NOT NULL,
    played_ms INTEGER NOT NULL, -- RPCS3's own playtime delta over the session
    source TEXT NOT NULL DEFAULT 'watch'  -- watch | play-button
);
CREATE INDEX IF NOT EXISTS sessions_serial ON sessions(serial, started);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


@dataclass
class Session:
    id: int
    serial: str
    started: float
    ended: float
    played_ms: int
    source: str


def db_path() -> Path:
    return config.data_dir() / "history.sqlite3"


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.executescript(SCHEMA)
    return con


def add_session(con, serial: str, started: float, ended: float, played_ms: int, source: str = "watch") -> None:
    con.execute(
        "INSERT INTO sessions(serial, started, ended, played_ms, source) VALUES (?,?,?,?,?)",
        (serial, started, ended, played_ms, source),
    )
    con.commit()


def sessions(con, serial: str | None = None) -> list[Session]:
    q = "SELECT id, serial, started, ended, played_ms, source FROM sessions"
    args: tuple = ()
    if serial:
        q += " WHERE serial = ?"
        args = (serial,)
    q += " ORDER BY started"
    return [Session(*r) for r in con.execute(q, args)]


def get_meta(con, k: str, default: str = "") -> str:
    r = con.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
    return r[0] if r else default


def set_meta(con, k: str, v: str) -> None:
    con.execute("INSERT INTO meta(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, v))
    con.commit()


def merge_from(con, other_path: Path) -> int:
    """Import sessions from another history file (a restored backup), skipping
    ones already present. Returns how many were added."""
    other = sqlite3.connect(other_path)
    try:
        rows = other.execute("SELECT serial, started, ended, played_ms, source FROM sessions").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        other.close()
    existing = {(s, round(st, 1)) for s, st in con.execute("SELECT serial, started FROM sessions")}
    added = 0
    for serial, started, ended, played, source in rows:
        if (serial, round(started, 1)) in existing:
            continue
        con.execute("INSERT INTO sessions(serial, started, ended, played_ms, source) VALUES (?,?,?,?,?)",
                    (serial, started, ended, played, source))
        added += 1
    con.commit()
    return added


def now() -> float:
    return time.time()
