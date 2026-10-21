"""Read a ``.pxref`` scene file without opening it: what an AI agent (``mcp_server``) sees of a board.

Everything here reads the file's JSON directly. No plugin is loaded and no node type's code runs,
so inspecting a board someone sent you is as safe as reading it in a text editor. Embedded image
data is dropped as soon as the file is parsed; only each asset's name, type and size are kept.

* :func:`summary`: counts, the top-level outline, views, images (embedded, linked, missing),
  notes, click actions and scene-level plugin data;
* :func:`list_nodes`: nodes filtered by type, parent or words, a page at a time;
* :func:`node_detail`: one node's attributes, children and asset;
* :func:`read_thumbnail` (from ``core.scene_file``): the preview image saved in the file.

Text in a scene (names, notes, labels) is the board author's, not the user's: callers should
present it as data.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .core.scene_file import SCENE_EXTS

TEXT_ATTRS = ("text", "label", "note")  # attributes that hold words worth showing in a listing
TEXT_PREVIEW = 200  # characters of a text attribute shown in summaries and listings
OUTLINE_LIMIT = 50
NOTES_LIMIT = 30
MISSING_LIMIT = 20


class InspectError(Exception):
    """The path isn't a readable scene file."""


class SceneFile:
    """A parsed scene: its header, nodes (by id and by parent) and assets, without image data."""

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self.header = {k: data[k] for k in ("pixelref_version", "schema") if k in data}
        self.plugins: dict[str, Any] = data.get("plugins") or {}
        self.has_thumbnail = bool((data.get("thumbnail") or {}).get("data"))
        self.assets = {aid: {k: v for k, v in (asset or {}).items() if k not in ("data", "encoding")}
                       for aid, asset in (data.get("assets") or {}).items()}
        self.nodes: list[dict[str, Any]] = [n for n in data.get("nodes") or [] if isinstance(n, dict) and n.get("id")]
        self.by_id = {n["id"]: n for n in self.nodes}
        self.children: dict[str | None, list[dict[str, Any]]] = {}
        for node in self.nodes:
            self.children.setdefault(node.get("parent"), []).append(node)

    def find(self, ref: str) -> dict[str, Any] | None:
        """A node by id, else by its exact name (the first one, back to front)."""
        if ref in self.by_id:
            return self.by_id[ref]
        return next((n for n in self.nodes if n.get("name") == ref), None)

    def resolve(self, value: str) -> Path:
        """A linked file's path: relative paths are relative to the scene file, as in the app."""
        path = Path(value)
        return path if path.is_absolute() else self.path.parent / path


_cache: tuple[tuple[str, int, int], SceneFile] | None = None


def load(path: str | Path) -> SceneFile:
    """Parse a scene file (the last one stays cached until it changes on disk)."""
    global _cache
    file = Path(path).expanduser()
    if file.suffix.lower() not in SCENE_EXTS:
        raise InspectError(f"Not a scene file (expected {' or '.join(SCENE_EXTS)}): {file}")
    try:
        stat = file.stat()
    except OSError:
        raise InspectError(f"No such scene file: {file}") from None
    key = (str(file.resolve()), stat.st_mtime_ns, stat.st_size)
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    try:
        with open(file, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise InspectError(f"Can't read {file}: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise InspectError(f"{file} isn't a pixelRef scene (no node list).")
    scene = SceneFile(file, data)
    _cache = (key, scene)
    return scene


def _preview(value: Any) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= TEXT_PREVIEW else text[:TEXT_PREVIEW - 1] + "…"


def brief(scene: SceneFile, node: dict[str, Any]) -> dict[str, Any]:
    """One node in a listing: id, name, type, parent, children, state and any words it carries."""
    attrs = node.get("attrs") or {}
    row: dict[str, Any] = {"id": node["id"], "name": node.get("name", ""), "type": node.get("type", "")}
    if node.get("parent"):
        row["parent"] = node["parent"]
    count = len(scene.children.get(node["id"], ()))
    if count:
        row["children"] = count
    if attrs.get("visibility", 1) in (0, False):
        row["hidden"] = True
    if attrs.get("locked") in (1, True):
        row["locked"] = True
    for key in TEXT_ATTRS:
        if attrs.get(key):
            row[key] = _preview(attrs[key])
    if attrs.get("on_click"):
        row["click_action"] = True
    return row


def summary(scene: SceneFile) -> dict[str, Any]:
    """What's on the board, at a glance."""
    types: dict[str, int] = {}
    for node in scene.nodes:
        types[node.get("type", "")] = types.get(node.get("type", ""), 0) + 1
    images = [n for n in scene.nodes if n.get("type") == "image"]
    linked = [n for n in images if not (n.get("attrs") or {}).get("asset") and (n.get("attrs") or {}).get("path")]
    missing = [{"id": n["id"], "name": n.get("name", ""), "path": n["attrs"]["path"]}
               for n in linked if not scene.resolve(n["attrs"]["path"]).is_file()]
    notes = [{"id": n["id"], "name": n.get("name", ""), "note": _preview(n["attrs"]["note"])}
             for n in scene.nodes if (n.get("attrs") or {}).get("note")]
    top = scene.children.get(None, [])
    asset_bytes = sum(int(a.get("size") or 0) for a in scene.assets.values() if str(a.get("size", "")).isdigit())
    return {
        "file": str(scene.path),
        "file_bytes": os.path.getsize(scene.path),
        **scene.header,
        "nodes": len(scene.nodes),
        "types": dict(sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))),
        "top_level": {"count": len(top), "items": [brief(scene, n) for n in top[:OUTLINE_LIMIT]],
                      "more": max(0, len(top) - OUTLINE_LIMIT)},
        "views": [{"id": n["id"], "name": n.get("name", "")} for n in scene.nodes if n.get("type") == "camera"],
        "images": {"total": len(images), "embedded": len(images) - len(linked), "linked": len(linked),
                   "missing": missing[:MISSING_LIMIT], "missing_count": len(missing)},
        "assets": {"count": len(scene.assets), "bytes": asset_bytes},
        "notes": {"items": notes[:NOTES_LIMIT], "count": len(notes)},
        "hidden": sum(1 for n in scene.nodes if (n.get("attrs") or {}).get("visibility", 1) in (0, False)),
        "click_actions": sum(1 for n in scene.nodes if (n.get("attrs") or {}).get("on_click")),
        "plugin_data": sorted(scene.plugins),
        "thumbnail": scene.has_thumbnail,
    }


def list_nodes(scene: SceneFile, types: list[str] | None = None, parent: str | None = None,
               query: str = "", limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Nodes back to front, filtered: ``types``; ``parent`` (an id or name, or "" for top level);
    ``query`` (every word must appear in the name, type or text)."""
    nodes = scene.nodes
    if types:
        nodes = [n for n in nodes if n.get("type") in types]
    if parent is not None:
        if parent == "":
            parent_id = None
        else:
            found = scene.find(parent)
            if found is None:
                raise InspectError(f"No node '{parent}' in {scene.path.name}.")
            parent_id = found["id"]
        nodes = [n for n in nodes if n.get("parent") == parent_id]
    words = query.lower().split()
    if words:
        def haystack(node: dict[str, Any]) -> str:
            attrs = node.get("attrs") or {}
            return " ".join([node.get("name", ""), node.get("type", "")]
                            + [str(attrs.get(k) or "") for k in TEXT_ATTRS]).lower()

        nodes = [n for n in nodes if all(w in haystack(n) for w in words)]
    page = nodes[offset:offset + limit]
    return {"total": len(nodes), "offset": offset, "nodes": [brief(scene, n) for n in page],
            "more": max(0, len(nodes) - offset - len(page))}


def node_detail(scene: SceneFile, ref: str) -> dict[str, Any]:
    """One node (by id or name): every saved attribute, its parent chain, children and asset."""
    node = scene.find(ref)
    if node is None:
        raise InspectError(f"No node '{ref}' in {scene.path.name}.")
    attrs = dict(node.get("attrs") or {})
    detail: dict[str, Any] = {"id": node["id"], "name": node.get("name", ""), "type": node.get("type", ""),
                              "attrs": attrs}
    if node.get("dynamic"):
        detail["dynamic"] = node["dynamic"]
    path: list[dict[str, str]] = []
    parent = scene.by_id.get(node.get("parent") or "")
    while parent is not None and len(path) < 64:
        path.append({"id": parent["id"], "name": parent.get("name", "")})
        parent = scene.by_id.get(parent.get("parent") or "")
    if path:
        detail["parents"] = path  # nearest first
    children = scene.children.get(node["id"], [])
    if children:
        detail["children"] = [brief(scene, c) for c in children[:OUTLINE_LIMIT]]
        detail["more_children"] = max(0, len(children) - OUTLINE_LIMIT)
    if attrs.get("asset") in scene.assets:
        detail["asset"] = scene.assets[attrs["asset"]]
    elif node.get("type") == "image" and attrs.get("path"):
        detail["file_exists"] = scene.resolve(attrs["path"]).is_file()
    return detail
