"""Per-view display settings: what a canvas view shows and how.

Each canvas view has its own :class:`DisplaySettings` (from a display preset):
type/tag filters, an isolate list, greyscale, a background override, and
which overlays are drawn. Nodes stay in the shared document — a view only
decides whether to paint them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BackgroundSpec:
    color: tuple[int, int, int] = (23, 25, 29)
    pattern: str = "lines"  # lines, dots, crosses, checker, none
    grid_size: int = 64
    pattern_color: tuple[int, int, int] | None = None  # None = derived from the colour


@dataclass
class DisplaySettings:
    preset: str = "All"
    show_types: list[str] = field(default_factory=list)  # empty = every type
    hide_types: list[str] = field(default_factory=list)
    show_tags: list[str] = field(default_factory=list)
    hide_tags: list[str] = field(default_factory=list)
    isolate: list[str] = field(default_factory=list)  # node ids; empty = no isolation
    grayscale: bool = False
    background: tuple[int, int, int] | None = None  # override the scene background
    show_grid: bool = True
    show_overlays: bool = True  # handles, outlines, banners
    show_markers: bool = True  # saved view-marker dots on the canvas
    frame_guide: str = "none"  # none, 16:9, 2.39:1, 1:1, 4:3, 3:2
    # Names hidden in this view, recomputed from the filters when the document changes.
    hidden: set[str] = field(default_factory=set)

    def shows(self, name: str) -> bool:
        return name not in self.hidden

    @property
    def filters_active(self) -> bool:
        return bool(self.show_types or self.hide_types or self.show_tags or self.hide_tags or self.isolate)

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset, "show_types": list(self.show_types), "hide_types": list(self.hide_types),
            "show_tags": list(self.show_tags), "hide_tags": list(self.hide_tags), "isolate": list(self.isolate),
            "grayscale": self.grayscale, "background": list(self.background) if self.background else None,
            "show_grid": self.show_grid, "show_overlays": self.show_overlays, "show_markers": self.show_markers,
            "frame_guide": self.frame_guide,
        }

    def apply(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            if key == "background":
                self.background = tuple(int(v) for v in value[:3]) if value else None
            elif key in ("show_types", "hide_types", "show_tags", "hide_tags", "isolate"):
                setattr(self, key, [str(v) for v in (value or [])])
            elif key in ("grayscale", "show_grid", "show_overlays", "show_markers"):
                setattr(self, key, bool(value))
            elif key in ("preset", "frame_guide"):
                setattr(self, key, str(value))


def compute_hidden(session: Any, display: DisplaySettings) -> set[str]:
    """Names of nodes the view should not paint, given its filters."""
    if not display.filters_active:
        return set()
    scene = session.scene
    types = session.types
    hidden: set[str] = set()
    isolate_names: set[str] = set()
    if display.isolate:
        for node_id in display.isolate:
            name = scene.name_of_id(node_id) or (node_id if scene.has(node_id) else None)
            if name is None:
                continue
            isolate_names.add(name)
            isolate_names.update(scene.descendants(name))
            isolate_names.update(scene.ancestors(name))
    for node in scene.iter_nodes():
        if not types.is_canvas(node.node_type) and node.node_type in types:
            continue
        name = node.name
        if isolate_names and name not in isolate_names:
            hidden.add(name)
            continue
        container = types.has_capability(node.node_type, "container")
        if not container:
            if display.show_types and not any(
                node.node_type == t or types.inherits(node.node_type, t) for t in display.show_types
            ):
                hidden.add(name)
                continue
            if display.hide_types and any(
                node.node_type == t or types.inherits(node.node_type, t) for t in display.hide_types
            ):
                hidden.add(name)
                continue
        tags = set(node.attrs.get("tags") or [])
        if display.show_tags and not container and not tags & set(display.show_tags):
            hidden.add(name)
            continue
        if display.hide_tags and tags & set(display.hide_tags):
            hidden.add(name)
    # Children of hidden nodes are hidden too.
    for name in list(hidden):
        if scene.has(name):
            hidden.update(scene.descendants(name))
    return hidden
