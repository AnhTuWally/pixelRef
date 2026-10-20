"""Panels and chrome: the Attribute Editor, background settings, zoom controls,
menus and command search, the tool palette, and the window manager."""

from __future__ import annotations

from harness import APP, QTest, approx, make, tmp_dir
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication


def choose(combo, value) -> None:
    combo.setCurrentIndex(combo.findData(value))


def commit_spin(spin, value) -> None:
    spin.setValue(value)
    spin.editingFinished.emit()


# ---- Attribute Editor ---------------------------------------------------------------------------


def test_inspector_header_rename_and_toggles():
    h = make()
    try:
        h.add_image("Photo", 0, 0, w=200, h=100)
        h.select(["Photo"])
        insp = h.inspector
        assert insp.name_edit.isVisible() and insp.name_edit.text() == "Photo"
        assert insp.type_label.text().startswith("Image · 200 × 100 px"), insp.type_label.text()
        insp.name_edit.setText("Hero shot")
        insp.name_edit.editingFinished.emit()
        QTest.qWait(10)
        assert h.has("Hero shot") and h.sel() == ["Hero shot"]
        QTest.mouseClick(insp.header_lock_btn, Qt.LeftButton)
        assert h.attr("Hero shot", "locked") == 1
        assert "locked" in insp.type_label.text() and insp.header_lock_btn.isChecked()
        QTest.mouseClick(insp.header_visible_btn, Qt.LeftButton)
        assert h.attr("Hero shot", "visibility") == 0
    finally:
        h.close()


def test_inspector_size_scale_flip_and_opacity():
    h = make()
    try:
        h.add_image("Img", 0, 0, w=200, h=100)
        h.select(["Img"])
        xf, look = h.section("transform"), h.section("appearance")
        assert (xf.width_spin.value(), xf.height_spin.value(), xf.scale_pct_spin.value()) == (200.0, 100.0, 100.0)
        commit_spin(xf.width_spin, 400.0)  # aspect lock on: both ways
        assert h.attr("Img", "scale") == [2.0, 2.0]
        assert xf.height_spin.value() == 200.0 and xf.scale_pct_spin.value() == 200.0
        xf.aspect_lock_btn.setChecked(False)  # unlocked: only the height changes
        commit_spin(xf.height_spin, 100.0)
        assert h.attr("Img", "scale") == [2.0, 1.0]
        commit_spin(xf.scale_pct_spin, 50.0)  # a scale % makes it uniform again
        assert h.attr("Img", "scale") == [0.5, 0.5]
        QTest.mouseClick(xf.flip_h_btn, Qt.LeftButton)
        assert h.attr("Img", "scale")[0] == -0.5 and xf.flip_h_btn.isChecked()
        assert xf.scale_pct_spin.value() == 50.0, "flipping keeps the scale"
        assert look.opacity_spin.value() == 100.0 and look.opacity_slider.value() == 100
        commit_spin(look.opacity_spin, 40.0)
        assert h.attr("Img", "opacity") == 0.4 and look.opacity_slider.value() == 40
        look.opacity_slider.setValue(75)  # click on the track / keyboard
        assert h.attr("Img", "opacity") == 0.75
    finally:
        h.close()


def test_inspector_scrub_label_and_collapsed_sections():
    from pixelref.ui.widgets import InspectorSection, ScrubLabel

    h = make()
    try:
        h.add_image("Img", 0, 0, w=200, h=100)
        h.select(["Img"])
        label = next(lbl for lbl in h.section("transform").findChildren(ScrubLabel) if lbl.text() == "X")
        depth = len(h.session.undo.stack.labels())
        QTest.mousePress(label, Qt.LeftButton, Qt.NoModifier, QPoint(2, 2))
        for x in range(2, 42, 5):  # drag 35px to the right (1 unit per px)
            move = QMouseEvent(QEvent.MouseMove, QPointF(x, 2), QPointF(label.mapToGlobal(QPoint(x, 2))),
                               Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
            QApplication.sendEvent(label, move)
        QTest.mouseRelease(label, Qt.LeftButton, Qt.NoModifier, QPoint(37, 2))
        assert approx(h.attr("Img", "translate")[0], 35, 1.5), h.attrs("Img")
        assert len(h.session.undo.stack.labels()) == depth + 1, "one scrub = one undo step"
        section = h.section("image")
        assert isinstance(section, InspectorSection) and section.header.isChecked()
        QTest.mouseClick(section.header, Qt.LeftButton)
        assert not section.body.isVisible()
        assert "image" in h.app.settings.value("plugins/inspector/collapsed")
        h2 = make()
        try:
            assert not h2.section("image").header.isChecked(), "collapsed sections are remembered"
        finally:
            h2.close()
        QTest.mouseClick(section.header, Qt.LeftButton)
        assert section.body.isVisible()
    finally:
        h.app.settings.remove("plugins/inspector/collapsed")
        h.close()


def test_inspector_multi_selection_summary_and_align():
    h = make()
    try:
        h.add_image("A", 0, 0, w=100, h=50)
        h.add_image("B", 300, 80, w=100, h=50)
        h.create("annotation_text", "Note", text="x", translate=[0.0, 300.0])
        h.run("select.all")
        insp = h.inspector
        assert insp.selection_label.text() == "3 items selected" and not insp.name_edit.isVisible()
        assert "2 images" in insp.type_label.text() and "1 text note" in insp.type_label.text(), insp.type_label.text()
        assert h.section("arrange").isVisible() and not h.section("transform").isVisible()
        top = next(b for b in h.section("arrange")._align_buttons if b.toolTip() == "Align top edges")
        QTest.mouseClick(top, Qt.LeftButton)
        tops = {round(h.item(n).scene_content_rect().top(), 2) for n in ("A", "B", "Note")}
        assert len(tops) == 1, tops
    finally:
        h.close()


def test_attribute_editor_is_generated_from_the_schema():
    from pixelref.plugin_api import Attr

    h = make()
    try:
        h.add_image("Img", 0, 0)
        h.run("add_attr", "Img", Attr("rating", "int", 3, minimum=0, maximum=5))
        h.select(["Img"])
        extra = h.section("extra_attributes")
        assert extra is not None and extra.isVisible()
        _spec, editor = extra._editors["rating"]
        editor.setValue(5)
        editor.editingFinished.emit()
        assert h.attr("Img", "rating") == 5
        h.undo()
        assert h.attr("Img", "rating") == 3
    finally:
        h.close()


# ---- Canvas background --------------------------------------------------------------------------


def _bg_pixels(h):
    image = h.grab(clean=True)
    return {image.pixelColor(x, y).name() for x in range(0, 200, 3) for y in range(0, 200, 3)}


def test_background_color_pattern_saved_and_undoable():
    h = make()
    try:
        bg = h.service("canvas_background")
        panel = h.inspector.empty_panels[0]
        assert panel.isVisible(), "canvas settings show when nothing is selected"
        white = next(b for b in panel._bg_preset_btns if b.toolTip() == "White background")
        QTest.mouseClick(white, Qt.LeftButton)
        assert bg.background_color.name() == "#ffffff"
        colors = _bg_pixels(h)
        assert "#ffffff" in colors and len(colors) >= 2, "white background with a visible grid"
        for pattern in ("dots", "crosses", "checker", "none", "lines"):
            choose(panel.bg_pattern_combo, pattern)
            assert bg.pattern == pattern
            colors = _bg_pixels(h)
            if pattern == "none":
                assert colors == {"#ffffff"}, colors
            else:
                assert len(colors) >= 2, (pattern, colors)
        panel.bg_grid_spin.setValue(32)
        assert bg.grid_size == 32
        h.run("canvas_background.set", pattern_color=[255, 0, 0])
        assert bg.grid_color.name() == "#ff0000" and not panel.bg_pattern_auto_btn.isChecked()
        QTest.mouseClick(panel.bg_pattern_auto_btn, Qt.LeftButton)
        assert bg.custom_pattern_color is None and panel.bg_pattern_auto_btn.isChecked()
        h.key(Qt.Key_Apostrophe, Qt.ControlModifier)  # Ctrl+' toggles the pattern off and back on
        assert bg.pattern == "none"
        h.key(Qt.Key_Apostrophe, Qt.ControlModifier)
        assert bg.pattern == "lines"
        h.undo()
        assert bg.pattern == "none"
        h.redo()
        h.add_image("Img", 0, 0)
        h.select(["Img"])
        assert not panel.isVisible() and h.section("transform").isVisible()
        path = tmp_dir() / "bg.pxref"
        assert h.save_to(path)
        saved = h.saved_json(path)["plugins"]["canvas_background"]
        assert saved["background"] == [255, 255, 255] and saved["grid_size"] == 32 and saved["pattern"] == "lines"
        h2 = make()
        try:
            bg2 = h2.service("canvas_background")
            assert bg2.background_color.name() == "#ffffff", "new scenes start with the last choice"
            h2.run("canvas_background.set", background=[10, 40, 80])
            assert h2.open(path)  # opening a file shows the file's own background
            assert bg2.background_color.name() == "#ffffff" and bg2.grid_size == 32
        finally:
            h2.close()
    finally:
        h.app.settings.remove("plugins/canvas_background/canvas_default")
        h.close()


# ---- Zoom, welcome card, toasts ------------------------------------------------------------------


def test_zoom_reset_and_presets_in_status_bar():
    h = make()
    try:
        block = h.app.bar_block("zoomBlock")
        h.view.zoom_by(3.0, around_cursor=False)
        assert block.zoom_label.text() == "300%"
        QTest.mouseClick(block.zoom_label, Qt.LeftButton)  # main part of the button resets
        assert approx(h.view.zoom_level(), 1.0, 1e-9) and block.zoom_label.text() == "100%"
        texts = [a.text() for a in block.zoom_label.menu().actions()]
        assert "50%" in texts and "400%" in texts
        next(a for a in block.zoom_label.menu().actions() if a.text() == "50%").trigger()
        assert approx(h.view.zoom_level(), 0.5, 1e-9)
        QTest.mouseClick(block.zoom_in_btn, Qt.LeftButton)
        assert approx(h.view.zoom_level(), 0.625, 1e-9)
        QTest.mouseClick(block.zoom_out_btn, Qt.LeftButton)
        assert approx(h.view.zoom_level(), 0.5, 1e-9)
        h.key(Qt.Key_0, Qt.ControlModifier)
        assert approx(h.view.zoom_level(), 1.0, 1e-9)
    finally:
        h.close()


def test_welcome_hint_drop_hint_and_toasts():
    from harness import TMP
    from PySide6.QtCore import QUrl

    h = make()
    try:
        assert h.app.canvas.scene_is_empty()
        empty = h.grab()
        center = empty.pixelColor(h.view.viewport().width() // 2, h.view.viewport().height() // 2)
        background = h.service("canvas_background").background_color
        assert center.name() != background.name(), "welcome card is drawn on an empty board"
        h.add_image("Img", 5000, 5000)
        assert not h.app.canvas.scene_is_empty()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(TMP / "a.png")), QUrl.fromLocalFile(str(TMP / "b.jpg"))])
        assert h.view.describe_drop(mime) == "Drop to add 2 images"
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(TMP / "board.pxref"))])
        assert h.view.describe_drop(mime) == "Drop to open board.pxref"
        mime = QMimeData()
        mime.setText("hello")
        assert h.view.describe_drop(mime) == "Drop to add a text note"
        h.select(["Img"])
        h.trigger("outliner.lock")
        h.undo()
        assert h.view._toast is not None and h.view._toast["text"] == "Undo · Toggle lock", h.view._toast
    finally:
        h.close()


# ---- Menus & command search ----------------------------------------------------------------------


def _result_texts(menu):
    return [a.text().replace("&", "") for a in menu.results()]


def test_context_menu_command_search():
    h = make()
    try:
        h.add_image("Img", 0, 0, w=100, h=50)
        h.select(["Img"])
        menu = h.app.build_context_menu(h.view)
        originals = list(menu.actions())
        assert menu.field.placeholderText().startswith("Search commands")
        menu.field.setText("mirror")  # synonym
        assert {"Flip Horizontal", "Flip Vertical"} <= set(_result_texts(menu)), _result_texts(menu)
        menu.field.setText("zoom 100")  # every word must match
        assert _result_texts(menu)[0] == "Reset Zoom (100%)", _result_texts(menu)
        menu.field.setText("eyedropper")
        assert _result_texts(menu)[0] == "Color Picker", _result_texts(menu)
        menu.field.setText("white")  # nested two levels deep: View › Background
        assert "White" in _result_texts(menu)
        assert "View › Background" in menu.header_texts(), menu.header_texts()
        menu.field.setText("qwertyuiop")
        assert menu.results() == [] and any(a.text() == "No matching commands" for a in menu.actions())
        menu.field.clear()
        assert list(menu.actions()) == originals, "clearing the search restores the menu"
        menu.field.setText("grayscale")  # Enter runs the highlighted result
        assert menu.activeAction() in (h.app.actions.get("image.grayscale"),
                                       h.app.actions.get("effects.add.grayscale")), menu.activeAction().text()
        QTest.keyClick(menu.field, Qt.Key_Return)
        assert [e["type"] for e in h.attr("Img", "effects")] == ["grayscale"]
    finally:
        h.close()


def test_command_search_finds_commands_outside_the_current_menu_and_keyboard_nav():
    h = make()
    try:
        h.select([])
        menu = h.app.build_context_menu(h.view)  # canvas menu: no Flip entries
        menu.field.setText("flip")
        assert "Flip Horizontal" in _result_texts(menu)
        assert "All commands" in menu.header_texts()
        first = menu.activeAction()
        QTest.keyClick(menu.field, Qt.Key_Down)
        assert menu.activeAction() is not first
        QTest.keyClick(menu.field, Qt.Key_Up)
        assert menu.activeAction() is first
        QTest.keyClick(menu.field, Qt.Key_Escape)
        assert menu.field.text() == "", "Esc clears the search first"
    finally:
        h.close()


def test_shortcuts_shown_in_menus_and_buttons():
    from PySide6.QtGui import QGuiApplication

    h = make()
    try:
        assert QGuiApplication.styleHints().showShortcutsInContextMenus()
        menu = h.app.build_context_menu(h.view)
        paste = next(a for a in menu.actions() if a.text() == "Paste")
        assert paste.isShortcutVisibleInContextMenu() and paste.shortcut().toString() == "Ctrl+V"
        palette = h.palette
        assert palette.tool_buttons["pen"].shortcut_text() == "P"
        assert palette.tool_buttons["rect"].shortcut_text() == "R"
        assert palette.buttons["image.insert"].text() == "Image"
        assert palette.buttons["image.insert"].shortcut_text() == "Ctrl+I"
        assert palette.buttons["grouping.add_group"].shortcut_text() == "Ctrl+⇧N"
    finally:
        h.close()


# ---- Window manager ------------------------------------------------------------------------------


def test_panels_live_outside_the_tiling_layout():
    """Tools, Outliner and Attribute Editor are panels next to (not inside) the tiles,
    and they stay on screen whichever workspace is shown."""
    h = make()
    try:
        wm, panels = h.app.wm, h.app.panels
        assert wm.doc.tiles(wm.doc.tiling_root(wm.focused_workspace())) == ["Main"], "Board is just the canvas"
        assert not any(wm.doc.attr(t, "view_type") in panels.types for t in wm.doc.tiles())
        assert [p["id"] for p in h.run("panels.list")] == ["tool_palette", "outliner", "inspector"]
        sidebar = panels.sidebar
        assert sidebar.isVisible() and sidebar.parentWidget() is h.window.body
        assert h.window.body.indexOf(sidebar) > h.window.body.indexOf(h.window.area), "right of the tiles"
        assert h.inspector.isVisible() and h.outliner.isVisible() and h.palette.isVisible()
        rail = panels.rail
        assert rail.isVisible() and list(rail.tabs) == ["tool_palette", "outliner", "inspector"]
        row = h.window.middle.layout()
        assert row.indexOf(rail) == row.count() - 1, "the tabs sit on the outer edge, beyond the sidebar"
        h.app.run_command("wm.workspace", "Review")
        assert sidebar.isVisible() and h.inspector.isVisible(), "the same panels in every workspace"
        h.app.run_command("wm.workspace", "Present")
        assert not sidebar.isVisible() and not rail.isVisible(), "Present shows no panels"
        h.app.run_command("wm.workspace", "Board")
        assert sidebar.isVisible()
    finally:
        h.close()


def test_sidebar_resizable_side_and_remembered():
    h = make()
    try:
        panels = h.app.panels
        assert panels.sidebar_width() == 300
        handle = h.window.body.handle(1)
        start = handle.rect().center()
        QTest.mousePress(handle, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(handle, start - QPoint(120, 0))
        QTest.mouseRelease(handle, Qt.LeftButton, Qt.NoModifier, start - QPoint(120, 0))
        assert approx(panels.sidebar_width(), 420, 3), panels.sidebar_width()
        assert h.run("panels.set_width", 10_000) == 640 and h.run("panels.set_width", 10) == 220
        h.run("panels.set_width", 380)
        h.run("panels.set_side", "left")
        body, row = h.window.body, h.window.middle.layout()
        assert body.indexOf(panels.sidebar) < body.indexOf(h.window.area)
        assert row.indexOf(panels.rail) == 0, "the tabs follow the sidebar to the left edge"
        h.run("panels.set_side", "right")
        assert body.indexOf(panels.sidebar) > body.indexOf(h.window.area)
        assert row.indexOf(panels.rail) == row.count() - 1
        h.run("panels.set_mode", "outliner", "floating")
        panels.set_collapsed("tool_palette", True)
        h2 = make(fresh_panels=False)
        try:
            p2 = h2.app.panels
            assert p2.sidebar_width() == 380 and p2.side == "right"
            assert p2.mode("outliner") == "floating" and p2.sections["tool_palette"].collapsed
            assert h2.run("panels.toggle_all") is False
            assert not p2.sidebar.isVisible() and not p2.cards["outliner"].isVisible()
            assert h2.run("panels.toggle_all") is True and p2.sidebar.isVisible()
        finally:
            h2.close()
    finally:
        h.close()


def test_panel_modes_docked_floating_hidden():
    h = make()
    try:
        panels = h.app.panels
        h.add_image("Img", 0, 0)
        h.select(["Img"])
        h.run("panels.set_mode", "inspector", "floating")
        card = panels.cards["inspector"]
        assert card.isVisible() and card.parentWidget() is h.window.area, "a card over the canvas"
        assert h.inspector.isVisible() and h.inspector.name_edit.text() == "Img"
        assert not panels.sections["inspector"].isVisible()
        card.set_collapsed(True)
        assert card.height() < 40 and not h.inspector.isVisible()
        card.set_collapsed(False)
        assert h.inspector.isVisible()
        h.trigger("panels.mode.inspector.docked")
        assert panels.sections["inspector"].isVisible() and not card.isVisible()
        # Hidden: off screen; showing it again puts it back where it was.
        assert h.run("panels.toggle", "outliner") == "hidden"
        assert not panels.is_shown("outliner")
        assert h.run("panels.toggle", "outliner") == "docked" and panels.is_shown("outliner")
        assert h.app.actions.get("panels.toggle.outliner").isChecked()
        # The tabs on the window edge do the same, and light up while their panel is open.
        tab = panels.rail.tabs["outliner"]
        assert tab.isChecked()
        QTest.mouseClick(tab, Qt.LeftButton)
        assert panels.mode("outliner") == "hidden" and not tab.isChecked()
        QTest.mouseClick(tab, Qt.LeftButton)
        assert panels.mode("outliner") == "docked" and tab.isChecked()
        h.run("panels.toggle_all")
        assert panels.rail.isVisible() and not tab.isChecked(), "Tab hides the panels, not their tabs"
        h.run("panels.toggle_all")
        assert tab.isChecked()
    finally:
        h.close()


def test_panels_pop_up_next_to_the_selection_or_cursor():
    h = make()
    try:
        panels = h.app.panels
        h.add_image("Img", 0, 0)
        h.select(["Img"])
        h.run("panels.set_mode", "inspector", "hidden")
        h.key(Qt.Key_A, Qt.AltModifier)
        popup = panels.open_popup
        assert popup is not None and popup.ptype.id == "inspector" and popup.isVisible()
        assert h.inspector is popup.widget, "the visible instance is the pop-up's"
        assert h.inspector.name_edit.text() == "Img"
        # Edits in the pop-up are ordinary undoable commands.
        look = h.inspector.section("appearance")
        look.opacity_spin.setValue(50.0)
        look.opacity_spin.editingFinished.emit()
        assert h.attr("Img", "opacity") == 0.5
        # Clicking the canvas closes it; Esc does too.
        h.click(h.vp(-400, 300))
        assert not popup.isVisible()
        h.trigger("panels.popup.inspector")
        QTest.keyClick(popup, Qt.Key_Escape)
        assert not popup.isVisible()
        # The pin turns it into a floating card.
        h.trigger("panels.popup.inspector")
        panels.pin_popup("inspector")
        assert panels.mode("inspector") == "floating" and panels.cards["inspector"].isVisible()
        # The context menu offers it for the selection.
        h.select(["Img"])
        menu = h.app.build_context_menu(h.view)
        assert any(a.text() == "Edit Attributes…" for a in menu.actions())
        # Tools pop up at the cursor and close once a tool is picked.
        h.key(Qt.Key_T, Qt.AltModifier)
        tools = panels.open_popup
        assert tools.ptype.id == "tool_palette" and tools.isVisible()
        QTest.mouseClick(tools.widget.tool_buttons["rect"], Qt.LeftButton)
        assert h.tool() == "rect" and not tools.isVisible()
        # F2 renames in an Outliner pop-up when the outliner isn't on screen.
        h.set_tool("select")
        h.run("panels.set_mode", "outliner", "hidden")
        h.select(["Img"])
        h.trigger("outliner.rename")
        assert panels.open_popup is not None and panels.open_popup.ptype.id == "outliner"
    finally:
        h.close()


def test_wm_split_tabs_close_and_workspaces():
    h = make()
    try:
        wm = h.app.wm
        h.app.run_command("wm.focus", tile="Main")
        h.app.run_command("wm.split", "v")
        tile = h.app.run_command("wm.open_view", "canvas", title="Second")
        assert tile in h.app.views and wm.doc.attr(wm.doc.parent(tile), "orientation") == "v"
        assert set(h.app.run_command("ui.views")) >= {"Main", tile}
        h.app.run_command("wm.layout", "tabbed")
        assert wm.doc.type_of(wm.doc.parent(tile)) == "wm_tabbed"
        h.app.run_command("wm.close_view", tile)
        assert tile not in h.app.views and not wm.doc.has(tile)
        for other in ("ReviewColor", "ReviewValue", "Present"):
            h.app.run_command("wm.close_view", other)
        assert wm.tiles_of_type("canvas") == ["Main"]
        h.app.run_command("wm.close_view", "Main")
        assert wm.doc.has("Main"), "the last canvas can't be closed"
        wm.build_default()
        wm.apply()
        # The built-in workspaces.
        titles = [wm.doc.attr(w, "title") for w in wm.doc.workspaces()]
        assert titles == ["Board", "Review", "Present"], titles
        h.app.run_command("wm.workspace", 2)
        assert {"ReviewColor", "ReviewValue"} <= set(wm.visible_tiles())
        assert h.app.views["ReviewValue"].display.grayscale
        h.app.run_command("wm.workspace", "Board")
        assert "Main" in wm.visible_tiles()
        # Leader key: Ctrl+W, then L focuses the view to the right.
        h.app.run_command("wm.focus", tile="Main")
        h.app.run_command("wm.split", "h")
        right = h.app.run_command("wm.open_view", "canvas", title="Right")
        h.app.run_command("wm.focus", tile="Main")
        QTest.keyClick(h.window, Qt.Key_W, Qt.ControlModifier)
        assert h.app.keymap.mode == "window"
        QTest.keyClick(h.window, Qt.Key_L)
        assert wm.focused == right, wm.focused
        assert h.app.keymap.mode == "window", "like i3, the mode stays for more commands"
        QTest.keyClick(h.window, Qt.Key_Escape)
        assert h.app.keymap.mode is None
    finally:
        h.close()


def test_scratchpad_console_and_contact_sheet_views():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        shown = h.app.run_command("console.toggle")
        assert shown is None  # toggling is a command without a result
        console = h.service("console")
        assert console.views, "the Script Editor opens from the scratchpad"
        view = console.views[0]
        editor = view.current_editor()
        editor.setPlainText("pxcmd.set_attr('Img.rotate', 45)\nprint(pxcmd.get_attr('Img.rotate'))")
        view.run_editor(editor)
        assert h.attr("Img", "rotate") == 45.0
        assert "45.0" in view.output.toPlainText()
        h.undo()
        assert h.attr("Img", "rotate") == 0.0, "a console run is one undo step"
        # Echo: UI actions print their pxcmd call.
        h.select(["Img"])
        h.trigger("transform.flip_h")
        assert "pxcmd.flip('h')" in view.output.toPlainText(), view.output.toPlainText()[-400:]
        # The contact sheet opens as a view (the minimap is a canvas overlay instead).
        assert "minimap" not in h.app.wm.view_types
        tile2 = h.app.run_command("wm.open_view", "contact_sheet")
        APP.processEvents()
        sheet = h.service("contact_sheets")[0]
        sheet.refresh()
        assert list(sheet.items) == ["Img"]
        h.app.run_command("wm.close_view", tile2)
    finally:
        h.close()


def test_wm_split_buttons_rotate_swap_and_join():
    h = make()
    try:
        wm = h.app.wm
        h.app.run_command("wm.workspace", "Board")
        h.app.view.set_camera([120.0, -40.0], 2.0, 0.0, animate=False)
        frame = wm.frames["Main"]
        assert frame.header.split_right_button.isVisibleTo(frame) and frame.grips[0].isVisibleTo(frame)
        frame.header.split_right_button.click()
        right = wm.focused
        split = wm.doc.parent(right)
        assert right != "Main" and wm.doc.attr(right, "title") == "Main 2"
        assert wm.doc.type_of(split) == "wm_split" and wm.doc.attr(split, "orientation") == "h"
        assert wm.doc.children(split) == ["Main", right]
        camera = h.app.views[right].camera()
        assert approx(camera["center"][0], 120.0) and approx(camera["zoom"], 2.0), "the copy keeps the camera"
        # Split the copy downwards with a 30% share: a nested vertical split.
        below = h.app.run_command("wm.split_view", "down", right, ratio=0.3)
        inner = wm.doc.parent(below)
        assert wm.doc.attr(inner, "orientation") == "v" and wm.doc.parent(inner) == split
        assert approx(wm.doc.attr(inner, "sizes")[1], 0.3, 0.01)
        # Rotate, swap and even out from the divider.
        assert h.app.run_command("wm.rotate_split", below) == "h"
        assert wm.doc.attr(inner, "orientation") == "h"
        h.app.run_command("wm.swap_split", inner)
        assert wm.doc.children(inner) == [below, right]
        assert approx(wm.doc.attr(inner, "sizes")[0], 0.3, 0.01)
        h.app.run_command("wm.equalize_split", inner)
        assert wm.doc.attr(inner, "sizes") == [0.5, 0.5]
        # Join: the closed view's share goes to the one that takes over.
        h.app.run_command("wm.join_view", below, into=right)
        assert not wm.doc.has(below) and wm.doc.parent(right) == split
        h.app.run_command("wm.join_view", right, into="Main")
        assert not wm.doc.has(right) and wm.doc.tiling_root(wm.focused_workspace()) == "Main"
        # A lone view has nothing to rotate; the last canvas can't be joined away.
        assert h.app.run_command("wm.rotate_split", "Main") is None
        assert wm.doc.attr("Main", "view_type") == "canvas"
        # Fullscreen / floating views can't be split, so their grips and buttons hide.
        h.app.run_command("wm.fullscreen", "Main", on=True)
        assert not frame.header.split_right_button.isVisibleTo(frame)
        assert not any(g.isVisibleTo(frame) for g in frame.grips)
        h.app.run_command("wm.fullscreen", "Main", on=False)
        assert frame.header.split_right_button.isVisibleTo(frame)
    finally:
        h.close()


def test_wm_corner_drag_geometry():
    from PySide6.QtCore import QRect

    from pixelref.ui.wm.interact import adjacent, split_preview

    rect = QRect(0, 0, 400, 200)
    start = QPoint(399, 199)
    assert split_preview(rect, "br", start, QPoint(395, 197)) is None, "below the drag threshold"
    side = split_preview(rect, "br", start, QPoint(300, 190))
    assert side.direction == "right" and approx(side.ratio, 0.25, 0.01)
    down = split_preview(rect, "br", start, QPoint(390, 150))
    assert down.direction == "down" and approx(down.ratio, 0.25, 0.01)
    up = split_preview(rect, "tl", QPoint(0, 0), QPoint(5, 190))
    assert up.direction == "up" and approx(up.ratio, 0.9, 0.01), "clamped"
    assert adjacent(QRect(0, 0, 100, 100), QRect(105, 0, 100, 100))
    assert adjacent(QRect(0, 0, 100, 100), QRect(0, 104, 100, 50))
    assert not adjacent(QRect(0, 0, 100, 100), QRect(300, 0, 100, 100))
    assert not adjacent(QRect(0, 0, 100, 100), QRect(105, 300, 100, 100)), "no shared edge"


def test_wm_corner_drag_splits_and_joins():
    from pixelref.ui.wm.interact import CornerDrag

    h = make()
    try:
        wm = h.app.wm
        h.app.run_command("wm.workspace", "Board")
        window = wm.frames["Main"].window()
        window.resize(1000, 600)
        QApplication.processEvents()
        frame = wm.frames["Main"]
        origin = frame.mapToGlobal(QPoint(0, 0))
        corner = origin + QPoint(frame.width() - 2, frame.height() - 2)
        drag = CornerDrag(wm, "Main", "br", corner)
        drag.update(origin + QPoint(frame.width() // 2, frame.height() - 8))
        assert drag.preview is not None and drag.preview.direction == "right"
        drag.finish(apply=True)
        new = wm.focused
        assert new != "Main" and wm.doc.attr(wm.doc.parent(new), "orientation") == "h"
        QApplication.processEvents()
        # Esc / cancel leaves the layout alone.
        drag = CornerDrag(wm, "Main", "br", wm.frames["Main"].mapToGlobal(QPoint(5, 5)))
        drag.update(wm.frames["Main"].mapToGlobal(QPoint(5, 120)))
        drag.finish(apply=False)
        assert len(wm.doc.tiles(wm.doc.tiling_root(wm.focused_workspace()))) == 2
        # Drag from Main's corner over the neighbour: join.
        main = wm.frames["Main"]
        start = main.mapToGlobal(QPoint(main.width() - 2, main.height() - 2))
        target = wm.frames[new]
        drag = CornerDrag(wm, "Main", "br", start)
        drag.update(target.mapToGlobal(QPoint(target.width() // 2, target.height() // 2)))
        assert drag.preview is not None and drag.preview.kind == "join" and drag.preview.target == new
        drag.finish(apply=True)
        assert not wm.doc.has(new) and wm.doc.tiling_root(wm.focused_workspace()) == "Main"
    finally:
        h.close()


def test_minimap_overlay_corners_toggle_and_persistence():
    from PySide6.QtCore import QPoint

    h = make()
    try:
        wm = h.app.wm
        h.app.run_command("wm.workspace", "Board")
        h.add_image("Img", 0, 0)
        window = wm.frames["Main"].window()
        window.resize(1100, 700)
        APP.processEvents()
        view = h.app.views["Main"]
        mini = h.service("minimaps")["Main"]
        assert mini.parent() is view and mini.isVisible(), "shown by default, inside the canvas"
        area = view.viewport().geometry()
        assert mini.geometry().right() > area.center().x() and mini.geometry().bottom() > area.center().y(),             "bottom-right by default"
        # Drag the title bar towards the top-left: it snaps to that corner.
        press = QPoint(30, 8)
        QTest.mousePress(mini, Qt.LeftButton, Qt.NoModifier, press)
        target = mini.mapFromParent(QPoint(area.left() + 40, area.top() + 40))
        QTest.mouseMove(mini, target)
        mini.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(target), QPointF(mini.mapToGlobal(target)),
                                        Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        assert mini.hints.isVisible() and mini.hints.nearest == "tl"
        QTest.mouseRelease(mini, Qt.LeftButton, Qt.NoModifier, mini.mapFromParent(QPoint(area.left() + 40, area.top() + 40)))
        assert view.plugin_state["minimap"]["corner"] == "tl" and not mini.hints.isVisible()
        # Commands: corner, size, hide / show.
        h.app.run_command("minimap.place", "tr", "large", "Main")
        assert mini.state()["corner"] == "tr" and mini.width() >= 300
        assert h.app.run_command("minimap.toggle", "Main") is False and not mini.isVisible()
        # The × in the title bar hides it; the header's map button brings it back.
        h.app.run_command("minimap.toggle", "Main", True)
        assert mini.isVisible()
        QTest.mouseClick(mini, Qt.LeftButton, Qt.NoModifier, QPoint(mini.width() - 8, 8))
        assert not mini.isVisible()
        button = mini.buttons[0]
        assert not button.isChecked()
        button.click()
        assert mini.isVisible() and button.isChecked()
        # Clicking the map moves this view there.
        before = view.camera()["center"]
        QTest.mouseClick(mini, Qt.LeftButton, Qt.NoModifier, QPoint(mini.width() // 4, mini.height() - 10))
        assert view.camera()["center"] != before
        # Saved with the tile and copied by a split.
        assert wm.collect_states() is None and wm.doc.attr("Main", "state")["plugins"]["minimap"]["corner"] == "tr"
        copy = h.app.run_command("wm.split_view", "right", "Main")
        APP.processEvents()
        assert h.app.views[copy].plugin_state["minimap"]["corner"] == "tr"
        assert h.service("minimaps")[copy].view is h.app.views[copy]
        h.app.run_command("wm.close_view", copy)
        assert copy not in h.service("minimaps")
        # Clean views (Present) start without one.
        h.app.run_command("wm.workspace", "Present")
        APP.processEvents()
        assert not h.service("minimaps")["Present"].state()["visible"]
    finally:
        h.close()


def test_wm_floating_fullscreen_and_move_edge_cases():
    h = make()
    try:
        wm = h.app.wm
        h.app.run_command("wm.workspace", "Board")
        tile = h.app.run_command("wm.open_view", "canvas", title="Floater")
        h.app.run_command("wm.floating", tile, on=True)
        window = wm.floating_windows[tile]
        wm.apply()
        assert wm.frames[tile].parentWidget() is window, "a floating view stays in its window after apply()"
        assert not wm.frames[tile].isWindow()
        # Moving a view never swaps it with a floating one.
        h.app.run_command("wm.focus", tile="Main")
        for direction in ("left", "right", "up", "down"):
            h.app.run_command("wm.move", direction)
        assert wm.doc.attr(tile, "floating") and wm.doc.parent(tile) == wm.doc.workspace_of("Main")
        # A fullscreen view moved to another workspace doesn't stay that workspace's fullscreen view.
        h.app.run_command("wm.floating", tile, on=False)
        h.app.run_command("wm.fullscreen", tile, on=True)
        old = wm.doc.workspace_of(tile)
        h.app.run_command("wm.move_to_workspace", 7, tile)
        assert wm.doc.attr(old, "fullscreen") == ""
    finally:
        h.close()


def test_popup_close_hooks_are_released_with_the_panel():
    from PySide6.QtWidgets import QLabel

    from pixelref.ui.panels import PanelType

    h = make()
    try:
        panels = h.app.panels
        disposable = panels.register(PanelType("probe_panel", "Probe", lambda parent: QLabel("x", parent),
                                               popup_closes_on=("probe.event",)))
        panels.popup("probe_panel")
        disposable.dispose()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        h.app.events.emit("probe.event", {})  # used to call into the deleted pop-up
    finally:
        h.close()


def test_inspector_rotation_edit_ignores_rounded_position_display():
    h = make()
    try:
        h.add_image("Img", 10.37, 20.42, w=200, h=100)
        h.select(["Img"])
        xf = h.section("transform")
        commit_spin(xf.rotation_spin, 90.0)  # position spins show 10.4 / 20.4: not an edit
        assert h.attr("Img", "rotate") == 90.0
        center = h.item("Img").sceneTransform().map(h.item("Img").content_rect().center())
        assert approx(center.x(), 110.37, 1e-3) and approx(center.y(), 70.42, 1e-3), "rotates about the centre"
        commit_spin(xf.pos_x_spin, 50.0)  # editing X keeps the exact Y and rotation
        assert h.attr("Img", "rotate") == 90.0 and h.attr("Img", "translate")[0] == 50.0
    finally:
        h.close()


def test_outliner_selection_sync_and_no_scroll_jump_on_unrelated_refresh():
    h = make()
    try:
        names = [h.add_image(f"Img{i}", i * 10.0, 0, w=20, h=20) for i in range(60)]
        h.select(names[:3])
        tree = h.outliner.tree
        assert sorted(tree.node_name(i) for i in tree.selectedItems()) == sorted(names[:3])
        tree.verticalScrollBar().setValue(tree.verticalScrollBar().maximum())
        scrolled = tree.verticalScrollBar().value()
        h.run("outliner.set_flag", names[-1], "locked", 1)  # an eye/lock click elsewhere
        h.refresh()
        assert tree.verticalScrollBar().value() == scrolled, "the list stays where it was"
        assert sorted(tree.node_name(i) for i in tree.selectedItems()) == sorted(names[:3])
    finally:
        h.close()


def test_linked_views_follow_flips():
    h = make()
    try:
        wm = h.app.wm
        h.app.run_command("wm.workspace", "Board")
        other = h.app.run_command("wm.split_view", "right", "Main")
        main, copy = h.app.views["Main"], h.app.views[other]
        h.app.run_command("views.link", "Main", other)
        main.set_camera([10.0, 20.0], 1.5, 30.0, flip_x=True)
        camera = copy.camera()
        assert camera["flip_x"] and not camera["flip_y"], camera
        assert approx(camera["rotation"], 30.0, 0.01) and approx(camera["zoom"], 1.5), camera
        # A copy of a flipped view keeps the flip.
        third = h.app.run_command("wm.split_view", "down", "Main")
        assert h.app.views[third].camera()["flip_x"]
        assert wm.doc.has(third)
    finally:
        h.close()


def test_closing_the_app_pops_up_no_stray_windows():
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QWidget

    h = make()
    shown = []

    class Spy(QObject):
        def eventFilter(self, obj, event):  # noqa: N802
            if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
                shown.append(type(obj).__name__)
            return False

    spy = Spy()
    APP.installEventFilter(spy)
    try:
        h.app.close(force=True)
        for _ in range(5):
            APP.processEvents()
        assert not shown, f"windows flashed while closing: {shown}"
    finally:
        APP.removeEventFilter(spy)


def test_frameless_window_resizes_from_its_edges():
    h = make()
    try:
        app = h.app
        window = app.wm.windows[app.wm.doc.primary_output()]
        resizer = window.resizer
        w, hgt = window.width(), window.height()
        assert not resizer.edges_at(1, 1), "framed windows use the system frame"
        app.run_command("window_modes.toggle_frameless")
        assert window.windowFlags() & Qt.FramelessWindowHint
        assert resizer.edges_at(1, 1) == Qt.LeftEdge | Qt.TopEdge
        assert resizer.edges_at(w - 2, hgt / 2) == Qt.RightEdge
        assert resizer.edges_at(w / 2, hgt - 2) == Qt.BottomEdge
        assert not resizer.edges_at(w / 2, hgt / 2)
        assert resizer.cursor_for(Qt.RightEdge | Qt.BottomEdge) == Qt.SizeFDiagCursor
        assert resizer.cursor_for(Qt.RightEdge | Qt.TopEdge) == Qt.SizeBDiagCursor
        handle = window.windowHandle()
        move = QMouseEvent(QEvent.MouseMove, QPointF(w - 2, hgt / 2), QPointF(w - 2, hgt / 2),
                           Qt.NoButton, Qt.NoButton, Qt.NoModifier)
        QApplication.sendEvent(handle, move)
        assert QApplication.overrideCursor().shape() == Qt.SizeHorCursor
        leave = QEvent(QEvent.Leave)
        QApplication.sendEvent(handle, leave)
        assert QApplication.overrideCursor() is None
        # Blank parts of the bar and of view headers move the window; their buttons don't.
        bar = window.bar
        at = bar.status.mapTo(window, bar.status.rect().center())
        assert resizer.move_handle_at(at.x(), at.y()) is bar
        header = h.app.wm.frames["Main"].header
        at = header.title_label.mapTo(window, header.title_label.rect().center())
        assert resizer.move_handle_at(at.x(), at.y()) is header
        at = header.close_button.mapTo(window, header.close_button.rect().center())
        assert resizer.move_handle_at(at.x(), at.y()) is None
        app.run_command("window_modes.toggle_frameless")
        assert not resizer.edges_at(1, 1)
        at = bar.status.mapTo(window, bar.status.rect().center())
        assert resizer.move_handle_at(at.x(), at.y()) is None, "framed windows move by their title bar"
    finally:
        h.close()


def test_canvas_context_menu_is_organised_by_what_was_clicked():
    def layout(menu):
        """Top-level entries as text, '|' for separators (blank widget actions skipped)."""
        out = []
        for action in menu.actions():
            if action.isSeparator():
                if out and out[-1] != "|":
                    out.append("|")
            elif action.isVisible() and action.text():
                out.append(action.text().replace("&", ""))
        return out

    h = make()
    try:
        img = h.add_image("Img", 0, 0, w=40, h=40)
        img2 = h.add_image("Img2", 60, 0, w=40, h=40)
        empty = layout(h.app.menus.build("canvas"))
        assert empty.index("Paste") < empty.index("Select All") < empty.index("Undo Create image")
        assert empty.index("Undo Create image") < empty.index("Tools") < empty.index("File")
        assert "Copy" not in empty and "Transform" not in empty
        assert empty[-2].startswith("Keyboard Shortcuts") and empty[-1].startswith("About pixelRef")
        # The canvas-wide sub-menus sit together, then File, View and Window, without separators.
        start = empty.index("Tools")
        assert empty[start:start + 3] == ["Tools", "Arrange", "Effects"]
        start = empty.index("File")
        assert empty[start:start + 3] == ["File", "View", "Window"]

        h.select([img])
        one = layout(h.app.menus.build("canvas"))
        assert one.index("Copy") < one.index("Edit Attributes…") < one.index("Transform") < one.index("Image")
        assert one.index("Image") < one.index("Order") < one.index("Frame Selection") < one.index("Undo Create image")
        assert "Arrange" not in one, "arranging needs more than one item"
        assert "Tools" not in one and "Select All" not in one
        assert "|" not in one[:1] and all(a != b or a != "|" for a, b in zip(one, one[1:]))

        h.select([img, img2])
        two = h.app.menus.build("canvas")
        assert "Arrange" in layout(two)
        arrange = next(a.menu() for a in two.actions() if a.text() == "Arrange")
        assert "Align Left" in layout(arrange)
    finally:
        h.close()


def test_zoom_block_shows_and_drives_its_own_windows_view():
    h = make()
    try:
        main = h.view
        main.set_zoom(2.0)
        other_tile = h.app.run_command("wm.split_view", "right", main.view_name)
        h.app.run_command("wm.tear_off", other_tile)
        APP.processEvents()
        other = h.app.views[other_tile]
        other.set_zoom(0.5)
        h.app.wm.focus_tile(other_tile)
        APP.processEvents()
        assert h.app.active_view() is other
        primary = h.app.bar_block("zoomBlock")
        assert primary.zoom_label.text() == "200%", "the main window shows its own view's zoom"
        QTest.mouseClick(primary.zoom_in_btn, Qt.LeftButton)
        assert approx(main.zoom_level(), 2.5, 1e-9) and approx(other.zoom_level(), 0.5, 1e-9)
    finally:
        h.close()


def test_toggle_view_markers_announces_the_display_change():
    h = make()
    try:
        seen = []
        subscription = h.app.events.on("view.display_changed", seen.append)
        before = h.view.display.show_markers
        assert h.run("views.toggle_markers") is (not before)
        assert h.view.display.show_markers is (not before)
        assert seen, "the header menus and saved layout hear about it"
        subscription.dispose()
    finally:
        h.close()


def test_console_clips_huge_lines_and_survives_exit():
    h = make()
    try:
        h.app.run_command("console.toggle")
        console = h.service("console")
        view = console.views[0]
        console.run("print('x' * 300_000)")
        assert "more characters]" in view.output.toPlainText()[-200:]
        console.run("import sys; sys.exit(3)")
        assert "SystemExit" in view.output.toPlainText()[-300:]
    finally:
        h.close()


def test_window_mode_leaves_dialog_keys_alone_and_matches_shift_digits():
    import sys

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QLineEdit

    h = make()
    try:
        app, wm = h.app, h.app.wm
        app.run_command("wm.workspace", "Board")
        other = app.run_command("wm.split_view", "right", "Main")
        app.run_command("wm.focus", tile=other)
        typed = {}

        def type_in_prompt():
            dialog = QApplication.activeModalWidget()
            edit = dialog.findChild(QLineEdit) if dialog is not None else None
            if edit is not None:
                QTest.keyClicks(edit, "q")  # Q closes a view in window mode
                typed["text"] = edit.text()
                dialog.accept()
            elif dialog is not None:
                dialog.reject()

        QTimer.singleShot(50, type_in_prompt)
        target = app.views[other].viewport()
        QTest.keyClick(target, Qt.Key_W, Qt.ControlModifier)
        assert app.keymap.mode == "window"
        QTest.keyClick(target, Qt.Key_M)  # mark prompt
        assert typed.get("text") == "q", typed
        assert wm.doc.has(other) and wm.doc.attr(other, "mark") == "q"
        if sys.platform == "win32":
            # Shift+3 arrives as Shift+# with the "3" virtual key: it still moves the view to workspace 3.
            app.keymap.enter_mode("window")
            event = QKeyEvent(QEvent.KeyPress, Qt.Key_NumberSign, Qt.ShiftModifier, 0, 0x33, 0, "#")
            assert app.keymap.handle_mode_key(event)
            assert wm.doc.attr(wm.doc.workspace_of(other), "number") == 3
        app.keymap.exit_mode()
    finally:
        h.close()


def test_wm_layout_and_fullscreen_of_floating_and_scratchpad_views():
    h = make()
    try:
        app, wm = h.app, h.app.wm
        doc = wm.doc
        app.run_command("wm.workspace", "Board")
        tile = app.run_command("wm.open_view", "canvas", title="Floater")
        app.run_command("wm.floating", tile, on=True)
        workspace = doc.workspace_of(tile)
        # Fullscreen: the floating view fills its output window, then floats again.
        app.run_command("wm.fullscreen", tile, on=True)
        window = wm.windows[doc.output_showing(workspace)]
        assert window.content is wm.frames[tile] and wm.frames[tile].window() is window
        assert not wm.floating_windows[tile].isVisible()
        app.run_command("wm.fullscreen", tile, on=False)
        assert wm.frames[tile].window() is wm.floating_windows[tile]
        # A layout change on a floating or scratchpad view doesn't lose it.
        app.run_command("wm.layout", "tabbed")
        assert tile in doc.floating(workspace) and tile in wm.visible_tiles()
        app.run_command("wm.scratchpad_show")
        scratch = doc.children(doc.scratchpad())[0]
        app.run_command("wm.focus", tile=scratch)
        app.run_command("wm.layout", "stacked")
        assert doc.type_of(doc.children(doc.scratchpad())[0]) == "wm_tile"
        assert app.session.run("wm.scratchpad_show") is False
    finally:
        h.close()


def test_menu_conditions_evaluated_once_and_frameless_cursor_released_on_hide():
    h = make()
    try:
        app = h.app
        calls = []
        app.menus.add("canvas", "undo", path="Window/Deep", when=lambda: calls.append(1) or True)
        menu = app.menus.build("canvas")
        assert len(calls) == 1, calls
        window = next(a.menu() for a in menu.actions() if a.text() == "Window")
        assert any(a.text() == "Deep" for a in window.actions())
        # The frameless resize cursor doesn't outlive the window being hidden.
        out = app.wm.doc.primary_output()
        app.run_command("wm.output_style", frameless=True, output=out)
        main = app.wm.windows[out]
        w, hgt = main.width(), main.height()
        QApplication.sendEvent(main.windowHandle(), QMouseEvent(
            QEvent.MouseMove, QPointF(w - 2, hgt / 2), QPointF(w - 2, hgt / 2), Qt.NoButton, Qt.NoButton,
            Qt.NoModifier))
        assert QApplication.overrideCursor() is not None
        main.hide()
        assert QApplication.overrideCursor() is None
        main.show()
    finally:
        while QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        h.close()


def test_shutdown_does_not_rebuild_shortcuts_per_binding():
    h = make()
    keymap = h.app.keymap
    rebuilds = []
    original = keymap.rebuild_shortcuts
    keymap.rebuild_shortcuts = lambda: (rebuilds.append(1), original())
    h.app.close(force=True)
    assert not rebuilds, f"{len(rebuilds)} shortcut rebuilds while closing"


# ---- Note and metadata ----------------------------------------------------------------------------


def test_note_and_metadata_editing_round_trips_and_undoes():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        h.select(["Img"])
        note, meta = h.section("note"), h.section("metadata")
        assert note.isVisible() and meta.isVisible()
        note.edit.setText("check the sky")
        note.edit.editingFinished.emit()
        assert h.attr("Img", "note") == "check the sky"
        meta._add_blank()
        row = meta._rows[0]
        row.key_edit.setText("shot")
        row.value_edit.setText("12")
        row.value_edit.editingFinished.emit()
        assert h.attr("Img", "metadata") == {"shot": 12}
        row.value_edit.setText('"12"')
        row.value_edit.editingFinished.emit()
        assert h.attr("Img", "metadata") == {"shot": "12"}
        meta._remove(meta._rows[0])
        assert h.attr("Img", "metadata") == {}
        h.undo()
        assert h.attr("Img", "metadata") == {"shot": "12"}
        h.select(["Img"])
        assert meta._rows[0].value_edit.text() == '"12"'
    finally:
        h.close()


def test_metadata_rejects_bad_keys():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        try:
            h.run("set_attr", "Img", "metadata", {"": 1})
        except Exception:  # noqa: BLE001
            return
        raise AssertionError("empty key accepted")
    finally:
        h.close()
