"""Files: the scene document's lifecycle in the desktop app, scene thumbnails and
the start panel (recent files) on an empty board."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QStandardPaths, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from pixelref.plugin_api import SCENE_EXT, SCENE_EXTS
from pixelref.plugin_api.contracts import IngestHandler

SCENE_FILTER = "pixelRef Scene (" + " ".join(f"*{ext}" for ext in SCENE_EXTS) + ")"
MAX_RECENT = 10
AUTOSAVE_INTERVAL_MS = 60_000


class RecoveryService:
    """Autosave of unsaved work, and restoring it after a crash."""

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.session = ui.session
        self.autosaved_revision = 0
        self.owns_recovery = False
        self.timer = QTimer()
        self.timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self.timer.timeout.connect(self.autosave)
        self.timer.start()

    @staticmethod
    def recovery_dir() -> Path:
        base = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation) or str(Path.home() / ".pixelref")
        return Path(base) / "recovery"

    def recovery_files(self) -> tuple[Path, Path]:
        folder = self.recovery_dir()
        return folder / f"recovery{SCENE_EXT}", folder / "recovery.json"

    def autosave(self) -> bool:
        """Write a recovery copy of unsaved work (images included)."""
        session = self.session
        if not session.unsaved or session.revision == self.autosaved_revision:
            return False
        if self.ui.canvas.interacting or session.context.get("editing.text"):
            return False  # mid-drag or typing: try again next tick
        scene_file, meta_file = self.recovery_files()
        try:
            scene_file.parent.mkdir(parents=True, exist_ok=True)
            temp = scene_file.with_suffix(".tmp")
            temp.write_text(session.serialize(), encoding="utf-8", newline="\n")
            os.replace(temp, scene_file)
            meta = {
                "scene_file": str(session.scene_path) if session.scene_path else None,
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "items": len(session.scene.nodes),
            }
            meta_file.write_text(json.dumps(meta), encoding="utf-8")
        except OSError:
            return False
        self.autosaved_revision = session.revision
        self.owns_recovery = True
        return True

    def clear(self) -> None:
        if not self.owns_recovery:
            return  # never delete another session's recovery copy
        for path in self.recovery_files():
            try:
                path.unlink()
            except OSError:
                pass
        self.owns_recovery = False

    def pending(self) -> dict[str, Any] | None:
        scene_file, meta_file = self.recovery_files()
        if not scene_file.exists():
            return None
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        return {"path": scene_file, **meta}

    def restore(self) -> bool:
        info = self.pending()
        if info is None:
            return False
        try:
            self.session.load_text(info["path"].read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self.ui.app.main_window(), "Restore failed", str(exc))
            return False
        original = info.get("scene_file")
        self.session.scene_path = Path(original) if original else None
        cache = self.session.services.get("image_cache")
        if cache is not None:
            cache.pixmaps.clear()
        self.owns_recovery = True  # removed once the restored work is saved
        self.session.mark_unsaved()
        QTimer.singleShot(0, lambda: self.ui.app.run_command("navigation.frame_all"))
        self.ui.toast(f"Restored unsaved work from {info.get('saved_at', 'the last session')}")
        return True

    def offer(self) -> bool:
        """At startup: offer to restore work from a session that didn't close cleanly."""
        info = self.pending()
        if info is None:
            return False
        when = str(info.get("saved_at", "")).replace("T", " ")
        name = Path(info["scene_file"]).name if info.get("scene_file") else "an unsaved board"
        reply = QMessageBox.question(
            self.ui.app.main_window(), "Restore unsaved work?",
            f"pixelRef didn't close normally last time.\n\nRestore {name} "
            f"({info.get('items', '?')} items, autosaved {when})?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply == QMessageBox.Yes:
            return self.restore()
        self.owns_recovery = True
        self.clear()
        return False


def activate_ui(ui: Any) -> None:
    session = ui.session
    app = ui.app
    recovery = RecoveryService(ui)
    ui.provide_service("recovery", recovery)
    ui.track(recovery.timer.stop)

    def on_revision(event: Any) -> None:
        if event.payload.get("event") == "saved":
            recovery.autosaved_revision = session.revision
            recovery.clear()

    ui.on("revision.changed", on_revision)

    def on_replaced(_event: Any) -> None:
        # New / Open restart the revision count at 0: a stale autosaved revision would
        # otherwise skip autosaving the new document once its edits reach that number.
        recovery.autosaved_revision = 0

    ui.on("document.replaced", on_replaced)

    # ---- Recent files -----------------------------------------------------------------------

    def recent_files() -> list[str]:
        value = ui.settings.value("recent_files", [])
        if isinstance(value, str):
            value = [value]
        return [str(v) for v in (value or []) if Path(str(v)).exists()]

    def add_recent(path: Path) -> None:
        entry = str(Path(path).resolve())
        files = [f for f in recent_files() if f != entry]
        files.insert(0, entry)
        ui.settings.setValue("recent_files", files[:MAX_RECENT])

    def clear_recent() -> None:
        """Forget the recent files list."""
        ui.settings.setValue("recent_files", [])

    # ---- Confirmations ------------------------------------------------------------------------

    def confirm_discard() -> bool:
        reply = QMessageBox.question(
            app.main_window(), "Unsaved Changes", "Save changes to the current scene?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save,
        )
        if reply == QMessageBox.Save:
            save()
            return not session.unsaved
        return reply == QMessageBox.Discard

    def may_discard() -> bool:
        ui.canvas.flush_pending()
        return not session.unsaved or confirm_discard()

    # ---- Commands -----------------------------------------------------------------------------

    def new() -> None:
        """New, empty scene (asks about unsaved changes)."""
        if not may_discard():
            return
        session.run("new_scene")
        view = app.active_view()
        if view is not None:
            view.set_camera([0.0, 0.0], 1.0, 0.0)
        recovery.clear()
        ui.status("New scene")

    def open_path(path: str, confirm: bool = True) -> bool:
        """Open a scene file (asks about unsaved changes unless confirm=False)."""
        if confirm and not may_discard():
            return False
        try:
            session.run("open_scene", str(path))
        except Exception as exc:  # noqa: BLE001  (opening is atomic: the open scene is untouched)
            QMessageBox.critical(app.main_window(), "Open failed", f"{path}\n\n{exc}")
            return False
        cache = session.services.get("image_cache")
        if cache is not None:
            cache.pixmaps.clear()
            ui.canvas.request_full_refresh()
        recovery.clear()
        add_recent(Path(path))
        QTimer.singleShot(0, lambda: app.run_command("navigation.frame_all"))
        ui.status(f"Loaded: {Path(path).name}")
        return True

    def open_dialog() -> None:
        """Choose a scene file to open (asks about unsaved changes first)."""
        if not may_discard():
            return
        path, _ = QFileDialog.getOpenFileName(app.main_window(), "Open Scene", str(session.base_dir()),
                                              SCENE_FILTER)
        if path:
            open_path(path, confirm=False)

    def save_to(path: str) -> bool:
        """Save the scene to ``path``."""
        ui.canvas.flush_pending()
        try:
            saved = session.run("save_scene", str(path))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(app.main_window(), "Save failed", str(exc))
            return False
        add_recent(Path(saved))
        ui.status(f"Saved: {Path(saved).name}")
        app.events.emit("canvas.refreshed", {})
        return True

    def save_as() -> bool:
        """Save the scene under a new file name (dialog)."""
        default = str(session.scene_path or (Path.cwd() / f"scene{SCENE_EXT}"))
        path_str, _ = QFileDialog.getSaveFileName(app.main_window(), "Save Scene As", default, SCENE_FILTER)
        if not path_str:
            return False
        path = Path(path_str)
        if path.suffix.lower() not in SCENE_EXTS:
            path = path.with_suffix(SCENE_EXT)
        return save_to(str(path))

    def save() -> bool:
        """Save the scene to its file (asks for a name the first time)."""
        if session.scene_path is None:
            return save_as()
        return save_to(str(session.scene_path))

    def saved_path() -> Path | None:
        if session.scene_path is None:
            ui.status("The scene hasn't been saved to a file yet")
            return None
        return Path(session.scene_path).resolve()

    def copy_path() -> str | None:
        """Copy the open scene file's full path to the clipboard."""
        path = saved_path()
        if path is None:
            return None
        QApplication.clipboard().setText(str(path))
        ui.status(f"Copied: {path}")
        return str(path)

    def copy_folder() -> str | None:
        """Copy the path of the folder holding the open scene file to the clipboard."""
        path = saved_path()
        if path is None:
            return None
        QApplication.clipboard().setText(str(path.parent))
        ui.status(f"Copied: {path.parent}")
        return str(path.parent)

    def open_folder() -> bool:
        """Open the folder holding the open scene file in the system file browser."""
        path = saved_path()
        if path is None:
            return False
        if not path.parent.is_dir():
            QMessageBox.warning(app.main_window(), "Folder not found", str(path.parent))
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    for name, fn in (("new", new), ("open", open_dialog), ("open_path", open_path), ("save", save),
                     ("save_as", save_as), ("save_to", save_to), ("clear_recent", clear_recent),
                     ("copy_path", copy_path), ("copy_folder", copy_folder), ("open_folder", open_folder)):
        ui.command(name, fn, undoable=False)
    ui.provide_service("files", type("Files", (), {
        "open_path": staticmethod(open_path), "save_to": staticmethod(save_to),
        "recent_files": staticmethod(recent_files), "may_discard": staticmethod(may_discard),
    })())

    a = ui.action
    a("file.new", "file.new", "New Scene", keywords="blank board scene create", category="File", icon="plus")
    a("file.open", "file.open", "Open…", keywords="load file board scene", category="File", icon="folder")
    a("file.save", "file.save", "Save", keywords="store write file", category="File", icon="save")
    a("file.save_as", "file.save_as", "Save As…", keywords="copy file rename", category="File")
    def has_file() -> bool:
        return session.scene_path is not None

    a("file.copy_path", "file.copy_path", "Copy File Path", keywords="clipboard scene location full path",
      category="File", enabled=has_file)
    a("file.copy_folder", "file.copy_folder", "Copy Folder Path", keywords="clipboard containing directory location",
      category="File", enabled=has_file)
    a("file.open_folder", "file.open_folder", "Open Containing Folder",
      keywords="reveal show explorer finder file browser directory location", category="File", icon="folder",
      enabled=has_file)
    for key, action in (("Ctrl+N", "file.new"), ("Ctrl+O", "file.open"), ("Ctrl+S", "file.save"),
                        ("Ctrl+Shift+S", "file.save_as")):
        ui.bind(key, action)

    def recent_menu(menu: Any) -> None:
        files = recent_files()
        for path in files:
            menu.addAction(path, lambda p=path: app.run_command("file.open_path", p))
        if files:
            menu.addSeparator()
            menu.addAction("Clear Recent", lambda: app.run_command("file.clear_recent"))
        menu.setEnabled(bool(files))

    ui.menu("canvas", "file.new", path="File", group="file", order=0)
    ui.menu("canvas", "file.open", path="File", group="file", order=1)
    ui.menu("canvas", builder=recent_menu, text="Open Recent", path="File", group="file", order=2)
    ui.menu("canvas", "file.save", path="File", group="file", order=3)
    ui.menu("canvas", "file.save_as", path="File", group="file", order=4)
    ui.menu("canvas", "file.copy_path", path="File", group="file.location", order=0)
    ui.menu("canvas", "file.copy_folder", path="File", group="file.location", order=1)
    ui.menu("canvas", "file.open_folder", path="File", group="file.location", order=2)

    # ---- Dropped scene files -------------------------------------------------------------------

    def scene_files(mime: Any) -> list[Path]:
        if not mime.hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in mime.urls()
                if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in SCENE_EXTS]

    def describe(mime: Any) -> str | None:
        scenes = scene_files(mime)
        return f"Drop to open {scenes[0].name}" if scenes else None

    def ingest(_view: Any, _pos: Any, mime: Any) -> Any:
        scenes = scene_files(mime)
        if not scenes:
            return None
        # Defer so no modal dialog runs inside the OS drag-and-drop loop.
        QTimer.singleShot(0, lambda: app.run_command("file.open_path", str(scenes[0])))
        return "handled"

    ui.contribute("ingest.handlers", IngestHandler("file.scene", 0, lambda m: bool(scene_files(m)), describe, ingest))

    def on_close() -> bool:
        if not may_discard():
            return False
        recovery.timer.stop()
        recovery.clear()
        return True

    ui.contribute("app.close_handlers", on_close)

    from .start import activate_start_ui

    activate_start_ui(ui)
