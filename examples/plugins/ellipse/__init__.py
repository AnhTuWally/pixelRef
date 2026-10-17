"""Example plugin: ellipses.

Shows the layers of a pixelRef feature:

1. ``activate(ctx)`` is headless. It registers the ``ellipse`` node type (its
   attributes are the whole data model) and the ``ellipse.create`` command.
   This half also works in batch mode and from ``pxcmd``.
2. ``activate_ui(ui)`` is the Qt half. It says how an ellipse is drawn (a node
   UI factory) and adds the Ellipse tool. The Attribute Editor needs no code:
   it is generated from the attribute schema.
3. Everything is registered through the context objects, so disabling the
   plugin in Window > Plugins... removes it cleanly. Ellipses already on the
   board keep their data and show as placeholders until it is enabled again.

Try it::

    python -m pixelref --plugin-path examples/plugins
    # then press O and drag, or in the Script Editor:
    pxcmd.ellipse.create(0, 0, 200, 120, fill_color=[80, 160, 255], fill_opacity=0.5)
"""

from __future__ import annotations

import math
from typing import Any

from pixelref.plugin_api import CAP_COLORABLE, CAP_CONTENT, CAP_ERASABLE, Attr, NodeType

ELLIPSE_TYPE = NodeType(
    name="ellipse",
    inherits="canvas_node",
    label="Ellipse",
    color_attr="stroke_color",
    capabilities=frozenset({CAP_CONTENT, CAP_ERASABLE, CAP_COLORABLE}),
    # Lets the kernel (and so batch scripts) know an ellipse's extent without the UI.
    compute={"local_bounds": lambda _session, node: [0.0, 0.0, *map(float, node.attrs.get("size", [100.0, 60.0]))]},
    attrs=(
        Attr("size", "float2", [100.0, 60.0], group="Shape", doc="Width and height of the ellipse"),
        Attr("stroke_color", "color", [255, 64, 64], group="Stroke"),
        Attr("thickness", "float", 2.0, minimum=0.5, maximum=200.0, group="Stroke"),
        Attr("fill_color", "color", [0, 0, 0], group="Fill"),
        Attr("fill_opacity", "float", 0.0, minimum=0.0, maximum=1.0, group="Fill"),
    ),
    doc="An ellipse annotation (example plugin)",
)


def activate(ctx: Any) -> None:
    ctx.register_node_type(ELLIPSE_TYPE)
    session = ctx.session

    def create(x: float, y: float, width: float, height: float, name: str = "Ellipse", **attrs: Any) -> str:
        """Create an ellipse whose bounding box starts at (x, y); extra keywords are attributes."""
        final = session.scene.unique_name(name)
        session.scene.create_node("ellipse", final, attrs={
            "translate": [round(float(x), 3), round(float(y), 3)],
            "size": [round(abs(float(width)), 3), round(abs(float(height)), 3)],
            **attrs,
        })
        session.selection.set([final])
        return final

    # Registered as ``ellipse.create``: commands are namespaced by plugin id.
    ctx.command("create", create, alias="create_ellipse", label="Draw Ellipse")


def activate_ui(ui: Any) -> None:
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
    from PySide6.QtWidgets import QGraphicsItem, QVBoxLayout, QWidget

    from pixelref.plugin_api.ui import HANDLED, NodeItemMixin, PointerEvent, Tool, widgets

    class EllipseItem(NodeItemMixin, QGraphicsItem):
        """Draws one ellipse node; rebuilt from the node's attributes whenever they change."""

        def __init__(self, node_name: str) -> None:
            super().__init__()
            self._init_node(node_name, "ellipse", None)
            self.setFlag(QGraphicsItem.ItemIsSelectable, True)
            self.setFlag(QGraphicsItem.ItemIsMovable, True)
            self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
            self.attrs: dict[str, Any] = {}

        def apply_attrs(self, attrs: dict[str, Any]) -> None:
            self.prepareGeometryChange()
            self.attrs = dict(attrs)
            self.update()

        def content_rect(self) -> QRectF:
            width, height = self.attrs.get("size", [100.0, 60.0])
            return QRectF(0.0, 0.0, max(1.0, float(width)), max(1.0, float(height)))

        def boundingRect(self) -> QRectF:  # noqa: N802
            pad = float(self.attrs.get("thickness", 2.0)) / 2.0 + 2.0
            return self.content_rect().adjusted(-pad, -pad, pad, pad)

        def shape(self) -> QPainterPath:
            path = QPainterPath()
            path.addEllipse(self.content_rect())
            return path

        def itemChange(self, change, value):  # noqa: ANN001, N802
            if change == QGraphicsItem.ItemPositionHasChanged:
                self._notify_moved()
            return super().itemChange(change, value)

        def paint_content(self, painter: QPainter, _option, widget=None) -> None:  # noqa: ANN001
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(widgets.color_from(self.attrs.get("stroke_color", [255, 64, 64])),
                                float(self.attrs.get("thickness", 2.0))))
            fill = QColor(widgets.color_from(self.attrs.get("fill_color", [0, 0, 0])))
            fill.setAlphaF(max(0.0, min(1.0, float(self.attrs.get("fill_opacity", 0.0)))))
            painter.setBrush(QBrush(fill) if fill.alpha() else Qt.NoBrush)
            painter.drawEllipse(self.content_rect())

    class EllipseTool(Tool):
        id = "ellipse"
        title = "Ellipse"
        action_text = "Ellipse Tool"
        icon = "ellipse"
        tooltip = "Draw ellipses (O) · Shift: circle"
        keywords = "circle oval round shape draw"
        shortcuts = ("O",)
        order = 15.0
        cursor = Qt.CrossCursor
        uses_brush_color = True
        options_title = "Ellipse options"
        hint = "Drag to draw ellipses (Shift: circle) · Esc / V to finish"

        def __init__(self, ui_context: Any) -> None:
            super().__init__(ui_context)
            self.op: dict[str, Any] | None = None

        @property
        def busy(self) -> bool:
            return self.op is not None

        def cancel(self) -> bool:
            had_op, self.op = self.op is not None, None
            return had_op

        def pointer(self, e: PointerEvent) -> str | None:
            if e.kind == "press" and e.button == Qt.LeftButton:
                self.op = {"start": e.scene_pos, "end": e.scene_pos, "view": e.view}
                return HANDLED
            if self.op is None:
                return None
            if e.kind == "move":
                start, end = self.op["start"], e.view.to_scene(e.pos)
                if e.shift:
                    side = max(abs(end.x() - start.x()), abs(end.y() - start.y()))
                    end = QPointF(start.x() + math.copysign(side, end.x() - start.x() or 1.0),
                                  start.y() + math.copysign(side, end.y() - start.y() or 1.0))
                self.op["end"] = end
                e.view.viewport().update()
                return HANDLED
            if e.kind == "release":
                op, self.op = self.op, None
                e.view.viewport().update()
                rect = QRectF(op["start"], op["end"]).normalized()
                if max(rect.width(), rect.height()) * e.view.zoom_level() > 4:
                    brush = ui.service("brush")
                    # The tool only runs a command: exactly what a script would do.
                    self.app.run_command("ellipse.create", rect.x(), rect.y(), rect.width(), rect.height(),
                                         stroke_color=brush.rgb(), thickness=brush.width)
                return HANDLED
            return None

        def paint_overlay(self, view: Any, painter: QPainter) -> None:
            if self.op is None or self.op["view"] is not view:
                return
            brush = ui.service("brush")
            painter.setPen(QPen(brush.color, max(1.0, brush.width * view.zoom_level())))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QRectF(view.to_viewport(self.op["start"]), view.to_viewport(self.op["end"])))

        def options_panel(self, parent: QWidget) -> QWidget:
            panel = QWidget(parent)
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(0, 0, 0, 0)
            panel.brush_row = ui.service("brush").make_brush_row(panel)
            layout.addWidget(panel.brush_row)
            return panel

    def factory(_canvas: Any, node: dict[str, Any]) -> EllipseItem:
        item = EllipseItem(node["name"])
        item.apply_attrs(node.get("attrs", {}))
        return item

    ui.node_ui("ellipse", factory=factory, label="Ellipse", icon="ellipse", badge=("#7fb2f0", "O"))
    ui.register_tool(EllipseTool(ui))
