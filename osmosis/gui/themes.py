"""Themes: colours, fonts, wording, backdrop, timeline style and sound set.

Every theme names the same set of words (WORD_KEYS) so the UI never has to know which
theme is active -- it just asks `t.w("export_all")`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtGui import QColor

WORD_KEYS = (
    "profile", "library", "steam", "backup", "settings",
    "export_all", "export_data", "export_trophies", "import", "play", "sync",
    "trophies", "hours", "recent", "timeline", "games",
)

BASE_WORDS = dict(
    profile="Profile", library="Library", steam="Steam Sync", backup="Backup", settings="Settings",
    export_all="Export All", export_data="Export Data", export_trophies="Export Trophies",
    import_="Restore", play="Play", sync="Sync now", trophies="Trophies", hours="hours",
    recent="Recently played", timeline="Timeline", games="games",
)


@dataclass(frozen=True)
class Colors:
    bg: str
    panel: str
    border: str
    text: str
    muted: str
    accent: str
    accent_text: str
    track: str
    bar: str = ""          # progress fill ("" = accent)
    stamp: str = "#b0413e"


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    blurb: str
    light: Colors | None
    dark: Colors | None
    font_body: str
    font_display: str
    display_upper: bool = False
    radius: int = 10
    backdrop: str = "plain"
    timeline: str = "general"
    sounds: str = ""
    words: dict = field(default_factory=dict)
    mode: str = "dark"      # resolved variant ("light" | "dark")

    # -- helpers -----------------------------------------------------------------
    @property
    def c(self) -> Colors:
        if self.mode == "light" and self.light:
            return self.light
        return self.dark or self.light

    @property
    def has_variants(self) -> bool:
        return bool(self.light and self.dark)

    def w(self, key: str) -> str:
        k = "import_" if key == "import" else key
        return self.words.get(k, BASE_WORDS.get(k, key))

    def q(self, name: str, alpha: float | None = None) -> QColor:
        return qcolor(getattr(self.c, name), alpha)


def qcolor(s: str, alpha: float | None = None) -> QColor:
    s = s.strip()
    if s.startswith("rgba"):
        r, g, b, a = [x.strip() for x in s[s.index("(") + 1:s.index(")")].split(",")]
        col = QColor(int(r), int(g), int(b))
        col.setAlphaF(float(a))
    else:
        col = QColor(s)
    if alpha is not None:
        col.setAlphaF(alpha)
    return col


# --------------------------------------------------------------------------------
THEMES: dict[str, Theme] = {}


def _add(t: Theme):
    THEMES[t.id] = t


_add(Theme(
    "ribbon", "Ribbon", "Flowing light over a colour that changes with the month and the time of day.",
    light=None,
    dark=Colors(bg="#0a2058", panel="rgba(255,255,255,0.10)", border="rgba(255,255,255,0.20)", text="#eef3ff",
                muted="#bcd4ff", accent="#ffffff", accent_text="#0a1d4d", track="rgba(255,255,255,0.16)"),
    font_body="Outfit", font_display="Outfit", radius=10, backdrop="ribbon", timeline="ribbon", sounds="ribbon",
    words=dict(import_="Restore Backup"),
))
_add(Theme(
    "general", "General", "Clean and native-looking; fits right in with Plasma.",
    light=Colors(bg="#f5f6f8", panel="#ffffff", border="#e3e6eb", text="#16181d", muted="#5b6270",
                 accent="#2f6fde", accent_text="#ffffff", track="#e3e6eb"),
    dark=Colors(bg="#121418", panel="#1d2026", border="#2a2e36", text="#eceef2", muted="#a1a8b5",
                accent="#5b93f5", accent_text="#0b1a33", track="#2f343d"),
    font_body="Inter", font_display="Inter", radius=12, backdrop="plain", timeline="general", sounds="general",
    words=dict(export_all="Export all", export_data="Export data", export_trophies="Export achievements",
               import_="Restore backup", trophies="Achievements"),
))
_add(Theme(
    "boilerworks", "Boilerworks", "Brass, rivets and pressure gauges.",
    light=None,
    dark=Colors(bg="#171310", panel="#241e18", border="#5a4126", text="#efe2c4", muted="#b9a27a",
                accent="#c99a4b", accent_text="#1b140d", track="#120e0b", bar="#d8642c"),
    font_body="Spectral", font_display="Cinzel", radius=4, backdrop="boilerworks", timeline="boilerworks",
    sounds="boilerworks",
    words=dict(profile="Engineer", library="Boiler Room", steam="Steam Lines", backup="Bottling", settings="Calibration",
               export_all="Bottle Everything", export_data="Bottle Data", export_trophies="Bottle Trophies",
               import_="Uncork a Bottle", play="Fire Up", sync="Open the Valves", trophies="Pressure",
               hours="hours on the clock", recent="Recently Fired", timeline="Pressure Log", games="engines"),
))
_add(Theme(
    "trophy_hall", "Trophy Hall", "A private museum: velvet walls, glass cases, brass plaques.",
    light=None,
    dark=Colors(bg="#23090f", panel="rgba(255,240,210,0.06)", border="#6e4b2a", text="#f3e7cf", muted="#cbb592",
                accent="#d8b65a", accent_text="#2a1608", track="#3a1a22"),
    font_body="Lora", font_display="Playfair Display", radius=2, backdrop="trophy_hall", timeline="trophy_hall",
    sounds="trophy_hall",
    words=dict(profile="The Collection", library="Exhibits", steam="Loans to Steam", backup="Archives", settings="Curator",
               export_all="Archive the Collection", export_data="Archive Records", export_trophies="Archive Honours",
               import_="Restore from Archive", play="Visit", sync="Arrange Loans", trophies="Honours",
               hours="hours in residence", recent="Recent Acquisitions", timeline="Gallery", games="works"),
))
_add(Theme(
    "shelf", "Shelf", "Your games as cases on a shelf.",
    light=None,
    dark=Colors(bg="#2a241f", panel="#3a322b", border="#5b4a3a", text="#f4ead9", muted="#c9b9a2",
                accent="#f0c98a", accent_text="#2a241f", track="#241e19", bar="#a5441f"),
    font_body="DM Sans", font_display="DM Serif Display", radius=4, backdrop="shelf", timeline="shelf", sounds="shelf",
    words=dict(profile="My Shelf", steam="Steam Shelf", backup="Storage", export_all="Box up everything",
               export_data="Box up data", export_trophies="Box up trophies", import_="Unpack a box",
               sync="Restock Steam", recent="Pulled out lately", timeline="Chapters"),
))
_add(Theme(
    "summit", "Summit", "Topographic maps; every game is a peak.",
    light=Colors(bg="#f4efe3", panel="rgba(255,255,255,0.55)", border="#d9cfb8", text="#24332a", muted="#5a6a5e",
                 accent="#b4471a", accent_text="#f4efe3", track="#e3dac6", bar="#4f6f57"),
    dark=Colors(bg="#0e1519", panel="rgba(255,255,255,0.05)", border="#23343a", text="#e9e4d6", muted="#9fb0a8",
                accent="#f08a4b", accent_text="#1a0c04", track="#1a262b", bar="#8fb59a"),
    font_body="Space Grotesk", font_display="Space Grotesk", radius=4, backdrop="summit", timeline="summit",
    sounds="summit",
    words=dict(profile="Climber", library="Range", steam="Basecamp Sync", backup="Caches", settings="Gear",
               export_all="Leave a full cache", export_data="Cache data", export_trophies="Cache trophies",
               import_="Dig up a cache", play="Climb", sync="Radio basecamp", trophies="Waypoints",
               hours="hours on rock", recent="Recent ascents", timeline="The climb", games="routes"),
))
_add(Theme(
    "card_catalog", "Card Catalog", "A library card, a typewriter and a rubber stamp.",
    light=Colors(bg="#8a5a33", panel="#f2e3c0", border="#c9a24a", text="#2b1d10", muted="#6b5232",
                 accent="#2b1d10", accent_text="#f2e3c0", track="#e2cfa6", stamp="#b0413e"),
    dark=Colors(bg="#24160c", panel="#2a251e", border="#4a3a24", text="#efe3c8", muted="#b3a68c",
                accent="#e9d8b0", accent_text="#2a251e", track="#3b352c", stamp="#e0655f"),
    font_body="Courier Prime", font_display="Special Elite", radius=0, backdrop="card_catalog",
    timeline="card_catalog", sounds="card_catalog",
    words=dict(profile="Patron", library="Holdings", steam="Interlibrary", backup="Stacks", settings="Circulation",
               export_all="File everything", export_data="File data", export_trophies="File trophies",
               import_="Retrieve from file", play="Check out", sync="Send to Interlibrary", trophies="Stamps",
               hours="hours on loan", recent="Recent loans", timeline="Loan history", games="volumes"),
))
_add(Theme(
    "terminal", "Terminal", "Green phosphor, scanlines, a blinking cursor.",
    light=None,
    dark=Colors(bg="#050a06", panel="rgba(125,255,154,0.03)", border="#1f4a2a", text="#7dff9a", muted="#4fa865",
                accent="#7dff9a", accent_text="#050a06", track="#0f2414"),
    font_body="JetBrains Mono", font_display="JetBrains Mono", radius=0, backdrop="terminal", timeline="terminal",
    sounds="terminal",
    words=dict(profile="1:profile", library="2:library", steam="3:steam-sync", backup="4:backup", settings="5:config",
               export_all="[ export --all ]", export_data="[ export --data ]", export_trophies="[ export --trophies ]",
               import_="[ import ]", play="[ run ]", sync="[ sync ]", trophies="trophies", hours="h",
               recent="games --recent", timeline="git log", games="games"),
))

ORDER = ["ribbon", "general", "boilerworks", "trophy_hall", "shelf", "summit", "card_catalog", "terminal"]


def resolve(theme_id: str, dark: bool) -> Theme:
    from dataclasses import replace
    t = THEMES.get(theme_id) or THEMES["ribbon"]
    mode = "dark" if dark or not t.light else "light"
    if not t.dark:
        mode = "light"
    return replace(t, mode=mode)


# -- Ribbon: month colour + time-of-day brightness ---------------------------------
# Day-time colours of the original monthly backgrounds (Jan grey ... Dec red).
MONTH_COLORS = ["#8c9099", "#b0a03a", "#7aa82c", "#cf86ab", "#2f8a3e", "#9a76d0",
                "#2aa3ad", "#3479d4", "#7444b6", "#c4962c", "#8a5a34", "#bb2e2e"]


def ribbon_color(now: datetime | None = None, follow_calendar: bool = True) -> QColor:
    """The month's colour, drifting toward next month's in the second half of the
    month, then dimmed by time of day (brightest ~10:00, darkest ~02:00)."""
    now = now or datetime.now()
    if not follow_calendar:
        return QColor("#1d4fa3")
    m = now.month - 1
    a, b = QColor(MONTH_COLORS[m]), QColor(MONTH_COLORS[(m + 1) % 12])
    mix = 0.0 if now.day <= 12 else min(1.0, (now.day - 12) / 19.0) * 0.6
    col = QColor.fromRgbF(*(a.getRgbF()[i] * (1 - mix) + b.getRgbF()[i] * mix for i in range(3)))
    h = now.hour + now.minute / 60.0
    # piecewise cosine: rises 02->10, falls 10->02
    if 2 <= h <= 10:
        f = (1 - math.cos(math.pi * (h - 2) / 8)) / 2
    else:
        hh = h - 10 if h > 10 else h + 14
        f = (1 + math.cos(math.pi * hh / 16)) / 2
    bright = 0.38 + 0.62 * f
    return QColor.fromRgbF(col.redF() * bright, col.greenF() * bright, col.blueF() * bright)
