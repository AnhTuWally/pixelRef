"""Keeps the canvas items in step with the document.

The node graph is the single source of truth. After every command (and on
undo/redo/open) :meth:`CanvasDocument.refresh` brings the QGraphicsScene in
line with the scene graph, recreating only items whose node data changed.
Items are built by the node-type plugins' factories (``ui.node_ui(...)``);
nodes with no factory are drawn as placeholders, and nodes without canvas
presence (cameras, presets) get no item.

During a drag, tools move the items directly (cheap live feedback) and the
result is written back to the document with one command when the drag ends
(:meth:`end_interaction`).
"""

from __future__ import annotations

import logging
import math
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterator

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QTransform
from PySide6.QtWidgets import QGraphicsItem

from ...core import perf
from ...core.extensions import Disposable
from ...core.values import differs
from .items import ContainerItem, NodeItemMixin, PlaceholderItem
from .view import CanvasScene

if TYPE_CHECKING:
    from ..app import App
    from .view import CanvasView

logger = logging.getLogger(__name__)


@dataclass
class NodeTypeUI:
    """How a node type looks and behaves in the Qt UI (registered by its plugin)."""

    type_name: str
    factory: Callable[["CanvasDocument", dict[str, Any]], Any] | None = None
    label: str = ""  # friendly name, e.g. "Text note"
    icon: str = ""  # icon name (ui.icons) for the inspector header
    badge: tuple[str, str] | None = None  # outliner badge (colour, letter)
    describe: Callable[[str, dict[str, Any]], list[str]] | None = None  # extra inspector header facts
    post_sync: Callable[[Any, dict[str, Any]], None] | None = None  # after every refresh
    double_click: Callable[["CanvasView", Any], bool] | None = None
    plugin: str | None = None


# Attributes an existing item shows by itself (its transform, opacity, visibility, lock):
# a change of only these updates the item instead of building a new one.
PLACEMENT_ATTRS = frozenset({"translate", "rotate", "scale", "pivot", "opacity", "visibility", "locked"})
_MISSING = object()


class CanvasDocument:
    COALESCE_MS = 600

    def __init__(self, app: "App") -> None:
        self.app = app
        self.session = app.session
        self.scene = CanvasScene(None)
        self.items: dict[str, Any] = {}
        self.nodes: dict[str, dict[str, Any]] = {}
        self.node_uis: dict[str, NodeTypeUI] = {}
        # canvas.item_hooks contributions: hook(item, node) after every item is (re)built
        self.rendering_final = 0  # > 0 while render_items(final=True) runs (see there)
        self.item_hooks: list[Callable[[Any, dict[str, Any]], None]] = []
        self._signatures: dict[str, tuple[Any, ...]] = {}
        # Scene revisions the items were last synced at (None: never): a refresh after
        # attribute edits only looks at the nodes changed since (SceneGraph.node_revisions).
        self._synced_revision: int | None = None
        self._synced_structure = -1
        self.syncing = False
        self._syncing_selection = False
        self._dirty: set[str] = set()
        self._guard_depth = 0
        self._batch_depth = 0
        self._pending_refresh = False
        self._force_full = False
        self._interaction = False
        self._coalesce_label: str | None = None
        self._coalesce_command: str | None = None
        self._coalesce_timer = QTimer()
        self._coalesce_timer.setSingleShot(True)
        self._coalesce_timer.timeout.connect(self.end_coalesced)
        self._refresh_timer = QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._flush_refresh)
        self._flush_handlers: list[Callable[[], None]] = []
        self._interaction_handlers: list[Callable[[str, list[str]], None]] = []
        self._disposables = [
            self.session.add_geometry_provider(self._geometry),
            self.session.selection.subscribe(lambda _names: self._on_selection_model_changed()),
            self.session.events.on("document.changed", lambda _e: self.refresh()),
            self.session.events.on("document.replaced", lambda _e: self._on_replaced()),
            self.session.commands.on_before(self._before_command),
        ]
        self.scene.selectionChanged.connect(self._on_scene_selection_changed)

    def shutdown(self) -> None:
        self._coalesce_timer.stop()
        self._refresh_timer.stop()
        for disposable in self._disposables:
            disposable.dispose()
        for item in self.items.values():
            stop = getattr(item, "stop_movie", None)
            if callable(stop):
                stop()

    # ---- Node type UIs --------------------------------------------------------------------

    def register_node_ui(self, ui: NodeTypeUI) -> Disposable:
        if ui.type_name in self.node_uis:
            raise ValueError(f"UI for node type '{ui.type_name}' is already registered")
        self.node_uis[ui.type_name] = ui
        self.request_full_refresh()

        def remove() -> None:
            if self.node_uis.get(ui.type_name) is ui:
                del self.node_uis[ui.type_name]
                self.request_full_refresh()

        return Disposable(remove)

    def node_ui(self, type_name: str) -> NodeTypeUI | None:
        return self.node_uis.get(type_name)

    def friendly_type(self, type_name: str) -> str:
        ui = self.node_uis.get(type_name)
        if ui is not None and ui.label:
            return ui.label
        return self.session.types.label(type_name) if type_name in self.session.types else type_name

    def add_flush_handler(self, handler: Callable[[], None]) -> Disposable:
        """Called before any command runs (e.g. commit a text edit in progress)."""
        self._flush_handlers.append(handler)
        return Disposable(lambda: handler in self._flush_handlers and self._flush_handlers.remove(handler))

    def add_interaction_handler(self, handler: Callable[[str, list[str]], None]) -> Disposable:
        """``handler(label, names)``: a canvas edit (drag, nudge, resize) was committed to the
        document, inside its still-open undo step. ``names`` are the nodes it changed (e.g. backdrops
        settle what a move dropped on them, so the move and the membership change undo together)."""
        self._interaction_handlers.append(handler)
        return Disposable(lambda: handler in self._interaction_handlers and self._interaction_handlers.remove(handler))

    def _commit_interaction(self, label: str) -> None:
        committed = self.commit_dirty()
        if committed and label != "Cancelled":
            for handler in list(self._interaction_handlers):
                handler(label, committed)

    # ---- Refresh ---------------------------------------------------------------------------

    @contextmanager
    def event_guard(self) -> Iterator[None]:
        """Defer refreshes while Qt is delivering an event to items that might be replaced."""
        self._guard_depth += 1
        try:
            yield
        finally:
            self._guard_depth -= 1
            if self._guard_depth == 0 and self._batch_depth == 0 and self._pending_refresh:
                self._pending_refresh = False
                self.refresh()

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Run several commands with a single refresh at the end."""
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1
            if self._batch_depth == 0 and self._guard_depth == 0 and self._pending_refresh:
                self._pending_refresh = False
                self.refresh()

    def request_full_refresh(self) -> None:
        self._force_full = True
        self.refresh()

    def _on_replaced(self) -> None:
        self._dirty.clear()
        self._interaction = False
        self._coalesce_label = None
        self._coalesce_timer.stop()
        self._refresh_timer.stop()
        self.refresh()

    def _flush_refresh(self) -> None:
        if self._guard_depth > 0 or self._batch_depth > 0:
            self._pending_refresh = True
            return
        self._refresh_timer.stop()
        self._do_refresh(force=self._force_full)
        self._force_full = False
        if self._pending_refresh:
            self._pending_refresh = False
            self.refresh()

    def refresh(self, force: bool = False) -> None:
        """Sync the canvas with the document. Deferred while Qt is delivering an
        event to canvas items unless ``force`` (the caller holds no item references)."""
        if not force and (self._guard_depth > 0 or self._batch_depth > 0):
            self._pending_refresh = True
            return
        if force:
            self._force_full = True
            self._refresh_timer.stop()
            self._pending_refresh = False
            self._do_refresh(force=True)
            return
        if self._refresh_timer.isActive():
            return
        self._refresh_timer.start(0)

    def _do_refresh(self, force: bool = False) -> None:
        scene = self.session.scene
        incremental = (not force and not self._force_full and self.items and self._synced_revision is not None
                       and scene.structure_revision == self._synced_structure)
        with perf.span("canvas.snapshot"):
            if incremental:
                since = self._synced_revision
                touched = [name for name, revision in scene.node_revisions.items() if revision > since]
                for name in touched:
                    node = scene.nodes.get(name)
                    if node is not None:
                        self.nodes[name] = node.to_dict()
            else:
                snapshot = scene.to_dict()["nodes"]
                self.nodes = {node["name"]: node for node in snapshot}
        self.syncing = True
        try:
            with perf.span("canvas.update_items"):
                if incremental:
                    self._update_nodes(touched)
                else:
                    self._update_canvas(snapshot)
        finally:
            self.syncing = False
        self._synced_revision = scene.revision
        self._synced_structure = scene.structure_revision
        self._dirty.clear()
        self._apply_selection_to_items()
        self.app.on_canvas_refreshed()

    @staticmethod
    def _signature(node: dict[str, Any]) -> tuple[Any, ...]:
        return node["type"], node.get("parent"), node.get("attrs"), node.get("dynamic")

    def _update_canvas(self, nodes: list[dict[str, Any]]) -> None:
        """Update items whose node moved (or changed opacity, visibility, lock), recreate
        those whose data changed otherwise; rebuild everything when most things changed
        (opening a file, new scene) or a node type's UI changed."""
        signatures = {node["name"]: self._signature(node) for node in nodes}
        changed = [n["name"] for n in nodes if self._signatures.get(n["name"]) != signatures[n["name"]]]
        removed = [name for name in self.items if name not in signatures]
        placed = [name for name in changed if self._placement_only(name, signatures[name])]
        recreate = set(changed).difference(placed)
        if self._force_full or not self.items or len(recreate) + len(removed) > max(8, len(nodes) * 0.6):
            self._force_full = False
            with perf.span("canvas.rebuild_all"):
                self._rebuild(nodes)
        else:
            self._place(placed)
            if recreate or removed:
                with perf.span("canvas.patch"):
                    self._patch(nodes, recreate, removed)
            else:
                self._finish(nodes, fresh=set())
        self._signatures = signatures

    def _update_nodes(self, names: list[str]) -> None:
        """Like _update_canvas after attribute edits only (no node added, removed, renamed,
        reparented or reordered): just ``names`` can have changed."""
        signatures = {name: self._signature(self.nodes[name]) for name in names if name in self.nodes}
        changed = [name for name, signature in signatures.items() if self._signatures.get(name) != signature]
        placed = [name for name in changed if self._placement_only(name, signatures[name])]
        recreate = set(changed).difference(placed)
        self._place(placed)
        nodes = list(self.nodes.values())  # parents first, as the scene lists them
        if recreate:
            with perf.span("canvas.patch"):
                self._patch(nodes, recreate, [])
        else:
            self._finish(nodes, fresh=set())
        self._signatures.update(signatures)

    def _placement_only(self, name: str, signature: tuple[Any, ...]) -> bool:
        """Whether the node's item can stay: only PLACEMENT_ATTRS changed."""
        old = self._signatures.get(name)
        if old is None or name not in self.items or old[0] != signature[0] or old[1] != signature[1] \
                or old[3] != signature[3]:
            return False
        before, after = old[2] or {}, signature[2] or {}
        return all(key in PLACEMENT_ATTRS for key in before.keys() | after.keys()
                   if before.get(key, _MISSING) != after.get(key, _MISSING))

    def _place(self, names: list[str]) -> None:
        if not names:
            return
        with perf.span("canvas.place"):
            for name in names:
                item = self.items[name]
                attrs = self.nodes[name].get("attrs", {})
                self.apply_node_transform(item, attrs)
                item.setOpacity(float(attrs.get("opacity", 1.0)))
                # Visibility and lock are inherited from parents: _finish sets them.

    def _rebuild(self, nodes: list[dict[str, Any]]) -> None:
        self._stop_movies(self.items.values())
        for item in list(self.items.values()):
            if item.scene() is not None and item.parentItem() is None:
                self.scene.removeItem(item)
        self.items.clear()
        for node in nodes:
            item = self._create_item(node)
            if item is None:
                continue
            self.items[node["name"]] = item
            parent_item = self.items.get(node.get("parent") or "")
            if parent_item is not None:
                item.setParentItem(parent_item)
            else:
                self.scene.addItem(item)
        self._finish(nodes, fresh=None)
        self.app.on_canvas_rebuilt()

    def _patch(self, nodes: list[dict[str, Any]], changed: set[str], removed: list[str]) -> None:
        self._stop_movies(self.items[name] for name in list(changed) + removed if name in self.items)
        stale: list[Any] = []
        for node in nodes:  # parents come before children
            name = node["name"]
            if name in changed:
                old = self.items.pop(name, None)
                item = self._create_item(node)
                if old is not None:
                    for child in old.childItems():
                        if isinstance(child, NodeItemMixin):
                            child.setParentItem(item if item is not None else None)
                    stale.append(old)
                if item is None:
                    continue
                self.items[name] = item
            item = self.items.get(name)
            if item is None:
                continue
            parent_item = self.items.get(node.get("parent") or "")
            if item.parentItem() is not parent_item:
                item.setParentItem(parent_item)
            if item.scene() is None:
                self.scene.addItem(item)
        for name in removed:
            stale.append(self.items.pop(name, None))
        # Only where the stale items were changed: the views' tile caches keep the rest.
        self.scene.remove_items([item for item in stale if item is not None and item.scene() is not None])
        self._finish(nodes, fresh=changed)

    @staticmethod
    def _stop_movies(items: Any) -> None:
        for item in items:
            stop = getattr(item, "stop_movie", None)
            if callable(stop):
                stop()

    def _finish(self, nodes: list[dict[str, Any]], fresh: set[str] | None) -> None:
        """Inherited lock state, visibility and stacking order (one pass)."""
        order: dict[str, int] = {}
        for index, name in enumerate(self.session.scene.roots):
            order[name] = index
        for node in nodes:
            for index, child in enumerate(node.get("children", [])):
                order[child] = index

        # Memoized and iterative: deep nesting must stay linear and can't hit the recursion limit.
        locked: dict[str, bool] = {}
        visible: dict[str, bool] = {}

        def inherit(name: str) -> None:
            chain = []
            current = name
            while current and current not in locked:
                node = self.nodes.get(current)
                if node is None:
                    break
                chain.append((current, node))
                current = node.get("parent")
            parent_locked = locked.get(current, False) if current else False
            parent_visible = visible.get(current, True) if current else True
            for chain_name, node in reversed(chain):
                parent_locked = locked[chain_name] = bool(node["attrs"].get("locked", 0)) or parent_locked
                parent_visible = visible[chain_name] = bool(node["attrs"].get("visibility", 1)) and parent_visible

        for name, item in self.items.items():
            if name not in locked:
                inherit(name)
            effective = locked.get(name, False)
            if fresh is None or name in fresh or item.locked != effective:
                item.set_locked(effective)
            expected_visible = visible.get(name, True)
            if fresh is None or name in fresh or item.isVisible() != expected_visible:
                item.setVisible(expected_visible)
            z = float(order.get(name, 0))
            if item.zValue() != z:
                item.setZValue(z)
        self.update_child_input()
        for name, item in self.items.items():
            node = self.nodes.get(name)
            ui = self.node_uis.get(item.node_type)
            if ui is not None and ui.post_sync is not None and node is not None:
                ui.post_sync(item, node)

    def update_child_input(self) -> None:
        """Items inside a closed compound (a group, a pen drawing) take no clicks of
        their own, so Qt picks, selects and drags the compound. Call it again when
        a compound is opened for editing or closed."""
        for item in self.items.values():
            blocked = False
            parent = item.parentItem()
            while parent is not None:
                if (isinstance(parent, NodeItemMixin) and parent.captures_children
                        and not getattr(parent, "editing", False)):
                    blocked = True
                    break
                parent = parent.parentItem()
            if blocked:
                item.setFlag(QGraphicsItem.ItemIsSelectable, False)
                item.setFlag(QGraphicsItem.ItemIsMovable, False)
                item.setAcceptedMouseButtons(Qt.NoButton)
                item._input_blocked = True
            elif getattr(item, "_input_blocked", False):
                item._input_blocked = False
                item.setAcceptedMouseButtons(Qt.AllButtons)
                item.set_locked(item.locked)  # restores selectable / movable

    def _create_item(self, node: dict[str, Any]) -> Any:
        node_type = node["type"]
        attrs = node.get("attrs", {})
        types = self.session.types
        ui = self.node_uis.get(node_type)
        item = None
        if ui is not None and ui.factory is not None:
            item = ui.factory(self, node)
        elif node_type in types and not types.is_canvas(node_type):
            return None
        elif "translate" in attrs or node_type not in types:
            if node.get("children"):
                item = ContainerItem(node["name"], node_type)
            else:
                item = PlaceholderItem(node["name"], node_type)
        if item is None:
            return None
        item.set_listener(self.on_item_geometry_changed)
        self.apply_node_transform(item, attrs)
        item.setOpacity(float(attrs.get("opacity", 1.0)))
        item.setVisible(bool(attrs.get("visibility", 1)))
        for hook in self.item_hooks:
            try:
                hook(item, node)
            except Exception:  # noqa: BLE001  (one plugin's hook must not break the canvas)
                logger.exception("canvas.item_hooks contribution failed for '%s'", node["name"])
        return item

    def _on_item_hooks_changed(self) -> None:
        self.item_hooks = list(self.session.extensions.contributions("canvas.item_hooks"))
        self.request_full_refresh()  # items are rebuilt, so every hook sees every item

    @staticmethod
    def apply_node_transform(item: NodeItemMixin, attrs: dict[str, Any]) -> None:
        def pair(value: Any, default: tuple[float, float]) -> tuple[float, float]:
            try:
                return float(value[0]), float(value[1])
            except (TypeError, ValueError, IndexError):
                return default

        x, y = pair(attrs.get("translate"), (0.0, 0.0))
        sx, sy = pair(attrs.get("scale"), (1.0, 1.0))
        pivot = pair(attrs.get("pivot"), (0.0, 0.0))
        item.apply_transform_values(x, y, sx, sy, float(attrs.get("rotate", 0.0) or 0.0), pivot=pivot)

    # ---- Selection ------------------------------------------------------------------------

    def _on_selection_model_changed(self) -> None:
        self._apply_selection_to_items()
        self.app.on_selection_changed()

    def _apply_selection_to_items(self) -> None:
        wanted = set(self.session.selection.names)
        self._syncing_selection = True
        changed = False
        try:
            # Inside the guard: closing a group deselects its members in Qt.
            self._open_compounds_for(wanted)
            for name, item in self.items.items():
                if item.is_content:
                    selected = name in wanted
                    if item.isSelected() != selected:
                        item.setSelected(selected)
                        changed = True
        finally:
            self._syncing_selection = False
        if changed:
            self.app.update_views()

    def _open_compounds_for(self, wanted: set[str]) -> None:
        """Groups (items with ``opens_for_selection``) are open exactly while something
        inside them is selected; their members then take clicks of their own."""
        inside: set[str] = set()
        for name in wanted:
            parent = self.nodes.get(name, {}).get("parent")
            while parent and parent not in inside:
                inside.add(parent)
                parent = self.nodes.get(parent, {}).get("parent")
        changed = False
        for name, item in self.items.items():
            if getattr(item, "opens_for_selection", False):
                should = name in inside
                if bool(getattr(item, "editing", False)) != should:
                    item.set_editing(should)
                    changed = True
        if changed:
            self.update_child_input()

    def open_compounds(self) -> list[Any]:
        """Groups currently open (something inside them is selected)."""
        return [item for item in self.items.values()
                if getattr(item, "opens_for_selection", False) and getattr(item, "editing", False)]

    def _on_scene_selection_changed(self) -> None:
        if self.syncing or self._syncing_selection:
            return
        try:
            selected = {item.node_name for item in self.scene.selectedItems() if isinstance(item, NodeItemMixin)}
        except RuntimeError:  # the scene is being destroyed
            return
        names = [name for name in self.nodes if name in selected]
        if names != self.session.selection.names:
            self.session.run("select", names)

    def select_items(self, items: Any) -> None:
        """Select exactly these canvas items (rubber band, clicks)."""
        wanted = {item.node_name for item in items}
        names = [name for name in self.nodes if name in wanted]
        if names != self.session.selection.names:
            self.session.run("select", names)

    def selected_names(self) -> list[str]:
        return self.session.selection.names

    def top_selected_names(self) -> list[str]:
        """Selected names without any selected ancestor, front-most first."""
        self.flush_pending(handlers=False)
        selected = set(self.session.selection.names)
        result = []
        for name in reversed(self.ordered_names()):
            if name not in selected:
                continue
            parent = self.nodes.get(name, {}).get("parent")
            covered = False
            while parent:
                if parent in selected:
                    covered = True
                    break
                parent = self.nodes.get(parent, {}).get("parent")
            if not covered:
                result.append(name)
        return result

    def ordered_names(self) -> list[str]:
        """Every node name in draw order (back to front, parents before children)."""
        self.flush_pending(handlers=False)
        return list(self.nodes)

    # ---- Item queries -------------------------------------------------------------------------

    def item(self, name: str) -> Any:
        return self.items.get(name)

    def content_items(self) -> list[Any]:
        return [item for item in self.items.values() if item.is_content]

    def visible_content_items(self) -> list[Any]:
        return [item for item in self.content_items() if item.isVisible()]

    def selected_canvas_items(self) -> list[Any]:
        return [item for item in self.scene.selectedItems() if isinstance(item, NodeItemMixin)]

    def scene_is_empty(self) -> bool:
        return not any(item.is_content for item in self.items.values())

    def transform_targets(self) -> list[Any]:
        """Selected items the gizmo and transform commands may move/scale/rotate."""
        names = set(self.session.selection.names)
        targets = []
        for name in names:
            item = self.items.get(name)
            if item is None or not item.is_content or item.locked or not item.isVisible() or item.blocks_transform:
                continue
            ancestor = item.parentItem()
            covered = False
            while ancestor is not None:
                if isinstance(ancestor, NodeItemMixin) and ancestor.is_content and ancestor.node_name in names:
                    covered = True
                    break
                ancestor = ancestor.parentItem()
            if not covered:
                targets.append(item)
        targets.sort(key=lambda it: it.node_name)
        return targets

    def outline_items(self) -> list[Any]:
        items = []
        for name in self.session.selection.names:
            item = self.items.get(name)
            if item is not None and item.is_content and item.isVisible():
                items.append(item)
        return items

    @staticmethod
    def scene_bounds(items: list[Any]) -> QRectF:
        rect = QRectF()
        listed = set(items)
        for item in items:
            # A listed ancestor's content rect already covers this item (and measuring every
            # level of a deep group tree separately is quadratic).
            parent = item.parentItem()
            while parent is not None and parent not in listed:
                parent = parent.parentItem()
            if parent is not None:
                continue
            rect = rect.united(item.scene_content_rect() if item.is_content else item.sceneBoundingRect())
        return rect

    @staticmethod
    def translate_items(items: list[Any], dx: float, dy: float) -> None:
        shift = QTransform.fromTranslate(dx, dy)
        for item in items:
            if item.is_content:
                item.set_scene_transform(item.sceneTransform() * shift)

    def effectively_locked(self, name: str | None) -> bool:
        while name:
            node = self.nodes.get(name)
            if node is None:
                return False
            if bool(node.get("attrs", {}).get("locked", 0)):
                return True
            name = node.get("parent")
        return False

    def _geometry(self, name: str) -> list[float] | None:
        item = self.items.get(name)
        if item is None or not item.is_content:
            return None
        rect = item.content_rect()
        return [rect.x(), rect.y(), rect.width(), rect.height()]

    # ---- Edits made on the canvas -------------------------------------------------------------

    def on_item_geometry_changed(self, item: Any) -> None:
        if self.syncing:
            return
        self._dirty.add(item.node_name)
        self.app.on_item_geometry_changed(item)

    def mark_dirty(self, item: Any) -> None:
        self._dirty.add(item.node_name)

    def commit_dirty(self) -> list[str]:
        """Write the edited items' values to the document; returns the nodes that changed."""
        dirty, self._dirty = self._dirty, set()
        if not dirty:
            return []
        scene = self.session.scene
        committed = []
        with self.batch():
            for name in sorted(dirty):
                item = self.items.get(name)
                if item is None or not scene.has(name):
                    continue
                stored = scene.get_node(name).attrs
                updates = {k: v for k, v in item.commit_values().items() if differs(stored.get(k), v, 1e-9)}
                if updates:
                    self.session.run("set_attrs", name, updates)
                    committed.append(name)
            # The items already show the new values: don't rebuild them.
            for name in committed:
                node = scene.get_node(name).to_dict()
                self.nodes[name] = node
                self._signatures[name] = self._signature(node)
        return committed

    def begin_interaction(self) -> None:
        """Start a canvas edit (drag): everything until end_interaction is one undo step."""
        self.end_coalesced()
        if not self._interaction:
            self.session.undo.begin("Edit")
            self._interaction = True

    def end_interaction(self, label: str = "Edit") -> None:
        if not self._interaction:
            self.commit_dirty()
            return
        try:
            self._commit_interaction(label)
        finally:
            self._interaction = False
            self.session.undo.end(label)
        self.refresh()

    def cancel_interaction(self) -> None:
        """Abort a canvas edit: the document goes back to how it was when it began (no undo step)."""
        if not self._interaction:
            return
        self._interaction = False
        self._dirty.clear()
        self.session.undo.cancel()
        self.refresh()

    @property
    def interacting(self) -> bool:
        return self._interaction or self._coalesce_label is not None

    def begin_coalesced(self, label: str, command: str | None = None) -> None:
        """Merge rapid repeated edits (nudging, wheel scaling) into one undo step."""
        if self._coalesce_label not in (None, label):
            self.end_coalesced()
        if self._coalesce_label is None:
            self.session.undo.begin(label)
            self._coalesce_label = label
            self._coalesce_command = command
        self._coalesce_timer.start(self.COALESCE_MS)

    def end_coalesced(self) -> None:
        if self._coalesce_label is None:
            return
        label, self._coalesce_label = self._coalesce_label, None
        self._coalesce_command = None
        self._coalesce_timer.stop()
        try:
            self._commit_interaction(label)
        finally:
            self.session.undo.end(label)
        self.refresh()

    def flush_pending(self, handlers: bool = True) -> None:
        """Apply pending canvas edits and refreshes; *handlers* also commits plugin edits in
        progress and closes a coalesced undo step (without it, rapid nudges stay one step)."""
        if handlers:
            for handler in list(self._flush_handlers):
                handler()
            self.end_coalesced()
        self.commit_dirty()
        if self._refresh_timer.isActive() or self._pending_refresh or self._force_full:
            self._refresh_timer.stop()
            self._pending_refresh = False
            self._do_refresh(force=self._force_full)
            self._force_full = False

    def _before_command(self, command: Any) -> None:
        if not command.echo and not command.undoable:
            return  # queries
        if command.name == "select":
            return
        if self._interaction and command.name in ("undo", "redo"):
            # Close the drag's undo chunk first: undo replaces the document and an
            # open chunk would stay open forever, so no later edit could be undone.
            self.end_interaction()
        for handler in list(self._flush_handlers):
            handler()
        if self._coalesce_label is not None and command.name != self._coalesce_command:
            self.end_coalesced()
        if not self._interaction:
            self.commit_dirty()

    # ---- Rendering ---------------------------------------------------------------------------

    def render_items(self, items: list[Any], opaque: bool = False, max_side: int = 16384,
                     final: bool = True) -> QImage | None:
        """Render ``items`` (with their children) to an image at 1:1 scene scale.

        ``final`` (export, copy as image): paint filters wait for their real
        result. Previews (thumbnails) pass False and get whatever is ready, so
        a slow effect on a huge image never blocks them. Paint filters read it
        as ``canvas.rendering_final``.
        """
        bounds = self.scene_bounds(items)
        if bounds.isNull() or bounds.width() < 1 or bounds.height() < 1:
            return None
        scale = min(1.0, max_side / max(bounds.width(), bounds.height()))
        image = QImage(max(1, math.ceil(bounds.width() * scale)), max(1, math.ceil(bounds.height() * scale)),
                       QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(*self.app.background_spec().color) if opaque else QColor(0, 0, 0, 0))
        keep = set()
        for item in items:
            keep.add(item)
            stack = list(item.childItems())
            while stack:
                child = stack.pop()
                keep.add(child)
                stack.extend(child.childItems())
        # Ancestors of kept items can't go transparent (opacity is inherited, the
        # kept child would vanish too): only stop them painting their own content.
        ancestors = set()
        for item in items:
            parent = item.parentItem()
            while parent is not None and parent not in keep:
                ancestors.add(parent)
                parent = parent.parentItem()
        hidden = []
        muted = []
        for item in self.content_items():
            if item in ancestors:
                if not item.flags() & QGraphicsItem.ItemHasNoContents:
                    muted.append(item)
                    item.setFlag(QGraphicsItem.ItemHasNoContents, True)
            elif item not in keep and item.isVisible() and item.opacity() > 0:
                hidden.append((item, item.opacity()))
                item.setOpacity(0.0)
        self.rendering_final += 1 if final else 0
        try:
            painter = QPainter(image)
            painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
            self.scene.render(painter, QRectF(0, 0, image.width(), image.height()), bounds)
            painter.end()
        finally:
            self.rendering_final -= 1 if final else 0
            for item, opacity in hidden:
                item.setOpacity(opacity)
            for item in muted:
                item.setFlag(QGraphicsItem.ItemHasNoContents, False)
        return image


