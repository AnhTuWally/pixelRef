"""Files: the scene document's lifecycle in the desktop app, scene thumbnails and
the start panel (recent files) on an empty board."""

from __future__ import annotations

import json
import logging
import os
import queue
import random
import secrets
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QStandardPaths, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
                               QLabel, QMessageBox, QVBoxLayout)

from pixelref.core import paths
from pixelref.plugin_api import SCENE_EXT, SCENE_EXTS, perf
from pixelref.plugin_api.contracts import EmptyPanel, IngestHandler

from . import recovery_store as rs
from . import workspace as ws
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
# Boards with no unsaved changes are not kept in memory as scene text once they are parked: the
# file on disk is the copy, and selecting the entry loads it from there. Dirty boards always keep
# their text (nothing else holds their edits). False restores the old keep-everything behaviour.
EVICT_CLEAN = True
AVAILABILITY_TTL = 30.0  # seconds before a listed file's existence is looked at again

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
        elif (portable := paths.portable()) is not None:
            path = portable.boards
        else:
            base = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or str(Path.home() / "Documents")
            path = Path(base) / "pixelRef"
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return Path.cwd()


class _AvailabilitySignals(QObject):
    done = Signal(dict)  # path -> whether the file exists


class _AutosaveSignals(QObject):
    done = Signal(int, int, bool, dict, object)  # generation, revision, written, meta, target


class _ParkSignals(QObject):
    done = Signal(object, bool)  # the write task, whether its temporary file was written


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class RecoveryService:
    """Autosave of unsaved work, and getting it back after a crash or a deliberate close.

    Every board that has a file keeps its own snapshot (see ``recovery_store``), so a board parked
    in the Files list is as protected as the current one. The old single slot remains for the
    Untitled board, and is still read for snapshots an older pixelRef left behind.

    The timer's autosaves capture the scene on the UI thread (cheap: image data
    is shared, not copied) and write it on a worker thread, so a board with
    hundreds of megabytes of embedded photos doesn't freeze every minute.
    """

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.session = ui.session
        self.autosaved_revision = 0
        self.owns_recovery = False  # the single slot (the Untitled board) holds this window's work
        self.generation = 0  # bumped by clear(): a write finishing after it is thrown away
        self._writing: threading.Thread | None = None
        self._signals = _AutosaveSignals()
        self._signals.done.connect(self._written)
        self.token = secrets.token_hex(8)  # tells this window's snapshots from another window's
        rs.register_owner(self.token)
        self.store = rs.SnapshotStore(self.recovery_dir())
        self.owned: set[str] = set()  # snapshot ids this window wrote, or took over by restoring
        self.committed: set[str] = set()  # ...of which the text is known to be on disk
        self.current_snapshot: str | None = None  # id of the snapshot that belongs to the current board
        self._park_gen: dict[str, int] = {}
        self._park_queue: queue.Queue = queue.Queue()
        self._park_thread: threading.Thread | None = None
        self._park_signals = _ParkSignals()
        self._park_signals.done.connect(self._parked_written)
        self.timer = QTimer()
        self.timer.setInterval(AUTOSAVE_INTERVAL_MS)
        self.timer.timeout.connect(lambda: self.autosave(background=True))
        self.timer.start()

    def dispose(self) -> None:
        rs.unregister_owner(self.token)

    @staticmethod
    def recovery_dir() -> Path:
        if (portable := paths.portable()) is not None:
            return portable.local_data / "recovery"
        base = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation) or str(Path.home() / ".pixelref")
        return Path(base) / "recovery"

    def recovery_files(self) -> tuple[Path, Path]:
        """The single slot's files (the Untitled board, and what older versions wrote)."""
        folder = self.recovery_dir()
        return folder / f"recovery{SCENE_EXT}", folder / "recovery.json"

    def _target(self) -> tuple[Path, Path, str | None]:
        """Where the current board's unsaved work goes: its own snapshot, or the single slot."""
        path = self.session.scene_path
        if path is None:
            scene_file, meta_file = self.recovery_files()
            return scene_file, meta_file, None
        resolved = str(Path(path).resolve())
        scene_file, meta_file = self.store.files(resolved)
        return scene_file, meta_file, rs.doc_id(resolved)

    # ---- Autosave of the current board --------------------------------------------------------

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
        scene_file, meta_file, key = target = self._target()
        try:
            scene_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        with perf.span("file.autosave_snapshot"):
            snapshot = session.format.snapshot(session.scene)
        original = str(session.scene_path) if session.scene_path else None
        meta = {
            "scene_file": original,
            "saved_at": _now(),
            "saved_ns": time.time_ns(),  # orders snapshots finer than saved_at's seconds
            "items": len(session.scene.nodes),
            "pid": os.getpid(),
            "token": self.token,
            **rs.disk_stamp(original),
            **(extra_meta or {}),
        }
        temp = scene_file.with_suffix(".tmp")
        generation, revision = self.generation, session.revision
        if not background:
            return self._written(generation, revision, self._write(snapshot, temp), meta, target)

        def run() -> None:
            self._signals.done.emit(generation, revision, self._write(snapshot, temp), meta, target)

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

    def _written(self, generation: int, revision: int, written: bool, meta: dict, target: Any) -> bool:
        """Put a written recovery copy in place (on the UI thread), unless clear() came first."""
        self._writing = None
        scene_file, meta_file, key = target
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
        if key is None:
            self.owns_recovery = True
        else:
            self.owned.add(key)
            self.committed.add(key)
            if key == self._target()[2]:  # still the current board (a late write must not claim another's)
                self.current_snapshot = key
        return True

    def wait(self) -> None:
        """Finish background writes now (tests, shutting down)."""
        writing = self._writing
        if writing is not None:
            writing.join()
            QApplication.sendPostedEvents(self._signals)
        self._park_queue.join()
        QApplication.sendPostedEvents(self._park_signals)

    def clear_slot(self) -> None:
        """Forget the single slot's copy (the Untitled board), leaving every board's snapshot alone."""
        if not self.owns_recovery:
            return
        for path in self.recovery_files():
            try:
                path.unlink()
            except OSError:
                pass
        self.owns_recovery = False

    def purge_all(self) -> None:
        """Delete every recovery copy there is (tests, a deliberate reset)."""
        self.generation += 1
        self.owns_recovery = True
        self.clear()
        self.owned.clear()
        self.committed.clear()
        self.current_snapshot = None
        for folder in (self.store.folder, self.store.root / rs.CORRUPT):
            shutil.rmtree(folder, ignore_errors=True)

    def clear(self) -> None:
        """The current board's unsaved work is saved, replaced or discarded: forget its recovery copy."""
        self.generation += 1
        key, self.current_snapshot = self.current_snapshot, None
        if key is not None:
            self._drop(key)
        if not self.owns_recovery:
            return  # never delete another session's recovery copy
        for path in self.recovery_files():
            try:
                path.unlink()
            except OSError:
                pass
        self.owns_recovery = False

    # ---- Boards parked in the Files list ------------------------------------------------------

    def _drop(self, key: str) -> None:
        self._park_gen[key] = self._park_gen.get(key, 0) + 1  # a write still in flight is thrown away
        if key in self.owned:
            self.store.remove(key)
            self.owned.discard(key)
            self.committed.discard(key)

    def discard(self, path: str) -> None:
        """A board's unsaved work is gone (saved, or deliberately discarded): remove its snapshot."""
        key = rs.doc_id(path)
        if self.current_snapshot == key:
            self.current_snapshot = None
        self._drop(key)

    def adopt(self, path: str) -> None:
        """A parked board became the current one: its snapshot is now the current board's."""
        key = rs.doc_id(path)
        self.owned.add(key)
        self.current_snapshot = key

    def park(self, path: str, text: str, items: int | None = None) -> None:
        """Write a parked board's unsaved text to its snapshot (on a worker thread)."""
        key = rs.doc_id(path)
        self.generation += 1  # an autosave of this board still in flight would land older text over it
        gen = self._park_gen[key] = self._park_gen.get(key, 0) + 1
        if self.current_snapshot == key:
            self.current_snapshot = None
        scene_file, meta_file = self.store.files(path)
        meta = {"scene_file": path, "saved_at": _now(), "saved_ns": time.time_ns(), "items": items,
                "pid": os.getpid(), "token": self.token, **rs.disk_stamp(path)}
        task = {"key": key, "gen": gen, "scene_file": scene_file, "meta_file": meta_file,
                "temp": scene_file.with_suffix(".tmp"), "text": text, "meta": meta}
        if self._park_thread is None or not self._park_thread.is_alive():
            self._park_thread = threading.Thread(target=self._park_loop, name="pixelref-recovery-park", daemon=True)
            self._park_thread.start()
        self._park_queue.put(task)

    def _park_loop(self) -> None:
        while True:
            task = self._park_queue.get()
            try:
                try:
                    task["scene_file"].parent.mkdir(parents=True, exist_ok=True)
                    task["temp"].write_text(task["text"], encoding="utf-8", newline="\n")
                    written = True
                except Exception:  # noqa: BLE001  (a failed write must not stop later ones)
                    logging.getLogger(__name__).exception("Saving a parked board's recovery copy failed")
                    written = False
                task["text"] = None
                self._park_signals.done.emit(task, written)
            except Exception:  # noqa: BLE001  (e.g. the app is already gone)
                pass
            finally:
                self._park_queue.task_done()

    def _parked_written(self, task: dict, written: bool) -> None:
        key, temp = task["key"], task["temp"]
        if not written or task["gen"] != self._park_gen.get(key):
            temp.unlink(missing_ok=True)  # discarded, saved or superseded meanwhile
            return
        try:
            os.replace(temp, task["scene_file"])
            task["meta_file"].write_text(json.dumps(task["meta"]), encoding="utf-8")
        except OSError:
            logging.getLogger(__name__).exception("Saving a parked board's recovery copy failed")
            return
        self.owned.add(key)
        self.committed.add(key)

    def keep(self, path: str, text: str, items: int | None = None) -> bool:
        """On a deliberate close: make sure a parked board's unsaved text is on disk, marked
        clean (left on purpose) so the next launch offers it back. False if that failed."""
        self.wait()
        key = rs.doc_id(path)
        scene_file, meta_file = self.store.files(path)
        try:
            snapshot = self.store.read(key) if key in self.committed else None
            if snapshot is None:
                scene_file.parent.mkdir(parents=True, exist_ok=True)
                self.session.format.write_text(scene_file, text)
                meta = {"scene_file": path, "items": items, **rs.disk_stamp(path)}
            else:
                meta = dict(snapshot.meta)
            meta.update(clean=True, saved_at=_now(), saved_ns=time.time_ns(), pid=os.getpid(), token=self.token)
            meta_file.write_text(json.dumps(meta), encoding="utf-8")
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("Keeping a parked board's unsaved work failed")
            return False
        self.owned.add(key)
        self.committed.add(key)
        return True

    # ---- What is on disk ----------------------------------------------------------------------

    def pending(self, path: str | None = None) -> dict[str, Any] | None:
        """The recovery copy of one board (``path``), or of the single slot when omitted."""
        if path is not None:
            resolved = str(Path(path).resolve())
            scene_file, meta_file = self.store.files(resolved)
            extra = {"key": rs.doc_id(resolved)}
        else:
            scene_file, meta_file = self.recovery_files()
            extra = {}
        if not scene_file.exists():
            return None
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        return {"path": scene_file, **extra, **meta}

    def collect(self, clean: bool) -> list[dict[str, Any]]:
        """Recoverable boards: those left on purpose (``clean``), or those a crash left behind (not
        ``clean``: anything another running window is still writing, or this one wrote, is skipped)."""
        found: list[dict[str, Any]] = []
        for snapshot in self.store.list():
            if snapshot.clean != clean or snapshot.id in self.owned:
                continue
            if not clean and self.store.written_by_live_window(snapshot, self.token):
                continue
            found.append(snapshot.info())
        legacy = self.pending()
        if legacy is not None and bool(legacy.get("clean")) == clean:
            if not clean or legacy.get("scene_file"):  # an Untitled draft is resume()'s own business
                found.append(legacy)
        return found

    def _forget(self, info: dict[str, Any]) -> None:
        """Declined: remove that recovery copy."""
        if info.get("key"):
            self.store.remove(info["key"])
            self.owned.discard(info["key"])
        else:
            self.owns_recovery = True
            self.clear()

    @staticmethod
    def _name(info: dict[str, Any]) -> str:
        return Path(info["scene_file"]).name if info.get("scene_file") else "an unsaved board"

    def _choose(self, items: list[dict[str, Any]], crash: bool) -> list[dict[str, Any]] | None:
        """One question for every board with recoverable work. Returns the boards to restore, an empty
        list to discard them all, or None to decide at the next launch."""
        parent = self.ui.app.main_window()
        if len(items) == 1:
            info = items[0]
            when = str(info.get("saved_at", "")).replace("T", " ")
            note = "\n\nThe file has changed on disk since then." if rs.changed_on_disk(info) else ""
            if crash:
                title = "Restore unsaved work?"
                text = (f"pixelRef didn't close normally last time.\n\nRestore {self._name(info)} "
                        f"({info.get('items', '?')} items, autosaved {when})?{note}")
            else:
                title = "Recover Unsaved Changes?"
                text = (f"You had unsaved changes when pixelRef last closed "
                        f"({info.get('items', '?')} items, {when}).\n\nRecover them?{note}")
            reply = QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            return [info] if reply == QMessageBox.Yes else []
        dialog = QDialog(parent)
        dialog.setWindowTitle("Restore unsaved work?" if crash else "Recover Unsaved Changes?")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(("pixelRef didn't close normally last time." if crash
                                 else "You had unsaved changes when pixelRef last closed.")
                                + "\nTick the boards to restore:"))
        boxes: list[QCheckBox] = []
        for info in items:
            when = str(info.get("saved_at", "")).replace("T", " ")
            note = " — file changed on disk since" if rs.changed_on_disk(info) else ""
            box = QCheckBox(f"{self._name(info)}  ({info.get('items', '?')} items, {when}){note}")
            box.setChecked(True)
            boxes.append(box)
            layout.addWidget(box)
        buttons = QDialogButtonBox(dialog)
        buttons.addButton("Restore Selected", QDialogButtonBox.AcceptRole)
        discard = buttons.addButton("Discard All", QDialogButtonBox.DestructiveRole)
        buttons.addButton("Decide Later", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        buttons.clicked.connect(lambda button: dialog.done(2) if button is discard else None)
        layout.addWidget(buttons)
        result = dialog.exec()
        if result == QDialog.Accepted:
            chosen = [info for info, box in zip(items, boxes) if box.isChecked()]
            for info in items:
                if info not in chosen:
                    self._forget(info)  # unticked: the user said no to that one
            return chosen
        return [] if result == 2 else None

    def restore(self, info: dict[str, Any] | None = None) -> bool:
        """Bring a recovery copy back as the current board (default: the single slot's)."""
        info = info if info is not None else self.pending()
        if info is None:
            return False
        try:
            self.session.load_text(Path(info["path"]).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self.ui.app.main_window(), "Restore failed", str(exc))
            if info.get("key"):
                self.store.quarantine(info["key"])  # kept aside, never deleted, and not offered again
            return False
        original = info.get("scene_file")
        self.session.scene_path = Path(original) if original else None
        cache = self.session.services.get("image_cache")
        if cache is not None:
            cache.clear()
        if info.get("key"):
            self.owned.add(info["key"])
            self.committed.add(info["key"])
            self.current_snapshot = info["key"]
        else:
            self.owns_recovery = True  # removed once the restored work is saved
        self.session.mark_unsaved()
        files = self.session.services.get("files")
        if files is not None and original:
            files.register_open(original)
        QTimer.singleShot(0, lambda: self.ui.app.run_command("navigation.frame_all"))
        self.ui.toast(f"Restored unsaved work from {info.get('saved_at', 'the last session')}")
        return True

    def _restore_many(self, infos: list[dict[str, Any]]) -> bool:
        """Restore several boards: the one open now becomes current again (an empty window shows the
        most recent); the rest join the Files list as boards with unsaved changes. True if one
        became current."""
        if not infos:
            return False
        current = self.session.scene_path
        current_key = rs.doc_id(str(Path(current).resolve())) if current else None
        active = next((i for i in infos if current_key and i.get("key") == current_key), None)
        if active is None and current is None and not self.session.scene.nodes:
            active = max(infos, key=rs.recency)  # an empty window: show the one worked on last
        restored = self.restore(active) if active is not None else False
        files = self.session.services.get("files")
        for info in infos:
            if info is active or files is None or not info.get("scene_file"):
                continue
            try:
                text = Path(info["path"]).read_text(encoding="utf-8")
            except OSError as exc:
                self.ui.toast(f"Could not restore {self._name(info)}: {exc}")
                if info.get("key"):
                    self.store.quarantine(info["key"])
                continue
            files.park_recovered(info["scene_file"], text)
            if info.get("key"):
                self.owned.add(info["key"])
                self.committed.add(info["key"])
            else:
                self._forget(info)  # an old single-slot copy: now held as a parked board
                self.park(info["scene_file"], text)
        return restored

    def offer(self) -> bool:
        """At startup: offer, in one dialog, the work of a session that didn't close cleanly."""
        if ws.is_child_window():
            return False  # a window opened by another one never claims its recovery copies
        items = self.collect(clean=False)
        if not items:
            return False
        chosen = self._choose(items, crash=True)
        if chosen is None:
            return False  # decide later: everything stays where it is
        for info in items:
            if not any(info is c for c in chosen):
                self._forget(info)
        return self._restore_many(chosen)

    # ---- Session Resume -----------------------------------------------------------------

    @property
    def restore_mode(self) -> str:
        """What launch restores: "workspace", "list" or "empty" (migrated from the old checkbox)."""
        return ws.resolve_mode(self.ui.settings)

    @restore_mode.setter
    def restore_mode(self, value: str) -> None:
        if value in ws.MODES:
            self.ui.settings.setValue(ws.MODE_SETTING, value)

    @property
    def resume_enabled(self) -> bool:
        """The Resume Last Session checkbox: on exactly when the workspace is restored."""
        return self.restore_mode == ws.MODE_WORKSPACE

    @resume_enabled.setter
    def resume_enabled(self, value: bool) -> None:
        self.restore_mode = ws.MODE_WORKSPACE if value else ws.MODE_EMPTY
        self.ui.settings.setValue(RESUME_SETTING, bool(value))  # keeps an older pixelRef's checkbox in step

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
        """At startup: bring back the workspace as the restore mode says, and offer the unsaved work
        that was left on purpose. True if a board was opened."""
        files = self.session.services.get("files")
        # Registers the saved Files list as dormant entries (nothing is loaded) and lets the
        # workspace start saving itself; a window that doesn't own the workspace gets nothing.
        restored = files.restore_workspace() if files is not None else 0
        if files is None or not files.workspace_owner() or ws.is_child_window():
            return False
        opened = False
        if self.restore_mode == ws.MODE_WORKSPACE:
            state = self.ui.settings.json_value(RESUME_STATE_SETTING)
            if not state:
                # Nothing unsaved in the current board: just reopen the one that was active.
                active = files.workspace_active() if restored else None
                if active and Path(active).exists() and files.open_path(active, confirm=False):
                    files.apply_saved_camera(active)
                    opened = True
            else:
                path = state.get("path")
                if path is None:
                    # Was an Untitled/unsaved board: bring its exact draft back automatically.
                    opened = self.restore() if self.pending() is not None else False
                elif Path(path).exists() and files.open_path(path, confirm=False):
                    files.apply_saved_camera(path)
                    opened = True
        # Work left on purpose in any board (the open one, or parked in the Files list): one question.
        return self.offer_unsaved() or opened

    def offer_unsaved(self) -> bool:
        """Offer back, in one dialog, the unsaved work of boards left open on purpose."""
        items = self.collect(clean=True)
        if not items:
            return False
        chosen = self._choose(items, crash=False)
        if chosen is None:
            return False
        for info in items:
            if not any(info is c for c in chosen):
                self._forget(info)
        return self._restore_many(chosen)


def activate_ui(ui: Any) -> None:
    session = ui.session
    app = ui.app
    recovery = RecoveryService(ui)
    ui.provide_service("recovery", recovery)
    ui.track(recovery.timer.stop)
    ui.track(recovery.dispose)

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
            save_workspace()

    ui.on("scene.opened", on_opened)
    # A first save of an untitled scene (or Save As to a new path) creates a file this
    # window now holds open; without this it never appears in the Files panel.
    ui.on("scene.saved", on_opened)

    def on_replaced(_event: Any) -> None:
        # New / Open restart the revision count at 0: a stale autosaved revision would
        # otherwise skip autosaving the new document once its edits reach that number.
        recovery.autosaved_revision = 0
        save_workspace()

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
        ui.canvas.flush_pending()  # an edit still in flight would make a "clean" board dirty
        view = app.active_view()
        entry.camera = view.camera() if view is not None else None
        entry.unsaved = session.unsaved
        if entry.unsaved or not EVICT_CLEAN or not Path(entry.path).is_file():
            entry.text = session.serialize()  # nothing but this copy holds the board's state
            if entry.unsaved:  # and a crash must not lose it either: keep a copy on disk too
                recovery.park(entry.path, entry.text, len(session.scene.nodes))
        else:
            entry.text = None  # clean and on disk: no serialization, nothing retained

    def _restore_state(entry: OpenFile) -> None:
        """Restore a registry entry's captured state into the session."""
        session.load_text(entry.text, Path(entry.path))
        session.revision = session.saved_revision = 0
        if entry.unsaved:
            session.mark_unsaved()
            recovery.adopt(entry.path)  # its snapshot is the current board's again
        if EVICT_CLEAN:
            # The board is current now, so the session holds its state; a second copy as text is
            # dead weight (switching away captures it again if it needs one).
            entry.text, entry.unsaved = None, False
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

        entry = _find_entry(target)
        if entry is not None and entry.text is None and not target.is_file():
            _mark_missing(entry.path)  # say so quietly, rather than failing with a dialog
            ui.status(f"File not found: {target.name}")
            return False

        # Capture the current file's state (no prompt)
        if current is not None:
            _capture_state()

        # Check if the target is in the registry with captured state
        if entry is not None and entry.text is not None:
            _restore_state(entry)
            ui.status(f"Switched to: {target.name}")
        else:
            # Load from disk (new open)
            if not open_path(str(target), confirm=False, track_recent=False):
                return False
            apply_saved_camera(str(target))  # a dormant or evicted entry's remembered view
        save_workspace()
        return True

    def reorder_open_files(from_path: str, to_path: str) -> None:
        """Reorder the open-files registry (drag-and-drop)."""
        from_idx = next((i for i, e in enumerate(open_files) if e.path == from_path), None)
        to_idx = next((i for i, e in enumerate(open_files) if e.path == to_path), None)
        if from_idx is None or to_idx is None:
            return
        entry = open_files.pop(from_idx)
        open_files.insert(to_idx, entry)
        save_workspace()

    def open_files_list() -> list[str]:
        """The open files in registry order (for the panel and cycling)."""
        return [e.path for e in open_files]

    def open_files_state() -> list[tuple[str, bool]]:
        """(path, unsaved) pairs in registry order, for the panel's border sync."""
        return [(e.path, e.unsaved) for e in open_files]

    # ---- Persistent workspace -----------------------------------------------------------------
    # The saved Files list (paths, order, remembered views; never scene data). Entries restored
    # from it are dormant: OpenFile.text is None, so nothing loads until one is selected.

    lock = ws.OwnerLock(RecoveryService.recovery_dir() / "workspace.lock")
    ui.track(lock.release)
    workspace = {"ready": False, "written": None}

    def workspace_owner() -> bool:
        """Whether this process owns the saved workspace. Windows spawned by Open in New Window
        never do, and a second pixelRef launched on its own finds it held: neither saves."""
        return not ws.is_child_window() and lock.acquire()

    def workspace_active() -> str | None:
        data = ws.parse_manifest(ui.settings.json_value(ws.MANIFEST_SETTING), SCENE_EXTS)
        return data["active"]

    def restore_workspace() -> int:
        """Register the saved Files list as dormant entries, honouring the startup mode, and let
        the workspace start saving itself. Returns how many entries were restored."""
        workspace["ready"] = True  # until now a startup event could overwrite what is being restored
        if not workspace_owner() or recovery.restore_mode == ws.MODE_EMPTY:
            return 0
        data = ws.parse_manifest(ui.settings.json_value(ws.MANIFEST_SETTING), SCENE_EXTS)
        known = {e.path for e in open_files}
        added = 0
        for item in data["files"]:
            if item["path"] in known:
                continue
            entry = OpenFile(item["path"])
            entry.camera = item["camera"]
            open_files.append(entry)
            added += 1
        if added:
            app.events.emit("files.changed", {})
            check_availability()
        return added

    def dormant_files() -> list[str]:
        """Open files whose scene is not held in memory (restored, never loaded or evicted)."""
        current = session.scene_path
        current = str(Path(current).resolve()) if current else None
        return [e.path for e in open_files if e.text is None and e.path != current]

    def apply_saved_camera(path: str) -> bool:
        """After a dormant entry has loaded, put the view back where it was left (once)."""
        entry = _find_entry(Path(path))
        if entry is None or entry.text is not None or entry.camera is None:
            return False
        cam, entry.camera = entry.camera, None
        QTimer.singleShot(0, lambda: _restore_camera(cam))
        return True

    def register_open_path(path: str) -> None:
        """List a file as open (a board restored from recovery that already has a path)."""
        _register_open(Path(path))
        save_workspace()
        app.events.emit("files.changed", {})

    def park_recovered(path: str, text: str) -> None:
        """List a board recovered from a snapshot as parked with unsaved changes."""
        entry = _find_entry(Path(path))
        if entry is None:
            entry = OpenFile(str(Path(path).resolve()))
            open_files.append(entry)
        entry.text, entry.unsaved = text, True
        save_workspace()
        app.events.emit("files.changed", {})

    def save_workspace() -> bool:
        """Write the manifest if it changed. Cheap: no scene is serialized."""
        if not workspace["ready"] or not workspace_owner():
            return False
        current = session.scene_path
        active = str(Path(current).resolve()) if current else None
        view = app.active_view()
        entries = [(e.path, view.camera() if (e.path == active and view is not None) else e.camera)
                   for e in open_files]
        untitled = current is None and bool(session.scene.nodes or session.unsaved)
        manifest = ws.build_manifest(entries, active, untitled)
        dumped = ws.dumps(manifest)
        if dumped == workspace["written"]:
            return False
        ui.settings.set_json(ws.MANIFEST_SETTING, manifest)
        workspace["written"] = dumped
        return True

    # ---- Missing files ------------------------------------------------------------------------
    # Whether each listed file still exists is learnt on a worker thread (a dead network share can
    # block for a long time), cached, and re-checked at most every AVAILABILITY_TTL seconds. An
    # unavailable file stays in the list and in the saved workspace: the drive may come back.

    availability: dict[str, Any] = {"known": {}, "checked": {}, "thread": None, "again": False}
    availability_signals = _AvailabilitySignals()

    def _availability_done(result: dict) -> None:
        availability["thread"] = None
        now, changed = time.monotonic(), False
        for path, present in result.items():
            changed |= availability["known"].get(path) != present
            availability["known"][path] = present
            availability["checked"][path] = now
        if changed:
            app.events.emit("files.availability_changed", {})
        if availability["again"]:  # a forced check arrived while this one ran: look again
            availability["again"] = False
            check_availability(force=True)

    availability_signals.done.connect(_availability_done)

    def check_availability(force: bool = False) -> bool:
        """Start a background look at which listed files exist (those not checked recently, or all
        with ``force``). Returns False when there was nothing to do or a check is running."""
        if availability["thread"] is not None:
            availability["again"] = availability["again"] or force
            return False
        now = time.monotonic()
        stale = [e.path for e in open_files
                 if force or now - availability["checked"].get(e.path, float("-inf")) > AVAILABILITY_TTL]
        if not stale:
            return False

        def run() -> None:
            availability_signals.done.emit({p: Path(p).is_file() for p in stale})

        availability["thread"] = threading.Thread(target=run, name="pixelref-availability", daemon=True)
        availability["thread"].start()
        return True

    def wait_availability() -> None:
        """Finish a running availability check now (tests)."""
        while (thread := availability["thread"]) is not None:
            thread.join()
            QApplication.sendPostedEvents(availability_signals)

    def file_missing(path: str) -> bool:
        """True only when the file is known to be missing (unknown counts as present)."""
        return availability["known"].get(str(path)) is False

    def _mark_missing(path: str) -> None:
        availability["known"][path] = False
        availability["checked"][path] = time.monotonic()
        app.events.emit("files.availability_changed", {})

    def relocate_file(old_path: str, new_path: str | None = None) -> bool:
        """Point a listed (typically missing) file at its new location, keeping its place in the
        list and its colour. If the new location is already listed, the old entry just goes."""
        entry = next((e for e in open_files if ws.path_key(e.path) == ws.path_key(old_path)), None)
        if entry is None:
            return False
        if entry.path == _current_key():
            ui.status("This is the open board: use Save As to put it somewhere new")
            return False
        if new_path is None:
            new_path, _ = QFileDialog.getOpenFileName(app.main_window(), "Locate File",
                                                      str(Path(entry.path).parent), SCENE_FILTER)
            if not new_path:
                return False
        target = Path(new_path)
        if target.suffix.lower() not in SCENE_EXTS or not target.is_file():
            ui.status(f"Not a scene file: {target.name}")
            return False
        new_key = str(target.resolve())
        old_key = entry.path
        existing = next((e for e in open_files if e.path == new_key and e is not entry), None)
        if existing is not None:
            open_files.remove(entry)  # already listed: no duplicate
        else:
            entry.path = new_key
        mapping = _load_colors()
        moved = mapping.pop(ws.path_key(old_key), None)
        if moved and ws.path_key(new_key) not in mapping:
            mapping[ws.path_key(new_key)] = moved
        if moved is not None:
            _store_colors(mapping)
        availability["known"].pop(old_key, None)
        availability["known"][new_key] = True
        availability["checked"][new_key] = time.monotonic()
        save_workspace()
        app.events.emit("files.changed", {})
        app.events.emit("files.color_changed", {"path": new_key})
        ui.status(f"Located: {target.name}")
        return True

    # ---- Colour labels ------------------------------------------------------------------------
    # A global path -> colour map in settings: independent of which files are open, and of the
    # document. Changing one touches nothing but that map and the row's colour strip.

    colors: dict[str, dict[str, str] | None] = {"map": None}

    def _load_colors() -> dict[str, str]:
        return ws.parse_colors(ui.settings.json_value(ws.COLORS_SETTING))

    def file_colors() -> dict[str, str]:
        """Path key -> colour name for every labelled file (cached; read once)."""
        if colors["map"] is None:
            colors["map"] = _load_colors()
        return dict(colors["map"])

    def file_color(path: str) -> str | None:
        if colors["map"] is None:
            file_colors()
        return colors["map"].get(ws.path_key(str(path)))

    def _store_colors(mapping: dict[str, str]) -> None:
        keep = [e.path for e in open_files] + recent_files()
        mapping = ws.prune_colors(mapping, keep)
        ui.settings.set_json(ws.COLORS_SETTING, mapping)
        colors["map"] = mapping

    def set_color(path: str | None = None, color: str | None = None) -> bool:
        """Label a file (default: the current one) with a colour from the palette
        (blue, green, yellow, orange, purple, pink); an empty colour removes the label."""
        target = str(Path(path).resolve()) if path else _current_key()
        if target is None:
            ui.status("The scene hasn't been saved to a file yet")
            return False
        if color and color not in ws.PALETTE:
            ui.status(f"Unknown colour: {color}")
            return False
        mapping = _load_colors()  # re-read: another window may have labelled a file meanwhile
        key = ws.path_key(target)
        if color:
            mapping.pop(key, None)  # re-inserting makes it the newest, so pruning forgets it last
            mapping[key] = color
        else:
            mapping.pop(key, None)
        _store_colors(mapping)
        app.events.emit("files.color_changed", {"path": target})
        return True

    def clear_color(path: str | None = None) -> bool:
        """Remove a file's colour label (default: the current file)."""
        return set_color(path, None)

    # ---- Closing files ------------------------------------------------------------------------
    # Closing removes a file from the Files list only: it stays in Recent Files, and nothing is
    # closed until every unsaved board involved has been saved, discarded or the user cancelled.

    def _current_key() -> str | None:
        current = session.scene_path
        return str(Path(current).resolve()) if current else None

    def _load_entry(entry: OpenFile) -> bool:
        """Make a registry entry the current document. Unlike switch_to this never captures the
        current one: it is being closed, and capturing would put it back in the list."""
        if entry.text is not None:
            _restore_state(entry)
            return True
        if not open_path(entry.path, confirm=False, track_recent=False):
            return False
        apply_saved_camera(entry.path)
        return True

    def _empty_document() -> None:
        session.run("new_scene")
        view = app.active_view()
        if view is not None:
            view.set_camera([0.0, 0.0], 1.0, 0.0)
        recovery.clear()

    def _closed(message: str) -> None:
        save_workspace()
        app.events.emit("files.changed", {})
        ui.status(message)

    def close_file(path: str | None = None) -> bool:
        """Close a file (default: the current one). Unsaved changes are asked about first; if the
        file is the current one the next file in the list (else the previous) takes its place,
        and with none left the empty board and start screen show."""
        ui.canvas.flush_pending()
        current = _current_key()
        target = str(Path(path).resolve()) if path else current
        if target is None:
            return new_unprompted()  # an Untitled board: closing it is a new, empty board
        entry = next((e for e in open_files if e.path == target), None)
        if entry is None:
            return False
        is_current = target == current
        if not confirm_close_all(include_current=is_current, entries=[entry]):
            return False
        index = open_files.index(entry)
        open_files.remove(entry)
        if is_current:
            neighbour = (open_files[index] if index < len(open_files)
                         else open_files[index - 1] if open_files else None)
            if neighbour is None:
                _empty_document()
            elif not _load_entry(neighbour):  # it could not be opened: leave everything as it was
                open_files.insert(index, entry)
                return False
        recovery.discard(target)  # saved or discarded: either way its unsaved work is gone
        _closed(f"Closed: {Path(target).name}")
        return True

    def close_others(path: str | None = None) -> bool:
        """Close every file except ``path`` (default: the current one)."""
        ui.canvas.flush_pending()
        keep_key = str(Path(path).resolve()) if path else _current_key()
        keep = next((e for e in open_files if e.path == keep_key), None)
        if keep is None:
            return False
        others = [e for e in open_files if e is not keep]
        if not others:
            return True
        current = _current_key()
        # An Untitled board goes too when a file replaces it.
        current_goes = current is None or current != keep.path
        if not confirm_close_all(include_current=current_goes, entries=others):
            return False
        before = list(open_files)
        open_files[:] = [keep]
        if current_goes and not _load_entry(keep):
            open_files[:] = before
            return False
        if current is None:
            recovery.clear_slot()  # the Untitled board went
        for gone in others:
            recovery.discard(gone.path)
        _closed(f"Closed {len(others)} other file{'s' if len(others) != 1 else ''}")
        return True

    def close_all() -> bool:
        """Close every file, leaving the empty board and start screen."""
        ui.canvas.flush_pending()
        if not confirm_close_all(include_current=True, entries=list(open_files)):
            return False
        count = len(open_files)
        gone = [e.path for e in open_files]
        open_files.clear()
        _empty_document()
        for path in gone:
            recovery.discard(path)
        _closed(f"Closed {count} file{'s' if count != 1 else ''}")
        return True

    def new_unprompted() -> bool:
        """Replace an Untitled board with a new one after asking about its unsaved work."""
        if not may_discard():
            return False
        _empty_document()
        _closed("Closed the Untitled board")
        return True

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

    def confirm_close_all(include_current: bool = True, entries: list[OpenFile] | None = None) -> bool:
        """Ask about every open file with unsaved changes (checkboxes, default checked).

        ``include_current`` leaves the current board out (Session Resume protects it separately);
        ``entries`` limits the question to a subset of the open files (default: all).
        Returns True to proceed with closing (after saving/discarding as chosen),
        False if the user cancelled.
        """
        ui.canvas.flush_pending()
        # Collect files with unsaved changes: the current session plus captured entries.
        dirty: list[tuple[str, str | None, OpenFile | None]] = []  # (display, text, entry)
        current = session.scene_path
        if include_current and session.unsaved:
            name = Path(current).name if current else "Untitled"
            dirty.append((name, None, None))  # None text = save via the normal save() path
        for entry in (open_files if entries is None else entries):
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
                        recovery.discard(entry.path)
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
                        recovery.discard(entry.path)
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

    def open_path(path: str, confirm: bool = True, track_recent: bool = True) -> bool:
        """Open a scene file (asks about unsaved changes unless confirm=False). Switching between
        files already in the Files list passes track_recent=False: it is not a new access."""
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
        if track_recent:
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
                     ("switch_to", switch_to), ("reorder_open_files", reorder_open_files),
                     ("close", close_file), ("close_others", close_others), ("close_all", close_all),
                     ("set_color", set_color), ("clear_color", clear_color), ("relocate", relocate_file)):
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
        "restore_workspace": staticmethod(restore_workspace),
        "register_open": staticmethod(register_open_path),
        "park_recovered": staticmethod(park_recovered),
        "workspace_owner": staticmethod(workspace_owner),
        "file_missing": staticmethod(file_missing),
        "check_availability": staticmethod(check_availability),
        "wait_availability": staticmethod(wait_availability),
        "relocate_file": staticmethod(relocate_file),
        "file_color": staticmethod(file_color),
        "file_colors": staticmethod(file_colors),
        "set_color": staticmethod(set_color),
        "close_file": staticmethod(close_file),
        "close_others": staticmethod(close_others),
        "close_all": staticmethod(close_all),
        "confirm_close_all": staticmethod(confirm_close_all),
        "open_entries": staticmethod(lambda: list(open_files)),
        "dormant_files": staticmethod(dormant_files),
        "workspace_active": staticmethod(workspace_active),
        "apply_saved_camera": staticmethod(apply_saved_camera),
        "save_workspace": staticmethod(save_workspace),
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
    a("file.close", "file.close", "Close File", keywords="close tab remove open files list", category="File",
      enabled=lambda: bool(open_files) or session.scene_path is None)
    a("file.close_others", "file.close_others", "Close Other Files", keywords="close all except others tabs",
      category="File", enabled=lambda: len(open_files) > 1)
    a("file.close_all", "file.close_all", "Close All Files", keywords="close everything tabs empty",
      category="File", enabled=lambda: bool(open_files))
    for color_name in ws.PALETTE:
        a(f"file.color_{color_name}", "file.set_color", f"Colour File: {color_name.title()}",
          args=(None, color_name), keywords="colour color label tag mark file list", category="File",
          enabled=has_file)
    a("file.clear_color", "file.clear_color", "Remove File Colour", keywords="colour color label clear none",
      category="File", enabled=has_file)
    for mode_id, (mode_text, mode_keywords) in WATCH_MODES.items():
        a(f"file.watch_{mode_id}", "file.set_watch_mode", mode_text, args=(mode_id,), keywords=mode_keywords,
          category="File", group="file.watch", checked=lambda m=mode_id: watcher.mode == m)
    for key, action in (("F5", "file.reload"), ("Ctrl+N", "file.new"), ("Ctrl+O", "file.open"), ("Ctrl+S", "file.save"),
                        ("Ctrl+Shift+S", "file.save_as"), ("Ctrl+Shift+W", "file.close")):
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
    ui.menu("canvas", "file.close", path="File", group="file.close", order=0)
    ui.menu("canvas", "file.close_others", path="File", group="file.close", order=1)
    ui.menu("canvas", "file.close_all", path="File", group="file.close", order=2)
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
            # The current board is kept by suspend(). Boards parked with unsaved edits are kept
            # on disk too, marked as left on purpose, and offered back at the next launch; any
            # that cannot be kept are asked about instead of being dropped.
            current = _current_key()
            parked = [e for e in open_files if e.unsaved and e.text is not None and e.path != current]
            failed = [e for e in parked if not recovery.keep(e.path, e.text)]
            if failed and not confirm_close_all(include_current=False, entries=failed):
                return False
            save_workspace()
            recovery.suspend()
            recovery.timer.stop()
            lock.release()
            return True
        if not confirm_close_all():
            return False
        for entry in open_files:
            recovery.discard(entry.path)  # saved or discarded just now
        save_workspace()
        lock.release()
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
