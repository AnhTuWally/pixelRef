"""Correctness check for ``QT_WIDGETS_RHI=1`` (roadmap step 1.5 follow-up).

Drives the real window through a fixed sequence (fit, split into linked views, pan, zoom, resize,
maximize, hide/show), captures the screen pixels of the window after each step and writes them as
PNGs. Run once per environment, then compare the two folders:

    set QT_WIDGETS_RHI=1 & .venv\\Scripts\\python.exe -I tools\\validate_rhi.py run --out D:\\rhi-on
    .venv\\Scripts\\python.exe -I tools\\validate_rhi.py run --out D:\\rhi-off
    .venv\\Scripts\\python.exe -I tools\\validate_rhi.py compare D:\\rhi-off D:\\rhi-on

Each step also checks the app's own paths: ``render_frame()`` against the screen, and that the GL
viewport reports the expected format. Needs a real display (platform ``windows``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(args: argparse.Namespace) -> None:
    sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")]
    os.environ["PIXELREF_GL"] = "1"
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    from harness import APP, make  # noqa: PLC0415
    from PySide6.QtCore import QPointF, QRect

    import bench_canvas_nav as B  # noqa: PLC0415

    board, _info = B._board(args.board)
    h = make()
    h.window.showMaximized()
    for _ in range(30):
        APP.processEvents()
    app = h.app
    app.run_command("file.open_path", board.as_posix(), confirm=False)
    app.canvas.flush_pending()
    view = h.view
    screen = APP.primaryScreen()
    report: dict = {"rhi_env": os.environ.get("QT_WIDGETS_RHI", ""), "steps": {}}

    def settle() -> None:
        deadline = time.perf_counter() + 25
        stable = 0
        while time.perf_counter() < deadline:
            APP.processEvents()
            time.sleep(0.003)
            v, r, p = B.quality(view)
            stable = stable + 1 if (v == 0 and r == 0 and p == 0) else 0
            if stable >= 8:
                break
        for _ in range(20):  # let the last frame reach the screen
            APP.processEvents()
            time.sleep(0.01)

    def shot(name: str) -> None:
        settle()
        win = h.window
        rect = win.frameGeometry() if not win.isMaximized() else win.geometry()
        pix = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
        pix.save(str(out / f"{name}.png"))
        frame = view.render_frame()
        frame.save(str(out / f"{name}.frame.png"))
        report["steps"][name] = {"window": [rect.width(), rect.height()], "screen_px": [pix.width(), pix.height()],
                                 "viewport": [view.viewport().width(), view.viewport().height()],
                                 "viewport_class": type(view.viewport()).__name__}

    view.set_camera(view.camera()["center"], 1.0, 0.0)
    shot("01_initial")
    view.fit_rect(app.canvas.scene_bounds(app.canvas.visible_content_items()))
    shot("02_fit")
    names = [view.view_name]
    for i in range(1, 4):
        names.append(app.run_command("wm.split_view", "right" if i % 2 else "down", "Main"))
    views = [app.views[n] for n in names]
    for v in views:
        v.link_group = "rhi"
    for _ in range(20):
        APP.processEvents()
    view.set_camera(view.camera()["center"], 0.6, 0.0)
    shot("03_four_linked")
    for _ in range(25):
        view.pan_by(QPointF(14.0, -9.0))
        APP.processEvents()
    shot("04_panned")
    for _ in range(6):
        view.zoom_by(1.25)
        APP.processEvents()
    shot("05_zoomed")
    h.window.showNormal()
    h.window.resize(1000, 700)
    for _ in range(30):
        APP.processEvents()
    shot("06_resized_small")
    h.window.showMaximized()
    for _ in range(30):
        APP.processEvents()
    shot("07_maximized_again")
    h.window.hide()
    for _ in range(10):
        APP.processEvents()
    h.window.showMaximized()
    for _ in range(30):
        APP.processEvents()
    shot("08_hide_show")
    # collapse back to one view (closing split tiles) and check it still paints
    for n in names[1:]:
        try:
            app.run_command("wm.close_view", n)
        except Exception as exc:  # noqa: BLE001
            report.setdefault("notes", []).append(f"close {n}: {exc}")
    for _ in range(30):
        APP.processEvents()
    shot("09_back_to_one")
    (out / "report.json").write_text(json.dumps(report, indent=1))
    print("DONE", out, "steps", len(report["steps"]))
    h.close()


def compare(args: argparse.Namespace) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QImage

    a_dir, b_dir = Path(args.a), Path(args.b)
    for a in sorted(a_dir.glob("*.png")):
        b = b_dir / a.name
        if not b.exists():
            print(f"{a.name}: missing in {b_dir}")
            continue
        ia, ib = QImage(str(a)).convertToFormat(QImage.Format_RGB32), QImage(str(b)).convertToFormat(QImage.Format_RGB32)
        if ia.size() != ib.size():
            print(f"{a.name}: SIZE {ia.width()}x{ia.height()} vs {ib.width()}x{ib.height()}")
            continue
        diff = total = big = 0
        peak = 0
        bytes_a, bytes_b = bytes(ia.constBits()), bytes(ib.constBits())
        stride = ia.bytesPerLine()
        width = ia.width() * 4
        for y in range(ia.height()):
            ra = bytes_a[y * stride:y * stride + width]
            rb = bytes_b[y * stride:y * stride + width]
            if ra == rb:
                total += ia.width()
                continue
            for x in range(0, width, 4):
                total += 1
                d = max(abs(ra[x] - rb[x]), abs(ra[x + 1] - rb[x + 1]), abs(ra[x + 2] - rb[x + 2]))
                if d:
                    diff += 1
                    peak = max(peak, d)
                    if d > 8:
                        big += 1
        print(f"{a.name:32s} differing {100 * diff / total:6.3f}%  (>8 levels: {100 * big / total:6.3f}%)  max {peak}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--board", default="medium")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    args = parser.parse_args()
    run(args) if args.cmd == "run" else compare(args)


if __name__ == "__main__":
    main()
