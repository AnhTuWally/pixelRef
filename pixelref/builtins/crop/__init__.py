"""Cropping images: a modal tool working on one image at a time."""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CommandError, crop as crop_geom, differs


def activate(ctx: Any) -> None:
    session = ctx.session
    scene = session.scene

    def images(nodes: Any) -> list[str]:
        names = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        return [n for n in names if scene.get_node(n).node_type == "image"]

    def set_crop(node: str, rect: Any = None) -> None:
        """Crop an image to [x, y, w, h], [[x, y], ...] points, or {'contours': [...]} in source pixels (None = uncropped)."""
        name = scene.resolve(node)
        value = crop_geom.validate_crop(rect)
        if differs(scene.get_node(name).attrs.get("crop"), value):
            scene.set_attr(name, "crop", value)

    def knife_crop(node: str, start: Any, end: Any, flip: bool = False) -> Any:
        """Slice an image's crop along line start -> end ([x, y] in source pixels)."""
        name = scene.resolve(node)
        nd = scene.get_node(name)
        if nd.node_type != "image":
            raise CommandError(f"'{name}' is not an image")
        current = nd.attrs.get("crop")
        contours = crop_geom.crop_contours(current)
        full_size: tuple[float, float] | None = None
        images_svc = session.services.get("images")
        if images_svc is not None:
            sz = images_svc.sizer(nd.attrs)
            if sz is not None:
                full_size = (float(sz[0]), float(sz[1]))
        if not contours:
            w, h = full_size if full_size is not None else (300.0, 200.0)
            contours = [{"points": crop_geom.rect_to_points(0.0, 0.0, w, h), "subtract": False}]
        ax, ay = float(start[0]), float(start[1])
        bx, by = float(end[0]), float(end[1])
        cut, _ = crop_geom.knife_cut_contours(contours, ax, ay, bx, by, flip=bool(flip))
        value = crop_geom.serialize_contours(cut, full_size=full_size)
        if differs(current, value):
            scene.set_attr(name, "crop", value)
        return value

    def reset_crop(nodes: Any = None) -> list[str]:
        """Remove the crop of the selected images."""
        names = [n for n in images(nodes) if scene.get_node(n).attrs.get("crop")]
        if not names:
            raise CommandError("No cropped image selected")
        for name in names:
            scene.set_attr(name, "crop", None)
        return names

    ctx.command("set", set_crop, alias="set_crop", label="Crop")
    ctx.command("knife", knife_crop, alias="knife_crop", label="Knife crop")
    ctx.command("reset", reset_crop, alias="reset_crop", label="Reset crop")


def activate_ui(ui: Any) -> None:
    from .ui import activate_crop_ui

    activate_crop_ui(ui)
