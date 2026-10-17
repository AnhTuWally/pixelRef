"""The window manager: turns the layout tree into windows and runs ``pxcmd.wm.*``.

Every change to the layout goes through the layout document and then
:meth:`WindowManager.apply`, which rebuilds the container widgets (tile
widgets are created once per tile and only moved, so a canvas keeps its
camera and caches).
"""

from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QInputDialog, QLabel, QMenu, QMessageBox, QWidget

from ...core.errors import CommandError
from ...core.extensions import Disposable
from ..icons import icon
from .layout import CONTAINER_TYPES, LayoutDocument, normalized_sizes
from .widgets import (
    FloatingWindow,
    OutputWindow,
    SplitContainer,
    StackedContainer,
    TabbedContainer,
    TileFrame,
    ViewType,
)

if TYPE_CHECKING:
    from ..app import App

logger = logging.getLogger(__name__)

LAYOUT_KEY = "wm/layout"
NAMED_LAYOUTS_KEY = "wm/named_layouts"
TEMPLATES_KEY = "wm/workspace_templates"
WORKSPACES_SECTION = "workspaces"  # the scene file's workspaces
DIRECTIONS = ("left", "right", "up", "down")


@dataclass
class WorkspacePreset:
    """A built-in workspace (contributed to the ``wm.layouts`` extension point).

    ``root`` is a nested spec::

        {"split": "h", "sizes": [0.24, 0.76], "children": [...]}
        {"tabbed": [...]}   {"stacked": [...]}
        {"tile": "canvas", "name": "Main", "title": "Main", "state": {...}, "header": 1}
    """

    number: int
    title: str
    root: dict[str, Any] | None
    floating: list[dict[str, Any]] = field(default_factory=list)
    window_style: dict[str, Any] | None = None


BLANK_WORKSPACE: dict[str, Any] = {"root": {"tile": "canvas", "name": "Main", "title": "Main"}}


def preset_spec(preset: WorkspacePreset) -> dict[str, Any]:
    return {"title": preset.title, "number": int(preset.number), "window_style": copy.deepcopy(preset.window_style),
            "root": copy.deepcopy(preset.root), "floating": copy.deepcopy(preset.floating)}


class WindowManager:
    def __init__(self, app: "App") -> None:
        self.app = app
        self.doc = LayoutDocument()
        self.view_types: dict[str, ViewType] = {}
        self.frames: dict[str, TileFrame] = {}
        self.windows: dict[str, OutputWindow] = {}
        self.floating_windows: dict[str, FloatingWindow] = {}
        self._containers: list[QWidget] = []
        self.focused: str | None = None
        self.last_canvas: str | None = None
        settings = app.settings
        self.focus_follows_mouse = bool(settings.value("wm/focus_follows_mouse", True, type=bool))
        self.placement_mode = str(settings.value("wm/placement", "manual") or "manual")
        self.rules: dict[str, str] = settings.json_value("wm/rules", {}) or {}
        self.next_split = "h"
        self.applying = False
        self.closing = False
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self.save_to_settings)
        self.persist = True
        self._register_commands()
        session = app.session
        self._hooks = [
            session.extensions.contribute("scene.before_save", self._store_in_scene, plugin=None),
            session.extensions.contribute("scene.after_new", self._reset_for_new_scene, plugin=None),
            session.events.on("scene.opened", self._restore_from_scene),
        ]

    # ---- Workspaces in scene files ---------------------------------------------------------------
    #
    # A scene file keeps its workspaces (titles, numbers, tiling, view states, window
    # style); windows (outputs) and their positions belong to the machine and stay.

    def _store_in_scene(self, session: Any, _path: Any) -> None:
        session.scene.sections[WORKSPACES_SECTION] = self.workspaces_section()
        session.scene.touch()  # undo caches scene snapshots by revision

    def _restore_from_scene(self, _event: Any = None) -> None:
        sections = self.app.session.scene.sections
        section = sections.get(WORKSPACES_SECTION)
        if isinstance(section, dict) and self.load_workspaces_section(section) and self.windows:
            self.apply()

    def _reset_for_new_scene(self, _session: Any) -> None:
        """A new scene starts with the default workspaces."""
        self.load_workspaces_section({"workspaces": [preset_spec(p) for p in self.presets()]})
        if self.windows:
            self.apply()

    def workspaces_section(self) -> dict[str, Any]:
        """The workspaces as plain data (what scene files store)."""
        self.collect_states()
        primary = self.doc.primary_output()
        current = self.doc.attr(primary, "workspace") if primary else None
        return {"version": 1,
                "current": current or "",
                "workspaces": [dict(self.workspace_spec(w), id=w) for w in self.doc.workspaces()]}

    def load_workspaces_section(self, section: dict[str, Any]) -> bool:
        """Replace every workspace with the ones in ``section`` (windows stay)."""
        specs = [w for w in section.get("workspaces", []) or [] if isinstance(w, dict)]
        if not specs:
            return False
        for workspace in self.doc.workspaces():
            self._delete_workspace_node(workspace)
        # Views of the old tiles go too: a new tile reusing an old name (every scene has a
        # "ReviewColor") must build its view from the new state (camera, display, markers),
        # not keep showing the old view.
        for tile in [t for t in self.frames if not self.doc.has(t)]:
            self._dispose_frame(tile)
        made: dict[str, str] = {}
        for spec in specs:
            made[str(spec.get("id") or spec.get("title") or "")] = self.create_workspace(spec, number=spec.get("number"))
        current = made.get(str(section.get("current") or "")) or next(iter(made.values()))
        for output in self.doc.outputs():
            self.doc.set(output, workspace=current if self.doc.attr(output, "primary") else "")
        self.focused = None
        return True

    def run(self, command: str, *args: Any, **kwargs: Any) -> Any:
        return self.app.run_command(command, *args, **kwargs)

    # ---- View types ---------------------------------------------------------------------

    def register_view_type(self, view_type: ViewType) -> Disposable:
        if view_type.id in self.view_types:
            raise ValueError(f"View type '{view_type.id}' is already registered")
        self.view_types[view_type.id] = view_type
        self._recreate_tiles_of(view_type.id)

        def remove() -> None:
            if self.view_types.get(view_type.id) is view_type:
                del self.view_types[view_type.id]
                self._recreate_tiles_of(view_type.id)

        return Disposable(remove)

    def _recreate_tiles_of(self, view_type: str) -> None:
        if self.closing:
            return
        changed = False
        for tile in self.doc.tiles():
            if self.doc.attr(tile, "view_type") == view_type and tile in self.frames:
                self._dispose_frame(tile, keep_state=True)
                changed = True
        if changed and not self.applying and self.windows:
            self.apply()

    def begin_shutdown(self) -> None:
        """Freeze WM updates while the app tears down plugin/UI contributions."""
        self.closing = True
        self._save_timer.stop()

    # ---- Building the layout --------------------------------------------------------------------

    def presets(self) -> list[WorkspacePreset]:
        presets = list(self.app.session.extensions.contributions("wm.layouts"))
        if not presets:
            presets = [WorkspacePreset(1, "Board", {"tile": "canvas", "name": "Main", "title": "Main"})]
        return sorted(presets, key=lambda p: p.number)

    def build_default(self) -> None:
        self._dispose_all_frames()
        self.doc.scene.clear()
        for preset in self.presets():
            self.add_workspace_from_preset(preset)
        scratch = self.doc.scratchpad()
        for spec in self.app.session.extensions.contributions("wm.scratchpad"):
            self._build_spec(scratch, dict(spec))
        workspaces = self.doc.workspaces()
        self.doc.scene.create_node("wm_output", "Main window", attrs={
            "primary": 1, "workspace": workspaces[0] if workspaces else "",
        })
        self.focused = None

    def add_workspace_from_preset(self, preset: WorkspacePreset) -> str:
        return self.create_workspace(preset_spec(preset), number=preset.number)

    # ---- Workspaces as data (templates, scene files) ----------------------------------------------

    def create_workspace(self, spec: dict[str, Any], number: int | None = None, title: str | None = None) -> str:
        """A new workspace from a spec (``workspace_spec``, a template or a preset)."""
        number = self.next_free_number() if number is None else int(number or 0)
        title = str(title or spec.get("title") or (f"Workspace {number}" if number else "Workspace"))
        name = self.doc.scene.unique_name(f"{number}: {title}" if number else title)
        self.doc.scene.create_node("wm_workspace", name, attrs={
            "number": number, "title": title, "window_style": copy.deepcopy(spec.get("window_style")),
        })
        self._fill_workspace(name, spec)
        return name

    def _fill_workspace(self, workspace: str, spec: dict[str, Any]) -> None:
        renames: dict[str, str] = {}
        if spec.get("root"):
            self._build_spec(workspace, copy.deepcopy(spec["root"]), renames)
        for floating in spec.get("floating", []) or []:
            tile = self._build_spec(workspace, copy.deepcopy(floating), renames)
            if tile:
                self.doc.set(tile, floating=1)
        # Linked views share a link group. Each copy of a workspace (a template used
        # twice) gets its own groups, so its views don't follow the other copy's.
        groups: dict[str, str] = {}
        for tile in renames.values():
            state = self.doc.attr(tile, "state") or {}
            if not isinstance(state, dict):
                continue
            old = state.get("link_group")
            if old:
                new = groups.setdefault(str(old), uuid.uuid4().hex[:8])
                self.doc.set(tile, state=dict(state, link_group=new))

    def next_free_number(self) -> int:
        """The lowest workspace number 1–9 not in use (0 when all are: no number)."""
        used = {int(self.doc.attr(w, "number", 0) or 0) for w in self.doc.workspaces()}
        return next((n for n in range(1, 10) if n not in used), 0)

    def workspace_spec(self, workspace: str, doc: LayoutDocument | None = None,
                       template: bool = False) -> dict[str, Any]:
        """A workspace as plain data; ``template`` leaves out what belongs to one scene
        (where each view looks, marks)."""
        doc = doc or self.doc

        def spec_of(name: str) -> dict[str, Any] | None:
            kind = doc.type_of(name)
            if kind == "wm_tile":
                state = copy.deepcopy(doc.attr(name, "state") or {})
                if template and isinstance(state, dict):
                    state.pop("camera", None)
                out: dict[str, Any] = {"tile": doc.attr(name, "view_type") or "", "name": name,
                                       "title": doc.attr(name, "title") or "", "state": state,
                                       "header": int(doc.attr(name, "header", 1) or 0)}
                if doc.attr(name, "mark") and not template:
                    out["mark"] = doc.attr(name, "mark")
                if doc.attr(name, "geometry"):
                    out["geometry"] = list(doc.attr(name, "geometry"))
                return out
            children = [c for c in (spec_of(child) for child in doc.children(name)) if c is not None]
            if kind == "wm_split":
                return {"split": doc.attr(name, "orientation") or "h", "sizes": list(doc.attr(name, "sizes") or []),
                        "children": children}
            if kind in ("wm_tabbed", "wm_stacked"):
                return {kind[3:]: children, "active": int(doc.attr(name, "active", 0) or 0)}
            return None

        root = doc.tiling_root(workspace)
        return {"title": doc.attr(workspace, "title") or workspace,
                "number": int(doc.attr(workspace, "number", 0) or 0),
                "window_style": copy.deepcopy(doc.attr(workspace, "window_style")),
                "root": spec_of(root) if root else None,
                "floating": [f for f in (spec_of(t) for t in doc.floating(workspace)) if f]}

    def replace_workspace(self, workspace: str, spec: dict[str, Any]) -> None:
        """Give a workspace the layout and window style of ``spec`` (keeps its number and title)."""
        for child in list(self.doc.children(workspace)):
            self.doc.scene.delete_node(child)
        self.doc.set(workspace, fullscreen="", window_style=copy.deepcopy(spec.get("window_style")))
        self._fill_workspace(workspace, spec)
        if self.focused and not self.doc.has(self.focused):
            self.focused = None

    def _delete_workspace_node(self, workspace: str) -> None:
        self.doc.scene.delete_node(workspace)
        for output in self.doc.outputs():
            if self.doc.attr(output, "workspace") == workspace:
                self.doc.set(output, workspace="")
        if self.focused and not self.doc.has(self.focused):
            self.focused = None
        if self.last_canvas and not self.doc.has(self.last_canvas):
            self.last_canvas = None

    # ---- Workspace templates ------------------------------------------------------------------

    def builtin_templates(self) -> dict[str, dict[str, Any]]:
        return {p.title: preset_spec(p) for p in self.presets()}

    def user_templates(self) -> dict[str, dict[str, Any]]:
        data = self.app.settings.json_value(TEMPLATES_KEY, {}) or {}
        return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}

    def templates(self) -> dict[str, dict[str, Any]]:
        """Every template by name: the built-in workspaces, then yours (yours win a name clash)."""
        merged = self.builtin_templates()
        merged.update(self.user_templates())
        return merged

    def template(self, name: str) -> dict[str, Any]:
        found = self.templates().get(name)
        if found is None:
            raise CommandError(f"No workspace template '{name}' (have: {', '.join(self.templates())})")
        return found

    def _set_user_templates(self, templates: dict[str, dict[str, Any]]) -> None:
        self.app.settings.set_json(TEMPLATES_KEY, templates)

    def _build_spec(self, parent: str, spec: dict[str, Any], renames: dict[str, str] | None = None) -> str | None:
        scene = self.doc.scene
        if "tile" in spec:
            wanted = str(spec.get("name") or spec["tile"]).replace(" ", "")
            name = scene.unique_name(wanted)
            if renames is not None:
                renames[wanted] = name
            scene.create_node("wm_tile", name, parent=parent, attrs={
                "view_type": str(spec["tile"]),
                "title": str(spec.get("title") or spec.get("name") or ""),
                "state": spec.get("state") or {},
                "header": int(spec.get("header", 1)),
                "mark": str(spec.get("mark", "")),
                "geometry": spec.get("geometry"),
            })
            return name
        for kind in ("split", "tabbed", "stacked"):
            if kind in spec:
                children = spec.get("children") if kind == "split" else spec[kind]
                if kind != "split" and not isinstance(children, list):
                    children = spec.get("children", [])
                node_type = {"split": "wm_split", "tabbed": "wm_tabbed", "stacked": "wm_stacked"}[kind]
                attrs: dict[str, Any] = {}
                if kind == "split":
                    attrs = {"orientation": spec["split"] if spec["split"] in ("h", "v") else "h",
                             "sizes": list(spec.get("sizes", []))}
                elif spec.get("active"):
                    attrs = {"active": int(spec["active"])}
                name = scene.unique_name(spec.get("name") or kind.capitalize())
                scene.create_node(node_type, name, parent=parent, attrs=attrs)
                for child in children or []:
                    self._build_spec(name, dict(child), renames)
                return name
        return None

    # ---- Persistence -------------------------------------------------------------------------

    def collect_states(self) -> None:
        for tile, frame in self.frames.items():
            vt = frame.view_type
            if vt is not None and vt.save_state is not None and self.doc.has(tile):
                try:
                    self.doc.set(tile, state=vt.save_state(frame.body))
                except Exception:  # noqa: BLE001
                    logger.exception("Saving state of tile %s failed", tile)
        for output, window in self.windows.items():
            if self.doc.has(output) and window.isVisible() and window.style_applied is None:
                geometry = window.geometry()
                self.doc.set(output, geometry=[geometry.x(), geometry.y(), geometry.width(), geometry.height()])

    def serialize(self) -> str:
        self.collect_states()
        return self.app.session.format.dumps(self.doc.scene, include_assets=False)

    def restore(self, text: str) -> bool:
        try:
            self._dispose_all_frames()
            self.app.session.format.load_into_scene(self.doc.scene, text)
        except Exception:  # noqa: BLE001
            logger.exception("Could not restore the window layout; using the default")
            self.build_default()
            return False
        if not self.doc.outputs() or not self.doc.workspaces():
            self.build_default()
            return False
        for workspace in self.doc.workspaces():
            self.doc.set(workspace, fullscreen="")
        if not any(self.doc.attr(o, "primary") for o in self.doc.outputs()):
            self.doc.set(self.doc.outputs()[0], primary=1)
        self.doc.scratchpad()
        self.focused = None
        return True

    def save_to_settings(self) -> None:
        if not self.persist:
            return
        try:
            self.app.settings.setValue(LAYOUT_KEY, self.serialize())
        except RuntimeError:
            pass

    def schedule_save(self) -> None:
        if self.persist:
            self._save_timer.start(1000)

    def load_from_settings(self) -> bool:
        text = self.app.settings.value(LAYOUT_KEY, "")
        if not text:
            return False
        return self.restore(str(text))

    # ---- Frames ---------------------------------------------------------------------------

    def _make_frame(self, tile: str) -> TileFrame:
        view_type_id = str(self.doc.attr(tile, "view_type", ""))
        vt = self.view_types.get(view_type_id)
        state = dict(self.doc.attr(tile, "state", {}) or {})
        body: QWidget
        if vt is not None:
            try:
                body = vt.factory(tile, state)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Creating view '%s' failed", view_type_id)
                body = QLabel(f"Could not open '{view_type_id}':\n{exc}")
                body.setObjectName("placeholderTile")
                body.setAlignment(Qt.AlignCenter)
                vt = None
        else:
            body = QLabel(f"Missing plugin for the view type '{view_type_id}'.\n"
                          "Enable it in the Plugins window, or close this tile.")
            body.setObjectName("placeholderTile")
            body.setAlignment(Qt.AlignCenter)
            body.setWordWrap(True)
        frame = TileFrame(self, tile, body, vt)
        self.frames[tile] = frame
        return frame

    def _dispose_frame(self, tile: str, keep_state: bool = False) -> None:
        frame = self.frames.pop(tile, None)
        if frame is None:
            return
        vt = frame.view_type
        if keep_state and vt is not None and vt.save_state is not None and self.doc.has(tile):
            try:
                self.doc.set(tile, state=vt.save_state(frame.body))
            except Exception:  # noqa: BLE001
                pass
        if vt is not None and vt.dispose is not None:
            try:
                vt.dispose(frame.body)
            except Exception:  # noqa: BLE001
                logger.exception("Disposing view %s failed", tile)
        window = self.floating_windows.pop(tile, None)
        frame.hide()
        frame.setParent(None)
        frame.deleteLater()
        if window is not None:
            window.hide()
            window.deleteLater()

    def _dispose_all_frames(self) -> None:
        for tile in list(self.frames):
            self._dispose_frame(tile)

    def frame(self, tile: str) -> TileFrame | None:
        return self.frames.get(tile)

    def view_widget(self, tile: str) -> QWidget | None:
        frame = self.frames.get(tile)
        return frame.body if frame is not None else None

    # ---- Applying the layout ---------------------------------------------------------------------

    def apply(self) -> None:
        if self.closing:
            return
        if self.applying:
            return
        self.applying = True
        try:
            self._apply()
        finally:
            self.applying = False
        self.app.on_layout_applied()
        self.schedule_save()

    def _apply(self) -> None:
        doc = self.doc
        # 1. Take every tile out of the old containers, then drop the containers.
        for frame in self.frames.values():
            frame.hide()
            frame.setParent(None)
        for window in self.windows.values():
            window.set_content(None)
        for container in self._containers:
            container.hide()
            container.setParent(None)
            container.deleteLater()
        self._containers = []

        # 2. Frames for new tiles; drop frames of deleted tiles.
        tiles = set(doc.tiles())
        for tile in list(self.frames):
            if tile not in tiles:
                self._dispose_frame(tile)
        for tile in tiles:
            if tile not in self.frames:
                self._make_frame(tile)
        for tile, frame in self.frames.items():
            frame.set_title(str(doc.attr(tile, "title") or tile))
            frame.set_header_visible(bool(doc.attr(tile, "header", 1)))

        # 3. Outputs.
        for output in list(self.windows):
            if not doc.has(output):
                window = self.windows.pop(output)
                # Floating windows are Qt children of their output: drop them with it, or
                # floating_windows would keep references to deleted widgets.
                for tile, floating in list(self.floating_windows.items()):
                    if floating.parentWidget() is window:
                        del self.floating_windows[tile]
                        floating.hide()
                        floating.deleteLater()
                if self.app.panels.window is window:
                    self.app.panels.detach_window()
                window.closing_allowed = True
                window.close()
                window.deleteLater()
        shown: set[str] = set()
        for output in doc.outputs():
            window = self.windows.get(output)
            if window is None:
                window = self._create_window(output)
            workspace = str(doc.attr(output, "workspace") or "")
            if workspace not in doc.workspaces() or workspace in shown:
                workspace = next((w for w in doc.workspaces()
                                  if w not in shown and doc.output_showing(w) in (None, output)), "")
                doc.set(output, workspace=workspace)
            if workspace:
                shown.add(workspace)
                root = self._root_widget(workspace)
                window.set_content(root)
            self._apply_window_style(output, window, workspace)
        if self.app.panels.window is None:
            primary_window = self.windows.get(doc.primary_output() or "")
            if primary_window is not None:
                self.app.panels.attach_window(primary_window)

        # 4. Floating tiles and the scratchpad.
        visible_floating = set()
        for workspace in shown:
            owner = self.windows.get(doc.output_showing(workspace) or "")
            fullscreen = doc.attr(workspace, "fullscreen")
            for tile in doc.floating(workspace):
                if tile == fullscreen:
                    continue  # shown alone in the output window (step 3); floats again when restored
                self._show_floating(tile, owner)
                visible_floating.add(tile)
        scratch = doc.scratchpad()
        primary = self.windows.get(doc.primary_output() or "")
        for tile in doc.children(scratch):
            if doc.type_of(tile) == "wm_tile" and doc.attr(tile, "shown"):
                self._show_floating(tile, primary, center=True)
                visible_floating.add(tile)
        for tile, window in list(self.floating_windows.items()):
            if tile not in visible_floating:
                window.hide()

        # 5. Focus.
        visible = self.visible_tiles()
        if self.focused not in visible:
            self.focused = None
            for tile in visible:
                if self.doc.attr(tile, "view_type") == "canvas":
                    self.focused = tile
                    break
            if self.focused is None and visible:
                self.focused = visible[0]
        if self.focused and self.doc.attr(self.focused, "view_type") == "canvas":
            self.last_canvas = self.focused
        for tile, frame in self.frames.items():
            frame.set_focused(tile == self.focused)
            frame.set_split_enabled(self.is_tiled(tile))
        self._update_bars()

    def _create_window(self, output: str) -> OutputWindow:
        window = OutputWindow(self, output)
        window.setStyleSheet(self.app.stylesheet)
        geometry = self.doc.attr(output, "geometry")
        if geometry and len(geometry) == 4:
            window.setGeometry(QRect(*[int(v) for v in geometry]))
        self.windows[output] = window
        self.app.on_output_created(output, window)
        window.show()
        return window

    def _apply_window_style(self, output: str, window: OutputWindow, workspace: str) -> None:
        doc = self.doc
        style = doc.attr(workspace, "window_style") if workspace else None
        frameless = bool(doc.attr(output, "frameless")) or bool(style and style.get("frameless"))
        on_top = bool(doc.attr(output, "on_top")) or bool(style and style.get("on_top"))
        flags_changed = (bool(window.windowFlags() & Qt.FramelessWindowHint) != frameless
                         or bool(window.windowFlags() & Qt.WindowStaysOnTopHint) != on_top)
        if style and window.style_applied is None:
            window.saved_geometry = window.geometry()
            window.style_applied = dict(style)
            size = style.get("size")
            if size:
                window.resize(int(size[0]), int(size[1]))
        elif not style and window.style_applied is not None:
            window.style_applied = None
            if window.saved_geometry is not None:
                saved = window.saved_geometry
                window.saved_geometry = None
                QTimer.singleShot(0, lambda: window.setGeometry(saved))
        if flags_changed:
            window.setWindowFlag(Qt.FramelessWindowHint, frameless)
            window.setWindowFlag(Qt.WindowStaysOnTopHint, on_top)
            window.show()
        window.bar.setVisible(not (style and style.get("bar") is False))
        if doc.attr(output, "primary"):
            self.app.panels.set_window_allows(not (style and style.get("panels") is False))
        window.setWindowOpacity(float(doc.attr(output, "opacity", 1.0) or 1.0))
        window.setWindowTitle(self.app.window_title(output))

    def _show_floating(self, tile: str, owner: QWidget | None, center: bool = False) -> None:
        frame = self.frames.get(tile)
        if frame is None:
            return
        window = self.floating_windows.get(tile)
        if window is None:
            window = FloatingWindow(self, tile, owner)
            window.setStyleSheet(self.app.stylesheet)
            self.floating_windows[tile] = window
            geometry = self.doc.attr(tile, "geometry")
            if geometry and len(geometry) == 4:
                window.setGeometry(QRect(*[int(v) for v in geometry]))
            else:
                window.resize(640, 360)
                if owner is not None:
                    rect = owner.geometry()
                    window.move(rect.center() - QPoint(320, 180 if center else 0))
        window.set_frame(frame)
        window.setWindowTitle(str(self.doc.attr(tile, "title") or tile))
        window.show()
        window.raise_()

    def remember_floating_geometry(self, tile: str, window: QWidget) -> None:
        if self.applying or not self.doc.has(tile) or not window.isVisible():
            return
        geometry = window.geometry()
        self.doc.set(tile, geometry=[geometry.x(), geometry.y(), geometry.width(), geometry.height()])
        self.schedule_save()

    def on_floating_closed(self, tile: str) -> None:
        if not self.doc.has(tile):
            return
        if self.doc.workspace_of(tile) == self.doc.scratchpad():
            self.doc.set(tile, shown=0)
            self.apply()
        else:
            QTimer.singleShot(0, lambda: self.doc.has(tile) and self.run("wm.close_view", tile))

    def _root_widget(self, workspace: str) -> QWidget | None:
        fullscreen = str(self.doc.attr(workspace, "fullscreen") or "")
        if fullscreen and self.doc.has(fullscreen) and fullscreen in self.frames:
            frame = self.frames[fullscreen]
            return frame
        root = self.doc.tiling_root(workspace)
        if root is None:
            return None
        return self._build_widget(root, workspace)

    def _build_widget(self, name: str, workspace: str) -> QWidget | None:
        doc = self.doc
        node_type = doc.type_of(name)
        if node_type == "wm_tile":
            return self.frames.get(name)
        children = doc.children(name)
        built = [(child, self._build_widget(child, workspace)) for child in children]
        visible = [(child, widget) for child, widget in built if widget is not None]
        if not visible:
            return None
        if node_type == "wm_split":
            if len(visible) == 1:
                return visible[0][1]
            container = SplitContainer(name, str(doc.attr(name, "orientation", "h")), self)
            all_sizes = normalized_sizes(doc.attr(name, "sizes"), len(children))
            sizes = [all_sizes[children.index(child)] for child, _w in visible]
            for _child, widget in visible:
                container.addWidget(widget)
                widget.show()
            container.setSizes([max(1, int(round(s * 10000))) for s in normalized_sizes(sizes, len(sizes))])
            visible_names = [child for child, _w in visible]
            container.splitterMoved.connect(
                lambda _pos, _index, c=container, n=name, v=visible_names: self._on_split_moved(n, c, v)
            )
            self._constrain(container, [w for _c, w in visible], str(doc.attr(name, "orientation", "h")))
        elif node_type == "wm_tabbed":
            container = TabbedContainer(name)
            for child, widget in visible:
                container.addTab(widget, self._title_of(child))
            container.setCurrentIndex(min(int(doc.attr(name, "active", 0) or 0), container.count() - 1))
            container.currentChanged.connect(lambda index, n=name: self._on_tab_changed(n, index))
            container.tabBarDoubleClicked.connect(
                lambda index, tabs=[c for c, _w in visible]: self._rename_tab(tabs, index))
        elif node_type == "wm_stacked":
            container = StackedContainer(name)
            for child, widget in visible:
                container.add(widget, self._title_of(child))
            container.set_current(min(int(doc.attr(name, "active", 0) or 0), container.count() - 1))
            container.current_changed.connect(lambda index, n=name: self._on_tab_changed(n, index))
        else:
            return None
        self._containers.append(container)
        return container

    @staticmethod
    def _constrain(container: QWidget, widgets: list[QWidget], orientation: str) -> None:
        """A column of panels can't get wider than its widest-allowed panel."""
        if orientation == "v":
            maxima = [w.maximumWidth() for w in widgets]
            if all(m < 16777215 for m in maxima):
                container.setMaximumWidth(max(maxima))
            minima = [w.minimumWidth() for w in widgets]
            if minima:
                container.setMinimumWidth(max(minima))

    def _title_of(self, name: str) -> str:
        if self.doc.type_of(name) == "wm_tile":
            return str(self.doc.attr(name, "title") or name)
        tiles = self.doc.tiles(name)
        return ", ".join(str(self.doc.attr(t, "title") or t) for t in tiles[:3])

    def _on_split_moved(self, name: str, container: SplitContainer, visible: list[str]) -> None:
        if not self.doc.has(name):
            return
        children = self.doc.children(name)
        sizes = normalized_sizes(self.doc.attr(name, "sizes"), len(children))
        pixels = container.sizes()
        total_visible_share = sum(sizes[children.index(c)] for c in visible if c in children) or 1.0
        pixel_total = sum(pixels) or 1
        for child, px in zip(visible, pixels):
            if child in children:
                sizes[children.index(child)] = px / pixel_total * total_visible_share
        self.doc.set(name, sizes=[round(s, 5) for s in sizes])
        self.schedule_save()

    def _on_tab_changed(self, name: str, index: int) -> None:
        if self.doc.has(name) and not self.applying:
            self.doc.set(name, active=int(index))
            children = self.doc.children(name)
            if 0 <= index < len(children):
                tiles = self.doc.tiles(children[index])
                if tiles:
                    self.focus_tile(tiles[0])

    def _update_bars(self) -> None:
        mode = self.app.keymap.mode
        title = self.app.keymap.mode_titles.get(mode, mode) if mode else None
        for output, window in self.windows.items():
            current = self.doc.attr(output, "workspace")
            entries = []
            for workspace in self.doc.workspaces():
                number = int(self.doc.attr(workspace, "number", 0) or 0)
                label = f"{number}: {self.doc.attr(workspace, 'title') or workspace}" if number else workspace
                entries.append((workspace, label, workspace == current))
            window.bar.set_workspaces(entries)
            window.bar.set_mode(title)

    # ---- Queries ---------------------------------------------------------------------------

    def visible_tiles(self) -> list[str]:
        out = []
        for tile, frame in self.frames.items():
            try:
                if frame.isVisible() or (frame.parent() is not None and frame.isVisibleTo(frame.window())
                                         and frame.window().isVisible()):
                    out.append(tile)
            except RuntimeError:
                continue
        # Visible in layout terms (window shown or not, e.g. offscreen tests): tiles in shown workspaces.
        for output in self.doc.outputs():
            workspace = self.doc.attr(output, "workspace")
            if not workspace or not self.doc.has(workspace):
                continue
            fullscreen = self.doc.attr(workspace, "fullscreen")
            root = self.doc.tiling_root(workspace)
            candidates = [fullscreen] if fullscreen and self.doc.has(fullscreen) else (
                self.doc.tiles(root) if root else [])
            candidates += self.doc.floating(workspace)
            for tile in candidates:
                if tile not in out:
                    out.append(tile)
        for tile in self.doc.children(self.doc.scratchpad()):
            if self.doc.attr(tile, "shown") and tile not in out:
                out.append(tile)
        return out

    def is_tiled(self, tile: str) -> bool:
        """In a workspace's tiling layout (not floating, fullscreen or in the scratchpad)."""
        doc = self.doc
        if not doc.has(tile) or doc.attr(tile, "floating"):
            return False
        workspace = doc.workspace_of(tile)
        if workspace is None or workspace == doc.scratchpad() or doc.attr(workspace, "fullscreen"):
            return False
        return True

    def tile_at(self, global_pos: QPoint, exclude: str | None = None) -> str | None:
        """The visible tiled view under a screen position."""
        for tile in self.visible_tiles():
            frame = self.frames.get(tile)
            if tile == exclude or frame is None or not frame.isVisible() or not self.is_tiled(tile):
                continue
            if QRect(frame.mapToGlobal(QPoint(0, 0)), frame.size()).contains(global_pos):
                return tile
        return None

    def can_close(self, tile: str) -> bool:
        return not (self.doc.attr(tile, "view_type") == "canvas" and len(self.tiles_of_type("canvas")) <= 1)

    def title(self, tile: str) -> str:
        return str(self.doc.attr(tile, "title") or tile)

    def nearest_split(self, name: str) -> str | None:
        current: str | None = name
        while current is not None:
            if self.doc.type_of(current) == "wm_split":
                return current
            current = self.doc.parent(current)
        return None

    def tiles_of_type(self, view_type: str) -> list[str]:
        return [t for t in self.doc.tiles() if self.doc.attr(t, "view_type") == view_type]

    def focused_output(self) -> str | None:
        if self.focused and self.doc.has(self.focused):
            workspace = self.doc.workspace_of(self.focused)
            if workspace is not None:
                output = self.doc.output_showing(workspace)
                if output:
                    return output
        return self.doc.primary_output()

    def focused_workspace(self) -> str | None:
        output = self.focused_output()
        return str(self.doc.attr(output, "workspace") or "") if output else None

    def primary_window(self) -> OutputWindow | None:
        return self.windows.get(self.doc.primary_output() or "")

    def tile_of_widget(self, widget: QWidget) -> str | None:
        for tile, frame in self.frames.items():
            if frame.body is widget:
                return tile
        return None

    # ---- Focus -----------------------------------------------------------------------------

    def focus_tile(self, tile: str, from_mouse: bool = False) -> None:
        if tile == self.focused or not self.doc.has(tile):
            return
        if from_mouse and QApplication.mouseButtons() != Qt.NoButton:
            return  # don't steal focus mid-drag
        self.focused = tile
        if self.doc.attr(tile, "view_type") == "canvas":
            self.last_canvas = tile
        for name, frame in self.frames.items():
            frame.set_focused(name == tile)
        self.app.on_focus_changed(tile)

    def focus_view_widget(self, widget: QWidget) -> None:
        tile = self.tile_of_widget(widget)
        if tile is not None:
            self.focus_tile(tile)

    def _neighbor(self, tile: str, direction: str) -> str | None:
        frame = self.frames.get(tile)
        if frame is None or not frame.isVisible():
            return None
        origin = frame.mapToGlobal(frame.rect().center())
        best: tuple[float, str] | None = None
        for other in self.visible_tiles():
            if other == tile:
                continue
            other_frame = self.frames.get(other)
            if other_frame is None or not other_frame.isVisible():
                continue
            center = other_frame.mapToGlobal(other_frame.rect().center())
            dx, dy = center.x() - origin.x(), center.y() - origin.y()
            if direction == "left" and dx >= -1:
                continue
            if direction == "right" and dx <= 1:
                continue
            if direction == "up" and dy >= -1:
                continue
            if direction == "down" and dy <= 1:
                continue
            along = abs(dx) if direction in ("left", "right") else abs(dy)
            across = abs(dy) if direction in ("left", "right") else abs(dx)
            score = along + across * 2.0
            if best is None or score < best[0]:
                best = (score, other)
        return best[1] if best else None

    # ---- Tree edits ---------------------------------------------------------------------------

    def _insert_next_to(self, target: str | None, tile: str, kind: str, workspace: str) -> None:
        """Put ``tile`` next to ``target``: kind is "h", "v", "tabbed" or "stacked"."""
        doc = self.doc
        scene = doc.scene
        root = doc.tiling_root(workspace)
        if root is None:
            scene.reparent(tile, workspace, index=0)
            return
        if target is None or not doc.has(target) or doc.workspace_of(target) != workspace or doc.attr(target, "floating"):
            target = root
        parent = doc.parent(target)
        parent_type = doc.type_of(parent) if parent else ""
        wanted_type = {"h": "wm_split", "v": "wm_split", "tabbed": "wm_tabbed", "stacked": "wm_stacked"}[kind]
        matches = parent_type == wanted_type and (
            wanted_type != "wm_split" or doc.attr(parent, "orientation") == kind
        )
        if matches and parent is not None:
            index = scene.index_of(target) + 1
            sizes = normalized_sizes(doc.attr(parent, "sizes"), len(doc.children(parent)))
            scene.reparent(tile, parent, index)
            if wanted_type == "wm_split" and sizes:
                share = sizes[index - 1] / 2.0
                sizes[index - 1] = share
                sizes.insert(index, share)
                doc.set(parent, sizes=sizes)
            elif wanted_type != "wm_split":
                doc.set(parent, active=index)
            return
        index = scene.index_of(target)
        container = scene.unique_name({"wm_split": "Split", "wm_tabbed": "Tabbed", "wm_stacked": "Stacked"}[wanted_type])
        attrs: dict[str, Any] = {"orientation": kind, "sizes": [0.5, 0.5]} if wanted_type == "wm_split" else {"active": 1}
        scene.create_node(wanted_type, container, parent=parent, attrs=attrs, index=index)
        scene.reparent(target, container)
        scene.reparent(tile, container)

    def _detach(self, tile: str) -> None:
        """Take a tile out of the tree, tidying containers left with 0 or 1 children."""
        doc = self.doc
        parent = doc.parent(tile)
        if parent is None:
            return
        # A view leaving its workspace (or floating) can't stay that workspace's fullscreen view.
        workspace = doc.workspace_of(tile)
        if workspace is not None and doc.attr(workspace, "fullscreen") == tile:
            doc.set(workspace, fullscreen="")
        index = doc.scene.index_of(tile)
        if doc.type_of(parent) == "wm_split":
            sizes = normalized_sizes(doc.attr(parent, "sizes"), len(doc.children(parent)))
            if 0 <= index < len(sizes):
                sizes.pop(index)
                doc.set(parent, sizes=sizes)
        doc.scene.reparent(tile, None)
        self._collapse(parent)

    def _collapse(self, container: str) -> None:
        doc = self.doc
        if not doc.has(container) or doc.type_of(container) not in CONTAINER_TYPES:
            return
        children = doc.children(container)
        parent = doc.parent(container)
        if not children:
            doc.scene.delete_node(container)
            if parent:
                self._collapse(parent)
            return
        if len(children) == 1:
            index = doc.scene.index_of(container)
            child = children[0]
            doc.scene.reparent(child, parent, index)
            doc.scene.delete_node(container)

    def _swap(self, a: str, b: str) -> None:
        scene = self.doc.scene
        node_a, node_b = scene.get_node(a), scene.get_node(b)
        list_a, list_b = scene.siblings_list(node_a.parent), scene.siblings_list(node_b.parent)
        index_a, index_b = list_a.index(a), list_b.index(b)
        list_a[index_a], list_b[index_b] = b, a
        node_a.parent, node_b.parent = node_b.parent, node_a.parent

    def _fresh_title(self, base: str) -> str:
        taken = {str(self.doc.attr(t, "title") or "") for t in self.doc.tiles()}
        if base not in taken:
            return base
        stem = base.rsplit(" ", 1)[0] if base.rsplit(" ", 1)[-1].isdigit() else base
        number = 2
        while f"{stem} {number}" in taken:
            number += 1
        return f"{stem} {number}"

    def _clone_tile(self, source: str, view_type: str | None = None) -> str:
        """A new (unparented) tile showing what ``source`` shows, or a fresh view of ``view_type``."""
        doc = self.doc
        same = view_type is None or view_type == doc.attr(source, "view_type")
        type_id = str(doc.attr(source, "view_type")) if same else str(view_type)
        vt = self.view_types.get(type_id)
        if vt is None:
            raise CommandError(f"Unknown view type '{type_id}' (is its plugin enabled?)")
        state: dict[str, Any] = {}
        if same:
            frame = self.frames.get(source)
            if frame is not None and frame.view_type is not None and frame.view_type.save_state is not None:
                state = dict(frame.view_type.save_state(frame.body))
            else:
                state = copy.deepcopy(dict(doc.attr(source, "state", {}) or {}))
            state.pop("link_group", None)  # a copy of a view starts unlinked
        title = self._fresh_title(self.title(source) if same else (vt.title or type_id))
        tile = doc.scene.unique_name(title.replace(" ", ""))
        doc.scene.create_node("wm_tile", tile, attrs={"view_type": type_id, "title": title, "state": state})
        return tile

    def _split_tile(self, target: str, tile: str, orientation: str, after: bool, ratio: float) -> None:
        """Put ``tile`` beside (h) or above/below (v) ``target``, taking ``ratio`` of its space."""
        doc = self.doc
        scene = doc.scene
        parent = doc.parent(target)
        if parent is not None and doc.type_of(parent) == "wm_split" and doc.attr(parent, "orientation") == orientation:
            children = doc.children(parent)
            sizes = normalized_sizes(doc.attr(parent, "sizes"), len(children))
            index = children.index(target)
            share = sizes[index] * ratio
            sizes[index] -= share
            at = index + 1 if after else index
            scene.reparent(tile, parent, at)
            sizes.insert(at, share)
            doc.set(parent, sizes=[round(s, 5) for s in sizes])
            return
        container = scene.unique_name("Split")
        sizes = [1.0 - ratio, ratio] if after else [ratio, 1.0 - ratio]
        scene.create_node("wm_split", container, parent=parent, index=scene.index_of(target),
                          attrs={"orientation": orientation, "sizes": [round(s, 5) for s in sizes]})
        scene.reparent(target, container)
        scene.reparent(tile, container, 1 if after else 0)

    # ---- Commands ----------------------------------------------------------------------------

    def _tile_ref(self, tile: str | None) -> str:
        if tile is None:
            if not self.focused:
                raise CommandError("No view is focused")
            return self.focused
        if self.doc.has(tile) and self.doc.type_of(tile) == "wm_tile":
            return tile
        found = self.doc.find_mark(tile)
        if found:
            return found
        for candidate in self.doc.tiles():
            if self.doc.attr(candidate, "title") == tile:
                return candidate
        raise CommandError(f"No view named '{tile}'")

    def _register_commands(self) -> None:
        session = self.app.session
        reg = session.commands.register
        wm = self

        def focus(direction: str | None = None, mark: str | None = None, tile: str | None = None) -> str | None:
            """Move focus left/right/up/down, to a mark, or to a named view."""
            if mark:
                target = wm.doc.find_mark(mark)
                if target is None:
                    raise CommandError(f"No view is marked '{mark}'")
            elif tile:
                target = wm._tile_ref(tile)
            elif direction in DIRECTIONS:
                current = wm.focused
                target = wm._neighbor(current, direction) if current else None
                if target is None:
                    return current
            else:
                raise CommandError("focus needs a direction (left/right/up/down), mark= or tile=")
            workspace = wm.doc.workspace_of(target)
            if workspace and workspace != wm.doc.scratchpad() and not wm.doc.output_showing(workspace):
                wm.show_workspace(workspace)
            wm.focus_tile(target)
            frame = wm.frames.get(target)
            if frame is not None:
                frame.body.setFocus()
            return target

        def move(direction: str) -> None:
            """Swap the focused view with its neighbour in that direction."""
            if direction not in DIRECTIONS:
                raise CommandError("direction must be left, right, up or down")
            current = wm._tile_ref(None)
            other = wm._neighbor(current, direction)
            # Only swap within tiling layouts: swapping with a floating or scratchpad view
            # would leave a floating-flagged tile inside a split (and vice versa).
            if other is None or not (wm.is_tiled(current) and wm.is_tiled(other)):
                return
            wm._swap(current, other)
            wm.apply()

        def split(orientation: str = "h") -> str:
            """Where the next view opens: h (side by side) or v (stacked)."""
            if orientation not in ("h", "v"):
                raise CommandError("orientation must be 'h' or 'v'")
            wm.next_split = orientation
            self.app.set_status(f"Next view opens {'beside' if orientation == 'h' else 'below'} the focused one")
            return orientation

        def layout(kind: str) -> None:
            """The focused view's container: tabbed, stacked, split_h, split_v or split_toggle."""
            current = wm._tile_ref(None)
            doc = wm.doc
            parent = doc.parent(current)
            workspace = doc.workspace_of(current)
            # Floating and scratchpad views sit directly under their workspace: wrapping one in a
            # container would take it out of the floating list / the scratchpad (and off screen).
            if parent is None or workspace is None or workspace == doc.scratchpad() or doc.attr(current, "floating"):
                return
            parent_type = doc.type_of(parent)
            if kind == "split_toggle":
                rotate_split(current)
                return
            new_type = {"tabbed": "wm_tabbed", "stacked": "wm_stacked", "split_h": "wm_split", "split_v": "wm_split"}.get(kind)
            if new_type is None:
                raise CommandError("kind must be tabbed, stacked, split_h, split_v or split_toggle")
            scene = doc.scene
            if parent_type not in CONTAINER_TYPES:
                # The tile is the workspace root: wrap it.
                container = scene.unique_name(new_type[3:].capitalize())
                attrs = {"orientation": "h" if kind == "split_h" else "v"} if new_type == "wm_split" else {}
                scene.create_node(new_type, container, parent=parent, attrs=attrs, index=scene.index_of(current))
                scene.reparent(current, container)
            else:
                index = scene.index_of(parent)
                grand = doc.parent(parent)
                attrs = {"orientation": "h" if kind == "split_h" else "v"} if new_type == "wm_split" else {
                    "active": doc.children(parent).index(current)}
                if new_type == "wm_split" and parent_type == "wm_split":
                    doc.set(parent, **attrs)
                else:
                    container = scene.unique_name(new_type[3:].capitalize())
                    scene.create_node(new_type, container, parent=grand, attrs=attrs, index=index)
                    for child in doc.children(parent):
                        scene.reparent(child, container)
                    scene.delete_node(parent)
            wm.apply()

        def split_view(direction: str = "right", tile: str | None = None, ratio: float = 0.5,
                       view_type: str | None = None) -> str:
            """Split a view: a copy of it (or a new view of view_type) opens to its right, left, down or up,
            taking ``ratio`` of the space."""
            if direction not in DIRECTIONS:
                raise CommandError("direction must be right, left, down or up")
            target = wm._tile_ref(tile)
            doc = wm.doc
            workspace = doc.workspace_of(target)
            if workspace is None or workspace == doc.scratchpad() or doc.attr(target, "floating"):
                raise CommandError("Only views in the tiled layout can be split")
            if doc.attr(workspace, "fullscreen") == target:
                doc.set(workspace, fullscreen="")
            new = wm._clone_tile(target, view_type)
            orientation = "h" if direction in ("left", "right") else "v"
            ratio = max(0.05, min(0.95, float(ratio)))
            wm._split_tile(target, new, orientation, direction in ("right", "down"), ratio)
            wm.apply()
            wm.focus_tile(new)
            return new

        def rotate_split(target: str | None = None) -> str:
            """Turn a split between side by side and stacked (target: a view or a split; default the focused view)."""
            doc = wm.doc
            name = target if target and doc.has(target) and doc.type_of(target) == "wm_split" else None
            if name is None:
                name = wm.nearest_split(wm._tile_ref(target))
            if name is None:
                raise CommandError("This view isn't split yet: use the split buttons or drag a corner first")
            orientation = "v" if doc.attr(name, "orientation") == "h" else "h"
            doc.set(name, orientation=orientation)
            wm.apply()
            wm.app.set_status("Views stacked" if orientation == "v" else "Views side by side")
            return orientation

        def swap_split(split: str) -> None:
            """Reverse the order of a split's views (swap left/right or top/bottom)."""
            doc = wm.doc
            if not doc.has(split) or doc.type_of(split) != "wm_split":
                raise CommandError(f"'{split}' is not a split")
            children = doc.children(split)
            sizes = normalized_sizes(doc.attr(split, "sizes"), len(children))
            doc.scene.restack(list(reversed(children)))
            doc.set(split, sizes=list(reversed(sizes)))
            wm.apply()

        def equalize_split(split: str) -> None:
            """Give every view of one split the same share."""
            doc = wm.doc
            if not doc.has(split) or doc.type_of(split) != "wm_split":
                raise CommandError(f"'{split}' is not a split")
            count = len(doc.children(split))
            doc.set(split, sizes=[1.0 / count] * count)
            wm.apply()

        def join_view(tile: str, into: str | None = None) -> None:
            """Close ``tile`` and let its neighbour ``into`` take over the space (Blender's join)."""
            target = wm._tile_ref(tile)
            keep = wm._tile_ref(into) if into else None
            if keep == target:
                raise CommandError("A view can't be joined into itself")
            if not wm.can_close(target):
                raise CommandError("The last canvas view can't be closed")
            doc = wm.doc
            parent = doc.parent(target)
            if keep is not None and parent is not None and doc.type_of(parent) == "wm_split":
                # The neighbour, or the branch holding it, inherits the closed view's share.
                branch: str | None = keep
                while branch is not None and doc.parent(branch) != parent:
                    branch = doc.parent(branch)
                if branch is not None:
                    children = doc.children(parent)
                    sizes = normalized_sizes(doc.attr(parent, "sizes"), len(children))
                    sizes[children.index(branch)] += sizes[children.index(target)]
                    doc.set(parent, sizes=sizes)
            close_view(target)
            if keep is not None:
                wm.focus_tile(keep)

        def open_view(view_type: str, title: str | None = None, state: dict[str, Any] | None = None,
                      placement: str | None = None, name: str | None = None) -> str:
            """Open a new view; placement: split (default), h, v, tabbed, stacked, floating, scratchpad."""
            if view_type not in wm.view_types:
                raise CommandError(f"Unknown view type '{view_type}' (is its plugin enabled?)")
            vt = wm.view_types[view_type]
            placement = placement or wm.rules.get(view_type) or vt.placement
            doc = wm.doc
            scene = doc.scene
            workspace = wm.focused_workspace() or (doc.workspaces()[0] if doc.workspaces() else None)
            if workspace is None:
                workspace = wm.add_workspace_from_preset(WorkspacePreset(1, "Board", None))
            tile = scene.unique_name((name or title or vt.title or view_type).replace(" ", ""))
            scene.create_node("wm_tile", tile, attrs={
                "view_type": view_type, "title": title or vt.title or view_type, "state": state or {},
            })
            if placement == "scratchpad":
                scene.reparent(tile, doc.scratchpad())
                doc.set(tile, shown=1)
            elif placement == "floating":
                scene.reparent(tile, workspace)
                doc.set(tile, floating=1)
            else:
                kind = placement if placement in ("h", "v", "tabbed", "stacked") else wm._auto_split()
                target = wm.focused if wm.focused and doc.workspace_of(wm.focused) == workspace else None
                wm._insert_next_to(target, tile, kind, workspace)
            wm.apply()
            wm.focus_tile(tile)
            return tile

        def close_view(tile: str | None = None) -> None:
            """Close a view (the last canvas can't be closed)."""
            target = wm._tile_ref(tile)
            doc = wm.doc
            if doc.attr(target, "view_type") == "canvas" and len(wm.tiles_of_type("canvas")) <= 1:
                raise CommandError("The last canvas view can't be closed")
            workspace = doc.workspace_of(target)
            if workspace and doc.attr(workspace, "fullscreen") == target:
                doc.set(workspace, fullscreen="")
            wm._detach(target)
            wm._dispose_frame(target)
            doc.scene.delete_node(target)
            if wm.focused == target:
                wm.focused = None
            wm.apply()

        def fullscreen(tile: str | None = None, on: bool | None = None) -> bool:
            """Show the view alone in its window (toggle)."""
            target = wm._tile_ref(tile)
            workspace = wm.doc.workspace_of(target)
            if workspace is None or workspace == wm.doc.scratchpad():
                return False
            current = wm.doc.attr(workspace, "fullscreen") == target
            state = (not current) if on is None else bool(on)
            wm.doc.set(workspace, fullscreen=target if state else "")
            wm.apply()
            wm.focus_tile(target)
            return state

        def floating(tile: str | None = None, on: bool | None = None) -> bool:
            """Float the view above the tiles, or put it back into the layout (toggle)."""
            target = wm._tile_ref(tile)
            doc = wm.doc
            workspace = doc.workspace_of(target)
            if workspace is None or workspace == doc.scratchpad():
                return False
            is_floating = bool(doc.attr(target, "floating"))
            state = (not is_floating) if on is None else bool(on)
            if state == is_floating:
                return state
            if state:
                wm._detach(target)
                doc.scene.reparent(target, workspace)
                doc.set(target, floating=1)
            else:
                doc.set(target, floating=0)
                doc.scene.reparent(target, None)
                window = wm.floating_windows.pop(target, None)
                if window is not None:
                    frame = wm.frames.get(target)
                    if frame is not None:
                        frame.hide()
                        frame.setParent(None)
                    window.hide()
                    window.deleteLater()
                wm._insert_next_to(None, target, wm.next_split, workspace)
            wm.apply()
            return state

        def resize(tile: str | None = None, dimension: str = "width", delta: float = 40) -> None:
            """Grow (positive delta, pixels) or shrink the view's width or height."""
            target = wm._tile_ref(tile)
            orientation = "h" if dimension == "width" else "v"
            doc = wm.doc
            child = target
            parent = doc.parent(child)
            while parent is not None and not (doc.type_of(parent) == "wm_split" and doc.attr(parent, "orientation") == orientation):
                child, parent = parent, doc.parent(parent)
            if parent is None:
                return
            children = doc.children(parent)
            sizes = normalized_sizes(doc.attr(parent, "sizes"), len(children))
            frame = wm.frames.get(target)
            span = 1000.0
            if frame is not None and frame.window() is not None:
                span = float(frame.window().width() if orientation == "h" else frame.window().height()) or 1000.0
            index = children.index(child)
            change = float(delta) / span
            others = [i for i in range(len(sizes)) if i != index]
            if not others:
                return
            new = max(0.05, min(0.95, sizes[index] + change))
            actual = new - sizes[index]
            pool = sum(sizes[i] for i in others) or 1.0
            for i in others:
                sizes[i] = max(0.02, sizes[i] - actual * sizes[i] / pool)
            sizes[index] = new
            doc.set(parent, sizes=normalized_sizes(sizes, len(sizes)))
            wm.apply()

        def equalize() -> None:
            """Give every view in the focused workspace the same share."""
            workspace = wm.focused_workspace()
            if not workspace:
                return
            for name in [workspace] + wm.doc.scene.descendants(workspace):
                if wm.doc.type_of(name) == "wm_split":
                    count = len(wm.doc.children(name))
                    wm.doc.set(name, sizes=[1.0 / count] * count)
            wm.apply()

        def workspace(ref: Any) -> str:
            """Show a workspace (by number, name or title); numbers that don't exist yet are created."""
            name = wm.doc.workspace_by_ref(ref)
            if name is None:
                if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
                    name = wm.add_workspace_from_preset(WorkspacePreset(int(ref), str(ref), None))
                else:
                    raise CommandError(f"No workspace '{ref}'")
            wm.show_workspace(name)
            return name

        def move_to_workspace(ref: Any, tile: str | None = None) -> str:
            """Move the focused view to another workspace (created if needed)."""
            target = wm._tile_ref(tile)
            name = wm.doc.workspace_by_ref(ref)
            if name is None:
                if isinstance(ref, int) or (isinstance(ref, str) and str(ref).isdigit()):
                    name = wm.add_workspace_from_preset(WorkspacePreset(int(ref), str(ref), None))
                else:
                    raise CommandError(f"No workspace '{ref}'")
            if wm.doc.workspace_of(target) == name:
                return name
            wm.doc.set(target, floating=0)
            wm._detach(target)
            wm._insert_next_to(None, target, "h", name)
            wm.apply()
            return name

        def tear_off(tile: str | None = None) -> str:
            """Move a view into a window of its own (a new output)."""
            target = wm._tile_ref(tile)
            doc = wm.doc
            numbers = [int(doc.attr(w, "number", 0) or 0) for w in doc.workspaces()]
            number = max(numbers + [0]) + 1
            title = str(doc.attr(target, "title") or target)
            ws = wm.add_workspace_from_preset(WorkspacePreset(number, title, None))
            doc.set(target, floating=0)
            wm._detach(target)
            doc.scene.reparent(target, ws)
            output = doc.scene.unique_name("Window")
            geometry = None
            primary = wm.primary_window()
            if primary is not None:
                rect = primary.geometry()
                geometry = [rect.x() + 60, rect.y() + 60, max(400, rect.width() // 2), max(300, rect.height() // 2)]
            doc.scene.create_node("wm_output", output, attrs={"workspace": ws, "geometry": geometry})
            wm.apply()
            wm.focus_tile(target)
            return output

        def scratchpad_show(name: str | None = None) -> bool:
            """Pop a scratchpad view (the console by default) up or away."""
            doc = wm.doc
            scratch = doc.scratchpad()
            tiles = [t for t in doc.children(scratch) if doc.type_of(t) == "wm_tile"]
            if not tiles:
                raise CommandError("The scratchpad is empty")
            target = None
            if name:
                for tile in tiles:
                    if name in (tile, doc.attr(tile, "title"), doc.attr(tile, "view_type"), doc.attr(tile, "mark")):
                        target = tile
                        break
                if target is None:
                    raise CommandError(f"No scratchpad view '{name}'")
            else:
                target = tiles[0]
            shown = not bool(doc.attr(target, "shown"))
            doc.set(target, shown=int(shown))
            wm.apply()
            if shown:
                wm.focus_tile(target)
                frame = wm.frames.get(target)
                if frame is not None:
                    frame.body.setFocus()
            return shown

        def scratchpad_move(tile: str | None = None) -> None:
            """Send a view to the scratchpad."""
            target = wm._tile_ref(tile)
            doc = wm.doc
            if doc.attr(target, "view_type") == "canvas" and len(wm.tiles_of_type("canvas")) <= 1:
                raise CommandError("The last canvas view can't go to the scratchpad")
            doc.set(target, floating=0)
            wm._detach(target)
            doc.scene.reparent(target, doc.scratchpad())
            doc.set(target, shown=0)
            wm.apply()

        def mark(name: str, tile: str | None = None) -> None:
            """Name a view so focus(mark=…) can jump to it."""
            target = wm._tile_ref(tile)
            for other in wm.doc.tiles():
                if wm.doc.attr(other, "mark") == name:
                    wm.doc.set(other, mark="")
            wm.doc.set(target, mark=name)

        def save_layout(name: str) -> None:
            """Remember the current layout under a name."""
            layouts = wm.app.settings.json_value(NAMED_LAYOUTS_KEY, {}) or {}
            layouts[name] = wm.serialize()
            wm.app.settings.set_json(NAMED_LAYOUTS_KEY, layouts)

        def load_layout(name: str) -> None:
            """Restore a layout saved with save_layout()."""
            layouts = wm.app.settings.json_value(NAMED_LAYOUTS_KEY, {}) or {}
            if name not in layouts:
                raise CommandError(f"No saved layout '{name}'")
            wm.restore(layouts[name])
            wm.apply()

        def list_layouts() -> list[str]:
            """Names of saved window layouts."""
            return sorted((wm.app.settings.json_value(NAMED_LAYOUTS_KEY, {}) or {}).keys())

        def reset_layout() -> None:
            """Back to the built-in workspaces."""
            wm.build_default()
            wm.apply()

        def tree() -> Any:
            """The layout as nested plain data."""
            wm.collect_states()
            return wm.doc.tree()

        def ls(type: str | None = None) -> list[str]:  # noqa: A002
            """Layout nodes (outputs, workspaces, containers, tiles)."""
            return [n.name for n in wm.doc.scene.iter_nodes() if type is None or n.node_type == type]

        def get_attr(target: str, attr: str | None = None) -> Any:
            """An attribute of a layout node (tile, split, workspace, window), or all of them."""
            if attr is None:
                target, attr = target.split(".", 1)
            if attr == "state":
                wm.collect_states()
            return wm.doc.scene.get_attr(target, attr)

        def set_attr(target: str, *values: Any) -> None:
            """Edit a layout node attribute, e.g. set_attr("Main", "header", 0)."""
            if "." in target and not wm.doc.has(target):
                target, _dot, attr = target.rpartition(".")
                value = values[0]
            else:
                attr, value = values[0], values[1]
            wm.doc.scene.set_attr(target, attr, value)
            if attr == "state":
                frame = wm.frames.get(target)
                if frame is not None:
                    wm._dispose_frame(target)
            wm.apply()

        def set_view_type(view_type: str, tile: str | None = None) -> None:
            """Change what a tile shows."""
            target = wm._tile_ref(tile)
            if view_type not in wm.view_types:
                raise CommandError(f"Unknown view type '{view_type}'")
            wm._dispose_frame(target)
            wm.doc.set(target, view_type=view_type, state={}, title=wm.view_types[view_type].title)
            wm.apply()

        def set_rule(view_type: str, placement: str | None) -> None:
            """Where new views of a type open: split, h, v, tabbed, stacked, floating, scratchpad (None = default)."""
            if placement:
                wm.rules[view_type] = placement
            else:
                wm.rules.pop(view_type, None)
            wm.app.settings.set_json("wm/rules", wm.rules)

        def set_placement(mode: str) -> None:
            """manual (i3: split the focused view the chosen way) or auto (alternate, spiral)."""
            if mode not in ("manual", "auto"):
                raise CommandError("mode must be 'manual' or 'auto'")
            wm.placement_mode = mode
            wm.app.settings.setValue("wm/placement", mode)

        def set_focus_follows_mouse(on: bool) -> None:
            """Whether hovering a view focuses it (default on); remembered."""
            wm.focus_follows_mouse = bool(on)
            wm.app.settings.setValue("wm/focus_follows_mouse", bool(on))

        def rename_view(title: str, tile: str | None = None) -> str:
            """Give a view a new title (shown in its header; scripts can refer to it by title)."""
            target = wm._tile_ref(tile)
            title = str(title).strip()
            if not title:
                raise CommandError("A view needs a name")
            if any(wm.doc.attr(t, "title") == title for t in wm.doc.tiles() if t != target):
                raise CommandError(f"Another view is already called '{title}'")
            wm.doc.set(target, title=title)
            window = wm.floating_windows.get(target)
            if window is not None:
                window.setWindowTitle(title)
            wm.apply()
            return target

        def rename_view_dialog(tile: str | None = None) -> None:
            wm.rename_view_prompt(tile)

        def workspace_ref(ref: Any) -> str:
            name = wm.focused_workspace() if ref is None else wm.doc.workspace_by_ref(ref)
            if name is None:
                raise CommandError(f"No workspace '{ref}'" if ref is not None else "No workspace")
            return name

        def rename_workspace(ref: Any, title: str) -> None:
            """Give a workspace a new title (saved with the scene)."""
            name = workspace_ref(ref)
            wm.doc.set(name, title=str(title))
            wm.app.session.mark_unsaved()
            wm.apply()

        def new_workspace(title: str | None = None, template: str | None = None, show: bool = True) -> str:
            """Add a workspace (empty canvas, or a copy of a template); it gets the next free number."""
            spec = wm.template(template) if template else BLANK_WORKSPACE
            name = wm.create_workspace(spec, title=title or (template if template else None))
            wm.app.session.mark_unsaved()
            if show:
                wm.show_workspace(name)
            else:
                wm.apply()
            return name

        def delete_workspace(ref: Any = None) -> None:
            """Remove a workspace and its views (the last one can't be removed)."""
            name = workspace_ref(ref)
            if len(wm.doc.workspaces()) <= 1:
                raise CommandError("The last workspace can't be removed")
            title = wm.doc.attr(name, "title") or name
            wm._delete_workspace_node(name)
            wm.app.session.mark_unsaved()
            wm.apply()
            wm.app.set_status(f"Removed workspace {title}")

        def save_workspace_template(name: str | None = None, ref: Any = None) -> str:
            """Save a workspace's layout, views and window style as a template (for every scene)."""
            workspace = workspace_ref(ref)
            wm.collect_states()
            spec = wm.workspace_spec(workspace, template=True)
            name = str(name or spec["title"]).strip()
            if not name:
                raise CommandError("A template needs a name")
            spec.pop("number", None)
            spec["title"] = name
            templates = wm.user_templates()
            templates[name] = spec
            wm._set_user_templates(templates)
            wm.app.set_status(f"Saved workspace template '{name}'")
            return name

        def apply_workspace_template(template: str, ref: Any = None) -> str:
            """Replace a workspace's layout and views with a template's (keeps its number and title)."""
            name = workspace_ref(ref)
            wm.replace_workspace(name, wm.template(template))
            wm.app.session.mark_unsaved()
            wm.apply()
            return name

        def delete_workspace_template(name: str) -> None:
            """Forget one of your workspace templates (built-in ones stay)."""
            templates = wm.user_templates()
            if name not in templates:
                raise CommandError(f"'{name}' is not one of your templates")
            del templates[name]
            wm._set_user_templates(templates)

        def workspace_templates() -> list[dict[str, Any]]:
            """Every workspace template: name and whether it is built in."""
            user = wm.user_templates()
            return [{"name": name, "builtin": name not in user} for name in wm.templates()]

        def output_style(frameless: bool | None = None, on_top: bool | None = None, opacity: float | None = None,
                         output: str | None = None) -> dict[str, Any]:
            """Window flags of the focused output (frameless, always on top, opacity)."""
            target = output or wm.focused_output()
            if target is None:
                raise CommandError("No output")
            values: dict[str, Any] = {}
            if frameless is not None:
                values["frameless"] = int(bool(frameless))
            if on_top is not None:
                values["on_top"] = int(bool(on_top))
            if opacity is not None:
                values["opacity"] = float(opacity)
            if values:
                wm.doc.set(target, **values)
                wm.apply()
            return {k: wm.doc.attr(target, k) for k in ("frameless", "on_top", "opacity")}

        def new_view_menu() -> None:
            """Pick a view type to open next to the focused view."""
            wm.show_new_view_menu()

        def rename_workspace_dialog() -> None:
            """Ask for a new name for the current workspace."""
            wm.rename_workspace_prompt()

        def delete_workspace_dialog() -> None:
            """Delete the current workspace (asks first)."""
            wm.delete_workspace_prompt()

        def save_template_dialog() -> None:
            """Save the current workspace as a template (asks for a name)."""
            wm.save_template_prompt()

        def mode(name: str) -> None:
            """Enter a keymap mode (window, resize)."""
            wm.app.keymap.enter_mode(name)

        for name, fn in (
            ("focus", focus), ("move", move), ("split", split), ("layout", layout), ("open_view", open_view),
            ("split_view", split_view), ("rotate_split", rotate_split), ("swap_split", swap_split),
            ("equalize_split", equalize_split), ("join_view", join_view),
            ("close_view", close_view), ("fullscreen", fullscreen), ("floating", floating), ("resize", resize),
            ("equalize", equalize), ("workspace", workspace), ("move_to_workspace", move_to_workspace),
            ("tear_off", tear_off), ("scratchpad_show", scratchpad_show), ("scratchpad_move", scratchpad_move),
            ("mark", mark), ("save_layout", save_layout), ("load_layout", load_layout),
            ("reset_layout", reset_layout), ("set_attr", set_attr),
            ("set_view_type", set_view_type), ("set_rule", set_rule), ("set_placement", set_placement),
            ("set_focus_follows_mouse", set_focus_follows_mouse), ("rename_workspace", rename_workspace),
            ("rename_view", rename_view),
            ("output_style", output_style), ("new_view_menu", new_view_menu), ("mode", mode),
            ("new_workspace", new_workspace), ("delete_workspace", delete_workspace),
            ("save_workspace_template", save_workspace_template),
            ("apply_workspace_template", apply_workspace_template),
            ("delete_workspace_template", delete_workspace_template),
        ):
            reg(f"wm.{name}", fn, undoable=False)
        for name, fn in (("list_layouts", list_layouts), ("tree", tree), ("ls", ls), ("get_attr", get_attr),
                         ("workspace_templates", workspace_templates),
                         ("rename_workspace_dialog", rename_workspace_dialog),
                         ("rename_view_dialog", rename_view_dialog),
                         ("delete_workspace_dialog", delete_workspace_dialog),
                         ("save_workspace_template_dialog", save_template_dialog)):
            reg(f"wm.{name}", fn, undoable=False, echo=False)

    # ---- Helpers used by commands --------------------------------------------------------------

    def _auto_split(self) -> str:
        if self.placement_mode == "auto" and self.focused and self.focused in self.frames:
            frame = self.frames[self.focused]
            # Spiral: split along the longer side.
            return "h" if frame.width() >= frame.height() else "v"
        return self.next_split

    def show_workspace(self, workspace: str, output: str | None = None) -> None:
        current_output = self.doc.output_showing(workspace)
        if current_output is not None:
            window = self.windows.get(current_output)
            if window is not None:
                window.raise_()
                window.activateWindow()
        else:
            target = output or self.focused_output() or self.doc.primary_output()
            if target is None:
                return
            self.doc.set(target, workspace=workspace)
        self.apply()
        root = self.doc.tiling_root(workspace)
        tiles = self.doc.tiles(root) if root else []
        canvas = [t for t in tiles if self.doc.attr(t, "view_type") == "canvas"]
        if canvas or tiles:
            self.focus_tile((canvas or tiles)[0])
        self.app.set_status(f"Workspace {self.doc.attr(workspace, 'title') or workspace}")

    # ---- Workspace menus (bar buttons, Window → Workspaces) ------------------------------------------

    def _workspace_title(self, workspace: str) -> str:
        return str(self.doc.attr(workspace, "title") or workspace)

    def fill_workspaces_menu(self, menu: QMenu) -> None:
        """Every workspace (click to show), then what can be done with the current one."""
        current = self.focused_workspace()
        for workspace in self.doc.workspaces():
            number = int(self.doc.attr(workspace, "number", 0) or 0)
            title = self._workspace_title(workspace)
            action = menu.addAction(f"{number}: {title}" if number else title,
                                    lambda w=workspace: self.run("wm.workspace", w))
            action.setCheckable(True)
            action.setChecked(workspace == current)
        menu.addSeparator()
        if current:
            self.fill_workspace_actions(menu, current)

    def fill_workspace_actions(self, menu: QMenu, workspace: str) -> None:
        self.fill_new_workspace_menu(menu.addMenu(icon("plus"), "New Workspace"))
        title = self._workspace_title(workspace)
        menu.addAction(f"Rename “{title}”…", lambda: self.rename_workspace_prompt(workspace))
        apply = menu.addMenu(f"Apply Template to “{title}”")
        for name in self.templates():
            apply.addAction(name, lambda n=name: self.run("wm.apply_workspace_template", n, workspace))
        menu.addAction(f"Save “{title}” as Template…", lambda: self.save_template_prompt(workspace))
        user = self.user_templates()
        if user:
            forget = menu.addMenu("Delete Template")
            for name in user:
                forget.addAction(name, lambda n=name: self.delete_template_prompt(n))
        menu.addSeparator()
        delete = menu.addAction(f"Delete “{title}”…", lambda: self.delete_workspace_prompt(workspace))
        delete.setEnabled(len(self.doc.workspaces()) > 1)

    def fill_new_workspace_menu(self, menu: QMenu) -> None:
        menu.addAction("Blank (one canvas)", lambda: self.run("wm.new_workspace"))
        menu.addSection("From template")
        user = self.user_templates()
        for name in self.templates():
            menu.addAction(f"{name}  (yours)" if name in user else name,
                           lambda n=name: self.run("wm.new_workspace", None, n))

    def show_workspace_menu(self, workspace: str, anchor: QWidget) -> None:
        menu = QMenu(anchor)
        self.fill_workspace_actions(menu, workspace)
        menu.exec(anchor.mapToGlobal(QPoint(0, 0)) - QPoint(0, menu.sizeHint().height()))

    def show_new_workspace_menu(self, anchor: QWidget) -> None:
        menu = QMenu(anchor)
        self.fill_new_workspace_menu(menu)
        menu.exec(anchor.mapToGlobal(QPoint(0, 0)) - QPoint(0, menu.sizeHint().height()))

    def _prompt(self, message: str, title: str, text: str = "") -> str | None:
        value = self.app.run_command("ui.prompt_dialog", message, title, text)
        return value.strip() if isinstance(value, str) and value.strip() else None

    def rename_workspace_prompt(self, workspace: str | None = None) -> None:
        workspace = workspace or self.focused_workspace()
        if workspace and self.doc.has(workspace):
            title = self._prompt("Workspace name:", "Rename Workspace", self._workspace_title(workspace))
            if title:
                self.run("wm.rename_workspace", workspace, title)

    def _rename_tab(self, children: list[str], index: int) -> None:
        if 0 <= index < len(children) and self.doc.type_of(children[index]) == "wm_tile":
            self.rename_view_prompt(children[index])

    def rename_view_prompt(self, tile: str | None = None) -> None:
        """Rename a view: inline in its header when the header shows, else in a dialog."""
        target = self._tile_ref(tile)
        frame = self.frames.get(target)
        if frame is not None and frame.isVisible() and not frame.header.isHidden():
            frame.header.edit_title(self.title(target))
            return
        title = self._prompt("View name:", "Rename View", self.title(target))
        if title:
            self.run("wm.rename_view", title, target)

    def save_template_prompt(self, workspace: str | None = None) -> None:
        workspace = workspace or self.focused_workspace()
        if not workspace or not self.doc.has(workspace):
            return
        name = self._prompt("Template name (available in every scene):", "Save Workspace as Template",
                            self._workspace_title(workspace))
        if not name:
            return
        if name in self.user_templates():
            answer = QMessageBox.question(self.app.main_window(), "Save Workspace as Template",
                                          f"Replace your template “{name}”?")
            if answer != QMessageBox.Yes:
                return
        self.run("wm.save_workspace_template", name, workspace)

    def delete_workspace_prompt(self, workspace: str | None = None) -> None:
        workspace = workspace or self.focused_workspace()
        if not workspace or not self.doc.has(workspace):
            return
        answer = QMessageBox.question(self.app.main_window(), "Delete Workspace",
                                      f"Delete the workspace “{self._workspace_title(workspace)}” and its views?")
        if answer == QMessageBox.Yes:
            self.run("wm.delete_workspace", workspace)

    def delete_template_prompt(self, name: str) -> None:
        answer = QMessageBox.question(self.app.main_window(), "Delete Template", f"Delete your template “{name}”?")
        if answer == QMessageBox.Yes:
            self.run("wm.delete_workspace_template", name)

    def show_tile_menu(self, tile: str, anchor: QWidget) -> None:
        menu = QMenu(anchor)
        if self.is_tiled(tile):
            menu.addAction(icon("split_right"), "Split right", lambda: self.run("wm.split_view", "right", tile))
            menu.addAction(icon("split_down"), "Split down", lambda: self.run("wm.split_view", "down", tile))
            others = menu.addMenu("Split right with")
            for vt in sorted(self.view_types.values(), key=lambda v: v.title):
                others.addAction(vt.title, lambda v=vt.id: self.run("wm.split_view", "right", tile, view_type=v))
            split = self.nearest_split(tile)
            if split is not None:
                stacked = self.doc.attr(split, "orientation") == "v"
                menu.addAction(icon("rotate_split"), "Place side by side" if stacked else "Stack vertically",
                               lambda: self.run("wm.rotate_split", split))
            menu.addSeparator()
        types = menu.addMenu("Change view to")
        for vt in sorted(self.view_types.values(), key=lambda v: v.title):
            types.addAction(vt.title, lambda v=vt.id: self.run("wm.set_view_type", v, tile))
        menu.addSeparator()
        menu.addAction("Fullscreen", lambda: self.run("wm.fullscreen", tile))
        floating = bool(self.doc.attr(tile, "floating"))
        menu.addAction("Put back in layout" if floating else "Float", lambda: self.run("wm.floating", tile))
        menu.addAction("Tear off into a window", lambda: self.run("wm.tear_off", tile))
        menu.addAction("Send to scratchpad", lambda: self.run("wm.scratchpad_move", tile))
        menu.addAction("Rename…", lambda: self.rename_view_prompt(tile))
        menu.addAction("Set mark…", lambda: self._ask_mark(tile))
        header = bool(self.doc.attr(tile, "header", 1))
        menu.addAction("Hide header" if header else "Show header",
                       lambda: self.run("wm.set_attr", tile, "header", 0 if header else 1))
        menu.addSeparator()
        menu.addAction("Close", lambda: self.run("wm.close_view", tile))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def show_split_menu(self, split: str, global_pos: QPoint) -> None:
        """Right-click on a divider."""
        if not self.doc.has(split):
            return
        stacked = self.doc.attr(split, "orientation") == "v"
        menu = QMenu(self.primary_window())
        menu.addAction(icon("split_right" if stacked else "split_down"),
                       "Place side by side" if stacked else "Stack vertically",
                       lambda: self.run("wm.rotate_split", split))
        menu.addAction("Swap top and bottom" if stacked else "Swap left and right",
                       lambda: self.run("wm.swap_split", split))
        menu.addAction("Even out sizes", lambda: self.run("wm.equalize_split", split))
        menu.exec(global_pos)

    def _ask_mark(self, tile: str) -> None:
        text, ok = QInputDialog.getText(self.primary_window(), "Mark view", "Mark name:",
                                        text=str(self.doc.attr(tile, "mark") or ""))
        if ok and text.strip():
            self.run("wm.mark", text.strip(), tile)

    def show_new_view_menu(self) -> None:
        menu = QMenu(self.primary_window())
        menu.addSection("Open a view")
        for vt in sorted(self.view_types.values(), key=lambda v: v.title):
            menu.addAction(vt.title, lambda v=vt.id: self.run("wm.open_view", v))
        menu.exec(QCursor.pos())

    def on_output_close(self, output: str) -> bool:
        if self.doc.attr(output, "primary"):
            return self.app.request_close()
        if self.doc.has(output):
            self.doc.scene.delete_node(output)
            QTimer.singleShot(0, self.apply)
        return True

    def close_all(self) -> None:
        self.begin_shutdown()
        for window in list(self.floating_windows.values()):
            window.hide()
            window.deleteLater()
        self.floating_windows.clear()
        for tile in list(self.frames):
            self._dispose_frame(tile)
        for window in list(self.windows.values()):
            window.closing_allowed = True
            window.close()
            window.deleteLater()
        self.windows.clear()


