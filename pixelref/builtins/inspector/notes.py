"""Attribute Editor sections for a node's free-form ``note`` and ``metadata``."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCompleter, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget

from pixelref.plugin_api.contracts import SelectionInfo
from pixelref.plugin_api.ui import widgets as w


def format_value(value: Any) -> str:
    """Text for a metadata value. Text that would read back as another type is quoted."""
    if isinstance(value, str):
        return json.dumps(value) if parse_value(value) != value else value
    return "true" if value is True else "false" if value is False else json.dumps(value)


def parse_value(text: str) -> Any:
    """Typed text: numbers and true/false become those, "quoted" text is text, anything else is text."""
    stripped = text.strip()
    if stripped in ("true", "false"):
        return stripped == "true"
    if stripped and (stripped[0] in "-0123456789" or stripped[0] == '"'):
        try:
            value = json.loads(stripped)
        except ValueError:
            return text
        if isinstance(value, (int, float, str)):
            return value
    return text


class NoteSection(w.InspectorSection):
    """A multi-line note; applied when the box loses focus (one undo step)."""

    def __init__(self, ui: Any, parent: QWidget) -> None:
        super().__init__("note", "Note", parent)
        self.ui = ui
        self._name: str | None = None
        self._shown = ""
        self.edit = w.TextField(self.body)
        self.edit.setPlaceholderText("Add a note about this item…")
        self.edit.setMinimumHeight(72)
        self.edit.setMaximumHeight(180)
        self.edit.setTabChangesFocus(True)
        self.edit.editingFinished.connect(self._commit)
        self.form.addRow(self.edit)

    def _commit(self) -> None:
        text = self.edit.text()
        if self._name is None or text == self._shown or not self.ui.session.scene.has(self._name):
            return
        self._shown = text
        self.ui.app.run_command("set_attr", self._name, "note", text)

    def refresh(self, info: SelectionInfo) -> None:
        if info.names[0] != self._name:
            self._commit()  # typing not yet applied belongs to the node it was typed for
            self._name = info.names[0]
        text = str(info.attrs.get("note") or "")
        if text != self._shown or not self.edit.hasFocus():
            self._shown = text
            self.edit.setText(text)


class _Row(QWidget):
    def __init__(self, key: str, value: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self.original = value
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.key_edit = QLineEdit(key, self)
        self.key_edit.setPlaceholderText("key")
        self.key_edit.setMinimumWidth(50)
        self.value_edit = QLineEdit(format_value(value) if key else "", self)
        self.value_edit.setPlaceholderText("value")
        self.value_edit.setToolTip('Text, a number, or true / false. Put "quotes" around text that looks like '
                                   "a number or true / false.")
        self.remove_btn = w.make_tool_button("✕", "Remove this field", width=24, parent=self)
        layout.addWidget(self.key_edit, 2)
        layout.addWidget(self.value_edit, 3)
        layout.addWidget(self.remove_btn)
        self.shown_value = self.value_edit.text()

    def key(self) -> str:
        return self.key_edit.text().strip()

    def value(self) -> Any:
        text = self.value_edit.text()
        if text == self.shown_value and self.key_edit.text():
            return self.original  # untouched: keeps lists / dicts a script stored
        return parse_value(text)


class MetadataSection(w.InspectorSection):
    """Key / value fields on a node: one row each, edited in place."""

    def __init__(self, ui: Any, parent: QWidget) -> None:
        super().__init__("metadata", "Metadata", parent)
        self.ui = ui
        self._name: str | None = None
        self._data: dict[str, Any] = {}
        self._rows: list[_Row] = []
        self.rows_box = QWidget(self.body)
        self.rows_layout = QVBoxLayout(self.rows_box)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(4)
        self.add_btn = QPushButton("+ Add field", self.body)
        self.add_btn.setToolTip("Add a key / value pair")
        self.add_btn.setFocusPolicy(Qt.NoFocus)
        self.add_btn.clicked.connect(self._add_blank)
        self.form.addRow(self.rows_box)
        self.form.addRow(self.add_btn)
        self._completer_model: Any = None

    # ---- Rows ----------------------------------------------------------------------------

    def _known_keys(self) -> list[str]:
        keys: set[str] = set()
        for node in self.ui.canvas.nodes.values():
            meta = node.get("attrs", {}).get("metadata")
            if isinstance(meta, dict):
                keys.update(meta)
        return sorted(keys)

    def _rebuild(self, data: dict[str, Any]) -> None:
        for row in self._rows:
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        for key, value in data.items():
            self._add_row(key, value)

    def _add_row(self, key: str, value: Any) -> _Row:
        row = _Row(key, value, self.rows_box)
        completer = QCompleter(self._known_keys(), row.key_edit)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        row.key_edit.setCompleter(completer)
        row.key_edit.editingFinished.connect(lambda r=row: self._commit(r))
        row.value_edit.editingFinished.connect(lambda r=row: self._commit(r))
        row.remove_btn.clicked.connect(lambda _c=False, r=row: self._remove(r))
        self.rows_layout.addWidget(row)
        self._rows.append(row)
        return row

    def _add_blank(self) -> None:
        for row in self._rows:
            if not row.key():
                row.key_edit.setFocus()  # a blank row is already waiting
                return
        self._add_row("", "").key_edit.setFocus()

    def _remove(self, row: _Row) -> None:
        self._rows.remove(row)
        row.hide()
        row.setParent(None)
        row.deleteLater()
        self._apply()

    def _commit(self, row: _Row) -> None:
        key = row.key()
        if key and sum(1 for r in self._rows if r.key() == key) > 1:
            self.ui.toast(f"There is already a metadata field named '{key}'")
            self._rebuild(self._data)
            return
        self._apply()

    def _apply(self) -> None:
        data = {row.key(): row.value() for row in self._rows if row.key()}
        if self._name is None or data == self._data and list(data) == list(self._data):
            return
        self._data = data
        self.ui.app.run_command("set_attr", self._name, "metadata", data)

    # ---- Refresh --------------------------------------------------------------------------

    def refresh(self, info: SelectionInfo) -> None:
        name = info.names[0]
        meta = info.attrs.get("metadata")
        data = dict(meta) if isinstance(meta, dict) else {}
        if name != self._name or data != self._data or list(data) != list(self._data):
            self._name = name
            self._data = data
            self._rebuild(data)
