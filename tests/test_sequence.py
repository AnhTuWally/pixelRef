"""Image sequences: pattern parsing and grouping (pure Python), the sequence node,
playback through the playback plugin, drops, rescanning and EXR frames."""

from __future__ import annotations

import os
from pathlib import Path

from harness import APP, make, tmp_dir
from PySide6.QtCore import QMimeData, QPointF, QUrl
from PySide6.QtGui import QColor, QImage

from pixelref.builtins.image.source import DECODER
from pixelref.builtins.sequence import seqscan
from pixelref.builtins.sequence.items import SequenceItem
from pixelref.plugin_api import CommandError

COLORS = ["#ff0000", "#00ff00", "#0000ff", "#ffff00", "#00ffff", "#ff00ff", "#ffffff", "#808080", "#400000",
          "#004000", "#000040", "#404000"]


def _frames(folder: Path, frames: list[int], stem: str = "shot.", pad: int = 4, w: int = 48, h: int = 24) -> Path:
    for n in frames:
        image = QImage(w, h, QImage.Format_RGB32)
        image.fill(QColor(COLORS[n % len(COLORS)]))
        image.save(str(folder / f"{stem}{n:0{pad}d}.png"))
    return folder / f"{stem}{'#' * pad}.png"


def _shown_color(h, name: str) -> int:
    """The colour of the frame the item shows (decoded now if it must be)."""
    item = h.item(name)
    assert item.handle is not None
    return item.handle.full().toImage().pixel(1, 1) & 0xFFFFFF


def _expected(n: int) -> int:
    return QColor(COLORS[n % len(COLORS)]).rgb() & 0xFFFFFF


def _mime(*paths: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return mime


# ---- seqscan -----------------------------------------------------------------------------------


def test_seqscan_parses_and_normalizes_patterns():
    numbered = seqscan.parse("shot_v003.1001.exr")
    assert (numbered.prefix, numbered.frame, numbered.digits, numbered.suffix) == ("shot_v003.", 1001, "1001", ".exr")
    assert seqscan.parse("still.png") is None
    assert seqscan.normalize("shot.%04d.exr") == "shot.####.exr"
    assert seqscan.normalize("shot.%d.exr") == "shot.#.exr"
    assert seqscan.normalize("shot.@@@.exr") == "shot.###.exr"
    assert seqscan.normalize("shot.$F4.exr") == "shot.####.exr"
    assert seqscan.frame_path("shot.####.exr", 7) == "shot.0007.exr"
    assert seqscan.frame_path("f#.png", 120) == "f120.png"
    assert Path(seqscan.pattern_for("a/shot.0001.png")).name == "shot.####.png"
    assert Path(seqscan.pattern_for("a/shot.1001.png")).name == "shot.####.png"
    assert Path(seqscan.pattern_for("a/f12.png")).name == "f#.png"
    assert seqscan.is_pattern("x/shot.####.exr") and not seqscan.is_pattern("x/shot.0001.exr")


def test_seqscan_scans_and_groups():
    folder = tmp_dir()
    pattern = _frames(folder, [1, 2, 3, 5])
    for n in (8, 9, 10):
        (folder / f"f{n}.png").write_bytes(b"x")
    (folder / "still.png").write_bytes(b"x")
    (folder / "v2.png").write_bytes(b"x")
    info = seqscan.scan(str(pattern))
    assert info.frames == [1, 2, 3, 5] and info.missing() == [4] and info.missing(0, 6) == [0, 4, 6]
    assert seqscan.scan(str(folder / "f#.png")).frames == [8, 9, 10]
    groups, singles = seqscan.group(sorted(folder.iterdir()))
    assert sorted((Path(g.pattern).name, tuple(g.frames)) for g in groups) == [
        ("f#.png", (8, 9, 10)), ("shot.####.png", (1, 2, 3, 5))]
    assert sorted(p.name for p in singles) == ["still.png", "v2.png"]


# ---- Nodes ---------------------------------------------------------------------------------------


def test_import_sequence_from_one_frame_finds_the_range():
    h = make()
    try:
        folder = tmp_dir()
        _frames(folder, [1001, 1002, 1003, 1004, 1006])
        name = h.run("sequence.import_sequence", (folder / "shot.1003.png").as_posix(), [0.0, 0.0])
        h.refresh()
        attrs = h.attrs(name)
        assert name.startswith("shot")
        assert Path(attrs["path"]).name == "shot.####.png"
        assert (attrs["first"], attrs["last"]) == (1001, 1006)
        assert "asset" not in attrs, "sequences are always linked"
        item = h.item(name)
        assert isinstance(item, SequenceItem)
        assert item.frame_count() == 6 and item.current_frame() == 1
        assert item.image_size().width() == 48
        assert h.run("sequence.info", name)["missing"] == [1005]
        same = h.run("sequence.import_sequence", str(folder / "shot.%04d.png"), [300.0, 0.0])
        assert h.attr(same, "first") == 1001
        try:
            h.run("sequence.import_sequence", str(folder / "nothing.####.png"))
            raise AssertionError("no frames should be refused")
        except CommandError as exc:
            assert "No frames" in str(exc)
    finally:
        h.close()


def test_sequence_frames_step_and_hold_missing():
    h = make()
    try:
        folder = tmp_dir()
        _frames(folder, [1, 2, 3, 5])
        name = h.run("sequence.import_sequence", str(folder / "shot.####.png"), [0.0, 0.0])
        h.refresh()
        item = h.item(name)
        assert _shown_color(h, name) == _expected(1)
        assert h.run("playback.go_to_frame", 3, [name]) is not None
        assert item.current_frame() == 3 and _shown_color(h, name) == _expected(3)
        item.go_to_frame(4)  # frame 4 has no file: frame 3 is held
        assert _shown_color(h, name) == _expected(3)
        item.step_frame(1)
        assert item.current_frame() == 5 and _shown_color(h, name) == _expected(5)
        item.step_frame(1)  # wraps around
        assert item.current_frame() == 1
        assert item.frame_label(4) == "4"
        h.run("set_attr", name, "missing", "black")
        h.refresh()
        item = h.item(name)
        item.go_to_frame(4)
        assert item._black
    finally:
        h.close()


def test_sequence_plays_with_the_clock():
    import time

    h = make()
    try:
        folder = tmp_dir()
        _frames(folder, list(range(1, 11)))
        name = h.run("sequence.import_sequence", str(folder / "shot.####.png"), [0.0, 0.0])
        h.run("set_attr", name, "fps", 50.0)
        h.refresh()
        item = h.item(name)
        h.select([name])
        h.run("playback.play")
        assert item.is_playing()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and item.current_frame() < 4:
            APP.processEvents()
            DECODER.wait()
            time.sleep(0.005)
        assert item.current_frame() >= 4, item.current_frame()
        h.run("playback.pause")
        assert not item.is_playing()
        frame = item.current_frame()
        h.run("set_attr", name, "loop", False)  # an edit rebuilds the item: the paused frame stays
        h.refresh()
        assert h.item(name).current_frame() == frame
    finally:
        h.close()


def test_sequence_filmstrip_arrives_in_the_background():
    h = make()
    try:
        folder = tmp_dir()
        _frames(folder, list(range(1, 21)))
        name = h.run("sequence.import_sequence", str(folder / "shot.####.png"), [0.0, 0.0])
        h.refresh()
        item = h.item(name)
        heard = []
        item.add_filmstrip_listener(heard.append)
        first = item.filmstrip(6)
        DECODER.wait()
        APP.processEvents()
        assert len(item.filmstrip(6)) == 6 and len(first) <= 6
    finally:
        h.close()


def test_rescan_picks_up_new_frames():
    h = make()
    try:
        folder = tmp_dir()
        _frames(folder, [1, 2, 3])
        name = h.run("sequence.import_sequence", str(folder / "shot.####.png"), [0.0, 0.0])
        _frames(folder, [4, 5])
        assert h.run("sequence.rescan", [name]) == [name]
        assert h.attr(name, "last") == 5
        assert h.run("sequence.rescan", [name]) == []
    finally:
        h.close()


def test_refresh_frames_extends_the_range_and_shows_frames_that_appeared():
    h = make()
    try:
        folder = tmp_dir()
        _frames(folder, [1, 2, 4])  # frame 3 not rendered yet
        name = h.run("sequence.import_sequence", str(folder / "shot.####.png"), [0.0, 0.0])
        h.refresh()
        h.item(name).go_to_frame(3)
        assert _shown_color(h, name) == _expected(2), "3 is missing: 2 is held"
        _frames(folder, [3, 5, 6])
        h.select([name])
        assert h.trigger("sequence.rescan") is not False  # Refresh Frames (Shift+F5)
        h.refresh()
        assert h.attr(name, "last") == 6
        item = h.item(name)
        assert item.frames_on_disk() == [1, 2, 3, 4, 5, 6]
        item.go_to_frame(3)
        assert _shown_color(h, name) == _expected(3), "the new frame inside the range shows"
        # With nothing selected, every sequence on the board is refreshed.
        _frames(folder, [7])
        h.select([])
        assert h.run("sequence.refresh_frames") == 1
        assert h.attr(name, "last") == 7
    finally:
        h.close()


def test_sequence_inspector_shows_files_and_keeps_the_attributes():
    h = make()
    try:
        folder = tmp_dir()
        _frames(folder, [1, 2, 4])
        name = h.run("sequence.import_sequence", str(folder / "shot.####.png"), [0.0, 0.0])
        h.refresh()
        h.select([name])
        APP.processEvents()
        section = h.section("sequence_files")
        assert section is not None and "1 missing" in section.files.text()
        assert h.section("attributes") is not None, "fps, loop, range… stay editable"
    finally:
        h.close()


def test_dropping_frames_makes_one_sequence_and_keeps_stills():
    h = make()
    try:
        folder = tmp_dir()
        _frames(folder, [1, 2, 3, 4])
        image = QImage(20, 20, QImage.Format_RGB32)
        image.fill(QColor("#123456"))
        image.save(str(folder / "reference.png"))
        before = set(h.scene.nodes)
        assert h.drop(QPointF(0.0, 0.0), _mime(folder))
        added = [n for n in h.scene.nodes if n not in before]
        types = sorted(h.type_of(n) for n in added)
        assert types == ["image", "sequence"], types
        still = next(n for n in added if h.type_of(n) == "image")
        assert h.attr(still, "asset"), "an unnumbered still follows the embed setting"
        # Off: frames come in as separate images, linked (numbered frames always are).
        h.run("sequence.set_detect", False)
        try:
            before = set(h.scene.nodes)
            h.drop(QPointF(0.0, 500.0), _mime(*sorted(folder.glob("shot.*.png"))))
            added = [n for n in h.scene.nodes if n not in before]
            assert sorted(h.type_of(n) for n in added) == ["image"] * 4
            assert all(h.attr(n, "path") and not h.attr(n, "asset") for n in added)
        finally:
            h.run("sequence.set_detect", True)
        # One frame alone is just an image, linked too.
        before = set(h.scene.nodes)
        h.drop(QPointF(0.0, 900.0), _mime(folder / "shot.0001.png"))
        alone = [n for n in h.scene.nodes if n not in before]
        assert [h.type_of(n) for n in alone] == ["image"]
        assert h.attr(alone[0], "path") and not h.attr(alone[0], "asset")
    finally:
        h.close()


def test_sequence_paths_are_relative_to_the_saved_scene():
    h = make()
    try:
        folder = tmp_dir()
        frames = folder / "renders"
        frames.mkdir()
        _frames(frames, [1, 2])
        name = h.run("sequence.import_sequence", str(frames / "shot.####.png"), [0.0, 0.0])
        h.run("save_scene", str(folder / "board.pxref"))
        assert h.attr(name, "path") == "renders/shot.####.png"
        h.run("open_scene", str(folder / "board.pxref"))
        h.refresh()
        assert not h.item(name).is_missing()
    finally:
        h.close()


def test_missing_sequence_shows_a_placeholder():
    h = make()
    try:
        name = h.create("sequence", "Gone", path=os.path.join(str(tmp_dir()), "gone.####.exr"),
                        first=1, last=10, translate=[0.0, 0.0])
        h.refresh()
        item = h.item(name)
        assert isinstance(item, SequenceItem) and item.is_missing()
        assert item.boundingRect().width() > 0
        item.play()
        assert not item.is_playing()
    finally:
        h.close()


def test_exr_sequence_frames_use_the_exr_view():
    import importlib.util

    if not all(importlib.util.find_spec(m) for m in ("OpenEXR", "numpy")):
        return
    import numpy as np
    import OpenEXR

    from test_exr import make_exr

    h = make_exr()
    try:
        folder = tmp_dir()
        for n in (1, 2, 3):
            rgba = np.zeros((16, 32, 4), "f")
            rgba[..., 0] = 0.18 * n
            rgba[..., 3] = 1.0
            OpenEXR.File({"type": OpenEXR.scanlineimage}, {"RGBA": rgba}).write(str(folder / f"r.{n:04d}.exr"))
        name = h.run("sequence.import_sequence", str(folder / "r.0002.exr"), [0.0, 0.0])
        h.refresh()
        assert (h.attr(name, "first"), h.attr(name, "last")) == (1, 3)
        assert h.run("exr.layers", name) == [""]
        assert (_shown_color(h, name) >> 16) == 118
        h.run("exr.set_view", [name], exposure=1.0)
        h.refresh()
        assert (_shown_color(h, name) >> 16) == 162
        h.item(name).go_to_frame(2)  # 0.36 linear at +1 stop = 0.72
        assert (_shown_color(h, name) >> 16) > 162
    finally:
        h.close()
