"""Image dimensions from file headers, without decoding (headless bounds)."""

from __future__ import annotations

import struct
from pathlib import Path


def image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) of PNG, GIF, BMP, JPEG or WebP data, or None."""
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
