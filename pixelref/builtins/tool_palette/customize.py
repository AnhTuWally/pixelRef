"""Customize Palette: the order of the buttons, sections, hidden buttons and their keys.

Every change applies at once (like the shortcut editor): the layout goes to the
user settings, the keys to keymap.toml.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pixelref.plugin_api.ui import icon
from pixelref.plugin_api.ui import widgets as w

from .layout import HOME_SECTIONS

KEY_ROLE = Qt.UserRole + 1
SECTION_ROLE = Qt.UserRole + 2


def native(sequence: str) -> str:
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)


class LayoutTree(QTreeWidget):
    """Sections with their buttons; drag buttons between sections, and sections around."""

    def __init__(self, on_moved: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.on_moved = on_moved
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

    def drop_allowed(self, dragged: QTreeWidgetItem, target: QTreeWidgetItem | None, position: Any) -> bool:
        positions = QAbstractItemView.DropIndicatorPosition
        if dragged.parent() is None:  # a section: only between sections
            return target is None or (target.parent() is None and position != positions.OnItem)
        if target is None:
            return False
        if target.parent() is None:  # a button onto a section header: to the end of it
            return position == positions.OnItem
        return position in (positions.AboveItem, positions.BelowItem)

    def dropEvent(self, event) -> None:  # noqa: ANN001, N802
        dragged = self.currentItem()
        target = self.itemAt(event.position().toPoint())
        if dragged is None or not self.drop_allowed(dragged, target, self.dropIndicatorPosition()):
            event.ignore()
            return
        super().dropEvent(event)
        self.setCurrentItem(dragged)
        self.on_moved()


class CustomizeDialog(QDialog):
    def __init__(self, ui: Any, service: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ui = ui
        self.app = ui.app
        self.service = service
        self.setWindowTitle("Customize Tools Palette")
        self.resize(560, 520)
        layout = QVBoxLayout(self)
        intro = QLabel("Drag buttons to reorder them or move them to another section · untick to hide · "
                       "double-click a shortcut to change it · double-click a section to rename it", self)
        intro.setWordWrap(True)
        intro.setObjectName("pathLabel")
        layout.addWidget(intro)
        self.tree = LayoutTree(self.save, self)
        self.tree.setHeaderLabels(["Button", "Shortcut", "ID"])
        self.tree.setRootIsDecorated(False)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.currentItemChanged.connect(lambda *_a: self.update_buttons())
        layout.addWidget(self.tree, 1)

        row = QHBoxLayout()
        self.key_btn = self._button(row, "Set Shortcut…", self.set_shortcut, "Press a key for this button")
        self.reset_key_btn = self._button(row, "Reset Shortcut", self.reset_shortcut, "Back to the built-in key")
        self.up_btn = self._button(row, "Up", lambda: self.move_current(-1), "Move up", "chevron_up")
        self.down_btn = self._button(row, "Down", lambda: self.move_current(1), "Move down", "chevron_down")
        row.addStretch(1)
        layout.addLayout(row)
        row = QHBoxLayout()
        self._button(row, "Add Command…", self.add_command, "Put any action, script or macro on the palette")
        self._button(row, "New Section…", self.new_section, "A section of your own")
        self.remove_btn = self._button(row, "Remove", self.remove_current,
                                       "Take a button you added off the palette, or remove a section of yours")
        row.addStretch(1)
        self._button(row, "Reset Palette…", self.reset_layout, "Back to the default palette (keys stay)")
        self._button(row, "Close", self.accept, "")
        layout.addLayout(row)
        self._keymap_watch = self.app.keymap.watch(self.refresh_keys)
        self.finished.connect(lambda *_a: self._keymap_watch.dispose())
        self.rebuild()

    def _button(self, row: QHBoxLayout, text: str, slot: Any, tip: str, icon_name: str = "") -> QPushButton:
        button = QPushButton(text, self)
        if icon_name:
            button.setIcon(icon(icon_name))
        button.setToolTip(tip)
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        row.addWidget(button)
        return button

    # ---- Model -------------------------------------------------------------------------------

    def rebuild(self) -> None:
        current = self.current_key() or self.current_section()
        sections, hidden = self.service.all_sections()
        bold = QFont(self.tree.font())
        bold.setBold(True)
        self.tree.blockSignals(True)
        self.tree.clear()
        for section in sections:
            parent = QTreeWidgetItem(self.tree, [section.title or section.id])
            parent.setData(0, SECTION_ROLE, section.id)
            parent.setFont(0, bold)
            parent.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable | Qt.ItemIsDragEnabled
                            | Qt.ItemIsDropEnabled)
            if section.id in HOME_SECTIONS:
                parent.setToolTip(0, "Where plugins put their new buttons · double-click to rename")
            if current == section.id:
                self.tree.setCurrentItem(parent)
            for key in section.items:
                item = QTreeWidgetItem(parent, [w.action_label(self.app, key), "", key])
                item.setData(0, KEY_ROLE, key)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
                item.setCheckState(0, Qt.Unchecked if key in hidden else Qt.Checked)
                action = self.app.actions.get(key)
                if action is None:
                    item.setForeground(0, self.palette().placeholderText())
                    item.setToolTip(0, "Its plugin is off: it comes back here when the plugin is on again")
                elif not action.icon().isNull():
                    item.setIcon(0, action.icon())
                if current == key:
                    self.tree.setCurrentItem(item)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        self.refresh_keys()
        self.tree.resizeColumnToContents(0)
        self.update_buttons()

    def refresh_keys(self) -> None:
        keymap = self.app.keymap
        self.tree.blockSignals(True)
        for item in self._button_items():
            key = item.data(0, KEY_ROLE)
            keys = keymap.shortcuts(key)
            shared = [k for k in keys if len(keymap.targets_of(k)) > 1]
            item.setText(1, ", ".join(native(k) for k in keys) + ("  ⚠" if shared else ""))
            tip = "Double-click to change"
            if shared:
                names = ", ".join(w.action_label(self.app, t) for k in shared for t in keymap.targets_of(k) if t != key)
                tip = f"Also used by {names}: double-click to give this button a key of its own"
            elif keymap.changed(key):
                defaults = ", ".join(native(k) for k in keymap.default_sequences(key)) or "none"
                tip = f"Changed by you · built-in: {defaults}"
            item.setToolTip(1, tip)
        self.tree.blockSignals(False)

    def _button_items(self) -> list[QTreeWidgetItem]:
        return [self.tree.topLevelItem(i).child(j) for i in range(self.tree.topLevelItemCount())
                for j in range(self.tree.topLevelItem(i).childCount())]

    def current_key(self) -> str | None:
        item = self.tree.currentItem()
        return item.data(0, KEY_ROLE) if item is not None else None

    def current_section(self) -> str | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        return (item.parent() or item).data(0, SECTION_ROLE)

    def update_buttons(self) -> None:
        key = self.current_key()
        item = self.tree.currentItem()
        self.key_btn.setEnabled(key is not None and key in self.app.actions)
        self.reset_key_btn.setEnabled(key is not None and self.app.keymap.changed(key)
                                      and key not in self.app.keymap.user_targets)
        self.up_btn.setEnabled(item is not None)
        self.down_btn.setEnabled(item is not None)
        section = self.current_section()
        self.remove_btn.setEnabled((key is not None and not self.service.is_default(key))
                                   or (key is None and section is not None and section not in HOME_SECTIONS))

    def save(self) -> None:
        """Keep the tree as the layout."""
        sections, hidden = [], set()
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            items = []
            for j in range(parent.childCount()):
                child = parent.child(j)
                items.append(child.data(0, KEY_ROLE))
                if child.checkState(0) != Qt.Checked:
                    hidden.add(child.data(0, KEY_ROLE))
            sections.append({"id": parent.data(0, SECTION_ROLE), "title": parent.text(0), "items": items})
        self.service.set_layout(sections, hidden)
        self.update_buttons()

    # ---- Edits -------------------------------------------------------------------------------

    def _item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column == 0:
            self.save()

    def _double_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if item.parent() is None:
            self.tree.editItem(item, 0)
        elif column != 0:
            self.set_shortcut()

    def set_shortcut(self) -> None:
        key = self.current_key()
        if key and key in self.app.actions:
            w.capture_shortcut(self.app, key, self)
            self.update_buttons()

    def reset_shortcut(self) -> None:
        key = self.current_key()
        if key:
            w.run_keymap_edit(lambda: self.app.keymap.reset_shortcuts(key), self)
            self.update_buttons()

    def move_current(self, step: int) -> None:
        """Move the current button (into the next section at either end) or section."""
        item = self.tree.currentItem()
        if item is None:
            return
        self.tree.blockSignals(True)
        parent = item.parent()
        if parent is None:
            index = self.tree.indexOfTopLevelItem(item)
            if 0 <= index + step < self.tree.topLevelItemCount():
                expanded = item.isExpanded()
                self.tree.takeTopLevelItem(index)
                self.tree.insertTopLevelItem(index + step, item)
                item.setExpanded(expanded)
        else:
            index = parent.indexOfChild(item)
            section_index = self.tree.indexOfTopLevelItem(parent)
            if 0 <= index + step < parent.childCount():
                parent.takeChild(index)
                parent.insertChild(index + step, item)
            elif 0 <= section_index + step < self.tree.topLevelItemCount():
                other = self.tree.topLevelItem(section_index + step)
                parent.takeChild(index)
                other.insertChild(other.childCount() if step < 0 else 0, item)
                other.setExpanded(True)
        self.tree.setCurrentItem(item)
        self.tree.blockSignals(False)
        self.save()

    def add_command(self) -> None:
        key = pick_action(self.app, self, exclude=set(self.service.all_keys()))
        if key:
            self.service.add(key, self.current_section() or "tools")
            self.rebuild()
            self._select(key)

    def new_section(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        title, ok = QInputDialog.getText(self, "New Section", "Section name:")
        if ok and title.strip():
            self.service.add_section(title.strip())
            self.rebuild()

    def remove_current(self) -> None:
        key = self.current_key()
        if key is not None:
            self.service.remove(key)
        elif self.current_section() not in (None, *HOME_SECTIONS):
            self.service.remove_section(self.current_section())
        self.rebuild()

    def reset_layout(self) -> None:
        answer = QMessageBox.question(self, "Customize Tools Palette",
                                      "Put the palette back as the plugins arrange it?\n"
                                      "(Your sections and added buttons go; your keys stay.)")
        if answer == QMessageBox.Yes:
            self.service.reset()
            self.rebuild()

    def _select(self, key: str) -> None:
        for item in self._button_items():
            if item.data(0, KEY_ROLE) == key:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)


class ActionPicker(QDialog):
    """Pick any action (by name, category, ID or key)."""

    def __init__(self, app: Any, parent: QWidget | None = None, exclude: set[str] | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.key: str | None = None
        self.setWindowTitle("Add Command to Palette")
        self.resize(460, 480)
        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search commands, scripts, macros…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        self.search.returnPressed.connect(self._accept_first)
        layout.addWidget(self.search)
        self.list = QListWidget(self)
        self.list.itemActivated.connect(self._accept_item)
        layout.addWidget(self.list, 1)
        exclude = exclude or set()
        entries = []
        for key, spec in app.actions.specs.items():
            if spec.category in ("", "hidden") or key in exclude:
                continue
            entries.append((spec.category, w.action_label(app, key), key))
        for category, text, key in sorted(entries, key=lambda e: (e[0], e[1].lower())):
            shortcut = app.actions.shortcut_label(key, compact=False)
            item = QListWidgetItem(f"{text}   ·   {category}" + (f"   ·   {shortcut}" if shortcut else ""), self.list)
            item.setData(Qt.UserRole, key)
            item.setToolTip(key)
            action = app.actions.get(key)
            if action is not None and not action.icon().isNull():
                item.setIcon(action.icon())
        self.search.setFocus()

    def apply_filter(self, text: str) -> None:
        words = text.lower().split()
        first = None
        for i in range(self.list.count()):
            item = self.list.item(i)
            hay = (item.text() + " " + item.data(Qt.UserRole)).lower()
            show = all(word in hay for word in words)
            item.setHidden(not show)
            if show and first is None:
                first = item
        if first is not None:
            self.list.setCurrentItem(first)

    def _accept_first(self) -> None:
        item = self.list.currentItem()
        if item is not None and not item.isHidden():
            self._accept_item(item)

    def _accept_item(self, item: QListWidgetItem) -> None:
        self.key = item.data(Qt.UserRole)
        self.accept()


def pick_action(app: Any, parent: QWidget | None = None, exclude: set[str] | None = None) -> str | None:
    dialog = ActionPicker(app, parent, exclude)
    return dialog.key if dialog.exec() == QDialog.Accepted else None
