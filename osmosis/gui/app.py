"""The osmosis window."""
from __future__ import annotations

import subprocess
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPalette
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
class _Job(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.progress.emit))
        except Exception as e:  # surfaced in the UI, never crashes the window
            traceback.print_exc()
            self.failed.emit(str(e))


def run_bg(owner, fn, on_done, on_fail=None, on_progress=None):
    th = QThread(owner)
    job = _Job(fn)
    job.moveToThread(th)
    th.started.connect(job.run)
    job.done.connect(on_done)
    if on_fail:
        job.failed.connect(on_fail)
    if on_progress:
        job.progress.connect(on_progress)
    job.done.connect(th.quit)
    job.failed.connect(th.quit)
    th.finished.connect(th.deleteLater)
    owner._jobs = getattr(owner, "_jobs", []) + [(th, job)]
    th.start()


# -- stylesheet ----------------------------------------------------------------------------------
def qss(t: Theme) -> str:
    c = t.c
    r = t.radius
    upper = "uppercase" if t.display_upper else "none"
    sheet = (f"QWidget#Sheet {{ background: {c.panel}; border-left: 1px solid {c.border}; }}"
             if t.id == "card_catalog" else "QWidget#Sheet { background: transparent; }")
    return f"""
    QWidget {{ color: {c.text}; }}
    QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget, QStackedWidget {{ background: transparent; }}
    QFrame#Panel {{ background: {c.panel}; border: 1px solid {c.border}; border-radius: {r}px; }}
    QFrame#Side {{ background: {c.panel}; border: none; border-right: 1px solid {c.border}; }}
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
        app.setStyleSheet(qss(t))
        pal = app.palette()
        pal.setColor(QPalette.Window, QColor(t.c.bg))
        pal.setColor(QPalette.WindowText, QColor(t.c.text))
        app.setPalette(pal)
        self.backdrop.set_theme(t, self.settings.ribbon_month_colors)
        PLAYER.enabled = self.settings.sounds
        PLAYER.set_name = t.sounds
        if reload:
            self.reload()
        else:
            self.build()

    # -- layout -----------------------------------------------------------------------------------------
    def build(self):
        old = self.backdrop.layout()
        if old:
            QWidget().setLayout(old)  # reparent+discard the previous tree
        t = self.theme
        top = t.backdrop == "ribbon"
        root = QVBoxLayout() if top else QHBoxLayout()
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.backdrop.setLayout(root)

        pages = [("profile", self.page_profile), ("library", self.page_library), ("steam", self.page_steam),
                 ("backup", self.page_backup), ("misc", self.page_misc), ("settings", self.page_settings)]
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
                nl.addSpacing(26)
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
        name = _local_username(p) or "Player"
        head = QHBoxLayout()
        avatar = QLabel(name[:1].upper())
        avatar.setFixedSize(72, 72)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setFont(W.font(t, 30, display=True, bold=True))
        avatar.setStyleSheet(f"background: {t.c.accent}; color: {t.c.accent_text}; "
                             f"border-radius: {36 if t.radius >= 8 else t.radius}px;")
        head.addWidget(avatar)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(h1(t, name, 32))
        c = p.trophy_counts
        col.addWidget(W.label(f"{len(p.entries)} {t.w('games')} · {W.hours(p.total_playtime_ms)} · "
                              f"{p.trophies_unlocked} / {p.trophies_total} {t.w('trophies').lower()}", t, 15, "muted"))
        head.addLayout(col, 1)
        head.addSpacing(16)
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
        lay.addStretch(1)
        lay.addWidget(self.export_bar())
        return scroll(inner)

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
            QTimer.singleShot(0, self.reload)

        run_bg(self, work, done, lambda err: self.say(f"Restore failed: {err}", sound="error"))

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
                         (lambda: subprocess.Popen([ref], start_new_session=True)) if ref else
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
        snd.toggled.connect(lambda v: self._set("sounds", v, retheme=True))
        p.layout().addWidget(snd)
        mc = QCheckBox("Ribbon follows the calendar (colour by month, brightness by time of day)")
        mc.setChecked(s.ribbon_month_colors)
        mc.toggled.connect(lambda v: self._set("ribbon_month_colors", v, retheme=True))
        p.layout().addWidget(mc)
        md = QCheckBox("Ribbon plays each game's menu music and preview video when you rest on it (needs ffmpeg)")
        md.setChecked(s.ribbon_media)
        md.toggled.connect(lambda v: self._set("ribbon_media", v, retheme=True))
        p.layout().addWidget(md)
        lay.addWidget(p)

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
