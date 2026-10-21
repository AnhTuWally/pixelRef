"""Navigation: camera movement on every canvas view.

Input (priority 900, before tools): middle-drag or Space+drag pans,
right-drag pans and a right click opens the context menu, the wheel zooms
around the cursor, and tapping Space maximises the view under the cursor
(window-manager fullscreen). Commands: ``frame``, ``frame_all``,
``frame_selected``, ``view_zoom``, ``zoom_by``, ``view_pan``,
``set_camera``, ``view_info``, ``world_to_screen``.
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolButton, QWidget

from pixelref.plugin_api import CommandError
from pixelref.plugin_api.ui import HANDLED, InputHandler, KeyEvent, PointerEvent, WheelEvent

ZOOM_PRESETS = (0.1, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
TAP_SECONDS = 0.3


class NavigationHandler(InputHandler):
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.ops: dict[int, dict[str, Any]] = {}
        self.space_held = False
        self._space_pressed_at = 0.0
        self._space_dragged = False

    def pointer(self, e: PointerEvent) -> str | None:
        view = e.view
        key = id(view)
        if e.kind == "press":
            current = self.ops.get(key)
            if (current is not None and e.button in (Qt.MiddleButton, Qt.RightButton, Qt.LeftButton)
                    and e.buttons & current.get("button", Qt.RightButton)):
                # A second button during a pan / right-drag: keep the running op (its own
                # release still ends it) instead of replacing it.
                return HANDLED if e.button != Qt.LeftButton or self.space_held else None
            if e.button == Qt.MiddleButton or (e.button == Qt.LeftButton and self.space_held):
                self.ops[key] = {"kind": "pan", "last": e.pos, "button": e.button}
                self._space_dragged = True
                view.viewport().setCursor(Qt.ClosedHandCursor)
                return HANDLED
            if e.button == Qt.RightButton:
                self.ops[key] = {"kind": "right", "press": e.pos, "last": e.pos, "panning": False}
                return HANDLED
            return None
        op = self.ops.get(key)
        if op is None:
            return None
        if e.kind == "move":
            if op["kind"] == "right":
                moved = e.pos - op["press"]
                if not op["panning"] and (abs(moved.x()) > 4 or abs(moved.y()) > 4):
                    op["panning"] = True
                    view.viewport().setCursor(Qt.ClosedHandCursor)
                if op["panning"]:
                    view.pan_by(e.pos - op["last"])
            else:
                view.pan_by(e.pos - op["last"])
            op["last"] = e.pos
            return HANDLED
        if e.kind == "release":
            if op["kind"] == "pan" and e.button == op["button"]:
                del self.ops[key]
                if self.space_held:
                    view.viewport().setCursor(Qt.OpenHandCursor)
            elif op["kind"] == "right" and e.button == Qt.RightButton:
                del self.ops[key]
                if not op["panning"]:
                    self.ui.app.show_context_menu(view, e.pos.toPoint(), view.to_scene(e.pos))
            return HANDLED
        return HANDLED

    def key(self, e: KeyEvent) -> str | None:
        if e.key != Qt.Key_Space:
            return None
        focus = e.view.scene().focusItem()
        if focus is not None and getattr(focus, "editing", False):
            return None  # typing a space in a note
        if e.kind == "press":
            if not e.auto_repeat and not self.space_held:
                self.space_held = True
                self._space_pressed_at = time.monotonic()
                self._space_dragged = False
                e.view.viewport().setCursor(Qt.OpenHandCursor)
            return HANDLED
        if e.auto_repeat:
            return HANDLED
        if self.space_held:
            self.space_held = False
            tapped = not self._space_dragged and time.monotonic() - self._space_pressed_at < TAP_SECONDS
            tool = self.ui.tools.active
            e.view.viewport().setCursor(tool.current_cursor() if tool is not None else Qt.ArrowCursor)
            if tapped:
                tile = self.ui.wm.tile_of_widget(e.view)
                if tile is not None:
                    self.ui.app.run_command("wm.fullscreen", tile)
        return HANDLED

    def wheel(self, e: WheelEvent) -> str | None:
        e.view.glide_zoom(1.15 ** e.steps, e.pos)
        return HANDLED

    def leave(self, view: Any) -> None:
        if self.space_held and not view.hasFocus():
            self.space_held = False


class ZoomBlock(QWidget):
    """Zoom readout and buttons in the bar of each window."""

    def __init__(self, ui: Any, output: str | None = None) -> None:
        super().__init__()
        self.ui = ui
        self.output = output
        self.setObjectName("zoomBlock")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        self.zoom_out_btn = QToolButton(self)
        self.zoom_out_btn.setText("−")
        self.zoom_out_btn.setToolTip("Zoom out (Ctrl+-)")
        self.zoom_label = QToolButton(self)
        self.zoom_label.setText("100%")
        self.zoom_label.setToolTip("Click to reset zoom to 100% (Ctrl+0) · arrow for presets")
        self.zoom_label.setPopupMode(QToolButton.MenuButtonPopup)
        self.zoom_in_btn = QToolButton(self)
        self.zoom_in_btn.setText("+")
        self.zoom_in_btn.setToolTip("Zoom in (Ctrl+=)")
        self.zoom_fit_btn = QToolButton(self)
        self.zoom_fit_btn.setText("Fit")
        self.zoom_fit_btn.setToolTip("Frame everything (F)")
        for button in (self.zoom_out_btn, self.zoom_label, self.zoom_in_btn, self.zoom_fit_btn):
            button.setObjectName("zoomButton")
            button.setFocusPolicy(Qt.NoFocus)
            layout.addWidget(button)
        menu = QMenu(self.zoom_label)
        for level in ZOOM_PRESETS:
            menu.addAction(f"{level * 100:g}%", lambda z=level: ui.app.run_command("navigation.zoom", z,
                                                                                     view=self.view_name()))
        menu.addSeparator()
        for key in ("navigation.frame_all", "navigation.frame_selected"):
            action = ui.app.actions.get(key)
            if action is not None:
                menu.addAction(action)
        self.zoom_label.setMenu(menu)
        self.zoom_label.clicked.connect(lambda: ui.app.run_command("navigation.zoom", 1.0, view=self.view_name()))
        self.zoom_out_btn.clicked.connect(
            lambda: ui.app.run_command("navigation.zoom_by", 1 / 1.25, view=self.view_name()))
        self.zoom_in_btn.clicked.connect(lambda: ui.app.run_command("navigation.zoom_by", 1.25, view=self.view_name()))
        self.zoom_fit_btn.clicked.connect(lambda: ui.app.run_command("navigation.frame_all", view=self.view_name()))
        self.update_zoom()

    def target_view(self) -> Any:
        """The canvas view this window's bar controls: the active view when it is in this
        window, else the first visible canvas view of this window (else the active view)."""
        app = self.ui.app
        active = app.active_view()
        if not self.output:
            return active
        doc = app.wm.doc

        def output_of(name: str) -> str | None:
            workspace = doc.workspace_of(name) if doc.has(name) else None
            return doc.output_showing(workspace) if workspace else None

        if active is not None and output_of(active.view_name) == self.output:
            return active
        visible = app.wm.visible_tiles()
        for name, view in app.views.items():
            if name in visible and output_of(name) == self.output:
                return view
        return active

    def view_name(self) -> str | None:
        view = self.target_view()
        return view.view_name if view is not None else None

    def update_zoom(self) -> None:
        view = self.target_view()
        if view is not None:
            percent = view.zoom_level() * 100.0
            # Whole percents, but two significant digits below 1% (never a bare "0%").
            self.zoom_label.setText(f"{percent:.0f}%" if percent >= 0.995 else f"{percent:.2g}%")


def activate_ui(ui: Any) -> None:
    app = ui.app
    canvas = ui.canvas
    ui.input_handler(NavigationHandler(ui), 900)

    def view_of(view: str | None) -> Any:
        if view is None:
            found = app.active_view()
        else:
            found = app.views.get(view)
        if found is None:
            raise CommandError(f"No canvas view '{view}'" if view else "No canvas view")
        return found

    def frame(nodes: Any = None, view: str | None = None) -> bool:
        """Fit nodes (default: the selection, else everything) in a view."""
        target = view_of(view)
        if nodes is None:
            items = canvas.outline_items() or canvas.visible_content_items()
        else:
            names = [nodes] if isinstance(nodes, str) else list(nodes)
            items = [canvas.item(ui.session.scene.resolve(n)) for n in names]
            items = [i for i in items if i is not None]
        bounds = canvas.scene_bounds(items)
        if bounds.isNull():
            return False
        target.fit_rect(bounds)
        return True

    def frame_all(view: str | None = None) -> None:
        """Fit everything visible in a view (Shift+F, Home)."""
        items = canvas.visible_content_items()
        if items and frame([i.node_name for i in items], view):
            ui.status("Framed all items")

    def frame_selected(view: str | None = None) -> None:
        """Fit the selection in a view (F); everything when nothing is selected."""
        items = canvas.outline_items()
        if not items:
            frame_all(view)
            return
        frame([i.node_name for i in items], view)
        ui.status("Framed selection")

    def zoom(level: float, view: str | None = None) -> float:
        """Set a view's zoom level (1.0 = 100%). Not undoable, like all camera moves."""
        target = view_of(view)
        target.set_zoom(float(level))
        ui.status(f"Zoom: {float(level) * 100:g}%")
        return target.zoom_level()

    def zoom_by(factor: float, view: str | None = None, around_cursor: bool = False) -> float:
        """Multiply a view's zoom by ``factor``."""
        target = view_of(view)
        target.zoom_by(float(factor), around_cursor=around_cursor)
        return target.zoom_level()

    def pan(dx: float, dy: float, view: str | None = None) -> None:
        """Pan by screen pixels."""
        view_of(view).pan_by(QPointF(float(dx), float(dy)))

    def set_camera(center: Any = None, zoom: float | None = None, rotation: float | None = None,
                   view: str | None = None, animate: bool = False, flip_x: bool | None = None,
                   flip_y: bool | None = None) -> dict[str, Any]:
        """Move a view's camera: centre (world point), zoom, rotation and optional mirror flips."""
        target = view_of(view)
        target.set_camera(center, zoom, rotation, animate=animate, flip_x=flip_x, flip_y=flip_y)
        return target.camera()

    def flip(horizontal: bool = True, vertical: bool = False, view: str | None = None) -> dict[str, Any]:
        """Mirror a view horizontally or vertically without changing the scene."""
        target = view_of(view)
        return target.flip(horizontal=horizontal, vertical=vertical)

    def camera(view: str | None = None) -> dict[str, Any]:
        """Where a view is looking: {center, zoom, rotation, flip_x, flip_y}."""
        return view_of(view).camera()

    def world_to_screen(x: float, y: float, view: str | None = None) -> list[float]:
        """A world point's position in a view's widget coordinates."""
        point = view_of(view).to_viewport(QPointF(float(x), float(y)))
        return [point.x(), point.y()]

    for name, fn, alias, echo in (
        ("frame", frame, "frame", True), ("frame_all", frame_all, "frame_all", True),
        ("frame_selected", frame_selected, "frame_selected", True), ("zoom", zoom, "view_zoom", True),
        ("zoom_by", zoom_by, "zoom_by", True), ("pan", pan, "view_pan", True),
        ("set_camera", set_camera, "set_camera", True), ("flip", flip, "flip_view", True),
        ("camera", camera, "view_info", False), ("world_to_screen", world_to_screen, "world_to_screen", False),
    ):
        ui.command(name, fn, alias=alias, undoable=False, echo=echo)

    a = ui.action
    a("navigation.frame_all", "navigation.frame_all", "Frame All", keywords="fit zoom everything view home",
      category="View", icon="frame")
    a("navigation.frame_selected", "navigation.frame_selected", "Frame Selection", keywords="fit zoom focus view",
      category="View", icon="frame")
    a("navigation.flip_h", "navigation.flip", "Flip View Horizontally", args=(True, False),
      keywords="mirror mirror horizontally left right view", category="View")
    a("navigation.flip_v", "navigation.flip", "Flip View Vertically", args=(False, True),
      keywords="mirror upside down vertical view", category="View")
    a("navigation.zoom_actual", "navigation.zoom", "Reset Zoom (100%)", args=(1.0,),
      keywords="100% reset actual size one", category="View")
    a("navigation.zoom_in", "navigation.zoom_by", "Zoom In", args=(1.25,), keywords="magnify bigger closer",
      category="View")
    a("navigation.zoom_out", "navigation.zoom_by", "Zoom Out", args=(1 / 1.25,), keywords="smaller further",
      category="View")
    for key, action in (("F", "navigation.frame_selected"), ("Home", "navigation.frame_all"),
                        ("Shift+F", "navigation.frame_all"), ("Shift+Alt+H", "navigation.flip_h"),
                        ("Shift+Alt+V", "navigation.flip_v"), ("Ctrl+0", "navigation.zoom_actual"),
                        ("1", "navigation.zoom_actual"), ("Ctrl+=", "navigation.zoom_in"),
                        ("Ctrl++", "navigation.zoom_in"), ("Ctrl+-", "navigation.zoom_out")):
        ui.bind(key, action)
    for group, keys in (("view.frame", ("navigation.frame_all", "navigation.frame_selected")),
                        ("view.zoom", ("navigation.zoom_in", "navigation.zoom_out", "navigation.zoom_actual")),
                        ("view.flip", ("navigation.flip_h", "navigation.flip_v"))):
        for order, key in enumerate(keys):
            ui.menu("canvas", key, path="View", group=group, order=order)
    ui.menu("canvas", "navigation.frame_selected", group="focus", order=0, when="selection.count > 0")
    ui.menu("canvas", "navigation.frame_all", group="select", order=20, when="selection.count == 0")

    blocks: list[ZoomBlock] = []

    def make_block(_app: Any, output: str) -> ZoomBlock:
        block = ZoomBlock(ui, output)
        blocks.append(block)
        block.destroyed.connect(lambda *_a, b=block: b in blocks and blocks.remove(b))
        return block

    def update_blocks(_event: Any = None) -> None:
        for block in list(blocks):
            try:
                block.update_zoom()
            except RuntimeError:
                blocks.remove(block)

    ui.bar_block(make_block, order=90)
    ui.on_ui("camera.changed", update_blocks)
    ui.on_ui("focus.changed", update_blocks)
