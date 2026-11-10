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

Progressive rendering (on by default; View > Progressive Zoom Rendering, or ``PIXELREF_TILE_PROGRESSIVE``,
see ``progressive_mode``): rendering a new
level inside ``paint`` is what makes zooming stutter on busy boards, so while the zoom is changing
nothing is rendered there. A coarse *overview* of the whole board, built in idle slices and kept
beside the levels, is the fallback instead: a zoom shows it (blurry) at once, and the new level
fills in after the zoom settles.
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
MERGE_RECTS = 64  # more changed rects than this are merged onto the tile grid first (see _merge_rects)
MERGE_SPAN = 4096  # a rect covering more grid cells than this is kept as it is
MAX_LEVELS = 3  # zoom levels kept (the current one and fallbacks for zooming back)
BUDGET_BYTES = 256 * 1024 * 1024  # per view
SETTLE_MS = 140  # after the last zoom step, render the new level
SLICE_MS = 12.0  # idle rendering works in slices this long
PREFETCH = 1  # tiles pre-rendered around the visible area
TILE_BYTES = TILE * TILE * 4
PROGRESSIVE_SETTLE_MS = 40  # progressive mode: _idle waits for the zoom itself, so the settle timer can be short
OVERVIEW_PIXELS = 3072  # the overview fits the whole board in about this many device pixels (longest side)
OVERVIEW_SLICE_MS = 6.0  # the overview is built in slices this long
OVERVIEW_QUIET_S = 0.3  # a stale overview is refreshed after the scene has been still this long
OVERVIEW_PLAN_S = 0.5  # the board is measured again for the overview at most this often
OVERVIEW_PATIENCE_S = 2.5  # the view's own tiles pending this long without a pause: build a missing overview anyway

Key = tuple[int, int]


def tiles_mode() -> str:
    """``PIXELREF_TILES``: "1" always use tiles, "0" never, unset: busy scenes only."""
    return os.environ.get("PIXELREF_TILES", "").strip()


_progressive_enabled = True  # the user's choice (View > Progressive Zoom Rendering); on unless they turn it off


def set_progressive_enabled(on: bool) -> None:
    """Apply the user's choice. ``PIXELREF_TILE_PROGRESSIVE`` still overrides it."""
    global _progressive_enabled
    _progressive_enabled = bool(on)


def progressive_mode() -> bool:
    """Overview level + no synchronous rendering while zooming (on), or the classic behaviour (off).
    ``PIXELREF_TILE_PROGRESSIVE`` ("1" / "0") overrides the user's setting when it is set."""
    forced = os.environ.get("PIXELREF_TILE_PROGRESSIVE", "").strip()
    if forced:
        return forced != "0"
    return _progressive_enabled


class Level:
    """The tiles of one zoom level."""

    __slots__ = ("zoom", "dpr", "r", "step", "tiles", "overview", "stale", "dirty")

    def __init__(self, zoom: float, dpr: float, overview: bool = False) -> None:
        self.zoom = zoom
        self.dpr = dpr
        self.r = zoom * dpr  # device pixels per scene unit
        self.step = TILE / self.r  # scene units per tile
        self.tiles: dict[Key, QImage] = {}
        self.overview = overview
        self.stale: set[Key] = set()  # overview tiles whose content changed: drawn as they are until redone
        self.dirty: set[Key] = set()  # tiles dropped by a change (not never rendered): see TileCache.paint

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
        # The overview (progressive mode): one coarse level over the whole board, outside ``levels`` so
        # that level eviction never takes it. ``_ov_keys`` are its tiles, centre first.
        self.overview: Level | None = None
        self._ov_keys: list[Key] = []
        self._ov_bounds: QRectF | None = None
        self._ov_done = False  # nothing missing or stale
        self._ov_geometry = True  # the board's extent may have changed: plan again
        self._ov_planned = 0.0
        self._ov_busy_since = 0.0  # since when the builder has been deferring to the view's own tiles
        self._ov_changed = 0.0  # when the scene last changed
        self._ov_timer = QTimer(view)
        self._ov_timer.setSingleShot(True)
        self._ov_timer.timeout.connect(self._overview_step)

    # ---- State ---------------------------------------------------------------------------

    def clear(self) -> None:
        self.levels.clear()
        self._drop_overview()

    def drop(self) -> None:
        """Forget every tile, if there are any (the view is hidden and its scene changed)."""
        if self.levels or self.overview is not None:
            self.clear()
            self._settle.stop()
            self._idle_timer.stop()

    def _drop_overview(self) -> None:
        self.overview = None
        self._ov_keys = []
        self._ov_bounds = None
        self._ov_done = False
        self._ov_geometry = True
        self._ov_timer.stop()

    @property
    def tile_count(self) -> int:
        return sum(len(level.tiles) for level in self.levels.values())

    @property
    def overview_tile_count(self) -> int:
        return len(self.overview.tiles) if self.overview is not None else 0

    def overview_pending(self) -> int:
        """Overview tiles still to render (missing or stale): 0 when there is no overview to build."""
        ov = self.overview
        if ov is None:
            return 0
        return sum(1 for k in self._ov_keys if k not in ov.tiles) + len(ov.stale)

    def content_geometry_changed(self) -> None:
        """The board's extent may have changed (items added, removed, moved): plan the overview again."""
        if self.overview is not None or self._ov_keys:
            self._ov_geometry = True
            self._ov_done = False

    def invalidate(self, rects: Iterable[QRectF]) -> None:
        """Drop the tiles under scene rects (a change there). The overview keeps its tiles, marked stale:
        a slightly old coarse picture is a better fallback than none."""
        rects = list(rects)
        if not rects:
            return
        if len(rects) > MERGE_RECTS:
            rects = self._merge_rects(rects)
        ov = self.overview
        if ov is not None and ov.tiles:
            self._ov_changed = time.monotonic()
            pad = 2.0 / ov.r
            for rect in rects:
                span = ov.index_range(rect.adjusted(-pad, -pad, pad, pad))
                if span is None:
                    ov.stale.update(ov.tiles)
                    break
                i0, i1, j0, j1 = span
                if (i1 - i0 + 1) * (j1 - j0 + 1) > len(ov.tiles):
                    ov.stale.update(k for k in ov.tiles if i0 <= k[0] <= i1 and j0 <= k[1] <= j1)
                else:
                    ov.stale.update(k for j in range(j0, j1 + 1) for i in range(i0, i1 + 1)
                                    if (k := (i, j)) in ov.tiles)
            if ov.stale:
                self._ov_done = False
        if not self.levels:
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
                    level.dirty.update(tiles)
                    tiles.clear()
                    break
                i0, i1, j0, j1 = span
                if (i1 - i0 + 1) * (j1 - j0 + 1) > len(tiles):
                    for key in [k for k in tiles if i0 <= k[0] <= i1 and j0 <= k[1] <= j1]:
                        del tiles[key]
                        level.dirty.add(key)
                else:
                    for j in range(j0, j1 + 1):
                        for i in range(i0, i1 + 1):
                            if tiles.pop((i, j), None) is not None:
                                level.dirty.add((i, j))
                if not tiles:
                    break

    def _merge_rects(self, rects: list[QRectF]) -> list[QRectF]:
        """Many changed rects (dragging a big selection reports thousands per frame) as a few: the
        cells of the newest level's tile grid they touch, joined into one rect per run of a row.

        Looking every rect up in every level is what took the time (60 ms per frame for 3000 moved
        items). The merged rects cover at least the same area, at most one tile more around it.
        """
        level = next(reversed(self.levels.values()), None) if self.levels else self.overview
        if level is None:
            return rects
        step = level.step
        pad = 2.0 / level.r  # antialiased edges, as invalidate() pads single rects
        floor = math.floor
        cells: set[Key] = set()
        large: list[QRectF] = []
        for rect in rects:
            try:
                i0, i1 = floor((rect.left() - pad) / step), floor((rect.right() + pad) / step)
                j0, j1 = floor((rect.top() - pad) / step), floor((rect.bottom() + pad) / step)
            except (OverflowError, ValueError):
                large.append(rect)
                continue
            if i0 == i1 and j0 == j1:
                cells.add((i0, j0))
            elif (i1 - i0 + 1) * (j1 - j0 + 1) > MERGE_SPAN:
                large.append(rect)
            else:
                for j in range(j0, j1 + 1):
                    for i in range(i0, i1 + 1):
                        cells.add((i, j))
        rows: dict[int, list[int]] = {}
        for i, j in cells:
            rows.setdefault(j, []).append(i)
        # Inset by the pad invalidate() adds again, so a run does not spill into the next cells.
        inset = 2.0 * pad
        for j, columns in rows.items():
            columns.sort()
            start = previous = columns[0]
            for i in columns[1:]:
                if i != previous + 1:
                    large.append(QRectF(start * step + inset, j * step + inset,
                                        (previous - start + 1) * step - 2 * inset, step - 2 * inset))
                    start = i
                previous = i
            large.append(QRectF(start * step + inset, j * step + inset,
                                (previous - start + 1) * step - 2 * inset, step - 2 * inset))
        return large

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
        progressive = progressive_mode()
        view.sync_geometry()
        if progressive and not self._ov_done and not self._ov_timer.isActive():
            self._ov_timer.start(200)
        visible = self._visible_keys(level)
        missing = [k for k in visible if k not in level.tiles]
        deferred: list[Key] = []
        if missing:
            fallbacks = [lv for lv in self.levels.values() if lv is not level and lv.tiles]
            ov = self.overview if progressive else None
            if ov is not None and ov.tiles:
                fallbacks.insert(0, ov)  # oldest: the sharper levels draw over it
            now = [k for k in missing if not self._covered(level.scene_rect(k), fallbacks)]
            zooming = progressive and self.view.zooming()
            if progressive and level.dirty and not zooming:
                # A tile that had been rendered and was dropped by a change (an edit, an animation frame)
                # is redone at once, as it always was: showing it from the overview would make animated
                # images flicker between sharp and blurry. Only tiles never rendered at this zoom wait.
                redo = [k for k in missing if k in level.dirty and k not in now]
                now = now + redo
            if now and ov is not None and zooming:
                # Tiles the overview does not cover but that touch the board are rendered (it is still being
                # built). Tiles beyond the board hold nothing but background: rendering them is what makes a
                # zoom stutter on a big board, so they wait until it settles.
                board = self._ov_bounds
                now = [k for k in now if board is not None and level.scene_rect(k).intersects(board)]
            if now:
                self._render(level, now)
            deferred = [k for k in missing if k not in level.tiles]
            if deferred:
                self._paint_fallbacks(painter, level, deferred, fallbacks)
                if not progressive:
                    self._settle.start(SETTLE_MS)
                elif not self._settle.isActive():
                    # Not restarted by every paint: animated images repaint all the time, and refining would
                    # never begin. _idle itself waits for a zoom to finish.
                    self._settle.start(PROGRESSIVE_SETTLE_MS)
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

    def _covered(self, rect: QRectF, fallbacks: list[Level]) -> bool:
        """Older levels (and the overview, over the board only: a tile that reaches past the board is
        covered if the part on the board is) show everything of ``rect`` that is more than background."""
        for lv in fallbacks:
            if lv.overview:
                board = self._ov_bounds
                if board is not None and rect.intersects(board) and lv.covers(rect.intersected(board)):
                    return True
            elif lv.covers(rect):
                return True
        return False

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
        if not level.overview:  # the overview's memory is accounted apart
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
            level.dirty.discard((i0, j))
            return
        for n in range(count):
            tile = image.copy(n * TILE, 0, TILE, TILE)
            tile.setDevicePixelRatio(dpr)
            level.tiles[(i0 + n, j)] = tile
            level.dirty.discard((i0 + n, j))

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
        if progressive_mode() and view.zooming():
            # Tiles of a level the next zoom step abandons are wasted work: refine once the zoom is still.
            self._settle.start(PROGRESSIVE_SETTLE_MS)
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

    # ---- Overview (progressive mode) ---------------------------------------------------------------

    def _plan_overview(self) -> None:
        """Measure the board and (re)create the overview level if it no longer fits."""
        view = self.view
        canvas = view.app.canvas
        self._ov_planned = time.monotonic()
        bounds = canvas.scene_bounds(canvas.visible_content_items())
        if bounds.isNull() or bounds.width() <= 0.0 or bounds.height() <= 0.0:
            self._drop_overview()
            self._ov_geometry = False
            self._ov_done = True
            return
        dpr = view.devicePixelRatioF()
        old, ov = self._ov_bounds, self.overview
        if ov is not None and old is not None and ov.dpr == dpr:
            margin = 0.05 * max(old.width(), old.height())
            fits = old.adjusted(-margin, -margin, margin, margin).contains(bounds)
            tight = bounds.width() * bounds.height() * 2.5 >= old.width() * old.height()
            if fits and tight:
                self._ov_geometry = False
                return
        r = min(dpr, OVERVIEW_PIXELS / max(bounds.width(), bounds.height()))
        level = Level(r / dpr, dpr, overview=True)
        span = level.index_range(bounds)
        if span is None:
            self._drop_overview()
            self._ov_geometry = False
            self._ov_done = True
            return
        i0, i1, j0, j1 = span
        keys = [(i, j) for j in range(j0, j1 + 1) for i in range(i0, i1 + 1)]
        ci, cj = (i0 + i1) / 2.0, (j0 + j1) / 2.0
        keys.sort(key=lambda k: (k[0] - ci) ** 2 + (k[1] - cj) ** 2)
        self.overview, self._ov_keys, self._ov_bounds = level, keys, QRectF(bounds)
        self._ov_geometry = False

    def _overview_busy(self, level: Level, stale_only: bool) -> bool:
        """Navigating, or the view still has its own tiles to render: the overview waits."""
        view = self.view
        if not view.camera_idle(0.15):
            return True
        # The view's own refinement goes first, but a scene that keeps repainting (animated images) keeps
        # dropping tiles: after OVERVIEW_PATIENCE_S a missing overview is built anyway.
        own_work = (self._settle.isActive() or self._idle_timer.isActive()
                    or any(k not in level.tiles for k in self._visible_keys(level, PREFETCH)))
        now = time.monotonic()
        if not own_work:
            self._ov_busy_since = 0.0
        else:
            if not self._ov_busy_since:
                self._ov_busy_since = now
            # Refreshing stale tiles never competes with the view's own work; only the first build of a
            # missing overview gives up waiting (a board of animated images is never without own work).
            if stale_only or now - self._ov_busy_since < OVERVIEW_PATIENCE_S:
                return True
        # Refreshing stale tiles is lower priority than building a missing overview: wait for a still scene.
        return stale_only and time.monotonic() - self._ov_changed < OVERVIEW_QUIET_S

    def _overview_step(self) -> None:
        """Build the overview a few milliseconds at a time, only while nothing else needs the GUI thread."""
        view = self.view
        if not progressive_mode() or not view.isVisible() or not view.uses_tiles():
            return
        view.sync_geometry()
        level = self._current_level()
        if self.overview is not None and self.overview.dpr != view.devicePixelRatioF():
            self._drop_overview()
        missing_any = self.overview is None or any(k not in self.overview.tiles for k in self._ov_keys)
        if self._overview_busy(level, stale_only=not missing_any):
            self._ov_timer.start(150)
            return
        if self._ov_geometry or self.overview is None:
            if time.monotonic() - self._ov_planned < OVERVIEW_PLAN_S and self.overview is not None:
                self._ov_timer.start(int(OVERVIEW_PLAN_S * 1000))
                return
            self._plan_overview()
        ov = self.overview
        if ov is None:
            return
        todo = [k for k in self._ov_keys if k not in ov.tiles] or [k for k in self._ov_keys if k in ov.stale]
        if not todo:
            ov.stale.clear()
            self._ov_done = True
            return
        deadline = time.perf_counter() + OVERVIEW_SLICE_MS / 1000.0
        for key in todo:
            self._render(ov, [key])
            ov.stale.discard(key)
            if time.perf_counter() >= deadline:
                break
        self._ov_timer.start(25)

