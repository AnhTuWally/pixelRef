"""Arrange, normalize and align (headless commands + menus and the inspector section)."""

from __future__ import annotations

import math
from typing import Any

from pixelref.plugin_api import CommandError, xform

from . import layout

ARRANGE_GAP = 8.0
ARRANGE_KINDS = {
    "optimal": layout.arrange_optimal,
    "horizontal": layout.arrange_row,
    "vertical": layout.arrange_column,
    "grid": layout.arrange_grid,
}
EDGES = ("left", "right", "top", "bottom", "hcenter", "vcenter")


def activate(ctx: Any) -> None:
    session = ctx.session
    scene = session.scene

    def transform_api() -> Any:
        return session.services.require("transform")

    def everything() -> list[str]:
        """Like PureRef: with less than two items selected, arrange everything."""
        out = []
        for node in scene.iter_nodes():
            node_type = node.node_type
            if not session.types.is_canvas(node_type) or session.types.has_capability(node_type, "container"):
                continue
            if any(session.types.is_canvas(scene.get_node(a).node_type)
                   and not session.types.has_capability(scene.get_node(a).node_type, "container")
                   for a in scene.ancestors(node.name)):
                continue  # inside content (a note on an image, a stroke in a drawing)
            if session.compute(node.name, "world_locked") or not session.compute(node.name, "world_visibility"):
                continue
            out.append(node.name)
        return out

    def arrange(mode: str = "optimal", nodes: Any = None, gap: float = ARRANGE_GAP) -> list[str]:
        """Lay items out: optimal (pack), horizontal (row), vertical (column) or grid."""
        if mode not in ARRANGE_KINDS:
            raise CommandError(f"mode must be one of {', '.join(ARRANGE_KINDS)}")
        api = transform_api()
        try:
            names = api.targets(nodes, minimum=2)
        except CommandError:
            names = everything() if nodes is None else []
        if len(names) < 2:
            raise CommandError("Nothing to arrange")
        rects = [session.world_bounds(n) for n in names]
        pairs = [(n, r) for n, r in zip(names, rects) if r is not None]
        positions = ARRANGE_KINDS[mode]([tuple(r) for _n, r in pairs], float(gap))
        for (name, rect), (x, y) in zip(pairs, positions):
            api.apply_world(name, xform.Matrix.translation(x - rect[0], y - rect[1]))
        return [n for n, _r in pairs]

    def normalize(mode: str = "size", nodes: Any = None) -> list[str]:
        """Make items the same height, width or size (area), about their centres."""
        api = transform_api()
        pairs = [(n, session.world_bounds(n)) for n in api.targets(nodes, minimum=2)]
        pairs = [(n, r) for n, r in pairs if r is not None]  # nothing to measure (an empty drawing)
        if len(pairs) < 2:
            raise CommandError("Select at least 2 item(s) with a size")
        names = [n for n, _r in pairs]
        rects = [r for _n, r in pairs]
        if mode == "height":
            goal = sum(r[3] for r in rects) / len(rects)
            factors = [goal / max(r[3], 1e-6) for r in rects]
        elif mode == "width":
            goal = sum(r[2] for r in rects) / len(rects)
            factors = [goal / max(r[2], 1e-6) for r in rects]
        elif mode == "size":
            goal = sum(r[2] * r[3] for r in rects) / len(rects)
            factors = [math.sqrt(goal / max(r[2] * r[3], 1e-6)) for r in rects]
        else:
            raise CommandError("mode must be height, width or size")
        for name, rect, factor in zip(names, rects, factors):
            cx, cy = rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0
            api.apply_world(name, xform.scaled_about(cx, cy, factor, factor))
        return names

    def align(edge: str = "left", nodes: Any = None) -> list[str]:
        """Align edges or centres: left, right, top, bottom, hcenter, vcenter."""
        if edge not in EDGES:
            raise CommandError(f"edge must be one of {', '.join(EDGES)}")
        api = transform_api()
        names = api.targets(nodes, minimum=2)
        box = api.bounds(names)
        for name in names:
            r = session.world_bounds(name)
            if r is None:
                continue
            dx = dy = 0.0
            if edge == "left":
                dx = box[0] - r[0]
            elif edge == "right":
                dx = (box[0] + box[2]) - (r[0] + r[2])
            elif edge == "top":
                dy = box[1] - r[1]
            elif edge == "bottom":
                dy = (box[1] + box[3]) - (r[1] + r[3])
            elif edge == "hcenter":
                dx = (box[0] + box[2] / 2.0) - (r[0] + r[2] / 2.0)
            elif edge == "vcenter":
                dy = (box[1] + box[3] / 2.0) - (r[1] + r[3] / 2.0)
            api.apply_world(name, xform.Matrix.translation(dx, dy))
        return names

    ctx.command("arrange", arrange, alias="arrange", label=lambda mode="optimal", *a, **k: f"Arrange {mode}")
    ctx.command("normalize", normalize, alias="normalize", label=lambda mode="size", *a, **k: f"Normalize {mode}")
    ctx.command("align", align, alias="align", label=lambda edge="left", *a, **k: f"Align {edge}")


def activate_ui(ui: Any) -> None:
    from PySide6.QtWidgets import QWidget

    from pixelref.plugin_api.contracts import InspectorSection, SelectionInfo
    from pixelref.plugin_api.ui import widgets as w

    app = ui.app

    def arrange_and_frame(mode: str) -> None:
        """Arrange the selection (or everything) and frame the result in the active view."""
        names = app.run_command("arrange.arrange", mode)
        if names and ui.session.commands.exists("frame"):
            app.run_command("frame", names)

    ui.command("arrange_ui", arrange_and_frame, undoable=False, echo=False)
    a = ui.action
    for key, text, shortcut, keywords in (
        ("optimal", "Arrange Optimally", "Ctrl+P", "pack layout tidy organize"),
        ("horizontal", "Arrange Horizontally", "Ctrl+Alt+H", "row line layout"),
        ("vertical", "Arrange Vertically", "Ctrl+Alt+V", "column stack layout"),
        ("grid", "Arrange in Grid", "Ctrl+Alt+G", "tile layout"),
    ):
        a(f"arrange.{key}", "arrange.arrange_ui", text, args=(key,), keywords=keywords, category="Arrange")
        ui.bind(shortcut, f"arrange.{key}")
    for key in ("height", "width", "size"):
        a(f"arrange.normalize_{key}", "arrange.normalize", f"Normalize {key.capitalize()}", args=(key,),
          keywords="same size match" + (" area" if key == "size" else ""), category="Arrange")
    for key, text in (("left", "Align Left"), ("right", "Align Right"), ("top", "Align Top"),
                      ("bottom", "Align Bottom"), ("hcenter", "Align Horizontal Centers"),
                      ("vcenter", "Align Vertical Centers")):
        a(f"arrange.align_{key}", "arrange.align", text, args=(key,), category="Arrange")
    # Laying out works on the selection or (with nothing selected) everything; matching sizes
    # and aligning need two or more selected items. With one item selected the sub-menu hides.
    order = 0
    for group, keys, when in (("a1", ("optimal", "horizontal", "vertical", "grid"), "selection.count != 1"),
                              ("a2", ("normalize_height", "normalize_width", "normalize_size"),
                               "selection.count >= 2"),
                              ("a3", ("align_left", "align_hcenter", "align_right", "align_top", "align_vcenter",
                                      "align_bottom"), "selection.count >= 2")):
        for key in keys:
            ui.menu("canvas", f"arrange.{key}", path="Arrange", group=group, order=order, when=when)
            order += 1
    ui.place_submenu("canvas", "Arrange", "arrange", 0, icon="grid")
    ui.place_submenu("canvas", "Arrange", "tools", 1, when="selection.count == 0")

    class ArrangeSection(w.InspectorSection):
        def __init__(self, parent: QWidget) -> None:
            super().__init__("arrange", "Align & arrange", parent)
            self._align_buttons = []
            for key, glyph, tip in (("left", "⇤", "Align left edges"), ("hcenter", "↔", "Align horizontal centers"),
                                    ("right", "⇥", "Align right edges"), ("top", "⤒", "Align top edges"),
                                    ("vcenter", "↕", "Align vertical centers"), ("bottom", "⤓", "Align bottom edges")):
                button = w.make_tool_button(glyph, tip, width=28, parent=self.body)
                button.clicked.connect(lambda _c=False, k=key: app.trigger(f"arrange.align_{k}"))
                self._align_buttons.append(button)
            self._arrange_buttons = []
            for key, label, tip in (("optimal", "Pack", "Arrange optimally (Ctrl+P)"),
                                    ("grid", "Grid", "Arrange in a grid"),
                                    ("horizontal", "Row", "Arrange in a row"),
                                    ("vertical", "Column", "Arrange in a column")):
                button = w.make_tool_button(label, tip, width=50, parent=self.body)
                button.clicked.connect(lambda _c=False, k=key: app.trigger(f"arrange.{k}"))
                self._arrange_buttons.append(button)
            self.form.addRow("Align", w.hrow(self.body, *self._align_buttons))
            self.form.addRow("Arrange", w.hrow(self.body, *self._arrange_buttons))

        def refresh(self, info: SelectionInfo) -> None:
            return None

    ui.contribute("inspector.sections", InspectorSection(
        key="arrange", title="Align & arrange", order=20, applies=lambda info: len(info.names) > 1,
        build=lambda parent, _i: ArrangeSection(parent)))
