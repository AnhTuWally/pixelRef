"""``ExrReader``: what the image plugin's ``ImageHandle`` reads EXRs with.

It quacks like the parts of ``QImageReader`` that ``open_handle`` and
``ImageHandle.decode`` use (see ``builtins/image/source.py``), so EXR images
get the same background decoding, levels and memory budget as every other
image. ``setScaledSize`` is honoured: a level is box-filtered from the float
pixels before the view transform, so a zoomed-out EXR costs little.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage, QImageIOHandler

from . import decode


def view_of(attrs: dict[str, Any]) -> tuple[str, float, float, str]:
    """(layer, exposure, gamma, channel) of a node's attributes, with defaults."""
    try:
        exposure = float(attrs.get("exposure", 0.0) or 0.0)
    except (TypeError, ValueError):
        exposure = 0.0
    try:
        gamma = float(attrs.get("gamma", 1.0) or 1.0)
    except (TypeError, ValueError):
        gamma = 1.0
    channel = str(attrs.get("channel", "rgb") or "rgb")
    return (str(attrs.get("exr_layer", "") or ""), exposure, gamma, channel if channel in decode.CHANNELS else "rgb")


class ExrReader:
    def __init__(self, source: Any, attrs: dict[str, Any]) -> None:
        self.source = source  # a file path (str) or the embedded bytes
        self.layer_name, self.exposure, self.gamma, self.channel = view_of(attrs)
        self.info = decode.info_of(source)
        self._scaled: QSize | None = None

    # ---- QImageReader, the parts ImageHandle uses -------------------------------------------

    def canRead(self) -> bool:  # noqa: N802
        return self.info is not None and decode.resolve_layer(self.info, self.layer_name) is not None

    def size(self) -> QSize:
        size = self.info.size if self.info is not None else None
        return QSize(*size) if size else QSize()

    def transformation(self) -> Any:
        return QImageIOHandler.Transformation.TransformationNone

    def supportsOption(self, option: Any) -> bool:  # noqa: N802
        return option == QImageIOHandler.ImageOption.ScaledSize

    def setAutoTransform(self, _on: bool) -> None:  # noqa: N802
        pass

    def setQuality(self, _quality: int) -> None:  # noqa: N802
        pass

    def setScaledSize(self, size: QSize) -> None:  # noqa: N802
        self._scaled = QSize(size)

    def read(self) -> QImage:
        if self.info is None:
            return QImage()
        layer = decode.resolve_layer(self.info, self.layer_name)
        if layer is None:
            return QImage()
        array = decode.FLOATS.get(self.source, layer)
        target = self._scaled if self._scaled is not None and self._scaled.isValid() else self.size()
        # At least the target size: the image plugin smooths the rest (``shrink``).
        reduced = decode.box_reduce(array, target.width(), target.height())
        pixels = decode.view_transform(reduced, self.exposure, self.gamma, self.channel)
        return decode.to_qimage(pixels)
