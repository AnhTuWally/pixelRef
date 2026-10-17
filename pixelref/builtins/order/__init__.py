"""Stacking order: actions and menus over the kernel's ``reorder`` command."""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CommandError


def activate(ctx: Any) -> None:
    session = ctx.session

    def restack_selection(mode: str = "front") -> list[str]:
        """front / forward / backward / back for the selected items (within each parent)."""
        names = session.scene.top_level(session.selection.names)
        if not names:
            raise CommandError("Nothing selected")
        ctx.run("reorder", names, mode)
        return names

    ctx.command("selection", restack_selection, alias="restack_selection", label=lambda mode="front": {
        "front": "Bring to front", "back": "Send to back", "forward": "Bring forward",
        "backward": "Send backward"}.get(mode, "Reorder"))


def activate_ui(ui: Any) -> None:
    for key, text, keys, keywords in (
        ("front", "Bring to Front", ("]",), "top raise order z"),
        ("forward", "Bring Forward", ("}", "Shift+]"), "raise order z"),
        ("backward", "Send Backward", ("{", "Shift+["), "lower order z"),
        ("back", "Send to Back", ("[",), "bottom lower order z"),
    ):
        ui.action(f"order.{key}", "order.selection", text, args=(key,), keywords=keywords, category="Arrange")
        for sequence in keys:
            ui.bind(sequence, f"order.{key}")
    for index, key in enumerate(("front", "forward", "backward", "back")):
        ui.menu("canvas", f"order.{key}", path="Order", group="order", order=index, when="selection.count > 0")
    ui.place_submenu("canvas", "Order", "arrange", 10, icon="order")
