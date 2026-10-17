"""Reading PureRef 2 boards (``.pur`` format 2.x, written by PureRef 2.0 and 2.1).

PureRef 2 replaced the 1.x item stream with an SQLite database. The layout
below was worked out for pixelRef from boards saved by PureRef 2.0.3 and
2.1.3; it isn't documented by PureRef, so anything unexpected is reported as a
:class:`PureRefError` or a warning rather than guessed at.

File::

    QString   format version ("2.1")
    u32       0
    u64       where the moved start of the database is (also the database's size)
    QString   application version ("2.1.3"), QString checksum (MD5, hex)
    u32 + …   a JPEG preview
    …         the database, except its first bytes: the header above sits in
              their place, and they are at the end of the file

So the database is ``file[offset:] + file[len(file) - offset:offset]``. Its
tables:

- ``items``: every item: ``parent`` (-1 on the board), ``transform``
  (``QTransform``), ``z``, ``opacity``, ``locked``, ``name``;
- ``items_images`` → ``images``: the image items, their own
  ``image_transform`` (pixels → item) and crop (``image_bounds``, a
  ``QPainterPath``); the image files, embedded (``data``) or linked
  (``source``);
- ``items_notes``: text notes as Qt rich text (HTML), ``background_color``
  as ``#AARRGGBB``;
- ``items_drawings``: pen strokes;
- ``items_groups``: groups (their members have them as ``parent``).

Qt values are stored as ``QVariant`` streams: type id (u32), is-null (u8),
then for custom types their name, then the value. Most are in TEXT columns,
one character per byte.
"""

from __future__ import annotations

import os
import re
import sqlite3
import struct
import tempfile
from html.parser import HTMLParser
from typing import Any

from .purformat import (
    PurBoard,
    PurDrawing,
    PureRefError,
    PurGroup,
    PurImage,
    PurItem,
    PurStroke,
    PurText,
    _qcolor,
    _Reader,
)

SQLITE_MAGIC = b"SQLite format 3\x00"
QT_SIZEF, QT_TRANSFORM, QT_USER = 22, 80, 1024
REQUIRED_TABLES = ("items", "items_images", "images")
CURVE_STEPS = 12  # line segments per Bézier curve of a pen stroke
CORNER = 0.5  # how close to a corner of a straight pen stroke its extra points go
PX_PER_PT = 96.0 / 72.0
MAX_ITEMS = 1_000_000


# ---- The database ----------------------------------------------------------------------------------


def extract_database(data: bytes, header_end: int) -> bytes:
    """The SQLite database inside a PureRef 2 file."""
    if header_end + 12 > len(data):
        raise PureRefError("Not a PureRef 2 board (the file is too short)")
    offset = struct.unpack_from(">Q", data, header_end + 4)[0]
    moved = len(data) - offset
    if not 0 < moved <= offset < len(data) or data[offset:offset + 16] != SQLITE_MAGIC:
        raise PureRefError("Not a PureRef 2 board (no board database where the header says)")
    return data[offset:] + data[moved:offset]


def open_database(db: bytes) -> tuple[sqlite3.Connection, str | None]:
    """A read-only connection to the database (in memory when SQLite allows)."""
    con = sqlite3.connect(":memory:")
    temp: str | None = None
    try:
        con.deserialize(db)
    except (AttributeError, sqlite3.Error):  # SQLite built without deserialize
        con.close()
        handle, temp = tempfile.mkstemp(suffix=".pur.db")
        with os.fdopen(handle, "wb") as file:
            file.write(db)
        con = sqlite3.connect(f"file:{temp}?mode=ro", uri=True)
    # The file is untrusted: no writes, no schema-defined functions or triggers.
    defensive = getattr(sqlite3, "SQLITE_DBCONFIG_DEFENSIVE", None)
    if defensive is not None and hasattr(con, "setconfig"):
        con.setconfig(defensive, True)
    con.execute("PRAGMA trusted_schema = OFF")
    con.execute("PRAGMA query_only = ON")
    con.text_factory = bytes
    return con, temp


def table_rows(con: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """Every row of a table: TEXT as ``str``, BLOB as ``bytes``."""
    columns = [row[1].decode("utf-8", errors="replace") if isinstance(row[1], bytes) else row[1]
               for row in con.execute(f'PRAGMA table_info("{table}")')]
    if not columns:
        return []
    select = ", ".join(f'"{c}", typeof("{c}")' for c in columns)
    rows = []
    for values in con.execute(f'SELECT {select} FROM "{table}"'):
        row: dict[str, Any] = {}
        for index, column in enumerate(columns):
            value, kind = values[2 * index], values[2 * index + 1]
            if kind == b"text" and isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            row[column] = value
        rows.append(row)
        if len(rows) > MAX_ITEMS:
            raise PureRefError(f"Too many rows in {table}")
    return rows


# ---- Qt values ---------------------------------------------------------------------------------------


def binary(value: Any) -> bytes | None:
    """The bytes of a value stored in a TEXT column one character per byte (or a BLOB)."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    try:
        return str(value).encode("latin-1")
    except UnicodeEncodeError:
        return None


def qvariant(value: Any) -> tuple[int, str, _Reader] | None:
    """(type id, custom type name, reader positioned at the value)."""
    raw = binary(value)
    if not raw or len(raw) < 5:
        return None
    reader = _Reader(raw)
    type_id = reader.u32()
    reader.skip(1)  # is-null
    name = ""
    if type_id == QT_USER:
        length = reader.u32()
        start = reader.need(length)
        name = raw[start:start + length].rstrip(b"\x00").decode("latin-1")
        reader.pos += length
    return type_id, name, reader


def qtransform(value: Any) -> tuple[tuple[float, float, float, float], float, float] | None:
    """A ``QTransform``: ((m11, m12, m21, m22), dx, dy)."""
    parsed = qvariant(value)
    if parsed is None or parsed[0] != QT_TRANSFORM:
        return None
    m11, m12, _m13, m21, m22, _m23, dx, dy, _m33 = parsed[2].unpack(">9d")
    return (m11, m12, m21, m22), dx, dy


def qsizef(value: Any) -> tuple[float, float] | None:
    parsed = qvariant(value)
    if parsed is None or parsed[0] != QT_SIZEF:
        return None
    return parsed[2].unpack(">dd")


def painter_path(reader: _Reader) -> list[list[tuple[float, float]]]:
    """A ``QPainterPath`` as polylines (one per sub-path, curves flattened)."""
    count = reader.u32()
    if count > MAX_ITEMS:
        raise PureRefError("Damaged path")
    elements = [reader.unpack(">idd") for _ in range(count)]
    reader.unpack(">ii")  # cStart, fill rule
    paths: list[list[tuple[float, float]]] = []
    index = 0
    while index < len(elements):
        kind, x, y = elements[index]
        if kind == 0 or not paths:  # MoveTo
            paths.append([(x, y)])
            index += 1
        elif kind == 2 and index + 2 < len(elements):  # CurveTo, then 2 CurveToData
            p0 = paths[-1][-1]
            p1, p2, p3 = (x, y), elements[index + 1][1:], elements[index + 2][1:]
            for step in range(1, CURVE_STEPS + 1):
                t = step / CURVE_STEPS
                u = 1.0 - t
                paths[-1].append(tuple(u ** 3 * a + 3 * u * u * t * b + 3 * u * t * t * c + t ** 3 * d
                                       for a, b, c, d in zip(p0, p1, p2, p3)))
            index += 3
        else:  # LineTo (or a stray CurveToData)
            px, py = paths[-1][-1]
            length = ((x - px) ** 2 + (y - py) ** 2) ** 0.5
            if length > 4 * CORNER:
                # pixelRef draws strokes as smooth curves through their points: points
                # just inside both ends keep a straight line's corners sharp.
                t = CORNER / length
                paths[-1] += [(px + (x - px) * t, py + (y - py) * t), (x - (x - px) * t, y - (y - py) * t)]
            paths[-1].append((x, y))
            index += 1
    return paths


def argb(value: Any, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """``#AARRGGBB`` / ``#RRGGBB`` (Qt colour names) as RGBA."""
    text = str(value or "").strip().lstrip("#")
    try:
        if len(text) == 8:
            a, r, g, b = (int(text[i:i + 2], 16) for i in range(0, 8, 2))
            return r, g, b, a
        if len(text) == 6:
            r, g, b = (int(text[i:i + 2], 16) for i in range(0, 6, 2))
            return r, g, b, 255
    except ValueError:
        pass
    return default


# ---- Notes (Qt rich text) -----------------------------------------------------------------------------


def _css(style: str) -> dict[str, str]:
    found = {}
    for part in style.split(";"):
        key, _, value = part.partition(":")
        if key.strip():
            found[key.strip().lower()] = value.strip()
    return found


def _font_px(value: str) -> float | None:
    match = re.fullmatch(r"([\d.]+)\s*(px|pt)?", value.strip().lower())
    if not match:
        return None
    size = float(match.group(1))
    return size * PX_PER_PT if match.group(2) == "pt" else size


def _style(css: dict[str, str], into: dict[str, Any]) -> dict[str, Any]:
    style = dict(into)
    if "color" in css:
        style["color"] = argb(css["color"], style.get("color", (255, 255, 255, 255)))
    if "background-color" in css:
        style["highlight"] = argb(css["background-color"], (0, 0, 0, 0))
    if "font-size" in css and _font_px(css["font-size"]):
        style["font_px"] = _font_px(css["font-size"])
    if "font-weight" in css:
        weight = css["font-weight"]
        style["bold"] = weight == "bold" or (weight.isdigit() and int(weight) >= 600)
    if "font-style" in css:
        style["italic"] = css["font-style"] in ("italic", "oblique")
    if "font-family" in css:
        style["font_family"] = css["font-family"].split(",")[0].strip().strip("'\"")
    if "text-decoration" in css:
        decoration = css["text-decoration"]
        style["underline"] = "underline" in decoration
        style["strike"] = "line-through" in decoration
    return style


class _RichText(HTMLParser):
    """Qt's rich-text HTML → plain text plus formatted runs."""

    BLOCKS = ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre")
    TAG_STYLES = {"b": {"bold": True}, "strong": {"bold": True}, "i": {"italic": True}, "em": {"italic": True},
                  "u": {"underline": True}, "s": {"strike": True}, "del": {"strike": True}}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base: dict[str, Any] = {}
        self.stack: list[tuple[str, dict[str, Any]]] = []
        self.runs: list[dict[str, Any]] = []
        self.blocks = 0
        self.in_block = False
        self.empty_block = False
        self.skip = 0

    def current(self) -> dict[str, Any]:
        return self.stack[-1][1] if self.stack else self.base

    def add(self, text: str) -> None:
        if not text:
            return
        style = self.current()
        if self.runs and {k: v for k, v in self.runs[-1].items() if k != "text"} == style:
            self.runs[-1]["text"] += text
        else:
            self.runs.append({"text": text, **style})

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        css = _css(dict(attrs).get("style") or "")
        if tag in ("head", "style", "script", "title"):
            self.skip += 1
            return
        if tag == "body":
            self.base = _style(css, {})
            return
        if tag == "br":
            if not self.empty_block:
                self.add("\n")
            return
        if tag in self.BLOCKS:
            if self.blocks:
                self.stack = []
                self.add("\n")
            self.blocks += 1
            self.in_block = True
            self.empty_block = "empty" in css.get("-qt-paragraph-type", "")
        style = _style(css, {**self.current(), **self.TAG_STYLES.get(tag, {})})
        self.stack.append((tag, style))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("head", "style", "script", "title"):
            self.skip = max(0, self.skip - 1)
            return
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag in self.BLOCKS:
            self.in_block = False

    def handle_data(self, data: str) -> None:
        if self.skip or not self.in_block:
            return  # markup whitespace between blocks (Qt puts all text in blocks)
        self.add(data)


def rich_text(html: str) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """(plain text, runs, the body's style) of a note's HTML; plain text stays as it is."""
    if "<" not in html:
        return html, [{"text": html}] if html else [], {}
    parser = _RichText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001  (malformed markup: keep what was read)
        pass
    runs = [run for run in parser.runs if run["text"]]
    return "".join(run["text"] for run in runs), runs, parser.base


# ---- Items ------------------------------------------------------------------------------------------


def _place(item: PurItem, row: dict[str, Any], board: PurBoard) -> None:
    item.id = int(row.get("id") or 0)
    try:
        item.z = float(row.get("z") or 0.0)
        item.opacity = max(0.0, min(1.0, float(1.0 if row.get("opacity") is None else row["opacity"])))
    except (TypeError, ValueError):
        pass
    item.locked = bool(row.get("locked"))
    item.name = str(row.get("name") or "")
    try:
        transform = qtransform(row.get("transform"))
    except PureRefError:
        transform = None
    if transform is None:
        board.warnings.append(f"Item {item.id} has no readable placement")
    else:
        item.matrix, item.x, item.y = transform


def _image(row: dict[str, Any], image_rows: dict[int, dict[str, Any]], board: PurBoard) -> PurImage:
    image = PurImage()
    file = image_rows.get(int(row.get("image") if row.get("image") is not None else -1))
    if file is None:
        board.warnings.append(f"Image item {row.get('id')} has no image")
    else:
        data = file.get("data")
        image.data = data if isinstance(data, bytes) and data else None
        image.source = str(file.get("source") or "")
    transform = qtransform(row.get("image_transform"))
    if transform is not None:
        image.inner, *offset = transform
        image.offset = (offset[0], offset[1])
    elif file is not None and file.get("width") and file.get("height"):
        image.offset = (-float(file["width"]) / 2.0, -float(file["height"]) / 2.0)
    bounds = qvariant(row.get("image_bounds"))
    if bounds is not None and bounds[1] == "QPainterPath":
        image.points = [point for path in painter_path(bounds[2]) for point in path]
    return image


def _note(row: dict[str, Any]) -> PurText:
    text, runs, base = rich_text(str(row.get("text") or ""))
    note = PurText(text=text)
    default = argb(row.get("text_color"), (255, 255, 255, 255))
    note.color = base.get("color", default)
    for run in runs:
        run.setdefault("color", note.color)
        for key, value in base.items():
            run.setdefault(key, value)
    note.runs = runs or None
    note.background = argb(row.get("background_color"), (0, 0, 0, 19))
    size = qsizef(row.get("fixed_size"))
    if size is not None and size[0] > 0:
        note.width = size[0]
    return note


def _drawing(row: dict[str, Any]) -> PurDrawing:
    drawing = PurDrawing()
    parsed = qvariant(row.get("strokes"))
    if parsed is None or not parsed[1].startswith("QList<"):
        raise PureRefError("unknown stroke data")
    reader = parsed[2]
    for _ in range(reader.u32()):
        reader.skip(1)  # always 100 in the boards seen
        color = _qcolor(*reader.unpack(">b5H"))
        width = reader.f64()
        paths = painter_path(reader)
        reader.skip(16 + 4)  # the drag's start point, and a kind (2)
        for points in paths:
            if points:
                drawing.strokes.append(PurStroke(color=color, width=width, points=points))
    if reader.pos != len(reader.data):
        raise PureRefError("unexpected stroke data")
    return drawing


def parse_board_v2(data: bytes, version: str, header_end: int) -> PurBoard:
    board = PurBoard(version=version)
    db = extract_database(data, header_end)
    con, temp = open_database(db)
    try:
        try:
            tables = {(row[0] or b"").decode("utf-8", errors="replace") for row in
                      con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            missing = [t for t in REQUIRED_TABLES if t not in tables]
            if missing:
                raise PureRefError(f"Not a PureRef 2 board (no {missing[0]} table)")
            rows = {name: table_rows(con, name) if name in tables else [] for name in
                    ("metadata", "items", "items_images", "images", "items_notes", "items_drawings", "items_groups")}
        except sqlite3.DatabaseError as exc:
            raise PureRefError(f"The board's database is damaged ({exc})") from exc
    finally:
        con.close()
        if temp is not None:
            try:
                os.unlink(temp)
            except OSError:
                pass
    for meta in rows["metadata"][:1]:
        board.app_version = str(meta.get("application_version") or "")
        board.folder = str(meta.get("last_save_path") or "")
    _build_items(board, rows)
    return board


def _build_items(board: PurBoard, rows: dict[str, list[dict[str, Any]]]) -> None:
    image_rows = {int(r["id"]): r for r in rows["images"] if r.get("id") is not None}
    kinds: dict[int, tuple[str, dict[str, Any]]] = {}
    for kind in ("items_images", "items_notes", "items_drawings", "items_groups"):
        for row in rows[kind]:
            if row.get("id") is not None:
                kinds[int(row["id"])] = (kind, row)
    items: dict[int, PurItem] = {}
    parents: dict[int, int] = {}
    unknown = 0
    for row in rows["items"]:
        if row.get("id") is None:
            continue
        item_id = int(row["id"])
        kind, detail = kinds.get(item_id, ("", {}))
        try:
            if kind == "items_images":
                item: PurItem = _image(detail, image_rows, board)
            elif kind == "items_notes":
                item = _note(detail)
            elif kind == "items_drawings":
                item = _drawing(detail)
            elif kind == "items_groups":
                item = PurGroup(background=argb(detail.get("background_color"), (0, 0, 0, 0)))
            else:
                unknown += 1
                continue
        except PureRefError as exc:
            board.warnings.append(f"Skipped item {item_id} ({exc})")
            continue
        _place(item, row, board)
        items[item_id] = item
        parent = row.get("parent")
        parents[item_id] = int(parent) if parent is not None else -1
    if unknown:
        board.warnings.append(f"Skipped {unknown} item(s) of a kind pixelRef doesn't know")
    for item_id, item in items.items():
        parent_id = parents[item_id]
        parent = items.get(parent_id) if parent_id != item_id else None
        ancestor, hops = parent_id, 0  # refuse parent loops
        while ancestor in parents and hops <= len(parents):
            if ancestor == item_id:
                parent = None
                break
            ancestor, hops = parents[ancestor], hops + 1
        if parent is None:
            if parent_id != -1:
                board.warnings.append(f"Item {item_id} belongs to a missing item: put on the board")
            board.items.append(item)
        else:
            parent.children.append(item)
