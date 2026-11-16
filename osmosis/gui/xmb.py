"""Ribbon's library: a vertical list of games in the style of the PS3 menu.

The highlighted game sits large at a fixed spot; the rest slide past it, smaller and
dimmer. Keys, wheel or clicks move the highlight (hovering does nothing); staying on a
game for a moment brings in its PS3 background art (PIC1), starts its menu music (SND0)
and plays its animated icon / mini trailer (ICON1) if it has one.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget

from . import widgets as W
from .media import GameMedia
from .sounds import PLAYER

ICON = QSize(320, 176)          # PS3 ICON0 aspect
ROW = 74                        # spacing between unselected rows
GAP = 92                        # extra space around the highlighted row
DWELL_MS = 1400


class XmbList(QWidget):
    open_game = Signal(str)
    play_game = Signal(str)

    def __init__(self, entries, theme, backdrop, settings, parent=None):
        super().__init__(parent)
        self.entries = entries
        self.theme = theme
        self.backdrop = backdrop
        self.media_on = settings.ribbon_media
        self.preview_bg = settings.ribbon_trailer_bg
        self.index = 0
        self.offset = 0.0
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(220)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._set_offset)
        self._dwell = QTimer(self)
        self._dwell.setSingleShot(True)
        self._dwell.timeout.connect(self._activate)
        self._wheel_acc = 0
        self._wheel_last = 0.0
        self.media = GameMedia(self)
        self.media.frame_changed.connect(self.update)
        self._icons: dict[str, QPixmap] = {}
        self._rects: list[tuple[int, QRectF]] = []
        if entries:
            self._dwell.start(DWELL_MS)

    # -- data ----------------------------------------------------------------------------------
    def _icon(self, e) -> QPixmap:
        pm = self._icons.get(e.serial)
        if pm is None:
            src = QPixmap(str(e.game.icon)) if e.game.icon else QPixmap()
            pm = W.scaled_crop(src, ICON) if not src.isNull() else W.placeholder_cover(e.title, ICON, self.theme)
            self._icons[e.serial] = pm
        return pm

    # -- movement --------------------------------------------------------------------------------
    def select(self, i: int, sound=True):
        if not self.entries:
            return
        i = max(0, min(len(self.entries) - 1, i))
        if i == self.index:
            return
        self.index = i
        if sound:
            PLAYER.play("nav")
        self._anim.stop()
        self._anim.setStartValue(self.offset)
        self._anim.setEndValue(float(i))
        self._anim.start()
        self.stop_media()
        self._dwell.start(DWELL_MS)

    def _set_offset(self, v):
        self.offset = float(v)
        self.update()

    def _activate(self):
        if not self.entries or not self.isVisible():
            return
        e = self.entries[self.index]
        bg = e.game.background
        if bg:
            self.backdrop.set_overlay(QPixmap(str(bg)))
        if self.media_on:
            self.media.play(e.game)

    def stop_media(self):
        self._dwell.stop()
        self.media.stop()
        self.backdrop.set_overlay(None)

    def hideEvent(self, e):
        self.stop_media()
        super().hideEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        self.setFocus()
        if self.entries:
            self._dwell.start(DWELL_MS)

    def keyPressEvent(self, e):
        k = e.key()
        if k in (Qt.Key_Down, Qt.Key_S, Qt.Key_J):
            self.select(self.index + 1)
        elif k in (Qt.Key_Up, Qt.Key_W, Qt.Key_K):
            self.select(self.index - 1)
        elif k in (Qt.Key_PageDown,):
            self.select(self.index + 5)
        elif k in (Qt.Key_PageUp,):
            self.select(self.index - 5)
        elif k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and self.entries:
            PLAYER.play("select")
            self.open_game.emit(self.entries[self.index].serial)
        else:
            super().keyPressEvent(e)

    def wheelEvent(self, e):
        """One step per full wheel notch. Touchpads and smooth-scrolling wheels send a
        stream of tiny deltas (and keep sending after you let go); those are added up
        instead of each moving the list."""
        e.accept()
        self._wheel_step(e.angleDelta().y(), e.phase() == Qt.ScrollMomentum)

    def _wheel_step(self, dy: int, momentum: bool = False):
        import time
        if momentum:                             # touchpad coasting after release
            return
        self._wheel_acc += dy
        now = time.monotonic()
        if abs(self._wheel_acc) >= 120 and now - self._wheel_last > 0.09:
            self.select(self.index + (1 if self._wheel_acc < 0 else -1))
            self._wheel_acc = 0
            self._wheel_last = now

    def _hit(self, pos) -> int | None:
        for i, r in self._rects:
            if r.contains(pos):
                return i
        return None

    def mouseMoveEvent(self, e):
        # Hovering never changes anything; it only shows that a game can be clicked.
        self.setCursor(Qt.PointingHandCursor if self._hit(e.position()) is not None else Qt.ArrowCursor)

    def mousePressEvent(self, e):
        i = self._hit(e.position())
        if i is None:
            return
        if i == self.index:
            PLAYER.play("select")
            self.open_game.emit(self.entries[i].serial)
        else:
            self.select(i)

    # -- painting --------------------------------------------------------------------------------
    def _y_for(self, d: float, cy: float) -> float:
        gap = GAP * max(-1.0, min(1.0, d))
        return cy + d * ROW + gap

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        t = self.theme
        self._rects = []
        if not self.entries:
            p.setPen(QColor(255, 255, 255, 200))
            p.setFont(W.font(t, 18))
            p.drawText(self.rect(), Qt.AlignCenter, "No games found in RPCS3.")
            return
        if self.preview_bg and self.media.frame is not None:
            img = self.media.frame.scaled(self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            p.setOpacity(0.55)
            p.drawImage((self.width() - img.width()) // 2, (self.height() - img.height()) // 2, img)
            p.setOpacity(1.0)
        x0 = 252
        cy = self.height() * 0.40
        lo = int(self.offset) - 6
        hi = int(self.offset) + 9
        for i in range(max(0, lo), min(len(self.entries), hi)):
            e = self.entries[i]
            d = i - self.offset
            y = self._y_for(d, cy)
            focus = max(0.0, 1.0 - abs(d))          # 1 at the highlight, 0 one row away
            w = 112 + (240 - 112) * focus
            h = w * ICON.height() / ICON.width()
            r = QRectF(x0 - w / 2 + 56, y - h / 2, w, h)
            alpha = 1.0 if abs(d) < 0.5 else max(0.0, 0.62 - 0.08 * (abs(d) - 1))
            if y < -h or y > self.height() + h:
                continue
            p.setOpacity(alpha)
            icon = (self.media.frame if (not self.preview_bg and i == self.index and self.media.frame is not None
                                         and abs(d) < 0.05) else None)
            if icon is not None:
                img = icon.scaled(r.size().toSize(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                sx = (img.width() - r.width()) / 2
                sy = (img.height() - r.height()) / 2
                p.drawImage(r, img, QRectF(sx, sy, r.width(), r.height()))
            else:
                p.drawPixmap(r.toRect(), self._icon(e))
            # text
            tx = r.right() + 26
            p.setPen(QColor(255, 255, 255))
            f = W.font(t, int(16 + 8 * focus))
            f.setWeight(QFont.Weight.Normal if focus > 0.5 else QFont.Weight.Light)
            p.setFont(f)
            p.drawText(QRectF(tx, y - 30 if focus > 0.5 else y - 14, self.width() - tx - 40, 30),
                       Qt.AlignLeft | Qt.AlignVCenter, e.title)
            if focus > 0.5:
                p.setOpacity(alpha * focus)
                p.setPen(QColor(220, 232, 255))
                p.setFont(W.font(t, 14))
                bits = [W.hours(e.game.playtime_ms) + " played"]
                if e.trophies:
                    bits.append(f"{e.trophies.unlocked}/{e.trophies.total} trophies · {e.percent}%")
                if e.game.last_played:
                    bits.append("last played " + e.game.last_played.strftime("%b %d, %Y"))
                p.drawText(QRectF(tx, y + 2, self.width() - tx - 40, 22), Qt.AlignLeft | Qt.AlignVCenter, "   ·   ".join(bits))
                p.setFont(W.font(t, 12))
                p.setPen(QColor(200, 215, 245, 200))
                p.drawText(QRectF(tx, y + 28, self.width() - tx - 40, 20), Qt.AlignLeft | Qt.AlignVCenter,
                           "Enter / click to open   ·   ↑ ↓ / scroll to move")
            self._rects.append((i, QRectF(r.left(), r.top(), self.width() - r.left() - 40, r.height())))
        p.setOpacity(1.0)
