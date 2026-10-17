"""Canvas interaction: selection, the outliner, moving/scaling/rotating, keys."""

from __future__ import annotations

import sys

from harness import APP, QTest, approx, corners_close, make
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtWidgets import QAbstractItemView


# ---- Selection -----------------------------------------------------------------------------


def test_rubber_band_is_blue():
    h = make()
    try:
        start, end = QPoint(100, 100), QPoint(300, 260)
        h.drag(start, end, release=False)
        image = h.view.render_frame()
        border = image.pixelColor(200, 100)
        inside = image.pixelColor(200, 180)
        background = h.service("canvas_background").background_color
        assert (border.red(), border.green(), border.blue()) == (61, 142, 240), border.name()
        assert inside.blue() > background.blue() + 15 and inside.blue() > inside.red(), inside.name()
        assert abs(inside.red() - inside.blue()) > 20, "fill must be tinted blue, not white/grey"
        h.mouse("release", end)
    finally:
        h.close()


def test_rubber_band_selects_intersecting_items():
    h = make()
    try:
        h.add_image("A", -300, -50)
        h.add_image("B", 150, -50)
        h.drag(h.vp(-350, -100), h.vp(-200, 100))
        assert h.sel() == ["A"]
        h.drag(h.vp(120, -100), h.vp(200, 100), modifiers=Qt.ShiftModifier)  # Shift adds
        assert sorted(h.sel()) == ["A", "B"]
        h.drag(h.vp(0, 300), h.vp(0, 300), steps=1)  # click on empty space clears
        assert h.sel() == []
    finally:
        h.close()


def test_selection_noop_skips_view_refresh():
    h = make()
    try:
        h.add_image("A", 0, 0)
        h.select(["A"])
        calls = []

        def spy() -> None:
            calls.append("updated")

        h.app.update_views = spy
        h.app.canvas._apply_selection_to_items()
        assert calls == []
    finally:
        h.close()


# ---- Outliner ---------------------------------------------------------------------------------


def _synthetic_tree_drop(h, target_name, indicator):
    """Run SceneTreeWidget.dropEvent the way Qt would for an internal move."""
    from PySide6.QtGui import QDropEvent

    tree = h.outliner.tree
    target = h.outliner.tree_item(target_name) if target_name else None
    pos = tree.visualItemRect(target).center() if target is not None else QPoint(5, tree.viewport().height() - 5)
    event = QDropEvent(QPointF(pos), Qt.MoveAction, QMimeData(), Qt.LeftButton, Qt.NoModifier)
    event.source = lambda: tree  # synthetic events have no drag source
    tree.dropIndicatorPosition = lambda: indicator
    tree.dropEvent(event)
    return event


def test_tree_drop_keeps_child_visible_and_in_place():
    h = make()
    try:
        h.add_image("Parent", -400, -200, scale=[2.0, 2.0], rotate=30.0)
        h.add_image("Child", 250, 120)
        before = h.corners("Child")
        h.select(["Child"])
        event = _synthetic_tree_drop(h, "Parent", QAbstractItemView.DropIndicatorPosition.OnItem)
        assert event.isAccepted()
        QTest.qWait(10)
        assert h.parent_of("Child") == "Parent"
        out = h.outliner
        assert out.tree_item("Child") is not None, "child vanished from the outliner"
        assert out.tree_item("Child").parent() is out.tree_item("Parent")
        item = h.item("Child")
        assert item.isVisible() and item.parentItem() is h.item("Parent")
        assert corners_close(before, h.corners("Child")), (before, h.corners("Child"))
        # Back to the root (drop on empty outliner space) — still in place.
        _synthetic_tree_drop(h, None, QAbstractItemView.DropIndicatorPosition.OnViewport)
        QTest.qWait(10)
        assert h.parent_of("Child") is None
        assert corners_close(before, h.corners("Child"))
    finally:
        h.close()


class _FakeDrag:
    """Stands in for QDrag the way the Windows drag loop behaves."""

    def __init__(self, h, target_name, indicator):
        self.h, self.target_name, self.indicator = h, target_name, indicator

    def __call__(self, _source):
        return self

    def setMimeData(self, mime):  # noqa: N802
        self.mime = mime

    def setPixmap(self, _pixmap):  # noqa: N802
        pass

    def exec(self, actions, _default=None):
        assert actions == Qt.MoveAction
        _synthetic_tree_drop(self.h, self.target_name, self.indicator)
        QTest.qWait(10)
        return Qt.MoveAction


def test_real_drag_flow_keeps_rows_and_canvas_drag_works():
    """Regression: reparenting via a real outliner drag deleted the child row and
    the next canvas selection crashed with 'Internal C++ object already deleted'."""
    from pixelref.builtins.outliner import ui as outliner_ui

    errors = []
    previous_hook = sys.excepthook
    sys.excepthook = lambda *exc: errors.append(exc)
    real_drag = outliner_ui.QDrag
    h = make()
    try:
        h.add_image("Parent", -300, -100)
        h.add_image("Child", 150, 50)
        before = h.corners("Child")
        h.select(["Child"])
        outliner_ui.QDrag = _FakeDrag(h, "Parent", QAbstractItemView.DropIndicatorPosition.OnItem)
        h.outliner.tree.startDrag(Qt.MoveAction)
        outliner_ui.QDrag = real_drag
        QTest.qWait(10)
        assert h.parent_of("Child") == "Parent"
        row = h.outliner.tree_item("Child")
        assert row is not None and row.parent() is h.outliner.tree_item("Parent"), "child row vanished"
        assert h.outliner.tree.topLevelItemCount() == 1
        assert corners_close(h.corners("Child"), before, 0.01)
        # Dragging the parent on the canvas selects it and moves the child along.
        h.drag(h.vp(-200, -50), h.vp(-100, 0))
        assert h.sel() == ["Parent"]
        assert h.attr("Parent", "translate") == [-200.0, -50.0]
        moved = h.corners("Child")
        assert approx(moved[0][0], before[0][0] + 100) and approx(moved[0][1], before[0][1] + 50)
        assert h.item("Child").isVisible()
        assert not errors, errors
    finally:
        outliner_ui.QDrag = real_drag
        sys.excepthook = previous_hook
        h.close()


def test_tree_reorder_between_rows_restacks():
    h = make()
    try:
        for name, x in (("One", 0), ("Two", 50), ("Three", 100)):
            h.add_image(name, x, 0)
        tree = h.outliner.tree

        def order():
            return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]

        assert order() == ["Three", "Two", "One"], order()  # front-most first
        h.select(["One"])
        _synthetic_tree_drop(h, "Three", QAbstractItemView.DropIndicatorPosition.AboveItem)
        QTest.qWait(10)
        assert order() == ["One", "Three", "Two"], order()
        assert h.item("One").zValue() > h.item("Three").zValue()
        assert h.scene.roots == ["Two", "Three", "One"], "draw order = order of the roots list"
        h.undo()
        assert order() == ["Three", "Two", "One"], order()
    finally:
        h.close()


def test_layer_children_keep_positions_and_inherit_visibility():
    h = make()
    try:
        h.add_image("Img", 120, 80)
        before = h.corners("Img")
        h.create("group", "Layer", pick_members=1)
        h.outliner.on_tree_reparent("Img", "Layer")
        assert h.corners("Img") == before
        h.select(["Layer"])
        h.trigger("outliner.hide")
        assert not h.item("Img").isVisible()
        h.select(["Layer"])
        h.trigger("outliner.lock")
        h.trigger("outliner.hide")
        assert h.item("Img").isVisible()
        assert h.item("Img").locked, "lock must propagate to children"
        assert h.run("get_attr", "Img.world_locked") == 1
    finally:
        h.close()


def test_outliner_filter_icons_and_empty_hint():
    h = make()
    try:
        out = h.outliner
        assert out.outliner_count.text() == ""
        assert not out.tree.viewport().grab().toImage().isNull()
        h.create("group", "Background", pick_members=1)
        h.add_image("Moodboard", 0, 0, parent="Background")
        h.add_image("Sketch", 300, 0)
        assert out.outliner_count.text() == "3 items"
        assert out.tree.isHeaderHidden()
        item = out.tree_item("Sketch")
        assert not item.icon(1).isNull() and not item.icon(2).isNull() and item.text(1) == ""
        out.outliner_filter.setText("mood")
        assert not out.tree_item("Moodboard").isHidden()
        assert not out.tree_item("Background").isHidden(), "ancestors of matches stay visible"
        assert out.tree_item("Sketch").isHidden()
        h.refresh()
        out.refresh()  # the filter survives rebuilds
        assert out.tree_item("Sketch").isHidden()
        out.outliner_filter.clear()
        assert not out.tree_item("Sketch").isHidden()
    finally:
        h.close()


def test_outliner_type_filter_and_menu():
    h = make()
    try:
        out = h.outliner
        h.create("group", "Board", pick_members=1)
        h.add_image("Photo", 0, 0, parent="Board")
        h.add_image("Loose", 300, 0)
        h.create("annotation_rect", "Box")
        h.create("annotation_text", "Note")
        out.set_hidden_types({"image"})
        assert out.tree_item("Photo").isHidden() and out.tree_item("Loose").isHidden()
        assert not out.tree_item("Box").isHidden()
        assert not out.tree_item("Board").isHidden() and not out.tree_item("Board").font(0).italic()
        assert out.outliner_count.text() == "3 of 5 items", out.outliner_count.text()
        assert out.type_button.text() == "Types 3/4", out.type_button.text()
        assert h.attr("Photo", "visibility") == 1, "the type filter doesn't touch the scene"
        # Only images: their group stays listed (in italics) to show where they live.
        out.set_hidden_types({"group", "annotation_rect", "annotation_text"})
        assert not out.tree_item("Photo").isHidden() and out.tree_item("Board").font(0).italic()
        out.refresh()
        out._force_rebuild()  # the type filter survives rebuilds
        assert out.tree_item("Box").isHidden()
        # The Types menu: check boxes per type plus the Annotations collection.
        out._build_type_menu()
        rows = {action.text().split("   ")[0]: action for action, _types in out._type_rows}
        assert {"All Types", "Annotations", "Image", "Group", "Rectangle", "Text note"} <= set(rows), rows
        assert rows["Image"].isChecked() and not rows["Annotations"].isChecked()
        rows["Annotations"].trigger()
        assert not out.tree_item("Box").isHidden() and not out.tree_item("Note").isHidden()
        assert rows["Rectangle"].isChecked(), "check boxes follow each other"
        out._solo_types(rows["Rectangle"])
        assert out.hidden_types == {"group", "image", "annotation_text"}
        rows["All Types"].trigger()
        assert not out.hidden_types and out.type_button.text() == "Types"
    finally:
        h.close()


def test_type_visibility_commands():
    h = make()
    try:
        h.create("group", "Board", pick_members=1)
        h.add_image("Photo", 0, 0, parent="Board")
        h.create("annotation_rect", "BoxIn", parent="Board")
        h.add_image("Loose", 300, 0)
        h.create("annotation_rect", "Box")
        h.create("annotation_text", "Note")
        assert h.run("outliner.set_type_flag", "annotation_*", "visibility", 0) == 3
        assert [h.attr(n, "visibility") for n in ("BoxIn", "Box", "Note", "Photo")] == [0, 0, 0, 1]
        h.undo()
        assert h.attr("Box", "visibility") == 1, "one undo step"
        # Isolate images: the group holding one stays visible, the rest is hidden.
        assert h.run("outliner.isolate_types", "image") == 3
        vis = {n: h.attr(n, "visibility") for n in ("Board", "Photo", "BoxIn", "Loose", "Box", "Note")}
        assert vis == {"Board": 1, "Photo": 1, "BoxIn": 0, "Loose": 1, "Box": 0, "Note": 0}, vis
        h.trigger("outliner.show_all")
        # Isolating groups keeps what's inside them.
        h.run("outliner.isolate_types", "group")
        assert h.attr("BoxIn", "visibility") == 1 and h.attr("Loose", "visibility") == 0
        h.trigger("outliner.show_all")
        h.run("outliner.set_type_flag", ["annotation_text"], "locked", 1)
        assert h.attr("Note", "locked") == 1 and h.attr("Box", "locked") == 0
        # From the selection: the "Same Type" actions.
        h.select(["Box"])
        h.trigger("outliner.select_same_type")
        assert sorted(h.sel()) == ["Box", "BoxIn"], h.sel()
        h.trigger("outliner.hide_same_type")
        assert h.attr("Box", "visibility") == 0 and h.attr("Photo", "visibility") == 1
        try:
            h.run("outliner.set_type_flag", "no_such_type")
        except Exception as exc:  # noqa: BLE001
            assert "Unknown node type" in str(exc), exc
        else:
            raise AssertionError("an unknown type must be refused")
    finally:
        h.close()


# ---- Moving, scaling, rotating ------------------------------------------------------------------


def test_drag_move_is_undoable_and_redoable():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        h.drag(h.vp(100, 50), h.vp(160, 90))
        moved = list(h.attr("Img", "translate"))
        assert approx(moved[0], 60, 1.0) and approx(moved[1], 40, 1.0), moved
        h.undo()
        assert h.attr("Img", "translate") == [0, 0]
        h.redo()
        assert h.attr("Img", "translate") == moved
        # Nudging after a drag must start from the dragged position.
        h.select(["Img"])
        h.run("transform.nudge", 1.0, 0.0)
        h.end_coalesced()
        assert approx(h.attr("Img", "translate")[0], moved[0] + 1.0)
    finally:
        h.close()


def test_corner_handle_scales_with_opposite_corner_fixed():
    h = make()
    try:
        h.add_image("Img", 0, 0, w=200, h=100)
        h.select(["Img"])
        h.drag(h.vp(200, 100), h.vp(400, 200))
        attrs = h.attrs("Img")
        assert approx(attrs["scale"][0], 2.0, 0.02) and approx(attrs["scale"][1], 2.0, 0.02), attrs["scale"]
        assert attrs["translate"] == [0, 0]
        # Top-left handle keeps the bottom-right corner anchored.
        h.drag(h.vp(0, 0), h.vp(200, 100))
        poly = h.item("Img").scene_polygon()
        assert approx(poly[2].x(), 400, 1.0) and approx(poly[2].y(), 200, 1.0)
        assert h.undo_label() == "Scale"
    finally:
        h.close()


def test_rotate_knob_and_shift_snapping():
    h = make()
    try:
        h.add_image("Img", -100, -50, w=200, h=100)
        h.select(["Img"])
        g = h.gizmo()
        knob = g["rotate_vp"].toPoint()
        center = h.view.to_viewport(g["center_scene"]).toPoint()
        right = QPoint(center.x() + 200, center.y() + 3)  # from straight above the centre to its right: +90°
        h.drag(knob, right, modifiers=Qt.ShiftModifier)
        assert h.attr("Img", "rotate") == 90.0, h.attr("Img", "rotate")
        poly = h.item("Img").scene_polygon()
        cx = sum(p.x() for p in poly) / 4
        cy = sum(p.y() for p in poly) / 4
        assert approx(cx, 0.0, 0.5) and approx(cy, 0.0, 0.5), "rotation must pivot on the centre"
    finally:
        h.close()


def test_multi_selection_with_rect_still_scales():
    h = make()
    try:
        h.create("annotation_rect", "Box", translate=[0.0, 0.0], size=[100.0, 50.0])
        h.add_image("Img", 200, 0, w=100, h=50)
        h.run("select.all")
        g = h.gizmo()
        assert not g.get("rect") and "radius" not in g["handles_vp"]
    finally:
        h.close()


def test_space_drag_pans_and_hover_outline():
    h = make()
    try:
        h.add_image("Img", -100, -50, w=200, h=100)
        h.select([])
        h.view.setFocus()
        before = h.view.center()
        QTest.keyPress(h.view, Qt.Key_Space)
        h.drag(QPoint(300, 300), QPoint(400, 350))
        QTest.keyRelease(h.view, Qt.Key_Space)
        after = h.view.center()
        assert approx(before.x() - after.x(), 100, 2) and approx(before.y() - after.y(), 50, 2)
        assert h.sel() == [], "space-drag pans instead of selecting"
        assert h.attr("Img", "translate") == [-100, -50]
        select_tool = h.app.tools.tools["select"]
        h.mouse("move", h.vp(0, 0), buttons=Qt.NoButton)
        assert select_tool.hover_item is h.item("Img")
        h.mouse("move", h.vp(900, 900), buttons=Qt.NoButton)
        assert select_tool.hover_item is None
    finally:
        h.close()


# ---- Edit commands & keys -------------------------------------------------------------------------


def test_multi_selection_delete_duplicate_copy_paste():
    h = make()
    try:
        h.add_image("A", -300, -50)
        h.add_image("B", 150, -50)
        h.run("select.all")
        h.trigger("clipboard.duplicate")
        assert sorted(h.sel()) == ["A_1", "B_1"], h.sel()
        pos = h.attr("A_1", "translate")
        assert approx(pos[0], -276) and approx(pos[1], -26)
        h.trigger("clipboard.copy")
        h.trigger("clipboard.delete")
        assert not h.has("A_1") and not h.has("B_1")
        h.app.menu_scene_pos = QPointF(1000, 1000)
        h.trigger("clipboard.paste")
        h.app.menu_scene_pos = None
        names = sorted(h.sel())
        assert len(names) == 2, names
        bounds = h.app.canvas.scene_bounds([h.item(n) for n in names])
        assert approx(bounds.center().x(), 1000, 1) and approx(bounds.center().y(), 1000, 1)
        h.undo()
        h.undo()
        assert h.has("A_1"), "undo should bring back the deleted duplicates"
    finally:
        h.close()


def test_keyboard_shortcuts_on_canvas():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        h.select(["Img"])
        h.key(Qt.Key_Right, Qt.ShiftModifier)
        h.end_coalesced()
        assert h.attr("Img", "translate") == [10.0, 0.0]
        h.key(Qt.Key_H)
        assert h.attr("Img", "visibility") == 0
        h.key(Qt.Key_Z, Qt.ControlModifier)
        assert h.attr("Img", "visibility") == 1
        h.key(Qt.Key_Delete)
        assert not h.has("Img")
        sidebar = h.app.panels.sidebar
        assert sidebar.isVisible()
        h.key(Qt.Key_Tab)
        assert not sidebar.isVisible(), "Tab hides/shows the panels"
        h.key(Qt.Key_Tab)
        assert sidebar.isVisible()
    finally:
        h.close()


def test_crop_mode():
    h = make()
    try:
        h.add_image("Img", 0, 0, w=200, h=100)
        h.select(["Img"])
        h.trigger("crop.start")
        assert h.tool() == "crop"
        h.drag(h.vp(0, 0), h.vp(50, 20))
        h.key(Qt.Key_Return)
        assert h.tool() == "select"
        assert h.attr("Img", "crop") == [50.0, 20.0, 150.0, 80.0]
        poly = h.item("Img").scene_polygon()
        assert approx(poly[0].x(), 50) and approx(poly[0].y(), 20), "cropping must not move the visible pixels"
        h.trigger("crop.reset")
        assert h.attr("Img", "crop") is None
    finally:
        h.close()


def test_flip_rotate_reset_grayscale():
    h = make()
    try:
        h.add_image("Img", 0, 0, w=200, h=100)
        h.select(["Img"])
        before = sorted(h.corners("Img"))
        h.trigger("transform.flip_h")
        attrs = h.attrs("Img")
        assert attrs["scale"] == [-1.0, 1.0] and attrs["rotate"] == 0.0, attrs
        assert sorted(h.corners("Img")) == before, "flip must happen in place"
        h.trigger("transform.rotate_cw")
        assert h.attr("Img", "rotate") == 90.0
        h.trigger("transform.reset")
        assert h.attr("Img", "scale") == [1.0, 1.0] and h.attr("Img", "rotate") == 0.0
        h.trigger("image.grayscale")  # grayscale is an effect now
        assert [e["type"] for e in h.attr("Img", "effects")] == ["grayscale"]
        h.refresh()
        assert h.item("Img").paint_filters, "the effect is drawn"
        h.trigger("image.grayscale")
        assert h.attr("Img", "effects") == []
    finally:
        h.close()


def test_arrange_normalize_align():
    h = make()
    try:
        h.add_image("A", 0, 0, w=300, h=100)
        h.add_image("B", 10, 10, w=100, h=200)
        h.add_image("C", 20, 20, w=150, h=150)
        h.run("select.all")
        h.run("arrange.arrange", "optimal")
        h.refresh()
        rects = [h.item(n).scene_content_rect() for n in "ABC"]
        for i in range(3):
            for j in range(i + 1, 3):
                assert not rects[i].adjusted(1, 1, -1, -1).intersects(rects[j]), "arranged items overlap"
        h.run("arrange.normalize", "height")
        h.refresh()
        heights = [h.item(n).scene_content_rect().height() for n in "ABC"]
        assert max(heights) - min(heights) < 0.01, heights
        h.run("arrange.align", "top")
        h.refresh()
        tops = {round(h.item(n).scene_content_rect().top(), 3) for n in "ABC"}
        assert len(tops) == 1, tops
    finally:
        h.close()


def test_group_and_ungroup_preserve_positions():
    h = make()
    try:
        h.add_image("A", 0, 0)
        h.add_image("B", 300, 0, rotate=20.0)
        before = {n: h.corners(n) for n in "AB"}
        h.run("select.all")
        h.trigger("grouping.group")
        group = h.sel()[0]
        assert h.type_of(group) == "group"
        assert h.parent_of("A") == group
        h.trigger("grouping.ungroup")
        assert not h.has(group)
        for n in "AB":
            assert corners_close(before[n], h.corners(n)), n
    finally:
        h.close()


def test_ctrl_k_opens_searchable_menu():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    h = make()
    try:
        h.add_image("Img", 0, 0, w=100, h=50)
        h.select(["Img"])
        seen = {}

        def type_into_popup():
            popup = QApplication.activePopupWidget()
            seen["popup"] = popup
            QTest.keyClicks(popup.field, "rotate clockwise")
            QTest.keyClick(popup.field, Qt.Key_Return)

        QTimer.singleShot(150, type_into_popup)
        h.key(Qt.Key_K, Qt.ControlModifier)
        assert seen.get("popup") is not None and hasattr(seen["popup"], "field")
        assert h.attr("Img", "rotate") == 90.0
    finally:
        h.close()


_ = APP


def test_zoomed_out_image_draws_whole_mip():
    """Far zoomed out, a small image's mip stops shrinking early; the whole image must still be drawn."""
    from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

    from pixelref.builtins.image.items import MIPS, PixmapItem

    source = QImage(64, 64, QImage.Format_ARGB32)
    source.fill(QColor("#0000ff"))
    fill = QPainter(source)
    fill.fillRect(0, 0, 32, 32, QColor("#ff0000"))
    fill.end()
    item = PixmapItem("Img", QPixmap.fromImage(source))
    MIPS.wait()  # its zoomed-out copies are made in the background
    assert item.pixmap().cacheKey() in MIPS.chains
    out = QImage(2, 2, QImage.Format_ARGB32)
    out.fill(QColor("#000000"))
    painter = QPainter(out)
    painter.scale(1 / 32, 1 / 32)
    item.paint(painter, None, None)
    painter.end()
    assert out.pixelColor(1, 1).blue() > 200, out.pixelColor(1, 1).name()
    assert out.pixelColor(0, 0).red() > 200, out.pixelColor(0, 0).name()


def test_fit_rect_keeps_flip_and_syncs_camera():
    from PySide6.QtCore import QRectF

    h = make()
    try:
        view = h.view
        view.set_camera([0.0, 0.0], 1.0, 30.0, flip_x=True)
        view.fit_rect(QRectF(0.0, 0.0, 400.0, 200.0))
        cam = view.camera()
        assert cam["flip_x"] is True and approx(cam["rotation"], 30.0), cam
        assert view.transform().m11() < 0, "a mirrored view must stay mirrored, not turn upside down"
        view.set_camera([0.0, 0.0], 1.0, 0.0, flip_x=False)
        view.fit_rect(QRectF(-100.0, -100.0, 200.0, 200.0))
        assert approx(view.camera()["zoom"], view.zoom_level(), 1e-3), (view.camera(), view.zoom_level())
    finally:
        h.close()


def test_grayscale_view_repaints_after_item_change():
    h = make()
    try:
        h.add_image("A", 0, 0)
        view = h.view
        view.display.grayscale = True
        view.render_frame()
        stale = view._gray_cache
        assert stale is not None
        h.item("A").setPos(400.0, 0.0)
        APP.processEvents()
        view.render_frame()
        assert view._gray_cache is not stale, "a stale greyscale frame would hide the item change"
    finally:
        h.close()


def test_render_items_child_of_compound_is_not_blank():
    h = make()
    try:
        h.set_tool("pen")
        h.scribble(0, 0)
        h.scribble(0, 60)
        h.set_tool("select")
        drawing = h.nodes_of("annotation_drawing")[0]
        stroke = h.nodes_of("annotation_path")[0]
        assert h.parent_of(stroke) == drawing
        image = h.app.canvas.render_items([h.item(stroke)])
        assert image is not None
        reds = sum(1 for x in range(0, image.width(), 2) for y in range(0, image.height(), 2)
                   if image.pixelColor(x, y).alpha() > 0)
        assert reds > 0, "the stroke vanished with its parent's opacity"
        assert h.item(drawing).opacity() == 1.0
    finally:
        h.close()


def test_scrub_label_click_jitter_keeps_value():
    from PySide6.QtCore import QPointF as _P
    from PySide6.QtGui import QMouseEvent

    from pixelref.ui.widgets import ScrubLabel, make_spin

    spin = make_spin(default=5.0)
    label = ScrubLabel("X", spin, 1.0)

    def send(kind, x):  # noqa: ANN001
        pos = _P(x, 0.0)
        buttons = Qt.NoButton if kind == QMouseEvent.MouseButtonRelease else Qt.LeftButton
        event = QMouseEvent(kind, pos, pos, Qt.LeftButton, buttons, Qt.NoModifier)
        {QMouseEvent.MouseButtonPress: label.mousePressEvent, QMouseEvent.MouseMove: label.mouseMoveEvent,
         QMouseEvent.MouseButtonRelease: label.mouseReleaseEvent}[kind](event)

    send(QMouseEvent.MouseButtonPress, 10.0)
    send(QMouseEvent.MouseMove, 11.0)
    send(QMouseEvent.MouseButtonRelease, 11.0)
    assert spin.value() == 5.0, spin.value()
    send(QMouseEvent.MouseButtonPress, 10.0)
    send(QMouseEvent.MouseMove, 15.0)
    assert spin.value() == 10.0, spin.value()
    send(QMouseEvent.MouseButtonRelease, 15.0)


def test_captured_handler_keeps_capture_on_second_button():
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent

    from pixelref.ui.canvas.input import HANDLED, InputHandler, InputPipeline, PointerEvent

    class Recorder(InputHandler):
        def __init__(self, button):  # noqa: ANN001
            self.button, self.events = button, []

        def pointer(self, e):  # noqa: ANN001
            if e.kind == "press" and e.button != self.button:
                return None
            self.events.append(e.kind)
            return HANDLED

    pipeline = InputPipeline()
    tool, nav = Recorder(Qt.LeftButton), Recorder(Qt.MiddleButton)
    pipeline.add(nav, 900)
    pipeline.add(tool, 500)
    view = object()

    def send(event_type, button, buttons):  # noqa: ANN001
        kind = {QEvent.MouseButtonPress: "press", QEvent.MouseMove: "move", QEvent.MouseButtonRelease: "release"}
        qt = QMouseEvent(event_type, QPointF(5, 5), QPointF(5, 5), button, buttons, Qt.NoModifier)
        pipeline.pointer(PointerEvent(view, kind[event_type], qt))

    send(QEvent.MouseButtonPress, Qt.LeftButton, Qt.LeftButton)
    send(QEvent.MouseButtonPress, Qt.MiddleButton, Qt.LeftButton | Qt.MiddleButton)  # declined mid-drag
    send(QEvent.MouseButtonRelease, Qt.MiddleButton, Qt.LeftButton)
    send(QEvent.MouseButtonRelease, Qt.LeftButton, Qt.NoButton)
    assert tool.events == ["press", "release", "release"], tool.events
    assert nav.events == [] and pipeline.captured(view) is None
    # A stale capture (its release was lost) still lets a fresh press reach the other handlers.
    send(QEvent.MouseButtonPress, Qt.LeftButton, Qt.LeftButton)
    send(QEvent.MouseButtonPress, Qt.MiddleButton, Qt.MiddleButton)
    assert pipeline.captured(view) is nav


def test_undo_during_drag_keeps_undo_working():
    h = make()
    try:
        h.create("annotation_rect", "A", translate=[0.0, 0.0], size=[100.0, 50.0])
        h.create("annotation_rect", "B", translate=[300.0, 0.0], size=[100.0, 50.0])
        canvas = h.app.canvas
        canvas.begin_interaction()
        h.item("A").setPos(50.0, 0.0)
        canvas.mark_dirty(h.item("A"))
        h.undo()  # Ctrl+Z while the mouse is still down
        canvas.end_interaction("Move")
        assert not h.session.undo.in_chunk
        assert h.attr("A", "translate") == [0.0, 0.0]
        h.app.run_command("set_attrs", "B", {"translate": [10.0, 10.0]})
        h.undo()
        assert h.attr("B", "translate") == [300.0, 0.0], h.attr("B", "translate")
    finally:
        h.close()


def test_direct_camera_change_stops_camera_animation():
    h = make()
    try:
        view = h.view
        view.set_camera([500.0, 500.0], 2.0, 0.0, animate=True)
        APP.processEvents()
        view.set_camera([0.0, 0.0], 1.0, 0.0)
        QTest.qWait(400)
        camera = view.camera()
        assert camera["center"] == [0.0, 0.0] and camera["zoom"] == 1.0, camera
    finally:
        h.close()


def test_rapid_nudges_are_one_undo_step():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        h.select(["Img"])
        before = len(h.session.undo.stack.labels())
        for _ in range(3):
            h.key(Qt.Key_Right)
        h.end_coalesced()
        assert h.attr("Img", "translate") == [3.0, 0.0]
        assert h.session.undo.stack.labels()[before:] == ["Nudge"], h.session.undo.stack.labels()[before:]
        h.undo()
        assert h.attr("Img", "translate") == [0.0, 0.0]
    finally:
        h.close()


def test_select_all_and_invert_skip_strokes_inside_drawings():
    h = make()
    try:
        h.set_tool("pen")
        h.scribble(0, 0)
        h.scribble(0, 50)
        h.set_tool("select")
        h.add_image("Img", 300, 0, w=50, h=50)
        drawing = h.nodes_of("annotation_drawing")[0]
        h.run("select.all")
        assert sorted(h.sel()) == sorted([drawing, "Img"]), h.sel()
        h.select([drawing])
        h.run("select.invert")
        assert h.sel() == ["Img"], h.sel()
    finally:
        h.close()


def test_shift_rotate_snaps_the_local_angle_under_a_mirrored_parent():
    import math

    h = make()
    try:
        h.create("group", "G", scale=[-1.0, 1.0])
        h.add_image("A", 0, 0, w=200, h=100)
        h.run("move_nodes", ["A"], "G", None, "end")
        h.session.scene.set_attr("A", "rotate", 10.0)
        h.refresh()
        h.select(["A"])
        gizmo = h.service("gizmo")
        g = gizmo.gizmo(h.view)
        c = g["center_scene"]
        gizmo.start(h.view, "rot", g, QPointF(c.x() + 100, c.y()))
        a = math.radians(37)
        gizmo.drag(QPointF(c.x() + 100 * math.cos(a), c.y() + 100 * math.sin(a)), Qt.ShiftModifier, h.view)
        gizmo.finish()
        rotate = h.attr("A", "rotate")
        assert approx(rotate / 15.0, round(rotate / 15.0), 1e-6), rotate
    finally:
        h.close()


def test_right_press_during_middle_pan_keeps_panning():
    h = make()
    try:
        view = h.view
        start = view.camera()["center"]
        both = Qt.MiddleButton | Qt.RightButton
        h.mouse("press", QPoint(200, 200), Qt.MiddleButton)
        h.mouse("press", QPoint(200, 200), Qt.RightButton, buttons=both)
        h.mouse("release", QPoint(200, 200), Qt.RightButton, buttons=Qt.MiddleButton)
        h.mouse("move", QPoint(260, 200), buttons=Qt.MiddleButton)
        h.mouse("release", QPoint(260, 200), Qt.MiddleButton)
        moved = view.camera()["center"][0] - start[0]
        assert approx(abs(moved), 60 / view.zoom_level(), 1.0), (start, view.camera())
    finally:
        h.close()


def test_wheel_zoom_glides_to_the_target_about_the_cursor():
    import time

    from PySide6.QtCore import QPointF

    h = make()
    try:
        view = h.view
        anchor = QPointF(300.0, 200.0)
        under = view.to_scene(anchor)
        view.glide_zoom(1.15, anchor)
        view.glide_zoom(1.15, anchor)  # a second notch mid-glide adds to the target
        assert view.zoom_level() < 1.15 ** 2 - 1e-3, "it eases in over several frames"
        deadline = time.time() + 2.0
        while view._glide_timer.isActive() and time.time() < deadline:
            APP.processEvents()
        assert approx(view.zoom_level(), 1.15 ** 2, 1e-6), view.zoom_level()
        moved = view.to_viewport(under) - anchor
        assert abs(moved.x()) <= 1.0 and abs(moved.y()) <= 1.0, "the point under the cursor stays put"
        assert approx(view.camera()["zoom"], 1.15 ** 2, 1e-4)
        view.glide_zoom(2.0, anchor)
        view.set_camera(zoom=0.5)  # a direct camera change ends the glide
        APP.processEvents()
        assert not view._glide_timer.isActive() and approx(view.zoom_level(), 0.5, 1e-6)
    finally:
        h.close()
