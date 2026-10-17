"""Links: connectors between elements, so the board shows what relates to what.

Each end of a ``link`` node is either **attached** to an element (``source`` /
``target`` hold its node id, so renaming never breaks it and the end follows
the element wherever it moves, inside groups too) or **free** (an empty id and
a point, ``source_point`` / ``target_point``, in the link's own space).

Where an attached end meets its element is set by its anchor: ``auto`` (the
side facing the path), ``center``, a side, or ``fixed`` (a spot on the
element's edge, ``source_spot`` / ``target_spot``, as fractions of its width
and height, so it stays put when the element moves or scales).

The path can be pinned with **waypoints** (link space), routed straight,
curved or as elbows, and styled: colour, thickness, dash, a cap at each end,
a label. A link whose attached element is deleted is removed in the same
undo step.
"""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CAP_COLORABLE, CAP_ERASABLE, Attr, CommandError, NodeType, xform

LINK = "link"
ENDS = ("source", "target")
ROUTINGS = ("straight", "curved", "elbow")
ANCHORS = ("auto", "center", "top", "right", "bottom", "left", "fixed")
CAPS = ("none", "arrow", "triangle", "circle", "diamond", "bar")
LINE_STYLES = ("solid", "dash", "dot", "dashdot")


def _bounds(session: Any, node: Any) -> list[float]:
    """Local bounds from the waypoints, free ends and attached ends' centres
    (headless estimate; the canvas knows the exact path)."""
    points = [list(p) for p in node.attrs.get("waypoints") or []]
    inverse = None
    for key in ENDS:
        name = session.scene.name_of_id(str(node.attrs.get(key) or ""))
        if not node.attrs.get(key):
            points.append(list(node.attrs.get(f"{key}_point") or [0.0, 0.0]))
            continue
        rect = session.world_bounds(name) if name else None
        if rect is None:
            continue
        if inverse is None:
            inverse = xform.world_matrix(session.scene, node.name).inverted()
            if inverse is None:
                break
        center = inverse.map(rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0)
        points.append([center[0], center[1]])
    if not points:
        return [0.0, 0.0, 1.0, 1.0]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs), min(ys), max(max(xs) - min(xs), 1.0), max(max(ys) - min(ys), 1.0)]


def _end_attrs(end: str) -> tuple[Attr, ...]:
    label = "start" if end == "source" else "end"
    return (
        Attr(end, "node_ref", "", hidden=True, doc=f"The element the link's {label} is attached to (empty: free)"),
        Attr(f"{end}_point", "float2", [0.0, 0.0], hidden=True, doc=f"Where a free {label} is (link space)"),
        Attr(f"{end}_anchor", "enum", "auto", choices=ANCHORS, group="Path",
             doc=f"Where the {label} meets its element (auto: the side facing the path; "
                 "fixed: the spot it was dropped on)"),
        Attr(f"{end}_spot", "float2", [0.5, 0.5], hidden=True,
             doc="With a fixed anchor: the spot on the element, as fractions of its width and height"),
    )


LINK_TYPE = NodeType(
    name=LINK,
    inherits="canvas_node",
    label="Link",
    color_attr="color",
    capabilities=frozenset({CAP_ERASABLE, CAP_COLORABLE}),
    compute={"local_bounds": _bounds},
    attrs=(
        *_end_attrs("source"),
        *_end_attrs("target"),
        Attr("waypoints", "points", [], hidden=True, doc="Points the path passes through (link space)"),
        Attr("routing", "enum", "curved", choices=ROUTINGS, group="Path"),
        Attr("gap", "float", 6.0, minimum=0.0, maximum=200.0, group="Path",
             doc="Space between an element's edge and the link's end"),
        Attr("color", "color", [110, 190, 255], group="Stroke"),
        Attr("thickness", "float", 2.0, minimum=0.5, maximum=64.0, soft_min=0.5, soft_max=20.0, step=0.5, precision=1,
             unit="px", group="Stroke"),
        Attr("stroke_style", "enum", "solid", choices=LINE_STYLES, group="Stroke"),
        Attr("start_cap", "enum", "none", choices=CAPS, group="Ends"),
        Attr("end_cap", "enum", "triangle", choices=CAPS, group="Ends"),
        Attr("cap_size", "float", 1.0, minimum=0.2, maximum=6.0, group="Ends", doc="Scales both caps"),
        Attr("label", "string", "", group="Label", doc="Text shown halfway along the link"),
        Attr("label_size", "float", 14.0, minimum=4.0, maximum=200.0, group="Label"),
    ),
    quick_attrs=("color", "thickness", "stroke_style", "routing", "end_cap"),
    doc="A connector between elements (or free points)",
)


def _is_point(value: Any) -> bool:
    return (isinstance(value, (list, tuple)) and len(value) == 2
            and all(isinstance(v, (int, float)) for v in value))


def activate(ctx: Any) -> None:
    ctx.register_node_type(LINK_TYPE)
    session = ctx.session
    scene = session.scene

    def node_id(ref: str) -> str:
        name = scene.resolve(ref)
        node = scene.get_node(name)
        if node.node_type == LINK:
            raise CommandError("Links connect elements, not other links")
        if not session.types.is_canvas(node.node_type):
            raise CommandError(f"'{name}' isn't on the canvas")
        return node.id

    def links_of(name: str) -> list[str]:
        node_id_ = scene.get_node(name).id
        return [n.name for n in scene.iter_nodes() if n.node_type == LINK
                and node_id_ in (n.attrs.get("source"), n.attrs.get("target"))]

    def link_names(nodes: Any) -> list[str]:
        names = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        found = [n for n in names if scene.get_node(n).node_type == LINK]
        if not found:
            raise CommandError("Select a link")
        return found

    def end_values(end: str, ref: Any) -> dict[str, Any]:
        """Attributes for one end: an element (name or id) or a free point [x, y]."""
        if ref is None or _is_point(ref):
            point = [round(float(ref[0]), 3), round(float(ref[1]), 3)] if ref is not None else [0.0, 0.0]
            return {end: "", f"{end}_point": point}
        return {end: node_id(str(ref))}

    def connect(source: Any, target: Any, name: str = "Link", **attrs: Any) -> str:
        """Link two ends. Each is an element (name or id) or a free point [x, y] on the
        board. Style with attrs: routing, color, end_cap, target_anchor="fixed",
        target_spot=[1, 0.5]…"""
        values = {**end_values("source", source), **end_values("target", target)}
        if values["source"] and values["source"] == values["target"]:
            raise CommandError("A link needs two different elements")
        final = scene.unique_name(name)
        scene.create_node(LINK, final, attrs={**values, **attrs})
        session.selection.set([final])
        return final

    def set_end(link: str, end: str = "target", node: Any = None, anchor: str | None = None,
                spot: Any = None) -> None:
        """Attach a link's end ("source" or "target") to an element, or free it at a
        point [x, y] (link space). ``anchor``/``spot`` say where it meets the element."""
        name = link_names(link)[0]
        if end not in ENDS:
            raise CommandError("end must be 'source' or 'target'")
        values = end_values(end, node)
        other = scene.get_node(name).attrs.get("target" if end == "source" else "source")
        if values[end] and values[end] == other:
            raise CommandError("A link needs two different elements")
        if anchor is not None:
            values[f"{end}_anchor"] = anchor
        if spot is not None:
            values[f"{end}_spot"] = [float(spot[0]), float(spot[1])]
        scene.set_attrs(name, values)

    def connect_selection(**attrs: Any) -> list[str]:
        """Link the selected elements in the order they were selected (a → b → c)."""
        names = [n for n in session.selection.names if scene.get_node(n).node_type != LINK]
        if len(names) < 2:
            raise CommandError("Select two or more elements to link")
        made = []
        for a, b in zip(names, names[1:]):
            made.append(connect(a, b, **attrs))
        session.selection.set(made)
        return made

    def add_waypoint(link: str, x: float, y: float, index: int | None = None) -> int:
        """Pin the path through (x, y) in the link's space; returns the waypoint's index."""
        name = link_names(link)[0]
        points = [list(p) for p in scene.get_attr(name, "waypoints") or []]
        index = len(points) if index is None else max(0, min(int(index), len(points)))
        points.insert(index, [round(float(x), 3), round(float(y), 3)])
        scene.set_attr(name, "waypoints", points)
        return index

    def remove_waypoint(link: str, index: int) -> None:
        """Unpin one waypoint."""
        name = link_names(link)[0]
        points = [list(p) for p in scene.get_attr(name, "waypoints") or []]
        if not 0 <= int(index) < len(points):
            raise CommandError(f"'{name}' has no waypoint {index}")
        del points[int(index)]
        scene.set_attr(name, "waypoints", points)

    def clear_waypoints(nodes: Any = None) -> None:
        """Remove every waypoint: the path goes straight (or curves) from end to end again."""
        for name in link_names(nodes):
            scene.set_attr(name, "waypoints", [])

    def reverse(nodes: Any = None) -> None:
        """Swap a link's ends (with their anchors and caps), keeping the path."""
        for name in link_names(nodes):
            a = scene.get_node(name).attrs
            values = {"start_cap": a.get("end_cap", "none"), "end_cap": a.get("start_cap", "none"),
                      "waypoints": list(reversed(a.get("waypoints") or []))}
            for suffix, default in (("", ""), ("_point", [0.0, 0.0]), ("_anchor", "auto"), ("_spot", [0.5, 0.5])):
                values[f"source{suffix}"] = a.get(f"target{suffix}", default)
                values[f"target{suffix}"] = a.get(f"source{suffix}", default)
            scene.set_attrs(name, values)

    def ends(link: str) -> list[str | None]:
        """The names of the elements a link's ends are attached to (None: a free end)."""
        node = scene.get_node(link_names(link)[0])
        return [scene.name_of_id(str(node.attrs.get(end) or "")) if node.attrs.get(end) else None
                for end in ENDS]

    def connected(nodes: Any = None) -> list[str]:
        """Elements linked to these (default: the selection)."""
        names = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        found: list[str] = []
        for name in names:
            if scene.get_node(name).node_type == LINK:
                found.extend(n for n in ends(name) if n)
                continue
            for link in links_of(name):
                found.extend(n for n in ends(link) if n and n != name)
        return list(dict.fromkeys(found))

    def select_connected(nodes: Any = None) -> list[str]:
        """Select the elements linked to the selection."""
        found = connected(nodes)
        if not found:
            raise CommandError("Nothing is linked to the selection")
        session.selection.set(found)
        return found

    def prune_dangling() -> None:
        """A link whose attached element was deleted goes too (free ends are fine)."""
        for node in list(scene.nodes.values()):
            if node.node_type != LINK or not scene.has(node.name):
                continue
            if any(node.attrs.get(end) and not scene.name_of_id(str(node.attrs[end])) for end in ENDS):
                scene.delete_node(node.name)
                session.selection.remove([node.name])

    ctx.command("connect", connect, alias="connect", label="Link elements")
    ctx.command("set_end", set_end, label="Reconnect link")
    ctx.command("connect_selection", connect_selection, alias="connect_selection", label="Link selection")
    ctx.command("add_waypoint", add_waypoint, label="Add waypoint")
    ctx.command("remove_waypoint", remove_waypoint, label="Remove waypoint")
    ctx.command("clear_waypoints", clear_waypoints, label="Straighten link")
    ctx.command("reverse", reverse, label="Reverse link")
    ctx.command("ends", ends, undoable=False, echo=False)
    ctx.command("links_of", lambda node: links_of(scene.resolve(node)), undoable=False, echo=False)
    ctx.command("connected", connected, undoable=False, echo=False)
    ctx.command("select_connected", select_connected, alias="select_connected", undoable=False)
    ctx.track(session.commands.on_tidy(prune_dangling))


def activate_ui(ui: Any) -> None:
    from .ui import activate_links_ui

    activate_links_ui(ui)
