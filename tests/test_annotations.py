"""Annotations: text notes, rectangles, arrows, pen drawings, the eraser and the colour picker."""

from __future__ import annotations

import math

from harness import QTest, approx, make, tmp_dir
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QPushButton


def choose(combo, value) -> None:
    combo.setCurrentIndex(combo.findData(value))


def commit_spin(spin, value) -> None:
    spin.setValue(value)
    spin.editingFinished.emit()


# ---- Text notes ------------------------------------------------------------------------------


def _make_note(h, text="hello world", **attrs) -> str:
    h.create("annotation_text", "Note", text=text, translate=[0.0, 0.0], **attrs)
    h.select(["Note"])
    return "Note"


def _select_chars(item, start: int, end: int) -> None:
    from PySide6.QtGui import QTextCursor

    cursor = item.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.KeepAnchor)
    item.setTextCursor(cursor)


def test_typing_in_note_does_not_trigger_shortcuts():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        h.select([])
        h.trigger("text.add")
        note = h.sel()[0]
        item = h.item(note)
        assert type(item).__name__ == "TextItem" and item.editing
        QTest.keyClicks(h.view, "HL")
        QTest.keyClick(h.view, Qt.Key_Escape)
        assert h.attr(note, "text") == "HL"
        assert h.attr(note, "visibility") == 1 and h.attr(note, "locked") == 0
    finally:
        h.close()


def test_text_paste_and_drop_ingest():
    from harness import make_png
    from PySide6.QtCore import QUrl

    h = make()
    try:
        mime = QMimeData()
        mime.setText("hello world")
        h.drop(QPointF(10, 20), mime)
        names = h.sel()
        assert len(names) == 1 and h.attr(names[0], "text") == "hello world"
        png = make_png("dropped.png", 80, 40)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(png))])
        mime.setText(str(png))  # browsers/explorer add text too; must not create a note
        h.drop(QPointF(0, 0), mime)
        names = h.sel()
        assert [h.type_of(n) for n in names] == ["image"], names
        assert h.attr(names[0], "translate") == [-40.0, -20.0], "drop centres the image"
    finally:
        h.close()


def test_text_whole_note_styles_from_inspector():
    h = make()
    try:
        note = _make_note(h)
        text = h.section("text")
        QTest.mouseClick(text.bold_btn, Qt.LeftButton)
        QTest.mouseClick(text.underline_btn, Qt.LeftButton)
        QTest.mouseClick(text.align_btns["center"], Qt.LeftButton)
        text.apply_format("highlight_color", [255, 235, 59, 200], "Highlight color")
        attrs = h.attrs(note)
        assert attrs["bold"] is True and attrs["underline"] is True and attrs["align"] == "center"
        assert attrs["highlight_color"] == [255, 235, 59, 200]
        assert text.bold_btn.isChecked()
        item = h.item(note)
        assert item.font().bold() and item.font().underline()
        fmt = item.document().begin().begin().fragment().charFormat()
        assert fmt.background().color().name() == "#ffeb3b", "highlight sits behind the glyphs"
        h.undo()
        assert h.attr(note, "highlight_color")[3] == 0
    finally:
        h.close()


def test_text_selection_styles_while_editing():
    from PySide6.QtGui import QFocusEvent

    h = make()
    try:
        note = _make_note(h)
        text = h.section("text")
        item = h.item(note)
        h.view.setFocus()
        item.start_edit()
        _select_chars(item, 6, 11)  # "world"
        QTest.mouseClick(text.bold_btn, Qt.LeftButton)  # NoFocus button: editing must continue
        assert item.editing, "clicking a style button ended the edit"
        text.apply_format("highlight_color", [255, 120, 0, 180], "Highlight color")
        text.apply_format("color", [10, 200, 90], "Text color")
        # A colour dialog deactivates the window; that must not end the edit either.
        item.focusOutEvent(QFocusEvent(QEvent.FocusOut, Qt.ActiveWindowFocusReason))
        assert item.editing
        h.run("escape")
        attrs = h.attrs(note)
        assert attrs["text"] == "hello world"
        assert attrs["runs"] == [
            {"text": "hello "},
            {"text": "world", "bold": True, "color": [10, 200, 90], "highlight_color": [255, 120, 0, 180]},
        ], attrs["runs"]
        assert attrs["bold"] is False, "whole-note bold must not change"
        h.undo()
        assert h.attr(note, "runs") is None
        h.redo()
        rebuilt = h.item(note)
        fragments = []
        iterator = rebuilt.document().begin().begin()
        while not iterator.atEnd():
            fragments.append((iterator.fragment().text(), iterator.fragment().charFormat().fontWeight()))
            iterator += 1
        assert fragments == [("hello ", 400), ("world", 700)], fragments
    finally:
        h.close()


def test_text_shortcuts_while_editing_and_uniform_promotion():
    h = make()
    try:
        note = _make_note(h, text="all of it")
        item = h.item(note)
        h.view.setFocus()
        item.start_edit()  # selects all text
        QTest.keyClick(h.view, Qt.Key_B, Qt.ControlModifier)
        QTest.keyClick(h.view, Qt.Key_I, Qt.ControlModifier)  # must not open "Insert Images"
        QTest.keyClick(h.view, Qt.Key_Escape)
        attrs = h.attrs(note)
        assert attrs["bold"] is True and attrs["italic"] is True, attrs
        assert attrs["runs"] is None, "uniform styling is stored as note attributes, not runs"
    finally:
        h.close()


def test_text_box_fill_border_and_presets_render():
    h = make()
    try:
        note = _make_note(h, text="box")
        text, box = h.section("text"), h.section("text_box")
        text.text_preset_combo.activated.emit(text.text_preset_combo.findText("Sticky note"))
        attrs = h.attrs(note)
        assert attrs["background_color"] == [255, 226, 110, 255] and attrs["border_width"] == 1.0
        commit_spin(box.text_border_width_spin, 4.0)
        choose(box.text_border_style_combo, "dash")
        assert h.attr(note, "border_style") == "dash"
        choose(box.text_fill_style_combo, "diagonal")
        assert h.attr(note, "fill_style") == "diagonal"
        choose(box.text_fill_style_combo, "solid")
        item = h.item(note)
        image = h.app.canvas.render_items([item])
        rect = item.boundingRect()
        inside = image.pixelColor(int(rect.width() / 2), 8)
        edge = image.pixelColor(int(rect.width() / 2), 1)
        assert (inside.red(), inside.green(), inside.blue()) == (255, 226, 110), inside.name()
        assert (edge.red(), edge.green(), edge.blue()) == (196, 160, 52), edge.name()
        commit_spin(box.text_width_spin, 300.0)  # wrap width makes the box exactly that wide
        assert approx(h.item(note).boundingRect().width(), 300.0, 0.5)
    finally:
        h.close()


def test_text_runs_survive_save_and_reload():
    h = make()
    try:
        runs = [{"text": "plain "}, {"text": "marked", "highlight_color": [255, 235, 59, 200], "strike": True}]
        _make_note(h, text="plain marked", runs=runs, border_width=2.0, border_style="dot", corner_radius=6.0)
        path = tmp_dir() / "notes.pxref"
        assert h.save_to(path)
        h2 = make()
        try:
            assert h2.open(path)
            attrs = h2.attrs("Note")
            assert attrs["runs"] == runs and attrs["border_style"] == "dot"
            text, extracted = h2.item("Note").extract_content()
            assert text == "plain marked" and extracted == runs, extracted
        finally:
            h2.close()
    finally:
        h.close()


def _edge_x(item, side: str) -> tuple[float, float]:
    poly = item.scene_polygon()
    return (poly[0].x(), poly[3].x()) if side == "left" else (poly[1].x(), poly[2].x())


def test_text_box_width_drag_handles():
    h = make()
    try:
        note = _make_note(h, text="one two three four five six seven eight nine ten")
        item = h.item(note)
        g = h.gizmo()
        assert "tm" not in g["handles_vp"] and "bm" not in g["handles_vp"], "note height follows its text"
        start_rect = item.content_rect()
        left_before = _edge_x(item, "left")
        # Right handle: narrower box, text wraps onto more lines, left edge fixed.
        mr = g["handles_vp"]["mr"].toPoint()
        h.drag(mr, QPoint(h.vp(120, 0).x(), mr.y()))
        attrs = h.attrs(note)
        assert approx(attrs["text_width"], 120, 1.0), attrs["text_width"]
        assert attrs["scale"] == [1.0, 1.0], "resizing the box must not stretch the text"
        assert item.content_rect().height() > start_rect.height() * 1.5, "text should reflow onto more lines"
        assert all(approx(a, b, 0.5) for a, b in zip(_edge_x(item, "left"), left_before))
        assert h.section("text_box").text_width_spin.value() == attrs["text_width"]
        assert h.undo_label() == "Resize text box"
        # Left handle: right edge stays where it is.
        right_before = _edge_x(item, "right")
        ml = h.gizmo()["handles_vp"]["ml"].toPoint()
        h.drag(ml, QPoint(ml.x() - 80, ml.y()))
        assert approx(h.attr(note, "text_width"), 200, 1.0)
        assert all(approx(a, b, 0.5) for a, b in zip(_edge_x(item, "right"), right_before))
        # Double-click a side handle: back to auto width.
        ml = h.gizmo()["handles_vp"]["ml"].toPoint()
        h.click(ml)
        h.mouse("double", ml)
        h.mouse("release", ml)
        assert h.attr(note, "text_width") == 0.0
        assert approx(item.content_rect().width(), start_rect.width(), 0.5)
        h.undo()
        assert approx(h.attr(note, "text_width"), 200, 1.0)
        QTest.mouseClick(h.section("text_box").text_auto_width_btn, Qt.LeftButton)
        assert h.attr(note, "text_width") == 0.0
    finally:
        h.close()


def test_text_box_width_on_scaled_rotated_note():
    h = make()
    try:
        note = _make_note(h, text="some words to wrap around", scale=[2.0, 2.0], rotate=90.0)
        width_before = h.item(note).content_rect().width()
        g = h.gizmo()
        mr = g["handles_vp"]["mr"]
        center = h.view.to_viewport(g["center_scene"])
        outward = (mr - center) / max(1e-6, (mr - center).manhattanLength())
        # 100 screen px outward along the note's (rotated) x axis = +50 local units at 2x scale.
        target = mr + QPointF(outward.x() * 100, outward.y() * 100)
        h.drag(mr.toPoint(), target.toPoint())
        width = h.attr(note, "text_width")
        assert approx(width, width_before + 50, 1.5), (width, width_before)
        assert h.attr(note, "rotate") == 90.0
    finally:
        h.close()


# ---- Rectangles & arrows ---------------------------------------------------------------------------


def _rect_attrs(h, name):
    return h.attr(name, "translate"), h.attr(name, "size")


def _line_scene_points(item):
    st = item.sceneTransform()
    return st.map(item.endpoint("start")), st.map(item.endpoint("end"))


def test_text_undo_while_editing_keeps_loaded_text_and_paste_is_plain():
    h = make()
    try:
        note = _make_note(h, "hello")
        h.refresh()
        item = h.item(note)
        h.view.setFocus()
        item.start_edit()
        QTest.keyClick(h.view, Qt.Key_Z, Qt.ControlModifier)
        assert item.toPlainText() == "hello", "Ctrl+Z must not undo loading the note's text"
        _select_chars(item, 5, 5)
        mime = QMimeData()
        mime.setHtml('<span style="background:#ffffff;font-size:40px">X<img src="a.png">Y</span>')
        mime.setText("XY")
        QApplication.clipboard().setMimeData(mime)
        count = len(h.session.scene.nodes)
        QTest.keyClick(h.view, Qt.Key_V, Qt.ControlModifier)
        assert item.editing and len(h.session.scene.nodes) == count, "pastes into the note, not the canvas"
        QTest.keyClick(h.view, Qt.Key_Escape)
        assert h.attr(note, "text") == "helloXY" and not h.attr(note, "runs"), "pasted as plain text"
    finally:
        h.close()


def test_draw_tools_create_shapes():
    h = make()
    try:
        h.set_tool("rect")
        h.drag(h.vp(0, 0), h.vp(120, 60))
        assert h.tool() == "rect", "the tool stays active after drawing"
        size = h.attr(h.sel()[0], "size")
        assert approx(size[0], 120, 1) and approx(size[1], 60, 1)
        h.set_tool("arrow")
        h.drag(h.vp(0, 100), h.vp(100, 100))
        assert h.attr(h.sel()[0], "arrow") == 1
    finally:
        h.close()


def test_arrow_endpoints_can_be_dragged():
    h = make()
    try:
        h.set_tool("arrow")
        h.drag(h.vp(0, 0), h.vp(200, 0))
        name = h.sel()[0]
        item = h.item(name)
        g = h.gizmo()
        assert g.get("line") and set(g["handles_vp"]) == {"start", "end"}, "a line shows endpoint handles only"
        # Drag the end point; the start point stays put.
        h.drag(g["handles_vp"]["end"].toPoint(), h.vp(150, 120))
        start, end = _line_scene_points(item)
        assert approx(start.x(), 0, 1) and approx(start.y(), 0, 1)
        assert approx(end.x(), 150, 1) and approx(end.y(), 120, 1), (end.x(), end.y())
        attrs = h.attrs(name)
        assert approx(attrs["end"][0], 150, 1) and approx(attrs["end"][1], 120, 1)
        assert h.undo_label() == "Move line point"
        # Drag the start point with Shift: snaps the angle to 45° steps.
        g = h.gizmo()
        h.drag(g["handles_vp"]["start"].toPoint(), h.vp(150 - 100, 120 - 92), modifiers=Qt.ShiftModifier)
        start, end = _line_scene_points(item)
        assert approx(abs(end.x() - start.x()), abs(end.y() - start.y()), 0.5), "Shift should snap to 45°"
        # In the Select tool, dragging the line body moves the whole arrow.
        h.set_tool("select")
        before = _line_scene_points(item)
        mid = (before[0] + before[1]) / 2.0
        h.drag(h.vp(mid.x(), mid.y()), h.vp(mid.x() + 30, mid.y() + 10))
        after = _line_scene_points(item)
        assert approx(after[1].x() - before[1].x(), 30, 1) and approx(after[1].y() - before[1].y(), 10, 1)
        h.undo()
        h.undo()
        h.undo()
        start, end = _line_scene_points(h.item(name))
        assert approx(end.x(), 200, 1) and approx(end.y(), 0, 1)
    finally:
        h.close()


def test_rect_and_arrow_tools_stay_active():
    h = make()
    try:
        h.set_tool("rect")
        h.drag(h.vp(0, 0), h.vp(100, 50))
        h.drag(h.vp(0, 100), h.vp(100, 150))
        h.drag(h.vp(0, 200), h.vp(100, 260), modifiers=Qt.AltModifier)  # Alt: from the centre
        assert len(h.nodes_of("annotation_rect")) == 3 and h.tool() == "rect"
        position, size = _rect_attrs(h, h.sel()[0])
        assert approx(position[0], -100, 1) and approx(position[1], 140, 1)
        assert approx(size[0], 200, 1) and approx(size[1], 120, 1)
        # The last shape's corner points can be adjusted without leaving the tool.
        last = h.sel()[0]
        h.drag(h.gizmo()["handles_vp"]["br"].toPoint(), h.vp(150, 300))
        assert len(h.nodes_of("annotation_rect")) == 3, "dragging a handle must not draw a new rectangle"
        _position, size = _rect_attrs(h, last)
        assert approx(size[0], 250, 1) and approx(size[1], 160, 1)
        assert h.tool() == "rect"
        # Starting a drag just outside the corner draws (no hidden rotate zone in tools).
        h.drag(h.vp(165, 315), h.vp(220, 360))
        assert len(h.nodes_of("annotation_rect")) == 4
        h.set_tool("arrow")
        h.drag(h.vp(300, 0), h.vp(400, 0))
        h.drag(h.vp(300, 50), h.vp(400, 50))
        assert len(h.nodes_of("annotation_line")) == 2 and h.tool() == "arrow"
        arrow = h.sel()[0]
        h.drag(h.gizmo()["handles_vp"]["end"].toPoint(), h.vp(450, 90))
        item = h.item(arrow)
        end = item.sceneTransform().map(item.endpoint("end"))
        assert approx(end.x(), 450, 1) and approx(end.y(), 90, 1)
        assert len(h.nodes_of("annotation_line")) == 2
        h.key(Qt.Key_Escape)
        assert h.tool() == "select"
    finally:
        h.close()


def test_arrow_tool_arrowhead_options_are_separate_from_pen():
    h = make()
    try:
        h.set_tool("arrow")
        panel = h.tool_panel("arrow")
        assert panel.arrows_row.isVisible() and panel.arrow_end_btn.isChecked()
        assert not panel.arrow_start_btn.isChecked()
        QTest.mouseClick(panel.arrow_start_btn, Qt.LeftButton)
        h.drag(h.vp(0, 0), h.vp(100, 0))
        attrs = h.attrs(h.sel()[0])
        assert attrs["arrow"] == 1 and attrs["arrow_start"] == 1
        h.set_tool("pen")
        pen = h.tool_panel("pen")
        assert not pen.arrow_end_btn.isChecked() and not pen.arrow_start_btn.isChecked(), "pen keeps its own"
        h.set_tool("arrow")
        assert h.tool_panel("arrow").arrow_start_btn.isChecked()
    finally:
        h.close()


def test_line_endpoint_drag_on_rotated_scaled_line():
    h = make()
    try:
        h.create("annotation_line", "L", translate=[0.0, 0.0], start=[0.0, 0.0], end=[100.0, 0.0],
                 scale=[2.0, 2.0], rotate=90.0)
        h.select(["L"])
        item = h.item("L")
        start, end = _line_scene_points(item)
        assert approx(end.x(), 0, 0.5) and approx(end.y(), 200, 0.5)
        h.drag(h.gizmo()["handles_vp"]["end"].toPoint(), h.vp(-100, 100))
        start, end = _line_scene_points(item)
        assert approx(end.x(), -100, 1) and approx(end.y(), 100, 1)
        assert approx(start.x(), 0, 0.5) and approx(start.y(), 0, 0.5)
    finally:
        h.close()


def test_rect_corner_and_edge_points_reshape():
    h = make()
    try:
        h.set_tool("rect")
        h.drag(h.vp(0, 0), h.vp(120, 60))
        name = h.sel()[0]
        g = h.gizmo()
        assert g.get("rect") and {"tl", "tr", "br", "bl", "tm", "bm", "ml", "mr"} <= set(g["handles_vp"])
        # Bottom-right corner: top-left stays, the border is not scaled.
        h.drag(g["handles_vp"]["br"].toPoint(), h.vp(200, 100))
        position, size = _rect_attrs(h, name)
        assert position == [0, 0] and approx(size[0], 200, 1) and approx(size[1], 100, 1), (position, size)
        assert h.attr(name, "scale") == [1.0, 1.0] and h.attr(name, "thickness") == 4.0, "border is not scaled"
        assert h.undo_label() == "Resize rectangle"
        # Top-left corner: bottom-right stays.
        h.drag(h.gizmo()["handles_vp"]["tl"].toPoint(), h.vp(50, 20))
        position, size = _rect_attrs(h, name)
        assert approx(position[0], 50, 1) and approx(position[1], 20, 1)
        assert approx(size[0], 150, 1) and approx(size[1], 80, 1)
        # Edge point changes one side only.
        h.drag(h.gizmo()["handles_vp"]["mr"].toPoint(), h.vp(300, 999))
        position, size = _rect_attrs(h, name)
        assert approx(size[0], 250, 1) and approx(size[1], 80, 1) and approx(position[1], 20, 1)
        # Shift keeps it square; dragging past the opposite corner flips it over.
        h.drag(h.gizmo()["handles_vp"]["br"].toPoint(), h.vp(150, 190), modifiers=Qt.ShiftModifier)
        _position, size = _rect_attrs(h, name)
        assert approx(size[0], size[1], 0.5), size
        h.drag(h.gizmo()["handles_vp"]["br"].toPoint(), h.vp(-10, -40))
        position, size = _rect_attrs(h, name)
        assert approx(position[0], -10, 1) and approx(position[1], -40, 1)
        assert approx(size[0], 60, 1) and approx(size[1], 60, 1), size
        # Alt resizes around the centre.
        center_before = h.item(name).scene_content_rect().center()
        h.drag(h.gizmo()["handles_vp"]["mr"].toPoint(), h.vp(50, 0), modifiers=Qt.AltModifier)
        center_after = h.item(name).scene_content_rect().center()
        assert approx(center_after.x(), center_before.x(), 1) and approx(center_after.y(), center_before.y(), 1)
    finally:
        h.close()


def test_rect_points_on_rotated_rect_and_radius_handle():
    h = make()
    try:
        h.create("annotation_rect", "Box", translate=[0.0, 0.0], size=[100.0, 50.0], rotate=90.0)
        h.select(["Box"])
        item = h.item("Box")
        # Rotated 90°: local bottom-right sits at scene (-50, 100).
        h.drag(h.gizmo()["handles_vp"]["br"].toPoint(), h.vp(-80, 150))
        _position, size = _rect_attrs(h, "Box")
        assert approx(size[0], 150, 1) and approx(size[1], 80, 1), size
        assert h.attr("Box", "rotate") == 90.0
        g = h.gizmo()
        assert "radius" in g["handles_vp"]
        corner = item.sceneTransform().map(QPointF(0, 0))
        # Pull the radius handle 20 units in along the local diagonal.
        h.drag(g["handles_vp"]["radius"].toPoint(), h.vp(corner.x() - 20, corner.y() + 20))
        radius = h.attr("Box", "corner_radius")
        assert approx(radius, 20, 1.5), radius
        assert approx(h.section("stroke").shape_radius_spin.value(), radius, 0.01)
        assert h.undo_label() == "Corner radius"
    finally:
        h.close()


def test_shape_line_and_fill_styles():
    h = make()
    try:
        h.create("annotation_rect", "Box", translate=[0.0, 0.0], size=[100.0, 60.0])
        h.select(["Box"])
        stroke = h.section("stroke")
        choose(stroke.stroke_style_combo, "dash")
        choose(stroke.shape_fill_style_combo, "cross")
        commit_spin(stroke.shape_radius_spin, 12.0)
        attrs = h.attrs("Box")
        assert attrs["stroke_style"] == "dash" and attrs["fill_style"] == "cross" and attrs["corner_radius"] == 12.0
    finally:
        h.close()


# ---- Pen, drawings, eraser ------------------------------------------------------------------------


def test_pen_tool_draws_smooth_strokes_and_stays_active():
    h = make()
    try:
        h.set_tool("pen")
        brush = h.service("brush")
        commit_spin(h.tool_panel("pen").brush_row.width_spin, 6.0)
        brush.set_color(QColor(30, 220, 120))
        h.mouse("press", h.vp(-100, 0))
        for i in range(1, 61):
            x = -100 + i * (200 / 60)
            h.mouse("move", h.vp(x, 40 * math.sin(i / 60 * math.pi * 2)), buttons=Qt.LeftButton)
        h.mouse("release", h.vp(100, 0))
        assert h.tool() == "pen", "pen stays active for the next stroke"
        strokes = h.nodes_of("annotation_path")
        assert len(strokes) == 1
        attrs = h.attrs(strokes[0])
        assert attrs["color"] == [30, 220, 120] and attrs["thickness"] == 6.0
        assert 5 <= len(attrs["points"]) < 61, f"points should be simplified, got {len(attrs['points'])}"
        rect = h.item(strokes[0]).scene_content_rect()
        assert approx(rect.left(), -100, 2) and approx(rect.right(), 100, 2)
        assert approx(rect.top(), -40, 3) and approx(rect.bottom(), 40, 3)
        pixel = h.grab(clean=True).pixelColor(h.vp(-100, 0))
        assert (pixel.red(), pixel.green(), pixel.blue()) == (30, 220, 120), pixel.name()
        h.drag(h.vp(0, 100), h.vp(50, 150))
        assert len(h.nodes_of("annotation_path")) == 2
        h.undo()
        assert len(h.nodes_of("annotation_path")) == 1
        h.key(Qt.Key_Escape)
        assert h.tool() == "select"
    finally:
        h.close()


def test_eraser_removes_only_strokes():
    h = make()
    try:
        h.add_image("Img", -150, -100, w=300, h=200)
        h.set_tool("pen")
        h.drag(h.vp(-100, -50), h.vp(100, -50))
        h.drag(h.vp(-100, 50), h.vp(100, 50))
        assert len(h.nodes_of("annotation_path")) == 2
        h.set_tool("eraser")
        h.drag(h.vp(0, -80), h.vp(0, -20))  # crosses only the top stroke (and the image)
        types = h.node_types()
        assert types.count("annotation_path") == 1 and "image" in types
        h.undo()
        assert len(h.nodes_of("annotation_path")) == 2
    finally:
        h.close()


def test_eraser_removes_rects_arrows_and_strokes_but_not_images_or_text():
    h = make()
    try:
        h.add_image("Img", -300, -100, w=200, h=200)
        h.create("annotation_text", "Note", text="keep me", translate=[-300.0, 150.0])
        h.set_tool("rect")
        h.drag(h.vp(0, 0), h.vp(100, 60))
        h.set_tool("arrow")
        h.drag(h.vp(150, 30), h.vp(250, 30))
        h.set_tool("pen")
        h.scribble(300, 30, length=80)
        h.set_tool("eraser")
        h.drag(h.vp(-400, 30), h.vp(450, 30), steps=40)  # sweeps across everything
        h.drag(h.vp(-250, 140), h.vp(-250, 170))  # and over the note
        assert sorted(h.node_types()) == ["annotation_text", "image"], sorted(h.node_types())
        h.undo()
        h.undo()
        types = h.node_types()
        assert "annotation_rect" in types and "annotation_line" in types
    finally:
        h.close()


def test_stroke_styling_and_round_trip():
    h = make()
    try:
        h.set_tool("pen")
        h.drag(h.vp(0, 0), h.vp(80, 60))
        h.set_tool("select")
        name = h.nodes_of("annotation_path")[0]
        h.select([name])
        stroke = h.section("stroke")
        assert stroke.isVisible() and stroke.arrow_row.isVisible() and stroke.smoothing_spin.isVisible()
        commit_spin(stroke.thickness_spin, 12.0)
        choose(stroke.stroke_style_combo, "dot")
        attrs = h.attrs(name)
        assert attrs["thickness"] == 12.0 and attrs["stroke_style"] == "dot"
        path = tmp_dir() / "strokes.pxref"
        assert h.save_to(path)
        h2 = make()
        try:
            assert h2.open(path)
            assert h2.attr(name, "points") == attrs["points"]
        finally:
            h2.close()
    finally:
        h.close()


def test_pen_session_groups_strokes_into_one_drawing():
    h = make()
    try:
        h.set_tool("pen")
        for y in (0, 40, 80):
            h.scribble(-60, y)
        drawings = h.nodes_of("annotation_drawing")
        assert len(drawings) == 1, drawings
        strokes = h.nodes_of("annotation_path")
        assert len(strokes) == 3 and all(h.parent_of(s) == drawings[0] for s in strokes)
        assert h.outliner.tree_item(strokes[0]).parent() is h.outliner.tree_item(drawings[0])
        assert not h.grab().isNull()  # banner + dashed frame are painted while drawing
        h.key(Qt.Key_Escape)
        assert h.tool() == "select"
        assert h.sel() == drawings, "finishing selects the new drawing"
        # Pressing P again starts a separate drawing, even though the finished
        # drawing is still selected (regression: everything went into one drawing).
        for session in range(2):
            h.set_tool("pen")
            h.scribble(-60, 200 + session * 100)
            h.key(Qt.Key_P)  # P toggles back to select
        assert len(h.nodes_of("annotation_drawing")) == 3
        assert len({h.parent_of(s) for s in h.nodes_of("annotation_path")}) == 3
    finally:
        h.close()


def test_drawing_moves_as_one_and_can_be_edited_later():
    h = make()
    try:
        h.set_tool("pen")
        h.scribble(0, 0)
        h.scribble(0, 50)
        h.set_tool("select")
        drawing = h.nodes_of("annotation_drawing")[0]
        strokes = h.nodes_of("annotation_path")
        h.select([])
        # Clicking a stroke selects the whole drawing; dragging moves every stroke.
        before = {s: h.item(s).scene_content_rect().topLeft() for s in strokes}
        h.drag(h.vp(60, 0), h.vp(160, 100))
        assert h.sel() == [drawing]
        for s in strokes:
            moved = h.item(s).scene_content_rect().topLeft() - before[s]
            assert approx(moved.x(), 100, 1) and approx(moved.y(), 100, 1)
        # Scale the drawing 2x, then double-click it to keep drawing.
        h.select([drawing])
        h.run("transform.scale", 2.0)
        point = h.item(drawing).scene_content_rect().center()
        h.click(h.vp(point.x(), point.y()))
        target = h.item(drawing).sceneTransform().map(QPointF(60, 0))  # on the first stroke
        h.mouse("double", h.vp(target.x(), target.y()))
        h.mouse("release", h.vp(target.x(), target.y()))
        assert h.tool() == "pen" and h.service("drawing_session").target == drawing
        h.scribble(400, 400, length=100)
        h.set_tool("select")
        strokes_after = h.nodes_of("annotation_path")
        assert len(strokes_after) == 3 and len(h.nodes_of("annotation_drawing")) == 1
        new = next(s for s in strokes_after if s not in strokes)
        assert h.parent_of(new) == drawing
        rect = h.item(new).scene_content_rect()
        assert approx(rect.left(), 400, 1.5) and approx(rect.right(), 500, 1.5), "new stroke lands where drawn"
        width = h.service("brush").width
        assert approx(h.attr(new, "thickness"), width / 2.0, 0.01), "width compensates 2x scale"
    finally:
        h.close()


def test_eraser_session_and_empty_drawing_removed():
    h = make()
    try:
        h.set_tool("pen")
        h.scribble(0, 0)
        h.set_tool("select")
        first = h.nodes_of("annotation_drawing")[0]
        h.select([])
        h.set_tool("pen")
        h.scribble(0, 100)
        h.set_tool("eraser")
        h.drag(h.vp(60, 70), h.vp(60, 130))  # crosses only the second drawing's stroke
        assert len(h.nodes_of("annotation_path")) == 1, "strokes the eraser didn't touch survive"
        h.set_tool("select")
        assert h.nodes_of("annotation_drawing") == [first], "an erased-empty drawing is removed"
    finally:
        h.close()


def test_eraser_works_on_drawings_from_a_reopened_file():
    """Regression: strokes inside an existing drawing (e.g. after reopening a
    file) could not be erased — the eraser only accepted the drawing being edited."""
    h = make()
    try:
        h.set_tool("pen")
        for y in (0, 80, 160):
            h.scribble(-60, y)
        h.set_tool("select")
        path = tmp_dir() / "drawing.pxref"
        assert h.save_to(path)
        h2 = make()
        try:
            assert h2.open(path)
            h2.set_tool("eraser")  # no drawing opened for editing
            h2.drag(h2.vp(0, -30), h2.vp(0, 30))
            assert len(h2.nodes_of("annotation_path")) == 2
            h2.drag(h2.vp(0, 50), h2.vp(0, 190), steps=20)
            assert h2.nodes_of("annotation_path") == [] and h2.nodes_of("annotation_drawing") == []
            h2.undo()
            assert len(h2.nodes_of("annotation_path")) == 2 and len(h2.nodes_of("annotation_drawing")) == 1
        finally:
            h2.close()
    finally:
        h.close()


def test_stroke_smoothing_and_arrowheads():
    h = make()
    try:
        h.set_tool("pen")
        panel = h.tool_panel("pen")
        commit_spin(panel.smooth_field, 0.0)
        QTest.mouseClick(panel.arrow_end_btn, Qt.LeftButton)
        h.scribble(-100, 0, length=200, steps=40)
        h.mouse("press", h.vp(-100, 100))
        for i in range(1, 41):  # zig-zag
            h.mouse("move", h.vp(-100 + i * 5, 100 + (10 if i % 2 else -10)), buttons=Qt.LeftButton)
        h.mouse("release", h.vp(100, 100))
        h.set_tool("select")
        h.select([])
        straight, zigzag = sorted(h.nodes_of("annotation_path"), key=lambda n: h.item(n).scene_content_rect().top())
        assert h.attr(straight, "arrow") == 1
        # The shaft is trimmed back to x~87, so painted pixels at x=92, y=±3 can only come from the head.
        image = h.grab()
        for y in (-3, 3):
            head = image.pixelColor(h.vp(92, y))
            assert (head.red(), head.green(), head.blue()) == (255, 64, 64), (y, head.name())
        assert image.pixelColor(h.vp(70, 8)).name() == "#17191d", "only the head is wider than the stroke"

        def interior_wobble(name):
            item = h.item(name)
            pts = [item.mapToScene(p) for p in item.stroke_points()]
            middle = [p.y() for p in pts if -50 < p.x() < 50]
            return max(middle) - min(middle)

        rough = interior_wobble(zigzag)
        h.select([zigzag])
        commit_spin(h.section("stroke").smoothing_spin, 100)
        smooth = interior_wobble(zigzag)
        assert h.attr(zigzag, "smoothing") == 1.0
        assert rough > 15 and smooth < rough * 0.2, (rough, smooth)
        h.undo()
        assert h.attr(zigzag, "smoothing") == 0.0
    finally:
        h.close()


def test_drawing_inspector_restyles_all_strokes():
    h = make()
    try:
        h.set_tool("pen")
        h.scribble(0, 0)
        h.scribble(0, 50)
        h.set_tool("select")
        drawing = h.sel()[0]
        rows = h.inspector.rows["stroke"][0]
        assert rows.edit_drawing_btn.isVisible() and rows.separate_drawing_btn.isVisible()
        assert rows.drawing_hint.isVisible()
        stroke = h.section("stroke")
        commit_spin(stroke.thickness_spin, 9.0)
        stroke.arrow_check.setChecked(True)
        for name in h.nodes_of("annotation_path"):
            attrs = h.attrs(name)
            assert attrs["thickness"] == 9.0 and attrs["arrow"] == 1, attrs
        assert h.sel() == [drawing]
        path = tmp_dir() / "drawing.pxref"
        assert h.save_to(path)
        h2 = make()
        try:
            assert h2.open(path)
            assert len(h2.nodes_of("annotation_path")) == 2
            assert type(h2.item(drawing)) is type(h.item(drawing))
        finally:
            h2.close()
    finally:
        h.close()


def test_drawings_can_be_separated_and_combined():
    h = make()
    try:
        h.set_tool("pen")
        h.scribble(0, 0)
        h.scribble(0, 60)
        h.scribble(0, 120)
        h.set_tool("select")
        assert len(h.nodes_of("annotation_drawing")) == 1
        strokes = h.nodes_of("annotation_path")
        h.trigger("transform.rotate_cw")  # separating must preserve placement
        before = {s: h.corners(s) for s in strokes}
        menu = h.app.build_context_menu()
        texts = [a.text() for a in menu.actions()]
        assert "Separate Strokes" in texts, texts
        h.key(Qt.Key_G, Qt.ControlModifier | Qt.ShiftModifier)
        assert h.nodes_of("annotation_drawing") == [], "the drawing is dissolved"
        assert sorted(h.sel()) == sorted(strokes)
        for s in strokes:
            assert h.parent_of(s) is None
            assert all(approx(a[0], b[0], 0.05) and approx(a[1], b[1], 0.05)
                       for a, b in zip(before[s], h.corners(s))), s
        # A loose stroke is selectable and transformable on its own.
        h.select([strokes[0]])
        assert h.app.canvas.transform_targets() == [h.item(strokes[0])]
        # Combine two of them again (Ctrl+J).
        h.select(strokes[:2])
        h.key(Qt.Key_J, Qt.ControlModifier)
        combined = h.nodes_of("annotation_drawing")
        assert len(combined) == 1 and h.sel() == combined
        assert {h.parent_of(s) for s in strokes[:2]} == {combined[0]}
        assert h.parent_of(strokes[2]) is None
        # Dragging the last strokes out in the outliner removes the now-empty drawing.
        h.outliner.on_tree_drop(strokes[:2], None, None, "end")
        assert h.nodes_of("annotation_drawing") == []
        h.undo()
        assert h.nodes_of("annotation_drawing") == combined, "undo restores drawing and strokes together"
    finally:
        h.close()


# ---- Colour picker ------------------------------------------------------------------------------


def test_color_picker_copies_hex():
    h = make()
    try:
        h.add_image("Img", -100, -50, w=200, h=100)  # filled with #c04040
        h.select([])
        h.set_tool("color_picker")
        h.click(h.vp(0, 0))
        assert QApplication.clipboard().text() == "#C04040", QApplication.clipboard().text()
    finally:
        h.close()


def test_pick_mode_shows_loupe_and_live_color():
    h = make()
    try:
        h.add_image("Img", -100, -50, w=200, h=100)  # #c04040
        h.select([])
        h.set_tool("color_picker")
        palette = h.palette
        panel = h.tool_panel("color_picker")
        assert palette.tool_options.isVisible() and panel.isVisible()
        assert palette.tool_options_title.text() == "PICK A COLOR", palette.tool_options_title.text()
        h.mouse("move", h.vp(0, 0), buttons=Qt.NoButton)
        assert panel.live_label.text() == "#C04040"
        # The loupe draws magnified pixels of the image next to the cursor.
        image = h.grab()
        cursor = h.vp(0, 0)
        loupe_center = QPoint(cursor.x() + 22 + 11 * 9 // 2, cursor.y() + 22 + 11 * 9 // 2)
        magnified = image.pixelColor(loupe_center + QPoint(12, 12))
        assert magnified.name() == "#c04040", magnified.name()
        # Panning refreshes what the loupe samples.
        h.view.pan_by(QPointF(0, 300))
        QTest.qWait(80)
        h.mouse("move", h.vp(0, -400), buttons=Qt.NoButton)
        assert panel.live_label.text() != "#C04040"
    finally:
        h.close()


def test_pick_from_pen_returns_to_pen_with_the_color():
    h = make()
    try:
        h.add_image("Img", 200, -50, w=100, h=100)
        h.set_tool("pen")
        h.scribble(-200, 0)
        h.key(Qt.Key_I)
        assert h.tool() == "color_picker"
        h.click(h.vp(250, 0))
        assert h.tool() == "pen", "back to the tool you were using"
        brush = h.service("brush")
        assert brush.color.name() == "#c04040"
        assert h.tool_panel("pen").brush_row.hex_label.text() == "#C04040"
        assert h.view._toast is not None and "#C04040" in h.view._toast["text"]
        assert brush.recent[0] == "#C04040"
        h.scribble(-200, 60)
        assert len(h.nodes_of("annotation_drawing")) == 1, "picking mid-drawing keeps the same drawing"
        colors = sorted(tuple(h.attr(s, "color")) for s in h.nodes_of("annotation_path"))
        assert (192, 64, 64) in colors
    finally:
        h.close()


def test_pick_from_select_apply_to_selection_and_recent_colors():
    h = make()
    try:
        h.add_image("Img", 200, -50, w=100, h=100)
        h.create("annotation_rect", "Box", translate=[-200.0, 0.0], size=[80.0, 40.0])
        h.create("annotation_text", "Note", text="hi", translate=[-200.0, 80.0])
        h.select(["Box", "Note"])
        h.set_tool("color_picker")
        h.click(h.vp(250, 0))
        assert h.tool() == "color_picker", "from Select, stay in pick mode to see the result"
        panel = h.tool_panel("color_picker")
        assert panel.current_label.text().startswith("#C04040")
        assert panel.apply_btn.isVisible() and "(2)" in panel.apply_btn.text()
        QTest.mouseClick(panel.apply_btn, Qt.LeftButton)
        assert h.attr("Box", "stroke_color") == [192, 64, 64]
        assert h.attr("Note", "color") == [192, 64, 64]
        h.undo()
        assert h.attr("Box", "stroke_color") != [192, 64, 64]
        # Recent colours are remembered and reusable.
        brush = h.service("brush")
        brush.set_color(QColor("#123456"))
        swatches = [b for b in panel.recent_row.findChildren(QPushButton) if "#C04040" in b.toolTip()]
        assert swatches
        QTest.mouseClick(swatches[0], Qt.LeftButton)
        assert brush.color.name() == "#c04040" and QApplication.clipboard().text() == "#C04040"
        assert h.app.settings.value("plugins/brush/recent_colors")[0] == "#C04040"
    finally:
        h.app.settings.remove("plugins/brush/recent_colors")
        h.close()


def test_tool_options_follow_the_active_tool():
    h = make()
    try:
        palette = h.palette
        assert not palette.tool_options.isVisible(), "no brush clutter while selecting"
        h.set_tool("pen")
        pen = h.tool_panel("pen")
        assert palette.tool_options.isVisible() and pen.smooth_field.isVisible() and pen.arrows_row.isVisible()
        assert palette.tool_options_title.text() == "PEN OPTIONS", palette.tool_options_title.text()
        commit_spin(pen.smooth_field, 30.0)
        assert h.app.tools.tools["pen"].smoothing == 0.3 and pen.smooth_field.text() == "30 %"
        h.set_tool("rect")
        assert palette.tool_options.isVisible() and not pen.isVisible()
        h.set_tool("select")
        assert not palette.tool_options.isVisible()
    finally:
        h.close()
