"""Blur and pixelate regions, and numbered step markers."""

from __future__ import annotations

from harness import APP, approx, make
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter


def _striped_image(h, name: str, x: float, y: float) -> str:
    """A 200×100 image of 10px blue/white stripes (blurring or pixelating it shows)."""
    image = QImage(200, 100, QImage.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    for i in range(0, 200, 20):
        painter.fillRect(i, 0, 10, 100, QColor("#2060d0"))
    painter.end()
    path = h.work / f"{name}.png"
    image.save(str(path))
    return h.create("image", name, path=path.as_posix(), translate=[x, y])


def _render(h, names: list[str]) -> QImage:
    return h.app.canvas.render_items([h.item(n) for n in names])


def test_blur_and_pixelate_tools_draw_regions():
    h = make()
    try:
        h.set_tool("blur")
        h.drag(h.vp(0, 0), h.vp(100, 50))
        assert h.nodes_of("annotation_blur") and h.tool() == "blur"
        blur = h.sel()[0]
        assert h.attr(blur, "size") == [100.0, 50.0] and h.attr(blur, "radius") == 12.0
        h.set_tool("pixelate")
        h.drag(h.vp(0, 100), h.vp(80, 160), modifiers=Qt.ShiftModifier)  # Shift: square
        pixelate = h.sel()[0]
        assert h.type_of(pixelate) == "annotation_pixelate"
        assert h.attr(pixelate, "size") == [80.0, 80.0]
        # The corner points reshape the region.
        h.drag(h.gizmo()["handles_vp"]["br"].toPoint(), h.vp(120, 200))
        assert h.attr(pixelate, "size") == [120.0, 100.0]
        assert len(h.nodes_of("annotation_pixelate")) == 1
        h.undo()
        assert h.attr(pixelate, "size") == [80.0, 80.0]
    finally:
        h.close()


def test_pixelate_shows_blocks_of_what_is_below():
    h = make()
    try:
        _striped_image(h, "Img", 0, 0)
        region = h.run("redact.pixelate", 0, 0, 100, 100, cell_size=20)
        h.refresh()
        image = _render(h, ["Img", region])
        # Each 20px block is one colour: the average of a blue and a white stripe.
        a, b = image.pixelColor(2, 2), image.pixelColor(17, 17)
        assert a == b, (a.name(), b.name())
        assert 0x70 < a.red() < 0xA0, a.name()
        # Outside the region the stripes are untouched.
        assert image.pixelColor(122, 50).name() == "#2060d0" and image.pixelColor(132, 50).name() == "#ffffff"
        # Moving the image under it changes what it shows.
        h.run("set_attr", "Img", "translate", [10.0, 0.0])
        h.refresh()
        APP.processEvents()
        moved = _render(h, ["Img", region])
        assert moved.pixelColor(12, 2) != image.pixelColor(12, 2) or moved.pixelColor(32, 2) != image.pixelColor(32, 2)
    finally:
        h.close()


def test_blur_softens_what_is_below_and_nothing_else():
    h = make()
    try:
        _striped_image(h, "Img", 0, 0)
        region = h.run("redact.blur", 0, 0, 100, 100, radius=10)
        h.refresh()
        image = _render(h, ["Img", region])
        # Stripes inside become a soft mix; none of them stay pure blue or white.
        middle = [image.pixelColor(x, 50) for x in range(20, 80)]
        assert all(c.name() not in ("#2060d0", "#ffffff") for c in middle)
        assert image.pixelColor(122, 50).name() == "#2060d0"
        # A region over nothing renders nothing (and doesn't fail).
        empty = h.run("redact.blur", 500, 500, 50, 50)
        h.refresh()
        assert _render(h, [empty]) is not None
    finally:
        h.close()


def test_regions_above_and_below_each_other_settle():
    h = make()
    try:
        _striped_image(h, "Img", 0, 0)
        low = h.run("redact.blur", 0, 0, 120, 80)
        high = h.run("redact.pixelate", 60, 20, 120, 80)
        h.refresh()
        for _ in range(20):  # overlapping regions must not keep repainting each other
            APP.processEvents()
        before = (h.item(low).revision, h.item(high).revision)
        for _ in range(20):
            APP.processEvents()
        assert (h.item(low).revision, h.item(high).revision) == before
        # The upper one sees the lower one's result.
        assert h.item(low) in h.item(high).find_below()
        assert h.item(high) not in h.item(low).find_below()
    finally:
        h.close()


def test_steps_count_up_and_renumber():
    h = make()
    try:
        h.set_tool("step")
        for x in (0, 60, 120):
            h.click(h.vp(x, 0))
        steps = h.nodes_of("annotation_step")
        assert [h.attr(n, "number") for n in steps] == [1, 2, 3] and h.tool() == "step"
        assert h.attr(steps[0], "translate") == [0.0, 0.0]
        # A drag places the marker where it ends, pointing back to where it began.
        h.drag(h.vp(200, 0), h.vp(260, 80))
        tailed = h.sel()[0]
        assert h.attr(tailed, "number") == 4
        tail = h.attr(tailed, "tail")
        assert approx(tail[0], -60, 1) and approx(tail[1], -80, 1)
        # Its tail point can be moved; dropping it on the marker removes the tail.
        h.drag(h.gizmo()["handles_vp"]["tail"].toPoint(), h.vp(200, 120))
        tail = h.attr(tailed, "tail")
        assert approx(tail[0], -60, 1) and approx(tail[1], 40, 1)
        h.drag(h.gizmo()["handles_vp"]["tail"].toPoint(), h.vp(262, 82))
        assert h.attr(tailed, "tail") == [0.0, 0.0]
        # Without a pointer the handle sits on the edge: pulling it out adds one (like ShareX).
        g = h.gizmo()
        assert list(g["handles_vp"]) == ["tail"], "steps have no scale handles"
        h.drag(g["handles_vp"]["tail"].toPoint(), h.vp(360, 120))
        tail = h.attr(tailed, "tail")
        assert approx(tail[0], 100, 1) and approx(tail[1], 40, 1)
        h.undo()
        assert h.attr(tailed, "tail") == [0.0, 0.0]
        # Deleting one leaves a gap; renumbering closes it.
        h.run("delete", [steps[1]])
        assert h.run("steps.next_number") == 5
        h.run("steps.renumber")
        assert sorted(h.attr(n, "number") for n in h.nodes_of("annotation_step")) == [1, 2, 3]
        assert h.run("steps.next_number") == 4
    finally:
        h.close()


def test_step_labels():
    from pixelref.builtins.steps import label_for

    assert [label_for(n, "number") for n in (1, 12)] == ["1", "12"]
    assert [label_for(n, "upper") for n in (1, 26, 27, 52)] == ["A", "Z", "AA", "AZ"]
    assert label_for(3, "lower") == "c"
    assert [label_for(n, "roman") for n in (4, 9, 14, 1994)] == ["IV", "IX", "XIV", "MCMXCIV"]
    assert label_for(0, "roman") == "0"
