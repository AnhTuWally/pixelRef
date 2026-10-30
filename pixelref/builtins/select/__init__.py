"""The Select tool and selection outlines."""

from __future__ import annotations

import inspect
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsItem

from pixelref.plugin_api.ui import DEFAULT, HANDLED, PointerEvent, Tool, theme


class SelectTool(Tool):
    id = "select"
    title = "Select"
    action_text = "Select Tool"
    icon = "select"
    tooltip = "Select / transform (V)"
    keywords = "pointer move cursor"
    shortcuts = ("V",)
    order = 0.0
    kind = "persistent"
    gizmo = "full"

    def __init__(self, ui: Any) -> None:
        super().__init__(ui)
        self.op: dict[str, Any] | None = None
        self.hover_item: Any = None

    @property
    def busy(self) -> bool:
        return self.op is not None

    def cancel(self) -> bool:
        if self.op is not None and self.op["kind"] == "rubber":
            self.op = None
            return True
        return False

    def deactivate(self) -> None:
        self.op = None
        self.hover_item = None

    def pointer(self, e: PointerEvent) -> str | None:
        canvas = self.ui.canvas
        if e.kind == "press":
            if e.button != Qt.LeftButton or e.alt:
                return None
            item = e.item
            if item is not None and getattr(item, "editing", False) and not item.captures_children:
                return DEFAULT  # clicks inside a note being edited place the text cursor
            if item is not None and item.flags() & QGraphicsItem.ItemIsSelectable:
                if e.shift:
                    self.ui.app.run_command("select", [item.node_name], toggle=True)
                    return HANDLED
                canvas.begin_interaction()
                self.op = {"kind": "move", "view": e.view, "press": e.scene_pos}
                return DEFAULT
            additive = bool(e.modifiers & (Qt.ControlModifier | Qt.ShiftModifier))
            initial = set(canvas.selected_canvas_items()) if additive else set()
            if not additive and self.ui.session.selection.names:
                self.ui.app.run_command("select", clear=True)
            self.op = {"kind": "rubber", "origin": e.pos, "rect": QRectF(), "initial": initial, "view": e.view}
            return HANDLED
        op = self.op
        if e.kind == "move":
            if op is None:
                return None
            if op["kind"] == "move":
                snap = self.ui.service("snap")
                if "snap" not in op:  # decided on the first move; without snapping Qt moves the items
                    op["snap"] = self._start_snap_move() if snap is not None and snap.active(e.ctrl) else None
                if op["snap"] is None:
                    return DEFAULT
                self._snap_move(op, e.scene_pos - op["press"], snap is not None and snap.active(e.ctrl))
                e.view.viewport().update()
                return HANDLED
            rect = QRectF(op["origin"], e.pos).normalized()
            op["rect"] = rect
            canvas.select_items(e.view.content_items_in(rect) | op["initial"])
            e.view.viewport().update()
            return HANDLED
        if e.kind == "release":
            if op is None:
                return None
            self.op = None
            if op["kind"] == "move":
                canvas.end_interaction("Move")
                if op.get("snap") is not None:
                    e.view.viewport().update()
                return DEFAULT
            e.view.viewport().update()
            return HANDLED
        if e.kind == "double":
            if e.button != Qt.LeftButton:
                return None
            item = e.item
            if item is None:
                return None
            node_ui = canvas.node_ui(item.node_type)
            if node_ui is not None and node_ui.double_click is not None:
                handler = node_ui.double_click
                try:  # handlers may take the clicked point: (view, item, scene_pos)
                    takes_pos = len(inspect.signature(handler).parameters) >= 3
                except (TypeError, ValueError):
                    takes_pos = False
                result = handler(e.view, item, e.scene_pos) if takes_pos else handler(e.view, item)
                if result == "default":
                    return DEFAULT
                if result:
                    return HANDLED
            return None
        return None

    # ---- Snapped moves (the snap plugin) -------------------------------------------------

    def _start_snap_move(self) -> dict[str, Any] | None:
        """Move the selection ourselves (not Qt) so the whole selection snaps as one."""
        targets = self.ui.canvas.transform_targets()
        if not targets:
            return None
        bounds = QRectF()
        moving = []
        for item in targets:
            bounds = bounds.united(item.scene_content_rect())
            parent = item.parentItem()
            parent_inv = parent.sceneTransform().inverted()[0] if parent is not None else QTransform()
            moving.append((item, item.sceneTransform(), parent_inv))
        return {"targets": moving, "bounds": bounds, "guides": ([], [])}

    def _snap_move(self, op: dict[str, Any], delta: QPointF, snapping: bool) -> None:
        state = op["snap"]
        snap = self.ui.service("snap")
        b = state["bounds"]
        dx, dy = delta.x(), delta.y()
        guides: tuple[list[float], list[float]] = ([], [])
        if snapping and snap is not None:
            view = op["view"]  # snap to the grid lines this view draws
            dx, dy = snap.delta((b.left(), b.top(), b.width(), b.height()), dx, dy, view)
            guides = ([x for x in (b.left() + dx, b.right() + dx) if snap.on_grid(x, view)],
                      [y for y in (b.top() + dy, b.bottom() + dy) if snap.on_grid(y, view)])
        state["guides"] = guides
        shift = QTransform.fromTranslate(dx, dy)
        for item, s0, parent_inv in state["targets"]:
            try:
                item.set_local_transform(s0 * shift * parent_inv)
            except RuntimeError:  # the item was replaced by a refresh
                pass
        x, y = b.left() + dx, b.top() + dy
        snapped = " · snapped to grid" if snapping else ""
        self.ui.status(f"Move to {x:.0f}, {y:.0f}{snapped} · Ctrl flips snapping")

    def hover(self, e: PointerEvent) -> None:
        hovered = e.item
        if hovered is not self.hover_item:
            self.hover_item = hovered
            e.view.viewport().update()

    def leave(self, view: Any) -> None:
        if self.hover_item is not None:
            self.hover_item = None
            view.viewport().update()

    def paint_overlay(self, view: Any, painter: QPainter) -> None:
        op = self.op
        if op is not None and op["kind"] == "rubber" and op["view"] is view and not op["rect"].isEmpty():
            painter.setPen(QPen(theme.ACCENT, 1.0))
            painter.setBrush(theme.RUBBER_FILL)
            painter.drawRect(op["rect"].adjusted(0.5, 0.5, -0.5, -0.5))
            return
        if op is not None and op.get("snap"):
            self._paint_guides(view, painter, op["snap"]["guides"])
            return
        hovered = self.hover_item
        if hovered is None or op is not None or not view.display.show_overlays:
            return
        if hovered in self.ui.canvas.outline_items():
            return
        try:
            polygon = QPolygonF([view.to_viewport(p) for p in hovered.scene_polygon()])
        except RuntimeError:  # the item was replaced by a refresh
            self.hover_item = None
            return
        painter.setPen(QPen(QColor(61, 142, 240, 140), 1.0, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(polygon)


    def _paint_guides(self, view: Any, painter: QPainter, guides: tuple[list[float], list[float]]) -> None:
        """The grid lines the moving selection snapped to, across the view."""
        xs, ys = guides
        if not xs and not ys:
            return
        size = view.viewport().rect()
        painter.setPen(QPen(theme.ACCENT, 1.0, Qt.DashLine))
        for x in xs:
            vx = view.to_viewport(QPointF(x, 0.0)).x()
            painter.drawLine(QPointF(vx, 0.0), QPointF(vx, size.height()))
        for y in ys:
            vy = view.to_viewport(QPointF(0.0, y)).y()
            painter.drawLine(QPointF(0.0, vy), QPointF(size.width(), vy))


def paint_outlines(ui: Any, view: Any, painter: QPainter) -> None:
    tool = ui.tools.active
    if tool is not None and not tool.show_selection:
        return
    transform = view.viewportTransform()  # once per frame, not per point (large selections)
    painter.setBrush(Qt.NoBrush)
    for item in ui.canvas.outline_items():
        if not view.display.shows(item.node_name):
            continue
        pen = QPen(theme.ACCENT if not item.locked else theme.MUTED, 1.0)
        if item.locked:
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        outline_path = getattr(item, "outline_path", None)  # e.g. links: their path, in scene space
        path = outline_path() if outline_path is not None else None
        if path is not None:
            painter.drawPath(transform.map(path))
            continue
        line = item.outline_line()
        if line is not None:
            painter.drawLine(transform.map(line[0]), transform.map(line[1]))
            continue
        painter.drawPolygon(transform.map(item.scene_polygon()))


def activate_ui(ui: Any) -> None:
    ui.register_tool(SelectTool(ui))
    ui.overlay(lambda view, painter: paint_outlines(ui, view, painter), order=40)
    canvas = ui.canvas

    def selectable_items() -> list[Any]:
        return [item for item in canvas.content_items() if not item.locked and item.isVisible()
                and not _covered_by_compound(item)]

    def select_all() -> list[str]:
        """Select every visible, unlocked item."""
        names = [item.node_name for item in selectable_items()]
        ui.run("select", names)
        ui.status(f"Selected {len(names)} item(s)")
        return names

    def invert_selection() -> list[str]:
        """Select every selectable item that isn't selected, and deselect the rest."""
        current = set(ui.session.selection.names)
        names = [item.node_name for item in selectable_items() if item.node_name not in current]
        ui.run("select", names)
        return names

    ui.command("all", select_all, alias="select_all", undoable=False)
    ui.command("invert", invert_selection, alias="invert_selection", undoable=False)
    ui.action("select.all", "select.all", "Select All", keywords="everything", category="Edit",
              icon="select_all")
    ui.action("select.invert", "select.invert", "Invert Selection", keywords="reverse opposite", category="Edit")
    ui.bind("Ctrl+A", "select.all")
    ui.bind("Ctrl+Shift+I", "select.invert")
    ui.menu("canvas", "select.all", group="select", order=0, when="selection.count == 0")

    def on_selection(_event: Any) -> None:
        names = ui.session.selection.names
        if len(names) == 1 and canvas.nodes.get(names[0], {}).get("type") == "image":
            ui.status("Drag corners to scale (Shift: free, Alt: from center) · drag outside a corner to rotate")
        elif len(names) > 1:
            ui.status(f"{len(names)} items selected")

    ui.on_ui("ui.selection_changed", on_selection)


def _covered_by_compound(item: Any) -> bool:
    parent = item.parentItem()
    while parent is not None:
        if getattr(parent, "captures_children", False):
            return True
        parent = parent.parentItem()
    return False
