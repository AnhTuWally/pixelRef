"""Workspaces: built-ins, adding/removing, saving them with the scene, templates."""

from __future__ import annotations

import json

from harness import make, tmp_dir


def titles(h) -> list[str]:
    wm = h.app.wm
    return [wm.doc.attr(w, "title") for w in wm.doc.workspaces()]


def test_builtin_workspaces():
    h = make()
    try:
        assert titles(h) == ["Board", "Review", "Present"]
    finally:
        h.close()


def test_add_rename_and_delete_workspaces():
    h = make()
    try:
        wm, session = h.app.wm, h.session
        session.mark_saved()
        name = h.app.run_command("wm.new_workspace", "Paint-over")
        assert wm.doc.attr(name, "number") == 4 and wm.doc.attr(name, "title") == "Paint-over"
        assert wm.focused_workspace() == name, "a new workspace is shown"
        assert [wm.doc.attr(t, "view_type") for t in wm.doc.tiles(name)] == ["canvas"]
        assert session.unsaved, "workspaces belong to the file"
        h.app.run_command("wm.rename_workspace", name, "Lighting")
        assert "Lighting" in titles(h)
        h.app.run_command("wm.delete_workspace", name)
        assert "Lighting" not in titles(h) and wm.focused_workspace() in wm.doc.workspaces()
        assert all(wm.doc.has(t) for t in wm.frames), "its views are gone"
        for workspace in wm.doc.workspaces()[1:]:
            h.app.run_command("wm.delete_workspace", workspace)
        assert len(wm.doc.workspaces()) == 1
        assert h.app.run_command("wm.delete_workspace") is None, "the last one stays (error reported)"
        assert len(wm.doc.workspaces()) == 1
        # The bar has a "+" button and the menus list what can be done.
        window = wm.windows[wm.doc.primary_output()]
        buttons = [b.text() for b in window.bar.workspaces.findChildren(type(window.bar.workspaces.children()[1]))
                   if hasattr(b, "text")]
        assert "+" in buttons
        from PySide6.QtWidgets import QMenu

        menu = QMenu()
        wm.fill_workspaces_menu(menu)
        texts = [a.text() for a in menu.actions()]
        assert "New Workspace" in texts and any(t.startswith("Rename") for t in texts)
    finally:
        h.close()


def test_workspaces_are_saved_with_the_scene_and_restored_on_open():
    h = make()
    try:
        wm = h.app.wm
        h.add_image("Img", 0, 0)
        name = h.app.run_command("wm.new_workspace", "Detail")
        h.app.run_command("wm.split_view", "right")
        h.app.run_command("wm.rename_workspace", "Review", "Compare")
        path = tmp_dir() / "with_workspaces.pxref"
        assert h.save_to(path)
        section = h.saved_json(path)["workspaces"]
        assert [w["title"] for w in section["workspaces"]] == ["Board", "Compare", "Present", "Detail"]
        assert section["current"] == name
        detail = section["workspaces"][3]
        assert detail["root"]["split"] == "h" and len(detail["root"]["children"]) == 2
        assert "camera" in detail["root"]["children"][0]["state"], "each view's camera is kept"

        h.session.mark_saved()
        h.run("new_scene")
        assert titles(h) == ["Board", "Review", "Present"], "a new scene starts with the defaults"

        assert h.run("file.open_path", str(path), confirm=False)  # (h.open() focuses the Main view)
        assert titles(h) == ["Board", "Compare", "Present", "Detail"]
        current = wm.focused_workspace() or wm.doc.attr(wm.doc.primary_output(), "workspace")
        assert wm.doc.attr(current, "title") == "Detail", "the workspace shown when saved is shown"
        assert len([t for t in wm.doc.tiles(current) if wm.doc.attr(t, "view_type") == "canvas"]) == 2
        assert not h.session.unsaved
    finally:
        h.close()


def test_files_without_workspaces_keep_the_current_ones():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        plain = tmp_dir() / "no_workspaces.pxref"
        assert h.save_to(plain)
        data = h.saved_json(plain)
        data.pop("workspaces")
        plain.write_text(json.dumps(data), encoding="utf-8")
        h.app.run_command("wm.new_workspace", "Mine")
        assert h.open(plain)
        assert "Mine" in titles(h), "no workspaces in the file: keep the current ones"
    finally:
        h.close()


def test_workspace_templates_save_apply_and_create():
    h = make()
    try:
        wm = h.app.wm
        # A template from Review: two linked canvases; cameras are not part of a template.
        h.app.run_command("wm.workspace", "Review")
        assert h.app.run_command("wm.save_workspace_template", "Two up") == "Two up"
        template = wm.template("Two up")
        assert all("camera" not in c["state"] for c in template["root"]["children"])
        assert {"name": "Two up", "builtin": False} in h.run("wm.workspace_templates")

        # New workspace from it: tile names clash with Review's, links follow the new names.
        name = h.app.run_command("wm.new_workspace", None, "Two up")
        assert wm.doc.attr(name, "title") == "Two up"
        tiles = wm.doc.tiles(name)
        assert len(tiles) == 2
        first, second = tiles
        group = wm.doc.attr(first, "state")["link_group"]
        assert group and wm.doc.attr(second, "state")["link_group"] == group, "the two views stay linked"

        # Apply a template to the current workspace: layout replaced, number and title kept.
        number = wm.doc.attr(name, "number")
        h.app.run_command("wm.apply_workspace_template", "Present", name)
        assert wm.doc.attr(name, "number") == number and wm.doc.attr(name, "title") == "Two up"
        assert wm.doc.attr(name, "window_style") == {"panels": False}
        assert len(wm.doc.tiles(name)) == 1 and not any(t in wm.frames for t in tiles)

        # Yours can replace a built-in one, and deleting it brings the built-in back.
        h.app.run_command("wm.workspace", "Board")
        h.app.run_command("wm.split_view", "right")
        h.app.run_command("wm.save_workspace_template", "Review")
        assert len(wm.template("Review")["root"]["children"]) == 2
        assert wm.template("Review")["root"]["children"][0]["name"] == "Main"
        h.app.run_command("wm.delete_workspace_template", "Review")
        assert wm.template("Review")["root"]["children"][0]["name"] == "ReviewColor"
        h.app.run_command("wm.delete_workspace_template", "Two up")
        assert "Two up" not in wm.templates()
    finally:
        h.close()
