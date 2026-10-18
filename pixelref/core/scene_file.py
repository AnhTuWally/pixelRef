"""The ``.pxref`` scene file format (readable JSON).

Files look like this (schema 1)::

    {
      "pixelref_version": "1.0.0",
      "schema": 1,
      "types": {"image": 1, ...},           # schema version of each node type used
      "plugins": {"canvas_background": {...}},  # scene-level plugin data
      "nodes": [                            # parents first, back to front
        {"id": "...", "name": "...", "type": "image", "parent": "<parent id>",
         "attrs": {...}, "dynamic": {"note": {"type": "string"}}}
      ],
      "workspaces": {...},                  # only when "save layout with scene" is on
      "assets": {"<sha1>": {"name": ..., "mime": ..., "size": ..., "encoding": "base64", "data": ...}}
    }

Sibling order in the node list is the draw order. Node types carry their own
schema version and may upgrade their attributes on load (``NodeType.upgrade``).
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
from pathlib import Path
from typing import Any

from . import perf
from .attributes import Attr
from .errors import SceneError
from .scene import APP_VERSION, Asset, SceneGraph

SCHEMA = 1
SCENE_EXT = ".pxref"
SCENE_EXTS = (SCENE_EXT, ".json")  # files the app opens as scenes
_KNOWN_KEYS = {"pixelref_version", "schema", "types", "plugins", "nodes", "assets"}
THUMBNAIL_KEY = "thumbnail"  # {"mime", "width", "height", "encoding": "base64", "data", "updated"}
THUMBNAIL_HEAD_BYTES = 4_000_000


def read_thumbnail(path: str | Path) -> dict[str, Any] | None:
    """A scene file's thumbnail section (or None), read from the start of the file
    only: big boards' embedded images come after it and are never parsed."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(THUMBNAIL_HEAD_BYTES)
    except OSError:
        return None
    text = head.decode("utf-8", errors="ignore")
    key = text.find(f'"{THUMBNAIL_KEY}"')
    nodes = text.find('"nodes"')
    if key < 0 or (0 <= nodes < key):
        return None  # no thumbnail section (a match after "nodes" is some node's text)
    start = text.find("{", text.find(":", key))
    if start < 0:
        return None
    try:
        value, _end = json.JSONDecoder().raw_decode(text, start)
    except ValueError:
        return None
    return value if isinstance(value, dict) and value.get("data") else None


class SceneParser:
    """Reads and writes scene files."""

    def __init__(self) -> None:
        # Undo snapshots' node JSON by node name: (node, its revision, parent id, text).
        self._fragments: dict[str, tuple[Any, int, str | None, str]] = {}
        self._fragments_scene: SceneGraph | None = None
        self._fragments_types = -1

    @staticmethod
    def read_text(path: str | Path) -> str:
        return Path(path).read_text(encoding="utf-8")

    @staticmethod
    def write_text(path: str | Path, text: str) -> None:
        # Write a sibling file and swap it in, so a failed write (disk full…)
        # never destroys the previous version of the scene.
        path = Path(path)
        temp = path.with_name(path.name + ".tmp")
        try:
            temp.write_text(text, encoding="utf-8", newline="\n")
            os.replace(temp, path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    # ---- Writing --------------------------------------------------------------------

    def dumps(self, scene: SceneGraph, include_assets: bool = True) -> str:
        """Serialize the scene.

        With ``include_assets`` (saving to disk) every embedded image the scene
        uses is written base64-encoded after the nodes, so the file is
        self-contained, and the extra file sections are included. Without it
        (undo snapshots) the output is compact and carries no image data.
        """
        if include_assets:
            buffer = io.StringIO()
            self.snapshot(scene).write_to(buffer)
            return buffer.getvalue()
        # Undo snapshots: each node's JSON is kept until the node changes
        # (``SceneGraph.node_revisions``), so a snapshot of a big board costs
        # about as much as the nodes that changed since the last one.
        id_by_name = {name: node.id for name, node in scene.nodes.items()}
        fragments = self._fragments
        if self._fragments_scene is not scene or self._fragments_types != scene.types.generation:
            fragments.clear()  # another scene, or node types changed (dynamic attrs depend on them)
            self._fragments_scene = scene
            self._fragments_types = scene.types.generation
        revisions = scene.node_revisions
        parts: list[str] = []
        used_types: dict[str, int] = {}
        live: set[str] = set()
        for node in scene.iter_nodes():
            name = node.name
            live.add(name)
            parent_id = id_by_name.get(node.parent) if node.parent else None
            revision = revisions.get(name, -1)
            cached = fragments.get(name)
            if cached is not None and cached[0] is node and cached[1] == revision and cached[2] == parent_id:
                parts.append(cached[3])
            else:
                text = json.dumps(self._node_entry(scene, node, parent_id), ensure_ascii=False, separators=(",", ":"))
                fragments[name] = (node, revision, parent_id, text)
                parts.append(text)
            version = self._type_version(scene, node.node_type)
            if version is not None:
                used_types[node.node_type] = version
        if len(fragments) > len(live):
            for name in [n for n in fragments if n not in live]:
                del fragments[name]
        payload = self._head(scene, used_types)
        # Compact output lets json use its C encoder (indenting forces the slow
        # pure-Python path), which matters for undo snapshots of big boards.
        head = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return f'{head[:-1]},"nodes":[{",".join(parts)}]}}'

    def snapshot(self, scene: SceneGraph) -> "SceneSnapshot":
        """Everything a save writes, captured now and cheaply: node attributes are shallow
        copies (values are never edited in place) and image data is shared, not copied.
        ``SceneSnapshot.write`` may then run on another thread while editing goes on."""
        id_by_name = {name: node.id for name, node in scene.nodes.items()}
        nodes: list[dict[str, Any]] = []
        used_types: dict[str, int] = {}
        for node in scene.iter_nodes():
            parent_id = id_by_name.get(node.parent) if node.parent else None
            nodes.append(self._node_entry(scene, node, parent_id))
            version = self._type_version(scene, node.node_type)
            if version is not None:
                used_types[node.node_type] = version
        payload: dict[str, Any] = {"pixelref_version": APP_VERSION, "schema": SCHEMA}
        thumbnail = scene.sections.get(THUMBNAIL_KEY)
        if isinstance(thumbnail, dict):
            # Near the top, so read_thumbnail() finds it without parsing the images.
            payload[THUMBNAIL_KEY] = thumbnail
        payload.update(self._head(scene, used_types))
        payload["nodes"] = nodes
        for key, value in scene.sections.items():
            if key != THUMBNAIL_KEY:
                payload[key] = value
        assets = []
        for asset_id in scene.referenced_assets():
            asset = scene.get_asset(asset_id)
            if asset is not None:
                assets.append((asset_id, asset.name, asset.mime, asset.size, asset.data))
        return SceneSnapshot(payload, assets)

    @staticmethod
    def _node_entry(scene: SceneGraph, node: Any, parent_id: str | None) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "id": node.id,
            "name": node.name,
            "type": node.node_type,
            "parent": parent_id,
            "attrs": dict(node.attrs),
        }
        dynamic = scene.dynamic_attrs(node)
        if dynamic:
            entry["dynamic"] = {name: attr.to_dict() for name, attr in dynamic.items()}
        return entry

    @staticmethod
    def _type_version(scene: SceneGraph, node_type: str) -> int | None:
        spec = scene.types.get(node_type)
        version = spec.schema_version if spec is not None else scene.type_versions.get(node_type)
        return int(version) if version is not None else None

    @staticmethod
    def _head(scene: SceneGraph, used_types: dict[str, int]) -> dict[str, Any]:
        payload: dict[str, Any] = {"pixelref_version": APP_VERSION, "schema": SCHEMA}
        if used_types:
            payload["types"] = dict(sorted(used_types.items()))
        if scene.plugin_data:
            payload["plugins"] = dict(sorted(scene.plugin_data.items()))
        return payload

    # ---- Reading ---------------------------------------------------------------------

    def load_into_scene(self, scene: SceneGraph, text: str, keep_assets: bool = False) -> None:
        """Replace ``scene`` with the parsed text.

        The text is parsed into a scratch scene first so a malformed file never
        leaves the live scene half-loaded. ``keep_assets`` is for undo
        snapshots, which are written without image data.
        """
        staging = SceneGraph(types=scene.types, app_version=scene.app_version)
        self._load_json(staging, text)
        scene.replace_from(staging, keep_assets=keep_assets)

    def _load_json(self, scene: SceneGraph, text: str) -> None:
        try:
            with perf.span("file.parse_json"):
                payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SceneError(f"Not a pixelRef scene file (invalid JSON): {exc}") from exc
        if not isinstance(payload, dict):
            raise SceneError("Invalid scene file: top level must be an object")
        scene.app_version = str(payload.get("pixelref_version", scene.app_version))
        schema = _int(payload.get("schema", SCHEMA), SCHEMA)
        if schema > SCHEMA:
            raise SceneError(f"This scene was saved by a newer pixelRef (file schema {schema}, "
                             f"this version reads {SCHEMA}).")

        assets = payload.get("assets")
        for asset_id, entry in (assets.items() if isinstance(assets, dict) else ()):
            try:
                with perf.span("file.decode_asset"):
                    data = base64.b64decode(entry["data"], validate=True)
            except (KeyError, TypeError, ValueError, binascii.Error) as exc:
                raise SceneError(f"Invalid embedded asset '{asset_id}': {exc}") from exc
            scene.assets[asset_id] = Asset(data=data, mime=str(entry.get("mime", "")), name=str(entry.get("name", "")))

        plugins = payload.get("plugins")
        scene.plugin_data = dict(plugins) if isinstance(plugins, dict) else {}
        scene.sections = {key: value for key, value in payload.items() if key not in _KNOWN_KEYS}

        file_types = payload.get("types") if isinstance(payload.get("types"), dict) else {}
        scene.type_versions = {
            str(k): _int(v, 1) for k, v in file_types.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        raw_nodes = payload.get("nodes", [])
        if not isinstance(raw_nodes, list):
            raise SceneError("Invalid scene file: 'nodes' must be a list")
        self._build(scene, raw_nodes, file_types=file_types)

    # ---- Building ------------------------------------------------------------------------

    def _build(self, scene: SceneGraph, raw_nodes: list[dict[str, Any]], file_types: dict[str, Any]) -> None:
        for raw in raw_nodes:
            if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not isinstance(
                    raw.get("type"), str):
                raise SceneError("Invalid scene file: every node needs a name and a type")
        # The first node with an id keeps it; a repeat (hand-edited file) gets a new id.
        id_owner: dict[str, int] = {}
        for index, raw in enumerate(raw_nodes):
            if isinstance(raw.get("id"), str) and raw["id"]:
                id_owner.setdefault(raw["id"], index)
        uuid_to_name = {node_id: raw_nodes[index]["name"] for node_id, index in id_owner.items()}
        rank: dict[str, int] = {}

        def parent_of(raw: dict[str, Any]) -> str | None:
            parent_ref = raw.get("parent")
            return uuid_to_name.get(parent_ref) if isinstance(parent_ref, str) else None

        def attrs_of(raw: dict[str, Any], node_type: str) -> dict[str, Any]:
            attrs = raw.get("attrs") or {}
            if not isinstance(attrs, dict):
                raise SceneError(f"Invalid attributes for node '{raw.get('name')}'")
            version = _int(file_types.get(node_type, 1), 1)
            spec = scene.types.get(node_type)
            if spec is not None and spec.upgrade is not None and version < spec.schema_version:
                attrs = spec.upgrade(dict(attrs), version)
            return attrs

        # Create parents before children even if the file lists them out of order.
        pending = list(enumerate(raw_nodes))
        while pending:
            deferred = []
            for index, raw in pending:
                parent_name = parent_of(raw)
                if parent_name is not None and parent_name not in scene.nodes:
                    deferred.append((index, raw))
                    continue
                node_type = raw["type"]
                attrs = attrs_of(raw, node_type)
                raw_dynamic = raw.get("dynamic")
                dynamic = {
                    name: Attr.from_dict(name, spec)
                    for name, spec in (raw_dynamic.items() if isinstance(raw_dynamic, dict) else ())
                    if isinstance(spec, dict)
                }
                node_id = raw.get("id")
                if not isinstance(node_id, str) or id_owner.get(node_id) != index:
                    node_id = None
                scene.create_node(
                    node_type=node_type,
                    name=raw["name"],
                    parent=parent_name,
                    attrs=attrs,
                    node_id=node_id,
                    strict=False,
                    dynamic=dynamic,
                )
                rank[raw["name"]] = index
            if len(deferred) == len(pending):
                names = ", ".join(raw.get("name", "?") for _i, raw in deferred)
                raise SceneError(f"Invalid scene file: parent cycle between {names}")
            pending = deferred

        # Draw order: file order (parents listed after their children were created late).
        scene.roots.sort(key=lambda name: rank[name])
        for node in scene.nodes.values():
            node.children.sort(key=lambda name: rank[name])
        scene.emit("scene.loaded", {"node_count": len(scene.nodes)})


class SceneSnapshot:
    """A scene captured for saving (``SceneParser.snapshot``), written by ``write``.

    The images are written straight from their bytes, base64-encoded a piece at
    a time, instead of building the whole file as one string: a board with
    hundreds of megabytes of photos saves faster, and a save on a worker thread
    (autosave) never holds the interpreter long enough to stall the UI.
    """

    CHUNK = 3 * 1024 * 1024  # bytes encoded per step (a multiple of 3: the pieces join seamlessly)

    def __init__(self, payload: dict[str, Any], assets: list[tuple[str, str, str, int, bytes]]) -> None:
        self.payload = payload
        self.assets = assets

    def write(self, path: str | Path) -> None:
        """Write the file (a sibling file swapped in, so a failed write keeps the old one)."""
        path = Path(path)
        temp = path.with_name(path.name + ".tmp")
        try:
            with open(temp, "w", encoding="utf-8", newline="\n") as handle:
                self.write_to(handle)
            os.replace(temp, path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    def write_to(self, handle: Any) -> None:
        text = json.dumps(self.payload, indent=2, ensure_ascii=False)
        if not self.assets:
            handle.write(text + "\n")
            return
        handle.write(text[:-2])  # reopen the top-level object: drop its closing "\n}"
        handle.write(',\n  "assets": {')
        for index, (asset_id, name, mime, size, data) in enumerate(self.assets):
            meta = json.dumps({"name": name, "mime": mime, "size": size, "encoding": "base64"}, ensure_ascii=False)
            handle.write(f'{"," if index else ""}\n    {json.dumps(asset_id)}: {meta[:-1]}, "data": "')
            with perf.span("file.encode_asset"):
                for offset in range(0, len(data), self.CHUNK):
                    handle.write(base64.b64encode(data[offset:offset + self.CHUNK]).decode("ascii"))
            handle.write('"}')
        handle.write("\n  }\n}\n")


def _int(value: Any, default: int) -> int:
    """An integer from file data (schema / type versions), or ``default`` for junk."""
    try:
        return int(value or default)
    except (TypeError, ValueError, OverflowError):
        return default
