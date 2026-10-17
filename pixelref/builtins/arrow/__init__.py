"""Arrows and lines: ``annotation_line`` nodes and the Arrow tool."""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CAP_COLORABLE, CAP_CONTENT, CAP_ERASABLE, Attr, NodeType

LINE_STYLES = ("solid", "dash", "dot", "dashdot", "none")


def _bounds(_session: Any, node: Any) -> list[float]:
    start = node.attrs.get("start", [0.0, 0.0])
    end = node.attrs.get("end", [100.0, 0.0])
    left, top = min(start[0], end[0]), min(start[1], end[1])
    return [float(left), float(top), float(abs(end[0] - start[0])) or 1.0, float(abs(end[1] - start[1])) or 1.0]


LINE_TYPE = NodeType(
    name="annotation_line",
    inherits="canvas_node",
    label="Arrow / line",
    color_attr="color",
    capabilities=frozenset({CAP_CONTENT, CAP_ERASABLE, CAP_COLORABLE}),
    compute={"local_bounds": _bounds},
    attrs=(
        Attr("start", "float2", [0.0, 0.0], group="Line"),
        Attr("end", "float2", [100.0, 0.0], group="Line"),
        Attr("color", "color", [255, 0, 0], group="Stroke"),
        Attr("thickness", "float", 2.0, minimum=0.5, soft_min=0.5, soft_max=40.0, step=0.5, precision=1, unit="px",
             group="Stroke"),
        Attr("stroke_style", "enum", "solid", choices=LINE_STYLES, group="Stroke"),
        Attr("arrow", "flag", 0, group="Line", doc="Arrowhead at the end point"),
        Attr("arrow_start", "flag", 0, group="Line", doc="Arrowhead at the start point"),
    ),
    quick_attrs=("color", "thickness", "stroke_style", "arrow_start", "arrow"),
)


def activate(ctx: Any) -> None:
    ctx.register_node_type(LINE_TYPE)
    session = ctx.session

    def create_line(x1: float, y1: float, x2: float, y2: float, name: str = "Arrow", arrow: int = 1,
                    arrow_start: int = 0, **attrs: Any) -> str:
        """Create a line from (x1, y1) to (x2, y2); an arrowhead at the end by default."""
        final = session.scene.unique_name(name)
        values = {
            "translate": [round(float(x1), 3), round(float(y1), 3)],
            "start": [0.0, 0.0],
            "end": [round(float(x2) - float(x1), 3), round(float(y2) - float(y1), 3)],
            "arrow": int(bool(arrow)), "arrow_start": int(bool(arrow_start)), **attrs,
        }
        session.scene.create_node("annotation_line", final, attrs=values)
        session.selection.set([final])
        return final

    ctx.command("create", create_line, alias="create_arrow", label="Draw Arrow")


def activate_ui(ui: Any) -> None:
    from .ui import activate_arrow_ui

    activate_arrow_ui(ui)
