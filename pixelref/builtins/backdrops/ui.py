"""Qt side of backdrops: the item, the draw tool, resize points, ``K``, and drag feedback.

The backdrop item draws a tinted frame with a title bar. Only the title bar and the border pick it,
so a click or rubber band inside the frame reaches what sits on it. Dragging things onto or off a
backdrop settles when the mouse is released (hold Alt then to move without joining or leaving).
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QApplication, QCheckBox, QGraphicsItem, QInputDialog, QLabel

from pixelref.plugin_api import Attr
from pixelref.plugin_api.contracts import InspectorSection, PaletteButton
from pixelref.plugin_api.ui import HANDLED, NodeItemMixin, PointerEvent, Tool, theme
from pixelref.plugin_api.ui import widgets as _widgets
from pixelref.plugin_api.ui import widgets as w

from . import BACKDROP, DEFAULT_SIZE

CORNERS = ("tl", "tr", "br", "bl")
EDGES = ("tm", "mr", "bm", "ml")
OPPOSITE = {"tl": "br", "tr": "bl", "br": "tl", "bl": "tr", "tm": "bm", "bm": "tm", "ml": "mr", "mr": "ml"}
MIN_HIT = 10.0  # border pick width (scene units)
TITLE_FACTOR = 1.8  # title bar height = title size × this
SNAP_MARGIN_PX = 4.0  # "touch" reaches this many screen pixels


def local_handle_points(r: QRectF) -> dict[str, QPointF]:
    cx, cy = r.center().x(), r.center().y()
    return {"tl": r.topLeft(), "tr": r.topRight(), "br": r.bottomRight(), "bl": r.bottomLeft(),
            "tm": QPointF(cx, r.top()), "bm": QPointF(cx, r.bottom()), "ml": QPointF(r.left(), cy),
            "mr": QPointF(r.right(), cy)}


class BackdropItem(NodeItemMixin, QGraphicsItem):
    """A titled, tinted frame. Its contents are child items drawn above it."""

    def __init__(self, node_name: str) -> None:
        super().__init__()
        self._init_node(node_name, BACKDROP, None)
        self._size = QPointF(*DEFAULT_SIZE)
        self._title = "Backdrop"
        self._title_size = 18.0
        self._color = QColor(90, 140, 200)
        self._fill_opacity = 0.18
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def apply_attrs(self, attrs: dict[str, Any]) -> None:
        self.prepareGeometryChange()
        size = attrs.get("size", DEFAULT_SIZE)
        self._size = QPointF(max(1.0, float(size[0])), max(1.0, float(size[1])))
        self._title = str(attrs.get("title", "Backdrop"))
        self._title_size = max(4.0, float(attrs.get("title_size", 18.0)))
        self._color = _widgets.color_from(attrs.get("color", [90, 140, 200]))
        self._fill_opacity = max(0.0, min(1.0, float(attrs.get("fill_opacity", 0.18))))
        self.update()

    # ---- Geometry -------------------------------------------------------------------------

    def header_height(self) -> float:
        return min(self._size.y(), self._title_size * TITLE_FACTOR)

    def header_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, self._size.x(), self.header_height())

    def content_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, self._size.x(), self._size.y())

    def boundingRect(self) -> QRectF:
        pad = MIN_HIT / 2.0 + 1.0
        return self.content_rect().adjusted(-pad, -pad, pad, pad)

    def shape(self) -> QPainterPath:
        """The title bar and the border: the frame's body lets clicks through to its contents."""
        border = QPainterPath()
        border.addRect(self.content_rect())
        stroker = QPainterPathStroker()
        stroker.setWidth(MIN_HIT)
        path = stroker.createStroke(border)
        path.addRect(self.header_rect())
        return path

    def set_rect_size(self, width: float, height: float) -> None:
        self.prepareGeometryChange()
        self._size = QPointF(max(1.0, float(width)), max(1.0, float(height)))
        self.update()
        self._notify_moved()

    def commit_values(self) -> dict[str, Any]:
        values = self.transform_attrs()
        values["size"] = [round(self._size.x(), 3), round(self._size.y(), 3)]
        return values

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        return super().itemChange(change, value)

    # ---- Painting -------------------------------------------------------------------------

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.content_rect()
        radius = min(10.0, rect.width() / 8.0, rect.height() / 8.0)
        body = QColor(self._color)
        body.setAlphaF(self._fill_opacity)
        edge = QColor(self._color)
        edge.setAlpha(190)
        painter.setPen(QPen(edge, 2.0))
        painter.setBrush(body)
        painter.drawRoundedRect(rect, radius, radius)
        header = QPainterPath()
        header.addRoundedRect(rect, radius, radius)
        clip = QPainterPath()
        clip.addRect(self.header_rect())
        title_bar = header.intersected(clip)
        bar = QColor(self._color)
        bar.setAlpha(150)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bar)
        painter.drawPath(title_bar)
        if not self._title:
            return
        font = QFont()
        font.setPixelSize(max(1, int(round(self._title_size))))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 235) if self._color.lightnessF() < 0.72 else QColor(20, 24, 32, 235))
        pad = self._title_size * 0.6
        text_rect = self.header_rect().adjusted(pad, 0.0, -pad, 0.0)
        elided = QFontMetricsF(font).elidedText(self._title, Qt.ElideRight, max(1.0, text_rect.width()))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, elided)


class BackdropHandles:
    """Corner and edge points change the backdrop's ``size`` (never its scale), so contents stay put."""

    node_type = BACKDROP
    labels = {"backdrop_resize": "Resize backdrop"}

    def build(self, gizmo: Any, view: Any, item: Any, g: dict[str, Any]) -> None:
        st = item.sceneTransform()
        for name, point in local_handle_points(item.content_rect()).items():
            g["handles_vp"][name] = view.to_viewport(st.map(point))
        return None

    def start(self, gizmo: Any, handle: str, g: dict[str, Any], item: Any) -> str | None:
        return "backdrop_resize" if handle in CORNERS + EDGES else None

    def drag(self, gizmo: Any, op: dict[str, Any], m: QPointF, modifiers: Any, view: Any) -> None:
        item, s0, l0, _parent_inv, _rotation = op["targets"][0]
        start = op["start_rect"]
        m_local = s0.inverted()[0].map(m)
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
        # Local (0, 0) is the top-left corner: move the origin to the new corner. The contents are
        # children of the item, so they would shift with it; counter-move them to keep them put.
        item.set_local_transform(QTransform.fromTranslate(rect.left(), rect.top()) * l0)
        _keep_contents_in_place(item, op, rect.left() - start.left(), rect.top() - start.top())
        gizmo.ui.status(f"Backdrop {rect.width():.0f} × {rect.height():.0f}px (Shift: square, Alt: from centre)")

    def paint(self, gizmo: Any, view: Any, painter: QPainter, g: dict[str, Any]) -> bool:
        painter.setPen(QPen(theme.ACCENT, 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(QPolygonF(g["corners_vp"]))
        painter.drawLine(g["top_mid_vp"], g["rotate_vp"])
        painter.setPen(QPen(theme.ACCENT, 1.5))
        painter.setBrush(QColor("#ffffff"))
        for name, point in g["handles_vp"].items():
            size = 5.0 if name in CORNERS else 4.0
            painter.drawEllipse(point, size, size)
        painter.drawEllipse(g["rotate_vp"], 5.0, 5.0)
        return True


def _keep_contents_in_place(item: BackdropItem, op: dict[str, Any], dx: float, dy: float) -> None:
    """Moving a backdrop's origin by (dx, dy) in its own space moves its children by the same; undo that."""
    starts = op.setdefault("child_starts", {child: child.pos() for child in item.childItems()
                                            if isinstance(child, NodeItemMixin)})
    for child, start in starts.items():
        child.setPos(start.x() - dx, start.y() - dy)


class BackdropTool(Tool):
    """Drag a rectangle: a backdrop appears and everything inside it joins."""

    id = "backdrop"
    title = "Backdrop"
    action_text = "Backdrop Tool"
    icon = "backdrop"
    tooltip = "Draw a backdrop (K) · everything inside joins it"
    keywords = "backdrop frame group stack tray board draw"
    order = 12.0
    hidden = True  # started by K (the palette button runs it); see make_backdrop
    cursor = Qt.CrossCursor
    gizmo = "handles"
    hint = "Drag to draw a backdrop around things · Esc / V to finish"
    creates = ""

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
        return "▭  Backdrop · drag around things to frame them   —   Esc / V to finish"

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind == "press" and e.button == Qt.LeftButton:
            self.op = {"start": e.scene_pos, "end": e.scene_pos, "view": e.view}
            return HANDLED
        if self.op is None:
            return None
        if e.kind == "move":
            self.op["end"] = e.view.to_scene(e.pos)
            e.view.viewport().update()
            return HANDLED
        if e.kind == "release":
            op, self.op = self.op, None
            e.view.viewport().update()
            a, b = op["view"].to_viewport(op["start"]), op["view"].to_viewport(op["end"])
            if math.hypot(a.x() - b.x(), a.y() - b.y()) > 8.0:
                rect = QRectF(op["start"], op["end"]).normalized()
                margin = SNAP_MARGIN_PX / max(op["view"].zoom_level(), 1e-6)
                self.app.run_command("backdrops.create", rect.x(), rect.y(), rect.width(), rect.height(),
                                     margin=margin)
            return HANDLED
        return None

    def paint_overlay(self, view: Any, painter: QPainter) -> None:
        if self.op is None or self.op["view"] is not view:
            return
        box = QRectF(view.to_viewport(self.op["start"]), view.to_viewport(self.op["end"])).normalized()
        color = QColor(90, 140, 200)
        painter.setPen(QPen(color, 1.5, Qt.DashLine))
        fill = QColor(color)
        fill.setAlphaF(0.18)
        painter.setBrush(fill)
        painter.drawRoundedRect(box, 6.0, 6.0)


# ---- Drag feedback -------------------------------------------------------------------------------


class DragPreview:
    """While items are dragged: light up the backdrop that release will join, or dim the one it leaves."""

    REFRESH_S = 0.05

    def __init__(self, ui: Any, engine: Any) -> None:
        self.ui = ui
        self.engine = engine
        self._stamp = 0.0
        self._result: list[tuple[str, str | None, str | None]] = []  # (node, current backdrop, target)

    def active(self) -> bool:
        tool = self.ui.app.tools.active
        op = getattr(tool, "op", None)
        return bool(op) and op.get("kind") == "move" and self.ui.canvas.interacting

    def compute(self, view: Any) -> list[tuple[str, str | None, str | None]]:
        now = time.monotonic()
        if now - self._stamp < self.REFRESH_S:
            return self._result
        self._stamp = now
        canvas, session = self.ui.canvas, self.ui.session
        names = [n for n in session.scene.top_level(session.selection.names) if canvas.items.get(n) is not None]
        margin = SNAP_MARGIN_PX / max(view.zoom_level(), 1e-6)
        result = []
        try:
            infos = self.engine.host_infos()
            for name in names:
                if not self.engine.eligible(name) or session.compute(name, "world_locked"):
                    continue
                item = canvas.items[name]
                box = item.scene_content_rect()
                rect = [box.x(), box.y(), box.width(), box.height()]
                target = self.engine.decide(name, rect, margin, infos)
                parent = session.scene.get_node(name).parent
                if target != parent:
                    result.append((name, parent, target))
        except Exception:  # noqa: BLE001  (a preview must never break a drag)
            result = []
        self._result = result
        return result

    def paint(self, view: Any, painter: QPainter) -> None:
        if not view.display.show_overlays or not self.active():
            self._result = []
            return
        changes = self.compute(view)
        if not changes:
            return
        canvas = self.ui.canvas
        joins = {target for _n, _p, target in changes if target is not None}
        leaves = {parent for _n, parent, target in changes if target is None and parent is not None}
        for names, color, width in ((leaves, QColor(230, 160, 60), 2.0), (joins, QColor(80, 200, 130), 3.0)):
            for name in names:
                item = canvas.items.get(name)
                if item is None:
                    continue
                try:
                    polygon = QPolygonF([view.to_viewport(p) for p in item.scene_polygon()])
                except RuntimeError:
                    continue
                painter.setPen(QPen(color, width))
                painter.setBrush(Qt.NoBrush)
                painter.drawPolygon(polygon)
        word = "+ Join" if joins else "Leave"
        names = joins or leaves
        title = ""
        if len(names) == 1:
            title = " " + str(self.ui.session.scene.get_node(next(iter(names))).attrs.get("title", ""))
        self._chip(view, painter, (word + title).strip(), QColor(80, 200, 130) if joins else QColor(230, 160, 60))

    @staticmethod
    def _chip(view: Any, painter: QPainter, text: str, color: QColor) -> None:
        pos = view.viewport().mapFromGlobal(QCursor.pos())
        font = painter.font()
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        box = QRectF(pos.x() + 14, pos.y() + 14, metrics.horizontalAdvance(text) + 14, metrics.height() + 6)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(box, 5.0, 5.0)
        painter.setPen(QColor(16, 20, 26))
        painter.drawText(box, Qt.AlignCenter, text)


def paint_contents_outline(ui: Any, view: Any, painter: QPainter) -> None:
    """A faint dashed outline around what a selected backdrop holds."""
    if not view.display.show_overlays or ui.canvas.interacting:
        return
    canvas, session, scene = ui.canvas, ui.session, ui.session.scene
    pen = QPen(theme.ACCENT, 1.0, Qt.DashLine)
    pen.setDashPattern([3.0, 4.0])
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    for name in session.selection.names:
        if not scene.has(name) or not scene.get_node(name).children:
            continue
        if scene.get_node(name).node_type != BACKDROP:
            continue
        item = canvas.items.get(name)
        if item is None:
            continue
        for child in item.childItems():
            if not isinstance(child, NodeItemMixin) or not child.isVisible() or not view.display.shows(child.node_name):
                continue
            try:
                polygon = QPolygonF([view.to_viewport(p) for p in child.scene_polygon()])
            except RuntimeError:
                continue
            painter.drawPolygon(polygon)


def activate_backdrops_ui(ui: Any) -> None:
    canvas = ui.canvas
    session = ui.session
    engine = ui.service("backdrops")

    def factory(_canvas: Any, node: dict[str, Any]) -> BackdropItem:
        item = BackdropItem(node["name"])
        item.apply_attrs(node.get("attrs", {}))
        return item

    def describe(_name: str, node: dict[str, Any]) -> list[str]:
        count = len(node.get("children", []))
        return [f"{count} item{'s' if count != 1 else ''}"]

    def edit_title(view: Any, item: Any, scene_pos: QPointF | None = None) -> bool:
        """Double-click the title bar: rename the backdrop."""
        if not isinstance(item, BackdropItem) or item.locked:
            return False
        if scene_pos is not None and not item.header_rect().contains(item.mapFromScene(scene_pos)):
            return False
        current = str(session.scene.get_node(item.node_name).attrs.get("title", ""))
        text, ok = QInputDialog.getText(ui.app.main_window(), "Backdrop title", "Title:", text=current)
        if ok:
            ui.app.run_command("set_attr", f"{item.node_name}.title", text)
        return True

    ui.node_ui(BACKDROP, factory=factory, label="Backdrop", icon="backdrop", badge=("#5f9ea0", "B"),
               describe=describe, double_click=edit_title)
    ui.register_tool(BackdropTool(ui))
    ui.contribute("gizmo.handles", BackdropHandles())

    # ---- Settle when a canvas edit ends -----------------------------------------------------------

    def settle_after_edit(_label: str, names: list[str]) -> None:
        if QApplication.keyboardModifiers() & Qt.AltModifier:
            return  # Alt: move without joining or leaving
        view = ui.app.active_view()
        margin = SNAP_MARGIN_PX / max(view.zoom_level(), 1e-6) if view is not None else 0.0
        try:
            ui.run("backdrops.settle", [n for n in names if session.scene.has(n)], margin)
        except Exception:  # noqa: BLE001  (never lose the edit itself over a membership problem)
            logging.getLogger(__name__).exception("Settling backdrops failed")

    ui.track(canvas.add_interaction_handler(settle_after_edit))

    preview = DragPreview(ui, engine)
    ui.overlay(preview.paint, order=42)
    ui.overlay(lambda view, painter: paint_contents_outline(ui, view, painter), order=41.5)

    # ---- K: wrap the selection, or draw a backdrop -----------------------------------------------

    def make_backdrop(title: str | None = None) -> None:
        """K: put a backdrop around the selection (asking for its title), or start the Backdrop tool."""
        if not session.selection.names:
            ui.app.tools.activate("backdrop")
            ui.status("Drag to draw a backdrop around things · Esc / V to finish")
            return
        if title is None:
            title, ok = QInputDialog.getText(ui.app.main_window(), "New backdrop", "Title:", text="Backdrop")
            if not ok:
                return
        ui.app.run_command("backdrops.wrap", None, 24.0, title.strip() or "Backdrop")

    ui.command("make", make_backdrop, undoable=False, echo=False)
    ui.action("backdrops.make", "backdrops.make", "Backdrop", keywords="frame wrap stack tray group board",
              category="Create", icon="backdrop")
    ui.bind("K", "backdrops.make")

    def toggle_text() -> str:
        names = session.selection.names
        acting = bool(names) and all(session.scene.has(n) and engine.mode(n) != "off" for n in names)
        return "Stop Acting as Backdrop" if acting else "Act as Backdrop"

    ui.action("backdrops.toggle", "backdrops.toggle", "Act as Backdrop", keywords="frame host stack tray contain",
              category="Create", dynamic_text=toggle_text)
    ui.menu("canvas", "backdrops.toggle", group="arrange", order=23, when="selection.count > 0")

    def after_toggle_mode() -> None:
        mode = ui.app.run_command("backdrops.toggle_mode")
        ui.app.actions.refresh_states()
        if mode == "free":
            ui.status("Free backdrops: rectangles and images take in what is dropped on them "
                      "(a node's own Backdrop setting still wins)")
        elif mode == "strict":
            ui.status("Strict backdrops: only backdrops and nodes switched on take things in")

    ui.command("toggle_free", after_toggle_mode, undoable=False, echo=False)
    ui.action("backdrops.free", "backdrops.toggle_free", "Free Backdrop Mode",
              keywords="strict hosting anything rectangle image drop stack", category="View",
              checked=lambda: engine.free)
    ui.menu("canvas", "backdrops.free", path="View", group="view.display", order=5)
    ui.action("backdrops.settle", "backdrops.settle", "Settle Backdrops",
              keywords="join leave adopt release refresh membership", category="Create", icon="backdrop")
    ui.menu("canvas", "backdrops.settle", group="arrange", order=24, when="selection.count > 0")

    # ---- Attribute Editor: the "Act as backdrop" switch -----------------------------------------

    JOIN_MODES = (("inside", "Mostly inside"), ("overlap", "Any overlap"), ("touch", "Touching (near)"))

    class BackdropSection(w.InspectorSection):
        def __init__(self, parent: Any) -> None:
            super().__init__("backdrop", "Backdrop", parent)
            self.names: list[str] = []
            self.check = QCheckBox("Act as backdrop", self.body)
            self.check.setToolTip("Other items dropped on it join it and move with it")
            self.check.toggled.connect(self._on_check)
            self.form.addRow(self.check)
            self.mode = w.make_combo(JOIN_MODES, self.body)
            self.mode.setToolTip("What it takes for an item to join: mostly inside, any overlap, or near")
            self.mode.activated.connect(self._on_mode)
            self.form.addRow("Join when", self.mode)
            self.count = QLabel(self.body)
            self.form.addRow("Holds", self.count)

        def refresh(self, info: Any) -> None:
            self.names = list(info.names)
            modes = [engine.mode(n) for n in self.names if session.scene.has(n)]
            acting = bool(modes) and all(m != "off" for m in modes)
            self.check.blockSignals(True)
            self.check.setChecked(acting)
            self.check.blockSignals(False)
            self.mode.setEnabled(acting)
            self.mode.blockSignals(True)
            w.set_combo(self.mode, modes[0] if acting else "inside")
            self.mode.blockSignals(False)
            held = sum(len(session.scene.get_node(n).children) for n in self.names if session.scene.has(n))
            self.count.setText(f"{held} item{'s' if held != 1 else ''}" if acting else "")
            self.count.setVisible(acting)
            self.form.labelForField(self.count).setVisible(acting)

        def _on_check(self, checked: bool) -> None:
            ui.app.run_command("backdrops.toggle", self.names, self.mode.currentData(), checked)

        def _on_mode(self, _index: int) -> None:
            ui.app.run_command("backdrops.toggle", self.names, self.mode.currentData(), True)

    ui.contribute("inspector.sections", InspectorSection(
        key="backdrop", title="Backdrop", order=45, generic=True,
        applies=lambda info: bool(info.nodes) and all(engine.policy(n.get("type", "")) is not None
                                                      for n in info.nodes),
        build=lambda parent, _inspector: BackdropSection(parent)))
    ui.menu("canvas", "backdrops.make", group="arrange", order=22)
    ui.contribute("palette.buttons", PaletteButton("backdrops.make", "Backdrop", "backdrop", order=31))
