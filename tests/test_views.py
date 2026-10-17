"""Views: renaming tiles and linking cameras."""

from __future__ import annotations

from harness import APP, make


def test_rename_view_command_header_and_errors():
    h = make()
    try:
        wm = h.app.wm
        h.app.run_command("wm.rename_view", "Reference", "Main")
        assert wm.title("Main") == "Reference"
        assert wm.frames["Main"].header.title_label.text() == "Reference"
        assert wm._tile_ref("Reference") == "Main", "scripts can use the new title"
        other = h.app.run_command("wm.split_view", "right", "Main")
        assert h.app.run_command("wm.rename_view", "Reference", other) is None, "duplicate titles are refused"
        assert h.app.run_command("wm.rename_view", "  ", other) is None
        # Inline editing in the header.
        header = wm.frames[other].header
        header.edit_title(wm.title(other))
        assert header.title_edit.isVisible()
        header.title_edit.setText("Detail")
        header.title_edit.returnPressed.emit()
        APP.processEvents()
        assert wm.title(other) == "Detail"
        assert wm.frames[other].header.title_label.isVisible()
    finally:
        h.close()


def test_linked_views_follow_without_feedback_and_toggle():
    h = make()
    try:
        app = h.app
        b = app.run_command("wm.split_view", "right", "Main")
        c = app.run_command("wm.split_view", "down", b)
        APP.processEvents()
        main, vb, vc = app.views["Main"], app.views[b], app.views[c]
        main.set_camera([100.0, 50.0], 2.0, 0.0)
        app.run_command("views.link", "Main", b)
        assert vb.camera()["center"] == [100.0, 50.0] and vb.camera()["zoom"] == 2.0, "linking snaps to the leader"
        app.run_command("views.link", b, c)
        assert {v.view_name for v in app.linked_views(main)} == {b, c}, "linking joins groups"
        from PySide6.QtCore import QPointF

        calls = []
        original = vb.set_camera
        vb.set_camera = lambda *a, **k: (calls.append(a), original(*a, **k))
        vb.pan_by(QPointF(30, 0))
        vb.pan_by(QPointF(0, 12))
        assert not calls, "followers don't push their camera back to the view being driven"
        del vb.set_camera
        assert vc.camera()["center"] == vb.camera()["center"]
        assert main.camera()["center"] == vb.camera()["center"]
        assert app.run_command("views.toggle_link", c, "Main") is False
        assert vc.link_group == "" and app.linked_views(main) == [vb]
        app.run_command("views.unlink", b)
        assert main.link_group == "" and vb.link_group == "", "a group of one is dissolved"
    finally:
        h.close()


def test_closed_view_does_not_leave_a_link_behind_and_clones_start_unlinked():
    h = make()
    try:
        app = h.app
        b = app.run_command("wm.split_view", "right", "Main")
        app.run_command("views.link", "Main", b)
        app.run_command("wm.close_view", b)
        again = app.run_command("wm.split_view", "right", "Main")
        assert app.views[again].link_group == "", "a split copies the view but not its link"
        assert app.linked_views(app.views["Main"]) == []
    finally:
        h.close()


def test_review_template_twice_gets_separate_link_groups():
    h = make()
    try:
        wm, app = h.app.wm, h.app
        first = [t for t in wm.doc.tiles() if wm.doc.attr(t, "title") in ("Colour", "Value check")]
        wm.collect_states()
        groups = {wm.doc.attr(t, "state").get("link_group") for t in first}
        assert len(groups) == 1 and None not in groups
        copy = app.run_command("wm.new_workspace", "Review 2", "Review")
        APP.processEvents()
        tiles = wm.doc.tiles(copy)
        copies = {app.views[t].link_group for t in tiles if t in app.views}
        assert len(copies) == 1 and not copies & groups
    finally:
        h.close()


def test_opening_a_scene_restores_views_that_share_a_name_with_the_open_ones():
    """Every scene has a "ReviewColor": opening one must rebuild that view from the file
    (camera, display, its view markers), not keep the view that was showing before."""
    from harness import approx, tmp_dir

    h = make()
    try:
        app = h.app
        app.views["ReviewColor"].set_camera([300.0, -120.0], 0.5, 0.0)
        marker = h.run("views.create_camera", "Detail", "ReviewColor")
        h.run("views.toggle_markers", "ReviewColor")
        path = tmp_dir() / "markers.pxref"
        assert h.save_to(path)
        # Change everything in the open scene, then reopen the file.
        app.views["ReviewColor"].set_camera([0.0, 0.0], 2.0, 0.0)
        h.run("views.toggle_markers", "ReviewColor")
        old = app.views["ReviewColor"]
        assert h.open(path)
        APP.processEvents()
        view = app.views["ReviewColor"]
        assert view is not old, "the view is rebuilt from the file"
        camera = view.camera()
        assert approx(camera["center"][0], 300.0) and approx(camera["center"][1], -120.0)
        assert approx(camera["zoom"], 0.5)
        assert view.display.show_markers is False
        h.run("views.toggle_markers", "ReviewColor")
        h.run("views.bookmark", 1, "ReviewColor")
        assert view._current_marker == marker, "the saved marker is there for that view"
    finally:
        h.close()


def test_markers_are_shared_coloured_and_show_the_current_one():
    """Every view sees every marker (a file saved in another workspace's view used to hide
    them); each gets its own colour; the header names the current marker that Update overwrites."""
    from PySide6.QtWidgets import QToolButton

    from pixelref.builtins.views import MARKER_COLORS

    h = make()
    try:
        app = h.app
        app.views["ReviewColor"].set_camera([250.0, 80.0], 0.5, 0.0)
        review = h.run("views.create_camera", "Review spot", "ReviewColor")
        main_spot = h.run("views.create_camera", "Main spot", "Main")
        colors = [h.scene.get_node(n).attrs["color"] for n in (review, main_spot)]
        assert colors == [MARKER_COLORS[0][1], MARKER_COLORS[1][1]], "each new marker takes the next colour"
        h.refresh()
        assert {review, main_spot} <= set(app.views["Main"]._camera_names()), "Main draws both"
        h.run("views.bookmark", 1, "Main")
        assert app.views["Main"]._current_marker == review, "a marker made in another view is usable here"
        h.run("views.look_through", review, "Main", animate=False)
        APP.processEvents()
        button = next(b for b in app.wm.frames["Main"].findChildren(QToolButton) if b.text().startswith(review))
        assert "•" not in button.text()
        app.views["Main"].set_camera([0.0, 0.0], 3.0, 0.0)
        APP.processEvents()
        assert "•" in button.text(), "moving away from the current marker shows it would change"
        button.menu().aboutToShow.emit()
        actions = {a.text(): a for a in button.menu().actions()}
        assert actions[f"{review}	Ctrl+1"].isChecked() and not actions[f"{main_spot}	Ctrl+2"].isChecked()
        actions[f"Update “{review}” to this view"].trigger()
        APP.processEvents()
        assert h.scene.get_node(review).attrs["zoom"] == 3.0
        assert "•" not in button.text()
        h.run("views.set_marker_color", MARKER_COLORS[4][1], None, "Main")
        assert h.scene.get_node(review).attrs["color"] == MARKER_COLORS[4][1]
        assert app.run_command("views.update_camera", None, "ReviewValue") is None, "no current marker: nothing to update"
    finally:
        h.close()
