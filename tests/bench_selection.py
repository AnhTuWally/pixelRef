"""Benchmark for big boards with big selections: ``python tests/bench_selection.py [items]``.

Every step is a real event (wheel, mouse) timed until its frame is painted, like
``bench_interactive.py``, but with most of the board selected: pan, zoom, hover,
rubber band, dragging the selection, select all / deselect.

``BENCH_PROFILE=<scenario>`` prints a cProfile of one scenario (pan, zoom, hover,
rubber, drag, select). ``QT_QPA_PLATFORM=windows`` for real-display numbers.
"""

from __future__ import annotations

import json
import math
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import APP, TMP, make  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QWheelEvent  # noqa: E402

ITEMS = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
PROFILE = os.environ.get("BENCH_PROFILE", "")
SIDE = 256
SPACING = SIDE * 1.2


def make_photo(index: int) -> str:
    path = TMP / f"sel_photo_{index}.png"
    if not path.exists():
        rnd = random.Random(index)
        image = QImage(SIDE, SIDE * 3 // 4, QImage.Format_RGB32)
        image.fill(QColor.fromHsv(rnd.randrange(360), 120, 200))
        p = QPainter(image)
        for _ in range(12):
            p.fillRect(rnd.randrange(SIDE), rnd.randrange(SIDE), rnd.randrange(20, 120), rnd.randrange(20, 120),
                       QColor.fromHsv(rnd.randrange(360), rnd.randrange(255), rnd.randrange(255)))
        p.end()
        image.save(str(path))
    return path.as_posix()


def build_scene() -> str:
    rnd = random.Random(1)
    photos = [make_photo(i) for i in range(16)]
    side = math.ceil(math.sqrt(ITEMS))
    nodes = []
    for i in range(ITEMS):
        x, y = (i % side) * SPACING + rnd.uniform(-20, 20), (i // side) * SPACING + rnd.uniform(-20, 20)
        if i % 5 == 4:
            nodes.append({"name": f"rect{i}", "type": "annotation_rect", "parent": None,
                          "attrs": {"translate": [x, y], "size": [200, 150]}})
        else:
            nodes.append({"name": f"img{i}", "type": "image", "parent": None,
                          "attrs": {"translate": [x, y], "rotate": rnd.choice([0.0, 0.0, 0.0, 8.0, -12.0]),
                                    "scale": [1.0, 1.0], "path": photos[i % len(photos)]}})
    path = TMP / "bench_selection.pxref"
    path.write_text(json.dumps({"pixelref_version": "1.0.0", "schema": 1, "nodes": nodes}), encoding="utf-8")
    return path.as_posix()


PAINTS = [0]
PAINT_MS: list[float] = []
RESULTS: dict[str, dict[str, float]] = {}


def _patch_paint() -> None:
    from pixelref.ui.canvas.view import CanvasView

    original = CanvasView.paintEvent

    def timed(self, event):  # noqa: ANN001
        PAINTS[0] += 1
        t0 = time.perf_counter()
        original(self, event)
        PAINT_MS.append((time.perf_counter() - t0) * 1000)

    CanvasView.paintEvent = timed


def stats(label: str, samples: list[float]) -> None:
    paint = PAINT_MS[:]
    PAINT_MS.clear()
    ordered = sorted(samples)
    p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
    RESULTS[label] = {"mean": round(statistics.mean(ordered), 2), "p95": round(p95, 2), "max": round(ordered[-1], 2),
                      "paint_mean": round(statistics.mean(paint), 2) if paint else 0.0}
    print(f"{label:<40} mean {statistics.mean(ordered):7.1f}  p95 {p95:7.1f}  max {ordered[-1]:7.1f} ms"
          f"  paint mean {statistics.mean(paint) if paint else 0:6.1f}", flush=True)


def main() -> None:
    _patch_paint()
    h = make()
    view = h.view
    vp = view.viewport()
    canvas = h.app.canvas
    start = time.perf_counter()
    h.app.run_command("file.open_path", build_scene(), confirm=False)
    canvas.flush_pending()
    APP.processEvents()
    print(f"open {ITEMS} items: {(time.perf_counter() - start) * 1000:.0f} ms, tiles="
          f"{'on' if view.uses_tiles() else 'off'}, viewport {vp.width()}x{vp.height()}, {type(vp).__name__}")

    def settle(seconds: float = 1.0) -> None:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            APP.processEvents()

    def frame(send) -> float:  # noqa: ANN001
        before = PAINTS[0]
        t0 = time.perf_counter()
        send()
        APP.processEvents()
        if PAINTS[0] == before:
            vp.repaint()
        return (time.perf_counter() - t0) * 1000

    centre = QPointF(vp.width() / 2, vp.height() / 2)

    def wheel(steps: int, pos: QPointF = centre):  # noqa: ANN202
        event = QWheelEvent(pos, vp.mapToGlobal(pos), QPoint(), QPoint(0, 120 * steps), Qt.NoButton,
                            Qt.NoModifier, Qt.NoScrollPhase, False)
        return lambda: APP.sendEvent(vp, event)

    def mouse(kind, pos: QPointF, button=Qt.MiddleButton, buttons=None):  # noqa: ANN001, ANN202
        if buttons is None:
            buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else button
        used = Qt.NoButton if kind == QEvent.MouseMove else button
        event = QMouseEvent(kind, pos, vp.mapToGlobal(pos), used, buttons, Qt.NoModifier)
        return lambda: APP.sendEvent(vp, event)

    def fit(scale: float = 1.0) -> None:
        view.fit_rect(canvas.scene_bounds(canvas.visible_content_items()))
        if scale != 1.0:
            view.zoom_by(scale, around_cursor=False)
        settle(1.5)

    def run(name: str, body) -> None:  # noqa: ANN001
        if PROFILE and PROFILE != name:
            return
        if PROFILE:
            import cProfile
            import pstats
            pr = cProfile.Profile()
            pr.enable()
            body()
            pr.disable()
            pstats.Stats(pr).sort_stats(os.environ.get("BENCH_SORT", "tottime")).print_stats(int(os.environ.get("BENCH_ROWS", "28")))
        else:
            body()

    def select_all() -> None:
        h.app.trigger("select.all")
        APP.processEvents()

    def pan(label: str) -> None:
        frame(mouse(QEvent.MouseButtonPress, centre))
        pos, samples = QPointF(centre), []
        for i in range(80):
            pos += QPointF(11, 5) if i < 40 else QPointF(-11, -5)
            samples.append(frame(mouse(QEvent.MouseMove, pos)))
        frame(mouse(QEvent.MouseButtonRelease, pos))
        stats(label, samples)

    def zoom(label: str) -> None:
        samples = [frame(wheel(1)) for _ in range(15)]
        samples += [frame(wheel(-1)) for _ in range(15)]
        stats(label, samples)

    def hover(label: str) -> None:
        samples = [frame(mouse(QEvent.MouseMove, QPointF(200 + i * 9, 300 + i * 3), buttons=Qt.NoButton))
                   for i in range(60)]
        stats(label, samples)

    # ---- Nothing selected (the reference) -------------------------------------------------
    fit(4.0)
    PAINT_MS.clear()
    run("pan", lambda: pan("pan, nothing selected"))
    run("zoom", lambda: zoom("zoom, nothing selected"))
    run("hover", lambda: hover("hover, nothing selected"))

    # ---- Select all / deselect ---------------------------------------------------------------
    def select_scenario() -> None:
        samples = []
        for _ in range(4):
            samples.append(frame(select_all))
            samples.append(frame(lambda: h.app.run_command("select", clear=True)))
        stats("select all / deselect", samples)

    fit()
    PAINT_MS.clear()
    run("select", select_scenario)

    # ---- Everything selected ------------------------------------------------------------------
    select_all()
    fit()
    PAINT_MS.clear()
    run("pan", lambda: pan("pan, all selected, whole board"))
    run("zoom", lambda: zoom("zoom, all selected, whole board"))
    run("hover", lambda: hover("hover, all selected, whole board"))
    fit(4.0)
    PAINT_MS.clear()
    run("pan", lambda: pan("pan, all selected, zoomed in"))
    run("hover", lambda: hover("hover, all selected, zoomed in"))

    # ---- Drag the whole selection ---------------------------------------------------------------
    def drag() -> None:
        target = view.to_viewport(canvas.item("img0").scene_content_rect().center())
        view.set_camera(center=[canvas.item("img0").scene_content_rect().center().x() + 400,
                                canvas.item("img0").scene_content_rect().center().y() + 250])
        settle(1.0)
        PAINT_MS.clear()
        target = view.to_viewport(canvas.item("img0").scene_content_rect().center())
        t0 = time.perf_counter()
        frame(mouse(QEvent.MouseButtonPress, target, Qt.LeftButton))
        press = (time.perf_counter() - t0) * 1000
        pos, samples = QPointF(target), []
        for _ in range(30):
            pos += QPointF(6, 3)
            samples.append(frame(mouse(QEvent.MouseMove, pos, Qt.LeftButton)))
        t0 = time.perf_counter()
        frame(mouse(QEvent.MouseButtonRelease, pos, Qt.LeftButton))
        release = (time.perf_counter() - t0) * 1000
        stats("drag all selected (30 moves)", samples)
        print(f"{'  press / release':<40} {press:7.1f} / {release:7.1f} ms", flush=True)
        RESULTS["drag press"] = {"mean": round(press, 2)}
        RESULTS["drag release"] = {"mean": round(release, 2)}
        h.undo()
        APP.processEvents()

    run("drag", drag)

    # ---- Rubber band over the whole board ------------------------------------------------------
    def rubber() -> None:
        h.app.run_command("select", clear=True)
        fit(0.8)
        PAINT_MS.clear()
        origin = QPointF(6, 6)
        frame(mouse(QEvent.MouseButtonPress, origin, Qt.LeftButton))
        samples = []
        for i in range(1, 41):
            pos = QPointF(6 + (vp.width() - 12) * i / 40.0, 6 + (vp.height() - 12) * i / 40.0)
            samples.append(frame(mouse(QEvent.MouseMove, pos, Qt.LeftButton)))
        frame(mouse(QEvent.MouseButtonRelease, pos, Qt.LeftButton))
        stats("rubber band over the board (40 moves)", samples)
        print(f"{'  selected':<40} {len(h.sel())}", flush=True)

    run("rubber", rubber)

    out = os.environ.get("BENCH_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as handle:
            json.dump({"items": ITEMS, "results": RESULTS}, handle, indent=1)
    h.close()


if __name__ == "__main__":
    main()
