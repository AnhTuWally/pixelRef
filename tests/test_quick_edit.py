"""The quick attribute editor: a bar next to the selection on the canvas."""

from __future__ import annotations

from harness import APP, QTest, make
from PySide6.QtCore import QSettings, Qt


def settle() -> None:
    QTest.qWait(5)
    APP.processEvents()


def fresh() -> object:
    QSettings().remove("plugins/quick_edit")
    return make()


def _controller_bar(h):
    from pixelref.builtins.quick_edit.ui import QuickBar

    bars = h.view.findChildren(QuickBar)
    return bars[0] if bars else None


def test_defaults_come_from_the_node_type():
    h = fresh()
    try:
        types = h.session.types
        assert types.quick_attrs("annotation_rect")[:2] == ("stroke_color", "thickness")
        assert types.quick_attrs("image") == ("opacity",), "inherited from canvas_node"
        assert h.run("quick_edit.fields", "annotation_text")[0] == "color"
    finally:
        h.close()


def test_bar_follows_selection_and_edits():
    h = fresh()
    try:
        h.run("rect.create", 0, 0, 120, 80, "Box")
        h.select(["Box"])
        settle()
        qb = _controller_bar(h)
        assert qb is not None and qb.isVisible()
        assert list(qb.editors)[:3] == ["stroke_color", "thickness", "stroke_style"]
        # below the selection
        bottom = h.view.mapFromScene(h.item("Box").sceneBoundingRect()).boundingRect().bottom()
        assert qb.y() > bottom, (qb.y(), bottom)
        thickness = qb.editors["thickness"]
        thickness.setValue(9.0)
        thickness.editingFinished.emit()
        assert h.attr("Box", "thickness") == 9.0
        h.undo()
        assert h.attr("Box", "thickness") != 9.0
        settle()
        assert qb.editors["thickness"].read() == h.attr("Box", "thickness"), "shows the document's value"
        h.select([])
        settle()
        assert not qb.isVisible()
    finally:
        h.close()


def test_multi_selection_sets_every_node_in_one_step():
    h = fresh()
    try:
        h.run("rect.create", 0, 0, 100, 60, "A")
        h.run("rect.create", 200, 0, 100, 60, "B")
        h.add_image("Img", 0, 200)
        h.select(["A", "B"])
        settle()
        qb = _controller_bar(h)
        combo = qb.editors["stroke_style"]
        combo.setCurrentIndex(combo.findData("dash"))
        assert h.attr("A", "stroke_style") == "dash" and h.attr("B", "stroke_style") == "dash"
        h.undo()
        assert h.attr("A", "stroke_style") == "solid" and h.attr("B", "stroke_style") == "solid"
        h.select(["A", "Img"])  # only fields both types show
        settle()
        assert not qb.isVisible()
    finally:
        h.close()


def test_user_override_reset_and_toggle():
    h = fresh()
    try:
        h.run("rect.create", 0, 0, 100, 60, "Box")
        h.select(["Box"])
        h.run("quick_edit.set_fields", "annotation_rect", ["corner_radius", "opacity"])
        settle()
        qb = _controller_bar(h)
        assert list(qb.editors) == ["corner_radius", "opacity"]
        assert h.service("quick_edit").is_customized("annotation_rect")
        try:
            h.run("quick_edit.set_fields", "annotation_rect", ["points"])
            raise AssertionError("unknown attribute accepted")
        except Exception as exc:  # noqa: BLE001
            assert "points" in str(exc)
        h.run("quick_edit.reset_fields", "annotation_rect")
        settle()
        assert list(qb.editors)[0] == "stroke_color"
        h.run("quick_edit.toggle", False)
        settle()
        assert not qb.isVisible() and not h.app.actions.get("quick_edit.toggle").isChecked()
        h.run("quick_edit.toggle")
        settle()
        assert _controller_bar(h).isVisible()
    finally:
        h.close()


def test_hidden_while_dragging_the_selection():
    h = fresh()
    try:
        h.add_image("Img", 0, 0, w=200, h=100)
        h.select(["Img"])
        settle()
        qb = _controller_bar(h)
        assert qb.isVisible()
        start = h.vp(100, 50)
        h.mouse("press", start)
        h.mouse("move", h.vp(140, 60), buttons=Qt.LeftButton)
        h.mouse("move", h.vp(160, 70), buttons=Qt.LeftButton)
        settle()
        if APP.mouseButtons() != Qt.NoButton:  # offscreen may not track buttons
            assert not qb.isVisible()
        h.mouse("release", h.vp(160, 70))
        settle()
        assert qb.isVisible()
        bottom = h.view.mapFromScene(h.item("Img").sceneBoundingRect()).boundingRect().bottom()
        assert qb.y() > bottom, "follows the moved selection"
    finally:
        h.close()


def test_first_show_is_laid_out_at_full_size():
    from PySide6.QtWidgets import QStyleFactory, QWidget

    # The desktop style measures unstyled widgets differently from styled ones (the bar
    # came up squeezed on the first selection); Fusion hides that, so use it when available.
    old_style = APP.style().name()
    native = next((k for k in QStyleFactory.keys() if k.lower() in ("windows11", "windowsvista", "macos")), None)
    if native:
        APP.setStyle(native)
    h = fresh()
    try:
        h.create("annotation_text", "Note", text="Hi")
        h.select(["Note"])  # the very first selection builds and shows the bar
        settle()
        qb = _controller_bar(h)
        assert qb.isVisible()
        assert qb.width() >= qb.sizeHint().width(), (qb.width(), qb.sizeHint().width())
        for child in qb.fields_box.findChildren(QWidget):
            if child.isVisible() and child.minimumWidth() != child.maximumWidth():
                assert child.width() >= child.minimumSizeHint().width(), (child, child.width())
        center = h.view.mapFromScene(h.item("Note").sceneBoundingRect()).boundingRect().center().x()
        area = h.view.viewport().geometry()
        assert abs(qb.geometry().center().x() - (center + area.left())) <= 2, "centred on the selection"
        # A field that grows after the bar is shown (e.g. styling or fonts arriving late) grows the bar.
        before = qb.width()
        qb.editors["bold"].setText("Bold (a longer caption)")
        settle()
        settle()
        assert qb.width() > before + 60, (before, qb.width())
        assert abs(qb.geometry().center().x() - (center + area.left())) <= 2, "still centred"
    finally:
        h.close()
        APP.setStyle(old_style)
