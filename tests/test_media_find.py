"""Finding a game's menu music/video in the folder layouts RPCS3 users actually have."""
import struct
from pathlib import Path

from osmosis.rpcs3 import Rpcs3
from osmosis.sfo import build_sfo
from tests.fakes import PNG, make_rpcs3

SECTOR = 2048


def _dirrec(name: bytes, lba: int, size: int, is_dir: bool) -> bytes:
    n = 33 + len(name)
    n += n % 2
    r = bytearray(n)
    r[0] = n
    struct.pack_into("<I", r, 2, lba); struct.pack_into(">I", r, 6, lba)
    struct.pack_into("<I", r, 10, size); struct.pack_into(">I", r, 14, size)
    r[25] = 2 if is_dir else 0
    r[32] = len(name)
    r[33:33 + len(name)] = name
    return bytes(r)


def build_iso(files: dict[str, bytes]) -> bytes:
    """A tiny ISO 9660 image with PS3_GAME/<files>."""
    root_lba, game_lba, data_lba = 18, 19, 20
    sectors: dict[int, bytes] = {}
    entries, lba = [], data_lba
    for name, data in files.items():
        entries.append(_dirrec(f"{name};1".encode(), lba, len(data), False))
        sectors[lba] = data
        lba += max(1, -(-len(data) // SECTOR))
    game = _dirrec(b"\x00", game_lba, SECTOR, True) + _dirrec(b"\x01", root_lba, SECTOR, True) + b"".join(entries)
    root = (_dirrec(b"\x00", root_lba, SECTOR, True) + _dirrec(b"\x01", root_lba, SECTOR, True)
            + _dirrec(b"PS3_GAME", game_lba, SECTOR, True))
    pvd = bytearray(SECTOR)
    pvd[0] = 1; pvd[1:6] = b"CD001"; pvd[6] = 1
    pvd[156:156 + 34] = _dirrec(b"\x00", root_lba, SECTOR, True)
    img = bytearray(lba * SECTOR)
    img[16 * SECTOR:17 * SECTOR] = pvd
    img[root_lba * SECTOR:root_lba * SECTOR + len(root)] = root
    img[game_lba * SECTOR:game_lba * SECTOR + len(game)] = game
    for at, data in sectors.items():
        img[at * SECTOR:at * SECTOR + len(data)] = data
    return bytes(img)


def test_iso_game_gets_title_art_and_menu_video(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    cfg = make_rpcs3(tmp_path)
    iso = tmp_path / "games" / "inFAMOUS.iso"
    iso.write_bytes(build_iso({
        "PARAM.SFO": build_sfo({"TITLE": "inFAMOUS", "TITLE_ID": "BCUS98119", "CATEGORY": "DG"}),
        "ICON0.PNG": PNG, "PIC1.PNG": PNG, "ICON1.PAM": b"PAMF" + b"v" * 5000, "SND0.AT3": b"RIFF" + b"a" * 300,
    }))
    (cfg / "config" / "games.yml").write_text(f'BCUS98119: "{iso}"\n')
    g = {x.serial: x for x in Rpcs3(cfg).games()}["BCUS98119"]
    assert g.kind == "iso" and g.title == "inFAMOUS"
    assert g.icon and g.background
    assert g.preview and g.preview.read_bytes().startswith(b"PAMF") and len(g.preview.read_bytes()) == 5004
    assert g.music


def test_menu_video_found_in_disc_root_lowercase_and_hdd_data_folder(tmp_path):
    cfg = make_rpcs3(tmp_path)
    disc = tmp_path / "games" / "Odd"
    (disc / "ps3_game").mkdir(parents=True)
    (disc / "ps3_game" / "param.sfo").write_bytes(build_sfo({"TITLE": "Odd Dump", "TITLE_ID": "BCUS90000", "CATEGORY": "DG"}))
    (disc / "ps3_game" / "icon0.png").write_bytes(PNG)
    (disc / "ps3_game" / "icon1.pam").write_bytes(b"PAMF")
    disc2 = tmp_path / "games" / "Other"
    (disc2 / "PS3_GAME").mkdir(parents=True)
    (disc2 / "PS3_GAME" / "PARAM.SFO").write_bytes(build_sfo({"TITLE": "Other", "TITLE_ID": "BCUS90001", "CATEGORY": "DG"}))
    data = cfg / "dev_hdd0" / "game" / "BCUS90001_INSTALL"
    data.mkdir(parents=True)
    (data / "SND0.AT3").write_bytes(b"RIFF")
    (cfg / "config" / "games.yml").write_text(f'BCUS90000: "{disc}/"\nBCUS90001: "{disc2}/"\n')
    games = {x.serial: x for x in Rpcs3(cfg).games()}
    assert games["BCUS90000"].title == "Odd Dump"
    assert games["BCUS90000"].icon and games["BCUS90000"].preview
    assert games["BCUS90001"].music == data / "SND0.AT3"


def test_missing_iso_is_harmless(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    cfg = make_rpcs3(tmp_path)  # games.yml lists /nonexistent/iso.iso
    g = {x.serial: x for x in Rpcs3(cfg).games()}["BLUS99999"]
    assert g.kind == "iso" and g.preview is None and g.icon is None
