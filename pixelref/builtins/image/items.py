"""The image item: local coordinates are source-pixel coordinates."""

from __future__ import annotations

import math
import os
from collections import OrderedDict
from typing import Any

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QRectF, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QMovie, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsItem

from pixelref.plugin_api.ui import NodeItemMixin

# ---- Zoomed-out copies (mipmaps), shared by every item showing the same pixmap ----
#
# Drawing a big photo small means resampling all of it on every frame, so a
# zoomed-out image is drawn from a copy halved enough times. The copies of an
# image (its whole chain, 1/3 of its size) are made in background threads as
# soon as an item shows it — a frame never waits for one: until the chain is
# ready the full image is drawn, and the items are repainted when it arrives.
# Chains are dropped least recently drawn first beyond MIP_CACHE_LIMIT.

MIP_CACHE_LIMIT = 1024 * 1024 * 1024
MIN_MIP_SIDE = 8
MIP_MIN_SOURCE = 64  # smaller images are drawn as they are
PREVIEW_LEVEL = 2  # from this zoom out, a rough copy stands in while the copies are made


def mip_level_for(screen_scale: float) -> int:
    """Halving steps that still leave at least one source pixel per screen pixel."""
    if screen_scale <= 0 or screen_scale >= 0.5:
        return 0
    return max(0, min(10, int(math.floor(math.log2(1.0 / screen_scale)))))


def build_chain(image: QImage) -> list[QImage]:
    """Copies of ``image`` halved 1, 2, … times (each from the previous one, for quality)."""
    chain: list[QImage] = []
    if image.format() not in (QImage.Format_ARGB32_Premultiplied, QImage.Format_RGB32):
        image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied if image.hasAlphaChannel()
                                      else QImage.Format_RGB32)
    current = image
    while min(current.width(), current.height()) > MIN_MIP_SIDE:
        current = current.scaled(max(1, current.width() // 2), max(1, current.height() // 2),
                                 Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        chain.append(current)
    return chain


class _ChainDone(QObject):
    done = Signal(object, object)  # cache key, list[QImage]


class _ChainJob(QRunnable):
    def __init__(self, key: int, image: QImage, signals: _ChainDone) -> None:
        super().__init__()
        self.key, self.image, self.signals = key, image, signals

    def run(self) -> None:
        try:
            chain = build_chain(self.image)
        except Exception:  # noqa: BLE001  (a broken image keeps drawing at full size)
            chain = []
        self.signals.done.emit(self.key, chain)


class MipStore:
    def __init__(self) -> None:
        self.chains: OrderedDict[int, list[QImage]] = OrderedDict()  # least recently drawn first
        self.bytes = 0
        self.waiting: dict[int, list[Any]] = {}  # key -> items to repaint when its chain is ready
        self.previews: dict[int, QImage] = {}  # key -> rough copy until its chain is ready
        self._pool: QThreadPool | None = None
        self._signals: _ChainDone | None = None

    def _start(self, key: int, pixmap: QPixmap) -> None:
        if self._pool is None:
            self._pool = QThreadPool()
            self._pool.setMaxThreadCount(max(1, (os.cpu_count() or 2) - 1))
            self._signals = _ChainDone()
            self._signals.done.connect(self._on_done, Qt.QueuedConnection)
        # toImage() of a raster pixmap shares its pixels; the job only reads them.
        self._pool.start(_ChainJob(key, pixmap.toImage(), self._signals))

    def prepare(self, pixmap: QPixmap, item: Any = None) -> None:
        """Make ``pixmap``'s chain in the background (if it isn't made or on its way)."""
        if pixmap.isNull() or min(pixmap.width(), pixmap.height()) < MIP_MIN_SOURCE:
            return
        key = pixmap.cacheKey()
        if key in self.chains:
            return
        waiting = self.waiting.get(key)
        if waiting is None:
            self.waiting[key] = [item] if item is not None else []
            self._start(key, pixmap)
        elif item is not None and not any(w is item for w in waiting):
            waiting.append(item)

    def get(self, pixmap: QPixmap, level: int, item: Any = None) -> QImage | QPixmap:
        """``pixmap`` shrunk by 2**level, or ``pixmap`` itself while the copies are being made."""
        if level <= 0:
            return pixmap
        key = pixmap.cacheKey()
        chain = self.chains.get(key)
        if chain is None:
            self.prepare(pixmap, item)
            if level < PREVIEW_LEVEL:
                return pixmap
            # Far zoomed out (a board just opened): a rough copy, made in a blink by
            # sampling, beats drawing (and uploading to the GPU) every photo at full size.
            preview = self.previews.get(key)
            if preview is None:
                preview = self.previews[key] = pixmap.toImage().scaled(
                    max(1, pixmap.width() >> PREVIEW_LEVEL), max(1, pixmap.height() >> PREVIEW_LEVEL),
                    Qt.IgnoreAspectRatio, Qt.FastTransformation)
            return preview
        self.chains.move_to_end(key)
        if not chain:
            return pixmap
        return chain[min(level, len(chain)) - 1]

    def _on_done(self, key: int, chain: list[QImage]) -> None:
        items = self.waiting.pop(key, [])
        self.previews.pop(key, None)
        self.chains[key] = chain
        self.bytes += sum(image.sizeInBytes() for image in chain)
        while self.bytes > MIP_CACHE_LIMIT and len(self.chains) > 1:
            _old, dropped = self.chains.popitem(last=False)
            self.bytes -= sum(image.sizeInBytes() for image in dropped)
        for item in items:
            try:
                item.update()
            except RuntimeError:  # the item was deleted meanwhile
                pass

    def wait(self) -> None:
        """Finish the chains on their way (tests)."""
        if self._pool is not None:
            self._pool.waitForDone()
            QCoreApplication.sendPostedEvents(self._signals, QEvent.MetaCall)

    def clear(self) -> None:
        self.wait()
        self.chains.clear()
        self.previews.clear()
        self.bytes = 0


MIPS = MipStore()


def mip_pixmap(pixmap: QPixmap, level: int) -> QImage | QPixmap:
    """``pixmap`` shrunk by 2**level (made at once: export and tests; the canvas uses MIPS)."""
    if level <= 0 or min(pixmap.width(), pixmap.height()) < MIP_MIN_SOURCE:
        return pixmap
    key = pixmap.cacheKey()
    if key not in MIPS.chains:
        MIPS._on_done(key, build_chain(pixmap.toImage()))
    return MIPS.get(pixmap, level)


# Grayscale copies by source pixmap: items are rebuilt on every edit, and converting a
# large image again each time took a second or more. The newest entry is always kept.
# Decoded animation frames by image cache key: items are rebuilt on every edit
# (a move, a nudge), and a paused animation shows its frame from these.
_FRAME_CACHE: dict[Any, list[QPixmap]] = {}


def prune_frame_cache(used: set[Any]) -> None:
    for key in list(_FRAME_CACHE):
        if key not in used:
            del _FRAME_CACHE[key]


class PixmapItem(NodeItemMixin, QGraphicsItem):
    def __init__(self, node_name: str, pixmap: QPixmap) -> None:
        super().__init__()
        self._init_node(node_name, "image", None)
        self._pixmap = pixmap
        self._crop: QRectF | None = None
        self._crop_preview = False
        self._movie: QMovie | None = None
        self.frame_listeners: list[Any] = []
        self._open_reader: Any = None
        self._frames: list[QPixmap] | None = None
        self._held: int | None = None  # 0-based frame shown while paused
        self._suspended = False  # playing, but the movie is paused while the item is hidden
        self.cache_key: Any = None
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        # Annotations parented to an image keep their own opacity.
        self.setFlag(QGraphicsItem.ItemDoesntPropagateOpacityToChildren, True)
        MIPS.prepare(pixmap, self)  # ready before the first zoom out

    # ---- Pixmap -------------------------------------------------------------------

    def pixmap(self) -> QPixmap:
        return self._pixmap

    def setPixmap(self, pixmap: QPixmap) -> None:  # noqa: N802
        if pixmap.size() != self._pixmap.size():
            self.prepareGeometryChange()
        self._pixmap = pixmap
        self.update()

    def set_movie(self, movie: QMovie) -> None:
        self.stop_movie()
        self._movie = movie
        movie.frameChanged.connect(self._on_movie_frame)
        movie.start()
        self._suspend_if_hidden()

    def _suspend_if_hidden(self) -> None:
        """Hidden items don't decode frames; they carry on when shown again."""
        movie = self._movie
        if movie is not None and not self.isVisible() and movie.state() == QMovie.Running:
            movie.setPaused(True)
            self._suspended = True

    def stop_movie(self) -> None:
        self._suspended = False
        if self._movie is not None:
            try:
                self._movie.frameChanged.disconnect(self._on_movie_frame)
            except (RuntimeError, TypeError):
                pass
            self._movie.stop()
            self._movie = None

    def _on_movie_frame(self, _frame: int) -> None:
        if self._movie is not None and self._held is None:
            self.setPixmap(self._movie.currentPixmap())
            self._notify_frame()

    # ---- Playback (animated GIF / WebP); frames are numbered from 1 -------------------------
    #
    # QMovie plays the animation. Showing a chosen frame uses frames decoded once
    # with QImageReader (QMovie's random access is unreliable for GIFs); playing
    # on from there steps the movie forward to that frame.

    def set_frame_source(self, open_reader: Any) -> None:
        """``open_reader() -> QImageReader`` over the same data as the movie."""
        self._open_reader = open_reader
        self._frames = None
        self._held = None

    def add_frame_listener(self, callback: Any) -> None:
        """``callback(item)`` after every frame change (playing or stepping)."""
        self.frame_listeners.append(callback)

    def _notify_frame(self) -> None:
        for listener in list(self.frame_listeners):
            listener(self)

    @property
    def movie(self) -> QMovie | None:
        return self._movie

    @property
    def animated(self) -> bool:
        return self._movie is not None

    def decoded_frames(self) -> list[QPixmap]:
        if self._frames is None:
            key = self.cache_key
            cached = _FRAME_CACHE.get(key) if key is not None else None
            if cached is not None:
                self._frames = cached
                return cached
            frames: list[QPixmap] = []
            opener = self._open_reader
            if opener is not None:
                reader = opener()
                while True:
                    image = reader.read()
                    if image.isNull():
                        break
                    frames.append(QPixmap.fromImage(image))
            self._frames = frames
            if key is not None and frames:
                _FRAME_CACHE[key] = frames
        return self._frames or []

    def frame_count(self) -> int:
        if self._movie is None:
            return 1
        count = self._movie.frameCount()
        return count if count > 0 else len(self.decoded_frames())

    def current_frame(self) -> int:
        if self._movie is None:
            return 1
        held = self._held
        return (held if held is not None else self._movie.currentFrameNumber()) + 1

    def is_playing(self) -> bool:
        return self._movie is not None and (self._suspended or self._movie.state() == QMovie.Running)

    def play_from(self, frame: int) -> None:
        """Play on from a frame (1-based) without decoding every frame first."""
        if self._movie is not None:
            self._held = max(0, int(frame) - 1)
            self._suspended = False
            self.play()

    def play(self) -> None:
        movie = self._movie
        if movie is None:
            return
        if self._suspended:
            return  # already playing; resumes when the item is shown
        held = self._held
        if held is not None:
            # Bring the movie to the frame on screen, then let it run from there.
            self._held = None
            movie.stop()
            movie.start()
            movie.setPaused(True)
            while movie.currentFrameNumber() < held and movie.jumpToNextFrame():
                pass
            movie.setPaused(False)
        elif movie.state() == QMovie.Paused:
            movie.setPaused(False)
        elif movie.state() == QMovie.NotRunning:
            movie.start()
        self._suspend_if_hidden()

    def pause(self) -> None:
        movie = self._movie
        if movie is not None and (self._suspended or movie.state() == QMovie.Running):
            self._suspended = False
            movie.setPaused(True)
            self._held = movie.currentFrameNumber()

    def go_to_frame(self, frame: int) -> int:
        """Pause on a frame (1-based, clamped to the last one); returns the frame shown."""
        movie = self._movie
        if movie is None:
            return 1
        frames = self.decoded_frames()
        if not frames:
            return self.current_frame()
        self._suspended = False
        if movie.state() == QMovie.Running:
            movie.setPaused(True)
        index = max(0, min(int(frame) - 1, len(frames) - 1))
        self._held = index
        self.setPixmap(frames[index])
        self._notify_frame()
        return index + 1

    def step_frame(self, count: int = 1) -> int:
        """Pause and move ``count`` frames, wrapping around; returns the frame shown."""
        total = self.frame_count() or 1
        return self.go_to_frame((self.current_frame() - 1 + int(count)) % total + 1)

    # ---- Crop -----------------------------------------------------------------------

    def full_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, float(self._pixmap.width()), float(self._pixmap.height()))

    def crop_rect(self) -> QRectF:
        full = self.full_rect()
        if self._crop is None:
            return full
        rect = self._crop.intersected(full)
        return rect if rect.width() >= 1.0 and rect.height() >= 1.0 else full

    def set_crop(self, crop: Any) -> None:
        rect: QRectF | None = None
        if crop and len(crop) == 4:
            rect = QRectF(float(crop[0]), float(crop[1]), float(crop[2]), float(crop[3]))
            if rect.contains(self.full_rect()) and self.full_rect().contains(rect):
                rect = None
        self.prepareGeometryChange()
        self._crop = rect
        self.update()

    def crop_value(self) -> list[float] | None:
        if self._crop is None:
            return None
        rect = self.crop_rect()
        if rect == self.full_rect():
            return None
        return [rect.x(), rect.y(), rect.width(), rect.height()]

    def set_crop_preview(self, enabled: bool) -> None:
        self.prepareGeometryChange()
        self._crop_preview = enabled
        self.update()

    # ---- QGraphicsItem ------------------------------------------------------------------

    def content_rect(self) -> QRectF:
        return self.crop_rect()

    def boundingRect(self) -> QRectF:
        return self.full_rect() if self._crop_preview else self.crop_rect()

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        elif change == QGraphicsItem.ItemVisibleHasChanged and self._movie is not None:
            if not self.isVisible():
                self._suspend_if_hidden()
            elif self._suspended:
                self._suspended = False
                self._movie.setPaused(False)
        return super().itemChange(change, value)

    # ---- Effects: the source pixels (whole image, before the crop) -----------------------

    def effect_source(self) -> tuple[QPixmap, Any] | None:
        if self.cache_key is None:
            return None  # a placeholder ("missing"), not the image
        frame = self.current_frame() if self._movie is not None else 1
        return self._pixmap, (self.cache_key, frame)

    def paint_source(self, painter: QPainter, pixmap: QPixmap) -> None:
        self._draw_pixmap(painter, pixmap)

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        self._draw_pixmap(painter, self._pixmap)

    def _draw_pixmap(self, painter: QPainter, pixmap: QPixmap) -> None:
        """Draw ``pixmap`` (the image, or an effect's result: the same picture, possibly
        at a lower resolution) cropped, and pre-shrunk when zoomed out (see MIPS)."""
        rect = self.boundingRect()
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # Source pixels of ``pixmap`` per image pixel (1 unless it's a smaller result).
        sx = pixmap.width() / max(1, self._pixmap.width())
        sy = pixmap.height() / max(1, self._pixmap.height())
        transform = painter.worldTransform()
        screen_scale = max(math.hypot(transform.m11(), transform.m12()), math.hypot(transform.m21(), transform.m22()))
        level = mip_level_for(screen_scale * max(sx, sy))
        picture: QPixmap | QImage = pixmap
        if level and self._movie is None:
            # Zoomed out: draw a pre-shrunk copy instead of resampling the full image.
            picture = MIPS.get(pixmap, level, self)
            # The copy's real size, not 2**-level: halving floors odd sides and stops at tiny sizes.
            sx = picture.width() / max(1, self._pixmap.width())
            sy = picture.height() / max(1, self._pixmap.height())
        source = rect if sx == 1.0 and sy == 1.0 else QRectF(rect.x() * sx, rect.y() * sy,
                                                              rect.width() * sx, rect.height() * sy)
        if isinstance(picture, QImage):
            painter.drawImage(rect, picture, source)
        else:
            painter.drawPixmap(rect, picture, source)
