"""Interactions: click actions, Demonstrate, connecting, templates and clicking on the canvas."""

from __future__ import annotations

import json

from harness import APP, make, make_gif
from PySide6.QtCore import Qt

from pixelref.core.errors import CommandError, SceneError


def headless():
    from pixelref.core.session import Session

    return Session().start()


def _vis(session, name) -> int:
    return 1 if session.scene.get_attr(name, "visibility") else 0


# ---- Headless -----------------------------------------------------------------------------------


def test_connect_show_hides_until_clicked_and_clicking_again_puts_it_back():
    s = headless()
    run = s.run
    run("create_node", "annotation_rect", "Answer")
    button = run("interactions.create_button", 0, 0, "Reveal")
    assert [v["id"] for v in run("interactions.verbs", "Answer")][:2] == ["show", "hide"]
    run("interactions.connect", button, "Answer", "show")
    assert _vis(s, "Answer") == 0, "Show hides the item until the click"
    assert run("interactions.describe", button) == ["Show Answer"]
    assert run("interactions.click", button)["on"] is True
    assert _vis(s, "Answer") == 1
    assert run("interactions.click", button)["on"] is False
    assert _vis(s, "Answer") == 0
    run("rename", "Answer", "Solution")  # steps name ids: renaming doesn't break them
    run("interactions.click", button)
    assert _vis(s, "Solution") == 1
    assert run("interactions.describe", button) == ["Show Solution"]
    assert s.undo.stack.labels()[-1] == "Click Reveal"


def test_click_actions_only_run_allowed_commands_and_skip_deleted_items():
    s = headless()
    run = s.run
    run("create_node", "annotation_rect", "Box")
    button = run("interactions.create_button")
    try:
        run("interactions.set_action", button, [{"command": "delete", "args": ["Box"]}])
        raise AssertionError("delete isn't a step kind")
    except CommandError as exc:
        assert "can't be used in a click action" in str(exc)
    try:
        s.scene.set_attr(button, "on_click", [{"args": []}])
        raise AssertionError("a step needs a command")
    except SceneError:
        pass
    # A file can still carry a step that isn't allowed: it's skipped, never run.
    s.scene.set_attr(button, "on_click", [{"command": "delete", "args": ["Box"]},
                                          {"command": "set_attrs", "args": ["Box", {"opacity": 0.5}]}])
    result = run("interactions.click", button)
    assert result["ran"] == 1 and s.scene.has("Box") and "isn't allowed" in result["problems"][0]["reason"]
    assert result["status"] == "partial" and run("interactions.status", button) == "partial"
    run("delete", ["Box"])
    assert [p["reason"] for p in run("interactions.check", button)][1] == "the item it acts on was deleted"
    result = run("interactions.click", button)
    assert result["ran"] == 0 and result["status"] == "failed" and "deleted" in result["problems"][1]["reason"]
    log = run("interactions.log", button)
    assert [e["status"] for e in log] == ["partial", "failed"] and log[-1]["node"] == button
    # Fixing the action clears the failed state; a step that can't run still shows as broken.
    run("interactions.remove_step", 0, node=button)
    assert run("interactions.status", button) == "broken"
    run("interactions.clear_log")
    assert run("interactions.log") == []


def test_a_set_is_one_at_a_time():
    s = headless()
    run = s.run
    for name in ("A", "B"):
        run("create_node", "annotation_rect", name)
    one, two = run("interactions.create_button", label="One"), run("interactions.create_button", label="Two")
    run("interactions.connect", one, "A", "show")
    run("interactions.connect", two, "B", "show")
    assert run("interactions.make_set", [one, two]) == "Set 1"
    run("interactions.click", one)
    assert (_vis(s, "A"), _vis(s, "B")) == (1, 0)
    run("interactions.click", two)
    assert (_vis(s, "A"), _vis(s, "B")) == (0, 1), "turning Two on turned One off"
    assert not s.scene.get_attr(one, "action_on") and s.scene.get_attr(two, "action_on")


def test_demonstrate_turns_what_changed_into_one_undoable_click_action():
    s = headless()
    run = s.run
    run("create_node", "annotation_rect", "Box", attrs={"translate": [0, 0]})
    run("create_node", "annotation_rect", "Gone")
    button = run("interactions.create_button", 0, 300, "Go")
    undo_before = len(s.undo.stack)
    run("interactions.demonstrate", button)
    assert run("interactions.demonstrating") == button
    run("set_attr", "Box", "opacity", 0.25)
    run("set_attr", "Box", "translate", [80, 40])
    run("create_node", "annotation_text", "Drawn", attrs={"text": "hi"})
    run("delete", ["Gone"])
    result = run("interactions.finish")
    assert result["steps"] == 3 and result["created"] == ["Drawn"]
    assert run("interactions.describe", button) == ["Box: move and fade to 25%", "Show Drawn", "Hide Gone"]
    # The board is back as it was, and what was drawn is kept, hidden until the click.
    assert s.scene.get_attr("Box", "opacity") == 1.0 and s.scene.get_attr("Box", "translate") == [0.0, 0.0]
    assert s.scene.has("Gone") and _vis(s, "Drawn") == 0
    assert len(s.undo.stack) == undo_before + 1, "the whole demonstration is one undo step"
    run("interactions.click", button)
    assert s.scene.get_attr("Box", "opacity") == 0.25 and _vis(s, "Drawn") == 1 and _vis(s, "Gone") == 0
    run("interactions.click", button)
    assert s.scene.get_attr("Box", "translate") == [0.0, 0.0] and _vis(s, "Drawn") == 0 and _vis(s, "Gone") == 1
    run("undo")
    run("undo")
    run("undo")
    assert not s.scene.get_node(button).attrs.get("on_click") and not s.scene.has("Drawn")


def test_cancelling_a_demonstration_leaves_the_board_and_history_as_they_were():
    s = headless()
    run = s.run
    run("create_node", "annotation_rect", "Box")
    button = run("interactions.create_button")
    labels = s.undo.stack.labels()
    run("interactions.demonstrate", button)
    run("set_attr", "Box", "opacity", 0.1)
    assert run("interactions.finish", cancel=True)["cancelled"]
    assert s.scene.get_attr("Box", "opacity") == 1.0
    assert s.undo.stack.labels() == labels
    assert run("interactions.demonstrating") is None
    try:
        run("interactions.finish")
        raise AssertionError("not demonstrating")
    except CommandError:
        pass


def test_demonstrating_again_starts_from_the_result_and_keeps_other_steps():
    s = headless()
    run = s.run
    run("create_node", "annotation_rect", "Box")
    button = run("interactions.create_button")
    run("interactions.add_step", button, "interactions.fly_to", s.scene.get_node("Box").id)
    run("interactions.demonstrate", button)
    run("set_attr", "Box", "opacity", 0.5)
    run("interactions.finish")
    run("interactions.demonstrate", button)
    assert s.scene.get_attr("Box", "opacity") == 0.5, "starts from what the click does"
    run("set_attr", "Box", "rotate", 30.0)
    run("interactions.finish")
    assert run("interactions.describe", button) == ["Box: rotate to 30° and fade to 50%", "Fly to Box"]
    assert run("interactions.remove_step", 1, node=button) == 1


def test_templates_tabs_reveal_and_before_after():
    s = headless()
    run = s.run
    for i, name in enumerate(("One", "Two", "Three")):
        run("create_node", "annotation_rect", name, attrs={"translate": [i * 200, 0], "size": [100, 100]})
    tabs = run("interactions.make_tabs", ["One", "Two", "Three"])
    assert len(tabs) == 3 and [_vis(s, n) for n in ("One", "Two", "Three")] == [1, 0, 0]
    assert s.scene.get_attr(tabs[0], "action_on")
    run("interactions.click", tabs[2])
    assert [_vis(s, n) for n in ("One", "Two", "Three")] == [0, 0, 1]
    assert not s.scene.get_attr(tabs[0], "action_on") and s.scene.get_attr(tabs[2], "action_on")
    run("interactions.click", tabs[2])  # tabs don't toggle: still showing
    assert _vis(s, "Three") == 1
    for name in ("One", "Two", "Three"):
        run("set_attr", name, "visibility", 1)
    reveal = run("interactions.make_reveal", ["Three"])
    assert _vis(s, "Three") == 0
    run("interactions.click", reveal)
    assert _vis(s, "Three") == 1
    compare = run("interactions.make_before_after", ["One", "Two"])
    assert (_vis(s, "One"), _vis(s, "Two")) == (1, 0)
    run("interactions.click", compare)
    assert (_vis(s, "One"), _vis(s, "Two")) == (0, 1)
    run("interactions.click", compare)
    assert (_vis(s, "One"), _vis(s, "Two")) == (1, 0)


def test_unused_scenes_are_unchanged_and_actions_survive_saving(tmp_path=None):
    s = headless()
    run = s.run
    run("create_node", "annotation_rect", "Box")
    text = s.serialize()
    assert "on_click" not in text and "action_on" not in text, "sparse attributes: nothing stored when unused"
    button = run("interactions.create_button")
    run("interactions.connect", button, "Box", "hide")
    data = json.loads(s.serialize())
    entry = next(n for n in data["nodes"] if n["name"] == button)
    box_id = s.scene.get_node("Box").id
    assert entry["attrs"]["on_click"] == [{"command": "set_attrs", "args": [box_id, {"visibility": 0}],
                                           "back": [{"command": "set_attrs", "args": [box_id, {"visibility": 1}]}]}]
    other = headless()
    other.load_text(s.serialize())
    other.run("interactions.click", button)
    assert _vis(other, "Box") == 0


# ---- In the app ---------------------------------------------------------------------------------


def _center(h, name):
    item = h.item(name)
    return h.view.mapFromScene(item.sceneBoundingRect().center())


def test_only_a_click_on_a_locked_item_runs_its_action():
    h = make()
    try:
        h.create("annotation_rect", "Answer", size=[100, 60], translate=[300, 0])
        button = h.run("interactions.create_button", 0, 0, "Show")
        h.run("interactions.connect", button, "Answer", "show")
        h.run("select", clear=True)
        h.refresh()
        h.click(_center(h, button))
        assert h.attr("Answer", "visibility") in (0, False), "unlocked: a click selects it for editing"
        assert h.sel() == [button]
        assert not h.session.commands.exists("interactions.interact"), "no Interact mode"
        h.run("set_attr", button, "locked", 1)
        h.run("select", clear=True)
        h.refresh()
        h.click(_center(h, button))
        assert h.attr("Answer", "visibility") in (1, True), "locked: a plain click runs it"
        h.click(_center(h, button))
        assert h.attr("Answer", "visibility") in (0, False), "and a second click puts it back"
        assert h.sel() == []
    finally:
        h.close()


def test_demonstrate_records_a_gif_frame_and_the_view():
    h = make()
    try:
        path = make_gif("Interact.gif", 40, 20, [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)])
        h.create("image", "Anim", path=path.as_posix(), translate=[0, 0])
        button = h.run("interactions.create_button", 0, 200, "Frame 3")
        h.refresh()
        h.run("playback.go_to_frame", 1, ["Anim"])
        h.run("interactions.demonstrate", button)
        h.run("playback.go_to_frame", 3, ["Anim"])
        h.service("interactions.canvas").include_view = True
        result = h.run("interactions.finish_ui")
        assert result["steps"] == 2
        sentences = h.run("interactions.describe", button)
        assert sentences[0] == "Go to frame 3 · Anim" and sentences[1].startswith("Fly to view marker"), sentences
        h.refresh()
        assert h.run("playback.info", "Anim")["frame"] == 1, "the board went back to frame 1"
        h.run("interactions.click", button)
        assert h.run("playback.info", "Anim")["frame"] == 3
        h.run("interactions.click", button)
        assert h.run("playback.info", "Anim")["playing"] is False or h.run("playback.info", "Anim")["frame"] == 1
    finally:
        h.close()


def test_dragging_the_handle_onto_an_item_offers_its_verbs():
    h = make()
    try:
        h.create("annotation_rect", "Target", size=[100, 60], translate=[400, 0])
        button = h.run("interactions.create_button", 0, 0, "Go")
        h.select([button])
        h.refresh()
        controller = h.service("interactions.canvas")
        offered = []
        controller.offer_verbs = lambda source, target, _pos: offered.append((source, target))
        start = controller.handle_center(h.view)[1].toPoint()
        h.drag(start, _center(h, "Target"))
        assert offered == [(button, "Target")]
        verbs = [v["id"] for v in h.run("interactions.verbs", "Target")]
        assert "fly_to" in verbs and "show" in verbs
        h.run("interactions.connect", button, "Target", "fly_to")
        assert h.run("interactions.click", button)["ran"] == 1
        APP.processEvents()
    finally:
        h.close()


def test_the_quick_bar_has_run_and_reset():
    from PySide6.QtCore import QSettings
    from PySide6.QtTest import QTest

    from pixelref.builtins.quick_edit.ui import QuickBar

    QSettings().remove("plugins/quick_edit")
    h = make()
    try:
        h.create("annotation_rect", "Answer", size=[100, 60], translate=[300, 0])
        button = h.run("interactions.create_button", 0, 0, "Show")
        h.run("interactions.connect", button, "Answer", "show")
        h.select([button])
        h.refresh()
        QTest.qWait(5)
        APP.processEvents()
        bars = [b for b in h.view.findChildren(QuickBar) if b.isVisible()]
        controls = bars[0].sections["click_action"][1]
        assert controls.isVisible() and not controls.reset_button.isEnabled()
        QTest.mouseClick(controls.run_button, Qt.LeftButton)
        assert h.attr("Answer", "visibility") in (1, True)
        controls.refresh([button])
        QTest.mouseClick(controls.reset_button, Qt.LeftButton)
        assert h.attr("Answer", "visibility") in (0, False)
    finally:
        h.close()


def test_a_failed_click_shows_an_amber_outline_the_reason_and_the_log():
    h = make()
    try:
        h.create("annotation_rect", "Answer", size=[100, 60], translate=[300, 0])
        h.create("annotation_rect", "Other", size=[100, 60], translate=[300, 200])
        button = h.run("interactions.create_button", 0, 0, "Show")
        h.run("interactions.connect", button, "Answer", "show")
        h.run("interactions.connect", button, "Other", "hide")
        controller = h.service("interactions.canvas")
        h.run("select", clear=True)
        h.refresh()
        assert controller.problem_items() == [] and controller.status(button) == "ok"
        h.run("delete", ["Answer"])
        h.refresh()
        assert controller.problem_items() == [button], "a step that can't run shows without running it"
        assert controller.status(button) == "broken"
        result = h.run("interactions.click", button)
        assert result["status"] == "partial" and controller.status(button) == "partial"
        assert "Last run at" in controller.problem_text(button)
        h.select([button])
        h.refresh()
        APP.processEvents()
        section = h.section("click_action")
        assert "1 of 2 steps ran" in section.last_label.text() and section.log_button.isVisible()
        h.run("interactions.show_log", button)
        dialog = next(w for w in APP.topLevelWidgets() if w.windowTitle() == "Click Action Log" and w.isVisible())
        text = dialog.text.toPlainText()
        assert "partly failed: 1 of 2 steps ran" in text and "the item it acts on was deleted" in text
        dialog.only_problems.setChecked(True)
        h.run("interactions.clear_log")
        assert "Nothing yet" in dialog.text.toPlainText()
        dialog.close()
    finally:
        h.close()
