"""Screen-capture check of progressive tile rendering (``PIXELREF_TILE_PROGRESSIVE``).

Opens a generated board in a real window and zooms it with the wheel glide: fit the board, zoom in
(frames are captured mid-glide and once settled), edit an item mid-glide and capture again. Run once
per setting, then compare the settled frames, which must match, and look at the mid-glide ones:

    .venv\\Scripts\\python.exe -I tools\\validate_progressive.py run --out D:\\prog-off
    set PIXELREF_TILE_PROGRESSIVE=1 & .venv\\Scripts\\python.exe -I tools\\validate_progressive.py run --out D:\\prog-on
    .venv\\Scripts\\python.exe -I tools\\validate_progressive.py compare D:\\prog-off D:\\prog-on

Frames are ``view.render_frame()`` images (the tile cache's own output); ``--screen`` adds real-screen captures,
which include whatever else is on the desktop and are too noisy to compare. Keep captures out of the repository.
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
    from PySide6.QtCore import QPointF

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
    report: dict = {"progressive": os.environ.get("PIXELREF_TILE_PROGRESSIVE", ""), "frames": {}}

    def settle() -> None:
        deadline = time.perf_counter() + 40
        stable = 0
        while time.perf_counter() < deadline:
            APP.processEvents()
            time.sleep(0.003)
            v, r, p = B.quality(view)
            stable = stable + 1 if (v == 0 and r == 0 and p == 0) else 0
            if stable >= 8 and view.camera_idle(0.3):  # not while a glide is still running
                break
        for _ in range(20):
            APP.processEvents()
            time.sleep(0.01)

    def shot(name: str) -> None:
        # What the tile cache paints, as an image: independent of whatever else is on the desktop.
        view.render_frame().save(str(out / f"{name}.png"))
        if args.screen:  # the real screen as well (includes other windows: noisy)
            win = h.window
            rect = win.geometry() if win.isMaximized() else win.frameGeometry()
            screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height()).save(str(out / f"{name}.screen.png"))
        report["frames"][name] = {"zoom": round(view.zoom_level(), 4), "zooming": view.zooming(),
                                  "overview_tiles": view.tiles.overview_tile_count,
                                  "tile_count": view.tiles.tile_count}

    def spin(seconds: float) -> None:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            APP.processEvents()

    bounds = app.canvas.scene_bounds(app.canvas.visible_content_items())
    view.fit_rect(bounds)
    settle()
    shot("01_fit_settled")
    home, zoom0 = view.camera()["center"], view.zoom_level()  # glide end states vary with timing: land on fixed ones
    centre = QPointF(view.viewport().width() / 2, view.viewport().height() / 2)
    for _ in range(6):  # zoom in about the centre
        view.glide_zoom(1.5, centre)
        spin(0.03)
    spin(0.04)
    shot("02_zoom_in_mid_glide")
    spin(0.06)
    shot("03_zoom_in_later_in_glide")
    view.set_camera(home, zoom0 * 3.0)
    settle()
    shot("04_zoom_in_settled")
    for _ in range(8):  # and back out, further than the fit
        view.glide_zoom(1 / 1.5, centre)
        spin(0.03)
    spin(0.05)
    shot("05_zoom_out_mid_glide")
    view.set_camera(home, zoom0 * 0.8)
    settle()
    shot("06_zoom_out_settled")
    # an edit during a glide: move the first image, keep zooming, look after it settles
    names = [n for n, it in app.canvas.items.items() if hasattr(it, "pixmap")] or list(app.canvas.items)
    victim = app.canvas.items[names[len(names) // 2]]
    view.glide_zoom(2.0, centre)
    spin(0.03)
    app.canvas.translate_items([victim], 300.0, 200.0)
    spin(0.05)
    shot("07_edit_mid_glide")
    view.set_camera(home, zoom0 * 2.0)
    settle()
    shot("08_edit_settled")
    (out / "report.json").write_text(json.dumps(report, indent=1))
    print("DONE", out)
    h.close()


def compare(args: argparse.Namespace) -> None:
    sys.argv = [sys.argv[0]]
    sys.path.insert(0, str(ROOT / "tools"))
    import validate_rhi  # noqa: PLC0415

    ns = argparse.Namespace(a=args.a, b=args.b)
    validate_rhi.compare(ns)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--out", required=True)
    r.add_argument("--board", default="large")
    r.add_argument("--screen", action="store_true", help="also capture the real screen (noisy)")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    args = parser.parse_args()
    run(args) if args.cmd == "run" else compare(args)


if __name__ == "__main__":
    main()
