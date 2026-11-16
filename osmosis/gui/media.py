"""Game music (SND0.AT3) and preview video (ICON1.PAM), as the PS3 menu plays them.

Both are PS3-specific formats (ATRAC3 audio; PAMF video), so they're converted once
with ffmpeg into ~/.local/share/osmosis/media/<serial>/ and played from there with
Qt Multimedia. No ffmpeg or no Qt Multimedia -> silently no media.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QImage

from .. import config

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
    HAVE_QTMM = True
except Exception:  # pragma: no cover
    HAVE_QTMM = False


def cache_dir(serial: str) -> Path:
    return config.data_dir() / "media" / serial


def _run(args: list[str]) -> bool:
    try:
        r = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def convert(game) -> tuple[Path | None, Path | None]:
    """Returns (music, video) playable files, converting on first use."""
    ff = shutil.which("ffmpeg")
    d = cache_dir(game.serial)
    music = video = None
    if game.music:
        out = d / "music.ogg"
        if not out.is_file() and ff:
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "music.tmp.ogg"
            if _run([ff, "-y", "-loglevel", "error", "-i", str(game.music), "-vn", "-c:a", "libvorbis", "-q:a", "5", str(tmp)]):
                tmp.replace(out)
        music = out if out.is_file() else None
    if game.preview:
        out = d / "preview.mp4"
        if not out.is_file() and ff:
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "preview.tmp.mp4"
            base = [ff, "-y", "-loglevel", "error"]
            enc = ["-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", str(tmp)]
            # PAMF = a 2 KiB-aligned header followed by an MPEG program stream.
            if (_run(base + ["-i", str(game.preview)] + enc)
                    or _run(base + ["-f", "mpeg", "-skip_initial_bytes", "2048", "-i", str(game.preview)] + enc)
                    or _run(base + ["-f", "mpeg", "-i", str(game.preview)] + enc)):
                tmp.replace(out)
        video = out if out.is_file() else None
    return music, video


class GameMedia(QObject):
    """One game's music + preview loop. `frame` is the latest video frame (or None)."""
    ready = Signal(object, object)        # (music, video) paths, from the worker thread
    frame_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frame: QImage | None = None
        self.serial = None
        self._gen = 0
        self.ready.connect(self._start, Qt.QueuedConnection)
        self._music = self._video = None
        self._made = False
        self._fade = QTimer(self)
        self._fade.timeout.connect(self._fade_step)
        self._target = 0.0

    def _ensure(self) -> bool:
        """Players are created on first use, never at startup: a broken audio stack
        then costs the preview, not the window."""
        import os
        if not HAVE_QTMM or os.environ.get("OSMOSIS_NO_MEDIA") or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return False
        if not self._made:
            self._made = True
            self._audio_out = QAudioOutput(self)
            self._audio_out.setVolume(0.0)
            self._music = QMediaPlayer(self)
            self._music.setAudioOutput(self._audio_out)
            self._music.setLoops(QMediaPlayer.Infinite)
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
            m, v = convert(game)
            if gen == self._gen:
                self.ready.emit(m, v)

        threading.Thread(target=work, daemon=True).start()

    @Slot(object, object)
    def _start(self, music, video):
        if not self._made or self.serial is None:
            return
        if music:
            self._music.setSource(QUrl.fromLocalFile(str(music)))
            self._audio_out.setVolume(0.0)
            self._music.play()
            self._fade.start(40)
        if video:
            self._video.setSource(QUrl.fromLocalFile(str(video)))
            self._video.play()

    def _fade_step(self):
        v = self._audio_out.volume()
        if v < self._target - 0.02:
            self._audio_out.setVolume(min(self._target, v + 0.04))
        else:
            self._audio_out.setVolume(self._target)
            self._fade.stop()

    def _on_frame(self, f):
        img = f.toImage()
        if not img.isNull():
            self.frame = img
            self.frame_changed.emit()

    def stop(self):
        self._gen += 1
        self.serial = None
        self.frame = None
        self._fade.stop()
        if self._made:
            self._music.stop()
            self._video.stop()
            self._music.setSource(QUrl())
            self._video.setSource(QUrl())
        self.frame_changed.emit()
