"""The colour picker tool."""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CommandError


def activate(ctx: Any) -> None:
    session = ctx.session
    scene = session.scene

    def apply_color(color: Any, nodes: Any = None) -> list[str]:
        """Recolor text, shapes, strokes and drawings ([r, g, b] or "#rrggbb")."""
        if isinstance(color, str):
            text = color.strip().lstrip("#")
            if len(text) == 3:
                text = "".join(c * 2 for c in text)  # "#f80" shorthand
            try:
                if len(text) != 6:
                    raise ValueError(text)
                color = [int(text[i:i + 2], 16) for i in (0, 2, 4)]
            except ValueError:
                raise CommandError(f"Not a colour: {color!r} (use \"#rrggbb\" or [r, g, b])") from None
        rgb = [int(c) for c in list(color)[:3]]
        names = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        targets = [n for n in names if session.types.has_capability(scene.get_node(n).node_type, "colorable")]
        if not targets:
            raise CommandError("Select text, shapes or drawings to recolor")
        for name in targets:
            node = scene.get_node(name)
            if session.types.has_capability(node.node_type, "compound"):
                for child in node.children:
                    child_node = scene.get_node(child)
                    attr = session.types.color_attr(child_node.node_type)
                    if attr:
                        scene.set_attr(child, attr, rgb)
                continue
            attr = session.types.color_attr(node.node_type)
            if attr:
                scene.set_attr(name, attr, rgb)
        return targets

    ctx.command("apply_color", apply_color, alias="apply_color", label="Apply color")


def activate_ui(ui: Any) -> None:
    from .ui import activate_picker_ui

    activate_picker_ui(ui)
