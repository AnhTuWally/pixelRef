"""Stroke and drawing items, plus freehand smoothing."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPainterPathStroker
from PySide6.QtWidgets import QGraphicsItem

from pixelref.plugin_api.ui import NodeItemMixin, paint
from pixelref.plugin_api.ui import widgets as _widgets

color_from = _widgets.color_from


def resample_polyline(points: list[QPointF], spacing: float) -> list[QPointF]:
    """Evenly spaced samples along a polyline (keeps both end points)."""
    if len(points) < 2 or spacing <= 0:
        return list(points)
    out = [QPointF(points[0])]
    carry = 0.0
    for a, b in zip(points, points[1:]):
        seg = math.hypot(b.x() - a.x(), b.y() - a.y())
        if seg < 1e-9:
            continue
        t = spacing - carry
        while t <= seg:
            f = t / seg
            out.append(QPointF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f))
            t += spacing
        carry = seg - (t - spacing)
    if math.hypot(out[-1].x() - points[-1].x(), out[-1].y() - points[-1].y()) > 1e-6:
        out.append(QPointF(points[-1]))
    return out


def smooth_stroke_points(points: list[QPointF], strength: float, spacing: float) -> list[QPointF]:
    """Remove hand jitter: resample evenly, then average each sample with its
    neighbours over a stretch that grows with ``strength`` (two passes — a soft
    bell-shaped filter). The window shrinks towards the ends so the stroke
    still starts and ends exactly where it was drawn."""
    strength = max(0.0, min(1.0, float(strength)))
    if strength <= 0.0 or len(points) < 3:
        return list(points)
    dense = resample_polyline(points, spacing)
    reach = strength * max(24.0, spacing * 8.0)
    radius = max(1, round(reach / spacing))
    xs = [q.x() for q in dense]
    ys = [q.y() for q in dense]
    for _pass in range(2):
        xs, ys = _window_means(xs, ys, radius)
    return [QPointF(x, y) for x, y in zip(xs, ys)]


def _window_means(xs: list[float], ys: list[float], radius: int) -> tuple[list[float], list[float]]:
    """Mean of each sample's window (radius shrinking at the ends), via prefix sums:
    linear in the stroke length, where summing every window was length × radius."""
    count = len(xs)
    sum_x, sum_y = [0.0] * (count + 1), [0.0] * (count + 1)
    for i in range(count):
        sum_x[i + 1] = sum_x[i] + xs[i]
        sum_y[i + 1] = sum_y[i] + ys[i]
    out_x, out_y = [0.0] * count, [0.0] * count
    for i in range(count):
        r = min(radius, i, count - 1 - i)
        lo, hi = i - r, i + r + 1
        size = hi - lo
        out_x[i] = (sum_x[hi] - sum_x[lo]) / size
        out_y[i] = (sum_y[hi] - sum_y[lo]) / size
    return out_x, out_y


class IncrementalStrokeSmoother:
    """Smoothed copy of a stroke that is still being drawn.

    Same result as :func:`smooth_stroke_points` but only the end of the stroke
    is recomputed per new sample: with two passes of radius ``r`` a smoothed
    sample can't change once it is more than ``2r + 1`` samples from the end,
    so those are cached (with their path) and drawing stays fast however long
    the stroke gets.
    """

    def __init__(self, strength: float, spacing: float) -> None:
        self.strength = max(0.0, min(1.0, float(strength)))
        self.spacing = max(0.5, float(spacing))
        reach = self.strength * max(24.0, self.spacing * 8.0)
        self.radius = max(1, round(reach / self.spacing)) if self.strength > 0 else 0
        self.raw: list[QPointF] = []
        self.dense: list[QPointF] = []
        self._carry = 0.0
        self._stable: list[QPointF] = []
        self._stable_path = QPainterPath()

    def add(self, point: QPointF) -> None:
        point = QPointF(point)
        if not self.raw:
            self.raw.append(point)
            self.dense.append(point)
            self._stable_path.moveTo(point)
            return
        a = self.raw[-1]
        self.raw.append(point)
        seg = math.hypot(point.x() - a.x(), point.y() - a.y())
        if seg < 1e-9:
            return
        t = self.spacing - self._carry
        while t <= seg:
            f = t / seg
            self.dense.append(QPointF(a.x() + (point.x() - a.x()) * f, a.y() + (point.y() - a.y()) * f))
            t += self.spacing
        self._carry = seg - (t - self.spacing)

    def _smooth_range(self, pts: list[QPointF], start: int) -> list[QPointF]:
        n, r = len(pts), self.radius
        lo = max(0, start - r)

        def average(source: list[QPointF], offset: int, i: int) -> QPointF:
            k = min(r, i, n - 1 - i)
            window = source[i - k - offset : i + k + 1 - offset]
            return QPointF(sum(q.x() for q in window) / len(window), sum(q.y() for q in window) / len(window))

        first = [average(pts, 0, i) for i in range(max(0, lo - r), n)]
        first_offset = max(0, lo - r)
        return [average(first, first_offset, j) for j in range(start, n)]

    def points(self) -> list[QPointF]:
        if self.radius == 0 or len(self.raw) < 3:
            return list(self.raw)
        pts = list(self.dense)
        last = self.raw[-1] if self.raw else None
        if last is not None and (not pts or math.hypot(pts[-1].x() - last.x(), pts[-1].y() - last.y()) > 1e-6):
            pts.append(QPointF(last))
        final_count = len(self.dense) - 2 * self.radius - 1
        if final_count > len(self._stable):
            frozen = self._smooth_range(self.dense, len(self._stable))[: final_count - len(self._stable)]
            for q in frozen:
                if self._stable:
                    self._stable_path.lineTo(q)
                self._stable.append(q)
        return self._stable + self._smooth_range(pts, len(self._stable))

    def path(self) -> QPainterPath:
        points = self.points()
        path = QPainterPath(self._stable_path) if self._stable else QPainterPath()
        start = len(self._stable)
        if not self._stable and points:
            path.moveTo(points[0])
            start = 1
        for q in points[start:]:
            path.lineTo(q)
        if len(points) == 1:
            path.lineTo(points[0] + QPointF(0.01, 0.0))
        return path


class StrokeItem(NodeItemMixin, QGraphicsItem):
    MIN_HIT_WIDTH = 10.0

    def __init__(self, node_name: str) -> None:
        super().__init__()
        self._init_node(node_name, "annotation_path", None)
        self._points: list[QPointF] = []
        self._smoothing = 0.0
        self._dense_cache: list[QPointF] | None = None
        self._render_cache: Any = None
        self._path_cache: QPainterPath | None = None
        self._shape_cache: QPainterPath | None = None
        self._stroke = QColor(255, 64, 64)
        self._thickness = 4.0
        self._arrow = False
        self._arrow_start = False
        self._stroke_style = "solid"
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def apply_attrs(self, attrs: dict[str, Any]) -> None:
        self.prepareGeometryChange()
        self._thickness = max(0.1, float(attrs.get("thickness", 4.0)))
        self._stroke_style = str(attrs.get("stroke_style", "solid"))
        self._points = [QPointF(float(p[0]), float(p[1])) for p in attrs.get("points", []) if len(p) >= 2]
        self._smoothing = max(0.0, min(1.0, float(attrs.get("smoothing", 0.0))))
        self._dense_cache = None
        self._render_cache = None
        self._path_cache = None
        self._shape_cache = None
        self._stroke = color_from(attrs.get("color", [255, 64, 64]))
        self._arrow = bool(attrs.get("arrow", 0))
        self._arrow_start = bool(attrs.get("arrow_start", 0))
        self.update()

    def stroke_points(self) -> list[QPointF]:
        """The freehand samples after smoothing (cached)."""
        if self._dense_cache is None:
            spacing = max(1.5, self._thickness * 0.5)
            self._dense_cache = smooth_stroke_points(self._points, self._smoothing, spacing)
        return self._dense_cache

    def stroke_path(self) -> QPainterPath:
        if self._path_cache is None:
            self._path_cache = paint.smooth_path(self.stroke_points())
        return self._path_cache

    def _arrow_size(self) -> float:
        return max(10.0, self._thickness * 4.0)

    def content_rect(self) -> QRectF:
        rect = self.stroke_path().boundingRect() if self._points else QRectF(0.0, 0.0, 1.0, 1.0)
        pad_w = max(0.0, 1.0 - rect.width()) / 2.0
        pad_h = max(0.0, 1.0 - rect.height()) / 2.0
        return rect.adjusted(-pad_w, -pad_h, pad_w, pad_h)

    def boundingRect(self) -> QRectF:
        pad = max(self._thickness, self.MIN_HIT_WIDTH) / 2.0 + 1.0
        if self._arrow or self._arrow_start:
            pad = max(pad, self._arrow_size() + self._thickness)
        return self.content_rect().adjusted(-pad, -pad, pad, pad)

    def shape(self) -> QPainterPath:
        # Hit tests (clicks, rubber band, every eraser move) call this a lot: stroke once.
        if self._shape_cache is None:
            stroker = QPainterPathStroker()
            stroker.setWidth(max(self._thickness, self.MIN_HIT_WIDTH))
            stroker.setCapStyle(Qt.RoundCap)
            stroker.setJoinStyle(Qt.RoundJoin)
            self._shape_cache = stroker.createStroke(self.stroke_path())
        return self._shape_cache

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        return super().itemChange(change, value)

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen_style = paint.PEN_STYLES.get(self._stroke_style, Qt.SolidLine)
        # Geometry only changes with the stroke itself: build it once, not per repaint.
        if self._render_cache is None:
            self._render_cache = paint.stroke_geometry(self.stroke_points(), self._thickness, self._arrow_start,
                                                       self._arrow)
        paint.draw_stroke_geometry(painter, self._render_cache, self._stroke, self._thickness, pen_style)


class DrawingItem(NodeItemMixin, QGraphicsItem):
    """A pen drawing: strokes live in its local space; it paints nothing itself and
    is hit-tested on the union of its strokes, so clicking empty space inside its
    bounds still reaches items underneath."""

    captures_children = True

    def __init__(self, node_name: str) -> None:
        super().__init__()
        self._init_node(node_name, "annotation_drawing", None)
        self._shape_cache: QPainterPath | None = None
        # The bounds Qt last read. The strokes have usually changed already when
        # children_changed() runs, and prepareGeometryChange() must see the old bounds
        # or the scene index keeps stale entries (a crash once the item is deleted).
        self._bounds_cache: QRectF | None = None
        self._editing = False
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.ItemHasNoContents, True)

    @property
    def editing(self) -> bool:
        return self._editing

    def set_editing(self, editing: bool) -> None:
        self._editing = editing
        self.update()

    def strokes(self) -> list[StrokeItem]:
        return [child for child in self.childItems() if isinstance(child, StrokeItem)]

    def children_changed(self) -> None:
        self._shape_cache = None
        if self._bounds_cache is None:
            return  # not read since the last change, so Qt holds no stale bounds
        self.prepareGeometryChange()  # reads the old bounds, so clear the cache after
        self._bounds_cache = None

    def content_rect(self) -> QRectF:
        rect = QRectF()
        for child in self.childItems():
            if child.isVisible():
                inner = child.content_rect() if isinstance(child, NodeItemMixin) else child.boundingRect()
                rect = rect.united(child.mapRectToParent(inner))
        return rect if rect.isValid() else QRectF(0.0, 0.0, 1.0, 1.0)

    def boundingRect(self) -> QRectF:
        if self._bounds_cache is None:
            self._bounds_cache = self.childrenBoundingRect()
        return QRectF(self._bounds_cache)

    def shape(self) -> QPainterPath:
        if self._shape_cache is None:
            path = QPainterPath()
            path.setFillRule(Qt.WindingFill)
            for child in self.childItems():
                if child.isVisible():
                    path.addPath(child.mapToParent(child.shape()))
            self._shape_cache = path
        return self._shape_cache

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        return super().itemChange(change, value)


