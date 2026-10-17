"""Groups act as one item on the canvas; double-click goes inside."""

from __future__ import annotations

from PySide6.QtCore import Qt

from harness import APP, make


def _grouped(h):
    h.add_image("A", 0, 0)
    h.add_image("B", 300, 0)
    h.add_image("C", 0, 300)
    h.select(["A", "B"])
    h.trigger("grouping.group")
    h.refresh()
    return h.sel()[0]


def test_click_picks_the_group_and_drag_moves_it_as_one():
    h = make()
    try:
        group = _grouped(h)
        h.select([])
        h.click(h.vp(50, 50))
        APP.processEvents()
        assert h.sel() == [group], h.sel()
        h.drag(h.vp(50, 50), h.vp(150, 90))
        h.refresh()
        assert h.attr(group, "translate") == [100.0, 40.0], h.attr(group, "translate")
        assert h.attr("A", "translate") == [0.0, 0.0], "members keep their place in the group"
        assert h.undo_label() == "Move"
        # Rubber band and Select All pick the group, not its members.
        h.run("select.all")
        assert set(h.sel()) == {group, "C"}, h.sel()
        # Copy/paste the group copies its members too.
        h.select([group])
        assert h.gizmo(), "a selected group gets the transform gizmo"
    finally:
        h.close()


def test_double_click_enters_group_and_esc_goes_back_out():
    h = make()
    try:
        group = _grouped(h)
        h.select([])
        h.mouse("double", h.vp(350, 50))
        APP.processEvents()
        assert h.sel() == ["B"], h.sel()
        assert h.item(group).editing, "the group is open while a member is selected"
        from PySide6.QtWidgets import QGraphicsItem

        assert h.item("B").flags() & QGraphicsItem.ItemIsSelectable
        # Inside, clicking another member picks it.
        h.click(h.vp(50, 50))
        APP.processEvents()
        assert h.sel() == ["A"], h.sel()
        h.drag(h.vp(50, 50), h.vp(80, 50))
        h.refresh()
        assert h.attr("A", "translate") == [30.0, 0.0] and h.attr(group, "translate") == [0.0, 0.0]
        h.key(Qt.Key_Escape)
        assert h.sel() == [group]
        assert not h.item(group).editing
        # Clicking outside closes it too.
        h.mouse("double", h.vp(80, 50))
        assert h.sel() == ["A"]
        h.click(h.vp(50, 350))
        APP.processEvents()
        assert h.sel() == ["C"] and not h.item(group).editing
        # Selecting a member from the outliner/script opens the group.
        h.select(["B"])
        assert h.item(group).editing and h.item("B").isSelected()
    finally:
        h.close()


def test_pick_members_groups_do_not_capture_clicks():
    h = make()
    try:
        folder = h.run("grouping.add_group", "Lay")
        assert h.attr(folder, "pick_members") == 1
        h.add_image("A", 0, 0, parent=folder)
        h.refresh()
        h.select([])
        h.click(h.vp(50, 50))
        APP.processEvents()
        assert h.sel() == ["A"]
    finally:
        h.close()


def test_toggling_pick_members_switches_click_behaviour():
    h = make()
    try:
        group = _grouped(h)
        h.run("set_attr", f"{group}.pick_members", 1)
        h.refresh()
        h.select([])
        h.click(h.vp(50, 50))
        APP.processEvents()
        assert h.sel() == ["A"], h.sel()
        h.run("set_attr", f"{group}.pick_members", 0)
        h.refresh()
        h.select([])
        h.click(h.vp(50, 50))
        APP.processEvents()
        assert h.sel() == [group], h.sel()
        # Moving the group carries its members: their translate is relative to it.
        before, corners = h.attr("A", "translate"), h.corners("A")
        h.drag(h.vp(50, 50), h.vp(150, 90))
        h.refresh()
        assert h.attr("A", "translate") == before and h.corners("A") != corners
    finally:
        h.close()
