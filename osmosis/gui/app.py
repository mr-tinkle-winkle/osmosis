"""The osmosis window."""
from __future__ import annotations

import subprocess
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget,
                               QVBoxLayout, QWidget)

from .. import backup, config, steam
from ..library import Profile, load_profile, open_rpcs3, rpcs3_binary
from ..watcher import Watcher
from . import widgets as W
from .backdrop import Backdrop
from .sounds import PLAYER
from .themes import ORDER, THEMES, Theme, resolve
from .timeline import Timeline, events_from


# -- background work ----------------------------------------------------------------------------
# Results must come back on the GUI thread. A signal connected to a plain Python
# function runs that function in the *emitting* thread, so everything is routed
# through _Relay, a QObject living on the GUI thread, which makes Qt queue the calls.
class _Worker(QThread):
    """Runs fn(progress) on its own thread. The QThread object itself lives on the GUI
    thread (parented to the window), so nothing is ever deleted from the wrong thread."""
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.progress.emit))
        except Exception as e:  # surfaced in the UI, never crashes the window
            traceback.print_exc()
            self.failed.emit(str(e))


class _Relay(QObject):
    def __init__(self, on_done, on_fail, on_progress, parent):
        super().__init__(parent)
        self._d, self._f, self._p = on_done, on_fail, on_progress

    @Slot(object)
    def done(self, v):
        self._d(v)

    @Slot(str)
    def failed(self, msg):
        if self._f:
            self._f(msg)

    @Slot(str)
    def progress(self, msg):
        if self._p:
            self._p(msg)


def run_bg(owner, fn, on_done, on_fail=None, on_progress=None):
    relay = _Relay(on_done, on_fail, on_progress, owner)   # lives on the GUI thread
    th = _Worker(fn, owner)
    th.done.connect(relay.done, Qt.QueuedConnection)
    th.failed.connect(relay.failed, Qt.QueuedConnection)
    th.progress.connect(relay.progress, Qt.QueuedConnection)

    def cleanup():
        owner._jobs = [j for j in getattr(owner, "_jobs", []) if j[0] is not th]
        relay.deleteLater()
        th.deleteLater()

    th.finished.connect(cleanup)
    owner._jobs = getattr(owner, "_jobs", []) + [(th, relay)]
    th.start()


# -- stylesheet ----------------------------------------------------------------------------------
def qss(t: Theme, frame_radius: int = 0) -> str:
    c = t.c
    r = t.radius
    fr = max(0, frame_radius - 4)
    upper = "uppercase" if t.display_upper else "none"
    sheet = (f"QWidget#Sheet {{ background: {c.panel}; border-left: 1px solid {c.border}; "
             f"border-top-right-radius: {max(0, frame_radius - 4)}px; border-bottom-right-radius: {max(0, frame_radius - 4)}px; }}"
             if t.id == "card_catalog" else "QWidget#Sheet { background: transparent; }")
    return f"""
    QWidget {{ color: {c.text}; }}
    QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget, QStackedWidget {{ background: transparent; }}
    QFrame#Panel {{ background: {c.panel}; border: 1px solid {c.border}; border-radius: {r}px; }}
    QFrame#Side {{ background: {c.panel}; border: none; border-right: 1px solid {c.border};
                   border-top-left-radius: {fr}px; border-bottom-left-radius: {fr}px; }}
    QFrame#TopNav {{ background: transparent; }}
    QFrame#Toast {{ background: {c.panel if t.id != 'ribbon' else 'rgba(10,20,50,0.85)'}; border: 1px solid {c.accent};
                    border-radius: {r}px; }}
    QPushButton {{ background: transparent; color: {c.text}; border: 1px solid {c.border}; border-radius: {r}px;
                   padding: 9px 18px; font-family: "{t.font_display}"; }}
    QPushButton:hover {{ border-color: {c.accent}; }}
    QPushButton:disabled {{ color: {c.muted}; border-color: {c.border}; }}
    QPushButton#Primary {{ background: {c.accent}; color: {c.accent_text}; border: 1px solid {c.accent}; font-weight: 600; }}
    QPushButton#Primary:hover {{ background: {c.accent}; border-color: {c.text}; }}
    QPushButton#Nav {{ border: none; text-align: left; padding: 10px 14px; color: {c.muted}; font-size: 15px;
                       text-transform: {upper}; }}
    QPushButton#Nav:checked {{ color: {c.accent_text}; background: {c.accent}; }}
    QPushButton#Nav:hover:!checked {{ color: {c.text}; }}
    QPushButton#TopNav {{ border: none; padding: 6px 4px; color: {c.muted}; font-size: 17px; font-weight: 300; }}
    QPushButton#TopNav:checked {{ color: {c.text}; font-size: 24px; font-weight: 400; }}
    QLineEdit, QSpinBox, QComboBox {{ background: {c.panel}; border: 1px solid {c.border}; border-radius: {max(0, r - 4)}px;
                                       padding: 7px 10px; color: {c.text}; selection-background-color: {c.accent};
                                       selection-color: {c.accent_text}; }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {c.accent}; }}
    QComboBox QAbstractItemView {{ background: {c.bg}; color: {c.text}; selection-background-color: {c.accent};
                                   selection-color: {c.accent_text}; border: 1px solid {c.border}; }}
    QListWidget {{ background: {c.panel}; border: 1px solid {c.border}; border-radius: {max(0, r - 4)}px; }}
    QListWidget::item {{ padding: 8px; }}
    QListWidget::item:selected {{ background: {c.accent}; color: {c.accent_text}; }}
    QCheckBox {{ spacing: 10px; background: transparent; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c.muted}; border-radius: 3px; background: {c.panel}; }}
    QCheckBox::indicator:checked {{ background: {c.accent}; border-color: {c.accent}; }}
    QLabel {{ background: transparent; }}
    {sheet}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c.border}; border-radius: 4px; min-height: 30px; }}
    QScrollBar::handle:vertical:hover {{ background: {c.muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QToolTip {{ background: {c.bg}; color: {c.text}; border: 1px solid {c.border}; }}
    QDialog {{ background: {c.bg}; }}
    QMessageBox {{ background: {c.bg}; }}
    """


def h1(t: Theme, text: str, px=30) -> QLabel:
    s = text.upper() if t.display_upper else text
    return W.label(s, t, px, display=True, bold=t.id not in ("ribbon",))


def section(t: Theme, text: str) -> QLabel:
    l = W.label(text.upper() if t.id != "terminal" else f"$ {text}", t, 12, "muted", bold=True)
    l.setStyleSheet(l.styleSheet() + " letter-spacing: 1px;")
    return l


def button(t: Theme, text: str, primary=False, on=None) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setObjectName("Primary")
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(lambda: PLAYER.play("select"))
    if on:
        b.clicked.connect(on)
    return b


def scroll(inner: QWidget) -> QScrollArea:
    s = QScrollArea()
    s.setWidgetResizable(True)
    s.setFrameShape(QFrame.NoFrame)
    s.setWidget(inner)
    s.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    return s


class FlowGrid(QWidget):
    """Wraps fixed-size cards into as many columns as fit."""

    def __init__(self, cards, spacing=16):
        super().__init__()
        self.cards = cards
        self.spacing = spacing
        for c in cards:
            c.setParent(self)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def _layout(self, width):
        if not self.cards:
            return 0
        cw, ch = self.cards[0].width(), self.cards[0].height()
        cols = max(1, (width + self.spacing) // (cw + self.spacing))
        for i, c in enumerate(self.cards):
            c.move((i % cols) * (cw + self.spacing), (i // cols) * (ch + self.spacing))
        rows = (len(self.cards) + cols - 1) // cols
        return rows * ch + (rows - 1) * self.spacing

    def resizeEvent(self, e):
        h = self._layout(e.size().width())
        if self.minimumHeight() != h:
            self.setMinimumHeight(h)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._layout(w)

    def sizeHint(self):
        return QSize(600, self._layout(max(self.width(), 600)))


# =======================================================================================================
class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("osmosis")
        self.setWindowIcon(QIcon.fromTheme("rpcs3"))
        self.resize(1280, 820)
        self.setMinimumSize(980, 640)
        self.settings = config.load()
        self.profile: Profile | None = None
        self.steamlib = None
        self.page_index = 0
        self.detail_serial: str | None = None
        self.backdrop = Backdrop()
        self.setCentralWidget(self.backdrop)
        # Own frame: no system title bar; the window paints its rounded border itself
        # (like Jeeves) and a slim bar at the top moves / minimises / maximises / closes it.
        if self.settings.own_frame:
            self.setWindowFlag(Qt.FramelessWindowHint, True)
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        outer = QVBoxLayout(self.backdrop)
        outer.setSpacing(0)
        self.titlebar = TitleBar(self)
        outer.addWidget(self.titlebar)
        self.body = QWidget()
        outer.addWidget(self.body, 1)
        self._outer = outer
        self.toast = W.Toast(self.backdrop)

        # in-window session tracking when the background service isn't running
        self.watcher = Watcher(on_session_end=lambda _s: QTimer.singleShot(0, self.reload))
        self._watch_timer = QTimer(self)
        self._watch_timer.timeout.connect(self._watch_tick)
        self._watch_timer.start(10_000)
        self._owns_watcher = self.watcher.acquire()

        self.apply_theme(reload=True)

    # -- data -------------------------------------------------------------------------------------------
    def reload(self):
        self.settings = config.load()
        self.profile = load_profile(self.settings)
        self.steamlib = steam.SteamLibrary.detect(self.settings)
        W.clear_cover_cache()
        self.build()
        if self.profile and self.profile.rpcs3:
            from .. import public
            try:
                public.write_own(self.settings, public.build(self.profile, self.settings))
            except Exception as e:
                print("public profile:", e, file=sys.stderr)
            QTimer.singleShot(300, self.check_mirror)

    # -- osmosis' record vs RPCS3 -----------------------------------------------------------------------
    def check_mirror(self):
        """Fold RPCS3's progress into osmosis' record; if RPCS3 is *behind* it, ask."""
        from .. import mirror
        rp = self.profile.rpcs3 if self.profile else None
        if not rp or backup.rpcs3_running() or getattr(self, "_mirror_busy", False):
            return
        self._mirror_busy = True
        titles = self.profile.titles()
        settings = self.settings

        def done(diffs):
            self._mirror_busy = False
            diffs = [d for d in diffs if (d.kind, d.key) not in getattr(self, "_mirror_snoozed", set())]
            if diffs:
                MirrorDialog(self, self.theme, rp, diffs).exec()

        def fail(err):
            self._mirror_busy = False
            print("mirror:", err, file=sys.stderr)

        run_bg(self, lambda _p: mirror.refresh(rp, settings, titles), done, fail)

    def _watch_tick(self):
        if not self._owns_watcher:
            self._owns_watcher = self.watcher.acquire()
            if not self._owns_watcher:
                return
        try:
            self.watcher.poll()
        except Exception as e:
            print("watcher:", e, file=sys.stderr)

    # -- theme ------------------------------------------------------------------------------------------
    def apply_theme(self, reload=False):
        self.settings = config.load()
        self.theme = resolve(self.settings.theme, self.settings.dark)
        t = self.theme
        app = QApplication.instance()
        from PySide6.QtGui import QFont
        f = QFont(t.font_body)
        f.setPixelSize(14)
        app.setFont(f)
        app.setStyleSheet(qss(t, self.settings.frame_radius if self.settings.own_frame else 0))
        pal = app.palette()
        pal.setColor(QPalette.Window, QColor(t.c.bg))
        pal.setColor(QPalette.WindowText, QColor(t.c.text))
        app.setPalette(pal)
        self.backdrop.set_theme(t, self.settings.ribbon_month_colors, fixed_color=self.settings.ribbon_color,
                                daylight=self.settings.ribbon_daylight)
        self.backdrop.ribbon_strength = self.settings.ribbon_opacity / 100
        from . import sounds as _snd
        _snd.CHOICES = dict(self.settings.sound_choices)
        PLAYER.enabled = self.settings.sounds
        PLAYER.set_name = t.sounds
        if self.settings.sounds:
            PLAYER.warm()
        if reload:
            self.reload()
        else:
            self.build()

    # -- layout -----------------------------------------------------------------------------------------
    def build(self):
        keep = None
        try:
            cur = self.stack.currentWidget()
            if isinstance(cur, QScrollArea) and self.stack.currentIndex() < getattr(self, "n_pages", 0):
                keep = (self.stack.currentIndex(), cur.verticalScrollBar().value())
        except (AttributeError, RuntimeError):
            pass
        old = self.body.layout()
        if old:
            QWidget().setLayout(old)  # reparent+discard the previous tree
        t = self.theme
        top = t.backdrop == "ribbon"
        root = QVBoxLayout() if top else QHBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.body.setLayout(root)
        self._apply_frame()

        pages = [("profile", self.page_profile), ("library", self.page_library), ("steam", self.page_steam),
                 ("backup", self.page_backup), ("install", self.page_install), ("misc", self.page_misc),
                 ("settings", self.page_settings)]
        self.n_pages = len(pages)
        if getattr(self, "xmb", None) is not None:
            try:
                self.xmb.stop_media()
            except RuntimeError:
                pass
        self.xmb = None
        nav = QFrame()
        nav.setObjectName("TopNav" if top else "Side")
        group = QButtonGroup(nav)
        self.nav_buttons = []
        if top:
            nl = QHBoxLayout(nav)
            nl.setContentsMargins(48, 22, 48, 6)
            brand = W.label("osmosis", t, 22, "text")
            brand.setStyleSheet(brand.styleSheet() + " font-weight: 300; letter-spacing: 2px;")
            nl.addWidget(brand)
            nl.addSpacing(60)
        else:
            nav.setFixedWidth(230)
            nl = QVBoxLayout(nav)
            nl.setContentsMargins(16, 24, 16, 18)
            nl.setSpacing(4)
            brand = W.label("osmosis", t, 24, "text", display=True, bold=True)
            nl.addWidget(brand)
            nl.addSpacing(22)
        for i, (key, _fn) in enumerate(pages):
            b = QPushButton(t.w(key).replace("&", "&&"))
            b.setObjectName("TopNav" if top else "Nav")
            if top:
                from PySide6.QtGui import QFont, QFontMetrics
                bf = QFont(t.font_body)
                bf.setPixelSize(24)
                b.setMinimumWidth(QFontMetrics(bf).horizontalAdvance(t.w(key)) + 16)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(i == self.page_index)
            b.clicked.connect(lambda _=False, i=i: self.go(i))
            group.addButton(b)
            self.nav_buttons.append(b)
            nl.addWidget(b)
            if top:
                nl.addSpacing(10)
        nl.addStretch(1)
        status = self._status_text()
        st = W.label(status, t, 12, "muted", wrap=True)
        nl.addWidget(st)
        if t.id == "terminal" and not top:
            nl.addWidget(W.Blink(t))
        root.addWidget(nav)

        self.stack = QStackedWidget()
        for _key, fn in pages:
            self.stack.addWidget(fn())
        if self.detail_serial and self.page_index == 1:
            self.stack.addWidget(self.page_game(self.detail_serial))
            self.stack.setCurrentIndex(self.stack.count() - 1)
        else:
            self.stack.setCurrentIndex(self.page_index)
        root.addWidget(self.stack, 1)
        self.toast.raise_()
        if keep and keep[0] == self.stack.currentIndex():
            w = self.stack.currentWidget()
            if isinstance(w, QScrollArea):
                QTimer.singleShot(0, lambda: w.verticalScrollBar().setValue(keep[1]))

    def _status_text(self) -> str:
        if not self.profile or not self.profile.rpcs3:
            return "RPCS3 not found — set its folder in Settings."
        n = len(self.profile.entries)
        if self.steamlib:
            synced = len(set(self.steamlib.entries_by_serial()) & {e.serial for e in self.profile.entries})
            return f"Steam: {synced} of {n} games in your library"
        return "Steam not found"

    def go(self, i: int):
        PLAYER.play("nav")
        self.page_index = i
        self.detail_serial = None
        self._check_nav(i)
        self._drop_detail()
        self.stack.setCurrentIndex(i)

    def _drop_detail(self):
        n = getattr(self, "n_pages", 6)
        if self.stack.count() > n:
            w = self.stack.widget(n)
            self.stack.removeWidget(w)
            w.deleteLater()

    def _check_nav(self, i):
        for j, b in enumerate(getattr(self, "nav_buttons", [])):
            b.setChecked(i == j)

    def open_game(self, serial: str):
        PLAYER.play("nav")
        self.detail_serial = serial
        self.page_index = 1
        self._check_nav(1)
        self._drop_detail()
        self.stack.addWidget(self.page_game(serial))
        self.stack.setCurrentIndex(self.n_pages)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast.move((self.backdrop.width() - self.toast.width()) // 2,
                            self.backdrop.height() - self.toast.height() - 28)

    def say(self, msg, action=None, sound="done"):
        PLAYER.play(sound)
        self.toast.show_message(msg, action)

    # =====================================================================================================
    # pages
    def _page(self, title: str | None = None):
        inner = QWidget()
        inner.setObjectName("Sheet")
        lay = QVBoxLayout(inner)
        top = self.theme.backdrop == "ribbon"
        lay.setContentsMargins(300 if top else 36, 18 if top else 30, 48 if top else 36, 30)
        lay.setSpacing(18)
        if title:
            lay.addWidget(h1(self.theme, title))
        return inner, lay

    def _no_rpcs3(self, lay):
        t = self.theme
        p = W.Panel()
        p.layout().addWidget(W.label("RPCS3 wasn't found.", t, 18, bold=True))
        p.layout().addWidget(W.label("osmosis looks in ~/.config/rpcs3. If yours is somewhere else, set it in "
                                     f"{t.w('settings')}.", t, 14, "muted", wrap=True))
        lay.addWidget(p)

    def export_bar(self) -> QWidget:
        t = self.theme
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(12)
        h.addWidget(button(t, t.w("export_all"), True, lambda: self.do_export("all")))
        h.addWidget(button(t, t.w("export_data"), False, lambda: self.do_export("data")))
        h.addWidget(button(t, t.w("export_trophies"), False, lambda: self.do_export("trophies")))
        h.addStretch(1)
        h.addWidget(button(t, t.w("import"), False, self.do_import))
        return bar

    # -- profile ------------------------------------------------------------------------------------------
    def page_profile(self):
        t = self.theme
        inner, lay = self._page()
        p = self.profile
        if not p or not p.rpcs3:
            self._no_rpcs3(lay)
            lay.addStretch(1)
            return scroll(inner)
        from .. import public
        name = public.display_name(self.settings, p)
        head = QHBoxLayout()
        head.addWidget(W.Avatar(t, name, public.avatar_path(), 72))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(h1(t, name, 32))
        c = p.trophy_counts
        col.addWidget(W.label(f"{len(p.entries)} {t.w('games')} · {W.hours(p.total_playtime_ms)} · "
                              f"{p.trophies_unlocked} / {p.trophies_total} {t.w('trophies').lower()}", t, 15, "muted"))
        head.addLayout(col, 1)
        head.addSpacing(16)
        share = QVBoxLayout()
        share.addWidget(button(t, "Copy Public Info", False, self.copy_public))
        share.addWidget(button(t, "View Copied Public Info", False, self.view_copied_public))
        head.addLayout(share)
        lay.addLayout(head)

        tiles = QGridLayout()
        tiles.setSpacing(12)
        vals = [(t.w("games").title(), str(len(p.entries))), ("Time played", W.hours(p.total_playtime_ms)),
                (t.w("trophies"), f"{p.trophies_unlocked} / {p.trophies_total}"),
                ("Platinum · Gold · Silver · Bronze", f"{c['platinum']} · {c['gold']} · {c['silver']} · {c['bronze']}")]
        for i, (k, v) in enumerate(vals):
            tile = W.Panel(pad=14, spacing=2)
            tile.layout().addWidget(W.label(k, t, 12, "muted"))
            tile.layout().addWidget(W.label(v, t, 26, display=True, bold=True))
            tiles.addWidget(tile, 0, i)
        lay.addLayout(tiles)

        lay.addWidget(section(t, t.w("recent")))
        recent = p.recent(10) or p.entries[:10]
        cards = []
        for e in recent:
            card = W.GameCard(e, t, self.steamlib, w=176)
            card.clicked.connect(lambda _=False, s=e.serial: self.open_game(s))
            cards.append(card)
        lay.addWidget(FlowGrid(cards))

        # friends: every other profile in the profiles folder
        fr = public.friends(self.settings)
        lay.addWidget(section(t, f"Friends ({len(fr)})"))
        if fr:
            grid = QGridLayout()
            grid.setSpacing(12)
            for i, d in enumerate(fr[:12]):
                b = QPushButton()
                b.setCursor(Qt.PointingHandCursor)
                b.setMinimumHeight(76)
                bl = QHBoxLayout(b)
                bl.setContentsMargins(12, 8, 12, 8)
                bl.addWidget(W.Avatar(t, d.get("name", "?"), d.get("avatar", ""), 52))
                tt = d.get("totals", {})
                txt = QVBoxLayout()
                txt.addWidget(W.label(d.get("name", "?"), t, 15, bold=True))
                txt.addWidget(W.label(f"{tt.get('games', 0)} {t.w('games')} · {W.hours(tt.get('playtime_ms', 0))} · "
                                      f"{tt.get('unlocked', 0)} {t.w('trophies').lower()}", t, 12, "muted"))
                bl.addLayout(txt, 1)
                b.clicked.connect(lambda _=False, d=d: self.open_friend(d))
                grid.addWidget(b, i // 3, i % 3)
            lay.addLayout(grid)
        else:
            lay.addWidget(W.label(f"Put friends' profile files in {public.profiles_dir(self.settings)} (or sync that "
                                  "folder with them, e.g. with Syncthing) and they'll show up here. Yours is kept "
                                  "there too.", t, 13, "muted", wrap=True))
        lay.addStretch(1)
        lay.addWidget(self.export_bar())
        return scroll(inner)

    # -- public profiles ------------------------------------------------------------------------------
    def _public(self) -> dict:
        from .. import public
        return public.build(self.profile, self.settings)

    def copy_public(self):
        from .. import public
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(public.encode(self._public()))
        self.say("Copied your public info — send it to a friend; they paste it with “View Copied Public Info”.")

    def view_copied_public(self):
        from .. import public
        from PySide6.QtGui import QGuiApplication
        try:
            d = public.decode(QGuiApplication.clipboard().text())
        except Exception:
            self.say("The clipboard doesn't hold osmosis public info. Ask your friend to press “Copy Public Info”.",
                     sound="error")
            return
        self.open_friend(d)

    def open_friend(self, d: dict):
        PLAYER.play("nav")
        self._drop_detail()
        self.detail_serial = None
        self.stack.addWidget(self.page_friend(d))
        self.stack.setCurrentIndex(self.n_pages)
        self._check_nav(0)

    def page_friend(self, d: dict):
        from .. import public
        t = self.theme
        inner, lay = self._page()
        top = QHBoxLayout()
        top.addWidget(button(t, "‹ " + t.w("profile"), False, lambda: self.go(0)))
        top.addStretch(1)
        if "_file" not in d:
            if d.get("id") and d.get("id") == self.settings.profile_id:
                top.addWidget(W.label("This is you.", t, 14, "muted"))
                existing = None
            else:
                existing = {f.get("name"): f for f in public.friends(self.settings)}
                by_id = {f.get("id"): f for f in existing.values() if f.get("id")}
                if d.get("id") in by_id:          # same person, maybe renamed
                    existing[d.get("name")] = by_id[d.get("id")]
            if existing is None:
                pass
            elif d.get("name") in existing:
                top.addWidget(button(t, "Update Friend", True,
                                     lambda: self._save_friend(d, replace=existing[d.get("name")])))
            else:
                top.addWidget(button(t, "Add to friends", True, lambda: self._save_friend(d)))
                if existing:
                    top.addWidget(button(t, "Replace Friend…", False, lambda: self._replace_friend(d, list(existing.values()))))
        else:
            top.addWidget(button(t, "Remove friend", False, lambda: self._remove_friend(d)))
        lay.addLayout(top)
        head = QHBoxLayout()
        head.addWidget(W.Avatar(t, d.get("name", "?"), d.get("avatar", ""), 72))
        col = QVBoxLayout()
        col.addWidget(h1(t, d.get("name", "?"), 32))
        upd = d.get("updated", "")[:16].replace("T", " ")
        tt = d.get("totals", {})
        col.addWidget(W.label(f"{tt.get('games', 0)} {t.w('games')} · {W.hours(tt.get('playtime_ms', 0))} · "
                              f"{tt.get('unlocked', 0)} / {tt.get('total', 0)} {t.w('trophies').lower()} · updated {upd}",
                              t, 15, "muted"))
        head.addLayout(col, 1)
        lay.addLayout(head)
        g = tt.get("grades", {})
        tiles = QGridLayout()
        tiles.setSpacing(12)
        for i, (k, v) in enumerate([(t.w("games").title(), str(tt.get("games", 0))),
                                    ("Time played", W.hours(tt.get("playtime_ms", 0))),
                                    (t.w("trophies"), f"{tt.get('unlocked', 0)} / {tt.get('total', 0)}"),
                                    ("Platinum · Gold · Silver · Bronze",
                                     f"{g.get('platinum', 0)} · {g.get('gold', 0)} · {g.get('silver', 0)} · {g.get('bronze', 0)}")]):
            tile = W.Panel(pad=14, spacing=2)
            tile.layout().addWidget(W.label(k, t, 12, "muted"))
            tile.layout().addWidget(W.label(v, t, 26, display=True, bold=True))
            tiles.addWidget(tile, 0, i)
        lay.addLayout(tiles)
        mine = {e.serial: e for e in (self.profile.entries if self.profile else [])}
        lay.addWidget(section(t, t.w("games")))
        for gm in sorted(d.get("games", []), key=lambda x: x.get("playtime_ms", 0), reverse=True):
            row = W.Panel(pad=10, spacing=14, horizontal=True)
            e = mine.get(gm.get("serial"))
            pic = QLabel()
            size = QSize(116, 64)
            if e:
                pic.setPixmap(W.scaled_crop(QPixmap(str(e.game.icon)), size) if e.game.icon else
                              W.placeholder_cover(gm.get("title", "?"), size, t))
            else:
                pic.setPixmap(W.placeholder_cover(gm.get("title", "?"), size, t))
            row.layout().addWidget(pic)
            c2 = QVBoxLayout()
            c2.addWidget(W.label(gm.get("title", "?"), t, 15, bold=True))
            extra = ""
            if e and e.trophies and gm.get("total"):
                extra = f"   ·   you: {e.percent}%"
            c2.addWidget(W.label(f"{W.hours(gm.get('playtime_ms', 0))} · {gm.get('unlocked', 0)}/{gm.get('total', 0)} "
                                 f"{t.w('trophies').lower()}{extra}", t, 12, "muted"))
            c2.addWidget(W.Bar(t, gm.get("percent", 0), 6))
            row.layout().addLayout(c2, 1)
            lay.addWidget(row)
        lay.addStretch(1)
        return scroll(inner)

    def _save_friend(self, d, replace: dict | None = None):
        from .. import public
        if replace and replace.get("_file"):
            Path(replace["_file"]).unlink(missing_ok=True)
        clean = {k: v for k, v in d.items() if not k.startswith("_")}
        path = public.save_friend(self.settings, clean)
        verb = "Updated" if replace and replace.get("name") == d.get("name") else ("Replaced " + replace.get("name", "") + " with") if replace else "Added"
        self.say(f"{verb} {d.get('name', 'them')}" + ("" if replace else " to your friends") + f" ({path.name}).")
        QTimer.singleShot(0, self.reload)

    def _replace_friend(self, d, friends: list[dict]):
        from PySide6.QtWidgets import QInputDialog
        names = [f.get("name", "?") for f in friends]
        name, ok = QInputDialog.getItem(self, "Replace Friend", f"Which friend is {d.get('name', 'this')} replacing?",
                                        names, 0, False)
        if ok:
            self._save_friend(d, replace=friends[names.index(name)])

    def _remove_friend(self, d):
        if QMessageBox.question(self, "Remove friend", f"Remove {d.get('name', 'this friend')} from your friends? "
                                "(If the folder is synced, it's removed for the others too.)") != QMessageBox.Yes:
            return
        Path(d["_file"]).unlink(missing_ok=True)
        self.go(0)
        QTimer.singleShot(0, self.reload)

    # -- library ------------------------------------------------------------------------------------------
    def page_library(self):
        t = self.theme
        inner, lay = self._page(t.w("library"))
        p = self.profile
        if not p or not p.rpcs3:
            self._no_rpcs3(lay)
            lay.addStretch(1)
            return scroll(inner)
        if t.backdrop == "ribbon":
            from .xmb import XmbList
            ordered = sorted(p.entries, key=lambda e: e.last_activity or __import__("datetime").datetime.min, reverse=True)
            self.xmb = XmbList(ordered, t, self.backdrop, self.settings)
            self.xmb.open_game.connect(self.open_game)
            holder = QWidget()
            hl = QVBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(self.xmb)
            return holder
        row = QHBoxLayout()
        search = QLineEdit()
        search.setPlaceholderText("Search games")
        sort = QComboBox()
        sort.addItems(["Recently played", "Most played", "Title", "Trophy progress"])
        row.addWidget(search, 1)
        row.addWidget(sort)
        lay.addLayout(row)
        holder = QVBoxLayout()
        lay.addLayout(holder)
        lay.addStretch(1)

        def refresh():
            while holder.count():
                it = holder.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            q = search.text().lower().strip()
            es = [e for e in p.entries if q in e.title.lower() or q in e.serial.lower()]
            key = sort.currentIndex()
            if key == 0:
                es.sort(key=lambda e: e.last_activity or __import__("datetime").datetime.min, reverse=True)
            elif key == 1:
                es.sort(key=lambda e: e.game.playtime_ms, reverse=True)
            elif key == 2:
                es.sort(key=lambda e: e.title.lower())
            else:
                es.sort(key=lambda e: e.percent, reverse=True)
            cards = []
            for e in es:
                card = W.GameCard(e, t, self.steamlib, w=176)
                card.clicked.connect(lambda _=False, s=e.serial: self.open_game(s))
                cards.append(card)
            holder.addWidget(FlowGrid(cards))

        search.textChanged.connect(refresh)
        sort.currentIndexChanged.connect(refresh)
        refresh()
        return scroll(inner)

    # -- game detail -----------------------------------------------------------------------------------------
    def page_game(self, serial: str):
        t = self.theme
        inner, lay = self._page()
        e = next(x for x in self.profile.entries if x.serial == serial)
        top = QHBoxLayout()
        top.addWidget(button(t, "‹ " + t.w("library"), False, lambda: self.go(1)))
        top.addStretch(1)
        lay.addLayout(top)
        lay.addWidget(W.Hero(e, t, self.steamlib))

        row = QHBoxLayout()
        info = QVBoxLayout()
        info.setSpacing(4)
        lp = e.game.last_played.strftime("%b %d, %Y") if e.game.last_played else "never"
        info.addWidget(W.label(f"{e.serial} · {W.hours(e.game.playtime_ms)} played · last played {lp}", t, 14, "muted"))
        row.addLayout(info, 1)
        play = button(t, f"▶  {t.w('play')}", True, lambda: self.play(serial))
        play.setMinimumWidth(150)
        row.addWidget(play, 0, Qt.AlignVCenter)
        lay.addLayout(row)

        if e.trophies:
            tp = W.Panel(pad=16, spacing=8)
            tr = e.trophies
            cg = tr.count_by_grade()
            tp.layout().addWidget(W.label(f"{t.w('trophies')} · {tr.unlocked} of {tr.total} · {e.percent}%   "
                                          f"(P{cg['platinum']} G{cg['gold']} S{cg['silver']} B{cg['bronze']})", t, 14))
            tp.layout().addWidget(W.Bar(t, e.percent, 8))
            lay.addWidget(tp)

        lay.addWidget(section(t, t.w("timeline")))
        tl = Timeline(t)
        tl.set_events(events_from(e))
        lay.addWidget(tl)

        if e.trophies:
            lay.addWidget(section(t, t.w("trophies")))
            order = sorted(e.trophies.trophies, key=lambda x: (not x.unlocked, -(x.unlocked_at.timestamp() if x.unlocked_at else 0), x.id))
            for tro in order:
                lay.addWidget(W.TrophyRow(tro, t))
        lay.addStretch(1)
        return scroll(inner)

    def play(self, serial: str):
        exe = rpcs3_binary(self.settings)
        if not exe:
            self.say("Couldn't find the rpcs3 program. Set its path in Settings.", sound="error")
            return
        subprocess.Popen([exe, "--no-gui", f"%RPCS3_GAMEID%:{serial}"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        self.say("Starting… this session will be added to the timeline when you're done.")

    # -- steam ------------------------------------------------------------------------------------------------
    def page_steam(self):
        t = self.theme
        inner, lay = self._page(t.w("steam"))
        s = self.settings
        info = W.Panel(pad=18, spacing=6)
        lib = self.steamlib
        exe = rpcs3_binary(s)
        n_games = len(self.profile.entries) if self.profile else 0
        synced = len(set(lib.entries_by_serial()) & {e.serial for e in self.profile.entries}) if lib and self.profile else 0
        for k, v in (("Steam", f"{lib.root}  (account {lib.user})" if lib else "not found"),
                     ("RPCS3 launcher", exe or "not found"),
                     ("In Steam", f"{synced} of {n_games} games"),
                     ):
            r = QHBoxLayout()
            kl = W.label(k, t, 14, "muted")
            kl.setFixedWidth(150)
            r.addWidget(kl)
            vl = W.label(v, t, 14, wrap=True)
            vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            r.addWidget(vl, 1)
            info.layout().addLayout(r)
        lay.addWidget(info)

        opts = W.Panel(pad=18, spacing=10)
        def chk(text, attr):
            cb = QCheckBox(text)
            cb.setChecked(getattr(s.steam, attr))
            cb.toggled.connect(lambda v: self._set_steam(attr, v))
            opts.layout().addWidget(cb)
        chk("Close Steam automatically while syncing (Steam overwrites its shortcut list when it exits)", "close_steam")
        chk("Reopen Steam afterwards", "reopen_steam")
        chk("Remove shortcuts for games no longer in RPCS3", "remove_missing")
        chk("Add library art (cover, banner above the title, logo, icon)", "fetch_art")
        ar = QHBoxLayout()
        ar.addWidget(W.label("Artwork from", t, 14, "muted"))
        src = QComboBox()
        src.addItem("The game's own art — the same as RPCS3's “Create Steam Shortcut”", "game")
        src.addItem("SteamGridDB (needs a free API key in Settings; falls back to the game's art)", "steamgriddb")
        src.setCurrentIndex(0 if s.steam.art_source != "steamgriddb" else 1)
        src.currentIndexChanged.connect(lambda _i: self._set_steam("art_source", src.currentData()))
        ar.addWidget(src, 1)
        opts.layout().addLayout(ar)
        opts.layout().addWidget(W.label("Changed the art source? Use “Re-download all art” to redo existing shortcuts.",
                                        t, 12, "muted", wrap=True))
        lay.addWidget(opts)

        row = QHBoxLayout()
        self.sync_btn = button(t, t.w("sync"), True, lambda: self.do_sync(False))
        row.addWidget(self.sync_btn)
        row.addWidget(button(t, "Re-download all art", False, lambda: self.do_sync(True)))
        row.addStretch(1)
        lay.addLayout(row)
        self.sync_log = W.label("Shortcuts made with RPCS3's own “Create Steam Shortcut” are picked up and "
                                "kept, not duplicated. Your other Steam shortcuts are never touched.", t, 13, "muted", wrap=True)
        lay.addWidget(self.sync_log)
        lay.addStretch(1)
        return scroll(inner)

    def _set_steam(self, attr, v):
        s = config.load()
        setattr(s.steam, attr, v)
        config.save(s)
        self.settings = s

    def do_sync(self, refresh_art: bool):
        lib, p = self.steamlib, self.profile
        if not lib:
            self.say("Steam wasn't found.", sound="error")
            return
        exe = rpcs3_binary(self.settings)
        if not exe:
            self.say("Couldn't find the rpcs3 program. Set its path in Settings.", sound="error")
            return
        self.sync_btn.setEnabled(False)
        games = [e.game for e in p.entries]
        s = config.load()

        def work(progress):
            return steam.sync_with_steam_closed(lib, games, exe, s, progress=progress, refresh_art=refresh_art)

        def done(rep):
            parts = []
            for k, xs in (("added", rep.added), ("picked up", rep.adopted), ("updated", rep.updated), ("removed", rep.removed)):
                if xs:
                    parts.append(f"{len(xs)} {k}")
            arts = sum(1 for v in rep.art.values() if v)
            if arts:
                parts.append(f"art for {arts}")
            msg = "Steam is up to date." if not parts else "Steam sync: " + ", ".join(parts) + "."
            if rep.warnings:
                msg += f"  ({len(rep.warnings)} warning{'s' if len(rep.warnings) > 1 else ''}: {rep.warnings[0]})"
            self.say(msg)
            QTimer.singleShot(0, self.reload)

        def fail(err):
            self.say(f"Sync failed: {err}", sound="error")
            try:
                self.sync_btn.setEnabled(True)
            except RuntimeError:
                pass

        def prog(m):
            try:
                self.sync_log.setText(m)
            except RuntimeError:
                pass

        run_bg(self, work, done, fail, prog)

    # -- backup ------------------------------------------------------------------------------------------------
    def page_backup(self):
        t = self.theme
        inner, lay = self._page(t.w("backup"))
        s = self.settings
        for kind, desc in (("all", "Saves, settings, per-game configs, PSN licences, trophies and play history."),
                           ("data", "Saves, settings, per-game configs, PSN licences and play history."),
                           ("trophies", "Every trophy set and when each trophy was unlocked.")):
            card = W.Panel(pad=16, spacing=14, horizontal=True)
            col = QVBoxLayout()
            col.addWidget(W.label(t.w(f"export_{kind}"), t, 17, display=True, bold=True))
            col.addWidget(W.label(desc + " Installed games and firmware are never included.", t, 13, "muted", wrap=True))
            card.layout().addLayout(col, 1)
            card.layout().addWidget(button(t, "Copy to clipboard", kind == "all", lambda _=False, k=kind: self.do_export(k)))
            lay.addWidget(card)
        lay.addWidget(W.label("The backup is copied as a file — paste it into Dolphin, a synced folder, or Google Drive.",
                              t, 13, "muted", wrap=True))
        rr = QHBoxLayout()
        rr.addWidget(button(t, t.w("import"), False, self.do_import))
        rr.addWidget(button(t, "Open exports folder", False, lambda: W.open_folder(backup.default_export_dir())))
        rr.addStretch(1)
        lay.addLayout(rr)

        # auto export
        lay.addWidget(section(t, "Automatic backups"))
        ae = s.auto_export
        panel = W.Panel(pad=18, spacing=12)
        trig = QComboBox()
        labels = {"never": "Never", "session": "After every play session", "daily": "Once a day", "weekly": "Once a week"}
        for k in config.TRIGGERS:
            trig.addItem(labels[k], k)
        trig.setCurrentIndex(list(config.TRIGGERS).index(ae.trigger))
        r1 = QHBoxLayout()
        r1.addWidget(W.label("Back up", t, 14))
        r1.addWidget(trig)
        r1.addStretch(1)
        panel.layout().addLayout(r1)
        parts_row = QHBoxLayout()
        boxes = {}
        for part, text in (("saves", "Saves && licences"), ("configs", "Settings && configs"), ("trophies", "Trophies"),
                           ("history", "Play history")):
            cb = QCheckBox(text)
            cb.setChecked(part in ae.parts)
            boxes[part] = cb
            parts_row.addWidget(cb)
        parts_row.addStretch(1)
        panel.layout().addLayout(parts_row)
        panel.layout().addWidget(W.label("…to these folders:", t, 13, "muted"))
        folders = QListWidget()
        folders.setFixedHeight(110)
        for f in ae.folders:
            folders.addItem(f)
        panel.layout().addWidget(folders)
        fr = QHBoxLayout()
        keep = QSpinBox()
        keep.setRange(0, 999)
        keep.setValue(ae.keep)
        keep.setSpecialValueText("all")

        def save_ae():
            st = config.load()
            st.auto_export.trigger = trig.currentData()
            st.auto_export.parts = [k for k, b in boxes.items() if b.isChecked()]
            st.auto_export.folders = [folders.item(i).text() for i in range(folders.count())]
            st.auto_export.keep = keep.value()
            config.save(st)
            self.settings = st

        def add_folder():
            d = QFileDialog.getExistingDirectory(self, "Back up to folder", str(Path.home()))
            if d:
                folders.addItem(d)
                save_ae()

        def remove_folder():
            for it in folders.selectedItems():
                folders.takeItem(folders.row(it))
            save_ae()

        fr.addWidget(button(t, "Add folder…", False, add_folder))
        fr.addWidget(button(t, "Remove", False, remove_folder))
        fr.addStretch(1)
        fr.addWidget(W.label("Keep the newest", t, 13, "muted"))
        fr.addWidget(keep)
        panel.layout().addLayout(fr)
        trig.currentIndexChanged.connect(lambda _i: save_ae())
        keep.valueChanged.connect(lambda _v: save_ae())
        for b in boxes.values():
            b.toggled.connect(lambda _v: save_ae())
        lay.addWidget(panel)
        lay.addStretch(1)
        return scroll(inner)

    def do_export(self, kind: str):
        rp = self.profile.rpcs3 if self.profile else None
        if not rp:
            self.say("RPCS3 wasn't found.", sound="error")
            return
        titles = self.profile.titles()
        self.toast.show_message("Packing your backup…", ms=60_000)

        def done(path: Path):
            W.copy_file_to_clipboard(path)
            self.say(f"Copied {path.name} ({W.human_size(path.stat().st_size)}) to the clipboard — paste it anywhere.",
                     ("Show file", lambda: W.open_folder(path.parent)))

        run_bg(self, lambda _p: backup.export(rp, kind, titles=titles), done,
               lambda err: self.say(f"Export failed: {err}", sound="error"))

    def do_import(self):
        rp = self.profile.rpcs3 if self.profile else None
        if not rp:
            self.say("RPCS3 wasn't found.", sound="error")
            return
        f, _ = QFileDialog.getOpenFileName(self, "Restore a backup", str(Path.home()), "osmosis backups (*.zip)")
        if not f:
            return
        if backup.rpcs3_running():
            self.say("Close RPCS3 first — it writes saves while it's running.", sound="error")
            return
        try:
            items = backup.plan(rp, Path(f), self.profile.titles())
        except Exception as e:
            self.say(f"That file can't be restored: {e}", sound="error")
            return
        decisions: dict[str, str] = {}
        sticky = None
        for it in items:
            if not it.conflict:
                continue
            if sticky:
                decisions[it.key] = sticky
                continue
            d = ConflictDialog(self, self.theme, it).ask()
            if d is None:
                return  # cancelled: nothing restored
            if d in ("replace_all", "skip_all"):
                sticky = d.split("_")[0]
                d = sticky
            decisions[it.key] = d

        def work(_p):
            return backup.apply(rp, Path(f), items, resolve=lambda it: decisions.get(it.key, "skip"))

        def done(res):
            msg = f"Restored {len(res.restored)} item(s)"
            if res.skipped:
                msg += f", skipped {len(res.skipped)}"
            if res.unchanged:
                msg += f", {len(res.unchanged)} already up to date"
            msg += "."
            action = ("Previous files", lambda: W.open_folder(res.safety_copy)) if res.safety_copy else None
            self.say(msg, action)
            # You chose to restore this backup, so osmosis' record follows it (no "RPCS3 is behind" prompt).
            from .. import mirror
            try:
                mirror.accept(rp, mirror.refresh(rp, self.settings, self.profile.titles()))
            except Exception as e:
                print("mirror after restore:", e, file=sys.stderr)
            QTimer.singleShot(0, self.reload)

        run_bg(self, work, done, lambda err: self.say(f"Restore failed: {err}", sound="error"))

    # -- install packages -------------------------------------------------------------------------------
    def page_install(self):
        from .. import install as inst
        t = self.theme
        inner, lay = self._page(t.w("install"))
        rp = self.profile.rpcs3 if self.profile else None
        if not rp:
            self._no_rpcs3(lay)
            lay.addStretch(1)
            return scroll(inner)
        lay.addWidget(W.label("Install games, updates and DLC you already have as files. Licence files (.rap, "
                              ".edat) are copied straight into RPCS3; each .pkg is installed by RPCS3 itself, one "
                              "after another.", t, 14, "muted", wrap=True))
        self._inst_paths: list[Path] = []
        lst = QListWidget()
        lst.setMinimumHeight(220)
        summary = W.label("Nothing chosen yet.", t, 13, "muted")

        def refresh():
            lst.clear()
            found = inst.scan(self._inst_paths)
            for f in found.pkgs:
                lst.addItem(f"package   {f.name}")
            for f in found.licences:
                lst.addItem(f"licence   {f.name}")
            summary.setText(f"{len(found.pkgs)} package(s), {len(found.licences)} licence file(s)." if found
                            else "No .pkg, .rap or .edat files found there.")
            go.setEnabled(bool(found))

        def add_files():
            fs, _ = QFileDialog.getOpenFileNames(self, "Choose packages and licences", str(Path.home()),
                                                 "PS3 content (*.pkg *.rap *.edat *.PKG *.RAP *.EDAT)")
            self._inst_paths += [Path(f) for f in fs]
            refresh()

        def add_folder():
            d = QFileDialog.getExistingDirectory(self, "Choose a folder to scan", str(Path.home()))
            if d:
                self._inst_paths.append(Path(d))
                refresh()

        def clear():
            self._inst_paths = []
            refresh()

        row = QHBoxLayout()
        row.addWidget(button(t, "Add files…", False, add_files))
        row.addWidget(button(t, "Add a folder…", False, add_folder))
        row.addWidget(button(t, "Clear", False, clear))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(lst)
        lay.addWidget(summary)
        status = W.label("", t, 13, "muted", wrap=True)
        go = button(t, "Install", True, lambda: self._do_install(status))
        go.setEnabled(False)
        gr = QHBoxLayout()
        gr.addWidget(go)
        gr.addStretch(1)
        lay.addLayout(gr)
        lay.addWidget(status)
        lay.addStretch(1)
        return scroll(inner)

    def _do_install(self, status: QLabel):
        from .. import install as inst
        rp = self.profile.rpcs3
        exe = rpcs3_binary(self.settings)
        if backup.rpcs3_running():
            self.say("Close RPCS3 first.", sound="error")
            return
        found = inst.scan(self._inst_paths)
        if found.pkgs and not exe:
            self.say("Couldn't find the rpcs3 program. Set its path in Settings.", sound="error")
            return

        def work(progress):
            lic = inst.install_licences(rp, found.licences)
            progress(f"Copied {len(lic)} licence file(s).")
            res = inst.install_pkgs(exe, found.pkgs, progress) if found.pkgs else []
            return lic, res

        def done(out):
            lic, res = out
            ok = sum(1 for _p, code in res if code == 0)
            self.say(f"Done: {len(lic)} licence file(s) copied, {ok} of {len(res)} package(s) installed.")
            QTimer.singleShot(0, self.reload)

        def prog(m):
            try:
                status.setText(m)
            except RuntimeError:
                pass

        run_bg(self, work, done, lambda e: self.say(f"Install failed: {e}", sound="error"), prog)

    # -- miscellaneous: LBP online ---------------------------------------------------------------------
    def page_misc(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from .. import lbp
        t = self.theme
        inner, lay = self._page(t.w("misc"))
        rp = self.profile.rpcs3 if self.profile else None
        if not rp:
            self._no_rpcs3(lay)
            lay.addStretch(1)
            return scroll(inner)
        open_url = lambda u: QDesktopServices.openUrl(QUrl(u))
        lay.addWidget(W.label("LittleBigPlanet online", t, 20, display=True, bold=True))
        lay.addWidget(W.label("Play LBP 1–3 online on fan-run custom servers. osmosis handles RPCS3's network "
                              "settings and switching servers; a few steps need you (marked below).", t, 14, "muted", wrap=True))

        games = lbp.games(rp, [e.game for e in self.profile.entries])
        net = lbp.net_status(rp)
        pw_ok = lbp.patchwork_installed(rp)
        server_key, server_url = lbp.current_server(rp)
        patched = [g for g in games if g.patched]

        def tick(ok):
            return "✓" if ok else "○"

        # -- status checklist -------------------------------------------------------------
        st = W.Panel(pad=18, spacing=10)

        def row(ok, title, detail, action=None, manual=False):
            r = QHBoxLayout()
            mark = W.label(tick(ok), t, 18, "accent" if ok else "muted", bold=True)
            mark.setFixedWidth(26)
            r.addWidget(mark, 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(2)
            head = title + ("   · you do this" if manual and not ok else "")
            col.addWidget(W.label(head, t, 15, bold=True))
            if detail:
                col.addWidget(W.label(detail, t, 13, "muted", wrap=True))
            r.addLayout(col, 1)
            if action and not ok:
                r.addWidget(action, 0, Qt.AlignTop)
            st.layout().addLayout(r)

        if games:
            names = ", ".join(f"{g.game.title} ({g.serial})" for g in games)
            missing = [g for g in games if not g.eboot]
            row(not missing, "1. Game installed with its latest update",
                names + (("\nNo update installed for: " + ", ".join(g.serial for g in missing) +
                          ". Install it in RPCS3 with File → Install Packages/Raps/Edats — Refresher patches "
                          "the copy in dev_hdd0/game.") if missing else ""), manual=True)
        else:
            row(False, "1. A LittleBigPlanet game", "No LittleBigPlanet game was found in RPCS3.", manual=True)

        net_ok = net.internet and net.rpcn
        row(net_ok, "2. RPCS3 network: Connected, PSN status RPCN",
            "Done." if net_ok else "Sets Configuration → Network to Connected and PSN status to RPCN. "
                                   "Close RPCS3 first; your config is backed up before it's changed.",
            button(t, "Turn on", True, self._lbp_network))
        row(bool(net.account), "3. RPCN account",
            f"Signed in as {net.account}." if net.account else
            "In RPCS3: Configuration → RPCN → Account → Create Account. Enter a username, password and email, "
            "then paste the token RPCN emails you. osmosis can't do this one — it needs your email.", manual=True)
        ref = lbp.find_refresher(self.settings)
        ref_btn = button(t, "Open Refresher" if ref else "Get Refresher", False,
                         (lambda: self._run_refresher(ref)) if ref else
                         (lambda: open_url(lbp.REFRESHER_RELEASES)))
        row(pw_ok and bool(patched), "4. Patch the game with Refresher (adds Patchwork)",
            ("Patched: " + ", ".join(g.serial for g in patched) + "." if patched and pw_ok else
             "Close RPCS3, open Refresher, choose “RPCS3 Patch”, and give it your dev_hdd0 folder:\n"
             f"{rp.dev_hdd0}\nPick your LBP game and a server (Beacon or Bonsai), then Patch. Do this once per "
             "LBP game. Refresher is the only patcher LBP Union approves; it also installs Patchwork, which "
             "blocks a known in-game exploit."), ref_btn, manual=True)
        acct = {k: v["account"] for k, v in lbp.SERVERS.items()}
        row(False, "5. Make an account on the server",
            "Beacon: " + acct["beacon"] + "\nBonsai: " + acct["bonsai"], manual=True)
        lay.addWidget(st)

        olds = lbp.old_server_patches(rp)
        if olds:
            lay.addWidget(W.label("Heads up: RPCS3 still has old server-redirect game patches turned on (" + ", ".join(olds)
                                  + "). With Patchwork in use, switch those off in RPCS3 → Manage → Game Patches.",
                                  t, 13, "stamp", wrap=True))

        # -- server switcher -------------------------------------------------------------------
        lay.addWidget(section(t, "Server"))
        now = {"beacon": "Beacon", "bonsai": "Bonsai", "custom": f"a custom server ({server_url})", None: "none yet"}[server_key]
        lay.addWidget(W.label(f"Your LBP games currently connect to: {now}", t, 14))
        cards = QHBoxLayout()
        for key, info in lbp.SERVERS.items():
            c = W.Panel(pad=16, spacing=8)
            c.layout().addWidget(W.label(f"{info['name']}  ·  {info['by']}", t, 17, display=True, bold=True))
            c.layout().addWidget(W.label(info["base"], t, 12, "muted"))
            br = QHBoxLayout()
            use = button(t, "Using this" if key == server_key else "Switch to " + info["name"], key != server_key,
                         lambda _=False, k=key: self._lbp_switch(k))
            use.setEnabled(pw_ok and key != server_key)
            br.addWidget(use)
            br.addWidget(button(t, "Website", False, lambda _=False, u=info["site"]: open_url(u)))
            br.addStretch(1)
            c.layout().addLayout(br)
            cards.addWidget(c)
        lay.addLayout(cards)
        if not pw_ok:
            lay.addWidget(W.label("Switching servers unlocks after step 4 — Refresher installs the Patchwork module "
                                  "that osmosis points at the server.", t, 13, "muted", wrap=True))

        jr = QHBoxLayout()
        jr.addWidget(W.label("Join key (optional)", t, 14, "muted"))
        jk = QLineEdit(lbp.lobby_password(rp))
        jk.setPlaceholderText("only players with the same key can join your games")
        jr.addWidget(jk, 1)
        save = button(t, "Save key", False, lambda: self._lbp_key(jk.text()))
        save.setEnabled(pw_ok)
        jr.addWidget(save)
        lay.addLayout(jr)

        if patched:
            lay.addWidget(section(t, "Undo"))
            ur = QHBoxLayout()
            for g in patched:
                ur.addWidget(button(t, f"Restore original {g.serial}", False, lambda _=False, g=g: self._lbp_unpatch(g)))
            ur.addStretch(1)
            lay.addLayout(ur)
        links = QHBoxLayout()
        links.addWidget(button(t, "Refresher's RPCS3 guide", False, lambda: open_url(lbp.REFRESHER_GUIDE)))
        links.addStretch(1)
        lay.addLayout(links)
        lay.addStretch(1)
        return scroll(inner)

    def _run_refresher(self, path):
        from .. import lbp
        cmd, env = lbp.refresher_command(path)
        log = open(config.data_dir() / "refresher.log", "w")
        try:
            proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            self.say(f"Couldn't start Refresher: {e}", sound="error")
            return

        def check():
            if proc.poll() not in (None, 0):
                self.say("Refresher closed straight away. Details: ~/.local/share/osmosis/refresher.log", sound="error")
        QTimer.singleShot(4000, check)

    def _lbp_network(self):
        from .. import lbp
        if backup.rpcs3_running():
            self.say("Close RPCS3 first — it rewrites its config when it exits.", sound="error")
            return
        lbp.enable_network(self.profile.rpcs3)
        self.say("RPCS3 network set to Connected + RPCN.")
        QTimer.singleShot(0, self.reload)

    def _lbp_switch(self, key):
        from .. import lbp
        rp = self.profile.rpcs3
        if backup.rpcs3_running():
            self.say("Close RPCS3 first.", sound="error")
            return
        self.toast.show_message(f"Asking {lbp.SERVERS[key]['name']} for its settings…", ms=30_000)
        run_bg(self, lambda _p: lbp.set_server(rp, key), lambda url: (self.say(f"LBP now connects to {url}"),
                                                                        QTimer.singleShot(0, self.reload)),
               lambda err: self.say(f"Couldn't switch: {err}", sound="error"))

    def _lbp_key(self, text):
        from .. import lbp
        rp = self.profile.rpcs3
        key, _url = lbp.current_server(rp)
        try:
            d = lbp._pw(rp, "x").parent
            d.mkdir(parents=True, exist_ok=True)
            f = d / "patchwork_lobby_password.txt"
            if text.strip():
                f.write_text(text.strip(), encoding="utf-8")
            else:
                f.unlink(missing_ok=True)
            self.say("Join key saved." if text.strip() else "Join key cleared.")
        except OSError as e:
            self.say(f"Couldn't save: {e}", sound="error")

    def _lbp_unpatch(self, g):
        from .. import lbp
        if QMessageBox.question(self, "Restore original", f"Put {g.game.title}'s unpatched EBOOT back? "
                                "It won't connect to custom servers until you patch it again.") != QMessageBox.Yes:
            return
        try:
            lbp.restore_original(g)
            self.say("Original EBOOT restored.")
            QTimer.singleShot(0, self.reload)
        except Exception as e:
            self.say(str(e), sound="error")

    # -- settings --------------------------------------------------------------------------------------------------
    def page_settings(self):
        t = self.theme
        inner, lay = self._page(t.w("settings"))
        s = self.settings
        lay.addWidget(section(t, "Theme"))
        grid = QGridLayout()
        grid.setSpacing(12)
        for i, tid in enumerate(ORDER):
            th = THEMES[tid]
            b = QPushButton(f"{th.name}\n{th.blurb}")
            b.setCheckable(True)
            b.setChecked(tid == s.theme)
            b.setMinimumHeight(70)
            b.setStyleSheet("text-align: left; padding: 10px 14px;" +
                            (f"border: 2px solid {t.c.accent};" if tid == s.theme else ""))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, tid=tid: self._set("theme", tid, retheme=True))
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)

        p = W.Panel(pad=18, spacing=10)
        dark = QCheckBox("Dark mode (General, Summit and Card Catalog)")
        dark.setChecked(s.dark)
        dark.toggled.connect(lambda v: self._set("dark", v, retheme=True))
        p.layout().addWidget(dark)
        snd = QCheckBox("Theme sounds")
        snd.setChecked(s.sounds)
        snd.toggled.connect(lambda v: self._set_live(sounds=v))
        p.layout().addWidget(snd)
        own = QCheckBox("Rounded window with osmosis' own border (no system title bar)")
        own.setChecked(s.own_frame)
        own.toggled.connect(lambda v: self._set_live(own_frame=v))
        p.layout().addWidget(own)
        fr_row = QHBoxLayout()
        fr_row.addWidget(W.label("Border thickness", t, 14, "muted"))
        bw = QSpinBox()
        bw.setRange(0, 12)
        bw.setSuffix(" px")
        bw.setValue(s.frame_border)
        bw.valueChanged.connect(lambda v: self._set_live(frame_border=v))
        fr_row.addWidget(bw)
        fr_row.addSpacing(24)
        fr_row.addWidget(W.label("Corner roundness", t, 14, "muted"))
        rr = QSpinBox()
        rr.setRange(0, 40)
        rr.setSuffix(" px")
        rr.setValue(s.frame_radius)
        rr.valueChanged.connect(lambda v: self._set_live(frame_radius=v))
        fr_row.addWidget(rr)
        fr_row.addStretch(1)
        p.layout().addLayout(fr_row)
        lay.addWidget(p)

        # -- Ribbon ---------------------------------------------------------------
        from .themes import MONTH_COLORS, MONTH_NAMES
        lay.addWidget(section(t, "Ribbon"))
        rb = W.Panel(pad=18, spacing=10)
        cr = QHBoxLayout()
        cr.addWidget(W.label("Colour", t, 14, "muted"))
        color = QComboBox()
        color.addItem("Follow the calendar, like the PS3", "calendar")
        for name, hexv in zip(MONTH_NAMES, MONTH_COLORS):
            color.addItem(f"{name} colour", hexv)
        color.addItem("Classic blue", "#1d4fa3")
        color.addItem("Custom…", "custom")
        from PySide6.QtGui import QPixmap as _QP
        for i in range(1, color.count() - 1):
            sw = _QP(14, 14)
            sw.fill(QColor(color.itemData(i)))
            color.setItemIcon(i, QIcon(sw))
        if s.ribbon_month_colors:
            color.setCurrentIndex(0)
        else:
            idx = color.findData(s.ribbon_color)
            color.setCurrentIndex(idx if idx > 0 else color.count() - 1)

        def pick_color(_i):
            v = color.currentData()
            if v == "calendar":
                self._set_many(ribbon_month_colors=True)
                return
            if v == "custom":
                from PySide6.QtWidgets import QColorDialog
                c = QColorDialog.getColor(QColor(s.ribbon_color), self, "Ribbon colour")
                if not c.isValid():
                    return
                v = c.name()
            self._set_many(ribbon_month_colors=False, ribbon_color=v)

        color.currentIndexChanged.connect(pick_color)
        cr.addWidget(color, 1)
        rb.layout().addLayout(cr)
        orow = QHBoxLayout()
        orow.addWidget(W.label("Wave strength", t, 14, "muted"))
        from PySide6.QtWidgets import QSlider
        op = QSlider(Qt.Horizontal)
        op.setRange(0, 200)
        op.setValue(s.ribbon_opacity)
        opl = W.label(f"{s.ribbon_opacity}%", t, 13, "muted")
        opl.setFixedWidth(48)
        op.valueChanged.connect(lambda v: (opl.setText(f"{v}%"), self._set_live(ribbon_opacity=v)))
        orow.addWidget(op, 1)
        orow.addWidget(opl)
        rb.layout().addLayout(orow)
        dl = QCheckBox("Darker at night, brighter in the day (like the PS3)")
        dl.setChecked(s.ribbon_daylight)
        dl.toggled.connect(lambda v: self._set_live(ribbon_daylight=v))
        rb.layout().addWidget(dl)
        md = QCheckBox("Play each game's menu music and animated icon when you rest on it (needs ffmpeg)")
        md.setChecked(s.ribbon_media)
        md.toggled.connect(lambda v: self._set_live(ribbon_media=v))
        rb.layout().addWidget(md)
        pr = QHBoxLayout()
        pr.addWidget(W.label("Animated icon plays", t, 14, "muted"))
        pv = QComboBox()
        pv.addItem("Across the whole background", True)
        pv.addItem("Inside the game's icon, like the PS3", False)
        pv.setCurrentIndex(0 if s.ribbon_trailer_bg else 1)
        pv.currentIndexChanged.connect(lambda _i: self._set_live(ribbon_trailer_bg=pv.currentData()))
        pr.addWidget(pv, 1)
        rb.layout().addLayout(pr)
        lay.addWidget(rb)

        # -- sounds: built-in choices + your own -------------------------------------------
        from . import sounds as snd
        set_name = THEMES.get(s.theme, THEMES["ribbon"]).sounds
        if set_name:
            lay.addWidget(section(t, f"{THEMES[s.theme].name} sounds"))
            sp = W.Panel(pad=18, spacing=10)
            for ev, label_text in (("nav", "Moving around"), ("select", "Pressing a button"), ("done", "Something finished"),
                                   ("error", "Something went wrong")):
                r = QHBoxLayout()
                lbl = W.label(label_text, t, 14, "muted")
                lbl.setFixedWidth(190)
                r.addWidget(lbl)
                cb = QComboBox()

                def fill(cb=cb, ev=ev):
                    cb.blockSignals(True)
                    cb.clear()
                    for opt in snd.options(set_name, ev):
                        cb.addItem(opt)
                    cur = snd.current(set_name, ev)
                    if cb.findText(cur) >= 0:
                        cb.setCurrentIndex(cb.findText(cur))
                    cb.blockSignals(False)

                fill()

                def changed(_i, ev=ev, cb=cb):
                    st = config.load()
                    st.sound_choices[f"{set_name}:{ev}"] = cb.currentText()
                    self._set_live(sound_choices=st.sound_choices)
                    PLAYER.play(ev, cb.currentText(), force=True)

                def add_own(_=False, ev=ev, cb=cb, fill=fill):
                    f, _f = QFileDialog.getOpenFileName(self, "Choose a sound", str(Path.home()),
                                                        "Audio (*.wav *.ogg *.mp3 *.flac *.opus *.m4a)")
                    if not f:
                        return
                    try:
                        name = snd.import_custom(set_name, ev, Path(f))
                    except Exception as e:
                        self.say(str(e), sound="error")
                        return
                    st = config.load()
                    st.sound_choices[f"{set_name}:{ev}"] = name
                    self._set_live(sound_choices=st.sound_choices)
                    fill()
                    PLAYER.play(ev, name, force=True)

                cb.currentIndexChanged.connect(changed)
                r.addWidget(cb, 1)
                prev = QPushButton("▶")
                prev.setFixedWidth(46)
                prev.clicked.connect(lambda _=False, ev=ev, cb=cb: PLAYER.play(ev, cb.currentText(), force=True))
                r.addWidget(prev)
                own = QPushButton("Add your own…")
                own.clicked.connect(add_own)
                r.addWidget(own)
                sp.layout().addLayout(r)
            sp.layout().addWidget(W.label("Your own sounds are copied into osmosis (the first 10 seconds) and stay "
                                          "available for this theme.", t, 12, "muted", wrap=True))
            lay.addWidget(sp)

        # -- your profile -----------------------------------------------------------------------
        from .. import public
        lay.addWidget(section(t, "Your profile"))
        pf = W.Panel(pad=18, spacing=10)
        pr = QHBoxLayout()
        av = W.Avatar(t, public.display_name(s, self.profile), public.avatar_path(), 64)
        pr.addWidget(av)
        nm = QLineEdit(s.profile_name)
        nm.setPlaceholderText(public.display_name(config.Settings(), self.profile))
        nm.editingFinished.connect(lambda: self._set("profile_name", nm.text().strip(), reload=True))
        pr.addWidget(nm, 1)

        def pick_pic():
            f, _f = QFileDialog.getOpenFileName(self, "Choose a picture", str(Path.home()),
                                                "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
            if not f:
                return
            img = QPixmap(f)
            if img.isNull():
                self.say("That picture couldn't be opened.", sound="error")
                return
            out = public.avatar_path()
            out.parent.mkdir(parents=True, exist_ok=True)
            W.scaled_crop(img, QSize(256, 256)).save(str(out), "PNG")
            QTimer.singleShot(0, self.reload)

        def clear_pic():
            public.avatar_path().unlink(missing_ok=True)
            QTimer.singleShot(0, self.reload)

        pr.addWidget(button(t, "Choose picture…", False, pick_pic))
        pr.addWidget(button(t, "Remove", False, clear_pic))
        pf.layout().addLayout(pr)
        fr = QHBoxLayout()
        fl = W.label("Profiles folder", t, 14, "muted")
        fl.setFixedWidth(190)
        fr.addWidget(fl)
        fe = QLineEdit(s.profiles_dir)
        fe.setPlaceholderText(str(public.profiles_dir(config.Settings())))
        fe.editingFinished.connect(lambda: self._set("profiles_dir", fe.text().strip(), reload=True))
        fr.addWidget(fe, 1)
        fr.addWidget(button(t, "Open", False, lambda: W.open_folder(public.profiles_dir(self.settings))))
        pf.layout().addLayout(fr)
        pf.layout().addWidget(W.label("Your public profile (name, picture, playtime and trophy progress — nothing else) is "
                                      "kept in this folder. Every other profile in it is shown as a friend, so share the "
                                      "folder with Syncthing or similar and you'll all see each other.", t, 12, "muted",
                                      wrap=True))
        lay.addWidget(pf)

        lay.addWidget(section(t, "osmosis' record"))
        mr = W.Panel(pad=18, spacing=8)
        ms = QCheckBox("Keep osmosis' own copy of saves too (playtime and trophies are always kept)")
        ms.setChecked(s.mirror_saves)
        ms.toggled.connect(lambda v: self._set_live(mirror_saves=v))
        mr.layout().addWidget(ms)
        mr.layout().addWidget(W.label("osmosis keeps its own record of your progress. If RPCS3 ever ends up with less "
                                      "(a reinstall, an old copy restored), osmosis asks before putting its data back.",
                                      t, 12, "muted", wrap=True))
        mr.layout().addWidget(button(t, "Check now", False, self.check_mirror))
        lay.addWidget(mr)

        lay.addWidget(section(t, "Locations & accounts"))
        f = W.Panel(pad=18, spacing=10)

        def field(label_text, attr, placeholder, secret=False):
            r = QHBoxLayout()
            l = W.label(label_text, t, 14, "muted")
            l.setFixedWidth(190)
            r.addWidget(l)
            e = QLineEdit(getattr(s, attr))
            e.setPlaceholderText(placeholder)
            if secret:
                e.setEchoMode(QLineEdit.PasswordEchoOnEdit)
            e.editingFinished.connect(lambda: self._set(attr, e.text().strip(), reload=True))
            r.addWidget(e, 1)
            f.layout().addLayout(r)

        rp = self.profile.rpcs3 if self.profile else None
        field("RPCS3 folder", "rpcs3_config_dir", str(rp.config_dir) if rp else "~/.config/rpcs3 (auto)")
        field("rpcs3 program", "rpcs3_binary", rpcs3_binary(config.Settings()) or "auto")
        field("SteamGridDB API key", "steamgriddb_api_key", "steamgriddb.com → Preferences → API", secret=True)
        field("Refresher program", "refresher_path", "auto (PATH, ~/Applications, ~/Downloads)")
        f.layout().addWidget(W.label("The API key is free and only used to download library art. It's never "
                                     "included in backups.", t, 12, "muted", wrap=True))
        lay.addWidget(f)
        lay.addStretch(1)
        return scroll(inner)

    def _apply_frame(self):
        s = self.settings
        own = s.own_frame
        self.backdrop.set_frame(own, s.frame_border, s.frame_radius)
        full = bool(self.windowState() & (Qt.WindowMaximized | Qt.WindowFullScreen))
        m = 0 if (not own or full) else max(s.frame_border, 6)
        self._outer.setContentsMargins(m, m if own else 0, m, m)
        self.titlebar.setVisible(own)
        self.titlebar.theme = self.theme
        self.titlebar.update()

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == e.Type.WindowStateChange and hasattr(self, "_outer"):
            self._apply_frame()

    def _set_many(self, **kw):
        self._set_live(**kw)

    def _set_live(self, **kw):
        """Save settings that don't change the layout and apply them in place -- no rebuild,
        so the page you're on stays exactly where it was."""
        s = config.load()
        for k, v in kw.items():
            setattr(s, k, v)
        config.save(s)
        self.settings = s
        from . import sounds as _snd
        _snd.CHOICES = dict(s.sound_choices)
        PLAYER.enabled = s.sounds
        self.backdrop.set_theme(self.theme, s.ribbon_month_colors, fixed_color=s.ribbon_color,
                                daylight=s.ribbon_daylight)
        self.backdrop.ribbon_strength = s.ribbon_opacity / 100
        if "own_frame" in kw:
            geo = self.geometry()
            self.setWindowFlag(Qt.FramelessWindowHint, s.own_frame)
            self.setAttribute(Qt.WA_TranslucentBackground, s.own_frame)
            self.setGeometry(geo)
            self.show()
        self._apply_frame()
        if "frame_radius" in kw or "own_frame" in kw:
            QApplication.instance().setStyleSheet(qss(self.theme, s.frame_radius if s.own_frame else 0))
        if getattr(self, "xmb", None) is not None:
            try:
                self.xmb.media_on = s.ribbon_media
                self.xmb.preview_bg = s.ribbon_trailer_bg
                if not s.ribbon_media:
                    self.xmb.media.stop()
                self.xmb.update()
            except RuntimeError:
                pass

    def _set(self, attr, value, retheme=False, reload=False):
        s = config.load()
        setattr(s, attr, value)
        config.save(s)
        self.settings = s
        if retheme:
            QTimer.singleShot(0, lambda: self.apply_theme(reload=False))
        elif reload:
            QTimer.singleShot(0, self.reload)

    def closeEvent(self, e):
        self.watcher.release()
        super().closeEvent(e)


class WinButton(QPushButton):
    def __init__(self, role, win):
        super().__init__()
        self.role, self.win = role, win
        self.setFixedSize(34, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip({"min": "Minimise", "max": "Maximise", "close": "Close"}[role])
        self.setStyleSheet("QPushButton { border: none; background: transparent; padding: 0; }")

    def paintEvent(self, _e):
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QPainter, QPen
        t = self.win.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        hover = self.underMouse()
        if hover:
            bg = QColor("#d9473d") if self.role == "close" else t.q("accent", 0.25)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r.adjusted(3, 2, -3, -2), 6, 6)
        col = QColor("#ffffff") if hover and self.role == "close" else t.q("text" if hover else "muted")
        p.setPen(QPen(col, 1.4))
        c, k = r.center(), 4.5
        if self.role == "min":
            p.drawLine(QPointF(c.x() - k, c.y()), QPointF(c.x() + k, c.y()))
        elif self.role == "max":
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(c.x() - k, c.y() - k, 2 * k, 2 * k))
        else:
            p.drawLine(QPointF(c.x() - k, c.y() - k), QPointF(c.x() + k, c.y() + k))
            p.drawLine(QPointF(c.x() + k, c.y() - k), QPointF(c.x() - k, c.y() + k))


class TitleBar(QWidget):
    """Drag to move, double-click to maximise; window buttons on the right."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.theme = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 4, 0)
        lay.setSpacing(0)
        lay.addStretch(1)
        for role in ("min", "max", "close"):
            b = WinButton(role, win)
            b.clicked.connect(lambda _c=False, r=role: self._act(r))
            lay.addWidget(b)
        self.setFixedHeight(26)

    def _act(self, role):
        if role == "min":
            self.win.showMinimized()
        elif role == "max":
            self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()
        else:
            self.win.close()

    def mousePressEvent(self, e):
        h = self.win.windowHandle()
        if e.button() == Qt.LeftButton and h is not None:
            h.startSystemMove()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._act("max")


class MirrorDialog(QDialog):
    """RPCS3 has less than osmosis' record: let the user choose what wins."""

    def __init__(self, win, theme: Theme, rp, diffs):
        super().__init__(win)
        from .. import mirror
        self.win, self.rp, self.diffs = win, rp, diffs
        self.setWindowTitle("osmosis has newer data")
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(W.label("RPCS3 is behind osmosis' record", theme, 20, display=True, bold=True))
        lay.addWidget(W.label("This can happen after reinstalling RPCS3, restoring an old copy, or another program "
                              "changing its files. Tick what osmosis should put back into RPCS3. Anything replaced is "
                              "kept in a safety folder.", theme, 14, "muted", wrap=True))
        self.boxes = []
        box = QWidget()
        bl = QVBoxLayout(box)
        for d in diffs:
            cb = QCheckBox(f"{d.title} — {d.detail}")
            cb.setChecked(True)
            bl.addWidget(cb)
            self.boxes.append((cb, d))
        sc = scroll(box)
        sc.setMinimumHeight(min(320, 40 + 32 * len(diffs)))
        lay.addWidget(sc)
        row = QHBoxLayout()
        row.addWidget(button(theme, "Put osmosis' data back", True, self._restore))
        row.addWidget(button(theme, "Keep RPCS3's", False, self._accept))
        row.addWidget(button(theme, "Ask me later", False, self._later))
        row.addStretch(1)
        lay.addLayout(row)
        self.setMinimumWidth(640)

    def _picked(self):
        return [d for cb, d in self.boxes if cb.isChecked()], [d for cb, d in self.boxes if not cb.isChecked()]

    def _restore(self):
        from .. import mirror
        if backup.rpcs3_running():
            self.win.say("Close RPCS3 first.", sound="error")
            return
        yes, no = self._picked()
        safety = mirror.restore(self.rp, yes) if yes else None
        if no:
            mirror.accept(self.rp, no)
        self.accept()
        self.win.say(f"Put back {len(yes)} item(s) into RPCS3." + (" RPCS3's old copies are in the safety folder." if safety else ""),
                     ("Safety folder", lambda: W.open_folder(safety)) if safety else None)
        QTimer.singleShot(0, self.win.reload)

    def _accept(self):
        from .. import mirror
        mirror.accept(self.rp, self.diffs)
        self.accept()
        self.win.say("Kept RPCS3's data; osmosis' record now matches it.")

    def _later(self):
        snoozed = getattr(self.win, "_mirror_snoozed", set())
        snoozed |= {(d.kind, d.key) for d in self.diffs}
        self.win._mirror_snoozed = snoozed
        self.reject()


class ConflictDialog(QDialog):
    def __init__(self, parent, theme: Theme, item):
        super().__init__(parent)
        self.setWindowTitle("Restore")
        self.setModal(True)
        self.result_value = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(W.label(item.title, theme, 20, display=True, bold=True))
        newer = {True: "The backup's copy is newer than yours.", False: "Your current copy is newer than the backup's.",
                 None: ""}[item.newer_in_backup]
        lay.addWidget(W.label("This is different from what's on this computer. " + newer + "\n"
                              "Whatever gets replaced is kept in a safety folder first.", theme, 14, "muted", wrap=True))
        row = QHBoxLayout()
        for text, val, primary in (("Replace", "replace", True), ("Skip", "skip", False),
                                   ("Replace all", "replace_all", False), ("Skip all", "skip_all", False)):
            b = button(theme, text, primary)
            b.clicked.connect(lambda _=False, v=val: self._pick(v))
            row.addWidget(b)
        lay.addLayout(row)
        self.setMinimumWidth(520)

    def _pick(self, v):
        self.result_value = v
        self.accept()

    def ask(self):
        self.exec()
        return self.result_value


def _local_username(p: Profile) -> str:
    try:
        return (p.rpcs3.user_home / "localusername").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("osmosis")
    app.setDesktopFileName("osmosis")
    W.load_fonts()
    w = Window()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
