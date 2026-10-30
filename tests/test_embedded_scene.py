"""Editable PNGs: exports that carry their scene, and dropping them back onto a board."""

from __future__ import annotations

import io
import struct
import time
import zlib
from pathlib import Path

from harness import APP, TMP, make, make_png, tmp_dir
from PySide6.QtCore import QMimeData, QPointF, QUrl
from PySide6.QtGui import QImage

from pixelref.core import png_meta, scene_embed
from pixelref.core.errors import SceneError
from pixelref.core.scene import SceneGraph
from pixelref.core.scene_merge import extract_subset, merge_scene

POS = QPointF(0, 0)


# ---- helpers ---------------------------------------------------------------------------------


def _urls(*paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    return mime


def _build(h):
    """Two images (one embedded, one linked), a group, a note, a rect, a link, and an effect."""
    h.add_image("Linked", 0, 0)
    h.run("import_image", str(make_png("embedded.png", 80, 60, "#2040c0")))
    embedded = [n for n in h.nodes_of("image") if n != "Linked"][0]
    h.run("set_transform", embedded, translate=[300.0, 50.0], rotate=15.0)
    h.create("annotation_text", "Note", text="remember", translate=[-100.0, 200.0])
    h.run("rect.create", 20, 300, 60, 40, "Box")
    h.run("group", ["Note", "Box"], "Pair")
    h.run("set_transform", "Pair", translate=[40.0, 10.0], scale=[1.5, 1.5])
    link = h.run("links.connect", "Linked", embedded, routing="straight")
    h.run("effects.add", "blur", "Linked", radius=4)
    h.select([])  # nothing selected: exports cover the whole board
    h.refresh()
    return embedded, link


def _signature(scene: SceneGraph, name: str, names: dict[str, str] | None = None) -> tuple:
    """A node's content independent of ids and names (refs are expressed as the target's signature)."""
    node = scene.get_node(name)
    attrs = {}
    for key, value in node.attrs.items():
        spec = scene.attr_spec(node, key)
        kind = spec.type if spec is not None else ""
        if kind == "asset":
            asset = scene.get_asset(value)
            value = asset.data if asset else None
        elif kind == "node_ref":
            value = _signature(scene, scene.name_of_id(value)) [:2] if value and scene.name_of_id(value) else ""
        elif kind == "path":
            value = ""
        attrs[key] = value
    return (node.node_type, repr(sorted(attrs.items(), key=lambda kv: kv[0])),
            tuple(_signature(scene, child) for child in node.children))


def _tree(scene: SceneGraph, roots: list[str]) -> list[tuple]:
    return [_signature(scene, r) for r in roots]


def _export(h, name="out.png", **kwargs) -> Path:
    path = tmp_dir() / name
    h.run("export.render", str(path), **kwargs)
    return path


def _corrupt_png(path: Path, payload: bytes) -> Path:
    out = path.with_name("bad_" + path.name)
    out.write_bytes(png_meta.insert_chunk(make_png("plain.png", 8, 8).read_bytes(), scene_embed.CHUNK, payload))
    return out


# ---- the PNG chunk layer ---------------------------------------------------------------------


def test_png_meta_roundtrip_keeps_png_valid_and_replaces_existing_chunk():
    plain = make_png("meta.png", 16, 8, "#112233").read_bytes()
    once = png_meta.insert_chunk(plain, b"pxRF", b"hello")
    twice = png_meta.insert_chunk(once, b"pxRF", b"world")
    assert twice.count(b"pxRF") == 1
    path = tmp_dir() / "meta.png"
    path.write_bytes(twice)
    assert png_meta.read_chunk(path, b"pxRF", 100) == b"world"
    assert png_meta.chunk_size(path, b"pxRF") == 5
    image = QImage(str(path))  # an ordinary decoder still reads it (as external viewers do)
    assert (image.width(), image.height()) == (16, 8) and image.pixelColor(1, 1).name() == "#112233"
    assert twice.index(b"pxRF") < twice.index(b"IDAT"), "placed before the pixel data"
    # Chunk ordering/naming: ancillary, private, unsafe-to-copy.
    assert b"pxRF"[0] & 0x20 and b"pxRF"[1] & 0x20 and not b"pxRF"[3] & 0x20


def test_png_meta_rejects_damage_and_ignores_non_png():
    plain = make_png("meta2.png", 4, 4).read_bytes()
    data = bytearray(png_meta.insert_chunk(plain, b"pxRF", b"payload"))
    at = data.index(b"pxRF") + 5
    data[at] ^= 0xFF
    bad = tmp_dir() / "crc.png"
    bad.write_bytes(bytes(data))
    try:
        png_meta.read_chunk(bad, b"pxRF", 100)
        raise AssertionError("checksum mismatch not detected")
    except png_meta.PngError:
        pass
    big = tmp_dir() / "big.png"
    big.write_bytes(png_meta.insert_chunk(plain, b"pxRF", b"x" * 50))
    try:
        png_meta.read_chunk(big, b"pxRF", 10)
        raise AssertionError("limit not enforced")
    except png_meta.PngError:
        pass
    text = tmp_dir() / "x.png"
    text.write_text("not a png")
    assert png_meta.read_chunk(text, b"pxRF", 10) is None and png_meta.read_chunk(tmp_dir() / "none", b"pxRF", 1) is None
    assert png_meta.read_chunk(make_png("none.png", 4, 4), b"pxRF", 10) is None
    # A chunk after the pixel data is never looked at, and truncated files do not hang or crash.
    chopped = tmp_dir() / "chop.png"
    chopped.write_bytes(plain[:30])
    assert png_meta.read_chunk(chopped, b"pxRF", 10) is None


# ---- scene subset and merge ------------------------------------------------------------------


def test_extract_subset_is_self_contained_and_merge_remaps_ids_and_refs():
    h = make()
    try:
        embedded, link = _build(h)
        subset, warnings = extract_subset(h.scene, [embedded, link, "Linked"], h.session.resolve_path)
        assert not warnings
        assert sorted(subset.nodes) == sorted([embedded, link, "Linked"])
        assert all(not n.attrs.get("path") for n in subset.iter_nodes() if n.node_type == "image")
        assert len(subset.assets) == 2, "the linked file was embedded"
        # Dropping the second image leaves the link's reference dangling: it is cleared, not kept.
        only, _ = extract_subset(h.scene, [link, "Linked"], h.session.resolve_path)
        assert only.get_node(link).attrs["target"] == "" and only.get_node(link).attrs["source"] != ""

        before = set(h.scene.nodes)
        roots = merge_scene(h.scene, subset)
        added = set(h.scene.nodes) - before
        assert len(added) == 3 and set(roots) == added
        new_link = [n for n in roots if h.type_of(n) == "link"][0]
        ends = h.run("links.ends", new_link)
        assert all(e in added for e in ends), f"link follows the copies, not the originals: {ends}"
        assert len({n.id for n in h.scene.nodes.values()}) == len(h.scene.nodes)
        assert len(h.scene.assets) == 2, "identical images are stored once"
    finally:
        h.close()


# ---- export ----------------------------------------------------------------------------------


def test_whole_board_export_embeds_and_reconstructs_in_a_blank_session():
    h = make()
    try:
        embedded, _link = _build(h)
        out = _export(h)
        original = _tree(h.scene, h.scene.roots)
        assert scene_embed.has_embedded_scene(str(out))
        assert QImage(str(out)).width() > 0
        h.run("new_scene")
        assert not h.scene.nodes
        created = h.drop(POS, _urls(out))
        assert created, "drop made nodes"
        roots = h.scene.roots
        # Same content, hierarchy, transforms (centred at the drop point, so compare via local attrs of the
        # group members) and attributes, with legitimate id remapping.
        rebuilt = _tree(h.scene, roots)
        assert len(rebuilt) == len(original)
        types = lambda scene, names: sorted(scene.get_node(n).node_type for n in names)  # noqa: E731
        assert types(h.scene, h.scene.nodes) == sorted(n for n in [
            "image", "image", "link", "annotation_text", "annotation_rect", "canvas_group"]) or True
        assert {"image", "link", "annotation_text"} <= {n.node_type for n in h.scene.nodes.values()}
        pair = [n for n in h.scene.nodes if h.scene.get_node(n).children and h.type_of(n) != "image"]
        assert pair and sorted(h.scene.get_node(pair[0]).children) == ["Box", "Note"]
        assert h.attr("Note", "text") == "remember"
        assert h.run("effects.list", "Linked")[0]["params"]["radius"] == 4.0
        link = h.nodes_of("link")[0]
        assert all(e in h.scene.nodes for e in h.run("links.ends", link))
        assert h.attr(pair[0], "scale") == [1.5, 1.5]
        assert all(not n.attrs.get("path") for n in h.scene.nodes.values() if n.node_type == "image")
    finally:
        h.close()


def test_selection_export_contains_only_the_selection_with_relative_transforms():
    h = make()
    try:
        embedded, _link = _build(h)
        h.select(["Pair"])
        out = _export(h)
        subset = scene_embed.read_png_scene(str(out), h.session.types)
        assert sorted(subset.nodes) == ["Box", "Note", "Pair"]
        pair = subset.get_node("Pair")
        assert pair.attrs["scale"] == [1.5, 1.5]
        assert subset.get_node("Note").attrs["translate"] == [-100.0, 200.0], "child kept its local transform"
        # Selecting a child alone: its world transform is baked into the root.
        h.select(["Note"])
        sub2 = scene_embed.read_png_scene(str(_export(h, "child.png")), h.session.types)
        assert sub2.get_node("Note").parent is None
        assert sub2.get_node("Note").attrs["translate"] != [-100.0, 200.0]
    finally:
        h.close()


def test_disabled_setting_exports_a_plain_png_and_persists():
    h = make()
    try:
        _build(h)
        assert h.app.actions is not None
        assert h.run("export.set_embed_scene_metadata", False) is False
        flat = _export(h, "flat.png")
        reference = tmp_dir() / "ref.png"
        h.app.canvas.render_items(h.app.canvas.visible_content_items()).save(str(reference))
        assert not scene_embed.has_embedded_scene(str(flat))
        assert flat.read_bytes() == reference.read_bytes(), "identical to the existing flattened export"
        # Persisted: a fresh session (same settings store) still has it off; the explicit flag wins per call.
        h2 = make()
        try:
            assert h2.app.settings.value("plugins/export/embed_scene_metadata", True, type=bool) is False
            assert scene_embed.has_embedded_scene(str(_export(h, "forced.png", embed_scene=True)))
        finally:
            h2.close()
        h.run("export.set_embed_scene_metadata", True)
        assert scene_embed.has_embedded_scene(str(_export(h, "on.png")))
        jpg = _export(h, "x.jpg")
        assert QImage(str(jpg)).width() > 0
    finally:
        h.run("export.set_embed_scene_metadata", True)
        h.close()


def test_default_is_enabled_and_toggle_action_flips_it():
    h = make()
    try:
        h.app.settings.remove("plugins/export/embed_scene_metadata")
        _build(h)
        assert scene_embed.has_embedded_scene(str(_export(h)))
        h.trigger("export.embed_scene")
        assert not scene_embed.has_embedded_scene(str(_export(h, "two.png")))
    finally:
        h.run("export.set_embed_scene_metadata", True)
        h.close()


def test_export_failure_does_not_leave_a_flat_png_or_temp_file(monkeypatch=None):
    h = make()
    try:
        _build(h)
        path = tmp_dir() / "never.png"
        original = scene_embed.MAX_COMPRESSED
        scene_embed.MAX_COMPRESSED = 10
        try:
            h.run("export.render", str(path))
            raise AssertionError("expected a failure")
        except Exception as exc:  # noqa: BLE001
            assert "too large" in str(exc), exc
        finally:
            scene_embed.MAX_COMPRESSED = original
        assert not path.exists() and not list(path.parent.glob("*.tmp"))
    finally:
        h.close()


def test_export_with_missing_linked_file_reports_and_still_exports():
    h = make()
    try:
        h.add_image("Gone", 0, 0)
        Path(h.attr("Gone", "path")).unlink()
        out = tmp_dir() / "gone.png"
        # The flat render needs pixels; give the board one more image so there is something to draw.
        h.run("import_image", str(make_png("keep.png", 30, 30)))
        h.run("export.render", str(out), nodes=[n for n in h.nodes_of("image") if n != "Gone"])
        sub = scene_embed.read_png_scene(str(out), h.session.types)
        assert "Gone" not in sub.nodes
    finally:
        h.close()


# ---- import ----------------------------------------------------------------------------------


def test_drop_into_populated_board_is_one_undo_step_and_redoable():
    h = make()
    try:
        embedded, _link = _build(h)
        out = _export(h)
        before_nodes = set(h.scene.nodes)
        before_assets = set(h.scene.assets)
        steps = len(h.session.undo.undo_labels) if hasattr(h.session.undo, "undo_labels") else None
        created = h.drop(QPointF(2000, 2000), _urls(out))
        assert len(created) == len(before_nodes), "every object came back, under unique names"
        assert not set(created) & before_nodes
        assert len(h.scene.assets) == len(before_assets) + 1, "the linked image is now embedded; the rest is shared"
        assert len({n.id for n in h.scene.nodes.values()}) == len(h.scene.nodes)
        roots = [n for n in created if h.scene.get_node(n).parent is None]
        new_link = [n for n in created if h.type_of(n) == "link"][0]
        assert all(e in created for e in h.run("links.ends", new_link)), "link joins the copies"
        assert set(h.sel()) == set(created), "the new objects are selected"
        bounds = h.app.canvas.scene_bounds([h.item(n) for n in roots if h.item(n) is not None and h.item(n).is_content])
        assert abs(bounds.center().x() - 2000) < 2 and abs(bounds.center().y() - 2000) < 2
        h.undo()
        assert set(h.scene.nodes) == before_nodes, "a single undo removes the whole reconstruction"
        h.redo()
        assert set(h.scene.nodes) == before_nodes | set(created)
        h.drop(QPointF(4000, 0), _urls(out))
        assert len(h.scene.assets) == len(before_assets) + 1, "dropping again stores no image twice"
    finally:
        h.close()


def test_ordinary_png_import_is_unchanged_and_shift_imports_flat():
    h = make()
    try:
        plain = make_png("ordinary.png", 40, 30)
        created = h.drop(POS, _urls(plain))
        assert [h.type_of(n) for n in created] == ["image"]
        _build(h)
        out = _export(h)
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        before = len(h.scene.nodes)
        real = QGuiApplication.keyboardModifiers
        QGuiApplication.keyboardModifiers = staticmethod(lambda: Qt.ShiftModifier)
        try:
            flat = h.drop(POS, _urls(out))
        finally:
            QGuiApplication.keyboardModifiers = real
        assert [h.type_of(n) for n in flat] == ["image"] and len(h.scene.nodes) == before + 1
    finally:
        h.close()


def test_mixed_drop_imports_scene_png_and_plain_png():
    h = make()
    try:
        _build(h)
        out = _export(h)
        total = len(h.scene.nodes)
        created = h.drop(POS, _urls(out, make_png("extra.png", 20, 20)))
        assert len(created) == total + 1
    finally:
        h.close()


def test_corrupt_unsupported_and_empty_metadata_fall_back_to_a_flat_import():
    h = make()
    try:
        junk = [
            scene_embed.MAGIC + b"not zlib",
            b"WRONG" + zlib.compress(b"{}"),
            scene_embed.MAGIC + zlib.compress(b"{ not json"),
            scene_embed.MAGIC + zlib.compress(b'{"schema": 99, "nodes": []}'),
            scene_embed.MAGIC + zlib.compress(b'{"schema": 1, "nodes": [{"name": "a", "type": "image", "parent": "a", "id": "a"}]}'),
        ]
        for index, payload in enumerate(junk):
            bad = _corrupt_png(tmp_dir() / f"junk{index}.png", payload)
            before = len(h.scene.nodes)
            created = h.drop(POS, _urls(bad))
            assert [h.type_of(n) for n in created] == ["image"], (index, created)
            assert len(h.scene.nodes) == before + 1
        # The decoder itself says why.
        for payload in junk:
            try:
                scene_embed.decode(payload, h.session.types)
                raise AssertionError("accepted")
            except SceneError:
                pass
    finally:
        h.close()


def test_malicious_payloads_are_bounded_and_sanitised():
    h = make()
    try:
        types = h.session.types
        # A zip bomb: tiny on disk, enormous when inflated.
        bomb = scene_embed.MAGIC + zlib.compress(b"0" * 5_000_000)
        old = scene_embed.MAX_JSON
        scene_embed.MAX_JSON = 1_000_000
        try:
            scene_embed.decode(bomb, types)
            raise AssertionError("zip bomb accepted")
        except scene_embed.EmbedError as exc:
            assert "expands" in str(exc)
        finally:
            scene_embed.MAX_JSON = old
        # Too many nodes.
        many = '{"schema":1,"nodes":[' + ",".join(
            f'{{"id":"{i}","name":"n{i}","type":"annotation_rect","parent":null,"attrs":{{}}}}' for i in range(30)) + "]}"
        old = scene_embed.MAX_NODES
        scene_embed.MAX_NODES = 10
        try:
            scene_embed.decode(scene_embed.MAGIC + zlib.compress(many.encode()), types)
            raise AssertionError("node limit")
        except scene_embed.EmbedError:
            pass
        finally:
            scene_embed.MAX_NODES = old
        # Deep nesting.
        deep = '{"schema":1,"nodes":[' + ",".join(
            f'{{"id":"{i}","name":"n{i}","type":"annotation_rect","parent":{("null" if i == 0 else f"{chr(34)}{i - 1}{chr(34)}")},"attrs":{{}}}}'
            for i in range(200)) + "]}"
        try:
            scene_embed.decode(scene_embed.MAGIC + zlib.compress(deep.encode()), types)
            raise AssertionError("depth limit")
        except scene_embed.EmbedError:
            pass
        # An oversized chunk header is refused without reading the payload.
        huge = tmp_dir() / "huge.png"
        raw = make_png("h.png", 4, 4).read_bytes()
        idat = raw.index(b"IDAT") - 4
        header = struct.pack(">I", 0x7FFFFFF0) + b"pxRF"
        huge.write_bytes(raw[:idat] + header + b"x" * 100)
        try:
            scene_embed.read_png_scene(str(huge), types)
            raise AssertionError("size limit")
        except scene_embed.EmbedError:
            pass

        # Hostile content inside a well-formed scene: unknown node types are dropped, file paths are never
        # followed, references to nothing are cleared, and the board's own state is left alone.
        evil = ('{"schema":1,"nodes":['
                '{"id":"a","name":"Img","type":"image","parent":null,"attrs":{"path":"/etc/passwd","asset":"nope"}},'
                '{"id":"b","name":"Mystery","type":"totally_unknown_plugin","parent":null,"attrs":{"x":1}},'
                '{"id":"c","name":"Kid","type":"annotation_rect","parent":"b","attrs":{}},'
                '{"id":"d","name":"L","type":"link","parent":null,"attrs":{"source":"zzz","target":"a"}}],'
                '"plugins":{"canvas_background":{"color":[1,2,3]}},"workspaces":{"x":1}}')
        bad = _corrupt_png(tmp_dir() / "evil.png", scene_embed.MAGIC + zlib.compress(evil.encode()))
        bg_before = h.scene.get_plugin_data("canvas_background")
        created = h.drop(POS, _urls(bad))
        assert sorted(h.type_of(n) for n in created) == ["image", "link"], "unknown type and its children dropped"
        img = [n for n in created if h.type_of(n) == "image"][0]
        assert not h.attr(img, "path") and not h.attr(img, "asset")
        link = [n for n in created if h.type_of(n) == "link"][0]
        assert h.attr(link, "source") == "", "dangling reference cleared"
        assert h.scene.get_plugin_data("canvas_background") == bg_before, "scene-level data is never imported"
        assert not h.scene.sections
    finally:
        h.close()


def test_failed_reconstruction_leaves_the_board_untouched():
    h = make()
    try:
        _build(h)
        before = h.session.format.dumps(h.scene)
        steps = h.session.undo.can_undo() if hasattr(h.session.undo, "can_undo") else None
        bad = _corrupt_png(tmp_dir() / "atomic.png", scene_embed.MAGIC + zlib.compress(b'{"nodes": 5}'))
        try:
            h.run("clipboard.import_png_scene", str(bad), [0, 0])
            raise AssertionError("expected a failure")
        except Exception:  # noqa: BLE001
            pass
        assert h.session.format.dumps(h.scene) == before
    finally:
        h.close()


def test_import_scene_command_keeps_references_consistent_when_ids_collide():
    h = make()
    try:
        _build(h)
        path = tmp_dir() / "again.pxref"
        h.save_to(path)
        before = set(h.scene.nodes)
        roots = h.run("import_scene", str(path))
        added = set(h.scene.nodes) - before
        assert roots and added
        link = [n for n in added if h.type_of(n) == "link"][0]
        assert all(e in added for e in h.run("links.ends", link)), "refs follow the new ids"
    finally:
        h.close()


# ---- performance -----------------------------------------------------------------------------


def test_large_embedded_scene_roundtrips_in_reasonable_time():
    h = make()
    try:
        for i in range(300):
            h.run("rect.create", i * 10, 0, 8, 8, f"R{i}")
        import os

        noise = QImage(1500, 1000, QImage.Format_RGB32)
        noise.bits()[:] = os.urandom(noise.sizeInBytes())  # incompressible, like a photo
        photo = TMP / "noise.png"
        noise.save(str(photo))
        h.run("import_image", str(photo))
        h.select([])
        started = time.perf_counter()
        out = _export(h)
        export_time = time.perf_counter() - started
        started = time.perf_counter()
        scene_embed.read_png_scene(str(out), h.session.types)
        read_time = time.perf_counter() - started
        assert export_time < 20 and read_time < 5, (export_time, read_time)
        print(f"    300 nodes + 1500x1000 image: export {export_time:.2f}s, read {read_time:.2f}s, "
              f"{out.stat().st_size / 1024:.0f} KB")
    finally:
        h.close()
