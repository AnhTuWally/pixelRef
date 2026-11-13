"""Qt side of packages: Save Package… (pre-flight, then a worker thread), Verify Package,
Find Missing Files…, the Path Roots dialog and the missing-files hint when a board opens."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from pixelref.core.scene_file import SCENE_EXT
from pixelref.plugin_api import CommandError

from . import PackagePlan, status_text, verify_text


class _JobSignals(QObject):
    progress = Signal(str)
    done = Signal(object, object)  # result, error


class BackgroundJob:
    """Runs ``work(progress)`` on a worker thread; progress text and the result come back on the
    UI thread (the autosave pattern: a thread plus a Qt signal)."""

    def __init__(self, work: Callable[[Callable[[int, int, str], None]], Any], verb: str,
                 status: Callable[[str], None], done: Callable[[Any, BaseException | None], None]) -> None:
        self.signals = _JobSignals()
        self.signals.progress.connect(status)
        self.signals.done.connect(done)
        self.verb = verb
        self.work = work
        self.thread = threading.Thread(target=self._run, name="pixelref-package", daemon=True)

    def _progress(self, index: int, count: int, relative: str) -> None:
        if relative:
            self.signals.progress.emit(f"{self.verb} {index + 1}/{count}: {relative}")

    def _run(self) -> None:
        try:
            result = self.work(self._progress)
        except Exception as exc:  # noqa: BLE001  (reported on the UI thread)
            logging.getLogger(__name__).exception("%s failed", self.verb)
            self.signals.done.emit(None, exc)
            return
        self.signals.done.emit(result, None)

    def start(self) -> None:
        self.thread.start()


def _size(value: int) -> str:
    from pixelref.plugin_api.ui import widgets as w

    return w.format_size(value)


class RootsDialog(QDialog):
    """Named path roots: files under a root's folder are saved as ``${NAME}/…``."""

    def __init__(self, ui: Any) -> None:
        super().__init__(ui.app.main_window())
        self.ui = ui
        self.setWindowTitle("Path Roots")
        self.resize(560, 320)
        layout = QVBoxLayout(self)
        note = QLabel("Linked files under one of these folders are saved as ${NAME}/… so the board opens on any "
                      "machine that defines the same root (here, or as an environment variable).", self)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Name", "Folder"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)
        add = QPushButton("Add…", self)
        remove = QPushButton("Remove", self)
        add.clicked.connect(self.add_root)
        remove.clicked.connect(self.remove_root)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        row.addWidget(buttons)
        layout.addLayout(row)
        self.reload()

    def reload(self) -> None:
        roots = self.ui.session.links.roots()
        self.table.setRowCount(len(roots))
        for row, (name, folder) in enumerate(sorted(roots.items())):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(folder))

    def add_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Root Folder", str(self.ui.session.base_dir()))
        if not folder:
            return
        suggestion = "".join(c if c.isalnum() else "_" for c in Path(folder).name.upper()).strip("_") or "ROOT"
        if suggestion[0].isdigit():
            suggestion = f"ROOT_{suggestion}"
        name, ok = QInputDialog.getText(self, "Root Name", "Name (letters, digits and _):", text=suggestion)
        if not ok or not name.strip():
            return
        try:
            self.ui.app.run_command("package.set_root", name.strip(), folder)
        except CommandError as exc:
            QMessageBox.warning(self, "Path Roots", str(exc))
        self.reload()

    def remove_root(self) -> None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        if item is not None:
            self.ui.app.run_command("package.remove_root", item.text())
            self.reload()


def activate_package_ui(ui: Any) -> None:
    session = ui.session
    app = ui.app
    api = session.services.require("packages")
    running: dict[str, Any] = {"job": None}

    def busy() -> bool:
        if running["job"] is not None:
            ui.status("A package job is still running")
            return True
        return False

    def window() -> Any:
        return app.main_window()

    # ---- Save Package… --------------------------------------------------------------------------

    def preflight_text(plan: PackagePlan) -> str:
        lines = [f"{len(plan.files)} file(s), {_size(plan.total_bytes)}, will be copied to",
                 f"{plan.files_dir}", f"with the board saved as {plan.scene_file.name}.", ""]
        if plan.old_manifest is not None:
            lines.append("Files already in that package and unchanged are not copied again.")
        if plan.missing:
            lines.append(f"{len(plan.missing)} linked file(s) are missing and will be left out:")
            lines += [f"  {m}" for m in plan.missing[:12]]
            if len(plan.missing) > 12:
                lines.append(f"  … and {len(plan.missing) - 12} more")
        lines.append("The open board is not changed.")
        return "\n".join(lines)

    def save_dialog() -> None:
        """Choose where to save a package of the board and every file it links, check, then copy."""
        if busy():
            return
        stem = session.scene_path.stem if session.scene_path is not None else "Board"
        start = (session.scene_path.parent.parent if session.scene_path is not None else session.base_dir())
        path_str, _ = QFileDialog.getSaveFileName(window(), "Save Package", str(start / stem / f"{stem}{SCENE_EXT}"),
                                                  f"pixelRef scene (*{SCENE_EXT})")
        if not path_str:
            return
        target = Path(path_str)
        if session.scene_path is not None and target.resolve() == Path(session.scene_path).resolve():
            QMessageBox.warning(window(), "Save Package", "Choose another place than the open board's own file.")
            return
        canvas_flush = getattr(ui.canvas, "flush_pending", None)
        if callable(canvas_flush):
            canvas_flush()
        try:
            plan = api.plan(target.parent, target.stem)
        except (OSError, CommandError) as exc:
            QMessageBox.warning(window(), "Save Package", str(exc))
            return
        if QMessageBox.question(window(), "Save Package", preflight_text(plan),
                                QMessageBox.Ok | QMessageBox.Cancel) != QMessageBox.Ok:
            return

        def done(result: Any, error: BaseException | None) -> None:
            running["job"] = None
            if error is not None:
                QMessageBox.critical(window(), "Save Package failed", str(error))
                ui.status("Package not saved")
                return
            api.finish(plan)
            note = f" · {len(plan.missing)} missing left out" if plan.missing else ""
            ui.status(f"Packaged {result['files']} file(s) ({result['copied']} copied) → {plan.scene_file}{note}")

        job = BackgroundJob(lambda progress: api.execute(plan, progress), "Packaging", ui.status, done)
        running["job"] = job
        job.start()

    # ---- Verify Package --------------------------------------------------------------------------

    def verify_ui() -> None:
        """Check the open board's package: every file present, right size, same content."""
        if busy():
            return
        try:
            folder = api.package_dir(None)
        except CommandError:
            chosen = QFileDialog.getExistingDirectory(window(), "Verify Package Folder", str(session.base_dir()))
            if not chosen:
                return
            folder = chosen

        def done(result: Any, error: BaseException | None) -> None:
            running["job"] = None
            if error is not None:
                QMessageBox.warning(window(), "Verify Package", str(error))
                return
            ui.status(verify_text(result))
            if result["problems"]:
                details = "\n".join(result["problems"][:30])
                more = f"\n… and {len(result['problems']) - 30} more" if len(result["problems"]) > 30 else ""
                QMessageBox.warning(window(), "Verify Package",
                                    f"{result['ok']} of {result['total']} intact in {result['folder']}:\n\n{details}{more}")

        job = BackgroundJob(lambda progress: api.verify(folder, progress), "Verifying", ui.status, done)
        running["job"] = job
        job.start()

    # ---- Find Missing Files… -----------------------------------------------------------------------

    def find_missing_dialog() -> None:
        """Choose a folder to look for the board's missing linked files in (matched by content)."""
        if not api.missing_links():
            ui.status("No missing files")
            return
        folder = QFileDialog.getExistingDirectory(window(), "Find Missing Files In", str(session.base_dir()))
        if not folder:
            return
        result = app.run_command("package.find_missing", folder)
        if result is None:
            return
        ui.canvas.request_full_refresh()
        problems = result["ambiguous"] + result["differs"]
        if problems:
            QMessageBox.information(window(), "Find Missing Files",
                                    status_text(result) + "\n\n" + "\n".join(problems[:30]))

    def roots_dialog() -> None:
        """Edit the named path roots (${NAME} in saved paths)."""
        RootsDialog(ui).exec()

    def has_package() -> bool:
        return session.links.package_id() is not None

    ui.command("save_dialog", save_dialog, undoable=False, echo=False)
    ui.command("verify_ui", verify_ui, undoable=False, echo=False)
    ui.command("find_missing_dialog", find_missing_dialog, undoable=False, echo=False)
    ui.command("roots_dialog", roots_dialog, undoable=False, echo=False)
    ui.action("package.save", "package.save_dialog", "Save Package…",
              keywords="collect gather copy linked files folder send archive movies sequences models", category="File",
              icon="export")
    ui.action("package.verify", "package.verify_ui", "Verify Package",
              keywords="check package files intact complete hash", category="File", icon="images")
    ui.action("package.find_missing", "package.find_missing_dialog", "Find Missing Files…",
              keywords="relink missing broken linked files moved renamed search folder", category="File",
              icon="link_file")
    ui.action("package.roots", "package.roots_dialog", "Path Roots…",
              keywords="named roots environment variable ${} portable paths", category="File", icon="settings")
    ui.menu("canvas", "package.save", path="File", group="file.export", order=5)
    ui.menu("canvas", "package.verify", path="File", group="file.export", order=6, when=has_package)
    ui.menu("canvas", "package.find_missing", path="File", group="file.location", order=4)
    ui.menu("canvas", "package.roots", path="File/Preferences", group="file.storage", order=9)

    def hint_missing(_event: Any) -> None:
        def show() -> None:
            count = len(api.missing_links())
            if count:
                ui.status(f"{count} linked file(s) missing · File ▸ Find Missing Files… can relink them")

        QTimer.singleShot(0, show)

    ui.on("scene.opened", hint_missing)

    # ---- "Path variables" in the Attribute Editor's Canvas panel ----------------------------------

    from PySide6.QtWidgets import QPlainTextEdit

    from pixelref.core import links
    from pixelref.plugin_api.contracts import CanvasRow

    rows: list[Any] = []

    def variables_text() -> str:
        return "\n".join(f"{name} = {folder}" for name, folder in session.links.variables().items())

    class VariablesRow(QPlainTextEdit):
        """The board's own path variables, one ``NAME = folder`` per line (saved with the board)."""

        def __init__(self, parent: Any) -> None:
            super().__init__(parent)
            self.setPlaceholderText("STUDIO_DRIVE = W:\\\nLINUX_PATH = /mnt/drive")
            self.setLineWrapMode(QPlainTextEdit.NoWrap)
            self.setTabChangesFocus(True)
            self.setFixedHeight(self.fontMetrics().lineSpacing() * 4 + 12)
            rows.append(self)
            self.destroyed.connect(lambda *_a, r=self: r in rows and rows.remove(r))
            self.refresh()

        def focusOutEvent(self, event: Any) -> None:  # noqa: N802
            super().focusOutEvent(event)
            self.commit()

        def commit(self) -> None:
            """Store what was typed (one undo step). Text that is not ``NAME = folder`` lines is
            reported and the board's variables are shown again."""
            try:
                typed = links.parse_variables(self.toPlainText())
            except ValueError as exc:
                ui.status(str(exc))
                self.refresh(force=True)
                return
            if typed != session.links.variables():
                app.run_command("package.set_variables", typed)
                ui.canvas.request_full_refresh()
            self.refresh(force=True)

        def refresh(self, force: bool = False) -> None:
            if force or not self.hasFocus():
                text = variables_text()
                if text != self.toPlainText():
                    self.setPlainText(text)
            lines = ["Path variables of this board, one NAME = folder per line (saved with the board).",
                     "${NAME}/… in a linked path means that folder, and files below one are saved that way.",
                     "A linked file that is not where its path says is looked for below every folder here,",
                     "so list the same drive as it is called on each system.",
                     "A path root or environment variable with the same name on a machine wins."]
            for name, folder in session.links.variables().items():
                here = session.links.value_of(name)
                if here and here != folder:
                    lines.append(f"{name} is {here} on this machine")
            self.setToolTip("\n".join(lines))

    def refresh_rows(_event: Any = None) -> None:
        for row in list(rows):
            try:
                row.refresh()
            except RuntimeError:  # deleted with its panel
                rows.remove(row)

    ui.on("document.changed", refresh_rows)
    ui.on("document.replaced", refresh_rows)
    ui.contribute("canvas_background.rows", CanvasRow("path_variables", "Path variables", 20, VariablesRow))
