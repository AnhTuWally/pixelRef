"""Pages: page frames on the board and the multi-page PDF export."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt

from harness import TMP, approx, make, wait_for_images


def media_boxes(data: bytes) -> list[tuple[float, float]]:
    """(width, height) in points of every page of a PDF written by Qt."""
    boxes = []
    for match in re.finditer(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", data):
        x0, y0, x1, y1 = (float(v) for v in match.groups())
        boxes.append((x1 - x0, y1 - y0))
    return boxes


def page_count(data: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page\b(?!s)", data))


MM_TO_PT = 72.0 / 25.4


def test_create_snaps_to_paper_and_turning_keeps_centre():
    h = make()
    try:
        a = h.run("pages.create", 0, 0, 420, 50, "A4", "landscape")
        w, height = h.attr(a, "size")
        assert approx(w / height, 297.0 / 210.0, 1e-3), "an A4 page keeps the A4 shape"
        free = h.run("pages.create", 0, 0, 300, 50, "Free", "landscape")
        assert h.attr(free, "size") == [300.0, 50.0], "a free page keeps the size it was given"
        assert [h.attr(a, "page_order"), h.attr(free, "page_order")] == [1, 2]
        h.run("pages.set_paper", [a], None, "portrait")
        pw, ph = h.attr(a, "size")
        assert approx(pw / ph, 210.0 / 297.0, 1e-3)
        tx, ty = h.attr(a, "translate")
        assert approx(tx + pw / 2, w / 2) and approx(ty + ph / 2, height / 2), "turning keeps the centre"
        h.run("pages.set_paper", [a], "16:9")
        sw, sh = h.attr(a, "size")
        assert approx(sh / sw, 16.0 / 9.0, 1e-3), "16:9 portrait is 9 wide, 16 tall"
    finally:
        h.close()


def test_reorder_move_and_numbers():
    h = make()
    try:
        names = [h.run("pages.create", i * 500, 0, 400, 300, "A4", "landscape") for i in range(3)]
        h.run("pages.reorder", [names[2], names[0]])
        assert [p["name"] for p in h.run("pages.list")] == [names[2], names[0], names[1]]
        h.refresh()
        assert h.item(names[2]).number == 1 and h.item(names[1]).number == 3
        h.run("pages.move", names[1], -2)
        assert [p["name"] for p in h.run("pages.list")] == [names[1], names[2], names[0]]
        h.undo()
        assert [p["name"] for p in h.run("pages.list")] == [names[2], names[0], names[1]], "reordering undoes"
    finally:
        h.close()


def test_pages_survive_save_and_reload():
    h = make()
    try:
        a = h.run("pages.create", 10, 20, 400, 300, "Letter", "portrait", "Cover")
        h.run("set_attr", f"{a}.export", False)
        path = TMP / "pages_roundtrip.pxref"
        assert h.save_to(path)
        assert h.open(path)
        assert h.type_of(a) == "page_frame"
        attrs = h.attrs(a)
        assert attrs["paper"] == "Letter" and attrs["orientation"] == "portrait"
        assert attrs["title"] == "Cover" and attrs["export"] is False
    finally:
        h.close()


def test_pdf_has_one_page_per_frame_with_paper_sizes():
    h = make()
    try:
        h.add_image("Img", 0, 0, 300, 200)
        wait_for_images()
        a4 = h.run("pages.create", 0, 0, 400, 1, "A4", "landscape")
        letter = h.run("pages.create", 500, 0, 300, 1, "Letter", "portrait")
        free = h.run("pages.create", 0, 400, 600, 200, "Free", "landscape")
        skipped = h.run("pages.create", 0, 900, 300, 1, "A5", "portrait")
        h.run("set_attr", f"{skipped}.export", False)
        h.run("pages.reorder", [letter, a4, free])
        h.refresh()
        out = TMP / "pages.pdf"
        assert h.run("pages.export_pdf", str(out), None, 150) == 3, "pages switched off are skipped"
        data = out.read_bytes()
        assert data.startswith(b"%PDF")
        assert page_count(data) == 3
        boxes = media_boxes(data)
        expected = [(215.9, 279.4), (297.0, 210.0), (297.0, 99.0)]  # letter first: the page order
        assert len(boxes) == 3, boxes
        for (w, height), (ew, eh) in zip(boxes, expected):
            assert approx(w, ew * MM_TO_PT, 1.5) and approx(height, eh * MM_TO_PT, 1.5), (boxes, expected)
        assert b"/Subtype /Image" in data or b"/Subtype/Image" in data, "the image under the pages is embedded"

        ranged = TMP / "pages_range.pdf"
        assert h.run("pages.export_pdf", str(ranged), "2-4") == 3, "a range picks pages by number"
        assert h.run("pages.export_pdf", str(ranged), [skipped]) == 1, "named pages export even when switched off"
    finally:
        h.close()


def test_pdf_without_pages_exports_the_view():
    h = make()
    try:
        h.add_image("Img", 0, 0, 300, 200)
        wait_for_images()
        out = TMP / "view.pdf"
        assert h.run("pages.export_pdf", str(out)) == 1
        assert page_count(out.read_bytes()) == 1
    finally:
        h.close()


def test_page_frames_never_show_in_image_exports():
    h = make()
    try:
        h.add_image("Img", 0, 0, 200, 100)
        wait_for_images()
        h.run("pages.create", -1000, -1000, 4000, 1, "A4", "landscape")
        h.select([])
        out = TMP / "board.png"
        size = h.run("export.render", str(out))
        assert size == [200, 100], "the page frame doesn't stretch the exported image"
    finally:
        h.close()


def test_add_from_view_fits_inside_the_view_and_next_page_fits_view():
    h = make()
    try:
        view = h.view
        name = h.run("pages.add_from_view", "A4")
        h.refresh()
        visible = view.visible_scene_rect()
        rect = h.item(name).scene_content_rect()
        assert visible.contains(rect), "the new page fits in what the view shows"
        other = h.run("pages.create", 5000, 5000, 400, 1, "A4", "landscape")
        h.refresh()
        assert h.run("pages.go_to", 2) == other
        assert view.visible_scene_rect().contains(h.item(other).scene_content_rect())
        assert h.run("pages.next") == name, "next wraps around to the first page"
    finally:
        h.close()


def test_page_tool_drag_tab_move_and_aspect_locked_resize():
    h = make()
    try:
        h.set_tool("page")
        h.app.tools.get("page").options.set("paper", "A4")
        h.drag(h.vp(0, 0), h.vp(150, 300))  # taller than wide: portrait
        pages = h.nodes_of("page_frame")
        assert len(pages) == 1
        name = pages[0]
        w, height = h.attr(name, "size")
        assert h.attr(name, "orientation") == "portrait"
        assert approx(height / w, 297.0 / 210.0, 1e-3) and approx(height, 300.0, 1.0), "covers the drag"
        h.refresh()

        # Drag the number tab (just above the top-left corner) to move the page.
        h.set_tool("select")
        tab = h.vp(0, 0)
        tab.setX(tab.x() + 6)
        tab.setY(tab.y() - 8)
        h.drag(tab, tab + (h.vp(100, 50) - h.vp(0, 0)))
        tx, ty = h.attr(name, "translate")
        assert approx(tx, 100.0, 1.5) and approx(ty, 50.0, 1.5), (tx, ty)
        assert h.attr(name, "size") == [w, height], "moving doesn't resize"
        assert h.sel() == [name]

        # The corner handle keeps the paper's shape; Shift frees it.
        h.refresh()
        corner = h.gizmo()["handles_vp"]["br"].toPoint()
        h.drag(corner, corner + (h.vp(200, 0) - h.vp(0, 0)))
        w2, h2 = h.attr(name, "size")
        assert approx(h2 / w2, 297.0 / 210.0, 1e-3) and w2 > w + 150
        corner = h.gizmo()["handles_vp"]["br"].toPoint()
        h.drag(corner, corner + (h.vp(0, 80) - h.vp(0, 0)), modifiers=Qt.ShiftModifier)
        assert h.attr(name, "paper") == "Free"
    finally:
        h.close()
