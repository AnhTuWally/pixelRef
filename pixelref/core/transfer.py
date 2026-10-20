"""Copying attribute values from one node onto others.

Two ways to match what is copied:

* **multi** (copy / paste attributes): an attribute transfers when the target has one with the
  same name *and* the same attribute type (colour with colour, number with number), whatever
  the node types are, so a rectangle's look can be pasted onto an ellipse or an arrow.
* **single** (push one attribute): the target only needs an attribute of the same name. The
  value goes through the target's own spec, and a target it doesn't fit is skipped.

Which attributes are offered, and which start ticked, comes from ``Attr.transfer``
("style" / "geometry" / "never"); when a spec leaves it empty :func:`kind_of` infers it.
Everything here is plain data and scene calls, so it works headless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .attributes import Attr
from .errors import SceneError
from .values import fast_copy

STYLE, GEOMETRY, NEVER = "style", "geometry", "never"
GEOMETRY_GROUP = "Geometry"

# Position, size, direction and the basic form (a rectangle or an ellipse): listed, but never ticked by default.
GEOMETRY_NAMES = frozenset({"translate", "rotate", "scale", "pivot", "size", "start", "end", "tail", "crop",
                            "points", "text_width", "shape"})
GEOMETRY_TYPES = frozenset({"float2", "rect", "points"})
# Identity and content: what the item *is*, not how it looks.
NEVER_NAMES = frozenset({"name", "note", "metadata", "tags", "visibility", "locked", "text", "runs", "effects",
                         "effects_muted", "effects_all_frames"})
NEVER_TYPES = frozenset({"asset", "path", "node_ref", "node_refs"})

# Types a single attribute may be converted between when the names match.
_FAMILIES = {"int": "number", "float": "number", "flag": "number", "bool": "number",
             "color": "color", "color4": "color", "string": "text", "text": "text", "enum": "text"}


def family(attr_type: str) -> str:
    return _FAMILIES.get(attr_type, attr_type)


def kind_of(attr: Attr) -> str:
    """"style", "geometry" or "never" for an attribute spec."""
    if attr.computed or attr.readonly or not attr.storable:
        return NEVER
    if attr.transfer:
        return attr.transfer
    if attr.name in NEVER_NAMES or attr.type in NEVER_TYPES:
        return NEVER
    if attr.name in GEOMETRY_NAMES or attr.type in GEOMETRY_TYPES:
        return GEOMETRY
    if attr.hidden:
        return NEVER
    return STYLE


def group_of(attr: Attr) -> str:
    """The group an attribute is listed under (geometry always gets its own)."""
    if kind_of(attr) == GEOMETRY:
        return GEOMETRY_GROUP
    return attr.group or "Other"


def _spec(scene: Any, node: Any, name: str) -> Attr | None:
    spec = scene.attr_spec(node, name)
    if spec is None or spec.computed or spec.readonly or not spec.storable:
        return None
    return spec


def spec_for(scene: Any, name: str, attr: str) -> Attr | None:
    """A node's writable attribute spec called ``attr``, or None."""
    return _spec(scene, scene.get_node(name), attr)


def specs_of(scene: Any, name: str) -> dict[str, Attr]:
    """Every writable attribute of a node (declared, inherited and dynamic)."""
    node = scene.get_node(name)
    out: dict[str, Attr] = {}
    if node.node_type in scene.types:
        out.update(scene.types.attrs(node.node_type))
    out.update(node.dynamic)
    return {key: spec for key, spec in out.items() if not (spec.computed or spec.readonly or not spec.storable)}


def value_of(scene: Any, name: str, attr: str) -> Any:
    node = scene.get_node(name)
    if attr in node.attrs:
        return fast_copy(node.attrs[attr])
    spec = scene.attr_spec(node, attr)
    return spec.default_value() if spec is not None else None


@dataclass
class Entry:
    """One copied attribute: its value and the spec it came from."""

    name: str
    spec: Attr
    value: Any

    @property
    def kind(self) -> str:
        return kind_of(self.spec)

    @property
    def group(self) -> str:
        return group_of(self.spec)

    @property
    def label(self) -> str:
        return self.spec.nice_name


@dataclass
class Snapshot:
    """The attributes of one node at the time they were copied."""

    source: str
    node_type: str
    entries: dict[str, Entry] = field(default_factory=dict)

    def values(self, names: Iterable[str] | None = None) -> dict[str, Any]:
        wanted = self.entries if names is None else [n for n in names if n in self.entries]
        return {n: fast_copy(self.entries[n].value) for n in wanted}

    def default_names(self) -> list[str]:
        return [n for n, e in self.entries.items() if e.kind == STYLE]


def snapshot(scene: Any, name: str) -> Snapshot:
    """Copy every offered attribute of a node."""
    node = scene.get_node(name)
    snap = Snapshot(name, node.node_type)
    for key, spec in specs_of(scene, name).items():
        if kind_of(spec) != NEVER:
            snap.entries[key] = Entry(key, spec, value_of(scene, name, key))
    return snap


def accepts(entry: Entry, spec: Attr | None, mode: str) -> bool:
    """Can a target attribute ``spec`` take ``entry``'s value?"""
    if spec is None:
        return False
    if mode == "multi":
        return spec.type == entry.spec.type and kind_of(spec) != NEVER
    return family(spec.type) == family(entry.spec.type)  # asked for by name: identity attributes too


def plan(scene: Any, snap: Snapshot, targets: Iterable[str], mode: str = "multi") -> dict[str, list[str]]:
    """For each copied attribute, the targets (other than the source) that can take it."""
    nodes = [(t, scene.get_node(t)) for t in targets if t != snap.source and scene.has(t)]
    out: dict[str, list[str]] = {}
    for key, entry in snap.entries.items():
        out[key] = [t for t, node in nodes if accepts(entry, _spec(scene, node, key), mode)]
    return out


@dataclass
class Result:
    changed: dict[str, list[str]] = field(default_factory=dict)  # target -> attributes written
    skipped: dict[str, list[str]] = field(default_factory=dict)  # attribute -> targets that couldn't take it

    @property
    def items(self) -> int:
        return len(self.changed)

    @property
    def writes(self) -> int:
        return sum(len(v) for v in self.changed.values())

    def summary(self) -> dict[str, Any]:
        return {"items": self.items, "values": self.writes, "skipped": {k: list(v) for k, v in self.skipped.items()}}


def apply_values(scene: Any, values: dict[str, Any], targets: Iterable[str], mode: str = "single",
                 source_specs: dict[str, Attr] | None = None, source: str | None = None) -> Result:
    """Write ``values`` onto the targets that can take them.

    ``source_specs`` (attribute name -> the spec the value came from) lets the type rules above
    apply; without it any target attribute of that name is tried and its own spec converts the
    value. Values a target already has are left alone, so an item that already matches isn't
    counted as changed.
    """
    result = Result()
    for target in dict.fromkeys(targets):
        if target == source or not scene.has(target):
            continue
        node = scene.get_node(target)
        prepared: dict[str, Any] = {}
        for key, value in values.items():
            spec = _spec(scene, node, key)
            if spec is None:
                result.skipped.setdefault(key, []).append(target)
                continue
            if source_specs is not None and key in source_specs:
                entry = Entry(key, source_specs[key], value)
                if not accepts(entry, spec, mode):
                    result.skipped.setdefault(key, []).append(target)
                    continue
            elif kind_of(spec) == NEVER and mode == "multi":
                result.skipped.setdefault(key, []).append(target)
                continue
            try:
                canonical = spec.validate(fast_copy(value))
            except SceneError:
                result.skipped.setdefault(key, []).append(target)
                continue
            if node.attrs.get(spec.name, _MISSING) != canonical and not _is_default(spec, node, canonical):
                prepared[spec.name] = canonical
        if prepared:
            scene.set_attrs(target, prepared)
            result.changed[target] = list(prepared)
    return result


_MISSING = object()


def _is_default(spec: Attr, node: Any, canonical: Any) -> bool:
    """A sparse attribute that was never set already reads as its default."""
    return spec.name not in node.attrs and canonical == spec.default_value()
