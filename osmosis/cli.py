"""osmosis-cli: everything the window does, scriptable."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import backup, config, steam
from .library import load_profile, open_rpcs3, rpcs3_binary


def fmt_hours(ms: int) -> str:
    h = ms / 3_600_000
    return f"{h:.1f} h" if h >= 1 else f"{ms // 60000} min"


def cmd_status(a, s):
    p = load_profile(s)
    if not p.rpcs3:
        print("RPCS3 not found. Set rpcs3_config_dir in", config.config_path())
        return 1
    rp = p.rpcs3
    c = p.trophy_counts
    print(f"RPCS3 config : {rp.config_dir}")
    print(f"dev_hdd0     : {rp.dev_hdd0}  (user {rp.active_user})")
    print(f"rpcs3 binary : {rpcs3_binary(s) or 'not found'}")
    lib = steam.SteamLibrary.detect(s)
    print(f"Steam        : {lib.root if lib else 'not found'}" + (f"  (user {lib.user})" if lib else ""))
    print(f"games        : {len(p.entries)}   played {fmt_hours(p.total_playtime_ms)}")
    print(f"trophies     : {p.trophies_unlocked}/{p.trophies_total}  "
          f"P{c['platinum']} G{c['gold']} S{c['silver']} B{c['bronze']}  ({p.points} pts)")
    return 0


def cmd_games(a, s):
    p = load_profile(s)
    for e in p.entries:
        t = f"{e.trophies.unlocked}/{e.trophies.total} ({e.percent}%)" if e.trophies else "-"
        lp = e.game.last_played.strftime("%Y-%m-%d") if e.game.last_played else "never"
        print(f"{e.serial}  {e.title[:40]:40}  {fmt_hours(e.game.playtime_ms):>9}  {t:>14}  {lp}  [{e.game.kind}]")
    return 0


def cmd_timeline(a, s):
    p = load_profile(s)
    e = next((e for e in p.entries if e.serial == a.serial.upper()), None)
    if not e:
        print("no such game", file=sys.stderr)
        return 1
    for when, kind, obj in e.timeline():
        if kind == "session":
            print(f"{when:%Y-%m-%d %H:%M}  played {fmt_hours(obj.played_ms)}")
        else:
            print(f"{when:%Y-%m-%d %H:%M}  {obj.grade:8} {obj.name}")
    return 0


def cmd_export(a, s):
    rp = open_rpcs3(s)
    if not rp:
        print("RPCS3 not found", file=sys.stderr)
        return 1
    titles = load_profile(s, with_history=False).titles()
    out = backup.export(rp, a.kind, Path(a.out).expanduser() if a.out else None, titles=titles)
    print(out)
    return 0


def cmd_import(a, s):
    rp = open_rpcs3(s)
    if not rp:
        print("RPCS3 not found", file=sys.stderr)
        return 1
    titles = load_profile(s, with_history=False).titles()
    items = backup.plan(rp, Path(a.zip), titles)
    for it in items:
        flag = "CONFLICT" if it.conflict else "ok"
        newer = "" if it.newer_in_backup is None else (" (backup newer)" if it.newer_in_backup else " (yours newer)")
        print(f"{flag:9} {it.title}{newer}  [{len(it.members)} files]")
    if a.dry_run:
        return 0
    default = "replace_all" if a.replace_all else "skip_all" if a.skip_all else None

    def ask(it):
        while True:
            ans = input(f"'{it.title}' differs. [r]eplace / [s]kip / replace [a]ll / skip a[l]l? ").strip().lower()
            m = {"r": "replace", "s": "skip", "a": "replace_all", "l": "skip_all"}.get(ans)
            if m:
                return m

    res = backup.apply(rp, Path(a.zip), items, resolve=ask, default=default)
    print(f"restored: {', '.join(res.restored) or '-'}")
    print(f"skipped : {', '.join(res.skipped) or '-'}")
    if res.safety_copy:
        print(f"previous files kept in {res.safety_copy}")
    return 0


def cmd_steam_sync(a, s):
    lib = steam.SteamLibrary.detect(s)
    if not lib:
        print("Steam library not found", file=sys.stderr)
        return 1
    p = load_profile(s, with_history=False)
    exe = rpcs3_binary(s)
    if not exe:
        print("rpcs3 binary not found", file=sys.stderr)
        return 1
    games = [e.game for e in p.entries]
    if a.dry_run:
        rep = lib.sync(games, exe, s, dry_run=True)
    else:
        if a.no_close:
            s.steam.close_steam = False
        rep = steam.sync_with_steam_closed(lib, games, exe, s, progress=print, refresh_art=a.refresh_art)
    for label, xs in (("added", rep.added), ("adopted", rep.adopted), ("updated", rep.updated), ("removed", rep.removed)):
        if xs:
            print(f"{label:8}: {', '.join(xs)}")
    for t, slots in rep.art.items():
        print(f"art     : {t}: {', '.join(slots)}")
    for w in rep.warnings:
        print("warning :", w)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="osmosis-cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("games")
    t = sub.add_parser("timeline"); t.add_argument("serial")
    e = sub.add_parser("export"); e.add_argument("kind", choices=["all", "data", "trophies"]); e.add_argument("--out")
    i = sub.add_parser("import"); i.add_argument("zip")
    g = i.add_mutually_exclusive_group()
    g.add_argument("--replace-all", action="store_true"); g.add_argument("--skip-all", action="store_true")
    i.add_argument("--dry-run", action="store_true")
    ss = sub.add_parser("steam-sync")
    ss.add_argument("--dry-run", action="store_true"); ss.add_argument("--refresh-art", action="store_true")
    ss.add_argument("--no-close", action="store_true", help="fail instead of closing Steam")
    sub.add_parser("watch", help="run the play-session watcher in the foreground")
    a = ap.parse_args(argv)
    s = config.load()
    if a.cmd == "watch":
        from .watcher import main as watch_main
        watch_main()
        return 0
    return {"status": cmd_status, "games": cmd_games, "timeline": cmd_timeline, "export": cmd_export,
            "import": cmd_import, "steam-sync": cmd_steam_sync}[a.cmd](a, s)


if __name__ == "__main__":
    sys.exit(main())
