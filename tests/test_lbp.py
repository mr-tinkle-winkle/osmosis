from pathlib import Path

import yaml

from fakes import make_rpcs3
from osmosis import lbp
from osmosis.rpcs3 import Rpcs3
from osmosis.sfo import build_sfo


def add_lbp(cfg: Path, patched=True, patchwork=True):
    g = cfg / "dev_hdd0/game/BCUS98245"
    (g / "USRDIR").mkdir(parents=True)
    (g / "PARAM.SFO").write_bytes(build_sfo({"TITLE": "LittleBigPlanet™ 3", "TITLE_ID": "BCUS98245", "CATEGORY": "HG"}))
    (g / "USRDIR/EBOOT.BIN").write_bytes(b"patched")
    if patched:
        (g / "USRDIR/EBOOT.BIN.ORIG").write_bytes(b"orig")
    if patchwork:
        (cfg / "dev_hdd0/plugins").mkdir(parents=True)
        (cfg / "dev_hdd0/plugins/patchwork.sprx").write_bytes(b"sprx")


def test_detect_and_switch(tmp_path):
    cfg = make_rpcs3(tmp_path)
    add_lbp(cfg)
    rp = Rpcs3(cfg)
    gs = lbp.games(rp, rp.games())
    assert [g.serial for g in gs] == ["BCUS98245"] and gs[0].patched
    assert lbp.current_server(rp) == (None, None)
    url = lbp.set_server(rp, "bonsai", password="secret",
                         discovered={"url": "https://lbp.lbpbonsai.com/lbp", "usesCustomDigestKey": True})
    assert url.endswith("/lbp")
    assert lbp.current_server(rp)[0] == "bonsai"
    assert (cfg / "dev_hdd0/plugins/patchwork/patchwork_digest.txt").read_text() == "CustomServerDigest"
    assert lbp.lobby_password(rp) == "secret"
    lbp.set_server(rp, "beacon", discovered={"url": "http://beacon.lbpunion.com/LITTLEBIGPLANETPS3_XML"})
    assert lbp.current_server(rp)[0] == "beacon"
    assert not (cfg / "dev_hdd0/plugins/patchwork/patchwork_digest.txt").exists()
    assert lbp.lobby_password(rp) == "secret"   # untouched when not given
    lbp.restore_original(gs[0])
    assert (cfg / "dev_hdd0/game/BCUS98245/USRDIR/EBOOT.BIN").read_bytes() == b"orig"


def test_switch_needs_patchwork(tmp_path):
    cfg = make_rpcs3(tmp_path)
    add_lbp(cfg, patchwork=False)
    import pytest
    with pytest.raises(RuntimeError):
        lbp.set_server(Rpcs3(cfg), "beacon", discovered={"url": "x"})


def test_enable_network_keeps_rest(tmp_path):
    cfg = make_rpcs3(tmp_path)
    rp = Rpcs3(cfg)
    p = rp.yml("config.yml")
    p.write_text("Core:\n  PPU Decoder: Recompiler (LLVM)\nNet:\n  Internet enabled: Disconnected\n  DNS address: 8.8.8.8\n"
                 "Misc:\n  Silence All Logs: false\n")
    assert not lbp.net_status(rp).rpcn
    lbp.enable_network(rp)
    d = yaml.safe_load(p.read_text())
    assert d["Net"] == {"Internet enabled": "Connected", "DNS address": "8.8.8.8", "PSN status": "RPCN"}
    assert d["Core"]["PPU Decoder"] == "Recompiler (LLVM)" and d["Misc"]["Silence All Logs"] is False
    assert lbp.net_status(rp).internet and lbp.net_status(rp).rpcn


def test_is_lbp():
    assert lbp.is_lbp("LittleBigPlanet™ 2") and lbp.is_lbp("LittleBigPlanet 3")
    assert not lbp.is_lbp("LittleBigPlanet Karting") and not lbp.is_lbp("Demon's Souls")


def test_install_scan_and_licences(tmp_path):
    from osmosis import install
    cfg = make_rpcs3(tmp_path)
    d = tmp_path / "dl"
    (d / "sub").mkdir(parents=True)
    (d / "game.pkg").write_bytes(b"p")
    (d / "sub" / "UP0001-NPUB30001_00-0000000000000001.rap").write_bytes(b"r" * 16)
    (d / "notes.txt").write_text("x")
    f = install.scan([d, d / "game.pkg"])
    assert [p.name for p in f.pkgs] == ["game.pkg"] and len(f.licences) == 1
    done = install.install_licences(Rpcs3(cfg), f.licences)
    assert done and done[0].parent.name == "exdata"
    assert install.install_licences(Rpcs3(cfg), f.licences) == []   # already there


def test_pamf_and_at3_parsers():
    import struct
    from osmosis.gui.media import at3_loop, pamf_extract_h264
    es = b"\x00\x00\x00\x01\x67" + b"A" * 50 + b"\x00\x00\x00\x01\x65" + b"B" * 70

    def pes(sid, payload):
        hdr = b"\x80\x80\x05" + b"\x21\x00\x01\x00\x01"
        body = hdr + payload
        return b"\x00\x00\x01" + bytes([sid]) + struct.pack(">H", len(body)) + body

    pack = b"\x00\x00\x01\xba" + b"\x44\x00\x04\x00\x04\x01\x01\x89\xc3\xf8"
    stream = pack + pes(0xE0, es[:60]) + pes(0xBD, b"audio") + pack + pes(0xE0, es[60:]) + b"\x00\x00\x01\xb9"
    pamf = b"PAMF0041" + struct.pack(">I", 1) + b"\x00" * (2048 - 12) + stream
    assert pamf_extract_h264(pamf) == es

    fmt = struct.pack("<HHIIHH", 0x270, 2, 44100, 16000, 384, 0)
    smpl = struct.pack("<9I", 0, 0, 0, 60, 0, 0, 0, 1, 0) + struct.pack("<6I", 0, 0, 44100, 441000, 0, 0)
    riff_body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"smpl" + struct.pack("<I", len(smpl)) + smpl
    riff = b"RIFF" + struct.pack("<I", len(riff_body)) + riff_body
    l = at3_loop(riff)
    assert (l.start_ms, l.end_ms) == (1000, 10000)
