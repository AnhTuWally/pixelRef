"""Name labels: a pinned caption, 5% of the media's height, showing its name."""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, QPointF, QUrl
from PySide6.QtWidgets import QGraphicsItem

from harness import approx, make, make_png, tmp_dir


def _label(h, media: str) -> str:
    labels = h.run("name_labels.labels_of", media)
    assert len(labels) == 1, labels
    return labels[0]


def _box(h, label: str) -> list[float]:
    """The label's box in its media's space."""
    attrs = h.attrs(label)
    lx, ly, lw, lh = h.session.local_bounds(label)
    sx, sy = attrs["scale"]
    tx, ty = attrs["translate"]
    xs = sorted((tx + sx * lx, tx + sx * (lx + lw)))
    ys = sorted((ty + sy * ly, ty + sy * (ly + lh)))
    return [xs[0], ys[0], xs[1], ys[1]]


def test_add_places_a_synced_label_in_the_bottom_left_corner():
    h = make()
    try:
        img = h.add_image("Cat", 0, 0, 200, 100)
        h.select([img])
        created = h.run("name_labels.show")
        assert len(created) == 1
        label = created[0]
        assert h.parent_of(label) == img and h.attr(label, "text") == "Cat"
        assert h.run("name_labels.show") == [], "no second label"
        h.refresh()
        left, _top, _right, bottom = _box(h, label)
        assert approx(left, 1.5) and approx(bottom, 98.5), _box(h, label)
        assert h.item(label).flags() & QGraphicsItem.ItemIsSelectable
        assert not h.item(label).flags() & QGraphicsItem.ItemIsMovable, "pinned labels can't be dragged"
        h.undo()
        assert h.run("name_labels.labels_of", img) == []
    finally:
        h.close()


def test_label_text_is_5_percent_of_the_height_and_follows_crop():
    h = make()
    try:
        img = h.add_image("Big", 0, 0, 400, 1200)
        h.select([img])
        label = h.run("name_labels.show")[0]
        assert h.attr(label, "font_size") == 45.0, "5% of 1200 px, in points"
        h.refresh()
        assert 0.04 * 1200 < h.session.local_bounds(label)[3] < 0.12 * 1200
        h.run("set_attr", img, "scale", [0.2, 0.2])
        assert h.attr(label, "scale") == [1.0, 1.0], "it scales with the image"
        h.run("set_attr", img, "crop", [50.0, 20.0, 300.0, 800.0])
        h.refresh()
        left, _top, _right, bottom = _box(h, label)
        assert approx(left, 50 + 12) and approx(bottom, 820 - 12), _box(h, label)
    finally:
        h.close()


def test_rename_updates_text_until_edited_by_hand():
    h = make()
    try:
        img = h.add_image("Dog", 0, 0)
        h.select([img])
        label = h.run("name_labels.show")[0]
        h.run("rename", img, "Puppy")
        assert h.attr(label, "text") == "Puppy"
        h.run("text.commit", label, "My caption")
        assert not h.attr(label, "label_sync")
        h.run("rename", "Puppy", "Hound")
        assert h.attr(label, "text") == "My caption", "hand-edited text stays"
        h.run("name_labels.reset", [label])
        assert h.attr(label, "text") == "Hound"
    finally:
        h.close()


def test_unpinned_label_keeps_its_place_and_pin_snaps_back():
    h = make()
    try:
        img = h.add_image("Fox", 0, 0)
        h.select([img])
        label = h.run("name_labels.show")[0]
        pinned_at = list(h.attr(label, "translate"))
        h.run("set_attr", label, "translate", [120.0, 10.0])
        assert h.attr(label, "translate") == pinned_at, "a pinned label snaps back"
        h.run("name_labels.unpin", [label])
        h.run("set_attr", label, "translate", [120.0, 10.0])
        assert h.attr(label, "translate") == [120.0, 10.0]
        h.refresh()
        assert h.item(label).flags() & QGraphicsItem.ItemIsMovable
        h.run("name_labels.pin", [label])
        assert h.attr(label, "translate") == pinned_at
    finally:
        h.close()


def test_toggle_remove_and_save_round_trip():
    h = make()
    try:
        img = h.add_image("Owl", 0, 0)
        h.select([img])
        label = h.run("name_labels.toggle")[0]
        h.run("name_labels.unpin", [img])
        path = tmp_dir() / "labels.pxref"
        assert h.save_to(path)
        assert h.open(path)
        label = _label(h, img)
        assert h.attr(label, "name_label") is True and h.attr(label, "label_pinned") is False
        h.select([img])
        assert h.run("name_labels.toggle") == [label]
        assert h.has(label) and h.attr(label, "visibility") == 0, "toggling hides, it doesn't delete"
        h.select([img])
        assert h.run("name_labels.remove") == [label]
        assert not h.has(label)
        h.undo()
        assert h.has(label), "deleting is one undo step"
    finally:
        h.close()


def test_a_label_moved_off_its_media_becomes_a_plain_note():
    h = make()
    try:
        img = h.add_image("Bee", 0, 0)
        h.select([img])
        label = h.run("name_labels.show")[0]
        h.run("parent", label, None)
        assert not h.attrs(label).get("name_label", False)
        assert h.has(img)
    finally:
        h.close()


def test_a_duplicate_labels_itself_with_its_own_name():
    h = make()
    try:
        img = h.add_image("Elk", 0, 0)
        h.select([img])
        h.run("name_labels.show")
        h.select([img])
        copies = h.run("clipboard.duplicate")
        copy = next(n for n in copies if h.type_of(n) == "image")
        assert h.attr(_label(h, copy), "text") == copy != img
        assert h.attr(_label(h, img), "text") == img
    finally:
        h.close()


def test_hiding_keeps_the_labels_changes():
    h = make()
    try:
        img = h.add_image("Yak", 0, 0)
        h.select([img])
        label = h.run("name_labels.show")[0]
        h.run("text.commit", label, "Hero shot")
        h.run("set_attr", label, "background_color", [200, 40, 40, 255])
        h.run("set_attr", label, "scale", [2.0, 2.0])
        h.run("set_attr", label, "text_width", 150.0)
        h.run("name_labels.unpin", [label])
        h.run("set_attr", label, "translate", [30.0, 12.0])
        h.select([img])
        assert h.run("name_labels.toggle") == [label]
        assert h.attr(label, "visibility") == 0 and label not in h.sel()
        h.select([img])
        assert h.run("name_labels.toggle") == [label], "the same label comes back"
        attrs = h.attrs(label)
        assert attrs["visibility"] == 1 and attrs["text"] == "Hero shot"
        assert attrs["background_color"] == [200, 40, 40, 255] and attrs["translate"] == [30.0, 12.0]
        assert attrs["scale"] == [2.0, 2.0] and attrs["text_width"] == 150.0, "its size is kept too"
        assert h.run("name_labels.labels_of", img) == [label]
    finally:
        h.close()


def _view_rect(h, name):
    return h.view.mapFromScene(h.item(name).sceneBoundingRect()).boundingRect()


def test_buttons_on_the_selection_show_unlock_and_drag_the_label():
    h = make()
    try:
        img = h.add_image("Ram", 0, 0, 300, 200)
        h.select([img])
        h.refresh()
        rect = _view_rect(h, img)
        label_button = QPoint(rect.right() - 10 - 11, rect.top() + 10 + 11)
        h.mouse("move", label_button)
        h.click(label_button)
        label = _label(h, img)
        assert h.attr(label, "visibility") != 0 and h.sel() == [img], "shown; the selection is kept"
        h.refresh()
        lrect = _view_rect(h, label)
        lock_button = QPoint(lrect.right() + 10 + 11, lrect.center().y())
        h.mouse("move", lock_button)
        h.click(lock_button)
        assert h.attr(label, "label_pinned") is False, "unlocked from its button"
        h.refresh()
        before = list(h.attr(label, "translate"))
        h.select([label])
        h.refresh()
        lrect = _view_rect(h, label)
        h.drag(lrect.center(), lrect.center() + QPoint(60, -80))
        h.refresh()
        after = h.attr(label, "translate")
        assert approx(after[0] - before[0], 60, 1.0) and approx(after[1] - before[1], -80, 1.0), (before, after)
        assert h.parent_of(label) == img
        h.select([img])
        h.refresh()
        h.mouse("move", label_button)
        h.click(label_button)
        assert h.attr(label, "visibility") == 0
        h.click(label_button)
        assert h.attr(label, "visibility") == 1 and h.attr(label, "translate") == after, "hidden and back, same place"
    finally:
        h.close()


def test_label_new_items_from_the_canvas_panel():
    h = make()
    try:
        h.select([])
        row = next(p for p in h.inspector.empty_panels if hasattr(p, "check"))
        assert not row.check.isChecked() and not h.run("name_labels.label_new")
        row.check.click()
        assert h.run("name_labels.label_new") is True, "the checkbox turns it on"

        imported = h.run("import_image", make_png("Gull.png", 200, 400).as_posix())
        assert h.attr(_label(h, imported), "text") == "Gull"
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(make_png("Crow.png", 120, 80)))])
        h.drop(QPointF(500, 0), mime)
        dropped = h.sel()[0]
        assert h.type_of(dropped) == "image" and h.attr(_label(h, dropped), "text") == dropped
        h.undo()
        assert not h.has(dropped), "the drop and its label are one undo step"

        h.select([imported])
        h.run("name_labels.remove")
        h.select([imported])
        copy = next(n for n in h.run("clipboard.duplicate") if h.type_of(n) == "image")
        assert h.run("name_labels.labels_of", copy) == [], "a duplicate isn't a new file"
        path = tmp_dir() / "plain.pxref"
        assert h.save_to(path) and h.open(path)
        assert h.run("name_labels.labels_of", imported) == [], "opening a scene adds nothing"

        h.run("name_labels.set_label_new", False)
        assert not row.check.isChecked(), "the checkbox follows the setting"
        later = h.run("import_image", make_png("Wren.png", 50, 50).as_posix())
        assert h.run("name_labels.labels_of", later) == []
    finally:
        h.run("name_labels.set_label_new", False)
        h.close()
