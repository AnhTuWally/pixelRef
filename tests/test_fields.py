"""Draggable number fields, editors generated from attribute specs, live edits,
tool options with your defaults, and saved defaults for effects and text notes."""

from __future__ import annotations

from harness import APP, Harness
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication


def make():
    return Harness()


def drag(widget, xs, modifiers=Qt.NoModifier, release=True, y=4):
    """Press at xs[0], move through the rest (widget-local x), release at the last one."""
    target = widget.lineEdit() if hasattr(widget, "lineEdit") else widget

    # Like a real mouse, positions are fixed on screen from the press on (the panel may re-layout).
    target._drag_origin = QPointF(target.mapToGlobal(QPoint(0, 0)))

    def send(kind, x, button, buttons):
        local = QPointF(x, y)
        glob = target._drag_origin + local
        QApplication.sendEvent(target, QMouseEvent(kind, local, glob, button, buttons, modifiers))

    send(QEvent.MouseButtonPress, xs[0], Qt.LeftButton, Qt.LeftButton)
    for x in xs[1:]:
        send(QEvent.MouseMove, x, Qt.NoButton, Qt.LeftButton)
    if release:
        send(QEvent.MouseButtonRelease, xs[-1], Qt.LeftButton, Qt.NoButton)
    APP.processEvents()


def move(widget, x, y=4):
    target = widget.lineEdit() if hasattr(widget, "lineEdit") else widget
    glob = target._drag_origin + QPointF(x, y)
    QApplication.sendEvent(target, QMouseEvent(QEvent.MouseMove, QPointF(x, y), glob, Qt.NoButton, Qt.LeftButton,
                                                Qt.NoModifier))
    APP.processEvents()  # the canvas and the inspector refresh between moves, like on screen


def release(widget, x, y=4):
    target = widget.lineEdit() if hasattr(widget, "lineEdit") else widget
    glob = target._drag_origin + QPointF(x, y)
    QApplication.sendEvent(target, QMouseEvent(QEvent.MouseButtonRelease, QPointF(x, y), glob, Qt.LeftButton,
                                                Qt.NoButton, Qt.NoModifier))
    APP.processEvents()


def press_escape(widget):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    APP.processEvents()


def commit(editor, value):
    editor.setValue(value)
    editor.editingFinished.emit()
    APP.processEvents()


# ---- Attribute specs ----------------------------------------------------------------------------------


def test_editing_hints_round_trip_through_dynamic_attribute_specs():
    from pixelref.plugin_api import Attr

    attr = Attr("blur", "float", 2.0, minimum=0.0, maximum=500.0, soft_min=0.0, soft_max=60.0, step=0.5,
                precision=1, unit="px", display_scale=1.0)
    back = Attr.from_dict("blur", attr.to_dict())
    assert (back.soft_min, back.soft_max, back.step, back.precision, back.unit) == (0.0, 60.0, 0.5, 1, "px")
    opacity = Attr.from_dict("o", {"type": "float", "display_scale": 100, "unit": "%", "precision": "junk"})
    assert opacity.display_scale == 100 and opacity.unit == "%" and opacity.precision is None
    assert "soft_min" not in Attr("plain", "float", 0.0).to_dict(), "files without hints don't change"


# ---- The number field ---------------------------------------------------------------------------------


def test_number_field_drag_speed_soft_range_vs_fixed_step():
    from pixelref.plugin_api import Attr
    from pixelref.ui.fields import NumberField, editor_for

    # A soft range is covered in ~300 px, whatever its size.
    radius = editor_for(Attr("radius", "float", 4.0, minimum=0.0, maximum=500.0, soft_min=0.0, soft_max=60.0))
    assert abs(radius.per_pixel() - 0.2) < 1e-9
    level = editor_for(Attr("level", "float", 0.5, minimum=0.0, maximum=1.0))  # hard range acts as soft
    assert abs(level.per_pixel() - 1 / 300) < 1e-9
    # Without one: a fixed step per pixel.
    free = NumberField(scrub_step=1.0)
    assert free.per_pixel() == 1.0
    # Display scale: opacity 0-1 shown as 0-100 %.
    opacity = editor_for(Attr("o", "float", 1.0, minimum=0.0, maximum=1.0, display_scale=100.0, unit="%"))
    assert opacity.value() == 100.0 and opacity.read() == 1.0 and opacity.suffix() == " %"
    opacity.write(0.25)
    assert opacity.value() == 25.0
    integer = editor_for(Attr("levels", "int", 4, minimum=2, maximum=32))
    assert integer.decimals() == 0 and isinstance(integer.read(), int)


def test_number_field_drag_modifiers_limits_and_click_to_type():
    from pixelref.ui.fields import NumberField

    field = NumberField(soft_range=(0.0, 300.0), step=10.0, default=100.0)  # 1 per pixel
    field.resize(80, 24)
    field.show()
    previews, commits, finished = [], [], []
    field.previewed.connect(previews.append)
    field.committed.connect(commits.append)
    field.scrub_finished.connect(finished.append)
    try:
        drag(field, [10, 11])  # jitter: a click, not a drag
        assert field.value() == 100.0 and not commits and field.hasFocus(), "a click lets you type"
        field.clearFocus()
        drag(field, [10, 30, 50])
        assert field.value() == 140.0 and previews and commits == [140.0] and finished == [False]
        drag(field, [10, 20], modifiers=Qt.ShiftModifier)  # 10x faster
        assert field.value() == 240.0, field.value()
        drag(field, [10, 30], modifiers=Qt.AltModifier)  # 10x finer
        assert abs(field.value() - 242.0) < 1e-6, field.value()
        drag(field, [10, 400])  # a drag stops at the soft range
        assert field.value() == 300.0
        field.setValue(100.0)
        drag(field, [10, 24], modifiers=Qt.ControlModifier)  # 114 snaps to the step
        assert field.value() == 110.0, field.value()
    finally:
        field.close()


def test_number_field_escape_puts_the_value_back():
    from pixelref.ui.fields import NumberField

    field = NumberField(scrub_step=1.0, default=5.0)
    field.show()
    commits, finished = [], []
    field.committed.connect(commits.append)
    field.scrub_finished.connect(finished.append)
    try:
        drag(field, [10, 40], release=False)
        assert field.value() == 35.0 and field.scrubbing
        press_escape(field)
        assert field.value() == 5.0 and finished == [True] and not commits and not field.scrubbing
        assert QApplication.overrideCursor() is None, "the cursor comes back"
    finally:
        field.close()


# ---- Live edits in the Attribute Editor -----------------------------------------------------------------


def test_dragging_an_effect_parameter_previews_live_as_one_undo_step():
    h = make()
    try:
        h.add_image("Img", 0, 0, w=60, h=40)
        h.run("effects.add", "blur", "Img")
        h.select(["Img"])
        row = next(iter(h.section("effects").rows.values()))
        field = row.editors["radius"][1]
        depth = len(h.session.undo.stack.labels())
        drag(field, [10, 30, 60], release=False)  # 0.2 px per pixel of drag: +10
        assert abs(h.attr("Img", "effects")[0]["params"]["radius"] - 14.0) < 1e-6, "live while dragging"
        move(field, 85)  # the drag outlives the refreshes in between
        assert abs(h.attr("Img", "effects")[0]["params"]["radius"] - 19.0) < 1e-6
        release(field, 85)
        field = next(iter(h.section("effects").rows.values())).editors["radius"][1]
        assert len(h.session.undo.stack.labels()) == depth + 1, h.session.undo.stack.labels()
        assert h.undo_label() == "Edit blur"
        h.undo()
        assert h.attr("Img", "effects")[0]["params"].get("radius", 4.0) == 4.0
        # Esc while dragging: back where it was, nothing to undo.
        depth = len(h.session.undo.stack.labels())
        field = next(iter(h.section("effects").rows.values())).editors["radius"][1]
        drag(field, [10, 60], release=False)
        assert h.attr("Img", "effects")[0]["params"]["radius"] != 4.0
        press_escape(field)
        assert h.attr("Img", "effects")[0]["params"].get("radius", 4.0) == 4.0
        assert len(h.session.undo.stack.labels()) == depth
        assert h.sel() == ["Img"], "Esc cancels the drag, it doesn't deselect"
    finally:
        h.close()


def test_generated_editors_drag_live_and_offer_defaults_for_new_nodes():
    h = make()
    try:
        h.create("annotation_rect", "Box", translate=[0.0, 0.0], size=[80.0, 40.0])
        h.app.tools.get("rect").options.clear_defaults()
        h.run("add_attr", "Box", __import__("pixelref.plugin_api", fromlist=["Attr"]).Attr(
            "rating", "int", 3, minimum=0, maximum=5))
        h.select(["Box"])
        extra = h.section("extra_attributes")
        _spec, rating = extra._editors["rating"]
        drag(rating, [10, 70])  # 0-5 over 300 px: +1
        assert h.attr("Box", "rating") == 4
        entries = dict(extra._menu(_spec))
        assert "Reset to default" in entries
        entries["Reset to default"]()
        assert h.attr("Box", "rating") == 3
    finally:
        h.close()


def test_transform_and_opacity_preview_while_dragging():
    h = make()
    try:
        h.add_image("Img", 0, 0, w=200, h=100)
        h.select(["Img"])
        xf = h.section("transform")
        depth = len(h.session.undo.stack.labels())
        drag(xf.pos_x_spin, [10, 30, 50], release=False)
        assert abs(h.attr("Img", "translate")[0] - 40.0) < 1.0, "moves while dragging"
        release(xf.pos_x_spin, 50)
        assert len(h.session.undo.stack.labels()) == depth + 1
        appearance = h.section("appearance")
        drag(appearance.opacity_spin, [10, 10 - 60])  # 0-100 % over 300 px: -20 %
        assert abs(h.attr("Img", "opacity") - 0.8) < 0.01, h.attr("Img", "opacity")
        assert appearance.opacity_slider.value() == 80
    finally:
        h.close()


# ---- Tool options --------------------------------------------------------------------------------------


def test_each_drawing_tool_keeps_its_own_color_and_width():
    h = make()
    try:
        brush = h.service("brush")
        h.set_tool("pen")
        brush.set_color(QColor(10, 200, 30))
        brush.set_width(9.0)
        h.set_tool("rect")
        assert brush.color.name() != QColor(10, 200, 30).name(), "the rect keeps its own colour"
        h.drag(h.vp(0, 0), h.vp(80, 60))
        box = h.sel()[0]
        assert h.attr(box, "stroke_color") == [255, 64, 64] and h.attr(box, "thickness") == 4.0
        h.set_tool("pen")
        assert brush.color.name() == QColor(10, 200, 30).name() and brush.width == 9.0
        h.scribble(-200, 0)
        stroke = h.nodes_of("annotation_path")[0]
        assert h.attr(stroke, "color") == [10, 200, 30] and h.attr(stroke, "thickness") == 9.0
        h.app.tools.get("pen").options.reset(factory=True)
        assert h.tool_panel("pen").brush_row.hex_label.text() == "#FF4040"
    finally:
        h.close()


def test_tool_options_draw_with_them_and_save_as_your_default():
    h = make()
    try:
        rect = h.app.tools.get("rect")
        rect.options.clear_defaults()
        h.set_tool("rect")
        panel = h.tool_panel("rect")
        assert {"stroke_style", "fill_color", "fill_opacity", "fill_style", "corner_radius"} <= set(panel.form.editors)
        commit(panel.form.editors["corner_radius"], 12.0)
        commit(panel.form.editors["fill_opacity"], 50.0)  # shown in %
        commit(panel.brush_row.width_spin, 7.0)
        assert rect.options.get("fill_opacity") == 0.5
        h.drag(h.vp(0, 0), h.vp(80, 60))
        attrs = h.attrs(h.sel()[0])
        assert attrs["corner_radius"] == 12.0 and attrs["fill_opacity"] == 0.5 and attrs["thickness"] == 7.0
        # Save (the palette's ★), change, reset to your default, then to factory.
        h.set_tool("rect")
        palette = h.palette
        assert palette.save_defaults_btn.isVisible() and palette.reset_btn.isVisible()
        palette.save_defaults()
        assert h.app.settings.json_value("tools/rect/defaults", {})["corner_radius"] == 12.0
        commit(panel.form.editors["corner_radius"], 30.0)
        palette.reset_options(factory=False)
        assert rect.options.get("corner_radius") == 12.0 and panel.form.editors["corner_radius"].value() == 12.0
        # Kept across restarts.
        h2 = make()
        try:
            assert h2.app.tools.get("rect").options.get("corner_radius") == 12.0
        finally:
            h2.close()
        palette.reset_options(factory=True)
        assert rect.options.get("corner_radius") == 0.0
        palette.forget_defaults()
        assert h.run("tools.option_defaults", "rect")["user"] == {}
    finally:
        h.app.settings.remove("tools")
        h.close()


def test_tool_option_commands_and_per_field_default():
    from pixelref.core.errors import CommandError

    h = make()
    try:
        h.run("tools.set_options", "arrow", stroke_style="dash", arrow_start=1)
        assert h.run("tools.options", "arrow")["stroke_style"] == "dash"
        try:
            h.run("tools.set_option", "arrow", "nope", 1)
            raise AssertionError("unknown option accepted")
        except CommandError:
            pass
        try:
            h.run("tools.set_option", "arrow", "stroke_style", "wavy")
            raise AssertionError("bad value accepted")
        except CommandError:
            pass
        h.set_tool("arrow")
        panel = h.tool_panel("arrow")
        assert panel.arrow_start_btn.isChecked(), "buttons follow the options"
        entries = dict(panel.form.editors["stroke_style"].context_provider())
        entries["Set as my default"]()
        assert h.run("tools.option_defaults", "arrow")["user"] == {"stroke_style": "dash"}, "only that field"
        h.drag(h.vp(0, 0), h.vp(100, 0))
        attrs = h.attrs(h.sel()[0])
        assert attrs["stroke_style"] == "dash" and attrs["arrow_start"] == 1
    finally:
        h.app.settings.remove("tools")
        h.close()


def test_use_a_nodes_style_as_the_default_for_new_ones():
    h = make()
    try:
        h.app.tools.get("rect").options.clear_defaults()
        h.create("annotation_rect", "Box", translate=[0.0, 0.0], size=[80.0, 40.0], stroke_color=[0, 120, 255],
                 thickness=6.0, corner_radius=9.0, fill_opacity=0.3)
        h.select(["Box"])
        section = h.section("stroke")
        assert section.default_btn.isVisible()
        section.default_btn.click()
        rect = h.app.tools.get("rect").options
        assert rect.get("stroke_color") == [0, 120, 255] and rect.get("corner_radius") == 9.0
        assert h.app.settings.json_value("tools/rect/defaults", {})["thickness"] == 6.0
        # A drawing: its strokes' style goes to the pen.
        h.set_tool("pen")
        h.scribble(0, 100)
        h.set_tool("select")
        drawing = h.nodes_of("annotation_drawing")[0]
        h.run("brush.restyle", drawing, {"color": [1, 2, 3], "thickness": 11.0})
        h.run("tools.save_defaults_from", drawing)
        assert h.app.tools.get("pen").options.get("thickness") == 11.0
    finally:
        h.app.settings.remove("tools")
        h.close()


# ---- Effects and text notes ------------------------------------------------------------------------------


def test_effect_defaults_are_written_into_new_effects():
    h = make()
    try:
        h.add_image("A", 0, 0, w=40, h=40)
        h.add_image("B", 100, 0, w=40, h=40)
        fx, = h.run("effects.add", "blur", "A", radius=20.0, mix=0.5)
        saved = h.run("effects.save_default", "A", fx)
        assert saved == {"params": {"radius": 20.0}, "mix": 0.5}
        h.run("effects.add", "blur", "B")
        entry = h.attr("B", "effects")[0]
        assert entry["params"] == {"radius": 20.0} and entry["mix"] == 0.5, "stored in the scene, not looked up"
        h.run("effects.set", "B", entry["id"], radius=3.0)
        h.run("effects.reset", "B", entry["id"])
        assert h.attr("B", "effects")[0]["params"] == {"radius": 20.0}, "reset: to your default"
        h.run("effects.reset", "B", entry["id"], factory=True)
        assert h.attr("B", "effects")[0]["params"] == {} and h.attr("B", "effects")[0]["mix"] == 1.0
        h.run("effects.clear_default", "blur")
        assert h.run("effects.defaults", "blur")["user"] == {"params": {}, "mix": 1.0}
        assert h.attr("A", "effects")[0]["params"] == {"radius": 20.0}, "existing effects keep their values"
        # The row's menu does the same.
        h.select(["A"])
        row = next(iter(h.section("effects").rows.values()))
        labels = [text for text, _cb in row.menu_entries() if text]
        assert labels[0] == "Save as my default for new Blur" and "Reset to factory defaults" in labels
        row.save_one("radius")
        assert h.run("effects.defaults", "blur")["user"]["params"] == {"radius": 20.0}
    finally:
        h.app.settings.remove("plugins/effects/defaults")
        h.close()


def test_text_notes_use_your_default_style():
    h = make()
    try:
        h.create("annotation_text", "Styled", text="hi", translate=[0.0, 0.0], font_size=30.0, bold=True,
                 color=[10, 20, 30])
        h.select(["Styled"])
        h.section("text").default_style_btn.click()
        assert h.run("text.default_style")["font_size"] == 30.0
        note = h.run("text.create", "new", [0.0, 100.0])
        attrs = h.attrs(note)
        assert attrs["font_size"] == 30.0 and attrs["bold"] is True and attrs["color"] == [10, 20, 30]
        assert attrs["text"] == "new", "the style, not the text"
        h.run("text.clear_default_style")
        plain = h.run("text.create", "plain", [0.0, 200.0])
        assert h.attr(plain, "font_size") == 14.0 and h.attr(plain, "color") == [220, 220, 220]
    finally:
        h.app.settings.remove("plugins/text/default_style")
        h.close()


def test_number_field_with_real_mouse_input_drags_even_when_focused():
    """Through the window system (QTest), Qt focuses a widget before it gets the press:
    a focused field must still drag; a click types; Enter / Esc go back to dragging."""
    from PySide6.QtTest import QTest

    from pixelref.ui.fields import NumberField

    field = NumberField(scrub_step=1.0, default=5.0)
    field.resize(100, 24)
    field.show()
    QTest.qWaitForWindowExposed(field)
    edit = field.lineEdit()

    def real_drag():
        QTest.mousePress(edit, Qt.LeftButton, Qt.NoModifier, QPoint(10, 10))
        for x in range(15, 60, 5):
            QTest.mouseMove(edit, QPoint(x, 10))
        QTest.mouseRelease(edit, Qt.LeftButton, Qt.NoModifier, QPoint(55, 10))

    try:
        field.setFocus()
        assert edit.cursor().shape() == Qt.SizeHorCursor
        real_drag()
        assert field.value() == 50.0 and not edit.selectedText(), "dragged, not text selection"
        QTest.mouseClick(edit, Qt.LeftButton, Qt.NoModifier, QPoint(10, 10))
        assert field.typing and edit.cursor().shape() == Qt.IBeamCursor and edit.selectedText()
        QTest.keyClicks(edit, "12")
        QTest.keyClick(edit, Qt.Key_Return)
        assert field.value() == 12.0 and not field.typing and edit.cursor().shape() == Qt.SizeHorCursor
        real_drag()
        assert field.value() == 57.0, "drags again after Enter"
        QTest.mouseClick(edit, Qt.LeftButton, Qt.NoModifier, QPoint(10, 10))
        QTest.keyClicks(edit, "99")
        QTest.keyClick(edit, Qt.Key_Escape)
        assert field.value() == 57.0 and not field.typing, "Esc drops what was typed"
    finally:
        field.close()
