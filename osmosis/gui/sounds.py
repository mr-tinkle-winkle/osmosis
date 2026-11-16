"""Original UI sounds, synthesised on first use (no recorded audio is shipped).

Events: "nav" (moving between pages/items), "select" (pressing a button),
"done" (an export/sync finished), "error".
"""
from __future__ import annotations

import math
import random
import struct
import wave
from pathlib import Path

from .. import config

RATE = 44100


def _env(n, attack=0.004, decay=0.12):
    a = max(1, int(attack * RATE))
    out = []
    for i in range(n):
        t = i / RATE
        e = (i / a) if i < a else math.exp(-(t - attack) / decay)
        out.append(e)
    return out


def _tone(freqs, dur, decay=0.12, amp=0.3, attack=0.004, vibrato=0.0, square=False):
    n = int(dur * RATE)
    env = _env(n, attack, decay)
    out = []
    for i in range(n):
        t = i / RATE
        s = 0.0
        for j, f in enumerate(freqs):
            ff = f * (1 + vibrato * math.sin(2 * math.pi * 5.5 * t))
            v = math.sin(2 * math.pi * ff * t)
            if square:
                v = 1.0 if v >= 0 else -1.0
            s += v / (j + 1)
        out.append(s * env[i] * amp / max(1, len(freqs) ** 0.5))
    return out


def _noise(dur, decay=0.05, amp=0.4, lowpass=0.5, seed=1, attack=0.001):
    rnd = random.Random(seed)
    n = int(dur * RATE)
    env = _env(n, attack, decay)
    out, y = [], 0.0
    for i in range(n):
        y += lowpass * (rnd.uniform(-1, 1) - y)
        out.append(y * env[i] * amp)
    return out


def _mix(*tracks, offsets=None):
    offsets = offsets or [0.0] * len(tracks)
    n = max(int(o * RATE) + len(t) for t, o in zip(tracks, offsets))
    out = [0.0] * n
    for t, o in zip(tracks, offsets):
        s = int(o * RATE)
        for i, v in enumerate(t):
            out[s + i] += v
    return out


def _seq(notes, step, **kw):
    return _mix(*[_tone(f if isinstance(f, (list, tuple)) else [f], **kw) for f in notes],
                offsets=[i * step for i in range(len(notes))])


SETS = {
    "ribbon": {
        "nav": lambda: _tone([1760, 2640], 0.09, decay=0.035, amp=0.16),
        "select": lambda: _seq([1318, 1976], 0.06, dur=0.35, decay=0.14, amp=0.18),
        "done": lambda: _seq([988, 1318, 1568, 1976], 0.08, dur=0.6, decay=0.25, amp=0.15),
        "error": lambda: _seq([440, 330], 0.1, dur=0.3, decay=0.12, amp=0.2),
    },
    "general": {
        "nav": lambda: _noise(0.03, decay=0.008, amp=0.18, lowpass=0.6),
        "select": lambda: _tone([880], 0.08, decay=0.03, amp=0.15),
        "done": lambda: _seq([784, 1175], 0.07, dur=0.35, decay=0.14, amp=0.15),
        "error": lambda: _tone([300], 0.2, decay=0.08, amp=0.2),
    },
    "boilerworks": {
        "nav": lambda: _mix(_noise(0.06, decay=0.012, amp=0.35, lowpass=0.25, seed=3), _tone([110], 0.08, decay=0.03, amp=0.3)),
        "select": lambda: _noise(0.45, decay=0.2, amp=0.22, lowpass=0.9, seed=5, attack=0.03),
        "done": lambda: _mix(_tone([880, 1320], 0.9, decay=0.6, amp=0.16, attack=0.08, vibrato=0.006),
                             _noise(0.9, decay=0.5, amp=0.08, lowpass=0.9, seed=9, attack=0.05)),
        "error": lambda: _mix(_noise(0.15, decay=0.05, amp=0.4, lowpass=0.15), _tone([70], 0.2, decay=0.1, amp=0.4)),
    },
    "trophy_hall": {
        "nav": lambda: _noise(0.05, decay=0.015, amp=0.25, lowpass=0.12, seed=2),
        "select": lambda: _tone([1568, 2352, 3136], 0.7, decay=0.35, amp=0.15),
        "done": lambda: _seq([[523, 659], [659, 784], [784, 1047]], 0.13, dur=0.9, decay=0.45, amp=0.16),
        "error": lambda: _tone([220, 233], 0.4, decay=0.2, amp=0.2),
    },
    "shelf": {
        "nav": lambda: _mix(_noise(0.04, decay=0.01, amp=0.3, lowpass=0.35, seed=4), _tone([620], 0.05, decay=0.015, amp=0.12)),
        "select": lambda: _mix(_noise(0.03, decay=0.006, amp=0.4, lowpass=0.7, seed=6),
                               _noise(0.04, decay=0.01, amp=0.3, lowpass=0.5, seed=7), offsets=[0, 0.07]),
        "done": lambda: _noise(0.5, decay=0.25, amp=0.18, lowpass=0.08, seed=8, attack=0.06),
        "error": lambda: _tone([180], 0.2, decay=0.08, amp=0.25),
    },
    "summit": {
        "nav": lambda: _noise(0.035, decay=0.01, amp=0.25, lowpass=0.4, seed=12),
        "select": lambda: _tone([3100, 4700, 6200], 0.25, decay=0.08, amp=0.12),
        "done": lambda: _mix(_noise(1.1, decay=0.6, amp=0.1, lowpass=0.05, seed=13, attack=0.3),
                             _seq([660, 990], 0.12, dur=0.5, decay=0.25, amp=0.12), offsets=[0, 0.2]),
        "error": lambda: _tone([250], 0.25, decay=0.1, amp=0.2),
    },
    "card_catalog": {
        "nav": lambda: _mix(_noise(0.025, decay=0.006, amp=0.45, lowpass=0.85, seed=14), _tone([2100], 0.03, decay=0.01, amp=0.08)),
        "select": lambda: _mix(_noise(0.12, decay=0.03, amp=0.5, lowpass=0.1, seed=15), _tone([90], 0.12, decay=0.05, amp=0.35)),
        "done": lambda: _tone([2400, 3600], 1.2, decay=0.5, amp=0.14),
        "error": lambda: _mix(_noise(0.025, decay=0.006, amp=0.45, lowpass=0.85), _noise(0.025, decay=0.006, amp=0.45, lowpass=0.85),
                              offsets=[0, 0.08]),
    },
    "terminal": {
        "nav": lambda: _noise(0.012, decay=0.003, amp=0.35, lowpass=0.9, seed=16),
        "select": lambda: _mix(_noise(0.012, decay=0.003, amp=0.35, lowpass=0.9), _noise(0.012, decay=0.003, amp=0.3, lowpass=0.9),
                               offsets=[0, 0.05]),
        "done": lambda: _tone([880], 0.14, decay=0.5, amp=0.1, square=True),
        "error": lambda: _seq([220, 220], 0.16, dur=0.12, decay=0.5, amp=0.1, square=True),
    },
}


def sound_file(set_name: str, event: str) -> Path | None:
    gen = SETS.get(set_name, {}).get(event)
    if not gen:
        return None
    p = config.data_dir() / "sounds" / "v1" / set_name / f"{event}.wav"
    if not p.is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        samples = gen()
        peak = max(1e-6, max(abs(s) for s in samples))
        scale = min(1.0, 0.9 / peak)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s * scale)) * 32000)) for s in samples))
    return p


class Player:
    """Plays a theme's sounds through QSoundEffect; silently does nothing if Qt
    Multimedia isn't available."""

    def __init__(self):
        self._effects = {}
        self.enabled = True
        self.set_name = ""
        try:
            from PySide6.QtMultimedia import QSoundEffect  # noqa: F401
            self._ok = True
        except Exception:
            self._ok = False

    def play(self, event: str):
        if not (self._ok and self.enabled and self.set_name):
            return
        key = (self.set_name, event)
        eff = self._effects.get(key)
        if eff is None:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect
            f = sound_file(self.set_name, event)
            if not f:
                return
            eff = QSoundEffect()
            eff.setSource(QUrl.fromLocalFile(str(f)))
            eff.setVolume(0.6)
            self._effects[key] = eff
        eff.play()


PLAYER = Player()
