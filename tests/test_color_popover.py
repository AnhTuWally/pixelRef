"""The colour popover plugin and the pluggable colour picker (extension point ``color.pickers``)."""

from __future__ import annotations

import math

from harness import APP, Harness
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QColorDialog

from pixelref.builtins.color_popover.popover import ColorPopover, parse_color
from pixelref.plugin_api.contracts import ColorPickerProvider
from pixelref.ui import color_dialog


def make():
    return Harness()


def click_square(dialog, sat, val):
    """Press inside the wheel's square at saturation ``sat`` / brightness ``val``."""
    square = dialog.wheel.square()
    point = QPointF(square.left() + sat * square.width(), square.bottom() - val * square.height()).toPoint()
    QTest.mouseClick(dialog.wheel, Qt.LeftButton, Qt.NoModifier, point)


def click_ring(dialog, hue):
    """Press on the hue ring at ``hue`` (0..1, anticlockwise from 3 o'clock)."""
    wheel = dialog.wheel
    angle = hue * 2 * math.pi
    mid = (wheel.outer() + wheel.inner()) / 2
    centre = wheel.centre()
    point = QPointF(centre.x() + mid * math.cos(angle), centre.y() - mid * math.sin(angle)).toPoint()
    QTest.mouseClick(wheel, Qt.LeftButton, Qt.NoModifier, point)


def test_parse_color():
    assert parse_color("#f80").name() == "#ff8800"
    assert parse_color("e04040").name() == "#e04040"
    c = parse_color("#11223380")
    assert c.name() == "#112233" and c.alpha() == 0x80
    assert parse_color("rgb(1, 2, 3)").name() == "#010203"
    assert parse_color("rgba(1, 2, 3, 0.5)").alpha() == 128
    assert parse_color("rgb(300, 0, 0)") is None and parse_color("nope") is None and parse_color("#12") is None


def test_popover_is_the_default_and_qt_is_the_fallback():
    h = make()
    try:
        ids = [p.id for p in color_dialog.providers()]
        assert ids[0] == "popover" and ids[-1] == "qt", ids
        assert color_dialog.active().id == "popover"
        dialog = color_dialog.create(None, QColor(10, 20, 30), "Fill")
        assert isinstance(dialog, ColorPopover)
        dialog.deleteLater()
        h.run("color_popover.use_picker", "qt")
        assert isinstance(color_dialog.create(None, QColor(10, 20, 30), "Fill"), QColorDialog)
        h.run("color_popover.use_picker", "default")
        assert color_dialog.active().id == "popover"
    finally:
        h.app.settings.remove(color_dialog.SETTING)
        h.close()


def test_a_failing_or_missing_picker_falls_back():
    h = make()
    try:
        def broken(*_args):
            raise RuntimeError("boom")

        h.app.session.extensions.contribute("color.pickers", ColorPickerProvider("broken", "Broken", broken, order=-5))
        color_dialog.set_preferred("broken")
        assert color_dialog.active().id == "broken"
        dialog = color_dialog.create(None, QColor(1, 2, 3), "Color")
        assert isinstance(dialog, QColorDialog), "a broken third-party picker falls back to Qt"
        color_dialog.set_preferred("not-installed")
        assert color_dialog.active().id == "broken", "an unknown choice means the default (first by order)"
    finally:
        h.app.settings.remove(color_dialog.SETTING)
        h.close()


def test_popover_changes_are_live_and_cancel_restores():
    h = make()
    try:
        h.create("annotation_rect", "Box", translate=[0.0, 0.0], size=[80.0, 40.0], fill_opacity=0.5,
                 stroke_color=[0, 120, 255])
        h.select(["Box"])
        section = h.section("stroke")
        original = h.attrs("Box")["fill_color"]
        seen = []

        def drive(accept):
            dialog = next(w for w in APP.topLevelWidgets() if isinstance(w, ColorPopover) and w.isVisible())
            for color in (QColor(255, 0, 0), QColor(0, 255, 0)):
                dialog.set_color(color)
                APP.processEvents()
                seen.append(h.attrs("Box")["fill_color"])
            dialog.hex.setText("#0000ff")
            dialog.hex.textEdited.emit("#0000ff")
            seen.append(h.attrs("Box")["fill_color"])
            dialog.accept() if accept else dialog.reject()

        QTimer.singleShot(50, lambda: drive(False))
        section._pick_fill()
        assert seen == [[255, 0, 0], [0, 255, 0], [0, 0, 255]], seen
        assert h.attrs("Box")["fill_color"] == original, "Esc puts the colour back"

        seen.clear()
        QTimer.singleShot(50, lambda: drive(True))
        section._pick_fill()
        assert h.attrs("Box")["fill_color"] == [0, 0, 255]
        h.undo()
        assert h.attrs("Box")["fill_color"] == original, "one dialog is one undo step"
    finally:
        h.app.settings.remove(color_dialog.SETTING)
        h.close()


def test_popover_controls_agree():
    dialog = ColorPopover(None, QColor(200, 100, 50, 128), "Color", True, recent=["#112233"], scene=[QColor(9, 9, 9)])
    try:
        assert dialog.currentColor().name() == "#c86432" and dialog.currentColor().alpha() == 128
        assert dialog.hex.text() == "#C8643280" and [s.value() for s in dialog.spins["rgb"]] == [200, 100, 50]
        heard = []
        dialog.currentColorChanged.connect(heard.append)
        click_square(dialog, 0.0, 0.0)  # black
        assert heard[-1].name() == "#000000" and dialog.currentColor().alpha() == 128
        click_square(dialog, 1.0, 1.0)  # the pure hue
        hue = dialog.currentColor()
        assert hue.saturation() == 255 and hue.value() == 255
        dialog.alpha.moved_to(dialog.alpha.rect().topLeft().toPointF())
        assert dialog.currentColor().alpha() == 0
        dialog.spins["rgb"][0].setValue(10)
        assert dialog.currentColor().red() == 10
        dialog.spins["hsv"][0].setValue(120)  # the codes agree with each other
        assert dialog.currentColor().hue() in (119, 120) and dialog.spins["hsl"][0].value() == 120
        dialog.spins["hsl"][2].setValue(50)
        assert dialog.spins["hsl"][2].value() == 50 and dialog.currentColor().lightness() in (127, 128)
        dialog.alpha_spin.setValue(25)
        assert dialog.currentColor().alpha() == round(0.25 * 255)
        dialog.compare.revert.emit()
        assert dialog.currentColor() == QColor(200, 100, 50, 128)
        # Without alpha, colours stay opaque whatever is typed.
        plain = ColorPopover(None, QColor(1, 2, 3, 40), "Color", False)
        assert plain.currentColor().alpha() == 255
        plain.hex.textEdited.emit("#11223344")
        assert plain.currentColor().alpha() == 255
        plain.deleteLater()
    finally:
        dialog.deleteLater()


def test_greys_keep_their_hue():
    dialog = ColorPopover(None, QColor(255, 0, 0), "Color")
    try:
        click_square(dialog, 0.0, 0.0)
        click_square(dialog, 1.0, 1.0)
        assert dialog.currentColor().name() == "#ff0000", "dragging back out of black returns the hue"
    finally:
        dialog.deleteLater()


def test_scene_colors_and_recent_swatches():
    h = make()
    try:
        h.create("annotation_rect", "A", translate=[0.0, 0.0], size=[10.0, 10.0], stroke_color=[1, 2, 3])
        h.create("annotation_rect", "B", translate=[50.0, 0.0], size=[10.0, 10.0], stroke_color=[1, 2, 3])
        from pixelref.builtins.color_popover.ui import scene_colors

        from types import SimpleNamespace

        colors = scene_colors(SimpleNamespace(session=h.app.session))
        assert colors and colors[0].name() == "#010203", [c.name() for c in colors]
    finally:
        h.close()


def test_wheel_ring_picks_the_hue_and_square_the_shade():
    dialog = ColorPopover(None, QColor(255, 0, 0), "Color")
    try:
        heard = []
        dialog.currentColorChanged.connect(heard.append)
        click_ring(dialog, 1 / 3)  # green
        assert abs(dialog.spins["hsv"][0].value() - 120) <= 1 and heard[-1].green() > 250 and heard[-1].red() < 5
        click_square(dialog, 0.5, 0.5)
        shade = dialog.currentColor()
        assert abs(shade.hue() - 120) <= 1 and 120 <= shade.saturation() <= 135 and 120 <= shade.value() <= 135
        click_ring(dialog, 2 / 3)  # blue: the shade stays, the hue moves
        assert abs(dialog.currentColor().hue() - 240) <= 1 and 120 <= dialog.currentColor().value() <= 135
        # A press in the empty corner outside the ring does nothing.
        count = len(heard)
        QTest.mouseClick(dialog.wheel, Qt.LeftButton, Qt.NoModifier, QPoint(3, 3))  # (0, 0) would mean the centre
        assert len(heard) == count
    finally:
        dialog.deleteLater()
