"""The tool system.

A :class:`Tool` is an input handler that is active for every canvas view at
once (there is one active tool). Plugins register tools with
``ui.register_tool(MyTool(ui))``; the tool palette, the keyboard shortcuts
and the context menu are built from the registered tools.

Tool kinds:

* ``persistent`` — the default tool (Select); never "finishes".
* ``sticky`` — stays active after each use until Esc / V (Rect, Pen…).
* ``momentary`` — used once, then the previous tool comes back.

Tool options: a tool declares ``option_specs`` (``Attr`` specs) and reads
``tool.options`` (:class:`ToolOptions`). Each value comes from three layers:
the factory default (the spec's ``default``), the user's default (saved in the
user settings with "Save as default", kept across restarts) and this
session's value. The options panel is generated from the specs unless the tool
builds its own. Name options after the attributes they give new nodes
(``stroke_color``, ``thickness``…) and declare ``creates`` (the node type), so
"use this style for new …" in the Attribute Editor can fill them from a node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import QWidget

from ...core.attributes import Attr
from ...core.errors import SceneError
from ...core.extensions import Disposable
from ...core.values import fast_copy
from .input import InputHandler, KeyEvent, PointerEvent, WheelEvent

if TYPE_CHECKING:
    from ...core.settings import Settings
    from ..app import App, UIContext
    from .view import CanvasView

DEFAULTS_KEY = "defaults"


class ToolOptions:
    """A tool's option values over its user defaults over the factory defaults."""

    def __init__(self, tool_id: str, specs: tuple[Attr, ...], store: "Settings | None") -> None:
        self.tool_id = tool_id
        self.specs: dict[str, Attr] = {spec.name: spec for spec in specs}
        self._store = store
        self._listeners: list[Callable[[str, Any], None]] = []
        self.values: dict[str, Any] = self.factory_defaults()
        self.values.update(self.user_defaults())

    def __contains__(self, name: str) -> bool:
        return name in self.specs

    def __bool__(self) -> bool:
        return bool(self.specs)

    # ---- Values -------------------------------------------------------------------------

    def get(self, name: str, default: Any = None) -> Any:
        return fast_copy(self.values[name]) if name in self.values else default

    def as_dict(self) -> dict[str, Any]:
        return fast_copy(self.values)

    def spec(self, name: str) -> Attr:
        if name not in self.specs:
            known = ", ".join(self.specs) or "none"
            raise SceneError(f"Tool '{self.tool_id}' has no option '{name}' (options: {known})")
        return self.specs[name]

    def set(self, name: str, value: Any) -> Any:
        """Set an option for this session (validated); returns the stored value."""
        value = self.spec(name).validate(value)
        if self.values.get(name) != value:
            self.values[name] = value
            self._notify(name, value)
        return fast_copy(value)

    def update(self, values: dict[str, Any]) -> None:
        checked = {name: self.spec(name).validate(value) for name, value in values.items()}
        for name, value in checked.items():
            if self.values.get(name) != value:
                self.values[name] = value
                self._notify(name, value)

    # ---- Defaults -----------------------------------------------------------------------

    def factory_defaults(self) -> dict[str, Any]:
        return {name: spec.default_value() for name, spec in self.specs.items()}

    def user_defaults(self) -> dict[str, Any]:
        """The saved defaults that still fit the specs (junk and renamed options are skipped)."""
        if self._store is None:
            return {}
        stored = self._store.json_value(DEFAULTS_KEY, {})
        out: dict[str, Any] = {}
        if isinstance(stored, dict):
            for name, value in stored.items():
                if name in self.specs:
                    try:
                        out[name] = self.specs[name].validate(value)
                    except SceneError:
                        continue
        return out

    def defaults(self) -> dict[str, Any]:
        """What "Reset" goes back to: the factory defaults with the user's on top."""
        return {**self.factory_defaults(), **self.user_defaults()}

    def save_defaults(self, names: Any = None) -> dict[str, Any]:
        """Keep the current values (all, or ``names``) as the user's defaults; returns them."""
        chosen = list(self.specs) if names is None else [names] if isinstance(names, str) else list(names)
        saved = self.user_defaults()
        for name in chosen:
            self.spec(name)
            saved[name] = fast_copy(self.values[name])
        factory = self.factory_defaults()
        saved = {k: v for k, v in saved.items() if v != factory.get(k)}  # only what differs
        self._write(saved)
        return fast_copy(saved)

    def set_default(self, name: str, value: Any) -> None:
        """Make ``value`` the user's default for one option (and the current value)."""
        self.set(name, value)
        self.save_defaults([name])

    def clear_defaults(self) -> None:
        """Forget the user's defaults (the factory ones apply again)."""
        self._write({})

    def reset(self, factory: bool = False) -> dict[str, Any]:
        """Back to the user's defaults (``factory``: to the factory ones); returns the values."""
        self.update(self.factory_defaults() if factory else self.defaults())
        return self.as_dict()

    def _write(self, saved: dict[str, Any]) -> None:
        if self._store is None:
            return
        if saved:
            self._store.set_json(DEFAULTS_KEY, saved)
        else:
            self._store.remove(DEFAULTS_KEY)

    # ---- Listeners ----------------------------------------------------------------------

    def watch(self, callback: Callable[[str, Any], None]) -> Disposable:
        """``callback(name, value)`` after an option changes."""
        self._listeners.append(callback)
        return Disposable(lambda: callback in self._listeners and self._listeners.remove(callback))

    def _notify(self, name: str, value: Any) -> None:
        for callback in list(self._listeners):
            callback(name, fast_copy(value))


class Tool(InputHandler):
    id = ""
    title = ""  # short label (tool palette)
    action_text = ""  # menu / command-search text (defaults to the title)
    icon = ""
    tooltip = ""
    keywords = ""  # extra words the command search matches
    shortcuts: tuple[str, ...] = ()  # default keys that choose the tool
    order = 100.0  # position in the palette
    hidden = False  # True: no palette button or Tools-menu entry (started by a command)
    kind = "sticky"
    cursor: QCursor | Qt.CursorShape = Qt.ArrowCursor
    # Whether the selection's transform handles stay usable while this tool is
    # active: None (no), "handles" (handles only), "full" (handles + rotate zones).
    gizmo: str | None = None
    show_selection = True  # draw selection outlines
    exclusive_overlay = False  # only this tool's overlay is painted
    hint = ""  # status bar text while active
    options_title = ""  # shown above the tool's options in the palette
    option_specs: tuple[Attr, ...] = ()  # the tool's options (see ToolOptions)
    creates = ""  # node type the tool makes (its options are that type's style for new nodes)
    # Tools that draw with a colour and width (``uses_brush_color``) name the
    # options holding them; the ``brush`` service reads and sets those.
    uses_brush_color = False
    color_option = ""
    width_option = ""

    def __init__(self, ui: "UIContext") -> None:
        self.ui = ui
        self.app: "App" = ui.app
        settings = getattr(ui.app, "settings", None)
        store = settings.scoped(f"tools/{self.id}") if settings is not None and self.id else None
        self.options = ToolOptions(self.id, tuple(self.option_specs), store)

    # ---- Lifecycle ----------------------------------------------------------------

    def activate(self, **args: Any) -> None:
        return None

    def deactivate(self) -> None:
        return None

    def cancel(self) -> bool:
        """Cancel an operation in progress (Esc). Return True if there was one."""
        return False

    @property
    def busy(self) -> bool:
        """True while an operation (drag) is in progress."""
        return False

    # ---- Presentation ----------------------------------------------------------------

    def banner(self, view: "CanvasView") -> str | None:
        """Text for the banner at the top of each canvas while the tool is active."""
        return None

    def paint_overlay(self, view: "CanvasView", painter: QPainter) -> None:
        return None

    def options_panel(self, parent: QWidget) -> QWidget | None:
        """Widget shown in the tool palette while active (built once).

        By default a form generated from ``option_specs`` (nothing without options)."""
        if not self.options:
            return None
        from ..fields import options_form

        return options_form(self, parent)

    def current_cursor(self) -> QCursor | Qt.CursorShape:
        return self.cursor

    # ---- Input (from the active-tool handler) -------------------------------------------

    def pointer(self, e: PointerEvent) -> str | None:
        return None

    def key(self, e: KeyEvent) -> str | None:
        return None

    def wheel(self, e: WheelEvent) -> str | None:
        return None


class ToolManager:
    def __init__(self, app: "App") -> None:
        self.app = app
        self.tools: dict[str, Tool] = {}
        self.default_id = "select"
        self.active: Tool | None = None
        self.previous_id: str | None = None
        self._listeners: list[Callable[[Tool | None, Tool | None], None]] = []
        self._registry_listeners: list[Callable[[], None]] = []
        self._switching = False

    def register(self, tool: Tool) -> Disposable:
        if not tool.id:
            raise ValueError("A tool needs an id")
        if tool.id in self.tools:
            raise ValueError(f"Tool '{tool.id}' is already registered")
        self.tools[tool.id] = tool
        self._registry_changed()
        if self.active is None and tool.id == self.default_id:
            self.activate(tool.id)

        def remove() -> None:
            if self.tools.get(tool.id) is tool:
                if self.active is tool:
                    self.activate(self.default_id if tool.id != self.default_id else None)
                del self.tools[tool.id]
                self._registry_changed()

        return Disposable(remove)

    def ordered(self) -> list[Tool]:
        return sorted(self.tools.values(), key=lambda t: (t.order, t.id))

    def get(self, tool_id: str) -> Tool | None:
        return self.tools.get(tool_id)

    def creator(self, node_type: str) -> Tool | None:
        """The tool that makes ``node_type`` nodes (``Tool.creates``) and has options."""
        return next((t for t in self.ordered() if t.creates == node_type and t.options), None)

    def creator_for(self, scene: Any, name: str, tool_id: str | None = None) -> tuple[str, Tool | None]:
        """(node whose attributes to read, tool) for "use this style for new …": the node
        itself, or for a container (a drawing) its first child some tool makes."""
        node = scene.get_node(name)
        if tool_id is not None:
            tool = self.get(tool_id)
            if tool is not None and node.node_type != tool.creates:
                child = next((c for c in scene.children_of(name)
                              if scene.get_node(c).node_type == tool.creates), None)
                return (child or name), tool
            return name, tool
        tool = self.creator(node.node_type)
        if tool is not None:
            return name, tool
        for child in scene.children_of(name):
            tool = self.creator(scene.get_node(child).node_type)
            if tool is not None:
                return child, tool
        return name, None

    @property
    def active_id(self) -> str | None:
        return self.active.id if self.active else None

    def watch(self, callback: Callable[[Tool | None, Tool | None], None]) -> Disposable:
        self._listeners.append(callback)
        return Disposable(lambda: callback in self._listeners and self._listeners.remove(callback))

    def watch_registry(self, callback: Callable[[], None]) -> Disposable:
        self._registry_listeners.append(callback)
        return Disposable(lambda: callback in self._registry_listeners and self._registry_listeners.remove(callback))

    def _registry_changed(self) -> None:
        for callback in list(self._registry_listeners):
            callback()

    def activate(self, tool_id: str | None, **args: Any) -> bool:
        """Make ``tool_id`` the active tool (re-activating restarts it)."""
        new = self.tools.get(tool_id) if tool_id else None
        if tool_id and new is None:
            return False
        old = self.active
        if old is not None:
            old.cancel()
            old.deactivate()
        if old is not None and old is not new:
            self.previous_id = old.id
        self.active = new
        if new is not None:
            new.activate(**args)
        for callback in list(self._listeners):
            callback(old, new)
        return True

    def set_tool(self, tool_id: str) -> None:
        """Palette/shortcut behaviour: choosing the active tool again returns to the default."""
        if self.active is not None and tool_id == self.active.id and tool_id != self.default_id:
            tool_id = self.default_id
        self.activate(tool_id)

    def cancel(self) -> bool:
        """Esc: cancel the active tool's operation, or go back to the default tool."""
        tool = self.active
        if tool is None:
            return False
        if tool.cancel():
            self.app.update_views()
            return True
        if tool.id != self.default_id:
            self.activate(self.default_id)
            return True
        return False


class ActiveToolHandler(InputHandler):
    """Routes canvas input to whichever tool is active (priority 500)."""

    def __init__(self, manager: ToolManager) -> None:
        self.manager = manager

    def pointer(self, e: PointerEvent) -> str | None:
        tool = self.manager.active
        return tool.pointer(e) if tool is not None else None

    def hover(self, e: PointerEvent) -> None:
        tool = self.manager.active
        if tool is not None:
            tool.hover(e)
            e.set_cursor(tool.current_cursor())

    def key(self, e: KeyEvent) -> str | None:
        tool = self.manager.active
        return tool.key(e) if tool is not None else None

    def wheel(self, e: WheelEvent) -> str | None:
        tool = self.manager.active
        return tool.wheel(e) if tool is not None else None

    def leave(self, view: "CanvasView") -> None:
        tool = self.manager.active
        if tool is not None:
            tool.leave(view)
