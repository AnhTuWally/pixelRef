# 5. Implementation Roadmap

Written so another engineer or coding agent can implement it without this investigation's context. Read
[01](01-canvas-performance-audit.md) for the code map and [03](03-benchmark-report.md) for how to measure. Line
numbers are from the investigated commit (branch `persistent-workspace`, `0f0e102`) and will drift.

**Ground rules**

* Measure before and after with `tools/bench_canvas_nav.py` on the same machine; compare against `data/*.jsonl`.
  Differences under ~10% are noise (see report §3.3). Take ≥3 runs for any gate.
* Keep every behaviour change switchable until its gate passes (`PIXELREF_*` env var, like `PIXELREF_TILES`).
* Do not change file/scene formats. All work is view-side; `Level`, `_idle`, `_paint_fallbacks`, `ImageHandle` are reused.
* Run the existing suite (`tests/run_tests.py`; `tests/test_tiles.py`, `test_canvas.py`, `test_views.py`,
  `test_performance.py` are the most relevant) after every step. One test fails on `main` already
  (`test_annotations.test_pick_mode_shows_loupe_and_live_color`, noted in `docs/perf-navigation-review.md`).

Targets, from the baseline (GL, 59.95 Hz, budget 16.68 ms):

| Gate | Baseline | Target |
|---|---|---|
| Settled zoom, active-window p95, medium/large/hires/stress | 76 / 97 / 115 / 459 ms | ≤ 25 ms on all four |
| Settled zoom, worst `paint` frame | 51 / 324 / 116 / 343 ms | ≤ 70 ms |
| Blank tile share during zoom (no-GIF boards) | 0% (stutters instead) | ≤ 5% |
| Visible tiles refined after glide ends | 0.09–1.56 s | ≤ 0.3 s (≤ 1 s stress) |
| 4 linked views, warm pan | 2.6 fps, p95 892 ms | ≥ 55 fps, p95 ≤ 20 ms |
| 500 Hz / 1000 Hz synthetic input, medium pan | 17.8 / 1.2 fps | ≥ 58 fps both |
| Wheel handler p95, stress | 42 ms | ≤ 1 ms |
| Warm pan, medium/large (regression guard) | p95 17.4–18.6 ms, 59.8 fps | no worse than +1 ms p95 |
| Tile memory per view (settled zoom) | 28–35 MB | ≤ 80 MB, shared overview counted once |

---

## Phase 1: immediate responsiveness (low risk, independent steps)

### 1.1 `min_zoom` bounds survive repaint-only scene changes

* **Problem.** `CanvasView._on_scene_changed` (`view.py:251`) sets `_content_bounds = None` on every
  `content_changed`, including pure repaints (decode deliveries, GIF frames). `min_zoom()` (`view.py:327`) then calls
  `canvas.scene_bounds(canvas.visible_content_items())`: 1.5 ms on 200 items, 42 ms on 5000, once per wheel notch.
* **Approach.** Invalidate `_content_bounds` from the events that can change geometry (`canvas.refreshed`,
  item add/remove, `item.geometry_changed`, transform edits), not from `content_changed`. Keep `_on_scene_changed`
  for tiles and the grayscale cache. Fallback: recompute at most once per 500 ms during a glide.
* **Files.** `pixelref/ui/canvas/view.py` (`_on_scene_changed`, `min_zoom`, new `invalidate_content_bounds`),
  `pixelref/ui/canvas/document.py` (call it where items are added/removed/moved).
* **Tests.** Add a test that adds/moves/removes an item and checks `min_zoom` reflects it; one that a decode
  delivery does not reset `_content_bounds`.
* **Gate.** Stress wheel-handler p95 ≤ 1 ms (`--board stress --scenario zoom`, `handler_ms.p95`).
* **Rollback.** Revert the commit; no data change.

### 1.2 Coalesce `camera.changed` and linked-view sync

* **Problem.** One pan step emits `_camera_moved` three times; each emits `camera_changed` → `App._on_camera_changed`
  (`app.py:725`) → bus event + `sync_linked_cameras`. With N linked views each follower repeats this. The minimap
  listener (`minimap/__init__.py:416`, `board_rect` at `:174`) loops all views per call.
* **Approach.** (a) In `CanvasView`, collapse the scrollbar-driven and explicit `_camera_moved` into one
  emission per loop pass (`QTimer.singleShot(0)` or flag + `QMetaObject.invokeMethod(Qt.QueuedConnection)`);
  keep `_camera_center`/`_camera_zoom` updated synchronously (callers read them). (b) In
  `App._on_camera_changed`, emit the bus event once per view per loop pass and run `sync_linked_cameras` once
  per pass, driven by the leading view only (the `_syncing_cameras` guard stays). (c) Minimap `repaint`: compute
  `board_rect` once per camera change, not per widget per view; skip when the widget is hidden.
  Prototype: `_patch_camera_coalesce` in `tools/bench_variants.py`.
* **Files.** `view.py: _camera_moved`, `app.py: _on_camera_changed, sync_linked_cameras`,
  `builtins/minimap/__init__.py: repaint, board_rect`.
* **Tests.** `tests/test_views.py` linked-view tests must still pass; add: after `pan_by`, `camera()` is current
  immediately; after one event-loop pass, subscribers saw exactly one `camera.changed` per view; follower cameras
  equal the leader's.
* **Gate.** 4 linked views warm pan ≥ 55 fps (prototype reached 35–40; expect to need (c) and per-pass follower
  batching to reach the gate; if not reached, report the number rather than relaxing the test).
* **Risk.** Listeners that assume a synchronous event. `grep camera.changed` shows: color picker, minimap,
  navigation, quick edit, views header: all refresh-type listeners.
* **Rollback.** Env switch `PIXELREF_CAMERA_COALESCE=0`.

### 1.3 Coalesce pan input

* **Problem.** Each mouse-move runs `pan_by` (1.4 ms core, 2.2–2.9 ms handler). Sustainable ≈400 events/s; above
  that the queue grows (synthetic 500 Hz: 17.8 fps).
* **Approach.** `NavigationHandler.pointer` accumulates `e.pos − op["last"]` into the view and applies it once
  per loop pass (flush also at press/release and before any scripted camera read). Keep `CanvasView.pan_by(delta)`
  synchronous for scripts/commands (`navigation.pan`); add `CanvasView.pan_input(delta)` for the pointer path.
  Prototype: `_patch_pan_coalesce`.
* **Files.** `builtins/navigation/__init__.py: NavigationHandler.pointer`, `view.py`.
* **Tests.** Pan 100 events then release: final camera equals the unbatched result; release flushes pending
  delta; scripted `pan` still immediate.
* **Gate.** Synthetic 500 Hz and 1000 Hz medium pan ≥ 58 fps; 125 Hz unchanged (p95 ≤ baseline + 1 ms).
* **Rollback.** Env switch.

### 1.4 Index `EventStream` listeners by event type (optional, after 1.2)

`pixelref/core/events.py: EventStream.on/emit` builds a filter closure per `on()` and `emit` calls every closure.
Store typed listeners in `dict[str, list]` plus a list for wildcards/untyped, preserving subscription order by a
serial number. **Test:** ordering between typed and untyped listeners; unsubscribe during emit. Expected
saving ≈0.05–0.08 ms per emit (estimate). Only do it if 1.2 leaves it visible in a profile.

### 1.5 Investigation (no change yet): GL `paintEvent` cost

**Done: see [07-gl-paint-investigation.md](07-gl-paint-investigation.md)** (the 4 ms is a blocking wait, not work; no-go on a
GL paint fix; `QT_WIDGETS_RHI=1` is a candidate to validate). The text below is the original plan.

Warm pan frames cost 5.8–6.0 ms in `paintEvent` on the GL viewport vs 2.1 ms software for identical tile blits.
Capture with PIX/ETW or time `QPainter.end()` separately in `CanvasView._paint_event`. Deliverable: a note in
`docs/performance/` saying where the 4 ms goes, and a go/no-go for a GL-specific fix.

**Phase 1 exit.** Gates for 1.1–1.3 pass; full test suite green; warm pan regression guard holds.

---

## Phase 2: progressive rendering and cache (extends `TileCache`)

Do these in order; each is behind `PIXELREF_TILE_PROGRESSIVE` until its gate passes. Prototype reference:
`tools/bench_variants.py` (`_patch_paint`, overview functions).

### 2.1 `navigating` state and no synchronous render while zooming

* **Approach.** `CanvasView.navigating()` returns true while `_glide_timer.isActive()`, a camera animation runs, a
  drag/pan op is active, or within 150 ms of the last navigation input. In `TileCache.paint`
  (`tiles.py:178`), when `view.navigating()`: do not call `_render(level, now)`; draw fallbacks for all
  missing tiles; start `_settle`. Exception: if nothing at all covers a tile and there is no overview yet, render
  at most one tile per frame, centre-first. After navigation ends the existing `_idle` path refines.
* **Files.** `tiles.py: paint`, `view.py: navigating, glide_zoom, _glide_step, _animate_camera`,
  navigation handler (mark drag activity).
* **Do not** add budgeted synchronous rendering or coarse tiles (rejected, report §2.3).
* **Tests.** `tests/test_tiles.py`: with a mocked `navigating()` true, a paint of uncovered tiles renders none
  (beyond the exception) and schedules `_settle`; once false, the tiles render and the view is exact; an item edit
  during navigation is visible after navigation.
* **Gate (with 2.2).** See the table above. This step alone is *expected to produce blank regions*; do not ship it
  without 2.2.

### 2.2 Overview level

* **Approach.** `Level` gains `overview: bool`. `TileCache.ensure_overview()` picks `zoom = min(1, 3072 / max(bounds.w,
  bounds.h))`, registers the level first in `self.levels` and exempts it from the `MAX_LEVELS` eviction and from
  `_current_level`'s "delete empty levels" pass. Build it from `_idle` at the lowest priority: only when no visible
  or ring tile is missing, `navigating()` is false, in 6 ms slices. Include it in `fallbacks` in `paint`; the
  `covers`/`_paint_fallbacks` path already draws partial coverage. Rebuild when `scene_bounds` changes by >25%.
  **Share it** between views of the same scene and dpr (a scene-level object owned by `CanvasScene` or the
  canvas document), keyed by `(dpr, bounds_signature)`; each view still owns its normal levels.
* **Files.** `tiles.py` (`Level`, `TileCache`, a new `OverviewCache`), `view.py` (`_on_scene_changed` forwards
  rects to the shared overview), `document.py` (content bounds change signal).
* **Memory.** Count it in `BUDGET_BYTES` accounting; expected +≈40 MB (once, not per view).
* **Tests.** Overview exists after load + idle; zoom path uses it (no `_render` in paint); edits invalidate its
  tiles; two views share one overview object; memory cap respected; empty board creates none.
* **Gate.** All rows of the table: settled zoom p95 ≤ 25 ms on medium/large/hires/stress; blank share ≤ 5%
  (needs 2.3 to get there on no-GIF boards); refine ≤ 0.3 s.
* **Risk.** Blank/blurry regions; first zoom right after load before the overview is built falls back to today's
  behaviour (acceptable). Overview build on 5000 items takes ≈2.7 s of GUI time in slices.
* **Rollback.** Env switch off; levels list is unchanged when disabled.

### 2.3 Scope invalidation of decode deliveries

* **Problem.** `ImageHandle.deliver` (`image/source.py`) calls `item.update()` for every waiting item, dropping
  tiles under it on all levels (hypothesised cause of the 14–25% blank tiles).
* **Approach.** In `PixmapItem._draw_handle` record the level it wanted (`self._wanted_level`). In `deliver`, call
  `update()` only for waiting items whose `_wanted_level` equals the delivered level or whose currently shown
  level is coarser than the delivered one *and* the delivered level is ≤ wanted. Keep full `update()` for edits.
* **Files.** `builtins/image/items.py`, `builtins/image/source.py`.
* **Tests.** Existing image tests (`test_canvas.py`, `test_performance.py`); add: delivering a finer-than-wanted
  level does not call `update()`; delivering the wanted level does; an item never stays on a coarser level than
  decoded.
* **Gate.** Blank share ≤ 5% in the settled-zoom runs; invalidations per run drop (report `tiles.invalidations`).

### 2.4 Animated items outside the baked tiles

* **Problem.** Every GIF frame invalidates tiles at the current level, which re-render synchronously.
* **Approach (needs a prototype first; benefit unmeasured).** Add `NodeItemMixin.animated_live` (true for
  `PixmapItem` with a movie). While baking a tile (`rendering_for(view)` is active) animated items skip painting;
  `TileCache.paint` then draws animated items live over the tiles with `scene.render` restricted to their rects
  (or paints them directly). Animation frames call `viewport().update()` for their rect instead of invalidating.
  Resolve z-order: animated items stay in scene order relative to annotations or are documented as drawn on top.
* **Gate.** `gifonly` pan p95 ≤ 20 ms (baseline 33.7) and zoom p95 ≤ 30 ms (baseline 85); `mixed` pan ≥ 58 fps;
  paints per second ≤ display rate. If the prototype misses by >30%, drop this step and keep only the
  `defer_anim` throttle.

### 2.5 Refinement policy

* **Approach.** (a) `SETTLE_MS` is restarted by every deferred frame, so refinement never starts mid-drag; allow
  refining visible tiles when pointer speed is low (`< ~300 px/s`). (b) During input, shrink `SLICE_MS` to ~4 ms
  or pause the ring prefetch. (c) Priority: visible-missing → visible-from-fallback → ring → overview.
* **Measure first**: the evidence for (b) is weak (settled pan p95 28 vs 17 ms; `SLICE_MS` 4/2 gave 28.3/23.7 in
  single runs). Take ≥5 runs per setting (`--set tiles.SLICE_MS=N`) and only adopt what beats noise.
* **Gate.** Cold jump → full quality median ≤ 150 ms (baseline 148–439); cold pan after jump: frames with
  magnification > 1.25 ≤ 50% of the drag.

**Phase 2 exit.** All gates pass on medium/large/hires/mixed; stress is allowed to miss pan-at-speed targets
(Phase 3). Tests: add a blank-tile/stale-content regression test that edits an item during and just after a glide.

---

## Phase 3: only if Phase 2 leaves the large-board gaps

Trigger: stress (5000) pan at ≥3000 px/s still has p95 > 50 ms, or tile render > 10 ms/tile at fit zoom.

* **Reduce items per tile at far zoom.** Evidence: ≈300 items under a stress fit-zoom tile, ≈12.5 ms/tile
  (≈40 µs/item); caching `boundingRect` gave nothing. Options to prototype and measure, in order: (1) skip items
  whose device size < 1.5 px; (2) draw a pre-reduced proxy (the lowest decoded `ImageHandle` level as a single
  `drawPixmap`) bypassing `paint_allowed`/filters for items with no effects/crop; (3) tune
  `QGraphicsScene.setItemIndexMethod`/BSP depth (not measured). Selection and hit testing must keep using real items.
* **Shared tile store across views** of the same scene (saves memory in split layouts). Hypothesis only.
* **Do not start** worker-thread tile rendering or a GPU compositor without a profile that shows a bottleneck
  they remove.

---

## Rollback and release

Each step ships separately with its own switch and gate result in the commit message. Add the benchmark JSON of
the final run under `docs/performance/data/` and a one-paragraph update to `docs/architecture.md` (tile cache
section) and `CHANGELOG.md`. Remove the prototype variants from `tools/bench_variants.py` once the feature is in
(keep the harness).
