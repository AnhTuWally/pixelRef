"""Per-board recovery: parked boards are protected from a crash, kept on a deliberate close, and
offered back in one dialog."""

from __future__ import annotations

import json
from pathlib import Path

from harness import make, tmp_dir
from PySide6.QtCore import QSettings

import pixelref.builtins.file as file_plugin
from pixelref.builtins.file import recovery_store as rs
from pixelref.builtins.file import workspace as ws
from test_workspace import _board, _clear, _current, _dirty, _setup


class _Ask:
    """Stands in for QMessageBox: answers Yes/No questions, records warnings."""

    Yes, No = file_plugin.QMessageBox.Yes, file_plugin.QMessageBox.No
    Save, Discard, Cancel = (file_plugin.QMessageBox.Save, file_plugin.QMessageBox.Discard,
                             file_plugin.QMessageBox.Cancel)
    answer = Yes
    asked: list[str] = []
    warnings: list[str] = []

    @classmethod
    def question(cls, _parent, _title, text, *_a, **_k):
        cls.asked.append(text)
        return cls.answer

    @classmethod
    def warning(cls, _parent, _title, text, *_a, **_k):
        cls.warnings.append(text)

    @classmethod
    def critical(cls, *_a, **_k):
        return None


class _Dialog(file_plugin.QDialog):
    outcome = file_plugin.QDialog.Accepted
    shown = 0

    def exec(self):
        type(self).shown += 1
        return type(self).outcome


def _patch(answer=_Ask.Yes, outcome=file_plugin.QDialog.Accepted):
    file_plugin.QMessageBox = _Ask
    file_plugin.QDialog = _Dialog
    _Ask.answer, _Ask.asked, _Ask.warnings = answer, [], []
    _Dialog.outcome, _Dialog.shown = outcome, 0


def _unpatch(box, dialog):
    file_plugin.QMessageBox, file_plugin.QDialog = box, dialog


def _snap(h, path):
    return h.service("recovery").store.read(rs.doc_id(path))


def _images(h):
    return len(h.nodes_of("image"))


def _quit_with_parked(h, names=("a", "b", "c"), dirty=("a",)):
    """Boards open, ``dirty`` ones edited and parked, the last (clean) one current; quit in workspace mode."""
    paths = _setup(h, names)
    h.service("recovery").resume_enabled = True
    for name in dirty:
        _dirty(h, paths[names.index(name)])
    h.run("file.switch_to", paths[-1])
    assert h.app.request_close()
    return paths


def _launch(restore=True):
    h2 = make()
    h2.service("recovery").resume_enabled = True
    return h2


def test_parking_a_dirty_board_writes_a_recovery_snapshot_and_a_clean_one_does_not():
    h = make()
    try:
        a, b, c = _setup(h)
        recovery = h.service("recovery")
        _dirty(h, a)
        h.run("file.switch_to", b)  # a is parked dirty
        recovery.wait()
        snap = _snap(h, a)
        assert snap is not None and not snap.clean and snap.path == a and snap.meta["items"] == 2
        assert _snap(h, b) is None and _snap(h, c) is None, "clean boards need no snapshot"
        assert "disk_mtime_ns" in snap.meta and snap.meta["token"] == recovery.token
    finally:
        _clear(h)
        h.close()


def test_saving_or_discarding_a_parked_board_removes_its_snapshot():
    h = make()
    original = file_plugin.QMessageBox
    try:
        a, b, c = _setup(h)
        recovery = h.service("recovery")
        for target in (a, b):
            _dirty(h, target)
        h.run("file.switch_to", c)
        recovery.wait()
        assert _snap(h, a) and _snap(h, b)
        file_plugin.QMessageBox = _Ask
        _Ask.answer, _Ask.asked = _Ask.Save, []
        h.run("file.close", a)  # Save: written to disk, snapshot gone
        assert _snap(h, a) is None
        _Ask.answer = _Ask.Discard
        h.run("file.close", b)  # Discard: edits gone, snapshot gone
        assert _snap(h, b) is None
        assert (Path(a).stat().st_size > 0) and h.service("files").open_files_list() == [c]
    finally:
        file_plugin.QMessageBox = original
        _clear(h)
        h.close()


def test_switching_back_to_a_parked_board_and_saving_removes_its_snapshot():
    h = make()
    try:
        a, b = _setup(h, ("a", "b"))
        recovery = h.service("recovery")
        _dirty(h, a)
        h.run("file.switch_to", b)
        recovery.wait()
        assert _snap(h, a)
        h.run("file.switch_to", a)
        assert h.session.unsaved and _images(h) == 2
        assert h.save_to(Path(a))
        assert _snap(h, a) is None, "saved: nothing left to recover"
    finally:
        _clear(h)
        h.close()


def test_a_snapshot_write_finishing_after_the_board_was_discarded_leaves_nothing():
    h = make()
    try:
        a, b = _setup(h, ("a", "b"))
        recovery = h.service("recovery")
        _dirty(h, a)
        h.run("file.switch_to", b)
        recovery.discard(a)  # discarded while the write may still be running
        recovery.wait()
        assert _snap(h, a) is None
        assert not list(recovery.store.folder.glob("*.tmp")), "and no temporary file is left"
    finally:
        _clear(h)
        h.close()


def test_quitting_in_workspace_mode_keeps_parked_boards_without_asking_and_offers_them_back():
    h = make()
    box, dialog = file_plugin.QMessageBox, file_plugin.QDialog
    try:
        _patch()
        a, b, c = _quit_with_parked(h)
        assert not _Ask.asked and not _Dialog.shown, "no quit prompt"
        snap = rs.SnapshotStore(file_plugin.RecoveryService.recovery_dir()).read(rs.doc_id(a))
        assert snap is not None and snap.clean, "kept on purpose"

        h2 = _launch()
        try:
            assert h2.service("recovery").resume()
            assert len(_Ask.asked) == 1 and "unsaved changes" in _Ask.asked[0]
            files = h2.service("files")
            assert files.open_files_list() == [a, b, c]
            assert dict(files.open_files_state())[a], "a is back as a board with unsaved changes"
            assert _current(h2) == c, "and the board you were on is still the one shown"
            h2.run("file.switch_to", a)
            assert _images(h2) == 2 and h2.session.unsaved, "with its edit"
        finally:
            h2.close()
    finally:
        _unpatch(box, dialog)
        _clear(h)
        h.close()


def test_declining_the_recovery_offer_discards_the_unsaved_work():
    h = make()
    box, dialog = file_plugin.QMessageBox, file_plugin.QDialog
    try:
        _patch(answer=_Ask.No)
        a, b, c = _quit_with_parked(h)
        h2 = _launch()
        try:
            h2.service("recovery").resume()
            assert _snap(h2, a) is None, "declined: gone"
            assert not dict(h2.service("files").open_files_state())[a]
            h2.run("file.switch_to", a)
            assert _images(h2) == 1, "the saved version"
        finally:
            h2.close()
    finally:
        _unpatch(box, dialog)
        _clear(h)
        h.close()


def test_several_parked_boards_come_back_through_one_dialog():
    h = make()
    box, dialog = file_plugin.QMessageBox, file_plugin.QDialog
    try:
        _patch()
        a, b, c = _quit_with_parked(h, dirty=("a", "b"))
        h2 = _launch()
        try:
            h2.service("recovery").resume()
            assert _Dialog.shown == 1 and not _Ask.asked, "one dialog, not one prompt per board"
            state = dict(h2.service("files").open_files_state())
            assert state[a] and state[b] and not state[c]
        finally:
            h2.close()
    finally:
        _unpatch(box, dialog)
        _clear(h)
        h.close()


def test_discard_all_and_decide_later():
    h = make()
    box, dialog = file_plugin.QMessageBox, file_plugin.QDialog
    try:
        _patch(outcome=0)  # Decide Later: closed without choosing
        a, b, c = _quit_with_parked(h, dirty=("a", "b"))
        h2 = _launch()
        try:
            h2.service("recovery").resume()
            assert _snap(h2, a) and _snap(h2, b), "nothing was decided: both stay for the next launch"
            assert not dict(h2.service("files").open_files_state())[a]
        finally:
            h2.close()
        _Dialog.outcome = 2  # Discard All
        h3 = _launch()
        try:
            h3.service("recovery").resume()
            assert _snap(h3, a) is None and _snap(h3, b) is None
        finally:
            h3.close()
    finally:
        _unpatch(box, dialog)
        _clear(h)
        h.close()


def test_an_unticked_board_is_discarded_and_the_ticked_one_restored():
    h = make()
    box, dialog = file_plugin.QMessageBox, file_plugin.QDialog
    try:
        _patch()

        class _Picky(_Dialog):
            def exec(self):
                for box in self.findChildren(file_plugin.QCheckBox):
                    if box.text().startswith("a.pxref"):
                        box.setChecked(False)
                return file_plugin.QDialog.Accepted

        file_plugin.QDialog = _Picky
        a, b, c = _quit_with_parked(h, dirty=("a", "b"))
        h2 = _launch()
        try:
            h2.service("recovery").resume()
            assert _snap(h2, a) is None, "unticked: discarded"
            state = dict(h2.service("files").open_files_state())
            assert state[b] and not state[a]
        finally:
            h2.close()
    finally:
        _unpatch(box, dialog)
        _clear(h)
        h.close()


def test_a_kept_board_that_cannot_be_written_is_asked_about_instead_of_dropped():
    h = make()
    box = file_plugin.QMessageBox
    try:
        a, b = _setup(h, ("a", "b"))
        recovery = h.service("recovery")
        recovery.resume_enabled = True
        _dirty(h, a)
        h.run("file.switch_to", b)
        recovery.keep = lambda *_a, **_k: False  # the disk refuses
        file_plugin.QMessageBox = _Ask
        _Ask.answer, _Ask.asked = _Ask.Cancel, []
        assert not h.app.request_close(), "cancelling keeps the window open"
        assert _Ask.asked and "a.pxref" in _Ask.asked[0]
    finally:
        file_plugin.QMessageBox = box
        _clear(h)
        h.close()


def test_quitting_with_resume_off_still_asks_and_leaves_no_snapshots():
    h = make()
    box = file_plugin.QMessageBox
    try:
        a, b = _setup(h, ("a", "b"))
        recovery = h.service("recovery")
        _dirty(h, a)
        h.run("file.switch_to", b)
        recovery.wait()
        assert _snap(h, a)
        store = recovery.store
        file_plugin.QMessageBox = _Ask
        _Ask.answer, _Ask.asked = _Ask.Discard, []
        assert h.app.request_close() and _Ask.asked
        assert store.read(rs.doc_id(a)) is None
    finally:
        file_plugin.QMessageBox = box
        _clear(h)
        h.close()


# ---- crashes --------------------------------------------------------------------------------------


def _crashed(h, dirty=("a", "b"), names=("a", "b", "c")):
    """Boards edited and parked, current board edited too, and the window 'dies' (nothing is cleaned up)."""
    paths = _setup(h, names)
    recovery = h.service("recovery")
    for name in dirty:
        _dirty(h, paths[names.index(name)])
    h.run("file.switch_to", paths[-1])
    h.add_image("current edit", 10, 10)
    h.session.mark_unsaved()
    assert recovery.autosave()
    recovery.wait()
    return paths


def _new_window():
    h2 = make()
    h2.service("recovery").store.alive = lambda _pid, _token: False  # the writer is gone: a crash
    return h2


def test_a_crash_leaves_every_dirty_board_recoverable_in_one_offer():
    h = make()
    box, dialog = file_plugin.QMessageBox, file_plugin.QDialog
    try:
        _patch()
        a, b, c = _crashed(h)
        h2 = _new_window()
        try:
            assert h2.service("recovery").offer()
            assert _Dialog.shown == 1 and not _Ask.asked
            files = h2.service("files")
            assert set(files.open_files_list()) >= {a, b, c}
            state = dict(files.open_files_state())
            assert state[a] and state[b], "the parked boards come back with their edits"
            assert _current(h2) == c and h2.session.unsaved and _images(h2) == 2, "the board being edited"
        finally:
            h2.close()
    finally:
        _unpatch(box, dialog)
        _clear(h)
        h.close()


def test_snapshots_of_a_window_that_is_still_running_are_not_offered():
    h = make()
    box, dialog = file_plugin.QMessageBox, file_plugin.QDialog
    try:
        _patch()
        _crashed(h)
        h2 = make()  # h is alive in this process: its snapshots belong to it
        try:
            assert not h2.service("recovery").offer()
            assert not _Ask.asked and not _Dialog.shown
        finally:
            h2.close()
    finally:
        _unpatch(box, dialog)
        _clear(h)
        h.close()


def test_a_windows_opened_by_another_never_offers_recovery():
    import os

    h = make()
    box, dialog = file_plugin.QMessageBox, file_plugin.QDialog
    os.environ[ws.CHILD_ENV] = "1"
    try:
        _patch()
        _crashed(h)
        h2 = _new_window()
        try:
            assert not h2.service("recovery").offer()
        finally:
            h2.close()
    finally:
        del os.environ[ws.CHILD_ENV]
        _unpatch(box, dialog)
        _clear(h)
        h.close()


def test_a_corrupt_snapshot_is_set_aside_and_never_touches_the_original():
    h = make()
    box, dialog = file_plugin.QMessageBox, file_plugin.QDialog
    try:
        _patch()
        a, b = _setup(h, ("a", "b"))
        before = Path(a).read_bytes()
        scene_file, meta_file = h.service("recovery").store.files(a)
        scene_file.parent.mkdir(parents=True, exist_ok=True)
        scene_file.write_text("{ this is not a scene", encoding="utf-8")
        meta_file.write_text(json.dumps({"scene_file": a, "saved_at": "2026-01-01T00:00:00", "items": 3,
                                         "pid": 1, "token": "gone"}), encoding="utf-8")
        h2 = _new_window()
        try:
            h2.service("recovery").offer()
            assert _Ask.warnings, "the user is told it could not be restored"
            assert _snap(h2, a) is None, "no longer offered"
            assert list((h2.service("recovery").store.root / rs.CORRUPT).glob("*")), "but kept, not deleted"
            assert Path(a).read_bytes() == before, "the saved file is untouched"
        finally:
            h2.close()
    finally:
        _unpatch(box, dialog)
        _clear(h)
        h.close()


def test_a_snapshot_without_metadata_or_with_a_leftover_temp_file_is_ignored():
    h = make()
    box, dialog = file_plugin.QMessageBox, file_plugin.QDialog
    try:
        _patch()
        recovery = h.service("recovery")
        folder = recovery.store.folder
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "deadbeefdeadbeef.pxref").write_text("{}", encoding="utf-8")  # a write that never finished
        (folder / "cafebabecafebabe.tmp").write_text("{}", encoding="utf-8")
        h2 = _new_window()
        try:
            assert not h2.service("recovery").offer()
            assert not _Ask.asked and not _Dialog.shown
        finally:
            h2.close()
    finally:
        _unpatch(box, dialog)
        _clear(h)
        h.close()


def test_a_file_changed_since_the_snapshot_is_flagged_and_restoring_never_overwrites_it():
    h = make()
    box, dialog = file_plugin.QMessageBox, file_plugin.QDialog
    try:
        _patch()
        a, b = _setup(h, ("a", "b"))
        _dirty(h, a)
        h.run("file.switch_to", b)
        h.service("recovery").wait()
        snapshot = _snap(h, a)
        assert not rs.changed_on_disk(snapshot.meta)
        Path(a).write_bytes(Path(a).read_bytes() + b"\n")  # edited elsewhere meanwhile
        assert rs.changed_on_disk(snapshot.meta)
        on_disk = Path(a).read_bytes()
        h2 = _new_window()
        try:
            h2.service("recovery").offer()
            assert _Ask.asked and "changed on disk" in _Ask.asked[0]
            assert Path(a).read_bytes() == on_disk, "restoring only brings the work back unsaved"
            assert dict(h2.service("files").open_files_state())[a] or _current(h2) == a
        finally:
            h2.close()
    finally:
        _unpatch(box, dialog)
        _clear(h)
        h.close()


def test_the_single_slot_still_recovers_the_untitled_board():
    h = make()
    box, dialog = file_plugin.QMessageBox, file_plugin.QDialog
    try:
        _patch()
        _clear(h)
        h.add_image("draft", 0, 0)
        h.session.mark_unsaved()
        assert h.service("recovery").autosave()
        h2 = make()
        try:
            assert h2.service("recovery").offer()
            assert len(_Ask.asked) == 1 and "didn't close normally" in _Ask.asked[0]
            assert h2.session.scene_path is None and _images(h2) == 1 and h2.session.unsaved
        finally:
            h2.close()
    finally:
        _unpatch(box, dialog)
        _clear(h)
        h.close()
