"""The Tools palette: the user's layout (order, sections, hidden, added commands) and keys."""

from __future__ import annotations

from types import SimpleNamespace

from harness import make, tmp_dir
from PySide6.QtCore import QSettings, Qt

from pixelref.builtins.tool_palette.layout import Section, merge
from pixelref.ui import keymap_file


def fresh():
    QSettings().remove("plugins/tool_palette")
    h = make()
    path = tmp_dir() / "keymap_palette.toml"
    if path.exists():
        path.unlink()
    h.app.keymap.user_file = path
    return h


def shown(h) -> list[tuple[str, list[str]]]:
    return [(s.id, s.items) for s in h.service("tool_palette").sections()]


def tools_menu_texts(h) -> list[str]:
    menu = h.app.build_context_menu(h.view)
    tools = next(a.menu() for a in menu.actions() if a.menu() is not None and a.text() == "Tools")
    return [a.text() for a in tools.actions() if not a.isSeparator()]


def test_merge_puts_new_default_buttons_next_to_their_neighbours():
    user = [Section("mine", "Mine", ["tool.pen"]), Section("tools", "Tools", ["tool.rect", "tool.select"])]
    merged = merge(user, {"tools": ["tool.select", "tool.rect", "tool.new", "tool.pen", "tool.last"],
                          "add": ["image.insert"]})
    assert merged[0].items == ["tool.pen"], "moved buttons stay where the user put them"
    assert merged[1].items == ["tool.rect", "tool.new", "tool.last", "tool.select"]
    assert merged[2].id == "add" and merged[2].items == ["image.insert"], "a missing home section comes back"
    first = merge([Section("tools", "Tools", ["tool.b"])], {"tools": ["tool.a", "tool.b"]})
    assert first[0].items == ["tool.a", "tool.b"], "before the next neighbour when no earlier one is there"


def test_default_palette_comes_from_the_plugins():
    h = fresh()
    try:
        sections = dict(shown(h))
        assert list(sections) == ["tools", "add"]
        assert sections["tools"][:3] == ["tool.select", "tool.rect", "tool.arrow"]
        assert sections["add"] == ["image.insert", "text.add", "grouping.add_group"]
        assert not h.service("tool_palette").layout.customized()
        assert tools_menu_texts(h)[:2] == ["Select Tool", "Rectangle Tool"]
        assert tools_menu_texts(h)[-1] == "Customize Tools Palette…"
    finally:
        h.close()


def test_reorder_hide_sections_and_added_commands_drive_palette_and_tools_menu():
    h = fresh()
    try:
        palette = h.palette
        h.run("tool_palette.move", "tool.pen", "tools", 0)
        assert shown(h)[0][1][0] == "tool.pen"
        assert tools_menu_texts(h)[0].startswith("Pen"), "the Tools menu follows the palette"
        h.run("tool_palette.hide", "tool.color_picker")
        assert "tool.color_picker" not in palette.buttons and "color_picker" not in palette.tool_buttons
        assert not any("Pick" in t for t in tools_menu_texts(h))
        assert h.app.keymap.targets_of("I") == ["tool.color_picker"], "hidden tools keep their key"

        sid = h.run("tool_palette.add_section", "Annotate")
        h.run("tool_palette.move", "tool.arrow", sid)
        h.run("tool_palette.add", "navigation.frame_all", sid)
        assert dict(shown(h))[sid] == ["tool.arrow", "navigation.frame_all"]
        frame = palette.buttons["navigation.frame_all"]
        assert frame.shortcut_text() == h.app.actions.shortcut_label("navigation.frame_all") != ""
        assert not frame.isCheckable() and frame.text() == "Frame All"

        # The active tool's options sit under its section.
        h.run("tools.set", "arrow")
        boxes = {s: box for s, box, _g, _b in palette.section_widgets}
        assert palette.layout_.indexOf(palette.tool_options) == palette.layout_.indexOf(boxes[sid]) + 1

        # Kept across restarts (user settings).
        layout = h.run("tool_palette.layout")
        assert "tool.color_picker" in layout["hidden"]
        assert [s["id"] for s in layout["sections"]] == ["tools", "add", sid]

        h.run("tool_palette.remove", "navigation.frame_all")
        assert "navigation.frame_all" not in h.service("tool_palette").all_keys(), "added commands go"
        h.run("tool_palette.remove_section", sid)
        assert "tool.arrow" in h.run("tool_palette.layout")["hidden"], "a removed section's tools go home hidden"
        h.run("tool_palette.show", "tool.arrow")
        h.run("tool_palette.reset")
        assert dict(shown(h))["tools"][:3] == ["tool.select", "tool.rect", "tool.arrow"]
    finally:
        h.close()


def test_a_plugin_tool_keeps_its_place_and_new_tools_fit_into_a_custom_layout():
    from pixelref.plugin_api.ui import Tool

    h = fresh()
    try:
        h.run("tool_palette.move", "tool.rect", "tools", -1)  # customize: Rect last
        h.session.plugins.set_enabled("rect", False)
        assert "tool.rect" not in dict(shown(h))["tools"]
        h.session.plugins.set_enabled("rect", True)
        assert dict(shown(h))["tools"][-1] == "tool.rect", "back where the user put it"

        class Stamp(Tool):
            id = "stamp"
            title = "Stamp"
            order = 21.0  # after Arrow (20) by default
            shortcuts = ("Shift+S",)

        disposable = h.app.tools.register(Stamp(SimpleNamespace(app=h.app)))
        tools = dict(shown(h))["tools"]
        assert tools[tools.index("tool.arrow") + 1] == "tool.stamp", tools
        assert h.palette.buttons["tool.stamp"].shortcut_text() == "⇧S"
        disposable.dispose()
        assert "tool.stamp" not in h.palette.buttons
    finally:
        h.close()


def test_setting_a_key_from_the_palette_writes_the_keymap_and_takes_it_over():
    h = fresh()
    try:
        keymap = h.app.keymap
        assert h.run("keymap.set_shortcut", "tool.pen", "B") == ["B"]
        assert h.palette.buttons["tool.pen"].shortcut_text() == "B", "the label follows the keymap"
        data = keymap_file.load(keymap.user_path())
        assert {"key": "B", "command": "tool.pen"} in data["bind"]
        assert {"key": "P", "command": "tool.pen"} in data["unbind"]

        # Taking R from Rect leaves Rect without a key.
        h.run("keymap.set_shortcut", "tool.pen", "R")
        assert keymap.targets_of("R") == ["tool.pen"] and h.palette.buttons["tool.rect"].shortcut_text() == ""
        assert h.run("keymap.reset_shortcut", "tool.pen") == ["P"]
        h.run("keymap.reset_shortcut", "tool.rect")
        assert keymap.targets_of("R") == ["tool.rect"]

        menu = h.palette.build_menu("tool.pen")
        texts = [a.text() for a in menu.actions()]
        for entry in ("Set Shortcut…", "Remove Shortcut", "Move to", "Hide from Palette", "Add Command…",
                      "Customize Palette…"):
            assert entry in texts, (entry, texts)
    finally:
        h.close()


def test_customize_dialog_edits_the_layout():
    from pixelref.builtins.tool_palette.customize import CustomizeDialog

    h = fresh()
    try:
        service = h.service("tool_palette")
        dialog = CustomizeDialog(service.ui, service)
        tree = dialog.tree
        tools = tree.topLevelItem(0)
        assert tools.child(0).data(0, Qt.UserRole + 1) == "tool.select"
        assert tools.child(1).text(1) == "R"

        tree.setCurrentItem(tools.child(1))  # Rect down one
        dialog.move_current(1)
        assert dict(shown(h))["tools"][:3] == ["tool.select", "tool.arrow", "tool.rect"]

        tools.child(0).setCheckState(0, Qt.Unchecked)  # hide Select
        assert "tool.select" in h.run("tool_palette.layout")["hidden"]

        # Drops: buttons between buttons or onto a section, sections between sections.
        positions = type(tree).DropIndicatorPosition
        add = tree.topLevelItem(1)
        assert tree.drop_allowed(tools.child(0), add, positions.OnItem)
        assert not tree.drop_allowed(tools.child(0), add, positions.AboveItem)
        assert not tree.drop_allowed(tools.child(0), tools.child(1), positions.OnItem)
        assert tree.drop_allowed(add, tools, positions.AboveItem)
        assert not tree.drop_allowed(add, tools.child(0), positions.AboveItem)

        add.setText(0, "Create")  # rename a section
        assert h.run("tool_palette.layout")["sections"][1]["title"] == "Create"
        dialog.close()
    finally:
        h.close()
