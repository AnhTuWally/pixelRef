"""Movie nodes: always linked by file reference (never embedded), dropped and inserted
like images, and missing-file handling."""

from __future__ import annotations

import base64
from typing import Any

from harness import APP, TMP, make, tmp_dir
from PySide6.QtCore import QMimeData, QPointF, QUrl
from pixelref.builtins.movie.items import MovieItem
from pixelref.plugin_api import CommandError

# A real, tiny (32x24, 5 fps, 1s) H.264 .mp4 made with
# `ffmpeg -f lavfi -i testsrc=size=32x24:rate=5:duration=1 -pix_fmt yuv420p -c:v libx264
#  -preset ultrafast -crf 40 -an small.mp4`, so decoding/seeking/thumbnailing can be
# tested against a file Qt Multimedia can actually play, not just a dummy byte string.
_TINY_MP4_B64 = """
AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAAIZnJlZQAAA+xtZGF0AAACUwYF//9P3EXpvebZSLeWLNgg2SPu73gy
NjQgLSBjb3JlIDE2NSByMzIyMyAwNDgwY2IwIC0gSC4yNjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcHlsZWZ0IDIwMDMtMjAyNSAt
IGh0dHA6Ly93d3cudmlkZW9sYW4ub3JnL3gyNjQuaHRtbCAtIG9wdGlvbnM6IGNhYmFjPTAgcmVmPTEgZGVibG9jaz0wOjA6MCBh
bmFseXNlPTA6MCBtZT1kaWEgc3VibWU9MCBwc3k9MSBwc3lfcmQ9MS4wMDowLjAwIG1peGVkX3JlZj0wIG1lX3JhbmdlPTE2IGNo
cm9tYV9tZT0xIHRyZWxsaXM9MCA4eDhkY3Q9MCBjcW09MCBkZWFkem9uZT0yMSwxMSBmYXN0X3Bza2lwPTEgY2hyb21hX3FwX29m
ZnNldD0wIHRocmVhZHM9MSBsb29rYWhlYWRfdGhyZWFkcz0xIHNsaWNlZF90aHJlYWRzPTAgbnI9MCBkZWNpbWF0ZT0xIGludGVy
bGFjZWQ9MCBibHVyYXlfY29tcGF0PTAgY29uc3RyYWluZWRfaW50cmE9MCBiZnJhbWVzPTAgd2VpZ2h0cD0wIGtleWludD0yNTAg
a2V5aW50X21pbj01IHNjZW5lY3V0PTAgaW50cmFfcmVmcmVzaD0wIHJjPWNyZiBtYnRyZWU9MCBjcmY9NDAuMCBxY29tcD0wLjYw
IHFwbWluPTAgcXBtYXg9NjkgcXBzdGVwPTQgaXBfcmF0aW89MS40MCBhcT0wAIAAAAEWZYiEOgxgAgW2YABv3YBe9G+ABG3/K8Bn
8gQj3MOIR7n4FgAEwIGSAbBtjFLZywcsABFK5hw0/6EQTAiBMDoEwGMAH7TQAIuIMAOL3gQMmY9f4APtVbIgz93yyAbEHIBsA4AB
MAAQFQKYBmELBp3COWHlgdAmABOvdIfKm4dAmBECYCzgD+qhJAxeiAb5tyB7rVkOsTOloqM4CEyIATL8yeBMkEM1zw5DNc/Ch0FC
hgIwjgYnS0ADdItJjAPbZEY37SOAuTkQBoLc8LOAC5NPRMAYOoQLmpHhMig1I4EzBCAxmEgYvh8wYSs+VXcLEAmCq7g4gEwEEmDB
AojngAEWgOP54spA6uFgTpAA11LUhodOQAEgtyAAAAAIQZogyfXX1YwAAAASQZpCnwQxplrGWi/4IZcnyQbAAAAAJEGaYKnwzHFi
ZinACzgMmRYrilFSfhmOsqlbKKQAjxkXXFURjgAAACVBmoDpcmr/CsNzeeDOqHHqJ6i9+k9/CtC9N96VHlYJKeFamLX8AAADN21v
b3YAAABsbXZoZAAAAAAAAAAAAAAAAAAAA+gAAAPoAAEAAAEAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAA
AAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIAAAJidHJhawAAAFx0a2hkAAAAAwAAAAAAAAAAAAAAAQAAAAAAAAPo
AAAAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAQAAAAAAgAAAAGAAAAAAAJGVkdHMAAAAcZWxz
dAAAAAAAAAABAAAD6AAAAAAAAQAAAAAB2m1kaWEAAAAgbWRoZAAAAAAAAAAAAAAAAAAAKAAAACgAVcQAAAAAAC1oZGxyAAAAAAAA
AAB2aWRlAAAAAAAAAAAAAAAAVmlkZW9IYW5kbGVyAAAAAYVtaW5mAAAAFHZtaGQAAAABAAAAAAAAAAAAAAAkZGluZgAAABxkcmVm
AAAAAAAAAAEAAAAMdXJsIAAAAAEAAAFFc3RibAAAALlzdHNkAAAAAAAAAAEAAACpYXZjMQAAAAAAAAABAAAAAAAAAAAAAAAAAAAA
AAAgABgASAAAAEgAAAAAAAAAARVMYXZjNjIuMTEuMTAwIGxpYngyNjQAAAAAAAAAAAAAABj//wAAAC9hdmNDAULACv/hABdnQsAK
2iX5cBEAAAMAAQAAAwAKDxImoAEABWjOA5yAAAAAEHBhc3AAAAABAAAAAQAAABRidHJ0AAAAAAAAHyAAAAAAAAAAGHN0dHMAAAAA
AAAAAQAAAAUAAAgAAAAAFHN0c3MAAAAAAAAAAQAAAAEAAAAcc3RzYwAAAAAAAAABAAAAAQAAAAUAAAABAAAAKHN0c3oAAAAAAAAA
AAAAAAUAAANxAAAADAAAABYAAAAoAAAAKQAAABRzdGNvAAAAAAAAAAEAAAAwAAAAYXVkdGEAAABZbWV0YQAAAAAAAAAhaGRscgAA
AAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAAAAAsaWxzdAAAACSpdG9vAAAAHGRhdGEAAAABAAAAAExhdmY2Mi4zLjEwMA==
""".replace("\n", "")


def _dummy_video(name: str = "Clip.mp4"):
    path = TMP / name
    path.write_bytes(b"not a real video, just a placeholder file")
    return path


def _tiny_real_video(name: str = "Tiny.mp4"):
    """A real, 5-frame, 1-second decodable .mp4 (see ``_TINY_MP4_B64`` above)."""
    path = TMP / name
    path.write_bytes(base64.b64decode(_TINY_MP4_B64))
    return path


def _file_mime(path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def _wait_until(predicate, attempts: int = 200, interval_ms: int = 10) -> bool:
    """Pump the event loop until ``predicate()`` is true (Qt Multimedia decodes async)."""
    import time

    for _ in range(attempts):
        if predicate():
            return True
        APP.processEvents()
        time.sleep(interval_ms / 1000.0)
    return predicate()


def test_movie_node_type_is_linked_only():
    h = make()
    try:
        assert "movie" in h.run("node_types")
        path = _dummy_video()
        name = h.run("movie.import_movie", path.as_posix(), [10.0, 20.0])
        h.refresh()
        attrs = h.attrs(name)
        assert attrs["path"], "a movie is a linked file reference"
        assert "asset" not in attrs, "unlike images, movies have no embedded form"
        assert attrs["muted"] is False
        assert attrs["loop"] is True
        assert attrs["volume"] == 1.0
        assert attrs["autoplay"] is True
        item = h.item(name)
        assert isinstance(item, MovieItem)
        # The dummy file isn't a real video — Qt Multimedia may reject it asynchronously
        # (reported as "Can't play" rather than "Missing"); what matters here is that the
        # node and its item were created from a plain file reference, not embedded bytes.
    finally:
        h.close()


def test_movie_import_errors_for_missing_or_unsupported_files():
    h = make()
    try:
        try:
            h.run("movie.import_movie", str(TMP / "nope.mp4"), [0.0, 0.0])
            raise AssertionError("should have raised")
        except CommandError as exc:
            assert "No such movie file" in str(exc)
        text_file = TMP / "notes.txt"
        text_file.write_text("hello")
        try:
            h.run("movie.import_movie", text_file.as_posix(), [0.0, 0.0])
            raise AssertionError("should have raised")
        except CommandError as exc:
            assert "Not a supported movie file" in str(exc)
    finally:
        h.close()


def test_movie_missing_file_shows_a_placeholder_not_a_crash():
    h = make()
    try:
        name = h.create("movie", "Gone", path="no-such-clip.mp4", translate=[0.0, 0.0])
        item = h.item(name)
        assert isinstance(item, MovieItem)
        assert item.is_missing()
        assert item.boundingRect().width() > 0 and item.boundingRect().height() > 0
    finally:
        h.close()


def test_dropping_a_video_file_creates_a_movie_node():
    h = make()
    try:
        path = _dummy_video("Dropped.mkv")
        before = set(h.scene.nodes)
        created = h.drop(QPointF(50.0, 60.0), _file_mime(path))
        assert created, "the movie ingest handler should have claimed the drop"
        new_nodes = [n for n in h.scene.nodes if n not in before]
        assert len(new_nodes) == 1
        assert h.type_of(new_nodes[0]) == "movie"
        assert h.attr(new_nodes[0], "path")
    finally:
        h.close()


def test_dropping_an_unrelated_file_does_not_create_a_movie_node():
    h = make()
    try:
        path = tmp_dir() / "README.txt"
        path.write_text("not a video")
        before = set(h.scene.nodes)
        h.drop(QPointF(0.0, 0.0), _file_mime(path))
        new_nodes = [n for n in h.scene.nodes if n not in before]
        assert all(h.type_of(n) != "movie" for n in new_nodes), "a .txt drop is not a movie"
    finally:
        h.close()


def test_real_movie_decodes_and_steps_frame_by_frame():
    h = make()
    try:
        path = _tiny_real_video()
        name = h.create("movie", "Clip", path=path.as_posix(), translate=[0.0, 0.0])
        item = h.item(name)
        assert _wait_until(lambda: item.duration_ms() > 0), "the real clip should decode"
        assert not item.is_missing()
        assert item.duration_ms() == 1000
        assert item.frame_count() == 5
        assert item.is_playing(), "autoplay is on by default"
        h.select([name])
        assert h.run("playback.go_to_frame", 3) == 3
        assert _wait_until(lambda: item.current_frame() == 3)
        assert h.run("playback.next_frame") == 4
        assert h.run("playback.prev_frame") == 3
        assert h.run("playback.first_frame") == 1
        assert h.run("playback.last_frame") == 5
        assert not item.is_playing(), "stepping pauses, like it does for animated images"
        assert h.run("playback.toggle") is True and item.is_playing()
        assert h.run("playback.toggle") is False and not item.is_playing()
    finally:
        h.close()


def test_real_movie_updates_the_live_frame_display_and_playhead_while_playing():
    """Regression test: a playing movie must push its own frame changes into the playback
    service (via the ``movie.started`` hook), not just report them when explicitly stepped —
    otherwise the frame label, scrub slider and filmstrip playhead never move during playback."""
    h = make()
    try:
        path = _tiny_real_video()
        name = h.create("movie", "Clip", path=path.as_posix(), translate=[0.0, 0.0])
        item = h.item(name)
        assert _wait_until(lambda: item.duration_ms() > 0)
        assert item.is_playing()
        h.select([name])
        section = h.section("playback")
        seen_frames: list[int] = []
        h.service("playback").frame_changed.connect(lambda n: seen_frames.append(item.current_frame()) if n == name
                                                     else None)
        assert _wait_until(lambda: len(seen_frames) >= 2 and seen_frames[-1] > 1), (
            "the service's frame_changed signal should fire repeatedly while the movie plays, "
            "not only when a playback.* command is run"
        )
        assert _wait_until(lambda: section.frame_label.text() not in ("", "1 / 5")), (
            "the Attribute Editor's frame label should update live too"
        )
    finally:
        h.close()


def test_real_movie_filmstrip_decodes_thumbnails_in_the_background():
    h = make()
    try:
        path = _tiny_real_video()
        name = h.create("movie", "Clip", path=path.as_posix(), translate=[0.0, 0.0])
        item = h.item(name)
        seen: list[Any] = []
        item.add_filmstrip_listener(lambda i: seen.append(i))
        first = item.filmstrip()
        assert first == [], "nothing decoded yet on the first call"
        assert _wait_until(lambda: len(item.filmstrip()) == 12), "12 evenly spaced thumbnails"
        frames = item.filmstrip()
        assert all(not pixmap.isNull() for pixmap in frames)
        assert len(seen) == 12, "the listener fires once per thumbnail"
    finally:
        h.close()


def test_movie_filmstrip_appears_in_the_quick_editor_bar():
    from PySide6.QtCore import QSettings
    from pixelref.builtins.quick_edit.ui import QuickBar

    QSettings().remove("plugins/quick_edit")
    h = make()
    try:
        path = _tiny_real_video()
        name = h.create("movie", "Clip", path=path.as_posix(), translate=[0.0, 0.0])
        item = h.item(name)
        assert _wait_until(lambda: item.duration_ms() > 0)
        h.select([name])
        h.run("playback.pause")
        bars = [b for b in h.view.findChildren(QuickBar) if b.isVisible()]
        assert bars, "a movie gets the quick editor bar too, same as a GIF"
        transport = bars[0].sections["playback"][1]
        assert transport.isVisible()
        strip = transport.filmstrip
        assert strip.isVisible()
        assert _wait_until(lambda: len(item.filmstrip()) == 12)
        strip._scrub_to(0.0)
        assert item.current_frame() == 1
        strip._scrub_to(float(strip.width()) - 1.0)
        assert item.current_frame() == 5
    finally:
        QSettings().remove("plugins/quick_edit")
        h.close()
