"""The crop tool."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QWidget

from pixelref.plugin_api.contracts import InspectorRows, SelectionInfo
from pixelref.plugin_api.ui import HANDLED, KeyEvent, PointerEvent, Tool, paint, theme
from pixelref.plugin_api.ui import widgets as w

CORNERS = ("tl", "tr", "br", "bl")
HANDLE_HIT = 8.0


def local_handle_points(r: QRectF) -> dict[str, QPointF]:
    cx, cy = r.center().x(), r.center().y()
    return {"tl": r.topLeft(), "tr": r.topRight(), "br": r.bottomRight(), "bl": r.bottomLeft(),
            "tm": QPointF(cx, r.top()), "bm": QPointF(cx, r.bottom()), "ml": QPointF(r.left(), cy),
            "mr": QPointF(r.right(), cy)}


def resize_cursor_for(vector: QPointF) -> Qt.CursorShape:
    angle = math.degrees(math.atan2(vector.y(), vector.x())) % 180.0
    if angle < 22.5 or angle >= 157.5:
        return Qt.SizeHorCursor
    if angle < 67.5:
        return Qt.SizeFDiagCursor
    if angle < 112.5:
        return Qt.SizeVerCursor
    return Qt.SizeBDiagCursor


class CropTool(Tool):
    id = "crop"
    title = "Crop"
    icon = "image"
    tooltip = "Crop the selected image (C)"
    order = 200.0
    kind = "momentary"
    show_selection = False
    exclusive_overlay = True
    hint = "Drag the handles to crop · Enter or double-click to apply · Esc to cancel"
    hidden = True  # not in the palette: started with C on an image

    def __init__(self, ui: Any) -> None:
        super().__init__(ui)
        self.item: Any = None
        self.rect = QRectF()
        self.op: dict[str, Any] | None = None
        self.apply_on_exit = False

    def activate(self, **args: Any) -> None:
        item = args.get("item")
        if item is None:
            names = [n for n in self.ui.session.selection.names
                     if self.ui.canvas.nodes.get(n, {}).get("type") == "image"]
            item = self.ui.canvas.item(names[0]) if names else None
        self.item = item
        self.apply_on_exit = False
        self.op = None
        if item is not None:
            self.rect = QRectF(item.crop_rect())
            item.set_crop_preview(True)
        self.app.update_views()

    def deactivate(self) -> None:
        item, self.item = self.item, None
        self.op = None
        if item is None:
            return
        try:
            item.set_crop_preview(False)
        except RuntimeError:
            return
        if self.apply_on_exit:
            full = item.full_rect()
            value = None if self.rect.contains(full) else [self.rect.x(), self.rect.y(), self.rect.width(),
                                                           self.rect.height()]
            self.app.run_command("crop.set", item.node_name, value)
        self.app.update_views()

    def finish(self, apply: bool) -> None:
        self.apply_on_exit = apply
        self.app.tools.activate(self.app.tools.default_id)

    def handles_vp(self, view: Any) -> dict[str, QPointF]:
        if self.item is None:
            return {}
        st = self.item.sceneTransform()
        return {name: view.to_viewport(st.map(p)) for name, p in local_handle_points(self.rect).items()}

    def _in_rect(self, view: Any, pos: QPointF) -> bool:
        handles = self.handles_vp(view)
        if not handles:
            return False
        return QPolygonF([handles[name] for name in CORNERS]).containsPoint(pos, Qt.OddEvenFill)

    def pointer(self, e: PointerEvent) -> str | None:
        item = self.item
        if e.kind == "double" and e.button == Qt.LeftButton:
            self.finish(apply=True)
            return HANDLED
        if e.kind == "press" and e.button == Qt.LeftButton:
            if item is None:
                self.finish(apply=False)
                return HANDLED
            for name, point in self.handles_vp(e.view).items():
                if math.hypot(e.pos.x() - point.x(), e.pos.y() - point.y()) <= HANDLE_HIT:
                    self.op = {"kind": "handle", "handle": name}
                    return HANDLED
            local = item.mapFromScene(e.scene_pos)
            if self.rect.contains(local):
                self.op = {"kind": "move", "start": local, "rect": QRectF(self.rect)}
                return HANDLED
            if not item.full_rect().contains(local):
                self.finish(apply=True)
            return HANDLED
        if e.kind == "move" and self.op is not None:
            self._drag(e.view.to_scene(e.pos))
            e.view.viewport().update()
            return HANDLED
        if e.kind == "release" and self.op is not None:
            self.op = None
            return HANDLED
        return None

    def _drag(self, scene_pos: QPointF) -> None:
        item = self.item
        if item is None:
            return
        local = item.mapFromScene(scene_pos)
        full = item.full_rect()
        op = self.op
        if op["kind"] == "move":
            rect = QRectF(op["rect"])
            rect.translate(local - op["start"])
            rect.moveLeft(max(full.left(), min(rect.left(), full.right() - rect.width())))
            rect.moveTop(max(full.top(), min(rect.top(), full.bottom() - rect.height())))
            rect.moveTo(round(rect.left()), round(rect.top()))  # whole source pixels, like the handles
            self.rect = rect
        else:
            x = max(full.left(), min(full.right(), local.x()))
            y = max(full.top(), min(full.bottom(), local.y()))
            rect = QRectF(self.rect)
            handle = op["handle"]
            minimum = 2.0
            if handle in ("tl", "ml", "bl"):
                rect.setLeft(min(x, rect.right() - minimum))
            if handle in ("tr", "mr", "br"):
                rect.setRight(max(x, rect.left() + minimum))
            if handle in ("tl", "tm", "tr"):
                rect.setTop(min(y, rect.bottom() - minimum))
            if handle in ("bl", "bm", "br"):
                rect.setBottom(max(y, rect.top() + minimum))
            self.rect = QRectF(round(rect.left()), round(rect.top()), round(rect.width()), round(rect.height()))
        self.ui.status(f"Crop {self.rect.width():.0f} × {self.rect.height():.0f}px — Enter to apply, Esc to cancel")

    def hover(self, e: PointerEvent) -> None:
        handles = self.handles_vp(e.view)
        for point in handles.values():
            if math.hypot(e.pos.x() - point.x(), e.pos.y() - point.y()) <= HANDLE_HIT:
                middle = (handles["tl"] + handles["br"]) / 2.0
                e.set_cursor(resize_cursor_for(point - middle))
                return
        e.set_cursor(Qt.SizeAllCursor if self._in_rect(e.view, e.pos) else Qt.ArrowCursor)

    def key(self, e: KeyEvent) -> str | None:
        if e.kind == "press" and e.key in (Qt.Key_Return, Qt.Key_Enter):
            self.finish(apply=True)
            return HANDLED
        return None

    def paint_overlay(self, view: Any, painter: QPainter) -> None:
        item = self.item
        if item is None:
            return
        st = item.sceneTransform()
        handles = self.handles_vp(view)
        crop = QPolygonF([handles[name] for name in CORNERS])
        full = item.full_rect()
        full_path = QPainterPath()
        full_path.addPolygon(QPolygonF([view.to_viewport(st.map(p)) for p in (
            full.topLeft(), full.topRight(), full.bottomRight(), full.bottomLeft())]))
        full_path.closeSubpath()
        crop_path = QPainterPath()
        crop_path.addPolygon(crop)
        crop_path.closeSubpath()
        painter.fillPath(full_path.subtracted(crop_path), QColor(0, 0, 0, 160))
        painter.setPen(QPen(theme.ACCENT, 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(crop)
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1.0))  # rule-of-thirds guides
        tl, tr, br, bl = (handles[name] for name in CORNERS)
        for t in (1.0 / 3.0, 2.0 / 3.0):
            painter.drawLine(tl + (tr - tl) * t, bl + (br - bl) * t)
            painter.drawLine(tl + (bl - tl) * t, tr + (br - tr) * t)
        for point in handles.values():
            paint.paint_handle(painter, point)


class CropRows(QWidget):
    def __init__(self, ui: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self.crop_btn = w.make_tool_button("Crop", "Crop (C)", width=48, parent=self)
        self.reset_crop_btn = w.make_tool_button("Reset crop", "Reset crop (Shift+C)", width=74, parent=self)
        self.crop_btn.clicked.connect(lambda: ui.app.trigger("crop.start"))
        self.reset_crop_btn.clicked.connect(lambda: ui.app.trigger("crop.reset"))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(self.crop_btn)
        layout.addWidget(self.reset_crop_btn)
        layout.addStretch()

    def refresh(self, info: SelectionInfo) -> None:
        self.reset_crop_btn.setEnabled(bool(info.attrs.get("crop")))


def activate_crop_ui(ui: Any) -> None:
    tool = CropTool(ui)
    ui.register_tool(tool)
    session = ui.session

    def start_crop() -> None:
        """Crop the selected image (one unlocked image)."""
        from pixelref.plugin_api import CommandError

        # Flush first: a pending refresh may replace the image's item.
        ui.canvas.flush_pending()
        images = [ui.canvas.item(n) for n in session.selection.names
                  if ui.canvas.nodes.get(n, {}).get("type") == "image"]
        images = [i for i in images if i is not None and not i.locked]
        if len(images) != 1:
            raise CommandError("Select a single unlocked image to crop")
        ui.tools.activate("crop", item=images[0])

    ui.command("start", start_crop, undoable=False)
    ui.action("crop.start", "crop.start", "Crop", keywords="trim cut image", category="Transform")
    ui.action("crop.reset", "crop.reset", "Reset Crop", keywords="uncrop restore image", category="Transform")
    ui.bind("C", "crop.start")
    ui.bind("Shift+C", "crop.reset")

    def has_image() -> bool:
        return any(ui.canvas.nodes.get(n, {}).get("type") == "image" for n in session.selection.names)

    ui.menu("canvas", "crop.start", path="Image", group="image", order=0, when=has_image)
    ui.menu("canvas", "crop.reset", path="Image", group="image", order=1, when=has_image)
    ui.contribute("inspector.rows", InspectorRows("image", 0, lambda parent, _i: CropRows(ui, parent)))
