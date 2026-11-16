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
