"""Colour management (the ocio plugin): configs, displays, the EXR view through OCIO, the
settings' sources (board, preferences, $OCIO, built-in) and the Colour panel."""

from __future__ import annotations

import importlib.util
import os

from harness import APP, TMP, make
from test_exr import _red, _wait_decoded, _write_exr

HAVE_OCIO = all(importlib.util.find_spec(m) is not None for m in ("PyOpenColorIO", "numpy", "OpenEXR"))


def _fresh(h):
    """The harness's app with the ocio preferences cleared (they persist between tests)."""
    api = h.service("color")
    for name in ("mode", "config", "display", "view", "look", "input"):
        api.prefs.remove(name)
    api._prefs = None
    api.changed()
    return api


def _reference(red: float, input_space: str, view: str = "") -> int:
    """What OCIO itself makes of the test EXR's pixel (``red``, 0, 0) on the built-in config's
    default display: its 8-bit red."""
    import numpy as np
    import PyOpenColorIO as ocio

    config = ocio.Config.CreateFromFile("ocio://default")
    display = config.getDefaultDisplay()
    transform = ocio.DisplayViewTransform(src=input_space, display=display,
                                          view=view or config.getDefaultView(display))
    pixel = np.zeros((1, 1, 4), np.float32)
    pixel[..., 0] = red
    pixel[..., 3] = 1.0
    config.getProcessor(transform).getDefaultCPUProcessor().applyRGBA(pixel)
    return int(np.clip(pixel[0, 0, 0] * 255.0 + 0.5, 0, 255))


def test_ocio_is_off_by_default_without_the_env_var():
    h = make()
    try:
        api = _fresh(h)
        if os.environ.get("OCIO"):
            return
        assert api.settings()["mode"] == "builtin"
        assert api.display() is None and api.key() == ("builtin",)
        assert h.session.run("packages.needed") == {}, "its packages are optional: never installed unasked"
        assert h.app.actions.get("ocio.install") is not None
    finally:
        _fresh(h)  # later tests expect the default (built-in) view
        h.close()


def test_display_snapshot_from_the_builtin_config():
    if not HAVE_OCIO:
        return
    from pixelref.builtins.ocio import config as cfg

    display = cfg.make_display(cfg.BUILTIN, "nope", "nope", "nope", "nope")
    config = display.config
    assert display.display == config.getDefaultDisplay(), "unknown names fall back to the config's defaults"
    assert display.view == config.getDefaultView(display.display)
    assert display.look == "" and display.input == ""
    assert display.input_for("ACEScg") == "ACEScg"
    assert display.input_for("nope", "") == cfg.scene_linear(config)
    raw = cfg.make_display(cfg.BUILTIN, view="Raw")
    assert raw.key != display.key
    assert raw.processor("ACEScg") is raw.processor("ACEScg"), "processors are cached"
    looks = cfg.looks(config)
    if looks:
        looked = cfg.make_display(cfg.BUILTIN, look=looks[0])
        assert looked.look == looks[0] and looked.key != display.key
        assert looked.processor("ACEScg") is not None
    try:
        cfg.load_config(str(TMP / "missing.ocio"))
        raise AssertionError("a missing config should raise")
    except ValueError as exc:
        assert "not found" in str(exc)


def test_exr_view_through_ocio():
    if not HAVE_OCIO:
        return
    from pixelref.builtins.exr.reader import ExrReader
    from pixelref.builtins.ocio import config as cfg

    path = _write_exr("ocio_view.exr")
    display = cfg.make_display(cfg.BUILTIN, input="ACEScg")
    assert _red(ExrReader(str(path), {}, display).read()) == _reference(0.18, "ACEScg")
    raw = cfg.make_display(cfg.BUILTIN, view="Raw")
    assert _red(ExrReader(str(path), {"colorspace": "ACEScg"}, raw).read()) == 46, "Raw: the linear value as is"
    assert _red(ExrReader(str(path), {}, None).read()) == 118, "no display: the built-in sRGB curve"
    alpha = ExrReader(str(path), {"channel": "a"}, display).read()
    assert (alpha.pixel(0, 0) & 0xFFFFFF) == 0xFFFFFF, "alpha is shown raw"


def test_board_switches_to_ocio_and_back():
    if not HAVE_OCIO:
        return
    h = make()
    try:
        api = _fresh(h)
        path = _write_exr("ocio_board.exr")
        name = h.run("image.import_image", path.as_posix(), [0.0, 0.0], embed=False)
        h.refresh()
        assert _red(_wait_decoded(h, name).pixmap().toImage()) == 118
        h.run("ocio.set", mode="ocio", input="ACEScg")
        h.refresh()
        assert api.display() is not None
        assert _red(_wait_decoded(h, name).pixmap().toImage()) == _reference(0.18, "ACEScg")
        h.run("ocio.set", view="Raw")
        h.refresh()
        assert _red(_wait_decoded(h, name).pixmap().toImage()) == 46
        h.run("exr.set_view", [name], colorspace="ACES2065-1")
        assert h.attr(name, "colorspace") == "ACES2065-1"
        try:
            h.run("exr.set_view", [name], colorspace="nope")
            raise AssertionError("an unknown colour space should be refused")
        except Exception as exc:  # noqa: BLE001
            assert "no colour space" in str(exc)
        h.run("exr.reset_view", [name])
        assert "colorspace" not in h.attrs(name)
        h.run("ocio.set", mode="builtin")
        h.refresh()
        assert _red(_wait_decoded(h, name).pixmap().toImage()) == 118
        info = h.run("ocio.info")
        assert info["source"] == "preferences" and info["mode"] == "builtin"
        try:
            h.run("ocio.set", display="nope")
            raise AssertionError("an unknown display should be refused")
        except Exception as exc:  # noqa: BLE001
            assert "No display" in str(exc)
    finally:
        _fresh(h)  # later tests expect the default (built-in) view
        h.close()


def test_settings_saved_with_the_board_win_and_round_trip():
    if not HAVE_OCIO:
        return
    h = make()
    try:
        api = _fresh(h)
        h.run("ocio.set", mode="ocio", view="Raw")
        assert h.run("ocio.save_with_board", True)
        h.run("ocio.set", input="ACEScg")  # goes to the board now
        assert api.preferences()["input"] == "", "the preferences are left alone"
        assert api.settings()["input"] == "ACEScg"
        target = TMP / "ocio_board.pxref"
        assert h.save_to(target)
        saved = h.saved_json(target)
        assert "ocio" in str(saved), "the colour settings are in the file"
        h.undo()
        assert api.settings()["input"] == "", "board changes are undoable"
        h.run("ocio.save_with_board", False)
        h.run("ocio.set", mode="builtin")
        assert h.open(target)
        assert api.saved_with_scene() and api.settings()["mode"] == "ocio"
        assert api.display() is not None and api.display().view == "Raw"
    finally:
        _fresh(h)  # later tests expect the default (built-in) view
        h.close()


def test_missing_config_falls_back_and_env_var_is_used():
    if not HAVE_OCIO:
        return
    h = make()
    previous = os.environ.get("OCIO")
    try:
        api = _fresh(h)
        h.scene.set_plugin_data("ocio", {"mode": "ocio", "config": str(TMP / "gone" / "config.ocio")})
        api.changed()
        display = api.display()
        assert display is not None, "a board's missing config falls back instead of failing"
        assert "not found" in api.error and display.config_spec == "ocio://default"
        h.scene.set_plugin_data("ocio", None)
        os.environ["OCIO"] = "ocio://studio-config-latest"
        api._prefs = None
        api.changed()
        assert api.settings()["mode"] == "ocio", "$OCIO turns colour management on"
        assert api.display().config_spec == "ocio://studio-config-latest"
    finally:
        if previous is None:
            os.environ.pop("OCIO", None)
        else:
            os.environ["OCIO"] = previous
        h.scene.set_plugin_data("ocio", None)
        _fresh(h)
        h.close()


def test_open_a_config_file_with_the_file_dialog():
    if not HAVE_OCIO:
        return
    import PyOpenColorIO as ocio
    from PySide6.QtWidgets import QFileDialog

    good = TMP / "picked" / "studio.ocio"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_text(ocio.Config.CreateFromFile("ocio://default").serialize(), encoding="utf-8")
    bad = TMP / "picked" / "broken.ocio"
    bad.write_text("not: [a config", encoding="utf-8")
    h = make()
    chooser = QFileDialog.getOpenFileName
    seen: dict[str, str] = {}

    def choose(path):
        def fake(_parent, _title, folder, _filter):
            seen["folder"] = folder
            return (str(path), "") if path else ("", "")
        QFileDialog.getOpenFileName = staticmethod(fake)

    try:
        api = _fresh(h)
        choose(good)
        assert h.run("ocio.pick_config") == os.path.normpath(str(good))
        settings = api.settings()
        assert settings["mode"] == "ocio", "opening a config turns OCIO on"
        assert settings["config"] == os.path.normpath(str(good))
        assert api.display() is not None and api.display().config_spec == settings["config"]
        assert not api.error
        choose(bad)
        assert h.run("ocio.pick_config") == "", "a file that isn't a config is refused"
        assert seen["folder"] == str(good.parent), "the dialog opens next to the config in use"
        assert api.settings()["config"] == os.path.normpath(str(good)), "and the config in use is kept"
        choose(None)
        assert h.run("ocio.pick_config") == "", "cancelled"
        assert api.settings()["config"] == os.path.normpath(str(good))
        h.select([])
        APP.processEvents()
        from pixelref.builtins.ocio.ui import ColorPanel

        panel = h.inspector.findChildren(ColorPanel)[0]
        assert panel.config.currentData() == os.path.normpath(str(good)), "the panel lists the opened file"
        assert panel.browse.isVisibleTo(panel), "the Browse… button is next to the config"
    finally:
        QFileDialog.getOpenFileName = chooser
        api.prefs.remove("last_config_dir")
        _fresh(h)
        h.close()


def test_colour_panel_shows_when_nothing_is_selected():
    h = make()
    try:
        api = _fresh(h)
        h.select([])
        APP.processEvents()
        from pixelref.builtins.ocio.ui import ColorPanel

        panels = h.inspector.findChildren(ColorPanel)
        assert panels, "the Colour panel is in the empty Attribute Editor"
        panel = panels[0]
        assert panel.mode.currentData() == api.settings()["mode"]
        if HAVE_OCIO:
            panel.mode.setCurrentIndex(panel.mode.findData("ocio"))
            assert api.settings()["mode"] == "ocio"
            APP.processEvents()
            assert panel.view.count() > 1 and panel.display.count() >= 1
            panel.view.setCurrentIndex(panel.view.findData("Raw"))
            assert api.settings()["view"] == "Raw"
    finally:
        _fresh(h)  # later tests expect the default (built-in) view
        h.close()
