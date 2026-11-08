"""Image dimensions from file headers, without decoding (headless bounds)."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

EXR_MAGIC = b"\x76\x2f\x31\x01"
_EXR_DEEP = 0x800
_EXR_MULTIPART = 0x1000


@dataclass
class ExrPart:
    """One part of an OpenEXR file, from its header."""

    name: str
    data_window: tuple[int, int, int, int]  # xMin, yMin, xMax, yMax
    channels: list[str] = field(default_factory=list)
    pixel_types: list[int] = field(default_factory=list)  # 0 uint, 1 half, 2 float (per channel)
    deep: bool = False

    @property
    def size(self) -> tuple[int, int]:
        x0, y0, x1, y1 = self.data_window
        return max(0, x1 - x0 + 1), max(0, y1 - y0 + 1)


@dataclass
class ExrInfo:
    """What an OpenEXR file's headers say (no pixels decoded)."""

    parts: list[ExrPart]

    @property
    def size(self) -> tuple[int, int] | None:
        return self.parts[0].size if self.parts else None


def exr_info(data: bytes) -> ExrInfo | None:
    """The parts, data windows and channel names of OpenEXR data (only its headers are read)."""
    if data[:4] != EXR_MAGIC or len(data) < 8:
        return None
    (flags,) = struct.unpack("<I", data[4:8])
    multipart = bool(flags & _EXR_MULTIPART)
    single_deep = bool(flags & _EXR_DEEP) and not multipart
    index = 8
    parts: list[ExrPart] = []
    try:
        while True:
            attrs: dict[str, tuple[str, bytes]] = {}
            if multipart and data[index] == 0:
                break  # the empty header that ends the list
            while True:
                end = data.index(b"\x00", index)
                name = data[index:end].decode("latin-1")
                index = end + 1
                if not name:
                    break
                end = data.index(b"\x00", index)
                kind = data[index:end].decode("latin-1")
                index = end + 1
                (size,) = struct.unpack("<i", data[index:index + 4])
                index += 4
                if size < 0 or index + size > len(data):
                    return None
                attrs[name] = (kind, data[index:index + size])
                index += size
            window = attrs.get("dataWindow")
            if window is None or window[0] != "box2i" or len(window[1]) != 16:
                return None
            box = tuple(int(v) for v in struct.unpack("<iiii", window[1]))
            channels, types = _exr_channels(attrs.get("channels", ("", b""))[1])
            part_name = attrs.get("name", ("", b""))[1].decode("utf-8", "replace")
            part_type = attrs.get("type", ("", b""))[1]
            parts.append(ExrPart(part_name, box, channels, types,  # type: ignore[arg-type]
                                 deep=single_deep or part_type.startswith(b"deep")))
            if not multipart:
                break
    except (struct.error, IndexError, ValueError):
        return None  # truncated headers (the caller may read more and try again)
    return ExrInfo(parts) if parts else None


def _exr_channels(value: bytes) -> tuple[list[str], list[int]]:
    """Channel names and pixel types of a ``chlist`` attribute."""
    names: list[str] = []
    types: list[int] = []
    index = 0
    while index < len(value) and value[index] != 0:
        end = value.index(b"\x00", index)
        names.append(value[index:end].decode("utf-8", "replace"))
        (pixel_type,) = struct.unpack("<i", value[end + 1:end + 5])
        types.append(int(pixel_type))
        index = end + 1 + 16  # pixel type, pLinear + 3 reserved, x and y sampling
    return names, types


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of PNG, GIF, BMP, JPEG, WebP or OpenEXR data, or None."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if data[:6] in (b"GIF87a", b"GIF89a"):
            width, height = struct.unpack("<HH", data[6:10])
            return int(width), int(height)
        if data[:2] == b"BM" and len(data) >= 26:
            width, height = struct.unpack("<ii", data[18:26])
            return abs(int(width)), abs(int(height))
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8X":
                width = int.from_bytes(data[24:27], "little") + 1
                height = int.from_bytes(data[27:30], "little") + 1
                return width, height
            if chunk == b"VP8 ":
                width, height = struct.unpack("<HH", data[26:30])
                return width & 0x3FFF, height & 0x3FFF
            if chunk == b"VP8L":
                bits = int.from_bytes(data[21:25], "little")
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if data[:4] == EXR_MAGIC:
            info = exr_info(data)
            return info.size if info is not None else None
        if data[:2] == b"\xff\xd8":
            index = 2
            orientation = 1
            while index + 9 < len(data):
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                if marker == 0xFF:  # fill byte before a marker
                    index += 1
                    continue
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    height, width = struct.unpack(">HH", data[index + 5 : index + 9])
                    if 5 <= orientation <= 8:  # EXIF: stored rotated by 90 / 270 degrees
                        width, height = height, width
                    return int(width), int(height)
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    index += 2
                    continue
                length = struct.unpack(">H", data[index + 2 : index + 4])[0]
                if marker == 0xE1:
                    orientation = _exif_orientation(data[index + 4 : index + 2 + length]) or orientation
                index += 2 + length
    except (struct.error, IndexError):
        return None
    return None


def _exif_orientation(segment: bytes) -> int | None:
    """The Orientation tag (1-8) of an APP1 Exif segment, or None."""
    if segment[:6] != b"Exif\x00\x00":
        return None
    tiff = segment[6:]
    order = {b"II": "<", b"MM": ">"}.get(tiff[:2])
    if order is None:
        return None
    try:
        (ifd,) = struct.unpack(order + "I", tiff[4:8])
        (count,) = struct.unpack(order + "H", tiff[ifd : ifd + 2])
        for entry in range(count):
            offset = ifd + 2 + entry * 12
            tag, kind = struct.unpack(order + "HH", tiff[offset : offset + 4])
            if tag == 0x0112 and kind == 3:  # Orientation, a SHORT
                (value,) = struct.unpack(order + "H", tiff[offset + 8 : offset + 10])
                return value if 1 <= value <= 8 else None
    except struct.error:
        return None
    return None


def file_image_size(path: str | Path) -> tuple[int, int] | None:
    try:
        with open(path, "rb") as handle:
            head = handle.read(256 * 1024)
    except OSError:
        return None
    return image_size(head)


def file_exr_info(path: str | Path) -> ExrInfo | None:
    """``exr_info`` of an OpenEXR file on disk (only its headers are read)."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(64 * 1024)
            info = exr_info(head)
            if info is None and head[:4] == EXR_MAGIC and len(head) == 64 * 1024:
                head += handle.read(4 * 1024 * 1024)  # big headers: many channels or much metadata
                info = exr_info(head)
    except OSError:
        return None
    return info
