"""Cutting a part of a scene out as a scene, and adding a scene to another.

Both work on ordinary :class:`SceneGraph` objects, so the result of an
extraction serializes with ``SceneParser`` (the ``.pxref`` format) and a parsed
file merges with ``merge_scene``: ``import_scene`` and the PNG-embedded scenes
share this path.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from . import xform
from .scene import SceneGraph
from .values import fast_copy

REF_KINDS = ("node_ref", "node_refs")


def extract_subset(
    scene: SceneGraph,
    names: list[str] | None = None,
    resolve_path: Callable[[str], Path] | None = None,
    max_linked_bytes: int = 256 * 1024 * 1024,
) -> tuple[SceneGraph, list[str]]:
    """A new scene holding ``names`` (default: everything) with their descendants.

    Roots are re-expressed in world space, so the subset looks the same on its
    own. Linked image files that can be read are embedded as assets (the subset
    is self-contained); ones that cannot are left unlinked and reported. References to
    nodes outside the subset (link ends, view filters) are cleared. Returns
    ``(subset, warnings)``. The subset shares node attribute values' content
    by copy and asset bytes by reference; ``scene`` is not changed.
    """
    subset = SceneGraph(types=scene.types, app_version=scene.app_version)
    warnings: list[str] = []
    roots = scene.top_level(list(names)) if names is not None else list(scene.roots)
    keep: set[str] = set()
    for root in roots:
        keep.add(root)
        keep.update(scene.descendants(root))
    root_set = set(roots)
    ids = {scene.nodes[name].id for name in keep}
    for node in scene.iter_nodes():
        if node.name not in keep:
            continue
        attrs = fast_copy(node.attrs)
        parent = node.parent
        if node.name in root_set:
            if parent is not None and "translate" in attrs:
                attrs.update(xform.attrs_for_matrix(xform.world_matrix(scene, node.name), attrs))
            parent = None
        created = subset.create_node(node.node_type, node.name, parent=parent, attrs=attrs, node_id=node.id,
                                     strict=False, dynamic=dict(scene.dynamic_attrs(node)))
        _bind_assets(scene, subset, created, resolve_path, max_linked_bytes, warnings)
        _filter_refs(subset, created, ids)
    return subset, warnings


def _bind_assets(scene: SceneGraph, subset: SceneGraph, node: Any, resolve_path: Callable[[str], Path] | None,
                 max_bytes: int, warnings: list[str]) -> None:
    attrs = node.attrs
    asset_id = attrs.get("asset")
    if asset_id:
        asset = scene.get_asset(str(asset_id))
        if asset is not None:
            subset.assets[str(asset_id)] = asset
        else:
            attrs["asset"] = ""
            warnings.append(f"{node.name}: its embedded image is missing")
        return
    path = attrs.get("path")
    if not path or not subset.types.inherits(node.node_type, "image"):
        return
    attrs["path"] = ""  # a linked file's location is never written into an export
    source = resolve_path(str(path)) if resolve_path is not None else Path(str(path))
    try:
        if not source.is_file() or source.stat().st_size > max_bytes:
            raise OSError("missing or too large")
        data = source.read_bytes()
    except OSError:
        warnings.append(f"{node.name}: linked file {source.name} could not be embedded")
        return
    attrs["asset"] = subset.add_asset(data, name=source.name)


def _filter_refs(scene: SceneGraph, node: Any, ids: set[str]) -> None:
    for key, value in list(node.attrs.items()):
        spec = scene.attr_spec(node, key)
        kind = spec.type if spec is not None else None
        if kind == "node_ref" and value and value not in ids:
            node.attrs[key] = ""
        elif kind == "node_refs" and isinstance(value, list):
            node.attrs[key] = [ref for ref in value if ref in ids]


def merge_scene(scene: SceneGraph, other: SceneGraph, strip_paths: bool = False) -> list[str]:
    """Add every node of ``other`` to ``scene`` (on top); returns the new root names.

    Names that are taken get a numeric suffix. Ids are kept unless already in
    use, and every reference to a node (links, filters, click actions) and to
    an image asset follows the change. Assets are stored again by content hash,
    so identical images are shared with the ones already in ``scene``.
    ``strip_paths`` clears linked file paths (for scenes from untrusted sources).
    """
    asset_map = {
        asset_id: scene.add_asset(asset.data, asset.mime, asset.name)
        for asset_id in other.referenced_assets()
        if (asset := other.get_asset(asset_id)) is not None
    }
    nodes = list(other.iter_nodes())
    id_map = {node.id: (node.id if node.id and node.id not in scene.by_id else str(uuid.uuid4())) for node in nodes}
    renamed: dict[str, str] = {}
    roots: list[str] = []
    for node in nodes:
        new_name = scene.unique_name(node.name)
        renamed[node.name] = new_name
        parent = renamed.get(node.parent) if node.parent else None
        created = scene.create_node(node.node_type, new_name, parent=parent, attrs=fast_copy(node.attrs),
                                    node_id=id_map[node.id], strict=False, dynamic=dict(other.dynamic_attrs(node)))
        _remap(scene, created, id_map, asset_map, strip_paths)
        if parent is None:
            roots.append(new_name)
    return roots


def _remap(scene: SceneGraph, node: Any, id_map: dict[str, str], asset_map: dict[str, str],
           strip_paths: bool) -> None:
    for key, value in list(node.attrs.items()):
        spec = scene.attr_spec(node, key)
        kind = spec.type if spec is not None else None
        if kind == "asset":
            node.attrs[key] = asset_map.get(value, "") if value else ""
        elif kind == "path" and strip_paths:
            node.attrs[key] = ""
        elif kind == "node_ref" and value:
            node.attrs[key] = id_map.get(value, "")
        elif kind == "node_refs" and isinstance(value, list):
            node.attrs[key] = [id_map[ref] for ref in value if ref in id_map]
        elif key == "on_click" and isinstance(value, list):
            node.attrs[key] = _remap_steps(value, id_map)


def _remap_steps(value: Any, id_map: dict[str, str]) -> Any:
    """Click-action steps name nodes by id anywhere in their arguments."""
    if isinstance(value, str):
        return id_map.get(value, value)
    if isinstance(value, list):
        return [_remap_steps(item, id_map) for item in value]
    if isinstance(value, dict):
        return {key: _remap_steps(item, id_map) for key, item in value.items()}
    return value
