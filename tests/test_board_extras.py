"""Click-through window, screen colour picking, image info, ellipses, note links and checklists."""

from __future__ import annotations

from harness import APP, QTest, make
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent
from PySide6.QtWidgets import QApplication

# ---- Click-through -------------------------------------------------------------------------------


def test_click_through_keeps_window_on_top_and_turns_off_when_activated_again():
    h = make()
    try:
        output = h.app.wm.focused_output()
        window = h.app.wm.windows[output]
        assert not h.app.wm.doc.attr(output, "on_top")
        assert h.app.run_command("window_modes.toggle_click_through") is True
        assert window.windowFlags() & Qt.WindowTransparentForInput
        assert h.app.wm.doc.attr(output, "on_top"), "click-through pins the window on top"
        # Still active right after turning it on: activation alone doesn't turn it off.
        QApplication.sendEvent(window, QEvent(QEvent.WindowActivate))
        APP.processEvents()
        assert window.windowFlags() & Qt.WindowTransparentForInput
        # Left (a click went to the app below), then activated again (Alt+Tab): off.
        QApplication.sendEvent(window, QEvent(QEvent.WindowDeactivate))
        QApplication.sendEvent(window, QEvent(QEvent.WindowActivate))
        QTest.qWait(20)
        assert not window.windowFlags() & Qt.WindowTransparentForInput
        assert not h.app.wm.doc.attr(output, "on_top"), "the on-top it added is removed again"
        # Without auto-off it stays until toggled; an on-top chosen before is kept.
        h.app.run_command("window_modes.toggle_click_through_auto_off")
        h.app.run_command("window_modes.toggle_on_top")
        h.app.run_command("window_modes.set_click_through", True)
        QApplication.sendEvent(window, QEvent(QEvent.WindowDeactivate))
        QApplication.sendEvent(window, QEvent(QEvent.WindowActivate))
        QTest.qWait(20)
        assert window.windowFlags() & Qt.WindowTransparentForInput
        h.app.run_command("window_modes.toggle_click_through")
        assert not window.windowFlags() & Qt.WindowTransparentForInput
        assert h.app.wm.doc.attr(output, "on_top")
        assert "click_through" not in str(h.app.wm.doc.attr(output, "window_style") or ""), "never saved"
    finally:
        h.app.run_command("window_modes.toggle_click_through_auto_off")
        h.close()


# ---- Colour picker ---------------------------------------------------------------------------------


def test_color_readouts_and_copy_formats():
    from pixelref.builtins.color_picker.ui import hsv_text, rgb_text

    assert rgb_text(QColor("#c04040")) == "rgb(192, 64, 64)"
    assert hsv_text(QColor("#c04040")) == "hsv(0, 67%, 75%)"
    assert hsv_text(QColor("#808080")) == "hsv(0, 0%, 50%)", "greys have no hue (Qt says -1)"
    h = make()
    try:
        h.service("brush").set_color(QColor("#3366cc"))
        assert h.app.run_command("color_picker.copy_color", "rgb") == "rgb(51, 102, 204)"
        assert QApplication.clipboard().text() == "rgb(51, 102, 204)"
        assert h.app.run_command("color_picker.copy_color", "hsv") == "hsv(220, 75%, 80%)"
        assert h.app.run_command("color_picker.copy_color", "hex") == "#3366CC"
        h.set_tool("color_picker")
        panel = h.tool_panel("color_picker")
        assert panel.current_detail.text() == "rgb(51, 102, 204)   hsv(220, 75%, 80%)"
    finally:
        h.close()


def test_split_desktop_cuts_one_picture_per_screen():
    from pixelref.builtins.color_picker.screen import split_desktop

    screen = QGuiApplication.primaryScreen()
    g = screen.geometry()
    desktop = QImage(g.width() * 2, g.height() * 2, QImage.Format_RGB32)  # a 2x scaled desktop
    desktop.fill(QColor("#204060"))
    parts = split_desktop(desktop)
    assert len(parts) == len(QGuiApplication.screens())
    assert parts[0][0] is screen and parts[0][1].size() == desktop.size()


def test_pick_from_frozen_screen_sets_brush_color():
    from pixelref.builtins.color_picker.screen import ScreenPicker

    h = make()
    try:
        picked = []
        picker = ScreenPicker(picked.append, lambda _c: None, lambda _m: None)
        screen = QGuiApplication.primaryScreen()
        image = QImage(screen.geometry().size(), QImage.Format_RGB32)
        image.fill(QColor("#123456"))
        picker.busy = True
        picker.show([(screen, image)])
        window = picker.windows[0]
        QTest.qWaitForWindowExposed(window)
        pos = QPointF(40, 40)
        QApplication.sendEvent(window, QMouseEvent(QEvent.MouseMove, pos, window.mapToGlobal(pos), Qt.NoButton,
                                                   Qt.NoButton, Qt.NoModifier))
        assert window.pos is not None, "the loupe follows the cursor"
        QApplication.sendEvent(window, QMouseEvent(QEvent.MouseButtonPress, pos, window.mapToGlobal(pos),
                                                   Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        assert [c.name() for c in picked] == ["#123456"] and not picker.windows and not picker.busy
        # Esc cancels.
        picker.busy = True
        picker.show([(screen, image)])
        QTest.keyClick(picker.windows[0], Qt.Key_Escape)
        assert picked[-1] is None and not picker.windows
    finally:
        h.close()


# ---- Image info ------------------------------------------------------------------------------------


def test_image_info_reads_source_pixels_through_crop_and_flip():
    h = make()
    try:
        h.add_image("Img", -100, -50, w=200, h=100)
        info = h.service("image_info")
        assert not info.enabled()
        h.app.run_command("image_info.toggle")
        assert info.enabled()
        pixel = h.view.to_viewport(QPointF(-89.5, -39.5))  # the middle of pixel (10, 10)
        data = info.readout(h.view, pixel)
        assert (data["x"], data["y"]) == (10, 10), data
        assert data["lines"][0] == "Img" and "200 × 100 px" in data["lines"][2]
        assert data["lines"][3].endswith("Img.png")
        h.select(["Img"])
        h.run("transform.flip", "h")
        h.refresh()
        flipped = info.readout(h.view, pixel)
        assert (flipped["x"], flipped["y"]) == (189, 10), flipped
        assert info.readout(h.view, QPointF(h.vp(300, 300))) is None, "off the image"
        h.run("set_attr", "Img", "crop", [50, 0, 100, 100])
        h.refresh()
        assert "cropped from 200 × 100" in info.readout(h.view, h.view.to_viewport(QPointF(0, 0)))["lines"][2]
        # The overlay paints next to the cursor.
        h.mouse("move", h.vp(-90, -40), buttons=Qt.NoButton)
        h.grab()
    finally:
        h.app.run_command("image_info.toggle")
        h.close()


# ---- Ellipses -----------------------------------------------------------------------------------------


def test_ellipse_tool_draws_ellipses_that_hit_inside_the_curve():
    h = make()
    try:
        h.set_tool("oval")
        h.drag(h.vp(0, 0), h.vp(200, 100))
        names = h.nodes_of("annotation_rect")
        assert len(names) == 1 and h.attr(names[0], "shape") == "ellipse", names
        assert names[0].startswith("Ellipse")
        assert h.tool() == "oval", "stays active like the Rect tool"
        h.run("set_attr", names[0], "fill_opacity", 1.0)
        h.refresh()
        item = h.item(names[0])
        assert item.shape().contains(QPointF(100, 50))
        assert not item.shape().contains(QPointF(4, 4)), "the box corner is outside the ellipse"
        # Shift draws a circle.
        h.drag(h.vp(300, 0), h.vp(400, 60), modifiers=Qt.ShiftModifier)
        circle = [n for n in h.nodes_of("annotation_rect") if n != names[0]][0]
        assert h.attr(circle, "size") == [100.0, 100.0]
        h.select([circle])
        assert "radius" not in h.gizmo().get("handles_vp", {}), "no corner radius on an ellipse"
        made = h.run("rect.create_ellipse", 0, 300, 50, 20)
        assert h.attr(made, "shape") == "ellipse" and h.attr(made, "size") == [50.0, 20.0]
    finally:
        h.close()


# ---- Notes: links, checklists, copy / paste style -------------------------------------------------------


def test_note_links_round_trip_without_link_look_and_auto_links_are_not_stored():
    h = make()
    try:
        runs = [{"text": "see "}, {"text": "board", "link": "/tmp/x.pxref"}, {"text": " and https://example.com"}]
        h.create("annotation_text", "Note", text="see board and https://example.com", runs=runs)
        item = h.item("Note")
        text, extracted = item.extract_content()
        assert extracted == runs, extracted
        # The link under a point (the auto-detected address too).
        block = item.document().begin().layout()
        x_board = block.lineAt(0).cursorToX(6)[0]
        y = block.lineAt(0).y() + block.lineAt(0).height() / 2 + item.document().documentMargin()
        assert item.link_at(QPointF(x_board + item.document().documentMargin(), y)) == "/tmp/x.pxref"
        x_web = block.lineAt(0).cursorToX(text.index("example"))[0] + item.document().documentMargin()
        assert item.link_at(QPointF(x_web, y)) == "https://example.com"
        # Linking the whole note, then removing it.
        h.select(["Note"])
        h.run("text.set_link", "https://pixelref.org")
        assert all(run.get("link") == "https://pixelref.org" for run in h.attr("Note", "runs"))
        h.run("text.set_link", "")
        assert h.attr("Note", "runs") is None and h.attr("Note", "text") == text
    finally:
        h.close()


def test_link_while_editing_and_open_link_targets():
    h = make()
    try:
        h.create("annotation_text", "Note", text="open the board")
        item = h.item("Note")
        item.start_edit()
        from PySide6.QtGui import QTextCursor

        cursor = item.textCursor()
        cursor.setPosition(9)
        cursor.setPosition(14, QTextCursor.KeepAnchor)
        item.setTextCursor(cursor)
        item.set_link("/nowhere/board.pxref")
        item.commit_edit()
        h.refresh()
        assert h.attr("Note", "runs") == [{"text": "open the "}, {"text": "board", "link": "/nowhere/board.pxref"}]
        assert h.app.run_command("text.open_link", "/nowhere/board.pxref") is None, "missing file: an error"
        assert "Not found" in h.app.status_text()
        # Removing it while typing takes the link look away too.
        item = h.item("Note")
        item.start_edit()  # selects everything
        item.set_link("")
        item.commit_edit()
        h.refresh()
        assert h.attr("Note", "runs") is None and not h.attr("Note", "underline")
    finally:
        h.close()


def test_checklist_boxes_toggle_by_click_and_keep_formatting():
    h = make()
    try:
        runs = [{"text": "buy "}, {"text": "milk", "bold": True}, {"text": "\ncall"}]
        h.create("annotation_text", "Note", text="buy milk\ncall", runs=runs)
        h.select(["Note"])
        h.run("text.toggle_checklist")
        assert h.attr("Note", "text") == "☐ buy milk\n☐ call"
        assert {"text": "milk", "bold": True} in h.attr("Note", "runs")
        h.refresh()
        h.set_tool("select")
        h.select([])
        item = h.item("Note")
        layout = item.document().findBlockByNumber(1).layout()
        margin = item.document().documentMargin()
        local = QPointF(margin + 3, item.document().findBlockByNumber(1).layout().position().y()
                        + layout.lineAt(0).height() / 2)
        box = h.view.to_viewport(item.mapToScene(local)).toPoint()
        h.click(box)
        assert h.attr("Note", "text") == "☐ buy milk\n☑ call"
        assert h.sel() == [], "ticking a box doesn't select or drag the note"
        h.undo()
        assert h.attr("Note", "text") == "☐ buy milk\n☐ call"
        # A quick double click ticks twice, and doesn't start typing in the note.
        for kind in ("press", "release", "double", "release"):
            h.mouse(kind, box)
        assert h.attr("Note", "text") == "☐ buy milk\n☐ call" and not h.item("Note").editing
        h.select(["Note"])
        h.run("text.toggle_checklist")
        assert h.attr("Note", "text") == "buy milk\ncall" and h.attr("Note", "runs") == runs
    finally:
        h.close()


def test_copy_and_paste_text_style():
    h = make()
    try:
        h.create("annotation_text", "A", text="source", font_size=30.0, bold=True, color=[255, 0, 0],
                 background_color=[0, 0, 255, 255])
        h.create("annotation_text", "B", text="target", translate=[0, 200])
        h.select(["A"])
        h.key(Qt.Key_C, Qt.ControlModifier | Qt.ShiftModifier)
        h.select(["B"])
        h.key(Qt.Key_V, Qt.ControlModifier | Qt.ShiftModifier)
        attrs = h.attrs("B")
        assert attrs["font_size"] == 30.0 and attrs["bold"] and attrs["color"] == [255, 0, 0], attrs
        assert attrs["background_color"] == [0, 0, 255, 255] and attrs["text"] == "target"
        assert h.undo_label() == "Paste text style"
        # While typing, onto the selected words only.
        h.create("annotation_text", "C", text="one two", translate=[0, 400])
        item = h.item("C")
        item.start_edit()
        from PySide6.QtGui import QTextCursor

        cursor = item.textCursor()
        cursor.setPosition(4)
        cursor.setPosition(7, QTextCursor.KeepAnchor)
        item.setTextCursor(cursor)
        h.app.run_command("text.paste_style")
        item.commit_edit()
        runs = h.attr("C", "runs")
        assert runs and runs[0]["text"] == "one " and runs[1]["text"] == "two" and runs[1].get("bold"), runs
    finally:
        h.close()
