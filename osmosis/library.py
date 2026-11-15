"""One call that ties RPCS3's library, its trophy sets and osmosis' play history
together -- what the profile page and the CLI both read."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import config, db
from .rpcs3 import Game, Rpcs3, detect_config_dir, find_rpcs3_binary
from .trophies import POINTS, TrophySet, load_all, match_sets


@dataclass
class GameEntry:
    game: Game
    trophies: TrophySet | None
    sessions: list[db.Session] = field(default_factory=list)

    @property
    def serial(self) -> str:
        return self.game.serial

    @property
    def title(self) -> str:
        return self.game.title

    @property
    def percent(self) -> int:
        return self.trophies.percent if self.trophies else 0

    @property
    def last_activity(self) -> datetime | None:
        cands = [self.game.last_played]
        if self.trophies:
            cands.append(self.trophies.last_unlock.astimezone().replace(tzinfo=None) if self.trophies.last_unlock else None)
        cands = [c for c in cands if c]
        return max(cands) if cands else None

    def timeline(self) -> list[tuple[datetime, str, object]]:
        """Chronological events: ("session", Session) and ("trophy", Trophy)."""
        ev: list[tuple[datetime, str, object]] = []
        for s in self.sessions:
            ev.append((datetime.fromtimestamp(s.started), "session", s))
        if self.trophies:
            for t in self.trophies.trophies:
                if t.unlocked_at:
                    ev.append((t.unlocked_at.astimezone().replace(tzinfo=None), "trophy", t))
        ev.sort(key=lambda x: x[0])
        return ev


@dataclass
class Profile:
    rpcs3: Rpcs3 | None
    entries: list[GameEntry]
    orphan_trophy_sets: list[TrophySet]
    user: str = ""

    @property
    def total_playtime_ms(self) -> int:
        return sum(e.game.playtime_ms for e in self.entries)

    @property
    def trophy_counts(self) -> dict[str, int]:
        out = {g: 0 for g in POINTS}
        for s in self.all_sets:
            for g, n in s.count_by_grade().items():
                out[g] += n
        return out

    @property
    def all_sets(self) -> list[TrophySet]:
        return [e.trophies for e in self.entries if e.trophies] + self.orphan_trophy_sets

    @property
    def trophies_unlocked(self) -> int:
        return sum(s.unlocked for s in self.all_sets)

    @property
    def trophies_total(self) -> int:
        return sum(s.total for s in self.all_sets)

    @property
    def points(self) -> int:
        return sum(POINTS[g] * n for g, n in self.trophy_counts.items())

    def recent(self, n: int = 8) -> list[GameEntry]:
        return sorted((e for e in self.entries if e.last_activity),
                      key=lambda e: e.last_activity, reverse=True)[:n]

    def titles(self) -> dict[str, str]:
        return {e.serial: e.title for e in self.entries}


def open_rpcs3(settings: config.Settings | None = None) -> Rpcs3 | None:
    settings = settings or config.load()
    d = detect_config_dir(settings.rpcs3_config_dir or None)
    return Rpcs3(d) if d else None


def rpcs3_binary(settings: config.Settings) -> str | None:
    return settings.rpcs3_binary or find_rpcs3_binary()


def load_profile(settings: config.Settings | None = None, with_history: bool = True) -> Profile:
    settings = settings or config.load()
    rp = open_rpcs3(settings)
    if not rp:
        return Profile(None, [], [])
    games = rp.games()
    sets = load_all(rp.trophy_dir)
    matched = match_sets(games, sets)
    used = {id(s) for s in matched.values()}
    hist: dict[str, list[db.Session]] = {}
    if with_history:
        try:
            con = db.connect()
            for s in db.sessions(con):
                hist.setdefault(s.serial, []).append(s)
            con.close()
        except Exception:
            pass
    entries = [GameEntry(g, matched.get(g.serial), hist.get(g.serial, [])) for g in games]
    return Profile(rp, entries, [s for s in sets if id(s) not in used], rp.active_user)
