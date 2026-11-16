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
    from PySide6.QtMultimedia import (QAudioFormat, QAudioOutput, QAudioSink, QMediaDevices, QMediaPlayer,
                                      QVideoSink)
    HAVE_QTMM = True
except Exception:  # pragma: no cover
    HAVE_QTMM = False

CACHE_VERSION = "v3"
PCM_RATE = 48000          # music is decoded to raw 16-bit stereo at this rate
FRAME = 4                 # bytes per stereo s16 frame


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
_VIDEO_PES = __import__("re").compile(rb"\x00\x00\x01[\xe0-\xef]")


def pamf_extract_h264(data: bytes) -> bytes:
    """Raw H.264 from a PAMF file.

    PAMF (see RPCS3's cellPamf.h / cellDmuxPamf.cpp): a header whose size is given in
    2048-byte units, then 2048-byte packs, each holding one PES packet (optionally after a
    system header + private stream 2). The system header's length field doesn't reliably
    land on the next start code, so each pack is *searched* for its video PES (stream
    0xE0-0xEF) instead of walking lengths -- walking them skipped the key frames."""
    start = 0
    if data[:4] == b"PAMF" and len(data) >= 16:
        units = int.from_bytes(data[8:12], "big")
        if 0 < units * 2048 < len(data):
            start = units * 2048
    out = bytearray()
    for sec in range(start, len(data), 2048):
        pack = data[sec:sec + 2048]
        m = _VIDEO_PES.search(pack, 4)
        if not m:
            continue
        i = m.start()
        if i + 9 > len(pack):
            continue
        length = int.from_bytes(pack[i + 4:i + 6], "big")
        body = data[sec + i + 6: sec + i + 6 + length]
        if len(body) >= 3 and (body[0] & 0xC0) == 0x80:
            out += body[3 + body[2]:]
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
        out = d / "music.s16"
        if not out.is_file():
            tmp = d / "music.tmp.s16"
            if _ff([ff, "-y", "-loglevel", "error", "-i", str(game.music), "-vn", "-f", "s16le", "-acodec", "pcm_s16le",
                    "-ac", "2", "-ar", str(PCM_RATE), str(tmp)]):
                tmp.replace(out)
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


if HAVE_QTMM:
    from PySide6.QtCore import QIODevice

    class _LoopDevice(QIODevice):
        """Feeds decoded PCM to the sound card: the intro once, then [loop_start, loop_end)
        forever, joined sample-accurately -- no seeking, so no hiccup at the loop point."""

        def __init__(self, pcm: bytes, loop: "Loop | None", parent=None):
            super().__init__(parent)
            self.pcm = pcm
            n = len(pcm) - len(pcm) % FRAME
            if loop:
                ls = int(loop.start_ms * PCM_RATE / 1000) * FRAME
                le = int(loop.end_ms * PCM_RATE / 1000) * FRAME
            else:
                ls, le = 0, n
            self.ls = max(0, min(ls, n - FRAME))
            self.le = max(self.ls + FRAME, min(le, n))
            self.pos = 0

        def isSequential(self):
            return True

        def bytesAvailable(self):
            return 1 << 20

        def readData(self, maxlen):
            out = bytearray()
            maxlen -= maxlen % FRAME
            while len(out) < maxlen:
                take = min(maxlen - len(out), self.le - self.pos)
                out += self.pcm[self.pos:self.pos + take]
                self.pos += take
                if self.pos >= self.le:
                    self.pos = self.ls
            return bytes(out)

        def writeData(self, _data):
            return -1


class _Track(QObject):
    """One game's music on its own audio sink, with its own fades, so the previous game's
    music fades away while the next one fades up -- never a hard cut."""

    def __init__(self, pcm_path: Path, loop: Loop | None, parent):
        super().__init__(parent)
        fmt = QAudioFormat()
        fmt.setSampleRate(PCM_RATE)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Int16)
        self.dev = _LoopDevice(pcm_path.read_bytes(), loop, self)
        self.dev.open(QIODevice.ReadOnly)
        self.sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt, self)
        self.sink.setVolume(0.0)
        self.target, self.step, self.dead = 0.0, 0.04, False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(25)

    def play(self):
        self.sink.start(self.dev)

    def fade_to(self, v: float, seconds: float):
        self.target = v
        self.step = max(0.004, abs(v - self.sink.volume()) / max(1, seconds / 0.025))

    def _tick(self):
        v = self.sink.volume()
        if abs(v - self.target) > 1e-3:
            v = min(self.target, v + self.step) if v < self.target else max(self.target, v - self.step)
            self.sink.setVolume(v)
        elif self.target == 0.0 and self.dead:
            self._timer.stop()
            self.sink.stop()
            self.dev.close()
            self.deleteLater()

    def retire(self, seconds=0.5):
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
            try:
                self._track = _Track(music, loop, self)
                self._track.play()
                self._track.fade_to(self._target, 1.2)
            except Exception as e:
                log(f"music playback failed: {e}")
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
