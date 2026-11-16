import json

from fakes import make_rpcs3
from osmosis import config, mirror, public
from osmosis.library import load_profile
from osmosis.rpcs3 import Rpcs3

SAVE = "dev_hdd0/home/00000001/savedata/BLUS30443-SAVE01"
TRO = "dev_hdd0/home/00000001/trophy/NPWR00145_00"


def test_mirror_follows_progress_and_flags_regressions(tmp_path):
    cfg = make_rpcs3(tmp_path)
    rp = Rpcs3(cfg)
    s = config.Settings()
    assert mirror.refresh(rp, s) == []                      # first run: just records
    # progress moves the record forward silently
    ps = cfg / "GuiConfigs/persistent_settings.dat"
    ps.write_text(ps.read_text().replace("BLUS30443=221040000", "BLUS30443=231040000"))
    (cfg / SAVE / "SAVE.DAT").write_bytes(b"save-v2")
    assert mirror.refresh(rp, s) == []
    # now RPCS3 "loses" things (reinstall / old copy)
    ps.write_text(ps.read_text().replace("BLUS30443=231040000", "BLUS30443=1000"))
    import shutil
    shutil.rmtree(cfg / TRO)
    shutil.rmtree(cfg / SAVE)
    diffs = mirror.refresh(rp, s, {"BLUS30443": "Demon's Souls"})
    kinds = sorted(d.kind for d in diffs)
    assert kinds == ["playtime", "save", "trophies"]
    safety = mirror.restore(rp, diffs)
    assert "BLUS30443=231040000" in ps.read_text()
    assert "[LastPlayed]" in ps.read_text() and "active_user=00000001" in ps.read_text()
    assert (cfg / SAVE / "SAVE.DAT").read_bytes() == b"save-v2"
    assert (cfg / TRO / "TROPUSR.DAT").is_file()
    assert mirror.refresh(rp, s) == []
    assert safety.is_dir()


def test_mirror_accept_takes_rpcs3s_side(tmp_path):
    cfg = make_rpcs3(tmp_path)
    rp = Rpcs3(cfg)
    s = config.Settings()
    mirror.refresh(rp, s)
    import shutil
    shutil.rmtree(cfg / SAVE)
    diffs = mirror.refresh(rp, s)
    assert [d.kind for d in diffs] == ["save"]
    mirror.accept(rp, diffs)
    assert mirror.refresh(rp, s) == []
    assert not (cfg / SAVE).exists()


def test_public_profile_roundtrip_and_friends(tmp_path):
    cfg = make_rpcs3(tmp_path)
    s = config.Settings(rpcs3_config_dir=str(cfg), profile_name="mrtw", profiles_dir=str(tmp_path / "shared"))
    config.save(s)
    p = load_profile(s)
    d = public.build(p, s)
    assert d["name"] == "mrtw" and d["totals"]["games"] == len(p.entries)
    code = public.encode(d)
    assert code.startswith(public.PREFIX)
    back = public.decode(code)
    assert back["games"] == json.loads(json.dumps(d["games"]))
    # nothing private leaks
    blob = json.dumps(d)
    assert str(cfg) not in blob and "savedata" not in blob
    # profiles folder: mine + a friend's
    mine = public.write_own(s, d)
    assert mine.name == "mrtw.osmosis.json"
    friend = dict(d, name="Apple", id="someone-else")
    (tmp_path / "shared" / "Apple.osmosis.json").write_text(json.dumps(friend))
    fr = public.friends(config.load())
    assert [f["name"] for f in fr] == ["Apple"]
    # renaming yourself doesn't leave the old you behind as a friend
    s2 = config.load()
    s2.profile_name = "mrtw2"
    public.write_own(s2, dict(d, name="mrtw2"))
    assert sorted(f.name for f in (tmp_path / "shared").iterdir()) == ["Apple.osmosis.json", "mrtw2.osmosis.json"]


def test_never_your_own_friend(tmp_path):
    cfg = make_rpcs3(tmp_path)
    s = config.Settings(rpcs3_config_dir=str(cfg), profile_name="mrtw", profiles_dir=str(tmp_path / "shared"))
    config.save(s)
    d = public.build(load_profile(s), s)
    public.write_own(s, d)
    s2 = config.load()
    s2.own_profile_file = ""                  # settings forgot which file is mine
    config.save(s2)
    assert public.friends(config.load()) == []
    s3 = config.load()
    s3.profile_name = "renamed"
    public.write_own(s3, dict(d, name="renamed"))   # old file with my id is cleaned up
    assert [f.name for f in (tmp_path / "shared").iterdir()] == ["renamed.osmosis.json"]
