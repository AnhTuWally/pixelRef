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

    # ---- Click-through ----------------------------------------------------------------------
    # Not stored in the layout: a board must never reopen as a window that ignores the mouse.

    from PySide6.QtCore import QEvent, QObject, QTimer
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon

    from pixelref.plugin_api import CommandError
    from pixelref.plugin_api.ui import app_icon

    # output -> {"armed": left the window once, "pinned": we turned always-on-top on}
    through: dict[str, dict[str, bool]] = {}
    tray: dict[str, Any] = {}

    def auto_off() -> bool:
        return bool(ui.settings.value("click_through_auto_off", True, type=bool))

    class ActivationWatcher(QObject):
        """Turns click-through off when its window is activated again (Alt+Tab, taskbar)
        after it was left once: the window is still active right after turning it on."""

        def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802
            kind = event.type()
            if kind in (QEvent.WindowDeactivate, QEvent.WindowActivate):
                for target, state in through.items():
                    if wm.windows.get(target) is not watched:
                        continue
                    if kind == QEvent.WindowDeactivate:
                        state["armed"] = True
                    elif state["armed"] and auto_off():
                        # Not while Qt is delivering this event: changing flags recreates the window.
                        QTimer.singleShot(0, lambda t=target: t in through and set_click_through(False, t))
            return False

    watcher = ActivationWatcher()

    def update_tray() -> None:
        icon = tray.get("icon")
        if not through:
            if icon is not None:
                icon.hide()
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if icon is None:
            icon = QSystemTrayIcon(app_icon())
            menu = QMenu()
            menu.addAction("Turn Off Click-Through", turn_off_all)
            icon.setContextMenu(menu)
            icon.activated.connect(lambda reason: reason == QSystemTrayIcon.Trigger and turn_off_all())
            tray["icon"], tray["menu"] = icon, menu
        icon.setToolTip("pixelRef · click-through is on · click to turn it off")
        icon.show()

    def turn_off_all() -> None:
        for target in list(through):
            set_click_through(False, target)

    def set_click_through(enabled: bool, output: str | None = None) -> bool:
        """Let mouse clicks pass through the window to the application below, to trace over it.
        Turning it on also keeps the window on top; it turns off when the window is activated again."""
        target = output or wm.focused_output()
        window = wm.windows.get(target) if target else None
        if window is None:
            raise CommandError("No window")
        enabled = bool(enabled)
        if enabled == (target in through):
            return enabled
        if enabled:
            state = {"armed": False, "pinned": not bool(wm.doc.attr(target, "on_top"))}
            if state["pinned"]:  # without it the first click raises the app below over the board
                app.run_command("wm.output_style", on_top=True, output=target)
            through[target] = state
            window.installEventFilter(watcher)
        else:
            state = through.pop(target)
            window.removeEventFilter(watcher)
        window.setWindowFlag(Qt.WindowTransparentForInput, enabled)
        window.show()
        if not enabled and state["pinned"] and wm.doc.has(target):
            app.run_command("wm.output_style", on_top=False, output=target)
        update_tray()
        if enabled:
            ways = "switch back to pixelRef (Alt+Tab)" if auto_off() else "press Ctrl+Shift+T while it has focus"
            if tray.get("icon") is not None and tray["icon"].isVisible():
                ways += " or click the tray icon"
            ui.toast("Click-through on · clicks reach the app below")
            ui.status(f"Click-through on · clicks reach the app below · to turn it off, {ways}")
        else:
            ui.status("Click-through off")
        return enabled

    def toggle_click_through() -> bool:
        """Toggle click-through (Ctrl+Shift+T): mouse clicks pass through the window to the app below."""
        return set_click_through(wm.focused_output() not in through)

    def toggle_auto_off() -> bool:
        """Turn click-through off automatically when the window is activated again."""
        state = not auto_off()
        ui.settings.setValue("click_through_auto_off", state)
        ui.status(f"Click-through turns off when the window is activated: {'yes' if state else 'no'}")
        return state

    def dispose_click_through() -> None:
        turn_off_all()
        icon = tray.pop("icon", None)
        if icon is not None:
            icon.hide()
            icon.deleteLater()
        menu = tray.pop("menu", None)
        if menu is not None:
            menu.deleteLater()

    ui.track(dispose_click_through)
    ui.command("set_click_through", set_click_through, alias="set_click_through", undoable=False)
    ui.command("toggle_click_through", toggle_click_through, alias="toggle_click_through", undoable=False)
    ui.command("toggle_click_through_auto_off", toggle_auto_off, undoable=False)
    a("window_modes.click_through", "window_modes.toggle_click_through", "Click-Through (Transparent to Mouse)",
      keywords="mouse transparent pass through trace overlay ignore input", category="Window",
      checked=lambda: wm.focused_output() in through)
    a("window_modes.click_through_auto_off", "window_modes.toggle_click_through_auto_off",
      "Click-Through Turns Off When Activated", keywords="mouse transparent pass through automatic",
      category="Window", checked=auto_off)
    ui.bind("Ctrl+Shift+T", "window_modes.click_through")
    ui.menu("canvas", "window_modes.click_through", path="Window", group="w_modes", order=3)
    ui.menu("canvas", "window_modes.click_through_auto_off", path="Window", group="w_modes", order=4)
