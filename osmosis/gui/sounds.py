"""Original UI sounds, synthesised on first use (no recorded audio is shipped).

Events: "nav" (moving between pages/items), "select" (pressing a button),
"done" (an export/sync finished), "error".
"""
from __future__ import annotations

import math
import random
import struct
import threading
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
    dur = max(dur, attack + decay * 7)          # let the decay actually finish (no cut-off)
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
    dur = max(dur, attack + decay * 7)
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


def _glide(f0, f1, dur, decay=0.08, amp=0.3, attack=0.003):
    dur = max(dur, attack + decay * 7)
    n = int(dur * RATE)
    env = _env(n, attack, decay)
    out, ph = [], 0.0
    for i in range(n):
        f = f0 + (f1 - f0) * (i / n)
        ph += 2 * math.pi * f / RATE
        out.append(math.sin(ph) * env[i] * amp)
    return out


def _bell(f, dur, decay=0.4, amp=0.25):
    # inharmonic partials of a small bell
    return _mix(*[_tone([f * r], dur, decay=decay / (1 + i * 0.6), amp=amp / (1 + i)) for i, r in enumerate((1, 2.76, 5.4))])


def _sparkle(seed=3, n=7, amp=0.12):
    rnd = random.Random(seed)
    notes = [_tone([rnd.choice((1976, 2349, 2637, 2960, 3136, 3520))], 0.25, decay=0.06, amp=amp) for _ in range(n)]
    return _mix(*notes, offsets=[i * 0.045 + rnd.uniform(0, 0.02) for i in range(n)])


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


def _pluck(f, dur=1.0, amp=0.35, damp=0.996, seed=5, bright=0.5):
    """Karplus-Strong plucked string: a natural, gently fading pluck."""
    rnd = random.Random(seed)
    period = max(2, int(RATE / f))
    buf = [rnd.uniform(-1, 1) for _ in range(period)]
    for _ in range(int(3 * (1 - bright)) + 1):      # soften the initial burst
        buf = [(buf[i] + buf[i - 1]) * 0.5 for i in range(period)]
    out = []
    for i in range(int(dur * RATE)):
        v = buf[i % period]
        nxt = buf[(i + 1) % period]
        buf[i % period] = damp * 0.5 * (v + nxt)
        out.append(v * amp)
    return out


def _fm(fc, ratio, index, dur, decay=0.3, amp=0.25, attack=0.002):
    """Two-operator FM: bells, glass, gongs."""
    dur = max(dur, attack + decay * 7)
    n = int(dur * RATE)
    env = _env(n, attack, decay)
    out = []
    for i in range(n):
        t = i / RATE
        mod = index * env[i] * math.sin(2 * math.pi * fc * ratio * t)
        out.append(math.sin(2 * math.pi * fc * t + mod) * env[i] * amp)
    return out


def _verb(x, mix=0.3, room=0.8):
    """Small reverb (four combs + an all-pass) so sounds bloom and fade instead of stopping."""
    src = list(x) + [0.0] * int(RATE * 0.8)
    out = [0.0] * len(src)
    for d, g in ((1116, room), (1188, room - 0.02), (1277, room - 0.04), (1356, room - 0.06)):
        buf = [0.0] * d
        idx = 0
        for i, v in enumerate(src):
            y = buf[idx]
            buf[idx] = v + y * g
            idx = (idx + 1) % d
            out[i] += y * 0.25
    buf, idx, wet = [0.0] * 556, 0, []
    for v in out:
        y = buf[idx]
        buf[idx] = v + y * 0.5
        wet.append(y - v * 0.5)
        idx = (idx + 1) % 556
    return [d * (1 - mix) + w * mix for d, w in zip(src, wet)]


def _finish(x, fade_in=0.002, fade_out=0.08):
    """Trim trailing silence, then fade the last stretch to zero: no clicks, no cut-offs."""
    end = len(x)
    while end > 0 and abs(x[end - 1]) < 1e-4:
        end -= 1
    x = x[:end] or [0.0]
    fi, fo = int(fade_in * RATE), min(len(x) // 2, int(fade_out * RATE))
    for i in range(min(fi, len(x))):
        x[i] *= i / fi
    for i in range(fo):
        x[-1 - i] *= i / fo
    return x


def _soft(gen):
    return lambda: _verb(gen())


# Alternatives to pick from per event (Settings > Sounds). The default stays the one in SETS.
VARIANTS = {
    "ribbon": {
        "nav": {
            "Glass tick": SETS["ribbon"]["nav"],
            "Soft tick": lambda: _mix(_noise(0.02, decay=0.004, amp=0.22, lowpass=0.55, seed=21),
                                      _tone([2900], 0.03, decay=0.008, amp=0.08)),
            "Bubble": lambda: _glide(820, 1350, 0.07, decay=0.03, amp=0.22),
            "Pluck": lambda: _pluck(1318, 0.35, amp=0.28, damp=0.992),
            "Air": lambda: _mix(_noise(0.09, decay=0.03, amp=0.12, lowpass=0.18, seed=22, attack=0.02),
                                _tone([1976], 0.06, decay=0.02, amp=0.05)),
            "Droplet": lambda: _glide(1900, 1100, 0.06, decay=0.025, amp=0.2),
            "Marimba": lambda: _mix(_tone([880], 0.2, decay=0.05, amp=0.25), _tone([880 * 3.93], 0.1, decay=0.012, amp=0.08)),
            "Felt tap": lambda: _mix(_noise(0.04, decay=0.008, amp=0.3, lowpass=0.08, seed=24), _tone([520], 0.06, decay=0.02, amp=0.12)),
            "Crystal": lambda: _fm(2637, 3.5, 2.0, 0.2, decay=0.04, amp=0.16),
            "Sonar": _soft(lambda: _tone([1100], 0.08, decay=0.02, amp=0.18)),
        },
        "select": {
            "Chime": SETS["ribbon"]["select"],
            "Whoosh + ping": lambda: _mix(_noise(0.22, decay=0.08, amp=0.14, lowpass=0.12, seed=23, attack=0.06),
                                          _tone([1976, 2960], 0.4, decay=0.14, amp=0.14), offsets=[0, 0.1]),
            "Bell": lambda: _bell(1568, 0.7, decay=0.35, amp=0.2),
            "Rise": lambda: _mix(_glide(600, 1200, 0.16, decay=0.2, amp=0.16), _tone([1568], 0.35, decay=0.12, amp=0.12),
                                 offsets=[0, 0.12]),
            "Glass harp": _soft(lambda: _mix(_tone([1046], 0.6, decay=0.25, amp=0.14, attack=0.03),
                                             _tone([1568], 0.6, decay=0.25, amp=0.1, attack=0.03), offsets=[0, 0.05])),
            "Kalimba": lambda: _mix(_pluck(784, 0.9, amp=0.3, damp=0.997, bright=0.2),
                                    _pluck(1175, 0.9, amp=0.24, damp=0.997, seed=6, bright=0.2), offsets=[0, 0.07]),
            "Shimmer": _soft(lambda: _sparkle(seed=9, n=5, amp=0.1)),
            "Soft gong": _soft(lambda: _fm(392, 1.4, 3.0, 1.0, decay=0.35, amp=0.18)),
            "Bloom": _soft(lambda: _mix(*[_tone([f], 0.7, decay=0.22, amp=0.1, attack=0.01) for f in (784, 988, 1175)])),
            "Click + ring": lambda: _mix(_noise(0.02, decay=0.004, amp=0.25, lowpass=0.6, seed=25),
                                         _fm(1760, 2.0, 1.2, 0.5, decay=0.12, amp=0.14), offsets=[0, 0.012]),
        },
        "done": {
            "Arpeggio": SETS["ribbon"]["done"],
            "Pad swell": lambda: _mix(*[_tone([f], 1.4, decay=0.6, amp=0.1, attack=0.18) for f in (523, 659, 784, 1047)]),
            "Sparkle": lambda: _sparkle(),
            "Two bells": lambda: _mix(_bell(1318, 0.9, amp=0.16), _bell(1976, 0.9, amp=0.14), offsets=[0, 0.12]),
            "Fanfare": _soft(lambda: _seq([[523, 659], [659, 784], [784, 1047, 1318]], 0.11, dur=0.7, decay=0.3, amp=0.12)),
            "Sweep + chime": lambda: _mix(_glide(300, 1500, 0.35, decay=0.25, amp=0.1, attack=0.2), _bell(1976, 0.9, amp=0.14),
                                          offsets=[0, 0.3]),
            "Music box": _soft(lambda: _mix(*[_pluck(f, 0.9, amp=0.22, damp=0.998, seed=i, bright=0.1)
                                              for i, f in enumerate((1047, 1319, 1568, 2093))],
                                            offsets=[i * 0.12 for i in range(4)])),
            "Choir pad": _soft(lambda: _mix(*[_tone([f], 1.3, decay=0.5, amp=0.08, attack=0.25, vibrato=0.004)
                                              for f in (440, 554, 659, 880)])),
            "Glass cascade": _soft(lambda: _mix(*[_fm(f, 3.5, 1.5, 0.4, decay=0.1, amp=0.1) for f in (3136, 2637, 2349, 1976, 1568)],
                                                offsets=[i * 0.06 for i in range(5)])),
            "Soft confirm": _soft(lambda: _seq([784, 1175], 0.13, dur=0.5, decay=0.18, amp=0.15)),
        },
        "error": {
            "Two notes": SETS["ribbon"]["error"],
            "Thud": lambda: _mix(_tone([95], 0.25, decay=0.08, amp=0.4), _noise(0.06, decay=0.02, amp=0.2, lowpass=0.1)),
            "Fall": lambda: _glide(700, 330, 0.25, decay=0.12, amp=0.22),
            "Soft buzz": lambda: _tone([180], 0.18, decay=0.06, amp=0.12, square=True),
            "Double tap": lambda: _mix(_noise(0.04, decay=0.01, amp=0.3, lowpass=0.1, seed=26),
                                       _noise(0.04, decay=0.01, amp=0.3, lowpass=0.1, seed=27), offsets=[0, 0.11]),
            "Low bell": _soft(lambda: _bell(392, 0.8, decay=0.3, amp=0.18)),
            "Detuned": lambda: _tone([330, 349], 0.4, decay=0.15, amp=0.2),
            "Drop": lambda: _mix(_glide(500, 160, 0.2, decay=0.1, amp=0.22), _noise(0.05, decay=0.02, amp=0.1, lowpass=0.1)),
            "Wobble": lambda: _tone([260], 0.4, decay=0.15, amp=0.2, vibrato=0.04),
            "Hollow knock": lambda: _mix(_fm(220, 1.5, 2.5, 0.25, decay=0.05, amp=0.25),
                                         _noise(0.03, decay=0.008, amp=0.15, lowpass=0.2, seed=28)),
        },
    },
}
CHOICES: dict[str, str] = {}   # "set:event" -> variant; filled from settings by the window


def options(set_name: str, event: str) -> list[str]:
    return list(VARIANTS.get(set_name, {}).get(event, {}))


def sound_file(set_name: str, event: str, variant: str | None = None) -> Path | None:
    variant = variant if variant is not None else CHOICES.get(f"{set_name}:{event}", "")
    gen = VARIANTS.get(set_name, {}).get(event, {}).get(variant) if variant else None
    gen = gen or SETS.get(set_name, {}).get(event)
    if not gen:
        return None
    slug = "".join(c for c in (variant or "default").lower() if c.isalnum())
    p = config.data_dir() / "sounds" / "v3" / set_name / f"{event}-{slug}.wav"
    if not p.is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        samples = _finish(list(gen()))
        peak = max(1e-6, max(abs(s) for s in samples))
        scale = min(1.0, 0.9 / peak)
        tmp = p.with_suffix(f".{threading.get_ident()}.tmp")
        with wave.open(str(tmp), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s * scale)) * 32000)) for s in samples))
        tmp.replace(p)
    return p


class Player:
    """Plays a theme's sounds. Uses PipeWire's pw-play (or paplay) as a separate
    process, which works on any PipeWire/Pulse desktop without Qt Multimedia's own
    audio backend; falls back to QSoundEffect if neither tool is found."""

    def __init__(self):
        import shutil
        self._effects = {}
        self.enabled = True
        self.set_name = ""
        self._last = {}
        self._cmd = None
        for exe, args in (("pw-play", ["--volume", "0.7"]), ("paplay", [])):
            path = shutil.which(exe)
            if path:
                self._cmd = [path, *args]
                break
        try:
            from PySide6.QtMultimedia import QSoundEffect  # noqa: F401
            self._qt = True
        except Exception:
            self._qt = False
        self.last_error = ""

    def warm(self):
        """Synthesise the current theme's chosen sounds ahead of time (some take a few
        hundred ms), so the first click isn't late."""
        name = self.set_name

        def work():
            for ev in ("nav", "select", "done", "error"):
                try:
                    sound_file(name, ev)
                except Exception:
                    pass

        threading.Thread(target=work, daemon=True).start()

    def play(self, event: str, variant: str | None = None, force: bool = False):
        import subprocess
        import time
        if not (self.set_name and (self.enabled or force)):
            return
        now = time.monotonic()
        if now - self._last.get(event, 0) < 0.06:   # don't stack identical clicks
            return
        self._last[event] = now
        try:
            f = sound_file(self.set_name, event, variant)
        except OSError as e:
            self.last_error = str(e)
            return
        if not f:
            return
        if self._cmd:
            try:
                subprocess.Popen([*self._cmd, str(f)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return
            except OSError as e:
                self.last_error = str(e)
        if self._qt:
            from PySide6.QtCore import QUrl
            from PySide6.QtMultimedia import QSoundEffect
            key = (self.set_name, event, variant)
            eff = self._effects.get(key)
            if eff is None:
                eff = QSoundEffect()
                eff.setSource(QUrl.fromLocalFile(str(f)))
                eff.setVolume(0.7)
                self._effects[key] = eff
            eff.play()


PLAYER = Player()
