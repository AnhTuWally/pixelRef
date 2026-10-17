"""Pen strokes and drawings.

Choosing the pen starts a drawing session: every stroke until the tool
changes goes into one ``annotation_drawing`` node (strokes are its
``annotation_path`` children), which then moves, scales and rotates as one
object. Double-clicking a drawing (or Shift+P) re-opens it for drawing.
"""

from __future__ import annotations

import math
from typing import Any

from pixelref.plugin_api import (
    CAP_COLORABLE,
    CAP_COMPOUND,
    CAP_CONTENT,
    CAP_ERASABLE,
    Attr,
    CommandError,
    NodeType,
    xform,
)

LINE_STYLES = ("solid", "dash", "dot", "dashdot", "none")
STROKE = "annotation_path"
DRAWING = "annotation_drawing"


def _stroke_bounds(_session: Any, node: Any) -> list[float]:
    points = node.attrs.get("points") or [[0.0, 0.0]]
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return [min(xs), min(ys), max(max(xs) - min(xs), 1.0), max(max(ys) - min(ys), 1.0)]


STROKE_TYPE = NodeType(
    name=STROKE,
    inherits="canvas_node",
    label="Pen stroke",
    color_attr="color",
    capabilities=frozenset({CAP_CONTENT, CAP_ERASABLE, CAP_COLORABLE}),
    compute={"local_bounds": _stroke_bounds},
    attrs=(
        Attr("points", "points", [], hidden=True, group="Stroke", doc="Freehand samples [[x, y], …] in local space"),
        Attr("color", "color", [255, 64, 64], group="Stroke"),
        Attr("thickness", "float", 4.0, minimum=0.1, soft_min=0.5, soft_max=40.0, step=0.5, precision=1, unit="px",
             group="Stroke"),
        Attr("stroke_style", "enum", "solid", choices=LINE_STYLES, group="Stroke"),
        Attr("smoothing", "float", 0.0, minimum=0.0, maximum=1.0, group="Stroke",
             doc="0..1, applied when drawing so it can be changed later"),
        Attr("arrow", "flag", 0, group="Stroke"),
        Attr("arrow_start", "flag", 0, group="Stroke"),
    ),
    quick_attrs=("color", "thickness", "stroke_style", "opacity"),
)

DRAWING_TYPE = NodeType(
    name=DRAWING,
    inherits="canvas_node",
    label="Drawing",
    color_attr="color",
    capabilities=frozenset({CAP_CONTENT, CAP_COMPOUND, CAP_COLORABLE}),
    doc="A pen session: its strokes are its children",
)


def simplify(points: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    """Ramer–Douglas–Peucker: drop samples within ``epsilon`` of the line between neighbours."""
    if len(points) < 3 or epsilon <= 0:
        return list(points)

    def distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return math.hypot(p[0] - a[0], p[1] - a[1])
        return abs(dy * p[0] - dx * p[1] + b[0] * a[1] - b[1] * a[0]) / length

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        best, index = 0.0, -1
        for i in range(first + 1, last):
            d = distance(points[i], points[first], points[last])
            if d > best:
                best, index = d, i
        if index >= 0 and best > epsilon:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, k in zip(points, keep) if k]


def activate(ctx: Any) -> None:
    ctx.register_node_type(STROKE_TYPE)
    ctx.register_node_type(DRAWING_TYPE)
    session = ctx.session
    scene = session.scene

    def strokes_of(drawing: str) -> list[str]:
        return [c for c in scene.children_of(drawing) if scene.get_node(c).node_type == STROKE]

    def add_stroke(points: list[Any], drawing: str | None = None, color: Any = None, thickness: float = 4.0,
                   smoothing: float = 0.5, arrow: int = 0, arrow_start: int = 0, tolerance: float = 0.6) -> list[str]:
        """Add a freehand stroke (world points) to a drawing (a new one if None).

        Returns [drawing, stroke]. ``thickness`` is in world units; inside a
        scaled drawing the stored width compensates so it looks the same.
        """
        if not points:
            raise CommandError("A stroke needs at least one point")
        target = scene.resolve(drawing) if drawing and scene.has(drawing) else None
        to_local = xform.IDENTITY
        unit_scale = 1.0
        if target is None:
            target = scene.unique_name("Drawing")
            scene.create_node(DRAWING, target)
        else:
            world = xform.world_matrix(scene, target)
            inverse = world.inverted()
            if inverse is not None:
                to_local = inverse
            unit_scale = max(1e-6, math.hypot(world.m11, world.m12))
        local = [to_local.map(float(p[0]), float(p[1])) for p in points]
        local = simplify(local, tolerance / unit_scale)
        left = min(p[0] for p in local)
        top = min(p[1] for p in local)
        stroke = scene.unique_name("Stroke")
        attrs = {
            "translate": [round(left, 2), round(top, 2)],
            "points": [[round(p[0] - left, 2), round(p[1] - top, 2)] for p in local],
            "color": list(color) if color is not None else [255, 64, 64],
            "thickness": round(float(thickness) / unit_scale, 3),
            "smoothing": round(float(smoothing), 2),
            "arrow": int(bool(arrow)),
            "arrow_start": int(bool(arrow_start)),
        }
        scene.create_node(STROKE, stroke, parent=target, attrs=attrs)
        return [target, stroke]

    def combinable(names: list[str]) -> list[str] | None:
        """Strokes (front-most first) if the names are only drawings/strokes."""
        if not names:
            return None
        strokes: list[str] = []
        for name in names:
            node_type = scene.get_node(name).node_type
            if node_type == DRAWING:
                strokes.extend(reversed(scene.children_of(name)))
            elif node_type == STROKE:
                strokes.append(name)
            else:
                return None
        return strokes

    def combine(nodes: Any = None) -> str:
        """Merge drawings and loose strokes into one drawing (Ctrl+J)."""
        selected = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        names = scene.top_level(selected)
        strokes = combinable(names)
        if not strokes or len(names) < 2:
            raise CommandError("Select two or more drawings or pen strokes to combine")
        # Keep the overall stacking: strokes in on-screen order, front first.
        order = {n.name: i for i, n in enumerate(reversed(list(scene.iter_nodes())))}
        strokes.sort(key=lambda n: order.get(n, 0))
        parents = {scene.get_node(n).parent for n in names}
        parent = parents.pop() if len(parents) == 1 else None
        drawing = scene.unique_name("Drawing")
        siblings = list(reversed(scene.children_of(parent)))
        front_most = min((siblings.index(n) for n in names if n in siblings), default=0)
        scene.create_node(DRAWING, drawing, parent=parent)
        restacked = [n for n in siblings if n not in names]
        restacked.insert(min(front_most, len(restacked)), drawing)
        scene.restack(list(reversed(restacked)))
        ctx.run("move_nodes", strokes, drawing, None, "end")
        session.selection.set([drawing])
        return drawing

    def prune_empty_drawings() -> None:
        """Drawings left without strokes (erased, strokes dragged out) disappear."""
        for node in list(scene.nodes.values()):
            if node.node_type == DRAWING and not node.children and scene.has(node.name):
                scene.delete_node(node.name)
                session.selection.remove([node.name])

    ctx.command("add_stroke", add_stroke, alias="add_stroke", label="Draw stroke")
    ctx.command("combine", combine, alias="combine_drawings",
                label=lambda nodes=None: "Combine into drawing")
    ctx.track(session.commands.on_tidy(prune_empty_drawings))
    ctx.strokes_of = strokes_of  # type: ignore[attr-defined]
    ctx.combinable = combinable  # type: ignore[attr-defined]

    from pixelref.plugin_api.contracts import UngroupHandler

    ctx.contribute("grouping.ungroup", UngroupHandler(
        DRAWING, "Separate Strokes",
        done_message=lambda n: f"Separated into {n} stroke(s) · Ctrl+J combines them again"))


def activate_ui(ui: Any) -> None:
    from .ui import activate_pen_ui

    activate_pen_ui(ui)
