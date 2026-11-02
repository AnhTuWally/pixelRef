"""The canvas tile cache: busy views paint from cached tiles (ui.canvas.tiles)."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

from harness import APP, make
from PySide6.QtCore import QPointF

from pixelref.ui.canvas.tiles import PREFETCH, TILE_BYTES, tiles_mode


def tiled():
    h = make()
    h.view.TILE_MIN_ITEMS = 0  # tiles even for these small scenes
    return h


def paint(h):
    APP.processEvents()
    h.view.viewport().repaint()
    return h.view.render_frame()


@contextmanager
def progressive():
    """PIXELREF_TILE_PROGRESSIVE=1 (overview level, no synchronous rendering while zooming)."""
    old = os.environ.get("PIXELREF_TILE_PROGRESSIVE")
    os.environ["PIXELREF_TILE_PROGRESSIVE"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("PIXELREF_TILE_PROGRESSIVE", None)
        else:
            os.environ["PIXELREF_TILE_PROGRESSIVE"] = old


def still(h, quiet: float = 0.15) -> None:
    """Let a zoom finish: the tile cache refines (and builds its overview) only once the camera is still."""
    deadline = time.perf_counter() + 5
    while not h.view.camera_idle(quiet) and time.perf_counter() < deadline:
        APP.processEvents()
        time.sleep(0.01)


def refine(h) -> None:
    """Render everything the view wants at its zoom (visible tiles and the ring), as the idle timers would."""
    tiles = h.view.tiles
    still(h)
    for _ in range(500):
        tiles._settle.stop()
        tiles._idle_timer.stop()
        tiles._idle()
        level = tiles._current_level()
        if all(k in level.tiles for k in tiles._visible_keys(level, PREFETCH)):
            break
    tiles._settle.stop()
    tiles._idle_timer.stop()


def build_overview(h) -> None:
    """Build the overview to completion (what its idle timer does a slice at a time)."""
    tiles = h.view.tiles
    refine(h)
    for _ in range(2000):
        tiles._ov_timer.stop()
        tiles._settle.stop()
        tiles._idle_timer.stop()
        tiles._ov_planned = tiles._ov_changed = 0.0  # as if the waits (re-planning, a still scene) had passed
        tiles._overview_step()
        if tiles._ov_done:
            break
    tiles._ov_timer.stop()


class Spy:
    """Counts the tile runs rendered while it is installed."""

    def __init__(self, tiles) -> None:
        self.tiles, self.calls, self.orig = tiles, 0, tiles._render_run_now
        tiles._render_run_now = self

    def __call__(self, *args):
        self.calls += 1
        return self.orig(*args)

    def remove(self) -> None:
        del self.tiles._render_run_now


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
        still(h)  # progressive mode refines only once the zoom has stopped changing
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


def test_progressive_overview_is_built_when_idle_and_kept_beside_the_levels():
    with progressive():
        h = tiled()
        try:
            h.add_image("A", -100, -50)
            h.add_image("B", 1500, 900)
            paint(h)
            tiles = h.view.tiles
            assert tiles.overview is None, "nothing is built inside paint"
            build_overview(h)
            ov = tiles.overview
            assert ov is not None and ov.overview
            assert tiles.overview_pending() == 0 and len(ov.tiles) == len(tiles._ov_keys) >= 1
            assert all(lv is not ov for lv in tiles.levels.values()), "level eviction never sees it"
            assert tiles.overview_tile_count * TILE_BYTES < 128 * 1024 * 1024, "its memory is bounded"
            tiles.clear()  # a view filter change: the overview is rebuilt for what is shown now
            assert tiles.overview is None
        finally:
            h.close()


def test_progressive_empty_board_has_no_overview():
    with progressive():
        h = tiled()
        try:
            paint(h)
            h.view.tiles._plan_overview()
            assert h.view.tiles.overview is None and h.view.tiles._ov_done
        finally:
            h.close()


def test_progressive_zoom_shows_the_overview_and_renders_nothing_meanwhile():
    with progressive():
        h = tiled()
        try:
            h.add_image("A", -100, -50)
            h.add_image("B", 1500, 900)
            paint(h)
            build_overview(h)
            tiles = h.view.tiles
            tiles.levels.clear()  # no level to fall back on: only the overview can show the board
            spy = Spy(tiles)
            h.view.zoom_by(2.0, around_cursor=False)
            assert h.view.zooming()
            image = paint(h)
            assert spy.calls == 0, "no tile is rendered synchronously while the zoom changes"
            assert is_image(image.pixelColor(h.vp(0, 0))), "the overview stands in for the new level"
            spy.remove()
            refine(h)
            level = tiles._current_level()
            assert all(k in level.tiles for k in tiles._visible_keys(level)), "it is exact once the zoom has settled"
            assert is_image(paint(h).pixelColor(h.vp(0, 0)))
        finally:
            h.close()


def test_progressive_edit_during_a_zoom_is_in_the_overview_and_exact_afterwards():
    with progressive():
        h = tiled()
        try:
            h.add_image("A", -100, -50)
            h.add_image("B", 1500, 900)
            paint(h)
            build_overview(h)
            tiles = h.view.tiles
            h.view.zoom_by(1.5, around_cursor=False)
            h.app.canvas.translate_items([h.item("A")], 150.0, 0.0)  # A now spans x 50..250, on screen at 1.5x
            paint(h)
            ov = tiles.overview
            assert ov.stale and all(k in ov.tiles for k in tiles._ov_keys), "stale tiles stay: no hole in the overview"
            tiles._ov_changed = 0.0  # as if the scene had been still for a while
            refine(h)
            image = paint(h)
            assert not is_image(image.pixelColor(h.vp(-50, 0))), "the old place is gone"
            assert is_image(image.pixelColor(h.vp(150, 0))), "the moved item is where it now is"
            build_overview(h)
            assert not ov.stale and tiles.overview_pending() == 0, "the overview catches up when idle"
        finally:
            h.close()


def test_progressive_redoes_a_changed_tile_at_once_when_not_zooming():
    """An edit (or animation frame) must not show the old blurry overview for a moment: it re-renders now."""
    with progressive():
        h = tiled()
        try:
            h.add_image("A", -100, -50)
            h.add_image("B", 1500, 900)
            paint(h)
            build_overview(h)
            refine(h)
            assert not h.view.zooming()
            spy = Spy(h.view.tiles)
            h.app.canvas.translate_items([h.item("A")], 400.0, 0.0)
            image = paint(h)
            assert spy.calls > 0, "re-rendered inside the paint"
            assert is_image(image.pixelColor(h.vp(400, 0)))
            level = h.view.tiles._current_level()
            assert all(k not in level.tiles for k in level.dirty), "no tile is both present and marked changed"
            spy.remove()
        finally:
            h.close()


def test_progressive_zoom_is_on_by_default_and_the_user_can_turn_it_off():
    from pixelref.ui.canvas import tiles as tile_cache

    forced = os.environ.pop("PIXELREF_TILE_PROGRESSIVE", None)  # a developer's override must not decide this test
    h = tiled()
    try:
        action = h.app.actions.get("navigation.progressive_zoom")
        assert action is not None and action.isCheckable(), "a checkable View menu item"
        assert tile_cache.progressive_mode() and action.isChecked(), "on by default"
        h.add_image("A", -100, -50)
        paint(h)
        assert h.view.tiles.tile_count > 0
        assert h.run("navigation.progressive_zoom", False) is False
        assert not tile_cache.progressive_mode() and not action.isChecked()
        assert h.app.settings.value("plugins/navigation/progressive_zoom", None, bool) is False, "remembered"
        assert h.view.tiles.tile_count == 0 and h.view.tiles.overview is None, "the caches start over"
        assert is_image(paint(h).pixelColor(h.vp(0, 0))), "and the canvas draws as before"
        assert h.run("navigation.progressive_zoom") is True, "no argument: switch it"
        assert tile_cache.progressive_mode() and action.isChecked()
        assert h.app.settings.value("plugins/navigation/progressive_zoom", None, bool) is True
        os.environ["PIXELREF_TILE_PROGRESSIVE"] = "0"  # the environment variable wins over the setting
        assert not tile_cache.progressive_mode()
        assert h.run("navigation.progressive_zoom", True) is False, "and the command says what is in effect"
        del os.environ["PIXELREF_TILE_PROGRESSIVE"]
        assert tile_cache.progressive_mode(), "the setting (on) applies again"
    finally:
        h.run("navigation.progressive_zoom", True)
        if forced is None:
            os.environ.pop("PIXELREF_TILE_PROGRESSIVE", None)
        else:
            os.environ["PIXELREF_TILE_PROGRESSIVE"] = forced
        h.close()

