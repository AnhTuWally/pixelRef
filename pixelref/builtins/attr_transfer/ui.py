"""Copy attributes: actions and menus, the Paste Attributes pop-up, the Attribute Editor's
section for several selected items, and a Copy / Paste row in Appearance."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from pixelref.core import transfer
from pixelref.plugin_api import CommandError
from pixelref.plugin_api.contracts import InspectorRows, InspectorSection, SelectionInfo
from pixelref.plugin_api.ui import widgets as w

from .dialog import ask_paste

# Opacity already has its own multi-item control in Appearance.
SKIP_SHARED = frozenset({"opacity"})


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def shared_attrs(scene: Any, names: list[str], ref: str) -> dict[str, tuple[Any, list[str]]]:
    """The style attributes of ``ref`` that at least one other selected item has too (same name,
    same kind of value): attribute name -> (ref's spec, the selected items that have it)."""
    out: dict[str, tuple[Any, list[str]]] = {}
    for key, spec in transfer.specs_of(scene, ref).items():
        if key in SKIP_SHARED or spec.hidden or transfer.kind_of(spec) != transfer.STYLE:
            continue
        have = []
        for name in names:
            other = transfer.spec_for(scene, name, key)
            if other is not None and transfer.family(other.type) == transfer.family(spec.type):
                have.append(name)
        if len(have) >= 2:
            out[key] = (spec, have)
    return out


class Row:
    """One attribute in the shared section."""

    def __init__(self, spec: Any, editor: Any, mixed: QLabel, push: Any) -> None:
        self.spec, self.editor, self.mixed, self.push = spec, editor, mixed, push
        self.names: list[str] = []


class SharedSection(w.InspectorSection):
    """With several items selected: the attributes they share. Editing a row sets it on every item
    that has it; "mixed" marks rows where the items differ and ⇉ gives the others the reference
    item's value."""

    def __init__(self, ui: Any, parent: Any) -> None:
        super().__init__("shared", "Shared attributes", parent)
        self.ui = ui
        self._rows: dict[str, Row] = {}
        self._names: tuple[str, ...] = ()
        self._signature: Any = None
        self._ref = ""
        self._updating = False
        self.combo: QComboBox | None = None

    # ---- Refresh -----------------------------------------------------------------------------------------

    def _reference(self, names: list[str]) -> str:
        if self._ref in names:
            return self._ref
        active = self.ui.session.selection.active
        return active if active in names else names[-1]

    def refresh(self, info: SelectionInfo) -> None:
        scene = self.ui.session.scene
        names = list(info.names)
        if info.single or not all(scene.has(n) for n in names):
            return
        if tuple(names) != self._names:
            self._ref = ""  # a new selection: back to the item picked last
        ref = self._reference(names)
        shared = shared_attrs(scene, names, ref)
        signature = (tuple(names), ref, tuple((k, s.type, tuple(h)) for k, (s, h) in shared.items()))
        if signature != self._signature:
            self._rebuild(names, ref, shared)
            self._signature = signature
        self._updating = True
        try:
            for key in shared:
                row = self._rows[key]
                value = transfer.value_of(scene, ref, key)
                row.editor.write(value)
                mixed = any(transfer.value_of(scene, n, key) != value for n in row.names if n != ref)
                row.mixed.setVisible(mixed)
                row.push.setEnabled(mixed)
        finally:
            self._updating = False

    def _rebuild(self, names: list[str], ref: str, shared: dict[str, tuple[Any, list[str]]]) -> None:
        self._names, self._ref = tuple(names), ref
        while self.form.rowCount():  # (this deletes the widgets too, the combo included)
            self.form.removeRow(0)
        self._rows = {}
        self.combo = QComboBox(self.body)
        self.combo.setToolTip("The item whose values are shown, and given to the others by ⇉")
        for name in names:
            self.combo.addItem(name, name)
        self.combo.setCurrentIndex(max(0, self.combo.findData(ref)))
        self.combo.currentIndexChanged.connect(self._on_ref_chosen)
        self.form.addRow("Values from", self.combo)
        hint = QLabel(f"Editing a value sets it on every item that has it ({len(names)} selected). "
                      "“mixed” marks values that differ; ⇉ gives the other items the value shown.", self.body)
        hint.setWordWrap(True)
        hint.setObjectName("cardSubtitle")
        self.form.addRow(hint)
        group = None
        for key, (spec, have) in shared.items():
            if (spec.group or "Other") != group:
                group = spec.group or "Other"
                heading = QLabel(group.upper(), self.body)
                heading.setObjectName("cardSubtitle")
                self.form.addRow(heading)
            editor = w.editor_for(spec, self.body)
            mixed = QLabel("mixed", self.body)
            mixed.setObjectName("cardSubtitle")
            push = w.make_tool_button("⇉", "", width=26, parent=self.body)
            push.clicked.connect(lambda _c=False, k=key: self._push(k))
            box = QWidget(self.body)
            layout = QHBoxLayout(box)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            layout.addWidget(editor, 1)
            layout.addWidget(mixed)
            layout.addWidget(push)
            count = f" ({len(have)}/{len(names)})" if len(have) != len(names) else ""
            if isinstance(editor, w.NumberField):
                label: QWidget = w.ScrubLabel(spec.nice_name + count, editor, editor.per_pixel(), self.body)
            else:
                label = QLabel(spec.nice_name + count, self.body)
            label.setToolTip(spec.doc or spec.name)
            push.setToolTip(f"Give the other {_plural(len(have) - 1, 'item')} {ref}'s {spec.nice_name.lower()}")
            row = Row(spec, editor, mixed, push)
            row.names = have
            self._rows[key] = row
            w.bind_edits(self.ui.app, editor, lambda value, k=key: self._commit(k, value),
                         f"Set {spec.nice_name.lower()}")
            editor.context_provider = lambda k=key: self._menu(k)
            self.form.addRow(label, box)

    # ---- Doing things -----------------------------------------------------------------------------------------

    def _on_ref_chosen(self, _index: int) -> None:
        name = self.combo.currentData() if self.combo is not None else None
        if name and name != self._ref:
            self._ref = name
            self._signature = None
            inspector = self.ui.service("inspector")
            panel = inspector.panel() if inspector is not None else None
            if panel is not None:
                panel.refresh()

    def _commit(self, key: str, value: Any) -> None:
        if self._updating or key not in self._rows:
            return
        self.ui.app.run_command("attr_transfer.set_values", {key: value}, self._rows[key].names)

    def _push(self, key: str, targets: list[str] | None = None) -> None:
        row = self._rows.get(key)
        if row is None:
            return
        targets = [n for n in (targets if targets is not None else row.names) if n != self._ref]
        result = self.ui.app.run_command("attr_transfer.apply_attr", key, self._ref, targets)
        if result is not None:
            cannot = len(result["skipped"].get(key, []))
            self.ui.toast(f"{row.spec.nice_name}: {_plural(result['items'], 'item')} changed"
                          + (f", {_plural(cannot, 'item')} can't take it" if cannot else ""))

    def _menu(self, key: str) -> list[tuple[Any, Any]]:
        row = self._rows[key]
        scene = self.ui.session.scene
        ref_type = scene.get_node(self._ref).node_type
        label = self.ui.canvas.friendly_type(ref_type).lower()
        same_type = [n for n, node in scene.nodes.items() if node.node_type == ref_type]
        return [
            (f"Apply {self._ref}'s value to the other selected items", lambda: self._push(key)),
            (f"Apply to every {label} in the scene ({len(same_type) - 1} others)",
             (lambda: self._push(key, same_type)) if len(same_type) > 1 else None),
            (None, None),
            ("Reset to default on all selected",
             lambda: self.ui.app.run_command("attr_transfer.set_values", {key: row.spec.default_value()}, row.names)),
        ]


class CopyPasteRow(QWidget):
    """Copy / Paste attribute buttons in the Appearance section."""

    def __init__(self, ui: Any, parent: Any, service: Any) -> None:
        super().__init__(parent)
        self.service = service
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.copy_btn = w.make_tool_button("Copy attributes", "Copy this item's attributes (Ctrl+Alt+Shift+C)",
                                           width=110, parent=self)
        self.paste_btn = w.make_tool_button("Paste attributes…", "", width=120, parent=self)
        self.copy_btn.clicked.connect(lambda: ui.app.trigger("attr_transfer.copy"))
        self.paste_btn.clicked.connect(lambda: ui.app.trigger("attr_transfer.paste_dialog"))
        layout.addWidget(self.copy_btn)
        layout.addWidget(self.paste_btn)
        layout.addStretch()

    def refresh(self, info: SelectionInfo) -> None:
        snap = self.service.clipboard
        self.paste_btn.setEnabled(snap is not None and any(n != snap.source for n in info.names))
        self.paste_btn.setToolTip(
            "Paste attributes onto the selection (copy some first)" if snap is None else
            f"Choose which attributes copied from {snap.source} to paste onto the selection (Ctrl+Alt+Shift+V)")


def activate_attr_transfer_ui(ui: Any) -> None:
    session = ui.session
    scene = session.scene
    service = ui.service("attr_transfer")

    def copy_selected() -> dict[str, Any]:
        """Copy the attributes of the item you selected last."""
        result = ui.app.run_command("attr_transfer.copy")
        if result is None:
            return {}
        ui.status(f"Attributes of {result['source']} copied · Paste Attributes… (Ctrl+Alt+Shift+V) chooses what "
                  "goes onto the selected items")
        return result

    def paste_dialog() -> Any:
        """Choose which copied attributes to paste onto the selected items."""
        snap = service.clipboard
        if snap is None:
            raise CommandError("Copy attributes first: select an item and use Copy Attributes")
        targets = [n for n in session.selection.names if n != snap.source and scene.has(n)]
        if not targets:
            raise CommandError("Select the items to paste onto")
        plan = transfer.plan(scene, snap, targets, "multi")
        if not any(plan.values()):
            raise CommandError("None of the selected items has an attribute in common with the copied item")
        names = ask_paste(ui, snap, targets)
        if not names:
            return None
        result = ui.app.run_command("attr_transfer.paste", names, targets)
        if result is not None:
            ui.toast(f"Pasted {_plural(len(names), 'attribute')} onto {_plural(result['items'], 'item')}")
        return result

    def paste_style() -> Any:
        """Paste every style attribute without asking."""
        result = ui.app.run_command("attr_transfer.paste")
        if result is not None:
            ui.toast(f"Pasted style onto {_plural(result['items'], 'item')}")
        return result

    ui.command("copy_selected", copy_selected, undoable=False, echo=False)
    ui.command("paste_dialog", paste_dialog, undoable=False, echo=False)
    ui.command("paste_style", paste_style, undoable=False, echo=False)

    ui.context_key("attr_transfer.copied", lambda: service.clipboard is not None)
    keywords = "copy paste attributes style properties match batch apply"
    ui.action("attr_transfer.copy", "attr_transfer.copy_selected", "Copy Attributes", keywords=keywords,
              category="Edit")
    ui.action("attr_transfer.paste_dialog", "attr_transfer.paste_dialog", "Paste Attributes…", keywords=keywords,
              category="Edit")
    ui.action("attr_transfer.paste_style", "attr_transfer.paste_style", "Paste Style Attributes", keywords=keywords,
              category="Edit")
    ui.bind("Ctrl+Alt+Shift+C", "attr_transfer.copy")
    ui.bind("Ctrl+Alt+Shift+V", "attr_transfer.paste_dialog")

    def can_paste() -> bool:
        return len(session.selection) > 0 and service.clipboard is not None

    ui.menu("canvas", "attr_transfer.copy", path="Attributes", group="attributes", order=0,
            when="selection.count > 0")
    ui.menu("canvas", "attr_transfer.paste_dialog", path="Attributes", group="attributes", order=1, when=can_paste)
    ui.menu("canvas", "attr_transfer.paste_style", path="Attributes", group="attributes", order=2, when=can_paste)
    ui.place_submenu("canvas", "Attributes", "state", 5, when="selection.count > 0")

    def shared_applies(info: SelectionInfo) -> bool:
        if info.single or not all(scene.has(n) for n in info.names):
            return False
        active = session.selection.active
        ref = active if active in info.names else info.names[-1]
        return bool(shared_attrs(scene, list(info.names), ref))

    ui.contribute("inspector.sections", InspectorSection(
        key="shared", title="Shared attributes", order=85, applies=shared_applies, generic=True,
        build=lambda parent, _inspector: SharedSection(ui, parent)))
    ui.contribute("inspector.rows", InspectorRows(
        section="appearance", order=50, build=lambda parent, _inspector: CopyPasteRow(ui, parent, service)))

    def refresh_panels() -> None:
        inspector = ui.service("inspector")
        for panel in list(inspector.panels) if inspector is not None else []:
            panel.refresh()

    ui.track(service.watch(refresh_panels))
