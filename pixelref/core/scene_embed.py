"""A scene carried inside a PNG file (the "editable PNG").

The payload is a ``.pxref`` document (the one scene format, written and read by
``SceneParser``) of the exported part of the board, with the images it needs,
zlib-compressed and stored in the PNG's private ``pxRF`` chunk (``png_meta``).

Everything read back is untrusted: sizes are capped while decompressing, the
document is parsed into a scratch scene, and nodes of types this install does
not provide are dropped. Nothing in the payload is executed.
"""

from __future__ import annotations

import zlib

from . import png_meta
from .errors import SceneError
from .scene import SceneGraph
from .scene_file import SceneParser

CHUNK = b"pxRF"
MAGIC = b"PXR1"  # payload version: bumped if the layout around the .pxref text ever changes
MAX_COMPRESSED = 256 * 1024 * 1024
MAX_JSON = 768 * 1024 * 1024
MAX_NODES = 50_000
MAX_DEPTH = 64
MAX_ASSETS = 4096
MAX_ASSET_BYTES = 512 * 1024 * 1024


class EmbedError(SceneError):
    """The embedded scene is damaged, unsupported or over a limit."""


def encode(subset: SceneGraph) -> bytes:
    """The chunk payload for a scene (normally made by ``extract_subset``)."""
    text = SceneParser().dumps(subset)
    payload = MAGIC + zlib.compress(text.encode("utf-8"), 6)
    if len(payload) > MAX_COMPRESSED:
        raise EmbedError(f"The scene is too large to embed ({len(payload) // 2**20} MB; limit "
                         f"{MAX_COMPRESSED // 2**20} MB). Export without embedded scene data instead.")
    return payload


def embed_in_png(png: bytes, subset: SceneGraph) -> bytes:
    try:
        return png_meta.insert_chunk(png, CHUNK, encode(subset))
    except png_meta.PngError as exc:
        raise EmbedError(f"Could not embed the scene: {exc}") from exc


def has_embedded_scene(path: str) -> bool:
    """Whether the file is a PNG with an embedded scene (reads only chunk headers)."""
    return png_meta.chunk_size(path, CHUNK) is not None


def read_png_scene(path: str, types) -> SceneGraph | None:  # noqa: ANN001
    """The scene embedded in a PNG, validated, or None for a file without one.

    Raises EmbedError when there is one but it cannot be used.
    """
    try:
        payload = png_meta.read_chunk(path, CHUNK, MAX_COMPRESSED + len(MAGIC))
    except png_meta.PngError as exc:
        raise EmbedError(f"Damaged embedded scene: {exc}") from exc
    if payload is None:
        return None
    return decode(payload, types)


def decode(payload: bytes, types) -> SceneGraph:  # noqa: ANN001
    """Parse and validate a chunk payload into a scratch scene (nothing else is touched)."""
    if not payload.startswith(MAGIC):
        raise EmbedError("Unsupported embedded scene version")
    text = _inflate(payload[len(MAGIC):])
    scene = SceneGraph(types=types)
    try:
        SceneParser().load_into_scene(scene, text)
    except RecursionError as exc:
        raise EmbedError("Invalid embedded scene: nested too deeply") from exc
    except Exception as exc:  # noqa: BLE001  (untrusted input: whatever goes wrong, the board stays untouched)
        raise EmbedError(f"Invalid embedded scene: {exc}") from exc
    _validate(scene)
    return scene


def _inflate(data: bytes) -> str:
    inflater = zlib.decompressobj()
    out = bytearray()
    try:
        chunk = inflater.decompress(data, MAX_JSON + 1)
        out += chunk
        while inflater.unconsumed_tail and len(out) <= MAX_JSON:
            out += inflater.decompress(inflater.unconsumed_tail, MAX_JSON + 1 - len(out))
        if len(out) > MAX_JSON:
            raise EmbedError(f"Embedded scene expands beyond {MAX_JSON // 2**20} MB")
        if not inflater.eof:
            raise EmbedError("Truncated embedded scene")
        return out.decode("utf-8")
    except (zlib.error, UnicodeDecodeError) as exc:
        raise EmbedError(f"Damaged embedded scene: {exc}") from exc


def _validate(scene: SceneGraph) -> None:
    """Enforce the limits and drop what this install cannot make safely."""
    if len(scene.nodes) > MAX_NODES:
        raise EmbedError(f"Embedded scene has {len(scene.nodes):,} nodes (limit {MAX_NODES:,})")
    if len(scene.assets) > MAX_ASSETS or sum(a.size for a in scene.assets.values()) > MAX_ASSET_BYTES:
        raise EmbedError("Embedded scene has too many or too large images")
    depth: dict[str, int] = {}
    for node in scene.iter_nodes():
        depth[node.name] = depth[node.parent] + 1 if node.parent else 1
        if depth[node.name] > MAX_DEPTH:
            raise EmbedError(f"Embedded scene is nested deeper than {MAX_DEPTH} levels")
    unknown = [n.name for n in scene.iter_nodes()
               if (spec := scene.types.get(n.node_type)) is None or spec.abstract]
    for name in unknown:
        if scene.has(name):  # already gone with an unsupported ancestor
            scene.delete_node(name)
