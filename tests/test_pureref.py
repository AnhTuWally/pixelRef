"""PureRef import: reading .pur boards and turning them into pixelRef nodes."""

from __future__ import annotations

import random
import struct
from pathlib import Path
from typing import Any

from harness import APP, approx, make, make_png, tmp_dir
from PySide6.QtCore import QBuffer, QIODevice, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QImage

# ---- A small .pur writer for fixtures --------------------------------------------------------------
# Adapted from purformat/write.py in FyorDev's PureRef-format
# (https://github.com/FyorDev/PureRef-format, MIT License, Copyright (c) 2023 Fyor),
# extended with linked images and colour specs so the reader's extras are covered.


def _qstring(text: str) -> bytes:
    data = text.encode("utf-16-be")
    return struct.pack(">I", len(data)) + data


def _matrix(m: tuple) -> bytes:
    return struct.pack(">6d", m[0], m[1], 0.0, m[2], m[3], 0.0)


def _color(rgba: tuple, spec: int = 1) -> bytes:
    a, c1, c2, c3 = rgba
    return struct.pack(">b5H", spec, a, c1, c2, c3, 0)


def text(content: str, x: float = 0.0, y: float = 0.0, z: float = 0.0, matrix: tuple = (1.0, 0.0, 0.0, 1.0),
         color: tuple = (65535, 65535, 65535, 65535), spec: int = 1,
         background: tuple = (5000, 0, 0, 0), children: list | None = None) -> dict[str, Any]:
    return {"text": content, "x": x, "y": y, "z": z, "matrix": matrix, "color": color, "spec": spec,
            "background": background, "children": children or []}


def instance(w: int, h: int, x: float = 0.0, y: float = 0.0, z: float = 0.0, matrix: tuple = (1.0, 0.0, 0.0, 1.0),
             name: str = "image", source: str = "C:/refs/image.png", crop: tuple | None = None,
             children: list | None = None) -> dict[str, Any]:
    """An image item; ``crop`` is (x, y, w, h) in pixels."""
    ox, oy = -w / 2.0, -h / 2.0
    cx, cy, cw, ch = crop or (0.0, 0.0, w, h)
    left, top = ox + cx, oy + cy
    corners = [(left, top), (left + cw, top), (left + cw, top + ch), (left, top + ch)]
    return {"x": x, "y": y, "z": z, "matrix": matrix, "name": name, "source": source, "offset": (ox, oy),
            "points": [corners[0]] + corners, "children": children or []}


def write_pur(path: Path, images: list[tuple[bytes | None, list[dict]]], texts: list[dict] = (),
              folder: str = "C:/refs") -> Path:
    """``images``: (file bytes or None for a linked image, its instances)."""
    out = bytearray(224)
    items = [inst for _data, insts in images for inst in insts]
    for number, inst in enumerate(items):
        inst["id"] = number
    next_id = len(items)

    def number_texts(nodes: list[dict]) -> None:
        nonlocal next_id
        for node in nodes:
            node["id"] = next_id
            next_id += 1
            number_texts(node["children"])

    for inst in items:
        number_texts(inst["children"])
    number_texts(list(texts))

    struct.pack_into(">I", out, 0, 8)
    out[4:12] = "1.10".encode("utf-16-be")
    struct.pack_into(">HH", out, 12, len(items) + len(texts), len(items))
    struct.pack_into(">I", out, 24, 12)
    struct.pack_into(">I", out, 40, 64)
    struct.pack_into(">I", out, 108, next_id)
    struct.pack_into(">4dd", out, 112, -10000.0, -10000.0, 10000.0, 10000.0, 1.0)
    struct.pack_into(">d", out, 176, 1.0)
    struct.pack_into(">d", out, 208, 1.0)

    refs = []
    for data, insts in images:
        start = len(out)
        out += data if data is not None else b"\xff\xff\xff\xff"
        refs.append((insts[0]["id"], start, len(out)))
        for inst in insts[1:]:
            refs.append((inst["id"], len(out), len(out) + 4))
            out += struct.pack(">I", insts[0]["id"])

    def write_text(node: dict) -> None:
        start = len(out)
        out.extend(b"\0" * 8)
        out.extend(_qstring("GraphicsTextItem") + _qstring(node["text"]) + _matrix(node["matrix"]))
        out.extend(struct.pack(">ddd", node["x"], node["y"], 1.0) + struct.pack(">Id", node["id"], node["z"]))
        out.extend(_color(node["color"], node["spec"]) + _color(node["background"]))
        out.extend(struct.pack(">I", len(node["children"])))
        struct.pack_into(">Q", out, start, len(out))
        for child in node["children"]:
            write_text(child)

    def write_image(inst: dict) -> None:
        start = len(out)
        out.extend(b"\0" * 8)
        out.extend(_qstring("GraphicsImageItem") + _qstring(inst["source"]) + _qstring(inst["name"]))
        out.extend(struct.pack(">d", 1.0) + _matrix(inst["matrix"]) + struct.pack(">ddd", inst["x"], inst["y"], 1.0))
        out.extend(struct.pack(">Id", inst["id"], inst["z"]) + _matrix((1.0, 0.0, 0.0, 1.0)))
        out.extend(struct.pack(">ddd", *inst["offset"], 1.0) + struct.pack(">I", len(inst["points"])))
        for index, (px, py) in enumerate(inst["points"]):
            out.extend(struct.pack(">Idd", 1 if index else 0, px, py))
        out.extend(struct.pack(">dIbq", 0.0, 1, 0, -1) + struct.pack(">I", len(inst["children"])))
        struct.pack_into(">Q", out, start, len(out))
        for child in inst["children"]:
            write_text(child)

    for inst in items:
        write_image(inst)
    for node in texts:
        write_text(node)
    out += _qstring(folder)
    struct.pack_into(">Q", out, 16, len(out))
    for ref in refs:
        out += struct.pack(">IQQ", *ref)
    path.write_bytes(bytes(out))
    return path


def _png(w: int, h: int, color: str) -> bytes:
    return make_png(f"pur_{w}x{h}_{color[1:]}.png", w, h, color).read_bytes()


def _jpeg(w: int, h: int, color: str) -> bytes:
    image = QImage(w, h, QImage.Format_RGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "JPG")
    return bytes(buffer.data())


def _sample_board(folder: Path) -> Path:
    red = _png(40, 20, "#ff0000")
    note_on_red = text("on red", x=5.0, y=6.0, color=(65535, 65535, 0, 0))
    return write_pur(folder / "sample.pur", [
        (red, [
            instance(40, 20, x=100.0, y=50.0, z=2.0, matrix=(0.0, 2.0, -2.0, 0.0), name="red",
                     children=[note_on_red]),  # rotated 90°, scaled ×2
            instance(40, 20, x=-300.0, y=0.0, z=1.0, matrix=(-1.0, 0.0, 0.0, 1.0), name="red copy"),  # mirrored
        ]),
        (_jpeg(64, 32, "#0000ff"), [instance(64, 32, x=0.0, y=200.0, z=3.0, name="blue", crop=(8, 4, 32, 16))]),
    ], texts=[
        # HSV: hue 120° (in 1/100°), full saturation and value: green.
        text("Hello\nPureRef", x=-50.0, y=-80.0, z=0.5, color=(65535, 12000, 65535, 65535), spec=2,
             background=(32896, 0, 0, 0), children=[text("nested", x=0.0, y=40.0)]),
    ])


def headless() -> Any:
    from pixelref.core.session import Session

    return Session().start()


# ---- Tests ---------------------------------------------------------------------------------------


def test_pureref_reader_matches_the_board():
    from pixelref.builtins.pureref.purformat import PurImage, PurText, read_board

    board = read_board(_sample_board(tmp_dir()))
    assert board.version == "1.10" and board.folder == "C:/refs" and not board.warnings, board.warnings
    images = board.images
    assert [i.name for i in images] == ["red", "red copy", "blue"]
    assert images[0].data == images[1].data and images[0].data.startswith(b"\x89PNG"), "reused image stored once"
    assert images[2].data.startswith(b"\xff\xd8"), "non-PNG images are found through the address table"
    assert images[0].matrix == (0.0, 2.0, -2.0, 0.0) and (images[0].x, images[0].y) == (100.0, 50.0)
    assert images[2].crop_box == (8.0, 4.0, 32.0, 16.0)
    assert [c.text for c in images[0].children] == ["on red"]
    top = [i for i in board.items if isinstance(i, PurText)]
    assert len(top) == 1 and top[0].color == (0, 255, 0, 255), top[0].color
    assert top[0].background == (0, 0, 0, 128) and [c.text for c in top[0].children] == ["nested"]
    assert len(board.texts) == 3 and all(isinstance(i, (PurImage, PurText)) for i in board.items)


def test_pureref_open_places_images_and_notes():
    session = headless()
    session.run("create_node", "group", "Existing")
    path = _sample_board(tmp_dir())
    roots = session.run("pureref.open", str(path))
    scene = session.scene
    assert "Existing" not in scene.nodes, "open replaces the board"
    assert session.scene_path is None and session.unsaved, "a new, unsaved scene (Save asks for a .pxref name)"
    assert not session.undo.stack.can_undo(), "the opened board isn't an undo step"
    # Draw order follows PureRef's z order.
    assert roots == ["Note", "red copy", "red", "blue"], roots
    red, copy, blue = (scene.get_node(n).attrs for n in ("red", "red copy", "blue"))
    assert red["asset"] == copy["asset"] and len(scene.assets) == 2
    assert approx(red["rotate"], 90.0) and red["scale"] == [2.0, 2.0]
    x, y, w, h = session.world_bounds("red")
    assert approx(x, 80) and approx(y, 10) and approx(w, 40) and approx(h, 80), (x, y, w, h)  # centred on (100, 50)
    assert copy["scale"] == [-1.0, 1.0] and session.world_bounds("red copy") == [-320.0, -10.0, 40.0, 20.0]
    assert blue["crop"] == [8.0, 4.0, 32.0, 16.0]
    x, y, w, h = session.world_bounds("blue")
    assert approx(x, -24) and approx(y, 188) and approx(w, 32) and approx(h, 16), "the crop stays where it was"
    # Text attached to an image moves with it: it's a child, placed in the image's pixels.
    note = next(n for n in scene.children_of("red"))
    assert scene.get_node(note).attrs["text"] == "on red" and scene.get_node(note).attrs["translate"] == [25.0, 16.0]
    assert scene.get_node(note).attrs["color"] == [255, 0, 0]
    hello = scene.get_node("Note").attrs
    assert hello["text"] == "Hello\nPureRef" and hello["color"] == [0, 255, 0]
    assert hello["background_color"] == [0, 0, 0, 128] and hello["translate"] == [-50.0, -80.0]
    nested = scene.children_of("Note")
    assert len(nested) == 1 and scene.get_node(nested[0]).attrs["translate"] == [0.0, 40.0]


def test_pureref_import_groups_centres_and_undoes():
    session = headless()
    session.run("create_node", "group", "Existing")
    path = _sample_board(tmp_dir())
    before = len(session.scene.nodes)
    roots = session.run("pureref.import_file", str(path), [1000.0, 0.0])
    assert roots == ["sample"] and session.scene.get_node("sample").node_type == "group"
    assert session.selection.names == ["sample"]
    x, y, w, h = session.world_bounds("sample")
    assert approx(x + w / 2, 1000.0, 0.5) and approx(y + h / 2, 0.0, 0.5), (x, y, w, h)
    report = session.services.get("pureref").last_report
    assert report["images"] == 3 and report["notes"] == 3 and not report["warnings"]
    session.run("undo")
    assert len(session.scene.nodes) == before, "one undo step"
    # Into an empty board it keeps PureRef's own coordinates and needs no group.
    session.run("new_scene")
    roots = session.run("pureref.import_file", str(path))
    assert "sample" not in roots and session.world_bounds("red copy") == [-320.0, -10.0, 40.0, 20.0]


def test_pureref_linked_images_are_found_next_to_the_board():
    folder = tmp_dir()
    (folder / "linked.png").write_bytes(_png(30, 10, "#22aa44"))
    path = write_pur(folder / "linked.pur", [
        (None, [instance(30, 10, name="linked", source="D:/elsewhere/linked.png")]),
        (None, [instance(30, 10, name="gone", source="D:/elsewhere/gone.png")]),
    ])
    session = headless()
    session.run("pureref.open", str(path))
    linked = session.scene.get_node("linked").attrs
    assert not linked["asset"] and Path(linked["path"]) == folder / "linked.png", linked
    assert session.world_bounds("linked") == [-15.0, -5.0, 30.0, 10.0]
    report = session.services.get("pureref").last_report
    assert report["linked"] == 2 and report["missing"] == 1
    assert session.scene.get_node("gone").attrs["path"].endswith("gone.png"), "kept, shown as a placeholder"


def test_pureref_bad_files_fail_cleanly_and_keep_the_board():
    from pixelref.builtins.pureref.purformat import PureRefError, parse_board
    from pixelref.plugin_api import CommandError

    folder = tmp_dir()
    data = _sample_board(folder).read_bytes()
    session = headless()
    session.run("create_node", "group", "Existing")
    garbage = folder / "notes.pur"
    garbage.write_text("not a board")
    for command in ("pureref.open", "pureref.import_file"):
        try:
            session.run(command, str(garbage))
        except CommandError as exc:
            assert "notes.pur: Not a PureRef board" in str(exc), exc
        else:
            raise AssertionError("garbage was accepted")
    assert list(session.scene.nodes) == ["Existing"]
    v2 = bytearray(data)
    v2[4:12] = "3.00".encode("utf-16-be")
    try:
        parse_board(bytes(v2))
    except PureRefError as exc:
        assert "isn't supported" in str(exc)
    else:
        raise AssertionError("an unknown format version was accepted")
    # Truncated or corrupted files raise PureRefError (or still parse), never anything else.
    rng = random.Random(7)
    damaged = [data[:cut] for cut in range(0, len(data), 37)]
    for _ in range(150):
        copy = bytearray(data)
        for _ in range(4):
            copy[rng.randrange(224, len(copy))] = rng.randrange(256)
        damaged.append(bytes(copy))
    for sample in damaged:
        try:
            parse_board(sample)
        except PureRefError:
            pass


def test_pureref_drop_and_menus():
    h = make()
    try:
        path = _sample_board(tmp_dir())
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        h.add_image("Existing", 0, 0)
        assert h.service("ingest").describe(mime) == "Drop to add the PureRef board sample.pur"
        created = h.drop(QPointF(500, 500), mime)
        assert "sample" in created and h.type_of("sample") == "group"
        h.refresh()
        assert h.item("red") is not None and h.item("Note") is not None
        assert h.app.status_text().startswith("Drop: 3 image(s), 3 note(s)"), h.app.status_text()
        h.undo()
        assert not h.has("sample") and h.has("Existing")
        file_menu = next(a.menu() for a in h.app.menus.build("canvas").actions() if a.text() == "File")
        texts = [a.text() for a in file_menu.actions()]
        assert "Open PureRef Board (Import Only)…" in texts
        assert any(a.menu() and "Import PureRef Board…" in [s.text() for s in a.menu().actions()]
                   for a in file_menu.actions()) or "Import PureRef Board…" in texts
        # Opening replaces the board, and the canvas follows without a forced refresh.
        assert h.app.run_command("pureref.open", str(path)) is not None
        APP.processEvents()
        assert not h.has("Existing") and all(h.item(n) is not None for n in ("red", "red copy", "blue", "Note"))
        assert h.session.unsaved and h.undo_label() == ""
    finally:
        h.close()


# ---- PureRef 2 (an SQLite database in the .pur file) ---------------------------------------------------

PUR2_SCHEMA = (
    "CREATE TABLE metadata (id INTEGER PRIMARY KEY,scene_rect TEXT,application_version TEXT,view_transform TEXT,"
    "thumbnail BLOB,horizontal_scroll INTEGER,vertical_scroll INTEGER,last_save_path TEXT,last_load_path TEXT,"
    "last_load_checksum TEXT,saved INTEGER)",
    "CREATE TABLE images (id INTEGER PRIMARY KEY,source_type INTEGER,origin TEXT,source TEXT,format TEXT,"
    "checksum TEXT,data BLOB,width INTEGER,height INTEGER)",
    "CREATE TABLE items (parent INTEGER,id INTEGER PRIMARY KEY,name TEXT,transform BLOB,sort_order BLOB,z REAL,"
    "opacity REAL,locked INTEGER,comment INTEGER)",
    "CREATE TABLE items_images (image INTEGER,playback_speed REAL,id INTEGER PRIMARY KEY,playback_state INTEGER,"
    "image_transform BLOB,image_bounds BLOB,playback_frame INTEGER,flags INTEGER)",
    "CREATE TABLE items_notes (text_color TEXT,id INTEGER PRIMARY KEY,fixed_size TEXT,background_color TEXT,"
    "text TEXT,style INTEGER)",
    "CREATE TABLE items_drawings (id INTEGER PRIMARY KEY,strokes BLOB)",
    "CREATE TABLE items_groups (id INTEGER PRIMARY KEY,background_color TEXT,lock_mode INTEGER)",
)


def _as_text(raw: bytes) -> str:
    """PureRef 2 keeps Qt values in TEXT columns, one character per byte."""
    return raw.decode("latin-1")


def _q_transform(m11=1.0, m12=0.0, m21=0.0, m22=1.0, dx=0.0, dy=0.0) -> str:
    return _as_text(struct.pack(">Ib9d", 80, 0, m11, m12, 0.0, m21, m22, 0.0, dx, dy, 1.0))


def _q_user(name: str, payload: bytes) -> bytes:
    encoded = name.encode("latin-1") + b"\x00"
    return struct.pack(">IbI", 1024, 0, len(encoded)) + encoded + payload


def _q_path(elements: list[tuple[int, float, float]]) -> bytes:
    return (struct.pack(">I", len(elements)) + b"".join(struct.pack(">idd", *e) for e in elements)
            + struct.pack(">ii", 0, 0))


def _rect_path(x: float, y: float, w: float, h: float) -> list[tuple[int, float, float]]:
    return [(0, x, y), (1, x + w, y), (1, x + w, y + h), (1, x, y + h), (1, x, y)]


def _note_html(*spans: tuple[str, str]) -> str:
    body = "".join(f'<span style=" {style}">{text}</span>' for text, style in spans)
    return ('<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0//EN" "http://www.w3.org/TR/REC-html40/strict.dtd">\n'
            '<html><head><meta name="qrichtext" content="1" /><meta charset="utf-8" /><style type="text/css">\n'
            'p, li { white-space: pre-wrap; }\n</style></head>'
            '<body style=" font-family:\'Open Sans\'; font-size:22px; font-weight:400; font-style:normal;">\n'
            f'<p style=" margin-top:0px; margin-bottom:0px;">{body}</p>\n'
            '<p style=" margin-top:0px; margin-bottom:0px;">second &amp; last</p></body></html>')


def write_pur2(path: Path, fill: Any) -> Path:
    """A PureRef 2 board: ``fill(db)`` adds rows; the database is then wrapped like PureRef does."""
    import sqlite3

    db_path = path.with_suffix(".db")
    db_path.unlink(missing_ok=True)
    con = sqlite3.connect(db_path)
    for statement in PUR2_SCHEMA:
        con.execute(statement)
    thumbnail = _jpeg(16, 16, "#808080")
    con.execute("INSERT INTO metadata (id, application_version, thumbnail, last_save_path) VALUES (0, '2.1.3', ?, ?)",
                (thumbnail, "C:/boards"))
    fill(con)
    con.commit()
    con.close()
    db = db_path.read_bytes()

    def qstring(text: str) -> bytes:
        encoded = text.encode("utf-16-be")
        return struct.pack(">I", len(encoded)) + encoded

    prefix = (qstring("2.1") + struct.pack(">IQ", 0, len(db)) + qstring("2.1.3") + qstring("0" * 32)
              + struct.pack(">I", len(thumbnail)) + thumbnail)
    assert len(prefix) <= len(db)
    path.write_bytes(prefix + db[len(prefix):] + db[:len(prefix)])
    return path


def _sample_board_v2(folder: Path) -> Path:
    (folder / "linked2.png").write_bytes(_png(20, 20, "#00ffff"))

    def fill(con: Any) -> None:
        def item(item_id, z, transform, parent=-1, name="", opacity=1.0, locked=0):
            con.execute("INSERT INTO items (parent, id, name, transform, z, opacity, locked) VALUES (?,?,?,?,?,?,?)",
                        (parent, item_id, name, transform, z, opacity, locked))

        con.execute("INSERT INTO images (id, source_type, format, data, width, height) VALUES (0, 1, 'PNG', ?, 40, 20)",
                    (_png(40, 20, "#ff0000"),))
        con.execute("INSERT INTO images (id, source_type, source, format, width, height) VALUES (1, 2, ?, 'PNG', 20, 20)",
                    ("D:/gone/linked2.png",))
        # A rotated, cropped image (the right half), shown at half size by its own image transform.
        item(0, 2.0, _q_transform(0.0, 1.0, -1.0, 0.0, 100.0, 50.0), name="cropped", opacity=0.5, locked=1)
        con.execute("INSERT INTO items_images (image, id, image_transform, image_bounds, flags) VALUES (0, 0, ?, ?, 1)",
                    (_q_transform(0.5, 0.0, 0.0, 0.5, -10.0, -5.0),
                     _as_text(_q_user("QPainterPath", _q_path(_rect_path(0.0, -5.0, 10.0, 10.0))))))
        # A group holding a linked image and a note attached to it.
        item(1, 1.0, _q_transform(dx=-300.0, dy=0.0), name="refs")
        con.execute("INSERT INTO items_groups (id, background_color, lock_mode) VALUES (1, '#00000000', 0)")
        item(2, 1.0, _q_transform(dx=10.0, dy=10.0), parent=1)
        con.execute("INSERT INTO items_images (image, id, image_transform, image_bounds) VALUES (1, 2, ?, ?)",
                    (_q_transform(dx=-10.0, dy=-10.0),
                     _as_text(_q_user("QPainterPath", _q_path(_rect_path(-10.0, -10.0, 20.0, 20.0))))))
        item(3, 2.0, _q_transform(dx=-10.0, dy=12.0), parent=2)
        con.execute("INSERT INTO items_notes (id, fixed_size, background_color, text) VALUES (3, ?, '#13000000', ?)",
                    (_as_text(struct.pack(">Ibdd", 22, 0, -1.0, -1.0)),
                     _note_html(("Griffon", "color:#cc3a3b; font-weight:700;"), (" dogs", "color:#ffffff;"))))
        # A note on the board with a fixed width, and a pen drawing with a curve and a rectangle.
        item(4, 3.0, _q_transform(dx=-50.0, dy=200.0))
        con.execute("INSERT INTO items_notes (id, fixed_size, background_color, text) VALUES (4, ?, '#ffcc3a3b', ?)",
                    (_as_text(struct.pack(">Ibdd", 22, 0, 150.0, -1.0)), "plain words"))
        item(5, 4.0, _q_transform(2.0, 0.0, 0.0, 2.0, 0.0, 300.0))
        curve = [(0, 0.0, 0.0), (2, 10.0, 0.0), (3, 20.0, 10.0), (3, 20.0, 20.0)]
        strokes = struct.pack(">I", 2)
        # Colours are (alpha, red, green, blue): opaque yellow, half-transparent black.
        for color, elements in (((65535, 65535, 65535, 0), curve), ((32768, 0, 0, 0), _rect_path(0, 0, 40, 20))):
            strokes += (b"\x64" + struct.pack(">b5H", 1, *color, 0) + struct.pack(">d", 10.0) + _q_path(elements)
                        + struct.pack(">ddI", 0.0, 0.0, 2))
        con.execute("INSERT INTO items_drawings (id, strokes) VALUES (5, ?)",
                    (_as_text(_q_user("QList<GraphicsDrawItem::Stroke>", strokes)),))
        item(6, 5.0, _q_transform())  # a kind of item pixelRef doesn't know

    return write_pur2(folder / "board2.pur", fill)


def test_pureref2_reader_unpacks_the_database():
    from pixelref.builtins.pureref.purformat import PurDrawing, PurGroup, PurImage, PurText, read_board

    board = read_board(_sample_board_v2(tmp_dir()))
    assert (board.version, board.app_version, board.folder) == ("2.1", "2.1.3", "C:/boards")
    assert board.warnings == ["Skipped 1 item(s) of a kind pixelRef doesn't know"], board.warnings
    by_id = {item.id: item for item in board.walk()}
    cropped = by_id[0]
    assert isinstance(cropped, PurImage) and cropped.data.startswith(b"\x89PNG") and cropped.name == "cropped"
    assert cropped.matrix == (0.0, 1.0, -1.0, 0.0) and (cropped.x, cropped.y) == (100.0, 50.0)
    assert cropped.inner == (0.5, 0.0, 0.0, 0.5) and cropped.crop_box == (20.0, 0.0, 20.0, 20.0)
    assert cropped.opacity == 0.5 and cropped.locked
    group = by_id[1]
    assert isinstance(group, PurGroup) and [c.id for c in group.children] == [2]
    linked = by_id[2]
    assert linked.data is None and linked.source == "D:/gone/linked2.png" and [c.id for c in linked.children] == [3]
    note = by_id[3]
    assert isinstance(note, PurText) and note.text == "Griffon dogs\nsecond & last", repr(note.text)
    assert [(r["text"], r["color"][:3], r.get("bold")) for r in note.runs] == [
        ("Griffon", (204, 58, 59), True), (" dogs", (255, 255, 255), False), ("\nsecond & last", (255, 255, 255), False)]
    assert note.runs[0]["font_px"] == 22.0 and note.background == (0, 0, 0, 19)
    plain = by_id[4]
    assert plain.text == "plain words" and plain.width == 150.0 and plain.background == (204, 58, 59, 255)
    drawing = by_id[5]
    assert isinstance(drawing, PurDrawing) and len(drawing.strokes) == 2
    curve, rect = drawing.strokes
    assert curve.points[0] == (0.0, 0.0) and curve.points[-1] == (20.0, 20.0) and len(curve.points) == 13
    assert curve.color == (255, 255, 0, 255) and curve.width == 10.0
    assert rect.color == (0, 0, 0, 128)
    assert (40.0, 0.0) in rect.points and (39.5, 0.0) in rect.points, "corners stay sharp"


def test_pureref2_open_builds_groups_notes_and_drawings():
    session = headless()
    path = _sample_board_v2(tmp_dir())
    session.run("pureref.open", str(path))
    scene = session.scene
    report = session.services.get("pureref").last_report
    assert (report["images"], report["linked"], report["notes"], report["drawings"], report["groups"]) == (1, 1, 2, 1, 1)
    cropped = scene.get_node("cropped").attrs
    assert cropped["crop"] == [20.0, 0.0, 20.0, 20.0] and cropped["opacity"] == 0.5 and cropped["locked"] == 1
    assert approx(cropped["rotate"], 90.0) and cropped["scale"] == [0.5, 0.5]
    x, y, w, h = session.world_bounds("cropped")
    # The visible right half: item x 0..10 (pixels 20..40 at half size), y -5..5; rotated 90° about (100, 50).
    assert approx(x, 95) and approx(y, 50) and approx(w, 10) and approx(h, 10), (x, y, w, h)
    assert scene.get_node("refs").node_type == "group" and scene.get_node("refs").attrs["translate"] == [-300.0, 0.0]
    linked = next(n for n in scene.children_of("refs"))
    assert scene.get_node(linked).attrs["path"].endswith("linked2.png")
    assert session.world_bounds(linked) == [-300.0, 0.0, 20.0, 20.0]
    note = next(n for n in scene.children_of(linked))
    attrs = scene.get_node(note).attrs
    assert attrs["text"] == "Griffon dogs\nsecond & last" and attrs["translate"] == [0.0, 22.0]
    assert attrs["font_size"] == 16.5 and attrs["font_family"] == "Open Sans" and attrs["color"] == [255, 255, 255]
    assert attrs["runs"] == [{"text": "Griffon", "bold": True, "color": [204, 58, 59]},
                             {"text": " dogs\nsecond & last"}], attrs["runs"]
    plain = next(n for n in scene.nodes if scene.get_node(n).attrs.get("text") == "plain words")
    assert scene.get_node(plain).attrs["text_width"] == 150.0
    assert scene.get_node(plain).attrs["background_color"] == [204, 58, 59, 255]
    drawing = next(n for n in scene.nodes if scene.get_node(n).node_type == "annotation_drawing")
    strokes = scene.children_of(drawing)
    assert scene.get_node(drawing).attrs["scale"] == [2.0, 2.0] and len(strokes) == 2
    rect = scene.get_node(strokes[1]).attrs
    assert rect["color"] == [0, 0, 0] and rect["thickness"] == 10.0 and rect["opacity"] == round(128 / 255, 4)
    x, y, w, h = session.world_bounds(strokes[1])
    assert approx(x, 0) and approx(y, 300) and approx(w, 80) and approx(h, 40), (x, y, w, h)


def test_pureref2_damaged_databases_fail_cleanly():
    from pixelref.builtins.pureref.purformat import PureRefError, parse_board

    data = _sample_board_v2(tmp_dir()).read_bytes()
    offset = struct.unpack_from(">Q", data, 14)[0]
    broken = bytearray(data)
    broken[offset:offset + 16] = b"\x00" * 16  # no database header
    samples = [bytes(broken), data[:offset], data[:len(data) // 2]]
    rng = random.Random(11)
    for _ in range(60):
        copy = bytearray(data)
        for _ in range(8):
            copy[rng.randrange(64, len(copy))] = rng.randrange(256)
        samples.append(bytes(copy))
    for sample in samples:
        try:
            parse_board(sample)
        except PureRefError:
            pass


def test_pureref_open_warns_it_is_one_way_import_does_not():
    """Opening a board says pixelRef can't write .pur files (until hidden); importing doesn't."""
    h = make()
    try:
        path = _sample_board(tmp_dir())
        notice = h.service("pureref.notice")
        from PySide6.QtWidgets import QFileDialog  # the dialogs need a file chooser: stub it

        chooser = QFileDialog.getOpenFileName

        def open_board() -> None:
            h.session.mark_saved()  # no modal "Save changes?" prompt
            h.run("pureref.open_dialog")
        QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(path), ""))
        try:
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(path))])
            h.drop(QPointF(0, 0), mime)
            h.run("pureref.import_dialog")
            APP.processEvents()
            assert notice.box() is None and "import only" not in h.app.status_text(), "importing doesn't warn"
            open_board()
            APP.processEvents()
            assert h.app.status_text().endswith("(import only: save as a pixelRef scene (.pxref))"), h.app.status_text()
            box = notice.box()
            assert box is not None and box.isVisible() and "can't save them back as PureRef files" in box.text()
            assert box.windowModality() == Qt.NonModal, "the board stays usable"
            open_board()
            APP.processEvents()
            assert notice.box() is box, "one notice at a time"
            box.checkBox().setChecked(True)
            box.accept()
            APP.processEvents()
            assert notice.box() is None and notice.hidden()
            open_board()
            APP.processEvents()
            assert notice.box() is None, "hidden for good"
            assert h.app.status_text().endswith("(.pxref))"), "the status bar still says it"
        finally:
            QFileDialog.getOpenFileName = chooser
    finally:
        h.close()
