"""Blur and pixelate: ``annotation_blur`` / ``annotation_pixelate`` regions and their tools.

A region draws whatever is stacked below it, blurred or as coarse blocks; it
has no pixels of its own, so moving it (or what's under it) re-renders it.
"""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CAP_CONTENT, CAP_ERASABLE, Attr, NodeType


def _bounds(_session: Any, node: Any) -> list[float]:
    return [0.0, 0.0, *[float(v) for v in node.attrs.get("size", [120.0, 80.0])]]


SIZE = Attr("size", "float2", [120.0, 80.0], group="Shape")
RADIUS = Attr("radius", "float", 12.0, minimum=1.0, maximum=500.0, soft_min=1.0, soft_max=60.0, step=1.0, precision=1,
              unit="px", group="Blur", label="Strength", doc="Blur radius")
CELL_SIZE = Attr("cell_size", "float", 10.0, minimum=2.0, maximum=500.0, soft_min=2.0, soft_max=60.0, step=1.0,
                 precision=1, unit="px", group="Pixelate", label="Pixel size", doc="Size of the blocks")

BLUR_TYPE = NodeType(
    name="annotation_blur",
    inherits="canvas_node",
    label="Blur",
    capabilities=frozenset({CAP_CONTENT, CAP_ERASABLE}),
    compute={"local_bounds": _bounds},
    attrs=(SIZE, RADIUS),
    quick_attrs=("radius",),
)

PIXELATE_TYPE = NodeType(
    name="annotation_pixelate",
    inherits="canvas_node",
    label="Pixelate",
    capabilities=frozenset({CAP_CONTENT, CAP_ERASABLE}),
    compute={"local_bounds": _bounds},
    attrs=(SIZE, CELL_SIZE),
    quick_attrs=("cell_size",),
)


def activate(ctx: Any) -> None:
    ctx.register_node_type(BLUR_TYPE)
    ctx.register_node_type(PIXELATE_TYPE)
    session = ctx.session

    def create(node_type: str, x: float, y: float, width: float, height: float, name: str, attrs: dict) -> str:
        final = session.scene.unique_name(name)
        values = {"translate": [round(float(x), 3), round(float(y), 3)],
                  "size": [round(abs(float(width)), 3), round(abs(float(height)), 3)], **attrs}
        session.scene.create_node(node_type, final, attrs=values)
        session.selection.set([final])
        return final

    def create_blur(x: float, y: float, width: float, height: float, name: str = "Blur", **attrs: Any) -> str:
        """Create a blur region; extra keyword arguments are attributes (radius…)."""
        return create("annotation_blur", x, y, width, height, name, attrs)

    def create_pixelate(x: float, y: float, width: float, height: float, name: str = "Pixelate",
                        **attrs: Any) -> str:
        """Create a pixelate region; extra keyword arguments are attributes (cell_size…)."""
        return create("annotation_pixelate", x, y, width, height, name, attrs)

    ctx.command("blur", create_blur, alias="create_blur", label="Draw Blur")
    ctx.command("pixelate", create_pixelate, alias="create_pixelate", label="Draw Pixelate")


def activate_ui(ui: Any) -> None:
    from .ui import activate_redact_ui

    activate_redact_ui(ui)
