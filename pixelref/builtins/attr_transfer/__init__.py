"""Copy attributes between items.

* ``attr_transfer.copy`` snapshots an item's attributes; ``attr_transfer.paste`` writes the
  chosen ones onto other items (same attribute name *and* type).
* ``attr_transfer.apply_attr`` pushes single attributes of one item to others (same name).
* ``attr_transfer.set_values`` writes values you give onto the items that have those attributes
  (what editing a row of the Attribute Editor with several items selected uses).

The matching rules and the "which attributes are offered" logic are in :mod:`pixelref.core.transfer`;
the pop-up and the Attribute Editor parts are in ``ui.py``.
"""

from __future__ import annotations

from typing import Any

from pixelref.core import transfer
from pixelref.plugin_api import CommandError


class TransferService:
    """The copied attributes (a snapshot, so the source can change or go away afterwards)."""

    def __init__(self) -> None:
        self.clipboard: transfer.Snapshot | None = None
        self._listeners: list[Any] = []

    def set(self, snap: transfer.Snapshot | None) -> None:
        self.clipboard = snap
        for callback in list(self._listeners):
            callback()

    def watch(self, callback: Any) -> Any:
        self._listeners.append(callback)
        return lambda: callback in self._listeners and self._listeners.remove(callback)


def _names(value: Any) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def activate(ctx: Any) -> None:
    session = ctx.session
    scene = session.scene
    service = TransferService()
    ctx.provide_service("attr_transfer", service)

    def targets_for(nodes: Any, source: str | None) -> list[str]:
        names = _names(nodes) if nodes else session.selection.names
        names = [scene.resolve(n) for n in names]
        names = [n for n in dict.fromkeys(names) if n != source]
        if not names:
            raise CommandError("Select the items to apply to")
        return names

    def copy(node: Any = None) -> dict[str, Any]:
        """Copy the attributes of a node (default: the selected item you picked last)."""
        name = scene.resolve(node) if node else (session.selection.active or "")
        if not name:
            raise CommandError("Select an item to copy attributes from")
        snap = transfer.snapshot(scene, name)
        service.set(snap)
        return {"source": name, "type": snap.node_type, "attributes": list(snap.entries)}

    def paste(attrs: Any = None, nodes: Any = None) -> dict[str, Any]:
        """Paste the copied attributes onto nodes (default: the selection). ``attrs`` limits which
        (default: the style attributes); only targets with the same attribute name and type take them."""
        snap = service.clipboard
        if snap is None:
            raise CommandError("Copy attributes first")
        names = snap.default_names() if attrs is None else _names(attrs)
        unknown = [n for n in names if n not in snap.entries]
        if unknown:
            raise CommandError(f"The copied item has no attribute {', '.join(unknown)}")
        result = transfer.apply_values(scene, snap.values(names), targets_for(nodes, snap.source), "multi",
                                       {n: snap.entries[n].spec for n in names}, snap.source)
        return result.summary()

    def apply_attr(attr: Any, node: Any = None, nodes: Any = None) -> dict[str, Any]:
        """Push attributes of ``node`` (default: the last selected) onto other nodes that have an
        attribute of the same name (default targets: the rest of the selection)."""
        source = scene.resolve(node) if node else (session.selection.active or "")
        if not source:
            raise CommandError("Select the item to take the value from")
        names = _names(attr)
        values = {}
        for name in names:
            if scene.attr_spec(scene.get_node(source), name) is None:
                raise CommandError(f"'{source}' has no attribute '{name}'")
            values[name] = transfer.value_of(scene, source, name)
        specs = transfer.specs_of(scene, source)
        result = transfer.apply_values(scene, values, targets_for(nodes, source), "single",
                                       {n: specs[n] for n in names if n in specs}, source)
        return result.summary()

    def set_values(values: dict[str, Any], nodes: Any = None) -> dict[str, Any]:
        """Set attribute values on every node that has them (default: the selection)."""
        names = _names(nodes) if nodes else session.selection.names
        if not names:
            raise CommandError("Nothing selected")
        return transfer.apply_values(scene, dict(values), [scene.resolve(n) for n in names], "single").summary()

    ctx.command("copy", copy, alias="copy_attributes", undoable=False)
    ctx.command("paste", paste, alias="paste_attributes", label="Paste attributes")
    ctx.command("apply_attr", apply_attr, alias="apply_attribute", label=lambda attr, *a, **k: "Apply attribute")
    ctx.command("set_values", set_values, label=lambda values, nodes=None: "Edit " + ", ".join(
        str(k).replace("_", " ") for k in list(values)[:2]))


def activate_ui(ui: Any) -> None:
    from .ui import activate_attr_transfer_ui

    activate_attr_transfer_ui(ui)
