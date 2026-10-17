"""Images: the ``image`` node type, its item, and getting images onto the board.

An image node shows an embedded asset (``asset``, stored in the scene file)
or a linked file (``path``). New images are embedded unless the
image_storage plugin's "Embed New Images" setting is off.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pixelref.plugin_api import CAP_CONTENT, Attr, CommandError, NodeType

SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
MIME_EXTENSIONS = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
    "image/tiff": ".tif",
}
ARRANGE_GAP = 8.0

IMAGE_TYPE = NodeType(
    name="image",
    inherits="canvas_node",
    label="Image",
    capabilities=frozenset({CAP_CONTENT}),
    attrs=(
        Attr("asset", "asset", "", group="Image", doc="Embedded image id (scene file 'assets'); wins over path"),
        Attr("path", "path", "", group="Image", doc="Linked image file, used when asset is empty"),
        Attr("crop", "rect", None, group="Image", doc="[x, y, w, h] in source pixels, None = uncropped"),
    ),
)


def headless_size(session: Any, attrs: dict[str, Any]) -> tuple[int, int] | None:
    from pixelref.core.imagesize import file_image_size, image_size

    asset_id = str(attrs.get("asset", "") or "")
    if asset_id:
        asset = session.scene.get_asset(asset_id)
        return image_size(asset.data) if asset is not None else None
    path = str(attrs.get("path", "") or "")
    return file_image_size(session.resolve_path(path)) if path else None


class ImageApi:
    """Creating images (shared by the headless commands and the UI)."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.session = ctx.session
        self.sizer = lambda attrs: headless_size(self.session, attrs)

    def embed_new(self) -> bool:
        storage = self.session.services.get("image_storage")
        return bool(storage.embed_new) if storage is not None else True

    def source_from_file(self, path: Path) -> dict[str, Any]:
        """Image attrs for a file: embedded by default, or linked by path."""
        if self.embed_new():
            try:
                data = Path(path).read_bytes()
            except OSError:
                return {"path": self.session.to_scene_path(path), "asset": ""}
            return {"asset": self.session.scene.add_asset(data, name=Path(path).name), "path": ""}
        return {"path": self.session.to_scene_path(path), "asset": ""}

    def create(self, source: dict[str, Any], name_hint: str, x: float, y: float, centered: bool = True) -> str:
        scene = self.session.scene
        name = scene.unique_name(name_hint or "Image")
        if centered:
            size = self.sizer(source)
            if size is not None:
                x -= size[0] / 2.0
                y -= size[1] / 2.0
        attrs = {**source, "translate": [round(x, 3), round(y, 3)], "scale": [1.0, 1.0], "rotate": 0.0}
        scene.create_node("image", name, attrs=attrs)
        return name

    def create_from_path(self, path: Path, x: float, y: float, centered: bool = True) -> str:
        return self.create(self.source_from_file(path), Path(path).stem or "Image", x, y, centered)

    def create_from_bytes(self, data: bytes, name_hint: str, x: float, y: float, filename: str = "") -> str:
        asset_id = self.session.scene.add_asset(data, name=filename)
        return self.create({"asset": asset_id, "path": ""}, name_hint, x, y)

    def create_row(self, paths: list[Path], x: float, y: float) -> list[str]:
        """Images side by side, centred on (x, y)."""
        sources = [self.source_from_file(p) for p in paths]
        sizes = [self.sizer(s) or (300, 200) for s in sources]
        total = sum(size[0] for size in sizes) + ARRANGE_GAP * max(0, len(paths) - 1)
        left = x - total / 2.0
        names = []
        for path, source, size in zip(paths, sources, sizes):
            names.append(self.create(source, Path(path).stem or "Image", left + size[0] / 2.0, y))
            left += size[0] + ARRANGE_GAP
        return names


def activate(ctx: Any) -> None:
    session = ctx.session

    def local_bounds(sess: Any, node: Any) -> list[float] | None:
        crop = node.attrs.get("crop")
        if crop and len(crop) == 4:
            return [float(v) for v in crop]
        size = headless_size(sess, node.attrs)
        return [0.0, 0.0, float(size[0]), float(size[1])] if size else [0.0, 0.0, 300.0, 200.0]

    IMAGE_TYPE.compute = {"local_bounds": local_bounds}
    ctx.register_node_type(IMAGE_TYPE)
    api = ImageApi(ctx)
    ctx.provide_service("images", api)

    def import_image(path: str, translate: Any = None, name: str | None = None, embed: bool | None = None,
                     centered: bool = True) -> str:
        """Add an image file (embedded unless embed=False). Returns the node name."""
        file = session.resolve_path(path)
        if not file.is_file():
            raise CommandError(f"No such image file: {path}")
        x, y = (float(translate[0]), float(translate[1])) if translate is not None else (0.0, 0.0)
        if embed is None:
            source = api.source_from_file(file)
        elif embed:
            source = {"asset": session.scene.add_asset(file.read_bytes(), name=file.name), "path": ""}
        else:
            source = {"path": session.to_scene_path(file), "asset": ""}
        created = api.create(source, name or file.stem, x, y, centered)
        session.selection.set([created])
        return created

    def import_images(paths: list[str], translate: Any = None) -> list[str]:
        """Add several image files side by side, centred on ``translate``."""
        files = [session.resolve_path(p) for p in paths]
        missing = [str(f) for f in files if not f.is_file()]
        if missing:
            raise CommandError(f"No such image file: {missing[0]}")
        x, y = (float(translate[0]), float(translate[1])) if translate is not None else (0.0, 0.0)
        names = api.create_row(files, x, y)
        session.selection.set(names)
        return names

    def toggle_grayscale(nodes: Any = None) -> int:
        """Grayscale on/off for the selected images (on unless they're all grey already).

        Grayscale is an effect: this adds or removes a grayscale effect (effects plugin)."""
        names = session.selection.names if nodes is None else [
            session.scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        images = [n for n in names if session.scene.get_node(n).node_type == "image"]
        if not images:
            raise CommandError("Select image(s) to toggle grayscale")
        if not session.commands.exists("effects.toggle_type"):
            raise CommandError("Grayscale needs the Effects plugin (Window → Plugins…)")
        return session.run("effects.toggle_type", "grayscale", images)

    ctx.command("import_image", import_image, alias="import_image", label="Insert image")
    ctx.command("import_images", import_images, alias="import_images", label="Insert images")
    ctx.command("toggle_grayscale", toggle_grayscale, alias="toggle_grayscale", label="Toggle grayscale")


def activate_ui(ui: Any) -> None:
    from .ui import activate_image_ui

    activate_image_ui(ui)


