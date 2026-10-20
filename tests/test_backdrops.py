"""Backdrops: group by placement. Hierarchy adoption, the settle rules, stacking, strict vs free."""

from __future__ import annotations

from PySide6.QtCore import Qt

from harness import make, tmp_dir


def headless():
    from pixelref.core.session import Session

    return Session().start()


def _rect(session, name, x, y, w, h):
    return session.run("rect.create", x, y, w, h, name)


def _backdrop(session, name, x, y, w, h):
    return session.run("backdrops.create", x, y, w, h, name, base_name=name)


def test_backdrop_adopts_what_is_inside_and_carries_it():
    s = headless()
    inside = _rect(s, "In", 120, 120, 100, 80)
    straddling = _rect(s, "Half", 380, 120, 100, 80)  # 20% inside
    outside = _rect(s, "Out", 900, 900, 50, 50)
    back = _backdrop(s, "Tray", 100, 100, 300, 300)
    assert s.scene.nodes[inside].parent == back
    assert s.scene.nodes[straddling].parent is None and s.scene.nodes[outside].parent is None
    assert s.world_bounds(inside) == [120.0, 120.0, 100.0, 80.0], "joining keeps the placement"
    s.run("set_attr", f"{back}.translate", [500.0, 0.0])
    assert s.world_bounds(inside) == [520.0, 20.0, 100.0, 80.0], "contents travel with the backdrop"
    assert s.world_bounds(outside) == [900.0, 900.0, 50.0, 50.0]
    assert s.run("backdrops.contents", back) == [inside]


def test_settle_joins_and_leaves_with_hysteresis():
    s = headless()
    back = _backdrop(s, "Tray", 0, 0, 200, 200)
    rect = _rect(s, "R", 300, 0, 100, 100)
    assert s.run("backdrops.settle", [rect]) == []
    # Moved 60% inside: joins (threshold 50%).
    s.run("set_attr", f"{rect}.translate", [140.0, 0.0])
    assert s.run("backdrops.settle", [rect]) == [rect] and s.scene.nodes[rect].parent == back
    # Slid out to 40% inside: stays (it only leaves under 30%).
    s.run("set_attr", f"{rect}.translate", [160.0, 0.0])  # 40 of 100 wide inside the 200 wide backdrop
    assert s.run("backdrops.settle", [rect]) == [] and s.scene.nodes[rect].parent == back
    s.run("set_attr", f"{rect}.translate", [180.0, 0.0])  # 20% inside
    before = s.world_bounds(rect)
    assert s.run("backdrops.settle", [rect]) == [rect] and s.scene.nodes[rect].parent is None
    assert s.world_bounds(rect) == before, "leaving keeps the placement"


def test_nested_backdrops_pick_the_innermost_and_big_nodes_are_not_swallowed():
    s = headless()
    outer = _backdrop(s, "Outer", 0, 0, 600, 600)
    inner = _backdrop(s, "Inner", 50, 50, 200, 200)
    assert s.scene.nodes[inner].parent == outer
    item = _rect(s, "Item", 80, 80, 50, 50)
    s.run("backdrops.settle", [item])
    assert s.scene.nodes[item].parent == inner, "smallest backdrop that holds it"
    big = _rect(s, "Big", 60, 60, 400, 400)
    s.run("backdrops.settle", [big])
    assert s.scene.nodes[big].parent == outer, "a backdrop smaller than the node never takes it"
    # No cycles: the outer one cannot join the inner one.
    s.run("backdrops.settle", [outer])
    assert s.scene.nodes[outer].parent is None


def test_moving_a_backdrop_over_loose_nodes_captures_them_and_shrinking_releases():
    s = headless()
    a = _rect(s, "A", 500, 0, 50, 50)
    back = _backdrop(s, "Tray", 0, 300, 200, 200)
    s.run("set_attr", f"{back}.translate", [450.0, -20.0])
    assert s.run("backdrops.settle", [back]) == [a] and s.scene.nodes[a].parent == back
    s.run("set_attr", f"{back}.size", [20.0, 20.0])
    assert s.run("backdrops.settle", [back]) == [a] and s.scene.nodes[a].parent is None


def test_backdrops_always_draw_behind_their_siblings():
    s = headless()
    one = _rect(s, "One", 1000, 0, 10, 10)
    back = _backdrop(s, "Tray", 0, 0, 100, 100)
    two = _rect(s, "Two", 2000, 0, 10, 10)
    assert s.scene.children_of(None) == [back, one, two], s.scene.children_of(None)
    s.run("reorder", [back], "front")
    assert s.scene.children_of(None)[0] == back, "bring to front cannot lift a backdrop over its siblings"
    s.run("reorder", [one], "back")
    assert s.scene.children_of(None)[0] == back
    # Nested ones draw above their parent and stay behind their parent's other contents.
    inner = _backdrop(s, "Inner", 10, 10, 50, 50)
    member = _rect(s, "Member", 12, 12, 10, 10)
    s.run("backdrops.adopt", back, [member])
    s.run("backdrops.adopt", back, [inner])
    assert s.scene.children_of(back) == [inner, member], s.scene.children_of(back)


def test_wrap_adopt_release_and_undo():
    s = headless()
    a = _rect(s, "A", 100, 100, 50, 50)
    b = _rect(s, "B", 300, 200, 50, 50)
    box = s.run("backdrops.wrap", [a, b], 20, "Pair")
    assert s.scene.nodes[a].parent == box and s.scene.nodes[b].parent == box
    assert s.scene.nodes[box].attrs["title"] == "Pair"
    x, y, w, h = s.world_bounds(box)
    assert x == 80.0 and x + w == 370.0 and y + h == 270.0 and y < 80.0, (x, y, w, h)
    assert s.world_bounds(a) == [100.0, 100.0, 50.0, 50.0]
    s.run("backdrops.release", [a])
    assert s.scene.nodes[a].parent is None and s.world_bounds(a) == [100.0, 100.0, 50.0, 50.0]
    s.undo.undo()
    assert s.scene.nodes[a].parent == box, "release is one undo step"
    s.undo.undo()
    assert not s.scene.has(box) and s.scene.nodes[a].parent is None, "so is wrap"


def test_strict_and_free_hosting():
    s = headless()
    frame = _rect(s, "Frame", 0, 0, 400, 400)
    small = _rect(s, "Small", 50, 50, 60, 60)
    assert s.run("backdrops.get_mode") == "strict"
    assert s.run("backdrops.settle", [small]) == [] and s.scene.nodes[small].parent is None
    s.run("backdrops.set_mode", "free")
    assert s.run("backdrops.settle", [small]) == [small] and s.scene.nodes[small].parent == frame
    s.run("backdrops.release", [small])
    # A per-node value always wins over the mode.
    s.run("set_attr", f"{frame}.backdrop_mode", "off")
    assert s.run("backdrops.settle", [small]) == []
    s.run("backdrops.set_mode", "strict")
    s.run("set_attr", f"{frame}.backdrop_mode", "inside")
    assert s.run("backdrops.settle", [small]) == [small], "switched on in strict mode"


def test_touch_and_overlap_modes():
    s = headless()
    host = _rect(s, "Host", 0, 0, 200, 200)
    s.run("set_attr", f"{host}.backdrop_mode", "overlap")
    brush = _rect(s, "Brush", 190, 190, 100, 100)  # 10px overlap, 1% inside
    s.run("backdrops.settle", [brush])
    assert s.scene.nodes[brush].parent == host
    s.run("backdrops.release", [brush])
    s.run("set_attr", f"{host}.backdrop_mode", "touch")
    near = _rect(s, "Near", 204, 0, 100, 100)  # 4px away
    s.run("set_attr", f"{near}.size", [100.0, 300.0])
    assert s.run("backdrops.settle", [near], 0.0) == []
    assert s.run("backdrops.settle", [near], 6.0) == [near], "touch reaches across the margin"


def test_locked_nodes_do_not_move_between_backdrops():
    s = headless()
    back = _backdrop(s, "Tray", 0, 0, 200, 200)
    item = _rect(s, "Item", 20, 20, 50, 50)
    s.run("backdrops.settle", [item])
    assert s.scene.nodes[item].parent == back
    s.run("set_attr", f"{item}.translate", [900.0, 900.0])
    s.run("set_attr", f"{item}.locked", 1)
    assert s.run("backdrops.settle", [item]) == [] and s.scene.nodes[item].parent == back


def test_backdrop_survives_save_and_load_as_a_tree():
    s = headless()
    item = _rect(s, "Item", 20, 20, 50, 50)
    back = _backdrop(s, "Tray", 0, 0, 200, 200)
    assert s.scene.nodes[item].parent == back
    path = tmp_dir() / "board.pxref"
    s.save_file(path)
    again = headless()
    again.open_file(path)
    assert again.scene.nodes[item].parent == back and again.scene.nodes[back].attrs["title"] == "Tray"
    assert again.scene.children_of(back) == [item]


def test_the_canvas_draws_and_moves_a_backdrop_with_its_contents():
    h = make()
    try:
        item = h.run("rect.create", 120, 120, 100, 80, "In")
        back = h.run("backdrops.create", 100, 100, 300, 300, "Tray", base_name="Tray")
        h.refresh()
        assert h.parent_of(item) == back
        assert h.item(back).childItems(), "contents are child items of the backdrop item"
        before = h.corners(item)
        # The title bar picks the backdrop; the body does not (so you can rubber-band inside it).
        h.select([])
        h.click(h.vp(250, 350))
        assert h.sel() == [], h.sel()
        h.click(h.vp(150, 109))
        assert h.sel() == [back], h.sel()
        h.drag(h.vp(150, 109), h.vp(250, 159))
        h.refresh()
        assert h.attr(back, "translate") == [200.0, 150.0], h.attr(back, "translate")
        assert h.attr(item, "translate") == [20.0, 20.0], "contents keep their own placement in the backdrop"
        assert h.corners(item) != before
    finally:
        h.close()


def test_dragging_things_onto_and_off_a_backdrop_settles_on_release_in_one_undo_step():
    h = make()
    try:
        back = h.run("backdrops.create", 0, 0, 400, 400, "Tray", base_name="Tray")
        item = h.run("rect.create", 600, 100, 80, 60, "R", fill_opacity=0.5)
        h.refresh()
        assert h.parent_of(item) is None
        h.select([])
        h.drag(h.vp(640, 130), h.vp(140, 130))
        h.refresh()
        assert h.parent_of(item) == back, h.parent_of(item)
        assert h.undo_label() == "Move"
        h.drag(h.vp(140, 130), h.vp(640, 130))
        h.refresh()
        assert h.parent_of(item) is None
        h.undo()
        h.refresh()
        assert h.parent_of(item) == back, "the move and the membership change are one undo step"
    finally:
        h.close()


def test_resize_points_change_size_not_scale_and_leave_contents_in_place():
    h = make()
    try:
        item = h.run("rect.create", 120, 120, 100, 80, "In")
        back = h.run("backdrops.create", 100, 100, 300, 300, "Tray", base_name="Tray")
        h.refresh()
        h.select([back])
        before = h.corners(item)
        g = h.gizmo()
        assert g and g["single"], "a selected backdrop gets handles"
        br = g["handles_vp"]["br"].toPoint()
        h.drag(br, br + h.vp(-50, -50) - h.vp(0, 0))
        h.refresh()
        assert h.attr(back, "size") == [250.0, 250.0] and h.attr(back, "scale") == [1.0, 1.0], h.attrs(back)
        assert h.corners(item) == before
        tl = h.gizmo()["handles_vp"]["tl"].toPoint()
        h.drag(tl, tl + h.vp(20, 20) - h.vp(0, 0))
        h.refresh()
        assert h.attr(back, "translate") == [120.0, 120.0] and h.attr(back, "size") == [230.0, 230.0], h.attrs(back)
        assert h.corners(item) == before, "contents stay where they were while the origin moves"
        assert h.parent_of(item) == back
        assert h.undo_label() == "Resize backdrop"
    finally:
        h.close()


def test_k_wraps_the_selection_or_starts_the_draw_tool():
    h = make()
    try:
        a = h.run("rect.create", 100, 100, 50, 50, "A")
        b = h.run("rect.create", 300, 200, 50, 50, "B")
        h.refresh()
        h.select([a, b])
        h.app.run_command("backdrops.make", title="Pair")
        h.refresh()
        box = h.parent_of(a)
        assert box and h.type_of(box) == "backdrop" and h.parent_of(b) == box
        assert h.attr(box, "title") == "Pair" and h.sel() == [box]
        h.undo()
        assert h.parent_of(a) is None and not h.has(box)
        # Nothing selected: the draw tool starts; dragging a frame around things captures them.
        h.select([])
        h.app.run_command("backdrops.make")
        assert h.tool() == "backdrop"
        h.drag(h.vp(50, 50), h.vp(400, 300))
        h.refresh()
        made = h.nodes_of("backdrop")
        assert len(made) == 1 and h.parent_of(a) == made[0] and h.parent_of(b) == made[0]
        assert h.sel() == made
    finally:
        h.close()


def test_drag_feedback_paints_while_dragging_and_the_hosting_mode_follows_the_setting():
    h = make()
    try:
        h.run("backdrops.create", 0, 0, 400, 400, "Tray", base_name="Tray")
        item = h.run("rect.create", 600, 100, 80, 60, "R", fill_opacity=0.5)
        h.refresh()
        h.select([])
        h.drag(h.vp(640, 130), h.vp(140, 130), release=False)
        preview = next(p.__self__ for _o, p in h.session.extensions.contributions("canvas.overlays")
                       if type(getattr(p, "__self__", None)).__name__ == "DragPreview")
        assert preview.active() and preview.compute(h.view) == [(item, None, "Tray")], "release would join"
        h.view.viewport().grab()  # paints the overlay with the preview
        h.mouse("release", h.vp(140, 130))
        h.refresh()
        assert h.parent_of(item) == "Tray"
        h.run("backdrops.set_mode", "free")
        assert h.run("backdrops.get_mode") == "free"
    finally:
        h.run("backdrops.set_mode", "strict")  # the setting is saved: leave other tests a strict app
        h.close()


def test_any_rectangle_can_become_a_backdrop_from_the_menu_action_and_back():
    h = make()
    try:
        frame = h.run("rect.create", 0, 0, 400, 300, "Frame")
        small = h.run("rect.create", 50, 50, 60, 60, "Small")
        h.refresh()
        h.select([frame])
        assert h.app.actions.get("backdrops.toggle").text() == "Act as Backdrop"
        h.trigger("backdrops.toggle")
        h.refresh()
        assert h.attr(frame, "backdrop_mode") == "inside" and h.parent_of(small) == frame, "it captures what it covers"
        assert h.app.actions.get("backdrops.toggle").text() == "Stop Acting as Backdrop"
        h.select([frame])
        h.drag(h.vp(0, 150), h.vp(200, 150))  # the frame's border carries its contents
        h.refresh()
        assert h.corners(small)[0] == (250.0, 50.0), h.corners(small)
        h.trigger("backdrops.toggle")
        h.refresh()
        assert h.parent_of(small) is None and h.corners(small)[0] == (250.0, 50.0), "contents stay where they are"
        assert h.attr(frame, "backdrop_mode") == "off"
        h.undo()
        h.refresh()
        assert h.parent_of(small) == frame
    finally:
        h.close()


def test_attribute_editor_has_the_act_as_backdrop_switch():
    h = make()
    try:
        frame = h.run("rect.create", 0, 0, 400, 300, "Frame")
        small = h.run("rect.create", 50, 50, 60, 60, "Small")
        h.refresh()
        h.select([frame])
        section = h.section("backdrop")
        assert section is not None and section.isVisibleTo(h.inspector), "shown for a rectangle"
        assert not section.check.isChecked() and not section.mode.isEnabled()
        section.check.setChecked(True)  # the user ticks it
        h.refresh()
        assert h.attr(frame, "backdrop_mode") == "inside" and h.parent_of(small) == frame
        h.select([frame])
        assert section.check.isChecked() and section.mode.isEnabled() and section.count.text() == "1 item"
        h.select([frame])
        section.mode.setCurrentIndex(section.mode.findData("overlap"))
        section._on_mode(0)
        assert h.attr(frame, "backdrop_mode") == "overlap"
        section.check.setChecked(False)
        h.refresh()
        assert h.parent_of(small) is None and h.attr(frame, "backdrop_mode") == "off"
        group = h.run("add_group", "G")
        h.select([group])
        assert not h.section("backdrop").isVisibleTo(h.inspector), "not offered for groups"
    finally:
        h.close()


def test_view_menu_toggles_free_mode_and_settle_has_a_menu_item():
    h = make()
    try:
        h.run("backdrops.set_mode", "strict")
        frame = h.run("rect.create", 0, 0, 400, 300, "Frame")
        small = h.run("rect.create", 50, 50, 60, 60, "Small")
        h.refresh()
        free = h.app.actions.get("backdrops.free")
        assert free.isCheckable() and not free.isChecked()
        h.trigger("backdrops.free")
        assert h.run("backdrops.get_mode") == "free" and free.isChecked()
        h.select([small])
        h.trigger("backdrops.settle")
        h.refresh()
        assert h.parent_of(small) == frame, "settle (menu item) applies the free-mode rules"
        h.trigger("backdrops.free")
        assert h.run("backdrops.get_mode") == "strict" and not free.isChecked()
        entries = {e.action: e for e in h.app.menus.items if e.menu == "canvas"}
        assert entries["backdrops.free"].path == ("View",), "the toggle sits in the View menu"
        assert "backdrops.settle" in entries
    finally:
        h.close()
