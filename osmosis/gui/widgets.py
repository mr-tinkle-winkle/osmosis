"""Shared painted widgets: panels, progress bars, game cards, hero banners, toasts."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QMimeData, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QGuiApplication, QLinearGradient, QPainter, QPainterPath,
                           QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QSizePolicy,
                               QVBoxLayout, QWidget)

from .themes import Theme, qcolor
from .timeline import GRADE


# -- fonts ----------------------------------------------------------------------------
def load_fonts():
    dirs = [d for d in os.environ.get("OSMOSIS_FONT_DIR", "").split(":") if d]
    dirs.append(str(Path(__file__).parent / "fonts"))
    for d in dirs:
        for p in Path(d).rglob("*"):
            if p.suffix.lower() in (".ttf", ".otf"):
                QFontDatabase.addApplicationFont(str(p))


def font(theme: Theme, px: int, display=False, bold=False, italic=False) -> QFont:
    f = QFont(theme.font_display if display else theme.font_body)
    f.setPixelSize(px)
    f.setBold(bold)
    f.setItalic(italic)
    return f


# -- clipboard ----------------------------------------------------------------------------
def copy_file_to_clipboard(path: Path):
    md = QMimeData()
    url = QUrl.fromLocalFile(str(path))
    md.setUrls([url])
    md.setData("x-special/gnome-copied-files", f"copy\n{url.toString()}".encode())
    md.setText(str(path))
    QGuiApplication.clipboard().setMimeData(md)


def open_folder(path: Path):
    subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def hours(ms: int) -> str:
    h = ms / 3_600_000
    return f"{h:.1f} h" if h >= 1 else f"{ms // 60000} min"


# -- panel ------------------------------------------------------------------------------------
class Panel(QFrame):
    def __init__(self, parent=None, pad=18, spacing=12, horizontal=False):
        super().__init__(parent)
        self.setObjectName("Panel")
        lay = QHBoxLayout(self) if horizontal else QVBoxLayout(self)
        lay.setContentsMargins(pad, pad, pad, pad)
        lay.setSpacing(spacing)


def label(text: str, theme: Theme, px=14, role="text", display=False, bold=False, wrap=False) -> QLabel:
    l = QLabel(text)
    l.setFont(font(theme, px, display, bold))
    l.setStyleSheet(f"color: {getattr(theme.c, role)}; background: transparent;")
    l.setWordWrap(wrap)
    return l


# -- progress bar -------------------------------------------------------------------------------
class Bar(QWidget):
    def __init__(self, theme: Theme, value: int = 0, height: int = 6, parent=None):
        super().__init__(parent)
        self.theme, self.value = theme, value
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, v: int):
        self.value = v
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        rad = 0 if self.theme.radius == 0 else r.height() / 2
        t = self.theme
        p.setPen(Qt.NoPen)
        p.setBrush(t.q("track"))
        p.drawRoundedRect(r, rad, rad)
        fill = QRectF(r.left(), r.top(), r.width() * max(0, min(100, self.value)) / 100, r.height())
        if fill.width() <= 0:
            return
        col = t.q("bar" if t.c.bar else "accent")
        if t.id == "terminal":
            p.setBrush(col)
            x = r.left()
            while x < fill.right():
                p.drawRect(QRectF(x, r.top(), min(6, fill.right() - x), r.height()))
                x += 8
            return
        if t.id == "boilerworks":
            g = QLinearGradient(fill.topLeft(), fill.topRight())
            g.setColorAt(0, QColor("#8a2f1f"))
            g.setColorAt(1, QColor("#d8642c"))
            p.setBrush(g)
        else:
            p.setBrush(col)
        p.drawRoundedRect(fill, rad, rad)


# -- cover art ------------------------------------------------------------------------------------
_COVER_CACHE: dict = {}


def placeholder_cover(title: str, size: QSize, theme: Theme) -> QPixmap:
    pm = QPixmap(size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    h = abs(hash(title)) % 360
    g = QLinearGradient(0, 0, size.width(), size.height())
    g.setColorAt(0, QColor.fromHsv(h, 120, 120))
    g.setColorAt(1, QColor.fromHsv((h + 40) % 360, 140, 45))
    p.fillRect(pm.rect(), g)
    p.setPen(QColor(255, 255, 255, 220))
    p.setFont(font(theme, max(12, size.width() // 8), display=True, bold=True))
    words = [w for w in title.split() if w[:1].isalnum()]
    p.drawText(pm.rect(), Qt.AlignCenter, "".join(w[0] for w in words[:3]).upper() or "?")
    p.end()
    return pm


def scaled_crop(src: QPixmap, size: QSize) -> QPixmap:
    s = src.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = (s.width() - size.width()) // 2
    y = (s.height() - size.height()) // 2
    return s.copy(x, y, size.width(), size.height())


def art_for(entry, steamlib, slot: str) -> Path | None:
    """Steam library art if synced, else the game's own PS3 art."""
    if steamlib:
        try:
            e = steamlib.entries_by_serial().get(entry.serial)
            if e is not None:
                p = steamlib.art_path(int(e.get("appid", 0)), slot)
                if p:
                    return p
        except Exception:
            pass
    g = entry.game
    return g.background if slot in ("hero", "wide") else g.icon


def cover_pixmap(entry, steamlib, size: QSize, theme: Theme, slot="cover") -> QPixmap:
    path = art_for(entry, steamlib, slot)
    key = (str(path), entry.serial, size.width(), size.height(), theme.id)
    if key in _COVER_CACHE:
        return _COVER_CACHE[key]
    pm = QPixmap(str(path)) if path else QPixmap()
    pm = scaled_crop(pm, size) if not pm.isNull() else placeholder_cover(entry.title, size, theme)
    _COVER_CACHE[key] = pm
    return pm


def clear_cover_cache():
    _COVER_CACHE.clear()


# -- game card -----------------------------------------------------------------------------------------
class GameCard(QAbstractButton):
    def __init__(self, entry, theme: Theme, steamlib=None, words=None, parent=None, w=190):
        super().__init__(parent)
        self.entry, self.theme, self.steamlib = entry, theme, steamlib
        self._hover = False
        self.cover_size = QSize(w - 16, int((w - 16) * 1.35))
        self.setFixedSize(w, self.cover_size.height() + 92)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{entry.title}\n{entry.serial}")

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        t = self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        rad = t.radius
        bg = t.q("panel")
        if self._hover:
            bg = bg.lighter(125) if bg.alpha() == 255 else qcolor(t.c.panel, min(1.0, bg.alphaF() * 2.2))
        p.setPen(QPen(t.q("accent") if self._hover else t.q("border"), 1.5 if self._hover else 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, rad, rad)
        cover = QRectF(8, 8, self.cover_size.width(), self.cover_size.height())
        pm = cover_pixmap(self.entry, self.steamlib, self.cover_size, t)
        path = QPainterPath()
        path.addRoundedRect(cover, max(0, rad - 4), max(0, rad - 4))
        p.save()
        p.setClipPath(path)
        p.drawPixmap(cover.topLeft(), pm)
        p.restore()
        self._decor(p, r, cover)
        p.setPen(t.q("text"))
        p.setFont(font(t, 14, display=t.id in ("trophy_hall", "card_catalog"), bold=True))
        title_r = QRectF(10, cover.bottom() + 8, r.width() - 20, 20)
        p.drawText(title_r, Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(self.entry.title, Qt.ElideRight, int(title_r.width())))
        p.setPen(t.q("muted"))
        p.setFont(font(t, 12))
        tro = self.entry.trophies
        sub = hours(self.entry.game.playtime_ms) + (f"  ·  {tro.unlocked}/{tro.total}" if tro else "")
        p.drawText(QRectF(10, cover.bottom() + 30, r.width() - 20, 18), Qt.AlignLeft | Qt.AlignVCenter, sub)
        bar = QRectF(10, cover.bottom() + 58, r.width() - 20, 5)
        p.setPen(Qt.NoPen)
        p.setBrush(t.q("track"))
        p.drawRoundedRect(bar, 2.5, 2.5)
        if self.entry.percent:
            p.setBrush(t.q("bar" if t.c.bar else "accent"))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), bar.width() * self.entry.percent / 100, bar.height()), 2.5, 2.5)

    def _decor(self, p, r, cover):
        t = self.theme.id
        if t == "boilerworks":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#c99a4b"))
            for pt in (r.topLeft() + QPointF(5, 5), r.topRight() + QPointF(-5, 5),
                       r.bottomLeft() + QPointF(5, -5), r.bottomRight() + QPointF(-5, -5)):
                p.drawEllipse(pt, 2.5, 2.5)
        elif t == "trophy_hall":
            p.setPen(QPen(QColor("#d8b65a"), 3))
            p.setBrush(Qt.NoBrush)
            p.drawRect(cover.adjusted(-2, -2, 2, 2))
        elif t == "shelf":
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawRect(QRectF(cover.left(), cover.top(), 9, cover.height()))
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawRect(QRectF(cover.left() + 9, cover.top(), 2, cover.height()))
        elif t == "card_catalog":
            p.setPen(Qt.NoPen)
            p.setBrush(self.theme.q("bg"))
            p.drawEllipse(QPointF(r.center().x(), r.bottom() - 6), 5, 5)
        elif t == "terminal":
            p.setPen(self.theme.q("accent"))
            p.setFont(font(self.theme, 13))
            p.drawText(QRectF(0, 0, r.width(), 10), Qt.AlignCenter, "")


# -- hero banner --------------------------------------------------------------------------------------
class Hero(QWidget):
    def __init__(self, entry, theme: Theme, steamlib=None, parent=None):
        super().__init__(parent)
        self.entry, self.theme, self.steamlib = entry, theme, steamlib
        self.setFixedHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        path = art_for(entry, steamlib, "hero")
        self._src = QPixmap(str(path)) if path else QPixmap()
        lp = art_for(entry, steamlib, "logo") if steamlib else None
        self._logo = QPixmap(str(lp)) if lp and "_logo" in str(lp) else QPixmap()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect())
        rad = self.theme.radius
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        p.setClipPath(path)
        if not self._src.isNull():
            p.drawPixmap(0, 0, scaled_crop(self._src, self.size()))
        else:
            h = abs(hash(self.entry.title)) % 360
            g0 = QLinearGradient(0, 0, r.width(), r.height())
            g0.setColorAt(0, QColor.fromHsv(h, 120, 120))
            g0.setColorAt(1, QColor.fromHsv((h + 40) % 360, 140, 40))
            p.fillRect(r, g0)
        g = QLinearGradient(0, r.height() * 0.35, 0, r.height())
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0, 200))
        p.fillRect(r, g)
        if not self._logo.isNull():
            lg = self._logo.scaled(QSize(int(r.width() * 0.35), 110), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(QPointF(28, r.height() - lg.height() - 24), lg)
        else:
            p.setPen(QColor("#ffffff"))
            p.setFont(font(self.theme, 40, display=True, bold=True))
            p.drawText(r.adjusted(28, 0, -28, -22), Qt.AlignLeft | Qt.AlignBottom, self.entry.title)


# -- trophy row ------------------------------------------------------------------------------------------
class TrophyRow(QWidget):
    def __init__(self, trophy, theme: Theme, parent=None):
        super().__init__(parent)
        self.t, self.theme = trophy, theme
        self.setFixedHeight(58)
        self._icon = QPixmap(str(trophy.icon)) if trophy.icon else QPixmap()

    def paintEvent(self, _e):
        t, th = self.t, self.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0, 3, 0, -3)
        p.setPen(Qt.NoPen)
        p.setBrush(th.q("panel"))
        p.drawRoundedRect(r, th.radius / 2, th.radius / 2)
        icon = QRectF(r.left() + 6, r.top() + 4, r.height() - 8, r.height() - 8)
        if not self._icon.isNull():
            p.setOpacity(1.0 if t.unlocked else 0.3)
            p.drawPixmap(icon.toRect(), self._icon)
            p.setOpacity(1.0)
        p.setBrush(QColor(GRADE.get(t.grade, "#aaa")) if t.unlocked else th.q("track"))
        p.drawEllipse(QPointF(icon.right() + 16, r.center().y()), 6, 6)
        hidden = t.hidden and not t.unlocked
        p.setPen(th.q("text") if t.unlocked else th.q("muted"))
        p.setFont(font(th, 14, bold=True))
        p.drawText(QRectF(icon.right() + 32, r.top() + 4, r.width() - 300, 22), Qt.AlignLeft | Qt.AlignVCenter,
                   "Hidden trophy" if hidden else t.name)
        p.setPen(th.q("muted"))
        p.setFont(font(th, 12))
        detail = "" if hidden else t.detail
        p.drawText(QRectF(icon.right() + 32, r.top() + 25, r.width() - 300, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(detail, Qt.ElideRight, int(r.width() - 320)))
        right = t.unlocked_at.astimezone().strftime("%b %d, %Y  %H:%M") if t.unlocked_at else "Locked"
        p.drawText(QRectF(r.right() - 220, r.top(), 210, r.height()), Qt.AlignRight | Qt.AlignVCenter,
                   f"{t.grade.title()}  ·  {right}")


# -- toast -------------------------------------------------------------------------------------------------
class Toast(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("Toast")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 12, 18, 12)
        self.text = QLabel()
        self.text.setWordWrap(True)
        lay.addWidget(self.text, 1)
        self.action_btn = None
        self._eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._eff)
        self._anim = QPropertyAnimation(self._eff, b"opacity", self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.fade)
        self.hide()

    def show_message(self, msg: str, action: tuple | None = None, ms: int = 6000):
        from PySide6.QtWidgets import QPushButton
        self.text.setText(msg)
        if self.action_btn:
            self.action_btn.deleteLater()
            self.action_btn = None
        if action:
            self.action_btn = QPushButton(action[0])
            self.action_btn.clicked.connect(action[1])
            self.layout().addWidget(self.action_btn)
        self.adjustSize()
        par = self.parentWidget()
        w = min(640, par.width() - 60)
        self.setFixedWidth(w)
        self.adjustSize()
        self.move((par.width() - w) // 2, par.height() - self.height() - 28)
        self.raise_()
        self.show()
        self._anim.stop()
        self._anim.setDuration(180)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(ms)

    def fade(self):
        self._anim.stop()
        self._anim.setDuration(400)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.InQuad)
        self._anim.finished.connect(self._hide_once)
        self._anim.start()

    def _hide_once(self):
        try:
            self._anim.finished.disconnect(self._hide_once)
        except (RuntimeError, TypeError):
            pass
        if self._eff.opacity() < 0.05:
            self.hide()


class Blink(QLabel):
    """Terminal cursor."""

    def __init__(self, theme: Theme, parent=None):
        super().__init__("█", parent)
        self.setStyleSheet(f"color: {theme.c.accent}; background: transparent;")
        self._on = True
        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(550)

    def _tick(self):
        self._on = not self._on
        self.setText("█" if self._on else " ")


class Avatar(QWidget):
    """Round picture (from a file or base64 PNG/JPEG) or the name's first letter."""

    def __init__(self, theme: Theme, name: str, image=None, size: int = 72, parent=None):
        super().__init__(parent)
        import base64
        self.theme, self.name = theme, name
        self.setFixedSize(size, size)
        self._pm = QPixmap()
        try:
            if isinstance(image, (str, Path)) and Path(image).is_file():
                self._pm = QPixmap(str(image))
            elif isinstance(image, str) and image:
                self._pm.loadFromData(base64.b64decode(image))
        except Exception:
            self._pm = QPixmap()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        rad = r.width() / 2 if self.theme.radius >= 8 else self.theme.radius
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        p.setClipPath(path)
        if not self._pm.isNull():
            p.drawPixmap(r.toRect(), scaled_crop(self._pm, r.size().toSize()))
        else:
            p.fillRect(r, self.theme.q("accent"))
            p.setPen(self.theme.q("accent_text"))
            p.setFont(font(self.theme, int(r.height() * 0.42), display=True, bold=True))
            p.drawText(r, Qt.AlignCenter, (self.name[:1] or "?").upper())
