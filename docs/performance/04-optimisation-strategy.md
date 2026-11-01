# 4. Optimisation Strategy

Ranked by evidence, not by the initial hypotheses. "Measured" means a prototype or microbenchmark in
[03-benchmark-report.md](03-benchmark-report.md); "estimate" means reasoning only. Complexity and risk are for
a production implementation, not the prototype.

## 4.1 Ranked candidates

| Rank | Candidate | Fixes | Measured benefit | Complexity | Memory | Portability | Correctness risk | Maintainability |
|---:|---|---|---|---|---|---|---|---|
| 1 | **Overview level + no synchronous tile render while zooming** (`TileCache`) | Zoom stutter on every board size | Settled zoom p95: medium 76→21 ms, large 97→20, hires 115→18, stress 459→22, mixed 141→24; stress trackpad zoom 3→35 fps; paint max 343→53 ms | Medium | +≈40 MB per view (share across views to avoid ×N) | Pure Qt/Python | Blank tiles (measured 14–25%, hypothesised cause identified); stale content on edit | Good: reuses `Level`, `_idle`, `_paint_fallbacks` |
| 2 | **Coalesce `camera.changed` + linked-view sync to once per loop pass; memoise minimap bounds** | Linked views, camera event cost | 4 linked views 2.6 → 35 fps (camera) → 40 fps (+pan) | Low–Medium | none | Pure Python | Listeners that expect one event per scrollbar change; ordering vs linked sync | Good |
| 3 | **Coalesce pan deltas to one `pan_by` per loop pass** | Per-event cost ceiling | 500 Hz: 17.8 → 60 fps; 1000 Hz: 1.2 → 59.8 fps; handler p95 2.4 → 0.1–0.2 ms (work moves to the flush) | Low | none | Pure Python | +≤1 frame of latency under load; scripts calling `pan_by` and reading the camera immediately | Good |
| 4 | **`min_zoom` bounds survive repaint-only scene changes** | 42 ms wheel stall on 5000 items | Handler p95 42 → 0.14 ms (measured); frames unchanged alone | Low | none | Pure Python | Stale bounds if a geometry change is mistaken for a repaint | Good |
| 5 | **Scope tile invalidation of decode deliveries** (only when the delivered level is the wanted one) | Blank/refine churn, GIF/decode re-renders | Not measured alone; explains the warm 0% vs settled 14–25% blank | Low | none | Pure Python | An item stuck on a coarse level if the wanted level is mis-tracked | OK |
| 6 | **Animated items outside the baked tiles** (paint live) | GIF boards | Upper bound only (no-GIF p95 ≈ 18 vs 33 ms on pan; zoom 85 → ~19). `defer_anim` measured: +7–11 fps, p95 33.1→32.5 | Medium–High | none | Pure Qt | Draw order (live items over/under annotations), effects on GIFs, selection overlays | Needs care |
| 7 | **Refinement priority + allow refinement during slow motion** (`_settle`, `_idle`) | Persistent blur after a jump; time to full quality | Estimate only. Cold jump full quality median 148–439 ms today | Medium | none | Pure Python | Competes with panning for frame time | OK |
| 8 | **`EventStream` indexed by event type** | Bus cost per emit | Estimate: ~0.05–0.08 ms of ~0.1 ms per `camera.changed` emit | Low–Medium | none | Pure Python | Listener ordering between typed and untyped subscriptions | Good |
| 9 | **Fewer items painted per tile at far zoom (LOD / cull / proxy)** | 5000-item tile cost (12.5 ms/tile), stress pan at speed | Not prototyped. Per-item cost ≈40 µs; caching `boundingRect` gave nothing | High | small | Pure Python/Qt | Visible change at LOD switch; selection must not use proxies | Adds a path |
| 10 | **Investigate GL `paintEvent` overhead** (6 ms vs 2 ms for the same blits) | Per-frame cost | Unknown cause | ? | ? | GPU-driver specific | — | — |
| 11 | **Shorter idle slices / pause prefetch during input** | Settled pan p95 28 vs 17 ms | `SLICE_MS` 4/2: p95 28.3/23.7 (single runs, inconclusive) | Low | none | — | Pan prefetch may fall behind | — |

## 4.2 By category (the requested checklist)

**Immediate navigation responsiveness.** Items 2, 3, 4, 8. All low-risk; together they remove the input-rate
ceiling, the linked-view collapse and the 42 ms wheel stall. They do not change frame times on a healthy
200-item board (already vsync-locked), so they should not be sold as an fps gain there.

**Progressive rendering.** Items 1, 5, 7. This is the largest visible gain, and the only one that changes how
zooming *looks* (blurry-then-sharp instead of stuttering).

**Rendering architecture.** Viewport culling and incremental rendering already exist (tiles, `MAX_RUN`).
Selective invalidation exists but is over-broad for decode deliveries (item 5). Efficient scene traversal is a
per-item cost problem (item 9), not a traversal one: caching `boundingRect` did not help.

**Memory and caching.** Tile memory is bounded (3 levels, 256 MB per view) but scales with view count and
viewport pixels, and a tiny board still holds ≈50 MB. Overview adds ≈40 MB per view unless shared. There is no
shared tile store between views of the same scene (hypothesis: a shared pool would save ~50% memory for
split layouts; not measured). Image decode memory is already budgeted and shared (`PictureStore`, 1 GB).

**Asynchronous processing.** Decode, mip chains and effects are already off-thread, and the tests show they
do not stall navigation. Tile rendering cannot be moved off the GUI thread (items are Python
`QGraphicsItem`s). More threads would not help any measured bottleneck, and the brief's warning stands: do not
assume it would.

**GPU acceleration.** The OpenGL viewport and the software viewport have the same CPU-side stalls; GL is *more*
expensive for the same tile blits (6 vs 2 ms/paint) and gives vsync-locked pacing. Deeper GPU use (textured
tiles, GPU compositing of the overview) would not touch the dominant cost, which is `scene.render` of items
into tiles. Not recommended without a capture showing a GPU-side bottleneck.

## 4.3 Considered and rejected

| Idea | Why not |
|---|---|
| Coarse (¼-resolution) tiles rendered synchronously | Measured: medium 35 ms, large 85, stress 832 vs baseline 75/91/463. Item count, not pixels, sets the cost |
| Per-frame synchronous render budget (4–8 ms) | Measured: 63–91% blank tiles; one stress tile (12–19 ms) exceeds the budget; refinement took 1–5 s |
| Skip all sync rendering with no fallback (`nosync_zoom` alone) | Fast but 24–83% blank, and the whole deferred cost lands in a single 245–274 ms frame at glide end on stress |
| Cache the scene centre during a pan | Incorrect (centre moves; linked views read it); ~0.015 ms anyway |
| Move tile rendering to a worker thread | Qt item painting must stay on the GUI thread; a separate renderer would be a second rendering system |
| Cache `PixmapItem.boundingRect` | Measured no change (11.7–12.1 vs 11.6–12.7 ms/tile) |
| Re-tune the glide timer | Already refresh-paced; pacing is fine whenever tiles exist |
| Replace the OpenGL viewport | No evidence that GL is the cause of any stall |

## 4.4 Trade-offs to decide explicitly

1. **Blur vs. blank vs. stutter during zoom.** Today: stutter + blur. Proposed: blur only. The study shows blank is
   avoidable only with a complete overview; until the invalidation fix lands, 14–25% of tiles can be blank during a
   zoom, so ship behind a flag.
2. **Memory vs. smoothness.** The overview costs ≈40 MB per view (build it once and share it).
3. **Latency vs. cost under load** (pan coalescing): up to one extra frame of input-to-display delay *only when
   events arrive faster than frames*; at 125 Hz there is no change (frame rate and intervals identical to baseline).
4. **Animated GIFs in tiles vs. live.** Live painting keeps navigation smooth but changes draw order guarantees;
   decide whether GIFs must stay inside the group/annotation z-order.
