"""Files: one-file scenes with embedded images, image storage, recovery, export,
and the canvas staying identical to a full rebuild after every kind of edit."""

from __future__ import annotations

from pathlib import Path

from harness import QTest, make, make_png, tmp_dir
from PySide6.QtCore import QMimeData, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from pixelref.plugin_api import read_thumbnail


def _bitmap_mime(w: int, h: int, color: str) -> QMimeData:
    image = QImage(w, h, QImage.Format_ARGB32)
    image.fill(QColor(color))
    mime = QMimeData()
    mime.setImageData(image)
    return mime


def _file_mime(path: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def test_scene_packages_into_one_file():
    """Pasted, dropped and inserted images are embedded: one .pxref holds everything."""
    h = make()
    try:
        h.drop(QPointF(0, 0), _bitmap_mime(64, 32, "#336699"))  # clipboard bitmap
        png = make_png("dropped_file.png", 40, 20, "#22aa44")
        h.drop(QPointF(200, 0), _file_mime(png))  # file from Explorer
        images = h.nodes_of("image")
        assert len(images) == 2
        for name in images:
            assert h.attr(name, "asset") and not h.attr(name, "path"), h.attrs(name)
        h.select([images[0]])
        rows = h.inspector.rows["image"]
        storage = next(r for r in rows if hasattr(r, "path_label"))
        assert "Embedded in scene" in storage.path_label.text(), storage.path_label.text()
        project = tmp_dir()
        scene_path = project / "board.pxref"
        assert h.save_to(scene_path)
        assert sorted(p.name for p in project.iterdir()) == ["board.pxref"], "the scene is a single file"
        data = h.saved_json(scene_path)
        assert data["schema"] == 1 and "types" in data and "plugins" in data
        assert len(data["assets"]) == 2 and all(a["encoding"] == "base64" for a in data["assets"].values())
        assert data["assets"][h.attr(images[1], "asset")]["name"] in ("dropped_file.png", "pasted.png")
        # Move the file somewhere else and delete the original image: it still opens complete.
        moved = tmp_dir() / "copy.pxref"
        moved.write_bytes(scene_path.read_bytes())
        png.unlink()
        h2 = make()
        try:
            assert h2.open(moved)
            sizes = sorted((h2.item(n).pixmap().width(), h2.item(n).pixmap().height()) for n in h2.nodes_of("image"))
            assert sizes == [(40, 20), (64, 32)], sizes
            pixel = h2.app.canvas.render_items([h2.item(images[0])]).pixelColor(5, 5).name()
            assert pixel in ("#336699", "#22aa44")
        finally:
            h2.close()
    finally:
        h.close()


def test_undo_snapshots_skip_image_data():
    h = make()
    try:
        h.drop(QPointF(0, 0), _bitmap_mime(800, 800, "#884422"))
        name = h.sel()[0]
        assert len(h.session.dumps()) < 3000, "undo snapshots must not contain base64 image data"
        h.trigger("transform.flip_h")
        h.undo()
        h.trigger("clipboard.delete")
        h.undo()  # bring the deleted image back: its pixels must still be there
        assert h.item(name).pixmap().width() == 800
    finally:
        h.close()


def test_image_storage_embed_link_extract():
    from pixelref.builtins.image_storage import ui as storage_ui

    h = make()
    try:
        h.add_image("Linked", 0, 0, w=30, h=30)  # created by path, like older scenes
        h.select(["Linked"])
        rows = next(r for r in h.inspector.rows["image"] if hasattr(r, "embed_btn"))
        assert "Linked file" in rows.path_label.text() and rows.embed_btn.isVisible()
        QTest.mouseClick(rows.embed_btn, Qt.LeftButton)
        assert h.attr("Linked", "asset") and h.attr("Linked", "path") == ""
        assert rows.extract_btn.isVisible() and not rows.embed_btn.isVisible()
        # Save as file & link (dialog patched).
        out = tmp_dir() / "extracted.png"
        original = storage_ui.QFileDialog

        class _Dialog:
            @staticmethod
            def getSaveFileName(*_a, **_k):  # noqa: N802
                return str(out), ""

        try:
            storage_ui.QFileDialog = _Dialog
            h.trigger("image_storage.extract_dialog")
        finally:
            storage_ui.QFileDialog = original
        assert out.exists() and QImage(str(out)).width() == 30
        assert h.attr("Linked", "asset") == "" and Path(h.attr("Linked", "path")).name == "extracted.png"
        # Turning "embed new images" off links dropped files instead.
        h.trigger("image_storage.embed_new")
        storage = h.service("image_storage")
        assert not storage.embed_new and not h.app.actions.get("image_storage.embed_new").isChecked()
        png = make_png("linked_drop.png", 10, 10)
        h.drop(QPointF(100, 100), _file_mime(png))
        dropped = h.sel()[0]
        assert h.attr(dropped, "path") and not h.attr(dropped, "asset")
        h.trigger("image_storage.embed_all")
        assert all(h.attr(n, "asset") for n in h.nodes_of("image"))
    finally:
        h.trigger("image_storage.embed_new")
        h.close()


def test_images_over_qt_default_allocation_limit_load():
    h = make()
    try:
        path = tmp_dir() / "huge.png"
        image = QImage(8200, 8200, QImage.Format_RGB32)  # ~269 MB decoded: over Qt's 256 MB default
        image.fill(QColor("#336699"))
        assert image.save(str(path))
        del image
        h.create("image", "Huge", path=path.as_posix(), translate=[0.0, 0.0])
        item = h.item("Huge")
        assert item.pixmap().width() == 8200, "big images load instead of showing 'Missing'"
    finally:
        h.close()


def test_embedded_images_copy_paste_between_windows():
    h = make()
    h2 = make()
    try:
        h.drop(QPointF(0, 0), _bitmap_mime(20, 10, "#aa2266"))
        assert h.run("clipboard.copy")
        h2.app.menu_scene_pos = QPointF(0, 0)
        h2.run("clipboard.paste")
        pasted = h2.sel()[0]
        asset = h2.attr(pasted, "asset")
        assert asset and h2.scene.get_asset(asset) is not None
        assert h2.item(pasted).pixmap().width() == 20
    finally:
        h2.close()
        h.close()


def test_export_image_renders_selection():
    h = make()
    try:
        h.add_image("A", 0, 0, w=100, h=50)
        h.add_image("B", 500, 0, w=100, h=50)
        h.select(["A"])
        image = h.app.canvas.render_items(h.app.canvas.outline_items())
        assert image is not None and (image.width(), image.height()) == (100, 50)
        assert image.pixelColor(50, 25).name() == "#c04040"
        out = tmp_dir() / "export.png"
        size = h.run("export.render", str(out))
        assert out.exists() and list(size) == [100, 50] and QImage(str(out)).pixelColor(50, 25).name() == "#c04040"
    finally:
        h.close()


def test_export_copy_image_puts_selection_on_clipboard():
    h = make()
    try:
        h.add_image("A", 0, 0, w=100, h=50)
        h.add_image("B", 500, 0, w=100, h=50)
        h.select(["A"])
        size = h.run("export.copy_image")
        clipboard_image = QApplication.clipboard().image()
        assert list(size) == [100, 50]
        assert (clipboard_image.width(), clipboard_image.height()) == (100, 50)
        assert clipboard_image.pixelColor(50, 25).name() == "#c04040"
    finally:
        h.close()


# ---- Recovery ------------------------------------------------------------------------------------


def _clear_recovery(h):
    for path in h.service("recovery").recovery_files():
        if path.exists():
            path.unlink()


def test_autosave_and_crash_recovery():
    h = make()
    try:
        recovery = h.service("recovery")
        assert "qttest" in str(recovery.recovery_dir()).lower(), "tests must not touch the real recovery folder"
        _clear_recovery(h)
        assert not recovery.autosave(), "nothing to autosave on an untouched board"
        h.drop(QPointF(0, 0), _bitmap_mime(40, 20, "#11aa55"))
        h.app.canvas.begin_interaction()
        assert not recovery.autosave(), "never autosave in the middle of a drag"
        h.app.canvas.end_interaction("x")
        assert recovery.autosave() and recovery.pending() is not None
        assert not recovery.autosave(), "unchanged since the last autosave"
        # Simulate a crash: a new window finds the recovery copy and restores it.
        h2 = make()
        try:
            recovery2 = h2.service("recovery")
            info = recovery2.pending()
            assert info is not None and info["items"] == 1
            assert recovery2.restore()
            h2.refresh()
            names = h2.nodes_of("image")
            assert len(names) == 1 and h2.item(names[0]).pixmap().width() == 40
            assert h2.session.unsaved, "restored work still needs saving"
            assert h2.save_to(tmp_dir() / "rescued.pxref")
            assert recovery2.pending() is None, "saving removes the recovery copy"
        finally:
            h2.close()
    finally:
        _clear_recovery(h)
        h.close()


def test_recovery_is_not_deleted_by_other_windows():
    h = make()
    other = make()
    try:
        _clear_recovery(h)
        h.add_image("Img", 0, 0)
        h.session.mark_unsaved()
        assert h.service("recovery").autosave()
        other.close()  # a different session closing normally
        assert h.service("recovery").pending() is not None
        h.session.mark_saved()  # this session saves → its own copy goes away
        assert h.service("recovery").pending() is None
    finally:
        _clear_recovery(h)
        other.close()
        h.close()


def test_autosave_after_new_scene_restarts_revision_count():
    h = make()
    try:
        _clear_recovery(h)
        recovery = h.service("recovery")
        h.add_image("A", 0, 0)
        h.session.mark_unsaved()
        assert recovery.autosave() and h.session.revision >= 1
        h.session.mark_saved()
        stale = h.session.revision
        h.session.new_document()  # the revision count restarts at 0
        for _ in range(stale):
            h.session.mark_unsaved()
        assert recovery.autosave(), "new work is autosaved even at a revision number seen before"
    finally:
        _clear_recovery(h)
        h.close()


# ---- Session Resume -------------------------------------------------------------------------------


def _clear_resume(h=None):
    # file.toggle_resume's settings live scoped under the file plugin's own prefix.
    QSettings().remove("plugins/file/session_resume")
    QSettings().remove("plugins/file/session_resume_state")
    QSettings().remove("plugins/file/last_session_had_content")
    if h is not None and not h.app.closed:
        _clear_recovery(h)


def test_session_resume_disabled_by_default_and_keeps_save_prompt_on_close():
    import pixelref.builtins.file as file_plugin

    h = make()
    original = file_plugin.QMessageBox
    try:
        _clear_resume(h)
        assert not h.service("recovery").resume_enabled
        h.add_image("A", 0, 0)
        h.session.mark_unsaved()

        class _Box:
            Discard = file_plugin.QMessageBox.Discard

            @staticmethod
            def question(*_a, **_k):
                return _Box.Discard  # close without saving

        file_plugin.QMessageBox = _Box
        assert h.app.request_close(), "Discard must let the app close"
        assert h.app.closed
    finally:
        file_plugin.QMessageBox = original
        _clear_resume(h)
        h.close()


def test_session_resume_reopens_saved_file_and_offers_unsaved_changes():
    """A saved board comes back as its last saved version, with a clear, explicit
    option to recover the edits that hadn't been saved yet when the app closed."""
    import pixelref.builtins.file as file_plugin

    h = make()
    original = file_plugin.QMessageBox
    try:
        _clear_resume(h)
        recovery = h.service("recovery")
        recovery.resume_enabled = True
        h.add_image("A", 0, 0)
        path = tmp_dir() / "board.pxref"
        assert h.save_to(path)
        h.add_image("B", 100, 0)
        h.session.mark_unsaved()
        assert h.app.request_close(), "Session Resume must not prompt to save on close"
        assert h.app.closed

        h2 = make()
        try:
            recovery2 = h2.service("recovery")
            recovery2.resume_enabled = True
            asked = []

            class _Box:
                Yes = file_plugin.QMessageBox.Yes
                No = file_plugin.QMessageBox.No

                @staticmethod
                def question(_parent, _title, text, *_a, **_k):
                    asked.append(text)
                    return _Box.Yes  # recover the unsaved edit

            file_plugin.QMessageBox = _Box
            assert recovery2.resume()
            h2.refresh()
            assert asked, "must offer the unsaved edits explicitly, not restore them silently"
            assert h2.session.scene_path == path, "the saved file reopens at its own path"
            assert len(h2.nodes_of("image")) == 2, "the recovered edit (image B) came back"
            assert h2.session.unsaved, "the recovered state is still unsaved"
        finally:
            file_plugin.QMessageBox = original
            h2.close()
    finally:
        file_plugin.QMessageBox = original
        _clear_resume(h)
        h.close()


def test_session_resume_declining_unsaved_changes_keeps_last_saved_version():
    import pixelref.builtins.file as file_plugin

    h = make()
    original = file_plugin.QMessageBox
    try:
        _clear_resume(h)
        recovery = h.service("recovery")
        recovery.resume_enabled = True
        h.add_image("A", 0, 0)
        path = tmp_dir() / "board2.pxref"
        assert h.save_to(path)
        h.add_image("B", 100, 0)
        h.session.mark_unsaved()
        assert h.app.request_close()

        h2 = make()
        try:
            recovery2 = h2.service("recovery")
            recovery2.resume_enabled = True

            class _Box:
                Yes = file_plugin.QMessageBox.Yes
                No = file_plugin.QMessageBox.No

                @staticmethod
                def question(*_a, **_k):
                    return _Box.No  # keep only the last saved version

            file_plugin.QMessageBox = _Box
            assert recovery2.resume()
            h2.refresh()
            assert len(h2.nodes_of("image")) == 1, "declining drops the unsaved edit"
            assert not h2.session.unsaved
        finally:
            file_plugin.QMessageBox = original
            h2.close()
    finally:
        file_plugin.QMessageBox = original
        _clear_resume(h)
        h.close()


def test_session_resume_restores_untitled_draft_automatically():
    """An Untitled board with unsaved work comes back exactly as it was, with no prompt."""
    h = make()
    try:
        _clear_resume(h)
        recovery = h.service("recovery")
        recovery.resume_enabled = True
        h.add_image("Draft", 0, 0)
        h.session.mark_unsaved()
        assert h.session.scene_path is None
        assert h.app.request_close()
        assert h.app.closed

        h2 = make()
        try:
            recovery2 = h2.service("recovery")
            recovery2.resume_enabled = True
            assert recovery2.resume(), "an Untitled draft must come back automatically"
            h2.refresh()
            assert len(h2.nodes_of("image")) == 1
            assert h2.session.scene_path is None
            assert h2.session.unsaved
        finally:
            h2.close()
    finally:
        _clear_resume(h)
        h.close()


def test_resume_disabled_still_keeps_untitled_draft_recoverable_with_thumbnail():
    """Even without Session Resume turned on, closing an Untitled board with unsaved
    work keeps a recoverable, thumbnailed copy instead of losing it outright."""
    import pixelref.builtins.file as file_plugin

    h = make()
    original = file_plugin.QMessageBox
    try:
        _clear_resume(h)
        recovery = h.service("recovery")
        assert not recovery.resume_enabled
        assert recovery.resumable_untitled() is None, "nothing to resume on an untouched board"
        h.add_image("Draft", 0, 0, w=300, h=200)
        h.session.mark_unsaved()

        class _Box:
            Save, Discard, Cancel = (file_plugin.QMessageBox.Save, file_plugin.QMessageBox.Discard,
                                      file_plugin.QMessageBox.Cancel)

            @staticmethod
            def question(*_a, **_k):
                return _Box.Discard  # close without saving

        file_plugin.QMessageBox = _Box
        assert h.app.request_close()
        assert h.app.closed

        h2 = make()
        try:
            recovery2 = h2.service("recovery")
            info = recovery2.resumable_untitled()
            assert info is not None, "the discarded Untitled draft must still be recoverable"
            assert info.get("scene_file") is None and info.get("clean")
            assert read_thumbnail(info["path"]) is not None, "a thumbnail is kept for the start panel card"

            # A plain startup must not show the old blocking crash-recovery dialog for it.
            class _NoDialog:
                @staticmethod
                def question(*_a, **_k):
                    raise AssertionError("a clean, intentionally-kept draft must not prompt like a crash")

            file_plugin.QMessageBox = _NoDialog
            assert not recovery2.offer()

            assert h2.app.run_command("file.resume_last_session")
            h2.refresh()
            assert len(h2.nodes_of("image")) == 1
            assert h2.session.scene_path is None and h2.session.unsaved
        finally:
            file_plugin.QMessageBox = original
            h2.close()
    finally:
        file_plugin.QMessageBox = original
        _clear_resume(h)
        h.close()


def test_resume_card_shown_on_start_panel_only_when_resume_is_off():
    h = make()
    try:
        _clear_resume(h)
        recovery = h.service("recovery")
        h.add_image("Draft", 0, 0, w=300, h=200)
        h.session.mark_unsaved()
        recovery.suspend()  # same snapshot suspend() writes on close, without actually closing
        # suspend() alone (unlike a real close) doesn't record "the last close had content":
        # that's on_close()'s job, done here so this stand-in matches a real close.
        recovery.ui.settings.setValue("last_session_had_content", True)
        assert recovery.resumable_untitled() is not None

        h2 = make()
        try:
            panel = h2.service("start_panel").panels["Main"]
            panel.reload()
            assert panel.resume_card is not None, "an empty board offers the kept Untitled draft back"
            assert panel.resume_card.name.text() == "Resume Last Session"

            h2.service("recovery").resume_enabled = True
            panel.reload()
            assert panel.resume_card is None, "Session Resume handles it automatically once turned on"
        finally:
            h2.close()
    finally:
        _clear_resume(h)
        h.close()


def test_resume_card_survives_a_later_empty_close_that_never_touched_anything():
    """Opening pixelRef and closing it right away, without doing anything, must not
    erase an earlier non-empty session's resumable draft: a no-op session reports
    nothing new, so the last *meaningful* session is still what gets offered back."""
    import pixelref.builtins.file as file_plugin

    h = make()
    other = make()
    original = file_plugin.QMessageBox
    try:
        _clear_resume(h)
        h.add_image("Draft", 0, 0, w=300, h=200)
        h.session.mark_unsaved()

        class _Box:
            Save, Discard, Cancel = (file_plugin.QMessageBox.Save, file_plugin.QMessageBox.Discard,
                                      file_plugin.QMessageBox.Cancel)

            @staticmethod
            def question(*_a, **_k):
                return _Box.Discard

        file_plugin.QMessageBox = _Box
        assert h.app.request_close()
        assert other.service("recovery").resumable_untitled() is not None, "the draft exists on disk"

        assert other.app.request_close()  # a second window, untouched, closes right away

        h3 = make()
        try:
            assert h3.service("recovery").resumable_untitled() is not None, \
                "a no-op close must not erase the earlier non-empty session's draft"
        finally:
            _clear_recovery(h3)
            h3.close()
    finally:
        file_plugin.QMessageBox = original
        _clear_resume()


def test_resume_checkbox_hidden_when_last_session_was_empty():
    h = make()
    try:
        _clear_resume(h)
        assert h.app.request_close(), "nothing unsaved on a fresh board: closes without asking"
        assert h.app.closed

        h2 = make()
        try:
            panel = h2.service("start_panel").panels["Main"]
            panel.reload()
            assert not panel.resume_checkbox.isVisible(), "nothing to resume from an empty last session"
        finally:
            h2.close()
    finally:
        _clear_resume(h)
        h.close()


def test_resume_checkbox_shown_for_a_draft_edited_back_down_to_zero_items():
    """A board that had an image added and then removed is still unsaved: it isn't
    the same as an untouched, truly empty board, so the checkbox must still show
    (even though the resume card itself will report "0 items")."""
    import pixelref.builtins.file as file_plugin

    h = make()
    original = file_plugin.QMessageBox
    try:
        _clear_resume(h)
        h.add_image("Temp", 0, 0)
        h.run("delete", ["Temp"])
        h.session.mark_unsaved()
        assert h.session.unsaved and not h.scene.nodes

        class _Box:
            Save, Discard, Cancel = (file_plugin.QMessageBox.Save, file_plugin.QMessageBox.Discard,
                                      file_plugin.QMessageBox.Cancel)

            @staticmethod
            def question(*_a, **_k):
                return _Box.Discard

        file_plugin.QMessageBox = _Box
        assert h.app.request_close()
        assert h.app.closed

        h2 = make()
        try:
            panel = h2.service("start_panel").panels["Main"]
            panel.reload()
            assert panel.resume_checkbox.isVisible(), "edited-then-emptied still counts as a real last session"
        finally:
            h2.close()
    finally:
        file_plugin.QMessageBox = original
        _clear_resume(h)
        h.close()


def test_resume_checkbox_shown_and_toggles_the_setting():
    h = make()
    try:
        _clear_resume(h)
        h.add_image("A", 0, 0)
        assert h.save_to(tmp_dir() / "checkbox.pxref")
        assert h.app.request_close()
        assert h.app.closed

        h2 = make()
        try:
            recovery2 = h2.service("recovery")
            panel = h2.service("start_panel").panels["Main"]
            panel.reload()
            assert panel.resume_checkbox.isVisible(), "the last session had content worth offering to resume"
            assert not panel.resume_checkbox.isChecked()
            panel.resume_checkbox.setChecked(True)
            assert recovery2.resume_enabled, "checking the box turns Session Resume on"
            panel.resume_checkbox.setChecked(False)
            assert not recovery2.resume_enabled
        finally:
            h2.close()
    finally:
        _clear_resume(h)
        h.close()


def test_failed_load_keeps_current_scene():
    import pixelref.builtins.file as file_plugin
    from pixelref.core.errors import SceneError

    h = make()
    shown = []
    original = file_plugin.QMessageBox
    try:
        h.create("group", "Keep", pick_members=1)
        bad = tmp_dir() / "broken.pxref"
        bad.write_text('{"nodes": [{"name": "x", "type": "image", "parent": "missing-parent"', encoding="utf-8")
        try:
            h.session.open_file(bad)
        except SceneError:
            pass
        else:
            raise AssertionError("broken file should raise")
        assert h.has("Keep")

        class _Box:
            @staticmethod
            def critical(_parent, title, text):
                shown.append((title, text))

        file_plugin.QMessageBox = _Box
        assert not h.app.run_command("file.open_path", str(bad), confirm=False)
        assert shown and shown[0][0] == "Open failed"
        assert h.has("Keep") and h.item("Keep") is not None
    finally:
        file_plugin.QMessageBox = original
        h.close()


# ---- Incremental canvas == full rebuild ---------------------------------------------------------------


def _canvas_state(h):
    state = {}
    for name, item in h.app.canvas.items.items():
        parent = item.parentItem()
        poly = item.scene_polygon() if item.is_content else []
        state[name] = (
            type(item).__name__,
            getattr(parent, "node_name", None),
            tuple((round(p.x(), 3), round(p.y(), 3)) for p in poly),
            item.zValue(),
            item.isVisible(),
            round(item.opacity(), 4),
            item.locked,
            bool(item.flags() & item.GraphicsItemFlag.ItemIsSelectable),
        )
    return state


def test_incremental_canvas_matches_full_rebuild():
    """The canvas is updated incrementally after edits; it must always end up
    identical to rebuilding everything from the scene."""
    h = make()
    try:
        for i in range(4):
            h.add_image(f"Img{i}", i * 250, 0, w=200, h=100)
        h.create("group", "Layer", pick_members=1)
        h.create("annotation_text", "Note", text="hello", translate=[0.0, 200.0])
        h.set_tool("pen")
        h.scribble(0, 300)
        h.scribble(0, 360)
        h.set_tool("select")
        out = h.outliner
        steps = [
            lambda: h.drag(h.vp(100, 50), h.vp(140, 90)),
            lambda: (h.select(["Img1"]), h.trigger("outliner.lock")),
            lambda: (h.select(["Img2"]), h.trigger("outliner.hide")),
            lambda: out.on_tree_reparent("Img3", "Img0"),
            lambda: out.on_tree_reparent("Note", "Layer"),
            lambda: (h.select(["Img0", "Note"]), h.trigger("grouping.group")),
            lambda: h.trigger("grouping.ungroup"),
            lambda: (h.select(["Img0"]), h.trigger("transform.rotate_cw"), h.trigger("transform.flip_h")),
            lambda: (h.select(["Layer"]), h.trigger("outliner.lock")),
            lambda: h.run("rename", "Img2", "Renamed"),
            lambda: (h.select(["Renamed"]), h.trigger("clipboard.delete")),
            lambda: h.undo(),
            lambda: h.undo(),
            lambda: h.redo(),
            lambda: (h.run("select.all"), h.run("arrange.arrange", "grid")),
            lambda: (h.select(h.nodes_of("annotation_drawing")), h.trigger("grouping.ungroup")),
            lambda: (h.select(h.nodes_of("annotation_path")), h.trigger("pen.combine")),
            lambda: (h.select(["Img0"]), h.trigger("clipboard.duplicate")),
            lambda: h.run("restack", list(reversed(h.scene.roots))),
        ]
        for index, step in enumerate(steps):
            step()
            QTest.qWait(1)
            incremental = _canvas_state(h)
            h.app.canvas.request_full_refresh()
            full = _canvas_state(h)
            assert incremental == full, (index, {k: (incremental.get(k), full.get(k))
                                                 for k in set(incremental) | set(full)
                                                 if incremental.get(k) != full.get(k)})
    finally:
        h.close()


def test_export_originals_sanitises_names_dedupes_and_includes_group_children():
    h = make()
    try:
        data = make_png("orig.png", 8, 8).read_bytes()
        asset = h.scene.add_asset(data, name="../../escape?.png")
        h.create("image", "A", asset=asset, path="")
        h.create("image", "B", asset=asset, path="")  # same stored image twice
        h.add_image("Loose", 100, 0)
        h.run("grouping.group", ["A", "B"])
        h.select([h.parent_of("A")])
        out = tmp_dir() / "originals"
        assert h.run("export.originals", str(out)) == 1, "only the group's image, written once"
        assert [p.name for p in out.iterdir()] == ["escape_.png"]
        assert not (out.parent.parent / "escape_.png").exists()
    finally:
        h.close()


def test_fun_name_is_adjective_noun_and_respects_the_toggle():
    """New scenes get a random "Adjective-Noun" name, or plain "scene" when fun names are off."""
    from pixelref.builtins.file import ADJECTIVES, NOUNS, fun_name

    h = make()
    try:
        settings = h.app.settings.scoped("plugins/file")
        settings.setValue("fun_names", True)
        name = fun_name(settings)
        adjective, _, noun = name.partition("-")
        assert adjective in ADJECTIVES and noun in NOUNS, name
        assert all(part.isalpha() for part in name.split("-")), "filesystem-safe words only"
        assert fun_name(settings) != name or len(ADJECTIVES) * len(NOUNS) == 1, "randomised each call"
        settings.setValue("fun_names", False)
        assert fun_name(settings) == "scene"
    finally:
        h.app.settings.remove("plugins/file/fun_names")
        h.close()


def test_save_dir_defaults_to_documents_and_honours_the_setting():
    """New scenes save to Documents/pixelRef by default, or the user's file/save_dir."""
    from PySide6.QtCore import QStandardPaths

    from pixelref.builtins.file import save_dir

    h = make()
    try:
        settings = h.app.settings.scoped("plugins/file")
        base = tmp_dir()
        original = QStandardPaths.writableLocation
        QStandardPaths.writableLocation = staticmethod(lambda _standard: str(base))
        try:
            settings.remove("save_dir")
            default = save_dir(settings)
            assert default == base / "pixelRef" and default.is_dir(), default
            custom = base / "my scenes"
            assert h.run("file.set_save_dir", str(custom)) == str(custom)
            assert save_dir(settings) == custom and custom.is_dir()
            settings.setValue("save_dir", "")
            assert save_dir(settings) == default, "clearing the setting goes back to the default"
        finally:
            QStandardPaths.writableLocation = original
            settings.remove("save_dir")
    finally:
        h.close()


def test_save_as_defaults_to_the_save_dir_with_a_fun_name():
    """The Save As dialog opens in the save folder pre-filled with a fun name."""
    from PySide6.QtCore import QStandardPaths
    from PySide6.QtWidgets import QFileDialog

    from pixelref.builtins.file import SCENE_EXT

    h = make()
    base = tmp_dir()
    captured: dict[str, str] = {}
    original = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(
        lambda *args, **kwargs: (captured.update(default=str(args[2] if len(args) > 2 else kwargs.get("directory", "")))
                                or (str(base / "picked.pxref"), ""))
    )
    original_paths = QStandardPaths.writableLocation
    QStandardPaths.writableLocation = staticmethod(lambda _standard: str(base))
    try:
        h.app.settings.scoped("plugins/file").setValue("fun_names", True)
        assert h.run("file.save_as")
        default = captured["default"]
        assert default.endswith(SCENE_EXT) and Path(default).name != f"scene{SCENE_EXT}", default
        assert Path(default).parent.name == "pixelRef", default
        assert Path(default).stem.split("-")[0].isalpha()
    finally:
        QFileDialog.getSaveFileName = original
        QStandardPaths.writableLocation = original_paths
        h.app.settings.remove("plugins/file/fun_names")
        h.close()


def test_copy_path_and_open_containing_folder():
    """Copy File Path / Copy Folder Path / Open Containing Folder act on the saved scene file."""
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QApplication

    h = make()
    opened: list[str] = []
    original = QDesktopServices.openUrl
    QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toLocalFile()) or True)
    try:
        h.app.actions.refresh_states()
        assert not h.app.actions.get("file.open_folder").isEnabled(), "an unsaved scene has no folder"
        QApplication.clipboard().setText("untouched")
        assert h.run("file.copy_path") is None and not h.run("file.open_folder")
        assert QApplication.clipboard().text() == "untouched" and not opened
        scene_path = (tmp_dir() / "board.pxref").resolve()
        assert h.save_to(scene_path)
        h.app.actions.refresh_states()
        assert h.app.actions.get("file.copy_path").isEnabled()
        assert h.run("file.copy_path") == str(scene_path)
        assert QApplication.clipboard().text() == str(scene_path)
        assert h.run("file.copy_folder") == str(scene_path.parent)
        assert QApplication.clipboard().text() == str(scene_path.parent)
        assert h.run("file.open_folder")
        assert [Path(p) for p in opened] == [scene_path.parent]
    finally:
        QDesktopServices.openUrl = original
        h.close()
