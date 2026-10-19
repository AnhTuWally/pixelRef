"""Playback of animated images: play/pause, stepping, go to frame, saved paused frames."""

from __future__ import annotations

from harness import APP, QTest, make, make_gif, tmp_dir
from PySide6.QtCore import QMimeData, QPointF, QSettings, Qt, QUrl

COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
HEX = ["#ff0000", "#00ff00", "#0000ff", "#ffff00"]


def _gif(h, name="Anim", x=0.0, y=0.0) -> str:
    path = make_gif(f"{name}.gif", 40, 20, COLORS)
    h.create("image", name, path=path.as_posix(), translate=[x, y])
    return name


def _shown(h, name) -> str:
    return h.item(name).pixmap().toImage().pixelColor(5, 5).name()


def test_playback_commands_step_wrap_and_clamp():
    h = make()
    try:
        name = _gif(h)
        item = h.item(name)
        assert item.animated and item.frame_count() == 4 and item.is_playing(), "GIFs play by default"
        h.select([name])
        assert h.run("playback.toggle") is False and not item.is_playing()
        assert h.run("playback.first_frame") == 1 and _shown(h, name) == HEX[0]
        assert h.run("playback.next_frame") == 2 and _shown(h, name) == HEX[1]
        assert h.run("playback.next_frame", count=2) == 4 and _shown(h, name) == HEX[3]
        assert h.run("playback.next_frame") == 1, "stepping past the last frame wraps around"
        assert h.run("playback.prev_frame") == 4, "and back again"
        assert h.run("playback.go_to_frame", 3) == 3 and _shown(h, name) == HEX[2]
        assert h.run("playback.go_to_frame", 99) == 4, "clamped to the last frame"
        assert h.run("playback.go_to_frame", 0) == 1
        assert h.run("playback.last_frame") == 4
        info = h.run("playback.info")
        assert info == {"node": name, "frame": 4, "frames": 4, "playing": False}, info
        assert h.run("playback.toggle") is True and item.is_playing()
        before = h.session.undo.stack.labels()
        h.run("playback.pause")
        h.run("playback.next_frame")
        assert h.session.undo.stack.labels() == before, "playback is not undoable"
        # Still images aren't playable.
        h.add_image("Still", 300, 0)
        h.select(["Still"])
        assert h.app.run_command("playback.toggle") is None, "an error is reported, not raised"
    finally:
        h.close()


def test_playback_keys_act_on_the_selected_animation():
    h = make()
    try:
        name = _gif(h)
        item = h.item(name)
        h.select([])
        h.key(Qt.Key_K)
        assert item.is_playing(), "K does nothing without an animated image selected"
        h.select([name])
        h.key(Qt.Key_K)
        assert not item.is_playing()
        h.key(Qt.Key_Comma, Qt.ControlModifier)
        assert item.current_frame() == 1
        h.key(Qt.Key_Period)
        assert item.current_frame() == 2
        h.key(Qt.Key_Comma)
        assert item.current_frame() == 1
        h.key(Qt.Key_Period, Qt.ControlModifier)
        assert item.current_frame() == 4
        h.key(Qt.Key_K)
        assert item.is_playing()
        menu = h.app.build_context_menu(h.view)
        sub = next(a.menu() for a in menu.actions() if a.menu() is not None and a.text() == "Playback")
        assert [a.text() for a in sub.actions() if a.text()][:3] == ["Play / Pause", "Previous Frame", "Next Frame"]
    finally:
        h.close()


def test_playback_section_in_the_attribute_editor():
    h = make()
    try:
        name = _gif(h)
        h.select([name])
        section = h.section("playback")
        assert section is not None and section.isVisible()
        assert section.play_btn.isChecked(), "shown as playing"
        QTest.mouseClick(section.play_btn, Qt.LeftButton)
        assert not h.item(name).is_playing() and not section.play_btn.isChecked()
        QTest.mouseClick(section.next_btn, Qt.LeftButton)
        frame = h.item(name).current_frame()
        assert section.frame_label.text() == f"{frame} / 4" and section.slider.value() == frame
        section.frame_spin.setValue(3)
        section.frame_spin.editingFinished.emit()
        assert h.item(name).current_frame() == 3 and section.frame_label.text() == "3 / 4"
        section.slider.sliderMoved.emit(2)
        assert h.item(name).current_frame() == 2 and section.frame_spin.value() == 2
        QTest.mouseClick(section.last_btn, Qt.LeftButton)
        assert h.item(name).current_frame() == 4
        # Not shown for still images.
        h.add_image("Still", 300, 0)
        h.select(["Still"])
        assert not h.section("playback").isVisible()
        assert not h.grab().isNull()
    finally:
        h.close()


def test_filmstrip_shows_thumbnails_and_scrubs():
    h = make()
    try:
        name = _gif(h)
        h.select([name])
        section = h.section("playback")
        strip = section.filmstrip
        assert strip.isVisible()
        frames = h.item(name).filmstrip()
        assert len(frames) == 4, "all 4 GIF frames fit in a 12-thumbnail strip"
        strip.resize(100, strip.height())
        strip._scrub_to(0.0)
        assert h.item(name).current_frame() == 1 and _shown(h, name) == HEX[0]
        strip._scrub_to(99.0)
        assert h.item(name).current_frame() == 4 and _shown(h, name) == HEX[3]
        # Not shown for still images.
        h.add_image("Still", 300, 0)
        h.select(["Still"])
        assert not section.filmstrip.isVisible()
    finally:
        h.close()


def test_paused_frame_survives_edits_and_is_saved_with_the_scene():
    h = make()
    try:
        name = _gif(h)
        h.select([name])
        h.run("playback.go_to_frame", 3)
        assert h.session.unsaved
        h.run("transform.flip", "h")  # rebuilds the item
        assert not h.item(name).is_playing() and h.item(name).current_frame() == 3 and _shown(h, name) == HEX[2]
        h.undo()
        assert h.item(name).current_frame() == 3
        path = tmp_dir() / "paused.pxref"
        assert h.save_to(path)
        assert list(h.saved_json(path)["plugins"]["playback"].values()) == [{"frame": 3}]
        h2 = make()
        try:
            assert h2.open(path)
            item = h2.item(name)
            assert not item.is_playing() and item.current_frame() == 3 and _shown(h2, name) == HEX[2]
            h2.select([name])
            h2.run("playback.play")
            assert item.is_playing() and not h2.session.scene.get_plugin_data("playback")
        finally:
            h2.close()
    finally:
        h.close()


def test_dropped_gifs_are_embedded_and_still_playable():
    h = make()
    try:
        path = make_gif("dropped.gif", 30, 30, COLORS[:3])
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        name = h.drop(QPointF(0, 0), mime)[0]
        assert h.attr(name, "asset"), "embedded"
        item = h.item(name)
        assert item.animated and item.frame_count() == 3
        h.select([name])
        assert h.run("playback.go_to_frame", 2) == 2 and _shown(h, name) == HEX[1]
    finally:
        h.close()


def test_playing_gif_keeps_playing_after_rebuild_and_hidden_ones_stop_decoding():
    from PySide6.QtGui import QMovie

    h = make()
    try:
        path = make_gif("Rebuilt.gif", 40, 20, COLORS, delay_cs=5)
        h.create("image", "Anim", path=path.as_posix(), translate=[0.0, 0.0])
        item = h.item("Anim")
        for _ in range(200):
            if item.current_frame() >= 3:
                break
            QTest.qWait(10)
        assert item.current_frame() >= 2
        h.run("set_attr", "Anim", "translate", [10.0, 0.0])  # rebuilds the item
        h.refresh()
        item = h.item("Anim")
        assert item.is_playing(), "a playing GIF must not pause when its item is rebuilt"
        assert item._frames is None, "carrying on must not decode every frame"
        item.setVisible(False)  # e.g. a hidden parent
        assert item.movie.state() == QMovie.Paused and item.is_playing()
        item.setVisible(True)
        assert item.movie.state() == QMovie.Running
        h.run("set_attr", "Anim", "visibility", 0)
        h.refresh()
        item = h.item("Anim")
        assert item.movie.state() == QMovie.Paused, "hidden animations don't decode frames"
        h.select(["Anim"])
        h.run("playback.pause")
        h.run("set_attr", "Anim", "visibility", 1)
        h.refresh()
        assert not h.item("Anim").is_playing(), "a paused GIF stays paused when shown"
        frames = h.item("Anim").decoded_frames()
        h.run("set_attr", "Anim", "translate", [20.0, 0.0])
        h.refresh()
        assert h.item("Anim").decoded_frames() is frames, "rebuilt items reuse the decoded frames"
    finally:
        h.close()


_ = APP


def test_on_canvas_controls_play_pause_and_step():
    h = make()
    try:
        h.run("quick_edit.toggle", False)  # the painted bar is the fallback for when the quick editor is off
        name = _gif(h, x=0.0, y=0.0)
        h.run("set_attrs", name, {"scale": [5.0, 5.0]})
        h.refresh()
        item = h.item(name)
        controls = h.service("playback.controls")
        assert controls.layout(h.view) == [], "nothing shows until the GIF is selected"
        h.select([name])
        (_n, _i, buttons, bar), = controls.layout(h.view)
        image_rect = h.view.mapFromScene(item.sceneBoundingRect()).boundingRect()
        assert bar.top() > image_rect.bottom() and abs(bar.center().x() - image_rect.center().x()) < 2, \
            "the bar sits just under the image, centred"
        assert item.is_playing()
        h.click(buttons["toggle"].center().toPoint())
        assert not item.is_playing(), "play/pause button pauses"
        frame = item.current_frame()
        h.click(buttons["next"].center().toPoint())
        assert item.current_frame() == frame % 4 + 1
        h.click(buttons["prev"].center().toPoint())
        h.click(buttons["prev"].center().toPoint())
        assert item.current_frame() == (frame - 2) % 4 + 1
        h.click(buttons["toggle"].center().toPoint())
        assert item.is_playing()
        assert h.sel() == [name], "clicking the controls keeps the selection"
        # Clicking the bar between buttons doesn't fall through to the canvas.
        h.click(QPointF(bar.right() - 3, bar.center().y()).toPoint())
        assert h.sel() == [name]
        assert not h.grab().isNull()
        h.run("quick_edit.toggle", True)
        assert controls.layout(h.view) == [], "the quick editor carries the transport when it's on"
    finally:
        h.run("quick_edit.toggle", True)
        h.close()


def _settle() -> None:
    QTest.qWait(5)
    APP.processEvents()


def _quick_bar(h):
    from pixelref.builtins.quick_edit.ui import QuickBar

    bars = [b for b in h.view.findChildren(QuickBar) if b.isVisible()]
    return bars[0] if bars else None


def test_transport_is_part_of_the_quick_editor_bar():
    QSettings().remove("plugins/quick_edit")
    h = make()
    try:
        name = _gif(h, x=0.0, y=0.0)
        h.run("set_attrs", name, {"scale": [5.0, 5.0]})
        h.refresh()
        item = h.item(name)
        h.select([name])
        _settle()
        bar = _quick_bar(h)
        assert bar is not None, "a GIF gets the quick editor bar"
        transport = bar.sections["playback"][1]
        assert transport.isVisible() and bar.divider.isVisible() and "opacity" in bar.editors
        assert h.service("playback.controls").layout(h.view) == [], "no second, overlapping bar"
        image_rect = h.view.mapFromScene(item.sceneBoundingRect()).boundingRect()
        assert bar.geometry().top() > image_rect.bottom()
        assert transport.buttons["toggle"].isChecked() and item.is_playing()
        QTest.mouseClick(transport.buttons["toggle"], Qt.LeftButton)
        assert not item.is_playing() and not transport.buttons["toggle"].isChecked()
        frame = item.current_frame()
        QTest.mouseClick(transport.buttons["next"], Qt.LeftButton)
        assert item.current_frame() == frame % 4 + 1
        assert transport.frame_label.text() == f"{item.current_frame()} / 4"
        QTest.mouseClick(transport.buttons["prev"], Qt.LeftButton)
        assert item.current_frame() == frame
        width = bar.width()
        h.run("playback.go_to_frame", 4)
        _settle()
        assert transport.frame_label.text() == "4 / 4" and bar.width() == width, "fixed width: no wobble"
        assert h.sel() == [name]
        # The filmstrip (below the transport buttons) scrubs the same way as the inspector's.
        strip = transport.filmstrip
        assert strip.isVisible() and len(item.filmstrip()) == 4
        strip._scrub_to(0.0)
        assert item.current_frame() == 1
        strip._scrub_to(float(strip.width()) - 1.0)
        assert item.current_frame() == 4
        # Still images: the bar is just the fields.
        h.add_image("Still", 600, 0)
        h.select(["Still"])
        _settle()
        bar = _quick_bar(h)
        assert bar is not None and not bar.sections["playback"][1].isVisible() and not bar.divider.isVisible()
        assert not bar.sections["playback"][1].filmstrip.isVisible()
        # Several animations: one transport for all of them, also with a still image (no shared fields).
        _gif(h, "Anim2", x=0.0, y=400.0)
        h.select([name, "Anim2", "Still"])
        h.run("playback.pause", [name, "Anim2"])
        _settle()
        bar = _quick_bar(h)
        transport = bar.sections["playback"][1]
        assert transport.isVisible() and transport.names == [name, "Anim2"]
        QTest.mouseClick(transport.buttons["toggle"], Qt.LeftButton)
        assert h.item(name).is_playing() and h.item("Anim2").is_playing()
    finally:
        h.close()


def test_transport_can_be_hidden_from_the_quick_editor():
    QSettings().remove("plugins/quick_edit")
    h = make()
    try:
        name = _gif(h)
        h.select([name])
        _settle()
        controller_entries = [t for t, _c in _quick_bar(h).controller.menu_entries() if t]
        assert controller_entries[0] == "Hide Playback Controls", controller_entries
        assert h.run("quick_edit.show_section", "playback") is False
        _settle()
        bar = _quick_bar(h)
        assert bar is not None and not bar.sections["playback"][1].isVisible(), "the fields stay"
        assert h.service("playback.controls").layout(h.view) == [], "hidden means hidden, no fallback"
        assert [t for t, _c in bar.controller.menu_entries() if t][0] == "Show Playback Controls"
        h.run("quick_edit.set_fields", "image", [])
        _settle()
        assert _quick_bar(h) is None, "nothing left to show"
        h.run("quick_edit.show_section", "playback", True)
        _settle()
        assert _quick_bar(h) is not None and not _quick_bar(h).editors, "transport only"
        assert h.app.run_command("quick_edit.show_section", "nope") is None, "unknown sections are an error"
    finally:
        QSettings().remove("plugins/quick_edit")
        h.close()
