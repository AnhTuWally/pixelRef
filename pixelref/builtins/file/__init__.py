"""Files: the scene document's lifecycle in the desktop app, scene thumbnails and
the start panel (recent files) on an empty board."""

from __future__ import annotations

import json
import logging
import os
import random
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QStandardPaths, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                               QLabel, QMessageBox, QVBoxLayout)

from pixelref.plugin_api import SCENE_EXT, SCENE_EXTS, perf
from pixelref.plugin_api.contracts import EmptyPanel, IngestHandler

from .watch import MODES as WATCH_MODES
from .watch import FileWatcher

SCENE_FILTER = "pixelRef Scene (" + " ".join(f"*{ext}" for ext in SCENE_EXTS) + ")"
MAX_RECENT = 10
AUTOSAVE_INTERVAL_MS = 60_000
RESUME_SETTING = "session_resume"
RESUME_STATE_SETTING = "session_resume_state"
LAST_SESSION_HAD_CONTENT_SETTING = "last_session_had_content"
SAVE_DIR_SETTING = "save_dir"
FUN_NAMES_SETTING = "fun_names"

# Fun names: "Adjective-Noun" (e.g. Brave-Tiger). Curated, filesystem-safe.
ADJECTIVES = (
    "Amber", "Bold", "Brave", "Bright", "Calm", "Clever", "Cosmic", "Crisp", "Curly", "Daring",
    "Dawn", "Deep", "Dreamy", "Electric", "Fancy", "Feral", "Flickering", "Foggy", "Golden", "Gentle",
    "Gigantic", "Glossy", "Grumpy", "Hazy", "Hidden", "Humble", "Jolly", "Jumpy", "Lucky", "Mellow",
    "Mighty", "Misty", "Molten", "Neon", "Nimble", "Noisy", "Peachy", "Pixel", "Plucky", "Proud",
    "Quiet", "Rapid", "Radiant", "Retro", "Rustic", "Sassy", "Silent", "Sneaky", "Sparkly", "Sunny",
    "Swift", "Tiny", "Twinkly", "Velvet", "Vivid", "Wandering", "Whimsical", "Wild", "Wobbly", "Zesty",
)
NOUNS = (
    "Acorn", "Anchor", "Badger", "Basil", "Beacon", "Biscuit", "Blossom", "Boulder", "Breeze", "Bubble",
    "Cactus", "Candle", "Canyon", "Caramel", "Cedar", "Circuit", "Comet", "Copper", "Coral", "Crumbs",
    "Daisy", "Dune", "Ember", "Falcon", "Ferry", "Fiddle", "Flint", "Fog", "Garden", "Gizmo",
    "Glider", "Harbor", "Honey", "Island", "Jungle", "Kettle", "Lantern", "Lighthouse", "Lollipop", "Maple",
    "Meadow", "Meteor", "Muffin", "Nebula", "Noodle", "Orchid", "Otter", "Pebble", "Pinecone", "Pirate",
    "Puddle", "Quartz", "Raccoon", "Rocket", "Saffron", "Saddle", "Spiral", "Sprocket", "Tangerine", "Tumbleweed",
    "Voyage", "Walnut", "Willow", "Waffle", "Wren", "Yard", "Zephyr", "Zigzag",
)


def fun_name(settings: Any) -> str:
    """A random "Adjective-Noun" name for a new scene (e.g. ``Brave-Tiger``).

    Falls back to ``scene`` when fun names are switched off (``fun_names`` setting).
    """
    if not settings.value(FUN_NAMES_SETTING, True, type=bool):
        return "scene"
    return f"{random.choice(ADJECTIVES)}-{random.choice(NOUNS)}"


def save_dir(settings: Any) -> Path:
    """Where new scenes are saved: the user's folder (``save_dir`` setting), else
    ``Documents/pixelRef`` (created on demand). Never raises: falls back to cwd.
    """
    try:
        custom = str(settings.value(SAVE_DIR_SETTING, "") or "").strip()
        if custom:
            path = Path(custom).expanduser()
        else:
            base = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or str(Path.home() / "Documents")
            path = Path(base) / "pixelRef"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return Path.cwd()


class _AutosaveSignals(QObject):
    done = Signal(int, int, bool, dict)  # generation, revision, written, meta


class RecoveryService:
    """Autosave of unsaved work, and restoring it after a crash.

    The timer's autosaves capture the scene on the UI thread (cheap: image data
    is shared, not copied) and write it on a worker thread, so a board with
    hundreds of megabytes of embedded photos doesn't freeze every minute.
    """

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.session = ui.session
        self.autosaved_revision = 0
        self.owns_recovery = False
        self.generation = 0  # bumped by clear(): a write finishing after it is thrown away
        self._writing: threading.Thread | None = None
        self._signals = _AutosaveSignals()
        self._signals.done.connect(self._written)
        self.timer = QTimer()
        self.timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self.timer.timeout.connect(lambda: self.autosave(background=True))
        self.timer.start()

    @staticmethod
    def recovery_dir() -> Path:
        base = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation) or str(Path.home() / ".pixelref")
        return Path(base) / "recovery"

    def recovery_files(self) -> tuple[Path, Path]:
        folder = self.recovery_dir()
        return folder / f"recovery{SCENE_EXT}", folder / "recovery.json"

    def autosave(self, background: bool = False, extra_meta: dict[str, Any] | None = None) -> bool:
        """Write a recovery copy of unsaved work (images included). With ``background``
        the file is written on a worker thread and True means the write has started."""
        session = self.session
        if not session.unsaved or session.revision == self.autosaved_revision:
            return False
        if self.ui.canvas.interacting or session.context.get("editing.text"):
            return False  # mid-drag or typing: try again next tick
        if self._writing is not None:
            return False  # the previous autosave is still being written
        scene_file, _meta_file = self.recovery_files()
        try:
            scene_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        with perf.span("file.autosave_snapshot"):
            snapshot = session.format.snapshot(session.scene)
        meta = {
            "scene_file": str(session.scene_path) if session.scene_path else None,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "items": len(session.scene.nodes),
            **(extra_meta or {}),
        }
        temp = scene_file.with_suffix(".tmp")
        generation, revision = self.generation, session.revision
        if not background:
            return self._written(generation, revision, self._write(snapshot, temp), meta)

        def run() -> None:
            self._signals.done.emit(generation, revision, self._write(snapshot, temp), meta)

        self._writing = threading.Thread(target=run, name="pixelref-autosave", daemon=True)
        self._writing.start()
        return True

    @staticmethod
    def _write(snapshot: Any, temp: Path) -> bool:
        try:
            with perf.span("file.autosave_write"):
                snapshot.write(temp)
        except Exception:  # noqa: BLE001  (on the worker, anything uncaught would stop autosaving for good)
            logging.getLogger(__name__).exception("Autosave failed")
            return False
        return True

    def _written(self, generation: int, revision: int, written: bool, meta: dict) -> bool:
        """Put a written recovery copy in place (on the UI thread), unless clear() came first."""
        self._writing = None
        scene_file, meta_file = self.recovery_files()
        temp = scene_file.with_suffix(".tmp")
        if not written or generation != self.generation:
            temp.unlink(missing_ok=True)
            return False
        try:
            os.replace(temp, scene_file)
            meta_file.write_text(json.dumps(meta), encoding="utf-8")
        except OSError:
            return False
        self.autosaved_revision = revision
        self.owns_recovery = True
        return True

    def wait(self) -> None:
        """Finish a background autosave now (tests, shutting down)."""
        writing = self._writing
        if writing is not None:
            writing.join()
            QApplication.sendPostedEvents(self._signals)

    def clear(self) -> None:
        self.generation += 1
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
            cache.clear()
        self.owns_recovery = True  # removed once the restored work is saved
        self.session.mark_unsaved()
        QTimer.singleShot(0, lambda: self.ui.app.run_command("navigation.frame_all"))
        self.ui.toast(f"Restored unsaved work from {info.get('saved_at', 'the last session')}")
        return True

    def offer(self) -> bool:
        """At startup: offer to restore work from a session that didn't close cleanly."""
        info = self.pending()
        if info is None or info.get("clean"):
            return False  # a clean close's kept draft is offered by resume() or the start panel, not this
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

    # ---- Session Resume -----------------------------------------------------------------

    @property
    def resume_enabled(self) -> bool:
        return bool(self.ui.settings.value(RESUME_SETTING, False, type=bool))

    @resume_enabled.setter
    def resume_enabled(self, value: bool) -> None:
        self.ui.settings.setValue(RESUME_SETTING, bool(value))

    def suspend(self) -> None:
        """Session Resume (on close): remember exactly what was open, without asking
        to save first. A saved file reopens as-is next launch; an Untitled board, or
        one with unsaved edits, is kept (with a thumbnail) so it can come back exactly
        as it was."""
        session = self.session
        self.wait()  # finish any in-flight background autosave before touching recovery state
        worth_keeping = session.unsaved or (session.scene_path is None and bool(session.scene.nodes))
        if not worth_keeping:
            self.clear()
            self.ui.settings.remove(RESUME_STATE_SETTING)
            return
        if session.scene_path is None:
            self._embed_thumbnail()
        self.autosaved_revision = -1  # force a fresh write now even if the revision hasn't moved
        self.autosave(background=False, extra_meta={"clean": True})
        path = str(session.scene_path) if session.scene_path else None
        self.ui.settings.set_json(RESUME_STATE_SETTING, {"path": path})

    def _embed_thumbnail(self) -> None:
        """A picture of the board for the start panel's Untitled resume card."""
        try:
            from .start import render_thumbnail

            section = render_thumbnail(self.ui.canvas)
        except Exception:  # noqa: BLE001  (never let a thumbnail failure block closing)
            section = None
        if section is None:
            self.session.scene.sections.pop("thumbnail", None)
        else:
            self.session.scene.sections["thumbnail"] = section

    def resumable_untitled(self) -> dict[str, Any] | None:
        """An Untitled draft kept from the last close, for the start panel to offer back.
        Only relevant while Session Resume itself is off (it reopens the draft
        automatically otherwise)."""
        if self.resume_enabled or self.last_session_is_empty():
            # last_session_is_empty() only ever turns True from a board that genuinely
            # had content: a no-op close (open pixelRef, close it right away) never
            # flips it back off, so it still points at the last *meaningful* session.
            return None
        info = self.pending()
        if info is None or not info.get("clean") or info.get("scene_file"):
            return None
        return info

    def last_session_is_empty(self) -> bool:
        """Whether no session has ever had anything on its board: the Resume Last
        Session checkbox has nothing to offer then. True only until some session
        actually has content (a board edited down to no items still counts); a later
        session that never touches anything doesn't reset this back to empty, so it
        keeps pointing at the last session that had something worth coming back to."""
        return not self.ui.settings.value(LAST_SESSION_HAD_CONTENT_SETTING, False, type=bool)

    def resume(self) -> bool:
        """At startup, with Session Resume turned on: reopen what was open last time."""
        if not self.resume_enabled:
            return False
        state = self.ui.settings.json_value(RESUME_STATE_SETTING)
        if not state:
            return False
        path = state.get("path")
        if path is None:
            # Was an Untitled/unsaved board: bring its exact draft back automatically.
            return self.restore() if self.pending() is not None else False
        if not Path(path).exists():
            return False
        files = self.session.services.get("files")
        if files is None or not files.open_path(path, confirm=False):
            return False
        info = self.pending()
        if info is not None and info.get("scene_file") == path:
            self.offer_unsaved()
        return True

    def offer_unsaved(self) -> bool:
        """After reopening the last saved file: offer a clear way to recover any edits
        that hadn't been saved yet when the app last closed."""
        info = self.pending()
        if info is None:
            return False
        when = str(info.get("saved_at", "")).replace("T", " ")
        reply = QMessageBox.question(
            self.ui.app.main_window(), "Recover Unsaved Changes?",
            f"You had unsaved changes when pixelRef last closed "
            f"({info.get('items', '?')} items, {when}).\n\nRecover them?",
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

    # Every successful open registers the file in this window's open-files list,
    # regardless of which code path opened it (plugin open_path, startup arg,
    # session resume). Registered before the panel activates so the panel's own
    # scene.opened refresh sees the updated registry.
    def on_opened(event: Any) -> None:
        path = event.payload.get("path")
        if path:
            _register_open(Path(path))

    ui.on("scene.opened", on_opened)

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

    # ---- Open files (per-window registry, Notepad++-style) ------------------------------------

    class OpenFile:
        """One deliberately-open file in this window, with captured state for resuming."""

        __slots__ = ("path", "text", "camera", "unsaved")

        def __init__(self, path: str) -> None:
            self.path = path  # resolved absolute path
            self.text: str | None = None  # captured scene text (for resuming)
            self.camera: dict[str, Any] | None = None  # captured camera state
            self.unsaved = False  # whether it had unsaved changes when captured

    open_files: list[OpenFile] = []  # insertion order is fixed; user can reorder

    def _find_entry(path: Path) -> OpenFile | None:
        resolved = str(path.resolve())
        for entry in open_files:
            if entry.path == resolved:
                return entry
        return None

    def _register_open(path: Path) -> None:
        """Add a file to the open-files registry if not already present."""
        resolved = str(path.resolve())
        if not any(e.path == resolved for e in open_files):
            open_files.append(OpenFile(resolved))

    def _capture_state() -> None:
        """Capture the current file's state (text + camera) into its registry entry."""
        path = session.scene_path
        if path is None:
            return
        entry = _find_entry(Path(path))
        if entry is None:
            entry = OpenFile(str(Path(path).resolve()))
            open_files.append(entry)
        entry.text = session.serialize()
        view = app.active_view()
        entry.camera = view.camera() if view is not None else None
        entry.unsaved = session.unsaved

    def _restore_state(entry: OpenFile) -> None:
        """Restore a registry entry's captured state into the session."""
        session.load_text(entry.text, Path(entry.path))
        session.revision = session.saved_revision = 0
        if entry.unsaved:
            session.mark_unsaved()
        cache = session.services.get("image_cache")
        if cache is not None:
            cache.clear()
            ui.canvas.request_full_refresh()
        if entry.camera is not None:
            cam = entry.camera
            QTimer.singleShot(0, lambda: _restore_camera(cam))

    def _restore_camera(cam: dict[str, Any]) -> None:
        view = app.active_view()
        if view is not None:
            view.set_camera(cam["center"], cam["zoom"], cam["rotation"],
                            flip_x=cam["flip_x"], flip_y=cam["flip_y"])

    def switch_to(path: str) -> bool:
        """Switch to an open file (no prompt; captures the current state first)."""
        target = Path(path).resolve()
        current = session.scene_path
        if current is not None and Path(current).resolve() == target:
            return True  # already the current file

        # Capture the current file's state (no prompt)
        if current is not None:
            _capture_state()

        # Check if the target is in the registry with captured state
        entry = _find_entry(target)
        if entry is not None and entry.text is not None:
            _restore_state(entry)
            ui.status(f"Switched to: {target.name}")
        else:
            # Load from disk (new open)
            if not open_path(str(target), confirm=False):
                return False
        return True

    def reorder_open_files(from_path: str, to_path: str) -> None:
        """Reorder the open-files registry (drag-and-drop)."""
        from_idx = next((i for i, e in enumerate(open_files) if e.path == from_path), None)
        to_idx = next((i for i, e in enumerate(open_files) if e.path == to_path), None)
        if from_idx is None or to_idx is None:
            return
        entry = open_files.pop(from_idx)
        open_files.insert(to_idx, entry)

    def open_files_list() -> list[str]:
        """The open files in registry order (for the panel and cycling)."""
        return [e.path for e in open_files]

    def open_files_state() -> list[tuple[str, bool]]:
        """(path, unsaved) pairs in registry order, for the panel's border sync."""
        return [(e.path, e.unsaved) for e in open_files]

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

    def confirm_close_all() -> bool:
        """Ask about every open file with unsaved changes (checkboxes, default checked).

        Returns True to proceed with closing (after saving/discarding as chosen),
        False if the user cancelled.
        """
        ui.canvas.flush_pending()
        # Collect files with unsaved changes: the current session plus captured entries.
        dirty: list[tuple[str, str | None, OpenFile | None]] = []  # (display, text, entry)
        current = session.scene_path
        if session.unsaved:
            name = Path(current).name if current else "Untitled"
            dirty.append((name, None, None))  # None text = save via the normal save() path
        for entry in open_files:
            if entry.unsaved and entry.text is not None:
                if current is not None and Path(current).resolve() == Path(entry.path):
                    continue  # already listed above
                dirty.append((Path(entry.path).name, entry.text, entry))
        if not dirty:
            return True
        if len(dirty) == 1:
            # Single file: the classic Save/Discard/Cancel prompt (also what tests
            # mock via QMessageBox.question).
            name, text, entry = dirty[0]
            reply = QMessageBox.question(
                app.main_window(), "Unsaved Changes",
                f"Save changes to {name}?",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save,
            )
            if reply == QMessageBox.Cancel:
                return False
            if reply == QMessageBox.Save:
                if entry is None:
                    if not save():
                        return False  # save_as was cancelled: don't lose the choice
                else:
                    try:
                        session.format.write_text(entry.path, text)
                        entry.unsaved = False
                    except Exception as exc:  # noqa: BLE001
                        QMessageBox.critical(app.main_window(), "Save failed", f"{name}\n\n{exc}")
                        return False
            return True

        dialog = QDialog(app.main_window())
        dialog.setWindowTitle("Unsaved Changes")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("The following files have unsaved changes.\n"
                                "Tick the ones you want to save before closing:"))
        boxes: list[QCheckBox] = []
        for name, _text, _entry in dirty:
            box = QCheckBox(name)
            box.setChecked(True)
            boxes.append(box)
            layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Discard
                                   | QDialogButtonBox.Cancel, dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        reply = dialog.exec()
        if reply == QDialog.Rejected:
            return False
        if reply == QDialog.Accepted:  # Save
            for (name, text, entry), box in zip(dirty, boxes):
                if not box.isChecked():
                    continue
                if entry is None:
                    if not save():
                        return False  # save_as was cancelled: don't lose the choice
                else:
                    try:
                        session.format.write_text(entry.path, text)
                        entry.unsaved = False
                    except Exception as exc:  # noqa: BLE001
                        QMessageBox.critical(app.main_window(), "Save failed", f"{name}\n\n{exc}")
                        return False
        return True

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
            cache.clear()
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

    def open_for_reload(path: Path) -> bool:
        """Re-read the open scene file in place: no unsaved-changes prompt (the caller
        asked), and the camera stays where it is."""
        try:
            session.run("open_scene", str(path))
        except Exception as exc:  # noqa: BLE001  (reading is atomic: the open scene is untouched)
            ui.status(f"Reload failed: {exc}")
            return False
        cache = session.services.get("image_cache")
        if cache is not None:
            cache.clear()
            ui.canvas.request_full_refresh()
        recovery.clear()
        return True

    watcher = FileWatcher(ui, type("Reloader", (), {"open_for_reload": staticmethod(open_for_reload)})())
    ui.track(watcher.stop)
    ui.on("scene.opened", lambda _e: watcher.sync())
    ui.on("scene.saved", lambda _e: watcher.sync())
    ui.on("document.replaced", lambda e: watcher.sync() if e.payload.get("reason") == "new" else None)
    watcher.sync()

    def save_to(path: str) -> bool:
        """Save the scene to ``path``."""
        ui.canvas.flush_pending()
        if not watcher.confirm_save(path):
            return False
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
        if session.scene_path is not None:
            default = str(session.scene_path)
        else:
            default = str(save_dir(ui.settings) / f"{fun_name(ui.settings)}{SCENE_EXT}")
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

    def reload() -> bool:
        """Reload the open scene from disk (asks before discarding unsaved changes)."""
        return watcher.reload(manual=True)

    def set_watch_mode(mode: str) -> str:
        """When the open file changes on disk: "reload" automatically, "notify" with a Reload
        button, or "off" (manual only)."""
        watcher.mode = mode
        app.actions.refresh_states()
        return watcher.mode

    def check_external() -> str | None:
        """Look at the open file on disk now; returns "changed", "missing" or None."""
        return watcher.check()

    def resume_last_session() -> bool:
        """Bring back the Untitled draft kept from the last time pixelRef closed."""
        return recovery.restore()

    for name, fn in (("new", new), ("open", open_dialog), ("open_path", open_path), ("save", save),
                     ("save_as", save_as), ("save_to", save_to), ("clear_recent", clear_recent),
                     ("copy_path", copy_path), ("copy_folder", copy_folder), ("open_folder", open_folder),
                     ("resume_last_session", resume_last_session), ("reload", reload),
                     ("set_watch_mode", set_watch_mode), ("check_external", check_external),
                     ("switch_to", switch_to), ("reorder_open_files", reorder_open_files)):
        ui.command(name, fn, undoable=False)
    ui.provide_service("files", type("Files", (), {
        "open_path": staticmethod(open_path), "save_to": staticmethod(save_to),
        "recent_files": staticmethod(recent_files), "may_discard": staticmethod(may_discard),
        "reload": staticmethod(reload),
        "open_files_list": staticmethod(open_files_list),
        "open_files_state": staticmethod(open_files_state),
        "switch_to": staticmethod(switch_to),
        "reorder_open_files": staticmethod(reorder_open_files),
        "capture_state": staticmethod(_capture_state),
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
    a("file.reload", "file.reload", "Reload from Disk", keywords="refresh revert external changed update",
      category="File", enabled=has_file)
    for mode_id, (mode_text, mode_keywords) in WATCH_MODES.items():
        a(f"file.watch_{mode_id}", "file.set_watch_mode", mode_text, args=(mode_id,), keywords=mode_keywords,
          category="File", group="file.watch", checked=lambda m=mode_id: watcher.mode == m)
    for key, action in (("F5", "file.reload"), ("Ctrl+N", "file.new"), ("Ctrl+O", "file.open"), ("Ctrl+S", "file.save"),
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
    ui.menu("canvas", "file.reload", path="File", group="file", order=5)
    for order, mode_id in enumerate(WATCH_MODES):
        ui.menu("canvas", f"file.watch_{mode_id}", path="File/When the File Changes", group="file.watch", order=order)

    def toggle_resume() -> None:
        """Switch Session Resume (reopen your last board automatically on launch) on or off."""
        recovery.resume_enabled = not recovery.resume_enabled
        app.actions.refresh_states()

    def set_resume_enabled(value: bool) -> None:
        """Turn Session Resume on or off (the start panel's checkbox)."""
        recovery.resume_enabled = bool(value)
        app.actions.refresh_states()

    ui.command("toggle_resume", toggle_resume, undoable=False, echo=False)
    ui.command("set_resume_enabled", set_resume_enabled, undoable=False, echo=False)
    a("file.session_resume", "file.toggle_resume", "Resume Last Session on Launch",
      keywords="restore reopen autosave crash recovery startup untitled draft", category="File",
      checked=lambda: recovery.resume_enabled)
    ui.menu("canvas", "file.session_resume", path="File", group="file.session", order=0)

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

    # ---- Save location & fun names ------------------------------------------------------------

    def set_save_dir(path: str) -> str:
        """Set the folder new scenes are saved to ("" = the default Documents/pixelRef)."""
        path = str(path or "").strip()
        if path:
            Path(path).expanduser().mkdir(parents=True, exist_ok=True)
        ui.settings.setValue(SAVE_DIR_SETTING, path)
        return path

    def set_fun_names(enabled: bool) -> bool:
        """Give new scenes a random fun name (e.g. Brave-Tiger) instead of scene.pxref."""
        ui.settings.setValue(FUN_NAMES_SETTING, bool(enabled))
        return bool(enabled)

    ui.command("set_save_dir", set_save_dir, undoable=False, echo=False)
    ui.command("set_fun_names", set_fun_names, undoable=False, echo=False)

    def pick_save_dir() -> None:
        """Choose the folder new scenes are saved to."""
        current = str(ui.settings.value(SAVE_DIR_SETTING, "") or "") or str(save_dir(ui.settings))
        folder = QFileDialog.getExistingDirectory(app.main_window(), "Save New Scenes To", current)
        if folder:
            set_save_dir(folder)

    ui.command("pick_save_dir", pick_save_dir, undoable=False, echo=False)
    a("file.save_dir", "file.pick_save_dir", "Save Location…", keywords="folder location directory where scenes save",
      category="File")
    ui.menu("canvas", "file.save_dir", path="File", group="file.location", order=3)

    from .ui import FilesPanel

    def make_files_panel(parent: Any, _inspector: Any) -> Any:
        return FilesPanel(ui, parent)

    ui.contribute("inspector.empty_panels", EmptyPanel("files", 10, make_files_panel))

    def on_close() -> bool:
        # Anything edited (even deleted back down to nothing) still counts as content:
        # there may be a draft worth resuming, so the start panel's checkbox should show.
        # A session that never did anything at all doesn't get to erase that memory:
        # opening the app and closing it right away leaves an earlier non-empty
        # session's resumability untouched (there's simply nothing new to report).
        if session.scene.nodes or session.unsaved:
            ui.settings.setValue(LAST_SESSION_HAD_CONTENT_SETTING, True)
        if recovery.resume_enabled:
            # Session Resume: keep whatever was open (saved, Untitled, or edited)
            # instead of interrupting the close with a save/discard prompt.
            ui.canvas.flush_pending()
            recovery.suspend()
            recovery.timer.stop()
            return True
        if not confirm_close_all():
            return False
        recovery.timer.stop()
        if session.scene_path is None and session.unsaved:
            # Session Resume is off, but don't lose an Untitled draft entirely: keep it
            # recoverable (with a thumbnail) as a "Resume Last Session" card next time.
            ui.canvas.flush_pending()
            recovery.suspend()
        else:
            recovery.clear()
        return True

    ui.contribute("app.close_handlers", on_close)

    from .start import activate_start_ui

    activate_start_ui(ui)

    from .panel import activate_files_panel_ui

    activate_files_panel_ui(ui)
