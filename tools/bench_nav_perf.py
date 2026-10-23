"""Benchmark for the canvas-navigation performance findings.

Exercises the real hot-path code (pan_by / _camera_moved / center() /
zoom_level() / camera_changed fan-out) headlessly and measures:

  F1  per-pan-pixel cost of the camera_changed listener fan-out
      (navigation ZoomBlock.update_zoom -> target_view)
  F2  wheel-glide timer rate: 8 ms vs 16 ms steps vs a 60 Hz display
  F3  per-pan-pixel cost of _camera_moved's center()/zoom_level() read-back
  F5  per-pan-pixel cost of _stop_animation()

Run:  venv\\Scripts\\python.exe tools\\bench_nav_perf.py
"""
import math
import os
import statistics
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsView, QGraphicsScene  # noqa: E402

app = QApplication([])


def bench(fn, n=2000, warmup=200):
    for _ in range(warmup):
        fn()
    best = []
    for _ in range(5):
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        best.append((time.perf_counter() - t0) / n * 1e6)  # us/op
    return min(best), statistics.median(best)


# ---------------------------------------------------------------------------
# A minimal view that replicates the EXACT hot-path code from
# pixelref/ui/canvas/view.py (pan_by / _camera_moved / _stop_animation).
# ---------------------------------------------------------------------------
class MiniView(QGraphicsView):
    camera_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self._animation = None
        self._glide_timer = QTimer(self)
        self._glide_target = None
        self._gray_cache = None
        self._gray_cache_key = None
        self._camera_center = QPointF(0, 0)
        self._camera_zoom = 1.0
        self._applying_camera = False
        self._pending_camera = None
        self.setSceneRect(-1_000_000, -1_000_000, 2_000_000, 2_000_000)
        self.resize(1600, 900)

    # --- verbatim from view.py:367 ---
    def _stop_animation(self) -> None:
        if self._animation is not None:
            self._animation.stop()
        self._glide_timer.stop()
        self._glide_target = None

    def pan_by(self, delta: QPointF) -> None:
        self._stop_animation()
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        hbar.setValue(hbar.value() - round(delta.x()))
        vbar.setValue(vbar.value() - round(delta.y()))
        self._camera_moved(sync_state=True)

    def _camera_moved(self, *_args, sync_state: bool = False) -> None:
        if (sync_state or _args) and not self._applying_camera:
            self._camera_center = self.center()
            self._camera_zoom = self.zoom_level()
        self._gray_cache = None
        self._gray_cache_key = None
        self.camera_changed.emit()

    # --- verbatim from view.py:237 ---
    def to_scene(self, pos):
        inverse, _ok = self.viewportTransform().inverted()
        return inverse.map(QPointF(pos))

    # --- verbatim from view.py:284 ---
    def zoom_level(self):
        t = self.transform()
        return math.hypot(t.m11(), t.m12())

    # --- verbatim from view.py:292 ---
    def center(self):
        if self._pending_camera is not None:
            return QPointF(self._pending_camera[0])
        return self.to_scene(QRectF(self.viewport().rect()).center())

    # --- measurement hooks ---
    def center_only(self):
        return self.center()

    def zoom_only(self):
        return self.zoom_level()

    def emit_only(self):
        self.camera_changed.emit()


view = MiniView()

# ---------------------------------------------------------------------------
# F1: the real navigation ZoomBlock code, bound to a mock ui/app.
# ---------------------------------------------------------------------------
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "nav_mod", os.path.join(os.path.dirname(__file__), "..", "pixelref", "builtins", "navigation", "__init__.py"))
nav = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nav)


class MockLabel:
    def __init__(self):
        self._text = ""
        self.calls = 0

    def setText(self, t):
        self.calls += 1
        self._text = t


class MockView:
    def __init__(self, name):
        self.view_name = name
        self._z = 1.0

    def zoom_level(self):
        return self._z


class MockDoc:
    def has(self, name):
        return name in ("v1", "v2", "v3")

    def workspace_of(self, name):
        return "ws"

    def output_showing(self, ws):
        return "outA"


class MockWM:
    def __init__(self):
        self.doc = MockDoc()
        self._visible = ["v1", "v2", "v3"]

    def visible_tiles(self):
        return list(self._visible)


class MockApp:
    def __init__(self):
        self.views = {"v1": MockView("v1"), "v2": MockView("v2"), "v3": MockView("v3")}
        self.wm = MockWM()
        self._active = self.views["v1"]

    def active_view(self):
        return self._active


class Block:
    """ZoomBlock with the REAL target_view/update_zoom code from navigation."""
    def __init__(self, output):
        self.ui = type("U", (), {"app": MockApp()})()
        self.output = output
        self.zoom_label = MockLabel()

    target_view = nav.ZoomBlock.target_view
    update_zoom = nav.ZoomBlock.update_zoom


block = Block("outA")

# ---------------------------------------------------------------------------
# Run benchmarks
# ---------------------------------------------------------------------------
print("=" * 72)
print("Canvas navigation hot-path benchmark (offscreen, 1600x900 viewport)")
print("=" * 72)

# F3: per-pan-pixel read-back cost
t_center, _ = bench(view.center_only)
t_zoom, _ = bench(view.zoom_only)
print(f"\n[F3] _camera_moved per-pixel read-back:")
print(f"     view.center()      (transform inversion + map): {t_center:8.2f} us")
print(f"     view.zoom_level()  (transform read)            : {t_zoom:8.2f} us")
print(f"     combined per pan pixel                          : {t_center + t_zoom:8.2f} us")

# F5: _stop_animation per pixel
t_stop, _ = bench(view._stop_animation)
print(f"\n[F5] _stop_animation() per pan pixel                 : {t_stop:8.2f} us")

# F1: listener fan-out per camera change
t_tv, _ = bench(block.target_view)
t_uz, _ = bench(block.update_zoom)
print(f"\n[F1] camera_changed listener (ZoomBlock, 3 views, active match):")
print(f"     target_view()  (active_view + doc lookups)      : {t_tv:8.2f} us")
print(f"     update_zoom()  (target_view + setText)          : {t_uz:8.2f} us")

# F1 worst case: active view is NOT the block's output -> visible_tiles + loop
block2 = Block("outB")
t_tv2, _ = bench(block2.target_view)
print(f"     target_view()  (fallback: visible_tiles + loop) : {t_tv2:8.2f} us")

# F2: glide timer rate vs 60 Hz display
HZ = 60
steps_8 = 1000 / 8.0
wasted_8 = max(0.0, steps_8 - HZ) / steps_8 * 100
print(f"\n[F2] wheel-glide timer vs {HZ} Hz display:")
print(f"     8 ms  timer: {steps_8:6.0f} steps/s -> {wasted_8:5.1f}% of steps produce no visible frame")

# The interval the app actually uses (refresh-rate paced, see view.frame_interval_ms)
from pixelref.ui.canvas.view import frame_interval_ms  # noqa: E402

paced = frame_interval_ms()
steps_paced = 1000 / paced
wasted_paced = max(0.0, steps_paced - HZ) / steps_paced * 100
print(f"     {paced} ms timer (refresh-rate paced): {steps_paced:6.0f} steps/s -> {wasted_paced:5.1f}% of steps produce no visible frame")

# Full pan_by cost (everything per mouse-move pixel)
t_pan, _ = bench(lambda: view.pan_by(QPointF(3, 2)))
print(f"\n[ALL] full pan_by() per mouse-move pixel (no listeners)      : {t_pan:8.2f} us")

# Full pan with the real listener attached (worst case: 2 blocks, fallback path)
view.camera_changed.connect(block.update_zoom)
view.camera_changed.connect(block2.update_zoom)
t_pan_l, _ = bench(lambda: view.pan_by(QPointF(3, 2)))
print(f"[ALL] full pan_by() per pixel + 2 ZoomBlock listeners         : {t_pan_l:8.2f} us")
print(f"     listener overhead per pixel                             : {t_pan_l - t_pan:8.2f} us")

print("\n" + "=" * 72)
print("Done.")
