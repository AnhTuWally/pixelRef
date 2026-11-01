"""Persistent workspace: the saved Files list, its restore modes, ownership and the quit prompt."""

from __future__ import annotations

import json
import os
from pathlib import Path

from harness import make, tmp_dir
from PySide6.QtCore import QSettings

import pixelref.builtins.file as file_plugin
from pixelref.builtins.file import workspace as ws
from pixelref.core.settings import MemorySettings

EXTS = (".pxref",)


def _clear(*harnesses):
    for key in ("session_resume", "session_resume_state", "last_session_had_content",
                "workspace_restore", "workspace_manifest", "file_colors"):
        QSettings().remove(f"plugins/file/{key}")
    for h in harnesses:
        if h is not None and not h.app.closed:
            h.service("recovery").purge_all()
    lock = file_plugin.RecoveryService.recovery_dir() / "workspace.lock"
    lock.unlink(missing_ok=True)


def _board(h, folder: Path, name: str) -> str:
    """Save a one-image board and return its resolved path (it is open in h afterwards)."""
    h.run("file.new")
    h.add_image(name, 0, 0)
    path = folder / f"{name}.pxref"
    assert h.save_to(path)
    return str(path.resolve())


def _manifest() -> dict:
    return json.loads(QSettings().value("plugins/file/workspace_manifest"))


# ---- pure data handling ---------------------------------------------------------------------------


def test_manifest_parse_drops_malformed_entries_and_keeps_order():
    camera = {"center": [1, 2], "zoom": 1.5, "rotation": 0, "flip_x": False, "flip_y": True}
    raw = {"version": 1, "active": "/b/two.pxref", "files": [
        {"path": "/b/one.pxref", "camera": None},
        {"path": "/b/two.pxref", "camera": camera},
        {"path": "/b/ONE.pxref"} if os.name == "nt" else {"path": "/b/one.pxref"},  # duplicate
        {"path": "/b/notes.txt"},                                                   # unsupported
        {"path": 5}, "junk", {"camera": camera},                                    # malformed
        {"path": "/b/three.pxref", "camera": {"center": "x"}},                      # bad camera only
    ]}
    data = ws.parse_manifest(raw, EXTS)
    assert [f["path"] for f in data["files"]] == ["/b/one.pxref", "/b/two.pxref", "/b/three.pxref"]
    assert data["active"] == "/b/two.pxref"
    assert data["files"][1]["camera"]["zoom"] == 1.5
    assert data["files"][2]["camera"] is None, "a bad camera costs only the camera"


def test_manifest_parse_rejects_wrong_shapes():
    for bad in (None, "", "text", 3, [], {}, {"version": 2, "files": []}, {"version": 1, "files": "x"}):
        data = ws.parse_manifest(bad, EXTS)
        assert data == {"active": None, "untitled": False, "files": []}, bad
    data = ws.parse_manifest({"version": 1, "active": "/nowhere.pxref", "files": []}, EXTS)
    assert data["active"] is None, "an active file that isn't in the list is ignored"


def test_untitled_flag_only_without_an_active_file():
    one = [{"path": "/a.pxref"}]
    assert ws.parse_manifest({"version": 1, "untitled": True, "files": one}, EXTS)["untitled"]
    assert not ws.parse_manifest({"version": 1, "untitled": True, "active": "/a.pxref", "files": one}, EXTS)["untitled"]


def test_restore_mode_migrates_from_the_session_resume_checkbox():
    settings = MemorySettings()
    assert ws.resolve_mode(settings) == ws.MODE_EMPTY, "a profile that never chose keeps today's behaviour"
    settings.setValue("session_resume", True)
    assert ws.resolve_mode(settings) == ws.MODE_WORKSPACE, "Resume on becomes Restore Previous Workspace"
    settings.setValue("session_resume", False)
    assert ws.resolve_mode(settings) == ws.MODE_EMPTY
    settings.setValue("workspace_restore", "list")
    assert ws.resolve_mode(settings) == ws.MODE_LIST, "the explicit choice wins"
    settings.setValue("workspace_restore", "bogus")
    assert ws.resolve_mode(settings) == ws.MODE_EMPTY, "an unknown value falls back"


def test_owner_lock_is_exclusive_and_takes_over_a_stale_lock():
    folder = tmp_dir()
    first, second = ws.OwnerLock(folder / "w.lock"), ws.OwnerLock(folder / "w.lock")
    assert first.acquire() and first.acquire(), "acquiring twice is fine"
    assert not second.acquire(), "a live owner keeps it"
    first.release()
    assert second.acquire(), "released locks can be taken"
    second.release()
    (folder / "w.lock").write_text("4194301:deadbeef")  # a process that no longer exists
    assert first.acquire(), "a stale lock is taken over"
    first.release()
    assert not (folder / "w.lock").exists()


# ---- the live workspace ---------------------------------------------------------------------------


def test_workspace_round_trip_restores_order_and_active_without_loading_boards():
    h = make()
    try:
        _clear(h)
        folder = tmp_dir()
        files = h.service("files")
        files.restore_workspace()  # startup: from here the workspace saves itself
        a, b, c = (_board(h, folder, n) for n in ("a", "b", "c"))
        assert files.open_files_list() == [a, b, c]
        h.run("file.switch_to", b)
        h.run("file.reorder_open_files", c, a)
        assert files.open_files_list() == [c, a, b]
        recovery = h.service("recovery")
        recovery.resume_enabled = True
        saved = _manifest()
        assert [f["path"] for f in saved["files"]] == [c, a, b] and saved["active"] == b
        assert set(saved) == {"version", "active", "untitled", "files"}, "paths only: no scene data"
        assert h.app.request_close(), "nothing is unsaved: closing needs no prompt"
        assert h.app.closed

        h2 = make()
        try:
            h2.service("recovery").resume_enabled = True
            assert h2.service("recovery").resume(), "the active board reopens"
            f2 = h2.service("files")
            assert f2.open_files_list() == [c, a, b], "order survives the restart"
            assert str(Path(h2.session.scene_path).resolve()) == b, "the previous active board is back"
            assert sorted(f2.dormant_files()) == sorted([c, a]), "every other board stays unloaded"
        finally:
            h2.close()
    finally:
        _clear(h)
        h.close()


def test_a_closed_window_with_no_files_does_not_repopulate_from_recent_files():
    h = make()
    try:
        _clear(h)
        h.service("recovery").resume_enabled = True
        h.service("files").restore_workspace()
        assert h.app.request_close()
        h2 = make()
        try:
            h2.service("recovery").resume_enabled = True
            assert not h2.service("recovery").resume()
            assert h2.service("files").open_files_list() == []
        finally:
            h2.close()
    finally:
        _clear(h)
        h.close()


def test_list_mode_restores_the_list_but_loads_nothing():
    h = make()
    try:
        _clear(h)
        folder = tmp_dir()
        h.service("files").restore_workspace()
        a, b = _board(h, folder, "a"), _board(h, folder, "b")
        h.service("recovery").restore_mode = ws.MODE_LIST
        h.service("files").save_workspace()
        assert h.app.request_close(), "list mode keeps the normal close prompt; nothing is unsaved here"

        h2 = make()
        try:
            h2.service("recovery").restore_mode = ws.MODE_LIST
            assert not h2.service("recovery").resume(), "no board is opened"
            assert h2.session.scene_path is None
            assert h2.service("files").open_files_list() == [a, b]
            assert sorted(h2.service("files").dormant_files()) == sorted([a, b])
        finally:
            h2.close()
    finally:
        _clear(h)
        h.close()


def test_empty_mode_restores_nothing_even_with_a_saved_manifest():
    h = make()
    try:
        _clear(h)
        h.service("files").restore_workspace()
        _board(h, tmp_dir(), "a")
        assert _manifest()["files"]
        h2 = make()
        try:
            assert h2.service("recovery").restore_mode == ws.MODE_EMPTY
            assert not h2.service("recovery").resume()
            assert h2.service("files").open_files_list() == []
        finally:
            h2.close()
    finally:
        _clear(h)
        h.close()


def test_corrupt_manifest_is_ignored():
    h = make()
    try:
        _clear(h)
        for junk in ("{not json", '"a string"', "[1, 2]", '{"version": 1, "files": [1, null]}'):
            QSettings().setValue("plugins/file/workspace_manifest", junk)
            h.service("recovery").resume_enabled = True
            assert h.service("files").restore_workspace() == 0
            assert not h.service("recovery").resume()
    finally:
        _clear(h)
        h.close()


def test_a_second_window_does_not_own_or_overwrite_the_workspace():
    h = make()
    try:
        _clear(h)
        folder = tmp_dir()
        h.service("files").restore_workspace()
        a = _board(h, folder, "a")
        h.service("recovery").resume_enabled = True
        h2 = make()
        try:
            files2 = h2.service("files")
            assert not files2.workspace_owner(), "the first window owns the workspace"
            assert files2.restore_workspace() == 0 and not h2.service("recovery").resume()
            _board(h2, folder, "other")
            assert [f["path"] for f in _manifest()["files"]] == [a], "the second window never writes it"
        finally:
            h2.close()
    finally:
        _clear(h)
        h.close()


def test_windows_opened_by_open_in_new_window_never_own_the_workspace():
    os.environ[ws.CHILD_ENV] = "1"
    h = make()
    try:
        _clear(h)
        files = h.service("files")
        assert not files.workspace_owner()
        files.restore_workspace()
        _board(h, tmp_dir(), "a")
        assert QSettings().value("plugins/file/workspace_manifest") is None
    finally:
        del os.environ[ws.CHILD_ENV]
        _clear(h)
        h.close()


def test_nothing_is_saved_before_the_startup_restore_has_run():
    """A startup event must not overwrite the saved list before it has been read."""
    h = make()
    try:
        _clear(h)
        QSettings().setValue("plugins/file/workspace_manifest", json.dumps(
            {"version": 1, "active": None, "untitled": False, "files": [{"path": "/keep/me.pxref"}]}))
        h.run("file.new")
        assert not h.service("files").save_workspace()
        assert _manifest()["files"][0]["path"] == "/keep/me.pxref"
    finally:
        _clear(h)
        h.close()


# ---- the quit prompt ------------------------------------------------------------------------------


class _Box:
    """Stands in for QMessageBox: answers the Save/Discard/Cancel question with ``answer``."""

    Save, Discard, Cancel = (file_plugin.QMessageBox.Save, file_plugin.QMessageBox.Discard,
                             file_plugin.QMessageBox.Cancel)
    answer = Cancel
    asked: list[str] = []
    errors: list[str] = []

    @classmethod
    def critical(cls, _parent, _title, text, *_a, **_k):
        cls.errors.append(text)

    @classmethod
    def question(cls, _parent, _title, text, *_a, **_k):
        cls.asked.append(text)
        return cls.answer


def test_quitting_in_resume_mode_keeps_dirty_boards_in_the_files_list_without_asking():
    """Boards parked with unsaved edits are kept on disk for the next launch (see test_recovery.py):
    quitting no longer interrupts, and nothing is dropped silently."""
    h = make()
    original = file_plugin.QMessageBox
    try:
        _clear(h)
        folder = tmp_dir()
        h.service("files").restore_workspace()
        h.service("recovery").resume_enabled = True
        a, b = _board(h, folder, "a"), _board(h, folder, "b")
        h.run("file.switch_to", a)
        h.add_image("edit", 50, 50)
        h.session.mark_unsaved()
        h.run("file.switch_to", b)  # a is parked with its unsaved edit
        assert dict(h.service("files").open_files_state())[a], "a is parked dirty"

        file_plugin.QMessageBox = _Box
        _Box.asked.clear()
        assert h.app.request_close(), "nothing to ask: the parked board is kept for the next launch"
        assert h.app.closed and not _Box.asked
        store = file_plugin.rs.SnapshotStore(file_plugin.RecoveryService.recovery_dir())
        snapshot = store.read(file_plugin.rs.doc_id(a))
        assert snapshot is not None and snapshot.clean, "its edit is on disk, marked as left on purpose"
    finally:
        file_plugin.QMessageBox = original
        _clear(h)
        h.close()


def test_quitting_in_resume_mode_does_not_ask_about_the_current_board():
    h = make()
    original = file_plugin.QMessageBox
    try:
        _clear(h)
        h.service("files").restore_workspace()
        h.service("recovery").resume_enabled = True
        _board(h, tmp_dir(), "a")
        h.add_image("edit", 50, 50)
        h.session.mark_unsaved()
        file_plugin.QMessageBox = _Box
        _Box.asked.clear()
        assert h.app.request_close(), "the current board is kept by Session Resume, as before"
        assert not _Box.asked
    finally:
        file_plugin.QMessageBox = original
        _clear(h)
        h.close()


def test_confirm_close_all_can_be_limited_to_a_subset_of_files():
    h = make()
    original = file_plugin.QMessageBox
    try:
        _clear(h)
        folder = tmp_dir()
        h.service("files").restore_workspace()
        a, b, c = (_board(h, folder, n) for n in ("a", "b", "c"))
        for name in (a, b):
            h.run("file.switch_to", name)
            h.add_image("edit", 50, 50)
            h.session.mark_unsaved()
        h.run("file.switch_to", c)  # a and b parked dirty
        file_plugin.QMessageBox = _Box
        _Box.asked.clear()
        _Box.answer = _Box.Discard
        assert h.service("files").confirm_close_all(include_current=False, entries=[
            e for e in h.service("files").open_entries() if e.path == a])
        assert len(_Box.asked) == 1 and "a.pxref" in _Box.asked[0] and "b.pxref" not in _Box.asked[0]
    finally:
        file_plugin.QMessageBox = original
        _clear(h)
        h.close()


# ---- closing files --------------------------------------------------------------------------------


def _setup(h, names=("a", "b", "c")):
    """Open the workspace for saving and create boards; the last one is current."""
    _clear(h)
    folder = tmp_dir()
    h.service("files").restore_workspace()
    return [_board(h, folder, n) for n in names]


def _current(h):
    return str(Path(h.session.scene_path).resolve()) if h.session.scene_path else None


def _dirty(h, path):
    h.run("file.switch_to", path)
    h.add_image("edit", 50, 50)
    h.session.mark_unsaved()


def _answering(answer):
    file_plugin.QMessageBox = _Box
    _Box.asked.clear()
    _Box.errors.clear()
    _Box.answer = answer


def test_closing_a_clean_inactive_file_leaves_the_canvas_alone_and_keeps_it_in_recent_files():
    h = make()
    try:
        a, b, c = _setup(h)
        files = h.service("files")
        nodes = len(h.session.scene.nodes)
        h.run("file.close", a)
        assert files.open_files_list() == [b, c]
        assert _current(h) == c and len(h.session.scene.nodes) == nodes, "the active canvas is untouched"
        assert a in files.recent_files(), "Recent Files keeps closed files"
        assert [f["path"] for f in _manifest()["files"]] == [b, c], "the saved list follows"
    finally:
        _clear(h)
        h.close()


def test_closing_the_active_file_selects_the_next_then_the_previous_then_the_empty_board():
    h = make()
    try:
        a, b, c = _setup(h)
        files = h.service("files")
        h.run("file.switch_to", b)
        h.run("file.close", b)
        assert _current(h) == c, "the next file takes over"
        h.run("file.close", c)
        assert _current(h) == a, "with no next file, the previous one"
        h.run("file.close", a)
        assert files.open_files_list() == [] and h.session.scene_path is None, "the empty board"
        assert not h.session.unsaved
        assert _manifest()["files"] == []
    finally:
        _clear(h)
        h.close()


def test_closing_a_dormant_file_needs_no_load_and_no_prompt():
    h = make()
    original = file_plugin.QMessageBox
    try:
        a, b = _setup(h, ("a", "b"))
        h.service("recovery").resume_enabled = True
        h.service("files").save_workspace()
        assert h.app.request_close()
        h2 = make()
        try:
            h2.service("recovery").resume_enabled = True
            h2.service("recovery").resume()
            files2 = h2.service("files")
            assert files2.dormant_files() == [a]
            _answering(_Box.Cancel)
            h2.run("file.close", a)
            assert files2.open_files_list() == [b] and not _Box.asked
        finally:
            h2.close()
    finally:
        file_plugin.QMessageBox = original
        _clear(h)
        h.close()


def test_closing_a_dirty_inactive_file_asks_and_cancel_keeps_everything():
    h = make()
    original = file_plugin.QMessageBox
    try:
        a, b, c = _setup(h)
        files = h.service("files")
        _dirty(h, a)
        h.run("file.switch_to", c)
        _answering(_Box.Cancel)
        h.run("file.close", a)
        assert files.open_files_list() == [a, b, c] and _Box.asked, "cancelled: nothing closed"
        assert dict(files.open_files_state())[a], "and the parked edit is still there"
        _answering(_Box.Discard)
        h.run("file.close", a)
        assert files.open_files_list() == [b, c]
    finally:
        file_plugin.QMessageBox = original
        _clear(h)
        h.close()


def test_closing_with_save_writes_the_file_and_a_failed_save_keeps_it_open():
    h = make()
    original = file_plugin.QMessageBox
    real_write = h.session.format.write_text
    try:
        a, b = _setup(h, ("a", "b"))
        files = h.service("files")
        _dirty(h, a)
        h.run("file.switch_to", b)

        def boom(*_a, **_k):
            raise OSError("disk full")

        h.session.format.write_text = boom
        _answering(_Box.Save)
        before = Path(a).stat().st_size
        h.run("file.close", a)
        assert files.open_files_list() == [a, b], "a failed save never closes the file"
        assert _Box.errors and Path(a).stat().st_size == before and dict(files.open_files_state())[a]

        h.session.format.write_text = real_write
        h.run("file.close", a)
        assert files.open_files_list() == [b]
        assert Path(a).stat().st_size > before, "the unsaved edit was written to disk"
    finally:
        h.session.format.write_text = real_write
        file_plugin.QMessageBox = original
        _clear(h)
        h.close()


def test_closing_a_dirty_current_file_asks_first_and_cancel_keeps_it():
    h = make()
    original = file_plugin.QMessageBox
    try:
        a, b = _setup(h, ("a", "b"))
        h.add_image("edit", 5, 5)
        h.session.mark_unsaved()
        _answering(_Box.Cancel)
        h.run("file.close")
        assert _current(h) == b and h.session.unsaved and h.service("files").open_files_list() == [a, b]
        _answering(_Box.Discard)
        h.run("file.close")
        assert _current(h) == a and not h.session.unsaved, "discarded: the neighbour is current and clean"
        assert h.service("files").open_files_list() == [a]
    finally:
        file_plugin.QMessageBox = original
        _clear(h)
        h.close()


def test_close_others_keeps_the_target_and_cancel_closes_nothing():
    h = make()
    original = file_plugin.QMessageBox
    try:
        a, b, c = _setup(h)
        files = h.service("files")
        _dirty(h, a)
        h.run("file.switch_to", b)
        _answering(_Box.Cancel)
        h.run("file.close_others", b)
        assert files.open_files_list() == [a, b, c], "cancelled: every file is preserved"
        _answering(_Box.Discard)
        h.run("file.close_others", c)  # c is not current: it becomes current
        assert files.open_files_list() == [c] and _current(h) == c
        assert not h.session.unsaved
    finally:
        file_plugin.QMessageBox = original
        _clear(h)
        h.close()


class _Dialog(file_plugin.QDialog):
    outcome = file_plugin.QDialog.Rejected

    def exec(self):
        return type(self).outcome


def test_close_all_uses_one_dialog_for_many_dirty_files():
    h = make()
    original, original_dialog = file_plugin.QMessageBox, file_plugin.QDialog
    try:
        a, b, c = _setup(h)
        files = h.service("files")
        _dirty(h, a)
        _dirty(h, b)
        h.run("file.switch_to", c)
        file_plugin.QDialog = _Dialog
        _Dialog.outcome = original_dialog.Rejected
        _answering(_Box.Cancel)
        h.run("file.close_all")
        assert files.open_files_list() == [a, b, c] and not _Box.asked, "one dialog, cancelled: nothing closed"
        _Dialog.outcome = original_dialog.Accepted  # Save the ticked files
        sizes = [Path(a).stat().st_size, Path(b).stat().st_size]
        h.run("file.close_all")
        assert files.open_files_list() == [] and h.session.scene_path is None
        assert Path(a).stat().st_size > sizes[0] and Path(b).stat().st_size > sizes[1]
        assert not _Box.asked, "no per-file dialogs"
    finally:
        file_plugin.QDialog, file_plugin.QMessageBox = original_dialog, original
        _clear(h)
        h.close()


def test_closing_an_untitled_board_asks_then_starts_a_new_one():
    h = make()
    original = file_plugin.QMessageBox
    try:
        _clear(h)
        h.service("files").restore_workspace()
        h.add_image("draft", 0, 0)
        h.session.mark_unsaved()
        _answering(_Box.Cancel)
        h.run("file.close")
        assert h.session.unsaved, "cancelled"
        _answering(_Box.Discard)
        h.run("file.close")
        assert not h.session.unsaved and not h.session.scene.nodes
    finally:
        file_plugin.QMessageBox = original
        _clear(h)
        h.close()


def test_files_panel_row_has_a_keyboard_reachable_close_button_that_only_closes():
    from harness import APP
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    h = make()
    try:
        a, b = _setup(h, ("a", "b"))
        panel = h.service("files_panel").panels[0]
        row = next(r for r in panel.rows if r.path == a)
        button = row.close_button
        assert button.toolTip() == "Close file" and button.focusPolicy() & Qt.TabFocus
        selected = []
        row.clicked.connect(lambda *_: selected.append(1))
        QTest.mouseClick(button, Qt.LeftButton)
        for _ in range(3):
            APP.processEvents()
        assert h.service("files").open_files_list() == [b], "the click closed the file"
        assert not selected and _current(h) == b, "and did not select or switch"
        assert [r.path for r in panel.rows] == [b], "the panel follows"
    finally:
        _clear(h)
        h.close()


def test_files_panel_context_menu_requests_close_others_and_close_all():
    from harness import APP

    h = make()
    try:
        a, b, c = _setup(h)
        panel = h.service("files_panel").panels[0]
        next(r for r in panel.rows if r.path == b).close_others_requested.emit(b)
        APP.processEvents()
        assert h.service("files").open_files_list() == [b] and _current(h) == b
        panel.rows[0].close_all_requested.emit()
        APP.processEvents()
        assert h.service("files").open_files_list() == [] and not panel.rows
    finally:
        _clear(h)
        h.close()


# ---- colour labels --------------------------------------------------------------------------------


def _colors() -> dict:
    value = QSettings().value("plugins/file/file_colors")
    return json.loads(value) if value else {}


def test_colour_map_parsing_and_pruning():
    assert ws.parse_colors({"/a.pxref": "blue", "/b.pxref": "plaid", "": "pink", "/c": 3}) == {"/a.pxref": "blue"}
    for bad in (None, "x", [], 5):
        assert ws.parse_colors(bad) == {}
    big = {ws.path_key(f"/f{i}.pxref"): "green" for i in range(ws.MAX_COLORS + 20)}
    pruned = ws.prune_colors(big, keep=["/f0.pxref", "/f1.pxref"])
    assert len(pruned) == ws.MAX_COLORS, "pruned back to the cap"
    assert ws.path_key("/f0.pxref") in pruned and ws.path_key("/f1.pxref") in pruned, "open and recent files keep their labels"
    assert ws.path_key("/f2.pxref") not in pruned, "the oldest unused labels go first"
    small = {"/a.pxref": "blue"}
    assert ws.prune_colors(small, keep=[]) == small, "nothing is forgotten while under the cap"


def test_setting_a_colour_labels_only_that_row_and_rebuilds_nothing():
    h = make()
    try:
        a, b, c = _setup(h)
        _clear_colors()
        panel = h.service("files_panel").panels[0]
        rows_before = list(panel.rows)
        serialized = []
        real = h.session.serialize
        h.session.serialize = lambda *x, **k: serialized.append(1) or real(*x, **k)
        try:
            assert h.run("file.set_color", b, "green") is not False
        finally:
            h.session.serialize = real
        assert not serialized, "no scene is serialized to label a file"
        assert panel.rows == rows_before and all(x is y for x, y in zip(panel.rows, rows_before)), \
            "the rows (and their thumbnails) were not rebuilt"
        by_path = {r.path: r for r in panel.rows}
        assert by_path[b].color_name == "green" and by_path[a].color_name == "" == by_path[c].color_name
        assert _colors() == {ws.path_key(b): "green"}
        h.run("file.clear_color", b)
        assert by_path[b].color_name == "" and _colors() == {}
    finally:
        _clear(h)
        _clear_colors()
        h.close()


def _clear_colors():
    QSettings().remove("plugins/file/file_colors")


def test_colours_survive_closing_reopening_and_restart_and_are_not_in_the_manifest():
    h = make()
    try:
        a, b = _setup(h, ("a", "b"))
        _clear_colors()
        h.run("file.set_color", a, "purple")
        assert "purple" not in json.dumps(_manifest()), "membership and colour are stored separately"
        h.run("file.close", a)
        assert a not in h.service("files").open_files_list()
        assert _colors()[ws.path_key(a)] == "purple", "closing keeps the label"
        h.run("file.open_path", a)
        panel = h.service("files_panel").panels[0]
        assert next(r for r in panel.rows if r.path == a).color_name == "purple", "reopened with its colour"
        h2 = make()
        try:
            assert h2.service("files").file_color(a) == "purple", "and after a restart"
        finally:
            h2.close()
    finally:
        _clear(h)
        _clear_colors()
        h.close()


def test_colour_is_independent_of_the_active_and_unsaved_indicators():
    h = make()
    try:
        a, b = _setup(h, ("a", "b"))
        _clear_colors()
        h.run("file.set_color", b, "yellow")  # b is the active file
        h.add_image("edit", 5, 5)
        h.session.mark_unsaved()
        panel = h.service("files_panel").panels[0]
        panel.sync_unsaved()
        row = next(r for r in panel.rows if r.path == b)
        assert row.active and row.unsaved and row.color_name == "yellow", "three separate states"
        assert "#e8863c" in row.styleSheet().lower() or "232, 134, 60" in row.styleSheet(), "unsaved border intact"
        assert ws.PALETTE["yellow"] in row.color_strip.styleSheet()
        other = next(r for r in panel.rows if r.path == a)
        assert other.color_name == "" and not other.unsaved
    finally:
        _clear(h)
        _clear_colors()
        h.close()


def test_colour_does_not_touch_recent_files_order_and_rejects_unknown_colours():
    h = make()
    try:
        a, b, c = _setup(h)
        _clear_colors()
        files = h.service("files")
        recent = files.recent_files()
        h.run("file.set_color", a, "pink")
        assert files.recent_files() == recent
        h.run("file.set_color", a, "plaid")
        assert files.file_color(a) == "pink", "an unknown colour changes nothing"
        h.app.trigger("file.color_blue")  # the command-search action colours the current file
        assert files.file_color(c) == "blue"
    finally:
        _clear(h)
        _clear_colors()
        h.close()


def test_row_context_menu_signal_sets_and_removes_a_colour():
    from harness import APP

    h = make()
    try:
        a, b = _setup(h, ("a", "b"))
        _clear_colors()
        row = h.service("files_panel").panels[0].rows[0]
        row.color_requested.emit(row.path, "orange")
        APP.processEvents()
        assert row.color_name == "orange"
        row.color_requested.emit(row.path, "")
        assert row.color_name == ""
    finally:
        _clear(h)
        _clear_colors()
        h.close()


# ---- parked boards and memory ---------------------------------------------------------------------


def _entry(h, path):
    return next(e for e in h.service("files").open_entries() if e.path == path)


class _Counting:
    """Counts full scene serializations while active."""

    def __init__(self, h):
        self.h, self.count = h, 0

    def __enter__(self):
        self.real = self.h.session.serialize

        def counted(*a, **k):
            self.count += 1
            return self.real(*a, **k)

        self.h.session.serialize = counted
        return self

    def __exit__(self, *_):
        self.h.session.serialize = self.real


def test_switching_away_from_a_clean_board_serializes_nothing_and_retains_no_text():
    h = make()
    try:
        a, b, c = _setup(h)
        with _Counting(h) as counter:
            h.run("file.switch_to", a)
            h.run("file.switch_to", b)
            h.run("file.switch_to", c)
        assert counter.count == 0, "clean boards are not serialized when parked"
        assert all(_entry(h, p).text is None for p in (a, b, c)), "and nothing is retained for them"
        assert _current(h) == c
        h.run("file.switch_to", a)
        assert len(h.nodes_of("image")) == 1 and _current(h) == a, "a parked clean board loads from disk"
        assert not h.session.unsaved
    finally:
        _clear(h)
        h.close()


def test_a_dirty_board_keeps_its_text_and_its_edit_across_switches():
    h = make()
    try:
        a, b = _setup(h, ("a", "b"))
        _dirty(h, a)
        with _Counting(h) as counter:
            h.run("file.switch_to", b)
        assert counter.count == 1 and _entry(h, a).text is not None, "unsaved work is kept"
        h.run("file.switch_to", a)
        assert len(h.nodes_of("image")) == 2 and h.session.unsaved, "the edit is back, still unsaved"
        assert _entry(h, a).text is None, "the current board is held by the session alone"
    finally:
        _clear(h)
        h.close()


def test_a_clean_board_whose_file_is_gone_keeps_its_text():
    h = make()
    try:
        a, b = _setup(h, ("a", "b"))
        h.run("file.switch_to", a)
        Path(a).unlink()
        h.run("file.switch_to", b)
        assert _entry(h, a).text is not None, "no file on disk: the captured copy is all that is left"
        h.run("file.switch_to", a)
        assert _current(h) == a and len(h.nodes_of("image")) == 1, "so it can still be reached"
    finally:
        _clear(h)
        h.close()


def test_switching_between_open_files_does_not_reorder_recent_files():
    h = make()
    try:
        a, b, c = _setup(h)
        files = h.service("files")
        recent = files.recent_files()
        for target in (a, b, a, c, b):
            h.run("file.switch_to", target)
        assert files.recent_files() == recent
    finally:
        _clear(h)
        h.close()


def test_evict_clean_can_be_switched_off():
    h = make()
    original = file_plugin.EVICT_CLEAN
    try:
        file_plugin.EVICT_CLEAN = False
        a, b = _setup(h, ("a", "b"))
        h.run("file.switch_to", a)
        h.run("file.switch_to", b)
        assert _entry(h, a).text is not None, "the old keep-everything behaviour"
    finally:
        file_plugin.EVICT_CLEAN = original
        _clear(h)
        h.close()


# ---- missing files and the start screen -----------------------------------------------------------


def _restart_with_list(h, names=("a", "b", "c")):
    """Make boards, close, and come back in list mode: every board dormant. Returns (h2, paths)."""
    paths = _setup(h, names)
    h.service("recovery").restore_mode = ws.MODE_LIST
    h.service("files").save_workspace()
    assert h.app.request_close()
    h2 = make()
    h2.service("recovery").restore_mode = ws.MODE_LIST
    h2.service("recovery").resume()
    return h2, paths


def test_a_missing_file_is_flagged_in_the_background_and_stays_in_the_list_and_the_manifest():
    h = make()
    try:
        h2, (a, b, c) = _restart_with_list(h)
        try:
            files = h2.service("files")
            Path(b).unlink()
            files.check_availability(force=True)
            files.wait_availability()
            assert files.file_missing(b) and not files.file_missing(a)
            panel = h2.service("files_panel").panels[0]
            rows = {r.path: r for r in panel.rows}
            assert rows[b].missing and not rows[a].missing
            assert rows[b].warn.isVisibleTo(rows[b]) and not rows[a].warn.isVisibleTo(rows[a])
            assert "not found" in rows[b].toolTip()
            h2.run("file.close", a)  # any workspace write
            assert b in [f["path"] for f in _manifest()["files"]], "a missing file is never erased from the saved list"
        finally:
            h2.close()
    finally:
        _clear(h)
        h.close()


def test_availability_checks_are_throttled():
    h = make()
    try:
        h2, _paths = _restart_with_list(h)
        try:
            files = h2.service("files")
            files.check_availability(force=True)
            files.wait_availability()
            assert not files.check_availability(), "just checked: nothing is looked at again"
            assert files.check_availability(force=True)
            files.wait_availability()
        finally:
            h2.close()
    finally:
        _clear(h)
        h.close()


def test_selecting_a_missing_file_says_so_without_a_dialog_and_keeps_the_current_board():
    h = make()
    original = file_plugin.QMessageBox
    try:
        h2, (a, b, c) = _restart_with_list(h)
        try:
            files = h2.service("files")
            h2.run("file.switch_to", a)
            Path(b).unlink()
            _answering(_Box.Cancel)
            assert h2.run("file.switch_to", b) is False
            assert not _Box.errors and not _Box.asked, "no dialog"
            assert _current(h2) == a and b in files.open_files_list()
            assert files.file_missing(b), "and the row learns it at once"
        finally:
            h2.close()
    finally:
        file_plugin.QMessageBox = original
        _clear(h)
        h.close()


def test_a_missing_file_does_not_stop_the_others_restoring():
    h = make()
    try:
        paths = _setup(h, ("a", "b", "c"))
        h.service("recovery").resume_enabled = True
        h.service("files").save_workspace()
        assert h.app.request_close()
        Path(paths[2]).unlink()  # the active one is the last board, c
        h2 = make()
        try:
            h2.service("recovery").resume_enabled = True
            h2.service("recovery").resume()  # the active board is gone: nothing loads, nothing raises
            assert h2.service("files").open_files_list() == paths
            h2.run("file.switch_to", paths[0])
            assert _current(h2) == paths[0]
        finally:
            h2.close()
    finally:
        _clear(h)
        h.close()


def test_locate_file_updates_the_entry_in_place_and_keeps_its_colour():
    h = make()
    try:
        h2, (a, b, c) = _restart_with_list(h)
        try:
            files = h2.service("files")
            h2.run("file.set_color", b, "green")
            moved = tmp_dir() / "moved_b.pxref"
            Path(b).rename(moved)
            assert h2.run("file.relocate", b, str(moved))
            new = str(moved.resolve())
            assert files.open_files_list() == [a, new, c], "same place in the list, no duplicate"
            assert files.file_color(new) == "green" and files.file_color(b) is None
            assert not files.file_missing(new)
            h2.run("file.switch_to", new)
            assert _current(h2) == new
        finally:
            h2.close()
    finally:
        _clear(h)
        h.close()


def test_locate_file_onto_an_already_listed_path_removes_the_stale_entry():
    h = make()
    try:
        h2, (a, b, c) = _restart_with_list(h)
        try:
            files = h2.service("files")
            assert h2.run("file.relocate", b, c)
            assert files.open_files_list() == [a, c], "no second entry for the same file"
        finally:
            h2.close()
    finally:
        _clear(h)
        h.close()


def test_locate_file_refuses_non_scenes_and_the_open_board():
    h = make()
    try:
        a, b = _setup(h, ("a", "b"))
        junk = tmp_dir() / "x.txt"
        junk.write_text("hi")
        assert h.run("file.relocate", a, str(junk)) is False
        assert h.run("file.relocate", b, a) is False, "the open board is not relocated"
        assert h.service("files").open_files_list() == [a, b]
    finally:
        _clear(h)
        h.close()


def test_start_screen_marks_files_already_open_and_switches_instead_of_reloading():
    from harness import APP
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    h = make()
    try:
        a, b = _setup(h, ("a", "b"))
        h.run("file.close_all")  # an empty board: a and b are only in Recent Files
        h.run("file.open_path", b)  # b is listed again
        h.run("file.new")  # an empty board while b is still in the list
        h.refresh()
        APP.processEvents()
        panel = h.service("start_panel").panels["Main"]
        cards = {card.path: card for card in panel.cards}
        assert b in h.service("files").open_files_list()
        assert cards[b].is_open and "Already open" in cards[b].date.text()
        assert not cards[a].is_open, "a is closed: just a recent file"
        QTest.mouseClick(cards[b], Qt.LeftButton)
        APP.processEvents()
        assert _current(h) == b and h.service("files").open_files_list().count(b) == 1
    finally:
        _clear(h)
        h.close()
