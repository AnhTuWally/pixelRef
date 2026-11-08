"""Snap to grid: the maths, snapped drags and scaling, Ctrl flipping it, and the headless command."""

from __future__ import annotations

from harness import APP, approx, make
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QCheckBox


def snap_on(h, on: bool = True) -> None:
    h.run("snap.set_enabled", on)
    h.run("canvas_background.set", grid_size=64)


def test_delta_snaps_the_nearest_edge():
    h = make()
    try:
        snap_on(h)
        api = h.service("snap")
        assert api.step() == 64.0
        # Left edge 10 + 50 = 60 is 4 from 64; the right edge (10 + 100 + 50 = 160) is 32 from 128/192.
        assert api.delta((10.0, 0.0, 100.0, 100.0), 50.0, 0.0) == (54.0, 0.0)
        # The right edge wins when it's closer: 0 + 70 + 120 = 190 → 192.
        assert api.delta((0.0, 0.0, 70.0, 64.0), 120.0, 3.0) == (122.0, 0.0)
        assert api.factor(0.0, 100.0, 1.2) == 1.28  # 120 → 128
        h.run("canvas_background.set", grid_size=32)
        assert api.value(50.0) == 64.0 and api.value(47.0) == 32.0
    finally:
        snap_on(h, False)
        h.close()


def test_drag_snaps_the_selection_as_one_and_undoes():
    h = make()
    try:
        snap_on(h)
        h.add_image("A", 0, 0, w=100, h=50)
        h.add_image("B", 150, 0, w=100, h=50)
        h.run("select.all")
        h.drag(h.vp(50, 25), h.vp(110, 60))  # +60, +35: the left edge goes to 64, the bottom to 64
        assert h.attr("A", "translate") == [64, 14], h.attr("A", "translate")
        # B keeps its offset from A: the selection moves as one.
        assert h.attr("B", "translate") == [214, 14], h.attr("B", "translate")
        assert h.undo_label() == "Move"
        h.undo()
        assert h.attr("A", "translate") == [0, 0] and h.attr("B", "translate") == [150, 0]
    finally:
        snap_on(h, False)
        h.close()


def test_ctrl_flips_snapping_and_off_is_unchanged():
    h = make()
    try:
        h.add_image("Img", 0, 0, w=100, h=50)
        snap_on(h)
        h.select(["Img"])
        h.drag(h.vp(50, 25), h.vp(110, 60), modifiers=Qt.ControlModifier)
        moved = h.attr("Img", "translate")
        assert approx(moved[0], 60, 1.0) and approx(moved[1], 35, 1.0), moved
        snap_on(h, False)
        h.drag(h.vp(110, 60), h.vp(150, 70))
        moved = h.attr("Img", "translate")
        assert approx(moved[0], 100, 1.0) and approx(moved[1], 45, 1.0), moved
        # With snapping off, Ctrl turns it on for the drag.
        h.drag(h.vp(150, 70), h.vp(170, 90), modifiers=Qt.ControlModifier)  # 120..220 × 65..115
        assert h.attr("Img", "translate") == [128, 64], h.attr("Img", "translate")
    finally:
        snap_on(h, False)
        h.close()


def test_snapped_move_inside_a_scaled_group():
    h = make()
    try:
        snap_on(h)
        h.add_image("Img", 0, 0, w=100, h=50)
        h.run("group", ["Img"], "G")
        h.run("set_attr", "G", "scale", [2.0, 2.0])
        h.refresh()
        h.select(["Img"])
        before = h.item("Img").scene_content_rect()
        start = before.center().toPoint()
        h.drag(h.vp(start.x(), start.y()), h.vp(start.x() + 40, start.y() + 30))
        after = h.item("Img").scene_content_rect()
        assert approx(after.width(), before.width(), 0.01) and approx(after.height(), before.height(), 0.01)
        assert any(approx(x % 64, 0, 0.01) or approx(x % 64, 64, 0.01) for x in (after.left(), after.right())), after
        assert any(approx(y % 64, 0, 0.01) or approx(y % 64, 64, 0.01) for y in (after.top(), after.bottom())), after
        assert h.parent_of("Img") == "G"
    finally:
        snap_on(h, False)
        h.close()


def test_scale_handle_lands_on_the_grid():
    h = make()
    try:
        snap_on(h)
        h.add_image("Img", 0, 0, w=200, h=100)
        h.select(["Img"])
        h.drag(h.vp(200, 100), h.vp(250, 130))  # uniform corner scale to about 1.25
        poly = h.item("Img").scene_polygon()
        br = poly[2]
        assert approx(br.x() % 64, 0, 0.01) or approx(br.y() % 64, 0, 0.01), (br.x(), br.y())
        assert approx(poly[0].x(), 0, 0.01) and approx(poly[0].y(), 0, 0.01), "the opposite corner stays put"
        # An edge handle snaps its own axis.
        g = h.gizmo()
        mr = g["handles_vp"]["mr"].toPoint()
        h.drag(mr, mr + QPoint(37, 0))
        poly = h.item("Img").scene_polygon()
        assert approx(poly[1].x() % 64, 0, 0.01), poly[1].x()
    finally:
        snap_on(h, False)
        h.close()


def test_to_grid_command_and_toggle_action():
    h = make()
    try:
        snap_on(h, False)
        h.add_image("A", 10, 70, w=100, h=50)
        h.add_image("B", 300, 5, w=100, h=50)
        h.run("snap.to_grid", ["A", "B"])
        assert h.attr("A", "translate") == [0, 64], h.attr("A", "translate")
        assert h.attr("B", "translate") == [284, 0], h.attr("B", "translate")  # right edge 400 → 384 is 16 away
        assert h.undo_label() == "Snap to grid"
        assert h.service("snap").enabled is False
        h.trigger("snap.toggle")
        assert h.service("snap").enabled is True
        assert h.app.actions.get("snap.toggle").isChecked()
    finally:
        snap_on(h, False)
        h.close()


def test_to_grid_works_headless():
    from pixelref.core.session import Session

    session = Session().start()
    session.scene.create_node("annotation_rect", "Box", attrs={"translate": [70.0, -10.0], "size": [100.0, 40.0]})
    session.run("snap.to_grid", ["Box"])
    assert session.scene.nodes["Box"].attrs["translate"] == [64, 0], session.scene.nodes["Box"].attrs["translate"]


def test_snap_step_is_the_drawn_grid_at_every_zoom_and_pattern():
    from pixelref.plugin_api.ui import grid_step

    h = make()
    try:
        snap_on(h)
        api = h.service("snap")
        for pattern in ("lines", "dots", "checker", "none"):
            for size in (8, 30, 64, 100, 512):
                h.run("canvas_background.set", pattern=pattern, grid_size=size)
                for zoom in (4.0, 1.0, 0.5, 0.2, 0.05):
                    h.view.set_camera([0.0, 0.0], zoom, 0.0)
                    drawn = grid_step(h.view.background_spec(), h.view.zoom_level())
                    step = api.step(h.view)
                    assert step == drawn, (pattern, size, zoom, step, drawn)
                    assert step % size == 0 and step * h.view.zoom_level() >= 8.0, (pattern, size, zoom, step)
        # Hiding the grid in a view (or the Clean preset) keeps the spacing it would draw.
        h.run("canvas_background.set", pattern="lines", grid_size=64)
        h.view.set_camera([0.0, 0.0], 0.2, 0.0)
        h.view.display.show_grid = False
        assert api.step(h.view) == 256.0
    finally:
        h.view.display.show_grid = True
        h.run("canvas_background.set", pattern="lines")
        snap_on(h, False)
        h.close()


def test_zoomed_out_drag_snaps_to_the_visible_lines():
    h = make()
    try:
        snap_on(h)
        h.add_image("Img", 0, 0, w=100, h=50)
        h.view.set_camera([0.0, 0.0], 0.2, 0.0)  # 64px lines would be 12.8px apart: the view draws every 256
        h.select(["Img"])
        h.drag(h.vp(50, 25), h.vp(250, 125))  # +200, +100
        x, y = h.attr("Img", "translate")
        assert (x % 256 == 0 or (x + 100) % 256 == 0) and (y % 256 == 0 or (y + 50) % 256 == 0), (x, y)
        assert [x, y] == [156, 0], (x, y)  # right edge 300 → 256; bottom 150 → 256 is farther than top 100 → 0
    finally:
        snap_on(h, False)
        h.close()


def test_grid_size_comes_from_your_default_when_the_scene_has_none():
    h = make()
    try:
        snap_on(h)
        h.run("canvas_background.set", grid_size=100)  # also becomes your default for new scenes
        h.scene.set_plugin_data("canvas_background", {"pattern": "lines"})  # an older file without grid_size
        assert h.view.background_spec().grid_size == 100
        assert h.service("snap").grid_size() == 100.0
        assert h.service("snap").step(h.view) == 100.0
    finally:
        snap_on(h, False)
        h.close()


def test_canvas_panel_has_a_snap_toggle_that_stays_in_sync():
    h = make()
    try:
        snap_on(h, False)
        h.run("select", clear=True)
        h.refresh()
        row = h.inspector.findChild(QCheckBox, "canvasRow_snap")
        assert row is not None, "the Canvas panel shows the Snap to grid row"
        form = row.parentWidget().form
        assert form.labelForField(row).text() == "Snap to grid"
        assert not row.icon().isNull() and not row.isChecked()
        row.click()  # the checkbox turns snapping on…
        assert h.service("snap").enabled and h.app.actions.get("snap.toggle").isChecked()
        h.trigger("snap.toggle")  # …and the shortcut/menu action shows in the checkbox
        assert not row.isChecked()
        h.run("snap.set_enabled", True)  # so does a script
        assert row.isChecked()
        assert not h.app.actions.get("snap.toggle").icon().isNull()
        # Switching the snap plugin off removes the row; back on brings it back.
        h.session.plugins.set_enabled("snap", False)
        APP.processEvents()  # removed rows are deleted later
        assert h.inspector.findChild(QCheckBox, "canvasRow_snap") is None
        h.session.plugins.set_enabled("snap", True)
        assert h.inspector.findChild(QCheckBox, "canvasRow_snap") is not None
    finally:
        h.session.settings.remove("plugins/disabled")
        h.session.settings.remove("plugins/enabled")
        snap_on(h, False)
        h.close()
