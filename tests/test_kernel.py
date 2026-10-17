"""The headless kernel: scene graph, file format, undo, pxcmd, plugins, console, batch mode."""

from __future__ import annotations

import json
import subprocess
import sys

from harness import ROOT, TMP, approx, make, tmp_dir


def headless(**kwargs):
    from pixelref.core.session import Session

    return Session(**kwargs).start()


# ---- Scene graph & file format ---------------------------------------------------------------


def test_scene_rename_restack_and_out_of_order_load():
    from pixelref.core.errors import SceneError
    from pixelref.core.scene_file import SceneParser

    session = headless()
    scene = session.scene
    scene.create_node("group", "L")
    scene.create_node("image", "I", parent="L")
    scene.rename_node("L", "Layer")
    assert scene.nodes["I"].parent == "Layer" and scene.nodes["Layer"].children == ["I"]
    try:
        scene.rename_node("I", "Layer")
    except SceneError:
        pass
    else:
        raise AssertionError("duplicate rename must fail")
    text = json.dumps({"schema": 1, "nodes": [
        {"id": "c", "name": "Child", "type": "image", "parent": "p", "attrs": {}},
        {"id": "p", "name": "Parent", "type": "group", "parent": None, "attrs": {}},
    ]})
    loaded = session.scene.__class__(session.types)
    SceneParser().load_into_scene(loaded, text)
    assert loaded.nodes["Child"].parent == "Parent"


def test_snapshot_does_not_alias_scene_lists():
    session = headless()
    session.scene.create_node("image", "I", attrs={"translate": [1.0, 2.0]})
    snap = session.scene.to_dict()
    snap["nodes"][0]["attrs"]["translate"][0] = 999.0
    assert session.scene.nodes["I"].attrs["translate"] == [1.0, 2.0]


def test_scene_files_must_be_json_and_not_from_a_newer_version():
    from pixelref.core.errors import SceneError

    session = headless()
    session.scene.create_node("image", "Kept")
    for name, text, message in (
        ("ascii.pxref", 'createNode "image" -name "Shot";', "Not a pixelRef scene file"),
        ("future.pxref", json.dumps({"schema": 99, "nodes": []}), "newer pixelRef"),
    ):
        path = tmp_dir() / name
        path.write_text(text, encoding="utf-8")
        try:
            session.open_file(path)
        except SceneError as exc:
            assert message in str(exc), str(exc)
        else:
            raise AssertionError(f"{name} must not open")
        assert session.scene.has("Kept"), "a failed open leaves the scene alone"


def test_unknown_node_types_survive_a_round_trip():
    """Nodes of a type no plugin provides (plugin missing or disabled) keep their data."""
    text = json.dumps({"schema": 1, "types": {"sticky": 3}, "nodes": [
        {"id": "s", "name": "Sticky", "type": "sticky", "parent": None,
         "attrs": {"translate": [5, 6], "colour": "yellow", "votes": 7}},
    ]})
    path = tmp_dir() / "unknown.pxref"
    path.write_text(text, encoding="utf-8")
    session = headless()
    session.open_file(path)
    assert session.scene.nodes["Sticky"].attrs["votes"] == 7
    out = tmp_dir() / "unknown_out.pxref"
    session.save_file(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    node = data["nodes"][0]
    assert node["type"] == "sticky" and node["attrs"]["colour"] == "yellow" and data["types"]["sticky"] == 3


def test_undo_stack_semantics():
    from pixelref.core.undo import UndoStack

    stack = UndoStack()
    stack.checkpoint("A", "first")
    stack.checkpoint("B", "second")
    assert stack.undo_label() == "second"
    assert stack.undo("C") == "B"
    assert stack.redo("B") == "C"
    stack.undo("C")
    stack.checkpoint("X")
    assert not stack.can_redo(), "a new edit clears the redo stack"


def test_layout_algorithms():
    from pixelref.builtins.arrange import layout

    rects = [(0, 0, 100, 50), (500, 500, 30, 80), (40, 40, 60, 60)]
    row = layout.arrange_row(rects, 10)
    assert row[0] == (0, 0) and row[2] == (110, 0) and row[1] == (180, 0)
    col = layout.arrange_column(rects, 0)
    assert [p[1] for p in col] == [0, 110, 50]
    packed = layout.arrange_optimal(rects, 0)
    boxes = [(x, y, r[2], r[3]) for (x, y), r in zip(packed, rects)]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            overlap = a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
            assert not overlap, (a, b)


# ---- pxcmd -----------------------------------------------------------------------------------------


def test_pxcmd_api_and_undo_chunks():
    from pixelref import pxcmd
    from pixelref.core.errors import CommandError, SceneError

    session = headless()
    layer = pxcmd.create_node("group", "Refs")
    box = pxcmd.create_node("annotation_rect", "Box", parent=layer, translate=[10, 20], size=[100, 50])
    assert pxcmd.ls(type="annotation_rect") == ["Box"] and pxcmd.children(layer) == ["Box"]
    assert pxcmd.get_attr("Box.translate") == [10, 20] and pxcmd.get_attr("Box.t") == [10, 20], "short names work"
    pxcmd.set_attr("Box.rotate", 90)
    assert pxcmd.get_attr("Box", "rotate") == 90.0
    assert pxcmd.inherits("annotation_rect", "canvas_node")
    pxcmd.set_attr("Box.opacity", 3.0)
    assert pxcmd.get_attr("Box.opacity") == 1.0, "values are clamped to the attribute's range"
    try:
        pxcmd.set_attr("Box.no_such_attr", 1)
    except (CommandError, SceneError):
        pass
    else:
        raise AssertionError("unknown attributes are rejected")
    assert approx(pxcmd.get_attr("Box.world_bounds")[0], -40.0, 1e-6), pxcmd.get_attr("Box.world_bounds")
    pxcmd.set_transform(box, translate=[5, 0], relative=True)
    assert pxcmd.get_attr("Box.translate") == [15, 20]
    assert pxcmd.get_transform(box)["translate"] == [15, 20] and pxcmd.parent_of(box) == "Refs"
    assert pxcmd.parent_of(layer) is None
    with pxcmd.undo_chunk("Two edits"):
        pxcmd.set_attr("Box.opacity", 0.5)
        pxcmd.rename(box, "Frame")
    assert session.undo.stack.undo_label() == "Two edits"
    pxcmd.undo()
    assert pxcmd.exists("Box") and pxcmd.get_attr("Box.opacity") == 1.0
    # Plugin commands live in namespaces, many with a flat alias.
    pxcmd.select(["Box"])
    pxcmd.set_attr("Box.rotate", 0)
    pxcmd.transform.flip("h")
    assert pxcmd.get_attr("Box.scale") == [-1.0, 1.0]
    pxcmd.flip("h")
    assert pxcmd.get_attr("Box.scale") == [1.0, 1.0]
    assert "set_attr" in dir(pxcmd) and "transform" in dir(pxcmd)
    assert "Set" in pxcmd.help("set_attr") or "set_attr" in pxcmd.help("set_attr")
    # Dynamic attributes.
    pxcmd.add_attr("Box", "rating", "int", 3, minimum=0, maximum=5)
    assert pxcmd.get_attr("Box.rating") == 3 and "rating" in pxcmd.attrs("Box")


def test_node_names_may_contain_dots():
    """Images are named after their file ("ref.v2.png" -> "ref.v2"); "Node.attr" splits at the last dot."""
    from pixelref import pxcmd

    headless()
    pxcmd.create_node("group", "ref.v2")
    pxcmd.set_attr("ref.v2.opacity", 0.25)
    assert pxcmd.get_attr("ref.v2.opacity") == 0.25 and pxcmd.get_attr("ref.v2", "opacity") == 0.25


def test_echo_prints_the_pxcmd_call():
    session = headless()
    echoed = []
    session.commands.on_echo(echoed.append)
    session.run("create_node", "group", "L")
    session.run("set_attr", "L.opacity", 0.5)
    assert echoed == ["pxcmd.create_node('group', 'L')", "pxcmd.set_attr('L.opacity', 0.5)"], echoed


def test_plugins_can_be_disabled_at_runtime_and_keep_their_nodes():
    from pixelref import pxcmd

    session = headless()
    pxcmd.create_node("annotation_rect", "Box", size=[40, 30], corner_radius=6.0)
    assert session.plugins.get("rect").active
    pxcmd.unload_plugin("rect")
    assert not session.plugins.get("rect").active
    assert "annotation_rect" not in session.types and not session.commands.exists("rect.create")
    assert session.scene.nodes["Box"].attrs["corner_radius"] == 6.0, "data of unknown types is kept"
    pxcmd.load_plugin("rect")
    assert session.commands.exists("rect.create") and session.scene.nodes["Box"].attrs["size"] == [40, 30]
    info = {p["id"]: p for p in pxcmd.list_plugins()}
    assert info["rect"]["status"] == "active" and info["select"]["foundation"]
    try:
        pxcmd.unload_plugin("select")
    except Exception:  # noqa: BLE001
        pass
    else:
        raise AssertionError("foundation plugins can't be disabled")
    session.settings.remove("plugins/disabled")
    session.settings.remove("plugins/enabled")


def test_plugin_disabled_in_the_app_removes_its_tool_and_ui():
    h = make()
    try:
        h.create("annotation_rect", "Box", size=[40, 30])
        assert "rect" in h.app.tools.tools and "rect" in h.palette.tool_buttons
        h.session.plugins.set_enabled("rect", False)
        assert "rect" not in h.app.tools.tools and "rect" not in h.palette.tool_buttons
        assert h.app.actions.get("tool.rect") is None
        h.refresh()
        assert type(h.item("Box")).__name__ == "PlaceholderItem", "unknown types show as placeholders"
        h.session.plugins.set_enabled("rect", True)
        h.refresh()
        assert "rect" in h.app.tools.tools and type(h.item("Box")).__name__ == "RectItem"
    finally:
        h.session.settings.remove("plugins/disabled")
        h.session.settings.remove("plugins/enabled")
        h.close()


def test_external_plugin_example_ellipse():
    """The example plugin in examples/plugins: a node type, a tool and generated UI."""
    plugin_dir = ROOT / "examples" / "plugins"
    from harness import Harness

    h = Harness(plugin_paths=(plugin_dir,))
    try:
        assert h.session.plugins.get("ellipse").active, h.session.plugins.get("ellipse").error
        h.set_tool("ellipse")
        h.drag(h.vp(0, 0), h.vp(120, 60))
        name = h.sel()[0]
        assert h.type_of(name) == "ellipse"
        size = h.attr(name, "size")
        assert approx(size[0], 120, 1) and approx(size[1], 60, 1)
        assert h.section("attributes") is not None and h.section("attributes").isVisible()
        h.undo()
        assert not h.has(name)
    finally:
        h.close()


# ---- Console & batch mode ----------------------------------------------------------------------------


def test_console_engine_runs_scripts_as_one_undo_step():
    from pixelref.builtins.console import ConsoleEngine

    session = headless()
    engine = ConsoleEngine(session)
    output = []
    engine.writers.append(lambda text, kind: output.append((kind, text)))
    engine.execute("for i in range(3):\n    pxcmd.create_node('group', f'L{i}')")
    assert session.undo.stack.labels()[-1] == "Script" and len(session.undo.stack.labels()) == 1
    assert engine.execute("len(pxcmd.ls())") == 3
    assert ("output", "3\n") in output
    engine.execute("pxcmd.create_node('group', 'X')\nraise ValueError('boom')")
    assert any(kind == "error" and "ValueError: boom" in text for kind, text in output)
    assert session.scene.has("X"), "like Maya, what ran before the error is kept"


def test_batch_mode_runs_a_script_headless():
    script = TMP / "batch_script.py"
    out = TMP / "batch_out.pxref"
    script.write_text("pxcmd.create_node('annotation_rect', 'Box', size=[10, 20])\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "pixelref", "--batch", str(script), "--save", str(out)],
                            cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [n["name"] for n in data["nodes"]] == ["Box"]


# ---- Views ------------------------------------------------------------------------------------------


def test_view_markers_are_current_per_view_and_keep_mirror_state():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        h.view.set_camera([100, 200], 2.0, flip_x=True, flip_y=False)
        camera_a = h.run("views.create_camera", "A")
        scene_a = h.session.scene.get_node(camera_a)
        assert h.view._current_marker == camera_a and scene_a.attrs["flip_x"] is True

        h.app.run_command("wm.workspace", 2)
        h.app.active_view().set_camera([300, 400], 3.0, flip_x=False, flip_y=True)
        camera_b = h.run("views.create_camera", "B")
        scene_b = h.session.scene.get_node(camera_b)
        assert h.app.active_view()._current_marker == camera_b and scene_b.attrs["flip_y"] is True
        assert h.view._current_marker == camera_a, "each view keeps its own current marker"

        h.run("views.look_through", camera_a, animate=False)
        camera = h.view.camera()
        assert approx(camera["center"][0], 100, 1e-6) and approx(camera["zoom"], 2.0, 1e-6)
        assert camera["flip_x"] is True and camera["flip_y"] is False
    finally:
        h.close()


def test_update_current_view_marker_works_without_an_explicit_name():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        h.view.set_camera([100, 200], 2.0, flip_x=True)
        saved = h.run("views.create_camera", "Marker")
        h.view.set_camera([300, 400], 4.0, flip_x=False, flip_y=True)
        assert h.app.trigger("views.update_bookmark") == saved
        attrs = h.session.scene.get_node(saved).attrs
        assert approx(attrs["center"][0], 300, 1e-6) and approx(attrs["zoom"], 4.0, 1e-6)
        assert attrs["flip_x"] is False and attrs["flip_y"] is True
    finally:
        h.close()


def test_view_markers_are_visible_and_toggleable():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        h.view.set_camera([10, 20], 2.0)
        h.run("views.create_camera", "Marker")
        assert h.view.display.show_markers is True
        assert h.app.trigger("views.toggle_markers") is False
        assert h.view.display.show_markers is False
        assert h.app.trigger("views.toggle_markers") is True
    finally:
        h.close()


def test_view_marker_is_a_canvas_node_and_respects_lock_on_update():
    h = make()
    try:
        marker = h.run("views.create_camera", "Marker")
        assert h.run("get_attr", f"{marker}.visibility") == 1
        assert h.run("get_attr", f"{marker}.locked") == 0
        h.run("set_attr", f"{marker}.locked", 1)
        try:
            h.run("views.update_camera", marker)
        except Exception as exc:  # noqa: BLE001
            assert "locked" in str(exc).lower()
        else:
            assert False, "updating a locked marker should fail"
    finally:
        h.close()


def test_view_marker_center_and_translate_stay_in_sync():
    h = make()
    try:
        h.view.set_camera([321, 654], 1.5)
        marker = h.run("views.create_camera", "Marker")
        attrs = h.session.scene.get_node(marker).attrs
        assert approx(attrs["center"][0], 321, 1e-6)
        assert approx(attrs["translate"][0], 321, 1e-6)

        h.view.set_camera([777, 888], 2.5)
        h.run("views.update_camera", marker)
        attrs = h.session.scene.get_node(marker).attrs
        assert approx(attrs["center"][0], 777, 1e-6)
        assert approx(attrs["translate"][0], 777, 1e-6)
    finally:
        h.close()


def test_create_marker_uses_live_interactive_view_camera_state():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        h.view.set_camera([0, 0], 1.0)
        h.view.zoom_by(2.0, around_cursor=False)
        marker = h.run("views.create_camera", "Marker")
        attrs = h.session.scene.get_node(marker).attrs
        assert approx(attrs["zoom"], 2.0, 1e-6)
    finally:
        h.close()


def test_update_marker_uses_current_view_context():
    h = make()
    try:
        h.app.run_command("wm.workspace", 2)
        color = h.app.views["ReviewColor"]
        value = h.app.views["ReviewValue"]
        h.run("views.unlink", color.view_name)
        h.run("views.unlink", value.view_name)

        color.set_camera([111, 222], 1.25)
        marker = h.run("views.create_camera", "ColorMarker", color.view_name)

        value.set_camera([777, 888], 3.0)
        h.run("views.update_camera", marker, color.view_name)
        attrs = h.session.scene.get_node(marker).attrs
        assert approx(attrs["center"][0], 111, 1e-6)
        assert approx(attrs["zoom"], 1.25, 1e-6)
    finally:
        h.close()


def test_views_cameras_presets_and_linking():
    h = make()
    try:
        h.add_image("Img", 0, 0)
        h.create("annotation_rect", "Box", translate=[400, 0], size=[50, 50])
        cam = h.run("views.create_camera", "Overview")
        h.view.set_camera([1000, 1000], 2.0)
        h.run("views.look_through", cam, animate=False)
        camera = h.view.camera()
        assert approx(camera["zoom"], 1.0, 1e-6) and approx(camera["center"][0], 0.0, 1.0)
        h.run("views.set_display_preset", "Images only")
        assert h.view.display.shows("Img") and not h.view.display.shows("Box")
        h.run("views.set_display_preset", "All")
        assert h.view.display.shows("Box")
        h.run("views.isolate", ["Box"])
        assert h.view.display.shows("Box") and not h.view.display.shows("Img")
        h.run("views.isolate", [])
        h.run("views.value_check")
        assert h.view.display.grayscale
        # Two views can follow each other.
        h.app.run_command("wm.workspace", 2)
        color, value = h.app.views["ReviewColor"], h.app.views["ReviewValue"]
        color.set_camera([123, 45], 1.5)
        assert approx(value.camera()["center"][0], 123, 1) and approx(value.zoom_level(), 1.5, 1e-6)
        # Panning and zooming are not undoable.
        before = h.session.undo.stack.labels()
        h.run("navigation.zoom", 3.0)
        assert h.session.undo.stack.labels() == before
    finally:
        h.close()


# ---- Kernel review regressions ------------------------------------------------------------------


def test_kernel_review_edge_cases():
    import struct

    from pixelref.core.attributes import Attr, coerce
    from pixelref.core.errors import SceneError
    from pixelref.core.imagesize import image_size

    session = headless()
    scene = session.scene
    for name in ("A", "B", "C"):
        scene.create_node("group", name)
    try:
        scene.move_z("A", "sideways")
    except SceneError:
        pass
    else:
        raise AssertionError("bad move_z mode must fail")
    assert scene.roots == ["A", "B", "C"], "a rejected move_z must not reorder"
    assert coerce(Attr("c", "color"), [1, 2, 3, 4, 5]) == [1, 2, 3, 4]
    # JPEG with 0xFF fill bytes before the SOF marker.
    sof = b"\xff\xc0" + struct.pack(">HBHH", 17, 8, 30, 40) + b"\x00" * 12
    assert image_size(b"\xff\xd8" + b"\xff\xff" + sof) == (40, 30)


def test_reenabling_a_plugin_brings_back_its_dependents():
    session = headless()
    try:
        assert session.plugins.get("crop").active
        session.plugins.set_enabled("image", False)
        assert not session.plugins.get("crop").active
        session.plugins.set_enabled("image", True)
        assert session.plugins.get("crop").active, session.plugins.get("crop").error
    finally:
        session.settings.remove("plugins/disabled")
        session.settings.remove("plugins/enabled")


def test_import_scene_keeps_node_ids_and_save_leaves_no_temp_file():
    from pixelref import pxcmd

    session = headless()
    pxcmd.create_node("group", "L")
    original_id = pxcmd.node_id("L")
    folder = tmp_dir()
    path = folder / "a.pxref"
    pxcmd.save_scene(str(path))
    assert [p.name for p in folder.iterdir()] == ["a.pxref"]
    pxcmd.new_scene()
    pxcmd.import_scene(str(path))
    assert pxcmd.node_id("L") == original_id, "node_ref attributes point at ids"
    roots = pxcmd.import_scene(str(path))
    assert pxcmd.node_id(roots[0]) != original_id, "ids stay unique"


def test_apply_color_accepts_short_hex_and_rejects_bad_hex():
    from pixelref.plugin_api import CommandError

    h = make()
    try:
        rect = h.run("rect.create", 0, 0, 10, 10)
        h.run("color_picker.apply_color", "#f80", [rect])
        assert h.attr(rect, "stroke_color") == [255, 136, 0]
        try:
            h.run("color_picker.apply_color", "#12", [rect])
            raise AssertionError("bad hex accepted")
        except CommandError:
            pass
    finally:
        h.close()


def test_kernel_second_pass_edge_cases():
    from pixelref import pxcmd
    from pixelref.core.attributes import Attr, coerce
    from pixelref.core.errors import SceneError

    # Junk input is a SceneError, not a TypeError/OverflowError.
    for attr, value in ((Attr("t", "string_list"), 5), (Attr("i", "int"), float("inf")),
                        (Attr("c", "color"), [float("nan"), 0, 0]), (Attr("p", "points"), 3),
                        (Attr("a", "any"), {1, 2})):
        try:
            coerce(attr, value)
        except SceneError:
            pass
        else:
            raise AssertionError(f"{attr.type} accepted {value!r}")
    assert Attr.from_dict("x", {"type": "int", "min": "lo", "choices": 3}).minimum is None

    session = headless()
    scene = session.scene
    scene.create_node("group", "A")
    scene.create_node("group", "B", parent="A")
    scene.create_node("group", "C", parent="B")
    scene.create_node("group", "D")
    assert scene.top_level(["C", "A", "D"]) == ["D", "A"]
    assert scene.top_level(["C"]) == ["C"]

    # set_attrs is all-or-nothing.
    pxcmd.set_attr("D.opacity", 0.5)
    try:
        pxcmd.set_attrs("D", {"opacity": 0.25, "translate": "junk"})
    except SceneError:
        pass
    assert pxcmd.get_attr("D.opacity") == 0.5

    # A non-JSON value never reaches the document (undo and saving keep working).
    try:
        pxcmd.add_attr("D", "meta", value={1, 2})
    except SceneError:
        pass
    assert "meta" not in scene.get_node("D").attrs
    session.commands.register("t_bad_plugin_data", lambda: scene.set_plugin_data("bad", {1, 2}))
    try:
        session.run("t_bad_plugin_data")
    except TypeError:
        pass
    assert scene.get_plugin_data("bad") is None, "an unserializable change is rolled back"
    session.dumps()

    # Malformed files fail with SceneError; repeated ids are made unique.
    for payload in ({"nodes": [1]}, {"nodes": [{"name": ["x"], "type": "group"}]}):
        try:
            session.load_text(json.dumps(payload))
        except SceneError:
            pass
        else:
            raise AssertionError(f"loaded {payload}")
    session.load_text(json.dumps({"schema": "x", "types": {"group": "y"}, "assets": [], "nodes": [
        {"id": "u", "name": "P", "type": "group"}, {"id": "u", "name": "Q", "type": "group"},
        {"id": "c", "name": "K", "type": "group", "parent": "u", "dynamic": [1]}]}))
    assert scene.get_node("P").id == "u" and scene.get_node("Q").id != "u"
    assert scene.get_node("K").parent == "P" and scene.get_node("K").node_type == "group"

    # pxcmd.callbacks reaches events the document emits.
    seen = []
    cid = pxcmd.callbacks.add("plugin_data.changed", lambda event: seen.append(event.event_type))
    cid2 = pxcmd.callbacks.add("scene.*", lambda event: seen.append(event.event_type))
    scene.set_plugin_data("x", {"a": 1})
    pxcmd.new_scene()
    pxcmd.callbacks.remove(cid)
    pxcmd.callbacks.remove(cid2)
    assert "plugin_data.changed" in seen and "scene.cleared" in seen, seen

    # Undo skips re-serializing an unchanged document but still records changes.
    scene.create_node("group", "U")
    calls = []
    original = session.undo._dump_fn
    session.undo._dump_fn = lambda: calls.append(1) or original()
    try:
        session.run("set_attr", "U.opacity", 0.5)
        assert len(calls) == 2  # before (nothing cached yet) and after
        session.run("set_attr", "U.opacity", 0.25)
        assert len(calls) == 3, "the previous 'after' is this command's 'before'"
        session.run("ls")
        session.run("rename", "U", "U")  # undoable, changes nothing
        assert len(calls) == 3
    finally:
        session.undo._dump_fn = original
    assert session.undo.undo() and pxcmd.get_attr("U.opacity") == 0.5
    assert session.undo.undo() and pxcmd.get_attr("U.opacity") == 1.0

    # Headless JPEG size honours EXIF orientation 5-8 (stored rotated by 90 degrees).
    import struct

    from pixelref.core.imagesize import image_size

    ifd = b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 1) + struct.pack(">HHIHH", 0x0112, 3, 1, 6, 0)
    app1 = b"Exif\x00\x00" + ifd
    sof = b"\xff\xc0" + struct.pack(">HBHH", 17, 8, 30, 40) + b"\x00" * 12
    jpeg = b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + sof
    assert image_size(jpeg) == (30, 40)
    assert image_size(b"\xff\xd8" + sof) == (40, 30)


def test_plugin_dependency_cycles_and_bad_manifests_dont_stop_startup():
    from pixelref.core.session import Session

    folder = tmp_dir()
    for plugin_id, extra in (("cyc_a", 'requires = ["cyc_b"]'), ("cyc_b", 'requires = ["cyc_a"]'),
                             ("soft_a", 'optional = ["soft_b"]'), ("soft_b", 'optional = ["soft_a"]'),
                             ("bad_api", 'api = "one"'), ("one_dep", 'requires = "soft_a"')):
        plugin = folder / plugin_id
        plugin.mkdir()
        (plugin / "plugin.toml").write_text(f'id = "{plugin_id}"\nname = "{plugin_id}"\n{extra}\n', encoding="utf-8")
        (plugin / "__init__.py").write_text("def activate(ctx):\n    pass\n", encoding="utf-8")
    session = Session(builtins=False, plugin_paths=[folder], make_current=False).start()
    plugins = session.plugins
    assert plugins.get("soft_a").active and plugins.get("soft_b").active
    assert plugins.get("one_dep").active and plugins.get("one_dep").manifest.requires == ["soft_a"]
    assert plugins.get("cyc_a").state == "error" and plugins.get("cyc_b").state == "error"
    assert plugins.get("bad_api").broken
    session.shutdown()
