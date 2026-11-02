# 2. Progressive Rendering Feasibility Study

**Verdict.** The central hypothesis is **mostly confirmed, with one important qualification.** Navigation can
stay responsive while full-quality rendering is expensive, and it can be done by *extending the existing
`TileCache`*, not by adding a second renderer. But the gain comes from two changes together: (a) never render
tiles synchronously while the camera is moving, and (b) always having something complete to show instead. Doing
(a) alone trades stutter for blank regions, which is a visual defect (measured: 24–83% of tiles blank).

Evidence: prototypes in `tools/bench_variants.py` (monkeypatched at runtime, production untouched), measured
with `tools/bench_canvas_nav.py`. Numbers come from [03-benchmark-report.md](03-benchmark-report.md).
"Settled" = the start view is fully rendered, nothing else is cached.

## 2.1 What already exists in the tile cache

| Capability asked for | Status | Where | Weakness found by measurement |
|---|---|---|---|
| Cached tile reuse | Implemented | `Level.tiles`, tiles anchored to the scene so a pan keeps them | Reuse works: warm pan has 100% hits, 0 sync renders |
| Fallback tiles from other zoom levels | Implemented, **partial** | `paint` → `_paint_fallbacks`; `Level.covers` | Used only if one older level covers the *whole* tile. Anything else is rendered synchronously (`now`) |
| Progressive regeneration | Implemented | `_idle`: centre-first, 12 ms slices, one tile per `_render` | Serial; runs during input too |
| Idle prefetch | Implemented | ring of ±1 tile (`PREFETCH`) | Keeps up with pans up to 6000 px/s on 200 items; not on 5000 (624 sync tiles) |
| Incremental rendering | Implemented | `MAX_RUN = 8` tile runs share one `scene.render` | — |
| Delayed full-quality update | Implemented | `SETTLE_MS = 140` | Restarted by every deferred frame, so refinement never begins during motion |
| Image-level progression (not tiles) | Implemented | `ImageHandle` levels decoded on workers; nearest decoded level shown; mip chains | Works; but each delivery invalidates tiles on every level |
| Cancel / deprioritise obsolete work | **Missing** | — | Glide steps render levels nobody sees; stale decode requests are cheap and not the problem |
| A complete low-resolution fallback | **Missing** | — | The only fallbacks are levels the user happened to visit |

So the tile cache is already progressive for *zoom-in over cached content*. It is not progressive where it
matters: new areas at new zoom levels, and big boards.

## 2.2 What goes wrong today (measured)

Settled zoom from "fit all" to ~1:1 and back, wheel at 25 notches/s (active-window p95 of frame interval;
60 Hz budget = 16.7 ms):

| Board | baseline p95 | paint p95 | tiles rendered inside paint |
|---|---:|---:|---:|
| medium (200) | 76 ms | 40 ms | 812 |
| large (1000) | 97 ms | 74 ms | 567 |
| hires (150 × 12 MP) | 115 ms | 72 ms | 858 |
| mixed (530, GIFs) | 141 ms | 95 ms | 1148 |
| stress (5000) | 459 ms | 246 ms | 184 (but ≈19 ms each; worst paint 343 ms) |

Every glide step lands on a new `(zoom, dpr)` key, so it is a new `Level`. The tiles it needs that no older
level fully covers are rendered inside `paint` and then discarded one step later. Pan is fine: the level does
not change, the ring is already rendered, and fallbacks are not needed.

## 2.3 Strategies tried

All numbers are active-window p95 of presentation intervals unless noted; baseline in the first column.

| Strategy | medium | large | stress | Blank tiles | Verdict |
|---|---:|---:|---:|---:|---|
| baseline (cold/settled) | 66–76 | 91–97 | 459–579 | 0% | — |
| `coarse_zoom` (render a ¼-resolution level synchronously) | 35 | 85 | 832 | 0% | **Rejected.** Cost is bound by items painted, not pixels: a coarse tile covers 16× the items |
| `nosync_zoom` (skip all sync renders while zooming) | 23 | 20 | 133 | 24–28% / 78–83% (stress) | Fast but blank; and the deferred cost lands in one frame at glide end (stress paint max 245–274 ms) |
| `budgetN` (≤4–8 ms of sync render per frame, centre-first) | 24–30 | 49–73 | 107–135 | 65–91% | **Rejected as primary.** One stress tile costs 12–19 ms, so the budget is always exceeded; blank for seconds |
| **`overview` + no sync render while zooming** | **21** | **20** | **22** | 14–25% (see below) | **Recommended.** Complete coarse picture always available |
| overview + `budget6` | 25 | 59 | 98 | 10–54% | Worse than no-sync on big boards |

The overview prototype (`overview` in `bench_variants.py`): one extra `Level` per view, zoom chosen so the
whole content fits ≈3072 device pixels, kept as the oldest fallback, built in 6 ms idle slices only when the
cache is otherwise idle and the user is not navigating, invalidated by scene changes like any level. Cost:
≈35 MB → 72–82 MB total tile memory in the zoom runs (+≈40 MB per view; mixed: 39 MB), 0.4 s (200 items) – 2.7 s (5000 items) of GUI time spread
over idle slices after load.

Settled zoom with overview, vs baseline (`tools/summarize_nav_results.py`, matrices E/F):

| Board | p95 baseline → overview | paint max | worst case notes |
|---|---|---:|---|
| medium | 76 → 21 ms | 36 ms (was 51) | 25% blank |
| large | 97 → 20 ms | 38 ms (was 324) | 21% blank |
| hires | 115 → 18 ms | 24 ms (was 116) | 22% blank |
| stress | 459 → 22 ms (6 → 47 fps) | 53 ms (was 343) | 15% blank |
| mixed | 141 → 24 ms | 74 ms (was 127) | **74% blank**: GIF invalidations keep dropping overview tiles |
| medium trackpad zoom | 85 → 46 ms | 32 ms | 15% blank |
| stress trackpad zoom | 963 → 87–133 ms (3 → 35 fps) | 112 ms | 12% blank |

### The blank tiles are a defect to fix, not a cost of the idea

With the overview complete, blank tiles should be zero. They are not, and the data points at the cause:
warm runs (no new image decodes) show **0% blank**; settled runs (decodes arrive during the zoom) show 14–25%.
`ImageHandle.deliver` calls `item.update()` for every waiting item, which invalidates the tiles under that item
on *every* level, including the overview. A finer level arriving cannot change what a much coarser level
should show. This is a hypothesis supported by the warm/settled contrast; it has not been isolated by an
experiment. Acceptance criterion in the roadmap: blank-tile share ≤ 5% on the no-GIF boards.

### What did not improve

* **Pan on 5000 items** (3000/6000 px/s): p95 66/107 ms baseline vs 60/109 ms with the bundle. The remaining
  cost is item-count-bound tile rendering for tiles the ring did not reach. Overview does not help a pan at a
  zoom where the overview is far too coarse (fallback magnification is large). It needs LOD (Phase 3).
* **Non-zoom jumps** (`set_camera` to an unseen area at the same zoom): no change from `nosync_zoom` (it only
  applies while zooming). `budget6 + overview` halved first-frame time (21 → 9 ms median, stress p95 672 → 183 ms)
  at 22–34% blank.
* **GIF boards**: `defer_anim` (hold invalidations for 250 ms while navigating) gave +7–11 fps and fewer
  synchronous renders (416 → 165 tiles) but p95 only 33.1 → 32.5 ms on pan. Needs a structural fix (§2.6).

## 2.4 Recommended architecture (extends `TileCache`; no second renderer)

1. **Navigating state.** `TileCache` (or `CanvasView`) exposes `navigating`: true while a glide or camera
   animation runs, while a drag is active, and for ~150 ms after the last input. Set by `glide_zoom`/`_glide_step`,
   `_animate_camera`, and the navigation handler.
2. **No synchronous tile render while `navigating`**, except as a last resort when no fallback exists at all
   (and then at most one tile). `paint` draws: overview → older levels → current-level tiles, as it already does for
   fallbacks. `_settle` still schedules refinement.
3. **Overview level** (`Level` with a flag): fixed zoom for the current content bounds, built by the same
   `_idle` machinery at lowest priority, pauses while `navigating`, rebuilt when content bounds change
   materially, shared by all views of the scene for a given dpr (the prototype is per view and would multiply
   memory by the number of views), capped in memory by the existing `BUDGET_BYTES`.
4. **Priority order for background work** (the "critical / high / medium / low" ladder): camera and paint
   (always first) → visible tiles missing at the current level, centre-first → visible tiles currently drawn from
   a fallback (quality refinement) → ring prefetch → overview maintenance. All of it runs on the GUI thread.
5. **Invalidation scoping**: image-level deliveries invalidate only when the delivered level is the one the item
   currently wants (`_draw_handle` already computes it; store it on the item). Edits still invalidate all levels.
6. **Refinement during slow motion** (finding 7): let `_settle` expire for visible tiles when the pointer
   velocity is low, so a slow drag after a jump sharpens while moving.

### Qt threading constraint

`QGraphicsScene.render` and item `paint` must run on the GUI thread (items are Python `QGraphicsItem`
subclasses). Tile rendering therefore cannot move to a worker; progressive behaviour has to come from slicing
and from a better fallback, as above. What can and already does run on workers: image decoding, mip chains,
effect processing. A worker-rendered overview would need a separate non-item renderer (a second rendering
system) and is not recommended.

## 2.5 Visual quality and correctness

| Concern | Assessment |
|---|---|
| Blank viewport regions | Real risk; measured above. Gate on blank share and on "no frame with >N blank tiles" |
| Position jumps | None expected: fallbacks are drawn through the view transform and tiles are scene-anchored. `Level.covers`/`_paint_fallbacks` already do this for zoom-in |
| Persistent blur | With overview + no-sync, visible tiles were fully refined 0.05–0.23 s after the glide ended (matrix F, all boards; baseline 0.09–1.56 s). The budget variants took 1–5 s on large/stress boards (another reason to reject them) |
| Compositing / overlap | Unchanged: tiles are rendered by the same `scene.render` with the same hints |
| Stale content after edits | Edits still go through `content_changed` → `invalidate` on all levels. The only relaxation proposed is for *decode deliveries*. Needs a test: edit during/after a glide shows the edit |
| Selection / hit testing | Unaffected: hit testing uses the scene, not tiles |
| Multiple views | Overview must be shared or budgeted; each view still owns its tiles |
| Intentional vs defect | Blur from a scaled fallback during motion is intended; blank tiles, or blur that outlasts refinement, are defects |

## 2.6 Animated content

GIFs are the one case progressive rendering cannot fix alone: each frame change invalidates real tiles at the
*current* level, which have no fallback, so they re-render synchronously. Structural fix (not prototyped, so
benefit is unmeasured): while baking tiles, skip animated items; paint them live over the tiles each frame
(small clip, few items). The upper bound is the no-GIF board: pan p95 ~18 ms (medium) vs 33 ms (gifonly).

## 2.7 Risks

* Prototype fidelity: `bench_variants.py` replaces `TileCache.paint` wholesale; a production change must keep
  the existing selection of `visible`, fallbacks and ring prefetch.
* Overview build cost (0.4–3.6 s GUI time) must not block interaction; the prototype yields to input and to
  visible refinement. It was validated on synthetic boards only.
* Single machine, single display (59.95 Hz). Nothing was run on macOS/Linux or at 120 Hz.

## 2.8 Recommendation

Implement §2.4 steps 1–3 and 5 behind a flag (`PIXELREF_TILE_PROGRESSIVE=1`), with the acceptance gates in
[05-implementation-roadmap.md](05-implementation-roadmap.md). Do not implement `coarse_zoom` or budgeted
synchronous rendering. Treat animated content and 5000-item pans as separate work items.
