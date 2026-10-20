# Canvas navigation performance review

Status: review complete, one change made (tile threshold). Remaining items are candidates, not commitments.

This records an evaluation of an external review of pan / wheel-zoom performance, what we measured, what we changed, and where future effort is worth spending.

## Summary

- Painting, not per-event Python overhead, decides how smooth navigation feels. The per-pixel costs the review focused on are microseconds against a 16.7 ms frame budget.
- The one change with a large measured effect was the tile-cache gate: it was `300` items, which left boards of 10–299 items painting every item every frame. It is now `10` (`CanvasView.TILE_MIN_ITEMS`, `pixelref/ui/canvas/view.py`).
- The review's other proposals were measured or analysed and are not worth doing for speed. One (caching the scene center during a pan) is incorrect.
- The best remaining targets are cold-tile renders at a new zoom level, tile memory on small boards, and the ~18–20 ms pan floor.

## Method

- Benchmarks: `tests/bench_interactive.py` (real wheel / middle-drag events timed until the frame is painted) plus throwaway session and profiling scripts built on the same harness.
- Platform: Windows, GL viewport (`CanvasGLWidget`), maximized, 2226×1325 at dpr 1.0, `QT_QPA_PLATFORM=windows`. The offscreen platform has no vsync or GL swap and is not representative for frame timing.
- Boards: generated photos (512 / 1024 / 2048 px) and annotations, 3–200 images, 20–600 notes.
- Each configuration was run once unless noted. Differences of a few milliseconds, and small counts of slow frames, are within noise.
- Process memory (RSS / commit) was too noisy to use (session growth of -16 to +273 MB with tiles off). Tile memory is reported as `tile_count × 256 KB` from the tile cache.

## Findings from the external review

| # | Proposal | Verdict | Evidence |
|---|---|---|---|
| 1 | Glide timer 8 ms → 16 ms ("half the steps are wasted paints") | Not worth doing | Counting real paints over a 10-notch zoom burst: 8 ms gave 61 / 50 / 66 / 58 paints, 16 ms gave 56 / 51 / 60 / 59. No meaningful difference. Qt coalesces updates (1.0–1.5 steps per paint, not 2). `_glide_step` is time-based, so zoom speed is identical (final zoom matched in every run). The "52% wasted" figure was arithmetic (125 steps/s vs 60 Hz), not a measurement. Each step costs about 0.23 ms outside painting. |
| 2 | Cache the scene center during a pan | Incorrect | The point under the viewport center moves during a pan. `_camera_center` feeds `camera()` and linked-view sync (`app.py` `sync_linked_cameras`); caching it would make linked views and saved state stale. `center()` + `zoom_level()` cost ~0.01 ms per call anyway. |
| 3 | Guard `update_zoom` / cache `target_view()` | Valid, negligible | `_camera_moved` plus all listeners costs ~0.14 ms per call. Safe tidy-up, nothing a user would feel. |
| 4 | Lower or adapt the tile threshold | Done, large effect | See below. |
| 5 | Guard `_stop_animation()` | True, negligible | ~0.001 ms per call. |

A follow-up version of the review reported microsecond costs from `tools/bench_nav_perf.py` (not in the repo, so not reproduced). Taking its numbers at face value, `pan_by` is ~13 µs per pixel against a frame of 16,700–27,000 µs, about 0.08%. "33% of `pan_by`" is not 33% of a frame. That harness also ran offscreen and re-implemented the hot path, so it cannot see paint, swap or vsync cost.

## The change: tile threshold 300 → 10

`TILE_MIN_ITEMS` gated the tile cache on item count. Below the gate every item's Python `paint` ran every frame.

Measured, mean ms per event (tiles off → on):

| Board | Pan, fit all | Pan, zoomed in | Wheel zoom in |
|---|---|---|---|
| 200 photos (1024 px) | 46.6 → 24.4 | 34.3 → 26.3 | 43.6 → 15.1 |
| 150 notes only | 42.4 → 18.5 | 24.3 → 20.8 | 17.5 → 15.1 |
| 100 photos (512 px) | 28.4 → 18.7 | 23.5 → 18.8 | 26.3 → 10.5 |
| 3 photos | 24.4 → 19.0 | 22.9 → 20.3 | 15.0 → 10.1 |

- Tiles won at every size and count tested, down to 3 items. Image size (512–2048 px) made no difference.
- Paint time during a pan dropped from 5–18 ms to about 2 ms.
- Gains below ~20 items are only a few milliseconds of paint per frame, so 10 is a floor, not a tuned optimum.
- `PIXELREF_TILES=0` / `=1` still override the threshold. The docs table in `architecture.md` was updated.
- The full test suite passed apart from `test_annotations.test_pick_mode_shows_loupe_and_live_color`, which fails identically without this change.

### Costs of the change

- **Tile memory on small boards.** Tiles cover the background grid and the pre-render ring, not just items. A 3-image board held 441 tiles (116 MB) at this viewport; 200 photos held 240 (63 MB). Memory is bounded (3 zoom levels, 256 MB per view, `tiles.py`), but the 55–120 MB figure applies per view and scales with viewport pixels. Multi-view layouts were not tested.
- **Slow frames.** In a 384-frame zoom-and-pan session, tiles on gave fewer frames over 50 ms at 3, 15, 20 and 200 items, and the two worst frames overall were tiles-off. The exception was 40 images: 3 frames over 50 ms with tiles on (worst 81 ms) against none off (worst 47 ms). Single run, probably noise, but not ruled out.

## Where the time actually goes

- Pan step (tiles on): about 19–20 ms per event, of which paint is ~2 ms. Hover with nothing moving costs 10–12 ms. The harness blocks on the GL buffer swap, so vsync wait is mixed into these figures.
- Scrollbar `setValue`: about 1.2 ms per call, two calls per `pan_by` (~2.4 ms per step). This is the largest pan-specific cost we could identify.
- Each `pan_by` emits `camera_changed` three times (two scrollbar `valueChanged` connections at `view.py:210-211`, plus the explicit `_camera_moved` call). About 0.4 ms in total.
- ~13,000 item `boundingRect` calls per 60 pan steps come from `QGraphicsScene.render` during idle tile pre-rendering, about 1.5 ms per step. Not on the pan hot path.
- Cold tile renders: the first frame at a new zoom level took 60–150 ms, and some zoomed-in pan frames took 40–90 ms. These are the largest stutters observed.

## Candidate future work

Ordered by expected value. None has been implemented.

1. **Cold-tile stutter** (`tiles.py`). Investigate the 60–150 ms first frame at a new zoom level and 40–90 ms spikes while panning zoomed in: slice size (`SLICE_MS`), settle delay (`SETTLE_MS`), pre-render ring (`PREFETCH`), and whether a coarser interim level can be shown sooner. Highest user-visible payoff.
2. **Re-test the 40-image case.** Repeat the session benchmark several times with tiles on and off to decide whether the 3 slow frames were noise or a real cold-render effect at that size.
3. **Tile memory on small boards.** Options: restrict the ring and fallback levels when the scene is small, release unused levels after idle, or raise the gate (30 is the middle option; it costs nothing under 30 items, where the gain is a few ms of paint). Measure a two-view layout first.
4. **Pan floor of ~18–20 ms.** Needs a measurement that separates GL swap / vsync from real work (GPU capture, or timing inside `paintGL`). Candidate: avoid two scrollbar `setValue` calls per step by moving the viewport once.
5. **Low-priority tidy-ups** (no measurable effect): collapse the three `camera_changed` emissions per pan step into one; skip `ZoomBlock.update_zoom` `setText` when the label is unchanged; guard `_stop_animation`. Do these only for code clarity.

Not recommended: the 16 ms glide timer, caching the scene center during a pan.

## Reproducing

```
set QT_QPA_PLATFORM=windows
set BENCH_MAXIMIZED=1
python tests/bench_interactive.py <images> <notes>      :: PIXELREF_TILES=0|1 to compare
set BENCH_SIZE=2048                                      :: photo size
set PIXELREF_PERF=1                                      :: perf spans, slow ones printed
```

The session benchmark (384 frames of zoom and pan with pauses) and the paint-per-glide-step counter were throwaway scripts and are not in the repo.
