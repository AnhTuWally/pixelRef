"""Prototype progressive-rendering strategies for the canvas benchmark (investigation only).

``apply(names, tiles_module, recorder)`` monkeypatches ``pixelref.ui.canvas.tiles`` /
``CanvasView`` at runtime so a strategy can be benchmarked against the unmodified production code.
Nothing here is imported by the application. Variants combine with commas
(``--variant nosync_zoom,defer_anim``).

* ``nosync_zoom``   while zooming, tiles nothing covers are NOT rendered inside paint(): the area
                    shows whatever fallback exists (or bare background); idle rendering fills it in.
* ``coarse_zoom``   while zooming, uncovered tiles are rendered at a coarser power-of-two zoom
                    (2-4x magnified when drawn), shared by every step of the glide, then refined.
* ``budgetN``       synchronous tile rendering in paint() is capped at N ms per frame (centre-first);
                    tiles over budget are drawn from fallbacks / bare background and rendered next frame.
* ``overview``       (with nosync_zoom / budgetN) a coarse level covering the whole board is built in idle
                    slices and kept as the oldest fallback, so uncovered tiles show it, not bare background.
* ``defer_anim``    tile invalidations that arrive while the user is navigating (animation frames,
                    decoded image levels) are held and applied at most every 250 ms.
* ``minzoom_cache`` ``CanvasView.min_zoom`` reuses the previous content bounds during a glide
                    instead of re-measuring every item after each scene change.
* ``camera_coalesce`` ``camera.changed`` is emitted once per loop pass per view (not per scrollbar change).
* ``pan_coalesce``  ``pan_by`` deltas that arrive in one event-loop pass are applied once.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QTimer


def _zooming(view) -> bool:
    anim = view._animation
    return view._glide_timer.isActive() or (anim is not None and anim.state() == 2)


def _patch_paint(tiles, rec, mode: str, budget_ms: float = 0.0, overview: bool = False) -> None:
    TC = tiles.TileCache
    orig_render_run = TC._render_run_now  # already instrumented by the benchmark

    def coarse_level(self, level):
        zc = 2.0 ** (math.floor(math.log2(level.zoom)) - 1)
        key = (round(zc, 9), round(level.dpr, 4))
        lv = self.levels.get(key)
        if lv is None:
            lv = self.levels[key] = tiles.Level(zc, level.dpr)
        self.levels.move_to_end(key, last=False)  # oldest: painted first, newer levels draw over it
        return lv

    def render_coarse(self, level, keys):
        lv = coarse_level(self, level)
        area = None
        for k in keys:
            r = level.scene_rect(k)
            area = r if area is None else area.united(r)
        span = lv.index_range(area)
        if span is None:
            return
        i0, i1, j0, j1 = span
        need = [(i, j) for j in range(j0, j1 + 1) for i in range(i0, i1 + 1) if (i, j) not in lv.tiles]
        if need:
            self._render(lv, need)

    OV_PIXELS = 3072  # the overview fits the whole board in about this many device pixels

    def ensure_overview(self):
        """A coarse level covering every item, kept as the oldest fallback; built in idle slices."""
        ov = getattr(self, "_ov", None)
        view = self.view
        if ov is None:
            canvas = view.app.canvas
            bounds = canvas.scene_bounds(canvas.visible_content_items())
            if bounds.isNull():
                return None
            zoom = min(1.0, OV_PIXELS / max(bounds.width(), bounds.height(), 1.0))
            ov = self._ov = tiles.Level(zoom, view.devicePixelRatioF())
            span = ov.index_range(bounds)
            i0, i1, j0, j1 = span
            self._ov_keys = [(i, j) for j in range(j0, j1 + 1) for i in range(i0, i1 + 1)]
            ci, cj = (i0 + i1) / 2.0, (j0 + j1) / 2.0
            self._ov_keys.sort(key=lambda k: (k[0] - ci) ** 2 + (k[1] - cj) ** 2)
            self._ov_key = (round(zoom, 9), round(ov.dpr, 4))
            self._ov_timer = QTimer(view)
            self._ov_timer.setSingleShot(True)

            def step():
                busy = (time.perf_counter() - rec.last_input < 0.4 or _zooming(view)
                        or self._idle_timer.isActive() or self._settle.isActive())
                todo = [k for k in self._ov_keys if k not in ov.tiles]
                if not todo:
                    return
                if busy:
                    self._ov_timer.start(150)
                    return
                t0 = time.perf_counter()
                done = 0
                for k in todo:
                    self._render(ov, [k])
                    done += 1
                    if time.perf_counter() - t0 > 0.006:
                        break
                rec.extra["overview_tiles_rendered"] = rec.extra.get("overview_tiles_rendered", 0) + done
                rec.extra["overview_render_ms"] = round(rec.extra.get("overview_render_ms", 0.0)
                                                         + (time.perf_counter() - t0) * 1000.0, 1)
                self._ov_timer.start(25)

            self._ov_timer.timeout.connect(step)
            self._ov_timer.start(200)
        if self._ov_key not in self.levels:
            self.levels[self._ov_key] = ov
            self.levels.move_to_end(self._ov_key, last=False)
        return ov

    def paint(self, painter):
        view = self.view
        hidden = frozenset(view.display.hidden)
        if hidden != self._hidden:
            self._hidden = hidden
            self.clear()
            self._ov = None
        level = self._current_level()
        if overview:
            ensure_overview(self)
        visible = self._visible_keys(level)
        missing = [k for k in visible if k not in level.tiles]
        deferred = []
        if missing:
            fallbacks = [lv for lv in self.levels.values() if lv is not level and lv.tiles]
            now = [k for k in missing if not any(lv.covers(level.scene_rect(k)) for lv in fallbacks)]
            if now and _zooming(view):
                if mode == "nosync":
                    if rec.cur is not None:
                        rec.cur["blank"] += sum(1 for k in now
                                                if not any(lv.covers(level.scene_rect(k)) for lv in fallbacks))
                    now = []
                elif mode == "coarse":
                    render_coarse(self, level, now)
                    fallbacks = [lv for lv in self.levels.values() if lv is not level and lv.tiles]
                    now = [k for k in now if not any(lv.covers(level.scene_rect(k)) for lv in fallbacks)]
            if now and mode == "budget":
                # Centre-first (the key list is already sorted that way); stop once the frame's share is spent.
                deadline = time.perf_counter() + budget_ms / 1000.0
                skipped = 0
                for k in now:
                    if time.perf_counter() >= deadline and k not in level.tiles:
                        skipped += 1
                        continue
                    self._render(level, [k])
                if skipped and rec.cur is not None:
                    rec.cur["blank"] += sum(1 for k in now if k not in level.tiles
                                            and not any(lv.covers(level.scene_rect(k)) for lv in fallbacks))
                now = []
            if now:
                self._render(level, now)
            deferred = [k for k in missing if k not in level.tiles]
            if deferred:
                self._paint_fallbacks(painter, level, deferred, fallbacks)
                self._settle.start(tiles.SETTLE_MS)
        t = view.viewportTransform()
        dpr = level.dpr
        ox, oy = round(t.m31() * dpr), round(t.m32() * dpr)
        painter.save()
        painter.resetTransform()
        tl = level.tiles
        for key in visible:
            image = tl.get(key)
            if image is not None:
                painter.drawImage(QPointF((key[0] * tiles.TILE + ox) / dpr, (key[1] * tiles.TILE + oy) / dpr), image)
        painter.restore()
        if not deferred and not self._settle.isActive() and not self._idle_timer.isActive():
            if any(k not in tl for k in self._visible_keys(level, tiles.PREFETCH)):
                self._idle_timer.start(30)

    # keep the benchmark's instrumentation (visible count, fallback stats) around the patched paint
    instrumented_visible = TC._visible_keys
    instrumented_fallbacks = TC._paint_fallbacks

    def wrapped(self, painter):
        rec.ctx, rec.cur = "paint", {"visible": 0, "sync": 0, "sync_ms": 0.0, "fallback": 0, "blank": 0}
        try:
            paint(self, painter)
        finally:
            rec.ctx = ""

    TC.paint = wrapped
    tiles.MAX_LEVELS = max(tiles.MAX_LEVELS, 8)
    _ = (orig_render_run, instrumented_visible, instrumented_fallbacks)


def _patch_defer_anim(tiles, rec) -> None:
    TC = tiles.TileCache
    orig = TC.invalidate

    def invalidate(self, rects):
        navigating = time.perf_counter() - rec.last_input < 0.30 or _zooming(self.view)
        if not navigating and not getattr(self, "_held", None):
            return orig(self, rects)
        held = getattr(self, "_held", None)
        if held is None:
            held = self._held = []
            self._held_timer = QTimer(self.view)
            self._held_timer.setSingleShot(True)

            def flush():
                pending, self._held = self._held, []
                if pending:
                    orig(self, pending)
                    self.view.viewport().update()

            self._held_timer.timeout.connect(flush)
        held.extend(list(rects))
        if not self._held_timer.isActive():
            self._held_timer.start(250)

    TC.invalidate = invalidate


def _patch_minzoom() -> None:
    from pixelref.ui.canvas.view import CanvasView

    orig = CanvasView.min_zoom

    def min_zoom(self):
        if self._content_bounds is None and _zooming(self):
            prev = getattr(self, "_bounds_prev", None)
            if prev is not None:
                self._content_bounds = prev
        result = orig(self)
        self._bounds_prev = self._content_bounds
        return result

    CanvasView.min_zoom = min_zoom


def _patch_pan_coalesce() -> None:
    from pixelref.ui.canvas.view import CanvasView

    orig = CanvasView.pan_by

    def pan_by(self, delta):
        acc = getattr(self, "_pan_acc", None)
        self._pan_acc = QPointF(delta) if acc is None else acc + delta
        if not getattr(self, "_pan_sched", False):
            self._pan_sched = True

            def flush():
                self._pan_sched = False
                d, self._pan_acc = self._pan_acc, None
                if d is not None:
                    orig(self, d)

            QTimer.singleShot(0, flush)

    CanvasView.pan_by = pan_by


def _patch_camera_coalesce() -> None:
    """Emit ``camera.changed`` once per event-loop pass per view (linked-view sync stays immediate)."""
    from pixelref.ui.app import App

    def on_camera_changed(self, view):
        pending = self.__dict__.setdefault("_cam_pending", set())
        name = view.view_name
        if name not in pending:
            pending.add(name)

            def fire():
                pending.discard(name)
                self.events.emit("camera.changed", {"view": name})

            QTimer.singleShot(0, fire)
        if self._syncing_cameras or not view.link_group:
            return
        self.sync_linked_cameras(view)

    App._on_camera_changed = on_camera_changed


def apply(names, tiles, rec):
    ov = "overview" in names.split(",")
    for name in names.split(","):
        if name == "overview":
            if not any(n in ("nosync_zoom",) or n.startswith("budget") or n == "coarse_zoom"
                       for n in names.split(",")):
                _patch_paint(tiles, rec, "nosync", overview=True)  # overview alone = nosync + overview
        elif name == "nosync_zoom":
            _patch_paint(tiles, rec, "nosync", overview=ov)
        elif name == "coarse_zoom":
            _patch_paint(tiles, rec, "coarse")
        elif name.startswith("budget"):  # budget4, budget8: ms of synchronous tile rendering per frame
            _patch_paint(tiles, rec, "budget", float(name[6:] or 6), overview=ov)
        elif name == "defer_anim":
            _patch_defer_anim(tiles, rec)
        elif name == "minzoom_cache":
            _patch_minzoom()
        elif name == "camera_coalesce":
            _patch_camera_coalesce()
        elif name == "pan_coalesce":
            _patch_pan_coalesce()
        else:
            raise SystemExit(f"unknown variant {name}")
