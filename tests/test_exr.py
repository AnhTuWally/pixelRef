"""EXR support: header parsing (always), and decoding, layers, the view and the
inspector section (when the OpenEXR and numpy packages are installed)."""

from __future__ import annotations

import importlib.util
import struct

from harness import APP, TMP, make
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from pixelref.builtins.image.source import DECODER
from pixelref.core.imagesize import exr_info, file_image_size, image_size
from pixelref.core.scene import sniff_mime

HAVE_EXR = all(importlib.util.find_spec(m) is not None for m in ("OpenEXR", "numpy"))


def make_exr():
    """An app with EXR decoding (the exr plugin is on by default; its packages must be installed)."""
    h = make()
    assert h.service("exr") is not None, "the exr plugin is on by default"
    assert h.service("image_cache").decoder_for({"path": "x.exr"}) is not None
    return h


def _attr(name: str, kind: str, value: bytes) -> bytes:
    return name.encode() + b"\0" + kind.encode() + b"\0" + struct.pack("<i", len(value)) + value


def _chlist(names: list[str], pixel_type: int = 1) -> bytes:
    return b"".join(n.encode() + b"\0" + struct.pack("<iB3xii", pixel_type, 0, 1, 1) for n in names) + b"\0"


def _minimal_header(width: int, height: int, channels: list[str]) -> bytes:
    """Just the headers of a single-part scanline EXR (what the header parser reads)."""
    box = struct.pack("<iiii", 10, 20, 10 + width - 1, 20 + height - 1)
    return (b"\x76\x2f\x31\x01" + struct.pack("<I", 2)
            + _attr("channels", "chlist", _chlist(channels))
            + _attr("compression", "compression", b"\x03")
            + _attr("dataWindow", "box2i", box)
            + _attr("displayWindow", "box2i", box)
            + _attr("lineOrder", "lineOrder", b"\x00")
            + b"\0")


def _write_exr(name: str, width: int = 64, height: int = 32, red: float = 0.18, layers: bool = True):
    import numpy as np
    import OpenEXR

    rgba = np.zeros((height, width, 4), "f")
    rgba[..., 0] = red
    rgba[..., 3] = 1.0
    channels = {"RGBA": rgba}
    if layers:
        channels.update({"diffuse.R": np.full((height, width), 0.5, "f"),
                         "diffuse.G": np.full((height, width), 0.25, "f"),
                         "diffuse.B": np.zeros((height, width), "f"),
                         "Z": np.full((height, width), 3.0, "f")})
    path = TMP / name
    OpenEXR.File({"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}, channels).write(str(path))
    return path


def _red(image) -> int:
    return (image.pixel(0, 0) >> 16) & 0xFF


# ---- Headers (no OpenEXR needed) ---------------------------------------------------------------


def test_exr_header_gives_size_and_channels_without_openexr():
    data = _minimal_header(320, 180, ["A", "B", "G", "R", "diffuse.R", "diffuse.G", "diffuse.B"])
    assert image_size(data) == (320, 180)
    info = exr_info(data)
    assert info is not None and len(info.parts) == 1
    assert info.parts[0].channels == ["A", "B", "G", "R", "diffuse.R", "diffuse.G", "diffuse.B"]
    assert info.parts[0].data_window == (10, 20, 329, 199)
    assert sniff_mime(data) == "image/x-exr"
    path = TMP / "header_only.exr"
    path.write_bytes(data)
    assert file_image_size(path) == (320, 180)
    assert exr_info(data[:40]) is None  # truncated: no guess


def test_exr_layers_group_channels_by_prefix():
    from pixelref.builtins.exr.decode import layer_table

    info = exr_info(_minimal_header(8, 8, ["A", "B", "G", "R", "Z", "diffuse.B", "diffuse.G", "diffuse.R",
                                            "depth.Z"]))
    table = layer_table(info)
    assert list(table) == ["", "depth", "diffuse", "Z"]
    assert dict(table[""].roles) == {"R": "R", "G": "G", "B": "B", "A": "A"}
    assert dict(table["diffuse"].roles) == {"R": "diffuse.R", "G": "diffuse.G", "B": "diffuse.B"}
    assert dict(table["Z"].roles) == {"Y": "Z"}
    assert dict(table["depth"].roles) == {"Y": "depth.Z"}


def test_exrs_are_linked_even_when_new_images_are_embedded():
    h = make()
    try:
        path = TMP / "linked_default.exr"
        path.write_bytes(_minimal_header(64, 32, ["R", "G", "B"]))
        png = TMP / "embedded_default.png"
        QImage(8, 8, QImage.Format_RGB32).save(str(png))
        assert h.service("image_storage").embed_new
        exr = h.run("image.import_image", path.as_posix(), [0.0, 0.0])
        still = h.run("image.import_image", png.as_posix(), [100.0, 0.0])
        assert h.attr(exr, "path") and not h.attr(exr, "asset"), "EXRs are linked by default"
        assert h.attr(still, "asset"), "other images still follow the embed setting"
        forced = h.run("image.import_image", path.as_posix(), [200.0, 0.0], embed=True)
        assert h.attr(forced, "asset"), "embed=True still embeds an EXR"
    finally:
        h.close()


def test_exr_plugin_is_on_by_default_even_without_its_packages():
    h = make()
    try:
        assert h.service("exr") is not None
        assert h.session.run("packages.needed") == {}, "its packages are optional: never installed unasked"
        decoding = h.service("image_cache").decoder_for({"path": "x.exr"}) is not None
        assert decoding == HAVE_EXR
        assert h.app.actions.get("exr.install") is not None
        if not HAVE_EXR:
            hint = h.service("image_cache").hints["image/x-exr"][1]
            assert "Install EXR Support" in hint
    finally:
        h.close()


def test_an_exr_without_the_plugin_shows_a_card_at_its_real_size():
    h = make()
    try:
        path = TMP / "no_plugin.exr"
        path.write_bytes(_minimal_header(300, 120, ["R", "G", "B"]))
        cache = h.service("image_cache")
        cache_decoders = cache.decoders
        cache.decoders = lambda: []  # as if the exr plugin were off
        try:
            name = h.run("image.import_image", path.as_posix(), [0.0, 0.0], embed=False)
            h.refresh()
            item = h.item(name)
            assert item.image_size() == QSize(300, 120)
            assert item.handle is not None and item.handle.placeholder
            assert item.effect_source() is None
            assert not item.handle.full().isNull()
        finally:
            cache.decoders = cache_decoders
    finally:
        h.close()


# ---- Decoding (OpenEXR + numpy) ----------------------------------------------------------------


def test_exr_view_transform_values():
    if not HAVE_EXR:
        return
    from pixelref.builtins.exr.reader import ExrReader

    path = _write_exr("view.exr")
    assert _red(ExrReader(str(path), {}).read()) == 118  # 0.18 linear is sRGB 118
    assert _red(ExrReader(str(path), {"exposure": 1.0}).read()) == 162  # 0.36
    assert _red(ExrReader(str(path), {"exposure": -20.0}).read()) == 0
    diffuse = ExrReader(str(path), {"exr_layer": "diffuse"}).read()
    assert (diffuse.pixel(0, 0) & 0xFFFFFF) == 0xBC8900  # 0.5, 0.25, 0
    grey = ExrReader(str(path), {"exr_layer": "diffuse", "channel": "g"}).read()
    assert (grey.pixel(0, 0) & 0xFFFFFF) == 0x898989
    alpha = ExrReader(str(path), {"channel": "a"}).read()
    assert (alpha.pixel(0, 0) & 0xFFFFFF) == 0xFFFFFF
    brighter = ExrReader(str(path), {"gamma": 2.2}).read()
    assert _red(brighter) > 118
    missing = ExrReader(str(path), {"exr_layer": "nope"})  # falls back to the beauty
    assert missing.canRead() and _red(missing.read()) == 118


def test_exr_levels_are_box_filtered_to_size():
    if not HAVE_EXR:
        return
    from pixelref.builtins.exr.reader import ExrReader

    path = _write_exr("levels.exr", 256, 128, layers=False)
    reader = ExrReader(str(path), {})
    reader.setScaledSize(QSize(64, 32))
    image = reader.read()
    assert image.width() >= 64 and image.height() >= 32 and image.width() <= 128
    assert _red(image) == 118


def test_exr_float_pixels_are_read_once_per_layer():
    if not HAVE_EXR:
        return
    from pixelref.builtins.exr import decode
    from pixelref.builtins.exr.reader import ExrReader

    path = _write_exr("cached.exr")
    before = decode.FLOATS.reads
    ExrReader(str(path), {}).read()
    ExrReader(str(path), {"exposure": 2.0}).read()
    ExrReader(str(path), {"channel": "luma", "gamma": 1.5}).read()
    assert decode.FLOATS.reads == before + 1
    ExrReader(str(path), {"exr_layer": "diffuse"}).read()
    assert decode.FLOATS.reads == before + 2


def _wait_decoded(h, name: str):
    item = h.item(name)
    h.view.viewport().repaint()
    APP.processEvents()
    DECODER.wait()
    APP.processEvents()
    return item


def test_exr_image_nodes_linked_and_embedded():
    if not HAVE_EXR:
        return
    h = make_exr()
    try:
        path = _write_exr("board.exr")
        linked = h.run("image.import_image", path.as_posix(), [0.0, 0.0], embed=False)
        embedded = h.run("image.import_image", path.as_posix(), [200.0, 0.0], embed=True)
        h.refresh()
        asset = h.scene.get_asset(h.attr(embedded, "asset"))
        assert asset.mime == "image/x-exr"
        for name in (linked, embedded):
            item = _wait_decoded(h, name)
            assert item.image_size() == QSize(64, 32)
            assert not item.handle.placeholder
            assert _red(item.pixmap().toImage()) == 118
        assert h.run("exr.layers", linked) == ["", "diffuse", "Z"]
    finally:
        h.close()


def test_exr_view_commands_are_undoable_and_sparse():
    if not HAVE_EXR:
        return
    h = make_exr()
    try:
        path = _write_exr("cmds.exr")
        name = h.run("image.import_image", path.as_posix(), [0.0, 0.0], embed=False)
        h.refresh()
        assert "exposure" not in h.attrs(name), "view attributes are stored only once changed"
        h.select([name])
        h.run("exr.adjust_exposure", 1.0)
        h.refresh()
        assert h.attr(name, "exposure") == 1.0
        assert _red(_wait_decoded(h, name).pixmap().toImage()) == 162
        h.run("exr.set_view", [name], layer="diffuse", channel="r")
        assert h.attr(name, "exr_layer") == "diffuse" and h.attr(name, "channel") == "r"
        assert h.run("exr.cycle_channel", [name]) == "g"
        h.run("exr.reset_view", [name])
        assert "exposure" not in h.attrs(name) and "exr_layer" not in h.attrs(name)
        h.undo()
        assert h.attr(name, "channel") == "g"
        try:
            h.run("exr.set_view", [name], layer="nope")
            raise AssertionError("an unknown layer should be refused")
        except Exception as exc:  # noqa: BLE001
            assert "no EXR layer" in str(exc)
    finally:
        h.close()


def test_exr_view_section_shows_layers():
    if not HAVE_EXR:
        return
    h = make_exr()
    try:
        path = _write_exr("section.exr")
        name = h.run("image.import_image", path.as_posix(), [0.0, 0.0], embed=False)
        h.refresh()
        h.select([name])
        APP.processEvents()
        section = h.section("exr_view")
        assert section is not None
        labels = [section.layer.itemText(i) for i in range(section.layer.count())]
        assert labels == ["RGBA", "diffuse", "Z"]
        section.layer.setCurrentIndex(1)
        assert h.attr(name, "exr_layer") == "diffuse"
        assert "3 layers" in section.info.text()
    finally:
        h.close()
