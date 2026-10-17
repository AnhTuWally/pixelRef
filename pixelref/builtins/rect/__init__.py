"""Rectangles and ellipses: ``annotation_rect`` nodes (``shape`` picks the outline),
the Rect tool and the Ellipse tool."""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CAP_COLORABLE, CAP_CONTENT, CAP_ERASABLE, Attr, NodeType

LINE_STYLES = ("solid", "dash", "dot", "dashdot", "none")
SHAPES = ("rect", "ellipse")
FILL_STYLES = ("solid", "dense", "sparse", "diagonal", "cross", "horizontal", "vertical", "grid", "none")

RECT_TYPE = NodeType(
    name="annotation_rect",
    inherits="canvas_node",
    label="Rectangle",
    color_attr="stroke_color",
    capabilities=frozenset({CAP_CONTENT, CAP_ERASABLE, CAP_COLORABLE}),
    compute={"local_bounds": lambda _s, node: [0.0, 0.0, *[float(v) for v in node.attrs.get("size", [120.0, 80.0])]]},
    attrs=(
        Attr("size", "float2", [120.0, 80.0], group="Shape"),
        Attr("shape", "enum", "rect", choices=SHAPES, group="Shape", doc="rect, or the ellipse inside the box"),
        Attr("stroke_color", "color", [255, 0, 0], group="Stroke"),
        Attr("fill_color", "color", [0, 0, 0], group="Fill"),
        Attr("fill_opacity", "float", 0.0, minimum=0.0, maximum=1.0, group="Fill", label="Fill alpha",
             step=0.05, precision=0, unit="%", display_scale=100.0),
        Attr("fill_style", "enum", "solid", choices=FILL_STYLES, group="Fill"),
        Attr("thickness", "float", 2.0, minimum=0.5, soft_min=0.5, soft_max=40.0, step=0.5, precision=1, unit="px",
             group="Stroke"),
        Attr("stroke_style", "enum", "solid", choices=LINE_STYLES, group="Stroke"),
        Attr("corner_radius", "float", 0.0, minimum=0.0, soft_min=0.0, soft_max=100.0, step=1.0, precision=1,
             unit="px", group="Shape"),
    ),
    quick_attrs=("stroke_color", "thickness", "stroke_style", "fill_color", "fill_opacity", "corner_radius"),
)


def activate(ctx: Any) -> None:
    ctx.register_node_type(RECT_TYPE)
    session = ctx.session

    def create_rect(x: float, y: float, width: float, height: float, name: str = "Rect", **attrs: Any) -> str:
        """Create a rectangle; extra keyword arguments are attributes (stroke_color, thickness…)."""
        final = session.scene.unique_name(name)
        values = {"translate": [round(float(x), 3), round(float(y), 3)],
                  "size": [round(abs(float(width)), 3), round(abs(float(height)), 3)], **attrs}
        session.scene.create_node("annotation_rect", final, attrs=values)
        session.selection.set([final])
        return final

    def create_ellipse(x: float, y: float, width: float, height: float, name: str = "Ellipse", **attrs: Any) -> str:
        """Create an ellipse in the box at (x, y); extra keyword arguments are attributes."""
        return create_rect(x, y, width, height, name, **{**attrs, "shape": "ellipse"})

    ctx.command("create", create_rect, alias="create_rect", label="Draw Rect")
    ctx.command("create_ellipse", create_ellipse, alias="create_ellipse", label="Draw Ellipse")


def activate_ui(ui: Any) -> None:
    from .ui import activate_rect_ui

    activate_rect_ui(ui)


