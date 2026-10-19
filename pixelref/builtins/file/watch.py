"""Auto-Refresh: notice when the open scene file is changed, replaced, moved or
deleted outside pixelRef, and reload it (automatically, after a prompt, or only on
request, per the "When the File Changes" setting)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QFileSystemWatcher, QTimer
from PySide6.QtWidgets import QMessageBox

MODE_SETTING = "file_watch_mode"
MODES = {  # id: (menu text, search keywords)
    "reload": ("Reload Automatically", "auto refresh file watcher external change"),
    "notify": ("Notify When Changed", "ask toast prompt file watcher external change"),
    "off": ("Manual Only", "off disable file watcher refresh"),
}
DEFAULT_MODE = "notify"
DEBOUNCE_MS = 300
POLL_MS = 2000  # safety net: network drives and some editors' rename-saves don't always raise watcher events
NOTICE_MS = 0  # change notices stay until answered


def stamp_of(path: Path) -> tuple[int, int] | None:
    """What identifies a file's content on disk (None: it isn't there)."""
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_mtime_ns, info.st_size


class FileWatcher:
    """Keeps an eye on the open scene file. ``known`` is the disk state the open
    document corresponds to (set on open, save and reload); anything else is an
    external change."""

    def __init__(self, ui: Any, files: Any) -> None:
        self.ui = ui
        self.session = ui.session
        self.files = files  # the file plugin's reload-capable helpers: open_for_reload(path) -> bool
        self.path: Path | None = None
        self.known: tuple[int, int] | None = None
        self.announced: tuple[int, int] | None | str = "none"  # the disk state already told to the user
        self.prompting = False
        self.watcher = QFileSystemWatcher()
        self.debounce = QTimer()
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(DEBOUNCE_MS)
        self.debounce.timeout.connect(self.check)
        self.poll = QTimer()
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(self.check)
        self.watcher.fileChanged.connect(lambda _p: self.debounce.start())
        self.watcher.directoryChanged.connect(lambda _p: self.debounce.start())
        self.poll.start()

    # ---- Setting ------------------------------------------------------------------------------

    @property
    def mode(self) -> str:
        value = str(self.ui.settings.value(MODE_SETTING, DEFAULT_MODE))
        return value if value in MODES else DEFAULT_MODE

    @mode.setter
    def mode(self, value: str) -> None:
        if value not in MODES:
            raise ValueError(f"Unknown mode {value!r}: use {', '.join(MODES)}")
        self.ui.settings.setValue(MODE_SETTING, value)
        self._rearm()  # keep what's known: changes made while off still count
        if value != "off":
            self.check()

    # ---- Tracking the document's file ---------------------------------------------------------

    def sync(self) -> None:
        """The document's file (or the mode) changed: watch the right paths, take the disk state as known."""
        path = self.session.scene_path
        self.path = Path(path).resolve() if path is not None else None
        self.known = stamp_of(self.path) if self.path is not None else None
        self.announced = "none"
        self._rearm()

    def _rearm(self) -> None:
        old = self.watcher.files() + self.watcher.directories()
        if old:
            self.watcher.removePaths(old)
        if self.path is None or self.mode == "off":
            return
        paths = [str(self.path.parent)] if self.path.parent.is_dir() else []
        if self.path.exists():
            paths.append(str(self.path))
        if paths:
            self.watcher.addPaths(paths)

    def stop(self) -> None:
        self.debounce.stop()
        self.poll.stop()
        old = self.watcher.files() + self.watcher.directories()
        if old:
            self.watcher.removePaths(old)
        self.watcher.deleteLater()
        self.debounce.deleteLater()
        self.poll.deleteLater()

    # ---- Noticing changes ---------------------------------------------------------------------

    def check(self) -> str | None:
        """Compare the disk with what's open. Returns what was found: "changed", "missing" or None."""
        if self.path is None or self.mode == "off":
            return None
        self._rearm()  # an atomic replace drops the file watch
        current = stamp_of(self.path)
        if current == self.known:
            if current is not None:
                self.announced = "none"  # it came back / was restored to the open state
            return None
        if current == self.announced:
            return None  # already told them about this one
        if current is None:
            if self.known is None:
                return None
            self.announced = current
            self._on_missing()
            return "missing"
        self.announced = current
        self._on_changed()
        return "changed"

    def _on_missing(self) -> None:
        name = self.path.name if self.path else "The file"
        self.ui.status(f"{name} was deleted or moved")
        self.ui.toast(f"{name} was deleted or moved. Your open copy is kept: Save to write it back.",
                      duration_ms=NOTICE_MS, actions=[("Save", lambda: self.ui.app.run_command("file.save"))])

    def _on_changed(self) -> None:
        name = self.path.name if self.path else "The file"
        unsaved = self.session.unsaved
        if self.mode == "reload":
            self.reload(manual=False)
            return
        extra = " You have unsaved changes." if unsaved else ""
        self.ui.toast(f"{name} changed on disk.{extra}", duration_ms=NOTICE_MS,
                      actions=[("Reload", lambda: self.reload(manual=True, asked=False))])

    def confirm_save(self, target: str | Path) -> bool:
        """Before saving over the open file: if someone else changed it since it was
        opened, ask before clobbering their version."""
        if self.mode == "off" or self.path is None or Path(target).resolve() != self.path:
            return True
        current = stamp_of(self.path)
        if current is None or current == self.known:
            return True
        reply = QMessageBox.question(
            self.ui.app.main_window(), "File Changed on Disk",
            f"{self.path.name} was changed outside pixelRef since you opened it.\n\n"
            "Saving will overwrite those changes. Save anyway?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        return reply == QMessageBox.Yes

    # ---- Reloading ----------------------------------------------------------------------------

    def reload(self, manual: bool = True, asked: bool = False) -> bool:
        """Re-read the open scene from disk. Unsaved edits are only discarded after asking."""
        path = self.session.scene_path
        if path is None:
            self.ui.status("Nothing to reload: the scene hasn't been saved to a file yet")
            return False
        path = Path(path)
        if not path.exists():
            if manual:
                QMessageBox.warning(self.ui.app.main_window(), "Reload", f"{path}\n\nThe file no longer exists "
                                    "(deleted or moved). Your open copy is kept: Save to write it back.")
            else:
                self._on_missing()
            return False
        self.ui.canvas.flush_pending()
        if self.session.unsaved and not self._confirm_overwrite(path):
            return False
        ok = self.files.open_for_reload(path)
        if ok:
            self.sync()
            self.ui.status(f"Reloaded: {path.name}")
            self.ui.toast(f"Reloaded {path.name}", duration_ms=1500)
        elif not manual:
            # Probably caught mid-write: leave the disk state un-announced so the next change retries.
            self.announced = "none"
            self.ui.toast(f"Couldn't reload {path.name} (it may still be being written)", duration_ms=3500)
        return ok

    def _confirm_overwrite(self, path: Path) -> bool:
        if self.prompting:
            return False
        self.prompting = True
        try:
            box = QMessageBox(self.ui.app.main_window())
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle("Unsaved Changes")
            box.setText(f"{path.name} changed on disk, and you have unsaved changes here.")
            box.setInformativeText("Reloading replaces your unsaved changes with the version on disk.")
            reload_button = box.addButton("Reload and Discard Mine", QMessageBox.DestructiveRole)
            keep = box.addButton("Keep Mine", QMessageBox.RejectRole)
            box.setDefaultButton(keep)
            box.exec()
            return box.clickedButton() is reload_button
        finally:
            self.prompting = False
