"""Qt side of the rectangle plugin: item, Rect and Ellipse tools, reshape handles."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsItem, QWidget

from pixelref.plugin_api import Attr
from pixelref.plugin_api.contracts import StrokeStyle
from pixelref.plugin_api.ui import HANDLED, NodeItemMixin, PointerEvent, Tool, paint, theme
from pixelref.plugin_api.ui import widgets as _widgets

from . import FILL_STYLES, LINE_STYLES

color_from = _widgets.color_from

CORNERS = ("tl", "tr", "br", "bl")
EDGES = ("tm", "mr", "bm", "ml")
OPPOSITE = {"tl": "br", "tr": "bl", "br": "tl", "bl": "tr", "tm": "bm", "bm": "tm", "ml": "mr", "mr": "ml"}


def local_handle_points(r: QRectF) -> dict[str, QPointF]:
    cx, cy = r.center().x(), r.center().y()
    return {"tl": r.topLeft(), "tr": r.topRight(), "br": r.bottomRight(), "bl": r.bottomLeft(),
            "tm": QPointF(cx, r.top()), "bm": QPointF(cx, r.bottom()), "ml": QPointF(r.left(), cy),
            "mr": QPointF(r.right(), cy)}


class RectItem(NodeItemMixin, QGraphicsItem):
    MIN_HIT_WIDTH = 10.0

    def __init__(self, node_name: str) -> None:
        super().__init__()
        self._init_node(node_name, "annotation_rect", None)
        self._size = QPointF(120.0, 80.0)
        self._stroke = QColor(255, 0, 0)
        self._fill = QColor(0, 0, 0, 0)
        self._thickness = 2.0
        self._stroke_style = "solid"
        self._fill_style = "solid"
        self._corner_radius = 0.0
        self._ellipse = False
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def apply_attrs(self, attrs: dict[str, Any]) -> None:
        self.prepareGeometryChange()
        self._thickness = max(0.5, float(attrs.get("thickness", 2.0)))
        self._stroke_style = str(attrs.get("stroke_style", "solid"))
        self._fill_style = str(attrs.get("fill_style", "solid"))
        self._corner_radius = max(0.0, float(attrs.get("corner_radius", 0.0)))
        self._ellipse = attrs.get("shape", "rect") == "ellipse"
        size = attrs.get("size", [120.0, 80.0])
        self._size = QPointF(max(1.0, float(size[0])), max(1.0, float(size[1])))
        self._stroke = color_from(attrs.get("stroke_color", [255, 0, 0]))
        fill = color_from(attrs.get("fill_color", [0, 0, 0]))
        fill.setAlphaF(max(0.0, min(1.0, float(attrs.get("fill_opacity", 0.0)))))
        self._fill = fill
        self.update()

    is_rect = True

    @property
    def is_ellipse(self) -> bool:
        return self._ellipse

    def outline(self) -> QPainterPath:
        """The rectangle (corners rounded by the radius) or the ellipse inside it."""
        rect = self.content_rect()
        path = QPainterPath()
        if self._ellipse:
            path.addEllipse(rect)
        else:
            radius = min(self._corner_radius, rect.width() / 2.0, rect.height() / 2.0)
            path.addRoundedRect(rect, radius, radius)
        return path

    def set_rect_size(self, width: float, height: float) -> None:
        """Resize the rectangle itself (the border keeps its thickness)."""
        self.prepareGeometryChange()
        self._size = QPointF(max(1.0, float(width)), max(1.0, float(height)))
        self.update()
        self._notify_moved()

    def corner_radius(self) -> float:
        return self._corner_radius

    def set_corner_radius(self, radius: float) -> None:
        self._corner_radius = max(0.0, min(float(radius), self._size.x() / 2.0, self._size.y() / 2.0))
        self.update()
        self._notify_moved()

    def commit_values(self) -> dict[str, Any]:
        values = self.transform_attrs()
        values["size"] = [round(self._size.x(), 3), round(self._size.y(), 3)]
        values["corner_radius"] = round(self._corner_radius, 3)
        return values

    def content_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, self._size.x(), self._size.y())

    def boundingRect(self) -> QRectF:
        pad = max(self._thickness, self.MIN_HIT_WIDTH) / 2.0 + 1.0
        return self.content_rect().adjusted(-pad, -pad, pad, pad)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        if self._ellipse:
            path.addEllipse(self.content_rect())
        else:
            path.addRect(self.content_rect())
        if self._fill.alpha() > 0:
            return path
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self._thickness, self.MIN_HIT_WIDTH))
        stroker.setCapStyle(Qt.RoundCap)
        stroker.setJoinStyle(Qt.RoundJoin)
        return stroker.createStroke(path)

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        return super().itemChange(change, value)

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen_style = paint.PEN_STYLES.get(self._stroke_style, Qt.SolidLine)
        painter.setPen(QPen(self._stroke, self._thickness, pen_style, Qt.RoundCap, Qt.RoundJoin))
        brush_style = paint.BRUSH_STYLES.get(self._fill_style, Qt.SolidPattern)
        has_fill = self._fill.alpha() > 0 and brush_style != Qt.NoBrush
        painter.setBrush(QBrush(self._fill, brush_style) if has_fill else Qt.NoBrush)
        painter.drawPath(self.outline())


class RectHandles:
    """A selected rectangle gets editable corner/edge points (they reshape the
    rectangle instead of scaling it) and a corner-radius handle."""

    node_type = "annotation_rect"
    labels = {"rect_resize": "Resize rectangle", "rect_radius": "Corner radius"}

    def build(self, gizmo: Any, view: Any, item: Any, g: dict[str, Any]) -> None:
        g["rect"] = True
        st = item.sceneTransform()
        rect = item.content_rect()
        for name, point in local_handle_points(rect).items():
            g["handles_vp"][name] = view.to_viewport(st.map(point))
        # Radius handle on the diagonal inside the top-left corner, at the
        # rounding's centre (or a few pixels in when the corner is square).
        pixels_per_unit = max(1e-6, math.hypot(st.m11(), st.m12()) * view.zoom_level())
        inset = max(item.corner_radius(), gizmo.RADIUS_HANDLE_INSET / pixels_per_unit)
        if not item.is_ellipse and inset < min(rect.width(), rect.height()) / 2.0:
            g["handles_vp"]["radius"] = view.to_viewport(st.map(QPointF(rect.left() + inset, rect.top() + inset)))
        return None

    def hit(self, gizmo: Any, view: Any, g: dict[str, Any], pos: QPointF) -> str | None:
        point = g["handles_vp"].get("radius")
        if point is not None and math.hypot(pos.x() - point.x(), pos.y() - point.y()) <= gizmo.HANDLE_HIT - 1.0:
            return "radius"
        return None

    def start(self, gizmo: Any, handle: str, g: dict[str, Any], item: Any) -> str | None:
        if handle in CORNERS + EDGES:
            return "rect_resize"
        if handle == "radius":
            return "rect_radius"
        return None

    def cursor(self, gizmo: Any, handle: str, g: dict[str, Any], view: Any) -> Any:
        return Qt.SizeFDiagCursor if handle == "radius" else None

    def drag(self, gizmo: Any, op: dict[str, Any], m: QPointF, modifiers: Any, view: Any) -> None:
        item, s0, l0, _parent_inv, _rotation = op["targets"][0]
        start = op["start_rect"]
        m_local = s0.inverted()[0].map(m)
        if op["kind"] == "rect_radius":
            # Project onto the corner diagonal: distance in from the top-left corner.
            radius = max(0.0, ((m_local.x() - start.left()) + (m_local.y() - start.top())) / 2.0)
            item.set_corner_radius(radius)
            gizmo.ui.status(f"Corner radius {item.corner_radius():.0f}px")
            return
        # Corner/edge point: the opposite side stays fixed; Shift keeps it square,
        # Alt resizes around the centre; dragging past the opposite side flips it.
        handle = op["handle"]
        points = local_handle_points(start)
        about_center = bool(modifiers & Qt.AltModifier)
        anchor = start.center() if about_center else points[OPPOSITE[handle]]
        x, y = m_local.x(), m_local.y()
        if handle in CORNERS and modifiers & Qt.ShiftModifier:
            side = max(abs(x - anchor.x()), abs(y - anchor.y()))
            x = anchor.x() + math.copysign(side, (x - anchor.x()) or 1.0)
            y = anchor.y() + math.copysign(side, (y - anchor.y()) or 1.0)
        if handle in ("tm", "bm"):
            top, bottom = (2 * anchor.y() - y, y) if about_center else (anchor.y(), y)
            rect = QRectF(QPointF(start.left(), top), QPointF(start.right(), bottom)).normalized()
        elif handle in ("ml", "mr"):
            left, right = (2 * anchor.x() - x, x) if about_center else (anchor.x(), x)
            rect = QRectF(QPointF(left, start.top()), QPointF(right, start.bottom())).normalized()
        else:
            corner_a = QPointF(2 * anchor.x() - x, 2 * anchor.y() - y) if about_center else anchor
            rect = QRectF(corner_a, QPointF(x, y)).normalized()
        rect.setWidth(max(1.0, rect.width()))
        rect.setHeight(max(1.0, rect.height()))
        item.set_rect_size(rect.width(), rect.height())
        # Local (0, 0) is the rectangle's top-left: move the origin to the new corner.
        item.set_local_transform(QTransform.fromTranslate(rect.left(), rect.top()) * l0)
        if item.is_ellipse:
            gizmo.ui.status(f"Ellipse {rect.width():.0f} × {rect.height():.0f}px (Shift: circle, Alt: from centre)")
        else:
            gizmo.ui.status(f"Rectangle {rect.width():.0f} × {rect.height():.0f}px (Shift: square, Alt: from centre)")

    def paint(self, gizmo: Any, view: Any, painter: QPainter, g: dict[str, Any]) -> bool:
        # Round points = reshape the rectangle (like a line's end points).
        painter.setPen(QPen(theme.ACCENT, 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(QPolygonF(g["corners_vp"]))
        painter.drawLine(g["top_mid_vp"], g["rotate_vp"])
        painter.setPen(QPen(theme.ACCENT, 1.5))
        painter.setBrush(QColor("#ffffff"))
        for name, point in g["handles_vp"].items():
            if name != "radius":
                size = 5.0 if name in CORNERS else 4.0
                painter.drawEllipse(point, size, size)
        painter.drawEllipse(g["rotate_vp"], 5.0, 5.0)
        radius_point = g["handles_vp"].get("radius")
        if radius_point is not None:
            painter.setPen(QPen(QColor("#ffffff"), 1.2))
            painter.setBrush(theme.ACCENT)
            painter.drawEllipse(radius_point, 3.5, 3.5)
        return True


class RectTool(Tool):
    id = "rect"
    title = "Rect"
    action_text = "Rectangle Tool"
    icon = "rect"
    tooltip = "Draw rectangle (R) · drag its corner points to reshape it"
    keywords = "rectangle box square shape draw"
    shortcuts = ("R",)
    order = 10.0
    cursor = Qt.CrossCursor
    gizmo = "handles"
    uses_brush_color = True
    color_option = "stroke_color"
    width_option = "thickness"
    creates = "annotation_rect"
    option_specs = (
        Attr("stroke_color", "color", [255, 64, 64], label="Color"),
        Attr("thickness", "float", 4.0, minimum=0.5, maximum=200.0, soft_min=0.5, soft_max=40.0, step=0.5,
             precision=1, unit="px", label="Width"),
        Attr("stroke_style", "enum", "solid", choices=LINE_STYLES, label="Line"),
        Attr("fill_color", "color", [0, 0, 0], label="Fill"),
        Attr("fill_opacity", "float", 0.0, minimum=0.0, maximum=1.0, step=0.05, precision=0, unit="%",
             display_scale=100.0, label="Fill alpha", doc="0 % = no fill"),
        Attr("fill_style", "enum", "solid", choices=FILL_STYLES, label="Fill style"),
        Attr("corner_radius", "float", 0.0, minimum=0.0, soft_min=0.0, soft_max=100.0, step=1.0, precision=1,
             unit="px", label="Radius"),
    )
    options_title = "Rectangle options"
    create_command = "rect.create"
    ellipse = False
    hint = ("Drag to draw rectangles (Shift: square, Alt: from centre) · "
            "drag the selected one's corners to adjust · Esc / V to finish")

    def __init__(self, ui: Any) -> None:
        super().__init__(ui)
        self.op: dict[str, Any] | None = None

    @property
    def busy(self) -> bool:
        return self.op is not None

    def cancel(self) -> bool:
        if self.op is not None:
            self.op = None
            return True
        return False

    def banner(self, view: Any) -> str | None:
        return "▭  Rectangle · drag to draw (Shift square, Alt from centre)   —   Esc / V to finish"

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind == "press" and e.button == Qt.LeftButton:
            self.op = {"start": e.scene_pos, "end": e.scene_pos, "origin": e.scene_pos, "view": e.view}
            return HANDLED
        if self.op is None:
            return None
        if e.kind == "move":
            origin = self.op["origin"]
            end = e.view.to_scene(e.pos)
            if e.shift:
                dx, dy = end.x() - origin.x(), end.y() - origin.y()
                side = max(abs(dx), abs(dy))
                end = QPointF(origin.x() + math.copysign(side, dx or 1.0), origin.y() + math.copysign(side, dy or 1.0))
            self.op["start"] = QPointF(2 * origin.x() - end.x(), 2 * origin.y() - end.y()) if e.alt else origin
            self.op["end"] = end
            e.view.viewport().update()
            return HANDLED
        if e.kind == "release":
            op, self.op = self.op, None
            view = op["view"]
            start, end = op["start"], op["end"]
            e.view.viewport().update()
            a, b = view.to_viewport(start), view.to_viewport(end)
            if math.hypot(a.x() - b.x(), a.y() - b.y()) > 4.0:
                rect = QRectF(start, end).normalized()
                self.app.run_command(self.create_command, rect.x(), rect.y(), rect.width(), rect.height(),
                                     **self.options.as_dict())
            return HANDLED
        return None

    def paint_overlay(self, view: Any, painter: QPainter) -> None:
        if self.op is None or self.op["view"] is not view:
            return
        options = self.options
        start = view.to_viewport(self.op["start"])
        end = view.to_viewport(self.op["end"])
        width = max(1.0, float(options.get("thickness")) * view.zoom_level())
        painter.setPen(QPen(_widgets.color_from(options.get("stroke_color")), width, Qt.SolidLine, Qt.RoundCap,
                            Qt.RoundJoin))
        fill = _widgets.color_from(options.get("fill_color"))
        fill.setAlphaF(float(options.get("fill_opacity")))
        painter.setBrush(fill if fill.alpha() else Qt.NoBrush)
        box = QRectF(start, end).normalized()
        if self.ellipse:
            painter.drawEllipse(box)
            return
        radius = float(options.get("corner_radius")) * view.zoom_level()
        painter.drawRoundedRect(box, radius, radius)

    def options_panel(self, parent: QWidget) -> QWidget:
        return self.ui.service("brush").make_tool_panel(self, parent)


class EllipseTool(RectTool):
    """Ellipses and circles: ``annotation_rect`` nodes with ``shape`` "ellipse", so they
    share the rectangle's handles, line and fill styles."""

    id = "oval"  # "ellipse" is the example plugin's tool (examples/plugins/ellipse)
    title = "Ellipse"
    action_text = "Ellipse Tool"
    icon = "ellipse"
    tooltip = "Draw ellipse (O) · Shift for a circle"
    keywords = "ellipse circle oval round shape draw"
    shortcuts = ("O",)
    order = 21.5  # after Rect and Arrow, the other shapes
    option_specs = tuple(spec for spec in RectTool.option_specs if spec.name != "corner_radius")
    options_title = "Ellipse options"
    create_command = "rect.create_ellipse"
    ellipse = True
    hint = ("Drag to draw ellipses (Shift: circle, Alt: from centre) · "
            "drag the selected one's points to adjust · Esc / V to finish")

    def banner(self, view: Any) -> str | None:
        return "◯  Ellipse · drag to draw (Shift circle, Alt from centre)   —   Esc / V to finish"


def activate_rect_ui(ui: Any) -> None:
    def factory(_canvas: Any, node: dict[str, Any]) -> RectItem:
        item = RectItem(node["name"])
        item.apply_attrs(node.get("attrs", {}))
        return item

    ui.node_ui("annotation_rect", factory=factory, label="Rectangle", icon="rect", badge=("#d27b7b", "R"))
    ui.register_tool(RectTool(ui))
    ui.register_tool(EllipseTool(ui))
    ui.contribute("gizmo.handles", RectHandles())
    ui.contribute("stroke.styles", StrokeStyle("annotation_rect", color_attr="stroke_color", fill=True, radius=True))
