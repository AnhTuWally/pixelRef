"""Groups: hierarchy containers, Group (G), Ungroup (Ctrl+Shift+G) and New Group.

A group normally acts as one item on the canvas; with ``pick_members`` on it is
a plain folder whose members are clicked directly.

Other node types can be dissolved by Ungroup too: contribute an
``UngroupHandler`` to ``grouping.ungroup`` (the pen does, for drawings —
"Separate Strokes").
"""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CAP_CONTAINER, Attr, CommandError, NodeType

CONTAINER_TYPES = ("group",)


def top_level(scene: Any, names: list[str]) -> list[str]:
    return scene.top_level(names)


def activate(ctx: Any) -> None:
    ctx.register_node_type(NodeType(
        "group", inherits="container", label="Group", capabilities=frozenset({CAP_CONTAINER}),
        attrs=(Attr("pick_members", "flag", 0, short="pm", group="Group",
                    doc="Clicks pick the members, not the whole group (a plain folder)"),),
        doc="Items grouped together"))
    ctx.declare_extension_point("grouping.ungroup", "UngroupHandler: node types Ungroup may dissolve")
    session = ctx.session
    scene = session.scene

    def selection_or(nodes: Any) -> list[str]:
        if nodes is None:
            return session.selection.names
        return [scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]

    def front_to_back(parent: str | None) -> list[str]:
        return list(reversed(scene.children_of(parent)))

    def group(nodes: Any = None, name: str = "Group") -> str:
        """Put nodes in a new group, in place on the canvas. Returns the group."""
        names = top_level(scene, selection_or(nodes))
        if not names:
            raise CommandError("Nothing to group")
        parents = {scene.get_node(n).parent for n in names}
        parent = parents.pop() if len(parents) == 1 else None
        group_name = scene.unique_name(name)
        siblings = front_to_back(parent)
        front_most = min((siblings.index(n) for n in names if n in siblings), default=0)
        scene.create_node("group", group_name, parent=parent)
        order = [n for n in siblings if n not in names]
        order.insert(min(front_most, len(order)), group_name)
        scene.restack(list(reversed(order)))
        ctx.run("move_nodes", names, group_name, None, "end")
        session.selection.set([group_name])
        return group_name

    def handlers() -> dict[str, Any]:
        return {h.node_type: h for h in ctx.contributions("grouping.ungroup")}

    def ungroupable(names: list[str]) -> list[str]:
        extra = handlers()
        return [n for n in top_level(scene, names)
                if scene.get_node(n).node_type in CONTAINER_TYPES or scene.get_node(n).node_type in extra]

    def ungroup(nodes: Any = None) -> list[str]:
        """Dissolve groups (and other ungroupable nodes); children stay in place."""
        groups = ungroupable(selection_or(nodes))
        if not groups:
            raise CommandError("Select a group or drawing to ungroup")
        released: list[str] = []
        for group_name in groups:
            parent = scene.get_node(group_name).parent
            children = front_to_back(group_name)
            ctx.run("move_nodes", children, parent, group_name, "above")
            scene.delete_node(group_name)
            released.extend(children)
        session.selection.set([n for n in released if scene.has(n)])
        return released

    def ungroup_label(nodes: Any = None) -> str:
        names = ungroupable(selection_or(nodes))
        types = {scene.get_node(n).node_type for n in names}
        extra = handlers()
        if len(types) == 1 and next(iter(types)) in extra:
            return extra[next(iter(types))].label.capitalize()
        return "Ungroup"

    def add_group(name: str = "Group", pick_members: bool = True) -> str:
        """Create an empty group (by default a folder whose members are picked directly)."""
        final = scene.unique_name(name.strip() or "Group")
        scene.create_node("group", final, attrs={"pick_members": int(bool(pick_members))})
        return final

    ctx.command("group", group, alias="group", label="Group")
    ctx.command("ungroup", ungroup, alias="ungroup", label=ungroup_label)
    ctx.command("add_group", add_group, alias="add_group", label="New group")
    ctx.ungroup_label = ungroup_label  # type: ignore[attr-defined]
    ctx.ungroupable = ungroupable  # type: ignore[attr-defined]
    ctx.handlers = handlers  # type: ignore[attr-defined]


def activate_ui(ui: Any) -> None:
    from PySide6.QtWidgets import QInputDialog

    from pixelref.plugin_api.contracts import PaletteButton

    from .ui import activate_grouping_ui

    activate_grouping_ui(ui)
    ctx = ui.headless
    session = ui.session

    def add_group_dialog() -> None:
        """Ask for a name and add an empty group."""
        default = session.scene.unique_name("Group")
        name, ok = QInputDialog.getText(ui.app.main_window(), "New Group", "Group name:", text=default)
        if ok and name.strip():
            ui.app.run_command("grouping.add_group", name.strip())

    ui.command("add_group_dialog", add_group_dialog, undoable=False)

    def ungroup_text() -> str:
        names = ctx.ungroupable(session.selection.names)
        types = {session.scene.get_node(n).node_type for n in names}
        handlers = ctx.handlers()
        if len(types) == 1 and next(iter(types)) in handlers:
            return handlers[next(iter(types))].label
        return "Ungroup"

    def after_ungroup() -> None:
        """Ungroup the selection and report what happened (Ctrl+Shift+G)."""
        types = {session.scene.get_node(n).node_type for n in ctx.ungroupable(session.selection.names)}
        handlers = ctx.handlers()
        handler = handlers.get(next(iter(types))) if len(types) == 1 else None
        released = ui.app.run_command("grouping.ungroup")
        if released is None:
            return
        if handler is not None and handler.done_message is not None:
            ui.status(handler.done_message(len(released)))

    ui.command("ungroup_ui", after_ungroup, undoable=False, echo=False)
    ui.action("grouping.group", "grouping.group", "Group", keywords="combine nest folder", category="Create",
              icon="layer")
    ui.action("grouping.ungroup", "grouping.ungroup_ui", "Ungroup", keywords="split separate explode",
              category="Create", dynamic_text=ungroup_text)
    ui.action("grouping.add_group", "grouping.add_group_dialog", "New Group…",
              keywords="folder container layer empty", category="Create", icon="layer")
    ui.bind("G", "grouping.group")
    ui.bind("Ctrl+Shift+G", "grouping.ungroup")
    ui.bind("Ctrl+Shift+N", "grouping.add_group")
    ui.menu("canvas", "grouping.group", group="arrange", order=20, when="selection.count > 0")
    ui.menu("canvas", "grouping.ungroup", group="arrange", order=21, when="selection.count > 0")
    ui.menu("canvas", "grouping.add_group", group="create", order=30, when="selection.count == 0")
    ui.contribute("palette.buttons", PaletteButton("grouping.add_group", "Group", "layer", order=30))
