"""Where does a canvas ``paintEvent`` spend its time? (roadmap step 1.5; investigation only)

Re-implements ``CanvasView._paint_event`` with timers around each section, pans a settled board one
step per paint (paint-bound loop, no input), and prints per-section percentiles. Production code is
not modified. Run one configuration per process:

    .venv\\Scripts\\python.exe -I tools\\probe_gl_paint.py --board medium --mode gl            # MSAA x4 (default)
    .venv\\Scripts\\python.exe -I tools\\probe_gl_paint.py --board medium --mode gl --samples 0
    .venv\\Scripts\\python.exe -I tools\\probe_gl_paint.py --board medium --mode sw

``--finish`` calls glFinish after ``painter.end()`` (GL modes) to separate CPU submission from GPU time;
``--no-overlays`` skips the overlay painters.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def stats(values: list[float]) -> dict:
    return {"n": len(values), "mean": round(sum(values) / max(1, len(values)), 3), "p50": round(pct(values, 0.5), 3),
            "p95": round(pct(values, 0.95), 3), "max": round(max(values or [0.0]), 3)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--board", default="medium")
    parser.add_argument("--mode", choices=["gl", "sw"], default="gl")
    parser.add_argument("--samples", type=int, default=None, help="MSAA samples for the GL viewport (default 4)")
    parser.add_argument("--seconds", type=float, default=4.0)
    parser.add_argument("--finish", action="store_true")
    parser.add_argument("--no-overlays", action="store_true")
    parser.add_argument("--phase-ms", type=float, default=None,
                        help="start each paint this many ms after the previous frameSwapped (GL)")
    parser.add_argument("--views", type=int, default=1, help="split views; they are linked, view 1 is panned")
    parser.add_argument("--no-blit", action="store_true", help="skip tiles.paint (measures painter begin/end only)")
    args = parser.parse_args()

    sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")]
    os.environ["PIXELREF_GL"] = "1" if args.mode == "gl" else "0"
    os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
    from harness import APP, make  # noqa: PLC0415
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainter

    import bench_canvas_nav as B  # noqa: PLC0415
    from pixelref.ui.canvas import gpu
    from pixelref.ui.canvas.view import CanvasView

    if args.samples is not None:
        gpu.SAMPLES = args.samples

    board, _info = B._board(args.board)
    h = make()
    h.window.showMaximized()
    for _ in range(20):
        APP.processEvents()
    app = h.app
    app.run_command("file.open_path", board.as_posix(), confirm=False)
    app.canvas.flush_pending()
    APP.processEvents()
    view = h.view
    views = [view]
    for i in range(1, args.views):
        views.append(app.views[app.run_command("wm.split_view", "right" if i % 2 else "down", "Main")])
    for _ in range(10):
        APP.processEvents()
    if args.views > 1:
        for v in views:
            v.link_group = "probe"
    view.set_camera(view.camera()["center"], 1.0, 0.0)
    for _ in range(10):
        APP.processEvents()

    rec: dict[str, list[float]] = {k: [] for k in ("total", "flush", "begin", "tiles", "begin_cpu", "overlays",
                                                   "end", "finish")}
    on = [False]
    now = time.perf_counter

    def paint_event(self, event):  # noqa: ANN001
        t0 = now()
        if not self.uses_tiles():
            return CanvasView.__dict__["_orig_paint_event"](self, event)
        self.app.canvas.scene.flush_changes()
        t1 = now()
        c1 = time.thread_time()
        painter = QPainter(self.viewport())
        c2 = time.thread_time()
        t2 = now()
        if not args.no_blit:
            self.tiles.paint(painter)
        t3 = now()
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not args.no_overlays:
            self._paint_overlays(painter)
        t4 = now()
        painter.end()
        t5 = now()
        if args.finish and hasattr(self.viewport(), "context"):
            ctx = self.viewport().context()
            if ctx is not None and ctx.makeCurrent(self.viewport().context().surface()):
                ctx.functions().glFinish()
        t6 = now()
        if on[0]:
            rec["flush"].append((t1 - t0) * 1000)
            rec["begin"].append((t2 - t1) * 1000)
            rec["begin_cpu"].append((c2 - c1) * 1000)
            rec["tiles"].append((t3 - t2) * 1000)
            rec["overlays"].append((t4 - t3) * 1000)
            rec["end"].append((t5 - t4) * 1000)
            rec["finish"].append((t6 - t5) * 1000)
            rec["total"].append((t6 - t0) * 1000)

    CanvasView._orig_paint_event = CanvasView._paint_event
    CanvasView._paint_event = paint_event

    # settle: fully render the start view
    deadline = now() + 20
    while now() < deadline:
        APP.processEvents()
        time.sleep(0.002)
        v, r, p = B.quality(view)
        if v == 0 and r == 0 and p == 0:
            break

    swaps: list[float] = []
    if hasattr(view.viewport(), "frameSwapped"):
        view.viewport().frameSwapped.connect(lambda: swaps.append(now()) if on[0] else None)

    direction = 1.0
    frames = 0
    on[0] = True
    end = now() + args.seconds
    last_paint_count = 0
    interval: list[float] = []
    loop_cpu: list[float] = []
    last_t = now()
    while now() < end:
        view.pan_by(QPointF(3.0 * direction, 0.0))
        if frames % 40 == 39:
            direction = -direction
        before = len(rec["total"])
        cpu0 = time.thread_time()
        if args.phase_ms is not None and swaps:
            while now() - swaps[-1] < args.phase_ms / 1000.0:
                APP.processEvents()
        for v in views:
            v.viewport().update()
        # run the loop until this update painted
        guard = now() + 0.25
        while len(rec["total"]) < before + len(views) and now() < guard:
            APP.processEvents()
        loop_cpu.append((time.thread_time() - cpu0) * 1000)
        t = now()
        interval.append((t - last_t) * 1000)
        last_t = t
        frames += 1
    on[0] = False

    out = {"mode": args.mode, "samples": gpu.SAMPLES if args.mode == "gl" else None, "board": args.board,
           "renderer": gpu.renderer, "finish": args.finish, "no_overlays": args.no_overlays, "no_blit": args.no_blit, "phase_ms": args.phase_ms,
           "frames": frames, "views": args.views, "loop_interval_ms": stats(interval),
           "loop_cpu_ms": stats(loop_cpu)}
    out.update({k: stats(v) for k, v in rec.items()})
    if len(swaps) > 2:
        gaps = [(b - a) * 1000 for a, b in zip(swaps, swaps[1:])]
        out["swap_interval_ms"] = stats(gaps)
    print("PROBE " + json.dumps(out))
    h.close()


if __name__ == "__main__":
    main()
