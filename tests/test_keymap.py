"""Keyboard shortcuts: framing keys, keymap.toml, the shortcut editor, scripts and macros."""

from __future__ import annotations

import os

from harness import make, tmp_dir

from pixelref.ui import keymap_file


def _write(path, text: str):
    path.write_text(text, encoding="utf-8")
    return path


def test_f_frames_the_selection_and_shift_f_frames_everything():
    h = make()
    try:
        keymap = h.app.keymap
        assert keymap.targets_of("F") == ["navigation.frame_selected"]
        assert keymap.targets_of("Shift+F") == ["navigation.frame_all"]
        assert h.app.actions.shortcut_label("navigation.frame_selected", compact=False) == "F"
        assert not keymap.conflicts()
    finally:
        h.close()


def test_keymap_file_binds_unbinds_macros_modes_and_reloads():
    h = make()
    try:
        keymap = h.app.keymap
        h.add_image("Img", 0, 0, w=40, h=40)
        path = _write(tmp_dir() / "keymap_a.toml", '''
[[unbind]]
key = "Shift+H"
command = "transform.flip_h"

[[bind]]
key = "Ctrl+Alt+Shift+F"
command = "transform.flip_h"

[[bind]]
key = "Ctrl+Alt+9"
name = "Rotate and frame"
commands = ["transform.rotate_cw", "navigation.frame_all"]

[[bind]]
key = "Ctrl+Alt+8"
name = "Python rotate"
python = "pxcmd.set_transform(sel(), rotate=10, relative=True)"

[[mode]]
name = "macros"
key = "Ctrl+Space"
title = "Macros"

[[bind]]
mode = "macros"
key = "1"
command = "transform.rotate_cw"
''')
        assert keymap.load_user_file(path) == []
        assert "transform.flip_h" not in keymap.targets_of("Shift+H")
        assert keymap.targets_of("Ctrl+Alt+Shift+F") == ["transform.flip_h"]
        assert keymap.targets_of("Ctrl+Space") == ["keymap.mode.macros"]
        assert [b.target for b in keymap.bindings_for_sequence("1", mode="macros")] == ["transform.rotate_cw"]

        # A command-list macro: one undo step.
        h.select(["Img"])
        macro = keymap.targets_of("Ctrl+Alt+9")[0]
        assert macro.startswith("user.") and h.app.actions.specs[macro].category == "User Hotkeys"
        h.app.trigger(macro)
        assert abs(h.attr("Img", "rotate") - 90.0) < 1e-6
        h.session.undo.undo()
        assert abs(h.attr("Img", "rotate")) < 1e-6
        # Python runs in the console namespace.
        h.app.trigger(keymap.targets_of("Ctrl+Alt+8")[0])
        assert abs(h.attr("Img", "rotate") - 10.0) < 1e-6

        # Reloading a different file drops everything the first one added.
        other = _write(tmp_dir() / "keymap_b.toml", '[[bind]]\nkey = "Ctrl+Alt+7"\ncommand = "transform.flip_v"\n')
        assert keymap.load_user_file(other) == []
        assert "transform.flip_h" in keymap.targets_of("Shift+H")
        assert not keymap.targets_of("Ctrl+Alt+9") and not keymap.targets_of("Ctrl+Space")
        assert "keymap.mode.macros" not in h.app.actions and "macros" not in keymap.mode_titles
        assert keymap.targets_of("Ctrl+Alt+7") == ["transform.flip_v"]

        bad = _write(tmp_dir() / "keymap_c.toml", '[[bind]]\nkey = "Ctrl+Alt+6"\n')
        assert keymap.load_user_file(bad) and not keymap.targets_of("Ctrl+Alt+6")
    finally:
        h.close()


def test_a_failing_macro_step_undoes_the_whole_macro():
    h = make()
    try:
        h.add_image("Img", 0, 0, w=40, h=40)
        h.select(["Img"])
        before = h.attr("Img", "rotate")
        h.app.run_command("macros.run", commands=["transform.rotate_cw", "no.such_command"], label="Broken")
        assert h.attr("Img", "rotate") == before, "the first step is rolled back"
    finally:
        h.close()


def test_removed_builtin_keys_stay_removed_when_their_plugin_is_reenabled():
    h = make()
    try:
        keymap = h.app.keymap
        path = _write(tmp_dir() / "keymap_d.toml", '[[unbind]]\nkey = "Shift+H"\ncommand = "transform.flip_h"\n')
        keymap.load_user_file(path)
        plugins = h.session.plugins
        plugins.set_enabled("transform", False)
        plugins.set_enabled("transform", True)
        assert plugins.get("transform").state == "active"
        assert "transform.flip_h" not in keymap.targets_of("Shift+H")
        assert keymap.default_sequences("transform.flip_h") == ["Shift+H"]
    finally:
        h.close()


def test_shortcut_editor_assigns_steals_resets_and_writes_the_file():
    h = make()
    try:
        app, keymap = h.app, h.app.keymap
        path = tmp_dir() / "keymap_editor.toml"
        if path.exists():
            path.unlink()
        keymap.user_file = path
        app.run_command("help.show", "duplicate")
        from PySide6.QtWidgets import QApplication

        dialog = next(w for w in QApplication.topLevelWidgets() if hasattr(w, "editor"))
        editor = dialog.editor
        tree = editor.tree
        visible = [tree.topLevelItem(i).child(j) for i in range(tree.topLevelItemCount())
                   for j in range(tree.topLevelItem(i).childCount()) if not tree.topLevelItem(i).child(j).isHidden()]
        assert visible and all("duplicate" in (item.text(0) + item.text(2)).lower() for item in visible)

        # Give Duplicate the key of Flip Horizontal (taking it over), then check the file.
        editor.assign("clipboard.duplicate", ["Shift+H"], [("Shift+H", "transform.flip_h")])
        assert keymap.targets_of("Shift+H") == ["clipboard.duplicate"]
        assert keymap.targets_of("Ctrl+D") == []
        data = keymap_file.load(path)
        assert {"key": "Shift+H", "command": "clipboard.duplicate"} in data["bind"]
        assert {"key": "Ctrl+D", "command": "clipboard.duplicate"} in data["unbind"]
        assert {"key": "Shift+H", "command": "transform.flip_h"} in data["unbind"]
        assert editor.changed("clipboard.duplicate") and editor.changed("transform.flip_h")

        editor._edit(lambda data: keymap_file.reset(data, "clipboard.duplicate"))
        assert keymap.targets_of("Ctrl+D") == ["clipboard.duplicate"]
        assert "transform.flip_h" not in keymap.targets_of("Shift+H"), "flip_h's removal is its own entry"

        editor._edit(keymap_file.reset_all)
        assert keymap.targets_of("Shift+H") == ["transform.flip_h"]
        assert "bind" not in keymap_file.load(path) and "unbind" not in keymap_file.load(path)
        dialog.close()
    finally:
        h.close()


def test_keymap_file_round_trips_through_dumps():
    data = {"leader": {"window": "Ctrl+W"},
            "mode": [{"name": "macros", "key": "Ctrl+Space", "sticky": True}],
            "bind": [{"key": "F5", "python": 'print("hi")\nprint(\'x\')', "name": "Say \"hi\""},
                     {"key": "Ctrl+1", "commands": ["a.b", "c"]}],
            "unbind": [{"key": "Ctrl+D", "command": "clipboard.duplicate"}]}
    import tomllib

    assert tomllib.loads(keymap_file.dumps(data)) == data


def test_scripts_become_actions_and_recorded_macros_replay():
    h = make()
    folder = tmp_dir() / "scripts_test"
    folder.mkdir(exist_ok=True)
    saved = None
    old_path = os.environ.get("PIXELREF_SCRIPT_PATH")
    os.environ["PIXELREF_SCRIPT_PATH"] = str(folder)
    try:
        _write(folder / "spin_it.py", '"""Spin the selection"""\nfor n in sel():\n    pxcmd.set_transform(n, rotate=30, relative=True)\n')
        _write(folder / "broken.py", 'pxcmd.set_transform(sel(), rotate=5, relative=True)\nraise ValueError("nope")\n')
        _write(folder / "_private.py", "")
        _write(folder / "userSetup.py", "")
        keys = h.run("macros.reload")
        assert "script.spin_it" in keys and "script.broken" in keys
        assert "script._private" not in keys and "script.usersetup" not in keys
        assert h.app.actions["script.spin_it"].text() == "Spin the selection"
        assert h.app.actions.specs["script.spin_it"].category == "Scripts"

        h.add_image("Img", 0, 0, w=40, h=40)
        h.select(["Img"])
        h.app.trigger("script.spin_it")
        assert abs(h.attr("Img", "rotate") - 30.0) < 1e-6
        h.session.undo.undo()
        assert abs(h.attr("Img", "rotate")) < 1e-6
        h.app.trigger("script.broken")
        assert abs(h.attr("Img", "rotate")) < 1e-6, "a failing script is undone"

        # Record: rotate twice, then save; replaying repeats it.
        assert h.app.run_command("macros.record") is None
        assert h.app.actions["macros.record"].isChecked()
        h.app.run_command("set_transform", "Img", rotate=15, relative=True)
        h.app.run_command("set_transform", "Img", rotate=15, relative=True)
        h.app.run_command("no_such_command_at_all")  # failures are not recorded
        key = h.app.run_command("macros.record", "Two turns")
        assert key in h.app.actions and h.app.actions[key].text() == "Two turns"
        saved = h.run("macros.list")[key]
        text = open(saved, encoding="utf-8").read()
        assert text.count("pxcmd.set_transform(") == 2 and "no_such_command" not in text
        h.app.trigger(key)
        assert abs(h.attr("Img", "rotate") - 60.0) < 1e-6

        # The bar shows a REC pill while recording; pause skips steps, steps can be taken back.
        block = h.app.bar_block("macroBlock")
        assert block is not None and block.record_btn.isVisibleTo(block) and not block.pill.isVisibleTo(block)
        h.app.run_command("macros.record")
        assert block.pill.isVisibleTo(block) and not block.record_btn.isVisibleTo(block)
        h.app.run_command("set_transform", "Img", rotate=1, relative=True)
        assert "1 step" in block.text_btn.text()
        assert h.app.run_command("macros.pause") is True
        h.app.run_command("set_transform", "Img", rotate=2, relative=True)  # paused: not recorded
        assert "PAUSED" in block.text_btn.text() and "1 step" in block.text_btn.text()
        assert h.app.run_command("macros.pause") is False
        h.app.run_command("set_transform", "Img", rotate=3, relative=True)
        assert "rotate=3" in h.app.run_command("macros.remove_last")
        assert "1 step" in block.text_btn.text()
        h.app.active_view().viewport().repaint()  # the recording frame paints without errors
        h.app.run_command("macros.discard")
        assert not block.pill.isVisibleTo(block) and not h.app.actions["macros.record"].isChecked()
        assert len(h.run("macros.list")) == len(keys) + 1, "a discarded recording saves nothing"

        # Scripts can be bound like any action.
        h.app.keymap.bind("Ctrl+Alt+5", "script.spin_it", user=True)
        assert h.app.actions.shortcut_label("script.spin_it", compact=False) == "Ctrl+Alt+5"
    finally:
        if old_path is None:
            os.environ.pop("PIXELREF_SCRIPT_PATH", None)
        else:
            os.environ["PIXELREF_SCRIPT_PATH"] = old_path
        if saved:
            os.remove(saved)
        h.close()
