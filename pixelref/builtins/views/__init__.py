"""Views: what each canvas view looks at and how it shows it.

* ``camera`` nodes are colour-coded view markers (centre, zoom, rotation) saved with the
  scene; every view sees and can use all of them. Each view remembers its *current* marker
  (the one last looked through, made or updated), which "Update" overwrites.
* ``display_preset`` nodes (and the built-in presets) set a view's filters,
  greyscale, background override, overlays and framing guide.
* Each canvas tile's header has a camera menu and a preset menu.
* The built-in workspaces (and workspace templates): Board, Review and Present.
"""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import Attr, NodeType

FRAME_GUIDES = ("none", "16:9", "2.39:1", "1:1", "4:3", "3:2")

BUILTIN_PRESETS: dict[str, dict[str, Any]] = {
    "All": {"grayscale": False, "show_types": [], "hide_types": [], "show_tags": [], "hide_tags": [],
            "show_overlays": True, "show_grid": True, "background": None, "frame_guide": "none"},
    "Value check": {"grayscale": True},
    "Images only": {"show_types": ["image"], "hide_types": []},
    "Annotations only": {"hide_types": ["image"], "show_types": []},
    "Clean": {"show_overlays": False, "show_grid": False},
}

# New markers take the first of these no other marker uses (then cycle), so each is easy to tell apart.
MARKER_COLORS: tuple[tuple[str, list[int]], ...] = (
    ("Blue", [96, 178, 255]), ("Orange", [255, 160, 60]), ("Green", [120, 210, 90]),
    ("Pink", [255, 105, 160]), ("Purple", [180, 140, 255]), ("Teal", [70, 215, 200]),
    ("Yellow", [245, 215, 70]), ("Red", [240, 85, 85]),
)


def next_marker_color(scene: Any) -> list[int]:
    used = [list(n.attrs.get("color") or []) for n in scene.iter_nodes() if n.node_type == "camera"]
    for _name, rgb in MARKER_COLORS:
        if rgb not in used:
            return list(rgb)
    return list(MARKER_COLORS[len(used) % len(MARKER_COLORS)][1])


CAMERA_TYPE = NodeType(
    name="camera",
    inherits="canvas_node",
    label="View marker",
    attrs=(
        Attr("color", "color", [96, 178, 255], group="View marker", doc="Marker colour (canvas dot and menus)"),
        Attr("center", "float2", [0.0, 0.0], group="View marker", doc="World point at the centre of the view"),
        Attr("zoom", "float", 1.0, minimum=0.01, maximum=64.0, group="View marker"),
        Attr("rotation", "float", 0.0, group="View marker", doc="View rotation in degrees"),
        Attr("flip_x", "bool", False, group="View marker", doc="Mirror the view horizontally"),
        Attr("flip_y", "bool", False, group="View marker", doc="Mirror the view vertically"),
    ),
    doc="A saved view marker: where a view looks and how it is mirrored",
)

PRESET_TYPE = NodeType(
    name="display_preset",
    inherits="dg_node",
    label="Display preset",
    attrs=(
        Attr("show_types", "string_list", [], group="Filter", doc="Only these node types (empty = all)"),
        Attr("hide_types", "string_list", [], group="Filter"),
        Attr("show_tags", "string_list", [], group="Filter", doc="Only nodes with one of these tags"),
        Attr("hide_tags", "string_list", [], group="Filter"),
        Attr("isolate", "node_refs", [], group="Filter", doc="Only these nodes (and their children)"),
        Attr("grayscale", "bool", False, group="Display"),
        Attr("background", "color", None, group="Display", doc="Override the scene background"),
        Attr("show_grid", "bool", True, group="Display"),
        Attr("show_overlays", "bool", True, group="Display", doc="Handles, outlines, banners"),
        Attr("frame_guide", "enum", "none", choices=FRAME_GUIDES, group="Display"),
    ),
    doc="How a view shows the board",
)


def activate(ctx: Any) -> None:
    ctx.register_node_type(CAMERA_TYPE)
    ctx.register_node_type(PRESET_TYPE)
    session = ctx.session

    def create_display_preset(name: str = "Preset", **settings: Any) -> str:
        """Save display settings as a preset node (usable from every view)."""
        final = session.scene.unique_name(name)
        session.scene.create_node("display_preset", final, attrs=settings)
        return final

    ctx.command("create_display_preset", create_display_preset, alias="create_display_preset",
                label="New display preset")


def activate_ui(ui: Any) -> None:
    from .ui import activate_views_ui

    activate_views_ui(ui)


