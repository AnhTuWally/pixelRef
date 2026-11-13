"""Linked files (core.links): fingerprints, ${NAME} roots, the resolve chain and the save-time pass.

(``test_links.py`` is about the links/arrows between elements.)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from harness import tmp_dir

from pixelref.core import links
from pixelref.core.settings import MemorySettings


def headless(settings=None):
    from pixelref.core.session import Session

    return Session(settings=settings if settings is not None else MemorySettings(), make_current=False).start()


def _file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_fingerprint_is_stable_and_sees_changes():
    folder = tmp_dir()
    data = bytearray(os.urandom(300_000))
    path = _file(folder / "clip.mp4", bytes(data))
    first = links.fingerprint(path)
    assert first == links.fingerprint(path) == {"size": 300_000, "qh": first["qh"]}
    copy = _file(folder / "other" / "renamed.mp4", bytes(data))
    assert links.same_content(links.fingerprint(copy), first)  # the name does not matter
    data[-1] ^= 0xFF
    path.write_bytes(bytes(data))
    os.utime(path, ns=(1, 1))  # a different mtime: the cache must not answer
    assert not links.same_content(links.fingerprint(path), first)
    _file(path, bytes(data[:-1]))
    assert links.fingerprint(path)["size"] == 299_999
    assert links.fingerprint(folder / "nothing.mp4") is None
    assert links.full_hash(copy) != links.full_hash(path)


def test_named_root_and_environment_variable_tokens():
    session = headless()
    try:
        library = tmp_dir()
        clip = _file(library / "char" / "robot" / "clip.mp4", b"robot clip")
        session.links.set_root("LIB", library)
        board = tmp_dir()
        session.scene_path = board / "board.pxref"
        assert session.to_scene_path(clip) == "${LIB}/char/robot/clip.mp4"
        assert session.resolve_path("${LIB}/char/robot/clip.mp4") == Path(library.as_posix()) / "char/robot/clip.mp4"
        assert session.resolve_path("${LIB}/char/robot/clip.mp4").is_file()

        os.environ["PIXELREF_TEST_JOB"] = str(library)
        try:
            assert session.resolve_path("${PIXELREF_TEST_JOB}/char/robot/clip.mp4").is_file()
            session.scene.create_node("movie", "Clip", attrs={"path": "${PIXELREF_TEST_JOB}/char/robot/clip.mp4"})
            session.save_file(board / "board.pxref")
            # Only named roots are written automatically; a working variable the user wrote is kept.
            assert session.scene.nodes["Clip"].attrs["path"] == "${PIXELREF_TEST_JOB}/char/robot/clip.mp4"
            assert session.scene.sections["links"]["${PIXELREF_TEST_JOB}/char/robot/clip.mp4"]["size"] == 10
        finally:
            del os.environ["PIXELREF_TEST_JOB"]
        # Unset: the stored value stays as it is (it works where the variable is set).
        session.save_file(board / "board.pxref")
        assert session.scene.nodes["Clip"].attrs["path"] == "${PIXELREF_TEST_JOB}/char/robot/clip.mp4"
        assert not session.resolve_path("${PIXELREF_TEST_JOB}/char/robot/clip.mp4").exists()
    finally:
        session.shutdown()


def test_unset_variable_falls_through_to_this_machines_memory():
    settings = MemorySettings()
    session = headless(settings)
    try:
        clip = _file(tmp_dir() / "clip.mp4", b"the clip, wherever it is")
        value = "${PIXELREF_TEST_UNSET_ROOT}/shots/clip.mp4"
        session.scene.create_node("movie", "Clip", attrs={"path": value})
        session.scene.sections["links"] = {value: links.fingerprint(clip)}
        assert not session.resolve_path(value).exists()
        session.links.remember(links.fingerprint(clip), clip)
        assert session.resolve_path(value) == clip
        board = tmp_dir()
        session.save_file(board / "board.pxref")  # the healed path is written at save time
        assert session.scene.nodes["Clip"].attrs["path"] == clip.as_posix()
    finally:
        session.shutdown()


def test_a_same_named_file_with_other_content_is_never_substituted():
    session = headless()
    try:
        clip = _file(tmp_dir() / "clip.mp4", b"original content")
        fp = links.fingerprint(clip)
        session.scene.create_node("movie", "Clip", attrs={"path": "gone/clip.mp4"})
        session.scene.sections["links"] = {"gone/clip.mp4": fp}
        session.links.remember(fp, clip)
        assert session.resolve_path("gone/clip.mp4") == clip
        session.links.forget()
        _file(clip, b"other content!!!")  # same name, same size, different bytes
        assert session.resolve_path("gone/clip.mp4") != clip
        result = session.run("package.find_missing", str(clip.parent))
        assert result["relinked"] == 0 and len(result["differs"]) == 1
        assert session.scene.nodes["Clip"].attrs["path"] == "gone/clip.mp4"
    finally:
        session.shutdown()


def test_a_changed_file_at_its_path_is_still_used_and_flagged():
    session = headless()
    try:
        board = tmp_dir()
        clip = _file(board / "clip.mp4", b"version one")
        session.scene.create_node("movie", "Clip", attrs={"path": clip.as_posix()})
        session.save_file(board / "board.pxref")
        assert session.links.status("clip.mp4") == "ok"
        _file(clip, b"version two, longer")
        assert session.resolve_path("clip.mp4") == board / "clip.mp4"
        assert session.links.status("clip.mp4") == "changed"
        session.save_file(board / "board.pxref")  # the record follows the file
        assert session.links.status("clip.mp4") == "ok"
    finally:
        session.shutdown()


def test_save_relativizes_a_movie_path_and_writes_links():
    session = headless()
    try:
        board = tmp_dir()
        clip = _file(board / "media" / "clip.mp4", b"\x00\x00\x00\x18ftypmp42" + b"x" * 100)
        name = session.run("movie.import_movie", str(clip))
        assert session.scene.nodes[name].attrs["path"] == clip.as_posix()  # unsaved board: absolute
        saved = session.save_file(board / "board.pxref")
        assert session.scene.nodes[name].attrs["path"] == "media/clip.mp4"
        data = json.loads(saved.read_text(encoding="utf-8"))
        assert data["links"]["media/clip.mp4"] == links.fingerprint(clip)
        elsewhere = tmp_dir()
        session.save_file(elsewhere / "copy.pxref")  # outside the new folder: absolute again
        assert Path(session.scene.nodes[name].attrs["path"]) == clip.resolve()
        assert list(json.loads((elsewhere / "copy.pxref").read_text(encoding="utf-8"))["links"]) == [
            session.scene.nodes[name].attrs["path"]]
    finally:
        session.shutdown()


def test_sequence_records_its_first_frame():
    session = headless()
    try:
        board = tmp_dir()
        for frame in (1, 2, 3):
            _file(board / "renders" / f"shot.{frame:04d}.png", f"frame {frame}".encode())
        session.scene.create_node("sequence", "Shot", attrs={"path": (board / "renders" / "shot.####.png").as_posix(),
                                                              "first": 1, "last": 3})
        session.save_file(board / "board.pxref")
        record = session.scene.sections["links"]["renders/shot.####.png"]
        assert record["frames"] == 3 and record["frame"] == 1
        assert record["first"] == links.fingerprint(board / "renders" / "shot.0001.png")
    finally:
        session.shutdown()


def test_board_path_variables_resolve_tokens_search_and_save():
    session = headless()
    try:
        windows, linux = tmp_dir(), tmp_dir()  # the same drive as two systems call it
        clip = _file(linux / "shots" / "clip.mp4", b"studio clip")
        board = tmp_dir()
        session.scene_path = board / "board.pxref"
        assert links.parse_variables("STUDIO_DRIVE = W:\\\n$LINUX_PATH = /mnt/drive; ${C}='x y'") == {
            "STUDIO_DRIVE": "W:/", "LINUX_PATH": "/mnt/drive", "C": "x y"}

        assert session.run("package.set_variables", f"STUDIO_DRIVE = {windows}\nLINUX_PATH = {linux}") == {
            "STUDIO_DRIVE": windows.as_posix(), "LINUX_PATH": linux.as_posix()}
        assert session.run("package.variables") == session.links.variables()
        # ${NAME} means the board's folder ...
        assert session.resolve_path("${LINUX_PATH}/shots/clip.mp4").is_file()
        # ... and a file that is not below the variable it was saved with is found below another:
        session.scene.create_node("movie", "Token", attrs={"path": "${STUDIO_DRIVE}/shots/clip.mp4"})
        session.scene.create_node("movie", "Full", attrs={"path": f"{windows.as_posix()}/shots/clip.mp4"})
        session.scene.create_node("movie", "Relative", attrs={"path": "shots/clip.mp4"})
        for name in ("Token", "Full", "Relative"):
            found = session.resolve_path(session.scene.nodes[name].attrs["path"])
            assert found == Path(linux.as_posix()) / "shots/clip.mp4" and found.is_file(), name
        assert not session.resolve_path("${STUDIO_DRIVE}/shots/other.mp4").exists()
        assert session.resolve_path("shots/other.mp4") == board / "shots/other.mp4"  # missing: as stored

        # A file beside the board wins over the search.
        beside = _file(board / "shots" / "clip.mp4", b"beside")
        assert session.resolve_path("shots/clip.mp4") == board / "shots/clip.mp4"
        beside.unlink()

        # New links below a variable's folder are stored as ${NAME}/…; a working token is kept on save.
        assert session.to_scene_path(clip) == "${LINUX_PATH}/shots/clip.mp4"
        session.save_file(board / "board.pxref")
        attrs = {name: session.scene.nodes[name].attrs["path"] for name in ("Token", "Full", "Relative")}
        assert attrs == {"Token": "${STUDIO_DRIVE}/shots/clip.mp4", "Full": "${LINUX_PATH}/shots/clip.mp4",
                         "Relative": "${LINUX_PATH}/shots/clip.mp4"}, attrs
        data = json.loads((board / "board.pxref").read_text(encoding="utf-8"))
        assert data["plugins"]["path_search"] == {"vars": session.links.variables()}

        # The machine's own environment variable of the same name wins over the board's value.
        elsewhere = tmp_dir()
        _file(elsewhere / "only_here.mp4", b"x")
        os.environ["STUDIO_DRIVE"] = str(elsewhere)
        try:
            assert session.resolve_path("${STUDIO_DRIVE}/only_here.mp4").is_file()
        finally:
            del os.environ["STUDIO_DRIVE"]

        for bad in ("no equals sign", "9BAD = x", "EMPTY ="):
            try:
                session.run("package.set_variables", bad)
            except Exception:  # noqa: BLE001
                pass
            else:
                raise AssertionError(f"accepted: {bad}")
        session.run("package.set_variables", "")
        assert session.links.variables() == {} and session.scene.get_plugin_data("path_search") is None
    finally:
        session.shutdown()
