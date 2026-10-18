"""Tile cache: a canvas view paints its scene from cached tiles.

Painting the scene directly calls every visible item's Python ``paint`` on
every frame — panning, hovering, a gizmo or rubber band redraw all of them.
With thousands of items that is far too slow, so a busy view renders the
scene (background included) once into device-pixel tiles at the current zoom
and redraws frames by blitting them. Only tiles under a change are rendered
again: the scene reports changed areas (``CanvasScene.content_changed``) and
those tiles are dropped.

* Tiles are anchored to the scene: tile ``(i, j)`` of a zoom level covers
  ``TILE`` device pixels from scene point ``(i, j) * TILE / (zoom * dpr)``. A
  pan keeps every tile; newly exposed ones are rendered (a ring around the
  view is pre-rendered when idle, so most pans never wait).
* A zoom step makes a new level. Its tiles are rendered once the zoom settles
  (a little at a time, so input stays responsive); until then the area is
  drawn from the previous levels' tiles, scaled. Tiles nothing covers yet are
  rendered at once.
* Only axis-aligned cameras use tiles (rotated or mirrored views paint the
  scene directly, so they never look resampled).
"""

from __future__ import annotations

import math
import os
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Iterable

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QRegion, QTransform

from ...core import perf
from .items import rendering_for

if TYPE_CHECKING:
    from .view import CanvasView

TILE = 256  # device pixels
MAX_RUN = 8  # adjacent missing tiles rendered by one scene.render
MAX_LEVELS = 3  # zoom levels kept (the current one and fallbacks for zooming back)
BUDGET_BYTES = 256 * 1024 * 1024  # per view
SETTLE_MS = 140  # after the last zoom step, render the new level
SLICE_MS = 12.0  # idle rendering works in slices this long
PREFETCH = 1  # tiles pre-rendered around the visible area
TILE_BYTES = TILE * TILE * 4

Key = tuple[int, int]


def tiles_mode() -> str:
    """``PIXELREF_TILES``: "1" always use tiles, "0" never, unset: busy scenes only."""
    return os.environ.get("PIXELREF_TILES", "").strip()


class Level:
    """The tiles of one zoom level."""

    __slots__ = ("zoom", "dpr", "r", "step", "tiles")

    def __init__(self, zoom: float, dpr: float) -> None:
        self.zoom = zoom
        self.dpr = dpr
        self.r = zoom * dpr  # device pixels per scene unit
        self.step = TILE / self.r  # scene units per tile
        self.tiles: dict[Key, QImage] = {}

    def scene_rect(self, key: Key) -> QRectF:
        return QRectF(key[0] * self.step, key[1] * self.step, self.step, self.step)

    def index_range(self, rect: QRectF) -> tuple[int, int, int, int] | None:
        """Inclusive tile index range touching a scene rect (None if it isn't finite)."""
        try:
            return (math.floor(rect.left() / self.step), math.floor(rect.right() / self.step),
                    math.floor(rect.top() / self.step), math.floor(rect.bottom() / self.step))
        except (OverflowError, ValueError):
            return None

    def covers(self, rect: QRectF) -> bool:
        span = self.index_range(rect.adjusted(1e-6, 1e-6, -1e-6, -1e-6))
        if span is None:
            return False
        i0, i1, j0, j1 = span
        if (i1 - i0 + 1) * (j1 - j0 + 1) > len(self.tiles):
            return False
        tiles = self.tiles
        return all((i, j) in tiles for j in range(j0, j1 + 1) for i in range(i0, i1 + 1))


class TileCache:
    def __init__(self, view: "CanvasView") -> None:
        self.view = view
        self.levels: OrderedDict[tuple[float, float], Level] = OrderedDict()  # least recent first
        self._hidden: frozenset[str] = frozenset()
        self._settle = QTimer(view)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._idle)
        self._idle_timer = QTimer(view)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._idle)

    # ---- State ---------------------------------------------------------------------------

    def clear(self) -> None:
        self.levels.clear()

    @property
    def tile_count(self) -> int:
        return sum(len(level.tiles) for level in self.levels.values())

    def invalidate(self, rects: Iterable[QRectF]) -> None:
        """Drop the tiles under scene rects (a change there)."""
        rects = list(rects)
        if not rects or not self.levels:
            return
        for level in self.levels.values():
            tiles = level.tiles
            if not tiles:
                continue
            # Antialiased edges reach a little past an item's bounding rect.
            pad = 2.0 / level.r
            for rect in rects:
                span = level.index_range(rect.adjusted(-pad, -pad, pad, pad))
                if span is None:
                    tiles.clear()
                    break
                i0, i1, j0, j1 = span
                if (i1 - i0 + 1) * (j1 - j0 + 1) > len(tiles):
                    for key in [k for k in tiles if i0 <= k[0] <= i1 and j0 <= k[1] <= j1]:
                        del tiles[key]
                else:
                    for j in range(j0, j1 + 1):
                        for i in range(i0, i1 + 1):
                            tiles.pop((i, j), None)
                if not tiles:
                    break

    # ---- Painting --------------------------------------------------------------------------

    def applicable(self) -> bool:
        """Tiles can draw the view's current camera (axis-aligned, not mirrored)."""
        t = self.view.viewportTransform()
        return (t.m12() == 0.0 and t.m21() == 0.0 and t.m11() > 0.0
                and abs(t.m11() - t.m22()) <= 1e-9 * t.m11())

    def _current_level(self) -> Level:
        view = self.view
        zoom = view.viewportTransform().m11()
        dpr = view.devicePixelRatioF()
        key = (round(zoom, 9), round(dpr, 4))
        level = self.levels.get(key)
        if level is None:
            # Levels a zoom passed through without rendering anything are no use as fallbacks.
            for old in [k for k, lv in self.levels.items() if not lv.tiles]:
                del self.levels[old]
            level = self.levels[key] = Level(zoom, dpr)
            while len(self.levels) > MAX_LEVELS:
                self.levels.popitem(last=False)
        else:
            self.levels.move_to_end(key)
        return level

    def _visible_keys(self, level: Level, margin: int = 0) -> list[Key]:
        view = self.view
        t = view.viewportTransform()
        dpr = level.dpr
        ox, oy = t.m31() * dpr, t.m32() * dpr  # device position of the scene origin
        width, height = view.viewport().width() * dpr, view.viewport().height() * dpr
        i0, i1 = math.floor(-ox / TILE) - margin, math.floor((width - 1 - ox) / TILE) + margin
        j0, j1 = math.floor(-oy / TILE) - margin, math.floor((height - 1 - oy) / TILE) + margin
        # Centre first: progressive rendering fills the middle of the view before the edges.
        ci, cj = (i0 + i1) / 2.0, (j0 + j1) / 2.0
        keys = [(i, j) for j in range(j0, j1 + 1) for i in range(i0, i1 + 1)]
        keys.sort(key=lambda k: (k[0] - ci) ** 2 + (k[1] - cj) ** 2)
        return keys

    def paint(self, painter: QPainter) -> None:
        """Draw the scene (background and items) into the viewport."""
        view = self.view
        hidden = frozenset(view.display.hidden)
        if hidden != self._hidden:  # a view filter changed what's shown
            self._hidden = hidden
            self.clear()
        level = self._current_level()
        visible = self._visible_keys(level)
        missing = [k for k in visible if k not in level.tiles]
        deferred: list[Key] = []
        if missing:
            fallbacks = [lv for lv in self.levels.values() if lv is not level and lv.tiles]
            now = [k for k in missing if not any(lv.covers(level.scene_rect(k)) for lv in fallbacks)]
            if now:
                self._render(level, now)
            deferred = [k for k in missing if k not in level.tiles]
            if deferred:
                self._paint_fallbacks(painter, level, deferred, fallbacks)
                self._settle.start(SETTLE_MS)
        t = view.viewportTransform()
        dpr = level.dpr
        ox, oy = round(t.m31() * dpr), round(t.m32() * dpr)
        painter.save()
        painter.resetTransform()
        tiles = level.tiles
        for key in visible:
            image = tiles.get(key)
            if image is not None:
                painter.drawImage(QPointF((key[0] * TILE + ox) / dpr, (key[1] * TILE + oy) / dpr), image)
        painter.restore()
        if not deferred and not self._settle.isActive() and not self._idle_timer.isActive():
            if any(k not in tiles for k in self._visible_keys(level, PREFETCH)):
                self._idle_timer.start(30)

    def _paint_fallbacks(self, painter: QPainter, level: Level, keys: list[Key], fallbacks: list[Level]) -> None:
        """Older levels' tiles, scaled, where the current level isn't rendered yet."""
        view = self.view
        t = view.viewportTransform()
        dpr = level.dpr
        ox, oy = round(t.m31() * dpr), round(t.m32() * dpr)
        region = QRegion()
        area = QRectF()
        for key in keys:
            region += QRect(math.floor((key[0] * TILE + ox) / dpr), math.floor((key[1] * TILE + oy) / dpr),
                            math.ceil(TILE / dpr) + 1, math.ceil(TILE / dpr) + 1)
            area = area.united(level.scene_rect(key))
        painter.save()
        painter.setClipRegion(region)
        painter.fillRect(region.boundingRect(), QColor(*view.background_spec().color))
        painter.setWorldTransform(t)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        for fallback in fallbacks:  # oldest first: newer levels draw over them
            span = fallback.index_range(area)
            if span is None:
                continue
            i0, i1, j0, j1 = span
            for key, image in fallback.tiles.items():
                if i0 <= key[0] <= i1 and j0 <= key[1] <= j1:
                    painter.drawImage(fallback.scene_rect(key), image)
        painter.restore()

    # ---- Rendering -----------------------------------------------------------------------------

    def _render(self, level: Level, keys: list[Key]) -> None:
        """Render tiles; horizontally adjacent ones share one scene.render (fewer item paints)."""
        rows: dict[int, list[int]] = {}
        for i, j in keys:
            rows.setdefault(j, []).append(i)
        for j, columns in rows.items():
            columns.sort()
            run = [columns[0]]
            for i in columns[1:]:
                if i == run[-1] + 1 and len(run) < MAX_RUN:
                    run.append(i)
                else:
                    self._render_run(level, run[0], j, len(run))
                    run = [i]
            self._render_run(level, run[0], j, len(run))
        self._enforce_budget(level)

    def _render_run(self, level: Level, i0: int, j: int, count: int) -> None:
        with perf.span("tiles.render_run"):
            self._render_run_now(level, i0, j, count)

    def _render_run_now(self, level: Level, i0: int, j: int, count: int) -> None:
        view = self.view
        dpr = level.dpr
        image = QImage(count * TILE, TILE, QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        source = QRectF(i0 * level.step, j * level.step, count * level.step, level.step)
        target = QRectF(0.0, 0.0, count * TILE / dpr, TILE / dpr)
        painter = QPainter(image)
        # The same hints the view paints with.
        painter.setRenderHints(painter.renderHints(), False)
        painter.setRenderHints(view.renderHints(), True)
        painter.setWorldTransform(QTransform(level.zoom, 0.0, 0.0, level.zoom,
                                             -source.x() * level.zoom, -source.y() * level.zoom))
        view.drawBackground(painter, source)
        painter.resetTransform()
        with rendering_for(view):
            view.scene().render(painter, target, source, Qt.IgnoreAspectRatio)
        painter.end()
        if count == 1:
            level.tiles[(i0, j)] = image
            return
        for n in range(count):
            tile = image.copy(n * TILE, 0, TILE, TILE)
            tile.setDevicePixelRatio(dpr)
            level.tiles[(i0 + n, j)] = tile

    def _enforce_budget(self, current: Level) -> None:
        while self.tile_count * TILE_BYTES > BUDGET_BYTES:
            older = next((lv for lv in self.levels.values() if lv is not current), None)
            if older is not None:
                del self.levels[(round(older.zoom, 9), round(older.dpr, 4))]
                continue
            # Only the current level is left: drop the tiles farthest from the view.
            keep = set(self._visible_keys(current, PREFETCH))
            extra = [k for k in current.tiles if k not in keep]
            if not extra:
                return
            for key in extra[: max(1, len(extra) // 4)]:
                del current.tiles[key]

    def _idle(self) -> None:
        """Render missing tiles of the current level a slice at a time: visible ones, then a ring."""
        view = self.view
        if not view.isVisible() or not view.uses_tiles():
            return
        level = self._current_level()
        visible = self._visible_keys(level)
        todo = [k for k in visible if k not in level.tiles]
        repaint = bool(todo)
        if not todo:
            todo = [k for k in self._visible_keys(level, PREFETCH) if k not in level.tiles]
        if not todo:
            return
        deadline = time.perf_counter() + SLICE_MS / 1000.0
        done: list[Key] = []
        for key in todo:
            self._render(level, [key])
            done.append(key)
            if time.perf_counter() >= deadline:
                break
        if repaint:
            view.viewport().update()
        if len(done) < len(todo):
            self._idle_timer.start(0)
        elif repaint:
            self._idle_timer.start(30)  # then the ring around the view
