"""Qt side of steps: the marker item, its tail handle and the Step tool."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsItem

from pixelref.plugin_api import Attr
from pixelref.plugin_api.ui import HANDLED, NodeItemMixin, PointerEvent, Tool, theme
from pixelref.plugin_api.ui import widgets as _widgets

from . import LABEL_STYLES, SHAPES, STEP_TYPE, label_for

color_from = _widgets.color_from
DEFAULTS = {spec.name: spec.default for spec in STEP_TYPE.attrs}
MIN_TAIL = 2.0  # local units: a shorter tail is no tail


def marker_path(radius: float, shape: str, tail: QPointF) -> QPainterPath:
    """The marker's outline, centred on (0, 0), with a pointer to ``tail`` when it's outside."""
    path = QPainterPath()
    box = QRectF(-radius, -radius, 2 * radius, 2 * radius)
    if shape == "square":
        path.addRect(box)
    elif shape == "rounded":
        path.addRoundedRect(box, radius * 0.4, radius * 0.4)
    else:
        path.addEllipse(box)
    length = math.hypot(tail.x(), tail.y())
    if length > radius + MIN_TAIL:
        # A wedge from the marker's centre to the tip, its base as wide as ~0.9 r.
        nx, ny = -tail.y() / length, tail.x() / length
        half = radius * 0.45
        wedge = QPainterPath()
        wedge.addPolygon(QPolygonF([QPointF(nx * half, ny * half), tail, QPointF(-nx * half, -ny * half)]))
        wedge.closeSubpath()
        path = path.united(wedge)
    return path


def paint_marker(painter: QPainter, values: dict[str, Any]) -> None:
    """Draw a marker from attribute ``values`` (any missing ones are the defaults)."""
    v = {**DEFAULTS, **values}
    radius = max(1.0, float(v["radius"]))
    tail = QPointF(float(v["tail"][0]), float(v["tail"][1]))
    path = marker_path(radius, str(v["shape"]), tail)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    border = float(v["border"])
    painter.setPen(QPen(color_from(v["border_color"]), border, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                   if border > 0 else Qt.NoPen)
    painter.setBrush(color_from(v["fill_color"]))
    painter.drawPath(path)
    text = label_for(int(v["number"]), str(v["label_style"]))
    font = QFont(painter.font())
    font.setBold(True)
    # Lay out at a fixed size and scale the painter: pixel sizes are whole numbers.
    font.setPixelSize(100)
    metrics = QFontMetricsF(font)
    width = max(1.0, metrics.horizontalAdvance(text))
    height = metrics.ascent() * 0.72  # cap height, roughly
    # Capitals half the marker's height; long labels shrink to fit its width.
    scale = min(radius / height, radius * 1.45 / width)
    painter.save()
    painter.scale(scale, scale)
    painter.setFont(font)
    painter.setPen(color_from(v["text_color"]))
    painter.drawText(QPointF(-width / 2.0, height / 2.0), text)
    painter.restore()


class StepItem(NodeItemMixin, QGraphicsItem):
    def __init__(self, node_name: str) -> None:
        super().__init__()
        self._init_node(node_name, "annotation_step", None)
        self._values: dict[str, Any] = dict(DEFAULTS)
        self._path = QPainterPath()
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def apply_attrs(self, attrs: dict[str, Any]) -> None:
        self.prepareGeometryChange()
        self._values = {name: attrs.get(name, default) for name, default in DEFAULTS.items()}
        self._path = marker_path(self.radius(), str(self._values["shape"]), self.tail())
        self.update()

    def radius(self) -> float:
        return max(1.0, float(self._values["radius"]))

    def tail(self) -> QPointF:
        tail = self._values["tail"]
        return QPointF(float(tail[0]), float(tail[1]))

    def set_tail(self, point: QPointF) -> None:
        self.prepareGeometryChange()
        if math.hypot(point.x(), point.y()) <= self.radius() + MIN_TAIL:
            point = QPointF(0.0, 0.0)
        self._values["tail"] = [round(point.x(), 3), round(point.y(), 3)]
        self._path = marker_path(self.radius(), str(self._values["shape"]), point)
        self.update()
        self._notify_moved()

    def commit_values(self) -> dict[str, Any]:
        values = self.transform_attrs()
        values["tail"] = list(self._values["tail"])
        return values

    def content_rect(self) -> QRectF:
        r = self.radius()
        return QRectF(-r, -r, 2 * r, 2 * r).united(QRectF(self.tail(), self.tail()).adjusted(-0.5, -0.5, 0.5, 0.5))

    def boundingRect(self) -> QRectF:
        pad = float(self._values["border"]) / 2.0 + 1.0
        return self._path.boundingRect().adjusted(-pad, -pad, pad, pad)

    def shape(self) -> QPainterPath:
        return self._path

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        return super().itemChange(change, value)

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        paint_marker(painter, self._values)


class StepHandles:
    """A selected step has one handle, for its pointer (like ShareX): at the pointer's
    tip, or on the marker's edge to pull one out. The size is an attribute, so
    there are no scale or rotate handles to crowd a small marker."""

    node_type = "annotation_step"
    labels = {"step_tail": "Edit step pointer"}
    PULL_OFFSET = 7.0  # viewport pixels outside the marker's edge

    def build(self, gizmo: Any, view: Any, item: Any, g: dict[str, Any]) -> None:
        st = item.sceneTransform()
        tail = item.tail()
        g["step_has_tail"] = bool(tail.x() or tail.y())
        if g["step_has_tail"]:
            point = view.to_viewport(st.map(tail))
        else:
            # Bottom-right on the edge, a little outside it.
            center = view.to_viewport(st.map(QPointF(0.0, 0.0)))
            edge = view.to_viewport(st.map(QPointF(item.radius() * 0.7071, item.radius() * 0.7071)))
            direction = edge - center
            length = math.hypot(direction.x(), direction.y()) or 1.0
            point = edge + direction / length * self.PULL_OFFSET
        g["handles_vp"] = {"tail": point}

    def hit(self, gizmo: Any, view: Any, g: dict[str, Any], pos: QPointF) -> str | None:
        point = g["handles_vp"].get("tail")
        if point is not None and math.hypot(pos.x() - point.x(), pos.y() - point.y()) <= gizmo.HANDLE_HIT:
            return "tail"
        return ""  # nothing else: no scale handles or rotate zones

    def start(self, gizmo: Any, handle: str, g: dict[str, Any], item: Any) -> str | None:
        return "step_tail" if handle == "tail" else None

    def cursor(self, gizmo: Any, handle: str, g: dict[str, Any], view: Any) -> Any:
        return Qt.SizeAllCursor if handle == "tail" else None

    def drag(self, gizmo: Any, op: dict[str, Any], m: QPointF, modifiers: Any, view: Any) -> None:
        item, s0, _l0, _parent_inv, _rotation = op["targets"][0]
        item.set_tail(s0.inverted()[0].map(m))
        gizmo.ui.status("Step pointer · drop the tip on the marker to remove it")

    def paint(self, gizmo: Any, view: Any, painter: QPainter, g: dict[str, Any]) -> bool:
        point = g["handles_vp"].get("tail")
        if point is None:
            return True
        painter.setPen(QPen(theme.ACCENT, 1.5))
        if g.get("step_has_tail"):
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(point, 5.0, 5.0)
        else:  # a smaller, filled "pull me" point
            painter.setBrush(theme.ACCENT)
            painter.drawEllipse(point, 4.0, 4.0)
        return True


class StepTool(Tool):
    id = "step"
    title = "Step"
    action_text = "Step Tool"
    icon = "step"
    tooltip = "Numbered steps (N) · click to place the next number, drag to point at something"
    keywords = "number counter marker badge sequence callout"
    shortcuts = ("N",)
    order = 26.0
    cursor = Qt.CrossCursor
    gizmo = "handles"
    uses_brush_color = True
    color_option = "fill_color"
    creates = "annotation_step"
    option_specs = (
        Attr("fill_color", "color", DEFAULTS["fill_color"], label="Fill"),
        Attr("text_color", "color", DEFAULTS["text_color"], label="Text"),
        Attr("radius", "float", DEFAULTS["radius"], minimum=4.0, maximum=1000.0, soft_min=6.0, soft_max=80.0,
             step=1.0, precision=1, unit="px", label="Size"),
        Attr("label_style", "enum", "number", choices=LABEL_STYLES, label="Style"),
        Attr("shape", "enum", "circle", choices=SHAPES, label="Shape"),
        Attr("border_color", "color", DEFAULTS["border_color"], label="Border"),
        Attr("border", "float", DEFAULTS["border"], minimum=0.0, soft_min=0.0, soft_max=12.0, step=0.5,
             precision=1, unit="px", label="Border width"),
    )
    options_title = "Step options"
    hint = "Click to place the next step · drag to give it a pointer · Esc / V to finish"

    def __init__(self, ui: Any) -> None:
        super().__init__(ui)
        self.op: dict[str, Any] | None = None
        self._hover: tuple[Any, QPointF] | None = None

    @property
    def busy(self) -> bool:
        return self.op is not None

    def cancel(self) -> bool:
        if self.op is not None:
            self.op = None
            return True
        return False

    def deactivate(self) -> None:
        self._hover = None

    def next_number(self) -> int:
        return int(self.app.session.run("steps.next_number"))

    def banner(self, view: Any) -> str | None:
        return (f"Step · click to place {label_for(self.next_number(), str(self.options.get('label_style')))}"
                "  (drag to point at something)   —   Esc / V to finish")

    def hover(self, e: PointerEvent) -> None:
        self._hover = (e.view, e.scene_pos)
        e.view.viewport().update()

    def leave(self, view: Any) -> None:
        if self._hover is not None and self._hover[0] is view:
            self._hover = None
            view.viewport().update()

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind == "press" and e.button == Qt.LeftButton:
            self.op = {"press": e.scene_pos, "end": e.scene_pos, "view": e.view}
            return HANDLED
        if self.op is None:
            return None
        if e.kind == "move":
            self.op["end"] = e.view.to_scene(e.pos)
            self._hover = None
            e.view.viewport().update()
            return HANDLED
        if e.kind == "release":
            op, self.op = self.op, None
            view = op["view"]
            press, end = op["press"], view.to_scene(e.pos)
            options = self.options.as_dict()
            a, b = view.to_viewport(press), view.to_viewport(end)
            if math.hypot(a.x() - b.x(), a.y() - b.y()) > 6.0:
                # Dragged: the marker sits where the drag ended, its tail points where it began.
                self.app.run_command("steps.create", end.x(), end.y(),
                                     tail=[round(press.x() - end.x(), 3), round(press.y() - end.y(), 3)],
                                     **options)
            else:
                self.app.run_command("steps.create", press.x(), press.y(), **options)
            self._hover = (view, end)
            view.viewport().update()
            return HANDLED
        return None

    def paint_overlay(self, view: Any, painter: QPainter) -> None:
        if self.op is not None and self.op["view"] is view:
            center, tail = self.op["end"], self.op["press"] - self.op["end"]
        elif self._hover is not None and self._hover[0] is view:
            center, tail = self._hover[1], QPointF(0.0, 0.0)
        else:
            return
        values = {**self.options.as_dict(), "number": self.next_number(), "tail": [tail.x(), tail.y()]}
        painter.save()
        painter.setOpacity(0.55 if self.op is None else 0.85)
        painter.setTransform(QTransform.fromTranslate(center.x(), center.y()) * view.viewportTransform(), True)
        paint_marker(painter, values)
        painter.restore()


def activate_steps_ui(ui: Any) -> None:
    session = ui.session
    canvas = ui.canvas

    def factory(_canvas: Any, node: dict[str, Any]) -> StepItem:
        item = StepItem(node["name"])
        item.apply_attrs(node.get("attrs", {}))
        return item

    ui.node_ui("annotation_step", factory=factory, label="Step", icon="step", badge=("#eb4034", "N"))
    ui.register_tool(StepTool(ui))
    ui.contribute("gizmo.handles", StepHandles())

    def selected_steps() -> bool:
        return any(canvas.nodes.get(n, {}).get("type") == "annotation_step" for n in session.selection.names)

    ui.context_key("steps.selected", selected_steps)
    ui.action("steps.renumber", "steps.renumber", "Renumber Steps", keywords="number count order sequence fix gaps",
              category="Edit", icon="step")
    ui.menu("canvas", "steps.renumber", group="transform", order=45, when="steps.selected")
