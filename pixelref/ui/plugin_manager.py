"""The Plugins window: plugins (status, errors, enable/disable at runtime,
shortcut conflicts) and the Python packages plugins need.

The Python packages tab is for people who have never used pip: plugins that
are waiting for a package say so, one button installs what they need, and
packages can be updated or removed without a terminal. The work runs in the
background (:mod:`pixelref.ui.package_jobs`); pip's own log is one click away.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.packages import is_frozen, pip_available
from .package_jobs import package_runner

if TYPE_CHECKING:
    from .app import App

ERROR_COLOR = "#d27b7b"


class PluginManagerDialog(QDialog):
    def __init__(self, app: "App", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.session = app.session
        self.runner = package_runner(app)
        self.setWindowTitle("Plugins")
        self.resize(820, 580)
        self.setStyleSheet(app.stylesheet)
        layout = QVBoxLayout(self)
        self.restart_banner = QLabel("", self)
        self.restart_banner.setWordWrap(True)
        self.restart_banner.setStyleSheet(f"color: {ERROR_COLOR}; font-weight: bold;")
        layout.addWidget(self.restart_banner)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self._build_plugins_tab(), "Plugins")
        self.tabs.addTab(self._build_packages_tab(), "Python packages")
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self._build_job_area())
        row = QHBoxLayout()
        reload_button = QPushButton("Reload selected (developers)", self)
        reload_button.clicked.connect(self._reload_selected)
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.accept)
        row.addWidget(reload_button)
        row.addStretch()
        row.addWidget(close_button)
        layout.addLayout(row)
        self._filling = False
        self.runner.started.connect(self._on_job_started)
        self.runner.output.connect(self._on_job_output)
        self.runner.finished.connect(self._on_job_finished)
        self.refresh()
        if self.runner.busy:
            self._on_job_started(self.runner.job.description if self.runner.job else "")
            for line in self.runner.log:
                self.log.appendPlainText(line)

    def done(self, result: int) -> None:  # noqa: D401  (also runs for accept/reject)
        for signal, slot in ((self.runner.started, self._on_job_started), (self.runner.output, self._on_job_output),
                             (self.runner.finished, self._on_job_finished)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        super().done(result)

    # ---- Plugins tab -----------------------------------------------------------------------------

    def _build_plugins_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        intro = QLabel("Everything in pixelRef is a plugin. Foundation plugins can't be switched off; "
                       "switching a feature plugin off removes its tools, menus and panels at once — "
                       "its nodes stay in your scenes.", page)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.tree = QTreeWidget(page)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Plugin", "Status", "Version", "Notes"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree, 1)
        needed_row = QHBoxLayout()
        self.needed_label = QLabel("", page)
        self.needed_label.setWordWrap(True)
        self.install_needed_button = QPushButton("Install what's needed", page)
        self.install_needed_button.setToolTip("Download and install the Python packages these plugins need")
        self.install_needed_button.clicked.connect(self._install_needed)
        needed_row.addWidget(self.needed_label, 1)
        needed_row.addWidget(self.install_needed_button)
        layout.addLayout(needed_row)
        self.conflicts = QLabel("", page)
        self.conflicts.setWordWrap(True)
        layout.addWidget(self.conflicts)
        return page

    def refresh(self) -> None:
        self._fill_plugins()
        self._fill_packages()
        self._update_banner()
        self._update_buttons()

    def _fill_plugins(self) -> None:
        self._filling = True
        self.tree.clear()
        for info in self.session.plugins.list_plugins():
            status = info["status"]
            item = QTreeWidgetItem([info["name"], status, info["version"], info["error"] or info["description"]])
            item.setData(0, Qt.UserRole, info["id"])
            item.setToolTip(0, f"{info['id']} · {info['path']}")
            if info["python_requires"]:
                item.setToolTip(3, "Python packages: " + ", ".join(info["python_requires"]))
            if info["foundation"]:
                item.setText(1, f"{status} (foundation)")
            else:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                # An unavailable plugin is switched on: it runs once its packages are installed.
                on = status == "active" or (status == "unavailable" and info["enabled"])
                item.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
            if status in ("error", "unavailable"):
                item.setForeground(1, Qt.GlobalColor.red if status == "error" else Qt.GlobalColor.yellow)
            self.tree.addTopLevelItem(item)
        needed = self.session.plugins.packages_needed()
        if needed:
            plugins = sorted({p for ids in needed.values() for p in ids})
            self.needed_label.setText(
                f"{len(plugins)} plugin{'s are' if len(plugins) > 1 else ' is'} waiting for Python packages: "
                + ", ".join(sorted(needed)))
        else:
            self.needed_label.setText("")
        self.install_needed_button.setVisible(bool(needed))
        conflicts = self.app.keymap.conflicts()
        if conflicts:
            lines = [f"{key}: {', '.join(targets)}" for key, targets in conflicts]
            self.conflicts.setText("Shortcut conflicts (the last registered wins):\n" + "\n".join(lines))
        else:
            self.conflicts.setText("No shortcut conflicts.")
        self._filling = False

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._filling or column != 0:
            return
        plugin_id = item.data(0, Qt.UserRole)
        enabled = item.checkState(0) == Qt.Checked
        self.app.run_command("load_plugin" if enabled else "unload_plugin", plugin_id)
        self.refresh()

    def _reload_selected(self) -> None:
        item = self.tree.currentItem()
        if item is not None:
            self.app.run_command("reload_plugin", item.data(0, Qt.UserRole))
            self.refresh()

    def _install_needed(self) -> None:
        needed = self.session.plugins.packages_needed()
        if not needed or not self._pip_ready():
            return
        plugins = sorted({p for ids in needed.values() for p in ids})
        self.runner.start(lambda: self.session.packages.plan_install(list(needed), plugins=plugins))

    # ---- Packages tab ----------------------------------------------------------------------------

    def _build_packages_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        intro = QLabel("Some plugins need extra Python packages. pixelRef keeps them in its own folder, "
                       "separate from any Python installed on this computer.", page)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.packages_tree = QTreeWidget(page)
        self.packages_tree.setColumnCount(4)
        self.packages_tree.setHeaderLabels(["Package", "Version", "Needed by", "Notes"])
        self.packages_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.packages_tree.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.packages_tree.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.packages_tree.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.packages_tree, 1)
        buttons = QHBoxLayout()
        self.update_button = QPushButton("Update", page)
        self.update_button.setToolTip("Install the newest versions of the selected packages")
        self.update_button.clicked.connect(self._update_selected)
        self.update_all_button = QPushButton("Update all", page)
        self.update_all_button.clicked.connect(self._update_all)
        self.remove_button = QPushButton("Remove", page)
        self.remove_button.clicked.connect(self._remove_selected)
        self.file_button = QPushButton("Install from file…", page)
        self.file_button.setToolTip("Install a downloaded .whl file (for computers without internet)")
        self.file_button.clicked.connect(self._install_file)
        open_button = QPushButton("Open folder", page)
        open_button.clicked.connect(self._open_folder)
        for button in (self.update_button, self.update_all_button, self.remove_button, self.file_button):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(open_button)
        layout.addLayout(buttons)
        # Installing any package by name is for people who know what they want.
        self.advanced = QCheckBox("Advanced: install a package by name", page)
        layout.addWidget(self.advanced)
        self.advanced_row = QWidget(page)
        advanced_layout = QHBoxLayout(self.advanced_row)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.name_edit = QLineEdit(self.advanced_row)
        self.name_edit.setPlaceholderText("One package, e.g. numpy  or  numpy>=1.26")
        self.name_edit.returnPressed.connect(self._install_by_name)
        install_button = QPushButton("Install", self.advanced_row)
        install_button.clicked.connect(self._install_by_name)
        self.name_install_button = install_button
        advanced_layout.addWidget(self.name_edit, 1)
        advanced_layout.addWidget(install_button)
        self.advanced_row.setVisible(False)
        self.advanced.toggled.connect(self.advanced_row.setVisible)
        layout.addWidget(self.advanced_row)
        self.folder_label = QLabel("", page)
        self.folder_label.setObjectName("pathLabel")
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.folder_label)
        return page

    def _fill_packages(self) -> None:
        selected = {item.data(0, Qt.UserRole) for item in self.packages_tree.selectedItems()}
        self.packages_tree.clear()
        names = {info["id"]: info["name"] for info in self.session.plugins.list_plugins()}
        for info in self.session.run("packages.list"):
            notes = []
            if not info["chosen"]:
                notes.append("installed for another package")
            if info["file"]:
                notes.append(f"from {info['file']}")
            if info["chosen"] and not info["version"]:
                notes.append("not installed yet" if not self.session.packages.restart_needed else "after restart")
            needed_by = ", ".join(names.get(p, p) for p in info["needed_by"])
            item = QTreeWidgetItem([info["name"], info["version"] or "—", needed_by, "; ".join(notes)])
            item.setData(0, Qt.UserRole, info["name"])
            item.setData(1, Qt.UserRole, info["chosen"])
            self.packages_tree.addTopLevelItem(item)
            item.setSelected(info["name"] in selected)
        packages = self.session.packages
        self.folder_label.setText(f"Folder: {packages.site}  ·  Python {packages.tag}")

    def _selected_packages(self, chosen_only: bool = True) -> list[str]:
        return [item.data(0, Qt.UserRole) for item in self.packages_tree.selectedItems()
                if item.data(1, Qt.UserRole) or not chosen_only]

    def _update_buttons(self) -> None:
        busy = self.runner.busy
        chosen = bool(self._selected_packages())
        self.update_button.setEnabled(chosen and not busy)
        self.remove_button.setEnabled(chosen and not busy)
        self.update_all_button.setEnabled(bool(self.session.packages.wanted()) and not busy)
        self.file_button.setEnabled(not busy)
        self.name_install_button.setEnabled(not busy)
        self.install_needed_button.setEnabled(not busy)

    def _update_selected(self) -> None:
        names = self._selected_packages()
        if names and self._pip_ready():
            self.runner.start(lambda: self.session.packages.plan_update(names))

    def _update_all(self) -> None:
        if self._pip_ready():
            self.runner.start(lambda: self.session.packages.plan_update())

    def _remove_selected(self) -> None:
        names = self._selected_packages()
        if names and self._pip_ready():
            self.runner.start(lambda: self.session.packages.plan_remove(names))

    def _install_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Install a Python package file", str(Path.home()),
                                                    "Python wheels (*.whl)")
        if path and self._pip_ready():
            self.runner.start(lambda: self.session.packages.plan_install_file(Path(path)))

    def _install_by_name(self) -> None:
        text = self.name_edit.text().strip()
        if text and self._pip_ready():
            self.runner.start(lambda: self.session.packages.plan_install([text], user=True))

    def _open_folder(self) -> None:
        folder = self.session.packages.site
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _pip_ready(self) -> bool:
        if is_frozen() or pip_available():
            return True
        self._show_message("pip is missing from the Python that runs pixelRef. "
                           "Install it with:  python -m ensurepip", error=True)
        return False

    # ---- Job progress ----------------------------------------------------------------------------

    def _build_job_area(self) -> QWidget:
        area = QWidget(self)
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.progress = QProgressBar(area)
        self.progress.setRange(0, 0)  # pip reports no overall progress: a busy bar
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(8)
        self.progress.setVisible(False)
        self.message = QLabel("", area)
        self.message.setWordWrap(True)
        self.details = QPushButton("Details", area)
        self.details.setCheckable(True)
        self.details.setVisible(False)
        row.addWidget(self.message, 1)
        row.addWidget(self.details)
        layout.addWidget(self.progress)
        layout.addLayout(row)
        self.log = QPlainTextEdit(area)
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setMinimumHeight(120)
        self.log.setVisible(False)
        self.details.toggled.connect(self.log.setVisible)
        layout.addWidget(self.log)
        return area

    def _on_job_started(self, description: str) -> None:
        self.log.clear()
        self.progress.setVisible(True)
        self.details.setVisible(True)
        self._show_message(f"Installing {description}…" if description else "Working…")
        self._update_buttons()

    def _on_job_output(self, line: str) -> None:
        self.log.appendPlainText(line)

    def _on_job_finished(self, result: dict[str, Any]) -> None:
        self.progress.setVisible(False)
        self.details.setVisible(bool(self.log.toPlainText()))
        self._show_message(result["message"], error=not result["ok"])
        self.refresh()

    def _show_message(self, text: str, error: bool = False) -> None:
        self.message.setText(text)
        self.message.setStyleSheet(f"color: {ERROR_COLOR};" if error else "")

    def _update_banner(self) -> None:
        if self.session.packages.restart_needed:
            self.restart_banner.setText("Restart pixelRef to finish changing Python packages.")
            self.restart_banner.setVisible(True)
        else:
            self.restart_banner.setVisible(False)
