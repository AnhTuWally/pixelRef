"""The outliner view and the lock / visibility commands.

The ``*_types`` commands work on every node of some node types at once. Types
are given by name (``"image"``), by base type (``"container"``) or by glob
pattern (``"annotation_*"``, every annotation); leaving them out means the
types of the selected nodes.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from pixelref.plugin_api import CommandError


def activate(ctx: Any) -> None:
    session = ctx.session
    scene = session.scene

    def targets(nodes: Any) -> list[str]:
        if nodes is None:
            return session.selection.names
        return [scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]

    def toggle(attr: str, nodes: Any) -> int:
        names = targets(nodes)
        if not names:
            raise CommandError("Nothing selected")
        default = 1 if attr == "visibility" else 0
        all_set = all(bool(scene.get_node(n).attrs.get(attr, default)) for n in names)
        value = 0 if all_set else 1
        for name in names:
            scene.set_attr(name, attr, value)
        state = {"locked": ("unlocked", "locked"), "visibility": ("hidden", "visible")}[attr][value]
        session.events.emit("ui.status", {"text": f"{len(names)} item(s) {state}"})
        return value

    def toggle_lock(nodes: Any = None) -> int:
        """Lock the nodes (or unlock them if all are locked)."""
        return toggle("locked", nodes)

    def toggle_visibility(nodes: Any = None) -> int:
        """Hide the nodes (or show them if all are hidden)."""
        return toggle("visibility", nodes)

    def set_all(attr: str, value: int) -> None:
        for node in scene.iter_nodes():
            if attr in node.attrs and node.attrs.get(attr) != value:
                scene.set_attr(node.name, attr, value)

    def show_all() -> None:
        """Make every node visible (Alt+H)."""
        set_all("visibility", 1)

    def unlock_all() -> None:
        """Unlock every node (Alt+L)."""
        set_all("locked", 0)

    def type_matcher(types: Any) -> tuple[list[str], Any]:
        if types is None:
            wanted = sorted({scene.get_node(n).node_type for n in session.selection.names})
            if not wanted:
                raise CommandError("Nothing selected")
        else:
            wanted = [types] if isinstance(types, str) else list(types)
        for pattern in wanted:
            if pattern not in session.types and not any(c in pattern for c in "*?["):
                raise CommandError(f"Unknown node type '{pattern}'")

        def matches(node_type: str) -> bool:
            return any(node_type == p or fnmatch.fnmatchcase(node_type, p)
                       or (p in session.types and session.types.inherits(node_type, p)) for p in wanted)

        return wanted, matches

    def describe(wanted: list[str]) -> str:
        return ", ".join(session.types.label(t) if t in session.types else t for t in wanted)

    def set_type_flag(types: Any = None, attr: str = "visibility", value: int = 0) -> int:
        """Set ``visibility`` or ``locked`` on every node of these types. Returns how many changed."""
        if attr not in ("visibility", "locked"):
            raise CommandError(f"Expected 'visibility' or 'locked', got '{attr}'")
        wanted, matches = type_matcher(types)
        value = 1 if value else 0
        changed = 0
        for node in list(scene.iter_nodes()):
            if matches(node.node_type) and attr in node.attrs and node.attrs.get(attr) != value:
                scene.set_attr(node.name, attr, value)
                changed += 1
        state = {"locked": ("unlocked", "locked"), "visibility": ("hidden", "shown")}[attr][value]
        session.events.emit("ui.status", {"text": f"{describe(wanted)}: {changed} item(s) {state}"})
        return changed

    def isolate_types(types: Any = None) -> int:
        """Show only nodes of these types (and the groups holding them); hide everything else."""
        wanted, matches = type_matcher(types)
        nodes = list(scene.iter_nodes())
        keep: set[str] = set()
        for node in nodes:
            if matches(node.node_type):
                name: str | None = node.name
                while name is not None and name not in keep:  # the node and its ancestors
                    keep.add(name)
                    name = scene.get_node(name).parent
        inside: set[str] = set()  # below a kept node of a matching type: left alone
        for node in nodes:  # parents come first
            parent = node.parent
            if parent is not None and (parent in inside or (parent in keep and matches(scene.get_node(parent).node_type))):
                inside.add(node.name)
        shown = 0
        for node in nodes:
            if "visibility" not in node.attrs or node.name in inside:
                continue
            value = 1 if node.name in keep else 0
            shown += value
            if node.attrs.get("visibility") != value:
                scene.set_attr(node.name, "visibility", value)
        session.events.emit("ui.status", {"text": f"Isolated {describe(wanted)} ({shown} shown, Alt+H shows all)"})
        return shown

    def select_types(types: Any = None) -> list[str]:
        """Select every node of these types."""
        _wanted, matches = type_matcher(types)
        names = [node.name for node in scene.iter_nodes() if matches(node.node_type)]
        session.selection.set(names)
        return session.selection.names

    def set_flag(node: str, attr: str, value: int) -> None:
        """Set a node's visibility or locked flag (the outliner's eye and lock columns)."""
        scene.set_attr(scene.resolve(node), attr, value)

    ctx.command("toggle_lock", toggle_lock, alias="toggle_lock", label="Toggle lock")
    ctx.command("toggle_visibility", toggle_visibility, alias="toggle_visibility", label="Toggle visibility")
    ctx.command("show_all", show_all, alias="show_all", label="Show all")
    ctx.command("unlock_all", unlock_all, alias="unlock_all", label="Unlock all")
    ctx.command("set_type_flag", set_type_flag, label=lambda types=None, attr="visibility", value=0: (
        ("Hide", "Show") if attr == "visibility" else ("Unlock", "Lock"))[1 if value else 0] + " by type")
    ctx.command("isolate_types", isolate_types, label="Isolate types")
    ctx.command("select_types", select_types, undoable=False)
    ctx.command("set_flag", set_flag, label=lambda node, attr, value: "Toggle visibility" if attr == "visibility"
                else "Toggle lock")


def activate_ui(ui: Any) -> None:
    from .ui import activate_outliner_ui

    activate_outliner_ui(ui)
