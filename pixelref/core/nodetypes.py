"""Node types and the registry plugins add them to.

Like Maya, node types form an inheritance tree. The kernel registers the
abstract bases:

* ``node``          every node: ``tags``
* ``canvas_node``   everything that appears on the canvas: the core
                    transform and display attributes (``translate``,
                    ``rotate``, ``scale``, ``pivot``, ``visibility``,
                    ``opacity``, ``locked``) plus computed ``local_bounds``,
                    ``world_matrix``, ``world_bounds``, ``world_visibility``,
                    ``world_opacity`` and ``world_locked``
* ``container``     a canvas node that only holds children (groups)
* ``dg_node``       a node with no canvas presence (cameras, presets…)

Plugins register concrete types (``image``, ``annotation_rect``…) that
inherit from these. Node types are identified by name; the names are what the
scene file stores, so they never change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .attributes import Attr
from .errors import SceneError
from .extensions import Disposable

# compute(session, node) -> value, for a computed attribute
ComputeFn = Callable[[Any, Any], Any]
# upgrade(attrs, from_version) -> attrs, for files saved with an older type schema_version
UpgradeFn = Callable[[dict[str, Any], int], dict[str, Any]]

# Capabilities a node type can declare (free-form strings; these are the ones
# the built-in plugins understand).
CAP_CONTENT = "content"  # visible content: selectable, transformable on the canvas
CAP_CONTAINER = "container"  # only holds children (groups)
CAP_COMPOUND = "compound"  # clicking a child picks this node instead (pen drawings)
CAP_ERASABLE = "erasable"  # the eraser tool may delete it
CAP_COLORABLE = "colorable"  # "apply colour" writes its ``color_attr``


@dataclass
class NodeType:
    name: str
    inherits: str | None = "node"
    attrs: tuple[Attr, ...] = ()
    abstract: bool = False
    category: str = ""  # "canvas", "container", "dg"; inherited when empty
    label: str = ""
    schema_version: int = 1
    upgrade: UpgradeFn | None = None
    capabilities: frozenset[str] = frozenset()
    compute: dict[str, ComputeFn] = field(default_factory=dict)
    color_attr: str = ""  # attribute the "apply colour" command writes
    # Attributes the canvas's quick editor shows for a selected node of this type
    # (users can change the list); empty: the parent type's list.
    quick_attrs: tuple[str, ...] = ()
    plugin: str | None = None
    doc: str = ""

    @property
    def nice_name(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize()


@dataclass
class TypeExtension:
    """Attributes a plugin adds to a node type it doesn't own (Maya's extension
    attributes), e.g. an ``effects`` stack on every ``canvas_node``.

    The attributes are inherited by every subtype. They are *sparse*: nodes
    don't store them until they are set, so files and undo snapshots of scenes
    that never use them don't change; reading an unset one gives its default.
    The target type doesn't have to be registered yet.
    """

    type_name: str
    attrs: tuple[Attr, ...]
    plugin: str | None = None


class NodeTypeRegistry:
    """All known node types. Lookups resolve inheritance and are cached."""

    def __init__(self) -> None:
        self._types: dict[str, NodeType] = {}
        self._extensions: list[TypeExtension] = []
        self._attr_cache: dict[str, dict[str, Attr]] = {}
        self._sparse_cache: dict[str, frozenset[str]] = {}
        self._listeners: list[Callable[[str], None]] = []
        self.generation = 0  # bumped whenever the registered types change

    # ---- Registration ----------------------------------------------------------------

    def register(self, node_type: NodeType) -> Disposable:
        if node_type.name in self._types:
            owner = self._types[node_type.name].plugin or "the kernel"
            raise SceneError(f"Node type '{node_type.name}' is already registered by {owner}")
        if node_type.inherits and node_type.inherits not in self._types:
            raise SceneError(f"Node type '{node_type.name}' inherits unknown type '{node_type.inherits}'")
        self._types[node_type.name] = node_type
        self._clear_caches()
        self._notify(node_type.name)
        return Disposable(lambda: self.unregister(node_type.name))

    def unregister(self, name: str) -> None:
        if self._types.pop(name, None) is not None:
            self._clear_caches()
            self._notify(name)

    def extend(self, extension: TypeExtension) -> Disposable:
        """Add attributes to ``extension.type_name`` and its subtypes."""
        new = [attr.name for attr in extension.attrs]
        if len(set(new)) != len(new):
            raise SceneError(f"Extension of '{extension.type_name}' declares an attribute twice")
        for attr in extension.attrs:
            if attr.computed:
                raise SceneError(f"Extension attribute '{attr.name}' can't be computed")
        # A clash with any type the extension reaches (or that reaches it) would
        # make one plugin's attribute silently shadow another's.
        for type_name in list(self._types) + [extension.type_name]:
            if type_name != extension.type_name and not self.inherits(type_name, extension.type_name):
                continue
            taken = self.attrs(type_name)
            for name in new:
                if name in taken:
                    raise SceneError(f"'{type_name}' already has an attribute '{name}'")
        for other in self._extensions:
            if other.type_name == extension.type_name and set(new) & {a.name for a in other.attrs}:
                raise SceneError(f"Another plugin already extends '{extension.type_name}' with "
                                 f"{', '.join(sorted(set(new) & {a.name for a in other.attrs}))}")
        self._extensions.append(extension)
        self._clear_caches()
        self._notify(extension.type_name)

        def remove() -> None:
            if extension in self._extensions:
                self._extensions.remove(extension)
                self._clear_caches()
                self._notify(extension.type_name)

        return Disposable(remove)

    def extensions(self, type_name: str | None = None) -> list[TypeExtension]:
        return [e for e in self._extensions if type_name is None or e.type_name == type_name]

    def _clear_caches(self) -> None:
        self.generation += 1
        self._attr_cache.clear()
        self._sparse_cache.clear()

    def watch(self, callback: Callable[[str], None]) -> Disposable:
        self._listeners.append(callback)
        return Disposable(lambda: callback in self._listeners and self._listeners.remove(callback))

    def _notify(self, name: str) -> None:
        for callback in list(self._listeners):
            callback(name)

    # ---- Lookups ---------------------------------------------------------------------------

    def get(self, name: str) -> NodeType | None:
        return self._types.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._types

    def names(self, include_abstract: bool = False) -> list[str]:
        return sorted(n for n, t in self._types.items() if include_abstract or not t.abstract)

    def lineage(self, name: str) -> list[NodeType]:
        """The type and its ancestors, most derived first."""
        chain: list[NodeType] = []
        current = self._types.get(name)
        seen: set[str] = set()
        while current is not None and current.name not in seen:
            chain.append(current)
            seen.add(current.name)
            current = self._types.get(current.inherits) if current.inherits else None
        return chain

    def inherits(self, name: str, base: str) -> bool:
        return any(t.name == base for t in self.lineage(name))

    def attrs(self, name: str) -> dict[str, Attr]:
        """All attributes of a type (inherited first, overridden by subtypes),
        including attributes plugins added with :meth:`extend`."""
        cached = self._attr_cache.get(name)
        if cached is not None:
            return cached
        merged: dict[str, Attr] = {}
        sparse: set[str] = set()
        for node_type in reversed(self.lineage(name)):
            for attr in node_type.attrs:
                merged[attr.name] = attr
                sparse.discard(attr.name)
            for extension in self._extensions:
                if extension.type_name == node_type.name:
                    for attr in extension.attrs:
                        merged[attr.name] = attr
                        sparse.add(attr.name)
        self._attr_cache[name] = merged
        self._sparse_cache[name] = frozenset(sparse)
        return merged

    def sparse_attrs(self, name: str) -> frozenset[str]:
        """Names of the type's extension attributes (not stored until set)."""
        if name not in self._sparse_cache:
            self.attrs(name)
        return self._sparse_cache.get(name, frozenset())

    def attr(self, type_name: str, attr_name: str) -> Attr | None:
        attrs = self.attrs(type_name)
        found = attrs.get(attr_name)
        if found is not None:
            return found
        for attr in attrs.values():  # short names (t, r, s, v…)
            if attr.short and attr.short == attr_name:
                return attr
        return None

    def defaults(self, name: str) -> dict[str, Any]:
        sparse = self.sparse_attrs(name)
        return {
            attr.name: attr.default_value()
            for attr in self.attrs(name).values()
            if attr.storable and not attr.computed and attr.name not in sparse
        }

    def category(self, name: str) -> str:
        for node_type in self.lineage(name):
            if node_type.category:
                return node_type.category
        return ""

    def has_capability(self, name: str, capability: str) -> bool:
        return any(capability in t.capabilities for t in self.lineage(name))

    def compute_fn(self, type_name: str, attr_name: str) -> ComputeFn | None:
        for node_type in self.lineage(type_name):
            fn = node_type.compute.get(attr_name)
            if fn is not None:
                return fn
        return None

    def color_attr(self, name: str) -> str:
        for node_type in self.lineage(name):
            if node_type.color_attr:
                return node_type.color_attr
        return ""

    def quick_attrs(self, name: str) -> tuple[str, ...]:
        """The type's quick editor attributes (the nearest type in its lineage that declares some),
        keeping only attributes the type has."""
        attrs = self.attrs(name)
        for node_type in self.lineage(name):
            if node_type.quick_attrs:
                return tuple(a for a in node_type.quick_attrs if a in attrs)
        return ()

    def label(self, name: str) -> str:
        node_type = self._types.get(name)
        return node_type.nice_name if node_type else name

    def is_canvas(self, name: str) -> bool:
        return self.inherits(name, "canvas_node")


# ---- Kernel base types ----------------------------------------------------------------------------

CORE_CANVAS_ATTRS = (
    "translate", "rotate", "scale", "pivot", "visibility", "opacity", "locked",
)

BASE_TYPES: tuple[NodeType, ...] = (
    NodeType(
        name="node",
        inherits=None,
        abstract=True,
        attrs=(Attr("tags", "string_list", [], short="tg", group="Tags", doc="Free-form labels for organising"),),
        doc="Base of every node.",
    ),
    NodeType(
        name="canvas_node",
        inherits="node",
        abstract=True,
        category="canvas",
        attrs=(
            Attr("translate", "float2", [0.0, 0.0], short="t", group="Transform",
                 doc="Position of the local origin in the parent's space"),
            Attr("rotate", "float", 0.0, short="r", group="Transform", doc="Rotation in degrees"),
            Attr("scale", "float2", [1.0, 1.0], short="s", group="Transform", doc="Scale (negative flips)"),
            Attr("pivot", "float2", [0.0, 0.0], short="p", group="Transform",
                 doc="Local point that rotation and scale happen around"),
            Attr("visibility", "flag", 1, short="v", group="Display"),
            Attr("opacity", "float", 1.0, short="op", minimum=0.0, maximum=1.0, group="Display",
                 step=0.05, precision=0, unit="%", display_scale=100.0),
            Attr("locked", "flag", 0, short="lk", group="Display", doc="Locked nodes can't be selected on the canvas"),
            Attr("local_bounds", "rect", None, computed=True, storable=False, group="Computed",
                 doc="Content rectangle [x, y, w, h] in local space"),
            Attr("local_matrix", "any", None, computed=True, storable=False, group="Computed"),
            Attr("world_matrix", "any", None, computed=True, storable=False, group="Computed",
                 doc="Local → world 2D affine matrix [m11, m12, m21, m22, dx, dy]"),
            Attr("world_bounds", "rect", None, computed=True, storable=False, group="Computed",
                 doc="Axis-aligned bounds [x, y, w, h] in world space"),
            Attr("world_visibility", "flag", 1, computed=True, storable=False, group="Computed"),
            Attr("world_opacity", "float", 1.0, computed=True, storable=False, group="Computed"),
            Attr("world_locked", "flag", 0, computed=True, storable=False, group="Computed"),
        ),
        quick_attrs=("opacity",),
        doc="Every element that appears on the canvas.",
    ),
    NodeType(
        name="container",
        inherits="canvas_node",
        abstract=True,
        category="container",
        capabilities=frozenset({CAP_CONTAINER}),
        doc="A canvas node that only holds children.",
    ),
    NodeType(
        name="dg_node",
        inherits="node",
        abstract=True,
        category="dg",
        doc="A node without canvas presence (cameras, display presets…).",
    ),
)


def register_base_types(registry: NodeTypeRegistry) -> None:
    for node_type in BASE_TYPES:
        registry.register(node_type)
