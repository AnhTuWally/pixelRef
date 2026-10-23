# Canvas Navigation Performance Review

Performance review of the pixelRef canvas, focused on making navigation (pan / wheel-zoom) as fast and seamless as possible.

## How navigation works (context)

- **Pan** (middle/right/Space-drag) → `pan_by` → scrollbar `setValue` → `valueChanged` → `_camera_moved` → `camera_changed.emit()` → all listeners.
- **Wheel zoom** → `glide_zoom` → 8 ms `QTimer` → `_glide_step` → `scale()` + scrollbars → `_camera_moved`.
- **Paint** → `_paint_event` → tile blit (≥300 items) or direct item render.

The code is already well-optimized (tile cache, batched grid, cached camera names, cached minimap bounds, `perf` spans). The remaining wins are in the **per-frame overhead that runs on every single pan/zoom step**, not in the rendering itself.

## Findings (ordered by measured impact)

> Numbers below are from the benchmark in [Benchmark validation](#benchmark-validation) (`tools/bench_nav_perf.py`).

### 1. Wheel-zoom glide timer runs at 8 ms — faster than vsync, so half the steps are wasted paints ✅ *implemented*
`_glide_timer.setInterval(8)` fired ~125×/sec, but each step is a full viewport paint that the display only shows at 60 Hz (16.6 ms). **Measured: at 8 ms, 52% of the steps render a frame that is immediately overwritten; at 16 ms the waste drops to ~4%.**

**Fix (implemented):** pace the interval to the display's refresh rate via `frame_interval_ms()` in `view.py` — `max(8, round(1000 / refresh_rate))`, cached, with a 16 ms fallback when headless. On a 60 Hz display this yields **17 ms → 59 steps/s → ~0% invisible steps** (was 52% at 8 ms). The glide easing is time-based (`1 - exp(-elapsed / GLIDE_TIME)`), so the duration and feel are unchanged — only the wasted steps are removed.

### 2. `_camera_moved` recomputes the scene center (transform inversion) on every pan pixel
With `sync_state=True`, `_camera_moved` calls `self.center()` — which inverts the viewport transform and maps a point — and `zoom_level()` on **every** pan pixel, just to keep `_camera_center` in sync. **Measured: `center()` costs 4.28 µs and `zoom_level()` 0.98 µs — together ~5.26 µs, about 33% of the whole `pan_by()` (12.89 µs). This is the single largest per-pixel cost in the pan path.**

**Fix (low-risk):** during a pure pan the scene point under the viewport center is constant, so compute it once at drag start and only re-read after the drag ends / on zoom. At minimum, guard the read-back so it isn't repeated when the value is already current.

### 3. `camera.changed` fans out synchronously on every pan pixel — and `update_blocks` does real work for a label that doesn't change
`camera_changed` is emitted on **every** scrollbar change (`view.py:491`). One listener, `update_blocks` → `update_zoom`, calls `target_view()` for **every** window block on **every** pan step. `target_view()` does `active_view()`, `doc.workspace_of()`, `doc.output_showing()`, and — when the active view isn't the target — `app.wm.visible_tiles()` plus a loop over **all** views. All of this is to call `zoom_label.setText(...)`, which is a no-op during a pan (zoom is constant). **Measured: `target_view()` is 0.31 µs (active match) to 0.97 µs (fallback path); two window blocks add ~3.01 µs/pixel.** Real, but a "many small costs" issue rather than one big one.

**Fix:** cache the last label text per block and skip when unchanged, and/or cache the resolved target view.

### 4. Tile cache is count-gated at 300 items — a board of ~200 large images still re-paints every item per frame
`uses_tiles` only enables the (fast) tile blit when `len(items) >= TILE_MIN_ITEMS` (300). Below that, every pan/zoom frame re-invokes each item's Python `paint`. A board with 150–299 large images is exactly the case that feels slow, yet falls just under the threshold. *(Not covered by the micro-benchmark — this is a per-frame render-cost issue, not a per-pixel overhead.)*

**Fix:** make the threshold adaptive (e.g., also enable tiles when total visible pixel area is large), or lower the default. This is already overridable via `PIXELREF_TILES=1`, so it's a tuning/robustness improvement.

### 5. Minor: `pan_by` calls `_stop_animation()` on every pixel
`pan_by` calls `_stop_animation()` per mouse-move, which touches the animation object and calls `QTimer.stop()` on an already-stopped timer. **Measured: 0.46 µs/pixel** — cheap, but it's on the hot path; a `if self._animation is not None or self._glide_timer.isActive():` guard avoids the calls in the common case.

## What's already good (no change needed)
- **Tile cache** (`tiles.py`) — blits cached device-pixel tiles, pre-renders a ring when idle, renders new zoom levels in slices. This is the right architecture and the main reason large boards pan well.
- **Background** — `CacheBackground` is set, so Qt caches the grid; the "crosses"/"dots" paths are already batched into single `drawLines`/`drawPoints` calls.
- **Camera markers** — `_camera_names` caches the node walk instead of scanning every node per frame.
- **Minimap** — reuses last-render content bounds instead of re-measuring items per camera move.

## Suggested priority (by measured impact)
1. **Wheel-glide timer → refresh-rate paced (17 ms on 60 Hz)** ✅ *implemented* — was the biggest waste: 52% of zoom steps were invisible; now ~0%.
2. **Cache the scene center during a pure pan** (largest single per-pixel cost: ~4.3 µs, ~33% of `pan_by`).
3. **Guard `update_zoom` / cache the resolved target view** (modest, ~0.6 µs per block).
4. **Adaptive tile threshold** (per-frame render cost for 150–299-item boards).
5. **Guard `_stop_animation`** (minor, 0.46 µs/pixel).

---

## Benchmark validation

Measured with `tools/bench_nav_perf.py` (Python 3.13, PySide6 6.10.3, offscreen platform, 1600×900 viewport). The harness replicates the **exact** hot-path code from `view.py` (`pan_by`, `_camera_moved`, `center`, `to_scene`, `zoom_level`, `_stop_animation`) and the real `ZoomBlock.target_view`/`update_zoom` from `navigation/__init__.py`, then times each per mouse-move pixel.

```
[F3] _camera_moved per-pixel read-back:
     view.center()      (transform inversion + map):     4.28 us
     view.zoom_level()  (transform read)            :     0.98 us
     combined per pan pixel                          :     5.26 us

[F5] _stop_animation() per pan pixel                 :     0.46 us

[F1] camera_changed listener (ZoomBlock, 3 views, active match):
     target_view()  (active_view + doc lookups)      :     0.31 us
     update_zoom()  (target_view + setText)          :     0.62 us
     target_view()  (fallback: visible_tiles + loop) :     0.97 us

[F2] wheel-glide timer vs 60 Hz display:
     8 ms  timer:    125 steps/s ->  52.0% of steps produce no visible frame
     17 ms timer (refresh-rate paced):     59 steps/s ->   0.0% of steps produce no visible frame

[ALL] full pan_by() per mouse-move pixel (no listeners)      :    12.89 us
[ALL] full pan_by() per pixel + 2 ZoomBlock listeners         :    15.90 us
     listener overhead per pixel                             :     3.01 us
```

### What the numbers confirm (and correct)

- **F2 is the strongest, clearest win — now implemented.** At 8 ms the glide timer fired ~125×/sec but a 60 Hz display shows only 60 frames/sec — **52% of the steps rendered a frame that was immediately overwritten**. The refresh-rate-paced interval (17 ms on 60 Hz) drops the waste to ~0% and halves the paint work of wheel zoom, with no change to the glide feel (the easing is time-based).
- **F3 is the single largest per-pixel cost in the pan path.** `center()` (a `viewportTransform().inverted()` + map) costs **4.28 µs — about 33% of the whole `pan_by()` (12.89 µs)**. During a continuous drag this runs on every mouse-move event. Caching the scene-center during a pure pan (it is constant) removes it.
- **F1 is real but smaller than first ranked.** Two window blocks add **3.01 µs/pixel** (≈0.62 µs each). It is a "many small costs" issue, not one big one — still worth guarding (skip `setText` when the rounded % is unchanged), but it is not the dominant cost.
- **F5 is confirmed minor** (0.46 µs/pixel) — a cheap guard, low priority.

The measured order matches the [Suggested priority](#suggested-priority-by-measured-impact) section above.
