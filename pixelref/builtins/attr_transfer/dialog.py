"""The Paste Attributes pop-up: a checklist of the copied attributes, in groups, with a master
toggle, a search bar, multi-row selection and a live preview on the canvas."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QCursor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pixelref.core import transfer
from pixelref.core.values import fast_copy

NAME_ROLE = Qt.UserRole
GROUP_ROLE = Qt.UserRole + 1
COLLAPSED_GROUPS = {transfer.GEOMETRY_GROUP}  # the groups that start closed
SETTINGS_KEY = "paste_ticks"


# ---- Showing values -----------------------------------------------------------------------------------


def _number(value: Any, precision: int = 2) -> str:
    try:
        return f"{float(value):.{precision}f}".rstrip("0").rstrip(".") or "0"
    except (TypeError, ValueError):
        return str(value)


def describe(spec: Any, value: Any) -> str:
    """A short text for an attribute value."""
    kind = spec.type
    if value is None:
        return "none"
    if kind in ("color", "color4"):
        try:
            parts = [int(v) for v in value]
            text = "#%02X%02X%02X" % tuple(parts[:3])
            return text + (" α%d%%" % round(parts[3] / 2.55) if len(parts) > 3 and parts[3] != 255 else "")
        except (TypeError, ValueError):
            return str(value)
    if kind in ("flag", "bool"):
        return "On" if value else "Off"
    if kind in ("int", "float"):
        scaled = float(value) * (spec.display_scale or 1.0)
        return _number(scaled, 0 if kind == "int" else (spec.precision if spec.precision is not None else 2)) + (
            f" {spec.unit}" if spec.unit else "")
    if kind == "float2":
        return ", ".join(_number(v) for v in value)
    if kind in ("enum", "string", "text"):
        return str(value).replace("_", " ") if kind == "enum" else (str(value) or "empty")
    text = json.dumps(value) if not isinstance(value, str) else value
    return text if len(text) <= 28 else text[:27] + "…"


def swatch_icon(spec: Any, value: Any) -> QIcon:
    if spec.type not in ("color", "color4") or not value:
        return QIcon()
    try:
        parts = [int(v) for v in value]
    except (TypeError, ValueError):
        return QIcon()
    pixmap = QPixmap(14, 14)
    pixmap.fill(QColor(parts[0], parts[1], parts[2]))
    return QIcon(pixmap)


# ---- The tree ------------------------------------------------------------------------------------------


class MasterCheck(QCheckBox):
    """A tri-state display that a click only ever turns fully on or off."""

    def nextCheckState(self) -> None:  # noqa: N802
        self.setCheckState(Qt.Unchecked if self.checkState() == Qt.Checked else Qt.Checked)


class AttrTree(QTreeWidget):
    """Space ticks or unticks every selected row at once."""

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802
        if event.key() == Qt.Key_Space and event.modifiers() == Qt.NoModifier and self.currentItem() is not None:
            current = self.currentItem()
            wanted = Qt.Unchecked if current.checkState(0) == Qt.Checked else Qt.Checked
            self.setSelectedState(wanted)
            event.accept()
            return
        super().keyPressEvent(event)

    def setSelectedState(self, state: Qt.CheckState) -> None:  # noqa: N802
        items = self.selectedItems() or ([self.currentItem()] if self.currentItem() is not None else [])
        for item in items:
            if item.parent() is None:  # a group stands for its shown rows
                for index in range(item.childCount()):
                    child = item.child(index)
                    if not child.isHidden() and child.flags() & Qt.ItemIsEnabled:
                        child.setCheckState(0, state)
            else:
                item.setCheckState(0, state)


# ---- The preview ---------------------------------------------------------------------------------------


class Preview:
    """Shows the paste on the canvas without recording anything; put back on cancel and
    before the real paste, so the undo step is the paste alone."""

    def __init__(self, ui: Any, snap: transfer.Snapshot, targets: list[str]) -> None:
        self.ui = ui
        self.scene = ui.session.scene
        self.snap = snap
        self.targets = targets
        self.saved: dict[str, dict[str, Any]] = {}

    def show(self, names: list[str]) -> None:
        self.restore(refresh=False)
        for target in self.targets:
            node = self.scene.get_node(target)
            for name in names:
                if self.scene.attr_spec(node, name) is not None:
                    self.saved.setdefault(target, {})[name] = transfer.value_of(self.scene, target, name)
        specs = {n: self.snap.entries[n].spec for n in names}
        transfer.apply_values(self.scene, self.snap.values(names), self.targets, "multi", specs, self.snap.source)
        self.ui.canvas.refresh()

    def restore(self, refresh: bool = True) -> None:
        saved, self.saved = self.saved, {}
        for target, attrs in saved.items():
            if self.scene.has(target):
                self.scene.set_attrs(target, attrs)
        if saved and refresh:
            self.ui.canvas.refresh()


# ---- The dialog ----------------------------------------------------------------------------------------


class PasteDialog(QDialog):
    def __init__(self, ui: Any, snap: transfer.Snapshot, targets: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ui = ui
        self.snap = snap
        self.targets = targets
        self.scene = ui.session.scene
        self.preview = Preview(ui, snap, targets)
        self.chosen: list[str] = []
        self._busy = False
        self._expanded: dict[str, bool] = {}
        self.setWindowTitle("Paste Attributes")
        self.setMinimumSize(420, 380)
        self.resize(460, 600)
        self.setSizeGripEnabled(True)

        count = len(targets)
        outer = QVBoxLayout(self)
        outer.setSpacing(6)
        title = QLabel(f"Paste attributes from <b>{snap.source}</b> onto {count} item{'s' if count != 1 else ''}", self)
        title.setTextFormat(Qt.RichText)
        outer.addWidget(title)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search attributes…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        outer.addWidget(self.search)
        top = QHBoxLayout()
        self.master = MasterCheck("All attributes", self)
        self.master.setToolTip("Tick or untick every attribute listed, including closed groups")
        self.master.clicked.connect(self._on_master)
        self.differences = QCheckBox("Only differences", self)
        self.differences.setToolTip("Hide attributes whose value the items already have")
        self.differences.toggled.connect(self._filter)
        top.addWidget(self.master)
        top.addStretch()
        top.addWidget(self.differences)
        outer.addLayout(top)

        self.tree = AttrTree(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Attribute", "Value", "Applies to"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header().ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, self.tree.header().ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, self.tree.header().ResizeMode.ResizeToContents)
        outer.addWidget(self.tree, 1)

        bottom = QHBoxLayout()
        self.tick_btn = QPushButton("Tick selected", self)
        self.untick_btn = QPushButton("Untick selected", self)
        self.tick_btn.clicked.connect(lambda: self.tree.setSelectedState(Qt.Checked))
        self.untick_btn.clicked.connect(lambda: self.tree.setSelectedState(Qt.Unchecked))
        self.selected_label = QLabel("", self)
        bottom.addWidget(self.selected_label)
        bottom.addWidget(self.tick_btn)
        bottom.addWidget(self.untick_btn)
        bottom.addStretch()
        outer.addLayout(bottom)
        self.summary = QLabel("", self)
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_btn = QPushButton("Cancel", self)
        self.paste_btn = QPushButton("Paste", self)
        self.paste_btn.setDefault(True)
        self.cancel_btn.clicked.connect(self.reject)
        self.paste_btn.clicked.connect(self.accept)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.paste_btn)
        outer.addLayout(buttons)

        self._build()
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemSelectionChanged.connect(self._on_selection)
        self.tree.itemExpanded.connect(lambda item: self._remember(item, True))
        self.tree.itemCollapsed.connect(lambda item: self._remember(item, False))
        self._filter()
        self._on_selection()

    # ---- Building ---------------------------------------------------------------------------------------

    def _remembered(self) -> tuple[set[str], set[str]]:
        raw = self.ui.settings.value(f"{SETTINGS_KEY}/{self.snap.node_type}", "")
        try:
            data = json.loads(raw) if isinstance(raw, str) and raw else {}
        except ValueError:
            data = {}
        return set(data.get("on", [])), set(data.get("off", []))

    def _build(self) -> None:
        on, off = self._remembered()
        plan = transfer.plan(self.scene, self.snap, self.targets, "multi")
        total = len(self.targets)
        groups: dict[str, QTreeWidgetItem] = {}
        order = sorted(self.snap.entries.values(), key=lambda e: (e.group == transfer.GEOMETRY_GROUP, 0))
        self._busy = True
        for entry in order:
            group = entry.group
            if group not in groups:
                item = QTreeWidgetItem(self.tree, [group, "", ""])
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                item.setData(0, GROUP_ROLE, group)
                item.setCheckState(0, Qt.Unchecked)
                groups[group] = item
            applicable = plan[entry.name]
            leaf = QTreeWidgetItem(groups[group])
            leaf.setData(0, NAME_ROLE, entry.name)
            leaf.setText(0, entry.label)
            leaf.setToolTip(0, f"{entry.name}\n{entry.spec.doc}".strip())
            flags = Qt.ItemIsUserCheckable | Qt.ItemIsSelectable
            if applicable:
                flags |= Qt.ItemIsEnabled
            leaf.setFlags(flags)
            ticked = (entry.name in on) or (entry.name not in off and entry.kind == transfer.STYLE)
            leaf.setCheckState(0, Qt.Checked if applicable and ticked else Qt.Unchecked)
            current = [transfer.value_of(self.scene, t, entry.name) for t in applicable]
            differing = [c for c in current if c != entry.value]
            new_text = describe(entry.spec, entry.value)
            if not applicable:
                value_text, tip = new_text, "No selected item has this attribute with the same type"
            elif not differing:
                value_text, tip = f"{new_text}  (same)", ""
            else:
                distinct = {json.dumps(c, sort_keys=True) for c in current}
                before = describe(entry.spec, current[0]) if len(distinct) == 1 else "mixed"
                value_text, tip = f"{before} → {new_text}", ""
            leaf.setText(1, value_text)
            leaf.setIcon(1, swatch_icon(entry.spec, entry.value))
            if tip:
                leaf.setToolTip(1, tip)
            leaf.setText(2, f"{len(applicable)} of {total}" if applicable else "—")
            leaf.setData(1, Qt.UserRole, bool(differing))  # something would change
        self._busy = False
        for group, item in groups.items():
            expanded = group not in COLLAPSED_GROUPS
            self._expanded[group] = expanded
            item.setExpanded(expanded)

    # ---- Reading and writing the ticks ---------------------------------------------------------------------

    def _groups(self) -> list[QTreeWidgetItem]:
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    @staticmethod
    def _leaves_of(group: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        return [group.child(i) for i in range(group.childCount())]

    def _shown_leaves(self) -> list[QTreeWidgetItem]:
        return [leaf for group in self._groups() for leaf in self._leaves_of(group)
                if not leaf.isHidden() and leaf.flags() & Qt.ItemIsEnabled]

    def ticked(self) -> list[str]:
        """The attributes that will be pasted (ticked, and some item can take them), shown or not."""
        return [leaf.data(0, NAME_ROLE) for group in self._groups() for leaf in self._leaves_of(group)
                if leaf.flags() & Qt.ItemIsEnabled and leaf.checkState(0) == Qt.Checked]

    def _state_of(self, leaves: list[QTreeWidgetItem]) -> Qt.CheckState:
        states = {leaf.checkState(0) for leaf in leaves}
        if states == {Qt.Checked}:
            return Qt.Checked
        if not states or states == {Qt.Unchecked}:
            return Qt.Unchecked
        return Qt.PartiallyChecked

    def _sync(self) -> None:
        """Bring the groups, the master toggle, the summary and the preview in line with the rows."""
        self._busy = True
        try:
            for group in self._groups():
                shown = [leaf for leaf in self._leaves_of(group)
                         if not leaf.isHidden() and leaf.flags() & Qt.ItemIsEnabled]
                group.setCheckState(0, self._state_of(shown))
            self.master.setCheckState(self._state_of(self._shown_leaves()))
        finally:
            self._busy = False
        names = self.ticked()
        self.chosen = names
        self.paste_btn.setEnabled(bool(names))
        if names:
            self.preview.show(names)
        else:
            self.preview.restore()
        self._summarize(names)

    def _summarize(self, names: list[str]) -> None:
        if not names:
            self.summary.setText("Nothing ticked.")
            return
        plan = transfer.plan(self.scene, self.snap, self.targets, "multi")
        reached = {target for name in names for target in plan[name]}
        self.summary.setText(f"{len(names)} attribute{'s' if len(names) != 1 else ''} ticked · applies to "
                             f"{len(reached)} of {len(self.targets)} items")

    # ---- Reacting to the user -------------------------------------------------------------------------------

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._busy or column != 0:
            return
        state = item.checkState(0)
        self._busy = True
        try:
            if item.parent() is None:  # a group: its shown rows follow
                for leaf in self._leaves_of(item):
                    if not leaf.isHidden() and leaf.flags() & Qt.ItemIsEnabled:
                        leaf.setCheckState(0, Qt.Checked if state == Qt.Checked else Qt.Unchecked)
            else:  # a row inside a multi-row selection takes the others with it
                selected = [i for i in self.tree.selectedItems() if i.parent() is not None]
                if item in selected and len(selected) > 1:
                    for other in selected:
                        if other is not item and other.flags() & Qt.ItemIsEnabled:
                            other.setCheckState(0, state)
        finally:
            self._busy = False
        self._sync()

    def _on_master(self, checked: bool) -> None:
        self._busy = True
        try:
            for leaf in self._shown_leaves():
                leaf.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
        finally:
            self._busy = False
        self._sync()

    def _on_selection(self) -> None:
        rows = [i for i in self.tree.selectedItems()]
        self.tick_btn.setVisible(bool(rows))
        self.untick_btn.setVisible(bool(rows))
        self.selected_label.setVisible(bool(rows))
        self.selected_label.setText(f"{len(rows)} selected")

    def _remember(self, item: QTreeWidgetItem, expanded: bool) -> None:
        group = item.data(0, GROUP_ROLE)
        if group is not None and not self._filtering():
            self._expanded[group] = expanded

    def _filtering(self) -> bool:
        return bool(self.search.text().strip()) or self.differences.isChecked()

    def _filter(self, *_args: Any) -> None:
        text = self.search.text().strip().lower()
        only_differences = self.differences.isChecked()
        self.master.setText("All shown" if self._filtering() else "All attributes")
        self.tree.blockSignals(True)
        try:
            for group in self._groups():
                name = group.data(0, GROUP_ROLE)
                visible = 0
                for leaf in self._leaves_of(group):
                    entry = self.snap.entries[leaf.data(0, NAME_ROLE)]
                    matches = not text or any(text in part.lower() for part in (entry.label, entry.name, group.text(0)))
                    changes = not only_differences or (bool(leaf.flags() & Qt.ItemIsEnabled) and bool(
                        leaf.data(1, Qt.UserRole)))
                    leaf.setHidden(not (matches and changes))
                    visible += 0 if leaf.isHidden() else 1
                group.setHidden(visible == 0)
                if self._filtering():
                    group.setExpanded(visible > 0)
                else:
                    group.setExpanded(self._expanded.get(name, True))
        finally:
            self.tree.blockSignals(False)
        self._sync()

    # ---- Finishing --------------------------------------------------------------------------------------------

    def _remember_ticks(self) -> None:
        on, off = self._remembered()
        for group in self._groups():
            for leaf in self._leaves_of(group):
                if not leaf.flags() & Qt.ItemIsEnabled:
                    continue
                name = leaf.data(0, NAME_ROLE)
                (on if leaf.checkState(0) == Qt.Checked else off).add(name)
                (off if leaf.checkState(0) == Qt.Checked else on).discard(name)
        self.ui.settings.setValue(f"{SETTINGS_KEY}/{self.snap.node_type}",
                                  json.dumps({"on": sorted(on), "off": sorted(off)}))

    def accept(self) -> None:
        names = self.ticked()
        self.preview.restore(refresh=False)
        self._remember_ticks()
        self.chosen = names
        super().accept()

    def reject(self) -> None:
        self.preview.restore()
        super().reject()

    def done(self, code: int) -> None:  # closing with the window button also puts everything back
        if code != QDialog.Accepted:
            self.preview.restore()
        super().done(code)

    def place_near_cursor(self) -> None:
        screen = self.screen().availableGeometry() if self.screen() else None
        pos = QCursor.pos() + QPoint(16, 16)
        if screen is not None:
            pos.setX(max(screen.left(), min(pos.x(), screen.right() - self.width())))
            pos.setY(max(screen.top(), min(pos.y(), screen.bottom() - self.height())))
        self.move(pos)


def ask_paste(ui: Any, snap: transfer.Snapshot, targets: list[str]) -> list[str] | None:
    """Run the pop-up; the attributes to paste, or None when cancelled."""
    dialog = PasteDialog(ui, snap, targets, ui.app.main_window())
    dialog.place_near_cursor()
    result = dialog.exec()
    names = list(dialog.chosen)
    dialog.deleteLater()
    return names if result == QDialog.Accepted and names else None
