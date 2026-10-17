"""Qt side of the pen plugin: items, the Pen tool and drawing sessions."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QLabel, QPushButton, QVBoxLayout, QWidget

from pixelref.plugin_api import Attr
from pixelref.plugin_api.contracts import InspectorRows, SelectionInfo, StrokeStyle
from pixelref.plugin_api.ui import HANDLED, PointerEvent, Tool, paint, theme
from pixelref.plugin_api.ui import widgets as w

from . import DRAWING, STROKE
from .items import DrawingItem, IncrementalStrokeSmoother, StrokeItem


class DrawingSession:
    """While the pen (or eraser) is active, strokes go into one drawing."""

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.active = False
        self.target: str | None = None
        self.pending_target: str | None = None

    def drawing_item(self) -> DrawingItem | None:
        if not self.active or not self.target:
            return None
        item = self.ui.canvas.item(self.target)
        return item if isinstance(item, DrawingItem) else None

    def begin(self) -> None:
        # A plain pen session always starts a new drawing (created on the first
        # stroke); continuing an existing one is explicit via edit_drawing().
        target, self.pending_target = self.pending_target, None
        scene = self.ui.session.scene
        if target is not None and (not scene.has(target) or scene.get_node(target).node_type != DRAWING):
            target = None
        self.active = True
        self.target = target
        item = self.drawing_item()
        if item is not None:
            item.set_editing(True)
        if self.ui.session.selection.names:
            self.ui.app.run_command("select", clear=True)

    def end(self) -> None:
        target = self.target
        item = self.drawing_item()
        if item is not None:
            item.set_editing(False)
        self.active = False
        self.target = None
        scene = self.ui.session.scene
        if not target or not scene.has(target):
            return
        if not scene.children_of(target):
            self.ui.app.run_command("clipboard.delete", [target])
            return
        self.ui.app.run_command("select", [target])
        self.ui.status(f"Finished {target} · double-click it to keep drawing")

    def on_tool_changed(self, old: Tool | None, new: Tool | None) -> None:
        mode = getattr(new, "drawing_session", None) if new is not None else None
        drawing_mode = mode == "start" or (mode == "continue" and self.active)
        if drawing_mode and not self.active:
            self.begin()
        elif not drawing_mode and self.active:
            self.end()


class PenTool(Tool):
    id = "pen"
    title = "Pen"
    action_text = "Pen (Free Draw)"
    icon = "pen"
    tooltip = "Free draw (P) · each session makes a new drawing"
    keywords = "draw brush freehand sketch pencil"
    shortcuts = ("P",)
    order = 30.0
    cursor = Qt.CrossCursor
    uses_brush_color = True
    color_option = "color"
    width_option = "thickness"
    creates = STROKE
    option_specs = (
        Attr("color", "color", [255, 64, 64], label="Color"),
        Attr("thickness", "float", 4.0, minimum=0.5, maximum=200.0, soft_min=0.5, soft_max=40.0, step=0.5,
             precision=1, unit="px", label="Width"),
        Attr("smoothing", "float", 0.5, minimum=0.0, maximum=1.0, step=0.05, precision=0, unit="%",
             display_scale=100.0, label="Smooth",
             doc="Smoothing for new strokes (change it per stroke later in the Attribute Editor)"),
        Attr("arrow", "flag", 0, label="End arrowhead"),
        Attr("arrow_start", "flag", 0, label="Start arrowhead"),
    )
    drawing_session = "start"
    options_title = "Pen options"
    hint = "Drag to draw freehand · brush color/width in the Tools view · Esc or V to stop"

    def __init__(self, ui: Any, session: DrawingSession) -> None:
        super().__init__(ui)
        self.drawing = session
        self.op: dict[str, Any] | None = None

    @property
    def smoothing(self) -> float:
        return float(self.options.get("smoothing"))

    @property
    def arrow(self) -> bool:
        return bool(self.options.get("arrow"))

    @property
    def arrow_start(self) -> bool:
        return bool(self.options.get("arrow_start"))

    @property
    def width(self) -> float:
        return float(self.options.get("thickness"))

    def color(self) -> Any:
        return w.color_from(self.options.get("color"))

    @property
    def busy(self) -> bool:
        return self.op is not None

    def cancel(self) -> bool:
        if self.op is not None:
            self.op = None
            self.app.update_views()
            return True
        return False

    def banner(self, view: Any) -> str | None:
        item = self.drawing.drawing_item()
        name = item.node_name if item is not None else "new drawing"
        return f"✎  Pen · drawing into {name}   —   E eraser · Esc / V to finish"

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind == "press" and e.button == Qt.LeftButton:
            smoother = IncrementalStrokeSmoother(self.smoothing, max(1.5, self.width * 0.5))
            smoother.add(e.scene_pos)
            self.op = {"points": [e.scene_pos], "last": e.pos, "smoother": smoother, "view": e.view}
            e.view.viewport().update()
            return HANDLED
        op = self.op
        if op is None:
            return None
        if e.kind == "move":
            # Skip sub-pixel jitter; the stroke is simplified again on release.
            if math.hypot(e.pos.x() - op["last"].x(), e.pos.y() - op["last"].y()) >= 1.5:
                point = e.view.to_scene(e.pos)
                op["points"].append(point)
                op["smoother"].add(point)
                op["last"] = e.pos
                e.view.viewport().update(self._tail_rect(op, e.view))
            return HANDLED
        if e.kind == "release":
            self.op = None
            points = op["points"]
            if math.hypot(e.pos.x() - op["last"].x(), e.pos.y() - op["last"].y()) >= 0.5:
                points.append(e.view.to_scene(e.pos))
            target = self.drawing.target if self.drawing.target and self.ui.session.scene.has(self.drawing.target) \
                else None
            result = self.app.run_command(
                "pen.add_stroke", [[round(p.x(), 3), round(p.y(), 3)] for p in points], target,
                self.options.get("color"), self.width, round(self.smoothing, 2), int(self.arrow),
                int(self.arrow_start),
                0.6 / max(e.view.zoom_level(), 1e-6),
            )
            if result and self.drawing.active:
                self.drawing.target = result[0]
            e.view.viewport().update()
            return HANDLED
        return None

    def _tail_rect(self, op: dict[str, Any], view: Any) -> Any:
        """The viewport area the live preview changed in since the last move: only the
        last 2r+1 smoothed samples move, so repainting the whole board (every stroke
        on it) per mouse move is wasted. Includes the previous tail to erase it."""
        smoother = op["smoother"]
        tail = smoother.dense[-(2 * smoother.radius + 3):] + smoother.raw[-2:]
        zoom = max(view.zoom_level(), 1e-6)
        width = max(1.0 / zoom, self.width)
        pad = width / 2.0 + (max(10.0, width * 4.0) + width if self.arrow else 0.0)
        mapped = [view.to_viewport(p) for p in tail]
        xs, ys = [p.x() for p in mapped], [p.y() for p in mapped]
        pad_px = pad * zoom + 3.0
        rect = QRectF(min(xs) - pad_px, min(ys) - pad_px, max(xs) - min(xs) + 2 * pad_px,
                      max(ys) - min(ys) + 2 * pad_px).toAlignedRect()
        dirty = rect.united(op.get("dirty", rect))
        op["dirty"] = rect
        return dirty

    def paint_overlay(self, view: Any, painter: QPainter) -> None:
        item = self.drawing.drawing_item()
        if item is not None and item.strokes() and view.display.show_overlays:
            painter.setPen(QPen(theme.ACCENT, 1.0, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawPolygon(QPolygonF([view.to_viewport(p) for p in item.scene_polygon()]))
        op = self.op
        if op is None or op["view"] is not view:
            return
        # Live preview, smoothed incrementally in scene units and drawn through
        # the view transform (the cost stays flat however long the stroke gets).
        color = self.color()
        smoother = op["smoother"]
        points = smoother.points()
        width = max(1.0 / max(view.zoom_level(), 1e-6), self.width)
        painter.save()
        painter.setWorldTransform(view.viewportTransform())
        painter.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(smoother.path())
        size = max(10.0, width * 4.0)
        if len(points) > 1 and (self.arrow or self.arrow_start):
            painter.setPen(QPen(color, max(1.0, width * 0.5), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(color)
            if self.arrow:
                painter.drawPolygon(paint.arrow_head(paint.point_back_from_end(points, size), points[-1], size))
            if self.arrow_start:
                head = paint.point_back_from_end(list(reversed(points[:400])), size)
                painter.drawPolygon(paint.arrow_head(head, points[0], size))
        painter.restore()

    def options_panel(self, parent: QWidget) -> QWidget:
        from ..arrow.ui import arrowhead_button

        brush = self.ui.service("brush")
        panel = brush.make_tool_panel(self, parent, names=("smoothing",), recent=False)
        layout = panel.layout()
        panel.smooth_field = panel.form.editors["smoothing"]  # type: ignore[attr-defined]
        panel.arrow_start_btn = arrowhead_button(self, panel, "arrow_start", "←",
                                                 "Arrowhead at the start of new strokes")
        panel.arrow_end_btn = arrowhead_button(self, panel, "arrow", "→", "Arrowhead at the end of new strokes")
        panel.arrows_row = w.hrow(panel, QLabel("Arrowheads", panel), panel.arrow_start_btn, panel.arrow_end_btn)
        layout.addWidget(panel.arrows_row)
        panel.recent_row = brush.make_recent_row(panel)  # type: ignore[attr-defined]
        layout.addWidget(panel.recent_row)
        return panel


class DrawingRows(QWidget):
    """Extra rows in the Stroke section for drawings."""

    def __init__(self, ui: Any, parent: QWidget) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.drawing_hint = QLabel("Style changes apply to every stroke in the drawing", self)
        self.drawing_hint.setObjectName("pathLabel")
        self.drawing_hint.setWordWrap(True)
        self.edit_drawing_btn = QPushButton("Edit drawing", self)
        self.edit_drawing_btn.setObjectName("toolBtn")
        self.edit_drawing_btn.setToolTip("Add or erase strokes in this drawing (Shift+P / double-click)")
        self.edit_drawing_btn.clicked.connect(lambda: ui.app.trigger("pen.edit_drawing"))
        self.separate_drawing_btn = QPushButton("Separate strokes", self)
        self.separate_drawing_btn.setObjectName("toolBtn")
        self.separate_drawing_btn.setToolTip("Split into individual strokes (Ctrl+Shift+G); Ctrl+J combines again")
        self.separate_drawing_btn.clicked.connect(lambda: ui.app.trigger("grouping.ungroup"))
        layout.addWidget(self.drawing_hint)
        layout.addWidget(w.hrow(self, self.edit_drawing_btn, self.separate_drawing_btn))

    def refresh(self, info: SelectionInfo) -> None:
        self.setVisible(info.single and info.node["type"] == DRAWING)


def activate_pen_ui(ui: Any) -> None:
    session = ui.session
    canvas = ui.canvas
    drawing = DrawingSession(ui)
    ui.provide_service("drawing_session", drawing)
    tool = PenTool(ui, drawing)

    def stroke_factory(_canvas: Any, node: dict[str, Any]) -> StrokeItem:
        item = StrokeItem(node["name"])
        item.apply_attrs(node.get("attrs", {}))
        return item

    def drawing_factory(_canvas: Any, node: dict[str, Any]) -> DrawingItem:
        item = DrawingItem(node["name"])
        item.set_editing(drawing.active and node["name"] == drawing.target)
        return item

    def stroke_post_sync(item: Any, _node: dict[str, Any]) -> None:
        if isinstance(item.parentItem(), DrawingItem):
            # Strokes are picked, moved and selected through their drawing.
            item.setFlag(QGraphicsItem.ItemIsSelectable, False)
            item.setFlag(QGraphicsItem.ItemIsMovable, False)
            item.setAcceptedMouseButtons(Qt.NoButton)

    def drawing_post_sync(item: Any, node: dict[str, Any]) -> None:
        item.children_changed()
        item.set_editing(drawing.active and node["name"] == drawing.target)

    def describe(name: str, node: dict[str, Any]) -> list[str]:
        count = sum(1 for c in node.get("children", []) if canvas.nodes.get(c, {}).get("type") == STROKE)
        return [f"{count} stroke{'s' if count != 1 else ''}"]

    def edit_drawing(name: str | None = None) -> None:
        """Re-open a drawing so strokes can be added to it or erased (Shift+P)."""
        name = name or (session.selection.names[0] if len(session.selection.names) == 1 else None)
        if not name or not session.scene.has(name) or session.scene.get_node(name).node_type != DRAWING:
            return
        if drawing.active:
            ui.tools.activate(ui.tools.default_id)
        drawing.pending_target = name
        ui.tools.activate("pen")

    def double_click(view: Any, item: Any) -> bool:
        if isinstance(item, DrawingItem) and not item.locked:
            edit_drawing(item.node_name)
            return True
        return False

    ui.node_ui(STROKE, factory=stroke_factory, label="Pen stroke", icon="pen", badge=("#d27b7b", "P"),
               post_sync=stroke_post_sync)
    ui.node_ui(DRAWING, factory=drawing_factory, label="Drawing", icon="pen", badge=("#e0a060", "D"),
               post_sync=drawing_post_sync, describe=describe, double_click=double_click)
    ui.register_tool(tool)
    ui.track(ui.tools.watch(drawing.on_tool_changed))
    ui.contribute("stroke.styles", StrokeStyle(STROKE, color_attr="color", arrows=True, smoothing=True))
    ui.contribute("stroke.styles", StrokeStyle(DRAWING, color_attr="color", arrows=True, smoothing=True,
                                               children_type=STROKE))
    ui.contribute("inspector.rows", InspectorRows("stroke", 90, lambda parent, _i: DrawingRows(ui, parent)))
    ui.context_key("pen.session_active", lambda: drawing.active)

    ui.command("edit_drawing", edit_drawing, undoable=False)
    ui.action("pen.edit_drawing", "pen.edit_drawing", "Edit Drawing", keywords="pen strokes modify", category="Create")
    ui.action("pen.combine", "pen.combine", "Combine into Drawing", keywords="merge join strokes", category="Create")
    ui.bind("Shift+P", "pen.edit_drawing")
    ui.bind("Ctrl+J", "pen.combine")

    def single_drawing() -> bool:
        names = session.selection.names
        return len(names) == 1 and canvas.nodes.get(names[0], {}).get("type") == DRAWING

    def can_combine() -> bool:
        names = session.scene.top_level(session.selection.names)
        return len(names) > 1 and bool(ui.headless.combinable(names))

    ui.menu("canvas", "pen.edit_drawing", group="transform", order=10, when=single_drawing)
    ui.menu("canvas", "pen.combine", group="transform", order=11, when=can_combine)
