"""Image storage: embedded (one self-contained .pxref) or linked by path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pixelref.plugin_api import CommandError

MIME_EXTENSIONS = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
    "image/tiff": ".tif",
}


def asset_file_name(node_name: str, asset: Any) -> str:
    if asset.name:
        return asset.name
    return f"{node_name}{MIME_EXTENSIONS.get(asset.mime, '.png')}"


def free_file_name(path: Path) -> Path:
    candidate, counter = path, 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        counter += 1
    return candidate


class StorageService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx

    @property
    def embed_new(self) -> bool:
        return bool(self.ctx.settings.value("embed_images", True, type=bool))

    @embed_new.setter
    def embed_new(self, value: bool) -> None:
        self.ctx.settings.setValue("embed_images", bool(value))


def activate(ctx: Any) -> None:
    session = ctx.session
    scene = session.scene
    storage = StorageService(ctx)
    ctx.provide_service("image_storage", storage)

    def images(nodes: Any) -> list[str]:
        names = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        return [n for n in names if scene.get_node(n).node_type == "image"]

    def embed(nodes: Any = None) -> int:
        """Copy linked image files into the scene file. Returns how many were embedded."""
        embedded, missing = 0, []
        for name in images(nodes):
            attrs = scene.get_node(name).attrs
            if attrs.get("asset") or not attrs.get("path"):
                continue
            path = session.resolve_path(str(attrs["path"]))
            try:
                data = path.read_bytes()
            except OSError:
                missing.append(name)
                continue
            scene.set_attrs(name, {"asset": scene.add_asset(data, name=path.name), "path": ""})
            embedded += 1
        note = f" · {len(missing)} missing file(s) could not be embedded" if missing else ""
        session.events.emit("ui.status", {"text": f"Embedded {embedded} image(s) in the scene{note}"})
        return embedded

    def embed_all() -> int:
        """Embed every linked image: the scene becomes one self-contained file."""
        return embed([n.name for n in scene.iter_nodes() if n.node_type == "image"])

    def link(node: str, path: str) -> None:
        """Show a file on disk (by path) instead of embedded data."""
        name = scene.resolve(node)
        if scene.get_node(name).node_type != "image":
            raise CommandError(f"'{name}' is not an image")
        scene.set_attrs(name, {"path": session.to_scene_path(Path(path)), "asset": ""})

    def extract(node: str, path: str) -> str:
        """Write an embedded image to ``path`` and link to that file."""
        name = scene.resolve(node)
        asset = scene.get_asset(str(scene.get_node(name).attrs.get("asset", "") or ""))
        if asset is None:
            raise CommandError(f"'{name}' has no embedded image")
        Path(path).write_bytes(asset.data)
        scene.set_attrs(name, {"path": session.to_scene_path(Path(path)), "asset": ""})
        return str(path)

    def set_embed_new(on: bool) -> bool:
        """Whether new images are embedded (True) or linked by path (False); remembered."""
        storage.embed_new = bool(on)
        mode = "embedded in the scene file" if on else "linked by file path"
        session.events.emit("ui.status", {"text": f"New images will be {mode}"})
        return storage.embed_new

    def prepare_paths(sess: Any, scene_path: Path) -> None:
        """Make linked paths portable for a save: stored relative to the scene when possible."""
        new_dir = Path(scene_path).parent.resolve()
        for node in list(scene.iter_nodes()):
            attrs = node.attrs
            if node.node_type != "image" or attrs.get("asset") or not attrs.get("path"):
                continue
            source = sess.resolve_path(str(attrs["path"]))
            try:
                value = source.resolve().relative_to(new_dir).as_posix()
            except (ValueError, OSError):
                value = source.as_posix()
            if value != attrs["path"]:
                scene.set_attr(node.name, "path", value)

    ctx.command("embed", embed, alias="embed_images", label="Embed images")
    ctx.command("embed_all", embed_all, alias="embed_all_images", label="Embed images")
    ctx.command("link", link, alias="link_image", label="Link image to file")
    ctx.command("extract", extract, alias="extract_image", label="Save image as file")
    ctx.command("set_embed_new", set_embed_new, alias="set_embed_new_images", undoable=False)
    ctx.contribute("scene.before_save", prepare_paths)


def activate_ui(ui: Any) -> None:
    from .ui import activate_storage_ui

    activate_storage_ui(ui)
