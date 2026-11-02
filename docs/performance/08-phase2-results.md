# 8. Phase 2 results: progressive rendering (roadmap steps 2.1 and 2.2)

Implemented behind `PIXELREF_TILE_PROGRESSIVE=1` (default **off**, `PROGRESSIVE_DEFAULT` in `pixelref/ui/canvas/tiles.py`).
Same machine and harness as the baseline ([03](03-benchmark-report.md)): GL viewport, 59.95 Hz, i7-7700 / RTX 4060 Ti, Qt 6.12.
Baselines below are the **current code with the flag off** (Phase 1 included), measured in the same matrix, not the numbers of
the original report: Phase 1 alone already took stress settled-zoom p95 from about 459 ms to about 105–111 ms.

## What it does

* While the zoom is changing (`CanvasView.zooming()`: a glide or camera animation runs, or the zoom changed in the last 80 ms),
  `TileCache.paint` does not render tiles synchronously. A coarse **overview** of the whole board (longest side about 3072 device px),
  built in 6 ms idle slices, is the fallback; the exact level fills in after the zoom settles.
* The overview is held in `TileCache.overview`, outside `levels`, so level eviction never takes it. Invalidation marks its tiles
  **stale** instead of dropping them (a slightly old coarse picture is better than a hole); stale tiles are redone when the scene has been still.
* A tile that had already been rendered and was dropped by a change (an edit or an animation frame) is **re-rendered at once** when not zooming
  (`Level.dirty`), as before. Without this, animated images flicker between sharp and blurry.
* The view's own idle refinement waits while the zoom changes; the settle timer is 40 ms (was 140 ms) and is not restarted by every paint.
* The overview is built only while the camera is still and the view's own tiles are done. A board whose own tiles are never done (animated
  images) gives up waiting after 2.5 s and builds the missing overview anyway.

Deviations from the roadmap: gating is on **zoom only**, not on panning (the prototype that was measured gated on zoom); the overview is
**per view**, not shared (view filters change what tiles show, so sharing needs design); a tile that touches the board but is not covered by the
overview is still rendered at once; `_covered()` tests the overview only over the part of a tile that lies on the board.

## Results (final matrix, 60 runs, `data/phase2_progressive.jsonl`, configurations in `data/matrix_g.json`)

Settled zoom, active-window p95 frame interval (ms), range over 3 runs, baseline → progressive:

| Board | p95 | synchronous tiles per run | worst `paint` frame (ms, max of runs) | refine, visible tiles (s, 3 runs) |
|---|---|---|---|---|
| medium | 62–65 → **18** | 978 → 33 | 77 → 38 | 0.32 / 0.32 / 0.37 → 0.05 / 0.06 / 0.01 |
| large | 77–100 → **18–19** | 603 → 16 | 284 → 40 | 0.34 / 0.47 / 0.20 → 0.05 / 0.11 / 0.07 |
| hires | 63–86 → **18–19** | 998 → 35 | 83 → 35 | 0.37 / 0.28 / 0.37 → 0.01 / 0.06 / 0.01 |
| stress (5000) | 105–111 → **18–20** | 407 → 28 | 483 → 54 | 3.45 / 0.51 / 0.03 → 0.26 / 0.20 / 0.05 |
| mixed (GIFs) | 105–155 → **21–23** | 7208 → 64 | 442 → 68 | never → 0.12 / 0.24 / 0.13 |
| gifonly | 81–86 → **22–23** | 1379 → 82 | 80 → 44 | 0.28 / 0.23 / 0.23 → 0.11 / 0.10 / 0.20 |

Trackpad-style zoom (fps / p95 ms, one run each): medium 35.5 / 81.2 → 55.7 / 18.1; stress 29.8 / 91.3 → 51.9 / 19.1; mixed 16.2 / 133.4 → 54.9 / 23.0.

Warm pan, regression guard (fps / p95 ms, one run each): medium 59.6 / 17.1 → 59.8 / 17.1; large 59.8 / 17.3 → 59.6 / 17.2; hires 59.8 / 17.0 → 59.8 / 17.1;
stress 58.4 / 19.2 → 58.8 / 18.3; mixed 48.9 / 35.0 → 51.5 / 33.7; gifonly 56.8 / 24.8 → 89.5 / 28.0 (see open issues).

Cold jumps (8 jumps per run, time to full quality, ms): medium p50 216 → 142, p95 990 → 225; large p50 349 → 318, p95 503 → 448; stress p50 455 → 372, p95 505 → 512.

Peak RSS during settled zoom (mean of 3): medium 392 → 412 MB, large 404 → 423, stress 428 → 474, mixed 622 → 632.

Image decoding during settled zoom (3 runs per board): decode deliveries fall from 138–1964 per run to 1–9 (medium 138/198/171 → 3/3/3; stress 1964/1288/562 → 9/9/9),
because images are no longer decoded at every intermediate zoom level. Time until the visible tiles exist **and** the decode queue is idle: medium 0.32–0.37 → 0.05–0.06 s,
large 0.21–0.47 → 0.06–0.11 s, hires 0.28–0.42 → 0.04–0.09 s, stress 0.03–3.45 → 0.07–0.26 s, mixed never → 0.12–0.24 s. The harness' "image bake exact share" is lower in
progressive mode on some boards (medium 0.78–0.81 → 0.31–0.34, hires 0.68–0.76 → 0.18–0.42): the first tiles baked after the zoom settles use the level already decoded and the
sharp level arrives moments later and triggers the immediate re-render. The final pixels were compared (see Validation) and match.

### Gates ([05](05-implementation-roadmap.md))

| Gate | Result |
|---|---|
| Settled zoom p95 ≤ 25 ms on medium / large / hires / stress | **Met**, 18–20 ms in 3 of 3 runs on each |
| Worst `paint` frame ≤ 70 ms | **Met** on every board (35–68 ms; mixed is closest) |
| Blank share ≤ 5% | **Met**: uncovered tiles 0.0% on non-GIF boards, 3.3% mixed, 4.0% gifonly. The metric also counts tiles beyond the board that are skipped on purpose while zooming, so it overstates real blanks |
| Refine ≤ 0.3 s (≤ 1 s stress) | **Met**, except one baseline stress run at 3.45 s that the new code does not show |
| Warm pan medium / large not worse than +1 ms p95 | **Met** (17.1 vs 17.1, 17.2 vs 17.3) |
| Tile memory per view ≤ 80 MB | **Not measured directly** (the harness' tile-MB column does not include the overview). Peak RSS rose 10–46 MB |

## How the first pass differed from the final code (kept in `data/phase2_first_pass.jsonl`)

The first full matrix (44 runs finished) already met the zoom gates, but the data showed three defects that single runs had hidden:

1. **Idle refinement ran during the zoom** (52 of 87 slices were inside input windows) and rendered tiles of levels the next step abandons.
   `large` p95 50 → 19 ms once `_idle` waited for the zoom.
2. **Animated boards never reached full quality and would have flickered.** Every GIF frame invalidates tiles; they were then drawn from the blurry overview
   until the idle renderer caught up, and the settle timer was restarted by every paint so refinement never began. Fixed by the `dirty` re-render and the non-restarting settle timer.
3. **Cold jumps reached full quality later and with a long tail** (stress p95 up to 1.2–2.3 s, in about 1 jump in 8). The same invalidation chains, deferred through the idle path.
   Fixed by the `dirty` re-render; stress jumps now finish in 255–512 ms.

## Validation

* Tests: 5 new tests in `tests/test_tiles.py` (overview built when idle and held outside the levels; empty board has none; a zoom shows the overview and renders nothing meanwhile;
  an edit during a zoom is in the overview and exact afterwards; a changed tile is redone at once when not zooming). The tile, view, canvas and performance suites pass with the flag off and on
  (70 of 70 each); full-suite results are in the commit message.
* Pixels: `tools/validate_progressive.py` zooms a real window and saves `view.render_frame()` images. Settled frames of baseline and progressive differ by at most 4 levels in any channel
  and 0% of pixels beyond 8 levels, the same as baseline against baseline. Mid-glide frames (zoom in, zoom out, edit during a glide) were looked at: no holes.
  Real-screen captures were too noisy to compare (another window overlapped them) and are not used.
* Visible side effect: while zooming out the background grid appears only in a box around the board; tiles beyond the board wait for the settle.

## Open issues and limits

* **First frame after a jump is later**: p50 13 → 33 ms (medium), 11 → 37 ms (stress), 18 → 19 ms (large). Cause not diagnosed (drawing scaled overview tiles?).
* **gifonly warm pan p95 24.8 → 28.0 ms** and paint rate 56.8 → 89.5 per second, one run each; may be noise but not checked with repeats. The pan on 5000 items at speed was not addressed (Phase 3).
* **Memory** of the overview was not isolated; one overview per view, so split layouts multiply it.
* **Content outside `scene_bounds(visible_content_items())`** would be missing while zooming until it settles; not looked for.
* **Stale overview tiles** can show a moved or deleted item during a zoom, for up to the time the overview needs to catch up. Checked only in the test and one captured frame.
* Roadmap 2.3 (scope decode-delivery invalidation), 2.4 (animated items outside the tiles) and 2.5 (refinement policy) were **not done**. The `dirty` re-render and the stale overview
  removed the symptoms 2.3 targeted for this benchmark; 2.4 would still cut the repaint cost of animated boards (mixed warm pan is still 33.7 ms p95).
* One machine, one display refresh rate, synthetic boards.

## Reproduce

```
.venv\Scripts\python.exe tools\bench_canvas_nav.py matrix --configs docs\performance\data\matrix_g.json --out out.jsonl
.venv\Scripts\python.exe tools\summarize_nav_results.py out.jsonl --cols active
.venv\Scripts\python.exe -I tools\validate_progressive.py run --out D:\prog-off      & compare to a run with PIXELREF_TILE_PROGRESSIVE=1
```
