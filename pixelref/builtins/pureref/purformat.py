"""Reading PureRef ``.pur`` boards: the data model, and the 1.x format.

No Qt, no pixelRef imports: this module only turns the bytes of a ``.pur``
file into plain data (:class:`PurBoard`). The ``pureref`` plugin maps that data
onto pixelRef nodes. :func:`read_board` reads both formats: 1.x (PureRef
1.10–1.11, below) and 2.x (PureRef 2, in :mod:`.pur2format`).

Credit
------
The layout of the 1.x format was worked out by Fyor in the PureRef-format project,
and this reader is a port of its ``purformat/read.py`` and ``pur.hexpat``:
https://github.com/FyorDev/PureRef-format

    MIT License

    Copyright (c) 2023 Fyor

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.

Changes from the original reader:

- it reads with a cursor instead of deleting bytes from the front of the
  buffer (which copied the rest of the file for every field);
- images are located through the address table at the end of the file
  rather than by searching for PNG signatures, so non-PNG and linked images
  are found too;
- colours are decoded as Qt ``QColor`` streams (RGB, HSV, HSL and CMYK);
- every read is bounds-checked, and a damaged file raises
  :class:`PureRefError` instead of looping or raising ``IndexError``.

Layout (big-endian, Qt ``QDataStream``)
---------------------------------------
::

    header (224 bytes)  version string "1.10", item counts, offset of the
                        tail, an MD5 checksum, canvas rect, zoom, view centre
    images              the image files, back to back; an image used by more
                        than one item is stored once and later items get a
                        4-byte slot holding the id of the first one;
                        0xFFFFFFFF marks a linked (not embedded) image
    items               GraphicsImageItem / GraphicsTextItem records, each
                        starting with its own end offset; text attached to an
                        item follows that item's record
    tail                the board's folder (a string), then one 20-byte record
                        per image item: item id, start and end of its bytes
"""

from __future__ import annotations

import colorsys
import struct
from dataclasses import dataclass, field
from pathlib import Path

HEADER_SIZE = 224
IMAGE_ITEM = "GraphicsImageItem"
TEXT_ITEM = "GraphicsTextItem"
LINKED_SLOT = b"\xff\xff\xff\xff"
MAX_DEPTH = 64  # text attached to text attached to …
MAX_POINTS = 100_000


class PureRefError(ValueError):
    """The file isn't a PureRef board this reader understands."""


@dataclass
class PurItem:
    """What every item has. ``matrix`` and ``x``/``y`` (a Qt ``QTransform``) place
    it in its parent item, or on the board; ``z`` orders it among its siblings."""

    id: int = 0
    z: float = 0.0
    matrix: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 1.0)  # m11, m12, m21, m22
    x: float = 0.0
    y: float = 0.0
    name: str = ""
    opacity: float = 1.0
    locked: bool = False
    children: list["PurItem"] = field(default_factory=list)


@dataclass
class PurText(PurItem):
    """A text note, its top-left corner on the item origin.

    ``runs`` (PureRef 2) holds the formatted pieces of the text:
    ``{"text", "color", "highlight", "bold", "italic", "underline", "strike",
    "font_px", "font_family"}``, each style only when the text sets it."""

    text: str = ""
    color: tuple[int, int, int, int] = (255, 255, 255, 255)  # 8-bit RGBA
    background: tuple[int, int, int, int] = (0, 0, 0, 19)
    runs: list[dict] | None = None
    width: float = 0.0  # a fixed text width, 0 for none


@dataclass
class PurImage(PurItem):
    """An image on the board.

    Item coordinates put the image's pixel (0, 0) at ``offset``, through
    ``inner`` (identity, apart from PureRef 2 boards): an uncropped image is
    centred on the item origin. ``points`` outline the visible part in item
    coordinates: the crop.
    """

    source: str = ""  # the file it came from (a path or URL), may be empty
    data: bytes | None = None  # the image file; None when it's linked to ``source``
    offset: tuple[float, float] = (0.0, 0.0)
    inner: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 1.0)
    crop_scale: float = 1.0
    points: list[tuple[float, float]] = field(default_factory=list)

    def to_pixels(self, x: float, y: float) -> tuple[float, float]:
        """Item coordinates → image pixels."""
        a, b, c, d = self.inner
        det = a * d - b * c or 1e-12
        x, y = x - self.offset[0], y - self.offset[1]
        return (d * x - c * y) / det, (-b * x + a * y) / det

    @property
    def crop_box(self) -> tuple[float, float, float, float] | None:
        """Bounding box [x, y, w, h] of the visible part, in image pixels."""
        if not self.points:
            return None
        pixels = [self.to_pixels(*p) for p in self.points]
        xs = [p[0] for p in pixels]
        ys = [p[1] for p in pixels]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


@dataclass
class PurStroke:
    color: tuple[int, int, int, int] = (255, 255, 255, 255)
    width: float = 1.0
    points: list[tuple[float, float]] = field(default_factory=list)  # item coordinates


@dataclass
class PurDrawing(PurItem):
    """Pen strokes (PureRef 2)."""

    strokes: list[PurStroke] = field(default_factory=list)


@dataclass
class PurGroup(PurItem):
    """A group of items (PureRef 2); its items are its children."""

    background: tuple[int, int, int, int] = (0, 0, 0, 0)


@dataclass
class PurBoard:
    version: str = ""
    app_version: str = ""
    canvas: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    zoom: float = 1.0
    view: tuple[int, int] = (0, 0)
    folder: str = ""
    items: list[PurItem] = field(default_factory=list)  # top level, in file order
    warnings: list[str] = field(default_factory=list)

    def walk(self) -> list[PurItem]:
        """Every item, attached and grouped ones included."""
        found: list[PurItem] = []

        def visit(items: list[PurItem]) -> None:
            for item in items:
                found.append(item)
                visit(item.children)

        visit(self.items)
        return found

    @property
    def images(self) -> list[PurImage]:
        return [item for item in self.walk() if isinstance(item, PurImage)]

    @property
    def texts(self) -> list[PurText]:
        return [item for item in self.walk() if isinstance(item, PurText)]

    @property
    def drawings(self) -> list[PurDrawing]:
        return [item for item in self.walk() if isinstance(item, PurDrawing)]


def _qcolor(spec: int, alpha: int, c1: int, c2: int, c3: int, c4: int) -> tuple[int, int, int, int]:
    """A QDataStream colour (16-bit components) as 8-bit RGBA."""
    if spec == 2:  # Hsv: hue in 1/100°, 0xFFFF for greys
        hue = 0.0 if c1 == 0xFFFF else (c1 / 36000.0) % 1.0
        rgb = colorsys.hsv_to_rgb(hue, c2 / 65535.0, c3 / 65535.0)
    elif spec == 4:  # Hsl
        hue = 0.0 if c1 == 0xFFFF else (c1 / 36000.0) % 1.0
        rgb = colorsys.hls_to_rgb(hue, c3 / 65535.0, c2 / 65535.0)
    elif spec == 3:  # Cmyk
        k = 1.0 - c4 / 65535.0
        rgb = tuple((1.0 - c / 65535.0) * k for c in (c1, c2, c3))
    else:  # Rgb (and anything else: read the first three components as RGB)
        rgb = (c1 / 65535.0, c2 / 65535.0, c3 / 65535.0)
    r, g, b = (max(0, min(255, round(v * 255))) for v in rgb)
    return r, g, b, alpha >> 8


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def need(self, size: int, at: int | None = None) -> int:
        start = self.pos if at is None else at
        if start < 0 or start + size > len(self.data):
            raise PureRefError(f"The file ends unexpectedly (reading {size} bytes at {start})")
        return start

    def unpack(self, fmt: str) -> tuple:
        size = struct.calcsize(fmt)
        values = struct.unpack_from(fmt, self.data, self.need(size))
        self.pos += size
        return values

    def peek(self, fmt: str, at: int | None = None) -> int | float:
        return struct.unpack_from(fmt, self.data, self.need(struct.calcsize(fmt), at))[0]

    def u32(self) -> int:
        return self.unpack(">I")[0]

    def f64(self) -> float:
        return self.unpack(">d")[0]

    def skip(self, size: int) -> None:
        self.need(size)
        self.pos += size

    def string(self) -> str:
        """A QString: byte length, then UTF-16BE; 0xFFFFFFFF for a null string."""
        length = self.u32()
        if length == 0xFFFFFFFF:
            return ""
        start = self.need(length)
        self.pos += length
        return self.data[start:start + length].decode("utf-16-be", errors="replace")

    def matrix(self) -> tuple[float, float, float, float]:
        m11, m12, _m13, m21, m22, _m23 = self.unpack(">6d")
        return m11, m12, m21, m22

    def color(self) -> tuple[int, int, int, int]:
        return _qcolor(*self.unpack(">b5H"))

    def item_type(self, at: int) -> str | None:
        """The type name of an item record starting at ``at``, if one does."""
        if at + 12 > len(self.data):
            return None
        length = self.peek(">I", at + 8)
        if length not in (2 * len(IMAGE_ITEM), 2 * len(TEXT_ITEM)) or at + 12 + length > len(self.data):
            return None
        name = self.data[at + 12:at + 12 + length].decode("utf-16-be", errors="replace")
        return name if name in (IMAGE_ITEM, TEXT_ITEM) else None


def read_board(path: str | Path) -> PurBoard:
    """Read a ``.pur`` file (PureRef 1.10–1.11 or 2.x)."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise PureRefError(f"Can't read {path}: {exc.strerror or exc}") from exc
    return parse_board(data)


def format_version(data: bytes) -> tuple[str, int]:
    """The format version string the file starts with, and where it ends."""
    if len(data) < 16:
        raise PureRefError("Not a PureRef board")
    length = struct.unpack_from(">I", data, 0)[0]
    if length == 0 or length > 32 or length % 2 or 4 + length > len(data):
        raise PureRefError("Not a PureRef board")
    version = data[4:4 + length].decode("utf-16-be", errors="replace")
    if not version.replace(".", "").isdigit():
        raise PureRefError("Not a PureRef board")
    return version, 4 + length


def parse_board(data: bytes) -> PurBoard:
    """Parse the bytes of a ``.pur`` file."""
    data = bytes(data)
    version, header_end = format_version(data)
    if version.startswith("2."):
        import sqlite3

        from .pur2format import parse_board_v2

        try:
            return parse_board_v2(data, version, header_end)
        except PureRefError:
            raise
        except (sqlite3.Error, struct.error, ValueError, TypeError, OverflowError) as exc:
            raise PureRefError(f"The board's database is damaged ({exc})") from exc
    if not version.startswith("1."):
        raise PureRefError(f"PureRef format {version!r} isn't supported (only 1.x, from PureRef 1.10–1.11, "
                           "and 2.x, from PureRef 2)")
    return _parse_board_v1(data)


def _parse_board_v1(data: bytes) -> PurBoard:
    reader = _Reader(data)
    board = PurBoard()
    _read_header(reader, board)
    start = _first_item(reader.data)
    if start is not None:
        reader.pos = start
        while reader.item_type(reader.pos) is not None:
            board.items.append(_read_item(reader, 0))
    else:
        reader.pos = HEADER_SIZE  # an empty board
    # After the last item: the board's folder, then the table of image bytes.
    board.folder = reader.string() if reader.pos + 4 <= len(reader.data) else ""
    slots = _read_slots(reader, board, HEADER_SIZE if start is None else start)
    _attach_image_data(reader.data, board, slots)
    return board


def _read_header(reader: _Reader, board: PurBoard) -> None:
    if len(reader.data) < HEADER_SIZE or reader.peek(">I", 0) != 8:
        raise PureRefError("Not a PureRef board (or a 1.x one with an unknown header)")
    board.version = reader.data[4:12].decode("utf-16-be", errors="replace")
    board.canvas = struct.unpack_from(">4d", reader.data, 112)
    board.zoom = reader.peek(">d", 144)
    board.view = struct.unpack_from(">2i", reader.data, 216)


def _first_item(data: bytes) -> int | None:
    """Where the item records start: right after the image data.

    Found by the type name every record carries, which is quicker and safer
    than walking the image data."""
    found = []
    for name in (IMAGE_ITEM, TEXT_ITEM):
        encoded = name.encode("utf-16-be")
        at = data.find(struct.pack(">I", len(encoded)) + encoded, HEADER_SIZE + 8)
        if at >= 0:
            found.append(at - 8)
    return min(found) if found else None


def _read_slots(reader: _Reader, board: PurBoard, items_start: int) -> dict[int, tuple[int, int]]:
    """The image item id → (start, end) table of image bytes, at the end of the file."""
    slots: dict[int, tuple[int, int]] = {}
    bad = 0
    while reader.pos + 20 <= len(reader.data):
        item_id, start, end = reader.unpack(">IQQ")
        if HEADER_SIZE <= start <= end <= items_start:
            slots[item_id] = (start, end)
        else:
            bad += 1
    if bad:
        board.warnings.append(f"Ignored {bad} bad image address(es) at the end of the file")
    return slots


def _read_item(reader: _Reader, depth: int) -> PurImage | PurText:
    if depth > MAX_DEPTH:
        raise PureRefError("Text attached too deeply")
    start = reader.pos
    kind = reader.item_type(start)
    end = reader.unpack(">Q")[0]
    if kind is None or not start < end <= len(reader.data):
        raise PureRefError(f"Damaged item at byte {start}")
    reader.skip(4 + reader.peek(">I"))  # the type name
    item = _read_image(reader) if kind == IMAGE_ITEM else _read_text(reader)
    children = reader.u32()
    if reader.pos > end:
        raise PureRefError(f"Damaged item at byte {start}")
    reader.pos = end  # attached text follows the item's own record
    item.children = [_read_item(reader, depth + 1) for _ in range(children)]
    if not all(isinstance(child, PurText) for child in item.children):
        raise PureRefError(f"Damaged item at byte {start} (an image attached to an item)")
    return item


def _read_text(reader: _Reader) -> PurText:
    text = PurText()
    text.text = reader.string()
    text.matrix = reader.matrix()
    text.x, text.y = reader.f64(), reader.f64()
    reader.skip(8)  # m33 (1.0)
    text.id = reader.u32()
    text.z = reader.f64()
    text.color = reader.color()
    text.background = reader.color()
    return text


def _read_image(reader: _Reader) -> PurImage:
    image = PurImage()
    brute_force_loaded = reader.peek(">I") == 0
    if brute_force_loaded:
        reader.skip(4)
    image.source = reader.string()
    if not brute_force_loaded:
        image.name = reader.string()
    reader.skip(8)  # always 1.0
    image.matrix = reader.matrix()
    image.x, image.y = reader.f64(), reader.f64()
    reader.skip(8)  # m33 (1.0)
    image.id = reader.u32()
    image.z = reader.f64()
    reader.matrix()  # the matrix before cropping
    image.offset = (reader.f64(), reader.f64())
    image.crop_scale = reader.f64()
    count = reader.u32()
    if count > MAX_POINTS:
        raise PureRefError(f"Damaged image item {image.id} ({count} crop points)")
    image.points = []
    for _ in range(count):
        reader.skip(4)  # path element kind (move-to / line-to)
        image.points.append((reader.f64(), reader.f64()))
    reader.skip(21)  # unknown, always the same
    return image


def _attach_image_data(data: bytes, board: PurBoard, slots: dict[int, tuple[int, int]]) -> None:
    """Give every image item its bytes, following the 4-byte slots of reused images."""

    def resolve(item_id: int) -> bytes | None:
        seen: set[int] = set()
        while item_id in slots and item_id not in seen:
            seen.add(item_id)
            start, end = slots[item_id]
            chunk = data[start:end]
            if len(chunk) != 4:
                return chunk or None
            if chunk == LINKED_SLOT:
                return None
            item_id = struct.unpack(">I", chunk)[0]  # reuses that item's image
        return None

    for image in board.images:
        image.data = resolve(image.id)
        if image.data is None and image.id not in slots:
            board.warnings.append(f"No image data for item {image.id} ({image.name or image.source or 'unnamed'})")
