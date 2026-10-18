"""Load / memory / edit benchmark on boards of big photos.

``python tests/bench_load.py [big] [linked] [many]`` (default: all three).

The costs bench_canvas and bench_interactive don't cover: opening a board
(time to the first frame, and until every image is drawn at full detail),
memory (resident and peak), visiting images at 100%, saving, and edits on a
big board (move one image, move everything, undo).

Boards (generated once, then reused from ``BENCH_CACHE``):

* ``big``    – ``BENCH_IMAGES`` (40) photos of ``BENCH_MP`` (24) megapixels, embedded in the file
* ``linked`` – the same photos, linked from disk
* ``many``   – ``BENCH_MANY`` (1500) small distinct photos plus notes and strokes

Each board runs in its own process so peak memory isn't shared. Timing spans
(``pixelref.core.perf``) are on and printed after each board's results.
``BENCH_OUT=file.jsonl`` appends each board's numbers as one JSON line, to
compare runs before and after a change.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

BOARDS = ("big", "linked", "many")
IMAGES = int(os.environ.get("BENCH_IMAGES", "40"))
MEGAPIXELS = float(os.environ.get("BENCH_MP", "24"))
MANY = int(os.environ.get("BENCH_MANY", "1500"))
CACHE = Path(os.environ.get("BENCH_CACHE", Path.home() / ".cache" / "pixelref-bench"))
VISITS = int(os.environ.get("BENCH_VISITS", "12"))  # images looked at 100% in the zoom sweep


def main(argv: list[str]) -> int:
    boards = [a for a in argv if a in BOARDS] or list(BOARDS)
    if len(boards) > 1 or not os.environ.get("BENCH_CHILD"):
        status = 0
        for board in boards:
            env = dict(os.environ, BENCH_CHILD="1")
            status |= subprocess.run([sys.executable, __file__, board], env=env).returncode
        return status
    return run_board(boards[0])


# ---- Boards ------------------------------------------------------------------------------------


def _noise_tile():
    from PySide6.QtGui import QImage, QPixmap

    side = 512
    image = QImage(os.urandom(side * side * 4), side, side, side * 4, QImage.Format_RGB32).copy()
    return QPixmap.fromImage(image)


def make_photo(folder: Path, index: int, width: int, height: int, noise) -> Path:  # noqa: ANN001
    """A JPEG with photo-like detail (shapes plus grain), so its file size and decode cost are realistic."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage, QPainter

    path = folder / f"photo_{index}_{width}x{height}.jpg"
    if path.exists():
        return path
    rnd = random.Random(index)
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor.fromHsv(rnd.randrange(360), 90, 190))
    painter = QPainter(image)
    for _ in range(60):
        painter.fillRect(rnd.randrange(width), rnd.randrange(height), rnd.randrange(width // 40, width // 4),
                         rnd.randrange(height // 40, height // 4),
                         QColor.fromHsv(rnd.randrange(360), rnd.randrange(255), rnd.randrange(60, 255)))
    painter.setOpacity(0.18)
    painter.drawTiledPixmap(QRect(0, 0, width, height), noise)
    painter.end()
    image.save(str(path), "JPG", 85)
    return path


def board_path(board: str) -> Path:
    if board == "many":
        return CACHE / f"board_many_{MANY}.pxref"
    return CACHE / f"board_{board}_{IMAGES}x{MEGAPIXELS:g}mp.pxref"


def build_board(board: str) -> tuple[Path, list[tuple[str, float, float]]]:
    """The board file (made if missing) and its images' (name, centre x, centre y)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    photos = CACHE / "photos"
    photos.mkdir(exist_ok=True)
    rnd = random.Random(7)
    if board == "many":
        count, width, height = MANY, 640, 480
    else:
        count = IMAGES
        height = int(math.sqrt(MEGAPIXELS * 1_000_000 / 1.5))
        width = int(height * 1.5)
    side = math.ceil(math.sqrt(count))
    gap = max(width, height) * 1.1
    images = [(f"img{i}", (i % side) * gap + width / 2, (i // side) * gap + height / 2) for i in range(count)]
    path = board_path(board)
    if path.exists():
        return path, images

    noise = _noise_tile()
    started = time.perf_counter()
    nodes, assets = [], {}
    for i, (name, cx, cy) in enumerate(images):
        photo = make_photo(photos, i, width, height, noise)
        attrs = {"translate": [cx - width / 2, cy - height / 2]}
        if board == "big":
            data = photo.read_bytes()
            sha = hashlib.sha1(data).hexdigest()
            assets[sha] = {"name": photo.name, "mime": "image/jpeg", "size": len(data), "encoding": "base64",
                           "data": base64.b64encode(data).decode("ascii")}
            attrs["asset"] = sha
        else:
            attrs["path"] = photo.as_posix()
        nodes.append({"name": name, "type": "image", "parent": None, "attrs": attrs})
    if board == "many":
        span = side * gap
        for i in range(MANY // 2):
            x, y = rnd.uniform(0, span), rnd.uniform(0, span)
            if i % 2:
                nodes.append({"name": f"note{i}", "type": "annotation_text", "parent": None,
                              "attrs": {"translate": [x, y], "text": f"Reference {i}\nlight / colour"}})
            else:
                pts = [[j * 8.0, 40 * math.sin(j / 4.0)] for j in range(40)]
                nodes.append({"name": f"stroke{i}", "type": "annotation_path", "parent": None,
                              "attrs": {"translate": [x, y], "points": pts, "thickness": 6.0}})
    payload = {"pixelref_version": "1.0.0", "schema": 1, "nodes": nodes}
    if assets:
        payload["assets"] = assets
    path.write_text(json.dumps(payload), encoding="utf-8")
    print(f"(made {path.name} in {time.perf_counter() - started:.1f} s)", flush=True)
    return path, images


# ---- Measuring ---------------------------------------------------------------------------------


def memory_mb() -> tuple[float, float]:
    """(resident, peak resident) MB of this process."""
    try:
        fields = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
        return int(fields["VmRSS"].split()[0]) / 1024, int(fields["VmHWM"].split()[0]) / 1024
    except (OSError, KeyError, ValueError):
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024
        return float("nan"), peak_mb


def run_board(board: str) -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from harness import APP, TMP, make  # noqa: E402  (configures Qt first)

    from pixelref.builtins.image.items import MIPS
    from pixelref.builtins.image.source import DECODER
    from pixelref.core import perf

    path, images = build_board(board)
    perf.enable(True, slow_ms=0)
    h = make()
    view, canvas, vp = h.view, h.app.canvas, h.view.viewport()
    results: dict[str, float] = {}

    def busy() -> bool:
        tiles = view.tiles
        return bool(canvas._refresh_timer.isActive() or canvas._pending_refresh or MIPS.waiting or DECODER.busy
                    or tiles._idle_timer.isActive() or tiles._settle.isActive())

    def settle(timeout: float = 120.0) -> None:
        """Run the event loop until nothing is pending (refresh, mip chains, tiles), then paint."""
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            APP.processEvents()
            if not busy():
                break
            time.sleep(0.001)
        vp.repaint()

    def note(label: str, value: float, unit: str) -> None:
        results[label] = round(value, 1)
        print(f"  {label:<34} {value:10.1f} {unit}", flush=True)

    def timed_edit(label: str, fn, repeat: int = 5) -> None:  # noqa: ANN001
        samples = []
        for _ in range(repeat):
            start = time.perf_counter()
            fn()
            canvas.flush_pending()
            settle()
            samples.append((time.perf_counter() - start) * 1000)
        note(f"{label} (median)", statistics.median(samples), "ms")
        note(f"{label} (worst)", max(samples), "ms")

    size_mb = path.stat().st_size / 2**20
    print(f"\n== {board}: {path.name} ({len(images)} images, {size_mb:.0f} MB file)", flush=True)
    APP.processEvents()
    base_rss, _ = memory_mb()
    note("memory before open", base_rss, "MB")
    perf.reset()

    # Open: the first frame, then every image drawn at full detail.
    start = time.perf_counter()
    h.run("file.open_path", str(path), confirm=False)
    while canvas._refresh_timer.isActive() or canvas._pending_refresh:
        APP.processEvents()
    APP.processEvents()  # frame_all
    vp.repaint()
    note("open -> first frame", (time.perf_counter() - start) * 1000, "ms")
    settle()
    note("open -> settled", (time.perf_counter() - start) * 1000, "ms")
    rss, peak = memory_mb()
    note("memory after open", rss, "MB")
    note("peak memory during open", peak, "MB")
    opened = perf.report()

    # Look at images at 100% (full resolution drawn), then fit everything again.
    perf.reset()
    start = time.perf_counter()
    for name, cx, cy in images[:VISITS]:
        view.set_camera([cx, cy], 1.0, 0.0)
        settle()
    view.fit_rect(canvas.scene_bounds(canvas.visible_content_items()))
    settle()
    note(f"visit {min(VISITS, len(images))} images at 100%", (time.perf_counter() - start) * 1000, "ms")
    rss, peak = memory_mb()
    note("memory after visits", rss, "MB")
    note("peak memory so far", peak, "MB")
    visited = perf.report()

    # Edits on the whole board.
    perf.reset()
    every = [name for name, _, _ in images]
    timed_edit("move 1 image", lambda: h.run("move", 5.0, 0.0, nodes=[every[0]]))
    timed_edit(f"move all {len(every)} images", lambda: h.run("move", 5.0, 0.0, nodes=every))
    timed_edit("undo", h.undo)
    edited = perf.report()

    # Save.
    perf.reset()
    out = TMP / f"bench_{board}_saved.pxref"
    start = time.perf_counter()
    h.run("file.save_to", str(out))
    note("save", (time.perf_counter() - start) * 1000, "ms")
    note("saved file size", out.stat().st_size / 2**20, "MB")
    saved = perf.report()

    # Open the same board again: memory should come back to about the same level.
    h.run("file.open_path", str(path), confirm=False)
    settle()
    rss_again, peak = memory_mb()
    note("memory after reopening", rss_again, "MB")
    note("peak memory (whole run)", peak, "MB")

    for title, table in (("open", opened), ("visits", visited), ("edits", edited), ("save", saved)):
        print(f"\n  -- spans: {title}\n" + "\n".join("  " + line for line in table.splitlines()))
    if os.environ.get("BENCH_OUT"):
        with open(os.environ["BENCH_OUT"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"board": board, "images": len(images), "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                **results}) + "\n")
    perf.enable(False)
    h.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
