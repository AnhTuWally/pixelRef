"""The sequence item: numbered image files played in place, like a movie.

It is an image item (``PixmapItem``) whose handle changes with the frame, so
each frame is decoded in the background at the size it is drawn, within the
board's image memory budget, by the same code as every image (EXR frames too,
through the exr plugin's decoder). Frame handles come from the image cache's
shared set (``PixmapCache.shared_handle``), so an item rebuilt after an edit
finds its frames still decoded.

Playing is paced by the clock: when a frame isn't decoded yet at its time, the
last decoded one stays on screen (the frame is dropped, playback doesn't slow
down) and the next few frames are decoded ahead. The playback plugin drives it
through the same small API as animated GIFs and movies (``frame_count``,
``go_to_frame``, ``play`` …; frames numbered from 1 = ``first``).
"""

from __future__ import annotations

import bisect
import math
import time
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsItem

from pixelref.builtins.image.items import PixmapItem, device_scale
from pixelref.builtins.image.source import ImageHandle, level_for

PREFETCH = 8  # frames decoded ahead while playing
FILMSTRIP_FRAMES = 12
FILMSTRIP_SIDE = 96  # pixels: the longest side thumbnails are decoded at (at least)
BLACK = QColor(0, 0, 0)


class _Waiter:
    """Something an ImageHandle can "repaint" when a level arrives: calls back instead."""

    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback

    def update(self) -> None:
        self.callback()


class SequenceItem(PixmapItem):
    animated = True

    def __init__(self, node_name: str, frame_handle: Callable[[int], ImageHandle | None], first: int, last: int,
                 on_disk: list[int], fps: float = 24.0, loop: bool = True, missing: str = "hold",
                 placeholder: QPixmap | None = None) -> None:
        super().__init__(node_name, pixmap=placeholder if placeholder is not None else QPixmap())
        self.node_type = "sequence"
        self._frame_handle = frame_handle  # absolute frame number -> handle (None: no such file / unreadable)
        self._first = int(first)
        self._last = max(int(first), int(last))
        self._on_disk = sorted(on_disk)
        self._present = set(self._on_disk)
        self._fps = max(0.1, float(fps or 24.0))
        self._loop = bool(loop)
        self._missing = missing if missing in ("hold", "black") else "hold"
        self._index = 0  # 0-based frame in the range
        self._black = False  # the frame shown has no file and missing == "black"
        self._level = 0  # the level last drawn (prefetches decode the same)
        self._playing = False
        self._suspended = False  # playing, but hidden: the clock stops
        self._clock: tuple[float, int] = (0.0, 0)  # (monotonic start, index at start)
        self._timer = QTimer()
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(max(1, int(1000.0 / self._fps / 2.0)))  # twice per frame: steady pacing
        self._timer.timeout.connect(self._tick)
        self._filmstrip: list[QPixmap | None] | None = None
        self._filmstrip_count = 0
        self._filmstrip_listeners: list[Any] = []
        self._waiters: list[_Waiter] = []  # thumbnails on their way
        self._size: QSize | None = None
        first_file = self._source_frame(self._first) if self._on_disk else None
        handle = self._frame_handle(first_file) if first_file is not None else None
        if handle is not None:
            self._size = QSize(handle.size)
            self._show(0)

    # ---- Frames -------------------------------------------------------------------------------

    def image_size(self) -> QSize:
        return QSize(self._size) if self._size is not None else self._pixmap.size()

    def _source_frame(self, frame: int) -> int | None:
        """The file shown for ``frame`` (absolute): its own, the nearest earlier one (hold), or None."""
        if frame in self._present:
            return frame
        if self._missing == "black" or not self._on_disk:
            return None
        index = bisect.bisect_right(self._on_disk, frame) - 1
        return self._on_disk[index] if index >= 0 else self._on_disk[0]

    def _show(self, index: int) -> None:
        self._index = max(0, min(index, self.frame_count() - 1))
        if self._size is None:
            return
        source = self._source_frame(self._first + self._index)
        handle = self._frame_handle(source) if source is not None else None
        previous = self._handle
        self._black = handle is None
        if handle is not None and handle is not previous:
            if not handle.levels and previous is not None:
                # Until this frame is decoded, the last one stays up (or what it stood in with).
                handle.stand_in = previous if previous.levels else previous.stand_in
            self._handle = handle
        self.update()

    def _prefetch(self) -> None:
        count = self.frame_count()
        for step in range(1, PREFETCH + 1):
            index = self._index + step
            if index >= count:
                if not self._loop:
                    break
                index %= count
            source = self._source_frame(self._first + index)
            handle = self._frame_handle(source) if source is not None else None
            if handle is not None:
                handle.request(min(self._level, handle.max_level))

    def frame_label(self, frame: int) -> str:
        """The file's frame number for playback frame ``frame`` (1 = ``first``)."""
        return str(self._first + int(frame) - 1)

    def frames_on_disk(self) -> list[int]:
        """The frame numbers that had a file when the item was built."""
        return list(self._on_disk)

    def is_missing(self) -> bool:
        return self._size is None

    # ---- Playback (the API the playback plugin uses; frames numbered from 1) -----------------

    @property
    def movie(self) -> None:
        return None

    def frame_count(self) -> int:
        return self._last - self._first + 1

    def current_frame(self) -> int:
        return self._index + 1

    def is_playing(self) -> bool:
        return self._playing

    def play(self) -> None:
        if self._size is None or self.frame_count() <= 1 or self._playing:
            return
        if not self._loop and self._index >= self.frame_count() - 1:
            self._show(0)
        self._playing = True
        self._clock = (time.monotonic(), self._index)
        if self.isVisible():
            self._timer.start()
            self._prefetch()
        else:
            self._suspended = True

    def pause(self) -> None:
        self._playing = False
        self._suspended = False
        self._timer.stop()

    def play_from(self, frame: int) -> None:
        self.pause()
        self._show(int(frame) - 1)
        self.play()

    def go_to_frame(self, frame: int) -> int:
        self.pause()
        self._show(int(frame) - 1)
        self._notify_frame()
        return self.current_frame()

    def step_frame(self, count: int = 1) -> int:
        total = self.frame_count()
        return self.go_to_frame((self.current_frame() - 1 + int(count)) % total + 1)

    def _tick(self) -> None:
        start, index0 = self._clock
        due = index0 + int(math.floor((time.monotonic() - start) * self._fps))
        count = self.frame_count()
        if due >= count and not self._loop:
            self._show(count - 1)
            self.pause()
            self._notify_frame()
            return
        due %= count
        if due != self._index:
            self._show(due)
            self._prefetch()
            self._notify_frame()

    def stop_movie(self) -> None:
        """The item is being rebuilt or removed: stop the clock (decoded frames stay shared)."""
        self.pause()
        try:
            self._timer.timeout.disconnect(self._tick)
        except (RuntimeError, TypeError):
            pass

    # ---- Filmstrip ------------------------------------------------------------------------------

    def filmstrip(self, count: int = FILMSTRIP_FRAMES) -> list[QPixmap]:
        """Thumbnails across the range, decoded in the background (see add_filmstrip_listener)."""
        if self._size is None:
            return []
        total = self.frame_count()
        if self._filmstrip is None or self._filmstrip_count != count:
            self._filmstrip_count = count
            if total <= count or count < 2:
                indices = list(range(total))
            else:
                indices = [round(i * (total - 1) / (count - 1)) for i in range(count)]
            self._filmstrip = [None] * len(indices)
            longest = max(self._size.width(), self._size.height(), 1)
            for slot, index in enumerate(indices):
                source = self._source_frame(self._first + index)
                handle = self._frame_handle(source) if source is not None else None
                if handle is None:
                    continue
                level = level_for(FILMSTRIP_SIDE / longest, handle.max_level)
                found = handle.best(level)
                if found is not None and found[0] <= level:
                    self._filmstrip[slot] = found[1]
                else:
                    waiter = _Waiter(lambda s=slot, h=handle, lv=level, c=count: self._thumbnail_ready(s, h, lv, c))
                    self._waiters.append(waiter)
                    handle.request(level, waiter)
        return [p for p in self._filmstrip if p is not None]

    def _thumbnail_ready(self, slot: int, handle: ImageHandle, level: int, count: int) -> None:
        if self._filmstrip is None or self._filmstrip_count != count:
            return  # another number of thumbnails was asked for since
        found = handle.best(level)
        if found is not None:
            self._filmstrip[slot] = found[1]
            for listener in list(self._filmstrip_listeners):
                listener(self)

    def add_filmstrip_listener(self, callback: Any) -> None:
        self._filmstrip_listeners.append(callback)

    # ---- Effects, drawing -------------------------------------------------------------------------

    def effect_source(self) -> tuple[Any, Any] | None:
        result = super().effect_source()
        if result is None:
            return None
        source, (key, _frame) = result
        return source, (key, self._first + self._index)

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        if self._size is not None and self._black:
            painter.fillRect(self.boundingRect(), BLACK)
            return
        if self._handle is not None:
            self._level = level_for(device_scale(painter), self._handle.max_level)
        super().paint_content(painter, option, widget)

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemVisibleHasChanged:
            if not self.isVisible() and self._playing:
                self._timer.stop()
                self._suspended = True
            elif self.isVisible() and self._suspended:
                self._suspended = False
                self._clock = (time.monotonic(), self._index)
                self._timer.start()
        return super().itemChange(change, value)

