# 7. GL `paintEvent` investigation (roadmap step 1.5)

Question: warm pan frames cost 5.8–6.0 ms in `CanvasView.paintEvent` on the OpenGL viewport against 2.1 ms in
software for the same tile blits. Where does the time go, and is a GL-specific fix worth doing?
Investigation only; no production code was changed. Tool: `tools/probe_gl_paint.py` (re-implements
`CanvasView._paint_event` with a timer per section; one configuration per process). Same machine as the
baseline (i7-7700, RTX 4060 Ti, 59.95 Hz, Qt 6.12, maximized window, `medium` board, settled, warm pan one step
per paint). Numbers are means over ≈240 paints (≈3–4 s) unless a range is given.

## Answer

1. **The extra ≈4 ms is not drawing work.** It is the GUI thread blocking inside `QPainter(self.viewport())`
   (painter *begin*) on the GL widget. Wall time 5.0–5.9 ms, thread CPU time 0.1–0.4 ms.
2. **Our own paint work is ≈0.8–1 ms per view in both modes** (tile blits 0.3 ms, overlays 0.45 ms,
   `QPainter.end()` 0.01 ms). GL blits are cheaper than software ones (0.3 vs 1.2 ms). 4× MSAA is not the cause.
3. **The wait is a position in the frame, not a cost.** Starting the paint later after the previous
   `frameSwapped` shrinks it: begin 5.3 ms at +2 ms, 2.3 ms at +8 ms, 0.4 ms at +14 ms (total `paintEvent`
   6.1 → 3.0 → 1.1 ms), while the frame interval stays vsync-locked. In software the same wait happens
   elsewhere (outside `paintEvent`), which is why software "looks" cheaper in a paint-time metric.
4. **Linked views are bound by per-widget presentation cost outside our code.** With all pixelRef painting
   switched off (no blit, no overlays), 4 GL views still need 19.0 ms per round (budget 16.7). One to three views
   fit in a vsync. So the 4-linked-view ceiling (38.6–42 fps) is not reachable by making tile or overlay
   painting cheaper.
5. **`QT_WIDGETS_RHI=1` looked like a fix and is not: it does not display the canvas.** (Qt's RHI-based widget
   compositing; default backend on Windows is D3D11.) The timing run showed begin wait 5.0 → 0.05 ms,
   single-view `paintEvent` 4.8 → 1.0 ms, 4 linked views 41.6 → 45.6 fps. Validating the pixels on screen
   (`tools/validate_rhi.py`) showed why: every canvas viewport is **black** (100% of sampled pixels in the single
   view steps, 90% with four views; default path: 0%), while the chrome and the minimap draw. The speedup was
   the cost of not compositing the GL widget. Forcing its OpenGL backend (`QT_WIDGETS_RHI_BACKEND=opengl`)
   displays correctly (identical to default within the 0.003% capture noise) but is as slow as the default
   (19.5 ms per round), so there is nothing to gain.

## Measurements

Single view, GL (3 runs each of the first row; the others are single runs unless noted):

| Config | begin (wall) | begin (CPU) | tiles | overlays | end | `paintEvent` total |
|---|---:|---:|---:|---:|---:|---:|
| GL, 4× MSAA | 5.0–5.9 | 0.13–0.4 | 0.3 | 0.45 | 0.01 | 5.9–6.7 |
| GL, MSAA off | 5.5 | n/a | 0.31 | 0.48 | 0.01 | 6.35 |
| Software | 0.01 | 0 | 1.26 | 0.43 | 0.00 | 1.74 |
| GL, paint starts +2 ms after swap | 5.30 | 0.13 | 0.30 | 0.45 | 0.01 | 6.09 |
| GL, +8 ms | 2.26 | 0.07 | 0.28 | 0.42 | 0.01 | 3.00 |
| GL, +14 ms | 0.39 | 0.21 | 0.26 | 0.40 | 0.01 | 1.09 |

One run of the plain GL configuration read 1.0 ms begin (after I changed the probe to support several views);
six runs before and three after read 5.0–5.9 ms. Treated as an outlier, cause not identified.

Round time (all views updated and painted, vsync-locked at 16.7 ms), painting disabled:

| Views | GL (default) | GL, `QT_WIDGETS_RHI=1` | Software |
|---:|---:|---:|---:|
| 1 | 16.6 | | 7.0 |
| 2 | 16.7 | | 11.1 |
| 3 | 16.3 | | 11.6 |
| 4 | 19.0 (19.2 in a separate run) | 16.7 | 16.6 |

The `QT_WIDGETS_RHI=1` column was measured while the canvas was not being displayed (see validation below): it
shows the cost of skipping the GL widget, not a usable configuration.
End-to-end (`bench_canvas_nav.py`, 4 linked views, medium, warm pan, default path): 42.0 / 41.2 fps. (The
earlier Phase 1 figure of 38.6–39 fps was taken at a different time; compare only within a pair.) Software
viewport reached 51.5–51.9 fps. None reach the 55 fps gate.

## Validation of `QT_WIDGETS_RHI=1` (`tools/validate_rhi.py`)

Real window, maximized, `medium` board; screen pixels captured after each of nine steps (initial, fit, four
linked views, pan, zoom, small window, maximized again, hide/show, back to one view). Capture noise between two
default-path runs: ≤0.057% of pixels differ (0.000–0.003% in 8 of 9 steps).

| Path | Canvas pixels vs default | Pure-black canvas samples |
|---|---|---|
| default | reference | 0% in all 9 steps |
| `QT_WIDGETS_RHI=1` (D3D11) | 29–51% of the window's pixels differ | 100% (single view), 89.6% (four views), 57.4% (small window) |
| `QT_WIDGETS_RHI=1`, `QT_WIDGETS_RHI_BACKEND=opengl` | ≤0.003% (identical) | 0% |

The captures include whatever else was on the desktop (another window overlapped part of the app); it is the
same in all runs, and the captures are not kept in the repository.

## Verdict

* **No-go on a GL-specific paint fix.** There is no per-frame paint cost to remove: the work is ≈1 ms and the
  rest is waiting. Optimising tiles or overlays cannot change linked-view throughput.
* **Do not describe the earlier "6 ms vs 2 ms" as a GL slowdown** in any user-facing claim. It is where a
  vsync-locked wait lands.
* **No-go on `QT_WIDGETS_RHI=1`** (see 5 above and the validation below). Its timings must not be quoted as a
  gain. The app's own `render_frame` / `grab_clean` paths paint into software `QImage`s and do not involve the GL
  widget, so they were never at risk; the failure is only in what reaches the screen. Not tested: macOS, Linux,
  other GPUs/Qt versions, where behaviour may differ.
* The 55 fps linked-view gate is not reachable on this machine with GL widgets by any change identified here
  (the one candidate, RHI, failed validation). Options if it must be met: reduce what linked views repaint (paint
  only the views whose content changed, or skip hidden/occluded ones), use the software viewport for linked
  layouts (51.5–51.9 fps measured), or accept ≈40 fps. None was prototyped as a change.

## Limits

One machine, one GPU/driver, one window size (maximized, 1920×1080 class), one board. The blocked resource
(driver frame throttle, DWM, or GPU back-pressure) was not identified: the evidence is CPU 0.2 ms vs wall 5.5 ms
and the phase dependence. No GPU capture (PIX/ETW) was taken. The probe pans in a paint-bound loop, not with
real input.
