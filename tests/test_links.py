"""Links between elements: creating, following, waypoints, styles, reconnecting."""

from __future__ import annotations

from PySide6.QtCore import QPointF

from harness import APP, make, tmp_dir


def _two(h):
    h.add_image("A", 0, 0)          # 200 x 100
    h.add_image("B", 500, 300)
    h.refresh()


def _ends(h, link):
    item = h.item(link)
    points = item.route_points()
    st = item.sceneTransform()
    return st.map(points[0]), st.map(points[-1])


def test_link_tool_connects_two_elements():
    h = make()
    try:
        _two(h)
        h.set_tool("link")
        h.drag(h.vp(100, 50), h.vp(600, 350))
        h.refresh()
        links = h.nodes_of("link")
        assert len(links) == 1, links
        link = links[0]
        assert h.run("links.ends", link) == ["A", "B"]
        assert h.tool() == "link", "the tool stays active"
        start, end = _ends(h, link)
        # Ends sit on the elements' edges (plus the gap), not at their centres.
        assert start.x() > 200 - 1 or start.y() > 100 - 1, start
        assert end.x() < 500 + 1 or end.y() < 300 + 1, end
        # Releasing on empty space makes a link with a free end there.
        h.drag(h.vp(100, 50), h.vp(900, -300))
        free = [n for n in h.nodes_of("link") if n != link][0]
        assert h.run("links.ends", free) == ["A", None]
        assert h.attr(free, "target_point") == [900.0, -300.0]
        # A click without a drag makes nothing.
        h.click(h.vp(100, 50))
        assert len(h.nodes_of("link")) == 2
    finally:
        h.close()


def test_link_follows_its_ends_live_and_after_undo():
    h = make()
    try:
        _two(h)
        link = h.run("links.connect", "A", "B", routing="straight")
        h.refresh()
        _start, end_before = _ends(h, link)
        h.select(["B"])
        h.drag(h.vp(600, 350), h.vp(600, 550), release=False)
        APP.processEvents()
        _start, end_live = _ends(h, link)
        assert end_live.y() > end_before.y() + 150, (end_before, end_live)
        h.mouse("release", h.vp(600, 550))
        h.refresh()
        h.undo()
        h.refresh()
        APP.processEvents()
        _start, end_after = _ends(h, link)
        assert abs(end_after.y() - end_before.y()) < 1.0
    finally:
        h.close()


def test_waypoints_bend_move_and_remove():
    h = make()
    try:
        _two(h)
        link = h.run("links.connect", "A", "B", routing="straight")
        h.refresh()
        h.select([link])
        h.set_tool("select")
        g = h.gizmo()
        mid = g["handles_vp"]["mid:0"]
        h.drag(mid.toPoint(), h.vp(100, 400))
        h.refresh()
        assert h.attr(link, "waypoints") == [[100.0, 400.0]], h.attr(link, "waypoints")
        assert h.undo_label() == "Bend link"
        points = h.item(link).route_points()
        assert len(points) == 3 and abs(points[1].x() - 100) < 1e-6
        # Move the waypoint.
        g = h.gizmo()
        h.drag(g["handles_vp"]["wp:0"].toPoint(), h.vp(150, 420))
        h.refresh()
        assert h.attr(link, "waypoints") == [[150.0, 420.0]]
        # Double-click removes it.
        g = h.gizmo()
        h.mouse("double", g["handles_vp"]["wp:0"].toPoint())
        h.refresh()
        assert h.attr(link, "waypoints") == []
        # Commands.
        h.run("links.add_waypoint", link, 10, 20)
        h.run("links.add_waypoint", link, 30, 40, 0)
        assert h.attr(link, "waypoints") == [[30.0, 40.0], [10.0, 20.0]]
        h.run("links.clear_waypoints", link)
        assert h.attr(link, "waypoints") == []
    finally:
        h.close()


def test_routing_caps_and_label_render():
    h = make()
    try:
        _two(h)
        link = h.run("links.connect", "A", "B")
        h.run("links.add_waypoint", link, 400, 0)
        h.refresh()
        for routing in ("straight", "curved", "elbow"):
            for cap in ("arrow", "triangle", "circle", "diamond", "bar", "none"):
                h.run("set_attrs", link, {"routing": routing, "end_cap": cap, "start_cap": cap,
                                          "label": "depends on", "stroke_style": "dash"})
                h.refresh()
                item = h.item(link)
                assert not item.shape().isEmpty()
                assert item.content_rect().width() > 100
        elbow = h.item(link).segments()[0]
        h.grab()  # paints without errors
        assert elbow.elementCount() >= 3, "elbow segments have corners"
    finally:
        h.close()


def test_drag_end_reconnects_and_deleting_an_end_removes_the_link():
    h = make()
    try:
        _two(h)
        h.add_image("C", 0, 400)
        h.refresh()
        link = h.run("links.connect", "A", "B", routing="straight")
        h.refresh()
        h.select([link])
        g = h.gizmo()
        h.drag(g["handles_vp"]["dst"].toPoint(), h.vp(100, 450))
        h.refresh()
        assert h.run("links.ends", link) == ["A", "C"]
        assert h.undo_label() == "Reconnect link"
        h.undo()
        h.refresh()
        assert h.run("links.ends", link) == ["A", "B"]
        # Dropping an end on empty space frees it there; dropping it back attaches it again.
        h.select([link])
        g = h.gizmo()
        h.drag(g["handles_vp"]["dst"].toPoint(), h.vp(900, -400))
        h.refresh()
        assert h.run("links.ends", link) == ["A", None]
        _s, end = _ends(h, link)
        assert abs(end.x() - 900) < 1 and abs(end.y() + 400) < 1
        g = h.gizmo()
        h.drag(g["handles_vp"]["dst"].toPoint(), h.vp(600, 350))
        h.refresh()
        assert h.run("links.ends", link) == ["A", "B"] and h.attr(link, "target_anchor") == "auto"
        # Linked elements can be found, and links go with their ends.
        assert h.run("links.connected", "A") == ["B"]
        h.run("delete", ["B"])
        assert not h.has(link)
        h.undo()
        assert h.has(link) and h.has("B")
    finally:
        h.close()


def test_connect_selection_reverse_save_and_reload():
    h = make()
    try:
        _two(h)
        h.add_image("C", 0, 400)
        h.select(["A", "B", "C"])
        made = h.trigger("links.connect_selection")
        h.refresh()
        assert [h.run("links.ends", n) for n in h.nodes_of("link")] == [["A", "B"], ["B", "C"]]
        first = h.nodes_of("link")[0]
        h.run("set_attrs", first, {"end_cap": "triangle", "start_cap": "circle", "label": "x"})
        h.run("links.reverse", first)
        assert h.run("links.ends", first) == ["B", "A"]
        assert h.attr(first, "start_cap") == "triangle" and h.attr(first, "end_cap") == "circle"
        assert h.app.run_command("links.connect", "A", "A") is None, "self links are refused"
        path = tmp_dir() / "links.pxref"
        h.save_to(path)
        h.open(path)
        assert [h.run("links.ends", n) for n in h.nodes_of("link")] == [["B", "A"], ["B", "C"]]
        assert h.item(h.nodes_of("link")[0]).route_points(), "routes are rebuilt on load"
        h.run("rename", "A", "Alpha")
        assert h.run("links.ends", h.nodes_of("link")[0]) == ["B", "Alpha"], "renaming keeps links"
        _ = made
    finally:
        h.close()


def test_links_to_grouped_elements_follow_the_group():
    h = make()
    try:
        _two(h)
        link = h.run("links.connect", "A", "B", routing="straight")
        h.select(["A"])
        h.trigger("grouping.group")
        group = h.sel()[0]
        h.refresh()
        start_before, _e = _ends(h, link)
        h.run("set_attrs", group, {"translate": [0.0, 200.0]})
        h.refresh()
        APP.processEvents()
        start_after, _e = _ends(h, link)
        assert start_after.y() > start_before.y() + 150
        _ = QPointF
    finally:
        h.close()


def _choose(h, x, y, modifiers=None, snap=True, exclude=None):
    from PySide6.QtCore import Qt as _Qt

    from pixelref.builtins.links.ui import choose_end

    return choose_end(h.app.canvas, h.view, QPointF(x, y), modifiers or _Qt.NoModifier, snap, exclude)


def test_ends_attach_to_nested_elements_and_ctrl_picks_the_group():
    from PySide6.QtCore import Qt

    h = make()
    try:
        _two(h)
        h.add_image("C", 0, 400)
        h.select(["A", "C"])
        h.trigger("grouping.group")
        group = h.sel()[0]
        h.select(["B"])
        h.trigger("grouping.group")
        outer_inner = h.sel()[0]
        h.select([group, outer_inner])
        h.trigger("grouping.group")
        outer = h.sel()[0]
        h.refresh()
        choice = _choose(h, 100, 50)
        assert choice.name == "A" and choice.anchor == "auto", "the element itself, not its group"
        assert choice.chain == ["A", group, outer]
        assert _choose(h, 100, 50, Qt.ControlModifier).name == group, "Ctrl: the group around it"
        assert _choose(h, 100, 50, Qt.ControlModifier | Qt.ShiftModifier).name == outer, "…the outermost"
        assert _choose(h, 100, 250).name == group, "empty space in a group's frame picks the group"
        # The tool links nested elements directly.
        h.select([])
        h.set_tool("link")
        h.drag(h.vp(100, 50), h.vp(100, 450))
        link = h.nodes_of("link")[0]
        assert h.run("links.ends", link) == ["A", "C"]
        h.drag(h.vp(100, 50), h.vp(600, 350), modifiers=Qt.ControlModifier)
        second = [n for n in h.nodes_of("link") if n != link][0]
        assert h.run("links.ends", second) == [group, outer_inner]
        # The tool shows what an end would attach to, with the groups around it.
        feedback = h.service("links")
        h.mouse("move", h.vp(100, 50), buttons=Qt.NoButton)
        assert feedback.choice.name == "A" and feedback.breadcrumb("A") == f"{outer} › {group} › A"
        assert not h.grab().isNull()
    finally:
        h.close()


def test_edges_give_fixed_spots_alt_and_snap_off_give_free_ends():
    from PySide6.QtCore import Qt

    h = make()
    try:
        _two(h)
        edge = _choose(h, 200, 20)              # on A's right edge, near the top
        assert edge.name == "A" and edge.anchor == "fixed" and edge.spot == (1.0, 0.2), edge
        outside = _choose(h, 205, 50)           # just outside the edge: still snaps
        assert outside.name == "A" and outside.spot == (1.0, 0.5)
        assert _choose(h, 100, 50, Qt.AltModifier).name is None, "Alt: free"
        assert _choose(h, 100, 50, snap=False).name is None, "snapping off: free"
        assert _choose(h, 100, 50, exclude="A").refused, "the other end's element is refused"
        link = h.run("links.connect", "A", [600.0, -200.0], source_anchor="fixed", source_spot=[1.0, 0.2],
                     routing="straight")
        h.refresh()
        start, end = _ends(h, link)
        assert abs(start.x() - (200 + 6)) < 0.5 and abs(start.y() - 20) < 0.5, start
        assert abs(end.x() - 600) < 1e-6 and abs(end.y() + 200) < 1e-6
        # The fixed spot follows the element when it moves and scales.
        h.run("set_attrs", "A", {"translate": [100.0, 100.0], "scale": [2.0, 2.0]})
        h.refresh()
        APP.processEvents()
        start, _end = _ends(h, link)
        assert abs(start.x() - (100 + 400 + 6)) < 0.5 and abs(start.y() - (100 + 40)) < 0.5, start
        # Commands can free or re-attach an end.
        h.run("links.set_end", link, "source", [0.0, 0.0])
        assert h.run("links.ends", link) == [None, None]
        h.run("links.set_end", link, "target", "B", anchor="left")
        assert h.run("links.ends", link) == [None, "B"]
    finally:
        h.close()


def test_ends_can_pin_inside_the_content():
    h = make()
    try:
        _two(h)                                   # A: 0..200 x 0..100
        inside = _choose(h, 60, 30)
        assert inside.name == "A" and inside.anchor == "fixed" and inside.spot == (0.3, 0.3), inside
        assert _choose(h, 100, 50).anchor == "auto", "the centre attaches to the whole element"
        edge = _choose(h, 196, 70)                # inside, but within a few px of the right edge
        assert edge.spot == (1.0, 0.7), edge
        link = h.run("links.connect", "B", "A", target_anchor="fixed", target_spot=[0.3, 0.3],
                     routing="straight")
        h.refresh()
        _start, end = _ends(h, link)
        assert abs(end.x() - 60) < 1e-6 and abs(end.y() - 30) < 1e-6, "exactly at the spot, no gap"
        h.run("set_attrs", "A", {"translate": [100.0, 100.0], "scale": [2.0, 2.0]})
        h.refresh()
        APP.processEvents()
        _start, end = _ends(h, link)
        assert abs(end.x() - 220) < 1e-6 and abs(end.y() - 160) < 1e-6, "the spot follows the content"
        assert not h.grab().isNull()
    finally:
        h.close()


def test_any_link_end_has_a_handle_even_unselected():
    from PySide6.QtCore import Qt

    h = make()
    try:
        _two(h)
        h.add_image("C", 0, 400)
        link = h.run("links.connect", "A", "B", routing="straight")
        h.select([])
        h.refresh()
        grabber = None
        _start, end = _ends(h, link)
        h.mouse("move", h.view.to_viewport(end).toPoint(), buttons=Qt.NoButton)
        for handler in h.app.input.handlers():
            if type(handler).__name__ == "EndGrabber":
                grabber = handler
        assert grabber.hovered is not None and grabber.hovered[2] == "dst", "hovering an end shows its handle"
        assert not h.grab().isNull()
        # Drag it into C's content: the link re-attaches there, at that spot.
        h.drag(h.view.to_viewport(end).toPoint(), h.vp(40, 430))
        h.refresh()
        assert h.run("links.ends", link) == ["A", "C"]
        assert h.attr(link, "target_anchor") == "fixed" and h.attr(link, "target_spot") == [0.2, 0.3]
        assert h.undo_label() == "Reconnect link"
        assert not h.app.input._capture, "the drag ended cleanly"
    finally:
        h.close()


# ---- Finding the other end -----------------------------------------------------------------------


def _far(h):
    h.add_image("A", 0, 0)          # 200 x 100
    h.add_image("B", 6000, 3000)    # far off-screen
    h.refresh()
    return h.run("links.connect", "A", "B", routing="straight")


def _settle():
    from PySide6.QtTest import QTest

    QTest.qWait(400)  # camera glides take 280 ms
    APP.processEvents()


def _center(h):
    return h.view.camera()["center"]


def _finder(h):
    return next(x for x in h.app.input.handlers() if type(x).__name__ == "LinkFinder")


def test_go_to_follow_back_and_forward():
    h = make()
    try:
        link = _far(h)
        h.select(["A"])
        assert h.run("links.go_to", "B") == "B"
        _settle()
        cx, cy = _center(h)
        assert abs(cx - 6100) < 1 and abs(cy - 3050) < 1, _center(h)
        assert abs(h.view.zoom_level() - 1.0) < 1e-6, "an element that fits keeps the zoom"
        assert h.sel() == ["B"]
        h.run("links.back")
        _settle()
        assert abs(_center(h)[0]) < 1 and h.sel() == ["A"], "back restores the camera and the selection"
        h.run("links.forward")
        _settle()
        assert abs(_center(h)[0] - 6100) < 1 and h.sel() == ["B"]
        # follow: by default the end farther from the view's centre (here: A).
        assert h.run("links.follow", link) == "A"
        _settle()
        assert abs(_center(h)[0] - 100) < 1
        # Something too big for the view is zoomed out to fit.
        h.add_image("Huge", 20000, 0, w=8000, h=4000)
        h.refresh()
        h.run("links.go_to", "Huge")
        _settle()
        assert h.view.zoom_level() < 0.2
    finally:
        h.close()


def test_double_clicking_a_link_goes_to_its_far_end():
    h = make()
    try:
        link = _far(h)
        h.select([])
        h.refresh()
        start, _end = _ends(h, link)
        near = h.view.to_viewport(start + QPointF(40, 20)).toPoint()  # on the line, near A
        h.set_tool("select")
        h.click(near)
        h.mouse("double", near)
        h.mouse("release", near)
        _settle()
        assert abs(_center(h)[0] - 6100) < 1 and h.sel() == ["B"], (_center(h), h.sel())
        assert not h.app.input._capture
        # Double-clicking a selected link's end handle goes to the other end.
        h.run("links.back")
        _settle()
        h.select([link])
        h.refresh()
        start, _end = _ends(h, link)
        handle = h.view.to_viewport(start).toPoint()
        h.mouse("double", handle)
        h.mouse("release", handle)
        _settle()
        assert abs(_center(h)[0] - 6100) < 1 and h.sel() == ["B"], (_center(h), h.sel())
    finally:
        h.close()


def test_edge_chips_badges_and_hover():
    from PySide6.QtCore import Qt

    h = make()
    try:
        _far(h)
        h.set_tool("select")
        h.select(["A"])
        assert not h.grab().isNull()
        finder = _finder(h)
        chips = finder.chips[id(h.view)]
        assert [c.target.name for c in chips] == ["B"], "an off-screen linked element gets a chip"
        chip = chips[0]
        room = h.view.viewport().rect()
        assert chip.rect.right() >= room.right() - 20 or chip.rect.bottom() >= room.bottom() - 20, chip.rect
        assert chip.direction.x() > 0 and chip.direction.y() > 0, "it points toward B"
        badges = finder.badges[id(h.view)]
        assert [name for _r, name in badges] == ["A"], "the selected element shows its link count"
        h.click(chip.rect.center().toPoint())
        _settle()
        assert h.sel() == ["B"] and abs(_center(h)[0] - 6100) < 1
        assert not h.app.input._capture
        # On screen: no chip.
        h.run("links.back")
        _settle()
        h.select(["A"])
        h.run("set_attrs", "B", {"translate": [400.0, 0.0]})
        h.refresh()
        h.grab()
        assert finder.chips[id(h.view)] == []
        # Hovering an element highlights its links.
        h.select([])
        h.grab()
        h.mouse("move", h.vp(100, 50), buttons=Qt.NoButton)
        assert finder.hovered is not None and finder.hovered[1] == "A"
        h.grab()
        assert [name for _r, name in finder.badges[id(h.view)]] == ["A"]
        h.run("links.set_hover_highlight", False)
        h.mouse("move", h.vp(110, 50), buttons=Qt.NoButton)
        assert finder.hovered is None
        h.run("links.set_hover_highlight", True)
    finally:
        h.close()


def test_linked_to_menu_and_links_section():
    from PySide6.QtWidgets import QMenu

    from pixelref.builtins.links.nav import fill_linked_menu

    h = make()
    try:
        link = _far(h)
        h.add_image("C", 0, 400)
        h.refresh()
        h.run("links.connect", "C", "A")
        h.run("links.connect", "A", [900.0, -300.0])
        h.refresh()
        h.select(["A"])
        assert h.session.context.evaluate("links.connected > 0")
        nav = h.service("links.nav")
        menu = QMenu()
        fill_linked_menu(nav, menu, ["A"], h.view)
        texts = [a.text() for a in menu.actions()]
        assert texts[0] == "→  B" and texts[1] == "←  C" and texts[2].startswith("→  free end"), texts
        menu.actions()[1].trigger()
        _settle()
        assert h.sel() == ["C"]
        h.select(["A"])
        APP.processEvents()
        section = h.section("links")
        assert section is not None and section.isVisible()
        assert [b.text() for b in section.buttons] == ["→  B", "←  C", "→  free end"]
        section.buttons[0].click()
        _settle()
        assert h.sel() == ["B"]
        # A link lists its own ends; the link's style editor is still there.
        h.select([link])
        APP.processEvents()
        assert [b.text() for b in h.section("links").buttons] == ["from  A", "to  B"]
        assert h.section("attributes").isVisible()
    finally:
        h.close()
