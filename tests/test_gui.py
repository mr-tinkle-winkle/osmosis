"""Smoke test: every theme builds every page without errors (offscreen)."""
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from fakes import make_rpcs3, make_steam  # noqa: E402
from osmosis import config, db  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("theme", ["ribbon", "general", "boilerworks", "trophy_hall", "shelf", "summit",
                                   "card_catalog", "terminal"])
@pytest.mark.parametrize("dark", [True, False])
def test_theme_pages(app, tmp_path, theme, dark):
    from osmosis.gui import app as A
    cfg = make_rpcs3(tmp_path)
    st = make_steam(tmp_path, with_rpcs3_entry=True)
    con = db.connect(); db.add_session(con, "BLUS30443", 1.7e9, 1.7e9 + 3600, 3_600_000); con.close()
    s = config.Settings(rpcs3_config_dir=str(cfg), rpcs3_binary="/bin/true", theme=theme, dark=dark, sounds=False)
    s.steam.root = str(st)
    config.save(s)
    w = A.Window()
    w.resize(1200, 800)
    w.show()
    for i in range(5):
        w.go(i)
        app.processEvents()
        assert not w.grab().isNull()
    w.open_game("BLUS30443")
    app.processEvents()
    w.grab()
    w.close()


def test_sounds_render(tmp_path):
    from osmosis.gui import sounds
    for name, events in sounds.SETS.items():
        for ev in events:
            p = sounds.sound_file(name, ev)
            assert p and p.stat().st_size > 1000
