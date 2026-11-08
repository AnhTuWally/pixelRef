"""Write page frames to a multi-page PDF (one frame per page).

The scene is painted straight into a ``QPdfWriter``: text and shapes stay vector,
and images are embedded at the resolution the page's DPI asks for (each image
item picks its level from the painter's scale, so a 150 dpi export embeds
smaller pictures than a 600 dpi one, never more than the source pixels).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QPageLayout, QPageSize, QPainter, QPdfWriter

MM_PER_INCH = 25.4


@dataclass
class PdfPage:
    source: QRectF  # what to show, in scene coordinates
    size_mm: tuple[float, float]  # page width, height


def parse_range(text: str, count: int) -> list[int]:
    """``"1-3, 5"`` → [0, 1, 2, 4] (0-based, in the order written, no repeats, within ``count``)."""
    out: list[int] = []
    for part in re.split(r"[,;\s]+", text.strip()):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if match is None:
            raise ValueError(f"'{part}' is not a page number or range")
        first = int(match.group(1))
        last = int(match.group(2) or first)
        step = 1 if last >= first else -1
        for number in range(first, last + step, step):
            if 1 <= number <= count and number - 1 not in out:
                out.append(number - 1)
    return out


def _layout(size_mm: tuple[float, float]) -> QPageLayout:
    width, height = (max(1.0, float(v)) for v in size_mm)
    page_size = QPageSize(QSizeF(width, height), QPageSize.Millimeter, "", QPageSize.ExactMatch)
    # The size already has the page's shape; Portrait keeps it as given.
    return QPageLayout(page_size, QPageLayout.Portrait, QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter)


def write_pdf(canvas: Any, pages: list[PdfPage], path: str, dpi: int = 300,
              background: QColor | None = None, title: str = "", hide: Any = ()) -> int:
    """Render ``pages`` into ``path``; returns the number of pages written."""
    if not pages:
        return 0
    dpi = max(36, min(2400, int(dpi)))
    writer = QPdfWriter(str(path))
    writer.setResolution(dpi)
    writer.setCreator("pixelRef")
    if title:
        writer.setTitle(title)
    writer.setPageLayout(_layout(pages[0].size_mm))
    canvas.flush_pending()
    painter = QPainter()
    if not painter.begin(writer):
        raise OSError(f"Could not write {path}")
    try:
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        with canvas.isolated_render(None, hide=hide, final=True):
            for index, page in enumerate(pages):
                if index:
                    writer.setPageLayout(_layout(page.size_mm))
                    writer.newPage()
                w_mm, h_mm = page.size_mm
                target = QRectF(0.0, 0.0, w_mm / MM_PER_INCH * dpi, h_mm / MM_PER_INCH * dpi)
                if background is not None and background.alpha() > 0:
                    painter.fillRect(target, background)
                canvas.scene.render(painter, target, page.source, Qt.KeepAspectRatio)
    finally:
        painter.end()
    return len(pages)
