"""Image info: a readout next to the cursor over an image (Alt+I).

Coordinates are in the image's own pixels, origin at its top-left: an image
item's local units are source pixels, so cropping, flipping, rotating and
scaling the item don't change them.
"""

from __future__ import annotations

import math
from typing import Any

# Above this many screen pixels per image pixel, the pixel under the cursor is outlined.
PIXEL_OUTLINE_ZOOM = 6.0


def activate_ui(ui: Any) -> None:
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPolygonF
    from PySide6.QtWidgets import QApplication

    from pixelref.plugin_api.ui import InputHandler, PointerEvent, theme

    app = ui.app
    canvas = ui.canvas
    session = ui.session
    hover: dict[str, Any] = {}  # view, pos (viewport), name (image node)

    def enabled() -> bool:
        return bool(ui.settings.value("enabled", False, type=bool))

    def image_item(name: str | None) -> Any:
        if not name or canvas.nodes.get(name, {}).get("type") != "image":
            return None
        item = canvas.item(name)
        return item if item is not None and hasattr(item, "crop_rect") else None

    def source_text(attrs: dict[str, Any]) -> str:
        asset = session.scene.get_asset(str(attrs.get("asset", "") or ""))
        if asset is not None:
            return f"embedded · {asset.name}" if asset.name else "embedded"
        path = str(attrs.get("path", "") or "")
        return str(session.resolve_path(path)) if path else "no source"

    def info(view: Any, item: Any, pos: QPointF) -> dict[str, Any] | None:
        """What the readout shows for the image pixel under viewport position ``pos``."""
        local = item.mapFromScene(view.to_scene(pos))
        crop = item.crop_rect()
        if not crop.contains(local):
            return None
        full = item.full_rect()
        transform = item.sceneTransform()
        screen_scale = math.hypot(transform.m11(), transform.m12()) * view.zoom_level()
        node = canvas.nodes.get(item.node_name, {})
        size = f"{crop.width():.0f} × {crop.height():.0f} px"
        if crop != full:
            size += f"  (cropped from {full.width():.0f} × {full.height():.0f})"
        return {
            "x": math.floor(local.x()), "y": math.floor(local.y()),
            "lines": [
                item.node_name,
                f"x {math.floor(local.x())}   y {math.floor(local.y())}",
                f"{size}   ·   shown at {screen_scale * 100:.0f}%",
                source_text(node.get("attrs", {})),
            ],
            "screen_scale": screen_scale,
        }

    def toggle() -> bool:
        """Show or hide the image info readout (Alt+I): pixel coordinates, size, scale and source."""
        state = not enabled()
        ui.settings.setValue("enabled", state)
        if not state:
            hover.clear()
        for view in app.views.values():
            view.viewport().update()
        ui.status(f"Image info {'on · hover an image' if state else 'off'}")
        return state

    def describe(name: str | None = None) -> dict[str, Any]:
        """Size, crop and source of an image (the selected one by default)."""
        name = session.scene.resolve(name) if name else (session.selection.names or [None])[0]
        item = image_item(name)
        if item is None:
            from pixelref.plugin_api import CommandError

            raise CommandError("Select an image")
        crop, full = item.crop_rect(), item.full_rect()
        return {"name": name, "size": [round(full.width()), round(full.height())],
                "crop": [round(crop.x()), round(crop.y()), round(crop.width()), round(crop.height())],
                "source": source_text(canvas.nodes[name].get("attrs", {}))}

    class Tracker(InputHandler):
        def hover(self, e: PointerEvent) -> None:
            if not enabled():
                return
            item = e.item
            name = item.node_name if image_item(getattr(item, "node_name", None)) is not None else None
            if name is None and hover.get("name") is None:
                return
            hover.update(view=e.view, pos=QPointF(e.pos), name=name)
            e.view.viewport().update()

        def leave(self, view: Any) -> None:
            if hover.get("view") is view:
                hover.clear()
                view.viewport().update()

    def paint(view: Any, painter: QPainter) -> None:
        if not enabled() or hover.get("view") is not view or QApplication.mouseButtons() != Qt.NoButton:
            return
        item = image_item(hover.get("name"))
        if item is None or not item.isVisible():
            return
        pos = hover["pos"]
        data = info(view, item, pos)
        if data is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        if data["screen_scale"] >= PIXEL_OUTLINE_ZOOM:
            transform = item.sceneTransform()
            cell = QPolygonF([view.to_viewport(transform.map(QPointF(data["x"] + dx, data["y"] + dy)))
                              for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1))])
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 200), 3))
            painter.drawPolygon(cell)
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.drawPolygon(cell)
        lines = data["lines"]
        regular = QFont(painter.font())
        bold = QFont(regular)
        bold.setBold(True)
        metrics = QFontMetrics(bold)
        line_h = metrics.height() + 2
        width = max(metrics.horizontalAdvance(line) for line in lines) + 20
        height = line_h * len(lines) + 12
        viewport = view.viewport()
        # Above and right of the cursor (the Pick loupe sits below it); flipped near edges.
        x, y = pos.x() + 18, pos.y() - 18 - height
        if x + width > viewport.width() - 4:
            x = pos.x() - 18 - width
        if y < 4:
            y = pos.y() + 22
        box = QRectF(x, y, width, height)
        painter.setPen(QPen(theme.ACCENT, 1.0))
        painter.setBrush(theme.PANEL_BG)
        painter.drawRoundedRect(box, 5, 5)
        painter.setPen(theme.TEXT_LIGHT)
        for index, line in enumerate(lines):  # the name in bold
            painter.setFont(bold if index == 0 else regular)
            painter.drawText(QRectF(x + 10, y + 6 + index * line_h, width - 20, line_h),
                             Qt.AlignVCenter | Qt.AlignLeft, line)
        painter.restore()

    def readout(view: Any, pos: QPointF) -> dict[str, Any] | None:
        """What the readout shows at a viewport position (None off an image)."""
        item = image_item(getattr(view.node_item_at(pos.toPoint()), "node_name", None))
        return info(view, item, pos) if item is not None else None

    ui.provide_service("image_info", type("ImageInfo", (), {"readout": staticmethod(readout),
                                                            "enabled": staticmethod(enabled)})())
    ui.command("toggle", toggle, alias="toggle_image_info", undoable=False)
    ui.command("describe", describe, alias="image_info", undoable=False, echo=False)
    ui.action("image_info.toggle", "image_info.toggle", "Image Info", icon="info",
              keywords="pixel coordinates position size resolution source path dimensions", category="View",
              checked=enabled)
    ui.bind("Alt+I", "image_info.toggle")
    ui.menu("canvas", "image_info.toggle", path="View", group="view.display", order=4)
    ui.input_handler(Tracker(), 300)
    ui.overlay(paint, order=66)
    ui.track(hover.clear)
