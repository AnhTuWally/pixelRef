"""The canvas tile cache: busy views paint from cached tiles (ui.canvas.tiles)."""

from __future__ import annotations

from harness import APP, make
from PySide6.QtCore import QPointF

from pixelref.ui.canvas.tiles import tiles_mode


def tiled():
    h = make()
    h.view.TILE_MIN_ITEMS = 0  # tiles even for these small scenes
    return h


def paint(h):
    APP.processEvents()
    h.view.viewport().repaint()
    return h.view.render_frame()


def is_image(color) -> bool:  # make_png's #c04040
    return abs(color.red() - 0xC0) < 8 and abs(color.green() - 0x40) < 8 and abs(color.blue() - 0x40) < 8


def test_views_with_many_items_paint_from_tiles():
    h = make()
    try:
        h.add_image("A", -100, -50)
        if not tiles_mode():  # PIXELREF_TILES overrides the item threshold
            assert not h.view.uses_tiles(), "a small scene paints directly"
        h.view.TILE_MIN_ITEMS = 1
        assert h.view.uses_tiles()
        h.view.set_camera([0.0, 0.0], 1.0, 30.0)
        assert not h.view.uses_tiles(), "a rotated camera paints directly (never resampled)"
    finally:
        h.close()


def test_tiles_show_the_scene_and_follow_moves():
    h = tiled()
    try:
        h.add_image("A", -100, -50)
        image = paint(h)
        assert h.view.tiles.tile_count > 0
        p = h.vp(0, 0)
        assert is_image(image.pixelColor(p)), image.pixelColor(p).name()
        kept = h.view.tiles.tile_count
        h.app.canvas.translate_items([h.item("A")], 400.0, 0.0)
        image = paint(h)
        assert not is_image(image.pixelColor(p)), "the old place was repainted"
        assert is_image(image.pixelColor(h.vp(400, 0)))
        assert h.view.tiles.tile_count >= kept - 4, "only tiles under the change are dropped"
    finally:
        h.close()


def test_a_direct_repaint_right_after_an_edit_is_not_stale():
    h = tiled()
    try:
        h.add_image("A", -100, -50)
        paint(h)
        h.app.canvas.translate_items([h.item("A")], 400.0, 0.0)
        h.view.viewport().repaint()  # no event processing in between
        image = h.view.render_frame()
        assert not is_image(image.pixelColor(h.vp(0, 0)))
    finally:
        h.close()


def test_removing_an_item_drops_only_its_tiles():
    h = tiled()
    try:
        h.add_image("A", -100, -50)
        h.add_image("B", 300, -50)
        h.app.canvas.refresh()  # an incremental refresh from here on (not the harness's full rebuild)
        paint(h)
        before = h.view.tiles.tile_count
        h.run("delete", ["B"])
        h.app.canvas.flush_pending()
        image = paint(h)
        assert not is_image(image.pixelColor(h.vp(400, 0)))
        assert is_image(image.pixelColor(h.vp(0, 0)))
        assert h.view.tiles.tile_count >= before - 6, (before, h.view.tiles.tile_count)
    finally:
        h.close()


def test_view_filters_apply_to_tiles():
    h = tiled()
    try:
        h.add_image("A", -100, -50)
        assert is_image(paint(h).pixelColor(h.vp(0, 0)))
        h.app.set_display(h.view, {"hide_types": ["image"]})
        assert not is_image(paint(h).pixelColor(h.vp(0, 0))), "hidden in this view"
        h.app.set_display(h.view, {"hide_types": []})
        assert is_image(paint(h).pixelColor(h.vp(0, 0)))
    finally:
        h.close()


def test_zoom_shows_old_tiles_then_renders_the_new_level():
    h = tiled()
    try:
        h.add_image("A", -100, -50)
        paint(h)
        tiles = h.view.tiles
        h.view.zoom_by(1.5, around_cursor=False)
        image = paint(h)
        assert is_image(image.pixelColor(h.vp(0, 0))), "drawn from the previous zoom's tiles"
        assert tiles._settle.isActive(), "the new zoom level is rendered once the zoom settles"
        tiles._idle()
        while tiles._idle_timer.isActive():
            tiles._idle_timer.stop()
            tiles._idle()
        level = tiles._current_level()
        assert all(k in level.tiles for k in tiles._visible_keys(level))
        assert is_image(paint(h).pixelColor(h.vp(0, 0)))
    finally:
        h.close()


def test_pan_keeps_tiles():
    h = tiled()
    try:
        h.add_image("A", -100, -50)
        paint(h)
        level = h.view.tiles._current_level()
        before = dict(level.tiles)
        h.view.pan_by(QPointF(37, 11))
        image = paint(h)
        assert is_image(image.pixelColor(h.vp(0, 0)))
        assert all(level.tiles.get(k) is v for k, v in before.items()), "a pan re-renders nothing it had"
    finally:
        h.close()
