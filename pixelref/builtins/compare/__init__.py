"""Compare Viewer: A/B image comparison with wipe, difference, mix and toggle modes.

Modelled on Nuke and Natron viewers, the Compare Viewer takes two image inputs
and provides real-time split-screen wiping, pixel difference inspection,
onion-skin dissolve, and 1:1 pixel alignment.
"""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CAP_COLORABLE, CAP_CONTENT, CAP_ERASABLE, Attr, CommandError, NodeType

COMPARE_TYPE_NAME = "compare_viewer"
MODES = ("wipe", "difference", "mix", "toggle")
FIT_MODES = ("fit", "one_to_one", "fill", "stretch")
ACTIVE_CHOICES = ("a", "b")
LINK_COLOR_A = [100, 180, 255]
LINK_COLOR_B = [255, 160, 80]


DEFAULT_FRAME = (512.0, 512.0)


def input_sizes(session: Any, attrs: dict[str, Any]) -> list[tuple[float, float]]:
    """Content sizes (w, h) of the connected inputs, A first; missing inputs are skipped."""
    sizes: list[tuple[float, float]] = []
    for key in ("input_a", "input_b"):
        ref_id = str(attrs.get(key) or "")
        name = session.scene.name_of_id(ref_id) if ref_id else None
        if not name:
            continue
        bounds = session.local_bounds(name)
        if bounds and len(bounds) == 4 and bounds[2] > 0 and bounds[3] > 0:
            sizes.append((float(bounds[2]), float(bounds[3])))
    return sizes


def frame_size(sizes: list[tuple[float, float]], fit_mode: str) -> tuple[float, float]:
    """The viewer frame both images are placed in.

    1:1 shows both at native pixels, so the frame spans the larger width and height.
    Otherwise the frame is the larger image (by area) and the other is scaled to
    match it, so differing resolutions line up instead of one sitting inside the other.
    """
    if not sizes:
        return DEFAULT_FRAME
    if fit_mode == "one_to_one":
        return max(w for w, _h in sizes), max(h for _w, h in sizes)
    return max(sizes, key=lambda s: s[0] * s[1])


def _compute_local_bounds(session: Any, node: Any) -> list[float]:
    w, h = frame_size(input_sizes(session, node.attrs), str(node.attrs.get("fit_mode", "fit")))
    return [0.0, 0.0, w, h]


COMPARE_TYPE = NodeType(
    name=COMPARE_TYPE_NAME,
    inherits="canvas_node",
    label="Compare Viewer",
    color_attr="divider_color",
    capabilities=frozenset({CAP_CONTENT, CAP_ERASABLE, CAP_COLORABLE}),
    quick_attrs=("mode", "active_input", "wipe_pos", "wipe_angle", "mix", "fit_mode"),
    compute={"local_bounds": _compute_local_bounds},
    attrs=(
        Attr("input_a", "node_ref", "", group="Inputs", doc="Node ID of Image A (Master/Hero)"),
        Attr("input_b", "node_ref", "", group="Inputs", doc="Node ID of Image B"),
        Attr("mode", "enum", "wipe", choices=MODES, group="Compare", doc="Comparison composite mode"),
        Attr("wipe_pos", "float", 0.5, minimum=0.0, maximum=1.0, soft_min=0.0, soft_max=1.0,
             step=0.01, display_scale=100.0, unit="%", group="Wipe", doc="Divider position along the wipe axis"),
        Attr("wipe_angle", "float", 0.0, minimum=-180.0, maximum=180.0, step=1.0, unit="°",
             group="Wipe", doc="Angle of the wipe divider line in degrees"),
        Attr("mix", "float", 0.5, minimum=0.0, maximum=1.0, step=0.01, display_scale=100.0,
             unit="%", group="Mix", doc="Blend mix factor between A and B"),
        Attr("active_input", "enum", "a", choices=ACTIVE_CHOICES, group="Toggle",
             doc="Active image when in toggle mode"),
        Attr("fit_mode", "enum", "fit", choices=FIT_MODES, group="Alignment",
             doc="How both images are placed in the viewer frame (the larger image's size; 1:1 = native pixels)"),
        Attr("offset_b", "float2", [0.0, 0.0], group="Alignment",
             doc="Manual pixel offset [dx, dy] for Image B relative to A"),
        Attr("diff_gain", "float", 1.0, minimum=1.0, maximum=20.0, step=0.5, group="Compare",
             doc="Gain multiplier for difference mode to amplify subtle changes"),
        Attr("divider_color", "color", [255, 255, 255], group="Look", doc="Wipe divider line color"),
        Attr("divider_width", "float", 2.0, minimum=0.5, maximum=20.0, group="Look",
             doc="Divider line width in pixels"),
    ),
    doc="An A/B image comparison viewer node with interactive wipe, difference, mix, and toggle modes.",
)


def activate(ctx: Any) -> None:
    ctx.register_node_type(COMPARE_TYPE)
    session = ctx.session
    scene = session.scene

    def create(x: float = 0.0, y: float = 0.0, input_a: str = "", input_b: str = "",
               name: str = "CompareViewer", **attrs: Any) -> str:
        """Create a compare viewer node on the canvas."""
        final_name = scene.unique_name(name)
        node_attrs: dict[str, Any] = {
            "translate": [round(float(x), 3), round(float(y), 3)],
            **attrs,
        }
        if input_a:
            node_a = scene.name_of_id(input_a) if input_a in scene.by_id else scene.resolve(input_a)
            node_attrs["input_a"] = scene.get_node(node_a).id if node_a else input_a
        if input_b:
            node_b = scene.name_of_id(input_b) if input_b in scene.by_id else scene.resolve(input_b)
            node_attrs["input_b"] = scene.get_node(node_b).id if node_b else input_b

        scene.create_node(COMPARE_TYPE_NAME, final_name, attrs=node_attrs)
        if input_a or input_b:
            link_inputs(final_name)
        session.selection.set([final_name])
        return final_name

    def compare_nodes(node_a: str, node_b: str, name: str = "CompareViewer",
                      link_cables: bool = True, **attrs: Any) -> str:
        """Create a compare viewer comparing two nodes, placing it adjacent and linking them."""
        name_a = scene.resolve(node_a)
        name_b = scene.resolve(node_b)
        obj_a = scene.get_node(name_a)
        obj_b = scene.get_node(name_b)

        # Place the compare viewer to the right of both images
        bounds_a = session.world_bounds(name_a) or [0.0, 0.0, 512.0, 512.0]
        bounds_b = session.world_bounds(name_b) or [0.0, 0.0, 512.0, 512.0]

        max_x = max(bounds_a[0] + bounds_a[2], bounds_b[0] + bounds_b[2])
        min_y = min(bounds_a[1], bounds_b[1])
        viewer_x = max_x + 60.0
        viewer_y = min_y

        viewer_name = create(
            x=viewer_x,
            y=viewer_y,
            input_a=obj_a.id,
            input_b=obj_b.id,
            name=name,
            **attrs,
        )

        if link_cables:
            link_inputs(viewer_name)

        session.selection.set([viewer_name])
        return viewer_name

    def link_inputs(viewer_name: str) -> None:
        """Draw a cable from each of the viewer's A/B sources to it (skipping ones that exist)."""
        if not session.commands.exists("links.connect"):
            return
        viewer = scene.get_node(viewer_name)
        for key, color in (("input_a", LINK_COLOR_A), ("input_b", LINK_COLOR_B)):
            source_id = str(viewer.attrs.get(key) or "")
            source = scene.name_of_id(source_id) if source_id else None
            if not source:
                continue
            exists = any(n.node_type == "link" and n.attrs.get("source") == source_id
                         and n.attrs.get("target") == viewer.id for n in scene.iter_nodes())
            if not exists:
                ctx.run("links.connect", source, viewer_name, routing="elbow", color=color)
        session.selection.set([viewer_name])  # links.connect selects the new cable

    def compare_selected() -> str:
        """Compare the two selected items in a new compare viewer."""
        names = [n for n in session.selection.names if scene.get_node(n).node_type != "link"]
        if len(names) < 2:
            raise CommandError("Select at least two images to compare (Input A and Input B)")
        return compare_nodes(names[0], names[1])

    def toggle_active(node: str | None = None) -> str:
        """Toggle active_input between 'a' and 'b' on the given or selected compare viewer."""
        target = node or (session.selection.names[0] if session.selection.names else None)
        if not target:
            raise CommandError("No compare viewer selected")
        target_name = scene.resolve(target)
        n = scene.get_node(target_name)
        if n.node_type != COMPARE_TYPE_NAME:
            raise CommandError(f"Node '{target_name}' is not a compare viewer")
        cur = n.attrs.get("active_input", "a")
        nxt = "b" if cur == "a" else "a"
        scene.set_attr(target_name, "active_input", nxt)
        return nxt

    def swap_inputs(node: str | None = None) -> None:
        """Swap input_a and input_b on the given or selected compare viewer."""
        target = node or (session.selection.names[0] if session.selection.names else None)
        if not target:
            raise CommandError("No compare viewer selected")
        target_name = scene.resolve(target)
        n = scene.get_node(target_name)
        if n.node_type != COMPARE_TYPE_NAME:
            raise CommandError(f"Node '{target_name}' is not a compare viewer")
        a = n.attrs.get("input_a", "")
        b = n.attrs.get("input_b", "")
        scene.set_attrs(target_name, {"input_a": b, "input_b": a})
        # Re-colour the cables so A stays blue and B stays orange
        for link in scene.iter_nodes():
            if link.node_type == "link" and link.attrs.get("target") == n.id:
                src = link.attrs.get("source")
                if src == a:
                    scene.set_attr(link.name, "color", LINK_COLOR_B)
                elif src == b:
                    scene.set_attr(link.name, "color", LINK_COLOR_A)

    ctx.command("create", create, label="Create Compare Viewer")
    ctx.command("compare_nodes", compare_nodes, label="Compare Nodes")
    ctx.command("compare_selected", compare_selected, alias="compare_selected", label="Compare Selected Images")
    ctx.command("toggle_active", toggle_active, label="Toggle A/B Active Input")
    ctx.command("swap_inputs", swap_inputs, label="Swap Compare Inputs")


def activate_ui(ui: Any) -> None:
    from .ui import activate_compare_ui

    activate_compare_ui(ui)

