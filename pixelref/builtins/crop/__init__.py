"""Cropping images: a modal tool working on one image at a time."""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CommandError, differs


def activate(ctx: Any) -> None:
    session = ctx.session
    scene = session.scene

    def images(nodes: Any) -> list[str]:
        names = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        return [n for n in names if scene.get_node(n).node_type == "image"]

    def set_crop(node: str, rect: Any) -> None:
        """Crop an image to [x, y, w, h] source pixels (None = uncropped)."""
        name = scene.resolve(node)
        value = None if rect is None else [float(v) for v in rect]
        if differs(scene.get_node(name).attrs.get("crop"), value):
            scene.set_attr(name, "crop", value)

    def reset_crop(nodes: Any = None) -> list[str]:
        """Remove the crop of the selected images."""
        names = [n for n in images(nodes) if scene.get_node(n).attrs.get("crop")]
        if not names:
            raise CommandError("No cropped image selected")
        for name in names:
            scene.set_attr(name, "crop", None)
        return names

    ctx.command("set", set_crop, alias="set_crop", label="Crop")
    ctx.command("reset", reset_crop, alias="reset_crop", label="Reset crop")


def activate_ui(ui: Any) -> None:
    from .ui import activate_crop_ui

    activate_crop_ui(ui)
