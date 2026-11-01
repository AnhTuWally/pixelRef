# 3. Benchmark Report

Every number below comes from `tools/bench_canvas_nav.py` runs stored in `data/*.jsonl` (one JSON object per
run, including the exact configuration). Tables are generated from those files by
`tools/summarize_nav_results.py`. Nothing is estimated unless the text says so.

## 3.1 Environment

| Item | Value |
|---|---|
| OS | Windows 10 Pro 10.0.19045 |
| CPU / RAM | Intel Core i7-7700 @ 3.6 GHz (4 cores / 8 threads), 64 GB |
| GPU | NVIDIA GeForce RTX 4060 Ti (driver 32.0.15.6094); a second display runs on Intel HD 630 |
| Display under test | DELL U2410, 1920×1200, **59.95 Hz**, device pixel ratio 1.0 (no scaling). Frame budget 16.68 ms. 120 Hz (8.33 ms) was **not** tested |
| Software | Python 3.14.10, PySide6/Qt 6.12.0 |
| Backends | `windows` platform with `CanvasGLWidget` (OpenGL, 4× MSAA) = **GL**; `PIXELREF_GL=0` = **software viewport** on the same display; `offscreen` = no display, no vsync, no GL (software) |
| Window | maximized; viewport 1586×1085 (GL/software), 1066×808 (offscreen) |
| Full record | `data/environment.json` |

macOS and Linux were not available; portability claims are by code inspection only.

## 3.2 Boards

Generated and cached by `bench_canvas_nav.py generate` into `<repo>/../pixelRef-perf-data/` (outside the repo,
~1.7 GB). Synthetic JPEG "photos" (gradient + 90 shapes + noise, quality 82), distinct file per image, every
item scaled to ≈1024 scene units, laid out on a grid with 15% gaps, random ±60 jitter and some rotation.

| Board | Contents | Photos on disk |
|---|---|---|
| small | 20 images (1024 px) | 4 MB |
| medium | 200 images | 40 MB |
| large | 1000 images | 199 MB |
| stress | 5000 images (800 px) | 637 MB |
| hires | 150 images, 4000 px long edge | 441 MB |
| mixed | 200 images + 30 animated GIFs (4 frames, 10 fps) + 300 annotations + 40 effect images (20 grayscale, 20 blur r=6) | 40 MB |
| gifonly / fxonly / notesonly | medium + only the GIFs / only the effects / only the annotations (diagnostic splits) | 40 MB |

Not covered: boards with images of very mixed sizes, real photographs, real-world layouts (overlapping,
packed), long GIFs, video.

## 3.3 Method

* **Real-time driver.** Events are injected from a 1 ms precise timer through `QApplication.sendEvent` to the
  viewport (mouse press/move/release with the middle button; `QWheelEvent`), at 125 Hz by default. The event loop
  runs normally: glide timers, coalesced paints, tile idle slices and worker deliveries interleave as in use.
  Unlike `tests/bench_interactive.py`, nothing calls `processEvents()` per event.
* **Scenarios** (5 s of input unless noted): `pan_zoomed` (zoom 0.35, ~25 images visible, 1200 px/s, direction
  reversed half-way), `pan_fit` (whole board in view), `zoom` (30 notches in toward the cursor at 25/s, 0.6 s
  pause, 30 out), `zoom_trackpad` (fractional steps, 120 Hz), `jump` (8 `set_camera` jumps 0.8 s apart),
  `jump_animated`, `frame_toggle`.
* **Cache states.** *cold*: right after the board opens, only the first frame painted. *settled*: the start view is
  fully rendered (visible + ring + decodes), nothing else is cached. *warm*: the scenario ran once, settled, ran again.
* **Metrics.** Frame presentation = `frameSwapped` of the GL viewport (software/offscreen: end of
  `paintEvent`). *Active-window intervals* = intervals starting within 300 ms of an input event; this removes
  the deliberate pauses of `zoom`/`jump` scenarios (the first matrix, A, predates it, so its zoom/jump rows
  show raw intervals and are only used for pan and for jump timings). Tick gaps = time between the driver's
  1 ms timer ticks, i.e. GUI-thread unavailability (resolution ~1–5 ms). Tile/hit/blank/blurry counts and idle
  slices come from runtime instrumentation of `TileCache`; image-level quality from `PixmapItem._draw_handle`.
  Time to full quality: poll until visible tiles complete, decoder idle, 5 stable polls.
* **Fresh process per configuration.** Matrices: A (48 runs baseline), B (56: sweeps, multi-view, media,
  backends, repeats), C (50: settled pan sweep + first variants), D (43), E (35), F (27: overview prototypes
  and the corrected refine metric).

### Validity notes (read before quoting a number)

1. **Run-to-run variation.** `medium / pan_zoomed / warm` ran four times: 59.8/59.8/59.6/59.8 fps, p95
   17.4–18.6 ms. `medium / zoom / cold` ran four times: p95 62.4, 69.3, 74.2, 75.0 ms. Differences under ~10% are noise.
   Most other configurations ran once. **Large-board warm zoom is unstable:** p95 119 ms in matrix B, 18 ms in
   matrix C for the same configuration (depends on which exact zoom levels the first pass left in the cache).
2. **Main-thread CPU share is not meaningful on GL.** The NVIDIA driver spins in `swap`; the `blocked ms` and
   `main cpu` columns reflect vsync waiting (0.8–0.97 on pans) and are not used to rank anything.
3. **Input rate.** 500/1000 Hz results are a stress model without OS mouse-move coalescing. Windows coalesces
   `WM_MOUSEMOVE`, so real mice are unlikely to queue that many events; the finding is the per-event cost ceiling
   (≈400 events/s), not a claim that users see 1 fps.
4. **Synthetic input** skips the OS/hardware path; "latency" is injection → next swap, a lower bound.
5. **The refine metric** in matrix E counted the unfinished overview as pending work and is invalid; matrix F
   fixed it and repeated the key rows.
6. **Software viewport** has no vsync pacing in this harness (it paints at 84–92/s); compare paint and handler
   costs, not frame intervals.
7. Prototype variants replace parts of `TileCache.paint` at runtime; they demonstrate potential, not a finished
   implementation.

## 3.4 Baseline results (GL, real display)

### Pan: boards of 20–1000 images hold the display rate

Continuous input, so raw presentation intervals are valid. `late/N` counts intervals > 1.01 × budget
(vsync jitter makes ~⅓ of intervals "late" by 0.1–1 ms even when nothing is wrong).

| run | fps | present p50 | p95 | p99 | max | late/N | dropped | paint p95 | paint max | lat p50 | lat p95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| small / pan_zoomed / cold | 60.4 | 16.6 | 20.6 | 26.1 | 206.4 | 93/304 | 15 | 6.5 | 39.7 | 14.9 | 18.0 |
| small / pan_zoomed / warm | 59.8 | 16.6 | 17.7 | 24.4 | 26.8 | 90/301 | 3 | 6.2 | 6.7 | 15.0 | 17.3 |
| small / pan_fit / cold | 59.8 | 16.7 | 18.7 | 22.5 | 31.3 | 99/301 | 2 | 6.1 | 7.4 | 14.8 | 17.6 |
| small / pan_fit / warm | 59.8 | 16.6 | 17.7 | 26.5 | 28.3 | 98/301 | 4 | 6.0 | 6.8 | 15.1 | 17.4 |
| medium / pan_zoomed / cold | 60.2 | 16.7 | 19.7 | 28.6 | 217.1 | 96/304 | 17 | 6.5 | 51.3 | 14.9 | 17.3 |
| medium / pan_zoomed / warm | 59.8 | 16.7 | 17.5 | 24.9 | 26.9 | 93/301 | 2 | 6.1 | 6.7 | 15.0 | 17.1 |
| medium / pan_fit / cold | 59.8 | 16.6 | 20.0 | 24.7 | 27.7 | 113/301 | 3 | 6.3 | 8.4 | 14.9 | 18.2 |
| medium / pan_fit / warm | 59.8 | 16.7 | 17.5 | 24.2 | 30.4 | 103/301 | 3 | 6.1 | 7.2 | 14.9 | 17.1 |
| large / pan_zoomed / cold | 60.0 | 16.6 | 17.6 | 29.2 | 235.2 | 99/303 | 22 | 6.3 | 64.4 | 14.9 | 17.3 |
| large / pan_zoomed / warm | 59.8 | 16.6 | 17.6 | 22.2 | 31.0 | 96/301 | 2 | 6.3 | 7.7 | 15.0 | 17.2 |
| large / pan_fit / cold | 59.8 | 16.6 | 19.6 | 25.2 | 30.0 | 96/301 | 6 | 6.4 | 8.1 | 14.9 | 17.9 |
| large / pan_fit / warm | 59.6 | 16.7 | 17.5 | 26.0 | 27.0 | 89/300 | 4 | 5.8 | 9.5 | 15.0 | 17.2 |
| stress / pan_zoomed / cold | 52.5 | 18.6 | 24.4 | 63.2 | 213.1 | 264/265 | 23 | 7.0 | 54.1 | 17.3 | 21.3 |
| stress / pan_zoomed / warm | 53.9 | 17.7 | 21.2 | 29.7 | 61.6 | 253/271 | 8 | 6.4 | 49.9 | 16.8 | 20.6 |
| stress / pan_fit / cold | 52.7 | 18.2 | 23.4 | 30.4 | 61.6 | 236/265 | 9 | 6.9 | 49.6 | 16.9 | 21.0 |
| stress / pan_fit / warm | 51.3 | 18.5 | 22.4 | 32.7 | 73.1 | 254/258 | 13 | 6.6 | 61.3 | 17.2 | 22.1 |
| hires / pan_zoomed / cold | 50.9 | 16.6 | 37.5 | 71.3 | 119.4 | 101/256 | 55 | 8.8 | 57.9 | 15.4 | 58.1 |
| hires / pan_zoomed / warm | 59.8 | 16.7 | 17.6 | 23.1 | 29.1 | 93/301 | 3 | 6.0 | 6.6 | 14.9 | 17.1 |
| hires / pan_fit / cold | 52.5 | 16.7 | 25.8 | 88.1 | 108.6 | 104/264 | 40 | 6.7 | 53.9 | 15.8 | 71.6 |
| hires / pan_fit / warm | 59.8 | 16.6 | 17.7 | 22.3 | 29.9 | 94/301 | 1 | 6.3 | 8.6 | 15.0 | 17.2 |
| mixed / pan_zoomed / cold | 33.7 | 27.2 | 64.3 | 86.4 | 163.6 | 125/169 | 149 | 21.4 | 87.6 | 23.5 | 58.3 |
| mixed / pan_zoomed / warm | 44.2 | 24.6 | 58.0 | 74.7 | 91.0 | 161/222 | 148 | 17.4 | 23.5 | 17.6 | 35.5 |
| mixed / pan_fit / cold | 39.0 | 17.4 | 84.6 | 98.0 | 101.3 | 114/196 | 122 | 49.2 | 64.8 | 19.1 | 85.8 |
| mixed / pan_fit / warm | 37.6 | 16.9 | 81.8 | 94.8 | 96.6 | 98/189 | 127 | 44.5 | 57.9 | 21.0 | 82.5 |

The tile columns for the same runs (hit rate, synchronous renders, fallback and blur):

| run | hit % | sync tiles | sync frames | worst sync ms | fallback fr | blurry fr | mag p50 | blank | idle sl | idle@input | idle max ms | inval | img exact % | placeholder |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| small / pan_zoomed / cold | 31.1 | 125 | 12 | 28.3 | 304 | 304 | 1.53 | 0 | 6 | 1 | 12.6 | 24 | 41 | 0 |
| small / pan_zoomed / warm | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 0 | 0 | – | 0 | 0 | 0 |
| small / pan_fit / cold | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 15 | 15 | 6.7 | 0 | 0 | 0 |
| small / pan_fit / warm | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 0 | 0 | – | 0 | 0 | 0 |
| medium / pan_zoomed / cold | 0.8 | 72 | 2 | 36.9 | 300 | 300 | 5.37 | 0 | 8 | 0 | 13.0 | 48 | 19 | 0 |
| medium / pan_zoomed / warm | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 0 | 0 | – | 0 | 0 | 0 |
| medium / pan_fit / cold | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 15 | 15 | 11.0 | 0 | 0 | 0 |
| medium / pan_fit / warm | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 1 | 1 | 1.0 | 0 | 0 | 0 |
| large / pan_zoomed / cold | 0.4 | 61 | 2 | 50.7 | 300 | 300 | 12.28 | 0 | 12 | 1 | 15.4 | 48 | 74 | 0 |
| large / pan_zoomed / warm | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 1 | 1 | 13.6 | 8 | 75 | 0 |
| large / pan_fit / cold | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 14 | 14 | 8.6 | 0 | 0 | 0 |
| large / pan_fit / warm | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 0 | 0 | – | 0 | 0 | 0 |
| stress / pan_zoomed / cold | 0.5 | 59 | 2 | 34.2 | 262 | 262 | 27.19 | 0 | 9 | 2 | 14.7 | 56 | 68 | 0 |
| stress / pan_zoomed / warm | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 2 | 2 | 6.4 | 0 | 100 | 0 |
| stress / pan_fit / cold | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 15 | 15 | 6.1 | 0 | 0 | 0 |
| stress / pan_fit / warm | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 0 | 0 | – | 0 | 0 | 0 |
| hires / pan_zoomed / cold | 96.5 | 139 | 13 | 48.8 | 21 | 21 | 4.63 | 0 | 36 | 36 | 13.5 | 336 | 44 | 100 |
| hires / pan_zoomed / warm | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 0 | 0 | – | 0 | 0 | 0 |
| hires / pan_fit / cold | 99.4 | 63 | 10 | 20.2 | 0 | 0 | – | 0 | 20 | 20 | 13.6 | 104 | 65 | 227 |
| hires / pan_fit / warm | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 0 | 0 | – | 0 | 0 | 0 |
| mixed / pan_zoomed / cold | 92.7 | 469 | 73 | 74.3 | 0 | 0 | – | 0 | 99 | 95 | 16.6 | 904 | 73 | 0 |
| mixed / pan_zoomed / warm | 94.7 | 452 | 103 | 19.2 | 0 | 0 | – | 0 | 88 | 62 | 14.1 | 1232 | 87 | 0 |
| mixed / pan_fit / cold | 96.3 | 277 | 42 | 58.7 | 0 | 0 | – | 0 | 44 | 44 | 18.0 | 912 | 96 | 0 |
| mixed / pan_fit / warm | 95.9 | 292 | 43 | 53.5 | 0 | 0 | – | 0 | 42 | 42 | 17.5 | 1144 | 97 | 0 |

Reading it:

* 20–1000 images: 59.8–60.4 fps and p95 17.5–20 ms warm; p99 22–29 ms.
* **Cold pan right after a jump** (`pan_zoomed / cold`): hits 0.4–0.8% on 200–5000 items; all ~300 frames are drawn
  from fallbacks magnified by a median of 1.5× (small) to 27× (stress; medium 5×, large 12×), then refined 0.3–0.4 s after the
  drag ends. First-frame stall 206–235 ms.
* **Stress (5000)**: 51–54 fps, p95 21–24 ms, paint max 50–61 ms (a tile covers ~300 items).
* **Hires cold**: p95 25–37 ms, 40–55 dropped frames, 104–336 tile invalidations from decode deliveries.
* **Mixed** (GIFs): 34–44 fps, paint p95 17–49 ms, 277–469 tiles rendered synchronously in 42–103 frames.

### Pan speed, input rate, view count, media (medium board unless stated)

| run | fps | act p50 | p95 | p99 | max | late | >2x | >4x | gap>33 | gap max | paint p95 | paint max | sync tiles | blurry fr | refine s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| medium / pan_zoomed / warm / 3000px/s | 59.8 | 16.7 | 17.4 | 25.3 | 27.9 | 97/301 | 0 | 0 | 0 | 24.5 | 5.8 | 6.9 | 0 | 0 | 0.01 |
| medium / pan_zoomed / cold / 3000px/s | 60.2 | 16.6 | 20.2 | 23.6 | 198.1 | 95/303 | 2 | 1 | 0 | 21.3 | 6.5 | 40.7 | 97 | 301 | 0.29 |
| medium / pan_zoomed / warm / 6000px/s | 59.8 | 16.6 | 18.1 | 25.7 | 29.0 | 94/301 | 0 | 0 | 0 | 23.1 | 6.0 | 8.9 | 0 | 0 | 0.01 |
| medium / pan_zoomed / cold / 6000px/s | 53.7 | 16.6 | 36.0 | 50.8 | 75.0 | 109/270 | 16 | 2 | 12 | 50.5 | 10.0 | 28.6 | 238 | 82 | 0.02 |
| medium / pan_zoomed / warm / 500Hz | 17.8 | 49.2 | 84.9 | 90.6 | 90.6 | 89/89 | 87 | 28 | 51 | 47.9 | 1.3 | 1.4 | 0 | 0 | 0.02 |
| stress / pan_zoomed / warm / 500Hz | 12.9 | 65.2 | 118.3 | 140.7 | 140.7 | 64/64 | 64 | 30 | 71 | 72.3 | 5.9 | 6.4 | 0 | 0 | 0.02 |
| medium / pan_zoomed / warm / 1000Hz | 1.2 | 314.1 | 3839.7 | 3839.7 | 3839.7 | 6/6 | 5 | 5 | 7 | 2457.7 | 38.3 | 38.3 | 45 | 0 | 0.04 |
| stress / pan_zoomed / warm / 1000Hz | 1.0 | 783.9 | 5022.8 | 5022.8 | 5022.8 | 4/4 | 4 | 4 | 7 | 2897.2 | 56.6 | 56.6 | 25 | 0 | 0.02 |
| medium / pan_zoomed / warm / 4v | 52.7 | 18.1 | 22.8 | 27.5 | 28.5 | 244/265 | 0 | 0 | 0 | 20.5 | 1.1 | 1.8 | 0 | 0 | 0.01 |
| medium / pan_zoomed / warm / 4v-linked | 2.6 | 371.4 | 892.5 | 892.5 | 892.5 | 12/12 | 12 | 12 | 22 | 452.2 | 6.7 | 6.7 | 3 | 0 | 0.01 |
| medium / pan_zoomed / cold / 4v-linked | 2.6 | 179.9 | 1522.3 | 1522.3 | 1522.3 | 15/15 | 13 | 10 | 17 | 871.8 | 11.1 | 11.1 | 44 | 5 | 0.14 |

Rows in order: 3000 and 6000 px/s (warm, cold); 500 Hz and 1000 Hz input (medium, then stress); four views
unlinked and linked, then the cold linked case.

Settled start (prefetch ring only), pan speed sweep:

| run | fps | act p50 | p95 | p99 | max | late | >2x | >4x | gap>33 | gap max | paint p95 | paint max | sync tiles | blurry fr | refine s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| medium / pan_zoomed / settled | 58.4 | 16.6 | 28.0 | 42.8 | 48.0 | 94/294 | 8 | 0 | 0 | 32.8 | 6.2 | 11.4 | 0 | 0 | 0.01 |
| medium / pan_zoomed / settled / 3000px/s | 57.6 | 16.5 | 28.1 | 43.8 | 52.7 | 99/290 | 8 | 0 | 0 | 28.8 | 6.3 | 7.9 | 6 | 0 | 0.02 |
| medium / pan_zoomed / settled / 6000px/s | 55.6 | 16.5 | 31.4 | 45.5 | 66.3 | 97/280 | 11 | 0 | 7 | 43.9 | 6.3 | 15.6 | 77 | 0 | 0.01 |
| stress / pan_zoomed / settled | 43.8 | 18.4 | 50.0 | 61.9 | 67.0 | 207/220 | 29 | 1 | 10 | 72.6 | 7.8 | 52.1 | 2 | 0 | 0.01 |
| stress / pan_zoomed / settled / 3000px/s | 31.5 | 22.2 | 65.7 | 76.2 | 100.5 | 155/158 | 49 | 6 | 32 | 98.6 | 18.9 | 52.2 | 145 | 0 | 0.02 |
| stress / pan_zoomed / settled / 6000px/s | 21.4 | 32.7 | 107.3 | 123.7 | 145.7 | 105/107 | 52 | 27 | 37 | 143.1 | 44.9 | 53.2 | 624 | 0 | 0.01 |
| hires / pan_zoomed / settled | 58.6 | 16.5 | 24.2 | 44.9 | 65.0 | 96/295 | 4 | 0 | 0 | 29.1 | 6.1 | 12.3 | 0 | 0 | 0.01 |
| hires / pan_zoomed / settled / 3000px/s | 57.6 | 16.6 | 25.1 | 42.6 | 60.9 | 100/290 | 7 | 0 | 1 | 43.4 | 6.2 | 14.0 | 32 | 0 | 0.01 |
| hires / pan_zoomed / settled / 6000px/s | 55.4 | 16.6 | 27.7 | 61.5 | 87.5 | 100/279 | 7 | 2 | 3 | 71.0 | 5.8 | 55.4 | 104 | 0 | 0.01 |

### Media

| run | fps | act p50 | p95 | p99 | max | late | >2x | >4x | gap>33 | gap max | paint p95 | paint max | sync tiles | blurry fr | refine s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gifonly / pan_zoomed / warm | 52.5 | 18.8 | 33.7 | 55.8 | 68.1 | 155/258 | 13 | 1 | 0 | 31.9 | 10.0 | 16.1 | 442 | 0 | 0.03 |
| gifonly / zoom / warm | 27.0 | 32.0 | 80.9 | 160.0 | 160.0 | 68/80 | 39 | 11 | 39 | 84.2 | 59.7 | 73.4 | 1435 | 22 | 0.36 |
| fxonly / pan_zoomed / warm | 59.8 | 16.7 | 18.2 | 22.3 | 36.2 | 95/301 | 1 | 0 | 0 | 26.2 | 6.1 | 6.8 | 0 | 0 | 0.02 |
| fxonly / zoom / warm | 50.9 | 16.8 | 20.1 | 135.9 | 157.8 | 73/170 | 2 | 2 | 0 | 20.5 | 6.5 | 9.9 | 0 | 111 | 0.06 |
| notesonly / pan_zoomed / warm | 59.8 | 16.6 | 18.0 | 24.8 | 42.1 | 90/301 | 1 | 0 | 1 | 38.1 | 6.0 | 10.7 | 0 | 0 | 0.01 |
| notesonly / zoom / warm | 50.9 | 16.9 | 19.2 | 149.3 | 154.4 | 86/169 | 3 | 2 | 1 | 37.1 | 6.8 | 11.8 | 0 | 111 | 0.07 |

| run | hit % | sync tiles | sync frames | worst sync ms | fallback fr | blurry fr | mag p50 | blank | idle sl | idle@input | idle max ms | inval | img exact % | placeholder |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gifonly / pan_zoomed / warm | 95.6 | 442 | 117 | 12.5 | 0 | 0 | – | 0 | 78 | 68 | 13.3 | 1416 | 84 | 0 |
| gifonly / zoom / warm | 5.8 | 1435 | 61 | 64.6 | 80 | 22 | 1.09 | 0 | 9 | 0 | 13.1 | 512 | 91 | 0 |
| fxonly / pan_zoomed / warm | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 0 | 0 | – | 0 | 0 | 0 |
| fxonly / zoom / warm | 1.7 | 0 | 0 | 0.0 | 166 | 111 | 2.74 | 0 | 6 | 0 | 12.8 | 0 | 19 | 0 |
| notesonly / pan_zoomed / warm | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 1 | 1 | 3.0 | 0 | 2 | 0 |
| notesonly / zoom / warm | 2.2 | 0 | 0 | 0.0 | 166 | 111 | 2.92 | 0 | 9 | 0 | 13.4 | 0 | 7 | 0 |

### Zoom

| run | fps | act p50 | p95 | p99 | max | late | >2x | >4x | gap>33 | gap max | paint p95 | paint max | sync tiles | blurry fr | refine s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gifonly / zoom / warm | 27.0 | 32.0 | 80.9 | 160.0 | 160.0 | 68/80 | 39 | 11 | 39 | 84.2 | 59.7 | 73.4 | 1435 | 22 | 0.36 |
| fxonly / zoom / warm | 50.9 | 16.8 | 20.1 | 135.9 | 157.8 | 73/170 | 2 | 2 | 0 | 20.5 | 6.5 | 9.9 | 0 | 111 | 0.06 |
| notesonly / zoom / warm | 50.9 | 16.9 | 19.2 | 149.3 | 154.4 | 86/169 | 3 | 2 | 1 | 37.1 | 6.8 | 11.8 | 0 | 111 | 0.07 |
| medium / zoom / cold | 37.3 | 16.9 | 69.3 | 75.0 | 165.2 | 64/118 | 25 | 8 | 24 | 66.8 | 50.5 | 57.8 | 888 | 79 | 0.33 |
| medium / zoom / cold | 35.8 | 17.2 | 74.2 | 95.8 | 187.3 | 73/116 | 20 | 7 | 18 | 78.5 | 46.9 | 60.9 | 825 | 78 | 0.06 |
| small / zoom / cold | 40.0 | 17.1 | 40.2 | 104.8 | 162.7 | 82/131 | 15 | 3 | 9 | 81.6 | 24.9 | 64.1 | 1135 | 78 | 0.10 |
| small / zoom / warm | 50.6 | 16.8 | 18.7 | 130.3 | 157.7 | 83/170 | 2 | 2 | 0 | 21.5 | 6.2 | 12.8 | 0 | 110 | 0.05 |
| large / zoom / cold | 32.7 | 17.0 | 94.1 | 233.1 | 233.6 | 61/103 | 19 | 9 | 20 | 174.7 | 60.8 | 134.6 | 500 | 77 | 0.23 |
| large / zoom / warm | 30.9 | 16.9 | 119.5 | 228.5 | 228.5 | 52/96 | 16 | 14 | 17 | 185.8 | 73.0 | 154.5 | 450 | 80 | 0.27 |
| stress / zoom / cold | 3.0 | 19.3 | 509.3 | 509.3 | 509.3 | 3/4 | 2 | 2 | 7 | 1833.9 | 652.5 | 652.5 | 203 | 1 | 4.80 |
| stress / zoom / warm | 10.0 | 133.0 | 508.3 | 990.9 | 990.9 | 21/21 | 19 | 17 | 29 | 723.1 | 230.9 | 243.2 | 515 | 4 | 0.67 |
| hires / zoom / cold | 23.6 | 34.1 | 84.6 | 159.7 | 159.7 | 68/73 | 40 | 10 | 34 | 108.2 | 51.6 | 93.2 | 1242 | 28 | 0.11 |
| hires / zoom / warm | 50.9 | 16.8 | 20.4 | 125.3 | 162.4 | 75/170 | 2 | 2 | 0 | 22.7 | 7.0 | 11.6 | 0 | 111 | 0.05 |
| mixed / zoom / cold | 14.8 | 61.2 | 120.1 | 195.5 | 195.5 | 41/42 | 36 | 18 | 37 | 157.1 | 81.0 | 147.2 | 1074 | 4 | 0.23 |
| mixed / zoom / warm | 163.0 | 44.0 | 99.8 | 165.0 | 165.0 | 60/61 | 41 | 13 | 37 | 95.5 | 61.0 | 83.5 | 7381 | 5 | – |
| medium / zoom_trackpad / warm | 35.3 | 17.1 | 76.7 | 95.2 | 154.3 | 110/166 | 35 | 22 | 32 | 79.6 | 37.7 | 55.8 | 713 | 120 | 0.13 |
| stress / zoom_trackpad / warm | 11.1 | 64.3 | 359.8 | 415.3 | 415.3 | 37/44 | 24 | 22 | 36 | 1346.6 | 213.6 | 360.4 | 610 | 14 | 0.60 |

| run | hit % | sync tiles | sync frames | worst sync ms | fallback fr | blurry fr | mag p50 | blank | idle sl | idle@input | idle max ms | inval | img exact % | placeholder |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gifonly / zoom / warm | 5.8 | 1435 | 61 | 64.6 | 80 | 22 | 1.09 | 0 | 9 | 0 | 13.1 | 512 | 91 | 0 |
| fxonly / zoom / warm | 1.7 | 0 | 0 | 0.0 | 166 | 111 | 2.74 | 0 | 6 | 0 | 12.8 | 0 | 19 | 0 |
| notesonly / zoom / warm | 2.2 | 0 | 0 | 0.0 | 166 | 111 | 2.92 | 0 | 9 | 0 | 13.4 | 0 | 7 | 0 |
| medium / zoom / cold | 3.4 | 888 | 32 | 49.3 | 115 | 79 | 4.44 | 0 | 14 | 0 | 13.8 | 88 | 75 | 0 |
| medium / zoom / cold | 2.8 | 825 | 29 | 46.3 | 106 | 78 | 5.37 | 0 | 18 | 6 | 14.5 | 80 | 73 | 0 |
| small / zoom / cold | 2.0 | 1135 | 42 | 39.1 | 127 | 78 | 2.91 | 0 | 6 | 0 | 12.6 | 64 | 84 | 0 |
| small / zoom / warm | 1.2 | 0 | 0 | 0.0 | 165 | 110 | 2.73 | 0 | 4 | 0 | 12.5 | 0 | 47 | 0 |
| large / zoom / cold | 6.2 | 500 | 18 | 123.4 | 100 | 77 | 6.77 | 0 | 31 | 4 | 17.5 | 96 | 80 | 0 |
| large / zoom / warm | 6.5 | 450 | 14 | 145.2 | 98 | 80 | 8.50 | 0 | 50 | 25 | 17.6 | 24 | 90 | 0 |
| stress / zoom / cold | 10.3 | 203 | 6 | 638.8 | 4 | 1 | 1.09 | 0 | 10 | 5 | 67.6 | 48 | 86 | 0 |
| stress / zoom / warm | 28.8 | 515 | 18 | 223.2 | 25 | 4 | 1.00 | 0 | 57 | 29 | 20.8 | 96 | 98 | 0 |
| hires / zoom / cold | 4.7 | 1242 | 50 | 53.4 | 68 | 28 | 1.00 | 0 | 12 | 0 | 13.5 | 456 | 59 | 309 |
| hires / zoom / warm | 1.8 | 0 | 0 | 0.0 | 166 | 111 | 2.73 | 0 | 5 | 0 | 12.6 | 0 | 17 | 0 |
| mixed / zoom / cold | 13.7 | 1074 | 40 | 140.7 | 33 | 4 | 1.00 | 0 | 38 | 22 | 16.9 | 392 | 74 | 0 |
| mixed / zoom / warm | 62.9 | 7381 | 532 | 74.2 | 533 | 5 | 1.00 | 0 | 7 | 0 | 13.3 | 4208 | 100 | 0 |
| medium / zoom_trackpad / warm | 7.0 | 713 | 33 | 47.3 | 157 | 120 | 4.03 | 0 | 76 | 65 | 15.3 | 40 | 90 | 0 |
| stress / zoom_trackpad / warm | 16.2 | 610 | 24 | 348.7 | 46 | 14 | 1.04 | 0 | 66 | 42 | 28.3 | 64 | 98 | 0 |

Zoom is the weak spot. Pan frames come from tiles that already exist; each glide step is a new zoom level, and the
tiles it needs that no older level covers are rendered in `paint`.

### Jump (cold and warm), from matrix A

`jump 1st` = time from `set_camera` to the first completed paint; `jump full` = time until visible tiles and decodes
are complete.

| run | visible s | all s | frames | jump 1st ms p50 | jump 1st max | jump full p50 | jump full max | never |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| small / jump / cold | 0.01 | 0.01 | 0 | 9.6 | 15.2 | 148 | 343 | 0 |
| small / jump / warm | 0.01 | 0.01 | 0 | 6.2 | 6.6 | 17 | 21 | 0 |
| medium / jump / cold | 0.01 | – | 0 | 17.6 | 41.0 | 210 | 1005 | 0 |
| medium / jump / warm | 0.02 | 0.02 | 0 | 5.6 | 8.5 | 19 | 22 | 0 |
| large / jump / cold | 0.01 | 5.89 | 1 | 17.0 | 70.7 | 329 | 497 | 0 |
| large / jump / warm | 0.01 | 0.01 | 0 | 6.1 | 9.5 | 19 | 22 | 0 |
| stress / jump / cold | 0.01 | – | 0 | 11.1 | 53.4 | 439 | 496 | 0 |
| stress / jump / warm | 0.01 | 0.01 | 0 | 10.3 | 11.3 | 25 | 27 | 0 |
| hires / jump / cold | 0.01 | 0.01 | 0 | 60.8 | 98.4 | 166 | 1613 | 0 |
| hires / jump / warm | 0.02 | 0.02 | 0 | 5.7 | 10.1 | 17 | 24 | 0 |
| mixed / jump / cold | 0.01 | 0.01 | 0 | 49.5 | 64.6 | 203 | 261 | 0 |
| mixed / jump / warm | 0.01 | 0.04 | 0 | 12.7 | 18.5 | 29 | 56 | 0 |

Warm: first frame 6–13 ms, full quality 17–29 ms. Cold: first frame 10–61 ms (max 98 ms), full quality median
148–439 ms, worst 0.34–1.6 s.

### Backends

Medium board, GL vs software viewport vs offscreen (same code, same events):

| run | fps | act p50 | p95 | p99 | max | late | >2x | >4x | gap>33 | gap max | paint p95 | paint max | sync tiles | blurry fr | refine s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| medium / pan_zoomed / cold / sw | 84.2 | 11.3 | 16.5 | 23.6 | 219.9 | 13/424 | 3 | 1 | 0 | 18.5 | 3.5 | 44.3 | 69 | 422 | 0.34 |
| medium / pan_zoomed / warm / sw | 89.9 | 10.5 | 15.2 | 16.9 | 26.8 | 5/453 | 0 | 0 | 0 | 20.6 | 2.1 | 4.4 | 0 | 0 | 0.01 |
| medium / zoom / cold / sw | 37.6 | 17.2 | 69.9 | 86.2 | 176.9 | 87/121 | 18 | 8 | 14 | 66.7 | 43.0 | 57.8 | 947 | 78 | 0.11 |
| medium / zoom / warm / sw | 51.2 | 17.0 | 18.1 | 138.7 | 162.1 | 103/166 | 2 | 2 | 0 | 20.1 | 8.1 | 10.1 | 0 | 111 | 0.05 |
| medium / pan_zoomed / cold / offscreen | 124.2 | 8.0 | 8.3 | 9.1 | 195.6 | 2/626 | 1 | 1 | 0 | 7.0 | 2.0 | 19.4 | 29 | 625 | 0.24 |
| medium / pan_zoomed / warm / offscreen | 123.8 | 8.0 | 8.4 | 8.8 | 11.0 | 0/624 | 0 | 0 | 0 | 5.8 | 1.1 | 1.6 | 0 | 0 | 0.01 |
| medium / zoom / cold / offscreen | 42.1 | 17.2 | 49.7 | 83.7 | 167.9 | 102/136 | 12 | 4 | 6 | 71.3 | 26.0 | 66.5 | 761 | 79 | 0.05 |
| medium / zoom / warm / offscreen | 51.2 | 17.0 | 18.5 | 141.1 | 161.5 | 114/166 | 2 | 2 | 0 | 17.8 | 5.5 | 6.0 | 0 | 111 | 0.05 |
| medium / pan_zoomed / warm | 59.6 | 16.6 | 18.6 | 27.2 | 29.0 | 90/300 | 0 | 0 | 0 | 22.6 | 5.9 | 6.7 | 0 | 0 | 0.01 |
| medium / zoom / cold | 37.3 | 16.9 | 69.3 | 75.0 | 165.2 | 64/118 | 25 | 8 | 24 | 66.8 | 50.5 | 57.8 | 888 | 79 | 0.33 |
| medium / pan_zoomed / warm | 59.8 | 16.6 | 18.6 | 28.7 | 31.5 | 102/301 | 0 | 0 | 0 | 26.8 | 4.6 | 8.0 | 0 | 0 | 0.02 |
| medium / zoom / cold | 35.8 | 17.2 | 74.2 | 95.8 | 187.3 | 73/116 | 20 | 7 | 18 | 78.5 | 46.9 | 60.9 | 825 | 78 | 0.06 |

* Pure tile-blit frames (warm pan) cost **5.8–6.0 ms** in `paintEvent` on GL, **2.1 ms** software, **1.1 ms** offscreen.
  The GL path costs ~4 ms more per paint for the same work; why was not isolated (hypothesis: flush/composition
  inside `paintEvent`).
* GL gives vsync-locked 59.8 fps. The software viewport's cost per event is the same (handler 2.2–2.3 ms) and
  stalls identically on zoom, confirming the stalls are CPU-side tile rendering, not the GPU.
* Large boards on the software viewport:

| run | fps | act p50 | p95 | p99 | max | late | >2x | >4x | gap>33 | gap max | paint p95 | paint max | sync tiles | blurry fr | refine s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| stress / pan_zoomed / cold / sw | 59.4 | 16.5 | 19.4 | 61.4 | 260.4 | 114/299 | 3 | 2 | 1 | 57.9 | 8.1 | 52.8 | 59 | 298 | 0.43 |
| stress / pan_zoomed / warm / sw | 58.2 | 16.6 | 21.1 | 38.1 | 62.6 | 117/293 | 3 | 0 | 2 | 58.3 | 7.0 | 52.2 | 0 | 0 | 0.01 |
| stress / zoom / cold / sw | 5.5 | 17.7 | 431.2 | 431.2 | 431.2 | 4/5 | 2 | 2 | 8 | 1444.9 | 332.1 | 332.1 | 198 | 2 | 0.82 |
| stress / zoom / warm / sw | 11.5 | 56.5 | 524.2 | 639.0 | 639.0 | 27/27 | 22 | 12 | 29 | 351.0 | 177.1 | 239.0 | 552 | 4 | 0.69 |
| hires / pan_zoomed / cold / sw | 67.5 | 11.5 | 35.0 | 56.4 | 103.1 | 60/339 | 18 | 3 | 10 | 61.8 | 5.9 | 41.0 | 170 | 3 | 0.01 |
| hires / pan_zoomed / warm / sw | 91.9 | 10.2 | 14.5 | 16.9 | 25.8 | 5/463 | 0 | 0 | 0 | 18.9 | 1.9 | 4.3 | 0 | 0 | 0.01 |
| hires / zoom / cold / sw | 27.6 | 27.9 | 80.3 | 162.4 | 162.4 | 67/84 | 37 | 6 | 27 | 64.0 | 37.5 | 45.0 | 1260 | 31 | 0.14 |
| hires / zoom / warm / sw | 51.2 | 17.0 | 18.4 | 130.0 | 162.1 | 106/166 | 2 | 2 | 0 | 19.6 | 7.6 | 8.9 | 0 | 111 | 0.04 |

### Event handler cost (microbenchmark, not on-screen)

| Operation | ms |
|---|---:|
| `pan_by` (single view, 200 or 5000 items) | 1.41 |
| one scrollbar `setValue` | 0.58 (two per `pan_by`) |
| `camera_changed.emit()` (all listeners) | 0.107 |
| `_camera_moved(sync_state=True)` | 0.122 |
| `min_zoom()` cached | 0.007 (200) / 0.110 (5000) |
| `min_zoom()` after scene change | 1.49 (200) / **42.2** (5000) |
| `pan_by`, 4 linked views | 9.27 |
| one follower `set_camera` | 1.66 |
| tile render, 200 items (≈19 items/tile) | 2.5 ms/tile |
| tile render, 5000 items at fit zoom (≈300 items/tile) | 12.5 ms/tile |
| …with `PixmapItem.boundingRect` cached | 11.6–12.7 (no change) |

The 2.2–2.9 ms handler time the on-screen runs report is larger than `pan_by` alone (1.4 ms): the rest is the
input pipeline (event objects, `event_guard`, handler chain, cursor). It was not profiled separately.

### Memory

| run | items | open ms | tiles MB | img store MB | mip MB | rss end MB | rss peak MB |
|---|---:|---:|---:|---:|---:|---:|---:|
| small / pan_zoomed / warm | 20 | 114 | 61.0 | 18.8 | 0.0 | 342 | 342 |
| medium / pan_zoomed / warm | 200 | 350 | 65.8 | 78.5 | 0.0 | 416 | 416 |
| large / pan_zoomed / warm | 1000 | 1127 | 67.0 | 75.4 | 0.0 | 423 | 423 |
| stress / pan_zoomed / warm | 5000 | 4603 | 69.0 | 62.0 | 0.0 | 452 | 452 |
| hires / pan_zoomed / warm | 150 | 310 | 59.2 | 60.6 | 0.0 | 388 | 388 |
| mixed / pan_zoomed / warm | 530 | 1194 | 62.8 | 114.1 | 40.9 | 621 | 631 |
| small / zoom / warm | 20 | 109 | 33.8 | 54.6 | 0.0 | 358 | 360 |
| medium / zoom / warm | 200 | 325 | 35.5 | 81.0 | 0.0 | 390 | 391 |
| large / zoom / warm | 1000 | 1130 | 38.0 | 92.9 | 0.0 | 413 | 418 |
| stress / zoom / warm | 5000 | 4564 | 35.8 | 104.8 | 0.0 | 456 | 456 |
| hires / zoom / warm | 150 | 263 | 33.8 | 273.4 | 0.0 | 579 | 579 |
| mixed / zoom / warm | 530 | 1047 | 23.8 | 154.2 | 40.9 | 623 | 633 |
| medium / pan_zoomed / warm / 4v | 200 | 348 | 60.2 | 54.7 | 0.0 | 391 | 391 |
| medium / pan_zoomed / warm / 4v-linked | 200 | 354 | 128.2 | 58.5 | 0.0 | 466 | 466 |
| medium / pan_zoomed / cold / 4v-linked | 200 | 337 | 88.5 | 52.7 | 0.0 | 411 | 411 |

Tile memory 30–70 MB per view on a 1586×1085 viewport (budget 256 MB per view). A 20-image board still holds
≈50 MB of tiles (background grid + ring). Four views ≈ 4× that. RSS peaks 320–720 MB; the image store reached
273 MB on the hi-res board.

## 3.5 Prototype results

Zoom, matrices C and D (cold and warm; `nosync`/`budget`/`coarse` variants):

| run | fps | act p50 | p95 | p99 | max | late | >2x | >4x | gap>33 | gap max | paint p95 | paint max | sync tiles | blurry fr | refine s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| medium / zoom / cold | 36.4 | 17.2 | 75.0 | 90.9 | 176.2 | 75/119 | 19 | 8 | 18 | 73.3 | 44.4 | 61.0 | 826 | 78 | 0.10 |
| medium / zoom / cold / nosync | 51.2 | 17.0 | 22.5 | 129.6 | 187.2 | 100/169 | 2 | 2 | 1 | 42.4 | 5.3 | 28.9 | 61 | 110 | 0.06 |
| medium / zoom / cold / coarse_zoom | 46.1 | 17.0 | 35.4 | 128.5 | 176.8 | 80/148 | 9 | 2 | 6 | 58.9 | 21.9 | 48.1 | 236 | 97 | 0.25 |
| medium / zoom / warm | 50.9 | 16.9 | 19.3 | 123.0 | 160.9 | 87/170 | 2 | 2 | 0 | 20.1 | 6.1 | 11.7 | 0 | 110 | 0.05 |
| medium / zoom / warm / nosync | 50.3 | 16.8 | 19.1 | 137.8 | 402.2 | 78/170 | 2 | 2 | 0 | 20.6 | 6.4 | 11.9 | 0 | 64 | 0.06 |
| medium / zoom / warm / coarse_zoom | 50.9 | 16.9 | 19.7 | 129.0 | 152.0 | 88/169 | 2 | 2 | 0 | 23.3 | 6.1 | 12.3 | 0 | 51 | 0.06 |
| large / zoom / cold | 30.9 | 17.1 | 91.3 | 394.6 | 394.6 | 63/98 | 15 | 9 | 17 | 322.7 | 63.7 | 268.8 | 534 | 77 | 0.23 |
| large / zoom / cold / nosync | 50.6 | 16.9 | 20.1 | 164.3 | 194.5 | 87/168 | 3 | 2 | 2 | 53.1 | 6.8 | 37.5 | 35 | 108 | 0.10 |
| large / zoom / cold / coarse_zoom | 39.1 | 17.3 | 84.8 | 198.1 | 202.7 | 80/121 | 23 | 7 | 15 | 95.2 | 41.0 | 72.5 | 188 | 92 | 0.52 |
| large / zoom / warm | 51.2 | 16.8 | 18.4 | 158.8 | 162.7 | 74/169 | 3 | 2 | 1 | 38.0 | 6.6 | 12.4 | 0 | 112 | 0.08 |
| large / zoom / warm / nosync | 49.7 | 16.9 | 20.4 | 158.6 | 400.6 | 86/168 | 3 | 2 | 1 | 56.6 | 6.3 | 13.0 | 0 | 62 | 0.08 |
| large / zoom / warm / coarse_zoom | 51.5 | 16.8 | 18.3 | 154.2 | 162.7 | 80/169 | 3 | 2 | 1 | 36.7 | 6.2 | 8.1 | 0 | 60 | 0.08 |
| stress / zoom / cold | 3.0 | 20.6 | 463.2 | 463.2 | 463.2 | 4/4 | 2 | 2 | 7 | 1656.5 | 342.5 | 342.5 | 195 | 1 | 1.50 |
| stress / zoom / cold / nosync | 29.4 | 17.4 | 133.0 | 255.9 | 255.9 | 71/95 | 15 | 11 | 16 | 446.2 | 9.0 | 245.4 | 60 | 9 | 0.02 |
| stress / zoom / cold / coarse_zoom | 4.8 | 201.9 | 831.9 | 831.9 | 831.9 | 13/13 | 11 | 11 | 18 | 819.0 | 233.9 | 233.9 | 139 | 7 | 0.46 |
| stress / zoom / cold / minz | 2.7 | 236.2 | 404.6 | 404.6 | 404.6 | 4/5 | 3 | 3 | 7 | 1737.2 | 297.9 | 297.9 | 193 | 2 | 0.63 |
| stress / zoom / cold / minz,coarse_zoom | 7.0 | 101.5 | 432.0 | 792.2 | 792.2 | 20/21 | 18 | 13 | 21 | 832.8 | 212.2 | 215.6 | 190 | 7 | 0.46 |
| stress / zoom / warm | 8.8 | 102.9 | 519.8 | 519.8 | 519.8 | 19/19 | 17 | 13 | 26 | 437.9 | 173.9 | 260.2 | 455 | 3 | 0.55 |
| stress / zoom / warm / nosync | 31.2 | 17.2 | 105.8 | 254.3 | 354.2 | 69/102 | 19 | 10 | 13 | 473.3 | 11.3 | 243.4 | 60 | 6 | 0.01 |
| stress / zoom / warm / coarse_zoom | 12.1 | 56.5 | 262.2 | 436.7 | 436.7 | 35/37 | 27 | 17 | 29 | 425.7 | 206.9 | 221.0 | 302 | 16 | 0.46 |
| stress / zoom / warm / minz | 9.4 | 75.4 | 451.5 | 1093.7 | 1093.7 | 23/24 | 21 | 15 | 27 | 1078.0 | 249.9 | 642.0 | 590 | 4 | 3.95 |
| stress / zoom / warm / minz,coarse_zoom | 13.0 | 54.8 | 262.9 | 451.1 | 451.1 | 41/41 | 32 | 14 | 33 | 463.9 | 208.8 | 243.7 | 358 | 12 | 0.21 |
| medium / zoom / cold / budget4 | 45.8 | 19.5 | 24.1 | 131.1 | 164.2 | 139/142 | 2 | 2 | 0 | 26.0 | 9.6 | 12.8 | 473 | 1 | 0.39 |
| medium / zoom / cold / budget8 | 37.3 | 24.6 | 29.6 | 99.7 | 168.6 | 114/116 | 2 | 2 | 0 | 31.9 | 14.1 | 17.2 | 718 | 2 | 0.39 |
| medium / zoom / cold / budget6,minz,camco,panco | 42.1 | 21.5 | 26.5 | 119.7 | 168.4 | 126/133 | 3 | 2 | 0 | 29.9 | 11.6 | 15.1 | 637 | 1 | 0.38 |
| medium / zoom / warm / budget4 | 50.9 | 16.7 | 20.8 | 129.4 | 166.1 | 78/169 | 2 | 2 | 0 | 23.7 | 5.8 | 12.5 | 0 | 111 | 0.05 |
| medium / zoom / warm / budget8 | 50.9 | 16.9 | 19.5 | 145.5 | 155.0 | 88/169 | 2 | 2 | 0 | 20.2 | 5.1 | 9.3 | 0 | 111 | 0.06 |
| medium / zoom / warm / budget6,minz,camco,panco | 50.6 | 16.9 | 19.7 | 128.8 | 155.6 | 85/168 | 3 | 2 | 0 | 29.4 | 5.8 | 13.5 | 0 | 109 | 0.06 |
| large / zoom / cold / budget4 | 32.1 | 28.1 | 72.6 | 169.0 | 169.0 | 84/84 | 33 | 7 | 30 | 56.5 | 18.6 | 56.5 | 157 | 1 | 1.02 |
| large / zoom / cold / budget8 | 35.5 | 26.7 | 48.5 | 172.9 | 172.9 | 96/96 | 24 | 3 | 7 | 51.8 | 29.1 | 81.0 | 283 | 2 | 1.24 |
| large / zoom / cold / budget6,minz,camco,panco | 44.2 | 22.4 | 32.7 | 179.4 | 193.2 | 118/122 | 6 | 2 | 3 | 43.8 | 20.5 | 55.2 | 252 | 0 | 1.29 |
| large / zoom / warm / budget4 | 50.9 | 16.8 | 22.2 | 159.0 | 161.0 | 83/167 | 3 | 3 | 1 | 51.8 | 4.0 | 11.6 | 0 | 111 | 0.09 |
| large / zoom / warm / budget8 | 34.8 | 17.2 | 65.0 | 76.2 | 162.9 | 63/107 | 28 | 4 | 27 | 68.7 | 17.4 | 19.9 | 71 | 79 | 0.34 |
| large / zoom / warm / budget6,minz,camco,panco | 36.4 | 17.1 | 58.8 | 61.6 | 160.3 | 64/112 | 30 | 1 | 28 | 53.0 | 13.4 | 18.5 | 56 | 80 | 0.31 |
| stress / zoom / cold / budget4 | 23.3 | 30.9 | 107.3 | 444.9 | 444.9 | 52/52 | 23 | 12 | 23 | 443.3 | 68.5 | 142.7 | 129 | 5 | 5.33 |
| stress / zoom / cold / budget8 | 22.4 | 36.2 | 134.8 | 328.8 | 328.8 | 50/50 | 26 | 18 | 31 | 146.4 | 71.2 | 144.1 | 201 | 7 | 5.36 |
| stress / zoom / cold / nosync,minz | 28.5 | 19.3 | 89.8 | 275.1 | 275.1 | 91/91 | 21 | 7 | 19 | 481.1 | 12.1 | 263.3 | 73 | 8 | 0.01 |
| stress / zoom / cold / budget6,minz,camco,panco | 26.7 | 31.3 | 91.5 | 318.7 | 318.7 | 66/67 | 32 | 7 | 31 | 105.3 | 31.9 | 67.8 | 196 | 9 | 5.17 |
| stress / zoom / warm / budget4 | 23.9 | 25.4 | 116.9 | 347.1 | 347.1 | 69/69 | 22 | 13 | 27 | 238.8 | 22.2 | 31.1 | 127 | 7 | 0.72 |
| stress / zoom / warm / budget8 | 19.7 | 33.1 | 199.5 | 348.7 | 348.7 | 57/57 | 28 | 14 | 25 | 229.4 | 23.4 | 26.2 | 168 | 8 | 0.78 |
| stress / zoom / warm / nosync,minz | 29.7 | 19.6 | 77.9 | 286.6 | 286.6 | 96/96 | 21 | 6 | 19 | 501.4 | 13.0 | 274.5 | 68 | 4 | 0.01 |
| stress / zoom / warm / budget6,minz,camco,panco | 23.9 | 32.7 | 97.2 | 354.5 | 354.5 | 69/70 | 33 | 8 | 28 | 244.0 | 26.9 | 62.7 | 163 | 7 | 0.77 |

| run | hit % | sync tiles | sync frames | worst sync ms | fallback fr | blurry fr | mag p50 | blank | idle sl | idle@input | idle max ms | inval | img exact % | placeholder |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| medium / zoom / cold | 2.8 | 826 | 31 | 49.8 | 109 | 78 | 4.87 | 0 | 18 | 7 | 14.8 | 96 | 74 | 0 |
| medium / zoom / cold / nosync | 1.7 | 61 | 3 | 16.8 | 165 | 110 | 2.71 | 1759 | 6 | 0 | 13.3 | 16 | 20 | 0 |
| medium / zoom / cold / coarse_zoom | -0.7 | 236 | 36 | 45.3 | 148 | 97 | 2.09 | 0 | 11 | 0 | 14.4 | 120 | 66 | 0 |
| medium / zoom / warm | 1.7 | 0 | 0 | 0.0 | 166 | 110 | 2.74 | 0 | 6 | 0 | 12.9 | 0 | 16 | 0 |
| medium / zoom / warm / nosync | 1.2 | 0 | 0 | 0.0 | 164 | 64 | 1.00 | 0 | 0 | 0 | – | 0 | 0 | 0 |
| medium / zoom / warm / coarse_zoom | 1.7 | 0 | 0 | 0.0 | 166 | 51 | 1.00 | 0 | 6 | 0 | 12.9 | 0 | 82 | 0 |
| large / zoom / cold | 1.2 | 534 | 17 | 258.5 | 94 | 77 | 8.17 | 0 | 19 | 3 | 28.8 | 112 | 71 | 0 |
| large / zoom / cold / nosync | 1.9 | 35 | 2 | 24.9 | 163 | 108 | 2.97 | 1509 | 11 | 0 | 15.6 | 24 | 76 | 0 |
| large / zoom / cold / coarse_zoom | 3.7 | 188 | 27 | 68.8 | 125 | 92 | 3.12 | 0 | 23 | 0 | 19.1 | 144 | 89 | 0 |
| large / zoom / warm | 2.1 | 0 | 0 | 0.0 | 167 | 112 | 2.96 | 0 | 14 | 0 | 14.9 | 0 | 76 | 0 |
| large / zoom / warm / nosync | 1.4 | 0 | 0 | 0.0 | 162 | 62 | 1.00 | 0 | 0 | 0 | – | 0 | 75 | 0 |
| large / zoom / warm / coarse_zoom | 2.9 | 0 | 0 | 0.0 | 168 | 60 | 1.00 | 0 | 15 | 0 | 14.8 | 0 | 100 | 0 |
| stress / zoom / cold | 11.9 | 195 | 5 | 328.0 | 4 | 1 | 1.09 | 0 | 10 | 5 | 22.4 | 40 | 90 | 0 |
| stress / zoom / cold / nosync | 2.6 | 60 | 2 | 231.4 | 94 | 9 | 1.00 | 3008 | 26 | 15 | 23.2 | 72 | 99 | 0 |
| stress / zoom / cold / coarse_zoom | -12.4 | 139 | 12 | 223.1 | 15 | 7 | 1.16 | 0 | 24 | 17 | 21.0 | 72 | 98 | 0 |
| stress / zoom / cold / minz | 1.2 | 193 | 5 | 284.0 | 5 | 2 | 1.23 | 0 | 10 | 4 | 21.8 | 32 | 91 | 0 |
| stress / zoom / cold / minz,coarse_zoom | -10.4 | 190 | 18 | 202.4 | 22 | 7 | 1.00 | 0 | 30 | 22 | 21.2 | 96 | 98 | 0 |
| stress / zoom / warm | 29.6 | 455 | 17 | 247.7 | 26 | 3 | 1.00 | 0 | 47 | 19 | 22.6 | 72 | 98 | 0 |
| stress / zoom / warm / nosync | 2.7 | 60 | 2 | 227.7 | 101 | 6 | 1.00 | 3038 | 34 | 23 | 21.8 | 48 | 100 | 0 |
| stress / zoom / warm / coarse_zoom | -8.3 | 302 | 28 | 207.8 | 38 | 16 | 1.09 | 0 | 32 | 17 | 22.4 | 32 | 100 | 0 |
| stress / zoom / warm / minz | 13.8 | 590 | 21 | 622.9 | 20 | 4 | 1.00 | 0 | 41 | 21 | 64.7 | 88 | 92 | 0 |
| stress / zoom / warm / minz,coarse_zoom | -11.5 | 358 | 34 | 234.6 | 41 | 12 | 1.05 | 0 | 41 | 26 | 23.1 | 56 | 100 | 0 |
| medium / zoom / cold / budget4 | 3.2 | 473 | 145 | 9.2 | 149 | 1 | 1.00 | 4693 | 18 | 0 | 17.1 | 144 | 77 | 339 |
| medium / zoom / cold / budget8 | 3.5 | 718 | 117 | 11.7 | 121 | 2 | 1.00 | 3075 | 15 | 0 | 14.7 | 176 | 82 | 187 |
| medium / zoom / cold / budget6,minz,camco,panco | 3.6 | 637 | 134 | 8.7 | 137 | 1 | 1.00 | 3903 | 16 | 0 | 16.2 | 152 | 87 | 153 |
| medium / zoom / warm / budget4 | 1.6 | 0 | 0 | 0.0 | 166 | 111 | 2.74 | 0 | 6 | 0 | 12.9 | 0 | 16 | 0 |
| medium / zoom / warm / budget8 | 1.6 | 0 | 0 | 0.0 | 166 | 111 | 2.72 | 0 | 6 | 0 | 13.0 | 0 | 16 | 0 |
| medium / zoom / warm / budget6,minz,camco,panco | 1.6 | 0 | 0 | 0.0 | 165 | 109 | 2.73 | 0 | 6 | 0 | 12.7 | 0 | 16 | 0 |
| large / zoom / cold / budget4 | 11.3 | 157 | 104 | 32.2 | 104 | 1 | 1.00 | 3819 | 85 | 42 | 35.6 | 352 | 62 | 2379 |
| large / zoom / cold / budget8 | 7.9 | 283 | 114 | 40.3 | 115 | 2 | 1.00 | 3942 | 35 | 0 | 30.1 | 400 | 58 | 2503 |
| large / zoom / cold / budget6,minz,camco,panco | 6.7 | 252 | 142 | 19.8 | 144 | 0 | 1.00 | 5222 | 42 | 0 | 33.2 | 376 | 57 | 2733 |
| large / zoom / warm / budget4 | 2.6 | 0 | 0 | 0.0 | 166 | 111 | 3.03 | 0 | 16 | 0 | 14.8 | 0 | 76 | 0 |
| large / zoom / warm / budget8 | 6.9 | 71 | 30 | 15.6 | 113 | 79 | 5.73 | 893 | 70 | 38 | 19.7 | 104 | 88 | 0 |
| large / zoom / warm / budget6,minz,camco,panco | 8.5 | 56 | 30 | 11.9 | 118 | 80 | 4.71 | 959 | 80 | 47 | 22.4 | 112 | 89 | 0 |
| stress / zoom / cold / budget4 | 16.7 | 129 | 72 | 53.7 | 74 | 5 | 1.00 | 2368 | 62 | 13 | 52.7 | 232 | 73 | 6869 |
| stress / zoom / cold / budget8 | 17.1 | 201 | 69 | 62.0 | 71 | 7 | 1.00 | 1958 | 60 | 18 | 51.9 | 240 | 78 | 4527 |
| stress / zoom / cold / nosync,minz | 2.6 | 73 | 3 | 248.0 | 92 | 8 | 1.00 | 2939 | 36 | 23 | 24.9 | 96 | 99 | 0 |
| stress / zoom / cold / budget6,minz,camco,panco | 13.5 | 196 | 86 | 50.8 | 85 | 9 | 1.00 | 2643 | 76 | 29 | 74.8 | 296 | 78 | 4328 |
| stress / zoom / warm / budget4 | 8.5 | 127 | 65 | 23.0 | 77 | 7 | 1.00 | 1916 | 48 | 19 | 19.7 | 80 | 100 | 0 |
| stress / zoom / warm / budget8 | 9.0 | 168 | 56 | 18.2 | 63 | 8 | 1.00 | 1557 | 43 | 17 | 23.4 | 96 | 99 | 0 |
| stress / zoom / warm / nosync,minz | 3.2 | 68 | 2 | 258.3 | 96 | 4 | 1.00 | 2905 | 41 | 30 | 22.3 | 40 | 100 | 0 |
| stress / zoom / warm / budget6,minz,camco,panco | 9.5 | 163 | 66 | 25.9 | 77 | 7 | 1.00 | 1860 | 54 | 28 | 28.7 | 136 | 99 | 0 |

Pan variants (settled sweep, input rate, linked views, `camera_coalesce`, `pan_coalesce`, `budget`):

| run | fps | act p50 | p95 | p99 | max | late | >2x | >4x | gap>33 | gap max | paint p95 | paint max | sync tiles | blurry fr | refine s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| medium / pan_zoomed / settled | 58.4 | 16.6 | 28.0 | 42.8 | 48.0 | 94/294 | 8 | 0 | 0 | 32.8 | 6.2 | 11.4 | 0 | 0 | 0.01 |
| medium / pan_zoomed / settled / 3000px/s | 57.6 | 16.5 | 28.1 | 43.8 | 52.7 | 99/290 | 8 | 0 | 0 | 28.8 | 6.3 | 7.9 | 6 | 0 | 0.02 |
| medium / pan_zoomed / settled / 6000px/s | 55.6 | 16.5 | 31.4 | 45.5 | 66.3 | 97/280 | 11 | 0 | 7 | 43.9 | 6.3 | 15.6 | 77 | 0 | 0.01 |
| stress / pan_zoomed / settled | 43.8 | 18.4 | 50.0 | 61.9 | 67.0 | 207/220 | 29 | 1 | 10 | 72.6 | 7.8 | 52.1 | 2 | 0 | 0.01 |
| stress / pan_zoomed / settled / 3000px/s | 31.5 | 22.2 | 65.7 | 76.2 | 100.5 | 155/158 | 49 | 6 | 32 | 98.6 | 18.9 | 52.2 | 145 | 0 | 0.02 |
| stress / pan_zoomed / settled / 6000px/s | 21.4 | 32.7 | 107.3 | 123.7 | 145.7 | 105/107 | 52 | 27 | 37 | 143.1 | 44.9 | 53.2 | 624 | 0 | 0.01 |
| hires / pan_zoomed / settled | 58.6 | 16.5 | 24.2 | 44.9 | 65.0 | 96/295 | 4 | 0 | 0 | 29.1 | 6.1 | 12.3 | 0 | 0 | 0.01 |
| hires / pan_zoomed / settled / 3000px/s | 57.6 | 16.6 | 25.1 | 42.6 | 60.9 | 100/290 | 7 | 0 | 1 | 43.4 | 6.2 | 14.0 | 32 | 0 | 0.01 |
| hires / pan_zoomed / settled / 6000px/s | 55.4 | 16.6 | 27.7 | 61.5 | 87.5 | 100/279 | 7 | 2 | 3 | 71.0 | 5.8 | 55.4 | 104 | 0 | 0.01 |
| gifonly / pan_zoomed / warm / defer_anim | 58.2 | 15.8 | 32.5 | 45.0 | 50.2 | 104/293 | 11 | 0 | 1 | 37.9 | 9.6 | 22.3 | 165 | 0 | 0.01 |
| mixed / pan_zoomed / warm / defer_anim | 53.3 | 15.9 | 36.1 | 49.0 | 52.3 | 114/268 | 17 | 0 | 8 | 45.7 | 14.6 | 33.9 | 176 | 0 | 0.02 |
| medium / pan_zoomed / warm / 500Hz / panco | 59.8 | 16.7 | 17.4 | 24.7 | 26.6 | 93/301 | 0 | 0 | 0 | 23.7 | 6.2 | 8.6 | 0 | 0 | 0.02 |
| medium / pan_zoomed / warm / 1000Hz / panco | 59.8 | 16.6 | 17.5 | 25.4 | 30.3 | 81/301 | 0 | 0 | 0 | 21.4 | 5.8 | 10.0 | 0 | 0 | 0.01 |
| medium / pan_zoomed / warm / 4v-linked / panco | 29.9 | 34.9 | 38.4 | 42.7 | 45.0 | 150/150 | 105 | 0 | 0 | 33.3 | 1.1 | 1.3 | 0 | 0 | 0.01 |
| medium / pan_zoomed / warm / 4v-linked / camco | 35.0 | 26.6 | 37.0 | 66.5 | 74.0 | 176/176 | 18 | 1 | 5 | 57.9 | 1.3 | 1.9 | 0 | 0 | 0.02 |
| medium / pan_zoomed / warm / 4v-linked / panco,camco | 40.0 | 24.3 | 30.4 | 34.2 | 48.4 | 201/202 | 3 | 0 | 2 | 43.4 | 1.3 | 2.6 | 0 | 0 | 0.01 |
| medium / pan_zoomed / settled / camco | 58.2 | 16.6 | 27.0 | 46.5 | 49.1 | 107/295 | 9 | 0 | 3 | 37.0 | 6.0 | 13.5 | 5 | 0 | 0.02 |
| stress / pan_zoomed / settled / 3000px/s / budget4 | 28.1 | 31.6 | 68.3 | 73.2 | 73.8 | 141/141 | 65 | 13 | 31 | 63.6 | 16.5 | 52.0 | 107 | 38 | 0.02 |
| stress / pan_zoomed / settled / 6000px/s / budget4 | 19.6 | 50.5 | 73.0 | 98.8 | 98.8 | 98/98 | 91 | 17 | 65 | 76.4 | 16.7 | 60.1 | 208 | 81 | 0.02 |
| stress / pan_zoomed / settled / 3000px/s / budget8 | 26.7 | 37.4 | 63.2 | 72.4 | 73.4 | 134/134 | 82 | 5 | 24 | 80.3 | 21.1 | 58.6 | 253 | 55 | 0.02 |
| stress / pan_zoomed / settled / 6000px/s / budget8 | 18.4 | 56.3 | 76.8 | 123.9 | 123.9 | 92/92 | 83 | 11 | 63 | 92.3 | 19.7 | 61.2 | 332 | 57 | 0.02 |
| stress / pan_zoomed / settled / 3000px/s / budget6,minz,camco,panco | 38.4 | 20.0 | 49.7 | 60.1 | 74.6 | 160/196 | 44 | 1 | 31 | 77.0 | 14.7 | 63.9 | 84 | 12 | 0.02 |
| stress / pan_zoomed / settled / 6000px/s / budget6,minz,camco,panco | 35.0 | 27.7 | 33.9 | 63.8 | 79.4 | 175/177 | 12 | 1 | 4 | 77.5 | 19.2 | 64.4 | 543 | 155 | 0.01 |
| medium / pan_zoomed / warm / 4v-linked / budget6,minz,camco,panco | 39.0 | 24.6 | 30.9 | 37.4 | 37.4 | 196/197 | 4 | 0 | 2 | 34.8 | 1.4 | 2.6 | 0 | 0 | 0.01 |
| medium / pan_zoomed / warm / 500Hz / budget6,minz,camco,panco | 60.0 | 16.6 | 18.4 | 23.3 | 26.9 | 110/303 | 0 | 0 | 0 | 25.9 | 6.1 | 7.1 | 0 | 0 | 0.02 |
| medium / pan_zoomed / warm / 1000Hz / budget6,minz,camco,panco | 59.8 | 16.7 | 17.9 | 27.1 | 30.6 | 102/301 | 0 | 0 | 0 | 26.6 | 5.5 | 8.9 | 0 | 0 | 0.01 |
| mixed / pan_zoomed / warm / budget6,minz,camco,panco,defer_anim | 50.5 | 16.7 | 30.6 | 37.7 | 51.9 | 131/277 | 7 | 0 | 7 | 37.9 | 11.4 | 19.3 | 167 | 0 | 0.04 |

GIF boards (`defer_anim`):

| run | fps | act p50 | p95 | p99 | max | late | >2x | >4x | gap>33 | gap max | paint p95 | paint max | sync tiles | blurry fr | refine s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gifonly / pan_zoomed / warm | 50.9 | 18.2 | 33.1 | 41.7 | 47.3 | 146/256 | 12 | 0 | 1 | 34.3 | 11.1 | 15.1 | 416 | 0 | 0.01 |
| gifonly / pan_zoomed / warm / defer_anim | 58.2 | 15.8 | 32.5 | 45.0 | 50.2 | 104/293 | 11 | 0 | 1 | 37.9 | 9.6 | 22.3 | 165 | 0 | 0.01 |
| gifonly / zoom / warm | 26.4 | 34.4 | 85.5 | 161.2 | 161.2 | 70/77 | 42 | 8 | 30 | 93.0 | 59.6 | 64.2 | 1643 | 6 | 0.25 |
| gifonly / zoom / warm / defer_anim | 32.1 | 29.3 | 62.4 | 262.8 | 262.8 | 77/93 | 37 | 4 | 27 | 83.9 | 43.3 | 66.5 | 1542 | 19 | 0.46 |
| mixed / pan_zoomed / warm | 42.2 | 22.0 | 38.1 | 47.2 | 66.4 | 152/212 | 33 | 0 | 4 | 54.7 | 16.2 | 23.7 | 411 | 0 | 0.04 |
| mixed / pan_zoomed / warm / defer_anim | 53.3 | 15.9 | 36.1 | 49.0 | 52.3 | 114/268 | 17 | 0 | 8 | 45.7 | 14.6 | 33.9 | 176 | 0 | 0.02 |

Overview prototype (matrices E and F; **refine columns valid for F rows only**; `OVN` =
`nosync_zoom,overview,minzoom_cache`, `NSR` = that + `camera_coalesce,pan_coalesce`):

| run | fps | act p50 | p95 | p99 | max | late | >2x | >4x | gap>33 | gap max | paint p95 | paint max | sync tiles | blurry fr | refine s |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| medium / zoom / settled / nosync,overview,minz | 50.3 | 16.9 | 20.7 | 107.5 | 174.3 | 87/167 | 2 | 2 | 2 | 47.2 | 6.6 | 38.0 | 47 | 95 | – |
| medium / zoom / settled / budget6,overview,minz | 46.4 | 17.6 | 25.2 | 123.8 | 158.7 | 108/148 | 2 | 2 | 1 | 34.2 | 12.6 | 20.7 | 282 | 69 | – |
| medium / zoom / settled / budget6,overview,minz,camco,panco | 46.7 | 17.6 | 24.7 | 166.9 | 169.2 | 96/149 | 3 | 2 | 2 | 37.6 | 12.9 | 22.9 | 295 | 71 | – |
| medium / zoom / settled | 36.7 | 17.3 | 65.8 | 87.3 | 178.4 | 79/118 | 25 | 4 | 24 | 74.5 | 46.4 | 65.4 | 912 | 78 | 0.15 |
| medium / zoom / warm / nosync,overview,minz | 50.3 | 16.9 | 19.4 | 131.4 | 402.6 | 90/170 | 2 | 2 | 0 | 22.4 | 4.1 | 7.6 | 0 | 56 | – |
| medium / zoom / warm / budget6,overview,minz | 50.9 | 16.8 | 20.1 | 130.0 | 161.9 | 82/170 | 2 | 2 | 0 | 22.4 | 5.4 | 10.1 | 0 | 93 | – |
| medium / zoom / warm / budget6,overview,minz,camco,panco | 50.9 | 16.7 | 19.7 | 30.6 | 158.4 | 78/169 | 1 | 1 | 0 | 24.4 | 5.4 | 14.8 | 0 | 94 | – |
| large / zoom / settled / nosync,overview,minz | 50.6 | 16.7 | 21.1 | 157.9 | 164.0 | 81/168 | 2 | 2 | 2 | 59.0 | 7.6 | 42.7 | 40 | 93 | – |
| large / zoom / settled / budget6,overview,minz | 33.0 | 25.1 | 59.1 | 199.5 | 199.5 | 83/85 | 28 | 3 | 26 | 70.6 | 15.8 | 18.2 | 165 | 7 | 2.66 |
| large / zoom / settled / budget6,overview,minz,camco,panco | 44.8 | 17.1 | 58.7 | 89.7 | 151.6 | 61/111 | 27 | 2 | 28 | 64.5 | 14.2 | 17.9 | 80 | 73 | 4.86 |
| large / zoom / settled | 33.3 | 17.0 | 104.3 | 282.1 | 458.1 | 58/102 | 14 | 9 | 15 | 245.2 | 67.8 | 209.3 | 504 | 77 | 0.40 |
| large / zoom / warm / nosync,overview,minz | 50.3 | 16.9 | 18.7 | 169.1 | 405.4 | 86/170 | 2 | 2 | 0 | 20.9 | 6.2 | 10.4 | 0 | 56 | – |
| large / zoom / warm / budget6,overview,minz | 51.2 | 16.8 | 18.3 | 157.8 | 171.7 | 83/168 | 3 | 2 | 1 | 57.2 | 6.3 | 12.3 | 0 | 95 | 0.09 |
| large / zoom / warm / budget6,overview,minz,camco,panco | 51.5 | 16.8 | 20.0 | 153.2 | 158.7 | 80/170 | 2 | 2 | 0 | 23.6 | 6.4 | 15.2 | 0 | 95 | 0.08 |
| stress / zoom / settled / nosync,overview,minz | 47.6 | 17.0 | 20.6 | 200.2 | 269.6 | 90/156 | 3 | 3 | 6 | 68.4 | 10.1 | 60.4 | 50 | 89 | – |
| stress / zoom / settled / budget6,overview,minz | 59.7 | 17.3 | 97.5 | 276.1 | 276.1 | 59/96 | 20 | 11 | 24 | 201.3 | 24.2 | 75.7 | 143 | 69 | – |
| stress / zoom / settled / budget6,overview,minz,camco,panco | 59.4 | 17.0 | 79.5 | 168.6 | 168.6 | 53/99 | 22 | 9 | 25 | 144.2 | 23.7 | 53.5 | 146 | 71 | 17.10 |
| stress / zoom / settled | 3.3 | 247.5 | 578.9 | 578.9 | 578.9 | 7/7 | 5 | 5 | 12 | 1229.0 | 295.9 | 295.9 | 218 | 2 | 0.20 |
| stress / zoom / warm / nosync,overview,minz | 47.9 | 17.5 | 20.2 | 309.1 | 394.5 | 130/158 | 3 | 2 | 1 | 62.0 | 7.7 | 54.0 | 0 | 50 | – |
| stress / zoom / warm / budget6,overview,minz | 48.2 | 16.9 | 27.6 | 167.1 | 299.4 | 86/156 | 3 | 2 | 2 | 62.0 | 9.4 | 53.1 | 0 | 90 | – |
| stress / zoom / warm / budget6,overview,minz,camco,panco | 33.3 | 17.8 | 66.7 | 250.3 | 334.5 | 65/100 | 25 | 5 | 9 | 234.3 | 17.1 | 55.5 | 50 | 22 | 0.75 |
| stress / pan_zoomed / settled / 3000px/s / budget6,overview,minz | 30.7 | 24.1 | 63.1 | 80.0 | 134.5 | 148/154 | 53 | 6 | 39 | 93.0 | 16.0 | 55.3 | 139 | 25 | – |
| stress / pan_zoomed / settled / 3000px/s / budget6,overview,minz,camco,panco | 39.0 | 19.8 | 53.0 | 72.5 | 82.2 | 131/198 | 39 | 3 | 19 | 76.8 | 15.1 | 66.5 | 143 | 32 | – |
| stress / pan_zoomed / settled / 6000px/s / budget6,overview,minz | 18.0 | 55.4 | 86.2 | 112.0 | 112.0 | 90/90 | 85 | 14 | 66 | 106.6 | 16.5 | 17.1 | 266 | 59 | – |
| stress / pan_zoomed / settled / 6000px/s / budget6,overview,minz,camco,panco | 25.5 | 36.6 | 61.0 | 85.0 | 93.4 | 120/130 | 76 | 4 | 61 | 101.6 | 16.1 | 18.1 | 315 | 61 | – |
| medium / jump / settled | 2.7 | 47.1 | 833.3 | 833.3 | 833.3 | 17/17 | 10 | 7 | 8 | 47.8 | 46.2 | 46.2 | 346 | 4 | 0.01 |
| medium / jump / settled / budget6,overview,minz,camco,panco | 8.0 | 16.2 | 647.8 | 807.6 | 807.6 | 24/50 | 14 | 12 | 5 | 36.0 | 21.3 | 22.4 | 184 | 41 | – |
| medium / jump_animated / settled | 14.6 | 16.6 | 42.5 | 727.6 | 733.9 | 52/112 | 15 | 2 | 6 | 59.3 | 14.8 | 34.9 | 301 | 1 | 0.01 |
| medium / jump_animated / settled / budget6,overview,minz,camco,panco | 16.3 | 16.5 | 35.9 | 45.6 | 149.2 | 52/125 | 7 | 1 | 2 | 35.4 | 11.0 | 20.2 | 181 | 54 | – |
| stress / jump / settled | 3.9 | 83.8 | 672.2 | 727.5 | 727.5 | 18/22 | 17 | 16 | 23 | 122.2 | 91.5 | 99.0 | 568 | 6 | 0.01 |
| stress / jump / settled / budget6,overview,minz,camco,panco | 11.1 | 43.3 | 183.5 | 187.1 | 187.1 | 29/37 | 28 | 9 | 30 | 73.9 | 31.5 | 34.4 | 196 | 60 | – |
| stress / jump_animated / settled | 7.0 | 66.2 | 122.9 | 124.1 | 124.1 | 35/35 | 27 | 17 | 49 | 163.6 | 62.7 | 71.0 | 990 | 4 | 0.01 |
| stress / jump_animated / settled / budget6,overview,minz,camco,panco | 14.0 | 44.9 | 81.7 | 108.5 | 108.5 | 50/52 | 35 | 5 | 59 | 105.4 | 29.4 | 56.2 | 294 | 86 | – |
| stress / zoom_trackpad / settled | 3.0 | 266.8 | 963.1 | 963.1 | 963.1 | 7/8 | 6 | 6 | 13 | 1413.1 | 298.8 | 298.8 | 309 | 2 | 1.08 |
| stress / zoom_trackpad / settled / budget6,overview,minz,camco,panco | 35.5 | 62.7 | 132.6 | 201.9 | 201.9 | 63/63 | 63 | 23 | 65 | 174.3 | 24.6 | 79.3 | 224 | 4 | – |
| medium / zoom / settled | 36.4 | 17.2 | 76.2 | 94.5 | 177.7 | 78/119 | 23 | 8 | 21 | 74.1 | 39.7 | 50.6 | 812 | 78 | 0.09 |
| medium / zoom / settled / nosync,overview,minz | 50.9 | 16.7 | 20.6 | 121.5 | 177.6 | 78/168 | 2 | 2 | 2 | 44.3 | 6.8 | 35.8 | 47 | 94 | 0.05 |
| medium / zoom / settled / nosync,overview,minz,camco,panco | 50.6 | 16.8 | 21.9 | 28.1 | 185.5 | 70/169 | 1 | 1 | 2 | 42.8 | 10.1 | 33.9 | 47 | 95 | 0.07 |
| large / zoom / settled | 31.8 | 17.1 | 96.6 | 183.2 | 416.1 | 63/100 | 15 | 8 | 16 | 341.2 | 73.6 | 324.1 | 567 | 77 | 1.02 |
| large / zoom / settled / nosync,overview,minz | 50.6 | 16.8 | 20.3 | 135.0 | 162.3 | 81/168 | 3 | 2 | 2 | 50.2 | 6.9 | 37.7 | 41 | 94 | 0.08 |
| large / zoom / settled / nosync,overview,minz,camco,panco | 50.3 | 16.8 | 21.2 | 159.2 | 170.5 | 72/169 | 2 | 2 | 2 | 57.1 | 10.2 | 41.4 | 49 | 95 | 0.09 |
| stress / zoom / settled | 6.1 | 243.4 | 458.8 | 458.8 | 458.8 | 4/5 | 3 | 3 | 7 | 1681.7 | 246.3 | 343.3 | 184 | 1 | 1.56 |
| stress / zoom / settled / nosync,overview,minz | 47.3 | 17.1 | 22.0 | 194.2 | 280.5 | 95/153 | 3 | 2 | 6 | 65.9 | 10.2 | 53.0 | 50 | 88 | 0.22 |
| stress / zoom / settled / nosync,overview,minz,camco,panco | 47.3 | 17.2 | 22.0 | 194.5 | 292.0 | 101/153 | 3 | 3 | 6 | 69.2 | 10.2 | 59.8 | 50 | 87 | 0.23 |
| medium / jump / settled | 2.9 | 48.6 | 832.7 | 832.7 | 832.7 | 18/18 | 13 | 8 | 9 | 49.2 | 46.7 | 46.7 | 346 | 4 | 0.01 |
| medium / jump_animated / settled | 14.4 | 16.4 | 46.7 | 64.7 | 728.5 | 42/110 | 14 | 1 | 7 | 61.8 | 18.0 | 37.6 | 292 | 0 | 0.01 |
| medium / jump / settled / nosync,overview,minz,camco,panco | 3.8 | 35.1 | 770.4 | 807.5 | 807.5 | 20/24 | 15 | 10 | 8 | 53.8 | 38.4 | 39.0 | 316 | 13 | 0.01 |
| medium / jump_animated / settled / nosync,overview,minz,camco,panco | 15.4 | 16.4 | 42.4 | 55.3 | 729.9 | 45/118 | 13 | 1 | 9 | 51.2 | 19.1 | 29.3 | 290 | 26 | 0.01 |
| stress / jump / settled | 3.9 | 84.7 | 667.4 | 731.5 | 731.5 | 19/22 | 18 | 16 | 23 | 123.9 | 78.3 | 103.4 | 570 | 6 | 0.01 |
| stress / jump_animated / settled | 6.8 | 73.5 | 123.3 | 144.3 | 144.3 | 35/35 | 27 | 19 | 45 | 160.8 | 64.8 | 110.4 | 1026 | 3 | 0.02 |
| stress / jump / settled / nosync,overview,minz,camco,panco | 5.2 | 106.1 | 237.0 | 563.3 | 563.3 | 17/24 | 17 | 16 | 28 | 109.7 | 71.4 | 79.1 | 522 | 14 | 0.01 |
| stress / jump_animated / settled / nosync,overview,minz,camco,panco | 7.6 | 67.3 | 120.8 | 171.7 | 171.7 | 38/38 | 27 | 22 | 52 | 157.4 | 56.4 | 76.7 | 803 | 16 | 0.01 |
| stress / pan_zoomed / settled / 3000px/s / nosync,overview,minz,camco,panco | 35.8 | 18.0 | 59.8 | 81.5 | 115.2 | 111/182 | 49 | 5 | 32 | 82.1 | 18.1 | 70.8 | 121 | 0 | 0.01 |
| stress / pan_zoomed / settled / 6000px/s / nosync,overview,minz,camco,panco | 22.6 | 29.6 | 109.3 | 153.7 | 161.9 | 93/114 | 52 | 26 | 41 | 164.2 | 42.9 | 49.0 | 635 | 1 | 0.01 |
| stress / zoom_trackpad / settled / nosync,overview,minz,camco,panco | 34.9 | 17.0 | 86.8 | 150.9 | 214.8 | 85/157 | 37 | 10 | 31 | 135.3 | 11.2 | 112.5 | 68 | 96 | 0.59 |
| medium / zoom_trackpad / settled | 35.3 | 17.1 | 85.0 | 99.5 | 180.9 | 99/165 | 33 | 18 | 33 | 79.9 | 41.6 | 53.2 | 718 | 119 | 0.15 |
| medium / zoom_trackpad / settled / nosync,overview,minz,camco,panco | 39.1 | 17.3 | 46.3 | 83.7 | 180.8 | 127/183 | 48 | 2 | 3 | 51.5 | 8.4 | 31.9 | 63 | 94 | 0.10 |
| hires / zoom / settled | 35.8 | 17.0 | 114.9 | 153.0 | 163.9 | 65/113 | 17 | 9 | 14 | 132.8 | 72.2 | 116.3 | 858 | 79 | 0.32 |
| hires / zoom / settled / nosync,overview,minz,camco,panco | 50.9 | 16.7 | 18.0 | 123.2 | 153.0 | 36/169 | 2 | 2 | 0 | 32.7 | 3.2 | 24.4 | 36 | 95 | 0.05 |
| mixed / zoom / settled | 15.8 | 58.1 | 141.2 | 158.0 | 158.0 | 45/46 | 40 | 15 | 41 | 135.5 | 95.4 | 127.2 | 1148 | 2 | 0.40 |
| mixed / zoom / settled / nosync,overview,minz,camco,panco | 48.2 | 16.7 | 23.9 | 84.7 | 362.0 | 73/168 | 3 | 2 | 4 | 92.9 | 14.2 | 73.9 | 107 | 84 | 0.08 |
| medium / pan_zoomed / warm / 4v-linked / nosync,overview,minz,camco,panco | 41.8 | 23.1 | 28.3 | 31.6 | 39.4 | 209/211 | 1 | 0 | 0 | 29.5 | 1.1 | 1.9 | 0 | 0 | 0.01 |

| run | hit % | sync tiles | sync frames | worst sync ms | fallback fr | blurry fr | mag p50 | blank | idle sl | idle@input | idle max ms | inval | img exact % | placeholder |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| medium / zoom / settled / nosync,overview,minz | 1.6 | 47 | 3 | 13.6 | 162 | 95 | 1.52 | 1604 | 5 | 0 | 13.0 | 16 | 31 | 0 |
| medium / zoom / settled / budget6,overview,minz | 2.6 | 282 | 65 | 8.0 | 151 | 69 | 1.06 | 1472 | 16 | 0 | 14.5 | 64 | 85 | 0 |
| medium / zoom / settled / budget6,overview,minz,camco,panco | 2.7 | 295 | 66 | 9.4 | 152 | 71 | 1.06 | 1461 | 17 | 0 | 14.0 | 56 | 97 | 0 |
| medium / zoom / settled | 2.2 | 912 | 33 | 56.1 | 111 | 78 | 4.66 | 0 | 11 | 0 | 13.5 | 96 | 72 | 0 |
| medium / zoom / warm / nosync,overview,minz | 1.2 | 0 | 0 | 0.0 | 164 | 56 | 1.00 | 0 | 0 | 0 | – | 0 | 0 | 0 |
| medium / zoom / warm / budget6,overview,minz | 1.7 | 0 | 0 | 0.0 | 166 | 93 | 1.49 | 0 | 7 | 0 | 13.2 | 0 | 29 | 0 |
| medium / zoom / warm / budget6,overview,minz,camco,panco | 1.6 | 0 | 0 | 0.0 | 166 | 94 | 1.47 | 0 | 7 | 0 | 13.2 | 0 | 100 | 0 |
| large / zoom / settled / nosync,overview,minz | 1.4 | 40 | 3 | 33.0 | 164 | 93 | 1.48 | 1010 | 12 | 0 | 15.9 | 24 | 76 | 0 |
| large / zoom / settled / budget6,overview,minz | 22.6 | 165 | 95 | 13.7 | 102 | 7 | 1.00 | 2343 | 85 | 42 | 18.3 | 176 | 92 | 0 |
| large / zoom / settled / budget6,overview,minz,camco,panco | 25.7 | 80 | 53 | 11.2 | 135 | 73 | 1.48 | 866 | 82 | 40 | 23.6 | 256 | 91 | 0 |
| large / zoom / settled | 5.8 | 504 | 17 | 189.7 | 102 | 77 | 6.41 | 0 | 30 | 1 | 35.0 | 112 | 74 | 0 |
| large / zoom / warm / nosync,overview,minz | 1.4 | 0 | 0 | 0.0 | 164 | 56 | 1.00 | 0 | 2 | 0 | 15.0 | 0 | 75 | 0 |
| large / zoom / warm / budget6,overview,minz | 3.0 | 0 | 0 | 0.0 | 167 | 95 | 1.55 | 0 | 17 | 0 | 14.5 | 0 | 76 | 0 |
| large / zoom / warm / budget6,overview,minz,camco,panco | 2.8 | 0 | 0 | 0.0 | 168 | 95 | 1.46 | 0 | 16 | 0 | 14.1 | 0 | 76 | 0 |
| stress / zoom / settled / nosync,overview,minz | 1.7 | 50 | 3 | 22.9 | 153 | 89 | 1.51 | 953 | 7 | 0 | 13.4 | 24 | 100 | 0 |
| stress / zoom / settled / budget6,overview,minz | 48.2 | 143 | 93 | 18.6 | 164 | 69 | 1.00 | 816 | 110 | 27 | 22.2 | 576 | 97 | 0 |
| stress / zoom / settled / budget6,overview,minz,camco,panco | 46.8 | 146 | 92 | 17.6 | 165 | 71 | 1.00 | 892 | 110 | 28 | 27.2 | 552 | 97 | 0 |
| stress / zoom / settled | 10.7 | 218 | 6 | 283.7 | 5 | 2 | 1.15 | 0 | 13 | 7 | 22.0 | 48 | 93 | 0 |
| stress / zoom / warm / nosync,overview,minz | 1.3 | 0 | 0 | 0.0 | 156 | 50 | 1.00 | 0 | 1 | 0 | 3.3 | 0 | 100 | 0 |
| stress / zoom / warm / budget6,overview,minz | 2.2 | 0 | 0 | 0.0 | 157 | 90 | 1.50 | 0 | 9 | 0 | 13.8 | 0 | 100 | 0 |
| stress / zoom / warm / budget6,overview,minz,camco,panco | 9.1 | 50 | 22 | 10.5 | 108 | 22 | 1.00 | 319 | 65 | 34 | 23.7 | 64 | 100 | 0 |
| stress / pan_zoomed / settled / 3000px/s / budget6,overview,minz | 95.4 | 139 | 43 | 8.7 | 25 | 25 | 9.56 | 131 | 91 | 91 | 15.8 | 496 | 77 | 0 |
| stress / pan_zoomed / settled / 3000px/s / budget6,overview,minz,camco,panco | 96.8 | 143 | 50 | 9.0 | 32 | 32 | 9.56 | 94 | 98 | 98 | 14.6 | 560 | 78 | 0 |
| stress / pan_zoomed / settled / 6000px/s / budget6,overview,minz | 64.7 | 266 | 79 | 8.0 | 59 | 59 | 9.56 | 885 | 96 | 96 | 14.6 | 688 | 72 | 0 |
| stress / pan_zoomed / settled / 6000px/s / budget6,overview,minz,camco,panco | 75.3 | 315 | 98 | 9.0 | 61 | 61 | 9.56 | 841 | 109 | 109 | 14.4 | 752 | 74 | 0 |
| medium / jump / settled | 37.7 | 346 | 16 | 38.1 | 4 | 4 | 5.37 | 0 | 22 | 10 | 14.0 | 232 | 68 | 0 |
| medium / jump / settled / budget6,overview,minz,camco,panco | 58.2 | 184 | 43 | 7.7 | 41 | 41 | 2.03 | 424 | 49 | 4 | 14.1 | 456 | 76 | 0 |
| medium / jump_animated / settled | 93.2 | 301 | 35 | 27.2 | 1 | 1 | 5.37 | 0 | 54 | 14 | 13.8 | 360 | 69 | 0 |
| medium / jump_animated / settled / budget6,overview,minz,camco,panco | 89.1 | 181 | 45 | 7.4 | 54 | 54 | 2.03 | 170 | 67 | 9 | 14.6 | 456 | 74 | 0 |
| stress / jump / settled | 24.0 | 568 | 18 | 61.3 | 6 | 6 | 27.19 | 0 | 66 | 8 | 16.1 | 464 | 67 | 0 |
| stress / jump / settled / budget6,overview,minz,camco,panco | 48.2 | 196 | 57 | 8.1 | 60 | 60 | 9.56 | 985 | 124 | 7 | 14.9 | 688 | 77 | 0 |
| stress / jump_animated / settled | 48.9 | 990 | 39 | 53.1 | 4 | 4 | 27.19 | 0 | 93 | 17 | 15.1 | 592 | 49 | 0 |
| stress / jump_animated / settled / budget6,overview,minz,camco,panco | 52.8 | 294 | 86 | 8.9 | 86 | 86 | 9.56 | 1314 | 148 | 20 | 15.4 | 976 | 65 | 0 |
| stress / zoom_trackpad / settled | 9.7 | 309 | 9 | 272.2 | 5 | 2 | 1.07 | 0 | 18 | 12 | 21.6 | 56 | 93 | 0 |
| stress / zoom_trackpad / settled / budget6,overview,minz,camco,panco | 63.7 | 224 | 132 | 22.5 | 130 | 4 | 1.00 | 1521 | 195 | 106 | 24.4 | 696 | 97 | 0 |
| medium / zoom / settled | 3.9 | 812 | 31 | 41.1 | 111 | 78 | 4.66 | 0 | 20 | 8 | 17.4 | 88 | 75 | 0 |
| medium / zoom / settled / nosync,overview,minz | 1.6 | 47 | 3 | 13.1 | 164 | 94 | 1.47 | 1570 | 5 | 0 | 12.9 | 16 | 31 | 0 |
| medium / zoom / settled / nosync,overview,minz,camco,panco | 1.6 | 47 | 3 | 13.0 | 163 | 95 | 1.50 | 1526 | 5 | 0 | 13.4 | 16 | 78 | 0 |
| large / zoom / settled | 2.2 | 567 | 19 | 310.0 | 97 | 77 | 7.77 | 0 | 16 | 0 | 17.0 | 128 | 65 | 0 |
| large / zoom / settled / nosync,overview,minz | 1.3 | 41 | 3 | 25.3 | 164 | 94 | 1.47 | 1304 | 12 | 0 | 15.0 | 24 | 76 | 0 |
| large / zoom / settled / nosync,overview,minz,camco,panco | 0.9 | 49 | 2 | 29.4 | 164 | 95 | 1.49 | 1214 | 11 | 0 | 15.6 | 16 | 76 | 0 |
| stress / zoom / settled | 19.7 | 184 | 5 | 329.2 | 14 | 1 | 1.00 | 0 | 30 | 4 | 22.6 | 40 | 95 | 0 |
| stress / zoom / settled / nosync,overview,minz | 1.6 | 50 | 3 | 22.6 | 152 | 88 | 1.50 | 871 | 7 | 0 | 13.3 | 24 | 100 | 0 |
| stress / zoom / settled / nosync,overview,minz,camco,panco | 1.7 | 50 | 3 | 23.3 | 152 | 87 | 1.48 | 847 | 7 | 0 | 13.3 | 24 | 100 | 0 |
| medium / jump / settled | 41.2 | 346 | 16 | 34.3 | 4 | 4 | 5.37 | 0 | 24 | 8 | 13.5 | 248 | 68 | 0 |
| medium / jump_animated / settled | 93.4 | 292 | 34 | 29.7 | 0 | 0 | – | 0 | 54 | 14 | 14.3 | 360 | 70 | 0 |
| medium / jump / settled / nosync,overview,minz,camco,panco | 45.9 | 316 | 18 | 28.2 | 13 | 13 | 2.03 | 0 | 32 | 4 | 13.9 | 280 | 69 | 0 |
| medium / jump_animated / settled / nosync,overview,minz,camco,panco | 88.8 | 290 | 36 | 20.5 | 26 | 26 | 2.03 | 0 | 56 | 12 | 13.4 | 376 | 69 | 0 |
| stress / jump / settled | 23.8 | 570 | 18 | 54.8 | 6 | 6 | 27.19 | 0 | 64 | 7 | 14.9 | 440 | 66 | 0 |
| stress / jump_animated / settled | 49.3 | 1026 | 42 | 52.7 | 3 | 3 | 27.19 | 0 | 92 | 15 | 15.5 | 616 | 54 | 0 |
| stress / jump / settled / nosync,overview,minz,camco,panco | 37.4 | 522 | 18 | 52.0 | 14 | 14 | 9.56 | 0 | 74 | 7 | 15.0 | 480 | 66 | 0 |
| stress / jump_animated / settled / nosync,overview,minz,camco,panco | 50.4 | 803 | 41 | 58.2 | 16 | 16 | 9.56 | 0 | 96 | 15 | 15.7 | 600 | 55 | 0 |
| stress / pan_zoomed / settled / 3000px/s / nosync,overview,minz,camco,panco | 98.2 | 121 | 34 | 14.8 | 0 | 0 | – | 0 | 94 | 94 | 14.5 | 512 | 78 | 0 |
| stress / pan_zoomed / settled / 6000px/s / nosync,overview,minz,camco,panco | 85.3 | 635 | 43 | 36.0 | 1 | 1 | 9.56 | 0 | 85 | 85 | 14.8 | 536 | 64 | 0 |
| stress / zoom_trackpad / settled / nosync,overview,minz,camco,panco | 6.0 | 68 | 2 | 98.9 | 161 | 96 | 1.52 | 763 | 72 | 57 | 27.1 | 128 | 99 | 0 |
| medium / zoom_trackpad / settled | 8.2 | 718 | 34 | 44.4 | 156 | 119 | 3.93 | 0 | 72 | 61 | 15.7 | 120 | 84 | 0 |
| medium / zoom_trackpad / settled / nosync,overview,minz,camco,panco | 9.2 | 63 | 3 | 22.8 | 180 | 94 | 1.29 | 1041 | 106 | 97 | 14.8 | 96 | 93 | 0 |
| hires / zoom / settled | 3.3 | 858 | 29 | 64.5 | 107 | 79 | 5.46 | 0 | 18 | 6 | 22.6 | 136 | 66 | 0 |
| hires / zoom / settled / nosync,overview,minz,camco,panco | 1.7 | 36 | 2 | 11.6 | 165 | 95 | 1.48 | 1427 | 7 | 0 | 12.5 | 8 | 18 | 0 |
| mixed / zoom / settled | 13.3 | 1148 | 47 | 120.1 | 40 | 2 | 1.00 | 0 | 42 | 23 | 17.5 | 280 | 82 | 0 |
| mixed / zoom / settled / nosync,overview,minz,camco,panco | 3.5 | 107 | 7 | 61.5 | 152 | 84 | 1.41 | 4470 | 5 | 0 | 13.3 | 872 | 81 | 0 |
| medium / pan_zoomed / warm / 4v-linked / nosync,overview,minz,camco,panco | 100.0 | 0 | 0 | 0.0 | 0 | 0 | – | 0 | 1 | 1 | 8.3 | 28 | 58 | 0 |

## 3.6 Limitations

* One machine, one 59.95 Hz display, DPR 1.0. No 120 Hz, no high-DPI, no macOS/Linux, no integrated-GPU-only run.
* Synthetic photos and GIFs; no real-world board. Item sizes are uniform.
* One run per configuration except where stated; p99 and max on ~300 frames are single events.
* The harness cannot measure what the display actually shows (no photodiode / capture); "presented" is the GL
  swap signal.
* Whole-process CPU use was recorded (`process_cores_busy`) but is dominated by background decode jobs and is
  not analysed here.
* The visual quality of fallback frames was quantified (magnification, blank tiles), not judged by eye.

## 3.7 Reproducing

```
.venv\Scripts\python.exe tools\bench_canvas_nav.py generate            # boards (≈3 min, ~1.7 GB)
.venv\Scripts\python.exe tools\bench_canvas_nav.py env                 # environment record
set QT_QPA_PLATFORM=windows
.venv\Scripts\python.exe -I tools\bench_canvas_nav.py run --board medium --scenario zoom --cache settled
.venv\Scripts\python.exe -I tools\bench_canvas_nav.py run --board stress --scenario zoom --cache settled ^
    --variant nosync_zoom,overview,minzoom_cache,camera_coalesce,pan_coalesce
.venv\Scripts\python.exe tools\bench_canvas_nav.py matrix --configs docs\performance\data\matrix_f.json --out out.jsonl
.venv\Scripts\python.exe tools\summarize_nav_results.py out.jsonl --cols active   # frames|active|gui|tiles|refine|memory
```

`PIXELREF_PERF_DATA` relocates the boards; `--perf-spans` adds the app's own span table; `--set tiles.SLICE_MS=4`
overrides a constant. The `data/matrix_*.json` files are the exact configurations run.
