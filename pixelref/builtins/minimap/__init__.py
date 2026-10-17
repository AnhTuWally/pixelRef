"""Minimap: an overview of the whole board, floating in a corner of each canvas view.

The board is drawn small, with an outline for what each canvas view shows
(this view's outline is highlighted). Click or drag on the map to move this
view there; the wheel zooms it.

Hover the minimap to show its title bar: drag the bar to move the minimap to
another corner (it snaps to the nearest one), the size button cycles small /
medium / large and ``×`` hides it. Show it again with the map button in the
canvas header or View ▸ Minimap. Each view remembers its own minimap.
"""

from __future__ import annotations

from typing import Any

CORNERS = ("tl", "tr", "bl", "br")
SIZES = {"small": 160, "medium": 220, "large": 320}
SIZE_ORDER = ("small", "medium", "large")
ASPECT = 0.68
MARGIN = 16  # clear of the tile's corner split grips
BAR = 20
DEFAULT_KEY = "minimap/default_visible"


def activate_ui(ui: Any) -> None:
    from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, Qt, QTimer
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
    from PySide6.QtWidgets import QToolButton, QWidget
    from shiboken6 import isValid

    from pixelref.plugin_api import CommandError
    from pixelref.plugin_api.ui import icon, theme

    app = ui.app
    canvas = app.canvas
    maps: dict[str, "Minimap"] = {}

    def default_visible() -> bool:
        return bool(app.settings.value(DEFAULT_KEY, True, type=bool))

    class CornerHints(QWidget):
        """The four drop spots, shown while the minimap is being moved."""

        def __init__(self, view: Any) -> None:
            super().__init__(view)
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            self.spots: dict[str, QRect] = {}
            self.nearest = ""
            self.hide()

        def paintEvent(self, _event: Any) -> None:  # noqa: N802
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            for corner, rect in self.spots.items():
                rect = QRectF(rect.translated(-self.pos()))
                hot = corner == self.nearest
                fill = QColor(theme.ACCENT)
                fill.setAlpha(60 if hot else 18)
                painter.setBrush(fill)
                painter.setPen(QPen(QColor(theme.ACCENT) if hot else QColor(255, 255, 255, 70), 1.5, Qt.DashLine))
                painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 6, 6)

    # The board's content bounds, shared by every minimap and measured again only
    # after the canvas refreshes: measuring every item per map per camera move made
    # layouts with many views crawl.
    shared_content: list[QRectF | None] = [None]

    def content_bounds() -> QRectF:
        if shared_content[0] is None:
            shared_content[0] = canvas.scene_bounds(canvas.visible_content_items())
        return QRectF(shared_content[0])

    class Minimap(QWidget):
        def __init__(self, view: Any) -> None:
            super().__init__(view)
            self.view = view
            self.setObjectName("minimap")
            self.setMouseTracking(True)
            self.setAttribute(Qt.WA_Hover, True)
            self.setFocusPolicy(Qt.NoFocus)
            self.image: QImage | None = None
            self.world = QRectF()
            self.content: QRectF | None = None  # content bounds at the last render
            self.hover = False
            self._mode: str | None = None  # "navigate" or "move"
            self._grab = QPoint()
            self.hints = CornerHints(view)
            self._animation: QPropertyAnimation | None = None
            self.buttons: list[QToolButton] = []
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self.render_board)
            view.installEventFilter(self)
            self.apply_state()

        # ---- State (saved with the view) ---------------------------------------------------

        def state(self) -> dict[str, Any]:
            saved = self.view.plugin_state.get("minimap")
            saved = saved if isinstance(saved, dict) else {}
            visible = saved.get("visible")
            if visible is None:
                visible = default_visible() and self.view.display.show_overlays
            corner = saved.get("corner") if saved.get("corner") in CORNERS else "br"
            size = saved.get("size") if saved.get("size") in SIZES else "medium"
            return {"visible": bool(visible), "corner": corner, "size": size}

        def set_state(self, **values: Any) -> dict[str, Any]:
            state = self.state()
            state.update({k: v for k, v in values.items() if v is not None})
            self.view.plugin_state["minimap"] = state
            app.wm.schedule_save()
            self.apply_state()
            return state

        def available(self) -> bool:
            """Too small a view hides the minimap (without changing its setting)."""
            viewport = self.view.viewport().geometry()
            return viewport.width() >= 320 and viewport.height() >= 240

        def apply_state(self, animate: bool = False) -> None:
            state = self.state()
            show = state["visible"] and self.available()
            width = min(SIZES[state["size"]], int(self.view.viewport().width() * 0.45))
            self.resize(max(120, width), max(80, int(max(120, width) * ASPECT)))
            target = self.corner_pos(state["corner"])
            if self._animation is not None:
                self._animation.stop()  # one reused animation, not a new child object per move
            if animate and show:
                if self._animation is None:
                    self._animation = QPropertyAnimation(self, b"pos", self)
                    self._animation.setDuration(160)
                    self._animation.setEasingCurve(QEasingCurve.OutCubic)
                self._animation.setStartValue(self.pos())
                self._animation.setEndValue(target)
                self._animation.start()
            else:
                self.move(target)
            self.setVisible(show)
            if show:
                self.raise_()
                self.schedule()
            for button in self.buttons:
                try:
                    button.setChecked(state["visible"])
                except RuntimeError:
                    pass

        def corner_pos(self, corner: str, size: Any = None) -> QPoint:
            area = self.view.viewport().geometry()
            w, h = (size.width(), size.height()) if size is not None else (self.width(), self.height())
            x = area.left() + MARGIN if corner.endswith("l") else area.right() - MARGIN - w + 1
            y = area.top() + MARGIN if corner.startswith("t") else area.bottom() - MARGIN - h + 1
            return QPoint(x, y)

        def nearest_corner(self) -> str:
            area = self.view.viewport().geometry()
            centre = self.geometry().center()
            vertical = "t" if centre.y() < area.center().y() else "b"
            horizontal = "l" if centre.x() < area.center().x() else "r"
            return vertical + horizontal

        def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802
            if obj is self.view and event.type() == QEvent.Resize and self._mode != "move":
                QTimer.singleShot(0, self.apply_state)
            return False

        # ---- Mapping between the board and the map -------------------------------------------

        def map_rect(self) -> QRectF:
            return QRectF(self.rect()).adjusted(1, 1, -1, -1)

        def board_rect(self, content: QRectF | None = None) -> QRectF:
            rect = QRectF(content) if content is not None else content_bounds()
            for view in app.views.values():
                rect = rect.united(view.visible_scene_rect()) if not rect.isNull() else view.visible_scene_rect()
            if rect.isNull():
                rect = QRectF(-500, -500, 1000, 1000)
            margin = max(rect.width(), rect.height()) * 0.05
            return rect.adjusted(-margin, -margin, margin, margin)

        def _scale(self) -> tuple[float, QPointF]:
            world = self.world if not self.world.isNull() else self.board_rect()
            area = self.map_rect()
            scale = min(area.width() / max(world.width(), 1e-6), area.height() / max(world.height(), 1e-6))
            offset = QPointF(area.left() + (area.width() - world.width() * scale) / 2,
                             area.top() + (area.height() - world.height() * scale) / 2)
            return scale, offset

        def to_widget(self, point: QPointF) -> QPointF:
            scale, offset = self._scale()
            return QPointF((point.x() - self.world.left()) * scale + offset.x(),
                           (point.y() - self.world.top()) * scale + offset.y())

        def to_world(self, point: QPointF) -> QPointF:
            scale, offset = self._scale()
            return QPointF((point.x() - offset.x()) / scale + self.world.left(),
                           (point.y() - offset.y()) / scale + self.world.top())

        # ---- Painting ------------------------------------------------------------------------

        def schedule(self) -> None:
            if self.isVisible() or self.state()["visible"]:
                self._timer.start(120)

        def render_board(self) -> None:
            if self.width() < 2 or self.height() < 2 or not self.isVisible():
                return
            self.content = content_bounds()
            self.world = self.board_rect(self.content)
            scale, offset = self._scale()
            ratio = self.devicePixelRatioF()
            image = QImage(int(self.width() * ratio), int(self.height() * ratio), QImage.Format_ARGB32_Premultiplied)
            image.setDevicePixelRatio(ratio)
            image.fill(QColor(*self.view.background_spec().color))
            painter = QPainter(image)
            painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
            target = QRectF(offset.x(), offset.y(), self.world.width() * scale, self.world.height() * scale)
            canvas.scene.render(painter, target, self.world)
            painter.end()
            self.image = image
            self.update()

        def bar_rect(self) -> QRectF:
            return QRectF(0, 0, self.width(), BAR)

        def close_rect(self) -> QRectF:
            return QRectF(self.width() - BAR, 0, BAR, BAR)

        def size_rect(self) -> QRectF:
            return QRectF(self.width() - 2 * BAR, 0, BAR, BAR)

        def paintEvent(self, _event: Any) -> None:  # noqa: N802
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            outline = QPainterPath()
            outline.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
            painter.setClipPath(outline)
            painter.fillRect(self.rect(), QColor("#16181d"))
            if self.image is not None:
                painter.drawImage(0, 0, self.image)
            for view in app.views.values():
                polygon = view.to_scene_polygon(QRectF(view.viewport().rect()))
                mapped = [self.to_widget(QPointF(p)) for p in polygon]
                own = view is self.view
                painter.setPen(QPen(QColor(theme.ACCENT) if own else QColor(200, 205, 216, 140), 2 if own else 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawPolygon(mapped)
            if self.hover or self._mode == "move":
                painter.fillRect(self.bar_rect(), QColor(12, 14, 18, 210))
                painter.setPen(theme.TEXT_LIGHT)
                grip = "⠿  Minimap" if self.width() >= 170 else "⠿"
                painter.drawText(self.bar_rect().adjusted(8, 0, -2 * BAR, 0), Qt.AlignVCenter | Qt.AlignLeft, grip)
                painter.drawText(self.size_rect(), Qt.AlignCenter, {"small": "S", "medium": "M", "large": "L"}[
                    self.state()["size"]])
                painter.drawText(self.close_rect(), Qt.AlignCenter, "×")
            painter.setClipping(False)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(theme.ACCENT) if self._mode == "move" else QColor(255, 255, 255, 50), 1))
            painter.drawPath(outline)
            painter.end()

        def _update_cursor(self, pos: QPointF) -> None:
            if self._mode == "move":
                self.setCursor(Qt.ClosedHandCursor)
            elif self.close_rect().contains(pos) or self.size_rect().contains(pos):
                self.setCursor(Qt.PointingHandCursor)
            elif self.bar_rect().contains(pos):
                self.setCursor(Qt.OpenHandCursor)
            else:
                self.setCursor(Qt.CrossCursor)
            if self.close_rect().contains(pos):
                self.setToolTip("Hide the minimap (show it again with the map button in the header)")
            elif self.size_rect().contains(pos):
                self.setToolTip("Size: small · medium · large")
            elif self.bar_rect().contains(pos):
                self.setToolTip("Drag to move the minimap to another corner")
            else:
                self.setToolTip("Click or drag to move the view · wheel to zoom")

        # ---- Mouse ----------------------------------------------------------------------------

        def enterEvent(self, _event: Any) -> None:  # noqa: N802
            self.hover = True
            self.update()

        def leaveEvent(self, _event: Any) -> None:  # noqa: N802
            self.hover = False
            self.update()

        def _move_view(self, pos: QPointF) -> None:
            point = self.to_world(pos)
            self.view.set_camera([point.x(), point.y()])

        def mousePressEvent(self, event: Any) -> None:  # noqa: N802
            pos = event.position()
            app.wm.focus_view_widget(self.view)
            if event.button() != Qt.LeftButton:
                return
            if self.close_rect().contains(pos):
                run_toggle(self.view, False)
            elif self.size_rect().contains(pos):
                current = SIZE_ORDER.index(self.state()["size"])
                self.set_state(size=SIZE_ORDER[(current + 1) % len(SIZE_ORDER)])
            elif self.bar_rect().contains(pos):
                self._mode = "move"
                self._grab = event.position().toPoint()
                self.show_hints()
            else:
                self._mode = "navigate"
                self._move_view(pos)
            self._update_cursor(pos)
            event.accept()

        def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802
            pos = event.position()
            if self._mode == "navigate":
                self._move_view(pos)
            elif self._mode == "move":
                area = self.view.viewport().geometry()
                top_left = self.mapToParent(pos.toPoint()) - self._grab
                x = max(area.left(), min(area.right() - self.width() + 1, top_left.x()))
                y = max(area.top(), min(area.bottom() - self.height() + 1, top_left.y()))
                self.move(x, y)
                self.hints.nearest = self.nearest_corner()
                self.hints.update()
            self._update_cursor(pos)

        def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802
            if event.button() != Qt.LeftButton or self._mode is None:
                return
            mode, self._mode = self._mode, None
            if mode == "move":
                self.hints.hide()
                corner = self.nearest_corner()
                self.view.plugin_state["minimap"] = {**self.state(), "corner": corner}
                app.wm.schedule_save()
                self.apply_state(animate=True)
            else:
                self.schedule()
            self._update_cursor(event.position())

        def wheelEvent(self, event: Any) -> None:  # noqa: N802
            if event.angleDelta().y():
                self.view.zoom_by(1.15 if event.angleDelta().y() > 0 else 1 / 1.15, around_cursor=False)
            event.accept()

        def show_hints(self) -> None:
            area = self.view.viewport().geometry()
            self.hints.setGeometry(area)
            self.hints.spots = {c: QRect(self.corner_pos(c), self.size()) for c in CORNERS}
            self.hints.nearest = self.nearest_corner()
            self.hints.show()
            self.hints.raise_()
            self.raise_()

        def dispose(self) -> None:
            self.view.removeEventFilter(self)
            self.hints.deleteLater()
            self.hide()
            self.deleteLater()

    # ---- One minimap per canvas view -----------------------------------------------------------

    def ensure(view: Any) -> "Minimap":
        existing = maps.get(view.view_name)
        if existing is not None and not isValid(existing):
            existing = None
        if existing is not None and existing.view is view:
            return existing
        if existing is not None:
            existing.dispose()
        widget = Minimap(view)
        maps[view.view_name] = widget
        return widget

    def on_created(event: Any) -> None:
        view = app.views.get(getattr(event, "payload", {}).get("name"))
        if view is not None:
            ensure(view)

    def on_removed(event: Any) -> None:
        widget = maps.pop(getattr(event, "payload", {}).get("name"), None)
        if widget is not None and isValid(widget):
            widget.dispose()

    for view in list(app.views.values()):
        ensure(view)

    def remove_all() -> None:
        for widget in list(maps.values()):
            try:
                widget.dispose()
            except RuntimeError:
                pass
        maps.clear()

    ui.track(remove_all)
    ui.on_ui("view.created", on_created)
    ui.on_ui("view.removed", on_removed)

    def live_maps() -> list["Minimap"]:
        """Minimaps whose widget still exists (a view's widget can be destroyed with its tile
        before ``view.removed`` arrives); dead entries are dropped."""
        for name, widget in list(maps.items()):
            if not isValid(widget):
                del maps[name]
        return list(maps.values())

    def rerender(_event: Any) -> None:
        shared_content[0] = None
        for widget in live_maps():
            widget.schedule()

    def repaint(_event: Any) -> None:
        for widget in live_maps():
            if not widget.isVisible():
                continue
            # Reuse the content bounds of the last render (content edits re-render
            # anyway): measuring every item on each camera move is wasted work.
            if widget._mode != "navigate" and not widget.world.contains(widget.board_rect(widget.content)):
                widget.schedule()
            widget.update()

    def display_changed(_event: Any) -> None:
        for widget in live_maps():
            widget.apply_state()

    ui.on_ui("canvas.refreshed", rerender)
    ui.on_ui("view.display_changed", display_changed)
    ui.on_ui("camera.changed", repaint)

    # ---- Commands, header button and menu -----------------------------------------------------------

    def view_of(view: str | None) -> Any:
        found = app.active_view() if view is None else app.views.get(view)
        if found is None:
            raise CommandError(f"No canvas view '{view}'" if view else "No canvas view")
        return found

    def run_toggle(view: Any, visible: bool | None = None) -> None:
        app.run_command("minimap.toggle", view.view_name, visible)

    def toggle(view: str | None = None, visible: bool | None = None) -> bool:
        """Show or hide a view's minimap (no ``visible`` toggles); new views follow the last choice."""
        target = ensure(view_of(view))
        state = (not target.state()["visible"]) if visible is None else bool(visible)
        target.set_state(visible=state)
        app.settings.setValue(DEFAULT_KEY, state)
        ui.status("Minimap shown" if state else "Minimap hidden · the map button in the header brings it back")
        return state

    def place(corner: str | None = None, size: str | None = None, view: str | None = None) -> dict[str, Any]:
        """Put a view's minimap in a corner (tl, tr, bl, br) and/or set its size (small, medium, large)."""
        if corner is not None and corner not in CORNERS:
            raise CommandError("corner must be tl, tr, bl or br")
        if size is not None and size not in SIZES:
            raise CommandError("size must be small, medium or large")
        return ensure(view_of(view)).set_state(corner=corner, size=size)

    ui.command("toggle", toggle, alias="toggle_minimap", undoable=False)
    ui.command("place", place, alias="place_minimap", undoable=False)

    def active_visible() -> bool:
        view = app.active_view()
        widget = maps.get(view.view_name) if view is not None else None
        return bool(widget is not None and isValid(widget) and widget.state()["visible"])

    ui.action("minimap.toggle", "minimap.toggle", "Minimap", icon="map", keywords="overview navigator map",
              category="View", checked=active_visible)
    ui.menu("canvas", "minimap.toggle", path="View", group="view.display", order=3)

    def header_button(view: Any) -> QToolButton:
        button = QToolButton()
        button.setObjectName("tileHeaderButton")
        button.setIcon(icon("map"))
        button.setCheckable(True)
        button.setFocusPolicy(Qt.NoFocus)
        button.setToolTip("Show / hide the minimap")
        widget = ensure(view)
        widget.buttons.append(button)
        button.setChecked(widget.state()["visible"])
        button.clicked.connect(lambda checked: run_toggle(view, bool(checked)))
        return button

    ui.contribute("canvas.header_widgets", header_button)
    ui.provide_service("minimaps", maps)
