"""Per-frame annotations: pinning to a frame, the visibility rule, drawing on a paused frame."""

from __future__ import annotations

from harness import make, make_gif

COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]


def _gif(h, name="Anim", x=0.0, y=0.0) -> str:
    path = make_gif(f"{name}.gif", 40, 20, COLORS)
    h.create("image", name, path=path.as_posix(), translate=[x, y])
    return name


def _go(h, name, frame) -> None:
    h.run("playback.go_to_frame", frame, [name])


def test_pinned_annotations_show_only_on_their_frame():
    h = make()
    try:
        name = _gif(h)
        note = h.create("annotation_rect", "Note", parent=name, size=[10, 5])
        assert h.session.scene.get_node(note).attrs.get("frame", 0) == 0, "every frame by default"
        _go(h, name, 2)
        h.select([note])
        assert h.run("frames.pin") == [note]
        assert h.session.scene.get_attr(note, "frame") == 2
        h.refresh()
        item = h.item(note)
        assert item.isVisible(), "shown on its own frame"
        _go(h, name, 3)
        assert not item.isVisible(), "hidden on the others"
        _go(h, name, 2)
        assert item.isVisible()
        _go(h, name, 4)
        h.refresh()
        assert not h.item(note).isVisible(), "a rebuilt item follows the rule too"
        h.select([note])
        h.run("frames.unpin")
        h.refresh()
        assert h.session.scene.get_attr(note, "frame") == 0 and h.item(note).isVisible()
        h.undo()
        assert h.session.scene.get_attr(note, "frame") == 2, "pinning is undoable"
    finally:
        h.close()


def test_a_frame_past_the_end_is_kept_and_never_shows():
    h = make()
    try:
        name = _gif(h)
        note = h.create("annotation_rect", "Note", parent=name, size=[10, 5], frame=9)
        for frame in (1, 4):
            _go(h, name, frame)
            assert not h.item(note).isVisible()
        assert h.session.scene.get_attr(note, "frame") == 9
    finally:
        h.close()


def test_pin_adopts_an_annotation_lying_on_the_animation():
    h = make()
    try:
        name = _gif(h)
        note = h.create("annotation_rect", "Loose", size=[8, 4], translate=[5, 5])
        _go(h, name, 3)
        h.select([note])
        h.run("frames.pin")
        node = h.session.scene.get_node(note)
        assert node.parent == name and node.attrs["frame"] == 3
        far = h.create("annotation_rect", "Far", size=[8, 4], translate=[500, 500])
        h.select([far])
        assert h.app.run_command("frames.pin") is None, "nothing beneath it: an error is reported, not raised"
        assert h.session.scene.get_node(far).parent is None
    finally:
        h.close()


def test_drawing_on_a_paused_frame_pins_it_and_while_playing_does_not():
    h = make()
    try:
        name = _gif(h)
        _go(h, name, 3)
        drawn = h.run("rect.create", 4, 4, 10, 6)
        node = h.session.scene.get_node(drawn)
        assert node.parent == name and node.attrs["frame"] == 3
        h.run("playback.play", [name])
        loose = h.run("rect.create", 4, 4, 10, 6)
        assert h.session.scene.get_node(loose).parent is None, "playing: an annotation on the board, as before"
        _go(h, name, 2)
        stroke = h.run("pen.add_stroke", [[3, 3], [20, 10]])
        drawing = h.session.scene.get_node(stroke[0])
        assert drawing.parent == name and drawing.attrs["frame"] == 2
        h.run("pen.add_stroke", [[5, 5], [9, 9]], stroke[0])
        assert h.session.scene.get_node(stroke[0]).attrs["frame"] == 2, "the next stroke joins the same drawing"
        _go(h, name, 3)
        outside = h.run("rect.create", 300, 300, 10, 6)
        assert h.session.scene.get_node(outside).parent is None, "away from the picture it stays on the board"
        assert h.run("frames.of", name) == {2: 1, 3: 1}
    finally:
        h.close()


def test_copy_to_other_frames():
    h = make()
    try:
        name = _gif(h)
        _go(h, name, 1)
        note = h.run("rect.create", 2, 2, 10, 6)
        copies = h.run("frames.copy_to", "2-3,9", [note])
        assert [h.session.scene.get_attr(c, "frame") for c in copies] == [2, 3]
        assert h.run("frames.of", name) == {1: 1, 2: 1, 3: 1}
        assert h.app.run_command("frames.copy_to", "1", [note]) is None, "already there: an error"
    finally:
        h.close()


def test_the_default_frame_is_not_stored():
    h = make()
    try:
        name = _gif(h)
        note = h.create("annotation_rect", "Note", parent=name, size=[10, 5], frame=3)
        node = h.session.scene.get_node(note)
        assert node.attrs["frame"] == 3
        h.select([note])
        h.run("frames.unpin")
        assert "frame" not in node.attrs, "back to every frame: nothing left in the file"
        assert h.session.scene.get_attr(note, "frame") == 0
        h.run("frames.pin", [note], 2)
        assert node.attrs["frame"] == 2
    finally:
        h.close()


def test_links_anchor_to_the_frame_on_show_and_jump_back():
    h = make()
    try:
        name = _gif(h)
        _go(h, name, 3)
        note = h.create("annotation_rect", "Elsewhere", size=[8, 4], translate=[500, 500])
        link = h.run("links.connect", note, name)
        scene = h.session.scene
        assert scene.get_attr(link, "target_frame") == 3 and "source_frame" not in scene.get_node(link).attrs
        assert h.run("frames.of", name) == {}, "links are counted apart from notes"
        assert h.service("frames").tick_counts(name) == {3: [0, 1]}
        assert h.service("frames").describe_frame(name, 3) == "Frame 3 · 1 link"
        _go(h, name, 1)
        h.service("frames").show_frame(name, scene.get_attr(link, "target_frame"))
        assert h.item(name).current_frame() == 3
        h.select([link])
        h.run("frames.clear_link_frame")
        assert "target_frame" not in scene.get_node(link).attrs, "back to the whole element: nothing stored"
        assert h.app.run_command("frames.clear_link_frame") is None, "nothing to clear: an error"
        h.run("frames.link_frame", link, "target", 2)
        assert scene.get_attr(link, "target_frame") == 2
    finally:
        h.close()


def test_a_link_end_moved_off_the_animation_forgets_its_frame():
    h = make()
    try:
        name = _gif(h)
        other = h.create("annotation_rect", "Other", size=[8, 4], translate=[500, 500])
        third = h.create("annotation_rect", "Third", size=[8, 4], translate=[600, 500])
        _go(h, name, 2)
        link = h.run("links.connect", other, name)
        assert h.session.scene.get_attr(link, "target_frame") == 2
        h.run("links.set_end", link, "target", third)
        assert "target_frame" not in h.session.scene.get_node(link).attrs
    finally:
        h.close()


def test_selecting_a_pinned_note_jumps_to_its_frame():
    h = make()
    try:
        name = _gif(h)
        note = h.create("annotation_rect", "Note", parent=name, size=[10, 5], frame=3)
        _go(h, name, 1)
        h.select([note])
        assert h.item(name).current_frame() == 3
    finally:
        h.close()


def test_ghost_frames_fade_the_notes_around_their_own_frame():
    h = make()
    try:
        name = _gif(h)
        note = h.create("annotation_rect", "Note", parent=name, size=[10, 5], frame=2)
        _go(h, name, 4)
        item = h.item(note)
        assert not item.isVisible(), "off by default"
        h.select([name])
        assert h.section("frame_ghost") is not None
        h.run("frames.set_ghost", 2)
        assert h.session.scene.get_attr(name, "ghost") == 2
        assert item.isVisible() and item.isEnabled() is False and abs(item.opacity() - 1 / 3) < 0.01, \
            "two frames away with ghost 2: faint and not clickable"
        _go(h, name, 3)
        assert abs(item.opacity() - 2 / 3) < 0.01
        _go(h, name, 2)
        assert item.opacity() == 1.0 and item.isEnabled()
        _go(h, name, 1)
        assert abs(item.opacity() - 2 / 3) < 0.01, "before the frame too"
        h.refresh()
        assert abs(h.item(note).opacity() - 2 / 3) < 0.01, "a rebuilt item is faded too"
        h.run("frames.set_ghost", 0)
        assert not h.item(note).isVisible() and "ghost" not in h.session.scene.get_node(name).attrs
    finally:
        h.close()


def test_a_link_fades_towards_an_end_whose_frame_isnt_showing():
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    h = make()
    try:
        name = _gif(h)
        note = h.create("annotation_rect", "Far", size=[10, 10], translate=[300, 5])
        _go(h, name, 3)
        link = h.run("links.connect", note, name, routing="straight", thickness=6, color=[255, 0, 0])
        h.refresh()
        frames = h.service("frames")
        assert frames.link_alphas(link) == [1.0, 1.0], "the frame is showing"

        def alpha_at(x: int) -> int:
            h.item(link).update_route()
            image = QImage(400, 40, QImage.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            h.app.canvas.scene.render(painter, QRectF(0, 0, 400, 40), QRectF(0, 0, 400, 40))
            painter.end()
            return max(image.pixelColor(x, y).alpha() for y in range(8, 18))

        near_gif_shown = alpha_at(70)
        _go(h, name, 1)
        h.refresh()
        assert frames.link_alphas(link) == [1.0, 0.15]
        near_note, near_gif = alpha_at(270), alpha_at(70)
        assert near_note > 200, "the free end stays solid"
        assert near_gif < near_gif_shown * 0.6, (near_gif, near_gif_shown)
    finally:
        h.close()
