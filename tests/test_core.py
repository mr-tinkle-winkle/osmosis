import json
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from fakes import PNG, make_rpcs3, make_steam
from osmosis import backup, config, db, steam, vdf
from osmosis.library import load_profile
from osmosis.rpcs3 import Rpcs3
from osmosis.sfo import build_sfo, parse_sfo
from osmosis.trophies import rtc_tick_to_datetime, datetime_to_rtc_tick


def settings_for(cfg: Path, steam_root: Path | None = None) -> config.Settings:
    s = config.Settings(rpcs3_config_dir=str(cfg), rpcs3_binary="/run/current-system/sw/bin/rpcs3")
    if steam_root:
        s.steam.root = str(steam_root)
    return s


# -- parsing ----------------------------------------------------------------------
def test_sfo_roundtrip():
    d = parse_sfo(build_sfo({"TITLE": "Demon's Souls", "TITLE_ID": "BLUS30443", "ATTRIBUTE": 5}))
    assert d == {"TITLE": "Demon's Souls", "TITLE_ID": "BLUS30443", "ATTRIBUTE": 5}


def test_rtc_tick():
    d = datetime.fromisoformat("2026-11-15T19:58:00+00:00")
    assert rtc_tick_to_datetime(datetime_to_rtc_tick(d)) == d


@pytest.mark.parametrize("new_layout", [True, False])
def test_library(tmp_path, new_layout):
    cfg = make_rpcs3(tmp_path, new_layout)
    p = load_profile(settings_for(cfg))
    titles = {e.serial: e for e in p.entries}
    assert set(titles) == {"BLUS30443", "NPUB30001", "BLUS99999"}  # the update package isn't a game
    ds = titles["BLUS30443"]
    assert ds.title == "Demon's Souls" and ds.game.kind == "disc"
    assert ds.game.playtime_ms == 221040000
    assert ds.game.last_played == datetime(2026, 11, 15, 13, 58)
    assert titles["NPUB30001"].game.last_played == datetime(2026, 11, 2)  # old RPCS3 date format
    assert ds.trophies and ds.trophies.unlocked == 2 and ds.trophies.total == 4
    assert ds.percent == int((90 + 30) * 100 / (90 + 30 + 15))
    assert p.trophy_counts == {"platinum": 0, "gold": 1, "silver": 1, "bronze": 0}
    assert p.points == 120
    kinds = [k for _d, k, _o in ds.timeline()]
    assert kinds == ["trophy", "trophy"]
    assert [e.serial for e in p.recent()][0] == "BLUS30443"


# -- backups ------------------------------------------------------------------------
def names(zp):
    with zipfile.ZipFile(zp) as z:
        return set(z.namelist())


def test_export_kinds(tmp_path):
    cfg = make_rpcs3(tmp_path)
    rp = Rpcs3(cfg)
    con = db.connect(); db.add_session(con, "BLUS30443", 1000.0, 4600.0, 3_600_000); con.close()

    data = names(backup.export(rp, "data", tmp_path / "out"))
    assert "dev_hdd0/home/00000001/savedata/BLUS30443-SAVE01/SAVE.DAT" in data
    assert "dev_hdd0/home/00000001/exdata/UP0001-NPUB30001_00-0000000000000001.rap" in data
    assert "config/config/custom_configs/config_BLUS30443.yml" in data
    assert "config/GuiConfigs/persistent_settings.dat" in data
    assert "osmosis/history.sqlite3" in data
    assert not any("trophy" in n for n in data)
    assert not any(n.startswith(("config/dev_", "config/cache", "dev_hdd0/game")) or n.endswith(".log") for n in data)

    tro = names(backup.export(rp, "trophies", tmp_path / "out"))
    assert tro == {"manifest.json", *[f"dev_hdd0/home/00000001/trophy/NPWR00145_00/{f}" for f in ("TROPCONF.SFM", "TROPUSR.DAT", "ICON0.PNG")]}

    alls = names(backup.export(rp, "all", tmp_path / "out"))
    assert data | tro <= alls


def test_export_strips_api_key(tmp_path):
    cfg = make_rpcs3(tmp_path)
    s = config.Settings(steamgriddb_api_key="SECRET"); config.save(s)
    zp = backup.export(Rpcs3(cfg), "data", tmp_path / "out")
    with zipfile.ZipFile(zp) as z:
        assert b"SECRET" not in z.read("osmosis/config.toml")


def test_restore_to_fresh_machine(tmp_path):
    src = make_rpcs3(tmp_path / "a")
    zp = backup.export(Rpcs3(src), "all", tmp_path / "out", titles={"BLUS30443": "Demon's Souls"})
    # "distro swap": brand-new empty RPCS3 config dir
    fresh = tmp_path / "b" / "rpcs3"
    fresh.mkdir(parents=True)
    rp = Rpcs3(fresh)
    items = backup.plan(rp, zp)
    assert not any(i.conflict for i in items)
    res = backup.apply(rp, zp, items, resolve=lambda i: pytest.fail("no prompts expected"))
    assert (fresh / "dev_hdd0/home/00000001/savedata/BLUS30443-SAVE01/SAVE.DAT").read_bytes() == b"save-v1"
    assert (fresh / "dev_hdd0/home/00000001/trophy/NPWR00145_00/TROPUSR.DAT").is_file()
    assert (fresh / "config/custom_configs/config_BLUS30443.yml").is_file()
    # game save + its trophies grouped as one item
    ds = next(i for i in items if i.key == "game:BLUS30443")
    assert len(ds.roots) == 2


def test_restore_conflicts_per_game(tmp_path):
    cfg = make_rpcs3(tmp_path)
    rp = Rpcs3(cfg)
    zp = backup.export(rp, "data", tmp_path / "out", titles={"BLUS30443": "Demon's Souls", "NPUB30001": "Skate 3"})
    # change both games' saves after the backup
    (cfg / "dev_hdd0/home/00000001/savedata/BLUS30443-SAVE01/SAVE.DAT").write_bytes(b"save-v2")
    (cfg / "dev_hdd0/home/00000001/savedata/NPUB30001-AUTOSAVE/DATA").write_bytes(b"skate-v2")
    (cfg / "dev_hdd0/home/00000001/savedata/BLUS30443-SAVE01/EXTRA").write_bytes(b"new")
    items = backup.plan(rp, zp)
    conflicts = [i.title for i in items if i.conflict]
    assert conflicts == ["Demon's Souls", "Skate 3"]
    asked = []

    def resolve(it):
        asked.append(it.title)
        return "replace" if it.title == "Demon's Souls" else "skip"

    res = backup.apply(rp, zp, items, resolve=resolve)
    assert asked == ["Demon's Souls", "Skate 3"]
    ds = cfg / "dev_hdd0/home/00000001/savedata/BLUS30443-SAVE01"
    assert (ds / "SAVE.DAT").read_bytes() == b"save-v1"
    assert not (ds / "EXTRA").exists()  # folder replaced wholesale
    assert (cfg / "dev_hdd0/home/00000001/savedata/NPUB30001-AUTOSAVE/DATA").read_bytes() == b"skate-v2"
    assert res.safety_copy and (res.safety_copy / "dev_hdd0/home/00000001/savedata/BLUS30443-SAVE01/EXTRA").is_file()


def test_restore_replace_all_and_skip_all(tmp_path):
    cfg = make_rpcs3(tmp_path)
    rp = Rpcs3(cfg)
    zp = backup.export(rp, "data", tmp_path / "out")
    for f in ("BLUS30443-SAVE01/SAVE.DAT", "NPUB30001-AUTOSAVE/DATA"):
        (cfg / "dev_hdd0/home/00000001/savedata" / f).write_bytes(b"changed")
    items = backup.plan(rp, zp)
    calls = []
    backup.apply(rp, zp, items, resolve=lambda it: calls.append(it) or "skip_all")
    assert len(calls) == 1  # the first answer covered the rest
    assert (cfg / "dev_hdd0/home/00000001/savedata/NPUB30001-AUTOSAVE/DATA").read_bytes() == b"changed"
    backup.apply(rp, zp, backup.plan(rp, zp), default="replace_all")
    assert (cfg / "dev_hdd0/home/00000001/savedata/NPUB30001-AUTOSAVE/DATA").read_bytes() == b"skate"


def test_history_merges(tmp_path):
    cfg = make_rpcs3(tmp_path)
    rp = Rpcs3(cfg)
    con = db.connect(); db.add_session(con, "BLUS30443", 100.0, 200.0, 60_000); con.close()
    zp = backup.export(rp, "data", tmp_path / "out")
    con = db.connect(); db.add_session(con, "NPUB30001", 300.0, 400.0, 60_000); con.close()
    res = backup.apply(rp, zp, backup.plan(rp, zp))
    con = db.connect()
    assert len(db.sessions(con)) == 2 and res.history_added == 0
    con.close()


def test_restore_keeps_api_key(tmp_path):
    cfg = make_rpcs3(tmp_path)
    rp = Rpcs3(cfg)
    config.save(config.Settings(steamgriddb_api_key="KEY", theme="summit"))
    zp = backup.export(rp, "data", tmp_path / "out")
    config.save(config.Settings(steamgriddb_api_key="KEY", theme="terminal"))
    backup.apply(rp, zp, backup.plan(rp, zp), default="replace_all")
    s = config.load()
    assert s.theme == "summit" and s.steamgriddb_api_key == "KEY"


def test_auto_export_rotation(tmp_path):
    cfg = make_rpcs3(tmp_path)
    s = config.Settings()
    s.auto_export.parts = ["saves"]
    s.auto_export.folders = [str(tmp_path / "drive1"), str(tmp_path / "drive2")]
    s.auto_export.keep = 2
    import time
    for _ in range(3):
        backup.auto_export(Rpcs3(cfg), s)
        time.sleep(1.1)
    for d in ("drive1", "drive2"):
        zs = list((tmp_path / d).glob("osmosis-auto-*.zip"))
        assert len(zs) == 2
        assert all(n.startswith(("dev_hdd0/", "manifest")) for n in names(zs[0]))


# -- steam -------------------------------------------------------------------------
def test_vdf_binary_roundtrip(tmp_path):
    st = make_steam(tmp_path, with_rpcs3_entry=True)
    raw = (st / "userdata/12345678/config/shortcuts.vdf").read_bytes()
    assert vdf.dumps_binary(vdf.loads_binary(raw)) == raw


def test_detect_user(tmp_path):
    st = make_steam(tmp_path)
    assert steam.detect_user(st) == "12345678"


class FakeSGDB:
    def __init__(self):
        self.calls = []

    def find_game(self, title):
        self.calls.append(("find", title)); return 42

    def best(self, gid, slot):
        return None if slot == "logo" else f"https://img/{slot}.png"

    def download(self, url):
        return PNG, ".png"


def test_steam_sync(tmp_path):
    cfg = make_rpcs3(tmp_path)
    st = make_steam(tmp_path, with_rpcs3_entry=True)
    s = settings_for(cfg, st)
    s.steamgriddb_api_key = "x"
    p = load_profile(s)
    lib = steam.SteamLibrary.detect(s)
    games = [e.game for e in p.entries if e.game.kind != "iso"]
    rep = lib.sync(games, "/run/current-system/sw/bin/rpcs3", s, sgdb=FakeSGDB())
    assert rep.adopted == ["Demon's Souls"]          # RPCS3-made shortcut reused, not duplicated
    assert rep.added == ["Skate 3"]
    data = lib.load()["shortcuts"]
    assert len(data) == 3
    other = data["0"]
    assert other["AppName"] == "Some Other Game" and other["LastPlayTime"] == 5  # untouched
    by = lib.entries_by_serial()
    ds = by["BLUS30443"]
    assert ds["Exe"] == '"/run/current-system/sw/bin/rpcs3"'   # stale /nix/store path fixed
    assert int(ds["appid"]) == -2000000000                        # kept, so old art stays linked
    assert set(ds["tags"].values()) == {"PS3", "osmosis"}
    sk = by["NPUB30001"]
    assert int(sk["appid"]) & 0xFFFFFFFF == steam.steam_appid('"/run/current-system/sw/bin/rpcs3"', "Skate 3")
    appid = int(sk["appid"]) & 0xFFFFFFFF
    grid = lib.grid_dir
    for suffix in ("p.png", ".png", "_hero.png", "_icon.png", "_logo.png"):
        assert (grid / f"{appid}{suffix}").is_file(), suffix
    assert rep.art["Skate 3"][-1] == "logo (game art)"   # SGDB had no logo -> PS3 ICON0 fallback
    assert sk["icon"] == str(grid / f"{appid}_icon.png")
    assert rep.backup_path and rep.backup_path.is_file()

    # Second sync: nothing new; a removed game's shortcut + art go away.
    rep2 = lib.sync([g for g in games if g.serial != "NPUB30001"], "/run/current-system/sw/bin/rpcs3", s, sgdb=FakeSGDB())
    assert rep2.removed == ["Skate 3"] and not rep2.added
    assert not (grid / f"{appid}_hero.png").exists()
    assert "Some Other Game" in [e["AppName"] for e in lib.load()["shortcuts"].values()]


def test_steam_sync_dry_run_writes_nothing(tmp_path):
    cfg = make_rpcs3(tmp_path)
    st = make_steam(tmp_path)
    s = settings_for(cfg, st)
    lib = steam.SteamLibrary.detect(s)
    before = lib.shortcuts_path.read_bytes()
    rep = lib.sync([e.game for e in load_profile(s).entries], "/x/rpcs3", s, dry_run=True)
    assert rep.added and lib.shortcuts_path.read_bytes() == before


# -- watcher ---------------------------------------------------------------------------
def test_watcher_sessions(tmp_path, monkeypatch):
    from osmosis import watcher
    cfg = make_rpcs3(tmp_path)
    s = settings_for(cfg)
    pdat = cfg / "GuiConfigs/persistent_settings.dat"
    clock = [10_000.0]
    procs = [[]]
    w = watcher.Watcher(settings_loader=lambda: s, clock=lambda: clock[0], processes=lambda: procs[0])

    def set_ds(ms):
        pdat.write_text(f"[Playtime]\nBLUS30443={ms}\nNPUB30001=43560000\n[Users]\nactive_user=00000001\n")

    base = 221040000
    set_ds(base); w.poll()
    procs[0] = [(1, "rpcs3")]
    for i in range(1, 31):              # 30 polls, 10 s apart, playtime growing
        clock[0] += 10; set_ds(base + i * 10_000); w.poll()
    assert w.live_serial == "BLUS30443"
    procs[0] = []                        # RPCS3 closed
    clock[0] += 10; w.poll()
    assert w.live_serial is None
    con = db.connect(); ss = db.sessions(con); con.close()
    assert len(ss) == 1 and ss[0].serial == "BLUS30443" and ss[0].played_ms == 300_000


def test_watcher_cmdline_launch(tmp_path):
    from osmosis import watcher
    assert watcher.serial_from_cmdline('rpcs3 --no-gui %RPCS3_GAMEID%:BLUS30443') == "BLUS30443"
