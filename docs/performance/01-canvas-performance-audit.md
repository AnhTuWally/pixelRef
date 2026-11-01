# 1. Canvas Performance Audit

Status: investigation complete, no production code changed. Evidence is in
[03-benchmark-report.md](03-benchmark-report.md) and `data/`. Measurements are from one Windows machine
(see the report); anything not measured is labelled **hypothesis**.

## 1.1 What the canvas actually does

The view is a `QGraphicsView` (`pixelref/ui/canvas/view.py: CanvasView`) over one shared `CanvasScene`. With 10
or more items and an axis-aligned camera (`uses_tiles`, `view.py:265`, `TILE_MIN_ITEMS = 10`) it does not paint
items per frame: `TileCache` (`tiles.py`) renders the scene into 256 device-pixel tiles per zoom level and
paints frames by blitting them. Everything below is that path.

```
 INPUT                       CAMERA                          NOTIFY                           PAINT
 ─────                       ──────                          ──────                           ─────
 middle/right/space drag ─►  NavigationHandler.pointer       CanvasView.pan_by (view.py:534)
 (navigation plugin,         (builtins/navigation:34)  ───►   _stop_animation
  priority 900)                                               hbar.setValue ─► valueChanged ─┐
                                                              vbar.setValue ─► valueChanged ─┼► _camera_moved ×3
 wheel / trackpad ────────►  NavigationHandler.wheel                                         │   (view.py:541)
                             ─► glide_zoom (view.py:475)                                     │   center()+zoom_level()
                                _clamp_zoom_out ─► min_zoom()  ◄── 42 ms on a 5000-item     │   camera_changed.emit()
                                start 17 ms PreciseTimer          board after any scene     │        │
                                                                  change                    │        ▼
 timer tick ──────────────►  _glide_step (view.py:490)                                      │   App._on_camera_changed (app.py:725)
                              scale() + 2×setValue ─► _camera_moved ───────────────────────┘     ├► events.emit("camera.changed")
                                                                                                  │    EventStream.emit (core/events.py:57)
 set_camera / frame_all /                                                                         │    walks EVERY listener; ~65-83 closures
 jump / animation ────────►  _apply_camera: setTransform+centerOn                                 │    ├ minimap.repaint (O(views) board_rect each)
                              ─► _camera_moved                                                    │    ├ navigation.update_blocks (ZoomBlock)
                                                                                                  │    ├ views header button, color picker, quick edit…
                                                                                                  │    └ …
                                                                                                  └► sync_linked_cameras ─► follower.set_camera
                                                                                                         (≈1.7 ms each, then ITS camera_changed…)

 scrollbar change ─► QGraphicsView scrolls viewport ─► FullViewportUpdate ─► QPaintEvent (Qt coalesces to one per loop pass)

 CanvasView.paintEvent ─► _paint_event (view.py:772) ─► TileCache.paint (tiles.py:178)
     _current_level()           one Level per distinct zoom (round(zoom, 9)); MAX_LEVELS = 3
     missing = visible − cached
     now = missing not fully covered by ANY other level  ─►  _render(level, now)   ◄── SYNCHRONOUS, GUI thread
     deferred = still missing ─► _paint_fallbacks (older levels' tiles, scaled) ; _settle.start(140 ms)
     blit visible tiles
     if nothing deferred and ring (±1 tile) incomplete ─► _idle_timer.start(30)
   _paint_overlays: tool overlay, minimap-independent overlays, markers, banner

 TileCache._idle (settle/idle timers): renders missing visible tiles then the ring, one tile at a time, in
   12 ms slices (SLICE_MS), re-arming with 0 ms until done. Runs on the GUI thread, also while the user navigates.

 Scene change (item.update(), decode delivered, GIF frame, edit) ─► QGraphicsScene.changed ─►
   CanvasScene._on_changed ─► content_changed ─► CanvasView._on_scene_changed (view.py:251):
        _content_bounds = None ; gray cache dropped ; TileCache.invalidate(rects)  (ALL levels)

 Image pixels (builtins/image/source.py, items.py): ImageHandle keeps decoded power-of-two levels,
   decodes on a QThreadPool, draws the nearest decoded level meanwhile (placeholder if none), then item.update().
   Whole pictures (GIF frames, effect results) use background mip chains (MipStore).
```

### Work done per event

| Trigger | What runs on the GUI thread | Measured cost |
|---|---|---|
| Each mouse-move in a pan | input pipeline → `NavigationHandler` → `pan_by`: 2 scrollbar `setValue` (0.58 ms each), 3 × `_camera_moved` (scrollbar ×2 + explicit), 3 × `camera.changed` bus emits | `pan_by` 1.4 ms; whole handler p95 2.2–2.9 ms |
| Each wheel notch | `glide_zoom` → `_clamp_zoom_out` → `min_zoom()` (re-measures every item if `_content_bounds` was dropped) → timer start | 0.2 ms on ≤1000 items; **41–44 ms on 5000 items** |
| Each glide step (17 ms) | `scale()`, 2 × `setValue`, `_camera_moved(sync_state=True)`, then a paint at a **new zoom level** | see zoom results |
| Each `camera.changed` | iterate all bus listeners; minimap `board_rect()` (loops all views); zoom label; marker button; … | ~0.1 ms per emit single view; dominates linked views |
| Each linked follower | `set_camera` → `_apply_camera` → `setTransform`+`centerOn` → its own `camera_changed` fan-out | ~1.7 ms per follower |
| Each paint | `tiles.paint`; sync render of uncovered tiles; blit; overlays | 1–6 ms warm; 20–650 ms when tiles are rendered |
| Each scene change | `_on_scene_changed`: drop `_content_bounds`, invalidate tiles under the rects on **every** level | decode deliveries and every GIF frame take this path |

## 1.2 Verification of the two previous reviews

There are two earlier documents and they disagree: the root `canvas-navigation-performance.md` and
`docs/perf-navigation-review.md` (later, measured on the real display). The root file is **stale** (it still
says the tile gate is 300). I did not modify it.

| Earlier finding | Status in current code | Evidence now |
|---|---|---|
| Glide timer 8 ms → refresh-paced | **Implemented** (`frame_interval_ms()`, `view.py:40`; 17 ms at 59.95 Hz). `docs/perf-navigation-review.md` showed no change in paints per zoom burst | Zoom frame pacing at the display rate is fine when tiles are cached (warm medium: p50 16.9 ms). Not a bottleneck; no change needed |
| Cache scene centre during a pan | **Not implemented, correctly**: the centre moves during a pan and linked views read it | `center()`+`zoom_level()` are ~0.015 ms of a 1.4 ms `pan_by`; confirmed negligible |
| `camera_changed` fan-out; `update_zoom` / `target_view` cost | **Still present, wrong culprit.** `ZoomBlock.update_zoom` is ~0.1 ms/emit. The expensive listeners are the minimap (`board_rect`, O(views) per call) and the bus itself (O(all listeners) per emit) | 4 linked views: minimap `repaint` is ~54% of 9.3 ms per `pan_by` (profile, 150 pans) |
| Tile gate 300 → lower | **Fixed** (`TILE_MIN_ITEMS = 10`) | Pan of 200–1000 images holds 60 fps |
| `_stop_animation` guard | Not implemented; negligible | ≈0.001 ms |
| (docs) cold-tile stutter is the top target | **Confirmed and quantified**, see below | zoom p95 62–120 ms (1000-image: up to 119 ms warm in one run) |
| (docs) scrollbar `setValue` ×2 is the largest pan cost | **Confirmed**: 1.16 ms of 1.4 ms `pan_by` | Qt-side; only avoidable by moving the viewport once per frame |
| (docs) three `camera_changed` emissions per pan | **Confirmed** | `_camera_moved` ×3 per `pan_by` |

## 1.3 Ranked bottleneck assessment

Confirmed = reproduced by measurement and traced to code. Hypothesis = plausible, not shown.

| # | Priority | Bottleneck | Code | Evidence | Root cause | Proposed improvement | Expected benefit | Complexity | Risk |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **Critical** | Zoom stutters: each glide step makes a new tile level and `paint()` renders uncovered tiles synchronously | `tiles.py: TileCache.paint` (`now` → `_render`), `_current_level` | Cold/settled zoom active-window p95: medium 66–76 ms, large 91–97, hires 85–115, mixed 120–141, stress 459–579. Hundreds–1200 tiles rendered inside paint; worst frames 50–340 ms. Prototype removes it (report §5) | Intermediate zoom levels are rendered, shown for one frame, then abandoned; cost ∝ items under each tile (≈40 µs/item) | Do not render synchronously while the camera is zooming; show a complete coarse fallback (overview level); refine on settle | p95 → ~20 ms on every board incl. stress (measured prototype) | Medium | Medium (blank/blurry regions if the fallback is incomplete) |
| 2 | **Critical** (multi-view) | Linked views collapse | `app.py:725,740`, `minimap/__init__.py:416,174`, `core/events.py:57` | 4 linked views, warm pan: 2.6 fps, p95 892 ms, handler p95 8.9 ms (4 unlinked: 52.7 fps) | Every follower update re-emits `camera.changed`; minimap recomputes `board_rect` over all views per emit (504 `visible_scene_rect` calls per pan); ~1.7 ms per follower `set_camera` | Coalesce `camera.changed` and linked sync to once per loop pass; memoise minimap bounds per frame | 2.6 → 35–40 fps (measured prototype); more with follower batching | Low–Medium | Low |
| 3 | **High** | Animated GIFs defeat the tile cache | `items.py`/`image/items.py: _on_movie_frame → setPixmap → update()` → `content_changed` → `TileCache.invalidate` | gifonly warm: pan 52.5 fps p95 33.7, zoom 27 fps p95 81; mixed zoom warm: paints 163–173/s vs 60 Hz, 4456 invalidations/run, 7176 tiles rendered in paint | 30 GIFs × 10 fps = 300 invalidations/s, each re-rendered synchronously (no fallback for a same-level tile) | Keep animated items out of the baked tiles and paint them live, or throttle invalidation while navigating | Upper bound ≈ the no-GIF board (p95 ~18 ms); only partial measured (`defer_anim` +7–11 fps) | Medium–High | Medium |
| 4 | **High** | 5000-item board: wheel handler 42 ms + tile cost + pan | `view.py: min_zoom`, `_on_scene_changed` | Handler p95 41–44 ms per wheel notch (stress); pan p95 50 → 128 ms as speed rises 1200 → 6000 px/s; `tiles.render_run` mean 18.9 ms (worst 161) vs 2.6 ms on 200 items | `_content_bounds` is dropped by every scene change including pure repaints, then re-measured over 5000 items; one fit-zoom tile covers ~300 items | Keep bounds across repaint-only changes; reduce item count painted per tile at far zoom (Phase 3) | Handler 42 → 0.14 ms (measured); tile cost needs LOD | Low (bounds) / High (LOD) | Low / Medium |
| 5 | **High** (hypothesis for real users) | No input coalescing; per-event cost 2.2–2.9 ms | `NavigationHandler.pointer` → `pan_by` | Synthetic 500 Hz: 17.8 fps; 1000 Hz: 1.2 fps with 2.5 s stalls. Sustainable ceiling ≈ 1 / 2.4 ms ≈ 400 events/s | Every event does full camera work; paint is the only thing Qt coalesces | Apply the accumulated pan delta once per loop pass | 500/1000 Hz → 59.8–60 fps (measured prototype) | Low | Low–Medium (latency, script callers) |
| 6 | Medium | Time to full quality after a jump | `TileCache._idle`, `_settle` | Cold jump: first frame 10–61 ms, full quality median 148–439 ms, worst 1.0–1.6 s | Refinement is serial, 12 ms slices, one tile at a time, after a 140 ms settle | Priority-ordered refinement; start without the settle delay when nothing is moving | −30–50% (estimate, unmeasured) | Medium | Low |
| 7 | Medium | Cold pan right after a jump is blurry for the whole drag | `paint` restarts `_settle` on every deferred frame | cold pan_zoomed: 300/300 frames drawn from fallbacks magnified 1.5–27× (median 5×; stress 27×); refined 0.3–0.4 s after release | Refinement is gated on `_settle` expiring, which never happens during motion | Allow refinement of visible tiles during slow motion (priority 2) | Visible sharpness during drag | Medium | Medium |
| 8 | Medium | Decode deliveries invalidate coarse tiles | `ImageHandle.deliver → item.update()` → `_on_scene_changed` | Hypothesis, supported: overview prototype showed 14–25% blank tiles while zoom-settled but 0% warm (no deliveries) | A finer level arriving does not change what a coarser level should show, but invalidation is by rectangle on all levels | Update only items whose wanted level was just delivered | Fewer re-renders, fewer blanks | Low | Low |
| 9 | Medium | GL viewport blits cost more than raster | `gpu.py: CanvasGLWidget` | Warm pan, medium: paint p95 GL 5.8–6.0 ms vs software 2.1 ms vs offscreen 1.1 ms | **Hypothesis**: GL flush/composition inside `paintEvent`; not isolated | Investigate with a GPU/ETW capture before changing | 3–4 ms/frame if removable | ? | ? |
| 10 | Low | Idle slices run during motion | `tiles.py: _idle`, `SLICE_MS = 12` | Settled pan p95 28 ms vs warm 17.5; 35–64 slices ≤13.8 ms during input. `SLICE_MS` 4/2: p95 28.3/23.7 (single runs: inconclusive) | A 12 ms slice plus one tile can exceed a frame | Pause prefetch or shrink slices while input is live | small, unproven | Low | Low |
| 11 | Low | `EventStream.emit` is O(all listeners) | `core/events.py:57` | 0.1 ms per `camera.changed` emit (profile shows ~65–83 closure calls per emit) | typed `on()` is a filter closure, not an index | Index typed listeners | ~0.05–0.08 ms per emit (estimate) | Low–Medium | Low (ordering) |
| 12 | Low | Python item paint cost per tile | `image/items.py: _draw_handle`, `_draw_scaled` | stress tile ≈ 12.5 ms for ~300 items. Caching `boundingRect` gave **no** gain (11.7–12.1 vs 11.6–12.7 ms/tile) | Qt paint plumbing + Python per item | Fewer items per tile (LOD/cull), not micro-tuning | — | — | — |

Findings not reproduced as problems: pan on boards of 20–1000 images (warm or settled) runs at 59.8 fps,
p95 17.4–18.6 ms; effects (`fxonly`) and annotations (`notesonly`) add nothing measurable; software vs GL give
the same frame cost for tile blits; 4 *unlinked* views pan normally.

## 1.4 Multiple views, effects, animation

* **Views**: each `CanvasView` has its own `TileCache` (budget 256 MB each) and its own tiles. Unlinked views
  share only the scene and image store; panning one leaves the others idle (52.7 fps with 4 views). Linked views
  multiply every camera update by the view count (finding 2). Tile memory scales per view.
* **Effects**: processed on a `QThreadPool` and cached (`effects/engine.py`); the item shows the previous result
  meanwhile. Not a navigation cost in these tests. Heavy effects delivering a result invalidate tiles like any
  decode.
* **Animation**: see finding 3. The `QMovie` frame signal runs on the GUI thread.
