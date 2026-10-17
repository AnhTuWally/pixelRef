"""Canvas rendering benchmark: many mixed elements, timed repaints/pan/zoom/drag/refresh.

``BENCH_N=4000 python tests/bench_canvas.py`` (``PIXELREF_TILES=0`` to compare without the tile cache).
"""
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import APP, make, make_png  # noqa: E402
from PySide6.QtCore import QPointF  # noqa: E402

N = int(os.environ.get("BENCH_N", "4000"))
PROFILE = os.environ.get("BENCH_PROFILE")


def t(label, fn, repeat=5):
    fn()  # warm
    start = time.perf_counter()
    for _ in range(repeat):
        fn()
    ms = (time.perf_counter() - start) / repeat * 1000
    print(f"{label:<40} {ms:8.1f} ms")
    return ms


h = make()
view = h.view
canvas = h.app.canvas
img = make_png("shared.png", 1024, 768, "#4080c0").as_posix()
start = time.perf_counter()
side = int(math.ceil(math.sqrt(N)))
with canvas.batch():
    if True:
        for i in range(N):
            x, y = (i % side) * 300.0, (i // side) * 260.0
            k = i % 4
            name = f"n{i}"
            if k == 0:
                h.run("create_node", "image", name, attrs={"path": img, "translate": [x, y], "scale": [0.2, 0.2]})
            elif k == 1:
                h.run("create_node", "annotation_rect", name, attrs={"translate": [x, y], "size": [200, 150]})
            elif k == 2:
                h.run("create_node", "annotation_text", name, attrs={"translate": [x, y], "text": f"Note {i}\nsecond line"})
            else:
                pts = [[j * 4.0, 30 * math.sin(j / 5.0)] for j in range(50)]
                h.run("create_node", "annotation_path", name, attrs={"translate": [x, y], "points": pts})
canvas.refresh(force=True)
APP.processEvents()
print(f"{'build ' + str(N) + ' nodes':<40} {(time.perf_counter() - start) * 1000:8.1f} ms")

vp = view.viewport()


def repaint():
    vp.repaint()


bounds = canvas.scene_bounds(canvas.visible_content_items())
results = {}
view.fit_rect(bounds)
APP.processEvents()
results["repaint, fit all"] = t("repaint, fit all (everything visible)", repaint)
view.set_camera([1500, 1500], 1.0, 0.0)
APP.processEvents()
results["repaint, zoom 1"] = t("repaint, zoom 1.0 (few visible)", repaint)

view.fit_rect(bounds)
APP.processEvents()
c = view.center()


def pan():
    view.pan_by(QPointF(7, 3))
    vp.repaint()


results["pan step, fit all"] = t("pan step + repaint, fit all", pan, 10)
state = {"z": 1.0}


def zoom():
    state["z"] = 1 / state["z"] if False else state["z"]
    view.zoom_by(1.02, around_cursor=False)
    vp.repaint()


results["zoom step, fit all"] = t("zoom step + repaint, fit all", zoom, 10)

# Drag-move: select 200 items and translate (what a gizmo drag does each mouse move)
view.fit_rect(bounds)
APP.processEvents()
names = [f"n{i}" for i in range(0, min(N, 200))]
h.select(names)
items = canvas.transform_targets()
canvas.begin_interaction()
one = [canvas.item("n5")]


def drag1():
    canvas.translate_items(one, 3.0, 1.0)
    APP.processEvents()
    vp.repaint()


results["drag 1 item"] = t("drag-move 1 item + repaint", drag1, 10)


def drag():
    canvas.translate_items(items, 3.0, 1.0)
    APP.processEvents()
    vp.repaint()


def hover():
    vp.repaint()


view.fit_rect(bounds)
APP.processEvents()
results["hover (no change)"] = t("overlay-only repaint (hover), fit all", hover, 10)
results["drag 200 items"] = t("drag-move 200 selected + repaint", drag, 10)
canvas.end_interaction()
APP.processEvents()


def edit_one():
    h.run("set_attrs", "n1", {"size": [200 + time.time() % 5, 150]})
    canvas.flush_pending()
    APP.processEvents()


results["edit one attr"] = t("edit one attr -> refresh", edit_one, 5)
h.select([])



if PROFILE:
    import cProfile
    import pstats
    view.fit_rect(bounds)
    APP.processEvents()
    pr = cProfile.Profile()
    pr.enable()
    for _ in range(10):
        pan()
    pr.disable()
    pstats.Stats(pr).sort_stats("cumulative").print_stats(25)
h.close()
