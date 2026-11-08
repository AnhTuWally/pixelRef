"""Reading and writing one private ancillary chunk in a PNG file.

Knows nothing about scenes: it stores opaque bytes under a four-letter chunk
type and finds them again. The chunk is placed before the first ``IDAT``,
carries a CRC like every PNG chunk, and uses a private, unsafe-to-copy name
(``pxRF``) so viewers ignore it and editors that re-encode the pixels drop it
instead of carrying stale content along.

Reading streams over the file's chunk headers and seeks past the pixel data, so
probing a large PNG (or a dropped file that is not a PNG) reads almost nothing.
"""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path
from typing import BinaryIO

SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_CHUNK = 0x7FFFFFFF  # PNG chunk lengths are 31-bit


class PngError(ValueError):
    """The data is not a well-formed PNG (or the payload cannot be stored)."""


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def insert_chunk(png: bytes, kind: bytes, payload: bytes) -> bytes:
    """``png`` with a chunk added before its first ``IDAT`` (an existing ``kind`` chunk is replaced)."""
    if len(kind) != 4:
        raise PngError("chunk type must be 4 bytes")
    if len(payload) > MAX_CHUNK:
        raise PngError("payload too large for a PNG chunk")
    if not png.startswith(SIGNATURE):
        raise PngError("not a PNG")
    out = bytearray(SIGNATURE)
    position = len(SIGNATURE)
    inserted = False
    while position + 12 <= len(png):
        (length,) = struct.unpack_from(">I", png, position)
        end = position + 12 + length
        if end > len(png):
            raise PngError("truncated PNG")
        this = bytes(png[position + 4:position + 8])
        if this == kind:
            position = end
            continue
        if this == b"IDAT" and not inserted:
            out += _chunk(kind, payload)
            inserted = True
        out += png[position:end]
        position = end
    if not inserted:
        raise PngError("PNG has no image data")
    return bytes(out)


def write_atomic(path: str | Path, data: bytes) -> None:
    """Write ``data`` to a sibling file and swap it in: a failed write keeps the old file."""
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    try:
        with open(temp, "wb") as handle:
            handle.write(data)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def _read_chunks(handle: BinaryIO, kind: bytes, limit: int, size_only: bool = False) -> bytes | int | None:
    if handle.read(len(SIGNATURE)) != SIGNATURE:
        return None
    while True:
        header = handle.read(8)
        if len(header) < 8:
            return None
        length, this = struct.unpack(">I4s", header)
        if this in (b"IDAT", b"IEND"):
            return None  # the chunk is written before the pixel data; nothing later is scanned
        if this != kind:
            handle.seek(length + 4, os.SEEK_CUR)  # skip payload and CRC
            continue
        if size_only:
            return length
        if length > limit:
            raise PngError(f"embedded chunk is {length:,} bytes, over the {limit:,} byte limit")
        payload = handle.read(length)
        crc = handle.read(4)
        if len(payload) != length or len(crc) != 4:
            raise PngError("truncated chunk")
        if struct.unpack(">I", crc)[0] != zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF:
            raise PngError("chunk checksum mismatch")
        return payload


def read_chunk(path: str | Path, kind: bytes, limit: int) -> bytes | None:
    """The payload of the first ``kind`` chunk, or None if the file is not a PNG or has none.

    Raises PngError for a damaged chunk or one larger than ``limit`` bytes.
    """
    try:
        with open(path, "rb") as handle:
            return _read_chunks(handle, kind, limit)
    except (OSError, struct.error):
        return None


def chunk_size(path: str | Path, kind: bytes) -> int | None:
    """The payload size of the first ``kind`` chunk without reading it (None: no such chunk)."""
    try:
        with open(path, "rb") as handle:
            return _read_chunks(handle, kind, 0, size_only=True)  # type: ignore[return-value]
    except (OSError, struct.error):
        return None
