"""Find: the query syntax, the derived search index and the find.* commands (headless)."""

from __future__ import annotations

import json

from harness import tmp_dir


def headless(**kwargs):
    from pixelref.core.session import Session

    return Session(**kwargs).start()


def names(query: str, **kwargs) -> list[str]:
    from pixelref import pxcmd

    return [m["name"] for m in pxcmd.find(query, **kwargs)]


def board():
    """A small board with something in every searchable field."""
    from pixelref import pxcmd

    session = headless()
    pxcmd.create_node("annotation_rect", "HeroBox", tags=["hero", "Sky"])
    pxcmd.create_node("annotation_rect", "Backdrop", tags=["skyline"])
    pxcmd.create_node("annotation_text", "Caption", text="Warm light on the hill")
    pxcmd.set_attr("Caption", "note", "Fix the kerning before review")
    pxcmd.set_attr("Caption", "metadata", {"shot": "010", "approved": True})
    pxcmd.create_node("image", "Photo", path="/refs/holiday/beach_sunset.jpg", tags=["hero"])
    pxcmd.create_node("display_preset", "WarmPreset")
    return session


# ---- Query syntax ----------------------------------------------------------------------------------


def test_queries_parse_into_terms():
    from pixelref.builtins.find.search import Term, parse_query

    assert parse_query("") == [] and parse_query("  -  #  ") == []
    assert parse_query('Sky "Warm Light"') == [Term("sky"), Term("warm light")]
    assert parse_query("#hero tag:Done type:image name:ref note:fix") == [
        Term("hero", "tag"), Term("done", "tag"), Term("image", "type"), Term("ref", "name"), Term("fix", "note")]
    assert parse_query("shot:010 shot:") == [Term("010", "meta", "shot"), Term("", "meta", "shot")]
    assert parse_query('-#"warm palette" -draft') == [Term("warm palette", "tag", negate=True),
                                                      Term("draft", negate=True)]
    assert parse_query('note:"unclosed') == [Term("unclosed", "note")]
    assert parse_query("#sky*")[0].glob and not parse_query("#sky")[0].glob


# ---- Matching -----------------------------------------------------------------------------------------


def test_words_match_any_field_and_every_term_must_match():
    board()
    assert names("herobox") == ["HeroBox"], "names, ignoring case"
    assert names("hero") == ["HeroBox", "Photo"], "the name match ranks above a tag-only match"
    assert names("kerning") == ["Caption"], "the note"
    assert names("010") == ["Caption"] and names("approved") == ["Caption"], "metadata values and keys"
    assert names('"light on the"') == ["Caption"], "text, as a phrase"
    assert names("beach") == ["Photo"], "an image's file name"
    assert names("holiday") == [], "not the folders of its path (they would match everything)"
    assert names("rectangle") == ["HeroBox", "Backdrop"], "the type's label"
    assert names("node") == [], "plain words don't match base types every node has"
    assert names("hero sky") == ["HeroBox"], "AND: every term must match"
    assert names("hero -box") == ["Photo"], "leaving out"
    assert names("") == []


def test_field_prefixes_narrow_the_match():
    board()
    assert names("#sky") == ["HeroBox"], "whole tags only (not skyline), ignoring case"
    assert names("tag:SKY") == ["HeroBox"]
    assert names("#sky*") == ["HeroBox", "Backdrop"], "wildcards"
    assert names("type:annotation_rect") == ["HeroBox", "Backdrop"]
    assert names("type:annotation_*") == ["HeroBox", "Backdrop", "Caption"]
    assert len(names("type:canvas_node")) == 4, "base types work with type:"
    assert names("type:rect") == [], "type: wants a whole type name (or wildcards)"
    assert names("name:box") == ["HeroBox"]
    assert names("note:kerning") == ["Caption"] and names("note:hero") == []
    assert names("shot:010") == ["Caption"] and names("shot:") == ["Caption"] and names("shot:999") == []
    assert names("SHOT:010") == ["Caption"], "metadata keys ignore case too"
    assert names("-#hero") == ["Backdrop", "Caption"], "only exclusions: everything else"


def test_data_nodes_are_left_out_unless_asked_for():
    board()
    assert names("WarmPreset") == []
    assert names("WarmPreset", canvas_only=False) == ["WarmPreset"]


def test_results_say_where_they_matched_and_show_a_snippet():
    from pixelref import pxcmd

    board()
    [caption] = pxcmd.find("kerning")
    assert caption["fields"] == ["note"] and caption["type"] == "annotation_text"
    assert "kerning" in caption["snippet"] and len(caption["snippet"]) <= 50
    [hero] = pxcmd.find("#hero name:herobox")
    assert set(hero["fields"]) == {"tags", "name"} and hero["tags"] == ["hero", "Sky"] and not hero["snippet"]
    assert len(pxcmd.find("type:canvas_node", limit=2)) == 2


def test_ranking_prefers_exact_names_then_prefixes_then_tags():
    from pixelref import pxcmd

    headless()
    pxcmd.create_node("annotation_rect", "Tagged", tags=["sky"])
    pxcmd.create_node("annotation_rect", "NoteOnly", note="the sky is wrong")
    pxcmd.create_node("annotation_rect", "BigSky")
    pxcmd.create_node("annotation_rect", "SkyHigh")
    pxcmd.create_node("annotation_rect", "Sky")
    assert names("sky") == ["Sky", "SkyHigh", "Tagged", "BigSky", "NoteOnly"]


def test_nodes_of_unknown_types_are_searchable():
    text = json.dumps({"schema": 1, "types": {"sticky": 1}, "nodes": [
        {"id": "s", "name": "Sticky", "type": "sticky", "parent": None,
         "attrs": {"translate": [0, 0], "tags": ["todo"], "body": "Call the client"}},
    ]})
    path = tmp_dir() / "find_unknown.pxref"
    path.write_text(text, encoding="utf-8")
    session = headless()
    session.open_file(path)
    assert names("#todo") == ["Sticky"] and names("client") == ["Sticky"] and names("type:sticky") == ["Sticky"]


# ---- The index ------------------------------------------------------------------------------------------


def test_the_index_rebuilds_only_what_changed_and_follows_undo():
    from pixelref import pxcmd

    session = board()
    index = session.services.get("find")
    names("x")
    built = index.builds
    names("y")
    assert index.builds == built, "an unchanged scene isn't indexed again"
    pxcmd.add_tags("later", "Backdrop")
    assert names("#later") == ["Backdrop"] and index.builds == built + 1, "one node changed: one entry"
    pxcmd.rename("Backdrop", "Background")
    assert names("#later") == ["Background"] and names("backdrop") == []
    pxcmd.delete("Background")
    assert names("#later") == []
    pxcmd.undo()
    assert names("#later") == ["Background"], "undo restores what search finds"
    pxcmd.undo()
    assert names("#later") == ["Backdrop"]
    pxcmd.new_scene()
    assert names("hero") == []


def test_the_index_follows_node_types_coming_and_going():
    from pixelref import pxcmd

    session = board()
    assert names("rectangle") == ["HeroBox", "Backdrop"]
    pxcmd.unload_plugin("rect")
    try:
        assert names("rectangle") == [], "without its plugin the type has no label"
        assert names("#hero") == ["HeroBox", "Photo"], "but its nodes are still found"
    finally:
        pxcmd.load_plugin("rect")
    assert names("rectangle") == ["HeroBox", "Backdrop"]
    session.make_current()


# ---- Commands ----------------------------------------------------------------------------------------------


def test_find_select_selects_the_matches():
    from pixelref import pxcmd
    from pixelref.core.errors import CommandError

    session = board()
    steps = len(session.undo.stack)
    assert pxcmd.find.select("#hero") == ["HeroBox", "Photo"]
    assert pxcmd.find.select("caption", add=True) == ["HeroBox", "Photo", "Caption"]
    assert pxcmd.find.select("nothing_like_this") == []
    try:
        pxcmd.find.select("   ")
    except CommandError:
        pass
    else:
        raise AssertionError("an empty query is a mistake, not 'select nothing'")
    assert len(session.undo.stack) == steps, "selecting is not an undo step"


def test_find_commands_are_namespaced_with_a_flat_alias():
    from pixelref import pxcmd

    session = board()
    assert pxcmd.find("caption") == pxcmd.find.search("caption")
    command = session.commands.get("find")
    assert command.name == "find.search" and not command.undoable and not command.echo
    assert session.commands.get("find.select").echo


def test_disabling_find_removes_its_commands_and_keeps_the_board():
    from pixelref import pxcmd

    session = board()
    pxcmd.unload_plugin("find")
    try:
        assert not session.commands.exists("find") and session.services.get("find") is None
        assert pxcmd.ls(tags="hero") == ["HeroBox", "Photo"]
    finally:
        pxcmd.load_plugin("find")
    assert names("#hero") == ["HeroBox", "Photo"]


# ---- The Find panel (M4) ---------------------------------------------------------------------------


def _settle(times: int = 5) -> None:
    from harness import APP, QTest

    for _ in range(times):
        QTest.qWait(10)
        APP.processEvents()


def _panel(h, query: str | None = None):
    from harness import APP

    from pixelref.builtins.find.ui import FindPanel

    h.run("find.show", query)
    _settle()
    return next(w for w in APP.allWidgets() if isinstance(w, FindPanel) and w.isVisible())


def _ui_board(h) -> None:
    h.run("rect.create", 0, 0, 60, 40, "HeroBox")
    h.run("rect.create", 3000, 2000, 60, 40, "FarAway")
    h.run("add_tags", ["hero", "sky"], "HeroBox")
    h.run("add_tags", "hero", "FarAway")
    h.create("annotation_text", "Caption", text="Warm light", translate=[-200, 300])
    h.run("set_attr", "Caption", "note", "client wants the hero brighter")


def _rows(panel) -> list[str]:
    from pixelref.builtins.find.ui import NAME_ROLE

    return [panel.results.item(i).data(NAME_ROLE) for i in range(panel.results.count())]


def test_ctrl_f_opens_the_find_panel_and_typing_searches():
    from harness import make

    h = make()
    try:
        _ui_board(h)
        binding = [b for b in h.run("keymap.list") if b["key"] == "Ctrl+F"]
        assert binding and binding[0]["target"] == "find.show" and binding[0]["when"] == "!editing.text"
        panel = _panel(h)
        assert panel.field.hasFocus() and panel.results.count() == 0
        assert "Type to find" in panel.status.text()
        panel.field.setText("hero")
        assert panel.timer.isActive(), "typing waits a moment before searching"
        _settle(20)
        assert _rows(panel) == ["HeroBox", "FarAway", "Caption"] and panel.status.text() == "3 found"
        assert panel.results.currentRow() == 0
        panel.field.setText("#sky")
        panel.run()
        assert _rows(panel) == ["HeroBox"]
        panel.field.setText("nothing_like_it")
        panel.run()
        assert panel.status.text() == "Nothing matches." and not panel.select_btn.isEnabled()
        again = _panel(h, "caption")
        assert again is panel and _rows(panel) == ["Caption"], "Ctrl+F again reuses the panel"
    finally:
        h.close()


def test_keys_move_through_results_go_there_and_select_all():
    from harness import make
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    h = make()
    try:
        _ui_board(h)
        panel = _panel(h, "hero")
        QTest.keyClick(panel.field, Qt.Key_Down)
        assert panel.current_name() == "FarAway"
        QTest.keyClick(panel.field, Qt.Key_Up)
        QTest.keyClick(panel.field, Qt.Key_Up)
        assert panel.current_name() == "HeroBox", "stops at the first result"
        QTest.keyClick(panel.field, Qt.Key_Down)
        steps = len(h.session.undo.stack)
        before = list(h.session.selection.names)
        QTest.keyClick(panel.field, Qt.Key_Return)
        _settle(40)
        assert h.session.selection.names == ["FarAway"]
        center = h.run("navigation.camera")["center"]
        assert abs(center[0] - 3030) < 1 and abs(center[1] - 2020) < 1, center
        assert h.session.commands.exists("links.back"), "the links plugin's glide was used: Alt+Left comes back"
        h.run("links.back")
        _settle(40)
        assert h.session.selection.names == before, "back restores the selection from before the jump"
        QTest.keyClick(panel.field, Qt.Key_Return, Qt.ShiftModifier)
        assert h.session.selection.names == ["HeroBox", "FarAway", "Caption"]
        assert len(h.session.undo.stack) == steps, "finding and going there never adds undo steps"
    finally:
        # QTest leaves the application thinking Shift is still held after Shift+Enter: a key without
        # modifiers clears that (later tests drop PNGs, where Shift means "import flat").
        QTest.keyClick(panel.field, Qt.Key_Right)
        h.close()


def test_clicking_a_result_goes_there_and_results_follow_edits():
    from harness import make

    h = make()
    try:
        _ui_board(h)
        panel = _panel(h, "#hero")
        assert _rows(panel) == ["HeroBox", "FarAway"]
        panel._go_to_item(panel.results.item(1))
        _settle(40)
        assert h.session.selection.names == ["FarAway"]
        h.run("remove_tags", "hero", "FarAway")
        h.run("add_tags", "hero", "Caption")
        h.refresh()
        _settle(20)
        assert _rows(panel) == ["HeroBox", "Caption"], "the list updates when the board changes"
    finally:
        h.close()


def test_going_to_an_element_without_the_links_plugin_frames_it():
    from harness import make

    h = make()
    try:
        _ui_board(h)
        h.run("unload_plugin", "links")
        _settle()
        assert h.run("find.locate", "FarAway") == "FarAway"
        _settle(40)
        assert h.session.selection.names == ["FarAway"]
        center = h.run("navigation.camera")["center"]
        assert abs(center[0] - 3030) < 1 and abs(center[1] - 2020) < 1, center
    finally:
        h.run("load_plugin", "links")
        h.close()


def test_locate_reports_what_it_cannot_go_to():
    from harness import make

    h = make()
    try:
        _ui_board(h)
        h.run("create_node", "display_preset", "Warm")
        for target in ("Warm", "NoSuchThing"):
            try:
                h.session.run("find.locate", target)
            except Exception:  # noqa: BLE001
                continue
            raise AssertionError(f"locate {target} should fail")
        h.run("toggle_visibility", "FarAway")
        panel = _panel(h, "#hero")
        from pixelref.builtins.find.ui import HIDDEN_ROLE

        assert [panel.results.item(i).data(HIDDEN_ROLE) for i in range(2)] == [False, True], "hidden items dim"
        assert h.run("find.locate", "FarAway") == "FarAway", "a hidden item can still be found and selected"
    finally:
        h.close()


def test_long_result_lists_are_capped_with_a_count():
    from harness import make

    from pixelref.builtins.find.ui import LIMIT

    h = make()
    try:
        with h.session.undo.chunk("Many"):
            for i in range(LIMIT + 5):
                h.session.scene.create_node("annotation_rect", f"Many{i}", attrs={"tags": ["bulk"]})
        h.session.notify_changed()
        panel = _panel(h, "#bulk")
        assert panel.results.count() == LIMIT
        assert panel.status.text() == f"{LIMIT + 5} found · first {LIMIT} listed"
        panel.select_all()
        assert len(h.session.selection.names) == LIMIT + 5, "Select All takes every match, not just those listed"
    finally:
        h.close()


# ---- UX refinements (M5) -----------------------------------------------------------------------------


def test_tag_terms_can_offer_alternatives():
    from pixelref.builtins.find.search import parse_query, tag_query

    board()
    assert names("#sky,skyline") == ["HeroBox", "Backdrop"], "either tag"
    assert names("#nope,hero") == ["HeroBox", "Photo"]
    assert names("#sky,skyline -#hero") == ["Backdrop"]
    assert names("sky,skyline") == [], "plain words keep the comma"
    assert parse_query("#a, b")[0].options == ("a",), "a space ends the term, as everywhere"
    assert parse_query('#"warm palette,sky"')[0].options == ("warm palette", "sky")
    assert tag_query(["sky", "warm palette"]) == '#"sky,warm palette"' and tag_query(["a", "b"]) == "#a,b"
    assert tag_query([]) == ""


def test_find_similar_opens_find_with_the_selections_tags():
    from harness import make

    h = make()
    try:
        _ui_board(h)
        h.run("rect.create", 500, 0, 60, 40, "Other")
        h.run("add_tags", "sky", "Other")
        h.select(["HeroBox"])
        assert h.session.context.get("find.selection_tagged") is True
        assert h.run("find.similar") == "#hero,sky"
        panel = _panel(h)
        assert panel.field.text() == "#hero,sky" and _rows(panel) == ["HeroBox", "FarAway", "Other"]
        h.select(["Caption"])
        assert h.session.context.get("find.selection_tagged") is False, "the menu entry hides"
        try:
            h.session.run("find.similar")
        except Exception as exc:  # noqa: BLE001
            assert "no tags" in str(exc)
        else:
            raise AssertionError("an untagged selection has nothing to be similar to")
    finally:
        h.close()


def test_an_empty_find_field_offers_the_boards_tags():
    from harness import make

    h = make()
    try:
        _ui_board(h)
        panel = _panel(h, "")
        assert panel.tags_label.isVisible()
        text = panel.tags_label.text()
        assert text.index("#hero") < text.index("#sky"), "most used first"
        panel.tags_label.linkActivated.emit("sky")
        assert panel.field.text() == "#sky" and _rows(panel) == ["HeroBox"]
        assert not panel.tags_label.isVisible(), "hidden while there is a query"
        panel.field.setText("")
        panel.run()
        assert panel.tags_label.isVisible()
        h.run("new_scene")
        panel.run()
        assert not panel.tags_label.isVisible(), "no tags on the board: nothing to offer"
    finally:
        h.close()


def test_tag_chips_open_find_and_partial_ones_offer_to_add():
    from harness import make
    from PySide6.QtWidgets import QToolButton

    h = make()
    try:
        _ui_board(h)
        h.select(["HeroBox", "FarAway"])
        tags = h.section("tags")
        assert tags.chips["hero"].findChild(QToolButton, "tagAdd") is None
        add = tags.chips["sky"].findChild(QToolButton, "tagAdd")
        assert add is not None, "sky is only on HeroBox: + adds it to FarAway"
        add.click()
        assert h.attr("FarAway", "tags") == ["hero", "sky"]
        h.refresh()
        tags.chips["sky"].findChild(QToolButton, "tagLabel").click()
        _settle()
        panel = _panel(h)
        assert panel.field.text() == "#sky" and _rows(panel) == ["HeroBox", "FarAway"]
    finally:
        h.close()


# ---- Large boards (M6) --------------------------------------------------------------------------------


def test_a_large_board_is_indexed_once_and_searched_incrementally():
    """5,000 nodes: one full build, then only what changes. The time limits are ~20x what was
    measured (see docs/design-find.md), only to catch an accidental slowdown."""
    import time

    from pixelref import pxcmd

    session = headless()
    scene = session.scene
    with pxcmd.undo_chunk("Big board"):
        for i in range(5000):
            scene.create_node("annotation_rect", f"Item{i}", attrs={"tags": ["even" if i % 2 else "odd"]})
    index = session.services.get("find")
    started = time.perf_counter()
    assert len(index.search("#even")) == 2500
    first = time.perf_counter() - started
    assert index.builds == 5000
    started = time.perf_counter()
    assert len(index.search("item49")) == 111  # Item49, Item490–499, Item4900–4999
    warm = time.perf_counter() - started
    assert index.builds == 5000, "an unchanged board is not indexed again"
    assert first < 2.0 and warm < 0.5, (first, warm)

    names_ = list(scene.nodes)
    started = time.perf_counter()
    pxcmd.add_tags("all", names_)
    bulk = time.perf_counter() - started
    assert len(pxcmd.find("#all", limit=None)) == 5000 and bulk < 3.0, bulk
    pxcmd.set_attr("Item7", "note", "the one")
    builds = index.builds
    assert names("note:one") == ["Item7"] and index.builds == builds + 1
    assert len(session.undo.stack) >= 2 and session.undo.stack.undo_label().startswith("Set")
