"""Window modes for the focused window (an output of the window manager)."""

from __future__ import annotations

from typing import Any


def activate_ui(ui: Any) -> None:
    from PySide6.QtCore import Qt

    from pixelref.plugin_api.ui import HANDLED, InputHandler, PointerEvent

    app = ui.app
    wm = ui.wm

    def output() -> str | None:
        return wm.focused_output()

    def flag(name: str) -> bool:
        target = output()
        return bool(wm.doc.attr(target, name)) if target else False

    def toggle_on_top() -> bool:
        """Keep the window above other applications (Ctrl+T)."""
        state = not flag("on_top")
        app.run_command("wm.output_style", on_top=state)
        ui.status(f"Always-on-top {'on' if state else 'off'}")
        return state

    def toggle_frameless() -> bool:
        """Hide or show the window frame (F11); a frameless window resizes from its edges and moves by its bar or view headers (or Alt+drag)."""
        state = not flag("frameless")
        app.run_command("wm.output_style", frameless=state)
        ui.status(f"Frameless mode {'on' if state else 'off'}{' · drag an edge to resize · drag the bar or a view header (or Alt+drag) to move' if state else ''}")
        return state

    def set_opacity(value: float) -> None:
        """Set the window opacity (0.1 to 1.0)."""
        app.run_command("wm.output_style", opacity=float(value))
        ui.status(f"Window opacity {float(value) * 100:.0f}%")

    def ghost() -> None:
        """Toggle a translucent window to trace over other applications (G)."""
        target = output()
        current = float(wm.doc.attr(target, "opacity", 1.0) or 1.0) if target else 1.0
        set_opacity(0.25 if current > 0.5 else 1.0)

    ui.command("toggle_on_top", toggle_on_top, alias="toggle_always_on_top", undoable=False)
    ui.command("toggle_frameless", toggle_frameless, alias="toggle_frameless", undoable=False)
    ui.command("set_opacity", set_opacity, alias="set_window_opacity", undoable=False)
    ui.command("ghost", ghost, alias="toggle_ghost_mode", undoable=False)
    a = ui.action
    a("window_modes.on_top", "window_modes.toggle_on_top", "Always on Top", keywords="pin float window",
      category="Window", checked=lambda: flag("on_top"))
    a("window_modes.frameless", "window_modes.toggle_frameless", "Frameless Window",
      keywords="borderless fullscreen window", category="Window", checked=lambda: flag("frameless"))
    a("window_modes.ghost", "window_modes.ghost", "Ghost Mode (25% opacity)", keywords="transparent see through opacity window",
      category="Window", checked=lambda: float(wm.doc.attr(output(), "opacity", 1.0) or 1.0) < 1.0 if output() else False)
    ui.bind("Ctrl+T", "window_modes.on_top")
    ui.bind("F11", "window_modes.frameless")
    ui.bind("G", "window_modes.ghost")
    for order, key in enumerate(("window_modes.on_top", "window_modes.frameless", "window_modes.ghost")):
        ui.menu("canvas", key, path="Window", group="w_modes", order=order)

    def opacity_menu(menu: Any) -> None:
        for value in (1.0, 0.75, 0.5, 0.25):
            menu.addAction(f"{value * 100:.0f}%", lambda v=value: app.run_command("window_modes.set_opacity", v))

    ui.menu("canvas", builder=opacity_menu, text="Opacity", path="Window", group="w_modes", order=10)

    class WindowDrag(InputHandler):
        """Alt+drag moves the window (when the active tool doesn't use Alt)."""

        def __init__(self) -> None:
            self.offset: Any = None

        def pointer(self, e: PointerEvent) -> str | None:
            if e.kind == "press" and e.button == Qt.LeftButton and e.alt:
                window = e.view.window()
                self.offset = e.global_pos.toPoint() - window.frameGeometry().topLeft()
                e.view.viewport().setCursor(Qt.SizeAllCursor)
                return HANDLED
            if self.offset is None:
                return None
            if e.kind == "move":
                e.view.window().move(e.global_pos.toPoint() - self.offset)
                return HANDLED
            if e.kind == "release":
                self.offset = None
                return HANDLED
            return None

    ui.input_handler(WindowDrag(), 450)
