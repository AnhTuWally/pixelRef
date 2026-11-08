"""OpenEXR pixels to display pixels (numpy; safe on any thread).

An EXR holds linear, scene-referred values (often far above 1.0) in named
channels: ``R G B A`` for the beauty and ``diffuse.R``, ``specular.G``… for
the render's other layers (AOVs), possibly split over several *parts*. What
the board shows is a *view* of one layer:

    linear value × 2**exposure  →  channel isolate  →  clamp  →  sRGB  →  ** (1 / gamma)

Reading an EXR is the slow part, so the float pixels of the layers read last
are kept (``FLOATS``, ``PIXELREF_EXR_MEMORY_MB``, default 512): changing the
exposure, gamma or channel re-runs only the cheap view transform. Each level
the image plugin asks for (see ``builtins/image/source.py``) is box-filtered
in linear light before the view is applied, which is what a downscaled HDR
image should look like.
"""

from __future__ import annotations

import io
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from pixelref.core.imagesize import ExrInfo, exr_info, file_exr_info

MEMORY_LIMIT = int(os.environ.get("PIXELREF_EXR_MEMORY_MB", "512") or 512) * 1024 * 1024
CHANNELS = ("rgb", "r", "g", "b", "a", "luma")
CHANNEL_LABELS = {"rgb": "RGB", "r": "Red", "g": "Green", "b": "Blue", "a": "Alpha", "luma": "Luminance"}
BASE_NAMES = {"R": "R", "G": "G", "B": "B", "A": "A", "Y": "Y",
              "red": "R", "green": "G", "blue": "B", "alpha": "A", "r": "R", "g": "G", "b": "B", "a": "A"}
BASE_LABEL = "RGBA"  # what the layer menu calls the beauty ("" in the attribute)


# ---- Layers (headers only: no numpy) -----------------------------------------------------------


@dataclass(frozen=True)
class Layer:
    """Where one layer's pixels are: its part and the full names of its channels by role."""

    name: str  # "" (beauty), "diffuse", "Z", "spec/diffuse" (multi-part)…
    part: int
    roles: tuple[tuple[str, str], ...]  # (("R", "diffuse.R"), ("G", "diffuse.G"), …); role "Y" is grey

    @property
    def channel_names(self) -> list[str]:
        return [full for _role, full in self.roles]


def layer_table(info: ExrInfo) -> "OrderedDict[str, Layer]":
    """Every layer of a file, the beauty first: name -> Layer.

    Channels group by their prefix (``diffuse.R`` is layer ``diffuse``); bare
    ``R G B A Y`` make the beauty and any other bare channel (``Z``) is a layer of
    its own. In a multi-part file a part's layers are named ``part/layer`` (its
    beauty just ``part``).
    """
    table: OrderedDict[str, Layer] = OrderedDict()
    multi = len(info.parts) > 1
    for index, part in enumerate(info.parts):
        if part.deep:
            continue
        groups: dict[str, list[tuple[str, str]]] = {}
        for full in part.channels:
            prefix, _dot, suffix = full.rpartition(".")
            if not prefix and suffix not in BASE_NAMES:
                groups.setdefault(suffix, []).append(("Y", full))  # a bare data channel: Z, depth…
                continue
            role = BASE_NAMES.get(suffix)
            groups.setdefault(prefix, []).append((role or suffix, full))
        part_name = part.name or f"part{index}"
        for prefix in sorted(groups, key=lambda p: (p != "", p.lower())):
            roles = _pick_roles(groups[prefix])
            if not roles:
                continue
            name = prefix
            if multi:
                name = f"{part_name}/{prefix}" if prefix else part_name
            if name not in table:
                table[name] = Layer(name, index, roles)
    return table


def _pick_roles(channels: list[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """The channels to show for a layer: RGB(A), else Y(A), else its only/first channel as grey."""
    by_role = {role: full for role, full in channels}
    if all(role in by_role for role in "RGB"):
        roles = [("R", by_role["R"]), ("G", by_role["G"]), ("B", by_role["B"])]
    elif "Y" in by_role:
        roles = [("Y", by_role["Y"])]
    elif channels:
        rgb = [r for r in "RGB" if r in by_role]
        roles = [("Y", by_role[rgb[0]] if rgb else sorted(full for _r, full in channels)[0])]
    else:
        return ()
    if "A" in by_role:
        roles.append(("A", by_role["A"]))
    return tuple(roles)


def info_of(source: Any) -> ExrInfo | None:
    """Header info of an EXR file path or EXR bytes."""
    if isinstance(source, (bytes, bytearray, memoryview)):
        return exr_info(bytes(source[: 4 * 1024 * 1024]))
    return file_exr_info(source)


def resolve_layer(info: ExrInfo, name: str) -> Layer | None:
    """The layer called ``name``, or the file's beauty (first layer) when it has none by that name."""
    table = layer_table(info)
    if name in table:
        return table[name]
    return next(iter(table.values()), None)


def layer_label(name: str) -> str:
    return name or BASE_LABEL


def pixel_type_label(info: ExrInfo) -> str:
    types = {t for part in info.parts for t in part.pixel_types}
    return " + ".join(label for t, label in ((1, "half"), (2, "float"), (0, "uint")) if t in types)


# ---- Float pixels (numpy) ----------------------------------------------------------------------


def source_id(source: Any) -> tuple:
    """Identifies a source's content for the float cache: a path and its modification time,
    or the identity, length and ends of embedded bytes."""
    if isinstance(source, (bytes, bytearray)):
        return ("bytes", id(source), len(source), bytes(source[:64]), bytes(source[-64:]))
    try:
        stat = os.stat(source)
        return ("file", os.path.normcase(os.path.abspath(source)), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return ("file", str(source), 0, 0)


def read_layer(source: Any, layer: Layer) -> Any:
    """The layer's pixels as an H×W×4 array (RGBA; grey layers repeat Y; A is 1 when absent)."""
    import numpy as np
    import OpenEXR

    stream: Any = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else str(source)
    with OpenEXR.File(stream, separate_channels=True) as exr:
        part = exr.parts[layer.part]
        channels = {role: part.channels[full].pixels for role, full in layer.roles if full in part.channels}
    if not channels:
        raise ValueError(f"EXR layer '{layer.name}' has no readable channels")
    first = next(iter(channels.values()))
    dtype = np.float16 if all(a.dtype == np.float16 for a in channels.values()) else np.float32
    height, width = first.shape[:2]
    out = np.empty((height, width, 4), dtype=dtype)
    if "Y" in channels:
        out[..., 0] = out[..., 1] = out[..., 2] = channels["Y"]
    else:
        out[..., 0], out[..., 1], out[..., 2] = channels["R"], channels["G"], channels["B"]
    out[..., 3] = channels["A"] if "A" in channels else 1.0
    return out


class FloatCache:
    """The float pixels of recently read layers, least recently used first, within ``limit``.

    Two threads asking for the same layer at once (two levels of one image) read
    it once: the second waits for the first.
    """

    def __init__(self, limit: int = MEMORY_LIMIT) -> None:
        self.limit = limit
        self.entries: OrderedDict[tuple, Any] = OrderedDict()
        self.bytes = 0
        self.reads = 0  # files actually read (tests)
        self._lock = threading.Lock()
        self._loading: dict[tuple, threading.Event] = {}

    def get(self, source: Any, layer: Layer) -> Any:
        key = (source_id(source), layer)
        while True:
            with self._lock:
                array = self.entries.get(key)
                if array is not None:
                    self.entries.move_to_end(key)
                    return array
                event = self._loading.get(key)
                if event is None:
                    event = self._loading[key] = threading.Event()
                    break
            event.wait()  # another thread is reading it; take its result (or read it after a failure)
        try:
            array = read_layer(source, layer)
            self.reads += 1
            with self._lock:
                self.entries[key] = array
                self.bytes += array.nbytes
                while self.bytes > self.limit and len(self.entries) > 1:
                    _old, dropped = self.entries.popitem(last=False)
                    self.bytes -= dropped.nbytes
            return array
        finally:
            with self._lock:
                self._loading.pop(key, None)
            event.set()

    def clear(self) -> None:
        with self._lock:
            self.entries.clear()
            self.bytes = 0


FLOATS = FloatCache()


# ---- The view transform ------------------------------------------------------------------------


def box_reduce(array: Any, width: int, height: int) -> Any:
    """``array`` averaged over whole blocks to at least ``width`` × ``height`` (linear light)."""
    rows, cols = array.shape[:2]
    factor = max(1, min(rows // max(1, height), cols // max(1, width)))
    if factor == 1:
        return array
    rows, cols = rows // factor * factor, cols // factor * factor
    blocks = array[:rows, :cols].astype("float32").reshape(rows // factor, factor, cols // factor, factor, -1)
    return blocks.mean(axis=(1, 3))


def view_transform(array: Any, exposure: float = 0.0, gamma: float = 1.0, channel: str = "rgb") -> Any:
    """Linear RGBA (H×W×4) to premultiplied 8-bit sRGB RGBA, as numpy uint8."""
    import numpy as np

    data = np.nan_to_num(array.astype(np.float32, copy=False), nan=0.0, posinf=65504.0, neginf=0.0)
    alpha = np.clip(data[..., 3], 0.0, 1.0)
    rgb = data[..., :3]
    if channel == "a":
        color = np.repeat(alpha[..., None], 3, axis=2)
        alpha = np.ones_like(alpha)
    else:
        # EXR colour is premultiplied: undo it so the transfer curve sees the real colour.
        covered = alpha > 1e-6
        rgb = np.where(covered[..., None], rgb / np.where(covered, alpha, 1.0)[..., None], rgb)
        rgb = rgb * float(2.0 ** exposure)
        if channel in ("r", "g", "b"):
            index = "rgb".index(channel)
            color = np.repeat(rgb[..., index:index + 1], 3, axis=2)
        elif channel == "luma":
            luma = rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722
            color = np.repeat(luma[..., None], 3, axis=2)
        else:
            color = rgb
    color = np.clip(color, 0.0, 1.0)
    color = np.where(color <= 0.0031308, color * 12.92, 1.055 * np.power(color, 1.0 / 2.4) - 0.055)
    if gamma > 0 and abs(gamma - 1.0) > 1e-6:
        color = np.power(np.clip(color, 0.0, 1.0), 1.0 / gamma)
    out = np.empty(color.shape[:2] + (4,), dtype=np.uint8)
    out[..., :3] = np.clip(color * alpha[..., None] * 255.0 + 0.5, 0, 255)
    out[..., 3] = np.clip(alpha * 255.0 + 0.5, 0, 255)
    return out


def to_qimage(pixels: Any) -> Any:
    """A QImage owning a copy of premultiplied RGBA8 numpy pixels."""
    import numpy as np
    from PySide6.QtGui import QImage

    pixels = np.ascontiguousarray(pixels)
    height, width = pixels.shape[:2]
    image = QImage(pixels.data, width, height, width * 4, QImage.Format_RGBA8888_Premultiplied)
    return image.copy()  # detach from the numpy buffer
