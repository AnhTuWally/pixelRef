"""The Qt half of the kernel: the App object and the context plugins get.

The App owns the UI registries (actions, keymap, menus, tools, the canvas
input pipeline, node-type UIs, view types) and the window manager. It
contains no features: everything users see is contributed by plugins
through :class:`UIContext` in their ``activate_ui(ui)``.
"""

from __future__ import annotations

import contextlib
import copy
import logging
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import QPoint, QPointF, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QColor, QCursor
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QWidget
from shiboken6 import isValid

from ..core.events import EventStream
from ..core.extensions import Disposable
from ..core.session import Session
from .actions import ActionRegistry, Keymap, MenuRegistry
from .canvas.display import BackgroundSpec, compute_hidden
from .canvas.document import CanvasDocument, NodeTypeUI
from .canvas.input import InputHandler, InputPipeline
from .canvas.tools import ActiveToolHandler, Tool, ToolManager
from .canvas.view import CanvasView, cursor_pos_in
from .icons import app_icon
from .panels import PanelManager, PanelType
from .theme import stylesheet
from .wm.manager import WindowManager, WorkspacePreset
from .wm.widgets import ViewType

if TYPE_CHECKING:
    from ..core.plugins import PluginRecord

logger = logging.getLogger(__name__)

WINDOW_MODE_KEYS = (
    # key, action key, command, args, label
    ("H", "wm.focus.left", "wm.focus", ("left",), "Focus left"),
    ("J", "wm.focus.down", "wm.focus", ("down",), "Focus down"),
    ("K", "wm.focus.up", "wm.focus", ("up",), "Focus up"),
    ("L", "wm.focus.right", "wm.focus", ("right",), "Focus right"),
    ("Left", "wm.focus.left", None, None, None),
    ("Down", "wm.focus.down", None, None, None),
    ("Up", "wm.focus.up", None, None, None),
    ("Right", "wm.focus.right", None, None, None),
    ("Shift+H", "wm.move.left", "wm.move", ("left",), "Move view left"),
    ("Shift+J", "wm.move.down", "wm.move", ("down",), "Move view down"),
    ("Shift+K", "wm.move.up", "wm.move", ("up",), "Move view up"),
    ("Shift+L", "wm.move.right", "wm.move", ("right",), "Move view right"),
    ("Shift+Left", "wm.move.left", None, None, None),
    ("Shift+Down", "wm.move.down", None, None, None),
    ("Shift+Up", "wm.move.up", None, None, None),
    ("Shift+Right", "wm.move.right", None, None, None),
    ("S", "wm.split.h", "wm.split", ("h",), "Next view opens beside"),
    ("V", "wm.split.v", "wm.split", ("v",), "Next view opens below"),
    ("N", "wm.new_view", "wm.new_view_menu", (), "New view…"),
    ("T", "wm.layout.tabbed", "wm.layout", ("tabbed",), "Tabbed layout"),
    ("Shift+S", "wm.layout.stacked", "wm.layout", ("stacked",), "Stacked layout"),
    ("E", "wm.layout.toggle", "wm.layout", ("split_toggle",), "Toggle split orientation"),
    ("F", "wm.fullscreen", "wm.fullscreen", (), "Fullscreen view"),
    ("Space", "wm.floating", "wm.floating", (), "Toggle floating"),
    ("R", "keymap.mode.resize", "keymap.enter_mode", ("resize",), "Resize mode"),
    ("=", "wm.equalize", "wm.equalize", (), "Equalize sizes"),
    ("Q", "wm.close_view", "wm.close_view", (), "Close view"),
    ("-", "wm.scratchpad", "wm.scratchpad_show", (), "Show/hide scratchpad"),
    ("O", "wm.tear_off", "wm.tear_off", (), "Tear off into a window"),
    ("M", "wm.mark", "wm.mark_prompt", (), "Mark view…"),
    ("Shift+R", "wm.rename_view", "wm.rename_view_dialog", (), "Rename view…"),
    ("'", "wm.goto_mark", "wm.goto_mark_prompt", (), "Go to mark…"),
)

RESIZE_MODE_KEYS = (
    ("Left", "wm.resize.narrower", "wm.resize", (), {"dimension": "width", "delta": -40}, "Narrower"),
    ("H", "wm.resize.narrower", None, None, None, None),
    ("Right", "wm.resize.wider", "wm.resize", (), {"dimension": "width", "delta": 40}, "Wider"),
    ("L", "wm.resize.wider", None, None, None, None),
    ("Up", "wm.resize.shorter", "wm.resize", (), {"dimension": "height", "delta": -40}, "Shorter"),
    ("K", "wm.resize.shorter", None, None, None, None),
    ("Down", "wm.resize.taller", "wm.resize", (), {"dimension": "height", "delta": 40}, "Taller"),
    ("J", "wm.resize.taller", None, None, None, None),
    ("=", "wm.equalize", None, None, None, None),
)


class App:
    """The running desktop application (one per session)."""

    def __init__(self, session: Session, persist_layout: bool = True, load_user_files: bool = True) -> None:
        self.qapp = QApplication.instance() or QApplication(sys.argv)
        self.session = session
        session.headless = False
        session.make_current()
        self.settings = session.settings
        self.stylesheet = stylesheet()
        self.events = EventStream()
        self.menu_scene_pos: QPointF | None = None
        self.views: dict[str, CanvasView] = {}
        self._syncing_cameras = False
        self.closed = False
        self.started = False
        self.load_user_files = load_user_files
        self._status_text = "Ready"
        self._disposables: list[Disposable] = []
        self._view_keymaps: dict[str, Disposable] = {}

        self.actions = ActionRegistry(self)
        self.keymap = Keymap(self)
        self.keymap.mode_titles = {"window": "window", "resize": "resize"}
        self.keymap.persistent_modes = {"resize"}
        self.menus = MenuRegistry(self)
        self.input = InputPipeline()
        self.tools = ToolManager(self)
        self.canvas = CanvasDocument(self)
        self.panels = PanelManager(self)
        self.wm = WindowManager(self)
        self.wm.persist = persist_layout
        self._tool_bindings: dict[str, list[Disposable]] = {}

        self._declare_extension_points()
        self._register_kernel_ui()
        self.input.add(ActiveToolHandler(self.tools), 500)
        self.tools.watch(self._on_tool_changed)
        self.tools.watch_registry(self._sync_tool_actions)
        self._disposables.append(Disposable(session.undo.subscribe(self._on_undo_event)))
        session.events.on("revision.changed", lambda _e: self.update_titles())
        session.events.on("scene.opened", lambda _e: self.update_titles())
        session.events.on("scene.saved", lambda _e: self.update_titles())
        session.events.on("document.replaced", lambda _e: self.update_titles())
        session.events.on("ui.status", lambda e: self.set_status(str(e.payload.get("text", ""))))

        session.plugins.attach_ui(self)
        session.plugins.watch(self._on_plugins_changed)

        restored = persist_layout and self.wm.load_from_settings()
        if not restored:
            self.wm.build_default()
        self.wm.apply()
        if load_user_files:
            self.keymap.load_user_file(self.config_dir() / "keymap.toml")
        self.keymap.install_leader("window")
        self.keymap.end_batch()
        self.actions.update_all_shortcuts()
        self.canvas.request_full_refresh()
        self.started = True
        self.events.emit("app.started", {})
        self.update_titles()
        from .package_jobs import check_packages_on_start

        QTimer.singleShot(0, lambda: None if self.closed else check_packages_on_start(self))

    # ---- Paths ----------------------------------------------------------------------------

    @staticmethod
    def config_dir() -> Path:
        base = QStandardPaths.writableLocation(QStandardPaths.AppConfigLocation) or str(Path.home() / ".pixelref")
        return Path(base)

    @staticmethod
    def data_dir() -> Path:
        base = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation) or str(Path.home() / ".pixelref")
        return Path(base)

    # ---- Extension points ---------------------------------------------------------------------

    def _declare_extension_points(self) -> None:
        ext = self.session.extensions
        for name, doc in (
            ("wm.layouts", "WorkspacePreset objects: the built-in workspaces"),
            ("wm.scratchpad", "Tile specs placed in the scratchpad by default"),
            ("wm.bar_blocks", "(order, factory(app, output_name) -> QWidget) blocks in each window's bar"),
            ("canvas.header_widgets", "factory(view) -> QWidget added to every canvas tile header"),
            ("canvas.overlays", "(order, painter(view, QPainter)) drawn over every canvas"),
            ("canvas.background", "callable() -> BackgroundSpec for the canvas background"),
            ("app.close_handlers", "callable() -> bool; return False to cancel closing the app"),
            ("app.welcome_lines", "Lines of text on the empty-board welcome card"),
            ("escape", "(order, callable() -> bool) Esc handlers, before the tool and the selection"),
            ("canvas.item_hooks", "callable(item, node) run after every canvas item is built (any node type): "
                                  "e.g. install paint filters with item.add_paint_filter()"),
        ):
            self._disposables.append(ext.declare(name, owner="kernel", doc=doc))
        self.canvas.item_hooks = list(ext.contributions("canvas.item_hooks"))
        self._disposables.append(ext.watch("canvas.item_hooks", self.canvas._on_item_hooks_changed))
        for name in ("wm.bar_blocks", "canvas.header_widgets"):
            ext.watch(name, self._on_bar_blocks_changed if name == "wm.bar_blocks" else self._on_header_widgets_changed)

    # ---- Kernel commands / actions / keys -------------------------------------------------------

    def _register_kernel_ui(self) -> None:
        reg = self.session.commands.register
        app = self

        def set_tool(tool_id: str) -> str | None:
            """Choose a tool (choosing the active tool again goes back to Select)."""
            if tool_id not in app.tools.tools:
                from ..core.errors import CommandError

                raise CommandError(f"Unknown tool '{tool_id}' (available: {', '.join(app.tools.tools)})")
            app.tools.set_tool(tool_id)
            return app.tools.active_id

        def activate_tool(tool_id: str, **args: Any) -> str | None:
            """Make a tool active (no toggling); extra arguments go to the tool."""
            if not app.tools.activate(tool_id, **args):
                from ..core.errors import CommandError

                raise CommandError(f"Unknown tool '{tool_id}'")
            return app.tools.active_id

        def current_tool() -> str | None:
            """The active tool's id."""
            return app.tools.active_id

        def list_tools() -> list[str]:
            """Ids of the registered tools in palette order."""
            return [tool.id for tool in app.tools.ordered()]

        def tool_for(tool_id: str | None) -> Any:
            from ..core.errors import CommandError

            tool = app.tools.active if tool_id is None else app.tools.get(tool_id)
            if tool is None:
                raise CommandError(f"Unknown tool '{tool_id}' (available: {', '.join(app.tools.tools)})")
            return tool

        def checked(fn: Any) -> Any:
            from ..core.errors import CommandError, SceneError

            try:
                return fn()
            except SceneError as exc:
                raise CommandError(str(exc)) from None

        def tool_options(tool: str | None = None) -> dict[str, Any]:
            """A tool's option values (the active tool by default)."""
            return tool_for(tool).options.as_dict()

        def tool_option_defaults(tool: str | None = None) -> dict[str, Any]:
            """A tool's defaults: {"factory": {...}, "user": {...}} (user: only what you saved)."""
            options = tool_for(tool).options
            return {"factory": options.factory_defaults(), "user": options.user_defaults()}

        def set_tool_option(tool: str, name: str, value: Any) -> Any:
            """Set one of a tool's options for this session (``set_tool_option("rect", "thickness", 6)``)."""
            return checked(lambda: tool_for(tool).options.set(name, value))

        def set_tool_options(tool: str | None = None, **values: Any) -> dict[str, Any]:
            """Set several of a tool's options (``set_tool_options("rect", thickness=6, corner_radius=8)``)."""
            options = tool_for(tool).options
            checked(lambda: options.update(values))
            return options.as_dict()

        def save_tool_defaults(tool: str | None = None, names: Any = None) -> dict[str, Any]:
            """Keep a tool's current options (all, or ``names``) as your defaults (kept across restarts)."""
            return checked(lambda: tool_for(tool).options.save_defaults(names))

        def reset_tool_options(tool: str | None = None, factory: bool = False) -> dict[str, Any]:
            """Put a tool's options back to your defaults (``factory=True``: to the factory defaults)."""
            return tool_for(tool).options.reset(factory=factory)

        def clear_tool_defaults(tool: str | None = None) -> None:
            """Forget your saved defaults for a tool (the factory defaults apply again)."""
            tool_for(tool).options.clear_defaults()

        def save_tool_defaults_from(node: str, tool: str | None = None) -> dict[str, Any]:
            """Use a node's style as your default for new ones: the tool that makes that kind of
            node takes the node's values for its options (a drawing: its first stroke's)."""
            from ..core.errors import CommandError

            scene = app.session.scene
            name = scene.resolve(node)
            source, target = app.tools.creator_for(scene, name, tool)
            if target is None:
                raise CommandError(f"No tool makes {scene.get_node(name).node_type} nodes")
            attrs = scene.get_node(source).attrs
            values = {key: attrs[key] for key in target.options.specs if key in attrs}
            if not values:
                raise CommandError(f"{name} has none of {target.title}'s options")
            checked(lambda: target.options.update(values))
            return target.options.save_defaults(list(values))

        def escape() -> None:
            """Esc: finish text editing, cancel the tool's operation or mode, then deselect."""
            for _order, handler in sorted(app.session.extensions.contributions("escape"), key=lambda e: e[0]):
                if handler():
                    return
            if app.tools.cancel():
                return
            app.session.run("select", clear=True)
            app.set_status("Deselected")

        def command_search() -> None:
            """Open the searchable command menu at the mouse."""
            app.show_command_search()

        def status(text: str) -> None:
            """Show a message in the status bar."""
            app.set_status(text)

        def in_view_message(text: str, color: str | None = None) -> None:
            """Show a short message over the active canvas view."""
            app.toast(text, QColor(color) if color else None)

        def confirm_dialog(message: str, title: str = "pixelRef") -> bool:
            """Ask a yes/no question; returns True for yes."""
            reply = QMessageBox.question(app.main_window(), title, message, QMessageBox.Yes | QMessageBox.No)
            return reply == QMessageBox.Yes

        def prompt_dialog(message: str, title: str = "pixelRef", text: str = "") -> str | None:
            """Ask for a line of text; returns None when cancelled."""
            value, ok = QInputDialog.getText(app.main_window(), title, message, text=text)
            return value if ok else None

        def views() -> list[str]:
            """Names of the canvas views (tiles)."""
            return list(app.views)

        def active_view() -> str | None:
            """The name of the canvas view that commands act on (the focused or last-used one)."""
            view = app.active_view()
            return view.view_name if view is not None else None

        def bind_key(key: str, target: str, when: str | None = None, scope: str = "window",
                     mode: str | None = None) -> None:
            """Bind a key to an action or command for this session (use keymap.toml to keep it)."""
            app.keymap.bind(key, target, when=when, scope=scope, mode=mode, user=True)

        def unbind_key(key: str, mode: str | None = None, target: str | None = None) -> None:
            """Remove a key binding for this session (only from ``target`` when given)."""
            app.keymap.unbind(key, mode, target)

        def set_shortcut(target: str, key: str = "", add: bool = False) -> list[str]:
            """Give an action or command a key, kept in keymap.toml (taken over from whatever had
            it; ``add`` keeps its other keys; no key removes them all). Returns the keys."""
            _keymap_edit(lambda: app.keymap.set_shortcut(target, key, add=add))
            return app.keymap.shortcuts(target)

        def reset_shortcut(target: str) -> list[str]:
            """Back to the built-in keys of an action or command (in keymap.toml). Returns the keys."""
            _keymap_edit(lambda: app.keymap.reset_shortcuts(target))
            return app.keymap.shortcuts(target)

        def _keymap_edit(edit: Callable[[], list[str]]) -> None:
            from ..core.errors import CommandError

            try:
                problems = edit()
            except ValueError as exc:
                raise CommandError(f"keymap.toml can't be read, so it wasn't changed: {exc}") from None
            for problem in problems:
                app.set_status(problem)

        def reload_keymap() -> list[str]:
            """Re-read keymap.toml from the user config folder; returns the problems found."""
            problems = app.keymap.load_user_file(app.config_dir() / "keymap.toml")
            app.set_status(problems[0] if problems else "Reloaded keyboard shortcuts")
            return problems

        def set_leader(key: str) -> None:
            """Change the window-mode leader key (default Ctrl+W); remembered."""
            app.keymap.set_leader(key)

        def enter_mode(name: str) -> None:
            """Enter a key mode (window, resize): keys run that mode's bindings until Esc."""
            app.keymap.enter_mode(name)

        def exit_mode() -> None:
            """Leave the current key mode."""
            app.keymap.exit_mode()

        def list_bindings() -> list[dict[str, Any]]:
            """Every key binding: key, target, when-condition, scope and mode."""
            return [{"key": b.sequence, "target": b.target, "when": b.when if isinstance(b.when, str) else None,
                     "scope": b.scope, "mode": b.mode} for b in app.keymap.live_bindings()]

        def mark_prompt() -> None:
            """Ask for a mark name and give it to the focused view."""
            text = prompt_dialog("Mark name:", "Mark view")
            if text:
                app.session.run("wm.mark", text.strip())

        def goto_mark_prompt() -> None:
            """Ask for a mark name and focus that view."""
            text = prompt_dialog("Go to mark:", "Go to view")
            if text:
                app.session.run("wm.focus", mark=text.strip())

        def panel_id(panel: str) -> str:
            if panel not in app.panels.types:
                from ..core.errors import CommandError

                raise CommandError(f"Unknown panel '{panel}' (panels: {', '.join(app.panels.types)})")
            return panel

        def panel_set_mode(panel: str, mode: str) -> str:
            """Show a panel docked in the sidebar, floating over the canvas, or hidden (pop-up only)."""
            from ..core.errors import CommandError

            try:
                app.panels.set_mode(panel_id(panel), mode)
            except ValueError as exc:
                raise CommandError(str(exc)) from None
            return mode

        def panel_toggle(panel: str) -> str:
            """Hide a panel, or show it again where it was (docked or floating)."""
            pid = panel_id(panel)
            current = app.panels.mode(pid)
            if current != "hidden" and not app.panels.hidden_all and app.panels.window_allows:
                app.settings.setValue(f"panels/last_mode/{pid}", current)
                app.panels.set_mode(pid, "hidden")
                return "hidden"
            last = str(app.settings.value(f"panels/last_mode/{pid}", "") or "")
            mode = current if current != "hidden" else (last if last in ("docked", "floating") else "docked")
            app.panels.hidden_all = False
            app.panels.set_mode(pid, mode)
            if not app.panels.window_allows:
                app.panels.popup(pid)
            return mode

        def panel_popup(panel: str) -> None:
            """Pop a panel up next to the cursor (the Attribute Editor: next to the selection)."""
            app.panels.popup(panel_id(panel))

        def panels_toggle_all() -> bool:
            """Hide or show the sidebar and floating panels (Tab on the canvas). True when shown."""
            return app.panels.toggle_all()

        def panels_set_side(side: str) -> str:
            """Put the sidebar on the "left" or "right" of the main window; remembered."""
            from ..core.errors import CommandError

            try:
                app.panels.set_side(side)
            except ValueError as exc:
                raise CommandError(str(exc)) from None
            return side

        def panels_set_width(width: int) -> int:
            """Set the sidebar width in pixels (220–640); remembered."""
            return app.panels.set_width(width)

        def panels_list() -> list[dict[str, Any]]:
            """Every panel with its mode and whether it is on screen."""
            return [{"id": p.id, "title": p.title, "mode": app.panels.mode(p.id), "shown": app.panels.is_shown(p.id)}
                    for p in app.panels.ordered()]

        def show_plugins() -> None:
            """Open the plugin manager."""
            from .plugin_manager import PluginManagerDialog

            PluginManagerDialog(app, app.main_window()).exec()

        for name, fn, options in (
            ("tools.set", set_tool, {"alias": "set_tool", "undoable": False}),
            ("tools.activate", activate_tool, {"undoable": False}),
            ("tools.current", current_tool, {"alias": "current_tool", "undoable": False, "echo": False}),
            ("tools.list", list_tools, {"undoable": False, "echo": False}),
            ("tools.options", tool_options, {"undoable": False, "echo": False}),
            ("tools.option_defaults", tool_option_defaults, {"undoable": False, "echo": False}),
            ("tools.set_option", set_tool_option, {"alias": "set_tool_option", "undoable": False}),
            ("tools.set_options", set_tool_options, {"undoable": False}),
            ("tools.save_defaults", save_tool_defaults, {"undoable": False}),
            ("tools.reset_options", reset_tool_options, {"undoable": False}),
            ("tools.clear_defaults", clear_tool_defaults, {"undoable": False}),
            ("tools.save_defaults_from", save_tool_defaults_from, {"undoable": False}),
            ("escape", escape, {"undoable": False}),
            ("command_search", command_search, {"undoable": False, "echo": False}),
            ("ui.status", status, {"alias": "status", "undoable": False, "echo": False}),
            ("ui.in_view_message", in_view_message, {"alias": "in_view_message", "undoable": False, "echo": False}),
            ("ui.confirm_dialog", confirm_dialog, {"alias": "confirm_dialog", "undoable": False, "echo": False}),
            ("ui.prompt_dialog", prompt_dialog, {"alias": "prompt_dialog", "undoable": False, "echo": False}),
            ("ui.views", views, {"alias": "views", "undoable": False, "echo": False}),
            ("ui.active_view", active_view, {"alias": "active_view", "undoable": False, "echo": False}),
            ("ui.plugins", show_plugins, {"undoable": False}),
            ("panels.set_mode", panel_set_mode, {"undoable": False}),
            ("panels.toggle", panel_toggle, {"undoable": False}),
            ("panels.popup", panel_popup, {"undoable": False, "echo": False}),
            ("panels.toggle_all", panels_toggle_all, {"undoable": False}),
            ("panels.set_side", panels_set_side, {"undoable": False}),
            ("panels.set_width", panels_set_width, {"undoable": False}),
            ("panels.list", panels_list, {"undoable": False, "echo": False}),
            ("keymap.bind", bind_key, {"alias": "bind_key", "undoable": False}),
            ("keymap.unbind", unbind_key, {"undoable": False}),
            ("keymap.set_shortcut", set_shortcut, {"alias": "set_shortcut", "undoable": False}),
            ("keymap.reset_shortcut", reset_shortcut, {"undoable": False}),
            ("keymap.reload", reload_keymap, {"undoable": False, "echo": False}),
            ("keymap.set_leader", set_leader, {"undoable": False}),
            ("keymap.enter_mode", enter_mode, {"undoable": False, "echo": False}),
            ("keymap.exit_mode", exit_mode, {"undoable": False, "echo": False}),
            ("keymap.list", list_bindings, {"undoable": False, "echo": False}),
            ("wm.mark_prompt", mark_prompt, {"undoable": False, "echo": False}),
            ("wm.goto_mark_prompt", goto_mark_prompt, {"undoable": False, "echo": False}),
        ):
            self._disposables.append(reg(name, fn, **options))

        a = self.actions.define
        self._disposables += [
            a("undo", "undo", "Undo", keywords="back revert", category="Edit", icon_name="undo",
              dynamic_text=lambda: f"Undo {app.session.undo.stack.undo_label()}".strip(),
              enabled=lambda: app.session.undo.stack.can_undo()),
            a("redo", "redo", "Redo", keywords="again forward", category="Edit", icon_name="redo",
              dynamic_text=lambda: f"Redo {app.session.undo.stack.redo_label()}".strip(),
              enabled=lambda: app.session.undo.stack.can_redo()),
            a("escape", "escape", "Deselect / Cancel", category="Edit"),
            a("command_search", "command_search", "Search Commands…", keywords="find palette search commands",
              category="Edit"),
            a("keymap.mode.window", "keymap.enter_mode", "Window Mode", args=("window",),
              keywords="tile split layout i3 leader", category="Window"),
            a("keymap.mode.resize", "keymap.enter_mode", "Resize Mode", args=("resize",), category="Window"),
            a("panels.toggle_all", "panels.toggle_all", "Hide / Show Panels",
              keywords="sidebar panel tools outliner attribute editor inspector hide show",
              category="View"),
            a("wm.new_view", "wm.new_view_menu", "New View…", keywords="open panel tile window", category="Window"),
            a("wm.reset_layout", "wm.reset_layout", "Reset Layout", keywords="default workspaces", category="Window"),
            a("ui.plugins", "ui.plugins", "Plugins…", keywords="extensions addons enable disable", category="Window"),
            a("wm.new_workspace", "wm.new_workspace", "New Workspace", keywords="add workspace tab layout",
              category="Window"),
            a("wm.rename_workspace", "wm.rename_workspace_dialog", "Rename Workspace…",
              keywords="workspace name title", category="Window"),
            a("wm.delete_workspace", "wm.delete_workspace_dialog", "Delete Workspace…",
              keywords="remove close workspace", category="Window"),
            a("wm.save_workspace_template", "wm.save_workspace_template_dialog", "Save Workspace as Template…",
              keywords="workspace template preset layout reuse", category="Window"),
        ]
        seen_actions: set[str] = set()
        for key, action_key, command, args, label in WINDOW_MODE_KEYS:
            if command is not None and action_key not in seen_actions and action_key not in self.actions:
                self._disposables.append(a(action_key, command, label, args=args, category="Window"))
                seen_actions.add(action_key)
            self._disposables.append(self.keymap.bind(key, action_key, mode="window"))
        for number in range(1, 10):
            self._disposables.append(a(f"wm.workspace.{number}", "wm.workspace", f"Workspace {number}",
                                       args=(number,), category="Window"))
            self._disposables.append(a(f"wm.move_to_workspace.{number}", "wm.move_to_workspace",
                                       f"Move View to Workspace {number}", args=(number,), category="Window"))
            self._disposables.append(self.keymap.bind(str(number), f"wm.workspace.{number}", mode="window"))
            self._disposables.append(self.keymap.bind(f"Shift+{number}", f"wm.move_to_workspace.{number}",
                                                      mode="window"))
        for key, action_key, command, args, kwargs, label in RESIZE_MODE_KEYS:
            if command is not None and action_key not in seen_actions and action_key not in self.actions:
                self._disposables.append(a(action_key, command, label, args=args or (), kwargs=kwargs,
                                           category="hidden"))
                seen_actions.add(action_key)
            self._disposables.append(self.keymap.bind(key, action_key, mode="resize"))
        b = self.keymap.bind
        self._disposables += [
            b("Ctrl+Z", "undo"), b("Ctrl+Y", "redo"), b("Ctrl+Shift+Z", "redo"),
            b("Esc", "escape"), b("Ctrl+K", "command_search"),
            b("Tab", "panels.toggle_all", scope="canvas"),
            b("Ctrl+Alt+Left", "wm.focus.left"), b("Ctrl+Alt+Right", "wm.focus.right"),
            b("Ctrl+Alt+Up", "wm.focus.up"), b("Ctrl+Alt+Down", "wm.focus.down"),
        ]
        m = self.menus.add
        self._disposables += [
            m("canvas", "undo", group="history", order=0),
            m("canvas", "redo", group="history", order=1),
            m("canvas", builder=self._selection_header, group="header", when="selection.count > 0"),
            m("canvas", builder=self.panels.fill_menu, path="Window/Panels", group="window.panels", order=0),
            self.menus.place_submenu("canvas", "Window/Panels", "window.panels", 0, icon="dock_left"),
            m("canvas", "wm.new_view", path="Window", group="window", order=0),
            m("canvas", builder=self._workspace_menu, path="Window", text="Workspaces", icon="split_right",
              group="window", order=1),
            m("canvas", "keymap.mode.window", path="Window", group="window", order=2),
            m("canvas", "wm.reset_layout", path="Window", group="window", order=3),
            m("canvas", "ui.plugins", path="Window", group="window.extras", order=90),
            m("canvas", builder=self._tools_menu, path="Tools", group="tools", when="selection.count == 0"),
            self.menus.place_submenu("canvas", "Tools", "tools", 0, icon="tools"),
            self.menus.place_submenu("canvas", "File", "menus", 0, icon="folder"),
            self.menus.place_submenu("canvas", "View", "menus", 1, icon="eye"),
            self.menus.place_submenu("canvas", "Window", "menus", 2, icon="window"),
        ]
        ctx = self.session.context
        self._disposables += [
            ctx.provide("canvas.focused", lambda: isinstance(QApplication.focusWidget(), CanvasView)),
            ctx.provide("tool.active", lambda: app.tools.active_id),
            ctx.provide("wm.mode", lambda: app.keymap.mode),
            ctx.provide("wm.focused_view_type", lambda: app.wm.doc.attr(app.wm.focused, "view_type")
                        if app.wm.focused else None),
        ]
        self._disposables.append(self.wm.register_view_type(ViewType(
            id="canvas", title="Canvas", icon="image", factory=self._make_canvas_view,
            save_state=self._save_canvas_state, header_widgets=self._canvas_header_widgets,
            dispose=self._dispose_canvas_view, placement="split",
        )))

    def _selection_header(self, menu: Any) -> None:
        names = self.session.selection.names
        if len(names) == 1:
            node = self.canvas.nodes.get(names[0], {})
            menu.addSection(f"{names[0]}  [{node.get('type', '')}]")
        elif names:
            menu.addSection(f"{len(names)} items")

    def _workspace_menu(self, menu: Any) -> None:
        self.wm.fill_workspaces_menu(menu)

    # ---- Tools as actions ------------------------------------------------------------------------

    def _sync_tool_actions(self) -> None:
        for tool_id in list(self._tool_bindings):
            if tool_id not in self.tools.tools:
                for disposable in self._tool_bindings.pop(tool_id):
                    disposable.dispose()
        for tool in self.tools.ordered():
            if tool.id in self._tool_bindings:
                continue
            if tool.hidden:
                self._tool_bindings[tool.id] = []
                continue
            key = f"tool.{tool.id}"
            items = [self.actions.define(
                key, "tools.set", getattr(tool, "action_text", "") or tool.title, args=(tool.id,), icon_name=tool.icon, tip=tool.tooltip,
                keywords=getattr(tool, "keywords", ""), category="Tools",
                checked=lambda t=tool.id: self.tools.active_id == t, group="tools",
            )]
            for sequence in getattr(tool, "shortcuts", ()) or ():
                items.append(self.keymap.bind(sequence, key))
            self._tool_bindings[tool.id] = items

    def _tools_menu(self, menu: Any) -> None:
        """The Tools sub-menu: the tools in the user's palette order (without those they hid)."""
        palette = self.session.services.get("tool_palette")
        if palette is not None:
            keys = palette.tool_keys()
        else:
            keys = [f"tool.{tool.id}" for tool in self.tools.ordered() if not tool.hidden]
        for key in keys:
            action = self.actions.get(key)
            if action is not None:
                menu.addAction(action)

    def _on_tool_changed(self, old: Tool | None, new: Tool | None) -> None:
        self.actions.refresh_states()
        if new is not None and new.hint:
            self.set_status(new.hint)
        for view in self.views.values():
            view.viewport().setCursor(new.current_cursor() if new is not None else Qt.ArrowCursor)
            view.viewport().update()
        self.events.emit("tool.changed", {"old": old.id if old else None, "new": new.id if new else None})

    # ---- Canvas views ---------------------------------------------------------------------------

    def _make_canvas_view(self, tile: str, state: dict[str, Any]) -> CanvasView:
        view = CanvasView(self, tile)
        display = state.get("display")
        if isinstance(display, dict):
            view.display.apply(display)
        self.views[tile] = view
        view.camera_changed.connect(lambda v=view: self._on_camera_changed(v))
        # Released when the view is disposed (views come and go with splits and plugin changes).
        self._view_keymaps[tile] = self.keymap.install(view, "canvas")
        camera = state.get("camera")

        def place() -> None:
            if not isValid(view):  # the deferred call can outlive a view closed right away
                return
            if isinstance(camera, dict):
                view.set_camera(camera.get("center"), camera.get("zoom"), camera.get("rotation"),
                                flip_x=camera.get("flip_x"), flip_y=camera.get("flip_y"))
            else:
                view.set_camera([0.0, 0.0], 1.0, 0.0)

        QTimer.singleShot(0, place)
        place()
        group = state.get("link_group")
        view.link_group = group if isinstance(group, str) else ""
        plugins = state.get("plugins")
        view.plugin_state = copy.deepcopy(plugins) if isinstance(plugins, dict) else {}
        self.update_hidden(view)
        self.events.emit("view.created", {"name": tile})
        return view

    def _save_canvas_state(self, view: QWidget) -> dict[str, Any]:
        assert isinstance(view, CanvasView)
        return {"camera": view.camera(), "display": view.display.to_dict(), "link_group": view.link_group,
                "plugins": copy.deepcopy(view.plugin_state)}

    def _dispose_canvas_view(self, view: QWidget) -> None:
        for name, existing in list(self.views.items()):
            if existing is view:
                del self.views[name]
                keymap = self._view_keymaps.pop(name, None)
                if keymap is not None:
                    keymap.dispose()
                self.events.emit("view.removed", {"name": name})

    def _canvas_header_widgets(self, view: QWidget) -> list[QWidget]:
        widgets = []
        for factory in self.session.extensions.contributions("canvas.header_widgets"):
            try:
                widget = factory(view)
            except Exception:  # noqa: BLE001
                logger.exception("Canvas header widget failed")
                continue
            if widget is not None:
                widgets.append(widget)
        return widgets

    def _on_header_widgets_changed(self) -> None:
        if self.started:
            self.wm._recreate_tiles_of("canvas")

    def _on_camera_changed(self, view: CanvasView) -> None:
        self.events.emit("camera.changed", {"view": view.view_name})
        # Only the view being driven (by the user, a script or an animation) pushes its camera:
        # followers moving must not echo back, or rounding makes linked views fight a pan.
        if self._syncing_cameras or not view.link_group:
            return
        self.sync_linked_cameras(view)

    def linked_views(self, view: CanvasView) -> list[CanvasView]:
        """The other views in ``view``'s link group."""
        if not view.link_group:
            return []
        return [other for other in self.views.values()
                if other is not view and other.link_group == view.link_group and isValid(other)]

    def sync_linked_cameras(self, view: CanvasView) -> None:
        """Give every view linked with ``view`` its camera."""
        # camera() keeps the flips separate; rotation() of a mirrored view is off by 180°.
        camera = view.camera()
        self._syncing_cameras = True
        try:
            for other in self.linked_views(view):
                other.set_camera(camera["center"], camera["zoom"], camera["rotation"],
                                 flip_x=camera["flip_x"], flip_y=camera["flip_y"])
        finally:
            self._syncing_cameras = False

    def active_view(self) -> CanvasView | None:
        wm = self.wm
        visible = wm.visible_tiles()  # walks frames and the layout tree: compute once
        for tile in (wm.focused, wm.last_canvas):
            if tile and tile in self.views and tile in visible:
                return self.views[tile]
        for name, view in self.views.items():
            if name in visible:
                return view
        return next(iter(self.views.values()), None)

    @property
    def view(self) -> CanvasView | None:
        return self.active_view()

    def update_views(self) -> None:
        for view in self.views.values():
            view.viewport().update()

    def update_hidden(self, view: CanvasView) -> None:
        view.display.hidden = compute_hidden(self.session, view.display)

    def set_display(self, view: CanvasView, values: dict[str, Any]) -> None:
        view.display.apply(values)
        self.update_hidden(view)
        view.refresh_background()
        view.viewport().update()
        self.events.emit("view.display_changed", {"name": view.view_name})

    # ---- Overlays & background ------------------------------------------------------------------

    def overlay_painters(self) -> list[tuple[float, Callable[..., None]]]:
        return sorted(self.session.extensions.contributions("canvas.overlays"), key=lambda e: e[0])

    def background_spec(self) -> BackgroundSpec:
        for provider in self.session.extensions.contributions("canvas.background"):
            try:
                return provider()
            except Exception:  # noqa: BLE001
                logger.exception("Background provider failed")
        return BackgroundSpec()

    def refresh_backgrounds(self) -> None:
        for view in self.views.values():
            view.refresh_background()

    def welcome_lines(self) -> list[str]:
        lines = list(self.session.extensions.contributions("app.welcome_lines"))
        return lines or ["Your board is empty", "F1 shows every shortcut"]

    # ---- Bar ------------------------------------------------------------------------------------

    def on_output_created(self, output: str, window: QWidget) -> None:
        window.setWindowIcon(app_icon())
        if self.wm.doc.attr(output, "primary") and self.panels.window is None:
            self.panels.attach_window(window)
        self._disposables.append(self.keymap.install(window, "window"))
        self._build_bar_blocks(window)

    def _build_bar_blocks(self, window: Any) -> None:
        widgets = []
        for _order, factory in sorted(self.session.extensions.contributions("wm.bar_blocks"), key=lambda e: e[0]):
            try:
                widget = factory(self, window.name)
            except Exception:  # noqa: BLE001
                logger.exception("Bar block failed")
                continue
            if widget is not None:
                widgets.append(widget)
        window.bar.set_blocks(widgets)

    def _on_bar_blocks_changed(self) -> None:
        for window in self.wm.windows.values():
            self._build_bar_blocks(window)

    def bar_block(self, object_name: str, output: str | None = None) -> QWidget | None:
        window = self.wm.windows.get(output or self.wm.doc.primary_output() or "")
        if window is None:
            return None
        for widget in window.bar._block_widgets:
            if widget.objectName() == object_name:
                return widget
        return None

    # ---- Hooks from the canvas / WM ----------------------------------------------------------------

    def on_canvas_refreshed(self) -> None:
        for view in self.views.values():
            if view.display.filters_active:
                self.update_hidden(view)
        self.update_views()
        self.actions.refresh_states()
        self.events.emit("canvas.refreshed", {})

    def on_canvas_rebuilt(self) -> None:
        self.events.emit("canvas.rebuilt", {})

    def on_selection_changed(self) -> None:
        self.actions.refresh_states()
        self.events.emit("ui.selection_changed", {"names": self.session.selection.names})

    def on_item_geometry_changed(self, item: Any) -> None:
        self.events.emit("item.geometry_changed", {"item": item})

    def on_focus_changed(self, tile: str) -> None:
        self.events.emit("focus.changed", {"tile": tile})

    def on_mode_changed(self, mode: str | None) -> None:
        self.wm._update_bars()
        if mode:
            self.set_status(f"{mode.capitalize()} mode — Esc to leave")

    def on_layout_applied(self) -> None:
        self.panels.raise_cards()
        self.events.emit("layout.applied", {})

    def _on_plugins_changed(self) -> None:
        if self.started:
            self.actions.update_all_shortcuts()
            self.actions.refresh_states()

    def _on_undo_event(self, event: str, label: str) -> None:
        if event in ("undo", "redo"):
            word = "Undo" if event == "undo" else "Redo"
            self.set_status(f"{word} {label}".strip())
            self.toast(f"{word} · {label}" if label else word, duration_ms=1400)
        self.actions.refresh_states()

    # ---- Commands & feedback ---------------------------------------------------------------------

    def trigger(self, target: str) -> Any:
        spec = self.actions.specs.get(target)
        if spec is not None:
            return self.run_command(spec.command, *spec.args, **spec.kwargs)
        if self.session.commands.exists(target):
            return self.run_command(target)
        self.set_status(f"Nothing is bound to '{target}'")
        return None

    def run_command(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Run a command for the UI: errors are reported, not raised."""
        try:
            result = self.session.run(name, *args, **kwargs)
            # Flush handlers already ran before the command; running them again would end
            # edits the command just started (e.g. a new note's text editor).
            self.canvas.flush_pending(handlers=False)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.debug("Command %s failed:\n%s", name, traceback.format_exc())
            message = str(exc) or type(exc).__name__
            self.set_status(f"{name}: {message}")
            self.toast(message, QColor("#d27b7b"))
            self.events.emit("command.error", {"command": name, "error": exc})
            return None

    def set_status(self, text: str) -> None:
        self._status_text = text
        for window in self.wm.windows.values():
            window.bar.status.setText(text)

    def status_text(self) -> str:
        return self._status_text

    def toast(self, text: str, color: QColor | None = None, duration_ms: int = 2600) -> None:
        view = self.active_view()
        if view is not None:
            view.show_toast(text, color, duration_ms)

    def report_error(self, title: str, message: str) -> None:
        self.set_status(f"{title}: {message}")
        self.toast(f"{title}: {message}", QColor("#d27b7b"))

    # ---- Windows ------------------------------------------------------------------------------

    def main_window(self) -> QWidget | None:
        return self.wm.primary_window()

    def window_title(self, output: str) -> str:
        doc = self.wm.doc
        if doc.attr(output, "primary"):
            path = self.session.scene_path
            name = path.name if path else "Untitled"
            marker = " •" if self.session.unsaved else ""
            return f"pixelRef — {name}{marker}"
        workspace = doc.attr(output, "workspace")
        return f"pixelRef — {doc.attr(workspace, 'title') or workspace}" if workspace else "pixelRef"

    def update_titles(self) -> None:
        for output, window in self.wm.windows.items():
            window.setWindowTitle(self.window_title(output))

    def scene_pos_for_new_content(self) -> QPointF:
        """Where pasted/created content goes: the context-menu point, the mouse, or the view centre."""
        if self.menu_scene_pos is not None:
            return QPointF(self.menu_scene_pos)
        view = self.active_view()
        if view is None:
            return QPointF(0.0, 0.0)
        pos = cursor_pos_in(view)
        if pos is not None:
            return view.to_scene(QPointF(pos))
        return view.center()

    # ---- Context menu & command search --------------------------------------------------------------

    def show_context_menu(self, view: CanvasView, viewport_pos: QPoint, scene_pos: QPointF) -> None:
        item = view.node_item_at(viewport_pos)
        if item is not None:
            if item.node_name not in self.session.selection.names:
                self.session.run("select", [item.node_name])
        else:
            gizmo = self.session.services.get("gizmo")
            inside = gizmo is not None and gizmo.contains(view, QPointF(viewport_pos))
            if not inside and self.session.selection.names:
                self.session.run("select", clear=True)
        self.menu_scene_pos = scene_pos
        menu = self.menus.build("canvas", view)
        menu.exec(view.viewport().mapToGlobal(viewport_pos))
        QTimer.singleShot(0, lambda: setattr(self, "menu_scene_pos", None))

    def build_context_menu(self, parent: QWidget | None = None) -> Any:
        return self.menus.build("canvas", parent or self.main_window())

    def show_command_search(self) -> None:
        view = self.active_view()
        if view is None:
            menu = self.build_context_menu()
            menu.exec(QCursor.pos())
            return
        viewport = view.viewport()
        pos = viewport.mapFromGlobal(QCursor.pos())
        if not viewport.rect().contains(pos):
            pos = viewport.rect().center()
        self.menu_scene_pos = view.to_scene(QPointF(pos))
        menu = self.menus.build("canvas", view)
        menu.exec(viewport.mapToGlobal(pos))
        QTimer.singleShot(0, lambda: setattr(self, "menu_scene_pos", None))

    # ---- Plugin contexts ----------------------------------------------------------------------------

    def make_ui_context(self, record: "PluginRecord") -> "UIContext":
        return UIContext(self, record)

    # ---- Closing ----------------------------------------------------------------------------------

    def request_close(self) -> bool:
        """Ask the close handlers (unsaved changes…); shut down if they all agree."""
        if self.closed:
            return True
        for handler in self.session.extensions.contributions("app.close_handlers"):
            try:
                if not handler():
                    return False
            except Exception:  # noqa: BLE001
                logger.exception("Close handler failed")
        self.shutdown()
        return True

    def close(self, force: bool = False) -> bool:
        if force:
            self.shutdown()
            return True
        return self.request_close()

    @contextlib.contextmanager
    def ui_batch(self):  # noqa: ANN201
        """Rebuild the shortcuts once at the end instead of once per binding (plugin enable)."""
        keymap = self.keymap
        batching, keymap.batching = keymap.batching, True
        try:
            yield
        finally:
            if not batching:
                keymap.end_batch()

    def shutdown(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.events.emit("app.closing", {})
        self.wm.begin_shutdown()
        try:
            self.wm.save_to_settings()
        except Exception:  # noqa: BLE001
            logger.exception("Saving the layout failed")
        self.keymap.exit_mode()
        # Every binding disposed below would rebuild every QShortcut of every window and view.
        self.keymap.batching = True
        self.session.plugins.ui_host = None
        for plugin_id in reversed(self.session.plugins.load_order()):
            record = self.session.plugins.get(plugin_id)
            if record is not None and record.ui_context is not None:
                record.ui_context.dispose()
                record.ui_context = None
        self.panels.shutdown()
        self.wm.close_all()
        self.canvas.shutdown()
        for disposable in reversed(self._disposables):
            try:
                disposable.dispose()
            except Exception:  # noqa: BLE001
                pass
        self._disposables.clear()
        self.session.shutdown()


class UIContext:
    """What a plugin's ``activate_ui(ui)`` receives.

    Everything registered through it is undone when the plugin is disabled.
    """

    def __init__(self, app: App, record: "PluginRecord") -> None:
        self.app = app
        self.session = app.session
        self.plugin_id = record.id
        self.headless = record.context
        self.settings = app.settings.scoped(f"plugins/{record.id}")
        self.log = logging.getLogger(f"pixelref.plugin.{record.id}")
        self._disposables: list[Disposable] = []

    # ---- Bookkeeping ------------------------------------------------------------------------

    def track(self, disposable: Disposable | Callable[[], None]) -> Disposable:
        if not isinstance(disposable, Disposable):
            disposable = Disposable(disposable)
        self._disposables.append(disposable)
        return disposable

    def dispose(self) -> None:
        keymap = self.app.keymap
        batching, keymap.batching = keymap.batching, True  # rebuild the shortcuts once, not per binding
        try:
            while self._disposables:
                disposable = self._disposables.pop()
                try:
                    disposable.dispose()
                except Exception:  # noqa: BLE001
                    logger.exception("Error disposing a UI contribution of %s", self.plugin_id)
        finally:
            if not batching:
                keymap.end_batch()

    # ---- Shortcuts -----------------------------------------------------------------------------

    @property
    def pxcmd(self):  # noqa: ANN201
        return self.session.commands.proxy()

    @property
    def canvas(self) -> CanvasDocument:
        return self.app.canvas

    @property
    def tools(self) -> ToolManager:
        return self.app.tools

    @property
    def wm(self) -> WindowManager:
        return self.app.wm

    @property
    def scene(self):  # noqa: ANN201
        return self.session.scene

    def run(self, command: str, *args: Any, **kwargs: Any) -> Any:
        """Run a command, raising on errors (use ``app.run_command`` in UI callbacks)."""
        return self.session.run(command, *args, **kwargs)

    def status(self, text: str) -> None:
        self.app.set_status(text)

    def toast(self, text: str, color: QColor | None = None, duration_ms: int = 2600) -> None:
        self.app.toast(text, color, duration_ms)

    # ---- Registrations -------------------------------------------------------------------------

    def command(self, name: str, fn: Callable[..., Any], *, alias: str | None = None, **options: Any) -> Disposable:
        full = name if name.startswith(self.plugin_id + ".") else f"{self.plugin_id}.{name}"
        return self.track(self.session.commands.register(full, fn, alias=alias, plugin=self.plugin_id, **options))

    def action(self, key: str, command: str, text: str, **options: Any) -> Any:
        if "icon" in options:
            options["icon_name"] = options.pop("icon")
        self.track(self.app.actions.define(key, command, text, plugin=self.plugin_id, **options))
        return self.app.actions.get(key)

    def bind(self, sequence: str, target: str, when: Any = None, scope: str = "window",
             mode: str | None = None) -> Disposable:
        return self.track(self.app.keymap.bind(sequence, target, when=when, scope=scope, mode=mode,
                                               plugin=self.plugin_id))

    def menu(self, menu: str, action: str | None = None, **options: Any) -> Disposable:
        return self.track(self.app.menus.add(menu, action, plugin=self.plugin_id, **options))

    def place_submenu(self, menu: str, path: str, group: str, order: float = 0.0, **options: Any) -> Disposable:
        return self.track(self.app.menus.place_submenu(menu, path, group, order, **options))

    def register_tool(self, tool: Tool) -> Disposable:
        return self.track(self.app.tools.register(tool))

    def node_ui(self, type_name: str, **options: Any) -> Disposable:
        return self.track(self.app.canvas.register_node_ui(NodeTypeUI(type_name=type_name, plugin=self.plugin_id,
                                                                      **options)))

    def panel(self, panel_type: PanelType) -> Disposable:
        """A panel outside the tiling layout: docked in the sidebar, floating, or popped up."""
        panel_type.plugin = self.plugin_id
        return self.track(self.app.panels.register(panel_type))

    def view_type(self, view_type: ViewType) -> Disposable:
        view_type.plugin = self.plugin_id
        return self.track(self.app.wm.register_view_type(view_type))

    def overlay(self, painter: Callable[[CanvasView, Any], None], order: float = 50.0) -> Disposable:
        return self.contribute("canvas.overlays", (order, painter))

    def input_handler(self, handler: InputHandler, priority: float) -> Disposable:
        return self.track(self.app.input.add(handler, priority))

    def escape_handler(self, handler: Callable[[], bool], order: float = 0.0) -> Disposable:
        return self.contribute("escape", (order, handler))

    def flush_handler(self, handler: Callable[[], None]) -> Disposable:
        return self.track(self.app.canvas.add_flush_handler(handler))

    def bar_block(self, factory: Callable[[App, str], QWidget], order: float = 50.0) -> Disposable:
        return self.contribute("wm.bar_blocks", (order, factory))

    def workspace(self, preset: WorkspacePreset) -> Disposable:
        return self.contribute("wm.layouts", preset)

    def contribute(self, point: str, value: Any, order: float = 0.0) -> Disposable:
        return self.track(self.session.extensions.contribute(point, value, plugin=self.plugin_id, order=order))

    def declare_extension_point(self, name: str, doc: str = "") -> Disposable:
        return self.track(self.session.extensions.declare(name, owner=self.plugin_id, doc=doc))

    def contributions(self, point: str) -> list[Any]:
        return self.session.extensions.contributions(point)

    def provide_service(self, name: str, service: Any) -> Disposable:
        return self.track(self.session.services.provide(name, service, plugin=self.plugin_id))

    def service(self, name: str, default: Any = None) -> Any:
        return self.session.services.get(name, default)

    def on(self, event: str, callback: Callable[[Any], None]) -> Disposable:
        """Session events (document.changed, selection.changed, scene.opened…)."""
        return self.track(self.session.events.on(event, callback))

    def on_ui(self, event: str, callback: Callable[[Any], None]) -> Disposable:
        """UI events (canvas.refreshed, tool.changed, focus.changed, camera.changed, view.created…)."""
        return self.track(self.app.events.on(event, callback))

    def context_key(self, key: str, provider: Callable[[], Any]) -> Disposable:
        return self.track(self.session.context.provide(key, provider))


