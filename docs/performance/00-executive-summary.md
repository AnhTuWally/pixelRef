# 6. Executive Summary (read this first)

pixelRef's canvas already pans smoothly on boards of up to ~1000 images; it does **not** zoom smoothly, it falls
over with linked views, and it degrades with animated GIFs and on 5000-item boards. Most of the cost is
synchronous tile rendering on the GUI thread plus camera-event overhead, not image decoding (which is already
progressive and off-thread). No production code was changed; every claim below is backed by a benchmark in
[03-benchmark-report.md](03-benchmark-report.md) or labelled as an estimate.

Measured on one Windows 10 machine (i7-7700, RTX 4060 Ti, 59.95 Hz display, Qt 6.12). Not tested: 120 Hz, macOS,
Linux, high-DPI, real-world boards.

## The four questions

1. **What prevents navigation feeling consistently smooth?** Zoom: each wheel-glide step creates a new tile level
   and `TileCache.paint` renders the uncovered tiles synchronously, then throws them away. Active-window p95 frame
   interval: 66–76 ms (200 images), 97 (1000), 115 (150 hi-res), 141 (GIF/mixed), 459–579 (5000) against a 16.7 ms
   budget. Pan is healthy (59.8 fps, p95 17.4–18.6 ms on 20–1000 images).
2. **Can progressive rendering keep navigation responsive when full quality is expensive?** Yes, with a
   qualification. Prototype: stop rendering tiles synchronously while zooming **and** keep a complete low-resolution
   "overview" level as the always-available fallback. Settled zoom p95 fell to 18–24 ms on every board, including
   5000 items (459 → 22 ms, 6 → 47 fps). Doing the first half without the second produces blank regions
   (24–83% of tiles). It extends the existing `TileCache`; no second renderer is needed.
3. **Which changes give the most for the least complexity?** See below: camera/pan coalescing and the `min_zoom`
   fix are small and measured; the overview level is medium and has the largest visible effect.
4. **How to implement and verify safely?** [05-implementation-roadmap.md](05-implementation-roadmap.md): three
   phases, each step behind a switch, numeric gates measured by the same harness (`tools/bench_canvas_nav.py`).

## Top five bottlenecks (evidence-ranked)

| # | Bottleneck | Evidence |
|---|---|---|
| 1 | Synchronous tile rendering at throwaway zoom levels | Zoom p95 66–141 ms (459 on 5000 items); 570–1150 tiles rendered inside paint per run; worst frames 50–340 ms |
| 2 | Linked views multiply every camera update | 4 linked views pan at **2.6 fps** (4 unlinked: 52.7). 9.3 ms per pan event; the minimap listener alone is ~54% |
| 3 | Animated GIFs invalidate and re-render tiles every frame | gifonly zoom p95 85 ms, 27 fps; mixed board paints 163–173×/s on a 60 Hz display, 4456 invalidations per run |
| 4 | 5000-item boards: 42 ms wheel handler, 12.5 ms per tile | `min_zoom()` re-measures every item after any scene change; pan p95 50 → 128 ms as drag speed rises |
| 5 | No input coalescing: 2.2–2.9 ms per mouse event | Ceiling ≈400 events/s. Synthetic 500 Hz → 17.8 fps; 1000 Hz → 1.2 fps (stress model; Windows coalesces real mice, so this is a headroom problem, not an observed one) |

Also: cold jumps take a median 148–439 ms (worst 1.6 s) to reach full quality; right after a jump a drag is drawn
from fallbacks magnified up to 27× for its whole length.

## Three highest-value improvements

| Improvement | Measured effect (prototype) | Cost | Main risk |
|---|---|---|---|
| **A. Overview level + no synchronous render while zooming** | Settled zoom p95 76→21 / 97→20 / 115→18 / 459→22 ms (200 / 1000 / 150 hi-res / 5000 items); worst paint frame 343→53 ms; stress trackpad zoom 3→35 fps | Medium; ≈+40 MB per view (share it) | Blank tiles (14–25% measured; probable cause identified, fix is Phase 2.3) |
| **B. Coalesce camera events and pan input; fix minimap bounds** | 4 linked views 2.6→35–40 fps; 500/1000 Hz input 17.8/1.2→60 fps; 125 Hz unchanged | Low–Medium | Listeners expecting per-step events; ≤1 frame latency under overload |
| **C. `min_zoom` bounds survive repaint-only changes** | Wheel handler on 5000 items 42→0.14 ms | Low | Stale bounds if mis-scoped |

Honourable mention (not measured end to end): moving animated items out of the baked tiles. `defer_anim`
(throttled invalidation) recovered only +7–11 fps.

## What did not work (so nobody repeats it)

Coarse-resolution synchronous tiles (cost tracks item count, not pixels: stress 832 ms p95), per-frame
synchronous render budgets (63–91% blank, one stress tile exceeds any budget), skipping sync render without a
fallback (the deferred cost reappears as a 245–274 ms frame), caching `boundingRect` (no change), caching the pan
centre (incorrect), re-tuning the glide timer (already paced). Details in
[04-optimisation-strategy.md](04-optimisation-strategy.md).

## Honest gaps

* Pan at ≥3000 px/s on 5000 items is **not** fixed by anything tested (p95 60–109 ms); that needs fewer items per
  tile at far zoom (Phase 3, unprototyped).
* GL paint costs ~6 ms vs ~2 ms for the same blits; cause unknown.
* Per-event input pipeline overhead (≈1 ms beyond `pan_by`) was not profiled.
* The earlier root-level `canvas-navigation-performance.md` is stale (tile gate "300", code says 10) and several of
  its claims were already disproved in `docs/perf-navigation-review.md`; I left both files untouched.

## What was added

`tools/bench_canvas_nav.py` (real-time harness), `tools/bench_variants.py` (runtime prototypes),
`tools/summarize_nav_results.py`, and this folder including `data/` (raw JSONL, exact run configurations, tables).
