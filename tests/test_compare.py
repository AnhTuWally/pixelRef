"""Tests for the Compare Viewer: A/B wipe, difference, mix, toggle modes and interactions."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF
from harness import make


def test_compare_viewer_creation_and_attributes():
    h = make()
    try:
        viewer = h.run("compare.create", 100, 200, mode="wipe", wipe_pos=0.6, fit_mode="fit")
        h.refresh()
        assert viewer == "CompareViewer"
        node = h.scene.get_node(viewer)
        assert node.node_type == "compare_viewer"
        assert h.attr(viewer, "translate") == [100.0, 200.0]
        assert h.attr(viewer, "mode") == "wipe"
        assert h.attr(viewer, "wipe_pos") == 0.6
        assert h.attr(viewer, "fit_mode") == "fit"

        # Item exists on canvas
        item = h.item(viewer)
        assert item is not None
        assert item.node_type == "compare_viewer"
    finally:
        h.close()


def test_compare_selected_images_and_links():
    h = make()
    try:
        h.add_image("ImgA", 0, 0)
        h.add_image("ImgB", 300, 0)
        h.refresh()

        h.session.selection.set(["ImgA", "ImgB"])
        viewer = h.run("compare_selected")
        h.refresh()

        assert viewer is not None
        node = h.scene.get_node(viewer)
        node_a = h.scene.get_node("ImgA")
        node_b = h.scene.get_node("ImgB")

        assert node.attrs["input_a"] == node_a.id
        assert node.attrs["input_b"] == node_b.id

        # Viewer should be positioned to the right of both images
        trans = h.attr(viewer, "translate")
        assert trans[0] >= 300

        # Toggle active input
        nxt = h.run("compare.toggle_active", viewer)
        assert nxt == "b"
        assert h.attr(viewer, "active_input") == "b"

        nxt = h.run("compare.toggle_active", viewer)
        assert nxt == "a"
        assert h.attr(viewer, "active_input") == "a"

        # Swap inputs
        h.run("compare.swap_inputs", viewer)
        assert h.attr(viewer, "input_a") == node_b.id
        assert h.attr(viewer, "input_b") == node_a.id
    finally:
        h.close()


def test_compare_viewer_modes_and_geometry():
    h = make()
    try:
        viewer = h.run("compare.create", 0, 0)
        h.refresh()
        item = h.item(viewer)

        # Test wipe geometry calculation
        from pixelref.builtins.compare.ui import calculate_wipe_geometry
        rect = QRectF(0, 0, 800, 600)
        clip_path, p1, p2, center_handle = calculate_wipe_geometry(rect, 0.5, 0.0)

        assert center_handle.x() == 400.0
        assert center_handle.y() == 300.0
        assert clip_path.boundingRect().left() >= 399.0

        # Switch modes
        h.scene.set_attr(viewer, "mode", "difference")
        h.refresh()
        item = h.item(viewer)
        assert item.attrs["mode"] == "difference"

        h.scene.set_attr(viewer, "mode", "mix")
        h.scene.set_attr(viewer, "mix", 0.75)
        h.refresh()
        item = h.item(viewer)
        assert item.attrs["mode"] == "mix"
        assert item.attrs["mix"] == 0.75
    finally:
        h.close()


def test_compare_different_sizes_and_painting():
    from PySide6.QtGui import QPainter, QPixmap

    h = make()
    try:
        # Create two images of different dimensions
        img_a = h.add_image("A", 0, 0)
        img_b = h.add_image("B", 300, 0)
        h.refresh()

        # Connect them to a compare viewer
        viewer = h.run("compare.compare_nodes", "A", "B", mode="wipe", fit_mode="fit")
        h.refresh()
        item = h.item(viewer)

        # Test rendering all modes without error
        pix = QPixmap(500, 500)
        painter = QPainter(pix)

        for mode in ("wipe", "difference", "mix", "toggle"):
            h.scene.set_attr(viewer, "mode", mode)
            h.refresh()
            cur_item = h.item(viewer)
            cur_item.paint_content(painter, None, None)

        # Test all fit modes
        for fit in ("fit", "one_to_one", "fill", "stretch"):
            h.scene.set_attr(viewer, "fit_mode", fit)
            h.refresh()
            cur_item = h.item(viewer)
            rect_b = cur_item._fit_image_b(cur_item.content_rect(), 800, 400)
            assert rect_b.isValid()

        painter.end()
    finally:
        h.close()


def test_compare_wipe_handle_drag():
    h = make()
    try:
        h.add_image("A", 0, 0)
        h.add_image("B", 300, 0)
        h.refresh()

        viewer = h.run("compare.compare_nodes", "A", "B", mode="wipe", wipe_pos=0.5)
        h.refresh()
        item = h.item(viewer)

        st = item.sceneTransform()
        rect = item.content_rect()
        from pixelref.builtins.compare.ui import calculate_wipe_geometry
        _clip, _p1, _p2, center_pt = calculate_wipe_geometry(rect, 0.5, 0.0)
        scene_center = st.map(center_pt)

        vp_start = h.vp(scene_center.x(), scene_center.y())
        vp_end = h.vp(scene_center.x() + 50, scene_center.y())

        h.drag(vp_start, vp_end)
        h.refresh()

        new_pos = h.attr(viewer, "wipe_pos")
        assert new_pos > 0.5, f"Expected wipe_pos > 0.5, got {new_pos}"
    finally:
        h.close()


def test_compare_shift_rotate_and_toggle_hotkeys():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    h = make()
    try:
        h.add_image("A", 0, 0)
        h.add_image("B", 300, 0)
        h.refresh()

        viewer = h.run("compare.compare_nodes", "A", "B", mode="wipe", wipe_pos=0.5, wipe_angle=0.0)
        h.refresh()
        item = h.item(viewer)

        # 1. Test Shift + Drag Rotation
        st = item.sceneTransform()
        rect = item.content_rect()
        from pixelref.builtins.compare.ui import calculate_wipe_geometry
        _clip, _p1, _p2, center_pt = calculate_wipe_geometry(rect, 0.5, 0.0)
        scene_center = st.map(center_pt)

        vp_start = h.vp(scene_center.x(), scene_center.y())
        # Drag upwards with Shift to rotate angle towards ~-90
        vp_end = h.vp(scene_center.x(), scene_center.y() - 100)

        h.drag(vp_start, vp_end, modifiers=Qt.ShiftModifier)
        h.refresh()

        new_angle = h.attr(viewer, "wipe_angle")
        assert new_angle != 0.0, f"Expected non-zero angle after Shift-drag, got {new_angle}"

        # 2. Test Backslash Toggle hotkey
        h.session.selection.set([viewer])
        h.refresh()

        # Press '\' -> should toggle to B
        h.key(Qt.Key_Backslash)
        h.refresh()
        assert h.attr(viewer, "active_input") == "b"

        # Press '\' -> should toggle to A
        h.key(Qt.Key_Backslash)
        h.refresh()
        assert h.attr(viewer, "active_input") == "a"

        # 3. Test Clicking on Pill
        cur_item = h.item(viewer)
        pill = cur_item.pill_rect()
        st = cur_item.sceneTransform()
        pill_b_scene = st.map(QPointF(pill.left() + pill.width() * 0.75, pill.center().y()))
        pill_a_scene = st.map(QPointF(pill.left() + pill.width() * 0.25, pill.center().y()))

        # Click B half of pill
        h.click(h.vp(pill_b_scene.x(), pill_b_scene.y()))
        h.refresh()
        assert h.attr(viewer, "active_input") == "b"

        # Click A half of pill
        h.click(h.vp(pill_a_scene.x(), pill_a_scene.y()))
        h.refresh()
        assert h.attr(viewer, "active_input") == "a"
    finally:
        h.close()
