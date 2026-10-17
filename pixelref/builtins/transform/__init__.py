"""Transform commands on canvas nodes (they work headless: pure attribute maths)."""

from __future__ import annotations

from typing import Any, Callable

from pixelref.plugin_api import CommandError, xform


class TransformApi:
    def __init__(self, session: Any) -> None:
        self.session = session
        # The UI swaps in the canvas's notion of transform targets (it knows
        # which notes are being edited, what's visible…).
        self.targets_provider: Callable[[], list[str]] | None = None

    def targets(self, nodes: Any = None, minimum: int = 1) -> list[str]:
        session = self.session
        scene = session.scene
        if nodes is None and self.targets_provider is not None:
            names = self.targets_provider()
        else:
            raw = session.selection.names if nodes is None else [
                scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
            names = []
            for name in scene.top_level(raw):
                node_type = scene.get_node(name).node_type
                if not session.types.is_canvas(node_type) or session.types.has_capability(node_type, "container"):
                    continue
                if session.compute(name, "world_locked") or not session.compute(name, "world_visibility"):
                    continue
                names.append(name)
            names.sort()
        if len(names) < minimum:
            raise CommandError(f"Select at least {minimum} unlocked item(s)" if minimum > 1 else "Nothing selected")
        return names

    def bounds(self, names: list[str]) -> list[float] | None:
        rects = [r for r in (self.session.world_bounds(n) for n in names) if r is not None]
        if not rects:
            return None
        left = min(r[0] for r in rects)
        top = min(r[1] for r in rects)
        right = max(r[0] + r[2] for r in rects)
        bottom = max(r[1] + r[3] for r in rects)
        return [left, top, right - left, bottom - top]

    def apply_world(self, name: str, delta: xform.Matrix) -> None:
        """Apply a world-space change to a node (``world @ delta``)."""
        scene = self.session.scene
        node = scene.get_node(name)
        world = xform.world_matrix(scene, name)
        inverse = xform.parent_world_matrix(scene, node.parent).inverted()
        if inverse is None:
            return
        scene.set_attrs(name, xform.attrs_for_matrix(world @ delta @ inverse, node.attrs))

    def content_center(self, name: str) -> tuple[float, float] | None:
        rect = self.session.local_bounds(name)
        if rect is None:
            return None
        world = xform.world_matrix(self.session.scene, name)
        return world.map(rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0)


def activate(ctx: Any) -> None:
    session = ctx.session
    api = TransformApi(session)
    ctx.provide_service("transform", api)

    def flip(axis: str = "h", nodes: Any = None) -> list[str]:
        """Mirror the selection in place: axis "h" (horizontal) or "v"."""
        names = api.targets(nodes)
        box = api.bounds(names)
        if box is None:
            return []
        cx, cy = box[0] + box[2] / 2.0, box[1] + box[3] / 2.0
        delta = xform.scaled_about(cx, cy, -1.0, 1.0) if axis == "h" else xform.scaled_about(cx, cy, 1.0, -1.0)
        for name in names:
            api.apply_world(name, delta)
        return names

    def rotate(degrees: float = 90.0, nodes: Any = None) -> list[str]:
        """Rotate each selected item about its own centre."""
        names = api.targets(nodes)
        for name in names:
            center = api.content_center(name)
            if center is not None:
                api.apply_world(name, xform.rotated_about(center[0], center[1], float(degrees)))
        return names

    def reset(nodes: Any = None) -> list[str]:
        """Back to scale 1, no rotation, no flips (keeping each item's centre)."""
        names = api.targets(nodes)
        scene = session.scene
        for name in names:
            rect = session.local_bounds(name)
            attrs = scene.get_node(name).attrs
            if rect is None:
                scene.set_attrs(name, {"scale": [1.0, 1.0], "rotate": 0.0})
                continue
            cx, cy = rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0
            old = xform.local_matrix(attrs).map(cx, cy)
            scene.set_attrs(name, {"translate": [round(old[0] - cx, 3) + 0.0, round(old[1] - cy, 3) + 0.0],
                                   "scale": [1.0, 1.0], "rotate": 0.0})
        return names

    def scale(factor: float, nodes: Any = None) -> list[str]:
        """Scale the selection about its centre."""
        names = api.targets(nodes)
        box = api.bounds(names)
        if box is None:
            return []
        cx, cy = box[0] + box[2] / 2.0, box[1] + box[3] / 2.0
        delta = xform.scaled_about(cx, cy, float(factor), float(factor))
        for name in names:
            api.apply_world(name, delta)
        return names

    def move(dx: float, dy: float, nodes: Any = None) -> list[str]:
        """Move the selection by (dx, dy) world units."""
        names = api.targets(nodes)
        delta = xform.Matrix.translation(float(dx), float(dy))
        for name in names:
            api.apply_world(name, delta)
        return names

    ctx.command("flip", flip, alias="flip",
                label=lambda axis="h", nodes=None: f"Flip {'horizontal' if axis == 'h' else 'vertical'}")
    ctx.command("rotate", rotate, alias="rotate", label=lambda degrees=90.0, nodes=None: f"Rotate {degrees:+.0f}°")
    ctx.command("reset", reset, alias="reset_transform", label="Reset transform")
    ctx.command("scale", scale, alias="scale", label="Scale")
    ctx.command("move", move, alias="move", label="Move")


def activate_ui(ui: Any) -> None:
    from pixelref.plugin_api.ui import HANDLED, InputHandler, WheelEvent

    session = ui.session
    canvas = ui.canvas
    api = session.services.require("transform")
    api.targets_provider = lambda: [item.node_name for item in canvas.transform_targets()]
    ui.track(lambda: setattr(api, "targets_provider", None))

    def nudge(dx: float, dy: float) -> None:
        """Move the selection a little (arrow keys); rapid nudges are one undo step."""
        if not canvas.transform_targets():
            return
        canvas.begin_coalesced("Nudge", "transform.nudge")
        session.run("transform.move", dx, dy)
        session.notify_changed("nudge")

    def wheel_scale(factor: float) -> None:
        """Scale the selection about its centre (Ctrl+wheel); quick repeats are one undo step."""
        if not canvas.transform_targets():
            return
        canvas.begin_coalesced("Scale", "transform.wheel_scale")
        session.run("transform.scale", factor)
        session.notify_changed("scale")

    ui.command("nudge", nudge, undoable=False, echo=False)
    ui.command("wheel_scale", wheel_scale, undoable=False, echo=False)

    class CtrlWheelScale(InputHandler):
        def wheel(self, e: WheelEvent) -> str | None:
            if not e.ctrl:
                return None
            ui.app.run_command("transform.wheel_scale", 1.1 ** e.steps)
            return HANDLED

    ui.input_handler(CtrlWheelScale(), 950)

    a = ui.action
    a("transform.flip_h", "transform.flip", "Flip Horizontal", args=("h",), keywords="mirror reverse",
      category="Transform")
    a("transform.flip_v", "transform.flip", "Flip Vertical", args=("v",), keywords="mirror upside down",
      category="Transform")
    a("transform.rotate_cw", "transform.rotate", "Rotate 90° Clockwise", args=(90.0,), keywords="turn spin right",
      category="Transform")
    a("transform.rotate_ccw", "transform.rotate", "Rotate 90° Counter-clockwise", args=(-90.0,),
      keywords="turn spin left", category="Transform")
    a("transform.reset", "transform.reset", "Reset Transform", keywords="restore original size rotation scale",
      category="Transform")
    for key, action in (("Shift+H", "transform.flip_h"), ("Shift+V", "transform.flip_v"),
                        ("Ctrl+R", "transform.rotate_cw"), ("Ctrl+Shift+R", "transform.rotate_ccw"),
                        ("Ctrl+Shift+0", "transform.reset")):
        ui.bind(key, action)
    for order, key in enumerate(("transform.flip_h", "transform.flip_v", "transform.rotate_cw",
                                 "transform.rotate_ccw", "transform.reset")):
        ui.menu("canvas", key, path="Transform", group="transform", order=order, when="selection.count > 0")
    ui.place_submenu("canvas", "Transform", "transform", 0, icon="transform")
    for name, dx, dy in (("Left", -1.0, 0.0), ("Right", 1.0, 0.0), ("Up", 0.0, -1.0), ("Down", 0.0, 1.0)):
        a(f"transform.nudge_{name.lower()}", "transform.nudge", f"Nudge {name}", args=(dx, dy), category="hidden")
        a(f"transform.nudge_{name.lower()}_big", "transform.nudge", f"Nudge {name} ×10", args=(dx * 10, dy * 10),
          category="hidden")
        ui.bind(name, f"transform.nudge_{name.lower()}", scope="canvas")
        ui.bind(f"Shift+{name}", f"transform.nudge_{name.lower()}_big", scope="canvas")
