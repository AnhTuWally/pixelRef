"""The Keyboard Shortcuts editor: every action with its keys, editable.

Changes are written to ``keymap.toml`` in the user config folder (see
``pixelref.ui.keymap_file``) and applied at once, so the file and the editor
always agree and hand edits show up after *Reload*.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pixelref.ui import keymap_file
from pixelref.ui.actions import normalize_sequence
from pixelref.ui.widgets import action_label, capture_shortcut, run_keymap_edit

CATEGORY_ORDER = ("File", "Edit", "Create", "Tools", "Transform", "Arrange", "View", "Window", "Scripts",
                  "User Hotkeys", "Modes")
TARGET_ROLE = Qt.UserRole + 1


def native(sequence: str) -> str:
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)


class ShortcutEditor(QWidget):
    def __init__(self, app: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.keymap = app.keymap
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search actions, commands or keys (e.g. \"frame\" or \"Ctrl+D\")…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        layout.addWidget(self.search)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Action", "Shortcut", "ID"])
        self.tree.headerItem().setToolTip(2, "Use this in keymap.toml (command = …) and in macros (commands = […])")
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemDoubleClicked.connect(lambda item, _c: self.set_shortcut(item))
        self.tree.currentItemChanged.connect(lambda *_a: self.update_buttons())
        layout.addWidget(self.tree, 1)
        self.note = QLabel(self)
        self.note.setObjectName("pathLabel")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        row = QHBoxLayout()
        self.set_btn = self._button(row, "Set Shortcut…", lambda: self.set_shortcut(self.tree.currentItem()),
                                    "Replace this action's keys with a new one (or double-click the row)")
        self.add_btn = self._button(row, "Add Shortcut…", lambda: self.set_shortcut(self.tree.currentItem(), True),
                                    "Give this action another key, keeping the ones it has")
        self.remove_btn = self._button(row, "Remove", self.remove_current, "Take every key away from this action")
        self.reset_btn = self._button(row, "Reset", self.reset_current, "Back to the built-in keys")
        row.addStretch(1)
        self._button(row, "Reset All…", self.reset_all, "Undo every change to built-in shortcuts")
        self._button(row, "Open keymap.toml", self.open_file,
                     "Edit the file by hand: macros, Python, scripts, key modes (then Reload)")
        self._button(row, "Reload", self.reload, "Re-read keymap.toml")
        layout.addLayout(row)
        self.rebuild()

    def _button(self, row: QHBoxLayout, text: str, slot: Any, tip: str) -> QPushButton:
        button = QPushButton(text, self)
        button.setToolTip(tip)
        button.clicked.connect(slot)
        row.addWidget(button)
        return button

    # ---- Model -------------------------------------------------------------------------------

    def path(self) -> Path:
        return self.keymap.user_path()

    def shortcuts(self, target: str) -> list[str]:
        return self.keymap.shortcuts(target)

    def changed(self, target: str) -> bool:
        return self.keymap.changed(target)

    def rebuild(self) -> None:
        current = self.current_target()
        self.tree.clear()
        by_category: dict[str, list[tuple[str, str]]] = {}
        for key, spec in self.app.actions.specs.items():
            if spec.category in ("", "hidden"):
                continue
            text = self.app.actions.actions[key].text().replace("&&", "&")
            by_category.setdefault(spec.category, []).append((text, key))
        order = [c for c in CATEGORY_ORDER if c in by_category] + sorted(c for c in by_category
                                                                       if c not in CATEGORY_ORDER)
        bold = QFont(self.tree.font())
        bold.setBold(True)
        for category in order:
            parent = QTreeWidgetItem(self.tree, [category])
            parent.setFlags(Qt.ItemIsEnabled)
            parent.setFont(0, bold)
            for text, key in sorted(by_category[category], key=lambda e: e[0].lower()):
                item = QTreeWidgetItem(parent, [text, ", ".join(native(s) for s in self.shortcuts(key)), key])
                item.setData(0, TARGET_ROLE, key)
                if self.changed(key):
                    for column in (0, 1):
                        item.setFont(column, bold)
                    defaults = ", ".join(native(s) for s in self.keymap.default_sequences(key)) or "none"
                    item.setToolTip(1, f"Changed by you · built-in: {defaults}")
                if key == current:
                    self.tree.setCurrentItem(item)
        self._add_mode_rows(bold)
        self.tree.expandAll()
        for column in range(3):
            self.tree.resizeColumnToContents(column)
        self.apply_filter(self.search.text())
        self.update_buttons()

    def _add_mode_rows(self, bold: QFont) -> None:
        """Keys of the key modes (Ctrl+W, then …), read-only here: keymap.toml edits them."""
        modes: dict[str, list[Any]] = {}
        for binding in self.keymap.live_bindings():
            if binding.mode:
                modes.setdefault(binding.mode, []).append(binding)
        for mode, bindings in sorted(modes.items()):
            if mode == "window":
                entry = self.keymap.leader_sequence
            else:
                keys = self.shortcuts(f"keymap.mode.{mode}")
                entry = keys[0] if keys else ""
            title = self.keymap.mode_titles.get(mode, mode.title())
            parent = QTreeWidgetItem(self.tree, [f"{title} mode ({native(entry) or 'no key'}, then…)"])
            parent.setFlags(Qt.ItemIsEnabled)
            parent.setFont(0, bold)
            for binding in sorted(bindings, key=lambda b: b.sequence):
                action = self.app.actions.get(binding.target)
                text = action.text().replace("&&", "&") if action is not None else binding.target
                item = QTreeWidgetItem(parent, [text, native(binding.sequence), binding.target])
                item.setFlags(Qt.ItemIsEnabled)
                item.setToolTip(0, f'Mode keys are edited in keymap.toml ([[bind]] with mode = "{mode}")')

    def apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        as_key = normalize_sequence(text.strip()).lower() if text.strip() else ""
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            visible = 0
            for j in range(parent.childCount()):
                item = parent.child(j)
                hay = " ".join(item.text(c).lower() for c in range(3)) + " " + parent.text(0).lower()
                keys = item.text(1).lower().replace(" ", "")
                show = bool(not needle or needle in hay or (as_key and as_key.replace(" ", "") in keys))
                item.setHidden(not show)
                visible += show
            parent.setHidden(visible == 0)

    def current_target(self) -> str | None:
        item = self.tree.currentItem()
        return item.data(0, TARGET_ROLE) if item is not None else None

    def update_buttons(self) -> None:
        target = self.current_target()
        for button in (self.set_btn, self.add_btn, self.remove_btn):
            button.setEnabled(target is not None)
        self.reset_btn.setEnabled(target is not None and target not in self.keymap.user_targets
                                  and self.changed(target))
        self.note.setText(f"Saved to {self.path()} · macros, scripts and key modes: see Open keymap.toml")

    # ---- Edits -------------------------------------------------------------------------------

    def _apply(self, edit: Any) -> None:
        """Run a keymap edit (it writes keymap.toml and reloads it), then show the result."""
        if run_keymap_edit(edit, self):
            self.rebuild()

    def _edit(self, change: Any) -> None:
        self._apply(lambda: self.keymap.edit_user_file(change))

    def assign(self, target: str, keys: list[str], take_from: list[tuple[str, str]] = ()) -> None:
        """Give ``target`` exactly ``keys``; ``take_from`` lists (key, other target) to remove first."""
        self._apply(lambda: self.keymap.assign(target, keys, take_from))

    def set_shortcut(self, item: QTreeWidgetItem | None, add: bool = False) -> None:
        target = item.data(0, TARGET_ROLE) if item is not None else None
        if target and capture_shortcut(self.app, target, self, add=add, label=item.text(0)):
            self.rebuild()

    def _label(self, target: str) -> str:
        return action_label(self.app, target)

    def remove_current(self) -> None:
        target = self.current_target()
        if target:
            self.assign(target, [])

    def reset_current(self) -> None:
        target = self.current_target()
        if target and target not in self.keymap.user_targets:
            self._edit(lambda data: keymap_file.reset(data, target))

    def reset_all(self) -> None:
        answer = QMessageBox.question(self, "Keyboard Shortcuts",
                                      "Put every built-in shortcut back as it came?\n"
                                      "(Macros, scripts and key modes in keymap.toml are kept.)")
        if answer == QMessageBox.Yes:
            self._edit(keymap_file.reset_all)

    def open_file(self) -> None:
        path = self.path()
        if not path.exists():
            keymap_file.save(path, {})
            path.write_text(path.read_text(encoding="utf-8") + "\n" + _EXAMPLES, encoding="utf-8")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def reload(self) -> None:
        problems = self.keymap.load_user_file(self.path())
        if problems:
            QMessageBox.warning(self, "Keyboard Shortcuts", "\n".join(problems))
        self.rebuild()


_EXAMPLES = """# Examples (remove the # to use them):
#
# [[bind]]                       # a key for any action or command
# key = "Ctrl+Shift+D"
# command = "clipboard.duplicate"
#
# [[bind]]                       # a macro: commands in order, one undo step
# key = "Ctrl+Alt+T"
# name = "Tidy board"
# commands = ["select.all", "arrange.grid", "navigation.frame_all"]
#
# [[bind]]                       # Python (pxcmd, pxnode, sel(), session and app are defined)
# key = "Ctrl+Alt+1"
# name = "Rotate selection 45"
# python = "pxcmd.set_transform(sel(), rotate=45, relative=True)"
#
# [[bind]]                       # a script from the scripts folder (Scripts → Open Scripts Folder)
# key = "F5"
# script = "my_tool.py"
#
# [[unbind]]                     # free a built-in key
# key = "Ctrl+D"
# command = "clipboard.duplicate"
#
# [[mode]]                       # your own key mode: Ctrl+Space, then a key
# name = "macros"
# key = "Ctrl+Space"
# title = "Macros"
#
# [[bind]]
# mode = "macros"
# key = "1"
# script = "my_tool.py"
"""
