"""The Extract Color Palette action: image context menu and the command search."""

from __future__ import annotations

from typing import Any


def activate_color_palette_ui(ui: Any) -> None:
    session = ui.session

    ui.action("color_palette.extract", "color_palette.extract", "Extract Color Palette",
              keywords="dominant swatches scheme", category="Image")

    def single_image() -> bool:
        names = session.selection.names
        if len(names) != 1:
            return False
        scene = session.scene
        return any(scene.get_node(n).node_type == "image" for n in [names[0], *scene.descendants(names[0])])

    ui.menu("canvas", "color_palette.extract", path="Image", group="image", order=30, when=single_image)
