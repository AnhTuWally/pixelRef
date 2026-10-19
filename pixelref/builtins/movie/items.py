"""The movie item: plays a linked video file in place on the board.

Movies are always linked by file reference (see ``builtins/movie/__init__.py``);
there is no embedded form. The item exposes the same play/pause/frame API as
an animated image (``PixmapItem`` in ``builtins/image/items.py``) so the
playback plugin's transport, keys and inspector section work on movies without
knowing about them specifically — "frame" here is derived from the video's
position and frame rate rather than a decoded GIF frame.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRectF, QSize, QUrl, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import QGraphicsItem

from pixelref.plugin_api.ui import NodeItemMixin

DEFAULT_SIZE = QSize(480, 270)
DEFAULT_FPS = 30.0
FILMSTRIP_FRAMES = 12


class MovieThumbnailer(QObject):
    """Evenly spaced thumbnail frames from a video file, for the filmstrip.

    Captured with its own ``QMediaPlayer``, seeking through the file one
    position at a time while paused, so scrubbing the filmstrip doesn't
    disturb the item's own (possibly playing) player.
    """

    ready = Signal()

    def __init__(self, path: Path, count: int = FILMSTRIP_FRAMES) -> None:
        super().__init__()
        self.thumbnails: list[QPixmap] = []
        self._positions: list[int] = []
        self._index = 0
        self._count = max(2, int(count))
        self._player = QMediaPlayer()
        self._sink = QVideoSink()
        self._player.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._player.durationChanged.connect(self._on_duration)
        self._player.setSource(QUrl.fromLocalFile(str(path)))

    def _on_duration(self, duration: int) -> None:
        if duration <= 0 or self._positions:
            return
        self._positions = [round(duration * i / (self._count - 1)) for i in range(self._count)]
        self._player.pause()
        self._player.setPosition(self._positions[0])

    def _on_frame(self, frame: Any) -> None:
        if not frame.isValid() or self._index >= len(self._positions):
            return
        image = frame.toImage()
        if image.isNull():
            return
        self.thumbnails.append(QPixmap.fromImage(image))
        self._index += 1
        self.ready.emit()
        if self._index < len(self._positions):
            self._player.setPosition(self._positions[self._index])
        else:
            self.close()

    def close(self) -> None:
        # Not disconnecting videoFrameChanged here: ``close()`` is usually called from
        # inside its own handler (the last frame just arrived), and disconnecting a
        # signal mid-emit only logs a harmless warning. Dropping the sink and player
        # (by releasing this object) tears the connection down anyway.
        self._player.stop()
        self._player.setVideoSink(None)


def placeholder_image(size: QSize, text: str) -> QImage:
    image = QImage(size, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("#1a1c21"))
    painter = QPainter(image)
    painter.setPen(QPen(QColor("#3d4452"), 1))
    painter.drawRect(image.rect().adjusted(0, 0, -1, -1))
    painter.setPen(QPen(QColor("#606878")))
    painter.drawText(image.rect(), Qt.AlignCenter, text)
    painter.end()
    return image


class MovieItem(NodeItemMixin, QGraphicsItem):
    """A video, decoded and played by Qt Multimedia from its linked file."""

    def __init__(self, node_name: str, path: Path | None, muted: bool = False, loop: bool = True,
                volume: float = 1.0, autoplay: bool = True) -> None:
        super().__init__()
        self._init_node(node_name, "movie", None)
        self.cache_key: Any = None
        self.final_render: Any = lambda: False
        self.frame_listeners: list[Any] = []
        self._path = path
        self._missing = path is None or not path.is_file()
        self._was_playing = False
        self._size = QSize(DEFAULT_SIZE)
        self._thumbnailer: MovieThumbnailer | None = None
        self._filmstrip_listeners: list[Any] = []
        name = path.name if path is not None else "No file"
        self._frame = placeholder_image(self._size, f"Missing\n{name}") if self._missing else QImage()
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.ItemDoesntPropagateOpacityToChildren, True)

        self._player = QMediaPlayer()
        self._sink = QVideoSink()
        self._player.setVideoSink(self._sink)
        self._audio = QAudioOutput()
        self._player.setAudioOutput(self._audio)
        self._audio.setMuted(bool(muted))
        self._audio.setVolume(max(0.0, min(1.0, float(volume))))
        self._player.setLoops(QMediaPlayer.Loops.Infinite.value if loop else 1)
        self._sink.videoFrameChanged.connect(self._on_video_frame)
        self._player.positionChanged.connect(self._on_position)
        self._player.errorOccurred.connect(self._on_error)
        if not self._missing:
            self._player.setSource(QUrl.fromLocalFile(str(path)))
            if autoplay:
                self._player.play()

    # ---- Frames -----------------------------------------------------------------------------

    def _on_video_frame(self, frame: Any) -> None:
        if not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        if image.size() != self._size:
            self.prepareGeometryChange()
            self._size = image.size()
        self._missing = False
        self._frame = image
        self.update()
        self._notify_frame()

    def _on_error(self, error: Any, message: str) -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        self._missing = True
        self._frame = placeholder_image(self._size, f"Can't play\n{message}" if message else "Can't play this file")
        self.update()

    def _on_position(self, _ms: int) -> None:
        self._notify_frame()

    def add_frame_listener(self, callback: Any) -> None:
        """``callback(item)`` after every position change (playing or seeking)."""
        self.frame_listeners.append(callback)

    def _notify_frame(self) -> None:
        for listener in list(self.frame_listeners):
            listener(self)

    # ---- Playback (duck-typed like PixmapItem's animated GIFs; see builtins/playback) -------
    #
    # "Frame" is derived from the video's position and frame rate rather than a
    # decoded still: there is no equivalent of GIF frame extraction for video.

    animated = True

    def _fps(self) -> float:
        value = self._player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
        try:
            fps = float(value)
        except (TypeError, ValueError):
            fps = 0.0
        return fps if fps > 0 else DEFAULT_FPS

    def frame_count(self) -> int:
        duration = self._player.duration()
        return max(1, round(duration / 1000.0 * self._fps())) if duration > 0 else 1

    def current_frame(self) -> int:
        return max(1, round(self._player.position() / 1000.0 * self._fps()) + 1)

    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def play(self) -> None:
        if not self._missing:
            self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def play_from(self, frame: int) -> None:
        self.go_to_frame(frame)
        self.play()

    def go_to_frame(self, frame: int) -> int:
        duration = self._player.duration()
        if duration <= 0:
            return 1
        ms = max(0, min(duration, round((int(frame) - 1) / self._fps() * 1000.0)))
        self._player.setPosition(ms)
        return self.current_frame()

    def step_frame(self, count: int = 1) -> int:
        total = self.frame_count()
        return self.go_to_frame((self.current_frame() - 1 + int(count)) % total + 1)

    # ---- Filmstrip (a handful of thumbnails across the clip, for scrubbing) ---------------

    def filmstrip(self, count: int = FILMSTRIP_FRAMES) -> list[QPixmap]:
        """Thumbnails decoded so far (empty at first; see ``add_filmstrip_listener``)."""
        if self._missing or self._path is None:
            return []
        if self._thumbnailer is None:
            self._thumbnailer = MovieThumbnailer(self._path, count)
            self._thumbnailer.ready.connect(self._on_filmstrip_ready)
        return list(self._thumbnailer.thumbnails)

    def add_filmstrip_listener(self, callback: Any) -> None:
        """``callback(item)`` whenever another filmstrip thumbnail is decoded."""
        self._filmstrip_listeners.append(callback)

    def _on_filmstrip_ready(self) -> None:
        for listener in list(self._filmstrip_listeners):
            listener(self)

    def stop_movie(self) -> None:
        """Release the player's resources (the item is being rebuilt or discarded)."""
        self._player.stop()
        try:
            self._sink.videoFrameChanged.disconnect(self._on_video_frame)
        except (RuntimeError, TypeError):
            pass
        self._player.setVideoSink(None)
        if self._thumbnailer is not None:
            self._thumbnailer.close()
            self._thumbnailer = None

    # ---- Mute / loop / volume ------------------------------------------------------------

    def set_muted(self, muted: bool) -> None:
        self._audio.setMuted(bool(muted))

    def is_muted(self) -> bool:
        return self._audio.isMuted()

    def set_volume(self, volume: float) -> None:
        self._audio.setVolume(max(0.0, min(1.0, float(volume))))

    def volume(self) -> float:
        return self._audio.volume()

    def set_loop(self, loop: bool) -> None:
        self._player.setLoops(QMediaPlayer.Loops.Infinite.value if loop else 1)

    # ---- Misc info -------------------------------------------------------------------------

    def duration_ms(self) -> int:
        return max(0, self._player.duration())

    def native_size(self) -> QSize:
        return QSize(self._size)

    def is_missing(self) -> bool:
        return self._missing

    # ---- QGraphicsItem ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        return QRectF(0.0, 0.0, float(self._size.width()), float(self._size.height()))

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        elif change == QGraphicsItem.ItemVisibleHasChanged:
            if self.isVisible():
                if self._was_playing:
                    self.play()
            else:
                self._was_playing = self.is_playing()
                self._player.pause()
        return super().itemChange(change, value)

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(self.boundingRect(), self._frame)
