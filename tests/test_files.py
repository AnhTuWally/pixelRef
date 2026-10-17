"""Files: one-file scenes with embedded images, image storage, recovery, export,
and the canvas staying identical to a full rebuild after every kind of edit."""

from __future__ import annotations

from pathlib import Path

from harness import QTest, make, make_png, tmp_dir
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QImage


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
