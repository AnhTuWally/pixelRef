"""The Session: one open document plus everything that works on it.

A session owns the node type registry, the scene, undo, the command registry,
the selection, context keys, extension points, services, settings and the
plugin manager. ``pixelref.pxcmd`` talks to the *current* session (the most
recently created one, or whichever was made current).

Headless use (scripts, batch mode, tests)::

    from pixelref.core import Session
    from pixelref import pxcmd

    session = Session()
    session.start()                       # discover + activate plugins
    pxcmd.create_node("annotation_rect", "Box", translate=[10, 20])
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from . import perf, xform
from .commands import CommandRegistry, ContextKeys
from .errors import SceneError
from .events import EventStream
from .extensions import Disposable, ExtensionRegistry, ServiceRegistry
from .scene_file import SCENE_EXT, SCENE_EXTS, SceneParser
from .nodetypes import NodeTypeRegistry, register_base_types
from .packages import PackageManager
from .plugins import PluginManager
from .scene import SceneGraph
from .selection import Selection
from .settings import MemorySettings, Settings
from .undo import UndoManager

_CURRENT: "Session | None" = None

GeometryProvider = Callable[[str], "list[float] | None"]


def current_session() -> "Session":
    if _CURRENT is None:
        raise RuntimeError("No pixelRef session is running (create pixelref.core.Session() first)")
    return _CURRENT


def set_current_session(session: "Session | None") -> None:
    global _CURRENT
    _CURRENT = session


class Session:
    def __init__(
        self,
        settings: Settings | None = None,
        plugin_paths: list[Path] | tuple[Path, ...] = (),
        builtins: bool = True,
        user_plugin_dir: Path | None = None,
        only_plugins: set[str] | None = None,
        make_current: bool = True,
        packages: PackageManager | None = None,
    ) -> None:
        self.settings = settings if settings is not None else MemorySettings()
        # Python packages installed from the Plugins window: on sys.path before
        # any plugin is imported (a pending rebuild is swapped in first).
        self.packages = packages if packages is not None else PackageManager()
        self.packages.activate()
        self.types = NodeTypeRegistry()
        register_base_types(self.types)
        self.scene = SceneGraph(self.types)
        self.format = SceneParser()
        self.undo = UndoManager(self.dumps, self._load_snapshot, revision=lambda: self.scene.revision)
        # Snapshots record each node type's schema version: a type (un)registering changes them.
        self.types.watch(lambda _name: self.scene.touch())
        self.commands = CommandRegistry(self.undo)
        self.selection = Selection(self.scene.has, lambda: self.scene.removals)
        self.events = EventStream()
        self.context = ContextKeys()
        self.services = ServiceRegistry()
        self.extensions = ExtensionRegistry()
        self.scene_path: Path | None = None
        self.revision = 0
        self.saved_revision = 0
        self.headless = True
        self._geometry_providers: list[GeometryProvider] = []
        if user_plugin_dir is None and os.environ.get("PIXELREF_USER_PLUGINS"):
            user_plugin_dir = Path(os.environ["PIXELREF_USER_PLUGINS"])
        self.plugins = PluginManager(self, paths=list(plugin_paths), builtins=builtins,
                                     user_dir=user_plugin_dir, only=only_plugins)

        self.packages.watch(lambda _result: self.plugins.refresh_python_requirements())
        self.undo.subscribe(self._on_undo_event)
        self.selection.subscribe(lambda names: self.events.emit("selection.changed", {"names": names}))
        self.commands.on_after(self._on_command_finished)
        self.context.provide("selection.count", lambda: len(self.selection))
        self.context.provide("selection.types", lambda: {
            self.scene.nodes[n].node_type for n in self.selection.names if n in self.scene.nodes
        })
        self.extensions.declare("scene.before_save", owner="kernel",
                                doc="Callables (session, path) run before a scene is written")
        self.extensions.declare("scene.after_new", owner="kernel",
                                doc="Callables (session) run after a new, empty scene is created")

        from .kernel_commands import register_kernel_commands

        register_kernel_commands(self)
        if make_current:
            set_current_session(self)

    # ---- Lifecycle ------------------------------------------------------------------

    def start(self) -> "Session":
        self.plugins.discover()
        self.plugins.activate_all()
        self.events.emit("session.started", {})
        return self

    def make_current(self) -> None:
        set_current_session(self)

    def shutdown(self) -> None:
        for plugin_id in reversed(self.plugins.load_order()):
            self.plugins.deactivate(plugin_id)
        if _CURRENT is self:
            set_current_session(None)

    # ---- Commands --------------------------------------------------------------------

    @property
    def pxcmd(self):  # noqa: ANN201
        return self.commands.proxy()

    def run(self, command: str, *args: Any, **kwargs: Any) -> Any:
        return self.commands.execute(command, *args, **kwargs)

    def _on_command_finished(self, command: Any, result: Any, error: BaseException | None) -> None:
        self.events.emit("command.finished", {"command": command.name, "error": error})
        self.selection.prune()
        if command.undoable or self.commands.changed_document:
            # Even after an error: the chunk was rolled back and the canvas may
            # need to drop previews made before the failure.
            self.events.emit("document.changed", {"command": command.name})

    def notify_changed(self, reason: str = "script") -> None:
        """Tell views the document changed outside a top-level command."""
        self.selection.prune()
        self.events.emit("document.changed", {"command": reason})

    # ---- Serialization & undo -----------------------------------------------------------

    def dumps(self) -> str:
        """Serialize the document for undo snapshots (no embedded image data)."""
        return self.format.dumps(self.scene, include_assets=False)

    def loads(self, text: str) -> None:
        """Restore an undo snapshot, keeping the in-memory embedded images."""
        self._load_snapshot(text)

    def _load_snapshot(self, text: str) -> None:
        self.format.load_into_scene(self.scene, text, keep_assets=True)
        self.selection.prune()
        self.events.emit("document.replaced", {"reason": "undo"})

    def serialize(self) -> str:
        """The full scene file text (images included)."""
        return self.format.dumps(self.scene, include_assets=True)

    def load_text(self, text: str, path: Path | None = None) -> None:
        """Replace the document with a full scene file text (atomic)."""
        self.format.load_into_scene(self.scene, text, keep_assets=False)
        self.scene_path = Path(path) if path else None
        self.undo.reset()
        self.selection.clear()
        self.events.emit("document.replaced", {"reason": "open"})

    def _on_undo_event(self, event: str, label: str) -> None:
        if event in ("checkpoint", "undo", "redo"):
            self.revision += 1
            self.events.emit("revision.changed", {"revision": self.revision, "event": event, "label": label})

    @property
    def unsaved(self) -> bool:
        return self.revision != self.saved_revision

    def mark_saved(self) -> None:
        self.saved_revision = self.revision
        self.events.emit("revision.changed", {"revision": self.revision, "event": "saved", "label": ""})

    def mark_unsaved(self) -> None:
        self.revision += 1
        self.events.emit("revision.changed", {"revision": self.revision, "event": "dirty", "label": ""})

    # ---- Files ------------------------------------------------------------------------------

    def base_dir(self) -> Path:
        return self.scene_path.parent if self.scene_path is not None else Path.cwd()

    def resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.base_dir() / path
        return path

    def to_scene_path(self, path: str | Path) -> str:
        path = Path(path)
        if self.scene_path is not None:
            try:
                return path.resolve().relative_to(self.scene_path.parent.resolve()).as_posix()
            except (ValueError, OSError):
                return path.as_posix()
        return path.as_posix()

    def new_document(self) -> None:
        self.scene.clear()
        self.scene_path = None
        self.undo.reset()
        self.selection.clear()
        for hook in self.extensions.contributions("scene.after_new"):
            hook(self)
        self.revision = self.saved_revision = 0
        self.events.emit("document.replaced", {"reason": "new"})

    def open_file(self, path: str | Path) -> None:
        path = Path(path)
        with perf.span("file.read"):
            text = self.format.read_text(path)
        with perf.span("file.load"):
            self.load_text(text, path)
        self.revision = self.saved_revision = 0
        self.events.emit("scene.opened", {"path": path.as_posix()})

    def save_file(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.scene_path
        if target is None:
            raise SceneError("No file name: pass a path to save the scene")
        if target.suffix.lower() not in SCENE_EXTS:
            target = target.with_suffix(SCENE_EXT)
        for hook in self.extensions.contributions("scene.before_save"):
            hook(self, target)
        with perf.span("file.snapshot"):
            snapshot = self.format.snapshot(self.scene)
        with perf.span("file.write"):
            snapshot.write(target)
        self.scene_path = target
        self.mark_saved()
        self.events.emit("scene.saved", {"path": target.as_posix()})
        return target

    # ---- Computed attributes ------------------------------------------------------------------

    def add_geometry_provider(self, provider: GeometryProvider) -> Disposable:
        """Something that knows a node's content rectangle better than its type's
        estimate (the canvas, from the drawn item)."""
        self._geometry_providers.insert(0, provider)
        return Disposable(lambda: provider in self._geometry_providers and self._geometry_providers.remove(provider))

    def local_bounds(self, name: str) -> list[float] | None:
        for provider in self._geometry_providers:
            rect = provider(name)
            if rect is not None:
                return [float(v) for v in rect]
        node = self.scene.get_node(name)
        fn = self.types.compute_fn(node.node_type, "local_bounds")
        if fn is not None:
            rect = fn(self, node)
            return [float(v) for v in rect] if rect is not None else None
        if node.children and self.types.is_canvas(node.node_type):
            # Containers: union of their children, in this node's space.
            rects = []
            for child in node.children:
                child_rect = self.local_bounds(child)
                if child_rect is not None:
                    child_node = self.scene.get_node(child)
                    rects.append(xform.local_matrix(child_node.attrs).map_rect(child_rect))
            if rects:
                left = min(r[0] for r in rects)
                top = min(r[1] for r in rects)
                right = max(r[0] + r[2] for r in rects)
                bottom = max(r[1] + r[3] for r in rects)
                return [left, top, right - left, bottom - top]
        return None

    def world_bounds(self, name: str) -> list[float] | None:
        rect = self.local_bounds(name)
        if rect is None:
            return None
        return xform.world_matrix(self.scene, name).map_rect(rect)

    def get_attr(self, name: str, attr: str) -> Any:
        """Stored or computed attribute value."""
        node = self.scene.get_node(name)
        spec = self.scene.attr_spec(node, attr)
        key = spec.name if spec is not None else attr
        if spec is not None and spec.computed:
            return self.compute(name, key)
        if key in node.attrs or (spec is not None and key in self.types.sparse_attrs(node.node_type)):
            return self.scene.get_attr(name, key)
        raise SceneError(f"'{name}' has no attribute '{attr}'")

    def compute(self, name: str, attr: str) -> Any:
        node = self.scene.get_node(name)
        if attr == "local_bounds":
            return self.local_bounds(name)
        if attr == "local_matrix":
            return xform.local_matrix(node.attrs).to_list()
        if attr == "world_matrix":
            return xform.world_matrix(self.scene, name).to_list()
        if attr == "world_bounds":
            return self.world_bounds(name)
        chain = [node] + [self.scene.get_node(a) for a in self.scene.ancestors(name)]
        if attr == "world_visibility":
            return 1 if all(bool(n.attrs.get("visibility", 1)) for n in chain) else 0
        if attr == "world_opacity":
            value = 1.0
            for n in chain:
                value *= float(n.attrs.get("opacity", 1.0))
            return value
        if attr == "world_locked":
            return 1 if any(bool(n.attrs.get("locked", 0)) for n in chain) else 0
        fn = self.types.compute_fn(node.node_type, attr)
        if fn is not None:
            return fn(self, node)
        raise SceneError(f"Don't know how to compute '{name}.{attr}'")
