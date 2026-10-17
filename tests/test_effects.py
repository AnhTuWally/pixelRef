"""Effects: stacks and commands, the built-in processors, drawing on any node type,
the caches (memory, disk, scene file), background processing and animations."""

from __future__ import annotations

import json

from harness import APP, Harness, make_gif, tmp_dir
from PySide6.QtGui import QColor, QImage, QPainter


def headless():
    from pixelref.core.packages import PackageManager
    from pixelref.core.session import Session

    return Session(packages=PackageManager(root=tmp_dir()), make_current=False).start()


def app():
    """A harness whose effect disk cache is private to the test."""
    h = Harness()
    h.engine = h.service("effects_engine")
    h.engine.disk.folder = tmp_dir()
    h.engine.clear(memory=True, scene=True)
    return h


def render(h, name):
    return h.app.canvas.render_items([h.item(name)])


def paint_views(h):
    """A real paint of the canvas (like the screen): background requests start here.
    render_frame paints now; an OpenGL viewport's repaint() may paint later."""
    h.view.render_frame()
    APP.processEvents()


def gray_of(color: QColor) -> bool:
    return color.red() == color.green() == color.blue()


# ---- Stacks and commands (headless) -------------------------------------------------------------------


def test_stack_commands_validate_and_undo():
    from pixelref.core.errors import CommandError, SceneError

    session = headless()
    pxcmd = session.commands.proxy()
    try:
        pxcmd.create_node("annotation_rect", "Box")
        pxcmd.create_node("annotation_text", "Note")
        assert "effects" not in session.scene.nodes["Box"].attrs, "unused: not stored (files don't change)"
        names = [t["name"] for t in pxcmd.effects.types()]
        assert {"grayscale", "invert", "brightness_contrast", "threshold", "posterize", "blur",
                "edge_detect"} <= set(names)
        blur_id, = pxcmd.effects.add("blur", "Box", radius=8)
        pxcmd.effects.add("grayscale", ["Box", "Note"])
        stack = pxcmd.effects.list("Box")
        assert [e["type"] for e in stack] == ["blur", "grayscale"]
        assert stack[0] == {"id": blur_id, "type": "blur", "enabled": 1, "mix": 1.0, "params": {"radius": 8.0}}
        pxcmd.effects.set("Box", "blur", radius=600, mix=0.5)  # clamped like any attribute
        assert pxcmd.effects.list("Box")[0]["params"]["radius"] == 500.0
        assert pxcmd.effects.list("Box")[0]["mix"] == 0.5
        pxcmd.effects.move("Box", "grayscale", 0)
        assert [e["type"] for e in pxcmd.effects.list("Box")] == ["grayscale", "blur"]
        assert pxcmd.effects.toggle(blur_id, "Box") == 0
        assert pxcmd.effects.list("Box")[1]["enabled"] == 0
        pxcmd.effects.reset("Box", blur_id)
        assert pxcmd.effects.list("Box")[1]["params"] == {} and pxcmd.effects.list("Box")[1]["mix"] == 1.0
        for bad in (lambda: pxcmd.effects.add("sparkle", "Box"),
                    lambda: pxcmd.effects.set("Box", "blur", sharpness=2),
                    lambda: pxcmd.effects.set("Box", "nothing", radius=2),
                    lambda: pxcmd.set_attr("Box.effects", [{"params": {}}]),
                    lambda: pxcmd.set_attr("Box.effects", "grayscale")):
            try:
                bad()
            except (CommandError, SceneError):
                continue
            raise AssertionError("must be rejected")
        # Shift+G semantics: on unless every node has it already.
        assert pxcmd.effects.toggle_type("invert", ["Box", "Note"]) == 1
        assert pxcmd.effects.toggle_type("invert", ["Box", "Note"]) == 0
        assert all(e["type"] != "invert" for e in pxcmd.effects.list("Note"))
        pxcmd.effects.copy("Box")
        pxcmd.effects.paste("Note")
        note = pxcmd.effects.list("Note")
        assert [e["type"] for e in note] == ["grayscale", "blur"]
        assert {e["id"] for e in note}.isdisjoint({e["id"] for e in pxcmd.effects.list("Box")}), "fresh ids"
        pxcmd.effects.clear("Note")
        assert pxcmd.effects.list("Note") == []
        pxcmd.undo()
        assert len(pxcmd.effects.list("Note")) == 2
        # Effects of a type no plugin provides are kept as they are.
        pxcmd.set_attr("Box.effects", [{"type": "from_another_plugin", "params": {"anything": [1, 2]}}])
        assert pxcmd.effects.list("Box")[0]["params"] == {"anything": [1, 2]}
    finally:
        session.shutdown()



def two_tone(width=40, height=20) -> QImage:
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(200, 40, 40))
    painter = QPainter(image)
    painter.fillRect(width // 2, 0, width - width // 2, height, QColor(20, 20, 220))
    painter.end()
    image.setPixelColor(0, 0, QColor(200, 40, 40, 0))
    return image


def test_builtin_processors():
    from pixelref.builtins.effects import processors as p

    fx = p.EffectContext()
    image = two_tone()
    for name, processor in p.BUILTIN_PROCESSORS.items():
        out = processor(image, {}, fx)
        assert (out.width(), out.height()) == (40, 20), name
        assert out.pixelColor(0, 0).alpha() == 0, f"{name} keeps transparency"
    assert gray_of(p.grayscale(image, {}, fx).pixelColor(5, 5))
    half = p.grayscale(image, {"amount": 0.5}, fx).pixelColor(5, 5)
    assert 104 < half.red() < 200 and half.green() > 40, "amount blends"
    assert p.invert(image, {}, fx).pixelColor(5, 5).getRgb()[:3] == (55, 215, 215)
    assert p.threshold(image, {"level": 0.3}, fx).pixelColor(5, 5).getRgb()[:3] == (255, 255, 255)
    assert p.threshold(image, {"level": 0.6}, fx).pixelColor(5, 5).getRgb()[:3] == (0, 0, 0)
    assert p.posterize(image, {"levels": 2}, fx).pixelColor(5, 5).getRgb()[:3] == (255, 0, 0)
    brighter = p.brightness_contrast(image, {"brightness": 0.3}, fx).pixelColor(30, 5)
    assert brighter.red() > 20 and brighter.blue() == 255
    flat = p.brightness_contrast(image, {"contrast": -1.0}, fx).pixelColor(5, 5)
    assert flat.getRgb()[:3] == (128, 128, 128), "no contrast: mid grey"
    blurred = p.blur(image, {"radius": 3}, fx)
    seam = blurred.pixelColor(20, 10)
    assert 20 < seam.red() < 200 and 40 < seam.blue() < 220, "the edge between the halves is soft"
    assert blurred.pixelColor(2, 10).red() > 180, "borders don't darken"
    scaled = p.blur(image, {"radius": 3}, p.EffectContext(pixels_per_unit=0.1))
    assert scaled.pixelColor(19, 10).red() > 180, "radius is in canvas pixels: tiny at 1/10 resolution"
    lines = p.edge_detect(image, {}, fx)
    assert lines.pixelColor(5, 5).getRgb()[:3] == (255, 255, 255), "flat areas are paper"
    assert lines.pixelColor(20, 10).red() < 200, "a line where the colours meet"
    glow = p.edge_detect(image, {"style": "glow"}, fx)
    assert glow.pixelColor(5, 5).red() == 0 and glow.pixelColor(20, 10).red() > 55
    overlay = p.edge_detect(image, {"style": "overlay", "color": [0, 255, 0]}, fx)
    assert overlay.pixelColor(5, 5).getRgb()[:3] == (200, 40, 40) and overlay.pixelColor(20, 10).green() > 60
    stacked = p.run_stack(image, [(p.invert, {}, 1.0), (p.invert, {}, 1.0)], fx)
    assert stacked.pixelColor(5, 5).getRgb()[:3] == (200, 40, 40), "applied in order"


# ---- Drawing ------------------------------------------------------------------------------------------


def test_effects_draw_on_any_node_type_and_leave_annotations_alone():
    h = app()
    try:
        h.add_image("Img", 0, 0, w=100, h=60)
        h.create("annotation_rect", "Mark", parent="Img", translate=[10, 10], size=[30, 20],
                 fill_color=[0, 200, 0], fill_opacity=1.0, stroke_color=[0, 200, 0])
        h.create("annotation_rect", "Box", translate=[300, 0], size=[40, 30], fill_color=[255, 0, 0],
                 fill_opacity=1.0, stroke_color=[255, 0, 0])
        h.create("annotation_text", "Note", translate=[0, 200], text="Hello", color=[255, 0, 0])
        h.run("effects.add", "grayscale", "Img")
        h.run("effects.add", "invert", ["Box"])
        h.run("effects.add", "grayscale", "Note")
        h.refresh()
        image = render(h, "Img")
        assert gray_of(image.pixelColor(5, 5)), image.pixelColor(5, 5).getRgb()
        assert image.pixelColor(25, 20) == QColor(0, 200, 0), "the image's annotation keeps its colour"
        box = render(h, "Box")
        assert box.pixelColor(box.width() // 2, box.height() // 2).getRgb()[:3] == (0, 255, 255)
        note = render(h, "Note")
        coloured = [note.pixelColor(x, y) for x in range(note.width()) for y in range(note.height())
                    if note.pixelColor(x, y).alpha() > 200]
        assert coloured and all(abs(c.red() - c.green()) < 3 for c in coloured), "text is grey too"
        h.run("effects.toggle", "invert", "Box")
        h.refresh()
        box = render(h, "Box")
        assert box.pixelColor(box.width() // 2, box.height() // 2).getRgb()[:3] == (255, 0, 0), "off: as it was"
        h.run("set_attr", "Img.effects", [{"type": "not_installed"}])
        h.refresh()
        assert render(h, "Img").pixelColor(5, 5).red() > 150, "unknown effects are skipped, not fatal"
        section = h.section("effects")
        h.select(["Img"])
        assert h.section("effects") is not None
        _ = section
    finally:
        h.close()


def test_results_survive_item_rebuilds():
    """Moving or nudging rebuilds the item: it must reuse the result, not process again."""
    h = app()
    try:
        h.add_image("Big", 0, 0, w=600, h=400)
        h.run("effects.add", "grayscale", "Big")
        h.refresh()
        render(h, "Big")
        engine = h.engine
        sync = engine.stats["sync"]
        first = engine.stale["Big"][0]
        for step in range(3):
            h.run("set_attr", "Big.translate", [10.0 * step, 0.0])
            h.refresh()
            render(h, "Big")
        assert engine.stats["sync"] == sync, "no reprocessing after moves"
        assert engine.stale["Big"][0].cacheKey() == first.cacheKey(), "the same pixmap (its mipmaps stay cached)"
    finally:
        h.close()


def test_effects_can_be_muted_per_element_and_all_at_once():
    h = app()
    try:
        h.create("annotation_rect", "Box", translate=[0, 0], size=[40, 30], fill_color=[255, 0, 0],
                 fill_opacity=1.0, stroke_color=[255, 0, 0])
        h.create("annotation_rect", "Other", translate=[100, 0], size=[40, 30], fill_color=[255, 0, 0],
                 fill_opacity=1.0, stroke_color=[255, 0, 0])
        h.run("effects.add", "invert", ["Box", "Other"])
        h.refresh()

        def centre(name):
            image = render(h, name)
            return image.pixelColor(image.width() // 2, image.height() // 2).getRgb()[:3]

        assert centre("Box") == (0, 255, 255)
        assert h.run("effects.mute", "Box") == 1
        h.refresh()
        assert centre("Box") == (255, 0, 0) and centre("Other") == (0, 255, 255), "only Box is muted"
        assert [e["type"] for e in h.attr("Box", "effects")] == ["invert"], "the stack is kept"
        h.select(["Box"])
        APP.processEvents()
        section = h.section("effects")
        assert section.mute_button.isChecked() and "Muted" in section.note.text()
        section.mute_button.click()  # unmute from the Attribute Editor
        h.refresh()
        assert h.attr("Box", "effects_muted") == 0 and centre("Box") == (0, 255, 255)
        h.run("undo")
        h.refresh()
        assert h.attr("Box", "effects_muted") == 1, "muting is undoable"
        h.run("effects.mute", ["Box", "Other"])  # mixed selection: mute all
        assert h.attr("Other", "effects_muted") == 1
        h.run("effects.mute", ["Box", "Other"], on=False)
        # The app-wide switch: a before/after check, not saved, not undoable.
        assert h.run("effects.show", False) is False
        h.refresh()
        assert centre("Box") == (255, 0, 0) and centre("Other") == (255, 0, 0)
        h.run("effects.show")
        h.refresh()
        assert centre("Other") == (0, 255, 255)
    finally:
        h.run("effects.show", True)
        h.close()


# ---- Background processing and the caches -------------------------------------------------------------


def test_heavy_effects_run_in_the_background_and_are_kept_on_disk():
    h = app()
    try:
        h.add_image("Img", -50, -30, w=100, h=60)
        h.run("effects.add", "edge_detect", "Img")
        h.refresh()
        engine = h.engine
        paint_views(h)
        assert engine.busy(next(iter(engine._pending.values())).key) if engine._pending else engine._inflight, \
            "the first paint only asks for the result"
        assert engine.wait()
        assert engine.stats["computed"] == 1
        assert list(engine.disk.folder.glob("*/*.webp")), "written to the disk cache"
        paint_views(h)
        image = render(h, "Img")
        assert image.pixelColor(50, 30).getRgb()[:3] == (255, 255, 255), "edge detect on a flat image: paper"
        # A new session: nothing in memory, the result comes from the disk.
        engine.clear(memory=True, scene=True)
        paint_views(h)
        assert engine.wait()
        assert engine.stats["disk"] == 1 and engine.stats["computed"] == 1
    finally:
        h.close()


def test_previews_never_wait_for_heavy_effects_but_exports_do():
    h = app()
    try:
        h.add_image("Img", -50, -30, w=100, h=60)
        h.run("effects.add", "edge_detect", "Img", style="glow")
        h.refresh()
        engine = h.engine
        preview = h.app.canvas.render_items([h.item("Img")], final=False)  # minimap, thumbnails
        assert engine.stats["sync"] == 0 and preview.pixelColor(50, 30).red() > 150, "plain content, no waiting"
        assert engine._pending or engine._inflight, "but the result was asked for"
        export = h.app.canvas.render_items([h.item("Img")])  # export, copy as image
        assert export.pixelColor(50, 30).getRgb()[:3] == (0, 0, 0), "glow edges of a flat image: black"
        engine.wait()
    finally:
        h.close()


def test_slider_drags_process_only_the_last_value():
    h = app()
    try:
        h.add_image("Img", -50, -30, w=100, h=60)
        h.run("effects.add", "blur", "Img")
        h.refresh()
        engine = h.engine
        for radius in (2, 4, 6, 8):
            h.run("effects.set", "Img", "blur", radius=radius)
            h.refresh()
            paint_views(h)
        assert engine.wait()
        assert engine.stats["computed"] == 1, engine.stats
    finally:
        h.close()


def test_processed_effects_are_saved_in_the_scene_and_reused():
    h = app()
    try:
        h.add_image("Img", -50, -30, w=100, h=60)
        h.run("effects.add", "edge_detect", "Img", style="glow")
        h.refresh()
        engine = h.engine
        paint_views(h)
        assert engine.wait() and engine.stats["computed"] == 1
        path = h.work / "board.pxref"
        h.run("save_scene", str(path))
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert len(saved["effect_cache"]) == 1
        entry = next(iter(saved["effect_cache"].values()))
        assert entry["mime"] == "image/webp" and entry["data"]
        # Another computer: no memory, no disk cache. The scene has it.
        engine.disk.clear()
        h.run("open_scene", str(path))
        engine.clear(memory=True, scene=False)
        h.refresh()
        paint_views(h)
        assert engine.wait()
        assert engine.stats["scene"] == 1 and engine.stats["computed"] == 1, engine.stats
        # Removing the effect drops its saved copy at the next save.
        h.run("effects.clear", "Img")
        h.run("save_scene", str(path))
        assert "effect_cache" not in json.loads(path.read_text(encoding="utf-8"))
        # And the setting keeps scenes small.
        h.run("effects.add", "edge_detect", "Img")
        h.refresh()
        paint_views(h)
        engine.wait()
        h.run("effects.save_in_scene", False)
        h.run("save_scene", str(path))
        assert "effect_cache" not in json.loads(path.read_text(encoding="utf-8"))
    finally:
        h.run("effects.save_in_scene", True)
        h.close()


def test_huge_pictures_are_processed_at_a_bounded_size():
    from pixelref.builtins.effects.engine import MAX_SOURCE_PIXELS, EffectEngine

    class Size:
        def __init__(self, w, hgt):
            self._w, self._h = w, hgt

        def width(self):
            return self._w

        def height(self):
            return self._h

    assert EffectEngine.source_scale(Size(4000, 3000)) == 1.0
    scale = EffectEngine.source_scale(Size(16000, 16000))
    assert abs(16000 * scale * 16000 * scale - MAX_SOURCE_PIXELS) < MAX_SOURCE_PIXELS * 0.01


# ---- Animations ---------------------------------------------------------------------------------------


def test_animations_run_cheap_effects_while_playing_unless_every_frame_is_ready():
    h = app()
    try:
        gif = make_gif("fx_anim.gif", 30, 20, [(200, 40, 40), (40, 200, 40), (40, 40, 200)])
        h.create("image", "Anim", path=gif.as_posix(), translate=[0, 0])
        h.run("effects.add", "grayscale", "Anim")
        h.run("effects.add", "edge_detect", "Anim")  # heavy: white paper on these flat frames
        h.refresh()
        item = h.item("Anim")
        assert item.animated
        item.pause()
        item.go_to_frame(1)
        paused = render(h, "Anim")
        assert paused.pixelColor(15, 10).getRgb()[:3] == (255, 255, 255), "paused: the whole stack"
        h.engine.clear(memory=True, scene=False)  # a frame that isn't processed yet
        item.play()
        assert item.is_playing()
        playing = render(h, "Anim")
        colour = playing.pixelColor(15, 10)
        assert gray_of(colour) and colour.red() < 200, "playing: only the cheap grayscale"
        # Process every frame: playback shows the whole stack.
        h.run("effects.all_frames", "Anim", on=True)
        h.refresh()
        assert h.engine.wait()
        assert h.engine.frames["Anim"] == (3, 3)
        item = h.item("Anim")
        item.play()
        playing = render(h, "Anim")
        assert playing.pixelColor(15, 10).getRgb()[:3] == (255, 255, 255)
        item.pause()
    finally:
        h.close()


# ---- The Attribute Editor -----------------------------------------------------------------------------


def test_effects_section_edits_the_stack():
    h = app()
    try:
        h.add_image("Img", 0, 0, w=100, h=60)
        h.create("group", "Group")
        h.select(["Img"])
        APP.processEvents()
        section = h.section("effects")
        assert section is not None and section.isVisible()
        section._fill_add_menu()
        add_blur = next(a for a in section.add_menu.actions() if a.text().startswith("Blur") and not a.isSeparator())
        add_blur.trigger()
        APP.processEvents()
        assert [e["type"] for e in h.attr("Img", "effects")] == ["blur"]
        row = next(iter(section.rows.values()))
        spin = row.editors["radius"][1]
        spin.setValue(12.0)
        spin.editingFinished.emit()  # typed + Enter
        assert h.attr("Img", "effects")[0]["params"]["radius"] == 12.0
        row.enabled.setChecked(False)
        assert h.attr("Img", "effects")[0]["enabled"] == 0
        row.mix.setValue(40.0)
        row.mix.editingFinished.emit()
        assert h.attr("Img", "effects")[0]["mix"] == 0.4
        h.run("undo")
        APP.processEvents()
        assert section.rows and next(iter(section.rows.values())).mix.value() == 100.0, "follows undo"
        section.copy_button.click()
        h.add_image("Other", 200, 0, w=50, h=50)
        h.select(["Other"])
        APP.processEvents()
        h.section("effects").paste_button.click()
        assert [e["type"] for e in h.attr("Other", "effects")] == ["blur"]
        h.select(["Group"])
        APP.processEvents()
        effects = h.section("effects")
        assert effects is None or not effects.isVisible(), "groups and layers: not yet"
        assert h.app.actions.get("effects.add.edge_detect") is not None, "every effect is in command search"
    finally:
        h.close()


# ---- Effects from other plugins ---------------------------------------------------------------------------


PIXELATE = '''
from pixelref.plugin_api import Attr
from pixelref.plugin_api.contracts import EffectType

PIXELATE = EffectType("pixelate", "Pixelate", (
    Attr("size", "float", 8.0, minimum=1.0, maximum=200.0),
), category="Stylize")


def activate(ctx):
    ctx.contribute("effects.types", PIXELATE)


def activate_ui(ui):
    from PySide6.QtCore import Qt

    def pixelate(image, params, fx):
        block = max(1.0, params["size"] * fx.pixels_per_unit)
        small = image.scaled(max(1, int(image.width() / block)), max(1, int(image.height() / block)))
        return small.scaled(image.width(), image.height(), Qt.IgnoreAspectRatio, Qt.FastTransformation)

    ui.contribute("effects.processors", ("pixelate", pixelate))
'''


def test_other_plugins_add_effects():
    folder = tmp_dir()
    plugin = folder / "pixelate_fx"
    plugin.mkdir()
    (plugin / "plugin.toml").write_text('id = "pixelate_fx"\nname = "Pixelate"\nrequires = ["effects"]\n',
                                        encoding="utf-8")
    (plugin / "__init__.py").write_text(PIXELATE, encoding="utf-8")
    h = Harness(plugin_paths=(folder,))
    h.engine = h.service("effects_engine")
    h.engine.disk.folder = tmp_dir()
    try:
        assert h.session.plugins.get("pixelate_fx").active, h.session.plugins.get("pixelate_fx").error
        image = two_tone(64, 32)
        path = tmp_dir() / "two_tone.png"
        image.save(str(path))
        h.create("image", "Img", path=path.as_posix(), translate=[0, 0])
        h.run("effects.add", "pixelate", "Img", size=32)
        h.refresh()
        out = render(h, "Img")
        assert out.pixelColor(31, 16) == out.pixelColor(1, 16), "one 32 px block"
        assert h.app.actions.get("effects.add.pixelate") is not None
        h.session.plugins.set_enabled("pixelate_fx", False)
        h.refresh()
        assert h.app.actions.get("effects.add.pixelate") is None
        assert [e["type"] for e in h.attr("Img", "effects")] == ["pixelate"], "kept while its plugin is off"
        assert render(h, "Img").pixelColor(40, 16) != render(h, "Img").pixelColor(1, 16), "skipped, not drawn"
    finally:
        h.session.settings.remove("plugins/disabled")
        h.session.settings.remove("plugins/enabled")
        h.close()
