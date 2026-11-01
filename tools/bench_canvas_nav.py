"""Real-time canvas navigation benchmark (investigation tool: no production code is modified).

Unlike ``tests/bench_interactive.py`` (which delivers one event, then ``processEvents()``), this
drives the *real* event loop: synthetic mouse / wheel events are injected at a fixed input rate
from a 1 ms precise timer, so glide timers, coalesced paints, tile idle slices and worker-thread
deliveries all interleave exactly as they do under a real mouse. Everything is measured from
instrumentation patched in at runtime (``TileCache``, ``PixmapItem``, ``CanvasView.paintEvent``).

    python tools/bench_canvas_nav.py env
    python tools/bench_canvas_nav.py run --board medium --scenario pan_zoomed --cache cold
    python tools/bench_canvas_nav.py run --board mixed --scenario zoom --cache warm --views 4 --link
    python tools/bench_canvas_nav.py matrix --out docs/performance/data/baseline.jsonl

``run`` prints one ``RESULT {json}`` line. ``matrix`` runs many configurations, one fresh
process each (clean caches), and appends the results to a JSONL file.

Environment: ``QT_QPA_PLATFORM`` (``windows`` = real display, ``offscreen`` = no vsync / no GL),
``PIXELREF_GL=0`` (force software viewport), ``PIXELREF_PERF_DATA`` (folder for generated boards,
default ``<repo>/../pixelRef-perf-data``), ``BENCH_MAXIMIZED=1`` (default on real platforms).
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("PIXELREF_PERF_DATA", str(ROOT.parent / "pixelRef-perf-data")))

# name -> board recipe. ``size`` is the long edge of each source photo in pixels. Every item is
# scaled to ~1024 scene units wide, so one camera recipe fits every board.
BOARDS = {
    "small": dict(images=20, size=1024),
    "medium": dict(images=200, size=1024),
    "large": dict(images=1000, size=1024),
    "stress": dict(images=5000, size=800),
    "hires": dict(images=150, size=4000),
    "mixed": dict(images=200, size=1024, gifs=30, notes=300, effects=40),
    # diagnostic splits of "mixed": which kind of media costs what
    "gifonly": dict(images=200, size=1024, gifs=30),
    "fxonly": dict(images=200, size=1024, effects=40),
    "notesonly": dict(images=200, size=1024, notes=300),
}
ITEM_UNITS = 1024.0
SPACING = ITEM_UNITS * 1.15
ZOOMED = 0.35  # a "working" zoom: ~25 images visible on a 1600x1000 viewport


# ==============================================================================================
# Board generation (cached on disk, deterministic)
# ==============================================================================================


def _photo(path: Path, long_edge: int, seed: int) -> None:
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter

    rnd = random.Random(seed)
    w, h = rnd.choice([(long_edge, long_edge * 3 // 4), (long_edge * 3 // 4, long_edge),
                       (long_edge, long_edge), (long_edge, long_edge * 9 // 16)])
    image = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(image)
    gradient = QLinearGradient(QPointF(0, 0), QPointF(w, h))
    gradient.setColorAt(0, QColor.fromHsv(rnd.randrange(360), 140, 220))
    gradient.setColorAt(1, QColor.fromHsv(rnd.randrange(360), 160, 90))
    p.fillRect(QRectF(0, 0, w, h), QBrush(gradient))
    p.setPen(Qt.NoPen)
    for _ in range(90):
        c = QColor.fromHsv(rnd.randrange(360), rnd.randrange(255), rnd.randrange(255), rnd.randrange(60, 220))
        p.setBrush(c)
        sw, sh = rnd.randrange(w // 40, w // 5), rnd.randrange(h // 40, h // 5)
        x, y = rnd.randrange(-sw // 2, w), rnd.randrange(-sh // 2, h)
        if rnd.random() < 0.5:
            p.drawEllipse(QRectF(x, y, sw, sh))
        else:
            p.drawRect(QRectF(x, y, sw, sh))
    # Photographic texture: a random noise tile laid over everything (keeps JPEGs realistic in size).
    noise = QImage(bytes(rnd.randbytes(128 * 128 * 4)), 128, 128, QImage.Format_RGB32)
    p.setOpacity(0.18)
    for ty in range(0, h, 128):
        for tx in range(0, w, 128):
            p.drawImage(tx, ty, noise)
    p.end()
    image.save(str(path), "JPEG", 82)


def _board(name: str) -> tuple[Path, dict]:
    spec = BOARDS[name]
    folder = DATA / name
    folder.mkdir(parents=True, exist_ok=True)
    board = folder / "board.pxref"
    meta = folder / "board.meta.json"
    if board.exists() and meta.exists():
        return board, json.loads(meta.read_text())
    sys.path.insert(0, str(ROOT / "tests"))
    from harness import make_gif  # noqa: PLC0415  (tiny literal-LZW GIFs)

    rnd = random.Random(7)
    count = spec["images"]
    side = math.ceil(math.sqrt(count))
    scale = ITEM_UNITS / spec["size"]
    nodes = []
    started = time.perf_counter()
    for i in range(count):
        photo = folder / f"p{i:05d}.jpg"
        if not photo.exists():
            _photo(photo, spec["size"], i)
        x = (i % side) * SPACING + rnd.uniform(-60, 60)
        y = (i // side) * SPACING + rnd.uniform(-60, 60)
        attrs = {"translate": [x, y], "rotate": rnd.choice([0.0, 0.0, 0.0, 8.0, -12.0]),
                 "scale": [scale, scale], "path": photo.as_posix()}
        n_fx = spec.get("effects", 0)
        if i < n_fx // 2:
            attrs["effects"] = [{"id": f"fx_g{i:04d}", "type": "grayscale", "enabled": 1, "mix": 1.0, "params": {}}]
        elif i < n_fx:
            attrs["effects"] = [{"id": f"fx_b{i:04d}", "type": "blur", "enabled": 1, "mix": 1.0,
                                 "params": {"radius": 6.0}}]
        nodes.append({"name": f"img{i}", "type": "image", "parent": None, "attrs": attrs})
        if i and i % 500 == 0:
            print(f"  generated {i}/{count} photos ({time.perf_counter() - started:.0f} s)", file=sys.stderr)
    gifs = spec.get("gifs", 0)
    if gifs:
        files = [make_gif(f"anim{k}.gif", 192, 108, [(200, 40, 40), (40, 200, 40), (40, 40, 200), (230, 230, 40)])
                 for k in range(4)]
        for k in range(gifs):
            keep = DATA / name / f"anim{k % 4}.gif"
            keep.write_bytes(files[k % 4].read_bytes())
            cell = rnd.randrange(count)  # sits among the photos
            x = (cell % side) * SPACING + SPACING * 0.5
            y = (cell // side) * SPACING + SPACING * 0.5
            nodes.append({"name": f"gif{k}", "type": "image", "parent": None,
                          "attrs": {"translate": [x, y], "scale": [3.0, 3.0], "path": keep.as_posix()}})
    notes = spec.get("notes", 0)
    for i in range(notes):
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
    board.write_text(json.dumps({"pixelref_version": "1.0.0", "schema": 1, "nodes": nodes}), encoding="utf-8")
    info = {"extent": side * SPACING, "side": side, "nodes": len(nodes), "spacing": SPACING,
            "disk_mb": round(sum(f.stat().st_size for f in folder.glob("*.jpg")) / 1e6, 1)}
    meta.write_text(json.dumps(info))
    print(f"board '{name}': {len(nodes)} nodes, {info['disk_mb']} MB of photos, "
          f"{time.perf_counter() - started:.0f} s", file=sys.stderr)
    return board, info


# ==============================================================================================
# Small helpers
# ==============================================================================================


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(q / 100.0 * len(s)) - 1))]


def dist(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean": round(statistics.fmean(values), 2), "p50": round(pct(values, 50), 2),
            "p95": round(pct(values, 95), 2), "p99": round(pct(values, 99), 2), "max": round(max(values), 2)}


def rss_mb() -> tuple[float, float]:
    """(working set, peak working set) in MB."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.WorkingSetSize / 1048576, pmc.PeakWorkingSetSize / 1048576
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak = peak / 1048576 if sys.platform == "darwin" else peak / 1024
    return peak, peak


def environment() -> dict:
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import qVersion
    from PySide6.QtGui import QGuiApplication

    from pixelref.ui.canvas import gpu
    from pixelref.ui.canvas.view import frame_interval_ms

    screen = QGuiApplication.primaryScreen()
    gpu.gl_available()
    return {
        "os": platform.platform(), "python": platform.python_version(), "pyside6": pyside_version, "qt": qVersion(),
        "qpa": QGuiApplication.platformName(), "cpu": platform.processor(), "cores": os.cpu_count(),
        "gl_renderer": gpu.renderer or "(none: software viewport)", "gl_used": bool(gpu._available),
        "screen": {"name": screen.name(), "size": [screen.size().width(), screen.size().height()],
                   "refresh_hz": screen.refreshRate(), "dpr": screen.devicePixelRatio()},
        "glide_interval_ms": frame_interval_ms(),
    }


# ==============================================================================================
# Instrumentation (patched in at runtime)
# ==============================================================================================


class Recorder:
    """Everything the patched code reports. ``on`` gates recording to the measured window."""

    def __init__(self) -> None:
        self.on = False
        self.reset()

    def reset(self) -> None:
        self.frames: list[tuple] = []  # (view, start, end, paint_ms, visible, sync_tiles, sync_ms, fallback, blank, mag)
        self.swaps: list[float] = []
        self.handler_ms: list[tuple[float, float, str]] = []  # (t_inject, ms, kind)
        self.tick_gaps: list[float] = []
        self.idle_slices: list[tuple[float, float, int, bool]] = []  # (t, ms, tiles, during_input)
        self.invalidations = 0
        self.invalidated_tiles = 0
        self.deliveries = 0
        self.bake = {"exact": 0, "blurry": 0, "finer": 0, "placeholder": 0}
        self.ctx = ""
        self.cur: dict | None = None
        self.last_input = -1.0
        self.sync_render_calls: list[float] = []
        self.extra: dict = {}


REC = Recorder()


def install(variant: str, overrides: list[str]) -> None:
    from pixelref.builtins.image import items as image_items
    from pixelref.builtins.image.source import ImageHandle, level_for
    from pixelref.ui.canvas import tiles
    from pixelref.ui.canvas.view import CanvasView

    now = time.perf_counter

    # ---- frames
    original_paint_event = CanvasView.paintEvent

    def paint_event(self, event):  # noqa: ANN001
        t0 = now()
        original_paint_event(self, event)
        t1 = now()
        if REC.on:
            cur = REC.cur or {}
            REC.frames.append((self.view_name, t0, t1, (t1 - t0) * 1000.0, cur.get("visible", 0),
                               cur.get("sync", 0), cur.get("sync_ms", 0.0), cur.get("fallback", 0),
                               cur.get("blank", 0), cur.get("mag", 1.0)))
        REC.cur = None

    CanvasView.paintEvent = paint_event

    # ---- tile cache
    TC = tiles.TileCache
    o_paint, o_run_now, o_visible, o_fallbacks, o_idle, o_invalidate = (
        TC.paint, TC._render_run_now, TC._visible_keys, TC._paint_fallbacks, TC._idle, TC.invalidate)

    def t_paint(self, painter):
        REC.ctx, REC.cur = "paint", {"visible": 0, "sync": 0, "sync_ms": 0.0, "fallback": 0, "blank": 0}
        try:
            o_paint(self, painter)
        finally:
            REC.ctx = ""

    def t_run_now(self, level, i0, j, count):
        t0 = now()
        o_run_now(self, level, i0, j, count)
        ms = (now() - t0) * 1000.0
        if REC.ctx == "paint" and REC.cur is not None:
            REC.cur["sync"] += count
            REC.cur["sync_ms"] += ms
        elif REC.ctx == "idle" and REC.cur is not None:
            REC.cur["tiles"] = REC.cur.get("tiles", 0) + count

    def t_visible(self, level, margin=0):
        keys = o_visible(self, level, margin)
        if REC.ctx == "paint" and margin == 0 and REC.cur is not None and not REC.cur["visible"]:
            REC.cur["visible"] = len(keys)
        return keys

    def t_fallbacks(self, painter, level, keys, fallbacks):
        if REC.cur is not None:
            REC.cur["fallback"] += len(keys)
            have = [lv for lv in fallbacks if lv.tiles]
            if not have:
                REC.cur["blank"] += len(keys)
            else:  # magnification of the closest fallback level (1.0 = same zoom, 8 = drawn 8x too small)
                best = min((level.zoom / lv.zoom for lv in have), key=lambda m: abs(math.log(m)))
                REC.cur["mag"] = max(REC.cur.get("mag", 1.0), best)
        return o_fallbacks(self, painter, level, keys, fallbacks)

    def t_idle(self):
        REC.ctx, REC.cur = "idle", {"tiles": 0}
        t0 = now()
        try:
            o_idle(self)
        finally:
            ms = (now() - t0) * 1000.0
            if REC.on and REC.cur and REC.cur.get("tiles"):
                REC.idle_slices.append((t0, ms, REC.cur["tiles"], t0 - REC.last_input < 0.15))
            REC.ctx, REC.cur = "", None

    def t_invalidate(self, rects):
        before = self.tile_count
        o_invalidate(self, rects)
        if REC.on:
            REC.invalidations += 1
            REC.invalidated_tiles += max(0, before - self.tile_count)

    TC.paint, TC._render_run_now, TC._visible_keys = t_paint, t_run_now, t_visible
    TC._paint_fallbacks, TC._idle, TC.invalidate = t_fallbacks, t_idle, t_invalidate

    # ---- image quality baked into tiles
    PI = image_items.PixmapItem
    o_draw = PI._draw_handle

    def draw_handle(self, painter, handle, on_screen=True):
        if REC.on and not self.final_render():
            want = level_for(image_items.device_scale(painter), handle.max_level)
            found = handle.best(want)
            if found is None:
                REC.bake["placeholder"] += 1
            elif found[0] == want:
                REC.bake["exact"] += 1
            elif found[0] > want:
                REC.bake["blurry"] += 1
            else:
                REC.bake["finer"] += 1
        return o_draw(self, painter, handle, on_screen)

    PI._draw_handle = draw_handle
    o_deliver = ImageHandle.deliver

    def deliver(self, level, image):
        if REC.on:
            REC.deliveries += 1
        return o_deliver(self, level, image)

    ImageHandle.deliver = deliver

    # ---- constant overrides: --set tiles.PREFETCH=2
    for item in overrides:
        key, value = item.split("=", 1)
        module, attr = key.split(".", 1)
        target = {"tiles": tiles, "view": sys.modules["pixelref.ui.canvas.view"]}[module]
        setattr(target, attr, type(getattr(target, attr))(float(value)) if not isinstance(getattr(target, attr), str)
                else value)

    if variant != "baseline":
        import bench_variants  # noqa: PLC0415  (prototype strategies, isolated from production code)

        bench_variants.apply(variant, tiles, REC)


# ==============================================================================================
# Scenarios: lists of (time offset s, kind, callable)
# ==============================================================================================


def build_events(name: str, ctx: dict) -> tuple[list, float]:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent, QWheelEvent
    from PySide6.QtWidgets import QApplication

    view, vp = ctx["view"], ctx["view"].viewport()
    centre = QPointF(vp.width() / 2, vp.height() / 2)
    ev: list[tuple[float, str, object]] = []
    hz = ctx["input_hz"]

    def mouse(kind, pos, buttons):
        button = Qt.MiddleButton if kind != QEvent.MouseMove else Qt.NoButton
        event = QMouseEvent(kind, pos, vp.mapToGlobal(pos), button, buttons, Qt.NoModifier)
        return lambda: QApplication.sendEvent(vp, event)

    def wheel(steps, pos):
        event = QWheelEvent(pos, vp.mapToGlobal(pos), QPoint(), QPoint(0, round(120 * steps)), Qt.NoButton,
                            Qt.NoModifier, Qt.NoScrollPhase, False)
        return lambda: QApplication.sendEvent(vp, event)

    if name in ("pan", "pan_fit", "pan_zoomed"):
        speed = ctx["pan_speed"]  # px/s
        dur = ctx["pan_seconds"]
        step = speed / hz
        ev.append((0.0, "press", mouse(QEvent.MouseButtonPress, QPointF(centre), Qt.MiddleButton)))
        pos = QPointF(centre)
        n = int(dur * hz)
        for k in range(1, n + 1):
            sign = 1.0 if k <= n // 2 else -1.0  # reverse direction half-way
            pos = pos + QPointF(0.8 * step * sign, 0.6 * step * sign)
            ev.append((k / hz, "move", mouse(QEvent.MouseMove, QPointF(pos), Qt.MiddleButton)))
        ev.append((dur + 1.0 / hz, "release", mouse(QEvent.MouseButtonRelease, QPointF(pos), Qt.NoButton)))
        return ev, dur + 0.05
    if name in ("zoom", "zoom_notch"):
        notches, rate = ctx["zoom_notches"], ctx["zoom_rate"]
        t = 0.0
        for k in range(notches):  # in toward the cursor
            ev.append((t, "wheel", wheel(1, QPointF(centre.x() + 120, centre.y() - 60))))
            t += 1.0 / rate
        t += 0.6
        for k in range(notches):  # and out
            ev.append((t, "wheel", wheel(-1, QPointF(centre.x() + 120, centre.y() - 60))))
            t += 1.0 / rate
        return ev, t + 0.3
    if name == "zoom_trackpad":  # fractional steps at a high event rate
        total, t = 24, 0.0
        steps_per_event = 0.1
        for k in range(int(total / steps_per_event)):
            ev.append((t, "wheel", wheel(steps_per_event, QPointF(centre))))
            t += 1.0 / 120.0
        t += 0.4
        for k in range(int(total / steps_per_event)):
            ev.append((t, "wheel", wheel(-steps_per_event, QPointF(centre))))
            t += 1.0 / 120.0
        return ev, t + 0.3
    if name in ("jump", "jump_animated", "frame_toggle"):
        from PySide6.QtCore import QPointF as P

        rnd = random.Random(11)
        extent = ctx["extent"]
        gap = 1.0 if name != "jump" else 0.8
        t = 0.0
        for k in range(8):
            if name == "frame_toggle":
                if k % 2 == 0:
                    fn = (lambda: view.fit_rect(ctx["bounds"]))
                else:
                    c = P(rnd.uniform(0.1, 0.9) * extent, rnd.uniform(0.1, 0.9) * extent)
                    fn = (lambda c=c: view.set_camera([c.x(), c.y()], ZOOMED))
            else:
                c = P(rnd.uniform(0.1, 0.9) * extent, rnd.uniform(0.1, 0.9) * extent)
                animate = name == "jump_animated"
                fn = (lambda c=c, a=animate: view.set_camera([c.x(), c.y()], ZOOMED, animate=a))
            ev.append((t, "jump", fn))
            t += gap
        return ev, t + 0.2
    if name == "idle":
        return [], 2.0
    raise SystemExit(f"unknown scenario {name}")


def prepare_camera(name: str, ctx: dict) -> None:
    view = ctx["view"]
    if name in ("pan_zoomed", "pan", "jump", "jump_animated"):
        c = ctx["extent"] / 2
        view.set_camera([c, c], ZOOMED)
    else:  # fit everything (zoom scenarios start zoomed out, as a user landing on a board does)
        view.fit_rect(ctx["bounds"])


# ==============================================================================================
# Quality probe
# ==============================================================================================


def quality(view, with_overview: bool = True) -> tuple[int, int, int]:
    """(visible tiles missing, ring tiles missing, background jobs pending) for the view's current level."""
    from pixelref.builtins.image.source import DECODER
    from pixelref.builtins.image.items import MIPS
    from pixelref.ui.canvas import tiles as tilemod

    cache = view.tiles
    ov = getattr(cache, "_ov", None)  # prototype overview level (bench_variants): unfinished = pending work
    ov_missing = sum(1 for k in cache._ov_keys if k not in ov.tiles) if (ov is not None and with_overview) else 0
    zoom = view.viewportTransform().m11()
    key = (round(zoom, 9), round(view.devicePixelRatioF(), 4))
    level = cache.levels.get(key)
    if level is None:
        n = len(cache._visible_keys(tilemod.Level(zoom, view.devicePixelRatioF())))
        return n, n, DECODER.pending + len(MIPS.waiting) + ov_missing
    vis = sum(1 for k in cache._visible_keys(level) if k not in level.tiles)
    ring = sum(1 for k in cache._visible_keys(level, tilemod.PREFETCH) if k not in level.tiles)
    pending = DECODER.pending + len(MIPS.waiting) + ov_missing
    timers = 1 if (cache._settle.isActive()) else 0
    return vis + timers, ring + (1 if cache._idle_timer.isActive() else 0) + timers, pending


# ==============================================================================================
# Runner
# ==============================================================================================


def run(args: argparse.Namespace) -> dict:
    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(0, str(ROOT / "tools"))
    sys.path.insert(0, str(ROOT))
    if args.perf_spans:
        os.environ["PIXELREF_PERF"] = "1"
        os.environ["PIXELREF_PERF_SLOW"] = "0"
    os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
    from harness import APP, make  # noqa: PLC0415
    from PySide6.QtCore import QEventLoop, QTimer, Qt

    board, info = _board(args.board)
    install(args.variant, args.set)
    real = APP.platformName() not in ("offscreen", "minimal")
    h = make()
    if real:
        h.window.showMaximized()
        for _ in range(20):
            APP.processEvents()
    app = h.app
    t_open = time.perf_counter()
    app.run_command("file.open_path", board.as_posix(), confirm=False)
    app.canvas.flush_pending()
    APP.processEvents()
    open_ms = (time.perf_counter() - t_open) * 1000.0
    view = h.view
    views = [view]
    for i in range(1, args.views):
        name = app.run_command("wm.split_view", "right" if i % 2 else "down", "Main")
        views.append(app.views[name])
    for _ in range(10):
        APP.processEvents()
    if args.link and args.views > 1:
        for v in views:
            v.link_group = "bench"
    canvas = app.canvas
    bounds = canvas.scene_bounds(canvas.visible_content_items())
    from pixelref.ui.canvas import tiles as tilemod
    from pixelref.builtins.image.source import DECODER, STORE
    from pixelref.builtins.image.items import MIPS

    refresh = APP.primaryScreen().refreshRate() or 60.0
    ctx = {"view": view, "extent": info["extent"], "bounds": bounds, "input_hz": args.input_hz,
           "pan_speed": args.pan_speed, "pan_seconds": args.seconds, "zoom_notches": args.notches,
           "zoom_rate": args.zoom_rate}
    swap_view = view
    if hasattr(view.viewport(), "frameSwapped"):
        view.viewport().frameSwapped.connect(lambda: REC.swaps.append(time.perf_counter()) if REC.on else None)

    def spin(seconds: float) -> None:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            APP.processEvents()

    def settle(timeout: float = 30.0) -> float:
        """Wait until the view is fully refined (visible + ring) and background work is done."""
        t0 = time.perf_counter()
        stable = 0
        while time.perf_counter() - t0 < timeout:
            APP.processEvents()
            time.sleep(0.002)
            v, r, p = quality(view)
            stable = stable + 1 if (v == 0 and r == 0 and p == 0) else 0
            if stable >= 5:
                break
        return time.perf_counter() - t0

    def one_pass(measure: bool) -> dict:
        REC.reset()
        prepare_camera(args.scenario, ctx)
        APP.processEvents()
        if args.cache in ("warm", "settled") or not measure:
            settle()  # settled: the start camera is fully rendered, but nothing else is cached
        events, duration = build_events(args.scenario, ctx)
        loop = QEventLoop()
        state = {"idx": 0, "last": time.perf_counter(), "t0": 0.0, "jump_q": [], "jump_marks": []}
        timer = QTimer()
        timer.setTimerType(Qt.PreciseTimer)
        timer.setInterval(1)
        cpu0, th0, wall0 = time.process_time(), time.thread_time(), time.perf_counter()
        REC.on = measure
        state["t0"] = time.perf_counter()
        probe_every = 4
        ticks = [0]

        def tick() -> None:
            t = time.perf_counter()
            if measure:
                REC.tick_gaps.append((t - state["last"]) * 1000.0)
            state["last"] = t
            elapsed = t - state["t0"]
            while state["idx"] < len(events) and events[state["idx"]][0] <= elapsed:
                when, kind, fn = events[state["idx"]]
                state["idx"] += 1
                REC.last_input = time.perf_counter()
                h0 = time.perf_counter()
                fn()
                if measure:
                    REC.handler_ms.append((h0, (time.perf_counter() - h0) * 1000.0, kind))
                if kind == "jump":
                    state["jump_q"].append({"t": h0, "first": None, "exact": None, "stable": 0, "i": state["idx"]})
            # per-jump: time to first frame after the call, and to full quality
            ticks[0] += 1
            if state["jump_q"] and ticks[0] % probe_every == 0:
                for jq in state["jump_q"]:
                    if jq["exact"] is not None:
                        continue
                    if jq["first"] is None:
                        later = [f for f in REC.frames if f[2] >= jq["t"] and f[0] == view.view_name]
                        if later:
                            jq["first"] = later[0][2] - jq["t"]
                    v, r, p = quality(view, False)
                    nxt = state["idx"] < len(events) and events[state["idx"]][0] <= elapsed + 0.05
                    if v == 0 and p == 0 and not (view._animation is not None and view._animation.state() == 2):
                        jq["stable"] += 1
                        if jq["stable"] >= 3:
                            jq["exact"] = time.perf_counter() - jq["t"]
                    else:
                        jq["stable"] = 0
            if elapsed >= duration:
                timer.stop()
                loop.quit()

        timer.timeout.connect(tick)
        timer.start()
        loop.exec()
        wall = time.perf_counter() - wall0
        cpu, th = time.process_time() - cpu0, time.thread_time() - th0
        REC.on = False
        active_end = time.perf_counter()
        # return to idle: how long until the view is fully refined?
        REC.on = measure
        refine_start = time.perf_counter()
        frames_before = len(REC.frames)
        stable_v = stable_all = 0
        t_visible = t_all = None
        pre = len(REC.frames)
        while time.perf_counter() - refine_start < args.refine_timeout:
            APP.processEvents()
            time.sleep(0.002)
            v, r, p = quality(view, False)  # visible-tile quality: the prototype overview is background work
            stable_v = stable_v + 1 if (v == 0 and p == 0) else 0
            stable_all = stable_all + 1 if (v == 0 and r == 0 and p == 0) else 0
            if t_visible is None and stable_v >= 5:
                t_visible = time.perf_counter() - refine_start
            if stable_all >= 5:
                t_all = time.perf_counter() - refine_start
                break
        REC.on = False
        return {"events": events, "duration": duration, "wall": wall, "cpu": cpu, "thread": th,
                "refine_visible_s": t_visible, "refine_all_s": t_all, "jumps": state["jump_q"],
                "refine_frames": len(REC.frames) - pre}

    if args.cache == "warm":
        one_pass(measure=False)  # populate tiles, decoded levels, mips, effect results
    mem0 = rss_mb()
    result = one_pass(measure=True)

    # ---- reduce
    frames = [f for f in REC.frames if f[0] == view.view_name]
    all_paints = len(REC.frames)
    period = 1000.0 / refresh
    active = [f for f in frames if f[1] <= 0 or True]
    ends = [f[2] for f in frames]
    # Presentation times: GL frame swaps when available, else the paint-end times.
    present = REC.swaps[:] if REC.swaps else ends
    intervals = [(b - a) * 1000.0 for a, b in zip(present, present[1:])]
    t0_active = result["events"][0][0] if result["events"] else 0.0
    paint_ms = [f[3] for f in frames]
    handler = [m for _t, m, kind in REC.handler_ms]
    # input -> display: first presentation after each injected event
    lat = []
    sorted_present = sorted(present)
    for t, m, kind in REC.handler_ms:
        k = bisect.bisect_left(sorted_present, t + m / 1000.0)
        if k < len(sorted_present):
            lat.append((sorted_present[k] - t) * 1000.0)
    # Intervals that start while input (or its glide tail) is active: excludes the scenario's deliberate pauses.
    input_times = sorted(t for t, _m, _k in REC.handler_ms)
    pres_sorted = sorted(present)
    active_iv = []
    for a, b in zip(pres_sorted, pres_sorted[1:]):
        k = bisect.bisect_right(input_times, a) - 1
        if k >= 0 and a - input_times[k] < 0.30:
            active_iv.append((b - a) * 1000.0)
    gaps = REC.tick_gaps
    blocked = [g for g in gaps if g > 8.0]
    dropped = sum(max(0, round(i / period) - 1) for i in intervals if i > 1.5 * period)
    vis = sum(f[4] for f in frames)
    sync_tiles = sum(f[5] for f in frames)
    fb_tiles = sum(f[7] for f in frames)
    blank = sum(f[8] for f in frames)
    mags = [f[9] for f in frames if f[7]]
    cache = view.tiles
    mem1 = rss_mb()
    perf_text = ""
    if args.perf_spans:
        from pixelref.core import perf
        perf_text = perf.report()
    total_bake = sum(REC.bake.values()) or 1
    out = {
        "config": {k: getattr(args, k) for k in ("board", "scenario", "cache", "views", "link", "variant", "set",
                                                 "input_hz", "pan_speed", "seconds", "notches", "zoom_rate")},
        "frames": {"painted": len(frames), "paints_all_views": all_paints,
                   "active_s": round(result["duration"], 2),
                   "fps_paint": round(len(frames) / max(result["duration"], 1e-9), 1),
                   "present_interval_ms": dist(intervals), "present_interval_active_ms": dist(active_iv),
                   "active_late": sum(1 for i in active_iv if i > period * 1.01),
                   "active_gt_2x": sum(1 for i in active_iv if i > period * 2.0),
                   "active_gt_4x": sum(1 for i in active_iv if i > period * 4.0),
                   "dropped_frames": dropped,
                   "intervals_gt_budget": sum(1 for i in intervals if i > period * 1.01),
                   "intervals_gt_2x": sum(1 for i in intervals if i > period * 2.0),
                   "paint_ms": dist(paint_ms), "paint_gt_budget": sum(1 for p in paint_ms if p > period)},
        "input": {"events": len(result["events"]), "handler_ms": dist(handler),
                  "latency_ms": dist(lat), "events_per_paint": round(len(result["events"]) / max(1, len(frames)), 2)},
        "gui_thread": {"tick_gap_ms": dist(gaps), "gaps_gt_16": sum(1 for g in gaps if g > 16.7),
                       "gaps_gt_33": sum(1 for g in gaps if g > 33.4), "gaps_gt_50": sum(1 for g in gaps if g > 50),
                       "blocked_ms_total": round(sum(g - 1 for g in blocked), 1),
                       "main_thread_cpu_share": round(result["thread"] / max(result["wall"], 1e-9), 3),
                       "process_cores_busy": round(result["cpu"] / max(result["wall"], 1e-9), 2)},
        "tiles": {"visible_tiles_painted": vis, "hit": vis - sync_tiles - fb_tiles, "sync_rendered": sync_tiles,
                  "fallback": fb_tiles, "blank": blank,
                  "hit_rate": round((vis - sync_tiles - fb_tiles) / max(1, vis), 4),
                  "sync_render_ms_total": round(sum(f[6] for f in frames), 1),
                  "frames_with_sync_render": sum(1 for f in frames if f[5]),
                  "frames_with_fallback": sum(1 for f in frames if f[7]),
                  "fallback_magnification": dist(mags),
                  "frames_visibly_blurry": sum(1 for m in mags if m > 1.25 or m < 0.8),
                  "worst_frame_sync_ms": round(max([f[6] for f in frames] or [0]), 1),
                  "idle_slices": len(REC.idle_slices),
                  "idle_slices_during_input": sum(1 for s in REC.idle_slices if s[3]),
                  "idle_slice_ms": dist([s[1] for s in REC.idle_slices]),
                  "idle_slice_ms_during_input": dist([s[1] for s in REC.idle_slices if s[3]]),
                  "invalidations": REC.invalidations, "invalidated_tiles": REC.invalidated_tiles,
                  "tile_count_end": cache.tile_count, "levels_end": len(cache.levels)},
        "images": {"deliveries": REC.deliveries, "bake": dict(REC.bake),
                   "bake_exact_share": round(REC.bake["exact"] / total_bake, 3),
                   "bake_placeholder": REC.bake["placeholder"], "bake_blurry": REC.bake["blurry"]},
        "refine": {"visible_s": result["refine_visible_s"], "all_s": result["refine_all_s"],
                   "frames_during": result["refine_frames"]},
        "memory": {"tile_mb_all_views": round(sum(v.tiles.tile_count for v in views) * tilemod.TILE_BYTES / 1048576, 1),
                   "image_store_mb": round(STORE.bytes / 1048576, 1), "mip_mb": round(MIPS.bytes / 1048576, 1),
                   "rss_mb_start": round(mem0[0], 1), "rss_mb_end": round(mem1[0], 1),
                   "rss_peak_mb": round(mem1[1], 1)},
        "board": {"items": len(canvas.items), "open_ms": round(open_ms), "viewport": [view.viewport().width(),
                                                                                       view.viewport().height()],
                  "viewport_class": type(view.viewport()).__name__, "views": len(views),
                  "uses_tiles": view.uses_tiles()},
        "budget_ms": round(period, 2),
    }
    if result["jumps"]:
        out["jumps"] = {"first_frame_ms": dist([j["first"] * 1000 for j in result["jumps"] if j["first"] is not None]),
                        "full_quality_ms": dist([j["exact"] * 1000 for j in result["jumps"] if j["exact"] is not None]),
                        "never_full": sum(1 for j in result["jumps"] if j["exact"] is None)}
    if REC.extra:
        out["extra"] = dict(REC.extra)
    if perf_text:
        out["perf_spans"] = perf_text
    h.close()
    return out


# ==============================================================================================
# Matrix
# ==============================================================================================


def matrix(args: argparse.Namespace) -> None:
    configs = json.loads(Path(args.configs).read_text()) if args.configs else []
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for i, cfg in enumerate(configs):
        cmd = [sys.executable, "-I", str(Path(__file__).resolve()), "run"]
        for k, v in cfg.items():
            if k == "env":
                continue
            flag = "--" + k.replace("_", "-")
            if v is True:
                cmd.append(flag)
            elif v is False or v is None:
                continue
            elif isinstance(v, list):
                for x in v:
                    cmd += [flag, str(x)]
            else:
                cmd += [flag, str(v)]
        e = dict(env)
        e.update({k: str(v) for k, v in cfg.get("env", {}).items()})
        print(f"[{i + 1}/{len(configs)}] {' '.join(cmd[4:])} {cfg.get('env', '')}", flush=True)
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True, env=e, timeout=args.timeout)
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")), None)
        if line is None:
            print("  FAILED:", (proc.stderr or proc.stdout)[-600:], flush=True)
            continue
        data = json.loads(line[7:])
        data["env_vars"] = cfg.get("env", {})
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data) + "\n")
        print(f"  ok in {time.perf_counter() - t0:.0f} s", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("env")
    gen = sub.add_parser("generate", help="build the cached boards")
    gen.add_argument("boards", nargs="*", default=list(BOARDS))
    r = sub.add_parser("run")
    r.add_argument("--board", default="medium", choices=list(BOARDS))
    r.add_argument("--scenario", default="pan_zoomed")
    r.add_argument("--cache", default="cold", choices=["cold", "settled", "warm"])
    r.add_argument("--views", type=int, default=1)
    r.add_argument("--link", action="store_true")
    r.add_argument("--variant", default="baseline")
    r.add_argument("--set", action="append", default=[], help="override a constant, e.g. tiles.PREFETCH=2")
    r.add_argument("--input-hz", type=float, default=125.0)
    r.add_argument("--pan-speed", type=float, default=1200.0, help="px/s")
    r.add_argument("--seconds", type=float, default=5.0)
    r.add_argument("--notches", type=int, default=30)
    r.add_argument("--zoom-rate", type=float, default=25.0, help="notches/s")
    r.add_argument("--refine-timeout", type=float, default=30.0)
    r.add_argument("--perf-spans", action="store_true")
    m = sub.add_parser("matrix")
    m.add_argument("--configs", required=True, help="JSON file: list of run configurations")
    m.add_argument("--out", required=True)
    m.add_argument("--timeout", type=int, default=420)
    args = parser.parse_args()
    if args.cmd == "env":
        sys.path[:0] = [str(ROOT / "tests"), str(ROOT)]
        os.environ.setdefault("QT_QPA_PLATFORM", "windows" if os.name == "nt" else "offscreen")
        from harness import APP  # noqa: F401, PLC0415

        print(json.dumps(environment(), indent=2))
    elif args.cmd == "generate":
        sys.path[:0] = [str(ROOT / "tests"), str(ROOT)]
        from harness import APP  # noqa: F401, PLC0415

        for name in args.boards:
            _board(name)
    elif args.cmd == "run":
        print("RESULT " + json.dumps(run(args)))
    else:
        matrix(args)


if __name__ == "__main__":
    main()
