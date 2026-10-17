"""The built-in effects' pixel work, with Qt only (no numpy).

Each processor is ``fn(image, params, fx) -> QImage``: ``image`` is ARGB32
(not premultiplied), ``params`` are the effect's resolved parameters and
``fx`` an :class:`EffectContext`. Processors may run on a worker thread, so
they use QImage and QPainter on images only (never QPixmap or widgets).

Tone curves (brightness, threshold, posterize…) are 256-entry lookup tables
applied with ``bytes.translate``, which runs over every byte in C, so they
stay cheap on big images. Edges come from difference blends of the image with
itself shifted by a pixel; blur from smooth down- and up-scaling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter

Processor = Callable[[QImage, dict[str, Any], "EffectContext"], QImage]


@dataclass
class EffectContext:
    pixels_per_unit: float = 1.0  # image pixels per canvas pixel (sizes like a blur radius are in canvas pixels)
    playing: bool = False  # an animation is playing


# ---- Helpers ------------------------------------------------------------------------------------------


def argb(image: QImage) -> QImage:
    return image if image.format() == QImage.Format_ARGB32 else image.convertToFormat(QImage.Format_ARGB32)


def alpha_of(image: QImage) -> QImage | None:
    """The alpha channel (Alpha8), or None for an opaque image."""
    if not image.hasAlphaChannel():
        return None
    return image.convertToFormat(QImage.Format_Alpha8)


def with_alpha(rgb: QImage, alpha: QImage | None) -> QImage:
    """``rgb``'s colours with ``alpha`` as its alpha channel (replaced, not multiplied)."""
    opaque = rgb.convertToFormat(QImage.Format_RGB32)  # drop any alpha first: setAlphaChannel multiplies
    if alpha is None:
        return opaque.convertToFormat(QImage.Format_ARGB32)
    opaque.setAlphaChannel(alpha)
    return opaque.convertToFormat(QImage.Format_ARGB32)


def apply_lut(image: QImage, table: bytes) -> QImage:
    """``table`` (256 bytes) applied to the red, green and blue of every pixel; alpha kept."""
    source = argb(image)
    alpha = alpha_of(source)
    data = bytes(source.constBits()).translate(table)  # alpha bytes too: dropped below
    result = QImage(data, source.width(), source.height(), source.bytesPerLine(), QImage.Format_RGB32).copy()
    return with_alpha(result, alpha)


def gray8(image: QImage) -> QImage:
    return argb(image).convertToFormat(QImage.Format_Grayscale8)


def apply_lut_gray(gray: QImage, table: bytes) -> QImage:
    data = bytes(gray.constBits()).translate(table)
    return QImage(data, gray.width(), gray.height(), gray.bytesPerLine(), QImage.Format_Grayscale8).copy()


def lut(fn: Callable[[float], float]) -> bytes:
    """A lookup table from ``fn`` on 0..1 values (clamped)."""
    return bytes(max(0, min(255, round(fn(i / 255.0) * 255.0))) for i in range(256))


def blend(under: QImage, over: QImage, amount: float) -> QImage:
    """``over`` mixed over ``under`` by ``amount`` (0..1), alpha included."""
    if amount >= 1.0:
        return over
    if amount <= 0.0:
        return under
    result = QImage(under.size(), QImage.Format_ARGB32_Premultiplied)
    result.fill(0)
    painter = QPainter(result)
    painter.setCompositionMode(QPainter.CompositionMode_Source)
    painter.drawImage(0, 0, under)
    painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
    painter.setOpacity(amount)
    # Draw "over" through a copy of "under"'s footprint, so transparent areas stay transparent.
    painter.drawImage(0, 0, over)
    painter.end()
    return result.convertToFormat(QImage.Format_ARGB32)


# ---- Colour & tone ------------------------------------------------------------------------------------


def grayscale(image: QImage, params: dict[str, Any], fx: EffectContext) -> QImage:
    source = argb(image)
    gray = with_alpha(source.convertToFormat(QImage.Format_Grayscale8), alpha_of(source))
    return blend(source, gray, float(params.get("amount", 1.0)))


def invert(image: QImage, params: dict[str, Any], fx: EffectContext) -> QImage:
    result = argb(image).copy()
    result.invertPixels(QImage.InvertRgb)
    return result


def brightness_contrast(image: QImage, params: dict[str, Any], fx: EffectContext) -> QImage:
    brightness = float(params.get("brightness", 0.0))
    contrast = float(params.get("contrast", 0.0))
    # -1 → flat grey, 0 → unchanged, towards +1 → steep (no division by zero at 1).
    slope = 1.0 + contrast if contrast <= 0 else 1.0 / max(0.02, 1.0 - contrast)
    return apply_lut(image, lut(lambda v: (v - 0.5) * slope + 0.5 + brightness))


def threshold(image: QImage, params: dict[str, Any], fx: EffectContext) -> QImage:
    level = float(params.get("level", 0.5))
    source = argb(image)
    bw = apply_lut_gray(gray8(source), lut(lambda v: 1.0 if v >= level else 0.0))
    return with_alpha(bw, alpha_of(source))


def posterize(image: QImage, params: dict[str, Any], fx: EffectContext) -> QImage:
    steps = max(2, int(params.get("levels", 4)))
    return apply_lut(image, lut(lambda v: round(v * (steps - 1)) / (steps - 1)))


# ---- Blur ---------------------------------------------------------------------------------------------


def blur(image: QImage, params: dict[str, Any], fx: EffectContext) -> QImage:
    """Shrink smoothly (averaging), then scale back up (smooth interpolation): a soft blur
    whose cost falls as the radius grows. Edges are padded so they don't darken."""
    radius = float(params.get("radius", 4.0)) * fx.pixels_per_unit
    source = argb(image)
    if radius < 0.5 or source.isNull():
        return source
    width, height = source.width(), source.height()
    pad = int(math.ceil(radius * 2))
    padded = QImage(width + 2 * pad, height + 2 * pad, QImage.Format_ARGB32_Premultiplied)
    padded.fill(0)
    painter = QPainter(padded)
    painter.drawImage(pad, pad, source)
    # Extend the border pixels outwards (clamp), so the blur doesn't pull in transparency.
    for rect, src in (
        (QRect(0, pad, pad, height), QRect(0, 0, 1, height)),
        (QRect(pad + width, pad, pad, height), QRect(width - 1, 0, 1, height)),
        (QRect(pad, 0, width, pad), QRect(0, 0, width, 1)),
        (QRect(pad, pad + height, width, pad), QRect(0, height - 1, width, 1)),
    ):
        painter.drawImage(rect, source, src)
    for corner, point in ((QRect(0, 0, pad, pad), QPoint(0, 0)), (QRect(pad + width, 0, pad, pad), QPoint(width - 1, 0)),
                          (QRect(0, pad + height, pad, pad), QPoint(0, height - 1)),
                          (QRect(pad + width, pad + height, pad, pad), QPoint(width - 1, height - 1))):
        painter.fillRect(corner, source.pixelColor(point))
    painter.end()
    factor = max(1.0, radius / 1.5)
    small = QSize(max(1, round(padded.width() / factor)), max(1, round(padded.height() / factor)))
    shrunk = padded
    # Halve repeatedly for a true average (one big smooth scale skips pixels).
    while shrunk.width() / 2 >= small.width() and shrunk.height() / 2 >= small.height():
        shrunk = shrunk.scaled(max(1, shrunk.width() // 2), max(1, shrunk.height() // 2),
                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    shrunk = shrunk.scaled(small, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    grown = shrunk.scaled(padded.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    if factor >= 3:  # a second, smaller pass hides the bilinear grid of large radii
        grown = grown.scaled(max(1, padded.width() // 2), max(1, padded.height() // 2), Qt.IgnoreAspectRatio,
                             Qt.SmoothTransformation).scaled(padded.size(), Qt.IgnoreAspectRatio,
                                                             Qt.SmoothTransformation)
    result = grown.copy(pad, pad, width, height).convertToFormat(QImage.Format_ARGB32)
    alpha = alpha_of(source)
    return with_alpha(result, alpha) if alpha is not None else result


# ---- Edges --------------------------------------------------------------------------------------------


def _difference(gray_rgb: QImage, dx: int, dy: int) -> QImage:
    """|image - image shifted by (dx, dy)|, with the uncovered border cleared."""
    result = gray_rgb.copy()
    painter = QPainter(result)
    painter.setCompositionMode(QPainter.CompositionMode_Difference)
    painter.drawImage(dx, dy, gray_rgb)
    painter.setCompositionMode(QPainter.CompositionMode_Source)
    black = QColor(0, 0, 0)
    if dx:
        painter.fillRect(0 if dx > 0 else result.width() - 1, 0, 1, result.height(), black)
    if dy:
        painter.fillRect(0, 0 if dy > 0 else result.height() - 1, result.width(), 1, black)
    painter.end()
    return result


def edge_magnitude(image: QImage, strength: float, cutoff: float) -> QImage:
    """Grayscale8 edge strength: horizontal + vertical (+ diagonal) differences."""
    source = argb(image)
    # Judge edges on the picture over white, so a transparent background reads as paper.
    flat = QImage(source.size(), QImage.Format_RGB32)
    flat.fill(QColor(255, 255, 255))
    painter = QPainter(flat)
    painter.drawImage(0, 0, source)
    painter.end()
    gray = flat.convertToFormat(QImage.Format_Grayscale8).convertToFormat(QImage.Format_RGB32)
    total = _difference(gray, 1, 0)
    painter = QPainter(total)
    painter.setCompositionMode(QPainter.CompositionMode_Plus)
    painter.drawImage(0, 0, _difference(gray, 0, 1))
    painter.setOpacity(0.5)
    painter.drawImage(0, 0, _difference(gray, 1, 1))
    painter.drawImage(0, 0, _difference(gray, -1, 1))
    painter.end()
    span = max(1e-6, 1.0 - cutoff)
    return apply_lut_gray(total.convertToFormat(QImage.Format_Grayscale8),
                          lut(lambda v: (v * strength - cutoff) / span))


def _colored(mask: QImage, color: QColor) -> QImage:
    """``color`` everywhere, with ``mask`` (Grayscale8) as its alpha."""
    solid = QImage(mask.size(), QImage.Format_RGB32)
    solid.fill(color)
    solid.setAlphaChannel(mask)
    return solid


def edge_detect(image: QImage, params: dict[str, Any], fx: EffectContext) -> QImage:
    source = argb(image)
    edges = edge_magnitude(source, float(params.get("strength", 2.0)), float(params.get("threshold", 0.1)))
    style = str(params.get("style", "lines"))
    color_values = params.get("color") or [0, 0, 0]
    color = QColor(*[int(c) for c in color_values[:3]])
    alpha = alpha_of(source)
    if style == "glow":
        return with_alpha(edges.convertToFormat(QImage.Format_RGB32), alpha)
    result = QImage(source.size(), QImage.Format_ARGB32_Premultiplied)
    result.fill(0)
    painter = QPainter(result)
    if style == "overlay":
        painter.drawImage(0, 0, source)
    else:  # lines: paper where the picture was, lines on top (they may reach a little past it)
        paper = QImage(source.size(), QImage.Format_RGB32)
        paper.fill(QColor(255, 255, 255))
        painter.drawImage(0, 0, with_alpha(paper, alpha))
    painter.drawImage(0, 0, _colored(edges, color))
    painter.end()
    return result.convertToFormat(QImage.Format_ARGB32)


BUILTIN_PROCESSORS: dict[str, Processor] = {
    "grayscale": grayscale,
    "invert": invert,
    "brightness_contrast": brightness_contrast,
    "threshold": threshold,
    "posterize": posterize,
    "blur": blur,
    "edge_detect": edge_detect,
}


def run_stack(image: QImage, steps: list[tuple[Processor, dict[str, Any], float]], fx: EffectContext) -> QImage:
    """Apply ``(processor, params, mix)`` steps in order; returns ARGB32."""
    current = argb(image)
    for processor, params, mix in steps:
        if mix <= 0.0:
            continue
        processed = argb(processor(current, params, fx))
        current = blend(current, processed, mix) if mix < 1.0 else processed
    return current
