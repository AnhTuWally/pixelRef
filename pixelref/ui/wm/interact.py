"""Splitting and joining tiles with the mouse, Blender style.

Press a tile's corner grip and drag:

* **into the tile**: a line follows the cursor and shows where the tile will be
  split; the new view (a copy of this one) takes the part between the line and
  the corner you grabbed. Drag mostly sideways for side by side, mostly up or
  down for stacked.
* **onto a neighbouring tile**: that neighbour is darkened; on release this
  tile takes over its space (the neighbour closes).

Release to apply; ``Esc`` or a right-click cancels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .. import theme

if TYPE_CHECKING:
    from .manager import WindowManager

THRESHOLD = 10  # pixels before a drag counts
MIN_RATIO, MAX_RATIO = 0.1, 0.9
ADJACENT_GAP = 10  # splitter handle width plus borders


def global_rect(widget: QWidget) -> QRect:
    return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size())


def adjacent(a: QRect, b: QRect, gap: int = ADJACENT_GAP) -> bool:
    """Whether two rects share an edge (within ``gap``)."""
    a_right, b_right = a.x() + a.width(), b.x() + b.width()
    a_bottom, b_bottom = a.y() + a.height(), b.y() + b.height()
    overlap_y = min(a_bottom, b_bottom) - max(a.y(), b.y())
    overlap_x = min(a_right, b_right) - max(a.x(), b.x())
    side_by_side = (abs(a_right - b.x()) <= gap or abs(b_right - a.x()) <= gap) and overlap_y > 0
    stacked = (abs(a_bottom - b.y()) <= gap or abs(b_bottom - a.y()) <= gap) and overlap_x > 0
    return side_by_side or stacked


@dataclass
class Preview:
    kind: str  # "split" or "join"
    source: QRect  # global
    direction: str = ""  # split: where the new view goes (left/right/up/down)
    ratio: float = 0.5  # split: the new view's share
    target: str = ""  # join: the tile that closes
    target_rect: QRect | None = None
    label: str = ""


def split_preview(rect: QRect, corner: str, start: QPoint, pos: QPoint) -> Preview | None:
    """The split a drag from ``corner`` of ``rect`` to ``pos`` describes (None below the threshold)."""
    dx, dy = pos.x() - start.x(), pos.y() - start.y()
    if abs(dx) + abs(dy) < THRESHOLD:
        return None
    if abs(dx) >= abs(dy):
        if corner.endswith("r"):
            direction, ratio = "right", (rect.x() + rect.width() - pos.x()) / max(1, rect.width())
        else:
            direction, ratio = "left", (pos.x() - rect.x()) / max(1, rect.width())
    else:
        if corner.startswith("b"):
            direction, ratio = "down", (rect.y() + rect.height() - pos.y()) / max(1, rect.height())
        else:
            direction, ratio = "up", (pos.y() - rect.y()) / max(1, rect.height())
    ratio = max(MIN_RATIO, min(MAX_RATIO, ratio))
    return Preview("split", rect, direction=direction, ratio=ratio)


def new_part(preview: Preview) -> QRectF:
    """The part of the source the new view will take."""
    r = QRectF(preview.source)
    w, h = r.width() * preview.ratio, r.height() * preview.ratio
    if preview.direction == "right":
        return QRectF(r.right() - w, r.top(), w, r.height())
    if preview.direction == "left":
        return QRectF(r.left(), r.top(), w, r.height())
    if preview.direction == "down":
        return QRectF(r.left(), r.bottom() - h, r.width(), h)
    return QRectF(r.left(), r.top(), r.width(), h)


class DragOverlay(QWidget):
    """Paints the split line / join shading over the whole window during a drag."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.preview: Preview | None = None
        self.setGeometry(window.rect())
        self.show()
        self.raise_()

    def set_preview(self, preview: Preview | None) -> None:
        self.preview = preview
        self.update()

    def _local(self, rect: QRect | QRectF) -> QRectF:
        rect = QRectF(rect)
        top_left = self.mapFromGlobal(rect.topLeft().toPoint())
        return QRectF(QPointF(top_left), rect.size())

    def paintEvent(self, _event: Any) -> None:  # noqa: N802
        p = self.preview
        if p is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        source = self._local(p.source)
        if p.kind == "split":
            part = self._local(new_part(p))
            fill = QColor(theme.ACCENT)
            fill.setAlpha(45)
            painter.fillRect(part, fill)
            painter.setPen(QPen(theme.ACCENT, 2.0))
            if p.direction == "right":
                painter.drawLine(part.topLeft(), part.bottomLeft())
            elif p.direction == "left":
                painter.drawLine(part.topRight(), part.bottomRight())
            elif p.direction == "down":
                painter.drawLine(part.topLeft(), part.topRight())
            else:
                painter.drawLine(part.bottomLeft(), part.bottomRight())
            self._label(painter, part, "New view", p.label or "Release to split · Esc to cancel")
        else:
            target = self._local(p.target_rect) if p.target_rect is not None else source
            painter.fillRect(target, QColor(0, 0, 0, 130))
            painter.setPen(QPen(theme.ACCENT, 2.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(source.adjusted(1, 1, -1, -1))
            self._arrow(painter, source.center(), target.center())
            self._label(painter, target, "Join", p.label)
        painter.end()

    @staticmethod
    def _label(painter: QPainter, area: QRectF, title: str, hint: str) -> None:
        font = QFont(painter.font())
        font.setPointSizeF(font.pointSizeF() * 1.25)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(theme.TEXT_LIGHT)
        centre = area.center()
        painter.drawText(QRectF(area.left(), centre.y() - 26, area.width(), 24), Qt.AlignCenter, title)
        font.setBold(False)
        font.setPointSizeF(font.pointSizeF() / 1.25)
        painter.setFont(font)
        painter.drawText(QRectF(area.left() + 8, centre.y(), area.width() - 16, 40),
                         Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, hint)

    @staticmethod
    def _arrow(painter: QPainter, start: QPointF, end: QPointF) -> None:
        line = end - start
        length = (line.x() ** 2 + line.y() ** 2) ** 0.5
        if length < 1:
            return
        ux, uy = line.x() / length, line.y() / length
        tip = start + QPointF(ux, uy) * min(length * 0.8, length - 10)
        base = start + QPointF(ux, uy) * max(0.0, min(length * 0.8, length - 10) - 18)
        painter.setPen(QPen(theme.ACCENT, 3.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(start, base)
        head = QPainterPath()
        head.addPolygon(QPolygonF([tip, base + QPointF(-uy, ux) * 9, base + QPointF(uy, -ux) * 9]))
        painter.fillPath(head, theme.ACCENT)


class CornerDrag:
    """One corner drag, from press to release."""

    def __init__(self, manager: "WindowManager", tile: str, corner: str, start: QPoint) -> None:
        self.manager = manager
        self.tile = tile
        self.corner = corner
        self.start = start
        self.preview: Preview | None = None
        frame = manager.frames.get(tile)
        self.overlay = DragOverlay(frame.window()) if frame is not None and frame.window() is not None else None

    def update(self, pos: QPoint) -> None:
        self.preview = self.compute(pos)
        if self.overlay is not None:
            self.overlay.set_preview(self.preview)

    def compute(self, pos: QPoint) -> Preview | None:
        wm = self.manager
        frame = wm.frames.get(self.tile)
        if frame is None:
            return None
        rect = global_rect(frame)
        if rect.contains(pos):
            return split_preview(rect, self.corner, self.start, pos)
        target = wm.tile_at(pos, exclude=self.tile)
        if target is None:
            return None
        target_rect = global_rect(wm.frames[target])
        if not adjacent(rect, target_rect):
            return None
        if not wm.can_close(target):
            label = "The last canvas view can't be closed"
        else:
            label = (f"Release: “{wm.title(self.tile)}” takes over the space of "
                     f"“{wm.title(target)}” (which closes) · Esc to cancel")
        return Preview("join", rect, target=target, target_rect=target_rect, label=label)

    def finish(self, apply: bool) -> None:
        preview = self.preview if apply else None
        if self.overlay is not None:
            self.overlay.hide()
            self.overlay.deleteLater()
            self.overlay = None
        if preview is None:
            return
        if preview.kind == "split":
            self.manager.run("wm.split_view", preview.direction, self.tile, ratio=round(preview.ratio, 4))
        elif self.manager.can_close(preview.target):
            self.manager.run("wm.join_view", preview.target, into=self.tile)
