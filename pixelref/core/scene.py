"""The scene document: a node hierarchy (DAG) plus embedded assets.

Draw order is the order of each parent's ``children`` list (and of the root
list): the last entry is drawn on top. Attribute values are validated against
the node type's schema; nodes of types no plugin currently provides are kept
as-is (their attributes are written back unchanged), so disabling a plugin
never destroys data.
"""

from __future__ import annotations

import hashlib
import re
import uuid as _uuid
from dataclasses import dataclass
from typing import Any, Iterator

from .attributes import Attr
from .errors import SceneError
from .events import EventStream, SceneEvent
from .node import Node
from .nodetypes import NodeTypeRegistry, register_base_types
from .. import __version__ as APP_VERSION
from .values import fast_copy

_MAGIC_MIME = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


def sniff_mime(data: bytes) -> str:
    """Best-effort image MIME type from the first bytes of a file."""
    for magic, mime in _MAGIC_MIME:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


@dataclass
class Asset:
    """Binary content embedded in the scene file (e.g. an image)."""

    data: bytes
    mime: str = "application/octet-stream"
    name: str = ""

    @property
    def size(self) -> int:
        return len(self.data)


def _default_registry() -> NodeTypeRegistry:
    registry = NodeTypeRegistry()
    register_base_types(registry)
    return registry


class SceneGraph:
    """Nodes, their hierarchy and order, embedded assets and plugin data."""

    def __init__(self, types: NodeTypeRegistry | None = None, app_version: str = APP_VERSION) -> None:
        self.types = types if types is not None else _default_registry()
        self.app_version = app_version
        self.nodes: dict[str, Node] = {}
        self.roots: list[str] = []
        self.by_id: dict[str, str] = {}
        self.event_stream = EventStream()
        # Content-addressed (SHA-1) embedded files. Kept apart from node attrs so
        # undo snapshots stay small; nodes reference them via attrs["asset"].
        self.assets: dict[str, Asset] = {}
        # Scene-level data owned by plugins (e.g. the canvas background), saved
        # in the file's "plugins" section.
        self.plugin_data: dict[str, Any] = {}
        # Other top-level sections of the file (e.g. an opted-in window layout),
        # kept so they are written back.
        self.sections: dict[str, Any] = {}
        # Schema versions of node types as read from the file; kept for types no
        # plugin provides right now, so saving doesn't lose them.
        self.type_versions: dict[str, int] = {}
        # Bumped by every change made through this class (undo uses it to skip
        # re-serializing an unchanged document). Code must not edit nodes in place.
        self.revision = 0
        # The revision of each node's last change of its own (attributes, dynamic
        # attributes), and of the last change to names, hierarchy or order. With
        # them the canvas and undo snapshots redo only the nodes that changed.
        self.node_revisions: dict[str, int] = {}
        self.structure_revision = 0
        # Bumped only when node names go away (delete, rename, clear, replace), so the
        # selection checks its names only then instead of after every command.
        self.removals = 0

    def touch(self) -> None:
        """Mark the document as changed (e.g. the node types it is saved with changed)."""
        self.revision += 1
        self.structure_revision = self.revision

    def _node_changed(self, name: str) -> None:
        self.node_revisions[name] = self.revision

    def _structure_changed(self) -> None:
        self.structure_revision = self.revision

    # ---- Events --------------------------------------------------------------------

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.revision += 1
        if self.event_stream.has_listeners:
            self.event_stream.emit(SceneEvent(event_type=event_type, payload=payload))

    # ---- Whole-scene operations --------------------------------------------------------

    def clear(self) -> None:
        self.removals += 1
        self.nodes.clear()
        self.roots.clear()
        self.by_id.clear()
        self.assets.clear()
        self.plugin_data.clear()
        self.sections.clear()
        self.type_versions.clear()
        self.node_revisions.clear()
        self.emit("scene.cleared", {})
        self._structure_changed()

    def replace_from(self, other: "SceneGraph", keep_assets: bool = False) -> None:
        """Swap in the content of a fully built scene (atomic loads, undo).

        ``keep_assets`` keeps the current asset store and adds ``other``'s to it
        (undo snapshots carry no image data); it also keeps the file sections,
        which snapshots don't carry. Otherwise both are replaced.
        """
        self.removals += 1
        self.nodes = other.nodes
        self.roots = other.roots
        self.by_id = other.by_id
        if not keep_assets:
            self.assets.clear()
            self.sections = other.sections
        self.assets.update(other.assets)
        self.plugin_data = other.plugin_data
        self.type_versions = other.type_versions
        self.app_version = other.app_version
        self.node_revisions.clear()
        self.emit("scene.loaded", {"node_count": len(self.nodes)})
        self._structure_changed()

    # ---- Plugin data -----------------------------------------------------------------

    def get_plugin_data(self, key: str, default: Any = None) -> Any:
        value = self.plugin_data.get(key)
        return fast_copy(value) if value is not None else default

    def set_plugin_data(self, key: str, data: Any) -> None:
        if data is None:
            self.plugin_data.pop(key, None)
        else:
            self.plugin_data[key] = fast_copy(data)
        self.emit("plugin_data.changed", {"key": key})

    # ---- Assets ----------------------------------------------------------------------

    def add_asset(self, data: bytes, mime: str = "", name: str = "") -> str:
        """Store bytes once (deduplicated by hash) and return the asset id."""
        data = bytes(data)
        asset_id = hashlib.sha1(data).hexdigest()
        if asset_id not in self.assets:
            self.assets[asset_id] = Asset(data=data, mime=mime or sniff_mime(data), name=name)
            self.emit("asset.added", {"id": asset_id, "mime": self.assets[asset_id].mime, "size": len(data)})
        elif name and not self.assets[asset_id].name:
            self.assets[asset_id].name = name
        return asset_id

    def get_asset(self, asset_id: str) -> Asset | None:
        return self.assets.get(asset_id)

    def referenced_assets(self) -> list[str]:
        """Asset ids used by nodes, in scene order, without duplicates."""
        seen: dict[str, None] = {}
        for node in self.iter_nodes():
            asset_id = node.attrs.get("asset")
            if isinstance(asset_id, str) and asset_id:
                seen.setdefault(asset_id, None)
        return list(seen)

    # ---- Lookups ---------------------------------------------------------------------

    def has(self, name: str) -> bool:
        return name in self.nodes

    def get_node(self, name: str) -> Node:
        node = self.nodes.get(name)
        if node is None:
            raise SceneError(f"Node '{name}' does not exist")
        return node

    def resolve(self, ref: str) -> str:
        """Node name for a name or a UUID."""
        if ref in self.nodes:
            return ref
        name = self.by_id.get(ref)
        if name is None:
            raise SceneError(f"Node '{ref}' does not exist")
        return name

    def name_of_id(self, node_id: str) -> str | None:
        return self.by_id.get(node_id)

    def siblings_list(self, parent: str | None) -> list[str]:
        """The live child list of ``parent`` (the root list for None)."""
        if parent is None:
            return self.roots
        return self.get_node(parent).children

    def children_of(self, parent: str | None) -> list[str]:
        return list(self.siblings_list(parent))

    def index_of(self, name: str) -> int:
        node = self.get_node(name)
        return self.siblings_list(node.parent).index(name)

    def descendants(self, name: str) -> list[str]:
        result: list[str] = []
        for child in self.get_node(name).children:
            result.append(child)
            result.extend(self.descendants(child))
        return result

    def ancestors(self, name: str) -> list[str]:
        result: list[str] = []
        current = self.get_node(name).parent
        while current is not None:
            result.append(current)
            current = self.get_node(current).parent
        return result

    def is_descendant(self, name: str, possible_ancestor: str) -> bool:
        return possible_ancestor in self.ancestors(name)

    def top_level(self, names: list[str]) -> list[str]:
        """The names without an ancestor among them, front-most first."""
        if len(names) <= 1:  # common case (the selection), no scene walk needed
            return [name for name in names if name in self.nodes]
        chosen = set(names)
        # Parents come before children, so "has a chosen ancestor" is known for
        # a node's parent by the time the node is reached.
        covered: set[str] = set()
        result = []
        for node in self.iter_nodes():
            if node.parent is not None and (node.parent in chosen or node.parent in covered):
                covered.add(node.name)
            elif node.name in chosen:
                result.append(node.name)
        result.reverse()
        return result

    def iter_nodes(self) -> Iterator[Node]:
        """Every node, parents before children, back to front."""
        stack = [self.nodes[name] for name in reversed(self.roots)]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(self.nodes[child] for child in reversed(node.children))

    def to_dict(self) -> dict[str, Any]:
        return {"app_version": self.app_version, "nodes": [node.to_dict() for node in self.iter_nodes()]}

    def unique_name(self, base: str, taken: set[str] | None = None) -> str:
        """``base`` if free, else ``base_1``, ``base_2``… (globally unique)."""
        base = (base or "").strip() or "Node"
        taken = taken or set()

        def free(name: str) -> bool:
            return name not in taken and name not in self.nodes

        if free(base):
            return base
        stem = re.sub(r"_\d+$", "", base) or base
        index = 1
        while not free(f"{stem}_{index}"):
            index += 1
        return f"{stem}_{index}"

    # ---- Attribute specs -----------------------------------------------------------------

    def attr_spec(self, node: Node, key: str) -> Attr | None:
        spec = self.types.attr(node.node_type, key) if node.node_type in self.types else None
        if spec is None:
            spec = node.dynamic.get(key)
        return spec

    def _prepare_value(self, node: Node, key: str, value: Any, strict: bool) -> tuple[str, Any]:
        """Canonical (name, value) for setting ``key`` on ``node``."""
        known_type = node.node_type in self.types
        spec = self.attr_spec(node, key)
        if spec is None:
            if known_type and strict:
                raise SceneError(f"'{node.name}' ({node.node_type}) has no attribute '{key}'")
            if known_type:
                node.dynamic[key] = Attr(key, "any")
            return key, fast_copy(value)
        if spec.computed:
            if strict:
                raise SceneError(f"Attribute '{node.name}.{spec.name}' is computed (read-only)")
            return spec.name, value
        if strict:
            return spec.name, spec.validate(value)
        # Loading a file or an undo snapshot: the data was valid when saved and
        # was just parsed (nothing else references it), so store it as-is.
        return spec.name, value

    # ---- Node operations ------------------------------------------------------------------

    def create_node(
        self,
        node_type: str,
        name: str,
        parent: str | None = None,
        attrs: dict[str, Any] | None = None,
        node_id: str | None = None,
        index: int | None = None,
        strict: bool = True,
        dynamic: dict[str, Attr] | None = None,
    ) -> Node:
        if not name:
            raise SceneError("Node name must not be empty")
        if name in self.nodes:
            raise SceneError(f"Node '{name}' already exists")
        if parent and parent not in self.nodes:
            raise SceneError(f"Parent node '{parent}' does not exist")
        type_spec = self.types.get(node_type)
        if type_spec is not None and type_spec.abstract and strict:
            raise SceneError(f"Node type '{node_type}' is abstract")
        if type_spec is None and strict:
            raise SceneError(f"Unknown node type '{node_type}' (is the plugin providing it enabled?)")

        node = Node(
            id=node_id or str(_uuid.uuid4()),
            name=name,
            node_type=node_type,
            parent=parent or None,
            attrs=self.types.defaults(node_type) if type_spec is not None else {},
            dynamic=dict(dynamic or {}),
        )
        for key, value in (attrs or {}).items():
            key, value = self._prepare_value(node, key, value, strict)
            node.attrs[key] = value

        self.nodes[name] = node
        self.by_id[node.id] = name
        siblings = self.siblings_list(node.parent)
        if index is None or index >= len(siblings):
            siblings.append(name)
        else:
            siblings.insert(max(0, index), name)
        self.revision += 1
        if self.event_stream.has_listeners:
            self.emit("node.created", {"node": node.to_dict()})
        self._node_changed(name)
        self._structure_changed()
        return node

    def set_attr(self, name: str, key: str, value: Any, strict: bool = True) -> None:
        node = self.get_node(name)
        key, value = self._prepare_value(node, key, value, strict)
        node.attrs[key] = value
        self.revision += 1
        if self.event_stream.has_listeners:
            self.emit("node.attr_changed", {"name": name, "attr": key, "value": fast_copy(value)})
        self._node_changed(name)

    def set_attrs(self, name: str, attrs: dict[str, Any], strict: bool = True) -> None:
        # Validate everything first so a bad value leaves the node untouched.
        node = self.get_node(name)
        prepared = [self._prepare_value(node, key, value, strict) for key, value in attrs.items()]
        for key, value in prepared:
            node.attrs[key] = value
            self.revision += 1
            if self.event_stream.has_listeners:
                self.emit("node.attr_changed", {"name": name, "attr": key, "value": fast_copy(value)})
        self._node_changed(name)

    def get_attr(self, name: str, key: str) -> Any:
        node = self.get_node(name)
        spec = self.attr_spec(node, key)
        real = spec.name if spec is not None else key
        if real not in node.attrs:
            if spec is not None and real in self.types.sparse_attrs(node.node_type):
                return spec.default_value()  # an extension attribute that was never set
            raise SceneError(f"'{name}' has no stored attribute '{key}'")
        return fast_copy(node.attrs[real])

    def dynamic_attrs(self, node: Node) -> dict[str, Attr]:
        """The node's own dynamic attributes, without those its type now declares
        (a value saved while the plugin that declares it was off comes back as a
        dynamic attribute; once the plugin is on again, its spec wins)."""
        if not node.dynamic or node.node_type not in self.types:
            return node.dynamic
        declared = self.types.attrs(node.node_type)
        return {name: attr for name, attr in node.dynamic.items() if name not in declared}

    def add_attr(self, name: str, attr: Attr, value: Any = None) -> None:
        node = self.get_node(name)
        if self.attr_spec(node, attr.name) is not None:
            raise SceneError(f"'{name}' already has an attribute '{attr.name}'")
        node.dynamic[attr.name] = attr
        node.attrs[attr.name] = attr.default_value() if value is None else coerce_value(attr, value)
        self.emit("node.attr_added", {"name": name, "attr": attr.name})
        self._node_changed(name)

    def delete_attr(self, name: str, attr_name: str) -> None:
        node = self.get_node(name)
        if attr_name not in node.dynamic:
            raise SceneError(f"'{name}.{attr_name}' is not a dynamic attribute")
        del node.dynamic[attr_name]
        node.attrs.pop(attr_name, None)
        self.emit("node.attr_deleted", {"name": name, "attr": attr_name})
        self._node_changed(name)

    def rename_node(self, name: str, new_name: str) -> None:
        node = self.get_node(name)
        if new_name == name:
            return
        if not new_name:
            raise SceneError("Node name must not be empty")
        if new_name in self.nodes:
            raise SceneError(f"Node '{new_name}' already exists")
        self.removals += 1
        # Rebuild the dict in place so creation order is preserved.
        items = [((new_name if key == name else key), value) for key, value in self.nodes.items()]
        self.nodes.clear()
        self.nodes.update(items)
        node.name = new_name
        self.by_id[node.id] = new_name
        siblings = self.siblings_list(node.parent)
        siblings[siblings.index(name)] = new_name
        for child_name in node.children:
            self.nodes[child_name].parent = new_name
        self.node_revisions.pop(name, None)
        self.emit("node.renamed", {"name": name, "new_name": new_name})
        self._node_changed(new_name)
        self._structure_changed()

    def delete_node(self, name: str) -> list[str]:
        node = self.get_node(name)
        doomed = [name] + self.descendants(name)
        self.removals += 1
        siblings = self.siblings_list(node.parent)
        if name in siblings:
            siblings.remove(name)
        for candidate in doomed:
            removed = self.nodes.pop(candidate, None)
            if removed is not None:
                self.by_id.pop(removed.id, None)
        for candidate in doomed:
            self.node_revisions.pop(candidate, None)
        self.emit("node.deleted", {"name": name, "deleted": doomed})
        self._structure_changed()
        return doomed

    def reparent(self, name: str, new_parent: str | None, index: int | None = None) -> None:
        node = self.get_node(name)
        if new_parent is not None:
            if new_parent not in self.nodes:
                raise SceneError(f"Parent node '{new_parent}' does not exist")
            if new_parent == name:
                raise SceneError("A node cannot be parented to itself")
            if self.is_descendant(new_parent, name):
                raise SceneError("Reparent would create a cycle")
        old_parent = node.parent
        if old_parent == new_parent and index is None:
            return
        self.siblings_list(old_parent).remove(name)
        node.parent = new_parent
        siblings = self.siblings_list(new_parent)
        if index is None or index >= len(siblings):
            siblings.append(name)
        else:
            siblings.insert(max(0, index), name)
        self.emit("node.reparented", {"name": name, "old_parent": old_parent, "new_parent": new_parent})
        self._node_changed(name)
        self._structure_changed()

    def restack(self, ordered_names: list[str]) -> None:
        """Put siblings in the given order, back-most first.

        The named nodes keep the slots they collectively occupy; any siblings
        not named stay where they are.
        """
        if not ordered_names:
            return
        parents = {self.get_node(name).parent for name in ordered_names}
        if len(parents) > 1:
            raise SceneError("restack expects nodes that share the same parent")
        siblings = self.siblings_list(parents.pop())
        wanted = set(ordered_names)
        slots = [index for index, name in enumerate(siblings) if name in wanted]
        if len(slots) != len(ordered_names):
            raise SceneError("restack: duplicate or foreign names")
        if [siblings[i] for i in slots] == list(ordered_names):
            return
        for slot, name in zip(slots, ordered_names):
            siblings[slot] = name
        self.emit("node.reordered", {"names": list(ordered_names)})
        self._structure_changed()

    def move_z(self, name: str, mode: str) -> None:
        """Bring a node to the front or send it to the back of its siblings."""
        if mode not in ("front", "back"):
            raise SceneError("mode must be 'front' or 'back'")
        node = self.get_node(name)
        siblings = self.siblings_list(node.parent)
        siblings.remove(name)
        if mode == "front":
            siblings.append(name)
        else:
            siblings.insert(0, name)
        self.emit("node.reordered", {"names": [name]})
        self._structure_changed()

    def reorder(self, names: list[str], mode: str) -> None:
        """front / back / forward / backward for several nodes (per parent)."""
        by_parent: dict[str | None, list[str]] = {}
        for name in names:
            by_parent.setdefault(self.get_node(name).parent, []).append(name)
        for parent, group in by_parent.items():
            order = list(self.siblings_list(parent))  # back → front
            chosen = set(group)
            if mode == "front":
                order = [n for n in order if n not in chosen] + [n for n in order if n in chosen]
            elif mode == "back":
                order = [n for n in order if n in chosen] + [n for n in order if n not in chosen]
            elif mode == "forward":
                for i in range(len(order) - 2, -1, -1):
                    if order[i] in chosen and order[i + 1] not in chosen:
                        order[i], order[i + 1] = order[i + 1], order[i]
            elif mode == "backward":
                for i in range(1, len(order)):
                    if order[i] in chosen and order[i - 1] not in chosen:
                        order[i], order[i - 1] = order[i - 1], order[i]
            else:
                raise SceneError("mode must be front, back, forward or backward")
            self.restack(order)


def coerce_value(attr: Attr, value: Any) -> Any:
    from .attributes import coerce

    return coerce(attr, value)
