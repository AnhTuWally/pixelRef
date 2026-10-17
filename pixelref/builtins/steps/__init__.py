"""Steps: numbered ``annotation_step`` markers and the Step tool.

Each new marker takes the next number (one past the highest on the board);
``steps.renumber`` closes the gaps after some are deleted.
"""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CAP_COLORABLE, CAP_CONTENT, CAP_ERASABLE, Attr, NodeType

LABEL_STYLES = ("number", "upper", "lower", "roman")
SHAPES = ("circle", "rounded", "square")

ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
         (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))


def label_for(number: int, style: str) -> str:
    """The text a marker shows: 3 → "3", "C", "c" or "III"."""
    number = int(number)
    if style in ("upper", "lower") and number > 0:
        letters = ""
        while number > 0:  # 1 → A, 26 → Z, 27 → AA
            number, rest = divmod(number - 1, 26)
            letters = chr(ord("A") + rest) + letters
        return letters if style == "upper" else letters.lower()
    if style == "roman" and 0 < number < 4000:
        text = ""
        for value, numeral in ROMAN:
            while number >= value:
                text += numeral
                number -= value
        return text
    return str(number)


def _bounds(_session: Any, node: Any) -> list[float]:
    radius = float(node.attrs.get("radius", 16.0))
    tail = node.attrs.get("tail", [0.0, 0.0])
    left, top = min(-radius, float(tail[0])), min(-radius, float(tail[1]))
    right, bottom = max(radius, float(tail[0])), max(radius, float(tail[1]))
    return [left, top, right - left, bottom - top]


STEP_TYPE = NodeType(
    name="annotation_step",
    inherits="canvas_node",
    label="Step",
    color_attr="fill_color",
    capabilities=frozenset({CAP_CONTENT, CAP_ERASABLE, CAP_COLORABLE}),
    compute={"local_bounds": _bounds},
    attrs=(
        Attr("number", "int", 1, minimum=0, maximum=99999, group="Step"),
        Attr("label_style", "enum", "number", choices=LABEL_STYLES, group="Step", label="Style",
             doc="1 2 3, A B C, a b c or I II III"),
        Attr("radius", "float", 16.0, minimum=4.0, maximum=1000.0, soft_min=6.0, soft_max=80.0, step=1.0,
             precision=1, unit="px", group="Shape", label="Size"),
        Attr("shape", "enum", "circle", choices=SHAPES, group="Shape"),
        Attr("tail", "float2", [0.0, 0.0], group="Shape", doc="Where the pointer tail ends, from the centre"),
        Attr("fill_color", "color", [235, 64, 52], group="Look", label="Fill"),
        Attr("text_color", "color", [255, 255, 255], group="Look", label="Text"),
        Attr("border_color", "color", [255, 255, 255], group="Look", label="Border"),
        Attr("border", "float", 2.0, minimum=0.0, soft_min=0.0, soft_max=12.0, step=0.5, precision=1, unit="px",
             group="Look", label="Border width", doc="0 = no border"),
    ),
    quick_attrs=("number", "fill_color", "text_color", "radius", "label_style"),
)

STYLE_ATTRS = ("label_style", "radius", "shape", "fill_color", "text_color", "border_color", "border")


def activate(ctx: Any) -> None:
    ctx.register_node_type(STEP_TYPE)
    session = ctx.session

    def step_nodes() -> list[Any]:
        return [n for n in session.scene.iter_nodes() if n.node_type == "annotation_step"]

    def next_number() -> int:
        """The number the next step gets: one past the highest on the board."""
        return max((int(n.attrs.get("number", 0)) for n in step_nodes()), default=0) + 1

    def create_step(x: float, y: float, number: int | None = None, name: str = "Step", **attrs: Any) -> str:
        """Place a step marker centred on (x, y); ``number`` defaults to the next one.
        Extra keyword arguments are attributes (fill_color, radius, tail…)."""
        final = session.scene.unique_name(name)
        values = {"translate": [round(float(x), 3), round(float(y), 3)],
                  "number": next_number() if number is None else int(number), **attrs}
        session.scene.create_node("annotation_step", final, attrs=values)
        session.selection.set([final])
        return final

    def renumber(names: list[str] | None = None, start: int = 1) -> int:
        """Number the steps (``names``, or all) start, start + 1… in their current order; returns how many."""
        nodes = step_nodes() if names is None else [session.scene.get_node(n) for n in names]
        nodes = [n for n in nodes if n.node_type == "annotation_step"]
        order = sorted(range(len(nodes)), key=lambda i: (int(nodes[i].attrs.get("number", 0)), i))
        for offset, index in enumerate(order):
            if int(nodes[index].attrs.get("number", 0)) != start + offset:
                session.scene.set_attr(nodes[index].name, "number", start + offset)
        return len(nodes)

    ctx.command("create", create_step, alias="create_step", label="Place Step")
    ctx.command("next_number", next_number, undoable=False, echo=False)
    ctx.command("renumber", renumber, label="Renumber Steps")


def activate_ui(ui: Any) -> None:
    from .ui import activate_steps_ui

    activate_steps_ui(ui)
