"""Drawing effect stacks: caches, background processing, animations.

Every canvas item whose node has enabled effects gets a paint filter. It
draws the stack's result instead of the content:

* **Source.** Items that are pictures (images: ``item.effect_source()``) are
  processed at their own pixels, before the crop, so the result doesn't
  depend on zoom and can be kept between sessions. Everything else (text,
  shapes, plugin types) is rendered offscreen at the zoom it's seen at
  (rounded to a power of two) and processed from that.
* **Result key.** A SHA-1 of the source's key, the enabled steps (type,
  algorithm version, parameters, mix) and the resolution. Results are looked
  up by key, so a rebuilt item (any edit rebuilds it) reuses them.
* **Where results come from**, fastest first: memory (an LRU of pixmaps,
  newest always kept) → the disk cache (lossless WebP files) → the scene
  file's ``effect_cache`` section → computing. Picture results are written to
  the disk cache, and on save into the scene, so reopening a board never
  processes again.
* **When work happens.** Small cheap stacks are processed while drawing.
  Anything else runs on a worker thread; meanwhile the item shows its
  previous result (or its plain content) with a small dot. Requests are
  debounced per node, so dragging a slider processes the last value, not
  every step. Offscreen renders (export, copy as image) always wait for the
  result, so they're never unfiltered.
* **Animations.** While playing, only cheap effects run on each frame. With
  ``effects_all_frames`` on, every frame is processed in the background with
  the whole stack and cached; playback then shows the full stack.

Paint filters never change an item's geometry: results are drawn into the
item's existing rectangle.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QPointF, QRectF, QRunnable, Qt, QThreadPool, \
    QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

from . import COST_HEAVY, EffectType
from .processors import EffectContext, Processor, argb, run_stack

logger = logging.getLogger(__name__)

SCENE_SECTION = "effect_cache"
# Work done while drawing: pixels × steps a cheap stack may cost before it goes to a worker.
SYNC_BUDGET = 2_000_000
# Pictures bigger than this are processed at a lower resolution (memory and time stay bounded).
MAX_SOURCE_PIXELS = 40_000_000
# Offscreen renders of other items are capped at this many pixels.
MAX_RENDER_PIXELS = 16_000_000
DEBOUNCE_MS = 40
# Node attributes that don't change what an item draws (only where, or whether).
PLACEMENT_ATTRS = {"translate", "rotate", "scale", "pivot", "visibility", "opacity", "locked", "tags", "effects",
                   "effects_all_frames", "effects_muted"}


# ---- Caches --------------------------------------------------------------------------------------------


class PixmapCache:
    """Results by key, limited in bytes; the newest entry is always kept."""

    def __init__(self, limit_bytes: int) -> None:
        self.limit = limit_bytes
        self.items: OrderedDict[str, QPixmap] = OrderedDict()
        self.bytes = 0

    def get(self, key: str) -> QPixmap | None:
        pixmap = self.items.get(key)
        if pixmap is not None:
            self.items.move_to_end(key)
        return pixmap

    def put(self, key: str, pixmap: QPixmap) -> None:
        old = self.items.pop(key, None)
        if old is not None:
            self.bytes -= _size(old)
        self.items[key] = pixmap
        self.bytes += _size(pixmap)
        while self.bytes > self.limit and len(self.items) > 1:
            _key, evicted = self.items.popitem(last=False)
            self.bytes -= _size(evicted)

    def clear(self) -> None:
        self.items.clear()
        self.bytes = 0

    def __contains__(self, key: str) -> bool:
        return key in self.items


def _size(pixmap: QPixmap) -> int:
    return pixmap.width() * pixmap.height() * 4


class DiskCache:
    """Lossless WebP files by key in the user's cache folder (thread-safe for distinct keys)."""

    def __init__(self, folder: Path, limit_bytes: int) -> None:
        self.folder = folder
        self.limit = limit_bytes
        self.enabled = True

    def path(self, key: str) -> Path:
        return self.folder / key[:2] / f"{key}.webp"

    def has(self, key: str) -> bool:
        return self.enabled and self.path(key).is_file()

    def read_bytes(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        path = self.path(key)
        try:
            data = path.read_bytes()
            os.utime(path)  # most recently used: trimmed last
            return data
        except OSError:
            return None

    def save(self, key: str, data: bytes) -> None:
        if not self.enabled:
            return
        path = self.path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
            temp.write_bytes(data)
            os.replace(temp, path)
        except OSError:
            logger.debug("Could not write the effect cache file %s", path, exc_info=True)

    def trim(self) -> int:
        """Delete the least recently used files over the limit; returns how many."""
        if not self.folder.is_dir():
            return 0
        files = []
        total = 0
        for path in self.folder.glob("*/*.webp"):
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append((stat.st_mtime, stat.st_size, path))
            total += stat.st_size
        removed = 0
        files.sort()
        while total > self.limit * 0.9 and files:
            _mtime, size, path = files.pop(0)
            try:
                path.unlink()
                total -= size
                removed += 1
            except OSError:
                pass
        return removed

    def clear(self) -> None:
        for path in self.folder.glob("*/*.webp"):
            try:
                path.unlink()
            except OSError:
                pass


def encode(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "WEBP", 100)  # quality 100 is lossless in Qt's WebP writer
    return bytes(buffer.data())


def decode(data: bytes) -> QImage:
    image = QImage.fromData(QByteArray(data))
    return image if image.isNull() else argb(image)


# ---- Background work -----------------------------------------------------------------------------------


class _Signals(QObject):
    done = Signal(str, object, str)  # key, QImage or None, where it came from ("computed", "disk", "scene", "error")


class _Job(QRunnable):
    def __init__(self, engine: "EffectEngine", request: "Request") -> None:
        super().__init__()
        self.engine = engine
        self.request = request
        self.setAutoDelete(True)

    def run(self) -> None:
        request = self.request
        try:
            image, origin = self.engine.produce(request)
        except Exception:  # noqa: BLE001  (a processor's bug must not kill the worker thread)
            logger.exception("Effect processing failed for %s", request.node)
            image, origin = None, "error"
        self.engine._signals.done.emit(request.key, image, origin)


class Request:
    """Everything a worker needs to produce one result (no Qt objects bound to the GUI thread)."""

    def __init__(self, key: str, node: str, source: Callable[[], QImage] | QImage, steps: list[Any],
                 fx: EffectContext, scale: float, persist: bool, scene_data: str | None) -> None:
        self.key = key
        self.node = node
        self.source = source
        self.steps = steps
        self.fx = fx
        self.scale = scale  # process the source at this scale (1 unless it's huge)
        self.persist = persist
        self.scene_data = scene_data


# ---- The engine ------------------------------------------------------------------------------------------


class EffectEngine(QObject):
    """One per app: caches, workers and the per-node bookkeeping of the paint filters."""

    delivered = Signal(str)  # node name whose result arrived
    frames_progress = Signal(str, int, int)  # node, frames done, frames total

    def __init__(self, ui: Any, cache_folder: Path) -> None:
        super().__init__()
        self.ui = ui
        self.session = ui.session
        settings = ui.settings
        self.memory = PixmapCache(int(_number(settings.value("memory_mb", 512), 512)) * 1024 * 1024)
        self.disk = DiskCache(cache_folder, int(_number(settings.value("disk_mb", 2048), 2048)) * 1024 * 1024)
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max(1, (os.cpu_count() or 2) - 1))
        self._signals = _Signals()
        self._signals.done.connect(self._on_done, Qt.QueuedConnection)
        self._inflight: dict[str, set[str]] = {}  # key -> nodes waiting for it
        self._pending: dict[str, Request] = {}  # node -> latest request (debounced)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start_pending)
        self.stale: dict[str, tuple[QPixmap, bool]] = {}  # node -> (last result, it's a picture result)
        self.frames: dict[str, tuple[int, int]] = {}  # node -> (frames done, total) for "process all frames"
        self._frame_keys: dict[str, set[str]] = {}
        self.show_effects = True  # the app-wide "Show Effects" switch (before/after); not saved
        self.stats: dict[str, int] = {"computed": 0, "disk": 0, "scene": 0, "memory": 0, "sync": 0, "error": 0}
        self._saves = 0

    # ---- Settings ---------------------------------------------------------------------------

    @property
    def save_in_scene(self) -> bool:
        value = self.ui.settings.value("save_in_scene", True)
        return value not in (False, "false", "0", 0)

    # ---- Effect types and processors ------------------------------------------------------------

    def types(self) -> dict[str, EffectType]:
        api = self.session.services.get("effects")
        return api.types() if api is not None else {}

    def processors(self) -> dict[str, Processor]:
        found: dict[str, Processor] = {}
        for name, fn in self.session.extensions.contributions("effects.processors"):
            found.setdefault(name, fn)
        return found

    def steps(self, stack: list[dict[str, Any]], cheap_only: bool = False) -> list[tuple[Any, ...]]:
        """``(type name, version, processor, params, mix, heavy)`` for each effect that can run."""
        types = self.types()
        processors = self.processors()
        steps = []
        for entry in stack:
            if not entry.get("enabled", 1):
                continue
            effect_type = types.get(entry.get("type", ""))
            processor = processors.get(entry.get("type", ""))
            if effect_type is None or processor is None:
                continue  # its plugin is off: the entry is kept but skipped
            heavy = effect_type.cost == COST_HEAVY
            if heavy and cheap_only:
                continue
            steps.append((effect_type.name, effect_type.version, processor,
                          effect_type.resolved(entry.get("params") or {}), float(entry.get("mix", 1.0)), heavy))
        return steps

    @staticmethod
    def key(content: Any, steps: list[tuple[Any, ...]], resolution: Any) -> str:
        recipe = [[name, version, params, mix] for name, version, _fn, params, mix, _heavy in steps]
        text = json.dumps([repr(content), recipe, resolution], sort_keys=True, default=str)
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    # ---- Producing results (worker thread) ---------------------------------------------------------

    def produce(self, request: Request) -> tuple[QImage | None, str]:
        if request.persist:
            data = self.disk.read_bytes(request.key)
            if data:
                image = decode(data)
                if not image.isNull():
                    return image, "disk"
        if request.scene_data:
            try:
                image = decode(base64.b64decode(request.scene_data))
            except (ValueError, TypeError):
                image = QImage()
            if not image.isNull():
                if request.persist:
                    self.disk.save(request.key, base64.b64decode(request.scene_data))
                return image, "scene"
        source = request.source() if callable(request.source) else request.source
        image = self.compute(source, request.steps, request.fx, request.scale)
        if request.persist and not image.isNull():
            self.disk.save(request.key, encode(image))
        return image, "computed"

    @staticmethod
    def compute(source: QImage, steps: list[tuple[Any, ...]], fx: EffectContext, scale: float = 1.0) -> QImage:
        image = argb(source)
        if scale < 1.0:
            image = image.scaled(max(1, round(image.width() * scale)), max(1, round(image.height() * scale)),
                                 Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        context = EffectContext(pixels_per_unit=fx.pixels_per_unit * scale, playing=fx.playing)
        return run_stack(image, [(fn, params, mix) for _n, _v, fn, params, mix, _h in steps], context)

    # ---- Requests (GUI thread) ------------------------------------------------------------------------

    def lookup(self, key: str) -> QPixmap | None:
        pixmap = self.memory.get(key)
        if pixmap is not None:
            self.stats["memory"] += 1
        return pixmap

    def busy(self, key: str) -> bool:
        return key in self._inflight or any(r.key == key for r in self._pending.values())

    def request(self, request: Request, debounce: bool = True) -> None:
        if request.key in self._inflight:
            self._inflight[request.key].add(request.node)
            return
        if debounce:
            self._pending[request.node] = request  # the latest one wins
            if not self._timer.isActive():
                self._timer.start(DEBOUNCE_MS)
        else:
            self._submit(request)

    def _start_pending(self) -> None:
        pending, self._pending = self._pending, {}
        for request in pending.values():
            if request.key in self.memory:
                self._notify(request.node)
                continue
            self._submit(request)

    def _submit(self, request: Request) -> None:
        if request.key in self._inflight:
            self._inflight[request.key].add(request.node)
            return
        self._inflight[request.key] = {request.node}
        self.pool.start(_Job(self, request))

    def produce_now(self, request: Request) -> QPixmap | None:
        """Produce a result in this thread (offscreen renders, tests)."""
        image, origin = self.produce(request)
        self.stats[origin] = self.stats.get(origin, 0) + 1
        if image is None or image.isNull():
            return None
        pixmap = QPixmap.fromImage(image)
        self.memory.put(request.key, pixmap)
        return pixmap

    def _on_done(self, key: str, image: Any, origin: str) -> None:
        nodes = self._inflight.pop(key, set())
        self.stats[origin] = self.stats.get(origin, 0) + 1
        if image is not None and not image.isNull():
            self.memory.put(key, QPixmap.fromImage(image))
            if origin == "computed":
                self._saves += 1
                if self._saves % 50 == 0:
                    self.disk.trim()
        for node in nodes:
            self._advance_frames(node, key)
            self._notify(node)

    def _notify(self, node: str) -> None:
        item = self.ui.canvas.item(node)
        if item is not None:
            item.update()
        self.delivered.emit(node)

    def wait(self, msecs: int = 60_000) -> bool:
        """Finish all pending and running work (tests, shutdown); True when idle."""
        deadline = time.monotonic() + msecs / 1000.0
        from PySide6.QtWidgets import QApplication

        while time.monotonic() < deadline:
            if self._pending:
                self._timer.stop()
                self._start_pending()
            self.pool.waitForDone(50)
            QApplication.processEvents()
            if not self._pending and not self._inflight and self.pool.activeThreadCount() == 0:
                return True
        return False

    def shutdown(self) -> None:
        self._timer.stop()
        self._pending.clear()
        self.pool.clear()
        self.pool.waitForDone(5000)

    # ---- The scene file ---------------------------------------------------------------------------

    def scene_data(self, key: str) -> str | None:
        section = self.session.scene.sections.get(SCENE_SECTION)
        entry = section.get(key) if isinstance(section, dict) else None
        data = entry.get("data") if isinstance(entry, dict) else None
        return data if isinstance(data, str) else None

    def write_scene_cache(self) -> int:
        """Before saving: the scene's effect cache holds exactly the picture results its
        nodes use now (heavy stacks and big pictures). Returns how many entries."""
        scene = self.session.scene
        if not self.save_in_scene:
            scene.sections.pop(SCENE_SECTION, None)
            return 0
        old = scene.sections.get(SCENE_SECTION)
        old = old if isinstance(old, dict) else {}
        entries: dict[str, Any] = {}
        for name, item in self.ui.canvas.items.items():
            for key in self.persistent_keys(name, item):
                entry = old.get(key)
                if entry is None:
                    data = self.disk.read_bytes(key)
                    if data is None:
                        pixmap = self.memory.get(key)
                        if pixmap is None:
                            continue  # not processed yet: nothing to save
                        data = encode(pixmap.toImage())
                    entry = {"mime": "image/webp", "encoding": "base64", "data": base64.b64encode(data).decode("ascii")}
                entries[key] = entry
        if entries:
            scene.sections[SCENE_SECTION] = entries
        else:
            scene.sections.pop(SCENE_SECTION, None)
        return len(entries)

    def persistent_keys(self, name: str, item: Any) -> list[str]:
        node = self.ui.canvas.nodes.get(name)
        if node is None:
            return []
        stack = node.get("attrs", {}).get("effects") or []
        steps = self.steps(stack)
        source = item.effect_source() if hasattr(item, "effect_source") else None
        if not steps or source is None:
            return []
        pixmap, content = source
        if not self.persists(steps, pixmap.width() * pixmap.height()):
            return []
        resolution = self.source_scale(pixmap)
        keys = [self.key(content, steps, resolution)]
        if node.get("attrs", {}).get("effects_all_frames") and getattr(item, "animated", False):
            base = content[0] if isinstance(content, tuple) else content
            keys = [self.key((base, frame), steps, resolution) for frame in range(1, item.frame_count() + 1)]
        return keys

    @staticmethod
    def persists(steps: list[tuple[Any, ...]], pixels: int) -> bool:
        """Worth keeping between sessions: anything that isn't processed while drawing."""
        return any(step[5] for step in steps) or pixels * len(steps) > SYNC_BUDGET

    @staticmethod
    def source_scale(pixmap: QPixmap) -> float:
        pixels = pixmap.width() * pixmap.height()
        return 1.0 if pixels <= MAX_SOURCE_PIXELS else math.sqrt(MAX_SOURCE_PIXELS / pixels)

    def clear(self, memory: bool = True, scene: bool = True, disk: bool = False) -> None:
        if memory:
            self.memory.clear()
            self.stale.clear()
        if scene:
            self.session.scene.sections.pop(SCENE_SECTION, None)
        if disk:
            self.disk.clear()

    # ---- Animations: process every frame ------------------------------------------------------------

    def prepare_frames(self, name: str, item: Any, stack: list[dict[str, Any]]) -> None:
        """Queue every frame of an animated image with the whole stack (once per stack)."""
        steps = self.steps(stack)
        source = item.effect_source()
        if not steps or source is None:
            return
        pixmap, content = source
        base = content[0] if isinstance(content, tuple) else content
        scale = self.source_scale(pixmap)
        total = item.frame_count()
        keys = {self.key((base, frame), steps, scale): frame for frame in range(1, total + 1)}
        self._frame_keys[name] = set(keys)
        done = sum(1 for key in keys if key in self.memory)
        self.frames[name] = (done, total)
        self.frames_progress.emit(name, done, total)
        missing = [key for key in keys if key not in self.memory and not self.busy(key)]
        if not missing:
            return
        frames = item.decoded_frames()
        persist = True
        for key in missing:
            index = keys[key] - 1
            if index >= len(frames):
                continue
            image = frames[index].toImage()
            self.request(Request(key, name, image, steps, EffectContext(), scale, persist, self.scene_data(key)),
                         debounce=False)

    def _advance_frames(self, name: str, key: str) -> None:
        keys = self._frame_keys.get(name)
        if not keys or key not in keys:
            return
        done = sum(1 for k in keys if k in self.memory)
        self.frames[name] = (done, len(keys))
        self.frames_progress.emit(name, done, len(keys))


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---- The paint filter -------------------------------------------------------------------------------------


def screen_scale(painter: QPainter) -> float:
    t = painter.worldTransform()
    return max(math.hypot(t.m11(), t.m12()), math.hypot(t.m21(), t.m22()), 1e-6)


class EffectFilter:
    """Draws one item's effect stack (installed by the ``canvas.item_hooks`` contribution)."""

    def __init__(self, engine: EffectEngine, node: dict[str, Any]) -> None:
        self.engine = engine
        self.name = node["name"]
        attrs = node.get("attrs", {})
        self.stack = list(attrs.get("effects") or [])
        self.all_frames = bool(attrs.get("effects_all_frames", 0))
        # What the content looks like, without where it is: the cache key of rendered items.
        self.content = (node["type"], json.dumps({k: v for k, v in attrs.items() if k not in PLACEMENT_ATTRS},
                                                 sort_keys=True, default=str))

    def __call__(self, item: Any, painter: QPainter, option: Any, widget: Any, draw: Callable[[QPainter], None]
                 ) -> None:
        # Export and copy as image wait for the real result; previews (minimap,
        # thumbnails) and the views never block on it.
        offscreen = widget is None and getattr(self.engine.ui.canvas, "rendering_final", 0) > 0
        source = item.effect_source()
        if source is not None:
            self._paint_picture(item, painter, draw, source, offscreen)
        else:
            self._paint_rendered(item, painter, draw, offscreen)

    # ---- Pictures (images) ----------------------------------------------------------------------

    def _paint_picture(self, item: Any, painter: QPainter, draw: Callable[[QPainter], None], source: Any,
                       offscreen: bool) -> None:
        engine = self.engine
        pixmap, content = source
        playing = bool(getattr(item, "is_playing", lambda: False)())
        full = engine.steps(self.stack)
        if not full:
            draw(painter)
            return
        scale = engine.source_scale(pixmap)
        full_key = engine.key(content, full, scale)
        result = engine.lookup(full_key)
        if result is None and playing:
            # Playing: only cheap effects keep up with the frames (unless every frame is ready).
            cheap = engine.steps(self.stack, cheap_only=True)
            if not cheap:
                draw(painter)
                return
            full, full_key = cheap, engine.key(content, cheap, scale)
            result = engine.lookup(full_key)
        pixels = pixmap.width() * pixmap.height()
        if result is None:
            persist = engine.persists(full, pixels)
            sync = offscreen or (not persist and pixels * len(full) <= SYNC_BUDGET * (4 if playing else 1))
            request = Request(full_key, self.name, lambda pm=pixmap: pm.toImage(), full,
                              EffectContext(playing=playing), scale, persist, engine.scene_data(full_key))
            if sync:
                engine.stats["sync"] += 1
                result = engine.produce_now(request)
            else:
                if not engine.busy(full_key):
                    request.source = pixmap.toImage()
                    engine.request(request)
                self._paint_waiting(item, painter, draw, picture=True)
                return
        if result is None:
            draw(painter)
            return
        engine.stale[self.name] = (result, True)
        item.paint_source(painter, result)

    # ---- Everything else (rendered offscreen) -------------------------------------------------------

    def _paint_rendered(self, item: Any, painter: QPainter, draw: Callable[[QPainter], None], offscreen: bool
                        ) -> None:
        engine = self.engine
        steps = engine.steps(self.stack)
        rect = item.boundingRect()
        if not steps or rect.width() <= 0 or rect.height() <= 0:
            draw(painter)
            return
        # The zoom it's seen at, rounded up to a power of two (re-rendered at the next step).
        scale = 2.0 ** math.ceil(math.log2(min(16.0, max(1 / 16, screen_scale(painter)))))
        while rect.width() * rect.height() * scale * scale > MAX_RENDER_PIXELS and scale > 1 / 64:
            scale /= 2.0
        key = engine.key((self.content, rect.x(), rect.y(), rect.width(), rect.height()), steps, scale)
        result = engine.lookup(key)
        if result is None:
            width, height = max(1, math.ceil(rect.width() * scale)), max(1, math.ceil(rect.height() * scale))
            image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
            image.fill(0)
            target = QPainter(image)
            target.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
            target.scale(scale, scale)
            target.translate(-rect.x(), -rect.y())
            draw(target)
            target.end()
            request = Request(key, self.name, image, steps, EffectContext(pixels_per_unit=scale), 1.0, False, None)
            heavy = any(step[5] for step in steps)
            if offscreen or (not heavy and width * height * len(steps) <= SYNC_BUDGET):
                engine.stats["sync"] += 1
                result = engine.produce_now(request)
            else:
                if not engine.busy(key):
                    engine.request(request)
                self._paint_waiting(item, painter, draw, picture=False, rect=rect)
                return
        if result is None:
            draw(painter)
            return
        engine.stale[self.name] = (result, False)
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(rect, result, QRectF(result.rect()))
        painter.restore()

    # ---- While the result is being made -------------------------------------------------------------

    def _paint_waiting(self, item: Any, painter: QPainter, draw: Callable[[QPainter], None], picture: bool,
                       rect: QRectF | None = None) -> None:
        stale = self.engine.stale.get(self.name)
        if stale is not None and stale[1] == picture:
            if picture:
                item.paint_source(painter, stale[0])
            else:
                painter.save()
                painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
                painter.drawPixmap(rect or item.boundingRect(), stale[0], QRectF(stale[0].rect()))
                painter.restore()
        else:
            draw(painter)
        # A small dot in the corner: "effects are on their way".
        bounds = rect or item.content_rect()
        size = 1.0 / screen_scale(painter)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(61, 142, 240, 220))
        painter.drawEllipse(QPointF(bounds.left() + 9 * size, bounds.top() + 9 * size), 4 * size, 4 * size)
        painter.restore()
