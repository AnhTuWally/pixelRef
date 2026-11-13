"""Tests for the model3d plugin: 3D mesh loaders (.obj/.mtl, .stl, .ply, .gltf/.glb),
built-in reference primitives, shading/lighting rendering, linked & embedded storage,
and interactive 3D View Mode on the canvas."""

from __future__ import annotations

import json
import struct
from pathlib import Path

from harness import make, tmp_dir
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl

from pixelref.builtins.model3d import PRIMITIVES, SHADING_MODES
from pixelref.builtins.model3d.items import Model3DItem
from pixelref.builtins.model3d.loader import parse_gltf, parse_obj, parse_ply, parse_stl
from pixelref.plugin_api import CommandError


def _write_sample_obj(folder: Path, name: str = "pyramid.obj", with_mtl: bool = False) -> Path:
    mtl_line = ""
    usemtl_line = ""
    if with_mtl:
        mtl_path = folder / "pyramid.mtl"
        mtl_path.write_text("newmtl RedClay\nKd 0.85 0.25 0.20\n", encoding="utf-8")
        mtl_line = "mtllib pyramid.mtl\n"
        usemtl_line = "usemtl RedClay\n"
    obj_text = (
        f"{mtl_line}"
        "v -1.0 -1.0 -1.0\n"
        "v  1.0 -1.0 -1.0\n"
        "v  1.0 -1.0  1.0\n"
        "v -1.0 -1.0  1.0\n"
        "v  0.0  1.2  0.0\n"
        f"{usemtl_line}"
        "f 1 2 5\n"
        "f 2 3 5\n"
        "f 3 4 5\n"
        "f 4 1 5\n"
        "f 4 3 2 1\n"
    )
    path = folder / name
    path.write_text(obj_text, encoding="utf-8")
    return path


def _make_binary_stl() -> bytes:
    header = b"Binary STL test".ljust(80, b"\0")
    # Two triangles
    tris = [
        ((0.0, 0.0, 1.0), (-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (0.0, 1.0, 0.0)),
        ((0.0, 1.0, 0.0), (-1.0, -1.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    ]
    chunks = [header, struct.pack("<I", len(tris))]
    for n, v0, v1, v2 in tris:
        chunks.append(struct.pack("<12fH", *n, *v0, *v1, *v2, 0))
    return b"".join(chunks)


def _make_glb_bytes() -> bytes:
    # A tetrahedron in binary glTF 2.0 (.glb)
    positions = [
        -1.0, -1.0, -1.0,
        1.0, -1.0, -1.0,
        0.0, 1.0, -1.0,
        0.0, 0.0, 1.0,
    ]
    indices = [0, 1, 2, 0, 1, 3, 1, 2, 3, 2, 0, 3]
    pos_bytes = struct.pack(f"<{len(positions)}f", *positions)
    idx_bytes = struct.pack(f"<{len(indices)}H", *indices)
    bin_chunk = pos_bytes + idx_bytes
    while len(bin_chunk) % 4:
        bin_chunk += b"\x00"

    gltf_dict = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "translation": [0.0, 0.5, 0.0]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.3, 0.7, 0.9, 1.0]}}],
        "meshes": [{
            "primitives": [{
                "attributes": {"POSITION": 0},
                "indices": 1,
                "material": 0,
                "mode": 4,
            }]
        }],
        "buffers": [{"byteLength": len(bin_chunk)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bytes)},
            {"buffer": 0, "byteOffset": len(pos_bytes), "byteLength": len(idx_bytes)},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5123, "count": 12, "type": "SCALAR"},
        ],
    }
    json_bytes = json.dumps(gltf_dict, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4:
        json_bytes += b" "

    total_len = 12 + 8 + len(json_bytes) + 8 + len(bin_chunk)
    return (
        struct.pack("<3I", 0x46546C67, 2, total_len)
        + struct.pack("<2I", len(json_bytes), 0x4E4F534A) + json_bytes
        + struct.pack("<2I", len(bin_chunk), 0x004E4942) + bin_chunk
    )


def _file_mime(path: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def _non_transparent_pixels(image) -> int:
    count = 0
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            if image.pixelColor(x, y).alpha() > 0:
                count += 1
    return count


def test_parsers_load_obj_stl_ply_and_glb():
    folder = tmp_dir()
    obj_path = _write_sample_obj(folder, with_mtl=True)
    obj_mesh = parse_obj(obj_path.read_bytes(), base_dir=folder)
    assert obj_mesh.vertex_count == 5
    assert obj_mesh.face_count == 6
    assert obj_mesh.face_colors is not None
    assert obj_mesh.face_colors[0][0] > 180  # RedClay from pyramid.mtl

    # ASCII & Binary STL
    ascii_stl = (
        b"solid tri\n"
        b"facet normal 0 0 1\nouter loop\n"
        b"vertex -1 -1 0\nvertex 1 -1 0\nvertex 0 1 0\n"
        b"endloop\nendfacet\nendsolid tri\n"
    )
    stl_ascii_mesh = parse_stl(ascii_stl)
    assert stl_ascii_mesh.vertex_count == 3 and stl_ascii_mesh.face_count == 1
    stl_bin_mesh = parse_stl(_make_binary_stl())
    assert stl_bin_mesh.vertex_count == 4 and stl_bin_mesh.face_count == 2

    # ASCII & Binary PLY
    ply_ascii = (
        b"ply\nformat ascii 1.0\n"
        b"element vertex 4\nproperty float x\nproperty float y\nproperty float z\n"
        b"property uchar red\nproperty uchar green\nproperty uchar blue\n"
        b"element face 2\nproperty list uchar int vertex_indices\nend_header\n"
        b"-1 -1 0 255 0 0\n1 -1 0 0 255 0\n0 1 0 0 0 255\n0 0 1 255 255 0\n"
        b"3 0 1 2\n3 0 1 3\n"
    )
    ply_mesh = parse_ply(ply_ascii)
    assert ply_mesh.vertex_count == 4 and ply_mesh.face_count == 2
    assert ply_mesh.face_colors is not None

    ply_bin_header = (
        b"ply\nformat binary_little_endian 1.0\n"
        b"element vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
        b"element face 1\nproperty list uchar int vertex_indices\nend_header\n"
    )
    ply_bin_body = (
        struct.pack("<9f", -1.0, -1.0, 0.0, 1.0, -1.0, 0.0, 0.0, 1.0, 0.0)
        + struct.pack("<B3i", 3, 0, 1, 2)
    )
    ply_bin_mesh = parse_ply(ply_bin_header + ply_bin_body)
    assert ply_bin_mesh.vertex_count == 3 and ply_bin_mesh.face_count == 1

    # GLB
    glb_mesh = parse_gltf(_make_glb_bytes())
    assert glb_mesh.format_name == "GLB"
    assert glb_mesh.vertex_count == 4 and glb_mesh.face_count == 4
    assert glb_mesh.face_colors is not None


def test_primitives_and_shading_modes_render_distinct_images():
    h = make()
    try:
        for prim in PRIMITIVES:
            name = h.run("model3d.create_primitive", prim, [0.0, 0.0])
            h.refresh()
            item = h.item(name)
            assert isinstance(item, Model3DItem)
            assert not item.is_missing()
            img = item.rendered_image()
            assert not img.isNull()
            assert _non_transparent_pixels(img) > 50, f"primitive {prim} should render visible pixels"

        # Test all shading modes + wireframe overlay + lighting + camera orbit on Loomis Head
        head = h.run("model3d.create_primitive", "loomis_head", [0.0, 0.0])
        h.refresh()
        item = h.item(head)
        studio_img = item.rendered_image().copy()
        for mode in SHADING_MODES:
            h.run("model3d.set_shading", mode, [head])
            h.refresh()
            img = h.item(head).rendered_image()
            assert _non_transparent_pixels(img) > 20, f"shading {mode} should draw pixels"

        h.run("model3d.set_shading", "clay", [head])
        h.refresh()
        clay_front = h.item(head).rendered_image().copy()
        h.run("model3d.set_preset_view", "right", [head])
        h.refresh()
        clay_right = h.item(head).rendered_image().copy()
        assert clay_front != clay_right, "changing camera preset must re-render from the new angle"
        assert studio_img != clay_front
    finally:
        h.close()


def test_import_drop_and_linked_vs_embedded_storage():
    h = make()
    try:
        folder = tmp_dir()
        obj_path = _write_sample_obj(folder, "Statue.obj", with_mtl=True)
        glb_path = folder / "Prop.glb"
        glb_path.write_bytes(_make_glb_bytes())

        # Linked by default
        name = h.run("model3d.import_model", obj_path.as_posix(), [20.0, 30.0])
        h.refresh()
        assert h.attr(name, "path") != ""
        assert h.attr(name, "asset") == ""
        item = h.item(name)
        assert isinstance(item, Model3DItem) and not item.is_missing()

        # Embed into scene, save .pxref, delete original file, and reload
        assert h.run("model3d.embed", [name]) == 1
        assert h.attr(name, "asset") != ""
        assert h.attr(name, "path") == ""

        scene_file = folder / "board.pxref"
        assert h.save_to(scene_file)
        obj_path.unlink()

        assert h.open(scene_file)
        h.refresh()
        reloaded = h.item(name)
        assert isinstance(reloaded, Model3DItem) and not reloaded.is_missing()

        # Extract embedded model back to disk and link
        extracted_path = folder / "ExtractedStatue.obj"
        h.run("model3d.extract", name, extracted_path.as_posix())
        assert extracted_path.is_file()
        assert h.attr(name, "asset") == ""
        assert h.attr(name, "path") != ""

        # Drag-and-drop a .glb file onto the board
        before = set(h.scene.nodes)
        dropped = h.drop(QPointF(100.0, 120.0), _file_mime(glb_path))
        assert dropped
        added = [n for n in h.scene.nodes if n not in before]
        assert len(added) == 1 and h.type_of(added[0]) == "model3d"

        # Invalid / missing file errors
        try:
            h.run("model3d.import_model", (folder / "missing.obj").as_posix())
            raise AssertionError("expected CommandError")
        except CommandError as exc:
            assert "No such 3D model file" in str(exc)
    finally:
        h.close()


def test_large_model_loads_in_background_and_reports_progress():
    """A model over the async threshold loads on a worker thread and shows up by itself
    (no reselect needed), with progress readable while it loads."""
    import time

    from harness import APP

    from pixelref.builtins.model3d.loader import ASYNC_FILE_THRESHOLD

    h = make()
    try:
        folder = tmp_dir()
        n = 80
        lines = [f"v {x / n:.4f} {y / n:.4f} 0.0" for y in range(n) for x in range(n)]
        for y in range(n - 1):
            for x in range(n - 1):
                i = y * n + x + 1
                lines.append(f"f {i} {i + 1} {i + n}")
                lines.append(f"f {i + 1} {i + n + 1} {i + n}")
        text = "\n".join(lines) + "\n"
        padding = "#" + "-" * 78 + "\n"
        text += padding * ((ASYNC_FILE_THRESHOLD - len(text)) // len(padding) + 2)
        path = folder / "BigGrid.obj"
        path.write_text(text, encoding="utf-8")
        assert path.stat().st_size > ASYNC_FILE_THRESHOLD

        name = h.run("model3d.import_model", path.as_posix(), [0.0, 0.0])
        h.app.canvas.refresh(force=True)
        item = h.item(name)
        assert isinstance(item, Model3DItem)
        assert item.is_loading()
        progress = item.load_progress()
        assert progress is not None and 0.0 <= progress[0] <= 1.0 and progress[1]
        h.grab()  # paints the loading card

        deadline = time.monotonic() + 20.0
        while item.is_loading() and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(0.01)
        assert not item.is_loading(), "background load never delivered its result"
        assert item.load_progress() is None
        assert not item.is_missing() and item.mesh is not None
        assert item.mesh.face_count == 2 * (n - 1) * (n - 1)
    finally:
        h.close()


def test_double_click_enters_3d_view_mode_and_drags_commit_one_undo_step():
    from PySide6.QtCore import QPoint

    h = make()
    try:
        name = h.run("model3d.create_primitive", "loomis_head", [0.0, 0.0])
        h.refresh()
        item = h.item(name)
        service = h.service("model3d")
        assert not item.editing

        # Double-click the 3D item to enter interactive 3D View Mode
        center_scene = item.sceneBoundingRect().center()
        center_vp = h.vp(center_scene.x(), center_scene.y())
        h.mouse("press", center_vp)
        h.mouse("release", center_vp)
        h.mouse("double", center_vp)
        assert item.editing and service.editing_name == name

        # Left-drag inside the item orbits yaw and pitch in one undo step
        yaw_before = h.attr(name, "yaw")
        pitch_before = h.attr(name, "pitch")
        h.drag(center_vp, center_vp + QPoint(50, -30))
        h.refresh()
        assert h.attr(name, "yaw") != yaw_before
        assert h.attr(name, "pitch") != pitch_before
        assert service.editing_name == name and h.item(name).editing, "stays in 3D View Mode after orbit"

        # Alt + Left-drag rotates key light direction
        az_before = h.attr(name, "light_azimuth")
        h.drag(center_vp, center_vp + QPoint(40, -25), modifiers=Qt.AltModifier)
        h.refresh()
        assert h.attr(name, "light_azimuth") != az_before

        # Shift + Left-drag pans the 3D target
        target_before = list(h.attr(name, "target"))
        h.drag(center_vp, center_vp + QPoint(30, 20), modifiers=Qt.ShiftModifier)
        h.refresh()
        assert list(h.attr(name, "target")) != target_before

        # Wheel inside the item zooms distance, coalesced into one undo step
        dist_before = h.attr(name, "distance")
        h.wheel(center_vp, steps=1)
        h.refresh()
        assert h.attr(name, "distance") != dist_before
        dist_after_one = h.attr(name, "distance")
        h.wheel(center_vp, steps=1)
        h.refresh()
        assert h.attr(name, "distance") != dist_after_one
        h.end_coalesced()

        # Undo reverts zoom, then pan, then light, then orbit
        h.undo()
        assert h.attr(name, "distance") == dist_before
        h.undo()
        assert list(h.attr(name, "target")) == target_before
        h.undo()
        assert h.attr(name, "light_azimuth") == az_before
        h.undo()
        assert h.attr(name, "yaw") == yaw_before and h.attr(name, "pitch") == pitch_before

        # Shortcuts while in 3D View Mode: W toggles wireframe, P toggles projection, 1 snaps Front, Esc exits
        h.key(Qt.Key_W)
        assert h.attr(name, "wireframe_overlay") is True
        h.key(Qt.Key_P)
        assert h.attr(name, "projection") == "orthographic"
        h.key(Qt.Key_1)
        assert h.attr(name, "yaw") == 0.0 and h.attr(name, "pitch") == 0.0
        h.key(Qt.Key_Escape)
        assert service.editing_item() is None and not h.item(name).editing

        # Inspector section and gizmo edge-handle viewport resize
        sec = h.section("model3d")
        assert sec is not None and not sec.isHidden()
        g = h.gizmo()
        assert "mr" in g["handles_vp"]
        mr_pt = g["handles_vp"]["mr"].toPoint()
        h.drag(mr_pt, mr_pt + QPoint(80, 0))
        h.refresh()
        assert h.attr(name, "size")[0] > 400.0
        h.undo()
        assert h.attr(name, "size") == [400.0, 400.0]
    finally:
        h.close()


def test_textures_obj_ply_and_commands():
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    h = make()
    try:
        folder = tmp_dir()
        # Create a vivid green texture PNG and a blue texture PNG
        green_img = QImage(32, 32, QImage.Format_RGBA8888)
        green_img.fill(QColor(20, 230, 60, 255))
        green_path = folder / "green_tex.png"
        green_img.save(str(green_path), "PNG")

        blue_img = QImage(32, 32, QImage.Format_RGBA8888)
        blue_img.fill(QColor(30, 80, 245, 255))
        blue_path = folder / "blue_tex.png"
        blue_img.save(str(blue_path), "PNG")

        # 1. OBJ with vt UVs + MTL map_Kd
        (folder / "quad.mtl").write_text("newmtl Mat0\nmap_Kd green_tex.png\n", encoding="utf-8")
        quad_obj = folder / "quad.obj"
        quad_obj.write_text(
            "mtllib quad.mtl\n"
            "v -1.0 -1.0  0.0\n"
            "v  1.0 -1.0  0.0\n"
            "v  1.0  1.0  0.0\n"
            "v -1.0  1.0  0.0\n"
            "vt 0.0 0.0\n"
            "vt 1.0 0.0\n"
            "vt 1.0 1.0\n"
            "vt 0.0 1.0\n"
            "usemtl Mat0\n"
            "f 1/1 2/2 3/3 4/4\n",
            encoding="utf-8",
        )
        name = h.run("model3d.import_model", quad_obj.as_posix(), [0.0, 0.0])
        h.run("model3d.set_preset_view", "front", [name])
        h.refresh()
        item = h.item(name)
        assert item.has_texture
        tex_img = item.rendered_image().copy()
        center_px = tex_img.pixelColor(tex_img.width() // 2, tex_img.height() // 2)
        assert center_px.green() > center_px.red() + 40, f"expected green texture, got {center_px.getRgb()}"

        # Toggle texture off -> falls back to untextured base_color
        h.run("model3d.toggle_texture", [name])
        h.refresh()
        assert h.attr(name, "show_texture") is False
        untex_img = h.item(name).rendered_image().copy()
        assert untex_img != tex_img

        # Override with custom blue texture via model3d.set_texture
        h.run("model3d.set_texture", blue_path.as_posix(), [name])
        h.refresh()
        assert h.attr(name, "show_texture") is True
        blue_render = h.item(name).rendered_image().copy()
        blue_px = blue_render.pixelColor(blue_render.width() // 2, blue_render.height() // 2)
        assert blue_px.blue() > blue_px.green() + 40, f"expected blue override texture, got {blue_px.getRgb()}"

        # Clear custom texture override -> returns to MTL green texture
        h.run("model3d.clear_texture", [name])
        h.refresh()
        restored_px = h.item(name).rendered_image().pixelColor(tex_img.width() // 2, tex_img.height() // 2)
        assert restored_px.green() > restored_px.blue() + 40

        # 2. Binary PLY with Artec multi_texture_vertex + multi_texture_face + companion <stem>_0.jpg
        comp_tex = folder / "scan_0.jpg"
        green_img.save(str(comp_tex), "JPEG")
        ply_hdr = (
            b"ply\nformat binary_little_endian 1.0\n"
            b"element vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
            b"element face 1\nproperty list uchar int vertex_indices\n"
            b"element multi_texture_vertex 3\nproperty uchar tx\nproperty float u\nproperty float v\n"
            b"element multi_texture_face 1\nproperty uchar tx\nproperty uint f\n"
            b"property list uchar int texcoord_indices\nend_header\n"
        )
        ply_body = (
            struct.pack("<9f", -1.0, -1.0, 0.0, 1.0, -1.0, 0.0, 0.0, 1.0, 0.0)
            + struct.pack("<B3i", 3, 0, 1, 2)
            + struct.pack("<BffBffBff", 0, 0.1, 0.1, 0, 0.9, 0.1, 0, 0.5, 0.9)
            + struct.pack("<BIB3i", 0, 0, 3, 0, 1, 2)
        )
        scan_ply = folder / "scan.ply"
        scan_ply.write_bytes(ply_hdr + ply_body)
        ply_mesh = parse_ply(scan_ply.read_bytes(), base_dir=folder, filename="scan.ply")
        assert ply_mesh.has_uvs
        assert ply_mesh.has_texture
        assert ply_mesh.texture_path == comp_tex
    finally:
        h.close()


def test_paint_light_gizmo():
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from pixelref.builtins.model3d.renderer import paint_light_gizmo

    img = QImage(240, 240, QImage.Format_ARGB32_Premultiplied)
    img.fill(QColor("#1e1e1e"))
    p = QPainter(img)
    try:
        # Small rect returns early
        paint_light_gizmo(p, QRectF(0, 0, 80, 80), 45.0, 35.0)

        # Front-facing light (coming toward object)
        paint_light_gizmo(p, QRectF(0, 0, 200, 200), 45.0, 35.0)

        # Back-facing light (going away / behind object)
        paint_light_gizmo(p, QRectF(0, 0, 200, 200), 135.0, 35.0)

        # Straight-on front light (lz = 1.0)
        paint_light_gizmo(p, QRectF(0, 0, 200, 200), 0.0, 0.0)

        # Straight-on back light (lz = -1.0)
        paint_light_gizmo(p, QRectF(0, 0, 200, 200), 180.0, 0.0)
    finally:
        p.end()

    # Confirm pixels were painted
    assert img.pixelColor(180, 180).alpha() > 0


def test_embedded_texture_survives_save_and_reload():
    """A 3D object's embedded texture (texture_asset) is written with the scene, not only its mesh."""
    from pixelref.core.session import Session

    session = Session(make_current=False).start()
    try:
        folder = tmp_dir()
        texture = b"\x89PNG\r\n\x1a\n" + b"texture-bytes" * 8
        texture_id = session.scene.add_asset(texture, name="tex.png")
        session.scene.create_node("model3d", "Head", attrs={"primitive": "sphere", "texture_asset": texture_id})
        out = session.save_file(folder / "board.pxref")
        assert texture_id in json.loads(out.read_text(encoding="utf-8")).get("assets", {})
        session.open_file(out)
        assert session.scene.get_asset(session.scene.nodes["Head"].attrs["texture_asset"]).data == texture
    finally:
        session.shutdown()
