"""Clipboard: copy/cut/paste/duplicate/delete and dropping or pasting content.

Copies carry the nodes as JSON (``application/x-pixelref-nodes``) with
embedded image data, so they paste into other windows and scenes. What a
drop or a non-pixelRef paste becomes is decided by the ``ingest.handlers``
contributions (images from the image plugin, notes from the text plugin,
scene files from the file plugin).
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from pixelref.plugin_api import CommandError, fast_copy, xform

NODES_MIME = "application/x-pixelref-nodes"
PASTE_OFFSET = 24.0


def serialize_subtrees(session: Any, names: list[str], scene_space: bool) -> dict[str, Any]:
    """Nodes (with descendants) as a paste payload; roots in world space if ``scene_space``."""
    scene = session.scene
    order = {node.name: index for index, node in enumerate(scene.iter_nodes())}
    nodes: list[dict[str, Any]] = []
    assets: dict[str, dict[str, str]] = {}
    # Back-most root first so re-created roots stack in their original order.
    for root in reversed(names):
        subtree = [root] + scene.descendants(root)
        subtree.sort(key=lambda n: order.get(n, 0))
        for name in subtree:
            node = scene.get_node(name)
            attrs = fast_copy(node.attrs)
            if node.node_type == "image" and attrs.get("path"):
                attrs["path"] = session.resolve_path(str(attrs["path"])).as_posix()
            if node.node_type == "image" and attrs.get("asset") and scene_space:
                asset = scene.get_asset(attrs["asset"])
                if asset is not None:
                    assets[attrs["asset"]] = {"name": asset.name, "mime": asset.mime,
                                              "data": base64.b64encode(asset.data).decode("ascii")}
            entry = {"name": name, "type": node.node_type, "parent": node.parent, "attrs": attrs}
            dynamic = session.scene.dynamic_attrs(node)
            if dynamic:
                entry["dynamic"] = {k: v.to_dict() for k, v in dynamic.items()}
            if name == root:
                entry["parent"] = None if scene_space else node.parent
                if scene_space and "translate" in attrs:
                    attrs.update(xform.attrs_for_matrix(xform.world_matrix(scene, name), attrs))
            nodes.append(entry)
    return {"format": "pixelref-nodes", "version": 1, "nodes": nodes, "assets": assets}


def instantiate(session: Any, payload: dict[str, Any]) -> list[str]:
    """Create nodes from a paste payload; returns the new root names."""
    from pixelref.core.attributes import Attr

    scene = session.scene
    name_map: dict[str, str] = {}
    taken: set[str] = set()
    roots: list[str] = []
    asset_map: dict[str, str] = {}
    for asset_id, entry in (payload.get("assets") or {}).items():
        try:
            data = base64.b64decode(entry["data"])
        except (KeyError, ValueError):
            continue
        asset_map[asset_id] = scene.add_asset(data, mime=entry.get("mime", ""), name=entry.get("name", ""))
    for entry in payload.get("nodes", []):
        new_name = scene.unique_name(entry["name"], taken)
        taken.add(new_name)
        name_map[entry["name"]] = new_name
        parent = entry.get("parent")
        parent = name_map.get(parent, parent if parent and scene.has(parent) else None)
        attrs = dict(entry.get("attrs", {}))
        if entry["type"] == "image" and attrs.get("path"):
            attrs["path"] = session.to_scene_path(Path(attrs["path"]))
        if entry["type"] == "image" and attrs.get("asset"):
            attrs["asset"] = asset_map.get(attrs["asset"], attrs["asset"])
        dynamic = {k: Attr.from_dict(k, v) for k, v in (entry.get("dynamic") or {}).items()}
        scene.create_node(entry["type"], new_name, parent=parent, attrs=attrs, strict=False, dynamic=dynamic)
        if entry.get("parent") not in name_map:
            roots.append(new_name)
            scene.move_z(new_name, "front")  # in front of the new siblings
    return roots


def activate(ctx: Any) -> None:
    ctx.declare_extension_point("ingest.handlers", "IngestHandler: turn dropped/pasted data into nodes")
    ctx.declare_extension_point("clipboard.exporters", "callable(names, QMimeData): add formats when copying")
    session = ctx.session

    def delete_selected(nodes: Any = None) -> list[str]:
        """Delete the selected nodes (with their children)."""
        names = session.selection.names if nodes is None else [
            session.scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        names = session.scene.top_level(names)
        if not names:
            raise CommandError("Nothing selected")
        for name in names:
            if session.scene.has(name):
                session.scene.delete_node(name)
        return names

    def duplicate_selected(nodes: Any = None) -> list[str]:
        """Duplicate the selection, offset a little, and select the copies."""
        names = session.selection.names if nodes is None else [
            session.scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        names = session.scene.top_level(names)
        if not names:
            raise CommandError("Nothing selected")
        copies = ctx.run("duplicate", list(reversed(names)), offset=(PASTE_OFFSET, PASTE_OFFSET))
        session.selection.set(copies)
        return copies

    ctx.command("delete", delete_selected, alias="delete_selected", label="Delete")
    ctx.command("duplicate", duplicate_selected, alias="duplicate_selected", label="Duplicate")


def activate_ui(ui: Any) -> None:
    from .ui import activate_clipboard_ui

    activate_clipboard_ui(ui)


