"""Reading an image node's pixels for extraction (Qt's decoder; works without a window).

The source is the image's own pixels cut to its ``crop``: non-destructive effects are not applied,
so extraction never needs the rendering pipeline.
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage

from pixelref.plugin_api import CommandError

from .extract import SAMPLE_BUDGET


def sample_pixels(session: Any, node: Any, budget: int = SAMPLE_BUDGET) -> bytes:
    """RGBA bytes of at most about ``budget`` pixels from the node's (cropped) source image."""
    attrs = node.attrs
    asset_id = str(attrs.get("asset", "") or "")
    path = str(attrs.get("path", "") or "")
    image = QImage()
    if asset_id:
        asset = session.scene.get_asset(asset_id)
        if asset is None:
            raise CommandError(f"'{node.name}': its embedded image is missing")
        image.loadFromData(asset.data)
    elif path:
        file = session.resolve_path(path)
        if not file.is_file():
            raise CommandError(f"'{node.name}': image file not found: {path}")
        image.load(str(file))
    else:
        raise CommandError(f"'{node.name}' has no image source")
    if image.isNull():
        raise CommandError(f"'{node.name}': the image can't be read")
    crop = attrs.get("crop")
    if crop and len(crop) == 4:
        rect = QRect(int(crop[0]), int(crop[1]), int(math.ceil(crop[2])), int(math.ceil(crop[3])))
        rect = rect.intersected(image.rect())
        if not rect.isEmpty():
            image = image.copy(rect)
    pixels = image.width() * image.height()
    if pixels > budget:
        scale = math.sqrt(budget / pixels)
        image = image.scaled(max(1, int(image.width() * scale)), max(1, int(image.height() * scale)),
                             Qt.IgnoreAspectRatio, Qt.FastTransformation)
    image = image.convertToFormat(QImage.Format_RGBA8888)
    row, width = image.bytesPerLine(), image.width() * 4
    data = bytes(image.constBits())
    if row == width:
        return data[:width * image.height()]
    return b"".join(data[y * row:y * row + width] for y in range(image.height()))
