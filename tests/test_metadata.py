"""Element metadata: tags (kernel commands) and the note / metadata fields, and how they persist."""

from __future__ import annotations

import json

from harness import make, tmp_dir
from PySide6.QtCore import QMimeData, QPointF, QUrl


def headless(**kwargs):
    from pixelref.core.session import Session

    return Session(**kwargs).start()


def _board(pxcmd) -> list[str]:
    return [pxcmd.create_node("annotation_rect", f"Box{i}", translate=[i * 50, 0]) for i in range(3)]


# ---- Tag commands --------------------------------------------------------------------------------


def test_add_remove_and_set_tags_work_on_many_nodes_as_one_undo_step():
    from pixelref import pxcmd

    session = headless()
    a, b, c = _board(pxcmd)
    pxcmd.set_attr(a, "tags", ["ref"])
    steps = len(session.undo.stack)
    changed = pxcmd.add_tags(["  ref ", "light", "", "light"], [a, b])
    assert changed == [a, b]
    assert pxcmd.get_attr(a, "tags") == ["ref", "light"], "trimmed, no repeats, existing tags kept first"
    assert pxcmd.get_attr(b, "tags") == ["ref", "light"]
    assert pxcmd.get_attr(c, "tags") == []
    assert len(session.undo.stack) == steps + 1 and session.undo.stack.undo_label() == "Add tags"
    assert pxcmd.add_tags("ref", [a, b]) == [], "nothing to change: no node is touched"

    pxcmd.select([b, c])
    assert pxcmd.add_tags("todo") == [b, c], "default: the selection"
    assert session.undo.stack.undo_label() == "Add tag todo"
    assert pxcmd.remove_tags(["ref", "missing"], [a, b, c]) == [a, b]
    assert pxcmd.get_attr(b, "tags") == ["light", "todo"]
    assert pxcmd.set_tags(["x", "x", " y"], [a, c]) == [a, c]
    assert pxcmd.get_attr(a, "tags") == ["x", "y"] and pxcmd.get_attr(c, "tags") == ["x", "y"]
    assert pxcmd.set_tags([], c) == [c] and pxcmd.get_attr(c, "tags") == []

    pxcmd.undo()
    assert pxcmd.get_attr(c, "tags") == ["x", "y"]
    pxcmd.undo()
    assert pxcmd.get_attr(a, "tags") == ["light"] and pxcmd.get_attr(c, "tags") == ["todo"]
    pxcmd.redo()
    assert pxcmd.get_attr(a, "tags") == ["x", "y"]

    # Nodes by id work like every kernel command.
    pxcmd.add_tags("by_id", pxcmd.node_id(a))
    assert "by_id" in pxcmd.get_attr(a, "tags")


def test_tag_commands_report_mistakes_without_changing_anything():
    from pixelref import pxcmd
    from pixelref.core.errors import CommandError, SceneError

    session = headless()
    a, _b, _c = _board(pxcmd)
    pxcmd.select(clear=True)
    for call in (lambda: pxcmd.add_tags("x"), lambda: pxcmd.add_tags([" ", ""], a),
                 lambda: pxcmd.remove_tags([], a), lambda: pxcmd.add_tags("x", [a, "Nope"])):
        steps = len(session.undo.stack)
        try:
            call()
        except (CommandError, SceneError):
            pass
        else:
            raise AssertionError("expected an error")
        assert len(session.undo.stack) == steps
    assert pxcmd.get_attr(a, "tags") == [], "names are checked before anything is written"


def test_list_tags_counts_tags_in_the_scene_or_on_some_nodes():
    from pixelref import pxcmd

    headless()
    a, b, c = _board(pxcmd)
    pxcmd.add_tags(["sky", "Mood"], [a, b])
    pxcmd.add_tags("sky", c)
    assert pxcmd.list_tags() == {"Mood": 2, "sky": 3}
    assert list(pxcmd.list_tags()) == ["Mood", "sky"], "sorted by name, ignoring case"
    assert pxcmd.list_tags([c]) == {"sky": 1}
    assert pxcmd.ls(tags="Mood") == [a, b], "the tags the commands write are what ls(tags=) finds"


def test_clean_tags_is_part_of_the_plugin_api():
    from pixelref.plugin_api import clean_tags

    assert clean_tags([" a", "b ", "a", "", 3]) == ["a", "b", "3"]


# ---- Persistence ---------------------------------------------------------------------------------


def test_tags_note_and_metadata_survive_save_and_load():
    from pixelref import pxcmd

    session = headless()
    a, b, _c = _board(pxcmd)
    pxcmd.add_tags(["hero", "lighting"], a)
    pxcmd.set_attr(a, "note", "Key light reference\nwarm rim")
    pxcmd.set_attr(a, "metadata", {"shot": "010", "rating": 4})
    path = tmp_dir() / "meta.pxref"
    pxcmd.save_scene(str(path))

    saved = {n["name"]: n for n in json.loads(path.read_text(encoding="utf-8"))["nodes"]}
    assert "note" not in saved[b]["attrs"] and "metadata" not in saved[b]["attrs"], \
        "untouched nodes don't store the optional fields"

    other = headless()
    other.open_file(path)
    assert other.get_attr(a, "tags") == ["hero", "lighting"]
    assert other.get_attr(a, "note") == "Key light reference\nwarm rim"
    assert other.get_attr(a, "metadata") == {"shot": "010", "rating": 4}
    assert other.get_attr(b, "note") == "" and other.get_attr(b, "metadata") == {}
    session.make_current()


def test_files_from_before_metadata_load_and_save_unchanged():
    """A board saved without note/metadata (or without tags at all) loads; saving adds nothing."""
    text = json.dumps({"schema": 1, "types": {"annotation_rect": 1}, "nodes": [
        {"id": "r", "name": "Old", "type": "annotation_rect", "parent": None, "attrs": {"translate": [1, 2]}},
    ]})
    path = tmp_dir() / "old.pxref"
    path.write_text(text, encoding="utf-8")
    session = headless()
    session.open_file(path)
    assert session.run("list_tags") == {}
    session.run("add_tags", "new", "Old")
    assert session.get_attr("Old", "tags") == ["new"]
    out = tmp_dir() / "old_out.pxref"
    session.save_file(out)
    attrs = json.loads(out.read_text(encoding="utf-8"))["nodes"][0]["attrs"]
    assert attrs["tags"] == ["new"] and "note" not in attrs and "metadata" not in attrs


def test_nodes_of_unknown_types_can_be_tagged_and_keep_their_data():
    text = json.dumps({"schema": 1, "types": {"sticky": 3}, "nodes": [
        {"id": "s", "name": "Sticky", "type": "sticky", "parent": None,
         "attrs": {"tags": ["old"], "note": "from a plugin you don't have", "votes": 7}},
    ]})
    path = tmp_dir() / "unknown_meta.pxref"
    path.write_text(text, encoding="utf-8")
    session = headless()
    session.open_file(path)
    assert session.run("list_tags") == {"old": 1}
    session.run("add_tags", "kept", "Sticky")
    out = tmp_dir() / "unknown_meta_out.pxref"
    session.save_file(out)
    attrs = json.loads(out.read_text(encoding="utf-8"))["nodes"][0]["attrs"]
    assert attrs == {"tags": ["old", "kept"], "note": "from a plugin you don't have", "votes": 7}


def test_import_scene_keeps_tags_note_and_metadata():
    from pixelref import pxcmd

    source = headless()
    a, _b, _c = _board(pxcmd)
    pxcmd.add_tags("imported", a)
    pxcmd.set_attr(a, "note", "came from elsewhere")
    pxcmd.set_attr(a, "metadata", {"source": "lib"})
    path = tmp_dir() / "import_meta.pxref"
    pxcmd.save_scene(str(path))

    target = headless()
    pxcmd.create_node("annotation_rect", a)  # the name is taken: the import renames
    roots = pxcmd.import_scene(str(path))
    tagged = pxcmd.ls(tags="imported")
    assert len(tagged) == 1 and tagged[0] in roots and tagged[0] != a
    assert target.get_attr(tagged[0], "note") == "came from elsewhere"
    assert target.get_attr(tagged[0], "metadata") == {"source": "lib"}
    pxcmd.undo()
    assert pxcmd.list_tags() == {}, "the import is one undo step"
    source.make_current()


def test_extract_and_merge_keep_metadata():
    """The path editable PNGs use: cut a part of a board out and add it to another."""
    from pixelref import pxcmd
    from pixelref.core.scene_merge import extract_subset, merge_scene

    session = headless()
    a, b, _c = _board(pxcmd)
    pxcmd.add_tags(["png", "keep"], a)
    pxcmd.set_attr(a, "note", "exported")
    pxcmd.set_attr(a, "metadata", {"k": [1, 2]})
    subset, _warnings = extract_subset(session.scene, [a, b])
    other = headless()
    names = merge_scene(other.scene, subset)
    node = other.scene.get_node(names[0])
    assert node.attrs["tags"] == ["png", "keep"] and node.attrs["note"] == "exported"
    assert node.attrs["metadata"] == {"k": [1, 2]}
    session.make_current()


def test_editable_png_round_trip_keeps_metadata():
    from pixelref.core import scene_embed

    h = make()
    try:
        h.run("rect.create", 20, 30, 60, 40, "Box")
        h.run("add_tags", ["moodboard", "v2"], "Box")
        h.run("set_attr", "Box", "note", "approved")
        h.run("set_attr", "Box", "metadata", {"client": "Acme", "round": 2})
        h.select([])
        h.refresh()
        out = tmp_dir() / "meta_export.png"
        h.run("export.render", str(out))
        assert scene_embed.has_embedded_scene(str(out))
        h.run("new_scene")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(out))])
        assert h.drop(QPointF(0, 0), mime)
        tagged = h.run("ls", tags="moodboard")
        assert len(tagged) == 1, (tagged, h.run("ls"))  # a flat image instead: the drop saw Shift held
        assert h.attr(tagged[0], "tags") == ["moodboard", "v2"]
        assert h.attr(tagged[0], "note") == "approved"
        assert h.attr(tagged[0], "metadata") == {"client": "Acme", "round": 2}
    finally:
        h.close()


# ---- Editing UI (M2) -----------------------------------------------------------------------------


def _settle() -> None:
    from harness import APP, QTest

    QTest.qWait(5)
    APP.processEvents()


def _chip_labels(section) -> dict[str, str]:
    from PySide6.QtWidgets import QToolButton

    return {tag: chip.findChild(QToolButton, "tagLabel").text() for tag, chip in section.chips.items()}


def test_tags_section_adds_and_removes_tags_on_one_item():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        h.select(["Img"])
        tags = h.section("tags")
        assert tags.isVisible() and not tags.chips
        steps = len(h.session.undo.stack)
        tags.edit.setText(" sky, mood ,sky")
        tags.edit.returnPressed.emit()
        assert h.attr("Img", "tags") == ["sky", "mood"]
        assert tags.edit.text() == "" and len(h.session.undo.stack) == steps + 1
        h.refresh()
        assert _chip_labels(tags) == {"sky": "sky", "mood": "mood"}
        tags.remove("sky")
        assert h.attr("Img", "tags") == ["mood"]
        h.refresh()
        assert list(tags.chips) == ["mood"]
        h.undo()
        h.select(["Img"])
        assert list(tags.chips) == ["sky", "mood"], "the section follows undo"
    finally:
        h.close()


def test_tags_section_edits_a_multi_selection_without_replacing_tags():
    h = make()
    try:
        h.run("rect.create", 0, 0, 60, 40, "A")
        h.run("rect.create", 100, 0, 60, 40, "B")
        h.run("add_tags", ["shared", "only_a"], "A")
        h.run("add_tags", ["shared", "only_b"], "B")
        h.select(["A", "B"])
        tags = h.section("tags")
        assert tags.isVisible(), "tags are edited on several items at once"
        assert not h.section("note").isVisible(), "the note stays a one-item section"
        assert _chip_labels(tags) == {"shared": "shared", "only_a": "only_a  1/2", "only_b": "only_b  1/2"}
        assert tags.chips["only_a"].property("partial") is True

        tags.edit.setText("new")
        tags.edit.returnPressed.emit()
        assert h.attr("A", "tags") == ["shared", "only_a", "new"]
        assert h.attr("B", "tags") == ["shared", "only_b", "new"]
        steps = len(h.session.undo.stack)
        tags.add_to_all("only_a")
        assert h.attr("B", "tags") == ["shared", "only_b", "new", "only_a"]
        assert len(h.session.undo.stack) == steps + 1
        tags.remove("shared")
        assert "shared" not in h.attr("A", "tags") and "shared" not in h.attr("B", "tags")
        h.undo()
        assert h.attr("A", "tags") == ["shared", "only_a", "new"]
    finally:
        h.close()


def test_tags_section_completes_tags_used_on_the_board():
    h = make()
    try:
        h.run("rect.create", 0, 0, 60, 40, "A")
        h.run("rect.create", 100, 0, 60, 40, "B")
        h.run("rect.create", 200, 0, 60, 40, "C")
        h.run("add_tags", ["common", "rare"], ["A", "B"])
        h.run("add_tags", "common", "C")
        h.run("add_tags", "mine", "C")
        h.select(["C"])
        tags = h.section("tags")
        tags._update_completions()
        assert tags.model.stringList() == ["rare"], "the most used first, minus what the item already has"
        tags.edit.setText("mine, ra")
        completer = tags.edit.completer()
        assert completer.splitPath(tags.edit.text()) == ["ra"], "completes the tag after the last comma"
        completer.setCompletionPrefix("ra")
        assert completer.pathFromIndex(completer.currentIndex()) == "mine, rare"
    finally:
        h.close()


def test_quick_editor_tags_field_keeps_each_items_other_tags():
    from PySide6.QtCore import QSettings

    QSettings().remove("plugins/quick_edit")
    h = make()
    try:
        h.run("quick_edit.set_fields", "annotation_rect", ["tags"])
        h.run("rect.create", 0, 0, 60, 40, "A")
        h.run("rect.create", 100, 0, 60, 40, "B")
        h.run("add_tags", ["shared", "only_a"], "A")
        h.run("add_tags", ["shared", "only_b"], "B")
        h.select(["A", "B"])
        _settle()
        from pixelref.builtins.quick_edit.ui import QuickBar

        bar = h.view.findChildren(QuickBar)[0]
        assert bar.editors["tags"].read() == ["shared", "only_a"], "it shows the first item's tags"
        steps = len(h.session.undo.stack)
        bar.controller.commit("tags", ["only_a", "fresh"])  # removed "shared", added "fresh"
        assert h.attr("A", "tags") == ["only_a", "fresh"]
        assert h.attr("B", "tags") == ["only_b", "fresh"], "B keeps its own tag"
        assert len(h.session.undo.stack) == steps + 1 and h.session.undo.stack.undo_label() == "Edit tags"
        h.undo()
        assert h.attr("B", "tags") == ["shared", "only_b"]
    finally:
        h.run("quick_edit.reset_fields")
        h.close()
