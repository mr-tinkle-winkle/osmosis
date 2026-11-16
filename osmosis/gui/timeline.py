"""Per-game timeline of play sessions and trophy unlocks, drawn differently per theme."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .themes import Theme, qcolor

GRADE = {"platinum": "#cfe3ff", "gold": "#e8c25a", "silver": "#c0c6cf", "bronze": "#c4824f"}
ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
         (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def roman(n: int) -> str:
    out = ""
    for v, s in ROMAN:
        while n >= v:
            out += s
            n -= v
    return out


def dur(ms: int) -> str:
    m = ms // 60000
    return f"{m // 60}h {m % 60:02d}m" if m >= 60 else f"{m}m"


@dataclass
class Ev:
    when: datetime
    kind: str          # "session" | "trophy"
    label: str
    grade: str = ""
    ms: int = 0


def events_from(entry) -> list[Ev]:
    out = []
    for when, kind, obj in entry.timeline():
        if kind == "session":
            out.append(Ev(when, "session", dur(obj.played_ms), ms=obj.played_ms))
        else:
            out.append(Ev(when, "trophy", obj.name, obj.grade))
    return out


class Timeline(QWidget):
    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.ev: list[Ev] = []
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(200)

    def set_events(self, ev: list[Ev]):
        self.ev = ev
        style = self.theme.timeline
        n = len(ev)
        if style in ("shelf",):
            self.setMinimumHeight(max(160, 64 * min(10, len([e for e in ev if e.kind == "session"]) or len(self._days()))))
        elif style == "terminal":
            self.setMinimumHeight(max(140, 26 * min(16, n) + 20))
        elif style == "card_catalog":
            self.setMinimumHeight(max(180, 96 * math.ceil(min(n, 18) / 6) + 20))
        else:
            self.setMinimumHeight(230)
        self.update()

    # helpers ------------------------------------------------------------------------
    def _f(self, size=10, display=False, bold=False) -> QFont:
        f = QFont(self.theme.font_display if display else self.theme.font_body)
        f.setPixelSize(size)
        f.setBold(bold)
        return f

    def _span(self):
        t0 = min(e.when for e in self.ev)
        t1 = max(e.when for e in self.ev)
        span = max((t1 - t0).total_seconds(), 3600.0)
        return t0, span

    def _x(self, when, r: QRectF, t0, span):
        return r.left() + (when - t0).total_seconds() / span * r.width()

    def _days(self):
        days = {}
        for e in self.ev:
            days.setdefault(e.when.date(), []).append(e)
        return sorted(days.items())

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(24, 14, -24, -10)
        if not self.ev:
            p.setPen(self.theme.q("muted"))
            p.setFont(self._f(13))
            p.drawText(r, Qt.AlignCenter | Qt.TextWordWrap,
                       "Nothing here yet. Trophy unlocks show up on their own; play sessions are\n"
                       "recorded from the first time osmosis sees this game running.")
            return
        getattr(self, f"_p_{self.theme.timeline}", self._p_general)(p, r)

    # -- general ----------------------------------------------------------------------
    def _p_general(self, p, r):
        t0, span = self._span()
        axis = r.bottom() - 26
        p.setPen(QPen(self.theme.q("border"), 1.5))
        p.drawLine(QPointF(r.left(), axis), QPointF(r.right(), axis))
        maxms = max([e.ms for e in self.ev if e.kind == "session"] or [1])
        acc = self.theme.q("bar" if self.theme.c.bar else "accent")
        for e in self.ev:
            x = self._x(e.when, r, t0, span)
            if e.kind == "session":
                h = 18 + (axis - r.top() - 60) * e.ms / maxms
                p.setPen(Qt.NoPen)
                p.setBrush(acc)
                p.drawRoundedRect(QRectF(x - 4, axis - h, 8, h), 3, 3)
            else:
                p.setPen(QPen(self.theme.q("bg"), 2))
                p.setBrush(QColor(GRADE.get(e.grade, "#ccc")))
                rad = 7 if e.grade == "platinum" else 5
                p.drawEllipse(QPointF(x, r.top() + 22), rad, rad)
        self._end_labels(p, r, t0, span)

    def _end_labels(self, p, r, t0, span, color="muted"):
        from datetime import timedelta
        p.setPen(self.theme.q(color))
        p.setFont(self._f(11))
        p.drawText(QRectF(r.left(), r.bottom() - 18, 200, 18), Qt.AlignLeft, t0.strftime("%b %d, %Y"))
        p.drawText(QRectF(r.right() - 200, r.bottom() - 18, 200, 18), Qt.AlignRight,
                   (t0 + timedelta(seconds=span)).strftime("%b %d, %Y"))

    # -- ribbon -----------------------------------------------------------------------
    def _p_ribbon(self, p, r):
        t0, span = self._span()
        mid = r.center().y() - 6
        amp = r.height() * 0.18
        k = math.tau / (r.width() * 0.8)
        wave = lambda x: mid + amp * math.sin(k * (x - r.left()) + 0.6)
        for width, alpha in ((14, 18), (6, 40), (1.6, 150)):
            path = QPainterPath(QPointF(r.left(), wave(r.left())))
            for x in range(int(r.left()), int(r.right()) + 1, 6):
                path.lineTo(x, wave(x))
            p.setPen(QPen(QColor(255, 255, 255, alpha), width, Qt.SolidLine, Qt.RoundCap))
            p.drawPath(path)
        maxms = max([e.ms for e in self.ev if e.kind == "session"] or [1])
        for e in self.ev:
            x = self._x(e.when, r, t0, span)
            c = QPointF(x, wave(x))
            if e.kind == "session":
                rad = 4 + 9 * e.ms / maxms
                for k2, a in ((2.6, 30), (1.6, 70), (1.0, 230)):
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(255, 255, 255, a))
                    p.drawEllipse(c, rad * k2, rad * k2)
            else:
                col = QColor(GRADE.get(e.grade, "#fff"))
                self._star(p, c + QPointF(0, -26), 7 if e.grade == "platinum" else 5, col)
        self._end_labels(p, r, t0, span)

    def _star(self, p, c, s, col):
        path = QPainterPath()
        for i in range(8):
            rad = s if i % 2 == 0 else s * 0.35
            a = i * math.pi / 4 - math.pi / 2
            pt = QPointF(c.x() + math.cos(a) * rad, c.y() + math.sin(a) * rad)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath()
        p.setPen(Qt.NoPen)
        glow = QColor(col)
        glow.setAlpha(60)
        p.setBrush(glow)
        p.drawEllipse(c, s * 1.6, s * 1.6)
        p.setBrush(col)
        p.drawPath(path)

    # -- boilerworks: chart recorder ---------------------------------------------------
    def _p_boilerworks(self, p, r):
        paper = QRectF(r.left(), r.top() + 18, r.width(), r.height() - 36)
        p.setPen(QPen(QColor("#8a6330"), 2))
        p.setBrush(QColor("#efe2c4"))
        p.drawRect(paper)
        p.setPen(QPen(QColor(166, 50, 42, 40), 1))
        for i in range(1, 8):
            y = paper.top() + paper.height() * i / 8
            p.drawLine(QPointF(paper.left(), y), QPointF(paper.right(), y))
        for x in range(int(paper.left()), int(paper.right()), 28):
            p.drawLine(QPointF(x, paper.top()), QPointF(x, paper.bottom()))
        t0, span = self._span()
        total = sum(e.ms for e in self.ev if e.kind == "session") or 1
        acc = 0
        path = QPainterPath(QPointF(paper.left(), paper.bottom() - 4))
        for e in self.ev:
            x = self._x(e.when, paper, t0, span)
            if e.kind == "session":
                y_prev = paper.bottom() - 4 - (paper.height() - 12) * acc / total
                acc += e.ms
                y = paper.bottom() - 4 - (paper.height() - 12) * acc / total
                path.lineTo(x, y_prev)
                path.lineTo(x + 3, y)
            else:
                p.setPen(QPen(QColor("#3a2b1c"), 1.5))
                p.drawLine(QPointF(x, paper.top()), QPointF(x, paper.top() + 12))
                p.setFont(self._f(10, display=True, bold=True))
                p.drawText(QRectF(x - 8, paper.top() - 16, 16, 14), Qt.AlignCenter, e.grade[:1].upper())
        path.lineTo(paper.right(), path.currentPosition().y())
        p.setPen(QPen(QColor("#a6322a"), 2.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(self.theme.q("muted"))
        p.setFont(self._f(11, display=True))
        p.drawText(QRectF(r.left(), r.bottom() - 16, r.width(), 16), Qt.AlignLeft,
                   f"{t0:%d %b %Y}  ·  red trace = hours on the clock  ·  ticks = trophies")

    # -- trophy hall: plaques ----------------------------------------------------------
    def _p_trophy_hall(self, p, r):
        tro = [e for e in self.ev if e.kind == "trophy"][::-1]
        sess = [e for e in self.ev if e.kind == "session"]
        pw = 172
        n = max(1, int((r.width() + 14) // (pw + 14)))
        y = r.top() + 8
        for i, e in enumerate(tro[:n]):
            x = r.left() + i * (pw + 14)
            plaque = QRectF(x, y, pw, 132)
            g = QLinearGradient(plaque.topLeft(), plaque.bottomLeft())
            g.setColorAt(0, QColor("#efd48a"))
            g.setColorAt(1, QColor("#ad8436"))
            p.setPen(QPen(QColor("#6e4b2a"), 1))
            p.setBrush(g)
            p.drawRect(plaque)
            p.setPen(QPen(QColor(42, 22, 8, 120), 1))
            p.drawRect(plaque.adjusted(5, 5, -5, -5))
            p.setPen(QColor("#2a1608"))
            p.setFont(self._f(11))
            p.drawText(plaque.adjusted(10, 12, -10, 0), Qt.AlignHCenter | Qt.AlignTop, e.grade.upper())
            p.setFont(self._f(15, display=True, bold=True))
            p.drawText(plaque.adjusted(10, 34, -10, -34), Qt.AlignCenter | Qt.TextWordWrap, e.label)
            p.setFont(self._f(11))
            p.drawText(plaque.adjusted(10, 0, -10, -12), Qt.AlignHCenter | Qt.AlignBottom, f"Acquired {e.when:%d %B %Y}")
        p.setPen(self.theme.q("muted"))
        p.setFont(self._f(13, display=True))
        total = sum(e.ms for e in sess)
        p.drawText(QRectF(r.left(), r.bottom() - 40, r.width(), 20), Qt.AlignLeft,
                   f"{len(sess)} visits recorded · {dur(total)} in residence" if sess else "Visits are recorded from now on.")

    # -- shelf: chapters ----------------------------------------------------------------
    def _p_shelf(self, p, r):
        sess = [e for e in self.ev if e.kind == "session"]
        if sess:
            rows = []
            for i, s in enumerate(sess):
                nxt = sess[i + 1].when if i + 1 < len(sess) else datetime.max
                got = [e for e in self.ev if e.kind == "trophy" and s.when <= e.when < nxt]
                rows.append((i + 1, s.when, s.label, got))
        else:
            rows = [(i + 1, d, "", [e for e in evs if e.kind == "trophy"]) for i, (d, evs) in enumerate(self._days())]
            rows = [(n, datetime.combine(d, datetime.min.time()) if not isinstance(d, datetime) else d, l, g) for n, d, l, g in rows]
        y = r.top()
        for n, when, length, got in rows[::-1][:10]:
            row = QRectF(r.left(), y, r.width(), 56)
            p.setPen(Qt.NoPen)
            p.setBrush(self.theme.q("panel"))
            p.drawRect(row)
            p.setPen(self.theme.q("accent"))
            p.setFont(self._f(22, display=True))
            p.drawText(row.adjusted(14, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, f"Chapter {roman(n)}")
            p.setPen(self.theme.q("text"))
            p.setFont(self._f(13))
            detail = f"{when:%A, %d %B %Y}" + (f"  ·  {length}" if length else "")
            p.drawText(row.adjusted(220, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, detail)
            p.setPen(self.theme.q("muted"))
            names = ", ".join(e.label for e in got[:3]) + (" …" if len(got) > 3 else "")
            p.drawText(row.adjusted(0, 0, -14, 0), Qt.AlignVCenter | Qt.AlignRight, names or "—")
            y += 64

    # -- summit: elevation profile ------------------------------------------------------
    def _p_summit(self, p, r):
        days = self._days()
        chart = QRectF(r.left() + 6, r.top() + 22, r.width() - 12, r.height() - 54)
        total = max(1, sum(1 for e in self.ev if e.kind == "trophy"))
        acc = 0
        pts = [QPointF(chart.left(), chart.bottom())]
        n = len(days)
        for i, (_d, evs) in enumerate(days):
            acc += sum(1 for e in evs if e.kind == "trophy")
            x = chart.left() + (i + 0.5) / n * chart.width()
            pts.append(QPointF(x, chart.bottom() - (chart.height() * 0.9) * acc / total - 6))
        pts.append(QPointF(chart.right(), pts[-1].y()))
        area = QPainterPath(QPointF(chart.left(), chart.bottom()))
        for pt in pts:
            area.lineTo(pt)
        area.lineTo(chart.right(), chart.bottom())
        area.closeSubpath()
        g = QLinearGradient(chart.topLeft(), chart.bottomLeft())
        bar = self.theme.q("bar")
        g.setColorAt(0, bar)
        dim = QColor(bar)
        dim.setAlpha(60)
        g.setColorAt(1, dim)
        p.setPen(QPen(self.theme.q("text"), 1.6))
        p.setBrush(g)
        p.drawPath(area)
        p.setFont(self._f(11))
        step = max(1, n // 10)
        for i, pt in enumerate(pts[1:-1]):
            p.setPen(Qt.NoPen)
            p.setBrush(self.theme.q("accent"))
            tri = QPainterPath(QPointF(pt.x(), pt.y() - 9))
            tri.lineTo(pt.x() - 6, pt.y() + 2)
            tri.lineTo(pt.x() + 6, pt.y() + 2)
            tri.closeSubpath()
            p.drawPath(tri)
            if i % step == 0:
                p.setPen(self.theme.q("muted"))
                p.drawText(QRectF(pt.x() - 30, chart.bottom() + 4, 60, 16), Qt.AlignCenter, f"Day {i + 1}")
        top = pts[-2]
        p.setPen(QPen(self.theme.q("text"), 1.5))
        p.drawLine(top, top + QPointF(0, -22))
        p.setBrush(self.theme.q("accent"))
        p.setPen(Qt.NoPen)
        flag = QPainterPath(top + QPointF(0, -22))
        flag.lineTo(top + QPointF(14, -17))
        flag.lineTo(top + QPointF(0, -12))
        p.drawPath(flag)
        p.setPen(self.theme.q("muted"))
        p.drawText(QRectF(r.left(), r.top(), r.width(), 18), Qt.AlignLeft,
                   f"{n} days on the climb · elevation = waypoints (trophies) reached")

    # -- card catalog: stamps ------------------------------------------------------------
    def _p_card_catalog(self, p, r):
        ev = self.ev[::-1][:18]
        cols = 6
        cw = r.width() / cols
        for i, e in enumerate(ev):
            cx = r.left() + (i % cols + 0.5) * cw
            cy = r.top() + 48 + (i // cols) * 96
            seed = int(hashlib.md5(f"{e.when}{e.label}".encode()).hexdigest()[:4], 16)
            ang = (seed % 15) - 7
            col = self.theme.q("stamp") if e.kind == "trophy" else self.theme.q("text")
            p.save()
            p.translate(cx, cy)
            p.rotate(ang)
            box = QRectF(-cw / 2 + 10, -34, cw - 20, 68)
            pen = QPen(col, 2.5)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(box)
            p.drawRect(box.adjusted(4, 4, -4, -4))
            p.setFont(self._f(15, display=True))
            p.drawText(box.adjusted(6, 6, -6, -30), Qt.AlignCenter, f"{e.when:%d %b %Y}".upper())
            p.setFont(self._f(12, display=True))
            txt = f"★ {e.grade.upper()}" if e.kind == "trophy" else f"LOANED {e.label.upper()}"
            p.drawText(box.adjusted(6, 30, -6, -6), Qt.AlignCenter, txt)
            p.restore()

    # -- terminal: git log -----------------------------------------------------------------
    def _p_terminal(self, p, r):
        p.setFont(self._f(13))
        fm = QFontMetrics(p.font())
        y = r.top() + fm.ascent()
        for e in self.ev[::-1][:16]:
            h = hashlib.sha1(f"{e.when}{e.label}".encode()).hexdigest()[:7]
            p.setPen(QColor("#e5c07b"))
            p.drawText(QPointF(r.left(), y), f"* {h}")
            p.setPen(self.theme.q("muted"))
            p.drawText(QPointF(r.left() + 90, y), f"{e.when:%Y-%m-%d %H:%M}")
            p.setPen(self.theme.q("text"))
            msg = f"played {e.label}" if e.kind == "session" else f"unlock({e.grade}): {e.label}"
            p.drawText(QPointF(r.left() + 250, y), msg)
            y += 26
