"""Save Package, Verify Package and Find Missing Files (builtins/package), headless."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from harness import make, make_png, tmp_dir

from pixelref.core import links
from pixelref.core.settings import MemorySettings
from pixelref.plugin_api import CommandError


def headless(settings=None):
    from pixelref.core.session import Session

    return Session(settings=settings if settings is not None else MemorySettings(), make_current=False).start()


def _file(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _gltf(folder: Path) -> Path:
    """robot.gltf with an external buffer (robot.bin) and texture (textures/robot_diffuse.png)."""
    _file(folder / "robot.bin", bytes(range(48)))
    shutil.copy(make_png("robot_diffuse.png", 4, 4), _file(folder / "textures" / "robot_diffuse.png", b""))
    doc = {"asset": {"version": "2.0"}, "buffers": [{"uri": "robot.bin", "byteLength": 48}],
           "images": [{"uri": "textures/robot_diffuse.png"}], "textures": [{"source": 0}]}
    return _file(folder / "robot.gltf", json.dumps(doc).encode())


def _board(session) -> dict[str, Path]:
    """A board linking files from several folders outside the scene's: an image, a movie, a
    3-frame sequence and a glTF model with its buffer and texture."""
    sources = tmp_dir()
    image = shutil.copy(make_png("ref.png", 20, 10), _file(sources / "refs" / "ref.png", b""))
    clip = _file(sources / "Downloads" / "clip.mp4", b"\x00\x00\x00\x18ftypmp42" + os.urandom(2000))
    for frame in (1, 2, 3):
        shutil.copy(make_png(f"f{frame}.png", 8, 8, color=["#ff0000", "#00ff00", "#0000ff"][frame - 1]),
                    _file(sources / "sh010" / f"shot.{frame:04d}.png", b""))
    model = _gltf(sources / "robot")
    scene = session.scene
    scene.create_node("image", "Ref", attrs={"path": Path(image).as_posix()})
    scene.create_node("movie", "Clip", attrs={"path": clip.as_posix()})
    scene.create_node("sequence", "Shot", attrs={"path": (sources / "sh010" / "shot.####.png").as_posix(),
                                                 "first": 1, "last": 3})
    scene.create_node("model3d", "Robot", attrs={"path": model.as_posix()})
    board = tmp_dir()
    session.save_file(board / "Board.pxref")
    return {"sources": sources, "board": board / "Board.pxref", "clip": clip, "model": model}


def _all_found(session) -> bool:
    return not session.run("package.missing")


def test_companion_files_of_models():
    from pixelref.builtins.model3d.loader import companion_files

    folder = tmp_dir()
    model = _gltf(folder)
    names = sorted(p.relative_to(folder.resolve()).as_posix() for p in companion_files(model))
    assert names == ["robot.bin", "textures/robot_diffuse.png"]
    (folder / "chair.mtl").write_text("newmtl Wood\nmap_Kd wood.png\n", encoding="utf-8")
    _file(folder / "wood.png", b"png")
    obj = _file(folder / "chair.obj", b"mtllib chair.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    assert sorted(p.name for p in companion_files(obj)) == ["chair.mtl", "wood.png"]


def test_package_round_trip_rename_move_and_original_untouched():
    session = headless()
    try:
        board = _board(session)
        before = {n.name: dict(n.attrs) for n in session.scene.iter_nodes()}
        out = tmp_dir() / "send" / "Robot"
        result = session.run("package.save", str(out))
        files = out / "Board.files"
        assert result["files"] == 8 and result["copied"] == 8 and not result["missing"]
        manifest = links.read_manifest(files)
        assert manifest is not None and len(manifest["files"]) == 8
        assert {"refs/ref.png", "Downloads/clip.mp4", "sh010/shot.0002.png", "robot/robot.bin",
                "robot/textures/robot_diffuse.png"} <= {f["path"] for f in manifest["files"]}
        # The open board is untouched: same paths, same file, no package section.
        assert {n.name: dict(n.attrs) for n in session.scene.iter_nodes()} == before
        assert session.scene_path == board["board"] and "package" not in session.scene.sections
        saved = json.loads((out / "Board.pxref").read_text(encoding="utf-8"))
        assert saved["package"]["id"] == manifest["id"]
        paths = {n["name"]: n["attrs"]["path"] for n in saved["nodes"]}
        assert paths == {"Ref": "Board.files/refs/ref.png", "Clip": "Board.files/Downloads/clip.mp4",
                         "Shot": "Board.files/sh010/shot.####.png", "Robot": "Board.files/robot/robot.gltf"}
        assert saved["links"]["Board.files/Downloads/clip.mp4"] == links.fingerprint(board["clip"])
    finally:
        session.shutdown()

    # The whole folder moved: opens anywhere (plain relative paths), on a machine that knows nothing.
    moved = tmp_dir() / "elsewhere"
    shutil.move(str(out), str(moved))
    other = headless()
    try:
        other.open_file(moved / "Board.pxref")
        assert _all_found(other)
        assert other.run("package.verify")["problems"] == []
    finally:
        other.shutdown()

    # The files folder renamed: found as a sibling by its manifest id, files by fingerprint.
    (moved / "Board.files").rename(moved / "robot_media")
    other = headless()
    try:
        other.open_file(moved / "Board.pxref")
        assert _all_found(other)
        assert other.resolve_path("Board.files/Downloads/clip.mp4") == moved / "robot_media" / "Downloads" / "clip.mp4"
        frames = other.services.get("sequences").scan(other.scene.nodes["Shot"].attrs).frames
        assert frames == [1, 2, 3]
        # Healed paths are only written when the board is saved.
        assert other.scene.nodes["Clip"].attrs["path"] == "Board.files/Downloads/clip.mp4"
    finally:
        other.shutdown()

    # The files folder moved to another drive: one Find Missing Files; the id is remembered, no scene edit.
    away = tmp_dir() / "archive"
    shutil.move(str(moved / "robot_media"), str(away))
    settings = MemorySettings()
    other = headless(settings)
    try:
        other.open_file(moved / "Board.pxref")
        assert len(other.run("package.missing")) == 4
        result = other.run("package.find_missing", str(away.parent))
        assert result["package"] and result["relinked"] == 4 and _all_found(other)
        assert other.scene.nodes["Clip"].attrs["path"] == "Board.files/Downloads/clip.mp4"
    finally:
        other.shutdown()
    again = headless(settings)  # the same machine: remembered
    try:
        again.open_file(moved / "Board.pxref")
        assert _all_found(again)
    finally:
        again.shutdown()


def test_a_renamed_and_moved_file_is_found_by_content_and_heals_other_boards():
    settings = MemorySettings()
    session = headless(settings)
    try:
        board = _board(session)
        renamed = tmp_dir() / "ref" / "sh010_ref_v02.mp4"
        renamed.parent.mkdir(parents=True)
        shutil.move(str(board["clip"]), str(renamed))
        session.open_file(board["board"])
        assert [m.split(":")[0] for m in session.run("package.missing")] == ["Clip"]
        result = session.run("package.find_missing", str(renamed.parent.parent))
        assert result["relinked"] == 1 and not result["not_found"]
        assert Path(session.scene.nodes["Clip"].attrs["path"]) == renamed
        session.undo.undo()
        assert session.scene.nodes["Clip"].attrs["path"] == board["clip"].as_posix()  # one undo step
    finally:
        session.shutdown()
    other = headless(settings)  # another board using the same clip heals from this machine's memory
    try:
        other.scene.create_node("movie", "Same", attrs={"path": board["clip"].as_posix()})
        other.scene.sections["links"] = {board["clip"].as_posix(): links.fingerprint(renamed)}
        assert other.resolve_path(board["clip"].as_posix()) == renamed
    finally:
        other.shutdown()


def test_links_saved_without_fingerprints_relink_by_a_unique_name_only():
    session = headless()
    try:
        session.scene.create_node("movie", "A", attrs={"path": "gone/a.mp4"})
        session.scene.create_node("movie", "B", attrs={"path": "gone/b.mp4"})
        folder = tmp_dir()
        _file(folder / "x" / "a.mp4", b"a")
        _file(folder / "x" / "b.mp4", b"b1")
        _file(folder / "y" / "b.mp4", b"b2")
        result = session.run("package.find_missing", str(folder))
        assert result["relinked"] == 1 and len(result["ambiguous"]) == 1
        assert Path(session.scene.nodes["A"].attrs["path"]) == folder / "x" / "a.mp4"
        assert session.scene.nodes["B"].attrs["path"] == "gone/b.mp4"
    finally:
        session.shutdown()


def test_verify_flags_a_truncated_file_and_repackaging_is_incremental():
    session = headless()
    try:
        _board(session)
        out = tmp_dir()
        session.run("package.save", str(out))
        again = session.run("package.save", str(out))
        assert again["copied"] == 0 and again["skipped"] == 8
        clip = out / "Board.files" / "Downloads" / "clip.mp4"
        expected = clip.stat().st_size
        clip.write_bytes(b"")
        result = session.run("package.verify", str(out))
        assert result["ok"] == 7 and result["problems"] == [f"Downloads/clip.mp4: size 0, expected {expected:,}"]
        third = session.run("package.save", str(out))  # the broken copy is replaced
        assert third["copied"] == 1
        assert session.run("package.verify", str(out))["problems"] == []
    finally:
        session.shutdown()


def test_an_interrupted_package_leaves_no_manifest_and_no_scene():
    session = headless()
    try:
        _board(session)
        api = session.services.get("packages")
        out = tmp_dir()
        plan = api.plan(out)
        assert len(plan.files) == 8 and plan.total_bytes > 0 and not plan.missing
        assert not (out / "Board.files").exists()  # the pre-flight writes nothing

        def stop(index, _count, _path):
            if index == 3:
                raise OSError("disk full")

        try:
            api.execute(plan, stop)
        except OSError:
            pass
        else:
            raise AssertionError("expected the copy to stop")
        assert not (out / "Board.files" / "manifest.json").exists()
        assert not (out / "Board.pxref").exists()
        assert not list((out / "Board.files").rglob("*.tmp"))
    finally:
        session.shutdown()


def test_missing_files_are_listed_by_the_preflight_and_left_out():
    session = headless()
    try:
        board = _board(session)
        board["clip"].unlink()
        summary = session.run("package.preflight", str(tmp_dir()))
        assert summary["files"] == 7 and summary["missing"] == [f"Clip: {board['clip'].as_posix()}"]
        try:
            session.run("package.verify")
        except CommandError:
            pass
        else:
            raise AssertionError("a board that is not a package has nothing to verify")
    finally:
        session.shutdown()


def test_package_commands_in_the_app():
    h = make()
    try:
        assert h.app.actions.get("package.save") is not None
        assert h.app.actions.get("package.find_missing") is not None
        session = h.session
        clip = _file(tmp_dir() / "clip.mp4", b"clip bytes")
        name = h.run("movie.import_movie", str(clip))
        board = tmp_dir() / "board.pxref"
        h.save_to(board)
        moved = tmp_dir() / "moved.mp4"
        shutil.move(str(clip), str(moved))
        h.open(board)
        assert session.links.note(session.scene.nodes[name].attrs["path"]) == "  (missing)"
        h.run("package.find_missing", str(moved.parent))
        assert session.links.note(session.scene.nodes[name].attrs["path"]) == ""
        moved.write_bytes(b"clip bytes, edited")
        assert session.links.note(session.scene.nodes[name].attrs["path"]) == "  (changed)"
    finally:
        h.close()


def test_find_missing_by_package_id_refreshes_the_board():
    h = make()
    try:
        folder = tmp_dir()
        for frame in (1, 2):
            shutil.copy(make_png(f"g{frame}.png", 8, 8), _file(folder / "renders" / f"shot.{frame:04d}.png", b""))
        name = h.run("sequence.import_sequence", str(folder / "renders" / "shot.####.png"), [0.0, 0.0])
        out = tmp_dir()
        h.run("package.save", str(out), "Board")
        away = tmp_dir() / "away"
        shutil.move(str(out / "Board.files"), str(away))
        h.open(out / "Board.pxref")
        assert h.item(name).is_missing()
        h.run("package.find_missing", str(away))
        h.app.canvas.request_full_refresh()
        h.refresh()
        assert not h.item(name).is_missing()
    finally:
        h.close()


def test_save_package_dialog_runs_in_the_background_and_reports():
    import time

    import pixelref.builtins.package.ui as package_ui
    from harness import APP

    h = make()
    dialogs, boxes = package_ui.QFileDialog, package_ui.QMessageBox
    try:
        clip = _file(tmp_dir() / "clip.mp4", b"clip bytes")
        h.run("movie.import_movie", str(clip))
        target = tmp_dir() / "Pack" / "Pack.pxref"
        asked: list[str] = []

        class _Dialogs:
            @staticmethod
            def getSaveFileName(*_a, **_k):  # noqa: N802
                return str(target), ""

        class _Boxes:
            Ok, Cancel = boxes.Ok, boxes.Cancel

            @staticmethod
            def question(_parent, _title, text, *_a):
                asked.append(text)
                return boxes.Ok

            @staticmethod
            def critical(*args):
                raise AssertionError(args)

        package_ui.QFileDialog, package_ui.QMessageBox = _Dialogs, _Boxes
        h.trigger("package.save")
        assert asked and "1 file(s)" in asked[0] and "not changed" in asked[0]
        deadline = time.monotonic() + 10
        while not (target.parent / "Pack.pxref").exists() and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.01)
        APP.processEvents()
        assert links.read_manifest(target.parent / "Pack.files") is not None
        assert h.session.scene_path is None  # the open board is untouched

        dialog = package_ui.RootsDialog(type("UI", (), {"session": h.session, "app": h.app})())
        h.run("package.set_root", "PROJ", str(target.parent))
        dialog.reload()
        assert dialog.table.rowCount() == 1 and dialog.table.item(0, 0).text() == "PROJ"
        dialog.close()
        h.run("package.remove_root", "PROJ")
    finally:
        package_ui.QFileDialog, package_ui.QMessageBox = dialogs, boxes
        h.close()


def test_path_variables_row_in_the_canvas_panel():
    from PySide6.QtWidgets import QApplication, QPlainTextEdit

    h = make()
    try:
        rows = [w for top in QApplication.topLevelWidgets()
                for w in top.findChildren(QPlainTextEdit, "canvasRow_path_variables")]
        assert rows, "the Canvas panel has no Path variables row"
        row = rows[0]
        row.setPlainText("STUDIO_DRIVE = W:\\\nLINUX_PATH = /mnt/drive")
        row.commit()
        assert h.session.links.variables() == {"STUDIO_DRIVE": "W:/", "LINUX_PATH": "/mnt/drive"}
        assert row.toPlainText() == "STUDIO_DRIVE = W:/\nLINUX_PATH = /mnt/drive"
        row.setPlainText("not a variable")
        row.commit()  # refused: the board's variables are shown again
        assert row.toPlainText() == "STUDIO_DRIVE = W:/\nLINUX_PATH = /mnt/drive"
        h.run("undo")
        h.refresh()
        assert h.session.links.variables() == {} and row.toPlainText() == ""
    finally:
        h.close()
