# 9. Big selections and the item index

The earlier phases made pan and zoom smooth with nothing selected. This pass measured the same board with
**most of it selected**, plus hover, rubber band, dragging the selection and select all, and fixed what the
profiles showed. Every number is from `tests/bench_selection.py` (new): real wheel and mouse events, each
timed until its frame is painted.

Measured on the offscreen platform (software painting, 1066 × 808 viewport), 3000 items (2400 images of
16 files, 600 rectangles), one run before and one after, same machine as the earlier reports. Offscreen
numbers show the Python and Qt work per event; they do not include a real display's present. **Not measured
here:** the OpenGL viewport end to end, 120 Hz, high-DPI, real boards. `tools/bench_canvas_nav.py` was not
re-run for this pass, so the tables in [03](03-benchmark-report.md) and [08](08-phase2-results.md) are not
updated.

## Results (mean ms per event, 3000 items)

| Scenario | Before | After |
|---|---|---|
| Pan, nothing selected | 8.7 | 4.6 |
| Zoom (wheel), nothing selected | 6.8 | 4.6 |
| Hover (mouse move), nothing selected | 48.3 | 6.0 |
| Pan, all selected, whole board | 84.3 | 10.1 |
| Zoom, all selected, whole board | 91.9 | 16.9 |
| Hover, all selected, whole board | 154.9 | 14.7 |
| Pan, all selected, zoomed in | 82.7 | 7.2 |
| Hover, all selected, zoomed in | 146.0 | 7.2 |
| Select all / deselect | 1243 | 144 |
| Rubber band over the board, per move | 742 (p95 3220) | 66 (p95 167) |
| Drag all selected, per move | 418 | 166 |
| Drag: release | 5677 | 334 |

Zoom with everything selected still has one long frame per run (max 116 ms, was 228).

## What was wrong, and the fix

1. **Qt's item index did nothing.** The scene rect was the whole 2,000,000-unit canvas. Qt's BSP index divides
   the *scene rect*, so one cell was larger than most boards and every query looked at every item:
   `view.items(pos)` took 6.6 ms and rendering one tile 7.2 ms on 3000 items. With a fitted rect: 0.02 ms and
   0.33 ms. This is also the "12.5 ms per tile on 5000 items" of the first report.
   Fix: `CanvasScene.fit_index()` keeps the scene rect around the board (re-fitted after a canvas refresh, only
   when it no longer fits or is 16× too large); each `CanvasView` sets its own scene rect to the full extent, so
   scrolling is unchanged. Items outside the index rect still work.
2. **Overlays recomputed the selection on every frame.** Outlines (a transform and a draw call per item), the
   gizmo (sorted targets and their bounds), frame badges, the quick-edit bar, playback, links, backdrops and
   pages each walked the selection or every item per paint or per camera change.
   Fix: `CanvasDocument.cached(key, compute)`, valid until `canvas.epoch` (refresh, item moved or repainted) or
   `selection.revision` changes. Outlines are two scene-space paths drawn through the camera with a cosmetic pen.
3. **Selecting re-rendered every tile.** Qt repaints an item when its selected state changes; items here never
   draw their selection. Fix: `CanvasScene.unchanged_pixels()` around the selection sync.
4. **The Contact Sheet selected thumbnails one row at a time** (0.2–0.7 ms each, every selection change, hidden
   or not). Fix: one `QItemSelection`; a hidden sheet catches up when shown.
5. **Panels refreshed on every rubber-band move.** Fix: `App.set_selection_live()`: while the band is dragged,
   `ui.selection_changed` is sent at most every 120 ms and once at release. The canvas follows every move.
   The "Shared attributes" inspector section looks specs up per type instead of per node.
6. **A drag ran app-wide work per item per move.** Fixes: the event stream routes by event type and formats
   timestamps only when read; views notice a moved board through `canvas.geometry_serial` instead of being told
   per item; hidden views drop their tiles instead of searching them; more than 64 changed rects are merged onto
   the tile grid before the levels are searched; `content_changed` passes its list by reference.
7. **Release ran one command per moved item.** Fix: the new `set_attrs_many` command (one command, one undo
   step). A single moved item still uses `set_attrs`.

## Behaviour changes to know about

* `ui.selection_changed` is delayed (≤ 120 ms) **only** while a rubber band is being dragged.
* Moving several items echoes one `set_attrs_many({...})` instead of one `set_attrs` per item (console, macros).
* `scene.sceneRect()` is the index rect, not the canvas extent; use `CanvasScene.extent_rect()` for the extent.
* `SceneEvent` is no longer a dataclass (same constructor, `to_dict`, attributes).
* Selection outlines are drawn with a cosmetic pen under the camera transform; pixel-identical output was not
  compared against the old per-polygon drawing.

## Not fixed

* **Dragging thousands of items is still slow** (166 ms per move at 3000). Every item really moves, so Qt
  calls `itemChange` per item, every tile under them is rendered again, and the outline path is rebuilt, each
  frame. Fixing it needs the moving items drawn as one translated layer during the drag and applied at release;
  not attempted.
* Select all on 3000 items takes about 0.14 s, most of it per-item work in panels and menu conditions
  (effects, backdrops, colour picker sections each walk the selection once).
* Rubber band: 66 ms per move at 3000 items, mostly the shape hit test and re-syncing Qt's selected flags.
* The software painter draws 3000 outlines in about 6 ms when the whole board is in view (measured 0.7 ms on
  an OpenGL painter in a separate probe, not in the app).

## Reproduce

```
.venv\Scripts\python.exe tests\bench_selection.py 3000
set BENCH_PROFILE=drag & .venv\Scripts\python.exe tests\bench_selection.py 3000     (pan, zoom, hover, select, rubber, drag)
.venv\Scripts\python.exe tests\run_tests.py test_big_selection
```
