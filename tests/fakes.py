"""Builders for fake RPCS3 / Steam directory trees laid out like the real ones."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from osmosis import vdf
from osmosis.sfo import build_sfo
from osmosis.trophies import build_tropusr, datetime_to_rtc_tick

def _png() -> bytes:
    import struct, zlib
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"\x00" + b"\x40\x60\xa0" * 4
    rows = raw * 4
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


PNG = _png()


def tropconf(npid: str, title: str, trophies: list[tuple[int, str, str]]) -> bytes:
    rows = "".join(
        f'<trophy id="{i:03d}" hidden="no" ttype="{tt}" pid="-1"><name>{n}</name><detail>do {n}</detail></trophy>'
        for i, tt, n in trophies)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<trophyconf version="1.0" platform="ps3">'
            f"<npcommid>{npid}</npcommid><title-name>{title}</title-name>{rows}</trophyconf>").encode()


def make_rpcs3(root: Path, new_layout: bool = True) -> Path:
    cfg = root / "rpcs3"
    ydir = cfg / "config" if new_layout else cfg
    ydir.mkdir(parents=True)
    hdd = cfg / "dev_hdd0"

    # Disc game (games.yml) with TROPDIR
    disc = root / "games" / "Demons Souls"
    (disc / "PS3_GAME" / "TROPDIR" / "NPWR00145_00").mkdir(parents=True)
    (disc / "PS3_GAME" / "PARAM.SFO").write_bytes(build_sfo({"TITLE": "Demon's Souls", "TITLE_ID": "BLUS30443", "CATEGORY": "DG"}))
    (disc / "PS3_GAME" / "ICON0.PNG").write_bytes(PNG)
    (disc / "PS3_GAME" / "PIC1.PNG").write_bytes(PNG)
    (ydir / "games.yml").write_text(f'BLUS30443: "{disc}/"\nBLUS99999: /nonexistent/iso.iso\n')

    # HDD game + an update package (GD) that must not count as a game
    g = hdd / "game" / "NPUB30001"
    g.mkdir(parents=True)
    (g / "PARAM.SFO").write_bytes(build_sfo({"TITLE": "Skate 3", "TITLE_ID": "NPUB30001", "CATEGORY": "HG"}))
    (g / "ICON0.PNG").write_bytes(PNG)
    u = hdd / "game" / "BLUS30443"
    u.mkdir(parents=True)
    (u / "PARAM.SFO").write_bytes(build_sfo({"TITLE": "Demon's Souls", "TITLE_ID": "BLUS30443", "CATEGORY": "GD"}))
    (u / "EBOOT.BIN").write_bytes(b"x" * 1000)

    # Firmware + cache that exports must skip
    (cfg / "dev_flash" / "sys").mkdir(parents=True)
    (cfg / "dev_flash" / "sys" / "big.bin").write_bytes(b"f" * 5000)
    (cfg / "cache").mkdir()
    (cfg / "cache" / "shader.bin").write_bytes(b"c" * 100)
    (cfg / "RPCS3.log").write_text("log")

    user = hdd / "home" / "00000001"
    sd = user / "savedata" / "BLUS30443-SAVE01"
    sd.mkdir(parents=True)
    (sd / "SAVE.DAT").write_bytes(b"save-v1")
    (user / "savedata" / "NPUB30001-AUTOSAVE").mkdir(parents=True)
    (user / "savedata" / "NPUB30001-AUTOSAVE" / "DATA").write_bytes(b"skate")
    (user / "exdata").mkdir(parents=True)
    (user / "exdata" / "UP0001-NPUB30001_00-0000000000000001.rap").write_bytes(b"lic")
    (user / "localusername").write_text("mrtw")

    t1 = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 14, 21, 30, tzinfo=timezone.utc)
    ts = user / "trophy" / "NPWR00145_00"
    ts.mkdir(parents=True)
    (ts / "TROPCONF.SFM").write_bytes(tropconf("NPWR00145_00", "Demon's Souls",
                                               [(0, "P", "Platinum"), (1, "G", "Pure Bladestone"), (2, "S", "Old Monk"), (3, "B", "Storm Ruler")]))
    (ts / "TROPUSR.DAT").write_bytes(build_tropusr([
        (0, 1, False, 0), (1, 2, True, datetime_to_rtc_tick(t2)),
        (2, 3, True, datetime_to_rtc_tick(t1)), (3, 4, False, 0)]))
    (ts / "ICON0.PNG").write_bytes(PNG)

    gui = cfg / "GuiConfigs"
    gui.mkdir()
    (gui / "persistent_settings.dat").write_text(
        "[Playtime]\nBLUS30443=221040000\nNPUB30001=43560000\n\n"
        "[LastPlayed]\nBLUS30443=2026-11-15T13:58:00\nNPUB30001=November 2, 2026\n\n"
        "[Users]\nactive_user=00000001\n")
    (gui / "CurrentSettings.ini").write_text("[Meta]\nx=1\n")
    (ydir / "config.yml").write_text("Core:\n  PPU Decoder: Recompiler (LLVM)\n")
    (ydir / "vfs.yml").write_text('$(EmulatorDir): ""\n/dev_hdd0/: $(EmulatorDir)dev_hdd0/\n')
    (ydir / "custom_configs").mkdir()
    (ydir / "custom_configs" / "config_BLUS30443.yml").write_text("Video: {}\n")
    return cfg


def make_steam(root: Path, account: str = "12345678", with_rpcs3_entry: bool = False, exe: str = '"/old/store/rpcs3"') -> Path:
    s = root / "Steam"
    (s / "config").mkdir(parents=True)
    sid = int(account) + 76561197960265728
    (s / "config" / "loginusers.vdf").write_text(
        f'"users"\n{{\n\t"{sid}"\n\t{{\n\t\t"AccountName"\t\t"mrtw"\n\t\t"MostRecent"\t\t"1"\n\t\t"Timestamp"\t\t"1763000000"\n\t}}\n}}\n')
    cfg = s / "userdata" / account / "config"
    cfg.mkdir(parents=True)
    shortcuts = {"0": {"appid": vdf.Int32(-123456), "AppName": "Some Other Game", "Exe": '"/usr/bin/other"',
                       "StartDir": '"/usr/bin"', "LaunchOptions": "", "tags": {"0": "favorite"},
                       "LastPlayTime": vdf.Int32(5)}}
    if with_rpcs3_entry:
        shortcuts["1"] = {"appid": vdf.Int32(-2000000000), "AppName": "Demon's Souls", "Exe": exe,
                          "StartDir": '"/old/store"', "LaunchOptions": '--no-gui "%RPCS3_GAMEID%:BLUS30443"',
                          "tags": {}}
    (cfg / "shortcuts.vdf").write_bytes(vdf.dumps_binary({"shortcuts": shortcuts}))
    return s
