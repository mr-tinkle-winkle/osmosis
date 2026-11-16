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
    from test_lbp import add_lbp
    add_lbp(cfg)
    st = make_steam(tmp_path, with_rpcs3_entry=True)
    con = db.connect(); db.add_session(con, "BLUS30443", 1.7e9, 1.7e9 + 3600, 3_600_000); con.close()
    s = config.Settings(rpcs3_config_dir=str(cfg), rpcs3_binary="/bin/true", theme=theme, dark=dark, sounds=False)
    s.steam.root = str(st)
    config.save(s)
    w = A.Window()
    w.resize(1200, 800)
    w.show()
    for i in range(7):
        w.go(i)
        app.processEvents()
        assert not w.grab().isNull()
    w.open_game("BLUS30443")
    app.processEvents()
    w.grab()
    from osmosis import public
    d = public.build(w.profile, w.settings)
    w.open_friend(dict(d, name="Apple"))
    app.processEvents()
    w.grab()
    w.close()


def test_sounds_render(tmp_path):
    from osmosis.gui import sounds
    for name, events in sounds.SETS.items():
        for ev in events:
            p = sounds.sound_file(name, ev)
            assert p and p.stat().st_size > 1000


def test_background_results_arrive_on_gui_thread(app):
    """Regression: sync/export callbacks used to run on the worker thread and freeze the window."""
    import time
    from PySide6.QtCore import QObject, QThread
    from osmosis.gui.app import run_bg
    global _OWNER
    owner = _OWNER = QObject()   # kept alive: Qt objects die with their Python owner
    seen = {}

    def work(progress):
        progress("halfway")
        time.sleep(0.05)
        return QThread.currentThread()

    run_bg(owner, work, lambda worker: seen.update(done=QThread.currentThread(), worker=worker),
           on_progress=lambda _m: seen.update(progress=QThread.currentThread()))
    end = time.time() + 5
    while "done" not in seen and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert seen["done"] is app.thread() and seen["progress"] is app.thread()
    assert seen["worker"] is not app.thread()
    while getattr(owner, "_jobs", None):      # let the thread finish before `owner` goes away
        app.processEvents()
        time.sleep(0.01)


def test_xmb_only_moves_on_purpose(app, tmp_path):
    """Regression: the PS3-style list must never move by itself: hovering does nothing and
    smooth-scroll/touchpad deltas add up to whole notches instead of one step each."""
    import time
    from osmosis.gui.backdrop import Backdrop
    from osmosis.gui.themes import resolve
    from osmosis.gui.xmb import XmbList
    from osmosis.library import load_profile
    cfg = make_rpcs3(tmp_path)
    s = config.Settings(rpcs3_config_dir=str(cfg))
    w = XmbList(load_profile(s).entries, resolve("ribbon", True), Backdrop(), s)
    w.resize(1200, 800)
    w.show()
    app.processEvents()
    for _ in range(7):                 # 7 x 15 = 105 < one notch
        w._wheel_step(-15)
    assert w.index == 0
    w._wheel_step(-15)                 # 120: one notch
    assert w.index == 1
    for _ in range(30):                # coasting after the finger lifted
        w._wheel_step(-120, momentum=True)
    assert w.index == 1
    time.sleep(0.1)
    w._wheel_step(120)
    assert w.index == 0
    w.close()
