"""Qt side of the file plugin: where new scenes are saved and how they're named."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QCheckBox, QFormLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from pixelref.plugin_api.ui import widgets as w

from . import FUN_NAMES_SETTING, SAVE_DIR_SETTING, fun_name, save_dir


class FilesPanel(QWidget):
    """Save location and fun names, shown in the Attribute Editor when nothing is selected."""

    def __init__(self, ui: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self.ui = ui
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(w.section_header(self, "Files"))
        form = QFormLayout()
        form.setContentsMargins(2, 0, 2, 0)
        form.setSpacing(6)
        self.dir_edit = QLineEdit(self)
        self.dir_edit.setPlaceholderText("Documents/pixelRef (default)")
        self.dir_edit.setReadOnly(True)
        browse = QPushButton("Browse…", self)
        browse.clicked.connect(lambda: self.ui.app.run_command("file.pick_save_dir"))
        form.addRow("Save to", w.hrow(self, self.dir_edit, browse))
        self.fun_names = QCheckBox("Fun names for new scenes (e.g. " + fun_name(self.ui.settings) + ".pxref)", self)
        self.fun_names.toggled.connect(self._on_fun_names)
        form.addRow("", self.fun_names)
        layout.addLayout(form)
        hint = QLabel("New scenes save here with a random name — you can still rename in the dialog", self)
        hint.setObjectName("pathLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.refresh()

    def _on_fun_names(self, checked: bool) -> None:
        if not self._updating:
            self.ui.app.run_command("file.set_fun_names", bool(checked))

    def refresh(self) -> None:
        settings = self.ui.settings
        self._updating = True
        try:
            custom = str(settings.value(SAVE_DIR_SETTING, "") or "").strip()
            self.dir_edit.setText(custom or str(save_dir(settings)))
            self.fun_names.setChecked(bool(settings.value(FUN_NAMES_SETTING, True, type=bool)))
        finally:
            self._updating = False
