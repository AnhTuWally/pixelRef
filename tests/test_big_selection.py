"""Big boards with big selections stay cheap: what a frame, a selection change or a drag costs
does not grow with every item on the board or in the selection (see tests/bench_selection.py)."""

from __future__ import annotations

from harness import APP, make, wait_for_images
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt

from pixelref.core.events import EventStream, SceneEvent
from pixelref.ui.canvas.tiles import PREFETCH
from pixelref.ui.canvas.view import CanvasScene

_ = APP


def board(h, count: int = 12, spacing: float = 300.0) -> list[str]:
    names = [h.add_image(f"I{i}", (i % 4) * spacing, (i // 4) * spacing) for i in range(count)]
    h.view.TILE_MIN_ITEMS = 0
    return names


def boxes(h, count: int = 12, spacing: float = 300.0) -> list[str]:
    """Like ``board`` with rectangles: nothing decodes in the background and repaints on its own."""
    names = [h.create("annotation_rect", f"R{i}", translate=[(i % 4) * spacing, (i // 4) * spacing], size=[200.0, 100.0])
             for i in range(count)]
    h.view.TILE_MIN_ITEMS = 0
    return names


def paint(h):
    APP.processEvents()
    h.view.viewport().repaint()
    return h.view.render_frame()


def settle(h) -> None:
    """Render every tile the view wants (visible ones and the ring around them), as its idle timers would."""
    tiles = h.view.tiles
    paint(h)
    for _ in range(500):
        tiles._settle.stop()
        tiles._idle_timer.stop()
        tiles._idle()
        level = tiles._current_level()
        # Images decode in the background and repaint when a level arrives: wait for those too.
        if not wait_for_images() and all(key in level.tiles for key in tiles._visible_keys(level, PREFETCH)):
            break
    tiles._settle.stop()
    tiles._idle_timer.stop()


# ---- The scene's item index ---------------------------------------------------------------------


def test_scene_rect_fits_the_board_and_views_still_scroll_everywhere():
    h = make()
    try:
        board(h)
        scene = h.app.canvas.scene
        scene.fit_index()
        rect = scene.sceneRect()
        assert rect.contains(scene.itemsBoundingRect()), "the index covers every item"
        assert rect.width() < 20_000 and rect.height() < 20_000, "and is not the whole canvas"
        assert h.view.sceneRect() == CanvasScene.extent_rect(), "the view scrolls over the whole extent"
        h.view.set_camera([600_000.0, -400_000.0], 1.0, 0.0)
        center = h.view.center()
        assert abs(center.x() - 600_000.0) < 1.0 and abs(center.y() + 400_000.0) < 1.0
        # An item far outside the index rect still works (hit test, selection, painting)...
        far = h.add_image("Far", 600_000, -400_000)
        assert h.view.node_item_at(h.vp(600_050, -399_950)).node_name == far
        # ...and the index follows it.
        scene.fit_index()
        assert scene.sceneRect().contains(h.item(far).sceneBoundingRect())
    finally:
        h.close()


def test_scene_rect_is_kept_while_it_still_fits():
    h = make()
    try:
        board(h)
        scene = h.app.canvas.scene
        scene.fit_index()
        before = scene.sceneRect()
        h.run("set_attrs", "I0", {"translate": [40.0, 25.0]})
        h.refresh()
        scene.fit_index()
        assert scene.sceneRect() == before, "a small move does not re-index the board"
    finally:
        h.close()


def test_whole_scene_change_reaches_every_tile():
    h = make()
    try:
        board(h)
        scene = h.app.canvas.scene
        seen: list[list[QRectF]] = []
        scene.content_changed.connect(seen.append)
        scene.update()  # "everything changed": Qt reports the scene rect, which is only the index rect
        scene.flush_changes()
        assert seen and any(r.contains(CanvasScene.extent_rect()) for r in seen[-1])
    finally:
        h.close()


# ---- Selecting ---------------------------------------------------------------------------------------


def test_selecting_renders_no_tile_again():
    h = make()
    try:
        names = boxes(h)
        settle(h)
        tiles = h.view.tiles
        level = tiles._current_level()
        before = dict(level.tiles)
        assert before
        dropped: list[int] = []
        original = tiles.invalidate
        tiles.invalidate = lambda rects: (dropped.append(len(list(rects))), original(rects))[1]
        h.select(names)
        paint(h)
        h.select([])
        paint(h)
        del tiles.invalidate
        assert not dropped, "items look the same selected or not: selecting reports no changed pixels"
        assert all(level.tiles.get(key) is image for key, image in before.items()), "so every tile is kept"
    finally:
        h.close()


def test_a_change_made_just_before_selecting_still_repaints():
    h = make()
    try:
        names = boxes(h)
        settle(h)
        scene = h.app.canvas.scene
        reported: list[list[QRectF]] = []
        scene.content_changed.connect(reported.append)
        item = h.item(names[0])
        h.app.canvas.translate_items([item], 30.0, 0.0)  # pending when the selection changes
        h.run("select", names)
        assert len(reported) == 1, "the move was reported (to the tile caches too), the selection was not"
        assert any(rect.intersects(item.sceneBoundingRect()) for rect in reported[0])
    finally:
        h.close()


def test_outlines_follow_selection_and_moves():
    h = make()
    try:
        names = board(h)
        canvas = h.app.canvas
        assert canvas.outline_items() == [] and canvas.selection_bounds().isNull()
        h.select(names[:3])
        assert [i.node_name for i in canvas.outline_items()] == names[:3]
        assert canvas.is_outlined(h.item(names[0])) and not canvas.is_outlined(h.item(names[5]))
        first = canvas.outline_items()
        assert canvas.outline_items() == first and canvas.outline_items() is not first, "a copy of the cached list"
        bounds = canvas.selection_bounds()
        assert bounds.contains(h.item(names[2]).scene_content_rect())

        def outlined(point: QPointF) -> bool:
            frame = h.grab()
            ratio = frame.devicePixelRatio()
            p = h.view.to_viewport(point)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    c = frame.pixelColor(round(p.x() * ratio) + dx, round(p.y() * ratio) + dy)
                    if c.blue() > 180 and c.red() < 140:  # the accent colour
                        return True
            return False

        rect = h.item(names[0]).scene_content_rect()
        edge = QPointF(rect.center().x(), rect.top())
        assert outlined(edge)
        canvas.translate_items([h.item(names[0])], 0.0, -80.0)  # moved on the canvas (no command yet)
        assert outlined(edge + QPointF(0.0, -80.0)), "the outline moved with the item"
        assert not outlined(edge), "and left the old place"
        assert canvas.selection_bounds().top() < bounds.top()
        h.select([names[5]])
        assert not outlined(edge + QPointF(0.0, -80.0))
        h.run("delete", [names[5]])
        h.refresh()
        assert canvas.outline_items() == []
    finally:
        h.close()


def test_transform_targets_leave_out_a_note_being_edited():
    h = make()
    try:
        board(h)
        note = h.create("annotation_text", "Note", text="hello", translate=[900.0, 900.0])
        h.select(["I0", note])
        canvas = h.app.canvas
        assert [i.node_name for i in canvas.transform_targets()] == ["I0", note]
        item = h.item(note)
        item._editing = True  # what editing sets, without the item changing or repainting
        try:
            assert [i.node_name for i in canvas.transform_targets()] == ["I0"]
        finally:
            item._editing = False
        assert [i.node_name for i in canvas.transform_targets()] == ["I0", note]
    finally:
        h.close()


def test_selection_revision_and_name_set():
    h = make()
    try:
        names = board(h, 4)
        selection = h.session.selection
        revision = selection.revision
        h.select(names[:2])
        assert selection.revision > revision and selection.name_set == frozenset(names[:2])
        assert names[0] in selection and names[3] not in selection
        revision = selection.revision
        h.select(names[:2])
        assert selection.revision == revision, "the same selection again is no change"
        h.run("rename", names[0], "Renamed")
        assert "Renamed" in selection and names[0] not in selection
    finally:
        h.close()


# ---- Dragging ----------------------------------------------------------------------------------------


def test_moving_several_items_is_one_command_and_one_undo_step():
    h = make()
    try:
        names = board(h)
        h.select(names[:5])
        ran: list[str] = []
        tracker = h.session.commands.on_after(lambda command, _result, _error: ran.append(command.name))
        start = h.vp(100, 50)
        h.drag(start, start + QPoint(60, 40))
        APP.processEvents()
        tracker.dispose()
        assert ran.count("set_attrs_many") == 1 and "set_attrs" not in ran, ran
        moved = [h.attr(n, "translate") for n in names[:5]]
        assert moved[0][0] > 0.0 and moved[4] != [0.0, 300.0]
        h.undo()
        assert h.attr(names[0], "translate") == [0.0, 0.0] and h.attr(names[4], "translate") == [0.0, 300.0]
        h.redo()
        assert [h.attr(n, "translate") for n in names[:5]] == moved
    finally:
        h.close()


def test_set_attrs_many_is_all_or_nothing_per_command():
    h = make()
    try:
        names = board(h, 3)
        h.run("set_attrs_many", {names[0]: {"opacity": 0.5}, names[1]: {"translate": [7.0, 9.0], "opacity": 0.25}})
        assert h.attr(names[0], "opacity") == 0.5 and h.attr(names[1], "translate") == [7.0, 9.0]
        h.undo()
        assert h.attr(names[0], "opacity") == 1.0 and h.attr(names[1], "opacity") == 1.0
        try:
            h.run("set_attrs_many", {names[0]: {"opacity": 0.1}, "nobody": {"opacity": 0.1}})
        except Exception:  # noqa: BLE001
            pass
        else:
            raise AssertionError("an unknown node is an error")
        assert h.attr(names[0], "opacity") == 1.0, "nothing was set"
    finally:
        h.close()


def test_views_notice_a_moved_board_without_being_told_per_item():
    h = make()
    try:
        names = board(h)
        view = h.view
        view.resize(800, 600)
        near = view.min_zoom()
        h.app.canvas.translate_items([h.item(names[0])], -900_000.0, 0.0)  # the board is now very wide
        assert view.min_zoom() < near, "measured again after the move"
    finally:
        h.close()


def test_a_hidden_view_keeps_no_tiles_up_to_date():
    h = make()
    try:
        names = board(h)
        paint(h)
        tiles = h.view.tiles
        assert tiles.tile_count > 0
        h.view.hide()
        h.app.canvas.translate_items([h.item(names[0])], 30.0, 0.0)
        h.app.canvas.scene.flush_changes()
        assert tiles.tile_count == 0 and tiles.overview is None, "dropped, not searched"
        h.view.show()
        frame = paint(h)
        assert tiles.tile_count > 0 and not frame.isNull()
    finally:
        h.close()


def test_many_changed_rects_are_merged_onto_the_tile_grid():
    h = make()
    try:
        board(h)
        paint(h)
        tiles = h.view.tiles
        level = tiles._current_level()
        step = level.step
        # Like a dragged crowd: many small rects, overlapping, over a few tiles.
        rects = [QRectF((i * 0.618 % 6.0) * step, (i * 0.377 % 4.0) * step, step * 0.3, step * 0.3) for i in range(400)]
        merged = tiles._merge_rects(rects)
        assert len(merged) <= 8, len(merged)
        for rect in rects:  # every tile a rect touches is touched by a merged rect
            i0, i1, j0, j1 = level.index_range(rect)
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    tile = level.scene_rect((i, j))
                    assert any(m.intersects(tile) for m in merged), (i, j)
        huge = QRectF(-1e6, -1e6, 2e6, 2e6)
        assert huge in tiles._merge_rects(rects + [huge]), "a rect over the whole canvas is kept as it is"
    finally:
        h.close()


# ---- Rubber band --------------------------------------------------------------------------------------


def test_rubber_band_announces_the_selection_at_intervals_and_when_done():
    h = make()
    try:
        names = board(h)
        announced: list[list[str]] = []
        h.app.events.on("ui.selection_changed", lambda e: announced.append(list(e.payload["names"])))
        start = h.vp(-150, -150)
        h.mouse("press", start)
        for step in range(1, 9):
            h.mouse("move", start + QPoint(step * 150, step * 120), buttons=Qt.LeftButton)
        selected_mid_drag = h.sel()
        assert selected_mid_drag, "the canvas selection follows every move"
        assert len(announced) < 8, "panels are not told on every move"
        h.mouse("release", start + QPoint(8 * 150, 8 * 120))
        assert announced and announced[-1] == h.sel() == selected_mid_drag, "told the final selection at release"
        assert set(h.sel()) <= set(names)
        count = len(announced)
        h.select(names[:1])
        assert len(announced) == count + 1, "outside a rubber band every change is announced at once"
    finally:
        h.close()


# ---- Events -------------------------------------------------------------------------------------------


def test_event_stream_routes_by_type_in_subscription_order():
    stream = EventStream()
    seen: list[str] = []
    stream.on("a.one", lambda e: seen.append("one"))
    everything = stream.subscribe(lambda e: seen.append("all:" + e.event_type))
    prefix = stream.on("a.*", lambda e: seen.append("prefix"))
    stream.on("b", lambda e: seen.append("b"))
    stream.emit("a.one")
    assert seen == ["one", "all:a.one", "prefix"]
    del seen[:]
    stream.emit("a.two", {"x": 1})
    assert seen == ["all:a.two", "prefix"]
    prefix.dispose()
    everything.dispose()
    del seen[:]
    stream.emit("a.one")
    stream.emit("b")
    assert seen == ["one", "b"]
    late: list[str] = []
    stream.on("a.one", lambda e: late.append(e.event_type))  # after the route was built
    stream.emit("a.one")
    assert late == ["a.one"]


def test_event_timestamp_is_made_when_read():
    event = SceneEvent("x", {"k": 1})
    assert event._timestamp == ""
    stamp = event.timestamp
    assert stamp.endswith("+00:00") and event.timestamp == stamp
    assert event.to_dict() == {"type": "x", "payload": {"k": 1}, "timestamp": stamp}
    assert SceneEvent("x", timestamp="then").timestamp == "then"
