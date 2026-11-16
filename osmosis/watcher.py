"""Play-session tracking, and the auto-export triggers that hang off it.

RPCS3 adds elapsed time to [Playtime] in persistent_settings.dat while a game runs
(and on stop). Watching which serial's total grows tells us what is being played,
however it was launched (RPCS3's own UI, a Steam shortcut, or osmosis' Play button).
A game launched with `--no-gui "%RPCS3_GAMEID%:SERIAL"` is also identified from the
process command line, so a session is noticed even before the first playtime write.

Only one watcher runs at a time (flock on data_dir/watcher.lock): the background
service if it's enabled, otherwise the open osmosis window.
"""
from __future__ import annotations

import fcntl
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from . import backup, config, db
from .library import load_profile, open_rpcs3

log = logging.getLogger("osmosis.watcher")
POLL = 10.0             # seconds between polls
IDLE_END = 150.0        # no playtime growth for this long (with RPCS3 still open) ends a session
MIN_SESSION_MS = 30_000
_CMD_SERIAL = re.compile(r"%RPCS3_GAMEID%:([A-Z]{4}\d{5})|/([A-Z]{4}\d{5})/")


def rpcs3_processes() -> list[tuple[int, str]]:
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
            if "rpcs3" not in comm.lower():
                continue
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        out.append((int(pid), cmd))
    return out


def serial_from_cmdline(cmd: str) -> str | None:
    m = _CMD_SERIAL.search(cmd)
    return (m.group(1) or m.group(2)) if m else None


@dataclass
class _Live:
    serial: str
    started: float
    base_ms: int            # playtime total when the session started
    last_ms: int
    last_growth: float


class Watcher:
    def __init__(self, settings_loader=config.load, clock=time.time,
                 processes=rpcs3_processes, on_session_end=None):
        self._load_settings = settings_loader
        self._clock = clock
        self._processes = processes
        self._on_end = on_session_end
        self._prev: dict[str, int] | None = None
        self._live: _Live | None = None
        self._lock_fd = None

    # -- single-instance lock -----------------------------------------------
    def acquire(self) -> bool:
        p = config.data_dir() / "watcher.lock"
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._lock_fd = fd
        return True

    def release(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    # -- one poll ----------------------------------------------------------------
    def poll(self) -> None:
        settings = self._load_settings()
        rp = open_rpcs3(settings)
        now = self._clock()
        if not rp:
            return
        times = rp.playtimes()
        procs = self._processes()
        cmd_serial = next((s for s in (serial_from_cmdline(c) for _p, c in procs) if s), None)

        grown = []
        if self._prev is not None:
            for serial, ms in times.items():
                if ms > self._prev.get(serial, 0):
                    grown.append((ms - self._prev.get(serial, 0), serial))
        self._prev = dict(times)
        active = max(grown)[1] if grown else None

        live = self._live
        if live and active and active != live.serial:
            self._finish(now)
            live = None
        if active:
            if not live:
                delta = dict((s, d) for d, s in grown)[active]
                self._live = live = _Live(active, now - delta / 1000.0, times[active] - delta, times[active], now)
            live.last_ms = times[active]
            live.last_growth = now
        elif cmd_serial and not live:
            self._live = _Live(cmd_serial, now, times.get(cmd_serial, 0), times.get(cmd_serial, 0), now)
        elif live:
            still_running = bool(procs)
            if not still_running or now - live.last_growth > IDLE_END:
                self._finish(now if still_running else live.last_growth)

        self._maybe_scheduled_export(settings, now)

    def _finish(self, ended: float) -> None:
        live, self._live = self._live, None
        if not live:
            return
        # Prefer RPCS3's own figure (exact); fall back to wall time for --no-gui launches
        # that never wrote one.
        played = live.last_ms - live.base_ms or int((ended - live.started) * 1000)
        if played < MIN_SESSION_MS:
            return
        con = db.connect()
        try:
            db.add_session(con, live.serial, live.started, max(ended, live.started + played / 1000), played)
        finally:
            con.close()
        log.info("session recorded: %s %.1f min", live.serial, played / 60000)
        settings = self._load_settings()
        try:  # record the new progress in osmosis' own copy (never prompts here)
            from . import mirror
            rp = open_rpcs3(settings)
            if rp and not self._processes():
                mirror.refresh(rp, settings)
        except Exception as e:
            log.warning("mirror refresh failed: %s", e)
        if settings.auto_export.trigger == "session":
            self._run_auto_export(settings)
        if self._on_end:
            self._on_end(live.serial)

    def _maybe_scheduled_export(self, settings: config.Settings, now: float) -> None:
        ae = settings.auto_export
        period = {"daily": 86400, "weekly": 7 * 86400}.get(ae.trigger)
        if period and now - ae.last_run >= period and not self._live:
            self._run_auto_export(settings)

    def _run_auto_export(self, settings: config.Settings) -> None:
        rp = open_rpcs3(settings)
        if not rp or not settings.auto_export.folders:
            return
        try:
            titles = load_profile(settings, with_history=False).titles()
            written = backup.auto_export(rp, settings, titles)
            log.info("auto-export wrote %s", ", ".join(map(str, written)) or "nothing")
        except Exception as e:  # never let a backup failure kill the watcher
            log.warning("auto-export failed: %s", e)
        fresh = config.load()
        fresh.auto_export.last_run = self._clock()
        config.save(fresh)

    @property
    def live_serial(self) -> str | None:
        return self._live.serial if self._live else None

    def run_forever(self) -> None:
        while True:
            try:
                self.poll()
            except Exception as e:
                log.warning("poll failed: %s", e)
            time.sleep(POLL)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    w = Watcher()
    if not w.acquire():
        log.info("another osmosis watcher is already running; exiting")
        return
    w.run_forever()
