# 6. Phase 1 results

Implements roadmap steps 1.1 to 1.3 (branch `perf-phase1`). Same machine and harness as the baseline
([03](03-benchmark-report.md)): GL viewport, 59.95 Hz, budget 16.68 ms. Every number below was measured
after the change with `tools/bench_canvas_nav.py`; 3 runs where a gate depends on it. Baselines are from
`data/*.jsonl`.

| Step | Commit | Gate | Baseline | After | Verdict |
|---|---|---|---|---|---|
| 1.1 `min_zoom` bounds kept across repaints | `3258aa4` | stress wheel-handler p95 ≤ 1 ms | 42 ms | 0.14 / 0.19 / 0.32 ms (3 runs) | met |
| 1.2 one camera report per change; minimaps once per pass | `a1b211a`, `d8bdc46` | 4 linked views, warm pan ≥ 55 fps | 2.6 fps | 38.6 / 39.0 fps (p95 29–30 ms) | **not met** |
| 1.3 pointer pan deltas applied once per pass | `f4a8e70` | 500 / 1000 Hz medium pan ≥ 58 fps | 17.8 / 1.2 fps | 60.0, 59.8, 60.0 / 59.8, 59.8, 60.0 | met |
| regression guard: 125 Hz, medium warm pan | | p95 ≤ baseline + 1 ms | 17.4–18.6 ms | 17.4–17.6 ms, 59.8 fps | met |

## 1.2 in detail

* Batching the view's own scrollbar changes (3 reports per pan → 1) alone took 4 linked views from 2.6 to 32 fps
  (handler 8.9 → 3.1 ms).
* Updating the minimaps once per event-loop pass took it to 38.6–39 fps (handler 1.9 ms).
* The remainder is paint, not event handling. 4 linked views repaint on every pan, at ≈6.3 ms each on the GL
  viewport (≈25 ms per round). Same run with `PIXELREF_GL=0` (software viewport): 51.5 / 51.9 fps, p95 23 ms.
  Step 1.5 ([07](07-gl-paint-investigation.md)) later showed this is not paint cost: the 6 ms is a blocking wait, and with
  all painting disabled 4 GL views still take 19 ms per round. The gate was not relaxed and is not reachable by paint
  optimisation. `QT_WIDGETS_RHI=1` looked like +10% but displays a black canvas (validated), so it is rejected.
* Deviation from the roadmap: the reports are collapsed synchronously at the source (`CanvasView._camera_batch`),
  not deferred to the next loop pass. Cameras and followers stay current with no added latency, and no
  deferral switch was needed, so there is no `PIXELREF_CAMERA_COALESCE`. The bus event is therefore still
  emitted once per view per change (not once per pass); the minimap is the part that is deferred.

## 1.1 note

Repaint-only changes (decode deliveries, GIF frames) now only mark the bounds stale; they are re-measured
at most every 3 s (`CanvasView.BOUNDS_REMEASURE_S`). A 0.5 s interval was tried first and failed the gate
(p95 41.9 ms: more than 5% of the 60 notches in the 5 s run re-measured). Canvas refreshes and
`item.geometry_changed` drop the bounds at once. Residual risk: geometry that changes without either hook
shows a stale zoom-out limit for up to 3 s.

## 1.3 note

`CanvasView.pan_input` is used by the pointer path; `pan_by` (scripts, `navigation.pan`) stays immediate.
The camera is brought up to date before `camera()`, `center()`, `set_camera`, `flip`, `zoom_by`,
`glide_zoom`, `fit_rect`, `pan_by`, `_apply_camera` and on release. `PIXELREF_PAN_COALESCE=0` restores
per-event panning. Under overload input-to-display latency can grow by up to one frame; at 125 Hz frame times
are unchanged.

## Tests

Added to `tests/test_views.py`: bounds survive repaints and follow geometry changes; one camera report per
pan / set_camera / zoom_by / glide step / fit_rect; pan input equals the unbatched result, flushes on read,
loses to a later scripted camera change, and scripted pans stay immediate.
