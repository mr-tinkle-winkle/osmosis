"""Window backgrounds, one painter per theme.

Ribbon is drawn live (~30 fps): translucent wave *planes* -- each a band whose top and
bottom edges ride different phases so it twists as it drifts -- rendered at 1/5
resolution and scaled up smoothly, which blurs them into soft sheets; thin bright edges
are drawn crisp on top, plus drifting sparkles. Static themes are cached per size.
"""
from __future__ import annotations

import math
import random
import time
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import QWidget

from .themes import Theme, qcolor, ribbon_color

# (y as fraction of height, amplitude, thickness, top-phase, bottom-phase, speed, alpha)
RIBBON_BANDS = [
    (0.60, 0.075, 0.120, 0.0, 1.3, 0.050, 0.11),
    (0.62, 0.060, 0.080, 0.8, 2.6, -0.070, 0.14),
    (0.64, 0.085, 0.055, 1.9, 0.4, 0.090, 0.17),
    (0.58, 0.050, 0.160, 2.6, 4.1, -0.035, 0.07),
    (0.66, 0.095, 0.040, 3.4, 2.0, 0.060, 0.13),
]


class Backdrop(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self._theme: Theme | None = None
        self._month_colors = True
        self._cache: QPixmap | None = None
        self._cache_key = None
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        rnd = random.Random(7)
        self._sparks = [(rnd.random(), 0.52 + rnd.random() * 0.25, rnd.uniform(4, 9), rnd.uniform(0, 9),
                         rnd.choice((-1, 1)) * rnd.uniform(0.004, 0.012), rnd.uniform(1.0, 2.4)) for _ in range(28)]
        self._base_color = QColor("#1d4fa3")
        self._base_checked = 0.0

    def set_theme(self, theme: Theme, month_colors: bool = True, animate: bool = True):
        self._theme = theme
        self._month_colors = month_colors
        self._cache = None
        fps = {"ribbon": 33, "terminal": 50}.get(theme.backdrop)
        if fps and animate:
            self._timer.start(fps)
        else:
            self._timer.stop()
        self.update()

    # ------------------------------------------------------------------------------
    def paintEvent(self, _e):
        t = self._theme
        if not t:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        kind = t.backdrop
        if kind == "ribbon":
            self._paint_ribbon(p)
        elif kind == "terminal":
            self._paint_static(p)
            self._paint_terminal_scan(p)
        else:
            self._paint_static(p)
        p.end()

    # -- static themes (cached) ----------------------------------------------------
    def _paint_static(self, p: QPainter):
        key = (self._theme.id, self._theme.mode, self.width(), self.height())
        if self._cache is None or self._cache_key != key:
            pm = QPixmap(self.size())
            pm.fill(qcolor(self._theme.c.bg))
            q = QPainter(pm)
            q.setRenderHint(QPainter.Antialiasing)
            getattr(self, f"_bg_{self._theme.backdrop}", self._bg_plain)(q, QRectF(pm.rect()))
            q.end()
            self._cache, self._cache_key = pm, key
        p.drawPixmap(0, 0, self._cache)

    def _bg_plain(self, q, r):
        pass

    def _bg_boilerworks(self, q, r):
        g = QRadialGradient(r.width() * 0.7, r.height() * 0.2, max(r.width(), r.height()) * 0.9)
        g.setColorAt(0, QColor("#2c2520"))
        g.setColorAt(1, QColor("#120f0c"))
        q.fillRect(r, g)
        # pipes along the bottom
        for i, (y, th) in enumerate(((0.90, 26), (0.955, 14))):
            yy = r.height() * y
            lg = QLinearGradient(0, yy - th / 2, 0, yy + th / 2)
            lg.setColorAt(0, QColor(201, 154, 75, 30))
            lg.setColorAt(0.5, QColor(255, 220, 150, 20))
            lg.setColorAt(1, QColor(90, 60, 25, 35))
            q.fillRect(QRectF(0, yy - th / 2, r.width(), th), lg)
            for x in range(40 + i * 60, int(r.width()), 220):
                q.fillRect(QRectF(x, yy - th / 2 - 3, 14, th + 6), QColor(120, 85, 40, 45))
        q.setPen(Qt.NoPen)
        q.setBrush(QColor(201, 154, 75, 45))
        for x in range(18, int(r.width()), 48):
            q.drawEllipse(QPointF(x, 12), 3, 3)

    def _bg_trophy_hall(self, q, r):
        g = QRadialGradient(r.width() * 0.5, 0, max(r.width(), r.height()) * 1.1)
        g.setColorAt(0, QColor("#4a1626"))
        g.setColorAt(0.6, QColor("#23090f"))
        g.setColorAt(1, QColor("#140508"))
        q.fillRect(r, g)
        q.setPen(QPen(QColor(255, 220, 160, 10), 1))
        for x in range(0, int(r.width()), 18):
            q.drawLine(QPointF(x, 0), QPointF(x, r.height()))
        q.fillRect(QRectF(0, r.height() - 6, r.width(), 6), QColor(216, 182, 90, 60))

    def _bg_shelf(self, q, r):
        g = QLinearGradient(0, 0, 0, r.height())
        g.setColorAt(0, QColor("#3a322b"))
        g.setColorAt(1, QColor("#221d19"))
        q.fillRect(r, g)
        q.setPen(QPen(QColor(255, 240, 220, 8), 6))
        for x in range(0, int(r.width()), 42):
            q.drawLine(QPointF(x, 0), QPointF(x, r.height()))
        for y in (0.975,):
            yy = r.height() * y
            lg = QLinearGradient(0, yy, 0, yy + 18)
            lg.setColorAt(0, QColor("#8a5f38"))
            lg.setColorAt(1, QColor("#4a301b"))
            q.fillRect(QRectF(0, yy, r.width(), 18), lg)
            q.fillRect(QRectF(0, yy + 18, r.width(), 14), QColor(0, 0, 0, 70))

    def _bg_summit(self, q, r):
        dark = self._theme.mode == "dark"
        if dark:
            g = QRadialGradient(r.width() * 0.82, r.height() * 0.08, max(r.width(), r.height()))
            g.setColorAt(0, QColor("#1d2d3a"))
            g.setColorAt(1, QColor("#0b1115"))
            q.fillRect(r, g)
        line = QColor("#23343a") if dark else QColor("#d9cfb8")
        q.setPen(QPen(line, 1.2))
        q.setBrush(Qt.NoBrush)
        rnd = random.Random(3)
        for _ in range(4):
            cx, cy = rnd.uniform(0.1, 0.95) * r.width(), rnd.uniform(0.15, 0.95) * r.height()
            base = rnd.uniform(90, 220)
            seeds = [rnd.uniform(0, 6.28) for _ in range(3)]
            for ring in range(7):
                rad = base * (1 - ring * 0.13)
                path = QPainterPath()
                for i in range(73):
                    a = i / 72 * math.tau
                    wob = 1 + 0.18 * math.sin(3 * a + seeds[0]) + 0.1 * math.sin(5 * a + seeds[1])
                    pt = QPointF(cx + math.cos(a) * rad * wob * 1.4, cy + math.sin(a) * rad * wob)
                    path.moveTo(pt) if i == 0 else path.lineTo(pt)
                q.drawPath(path)
        if dark:
            q.setPen(Qt.NoPen)
            q.setBrush(QColor(233, 228, 214, 220))
            m = QPointF(r.width() - 90, 70)
            q.drawEllipse(m, 24, 24)
            q.setBrush(QColor("#1b2a35"))
            q.drawEllipse(m + QPointF(11, -8), 22, 22)
            q.setBrush(QColor(255, 255, 255, 120))
            for _ in range(60):
                q.drawEllipse(QPointF(rnd.random() * r.width(), rnd.random() * r.height() * 0.45), 0.8, 0.8)

    def _bg_card_catalog(self, q, r):
        dark = self._theme.mode == "dark"
        g = QLinearGradient(0, 0, r.width(), 0)
        a, b = (QColor("#24160c"), QColor("#33200f")) if dark else (QColor("#7a4c2a"), QColor("#8d5a33"))
        g.setColorAt(0, a); g.setColorAt(0.5, b); g.setColorAt(1, a)
        q.fillRect(r, g)
        rnd = random.Random(11)
        q.setBrush(Qt.NoBrush)
        for i in range(70):
            y0 = rnd.uniform(0, r.height())
            col = QColor(0, 0, 0, rnd.randint(14, 34)) if i % 3 else QColor(255, 220, 170, rnd.randint(6, 16))
            q.setPen(QPen(col, rnd.uniform(0.6, 2.2)))
            path = QPainterPath(QPointF(0, y0))
            amp, freq, ph = rnd.uniform(2, 9), rnd.uniform(0.002, 0.008), rnd.uniform(0, 6)
            for x in range(0, int(r.width()) + 20, 20):
                path.lineTo(x, y0 + amp * math.sin(x * freq + ph))
            q.drawPath(path)
        if dark:
            lamp = QRadialGradient(r.width() * 0.62, r.height() * 0.4, max(r.width(), r.height()) * 0.6)
            lamp.setColorAt(0, QColor(255, 190, 110, 40))
            lamp.setColorAt(1, QColor(0, 0, 0, 0))
            q.fillRect(r, lamp)

    def _bg_terminal(self, q, r):
        q.setPen(QPen(QColor(0, 0, 0, 70), 1))
        for y in range(0, int(r.height()), 3):
            q.drawLine(QPointF(0, y + 2), QPointF(r.width(), y + 2))
        vg = QRadialGradient(r.center(), max(r.width(), r.height()) * 0.75)
        vg.setColorAt(0, QColor(30, 80, 40, 30))
        vg.setColorAt(1, QColor(0, 0, 0, 140))
        q.fillRect(r, vg)

    def _paint_terminal_scan(self, p):
        h = self.height()
        y = ((time.monotonic() - self._t0) / 7.0 % 1.0) * (h + 160) - 160
        g = QLinearGradient(0, y, 0, y + 160)
        g.setColorAt(0, QColor(125, 255, 154, 0))
        g.setColorAt(0.5, QColor(125, 255, 154, 14))
        g.setColorAt(1, QColor(125, 255, 154, 0))
        p.fillRect(QRectF(0, y, self.width(), 160), g)

    # -- ribbon ----------------------------------------------------------------------
    def _ribbon_base(self) -> QColor:
        now = time.monotonic()
        if now - self._base_checked > 30:
            self._base_color = ribbon_color(datetime.now(), self._month_colors)
            self._base_checked = now
        return self._base_color

    def _paint_ribbon(self, p: QPainter):
        w, h = self.width(), self.height()
        t = time.monotonic() - self._t0
        base = self._ribbon_base()
        g = QLinearGradient(0, 0, w * 0.35, h)
        g.setColorAt(0, base.lighter(135))
        g.setColorAt(0.55, base)
        g.setColorAt(1, base.darker(320))
        p.fillRect(self.rect(), g)
        glow = QRadialGradient(w * 0.3, h * 0.22, max(w, h) * 0.6)
        glow.setColorAt(0, QColor(255, 255, 255, int(18 + 16 * math.sin(t * 0.45))))
        glow.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(self.rect(), glow)

        # soft sheets at 1/5 resolution, scaled up = blur
        sw, sh = max(1, w // 5), max(1, h // 5)
        img = QImage(sw, sh, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        q = QPainter(img)
        q.setRenderHint(QPainter.Antialiasing)
        q.scale(sw / w, sh / h)
        edges = []
        for (yf, amp, thick, p1, p2, speed, alpha) in RIBBON_BANDS:
            y0, a1, th = h * yf, h * amp, h * thick
            ph = t * speed * math.tau
            k = math.tau / (w * 0.9)
            bob = math.sin(t * 0.25 + p1) * h * 0.012
            top, bot = [], []
            for x in range(-20, w + 41, 12):
                top.append(QPointF(x, y0 + bob + a1 * math.sin(k * x + p1 + ph) + a1 * 0.25 * math.sin(2.3 * k * x + p2 - ph * 0.7)))
                bot.append(QPointF(x, y0 + bob + th + a1 * 0.8 * math.sin(k * x + p2 + ph * 1.15)))
            path = QPainterPath(top[0])
            for pt in top[1:]:
                path.lineTo(pt)
            for pt in reversed(bot):
                path.lineTo(pt)
            path.closeSubpath()
            lg = QLinearGradient(0, y0 - a1, 0, y0 + th + a1)
            lg.setColorAt(0, QColor(255, 255, 255, int(255 * alpha * 1.6)))
            lg.setColorAt(0.5, QColor(255, 255, 255, int(255 * alpha * 0.6)))
            lg.setColorAt(1, QColor(255, 255, 255, int(255 * alpha * 1.3)))
            q.fillPath(path, lg)
            edges.append(top)
        q.end()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(QRectF(0, 0, w, h), img)

        # crisp sheen on two edges
        for i, alpha in ((2, 0.38), (1, 0.22)):
            path = QPainterPath(edges[i][0])
            for pt in edges[i][1:]:
                path.lineTo(pt)
            p.setPen(QPen(QColor(255, 255, 255, int(255 * alpha)), 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        # sparkles
        p.setPen(Qt.NoPen)
        for (x0, y0, period, off, drift, rad) in self._sparks:
            ph = ((t + off) % period) / period
            a = math.sin(ph * math.pi) ** 2
            x = ((x0 + drift * (t + off)) % 1.0) * w
            y = (y0 - 0.02 * ph) * h
            p.setBrush(QColor(255, 255, 255, int(200 * a)))
            p.drawEllipse(QPointF(x, y), rad, rad)
