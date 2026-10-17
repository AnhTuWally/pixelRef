"""Qt side of the colour picker."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from pixelref.plugin_api.ui import HANDLED, PointerEvent, Tool, cursor_pos_in, paint, theme
from pixelref.plugin_api.ui import widgets as w

LOUPE_PIXELS = 11  # sampled pixels per side (odd, so one is centred)
LOUPE_CELL = 9  # on-screen size of each magnified pixel


class PickTool(Tool):
    id = "color_picker"
    title = "Pick"
    icon = "pick"
    tooltip = "Sample a color to the clipboard (I)"
    keywords = "eyedropper sample colour color picker"
    shortcuts = ("I",)
    action_text = "Color Picker"
    order = 50.0
    cursor = Qt.CrossCursor
    show_selection = False
    drawing_session = "continue"
    options_title = "Pick a color"
    hint = "Click to pick the color shown in the loupe · it becomes the brush color · Esc to cancel"

    def __init__(self, ui: Any) -> None:
        super().__init__(ui)
        self.snapshots: dict[int, QImage] = {}
        self.pos: QPoint | None = None
        self.pos_view: Any = None
        self.panels: list[Any] = []
        self.return_to: str | None = None
        self._refresh = QTimer()
        self._refresh.setSingleShot(True)
        self._refresh.timeout.connect(self.refresh_snapshots)


    # ---- Snapshot of what the canvas shows -------------------------------------------------

    def refresh_snapshots(self) -> None:
        self.snapshots = {}
        for view in self.app.views.values():
            if view.isVisible():
                self.snapshots[id(view)] = view.grab_clean()
                view.viewport().update()
        if self.pos is not None and self.pos_view is not None:
            self.update_live(self.color_at(self.pos_view, self.pos))

    def schedule_refresh(self, *_args: Any) -> None:
        if self.app.tools.active is self:
            self._refresh.start(30)

    def color_at(self, view: Any, pos: QPoint) -> QColor:
        """Colour of the canvas pixel under a viewport position."""
        image = self.snapshots.get(id(view))
        if image is None or image.isNull():
            image = view.grab_clean(QRect(pos.x(), pos.y(), 1, 1))
            return image.pixelColor(0, 0) if not image.isNull() else QColor()
        ratio = image.devicePixelRatio() or 1.0
        x = min(max(0, int(pos.x() * ratio)), image.width() - 1)
        y = min(max(0, int(pos.y() * ratio)), image.height() - 1)
        return image.pixelColor(x, y)

    # ---- Lifecycle -----------------------------------------------------------------------

    def activate(self, **args: Any) -> None:
        previous = self.app.tools.previous_id
        self.return_to = previous if previous and previous != self.id else None
        self.refresh_snapshots()
        self.pos, self.pos_view = None, None
        for view in self.app.views.values():
            pos = cursor_pos_in(view)
            if pos is not None:
                self.pos, self.pos_view = pos, view
        self.refresh_panels()

    def deactivate(self) -> None:
        self.snapshots = {}
        self.pos = None
        self.pos_view = None

    def leave(self, view: Any) -> None:
        if self.pos_view is view:
            self.pos = None
            self.update_live(None)
            view.viewport().update()

    # ---- Input ---------------------------------------------------------------------------

    def hover(self, e: PointerEvent) -> None:
        self.pos = e.pos.toPoint()
        self.pos_view = e.view
        self.update_live(self.color_at(e.view, self.pos))
        e.view.viewport().update()

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind == "press" and e.button == Qt.LeftButton:
            self.picked(self.color_at(e.view, e.pos.toPoint()))
            return HANDLED
        if e.kind in ("move", "release", "double"):
            return HANDLED if e.button == Qt.LeftButton or e.kind == "move" else None
        return None

    def picked(self, color: QColor) -> None:
        if not color.isValid():
            return
        brush = self.ui.service("brush")
        hex_value = color.name().upper()
        QApplication.clipboard().setText(hex_value)
        brush.set_color(color)
        brush.add_recent(color)
        previous = self.app.tools.get(self.return_to) if self.return_to else None
        if previous is not None and getattr(previous, "uses_brush_color", False):
            # Straight back to drawing, now with the picked colour.
            self.app.tools.activate(previous.id)
            message = f"Picked {hex_value} · copied · {previous.title} now draws in this color"
        else:
            self.refresh_panels()
            target = brush.tool()
            owner = f"{target.title}'s color" if target is not None else "the brush color"
            message = f"Picked {hex_value} · copied · now {owner}"
        self.ui.toast(message, color)
        self.ui.status(f"{message}  ·  rgb({color.red()}, {color.green()}, {color.blue()})")

    # ---- Painting ------------------------------------------------------------------------

    def banner(self, view: Any) -> str | None:
        return "◉  Pick a color · click to pick what's in the loupe — it becomes the brush color   —   Esc to cancel"

    def paint_overlay(self, view: Any, painter: QPainter) -> None:
        """Magnifier next to the cursor: enlarged pixels, the picked one outlined,
        plus a swatch and hex/RGB readout of that colour."""
        if not view.display.show_overlays:
            text = self.banner(view)
            if text:
                paint.paint_banner(painter, view.viewport().width(), text)
        pos, image = self.pos, self.snapshots.get(id(view))
        if pos is None or self.pos_view is not view or image is None or image.isNull():
            return
        ratio = image.devicePixelRatio() or 1.0
        n, cell = LOUPE_PIXELS, LOUPE_CELL
        size = n * cell
        cx, cy = int(pos.x() * ratio), int(pos.y() * ratio)
        patch = image.copy(QRect(cx - n // 2, cy - n // 2, n, n))
        patch.setDevicePixelRatio(1.0)
        color = self.color_at(view, pos)
        label = f"{color.name().upper()}   rgb({color.red()}, {color.green()}, {color.blue()})"
        metrics = painter.fontMetrics()
        label_h = metrics.height() + 12
        panel_w = max(size, metrics.horizontalAdvance(label) + 40)
        # Keep the loupe on screen: flip to the other side of the cursor near edges.
        x, y = pos.x() + 22, pos.y() + 22
        if x + panel_w > view.viewport().width() - 4:
            x = pos.x() - 22 - panel_w
        if y + size + label_h + 4 > view.viewport().height() - 4:
            y = pos.y() - 22 - size - label_h - 4
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.setRenderHint(QPainter.Antialiasing, False)
        frame = QRectF(x, y, size, size)
        painter.fillRect(frame, QColor("#0d0e11"))
        painter.drawImage(frame, patch)
        painter.setPen(QPen(QColor(0, 0, 0, 45), 1))
        for i in range(1, n):
            painter.drawLine(QPointF(x + i * cell, y), QPointF(x + i * cell, y + size))
            painter.drawLine(QPointF(x, y + i * cell), QPointF(x + size, y + i * cell))
        centre = QRectF(x + (n // 2) * cell, y + (n // 2) * cell, cell, cell)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#000000"), 3))
        painter.drawRect(centre)
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.drawRect(centre)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(theme.ACCENT, 1.5))
        painter.drawRect(frame)
        pill = QRectF(x, y + size + 4, panel_w, label_h)
        painter.setPen(QPen(theme.ACCENT, 1.0))
        painter.setBrush(QColor(20, 24, 32, 235))
        painter.drawRoundedRect(pill, 5, 5)
        swatch = QRectF(pill.left() + 7, pill.top() + 5, label_h - 10, label_h - 10)
        painter.setPen(QPen(QColor("#ffffff"), 1))
        painter.setBrush(color)
        painter.drawRoundedRect(swatch, 3, 3)
        painter.setPen(theme.TEXT_LIGHT)
        painter.drawText(pill.adjusted(swatch.width() + 14, 0, -6, 0), Qt.AlignVCenter | Qt.AlignLeft, label)
        painter.restore()

    # ---- Options panel ----------------------------------------------------------------------

    def options_panel(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        brush = self.ui.service("brush")
        panel.live_swatch = w.make_tool_button("", "Color under the cursor", parent=panel)
        panel.live_label = QLabel("Hover over the canvas…", panel)
        panel.live_label.setObjectName("hexLabel")
        layout.addWidget(w.hrow(panel, QLabel("Under cursor", panel), panel.live_swatch, panel.live_label))
        panel.current_swatch = w.make_tool_button("", "Picked color (the brush color)", parent=panel)
        panel.current_label = QLabel("", panel)
        panel.current_label.setObjectName("hexLabel")
        panel.copy_btn = w.make_tool_button("Copy", "Copy the hex code to the clipboard", width=44, parent=panel)

        def copy_hex() -> None:
            hex_value = brush.color.name().upper()
            QApplication.clipboard().setText(hex_value)
            self.ui.toast(f"Copied {hex_value}", brush.color)

        panel.copy_btn.clicked.connect(copy_hex)
        layout.addWidget(w.hrow(panel, QLabel("Picked", panel), panel.current_swatch, panel.current_label, None,
                                panel.copy_btn))
        usage = QLabel("Click the canvas to pick. The color is copied and becomes the color of the drawing "
                       "tool you came from (each tool keeps its own).", panel)
        usage.setObjectName("pathLabel")
        usage.setWordWrap(True)
        layout.addWidget(usage)
        panel.apply_btn = QPushButton("Apply to selection", panel)
        panel.apply_btn.setObjectName("toolBtn")
        panel.apply_btn.setFocusPolicy(Qt.NoFocus)
        panel.apply_btn.clicked.connect(self.apply_to_selection)
        layout.addWidget(panel.apply_btn)
        panel.recent_row = brush.make_recent_row(panel)
        layout.addWidget(panel.recent_row)
        self.panels.append(panel)
        panel.destroyed.connect(lambda *_a, p=panel: p in self.panels and self.panels.remove(p))
        self.refresh_panels()
        return panel

    def colorable_selection(self) -> list[str]:
        session = self.ui.session
        return [n for n in session.selection.names if session.scene.has(n)
                and session.types.has_capability(session.scene.get_node(n).node_type, "colorable")]

    def refresh_panels(self, *_args: Any) -> None:
        brush = self.ui.service("brush")
        color = brush.color
        targets = self.colorable_selection()
        for panel in list(self.panels):
            try:
                w.swatch(panel.current_swatch, color)
                panel.current_label.setText(
                    f"{color.name().upper()}  ({color.red()}, {color.green()}, {color.blue()})")
                panel.apply_btn.setVisible(bool(targets))
                panel.apply_btn.setText(f"Apply {color.name().upper()} to selection ({len(targets)})")
            except RuntimeError:
                self.panels.remove(panel)

    def update_live(self, color: QColor | None) -> None:
        for panel in list(self.panels):
            try:
                if color is None or not color.isValid():
                    w.swatch(panel.live_swatch, QColor(0, 0, 0, 0))
                    panel.live_label.setText("Hover over the canvas…")
                else:
                    w.swatch(panel.live_swatch, color)
                    panel.live_label.setText(color.name().upper())
            except RuntimeError:
                self.panels.remove(panel)

    def apply_to_selection(self) -> None:
        brush = self.ui.service("brush")
        names = self.colorable_selection()
        if not names:
            return
        if self.app.run_command("color_picker.apply_color", brush.rgb(), names) is not None:
            self.ui.toast(f"Applied {brush.color.name().upper()} to {len(names)} item(s)", brush.color)


def activate_picker_ui(ui: Any) -> None:
    tool = PickTool(ui)
    ui.register_tool(tool)
    ui.on_ui("camera.changed", tool.schedule_refresh)
    ui.on_ui("canvas.refreshed", tool.schedule_refresh)
    ui.on_ui("ui.selection_changed", tool.refresh_panels)
    ui.track(tool._refresh.stop)
    brush = ui.service("brush")
    brush.changed.connect(tool.refresh_panels)
    ui.track(lambda: brush.changed.disconnect(tool.refresh_panels))
