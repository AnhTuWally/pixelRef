"""Interactive canvas benchmark: real wheel / middle-drag events on a board of distinct photos.

``python tests/bench_interactive.py [images] [annotations]``. Every event is
delivered like the OS would and timed until its frame is painted; a smooth
canvas needs every one of them well under 16 ms. Run it on the real platform
for real numbers (``QT_QPA_PLATFORM=windows``, ``BENCH_MAXIMIZED=1``;
``BENCH_SIZE=2400`` for big photos; ``PIXELREF_GL=0`` to compare software).
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

IMAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 200
NOTES = int(sys.argv[2]) if len(sys.argv) > 2 else 600
SPACING = int(os.environ.get("BENCH_SIZE", "1024")) * 1.15


def make_photo(index: int) -> str:
    path = TMP / f"photo_{index}_{os.environ.get('BENCH_SIZE', '1024')}.jpg"
    if not path.exists():
        rnd = random.Random(index)
        big = int(os.environ.get("BENCH_SIZE", "1024"))
        w, h = rnd.choice([(big, big * 3 // 4), (big * 3 // 4, big), (big, big), (big, big * 9 // 16)])
        image = QImage(w, h, QImage.Format_RGB32)
        image.fill(QColor.fromHsv(rnd.randrange(360), 120, 200))
        p = QPainter(image)
        for _ in range(40):
            p.fillRect(rnd.randrange(w), rnd.randrange(h), rnd.randrange(20, 300), rnd.randrange(20, 300),
                       QColor.fromHsv(rnd.randrange(360), rnd.randrange(255), rnd.randrange(255)))
        p.end()
        image.save(str(path), quality=85)
    return path.as_posix()


def build_scene() -> str:
    rnd = random.Random(1)
    nodes = []
    side = math.ceil(math.sqrt(IMAGES))
    for i in range(IMAGES):
        x, y = (i % side) * SPACING + rnd.uniform(-60, 60), (i // side) * SPACING + rnd.uniform(-60, 60)
        nodes.append({"name": f"img{i}", "type": "image", "parent": None,
                      "attrs": {"translate": [x, y], "rotate": rnd.choice([0.0, 0.0, 0.0, 8.0, -12.0]),
                                "scale": [1.0, 1.0], "path": make_photo(i)}})
    for i in range(NOTES):
        x, y = rnd.uniform(0, side * SPACING), rnd.uniform(0, side * SPACING)
        kind = i % 3
        if kind == 0:
            nodes.append({"name": f"rect{i}", "type": "annotation_rect", "parent": None,
                          "attrs": {"translate": [x, y], "size": [300, 200]}})
        elif kind == 1:
            nodes.append({"name": f"text{i}", "type": "annotation_text", "parent": None,
                          "attrs": {"translate": [x, y], "text": f"Reference note {i}\nlighting / colour"}})
        else:
            pts = [[j * 8.0, 40 * math.sin(j / 4.0)] for j in range(40)]
            nodes.append({"name": f"stroke{i}", "type": "annotation_path", "parent": None,
                          "attrs": {"translate": [x, y], "points": pts, "thickness": 6.0}})
    path = TMP / "bench_board.pxref"
    path.write_text(json.dumps({"pixelref_version": "1.0.0", "schema": 1, "nodes": nodes}), encoding="utf-8")
    return path.as_posix()


PAINT_MS: list[float] = []


def stats(label: str, samples: list[float]) -> None:
    paint = PAINT_MS[:]
    PAINT_MS.clear()
    samples = sorted(samples)
    p95 = samples[int(len(samples) * 0.95) - 1]
    print(f"{label:<34} mean {statistics.mean(samples):7.1f}  p95 {p95:7.1f}  max {samples[-1]:7.1f} ms"
          f"  ({sum(s > 16.7 for s in samples)}/{len(samples)} > 16.7)"
          f"  paint mean {statistics.mean(paint) if paint else 0:5.1f} max {max(paint) if paint else 0:6.1f}")


PAINTS = [0]


def _patch_paint() -> None:
    from pixelref.ui.canvas.view import CanvasView

    original = CanvasView.paintEvent

    def timed(self, event):  # noqa: ANN001
        PAINTS[0] += 1
        t0 = time.perf_counter()
        original(self, event)
        PAINT_MS.append((time.perf_counter() - t0) * 1000)

    CanvasView.paintEvent = timed


def main() -> None:
    _patch_paint()
    h = make()
    if os.environ.get("BENCH_MAXIMIZED"):
        h.window.showMaximized()
        APP.processEvents()
    view = h.view
    vp = view.viewport()
    board = build_scene()
    start = time.perf_counter()
    h.app.run_command("file.open_path", board, confirm=False)
    h.app.canvas.flush_pending()
    APP.processEvents()
    print(f"open {IMAGES} images + {NOTES} notes: {(time.perf_counter() - start) * 1000:.0f} ms,"
          f" {len(h.app.canvas.items)} items, tiles={'on' if view.uses_tiles() else 'off'},"
          f" viewport {vp.width()}x{vp.height()} @ dpr {view.devicePixelRatioF()}, {type(vp).__name__}")
    canvas = h.app.canvas
    view.fit_rect(canvas.scene_bounds(canvas.visible_content_items()))
    APP.processEvents()
    vp.repaint()

    def frame(send) -> float:
        before = PAINTS[0]
        t0 = time.perf_counter()
        send()
        APP.processEvents()  # handlers, timers due, and the paint the event asked for
        if PAINTS[0] == before:
            vp.repaint()  # make sure every sample includes a frame
        return (time.perf_counter() - t0) * 1000

    centre = QPointF(vp.width() / 2, vp.height() / 2)

    def wheel(steps: int, pos: QPointF = centre):
        event = QWheelEvent(pos, vp.mapToGlobal(pos), QPoint(), QPoint(0, 120 * steps), Qt.NoButton,
                            Qt.NoModifier, Qt.NoScrollPhase, False)
        return lambda: APP.sendEvent(vp, event)

    def mouse(kind, pos: QPointF, buttons=Qt.MiddleButton):
        button = Qt.MiddleButton if kind != QEvent.MouseMove else Qt.NoButton
        event = QMouseEvent(kind, pos, vp.mapToGlobal(pos), button, buttons, Qt.NoModifier)
        return lambda: APP.sendEvent(vp, event)

    # Zoom in from "fit all" to 1:1-ish, then back out: 2 x 25 wheel notches.
    end = time.perf_counter() + 2.0
    while time.perf_counter() < end:  # let background work (mipmaps) finish, like a user would
        APP.processEvents()
    if os.environ.get("BENCH_PROFILE_FIRST"):
        import cProfile
        import pstats
        pr = cProfile.Profile()
        pr.enable()
        samples = [frame(wheel(1)) for _ in range(25)]
        samples += [frame(wheel(-1)) for _ in range(25)]
        pr.disable()
        print("paints:", [round(x) for x in PAINT_MS])
        pstats.Stats(pr).sort_stats("tottime").print_stats(18)
        h.close()
        return
    samples = [frame(wheel(1)) for _ in range(25)]
    stats("wheel zoom in (25 notches)", samples)
    samples = [frame(wheel(-1)) for _ in range(25)]
    stats("wheel zoom out (25 notches)", samples)
    # Settle (the idle renderer finishes), then pan with the middle button.
    end = time.perf_counter() + 1.0
    while time.perf_counter() < end:
        APP.processEvents()
    frame(mouse(QEvent.MouseButtonPress, centre))
    samples = []
    pos = QPointF(centre)
    for _ in range(60):
        pos += QPointF(9, 4)
        samples.append(frame(mouse(QEvent.MouseMove, pos)))
    frame(mouse(QEvent.MouseButtonRelease, pos, Qt.NoButton))
    stats("middle-drag pan, fit all (60 moves)", samples)
    # Zoomed in on part of the board, pan again.
    for _ in range(12):
        frame(wheel(1))
    end = time.perf_counter() + 1.0
    while time.perf_counter() < end:
        APP.processEvents()
    frame(mouse(QEvent.MouseButtonPress, centre))
    samples = []
    pos = QPointF(centre)
    for _ in range(60):
        pos += QPointF(-14, -6)
        samples.append(frame(mouse(QEvent.MouseMove, pos)))
    frame(mouse(QEvent.MouseButtonRelease, pos, Qt.NoButton))
    stats("middle-drag pan, zoomed in", samples)
    samples = [frame(mouse(QEvent.MouseMove, QPointF(200 + i * 5, 300), Qt.NoButton)) for i in range(40)]
    stats("hover (mouse move, no button)", samples)
    if os.environ.get("BENCH_PROFILE"):
        import cProfile
        import pstats
        view.fit_rect(canvas.scene_bounds(canvas.visible_content_items()))
        APP.processEvents()
        pr = cProfile.Profile()
        pr.enable()
        for _ in range(15):
            frame(wheel(1))
        for _ in range(15):
            frame(wheel(-1))
        pr.disable()
        pstats.Stats(pr).sort_stats("tottime").print_stats(30)
    h.close()


if __name__ == "__main__":
    main()
