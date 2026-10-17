"""Painting helpers shared by several plugins (line styles, arrowheads, polylines)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

PEN_STYLES: dict[str, Qt.PenStyle] = {
    "solid": Qt.SolidLine,
    "dash": Qt.DashLine,
    "dot": Qt.DotLine,
    "dashdot": Qt.DashDotLine,
    "none": Qt.NoPen,
}

BRUSH_STYLES: dict[str, Qt.BrushStyle] = {
    "solid": Qt.SolidPattern,
    "dense": Qt.Dense4Pattern,
    "sparse": Qt.Dense6Pattern,
    "diagonal": Qt.BDiagPattern,
    "cross": Qt.DiagCrossPattern,
    "horizontal": Qt.HorPattern,
    "vertical": Qt.VerPattern,
    "grid": Qt.CrossPattern,
    "none": Qt.NoBrush,
}


def arrow_head(start: QPointF, end: QPointF, size: float) -> QPolygonF:
    angle = math.atan2(end.y() - start.y(), end.x() - start.x())
    spread = math.radians(25.0)
    left = QPointF(end.x() - size * math.cos(angle - spread), end.y() - size * math.sin(angle - spread))
    right = QPointF(end.x() - size * math.cos(angle + spread), end.y() - size * math.sin(angle + spread))
    return QPolygonF([end, left, right])


def paint_line(
    painter: QPainter,
    start: QPointF,
    end: QPointF,
    color: QColor,
    thickness: float,
    pen_style: Qt.PenStyle = Qt.SolidLine,
    arrow_start: bool = False,
    arrow_end: bool = True,
) -> None:
    """Draw a line with optional arrowheads; the shaft stops at each head's base
    so thick lines don't poke through the tip."""
    size = max(10.0, thickness * 4.0)
    length = math.hypot(end.x() - start.x(), end.y() - start.y())
    heads = int(arrow_start) + int(arrow_end)
    inset = min(size * 0.8, length / max(1, heads) * 0.9) if length > 0 else 0.0
    direction = (end - start) / length if length > 1e-9 else QPointF(0.0, 0.0)
    shaft_start = start + direction * inset if arrow_start else start
    shaft_end = end - direction * inset if arrow_end else end
    painter.setPen(QPen(color, thickness, pen_style, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    painter.drawLine(shaft_start, shaft_end)
    painter.setPen(QPen(color, max(1.0, thickness * 0.5), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(color)
    if arrow_end:
        painter.drawPolygon(arrow_head(start, end, size))
    if arrow_start:
        painter.drawPolygon(arrow_head(end, start, size))


def smooth_path(points: list[QPointF]) -> QPainterPath:
    """Smooth curve through samples (quadratic segments via midpoints)."""
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0])
    if len(points) == 1:
        # Zero-length segment: drawn as a dot thanks to the round pen cap.
        path.lineTo(points[0] + QPointF(0.01, 0.0))
        return path
    if len(points) == 2:
        path.lineTo(points[1])
        return path
    for i in range(1, len(points) - 1):
        mid = (points[i] + points[i + 1]) / 2.0
        path.quadTo(points[i], mid)
    path.lineTo(points[-1])
    return path


def polyline_length(points: list[QPointF]) -> float:
    return sum(math.hypot(b.x() - a.x(), b.y() - a.y()) for a, b in zip(points, points[1:]))


def point_back_from_end(points: list[QPointF], distance: float) -> QPointF:
    """Point ``distance`` back along the polyline from its last point."""
    remaining = distance
    for i in range(len(points) - 1, 0, -1):
        a, b = points[i - 1], points[i]
        seg = math.hypot(b.x() - a.x(), b.y() - a.y())
        if seg >= remaining and seg > 1e-9:
            f = remaining / seg
            return QPointF(b.x() + (a.x() - b.x()) * f, b.y() + (a.y() - b.y()) * f)
        remaining -= seg
    return QPointF(points[0])


def trim_polyline_end(points: list[QPointF], distance: float) -> list[QPointF]:
    """Polyline with ``distance`` removed from its end (for arrowhead room)."""
    if distance <= 0 or len(points) < 2:
        return list(points)
    cut = point_back_from_end(points, distance)
    remaining = distance
    kept = list(points)
    while len(kept) > 1:
        a, b = kept[-2], kept[-1]
        seg = math.hypot(b.x() - a.x(), b.y() - a.y())
        if seg >= remaining:
            break
        remaining -= seg
        kept.pop()
    kept[-1] = cut
    return kept


def stroke_geometry(
    points: list[QPointF], thickness: float, arrow_start: bool = False, arrow_end: bool = False
) -> tuple[QPainterPath, list[QPolygonF]]:
    """Shaft path and arrowheads for a freehand stroke. The shaft stops short of
    each head so thick strokes don't poke through the tips."""
    if not points:
        return QPainterPath(), []
    size = max(10.0, thickness * 4.0)
    length = polyline_length(points)
    heads_wanted = int(arrow_start) + int(arrow_end)
    can_head = heads_wanted > 0 and length > size * 0.5
    inset = min(size * 0.8, length / max(1, heads_wanted) * 0.45) if can_head else 0.0
    shaft = list(points)
    heads: list[QPolygonF] = []
    if can_head and arrow_end:
        heads.append(arrow_head(point_back_from_end(points, min(size, length)), points[-1], size))
        shaft = trim_polyline_end(shaft, inset)
    if can_head and arrow_start:
        heads.append(arrow_head(point_back_from_end(list(reversed(points)), min(size, length)), points[0], size))
        shaft = list(reversed(trim_polyline_end(list(reversed(shaft)), inset)))
    return smooth_path(shaft), heads


def draw_stroke_geometry(
    painter: QPainter,
    geometry: tuple[QPainterPath, list[QPolygonF]],
    color: QColor,
    thickness: float,
    pen_style: Qt.PenStyle = Qt.SolidLine,
) -> None:
    shaft, heads = geometry
    painter.setPen(QPen(color, thickness, pen_style, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(shaft)
    if heads:
        painter.setPen(QPen(color, max(1.0, thickness * 0.5), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(color)
        for head in heads:
            painter.drawPolygon(head)


def paint_stroke(
    painter: QPainter,
    points: list[QPointF],
    color: QColor,
    thickness: float,
    pen_style: Qt.PenStyle = Qt.SolidLine,
    arrow_start: bool = False,
    arrow_end: bool = False,
) -> None:
    draw_stroke_geometry(painter, stroke_geometry(points, thickness, arrow_start, arrow_end), color, thickness,
                         pen_style)


def paint_handle(painter: QPainter, point: QPointF, size: float = 8.0, accent: QColor | None = None) -> None:
    from ..theme import ACCENT

    half = size / 2.0
    painter.setPen(QPen(accent or ACCENT, 1.2))
    painter.setBrush(QColor("#ffffff"))
    # A QRectF, not four numbers: PySide picks the int overload for those, which overflows
    # for handles far off-screen (a hugely scaled node at a high zoom).
    painter.drawRect(QRectF(point.x() - half, point.y() - half, size, size))


def paint_banner(painter: QPainter, viewport_width: float, text: str) -> None:
    from ..theme import ACCENT, PANEL_BG, TEXT_LIGHT
    from PySide6.QtCore import QRectF

    metrics = painter.fontMetrics()
    width = metrics.horizontalAdvance(text) + 24
    rect = QRectF((viewport_width - width) / 2.0, 10.0, width, metrics.height() + 12)
    painter.setPen(QPen(ACCENT, 1.0))
    painter.setBrush(PANEL_BG)
    painter.drawRoundedRect(rect, 6, 6)
    painter.setPen(TEXT_LIGHT)
    painter.drawText(rect, Qt.AlignCenter, text)
