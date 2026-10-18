"""Image pixels at the resolution they're drawn at, decoded in the background.

An image item doesn't hold its decoded pixels. It holds an ``ImageHandle``
(one per file or embedded asset, shared by every item showing it) that knows
the image's size from its header and keeps decoded *levels*: level ``k`` is
the image shrunk ``2**k`` times. Drawing asks for the level its zoom needs:

* on screen, a frame never waits: the closest level already decoded is drawn
  (or a plain placeholder) while the right one is decoded on a worker thread,
  then the items showing it repaint;
* a final render (export, copy as image) decodes what it needs at once.

JPEGs decode straight to a level (the decoder scales while decoding, which is
much faster than decoding everything and shrinking it); other formats decode
in full and are shrunk, on the worker. A level is made from a sharper one
already in memory when there is one.

Decoded levels of every image share one memory budget (``PIXELREF_IMAGE_MEMORY_MB``,
default 1024): the least recently drawn go first, but never one drawn in the
last couple of seconds (it is on screen; evicting it would only decode it
again on the next frame), and never a small one (a zoomed-out board's levels
cost little memory and a lot to decode again).
"""

from __future__ import annotations

import math
import os
import time
from collections import OrderedDict
from typing import Any, Callable

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageIOHandler, QImageReader, QPixmap

from pixelref.plugin_api import perf

MEMORY_LIMIT = int(os.environ.get("PIXELREF_IMAGE_MEMORY_MB", "1024") or 1024) * 1024 * 1024
RECENT_S = 2.0  # levels drawn this recently are never evicted
KEEP_BYTES = 1024 * 1024  # nor levels this small: cheap to keep, but decoding one again reads the whole file
LEVEL_MIN_SIDE = 16  # no level is made with a shorter side than this


def level_for(screen_scale: float, max_level: int) -> int:
    """Halving steps that still leave at least one image pixel per device pixel."""
    if screen_scale <= 0 or screen_scale >= 0.5:
        return 0
    return max(0, min(max_level, int(math.floor(math.log2(1.0 / screen_scale)))))


def shrink(image: QImage, size: QSize) -> QImage:
    """``image`` smoothly scaled down to ``size``, halving first for big ratios (sharper)."""
    current = image
    while current.width() >= size.width() * 4 and current.height() >= size.height() * 4:
        current = current.scaled(max(1, current.width() // 2), max(1, current.height() // 2),
                                 Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    if current.size() == size:
        return current
    return current.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


class ImageHandle:
    """One image's size and decoded levels (see the module docstring)."""

    def __init__(self, key: Any, open_reader: Callable[[], QImageReader], stored: QSize, transposed: bool,
                 scalable: bool) -> None:
        self.key = key
        self.open_reader = open_reader  # a new reader each call; safe on any thread
        self.stored = stored  # size as encoded, before the EXIF orientation is applied
        self.transposed = transposed  # the orientation turns it by 90 / 270 degrees
        self.size = QSize(stored.height(), stored.width()) if transposed else QSize(stored)
        self.scalable = scalable  # the decoder can decode straight to a smaller size
        side = min(self.size.width(), self.size.height())
        self.max_level = max(0, int(math.floor(math.log2(side / LEVEL_MIN_SIDE)))) if side > LEVEL_MIN_SIDE else 0
        self.levels: dict[int, QPixmap] = {}
        self.pending: set[int] = set()
        self.waiting: list[Any] = []  # items to repaint when a level arrives
        self.failed = False

    # ---- Sizes ----------------------------------------------------------------------------------

    def level_size(self, level: int) -> QSize:
        return QSize(max(1, self.size.width() >> level), max(1, self.size.height() >> level))

    def _stored_level_size(self, level: int) -> QSize:
        return QSize(max(1, self.stored.width() >> level), max(1, self.stored.height() >> level))

    # ---- Pictures -------------------------------------------------------------------------------

    def best(self, level: int) -> tuple[int, QPixmap] | None:
        """The decoded level closest to ``level`` (the coarser one on a tie), or None."""
        if level in self.levels:
            return level, self.levels[level]
        if not self.levels:
            return None
        nearest = min(self.levels, key=lambda k: (abs(k - level), -k))
        return nearest, self.levels[nearest]

    def request(self, level: int, item: Any = None) -> None:
        """Decode ``level`` in the background (if it isn't there or on its way); repaint ``item`` then."""
        if self.failed:
            return
        if item is not None and not any(w is item for w in self.waiting):
            self.waiting.append(item)
        if level in self.levels or level in self.pending:
            return
        self.pending.add(level)
        DECODER.start(self, level, self._source_for(level))

    def picture_now(self, level: int) -> QPixmap:
        """``level``, decoded now if it isn't there (final renders, full-size copies)."""
        picture = self.levels.get(level)
        if picture is not None:
            STORE.touch(self, level)
            return picture
        with perf.span("image.decode (sync)"):
            image = self.decode(level, self._source_for(level))
        if image.isNull():
            return QPixmap()
        return self.deliver(level, image)

    def full(self) -> QPixmap:
        return self.picture_now(0)

    def deliver(self, level: int, image: QImage) -> QPixmap:
        """Keep a decoded level (UI thread) and repaint the items waiting for it."""
        self.pending.discard(level)
        if image.isNull():
            self.failed = True
            return QPixmap()
        with perf.span("image.to_pixmap"):
            picture = QPixmap.fromImage(image)
        self.levels[level] = picture
        STORE.add(self, level, picture)
        waiting, self.waiting = self.waiting, []
        for item in waiting:
            try:
                item.update()
            except RuntimeError:  # the item was deleted meanwhile
                pass
        return picture

    def forget(self) -> None:
        """Drop every decoded level (the image is no longer shown)."""
        for level in list(self.levels):
            STORE.remove(self, level)
        self.levels.clear()
        self.waiting.clear()

    # ---- Decoding (any thread) -----------------------------------------------------------------

    def _source_for(self, level: int) -> QImage | None:
        """A sharper level already decoded, to shrink instead of decoding again (UI thread)."""
        sharper = [k for k in self.levels if k < level]
        if not sharper:
            return None
        # toImage() of a raster pixmap shares its pixels; the decoder only reads them.
        return self.levels[max(sharper)].toImage()

    def decode(self, level: int, source: QImage | None = None) -> QImage:
        """Level ``level`` as an image: shrunk from ``source`` (a sharper level) or decoded."""
        size = self.level_size(level)
        if source is not None and not source.isNull():
            with perf.span("image.shrink"):
                return shrink(source, size)
        reader = self.open_reader()
        reader.setAutoTransform(True)  # honour the EXIF orientation of photos
        if level > 0 and self.scalable:
            reader.setScaledSize(self._stored_level_size(level))  # scaling happens before the orientation
            reader.setQuality(100)  # smooth scaling of the rest after the decoder's own 1/2, 1/4, 1/8
        with perf.span(f"image.decode L{min(level, 3)}{'+' if level > 3 else ''}"):
            image = reader.read()
        if image.isNull():
            return image
        if image.size() != size:
            image = shrink(image, size)
        return image


class LazySource:
    """An image's full pixels for effects (``effect_source``), decoded only when an
    effect is computed: an image with effects doesn't keep its full size in memory
    while the cached result is drawn. Quacks like the QPixmap the engine expects."""

    def __init__(self, handle: ImageHandle) -> None:
        self.handle = handle

    def width(self) -> int:
        return self.handle.size.width()

    def height(self) -> int:
        return self.handle.size.height()

    def isNull(self) -> bool:  # noqa: N802
        return False

    def toImage(self) -> QImage:  # noqa: N802
        return self.handle.full().toImage()

    def loader(self) -> Callable[[], QImage]:
        """A function giving the full image on a worker thread."""
        full = self.handle.levels.get(0)
        if full is not None:
            image = full.toImage()
            return lambda: image
        handle = self.handle
        return lambda: handle.decode(0)


# ---- Background decoding -----------------------------------------------------------------------


class _Delivered(QObject):
    done = Signal(object, int, object)  # handle, level, QImage


class _DecodeJob(QRunnable):
    def __init__(self, handle: ImageHandle, level: int, source: QImage | None, signals: _Delivered) -> None:
        super().__init__()
        self.handle, self.level, self.source, self.signals = handle, level, source, signals

    def run(self) -> None:
        try:
            image = self.handle.decode(self.level, self.source)
        except Exception:  # noqa: BLE001  (a broken file shows as missing, it must not kill the worker)
            image = QImage()
        self.signals.done.emit(self.handle, self.level, image)


class Decoder:
    """The worker threads decoding image levels."""

    def __init__(self) -> None:
        self._pool: QThreadPool | None = None
        self._signals: _Delivered | None = None
        self.pending = 0

    def start(self, handle: ImageHandle, level: int, source: QImage | None) -> None:
        if self._pool is None:
            self._pool = QThreadPool()
            self._pool.setMaxThreadCount(max(1, (os.cpu_count() or 2) - 1))
            self._signals = _Delivered()
            self._signals.done.connect(self._on_done, Qt.QueuedConnection)
        self.pending += 1
        self._pool.start(_DecodeJob(handle, level, source, self._signals))

    def _on_done(self, handle: ImageHandle, level: int, image: QImage) -> None:
        self.pending -= 1
        handle.deliver(level, image)

    @property
    def busy(self) -> bool:
        return self.pending > 0

    def wait(self) -> None:
        """Finish the decodes on their way and deliver them (tests, benchmarks)."""
        if self._pool is not None:
            self._pool.waitForDone()
            QCoreApplication.sendPostedEvents(self._signals, QEvent.MetaCall)


class PictureStore:
    """Every handle's decoded levels, least recently drawn first, within MEMORY_LIMIT."""

    def __init__(self, limit: int = MEMORY_LIMIT) -> None:
        self.limit = limit
        self.entries: OrderedDict[tuple[int, int], tuple[ImageHandle, int, float]] = OrderedDict()
        self.bytes = 0

    @staticmethod
    def _size(picture: QPixmap) -> int:
        return picture.width() * picture.height() * max(1, picture.depth() // 8)

    def add(self, handle: ImageHandle, level: int, picture: QPixmap) -> None:
        key = (id(handle), level)
        old = self.entries.pop(key, None)
        if old is not None:
            self.bytes -= old[1]
        size = self._size(picture)
        self.entries[key] = (handle, size, time.monotonic())
        self.bytes += size
        self.trim()

    def touch(self, handle: ImageHandle, level: int) -> None:
        key = (id(handle), level)
        entry = self.entries.get(key)
        if entry is not None:
            self.entries[key] = (entry[0], entry[1], time.monotonic())
            self.entries.move_to_end(key)

    def remove(self, handle: ImageHandle, level: int) -> None:
        entry = self.entries.pop((id(handle), level), None)
        if entry is not None:
            self.bytes -= entry[1]

    def trim(self) -> None:
        if self.bytes <= self.limit:
            return
        now = time.monotonic()
        for key, (handle, size, used) in list(self.entries.items()):
            if self.bytes <= self.limit or now - used < RECENT_S:
                break  # the rest was drawn just now (on screen): go over the budget rather than thrash
            if size < KEEP_BYTES:
                continue
            del self.entries[key]
            self.bytes -= size
            handle.levels.pop(key[1], None)

    def clear(self) -> None:
        for handle, _size, _used in list(self.entries.values()):
            handle.levels.clear()
        self.entries.clear()
        self.bytes = 0


DECODER = Decoder()
STORE = PictureStore()


def open_handle(key: Any, open_reader: Callable[[], QImageReader]) -> ImageHandle | None:
    """A handle for an image the decoder can read and size from its header, or None
    (unreadable, or a format that only tells its size by decoding)."""
    reader = open_reader()
    reader.setAutoTransform(True)
    if not reader.canRead():
        return None
    stored = reader.size()
    if not stored.isValid() or stored.isEmpty():
        return None
    transform = reader.transformation()
    transposed = bool(transform & QImageIOHandler.Transformation.TransformationRotate90)
    scalable = reader.supportsOption(QImageIOHandler.ImageOption.ScaledSize)
    return ImageHandle(key, open_reader, stored, transposed, scalable)
