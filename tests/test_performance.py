"""The costs that grow with the board stay with what changed or what's on screen:
images decode at the size they're drawn, edits update items in place, undo
snapshots reuse unchanged nodes, autosave writes off the UI thread."""

from __future__ import annotations

import json

from harness import APP, make, make_png, wait_for_images
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage, QPainter

_ = APP


def _photo(name: str, w: int, h: int):
    """A JPEG with a red left half and a blue right half."""
    from harness import TMP

    image = QImage(w, h, QImage.Format_RGB32)
    image.fill(QColor("#0000ff"))
    painter = QPainter(image)
    painter.fillRect(0, 0, w // 2, h, QColor("#ff0000"))
    painter.end()
    path = TMP / name
    image.save(str(path), "JPG", 95)
    return path


def test_images_decode_at_the_size_they_are_drawn():
    from pixelref.builtins.image.source import STORE

    h = make()
    try:
        h.view.set_camera([0.0, 0.0], 1 / 16, 0.0)  # zoomed far out before the image arrives
        name = h.run("import_image", str(_photo("big_photo.jpg", 2048, 1024)))  # centred on 0, 0
        h.refresh()
        item = h.item(name)
        handle = item.handle
        assert handle is not None and item.image_size().width() == 2048, "sized from the header"
        assert item.full_rect().width() == 2048 and item.full_rect().height() == 1024
        # Zoomed far out: only a small level is decoded, and it's what gets drawn.
        frame = h.grab(clean=True)
        assert 0 not in handle.levels and any(level >= 3 for level in handle.levels), sorted(handle.levels)
        ratio = frame.devicePixelRatio()
        left = h.view.to_viewport(QPointF(-500.0, 0.0))
        assert frame.pixelColor(round(left.x() * ratio), round(left.y() * ratio)).red() > 200, "the left half is red"
        # At 100% the full image is decoded (in the background) and drawn.
        h.view.set_camera([0.0, 0.0], 1.0, 0.0)
        h.grab(clean=True)
        assert 0 in handle.levels
        # Asking for the pixels decodes them at once; the budget keeps what's on screen.
        assert item.pixmap().width() == 2048
        assert STORE.bytes > 0
    finally:
        h.close()


def test_export_renders_images_at_full_quality_before_they_are_decoded():
    h = make()
    try:
        h.view.set_camera([100000.0, 100000.0], 1.0, 0.0)  # looking elsewhere: the image isn't drawn
        name = h.run("import_image", str(_photo("export_photo.jpg", 400, 200)))
        h.app.canvas.refresh(force=True)
        APP.processEvents()
        assert not h.item(name).handle.levels, "nothing decoded until it's drawn"
        image = h.app.canvas.render_items([h.item(name)], opaque=True)
        assert image is not None and image.width() == 400
        assert image.pixelColor(50, 100).red() > 200 and image.pixelColor(350, 100).blue() > 200
    finally:
        h.close()


def test_picture_budget_never_evicts_what_is_on_screen():
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QImageReader, QPixmap

    from pixelref.builtins.image.source import ImageHandle, PictureStore

    store = PictureStore(limit=100)
    handles = [ImageHandle(i, lambda: QImageReader(), QSize(1024, 1024), False, False) for i in range(3)]
    for handle in handles:
        picture = QPixmap(1024, 1024)  # 4 MB: big enough to be evicted
        handle.levels[0] = picture
        store.add(handle, 0, picture)
    assert all(0 in handle.levels for handle in handles), "drawn just now: over the budget, not thrashing"
    for key, (handle, size, _used) in list(store.entries.items()):
        store.entries[key] = (handle, size, 0.0)  # long ago
    store.trim()
    assert sum(0 in handle.levels for handle in handles) == 0 and store.bytes == 0
    small = ImageHandle(9, lambda: QImageReader(), QSize(64, 64), False, False)
    small.levels[0] = QPixmap(64, 64)
    store.add(small, 0, small.levels[0])
    store.entries[(id(small), 0)] = (small, store.entries[(id(small), 0)][1], 0.0)
    store.trim()
    assert 0 in small.levels, "small levels stay: decoding one again costs more than keeping it"


def test_moving_keeps_items_and_never_rebuilds_the_board():
    h = make()
    try:
        path = make_png("tile.png", 40, 30, "#22aa44")
        names = [h.run("import_image", str(path), translate=[i * 50.0, 0.0]) for i in range(12)]
        h.refresh()
        before = {name: h.item(name) for name in names}
        h.run("move", 5.0, 3.0, nodes=names)  # every image: once a full rebuild
        h.app.canvas.flush_pending()
        assert all(h.item(name) is before[name] for name in names), "moved in place"
        assert h.attr(names[3], "translate")[0] == 3 * 50.0 + 5.0 - 20.0
        h.undo()
        h.app.canvas.flush_pending()
        assert all(h.item(name) is before[name] for name in names), "undo moves them back in place too"
        assert h.attr(names[3], "translate")[0] == 3 * 50.0 - 20.0
        # Other edits still rebuild the item.
        h.run("set_attrs", names[0], {"crop": [0, 0, 20, 30]})
        h.app.canvas.flush_pending()
        assert h.item(names[0]) is not before[names[0]] and h.item(names[0]).crop_rect().width() == 20
    finally:
        h.close()


def test_undo_snapshots_match_a_fresh_serialization():
    from pixelref.core.scene_file import SceneParser

    h = make()
    try:
        path = make_png("snap.png", 20, 20, "#4466cc")
        names = [h.run("import_image", str(path), translate=[i * 30.0, 0.0]) for i in range(6)]
        steps = [
            lambda: h.run("move", 4.0, 0.0, nodes=names[2:4]),
            lambda: h.session.scene.set_attrs(names[4], {"opacity": 0.25}),
            lambda: h.session.scene.rename_node(names[5], "renamed_tile"),
            lambda: h.session.scene.reparent(names[3], None),
            lambda: h.session.scene.delete_node(names[2]),
        ]
        for step in steps:
            cached = h.session.dumps()
            fresh = SceneParser().dumps(h.session.scene, include_assets=False)
            assert cached == fresh
            step()
        assert h.session.dumps() == SceneParser().dumps(h.session.scene, include_assets=False)
        h.undo()
        assert h.session.dumps() == SceneParser().dumps(h.session.scene, include_assets=False)
    finally:
        h.close()


def test_saved_file_reads_back_with_embedded_images():
    from harness import TMP

    h = make()
    try:
        name = h.run("import_image", str(_photo("saved_photo.jpg", 300, 100)))
        out = TMP / "streamed.pxref"
        assert h.save_to(out)
        data = json.loads(out.read_text(encoding="utf-8"))
        asset_id = h.attr(name, "asset")
        assert data["assets"][asset_id]["size"] == h.session.scene.get_asset(asset_id).size
        h2 = make()
        try:
            assert h2.open(out)
            assert h2.item(name).pixmap().width() == 300
            assert h2.session.scene.get_asset(asset_id).data == h.session.scene.get_asset(asset_id).data
        finally:
            h2.close()
    finally:
        h.close()


def test_background_autosave_lands_unless_cleared_first():
    h = make()
    try:
        recovery = h.service("recovery")
        for path in recovery.recovery_files():
            path.unlink(missing_ok=True)
        h.run("import_image", str(make_png("auto.png", 30, 30)))
        assert recovery.autosave(background=True)
        assert not recovery.autosave(background=True), "one autosave at a time"
        recovery.wait()
        assert recovery.pending() is not None and recovery.pending()["items"] == 1
        h.run("move", 1.0, 0.0, nodes=h.nodes_of("image"))
        recovery.clear()
        assert recovery.autosave(background=True)
        recovery.clear()  # saved meanwhile: the write in flight must not come back as a recovery copy
        recovery.wait()
        assert recovery.pending() is None
    finally:
        h.close()
        wait_for_images()
