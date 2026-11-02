"""The Files panel: files deliberately opened in this window, as a right-edge panel tab.

A vertical list of the window's open files (Notepad++-style tabs, in a fixed
user-reorderable order) with the thumbnails the start panel already uses. The
active file is highlighted with the accent colour; a file with unsaved changes
gets an orange border. Click to switch (no prompt: unsaved work is a temporary
state that resumes when you switch back), double-click per the *Open files on
double-click* preference, drag rows to reorder, right-click for Open in New
Window and friends.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtCore import QMimeData
from PySide6.QtGui import QColor, QDrag, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QFrame, QLabel, QMenu,
                               QScrollArea, QToolButton, QVBoxLayout, QWidget)

from pixelref.ui.panels import PanelType

from .start import ThumbnailCache
from .workspace import CHILD_ENV, PALETTE

OPEN_PREF_SETTING = "open_double_click"  # "current" (default) or "new"
THUMB = QSize(64, 44)
ORANGE = "#e8863c"
ACCENT = "#6ea8fe"


def open_preference(settings: Any) -> str:
    value = str(settings.value(OPEN_PREF_SETTING, "current") or "current")
    return value if value in ("current", "new") else "current"


def set_open_preference(settings: Any, value: str) -> str:
    value = "new" if value == "new" else "current"
    settings.setValue(OPEN_PREF_SETTING, value)
    return value


def spawn_window(path: str) -> bool:
    """Open ``path`` in a new pixelRef process (a separate top-level window)."""
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        # The new window never owns the saved workspace: only the first window does.
        env = {**os.environ, CHILD_ENV: "1"}
        subprocess.Popen([sys.executable, "-m", "pixelref", str(path)], env=env, **kwargs)
    except OSError as exc:
        raise OSError(f"Could not start pixelRef: {exc}") from exc
    return True


class FileRow(QFrame):
    """One file in the list: thumbnail, name, folder; orange border when unsaved."""

    clicked = Signal(str, object)  # path, modifiers
    double_clicked = Signal(str, object)  # path, modifiers
    open_in_new_window = Signal(str)
    dropped_on = Signal(str, str)  # dragged path, target path
    close_requested = Signal(str)
    locate_requested = Signal(str)
    color_requested = Signal(str, str)  # path, colour name ("" removes the label)
    close_others_requested = Signal(str)
    close_all_requested = Signal()

    def __init__(self, path: str, image: QImage | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.active = False
        self.unsaved = False
        self._drag_start: QPoint | None = None
        self.setObjectName("fileRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(path)
        self.setAcceptDrops(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(8)
        # A narrow colour strip (the user's label); it keeps its width when unlabelled so rows
        # line up, and it is separate from the active highlight and the unsaved border.
        self.color_strip = QFrame(self)
        self.color_strip.setObjectName("fileRowColor")
        self.color_strip.setFixedWidth(4)
        self.color_name = ""
        self.set_color("")
        layout.addWidget(self.color_strip)
        self.thumb = QLabel(self)
        self.thumb.setObjectName("fileRowThumb")
        self.thumb.setFixedSize(THUMB)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setPixmap(_row_pixmap(image, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(1)
        file = Path(path)
        self.name = QLabel(file.stem, self)
        self.name.setObjectName("fileRowName")
        self.folder = QLabel(self)
        self.folder.setObjectName("fileRowFolder")
        metrics = self.folder.fontMetrics()
        self.folder.setText(metrics.elidedText(str(file.parent), Qt.ElideMiddle, 180))
        titles.addWidget(self.name)
        titles.addWidget(self.folder)
        layout.addWidget(self.thumb)
        layout.addLayout(titles, 1)
        # A small, muted close control; it also takes keyboard focus (Tab) and never starts a drag
        # or selects the row, because a button swallows its own mouse events.
        self.missing = False
        self.warn = QLabel("\u26a0", self)
        self.warn.setObjectName("fileRowWarn")
        self.warn.setToolTip("File not found")
        self.warn.setStyleSheet("#fileRowWarn { color: #d9a441; }")
        self.warn.setVisible(False)
        layout.addWidget(self.warn, 0, Qt.AlignTop)
        self.close_button = QToolButton(self)
        self.close_button.setObjectName("fileRowClose")
        self.close_button.setText("\u00d7")
        self.close_button.setToolTip("Close file")
        self.close_button.setAccessibleName("Close file")
        self.close_button.setFocusPolicy(Qt.TabFocus)
        self.close_button.setCursor(Qt.ArrowCursor)
        self.close_button.setFixedSize(18, 18)
        self.close_button.setStyleSheet(
            "#fileRowClose { border: none; border-radius: 3px; background: transparent; color: #7d8590;"
            " font-size: 14px; }"
            "#fileRowClose:hover, #fileRowClose:focus { background: rgba(255, 255, 255, 0.14); color: #e6e6e6; }")
        # Closing rebuilds the list under the pointer, so let this click finish first.
        self.close_button.clicked.connect(lambda: QTimer.singleShot(0, lambda: self.close_requested.emit(self.path)))
        layout.addWidget(self.close_button, 0, Qt.AlignTop)

    def set_missing(self, missing: bool) -> None:
        """Mark the file as not found: muted name and a warning sign. It stays selectable and closable."""
        if missing == self.missing:
            return
        self.missing = missing
        self.warn.setVisible(missing)
        self.name.setStyleSheet("color: #7d8590; font-style: italic;" if missing else "")
        self.setToolTip(f"{self.path}\nFile not found" if missing else self.path)

    def set_color(self, name: str | None) -> None:
        name = name if name in PALETTE else ""
        self.color_name = name
        self.color_strip.setToolTip(f"Colour: {name.title()}" if name else "")
        rule = f"background: {PALETTE[name]}; border-radius: 2px;" if name else "background: transparent;"
        self.color_strip.setStyleSheet(f"#fileRowColor {{ {rule} }}")

    def set_state(self, active: bool, unsaved: bool) -> None:
        if active == self.active and unsaved == self.unsaved:
            return
        self.active, self.unsaved = active, unsaved
        self._style()

    def _style(self) -> None:
        if self.unsaved:
            border, bg = f"1px solid {ORANGE}", "rgba(232, 134, 60, 0.10)"
        elif self.active:
            border, bg = f"1px solid {ACCENT}", "rgba(110, 168, 254, 0.10)"
        else:
            border, bg = "1px solid transparent", "transparent"
        self.setStyleSheet(f"#fileRow {{ border: {border}; border-radius: 4px; background: {bg}; }}")

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_start = event.position().toPoint()
        event.accept()

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802
        if self._drag_start is None:
            return
        start = self._drag_start
        self._drag_start = None
        if (event.position().toPoint() - start).manhattanLength() < QApplication.startDragDistance():
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData("application/x-pixelref-file-row", self.path.encode("utf-8"))
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event: Any) -> None:  # noqa: N802
        if event.mimeData().hasFormat("application/x-pixelref-file-row"):
            event.acceptProposedAction()

    def dropEvent(self, event: Any) -> None:  # noqa: N802
        dragged = bytes(event.mimeData().data("application/x-pixelref-file-row")).decode("utf-8")
        if dragged != self.path:
            self.dropped_on.emit(dragged, self.path)
        event.acceptProposedAction()

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802
        self._drag_start = None
        event.accept()
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            path, mods = self.path, event.modifiers()
            # Open once this click is over (opening rebuilds the board under the pointer).
            QTimer.singleShot(0, lambda: self.clicked.emit(path, mods))

    def mouseDoubleClickEvent(self, event: Any) -> None:  # noqa: N802
        event.accept()
        if event.button() == Qt.LeftButton:
            path, mods = self.path, event.modifiers()
            QTimer.singleShot(0, lambda: self.double_clicked.emit(path, mods))

    def contextMenuEvent(self, event: Any) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.addAction("Open in New Window", lambda: self.open_in_new_window.emit(self.path))
        menu.addAction("Reveal in Explorer", lambda: _reveal(self.path))
        menu.addAction("Copy Path", lambda: _copy_path(self.path))
        if self.missing:
            menu.addAction("Locate File\u2026", lambda: self.locate_requested.emit(self.path))
        menu.addSeparator()
        colours = menu.addMenu("Set Colour")
        for name, swatch in PALETTE.items():
            action = colours.addAction(_swatch_icon(swatch), name.title(),
                                       lambda n=name: self.color_requested.emit(self.path, n))
            action.setCheckable(True)
            action.setChecked(name == self.color_name)
        remove = menu.addAction("Remove Colour", lambda: self.color_requested.emit(self.path, ""))
        remove.setEnabled(bool(self.color_name))
        menu.addSeparator()
        menu.addAction("Close", lambda: self.close_requested.emit(self.path))
        menu.addAction("Close Others", lambda: self.close_others_requested.emit(self.path))
        menu.addAction("Close All", lambda: self.close_all_requested.emit())
        menu.exec(event.globalPos())


def _swatch_icon(color: str) -> QIcon:
    pixmap = QPixmap(12, 12)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)


def _reveal(path: str) -> None:
    from PySide6.QtGui import QDesktopServices, QUrl

    QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))


def _copy_path(path: str) -> None:
    from PySide6.QtWidgets import QApplication

    QApplication.clipboard().setText(path)


def _row_pixmap(image: QImage | None, ratio: float) -> QPixmap:
    """The thumbnail fitted into the row's frame (or a placeholder)."""
    size = THUMB * ratio
    pixmap = QPixmap(size)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QColor(14, 16, 20))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    if image is not None:
        scaled = image.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        painter.drawImage((THUMB.width() - scaled.width() / ratio) / 2,
                          (THUMB.height() - scaled.height() / ratio) / 2, scaled)
    painter.end()
    return pixmap


class FilesPanel(QWidget):
    """The list of recent files; one instance per host window."""

    def __init__(self, ui: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ui = ui
        self.cache = ThumbnailCache()
        self.rows: list[FileRow] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(4)
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("filesScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget(self.scroll)
        host.setObjectName("filesHost")
        self.list_layout = QVBoxLayout(host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(3)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(host)
        layout.addWidget(self.scroll)
        self.empty = QLabel("No files open in this window yet.\n"
                            "Open a scene (File > Open) and it appears here.", self)
        self.empty.setObjectName("filesEmpty")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.refresh()

    def refresh(self) -> None:
        """Re-read the window's open files (in their fixed order); keep scroll position."""
        bar = self.scroll.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        for row in self.rows:
            self.list_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []
        files_service = self.ui.service("files")
        files_service.check_availability()  # background, throttled: never blocks the panel
        files = files_service.open_files_list()
        current = self.ui.session.scene_path
        current = str(Path(current).resolve()) if current else None
        for path in files:
            row = FileRow(path, self.cache.get(path), self.scroll.widget())
            row.set_state(path == current, False)
            row.clicked.connect(self._on_click)
            row.double_clicked.connect(self._on_double_click)
            row.open_in_new_window.connect(self._open_new_window)
            row.dropped_on.connect(self._on_drop)
            row.set_color(files_service.file_color(path))
            row.set_missing(files_service.file_missing(path))
            row.locate_requested.connect(lambda p: self.ui.app.run_command("file.relocate", p))
            row.color_requested.connect(lambda p, n: self.ui.app.run_command("file.set_color", p, n))
            row.close_requested.connect(lambda p: self.ui.app.run_command("file.close", p))
            row.close_others_requested.connect(lambda p: self.ui.app.run_command("file.close_others", p))
            row.close_all_requested.connect(lambda: self.ui.app.run_command("file.close_all"))
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            row.show()
            self.rows.append(row)
        self.empty.setVisible(not self.rows)
        self.scroll.setVisible(bool(self.rows))
        if at_end:
            bar.setValue(bar.maximum())

    def sync_unsaved(self) -> None:
        """Update the orange border of rows with unsaved changes (current or captured)."""
        current = self.ui.session.scene_path
        current = str(Path(current).resolve()) if current else None
        current_unsaved = self.ui.session.unsaved
        captured = {path: unsaved for path, unsaved in self.ui.service("files").open_files_state()}
        for row in self.rows:
            if row.path == current:
                row.set_state(row.active, current_unsaved)
            else:
                row.set_state(row.active, captured.get(row.path, False))

    def _on_click(self, path: str, _mods: Any) -> None:
        # Switching is prompt-free: unsaved work is a temporary state that resumes
        # when the user switches back.
        self.ui.app.run_command("file.switch_to", path)

    def _on_drop(self, dragged: str, target: str) -> None:
        self.ui.app.run_command("file.reorder_open_files", dragged, target)

    def _on_double_click(self, path: str, mods: Any) -> None:
        pref = open_preference(self.ui.settings)
        shift = bool(mods & Qt.ShiftModifier)
        new_window = (pref == "new") != shift  # Shift forces the opposite of the preference
        if new_window:
            self._open_new_window(path)
        else:
            self.ui.app.run_command("file.open_path", path)

    def _open_new_window(self, path: str) -> None:
        try:
            spawn_window(path)
            self.ui.status(f"Opening in a new window: {Path(path).name}")
        except OSError as exc:
            self.ui.toast(str(exc), QColor("#d27b7b"))


def activate_files_panel_ui(ui: Any) -> None:
    """Register the Files panel tab and keep its rows in sync with the session."""
    service = type("FilesPanelService", (), {"panels": []})()
    ui.provide_service("files_panel", service)

    def make_panel(_parent: Any = None) -> FilesPanel:
        panel = FilesPanel(ui)
        service.panels.append(panel)
        return panel

    def dispose_panel(panel: QWidget) -> None:
        if panel in service.panels:
            service.panels.remove(panel)

    ui.panel(PanelType(id="files", title="Files", icon="files", color="#e8863c", factory=make_panel,
                       dispose=dispose_panel, order=30, popup_keys=("Alt+F",), popup_size=(300, 420),
                       float_size=(300, 380), stretch=2,
                       keywords="recent files scenes open switch windows unsaved"))

    def refresh_all(_event: Any = None) -> None:
        for panel in list(service.panels):
            panel.refresh()

    def sync_unsaved(_event: Any = None) -> None:
        for panel in list(service.panels):
            panel.sync_unsaved()

    # The list changes when a file is opened/saved (recent order) or the document changes.
    ui.on_ui("canvas.refreshed", lambda _e: [p.sync_unsaved() for p in list(service.panels)])
    ui.on("revision.changed", sync_unsaved)
    ui.on("document.replaced", refresh_all)
    ui.on("scene.saved", refresh_all)
    ui.on("scene.opened", refresh_all)
    ui.on_ui("files.changed", refresh_all)

    def availability_changed(_event: Any = None) -> None:
        files = ui.service("files")
        for panel in list(service.panels):
            for row in panel.rows:
                row.set_missing(files.file_missing(row.path))

    ui.on_ui("files.availability_changed", availability_changed)

    def color_changed(event: Any) -> None:
        # Only the colour strip of the one row: no rebuild, so thumbnails are not touched.
        path = (event.payload if hasattr(event, "payload") else event or {}).get("path")
        color = ui.service("files").file_color(path) if path else None
        for panel in list(service.panels):
            for row in panel.rows:
                if row.path == path:
                    row.set_color(color)

    ui.on_ui("files.color_changed", color_changed)

    # ---- Commands -----------------------------------------------------------------------------

    def open_in_new_window(path: str | None = None) -> bool:
        """Open a scene file in its own window (a new pixelRef process)."""
        if path is None:
            path = ui.session.scene_path
            if path is None:
                ui.status("The scene hasn't been saved to a file yet")
                return False
        try:
            spawn_window(str(path))
        except OSError as exc:
            ui.toast(str(exc), QColor("#d27b7b"))
            return False
        ui.status(f"Opening in a new window: {Path(path).name}")
        return True

    def set_open_pref(value: str) -> str:
        """Set the *Open files on double-click* preference ("current" or "new")."""
        return set_open_preference(ui.settings, value)

    def _open() -> list[str]:
        return ui.service("files").open_files_list()

    def cycle(direction: int = 1) -> bool:
        """Cycle to the next/previous open file (current window)."""
        files = _open()
        if not files:
            ui.status("No files open in this window")
            return False
        current = ui.session.scene_path
        current = str(Path(current).resolve()) if current else None
        if current in files:
            index = (files.index(current) + direction) % len(files)
        else:
            index = 0 if direction > 0 else len(files) - 1
        ui.app.run_command("file.switch_to", files[index])
        return True

    def jump(n: int) -> bool:
        """Jump to the n-th open file (1-based)."""
        files = _open()
        if not 1 <= n <= len(files):
            ui.status(f"No open file #{n}")
            return False
        ui.app.run_command("file.switch_to", files[n - 1])
        return True

    ui.command("open_in_new_window", open_in_new_window, undoable=False)
    ui.command("set_open_pref", set_open_pref, undoable=False, echo=False)
    ui.command("cycle", cycle, undoable=False)
    ui.command("jump", jump, undoable=False)

    a = ui.action
    a("file.open_in_new_window", "file.open_in_new_window", "Open in New Window",
      keywords="detach pop out separate window scene", category="File", enabled=lambda: ui.session.scene_path is not None)
    a("file.pref_current", "file.set_open_pref", "Open on Double-Click: Current Window",
      args=("current",), keywords="preference double click opening", category="File", group="file.open_pref",
      checked=lambda: open_preference(ui.settings) == "current")
    a("file.pref_new", "file.set_open_pref", "Open on Double-Click: New Window",
      args=("new",), keywords="preference double click opening", category="File", group="file.open_pref",
      checked=lambda: open_preference(ui.settings) == "new")
    a("file.cycle", "file.cycle", "Next Open File", args=(1,),
      keywords="cycle next previous open switch", category="File")
    for n in range(1, 10):
        a(f"file.jump_{n}", "file.jump", f"Open File {n}", args=(n,),
          keywords=f"jump open file {n} switch", category="File")

    ui.menu("canvas", "file.open_in_new_window", path="File", group="file.location", order=4)
    ui.menu("canvas", "file.pref_current", path="File/File Opening", group="file.open_pref", order=0)
    ui.menu("canvas", "file.pref_new", path="File/File Opening", group="file.open_pref", order=1)

    # Keybindings (all free per the design doc's keymap audit).
    ui.bind("Ctrl+Shift+Tab", "file.cycle")
    ui.bind("Ctrl+Shift+O", "file.open_in_new_window")
    for n in range(1, 10):
        ui.bind(f"Ctrl+Shift+{n}", f"file.jump_{n}")