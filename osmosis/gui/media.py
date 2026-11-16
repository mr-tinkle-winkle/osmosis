"""Game music (SND0.AT3) and animated icon (ICON1.PAM), as the PS3 menu plays them.

* ICON1.PAM is the PS3's own "mini trailer": a 320x176 video in Sony's PAMF container
  (a header, then an MPEG program stream whose video stream 0xE0 carries H.264).
  ffmpeg doesn't read PAMF, so osmosis pulls the H.264 out itself (`pamf_extract_h264`)
  and lets ffmpeg wrap it as MP4.
* SND0.AT3 is ATRAC3 in a RIFF/WAVE file. Like the PS3, playback honours the loop
  points in its `smpl` chunk: play the intro once, then repeat only the loop region,
  instead of jumping back to the very start.

Converted files are cached in ~/.local/share/osmosis/media/<serial>/; what happened is
logged to ~/.local/share/osmosis/media/log.txt.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QImage

from .. import config

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
    HAVE_QTMM = True
except Exception:  # pragma: no cover
    HAVE_QTMM = False

CACHE_VERSION = "v2"


def cache_dir(serial: str) -> Path:
    return config.data_dir() / "media" / CACHE_VERSION / serial


def log(msg: str):
    try:
        p = config.data_dir() / "media" / "log.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except OSError:
        pass


# -- PAMF -------------------------------------------------------------------------------------
def pamf_extract_h264(data: bytes) -> bytes:
    """Concatenate the payloads of video PES packets (stream 0xE0..0xEF) from a PAMF file."""
    start = 0
    if data[:4] == b"PAMF" and len(data) >= 12:
        units = struct.unpack(">I", data[8:12])[0]
        if 0 < units * 2048 < len(data):
            start = units * 2048
    i = data.find(b"\x00\x00\x01\xba", start)
    if i < 0:
        i = start
    out = bytearray()
    n = len(data)
    while i + 4 <= n:
        if data[i:i + 3] != b"\x00\x00\x01":
            j = data.find(b"\x00\x00\x01", i + 1)
            if j < 0:
                break
            i = j
            continue
        sid = data[i + 3]
        if sid == 0xBA:                                   # pack header
            if i + 14 > n:
                break
            if (data[i + 4] & 0xC0) == 0x40:              # MPEG-2
                stuffing = data[i + 13] & 0x07
                i += 14 + stuffing
            else:                                         # MPEG-1
                i += 12
            continue
        if sid == 0xB9:                                   # end code
            break
        if i + 6 > n:
            break
        length = struct.unpack(">H", data[i + 4:i + 6])[0]
        body = data[i + 6:i + 6 + length]
        if 0xE0 <= sid <= 0xEF and len(body) >= 3 and (body[0] & 0xC0) == 0x80:
            hdr = body[2]
            out += body[3 + hdr:]
        i += 6 + length
    return bytes(out)


# -- AT3 loop points -------------------------------------------------------------------------------
@dataclass
class Loop:
    start_ms: int
    end_ms: int


def at3_loop(data: bytes) -> Loop | None:
    """Loop region from a RIFF/WAVE `smpl` chunk (sample positions at the file's rate)."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return None
    pos, rate, loop = 12, 0, None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt " and len(body) >= 8:
            rate = struct.unpack("<I", body[4:8])[0]
        elif cid == b"smpl" and len(body) >= 36:
            count = struct.unpack("<I", body[28:32])[0]
            if count and len(body) >= 36 + 24:
                _cue, _type, start, end = struct.unpack("<IIII", body[36:52])
                loop = (start, end)
        pos += 8 + size + (size & 1)
    if loop and rate and loop[1] > loop[0]:
        return Loop(int(loop[0] * 1000 / rate), int(loop[1] * 1000 / rate))
    return None


# -- conversion -----------------------------------------------------------------------------------
def _ff(args: list[str]) -> bool:
    try:
        r = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"ffmpeg failed to run: {e}")
        return False
    if r.returncode != 0:
        log("ffmpeg: " + " ".join(args[1:]) + "\n    " + r.stderr.decode("utf-8", "replace")[-600:].replace("\n", "\n    "))
    return r.returncode == 0


def convert(game) -> tuple[Path | None, Path | None, Loop | None]:
    """(music, video, loop) as playable files, converting on first use."""
    ff = shutil.which("ffmpeg")
    if not ff:
        log("ffmpeg not found on PATH: no game music or previews")
        return None, None, None
    d = cache_dir(game.serial)
    d.mkdir(parents=True, exist_ok=True)
    music = video = loop = None

    if game.music:
        raw = game.music.read_bytes()
        loop = at3_loop(raw)
        out = d / "music.ogg"
        if not out.is_file():
            tmp = d / "music.tmp.ogg"
            if _ff([ff, "-y", "-loglevel", "error", "-i", str(game.music), "-vn", "-c:a", "libvorbis", "-q:a", "6", str(tmp)]):
                tmp.replace(out)
            elif _ff([ff, "-y", "-loglevel", "error", "-i", str(game.music), "-vn", "-c:a", "flac",
                      str(d / "music.tmp.flac")]):
                (d / "music.tmp.flac").replace(out)
        music = out if out.is_file() else None
        log(f"{game.serial}: SND0 -> {'ok' if music else 'FAILED'}; loop {loop}")
    else:
        log(f"{game.serial}: no SND0.AT3 in {game.sfo_dir}")

    if game.preview:
        out = d / "preview.mp4"
        if not out.is_file():
            h264 = d / "preview.h264"
            es = pamf_extract_h264(game.preview.read_bytes())
            ok = False
            tmp = d / "preview.tmp.mp4"
            if len(es) > 1000:
                h264.write_bytes(es)
                ok = (_ff([ff, "-y", "-loglevel", "error", "-r", "29.97", "-f", "h264", "-i", str(h264), "-c:v", "copy", str(tmp)])
                      or _ff([ff, "-y", "-loglevel", "error", "-f", "h264", "-i", str(h264), "-c:v", "libx264",
                              "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(tmp)]))
                h264.unlink(missing_ok=True)
            if not ok:  # last resort: maybe it isn't PAMF at all
                ok = _ff([ff, "-y", "-loglevel", "error", "-i", str(game.preview), "-an", "-c:v", "libx264",
                          "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(tmp)])
            if ok:
                tmp.replace(out)
        video = out if out.is_file() else None
        log(f"{game.serial}: ICON1 -> {'ok' if video else 'FAILED'}")
    else:
        log(f"{game.serial}: no ICON1.PAM in {game.sfo_dir}")
    return music, video, loop


class _Track(QObject):
    """One music player that can fade in and out on its own, so the previous game's
    music fades away while the next one fades up -- never a hard cut."""

    def __init__(self, path: Path, loop: Loop | None, parent):
        super().__init__(parent)
        self.loop = loop
        self.out = QAudioOutput(self)
        self.out.setVolume(0.0)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.out)
        self.player.mediaStatusChanged.connect(self._status)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.target = 0.0
        self.step = 0.04
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(25)
        self.dead = False

    def fade_to(self, v: float, seconds: float):
        self.target = v
        self.step = max(0.005, abs(v - self.out.volume()) / max(1, seconds / 0.025))

    def _tick(self):
        # volume ramp
        v = self.out.volume()
        if abs(v - self.target) > 1e-3:
            v = min(self.target, v + self.step) if v < self.target else max(self.target, v - self.step)
            self.out.setVolume(v)
        elif self.target == 0.0 and self.dead:
            self._timer.stop()
            self.player.stop()
            self.deleteLater()
            return
        # loop region, checked every 25 ms (position signals can be too coarse)
        l = self.loop
        if l and self.player.position() >= l.end_ms - 20:
            self.player.setPosition(l.start_ms)

    def _status(self, status):
        if status == QMediaPlayer.EndOfMedia and not self.dead:
            self.player.setPosition(self.loop.start_ms if self.loop else 0)
            self.player.play()

    def retire(self, seconds=0.45):
        self.dead = True
        self.fade_to(0.0, seconds)


class GameMedia(QObject):
    """One game's music + animated icon. `frame` is the latest video frame (or None)."""
    ready = Signal(object, object, object)        # (music, video, loop), from the worker thread
    frame_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frame: QImage | None = None
        self.serial = None
        self._gen = 0
        self.ready.connect(self._start, Qt.QueuedConnection)
        self._made = False
        self._track: _Track | None = None
        self._target = 0.8

    def _ensure(self) -> bool:
        """Players are created on first use, never at startup: a broken audio stack
        then costs the preview, not the window."""
        import os
        if not HAVE_QTMM or os.environ.get("OSMOSIS_NO_MEDIA") or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return False
        if not self._made:
            self._made = True
            self._video = QMediaPlayer(self)
            self._sink = QVideoSink(self)
            self._video.setVideoSink(self._sink)
            self._video.setLoops(QMediaPlayer.Infinite)
            self._sink.videoFrameChanged.connect(self._on_frame)
        return True

    def play(self, game, volume: float = 0.8):
        self.stop()
        if not self._ensure():
            return
        self.serial = game.serial
        self._gen += 1
        gen = self._gen
        self._target = volume

        def work():
            m, v, l = convert(game)
            if gen == self._gen:
                self.ready.emit(m, v, l)

        threading.Thread(target=work, daemon=True).start()

    @Slot(object, object, object)
    def _start(self, music, video, loop):
        if not self._made or self.serial is None:
            return
        if music:
            self._track = _Track(music, loop, self)
            self._track.player.play()
            self._track.fade_to(self._target, 1.2)
        if video:
            self._video.setSource(QUrl.fromLocalFile(str(video)))
            self._video.play()

    def _on_frame(self, f):
        img = f.toImage()
        if not img.isNull():
            self.frame = img
            self.frame_changed.emit()

    def stop(self):
        self._gen += 1
        self.serial = None
        self.frame = None
        if self._track is not None:
            self._track.retire()
            self._track = None
        if self._made:
            self._video.stop()
            self._video.setSource(QUrl())
        self.frame_changed.emit()
