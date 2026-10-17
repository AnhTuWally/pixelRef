"""Qt side of the arrow plugin: item, Arrow tool, endpoint handles."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPainterPathStroker, QPen
from PySide6.QtWidgets import QGraphicsItem, QLabel, QWidget

from pixelref.plugin_api import Attr
from pixelref.plugin_api.contracts import StrokeStyle
from pixelref.plugin_api.ui import HANDLED, NodeItemMixin, PointerEvent, Tool, paint, theme
from pixelref.plugin_api.ui import widgets as w
from pixelref.plugin_api.ui import widgets as _widgets

from . import LINE_STYLES

color_from = _widgets.color_from


class LineItem(NodeItemMixin, QGraphicsItem):
    MIN_HIT_WIDTH = 10.0
    is_line = True

    def __init__(self, node_name: str) -> None:
        super().__init__()
        self._init_node(node_name, "annotation_line", None)
        self._start = QPointF(0.0, 0.0)
        self._end = QPointF(100.0, 0.0)
        self._stroke = QColor(255, 0, 0)
        self._thickness = 2.0
        self._stroke_style = "solid"
        self._arrow = False
        self._arrow_start = False
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def apply_attrs(self, attrs: dict[str, Any]) -> None:
        self.prepareGeometryChange()
        self._thickness = max(0.5, float(attrs.get("thickness", 2.0)))
        self._stroke_style = str(attrs.get("stroke_style", "solid"))
        start = attrs.get("start", [0.0, 0.0])
        end = attrs.get("end", [100.0, 0.0])
        self._start = QPointF(float(start[0]), float(start[1]))
        self._end = QPointF(float(end[0]), float(end[1]))
        self._stroke = color_from(attrs.get("color", [255, 0, 0]))
        self._arrow = bool(attrs.get("arrow", 0))
        self._arrow_start = bool(attrs.get("arrow_start", 0))
        self.update()

    def endpoint(self, which: str) -> QPointF:
        """Local position of the ``"start"`` or ``"end"`` point."""
        return QPointF(self._start if which == "start" else self._end)

    def set_endpoint(self, which: str, point: QPointF) -> None:
        self.prepareGeometryChange()
        if which == "start":
            self._start = QPointF(point)
        else:
            self._end = QPointF(point)
        self.update()
        self._notify_moved()

    def commit_values(self) -> dict[str, Any]:
        values = self.transform_attrs()
        values["start"] = [round(self._start.x(), 3), round(self._start.y(), 3)]
        values["end"] = [round(self._end.x(), 3), round(self._end.y(), 3)]
        return values

    def outline_line(self) -> tuple[QPointF, QPointF]:
        st = self.sceneTransform()
        return st.map(self._start), st.map(self._end)

    def _arrow_size(self) -> float:
        return max(10.0, self._thickness * 4.0)

    def content_rect(self) -> QRectF:
        rect = QRectF(self._start, self._end).normalized()
        # Give straight horizontal/vertical lines some extent so the gizmo stays usable.
        pad_w = max(0.0, 1.0 - rect.width()) / 2.0
        pad_h = max(0.0, 1.0 - rect.height()) / 2.0
        return rect.adjusted(-pad_w, -pad_h, pad_w, pad_h)

    def boundingRect(self) -> QRectF:
        pad = max(self._thickness, self.MIN_HIT_WIDTH) / 2.0 + 1.0
        if self._arrow or self._arrow_start:
            pad = max(pad, self._arrow_size() + self._thickness)
        return self.content_rect().adjusted(-pad, -pad, pad, pad)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(self._start)
        path.lineTo(self._end)
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
        paint.paint_line(painter, self._start, self._end, self._stroke, self._thickness, pen_style,
                         self._arrow_start, self._arrow)


class LineHandles:
    """A selected line/arrow shows a handle on each end instead of a box."""

    node_type = "annotation_line"
    labels = {"line_point": "Move line point"}

    def build(self, gizmo: Any, view: Any, item: Any, g: dict[str, Any]) -> dict[str, Any]:
        st = item.sceneTransform()
        start_scene = st.map(item.endpoint("start"))
        end_scene = st.map(item.endpoint("end"))
        rect = item.content_rect()
        corners_scene = [st.map(p) for p in (rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft())]
        return {
            "line": True,
            "targets": [item],
            "single": True,
            "corners_scene": corners_scene,
            "corners_vp": [view.to_viewport(p) for p in corners_scene],
            "center_scene": (start_scene + end_scene) / 2.0,
            "handles_vp": {"start": view.to_viewport(start_scene), "end": view.to_viewport(end_scene)},
        }

    def hit(self, gizmo: Any, view: Any, g: dict[str, Any], pos: QPointF) -> str:
        # The end handle wins when both overlap so a zero-length line can be pulled out.
        for name in ("end", "start"):
            point = g["handles_vp"][name]
            if math.hypot(pos.x() - point.x(), pos.y() - point.y()) <= gizmo.HANDLE_HIT + 2.0:
                return name
        return ""

    def start(self, gizmo: Any, handle: str, g: dict[str, Any], item: Any) -> str:
        return "line_point"

    def cursor(self, gizmo: Any, handle: str, g: dict[str, Any], view: Any) -> Any:
        return Qt.CrossCursor

    def drag(self, gizmo: Any, op: dict[str, Any], m: QPointF, modifiers: Any, view: Any) -> None:
        """Drag one end; Shift snaps the angle to 45° steps."""
        item, s0, _l0, _parent_inv, _rotation = op["targets"][0]
        which = op["handle"]
        other = s0.map(item.endpoint("start" if which == "end" else "end"))
        dx, dy = m.x() - other.x(), m.y() - other.y()
        if modifiers & Qt.ShiftModifier:
            angle = round(math.atan2(dy, dx) / (math.pi / 4.0)) * (math.pi / 4.0)
            length = math.hypot(dx, dy)
            dx, dy = length * math.cos(angle), length * math.sin(angle)
            m = QPointF(other.x() + dx, other.y() + dy)
        item.set_endpoint(which, s0.inverted()[0].map(m))
        angle_deg = math.degrees(math.atan2(-dy, dx))
        gizmo.ui.status(f"Length {math.hypot(dx, dy):.0f}px · angle {angle_deg:.0f}° (Shift snaps 45°)")

    def paint(self, gizmo: Any, view: Any, painter: QPainter, g: dict[str, Any]) -> bool:
        painter.setPen(QPen(theme.ACCENT, 1.5))
        painter.setBrush(QColor("#ffffff"))
        for point in g["handles_vp"].values():
            painter.drawEllipse(point, 5.5, 5.5)
        return True


class ArrowTool(Tool):
    id = "arrow"
    title = "Arrow"
    action_text = "Arrow Tool"
    icon = "arrow"
    tooltip = "Draw arrow (A) · drag its end points to adjust it"
    keywords = "line pointer draw annotation"
    shortcuts = ("A",)
    order = 20.0
    cursor = Qt.CrossCursor
    gizmo = "handles"
    uses_brush_color = True
    color_option = "color"
    width_option = "thickness"
    creates = "annotation_line"
    option_specs = (
        Attr("color", "color", [255, 64, 64], label="Color"),
        Attr("thickness", "float", 4.0, minimum=0.5, maximum=200.0, soft_min=0.5, soft_max=40.0, step=0.5,
             precision=1, unit="px", label="Width"),
        Attr("stroke_style", "enum", "solid", choices=LINE_STYLES, label="Line"),
        Attr("arrow", "flag", 1, label="End arrowhead"),
        Attr("arrow_start", "flag", 0, label="Start arrowhead"),
    )
    options_title = "Arrow options"
    hint = "Drag to draw arrows (Shift: snap 45°) · drag the selected one's end points · Esc / V to finish"

    def __init__(self, ui: Any) -> None:
        super().__init__(ui)
        self.op: dict[str, Any] | None = None

    @property
    def arrow_end(self) -> bool:
        return bool(self.options.get("arrow"))

    @property
    def arrow_start(self) -> bool:
        return bool(self.options.get("arrow_start"))

    @property
    def busy(self) -> bool:
        return self.op is not None

    def cancel(self) -> bool:
        if self.op is not None:
            self.op = None
            return True
        return False

    def banner(self, view: Any) -> str | None:
        return "➚  Arrow · drag to draw (Shift snaps 45°)   —   Esc / V to finish"

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind == "press" and e.button == Qt.LeftButton:
            self.op = {"start": e.scene_pos, "end": e.scene_pos, "view": e.view}
            return HANDLED
        if self.op is None:
            return None
        if e.kind == "move":
            start = self.op["start"]
            end = e.view.to_scene(e.pos)
            if e.shift:
                dx, dy = end.x() - start.x(), end.y() - start.y()
                angle = round(math.atan2(dy, dx) / (math.pi / 4.0)) * (math.pi / 4.0)
                length = math.hypot(dx, dy)
                end = QPointF(start.x() + length * math.cos(angle), start.y() + length * math.sin(angle))
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
                self.app.run_command("arrow.create", start.x(), start.y(), end.x(), end.y(),
                                     **self.options.as_dict())
            return HANDLED
        return None

    def paint_overlay(self, view: Any, painter: QPainter) -> None:
        if self.op is None or self.op["view"] is not view:
            return
        start = view.to_viewport(self.op["start"])
        end = view.to_viewport(self.op["end"])
        width = max(1.0, float(self.options.get("thickness")) * view.zoom_level())
        paint.paint_line(painter, start, end, _widgets.color_from(self.options.get("color")), width, Qt.SolidLine,
                         self.arrow_start, self.arrow_end)

    def options_panel(self, parent: QWidget) -> QWidget:
        brush = self.ui.service("brush")
        panel = brush.make_tool_panel(self, parent, names=("stroke_style",), recent=False)
        layout = panel.layout()
        panel.arrow_start_btn = arrowhead_button(self, panel, "arrow_start", "←",
                                                 "Arrowhead at the start of new arrows")
        panel.arrow_end_btn = arrowhead_button(self, panel, "arrow", "→", "Arrowhead at the end of new arrows")
        panel.arrows_row = w.hrow(panel, QLabel("Arrowheads", panel), panel.arrow_start_btn, panel.arrow_end_btn)
        layout.addWidget(panel.arrows_row)
        panel.recent_row = brush.make_recent_row(panel)  # type: ignore[attr-defined]
        layout.addWidget(panel.recent_row)
        return panel


def arrowhead_button(tool: Any, parent: QWidget, option: str, text: str, tip: str) -> Any:
    """A toggle bound to one of a tool's arrowhead (flag) options."""
    button = w.make_tool_button(text, tip + " (right-click: defaults)", True, parent=parent)
    button.setChecked(bool(tool.options.get(option)))
    button.toggled.connect(lambda on: tool.options.set(option, int(on)))
    handle = tool.options.watch(lambda name, value: name == option and button.setChecked(bool(value)))
    button.destroyed.connect(lambda *_a: handle.dispose())
    w.install_context_menu(button, lambda: w.fields.option_menu(tool, option))
    return button


def activate_arrow_ui(ui: Any) -> None:
    def factory(_canvas: Any, node: dict[str, Any]) -> LineItem:
        item = LineItem(node["name"])
        item.apply_attrs(node.get("attrs", {}))
        return item

    ui.node_ui("annotation_line", factory=factory, label="Arrow / line", icon="arrow", badge=("#d27b7b", "A"))
    ui.register_tool(ArrowTool(ui))
    ui.contribute("gizmo.handles", LineHandles())
    ui.contribute("stroke.styles", StrokeStyle("annotation_line", color_attr="color", arrows=True))
