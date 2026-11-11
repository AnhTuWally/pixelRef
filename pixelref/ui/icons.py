"""Small vector icons for panels, menus and tools, drawn with QPainter (no image files),
plus the app logo loaded from pixelref/resources.

Icons are drawn on a 16×16 grid at 2× resolution so they stay crisp on
high-DPI screens.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

ICON_COLOR = "#c8cdd8"
SCALE = 2


def _canvas() -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(16 * SCALE, 16 * SCALE)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(SCALE, SCALE)
    return pixmap, painter


def _pen(color: QColor, width: float = 1.4) -> QPen:
    return QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


def _draw(name: str, painter: QPainter, color: QColor) -> None:
    painter.setPen(_pen(color))
    painter.setBrush(Qt.NoBrush)
    if name == "select":
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([
            QPointF(4, 2), QPointF(4, 13.5), QPointF(7, 10.5), QPointF(9.2, 14.5),
            QPointF(11, 13.6), QPointF(8.8, 9.8), QPointF(12.5, 9.6),
        ]))
    elif name == "rect":
        painter.drawRoundedRect(QRectF(2.5, 3.5, 11, 9), 1.5, 1.5)
    elif name == "arrow":
        painter.drawLine(QPointF(3, 13), QPointF(12, 4))
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(13.5, 2.5), QPointF(13, 8), QPointF(8, 3)]))
    elif name == "pen":
        path = QPainterPath(QPointF(2, 11))
        path.cubicTo(QPointF(4, 4), QPointF(7, 4), QPointF(8, 8))
        path.cubicTo(QPointF(9, 12), QPointF(12, 12), QPointF(14, 5))
        painter.setPen(_pen(color, 1.6))
        painter.drawPath(path)
    elif name == "eraser":
        painter.save()
        painter.translate(8, 8)
        painter.rotate(-40)
        painter.drawRoundedRect(QRectF(-6, -3, 12, 6), 1.2, 1.2)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(-6, -3, 5, 6), 1.2, 1.2)
        painter.restore()
        painter.drawLine(QPointF(6, 14), QPointF(14, 14))
    elif name == "pick":
        painter.setPen(_pen(color, 1.6))
        painter.drawLine(QPointF(3, 13), QPointF(10, 6))
        painter.setBrush(color)
        painter.drawEllipse(QPointF(11.5, 4.5), 2.4, 2.4)
        painter.drawLine(QPointF(8, 4.5), QPointF(11.5, 8))
    elif name == "image":
        painter.drawRoundedRect(QRectF(2, 3, 12, 10), 1.5, 1.5)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(5.5, 6.5), 1.2, 1.2)
        painter.drawPolygon(QPolygonF([QPointF(3.5, 12), QPointF(7, 8.5), QPointF(9, 10.5),
                                       QPointF(11, 8), QPointF(12.5, 12)]))
    elif name == "movie":
        painter.drawRoundedRect(QRectF(2, 3, 12, 10), 1.5, 1.5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(6.5, 5.8), QPointF(6.5, 10.2), QPointF(10.2, 8)]))
    elif name == "text":
        painter.setPen(_pen(color, 1.8))
        painter.drawLine(QPointF(3, 3.5), QPointF(13, 3.5))
        painter.drawLine(QPointF(8, 3.5), QPointF(8, 13))
        painter.drawLine(QPointF(6, 13), QPointF(10, 13))
    elif name == "layer":
        painter.drawPolygon(QPolygonF([QPointF(8, 2.5), QPointF(14, 6), QPointF(8, 9.5), QPointF(2, 6)]))
        path = QPainterPath(QPointF(2, 9))
        path.lineTo(8, 12.5)
        path.lineTo(14, 9)
        painter.drawPath(path)
    elif name in ("eye", "eye_off"):
        path = QPainterPath(QPointF(1.5, 8))
        path.quadTo(QPointF(8, 1.5), QPointF(14.5, 8))
        path.quadTo(QPointF(8, 14.5), QPointF(1.5, 8))
        painter.drawPath(path)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(8, 8), 2.0, 2.0)
        if name == "eye_off":
            painter.setPen(_pen(color, 1.6))
            painter.drawLine(QPointF(2.5, 13.5), QPointF(13.5, 2.5))
    elif name in ("lock", "lock_open"):
        painter.setBrush(color if name == "lock" else Qt.NoBrush)
        painter.drawRoundedRect(QRectF(3.5, 7.5, 9, 6.5), 1.2, 1.2)
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath(QPointF(5.5, 7.5))
        path.lineTo(5.5, 5.5)
        path.arcTo(QRectF(5.5, 2.5, 5, 6), 180, -180)
        if name == "lock":
            path.lineTo(10.5, 7.5)
        painter.drawPath(path)
    elif name == "plus":
        painter.drawLine(QPointF(8, 3), QPointF(8, 13))
        painter.drawLine(QPointF(3, 8), QPointF(13, 8))
    elif name == "console":
        painter.drawRoundedRect(QRectF(1.5, 2.5, 13, 11), 1.5, 1.5)
        path = QPainterPath(QPointF(4, 6))
        path.lineTo(6.5, 8)
        path.lineTo(4, 10)
        painter.drawPath(path)
        painter.drawLine(QPointF(8, 10.5), QPointF(12, 10.5))
    elif name == "camera":
        painter.drawRoundedRect(QRectF(1.5, 4.5, 10, 8), 1.5, 1.5)
        painter.drawPolygon(QPolygonF([QPointF(11.5, 7.5), QPointF(14.5, 5.5), QPointF(14.5, 11.5), QPointF(11.5, 9.5)]))
    elif name == "grid":
        for x in (2.5, 8.5):
            for y in (2.5, 8.5):
                painter.drawRect(QRectF(x, y, 5, 5))
    elif name == "map":
        painter.drawRect(QRectF(1.5, 2.5, 13, 11))
        painter.setPen(_pen(color, 1.2))
        painter.drawRect(QRectF(6, 5.5, 5, 4))
    elif name == "window":
        painter.drawRect(QRectF(1.5, 2.5, 13, 11))
        painter.drawLine(QPointF(1.5, 5.5), QPointF(14.5, 5.5))
    elif name in ("split_right", "split_down", "rotate_split"):
        frame = QRectF(1.5, 2.5, 13, 11)
        painter.drawRoundedRect(frame, 1.2, 1.2)
        fill = QColor(color)
        fill.setAlpha(max(0, color.alpha() // 3))
        if name == "split_right":
            painter.fillRect(QRectF(8, 2.5, 6.5, 11), fill)
            painter.drawLine(QPointF(8, 2.5), QPointF(8, 13.5))
        elif name == "split_down":
            painter.fillRect(QRectF(1.5, 8, 13, 5.5), fill)
            painter.drawLine(QPointF(1.5, 8), QPointF(14.5, 8))
        else:
            painter.drawLine(QPointF(8, 2.5), QPointF(8, 13.5))
            painter.setPen(_pen(color, 1.1))
            painter.drawArc(QRectF(3.5, 5, 9, 6), 30 * 16, 120 * 16)
    elif name == "inspector":  # sliders: attribute values
        for y, knob in ((3.5, 10.5), (8, 5), (12.5, 9)):
            painter.drawLine(QPointF(2, y), QPointF(14, y))
            painter.setBrush(color)
            painter.drawEllipse(QPointF(knob, y), 1.9, 1.9)
            painter.setBrush(Qt.NoBrush)
    elif name == "outliner":  # a hierarchy: parent box, two children
        painter.drawRoundedRect(QRectF(1.8, 1.8, 6, 3.6), 0.8, 0.8)
        path = QPainterPath(QPointF(4.2, 5.4))
        path.lineTo(4.2, 12.6)
        path.lineTo(7.6, 12.6)
        path.moveTo(4.2, 8.6)
        path.lineTo(7.6, 8.6)
        painter.drawPath(path)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(7.6, 7, 6.6, 3.2), 0.8, 0.8)
        painter.drawRoundedRect(QRectF(7.6, 11, 6.6, 3.2), 0.8, 0.8)
    elif name in ("chevron_down", "chevron_right", "chevron_up", "chevron_left"):
        painter.setPen(_pen(color, 1.9))
        points = {"chevron_down": ((3.5, 6), (8, 10.5), (12.5, 6)),
                  "chevron_up": ((3.5, 10.5), (8, 6), (12.5, 10.5)),
                  "chevron_right": ((6, 3.5), (10.5, 8), (6, 12.5)),
                  "chevron_left": ((10.5, 3.5), (6, 8), (10.5, 12.5))}[name]
        painter.drawPolyline(QPolygonF([QPointF(*p) for p in points]))
    elif name == "close":
        painter.setPen(_pen(color, 1.6))
        painter.drawLine(QPointF(4, 4), QPointF(12, 12))
        painter.drawLine(QPointF(12, 4), QPointF(4, 12))
    elif name == "more":
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        for x in (3.5, 8, 12.5):
            painter.drawEllipse(QPointF(x, 8), 1.4, 1.4)
    elif name == "float":  # a card lifted out of its frame
        painter.drawRoundedRect(QRectF(1.5, 4.5, 9, 9), 1.2, 1.2)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), color.alpha() // 3))
        painter.drawRoundedRect(QRectF(5.5, 1.5, 9, 8), 1.2, 1.2)
    elif name in ("dock_left", "dock_right"):  # a window with its side column filled
        painter.drawRoundedRect(QRectF(1.5, 2.5, 13, 11), 1.2, 1.2)
        fill = QColor(color)
        fill.setAlpha(color.alpha() * 2 // 3)
        column = QRectF(1.5, 2.5, 4.5, 11) if name == "dock_left" else QRectF(10, 2.5, 4.5, 11)
        painter.fillRect(column, fill)
    elif name == "pin":
        painter.save()
        painter.translate(8, 8)
        painter.rotate(45)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(-2.5, -6.5, 5, 6), 1, 1)
        painter.drawLine(QPointF(-4.5, -0.5), QPointF(4.5, -0.5))
        painter.drawLine(QPointF(0, -0.5), QPointF(0, 7))
        painter.restore()
    elif name == "chain":  # two interlocking links: cameras moving together
        painter.save()
        painter.translate(8, 8)
        painter.rotate(-45)
        painter.setPen(_pen(color, 1.5))
        painter.drawRoundedRect(QRectF(-7, -2.6, 8, 5.2), 2.6, 2.6)
        painter.drawRoundedRect(QRectF(-1, -2.6, 8, 5.2), 2.6, 2.6)
        painter.restore()
    elif name == "chain_off":
        painter.save()
        painter.translate(8, 8)
        painter.rotate(-45)
        painter.setPen(_pen(color, 1.5))
        painter.drawRoundedRect(QRectF(-7.5, -2.6, 6.5, 5.2), 2.6, 2.6)
        painter.drawRoundedRect(QRectF(1, -2.6, 6.5, 5.2), 2.6, 2.6)
        painter.restore()
    elif name == "marker":  # a bookmark
        painter.drawPolygon(QPolygonF([QPointF(4, 2), QPointF(12, 2), QPointF(12, 14),
                                       QPointF(8, 10.5), QPointF(4, 14)]))
    elif name == "display":  # a monitor
        painter.drawRoundedRect(QRectF(1.5, 2.5, 13, 8.5), 1.2, 1.2)
        painter.drawLine(QPointF(8, 11), QPointF(8, 13.5))
        painter.drawLine(QPointF(5, 13.5), QPointF(11, 13.5))
    elif name == "tools":
        painter.drawRoundedRect(QRectF(2, 2, 5, 5), 1, 1)
        painter.drawRoundedRect(QRectF(9, 2, 5, 5), 1, 1)
        painter.drawRoundedRect(QRectF(2, 9, 5, 5), 1, 1)
        painter.drawRoundedRect(QRectF(9, 9, 5, 5), 1, 1)
    elif name == "ellipse":
        painter.drawEllipse(QRectF(2, 3.5, 12, 9))
    elif name == "blur":
        # A drop with soft rings.
        path = QPainterPath(QPointF(8, 1.8))
        path.cubicTo(QPointF(10, 5), QPointF(13, 7.5), QPointF(13, 10))
        path.arcTo(QRectF(3, 5, 10, 10), 0, -180)
        path.cubicTo(QPointF(3, 7.5), QPointF(6, 5), QPointF(8, 1.8))
        painter.drawPath(path)
        faint = QColor(color)
        faint.setAlpha(color.alpha() // 2)
        painter.setPen(_pen(faint, 1.2))
        painter.drawArc(QRectF(5.5, 8, 5, 4.5), 200 * 16, 140 * 16)
    elif name == "pixelate":
        painter.setPen(Qt.NoPen)
        for row in range(3):
            for column in range(3):
                shade = QColor(color)
                shade.setAlpha(color.alpha() if (row + column) % 2 == 0 else color.alpha() * 2 // 5)
                painter.setBrush(shade)
                painter.drawRect(QRectF(2.5 + column * 3.8, 2.5 + row * 3.8, 3.4, 3.4))
    elif name == "step":
        painter.setBrush(color)
        painter.drawEllipse(QPointF(8, 8), 6.2, 6.2)
        painter.setPen(_pen(QColor(30, 33, 40), 1.6))
        painter.drawLine(QPointF(8.4, 4.8), QPointF(8.4, 11.4))
        painter.drawLine(QPointF(8.4, 4.8), QPointF(6.4, 6.4))
    elif name == "link":
        painter.drawRoundedRect(QRectF(1.5, 2, 5, 4), 1, 1)
        painter.drawRoundedRect(QRectF(9.5, 10, 5, 4), 1, 1)
        path = QPainterPath(QPointF(6.5, 4))
        path.cubicTo(QPointF(11, 4), QPointF(5, 12), QPointF(9.5, 12))
        painter.drawPath(path)
    elif name in ("play", "pause", "frame_first", "frame_prev", "frame_next", "frame_last"):
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)

        def triangle(left: float, right: float) -> None:  # pointing from left to right
            painter.drawPolygon(QPolygonF([QPointF(left, 3.5), QPointF(left, 12.5), QPointF(right, 8)]))

        if name == "play":
            triangle(4.5, 12.5)
        elif name == "pause":
            painter.drawRect(QRectF(4, 3.5, 2.8, 9))
            painter.drawRect(QRectF(9.2, 3.5, 2.8, 9))
        elif name == "frame_next":
            triangle(4, 10.5)
            painter.drawRect(QRectF(10.5, 3.5, 1.8, 9))
        elif name == "frame_prev":
            triangle(12, 5.5)
            painter.drawRect(QRectF(3.7, 3.5, 1.8, 9))
        elif name == "frame_last":
            triangle(2.5, 8)
            triangle(7.5, 12.5)
            painter.drawRect(QRectF(12.5, 3.5, 1.6, 9))
        else:  # frame_first
            triangle(13.5, 8)
            triangle(8.5, 3.5)
            painter.drawRect(QRectF(1.9, 3.5, 1.6, 9))
    elif name in ("copy", "duplicate"):  # a sheet in front of another
        painter.drawRoundedRect(QRectF(2.5, 5, 8, 9.5), 1.2, 1.2)
        back = QPainterPath(QPointF(5.5, 5))
        back.lineTo(5.5, 2.7)
        back.quadTo(QPointF(5.5, 1.5), QPointF(6.7, 1.5))
        back.lineTo(12.3, 1.5)
        back.quadTo(QPointF(13.5, 1.5), QPointF(13.5, 2.7))
        back.lineTo(13.5, 9.8)
        back.quadTo(QPointF(13.5, 11), QPointF(12.3, 11))
        back.lineTo(10.5, 11)
        painter.drawPath(back)
        if name == "duplicate":
            painter.setPen(_pen(color, 1.3))
            painter.drawLine(QPointF(6.5, 7.5), QPointF(6.5, 12))
            painter.drawLine(QPointF(4.25, 9.75), QPointF(8.75, 9.75))
    elif name == "paste":  # a clipboard
        painter.drawRoundedRect(QRectF(3, 3, 10, 11.5), 1.4, 1.4)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(5.5, 1.5, 5, 3), 1, 1)
        painter.setPen(_pen(color, 1.2))
        painter.drawLine(QPointF(5.5, 8), QPointF(10.5, 8))
        painter.drawLine(QPointF(5.5, 11), QPointF(9, 11))
    elif name == "cut":  # scissors
        painter.drawEllipse(QPointF(4.5, 12), 2, 2)
        painter.drawEllipse(QPointF(11.5, 12), 2, 2)
        painter.drawLine(QPointF(5.8, 10.4), QPointF(11, 2.5))
        painter.drawLine(QPointF(10.2, 10.4), QPointF(5, 2.5))
    elif name == "delete":  # a bin
        painter.drawLine(QPointF(2.5, 4), QPointF(13.5, 4))
        handle = QPainterPath(QPointF(6, 4))
        handle.lineTo(6, 2.2)
        handle.lineTo(10, 2.2)
        handle.lineTo(10, 4)
        painter.drawPath(handle)
        painter.drawPolyline(QPolygonF([QPointF(4, 4), QPointF(4.8, 14), QPointF(11.2, 14), QPointF(12, 4)]))
        painter.setPen(_pen(color, 1.2))
        painter.drawLine(QPointF(6.8, 6.8), QPointF(6.8, 11.5))
        painter.drawLine(QPointF(9.2, 6.8), QPointF(9.2, 11.5))
    elif name in ("undo", "redo"):  # a hooked arrow, pointing back (undo) or forward (redo)
        painter.save()
        if name == "redo":
            painter.translate(16, 0)
            painter.scale(-1, 1)
        path = QPainterPath(QPointF(6, 6.5))
        path.arcTo(QRectF(6.5, 6.5, 7, 6.5), 90, -180)
        path.lineTo(6, 13)
        painter.setPen(_pen(color, 1.6))
        painter.drawPath(path)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(2, 6.5), QPointF(6.5, 3), QPointF(6.5, 10)]))
        painter.restore()
    elif name == "backdrop":  # a frame with a title bar and something sitting on it
        painter.drawRoundedRect(QRectF(1.8, 2.5, 12.4, 11), 1.8, 1.8)
        painter.drawLine(QPointF(1.8, 5.8), QPointF(14.2, 5.8))
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(4.5, 8, 4, 3.2), 0.6, 0.6)
    elif name == "frame":  # corner brackets around a picture
        for x, y, dx, dy in ((2, 2, 1, 1), (14, 2, -1, 1), (2, 14, 1, -1), (14, 14, -1, -1)):
            painter.drawPolyline(QPolygonF([QPointF(x, y + 3.5 * dy), QPointF(x, y), QPointF(x + 3.5 * dx, y)]))
        painter.drawRoundedRect(QRectF(5.5, 6, 5, 4), 0.8, 0.8)
    elif name == "select_all":  # a dashed marquee around everything
        dashed = _pen(color, 1.3)
        dashed.setStyle(Qt.CustomDashLine)
        dashed.setDashPattern([1.6, 1.8])
        dashed.setCapStyle(Qt.FlatCap)
        painter.setPen(dashed)
        painter.drawRect(QRectF(2, 2, 12, 12))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(5, 5, 6, 6), 1, 1)
    elif name == "folder":
        painter.drawPolygon(QPolygonF([QPointF(1.5, 3.5), QPointF(6, 3.5), QPointF(7.5, 5.5),
                                       QPointF(14.5, 5.5), QPointF(14.5, 13), QPointF(1.5, 13)]))
    elif name == "files":  # a document with a folded corner and text lines
        painter.drawPolygon(QPolygonF([QPointF(3, 1.5), QPointF(10.5, 1.5), QPointF(13, 4),
                                       QPointF(13, 14.5), QPointF(3, 14.5)]))
        painter.drawPolyline(QPolygonF([QPointF(10.5, 1.5), QPointF(10.5, 4), QPointF(13, 4)]))
        painter.drawLine(QPointF(5, 7), QPointF(11, 7))
        painter.drawLine(QPointF(5, 9.5), QPointF(11, 9.5))
        painter.drawLine(QPointF(5, 12), QPointF(9, 12))
    elif name == "save":  # a floppy disk
        painter.drawPolygon(QPolygonF([QPointF(2.5, 2.5), QPointF(11.5, 2.5), QPointF(13.5, 4.5),
                                       QPointF(13.5, 13.5), QPointF(2.5, 13.5)]))
        painter.drawRect(QRectF(5, 2.5, 5.5, 3.5))
        painter.drawRect(QRectF(4.5, 9, 7, 4.5))
    elif name == "export":  # an arrow leaving a box
        painter.drawPolyline(QPolygonF([QPointF(7, 3), QPointF(3, 3), QPointF(3, 13), QPointF(13, 13),
                                        QPointF(13, 9)]))
        painter.drawLine(QPointF(7.5, 8.5), QPointF(13.5, 2.5))
        painter.drawPolyline(QPolygonF([QPointF(9, 2.5), QPointF(13.5, 2.5), QPointF(13.5, 7)]))
    elif name == "keyboard":
        painter.drawRoundedRect(QRectF(1, 3.5, 14, 9), 1.5, 1.5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        for y in (5.6, 7.8):
            for column in range(5):
                painter.drawRect(QRectF(3 + column * 2.2, y, 1.4, 1.3))
        painter.drawRoundedRect(QRectF(5, 10, 6, 1.3), 0.5, 0.5)
    elif name == "info":
        painter.drawEllipse(QPointF(8, 8), 6.2, 6.2)
        painter.setPen(_pen(color, 1.7))
        painter.drawLine(QPointF(8, 7.3), QPointF(8, 11.3))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(8, 4.9), 1.05, 1.05)
    elif name == "effects":  # sparkles
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)

        def star(cx: float, cy: float, r: float) -> None:
            k = r * 0.24
            painter.drawPolygon(QPolygonF([QPointF(cx, cy - r), QPointF(cx + k, cy - k), QPointF(cx + r, cy),
                                           QPointF(cx + k, cy + k), QPointF(cx, cy + r), QPointF(cx - k, cy + k),
                                           QPointF(cx - r, cy), QPointF(cx - k, cy - k)]))

        star(6.5, 9, 5.5)
        star(12.5, 3.5, 2.6)
        star(12.8, 12.5, 1.8)
    elif name == "transform":  # move arrows in four directions
        painter.drawLine(QPointF(8, 2), QPointF(8, 14))
        painter.drawLine(QPointF(2, 8), QPointF(14, 8))
        for points in (((6, 4), (8, 2), (10, 4)), ((6, 12), (8, 14), (10, 12)),
                       ((4, 6), (2, 8), (4, 10)), ((12, 6), (14, 8), (12, 10))):
            painter.drawPolyline(QPolygonF([QPointF(*p) for p in points]))
    elif name == "order":  # one card stacked in front of another
        painter.drawRoundedRect(QRectF(2, 2, 8, 8), 1.2, 1.2)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(6, 6, 8, 8), 1.2, 1.2)
    elif name == "record":  # a ring around a filled dot
        painter.drawEllipse(QPointF(8, 8), 5.8, 5.8)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(8, 8), 3.4, 3.4)
    elif name == "stop":  # a filled square
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(4, 4, 8, 8), 1.2, 1.2)
    elif name == "isolate":  # a target
        painter.drawEllipse(QPointF(8, 8), 5.8, 5.8)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(8, 8), 2.2, 2.2)
    elif name == "snap":  # a horseshoe magnet, poles down
        body = QPainterPath(QPointF(4.5, 10))
        body.lineTo(4.5, 7.5)
        body.arcTo(QRectF(4.5, 4, 7, 7), 180, -180)
        body.lineTo(11.5, 10)
        painter.setPen(QPen(color, 3.0, Qt.SolidLine, Qt.FlatCap, Qt.RoundJoin))
        painter.drawPath(body)
        for x in (4.5, 11.5):
            painter.drawLine(QPointF(x, 11.2), QPointF(x, 13.8))
    elif name == "search":  # a magnifying glass
        painter.drawEllipse(QPointF(6.8, 6.8), 4.3, 4.3)
        painter.setPen(QPen(color, 2.2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(10, 10), QPointF(13.8, 13.8))
    elif not _draw_command(name, painter, color):  # unknown icon: a small dot so buttons still show something
        painter.setBrush(color)
        painter.drawEllipse(QPointF(8, 8), 2.5, 2.5)


def _line(painter: QPainter, *points: tuple[float, float]) -> None:
    painter.drawPolyline(QPolygonF([QPointF(*p) for p in points]))


def _filled(painter: QPainter, color: QColor, *points: tuple[float, float]) -> None:
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawPolygon(QPolygonF([QPointF(*p) for p in points]))
    painter.restore()


def _faint(color: QColor, divisor: int = 3) -> QColor:
    faint = QColor(color)
    faint.setAlpha(max(0, color.alpha() // divisor))
    return faint


def _page(painter: QPainter) -> None:
    """A sheet with a folded corner."""
    painter.drawPolygon(QPolygonF([QPointF(3, 1.5), QPointF(10.5, 1.5), QPointF(13, 4),
                                   QPointF(13, 14.5), QPointF(3, 14.5)]))
    _line(painter, (10.5, 1.5), (10.5, 4), (13, 4))


def _circular_arrow(painter: QPainter, color: QColor) -> None:
    """Most of a circle, clockwise, ending in an arrowhead at the upper right."""
    painter.drawArc(QRectF(3, 3, 10, 10), -30 * 16, -290 * 16)
    _filled(painter, color, (13.6, 7.8), (14.6, 3.6), (10.2, 5.2))


def _draw_command(name: str, painter: QPainter, color: QColor) -> bool:
    """The glyphs of menu commands (transforming, arranging, files, view…). False when the
    name is unknown."""
    if name in ("flip_h", "flip_v"):  # a shape and its mirror image across a dashed axis
        painter.save()
        if name == "flip_v":
            painter.translate(8, 8)
            painter.rotate(90)
            painter.translate(-8, -8)
        dashed = _pen(color, 1.2)
        dashed.setStyle(Qt.CustomDashLine)
        dashed.setDashPattern([1.5, 1.8])
        painter.setPen(dashed)
        painter.drawLine(QPointF(8, 1.5), QPointF(8, 14.5))
        painter.setPen(_pen(color, 1.3))
        painter.drawPolygon(QPolygonF([QPointF(10.5, 3.5), QPointF(10.5, 12.5), QPointF(14.5, 8)]))
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(5.5, 3.5), QPointF(5.5, 12.5), QPointF(1.5, 8)]))
        painter.restore()
    elif name in ("rotate_cw", "rotate_ccw"):  # a square and a quarter turn around it
        painter.save()
        if name == "rotate_ccw":
            painter.translate(16, 0)
            painter.scale(-1, 1)
        painter.drawRoundedRect(QRectF(2, 8.5, 5.5, 5.5), 1, 1)
        painter.drawArc(QRectF(-2, 3.5, 13, 13), 90 * 16, -80 * 16)
        _filled(painter, color, (11, 14.2), (8.4, 10.2), (13.6, 10.2))
        painter.restore()
    elif name == "refresh":
        _circular_arrow(painter, color)
    elif name == "reset":  # back to where it started: a counter-clockwise arrow around a dot
        painter.save()
        painter.translate(16, 0)
        painter.scale(-1, 1)
        _circular_arrow(painter, color)
        painter.restore()
        painter.setBrush(color)
        painter.drawEllipse(QPointF(8, 8), 1.5, 1.5)
    elif name == "crop":
        _line(painter, (4.5, 1.5), (4.5, 11.5), (14.5, 11.5))
        _line(painter, (1.5, 4.5), (11.5, 4.5), (11.5, 14.5))
    elif name == "grayscale":  # a disc, half filled
        painter.drawEllipse(QPointF(8, 8), 5.8, 5.8)
        painter.setBrush(color)
        painter.drawPie(QRectF(2.2, 2.2, 11.6, 11.6), -90 * 16, 180 * 16)
    elif name == "opacity":  # a square, half solid and half faint
        painter.fillRect(QRectF(2.5, 2.5, 5.5, 11), color)
        painter.fillRect(QRectF(8, 2.5, 5.5, 11), _faint(color, 4))
        painter.drawRect(QRectF(2.5, 2.5, 11, 11))
    elif name == "palette":  # a strip of swatches
        painter.setPen(Qt.NoPen)
        for index, alpha in enumerate((255, 190, 125, 70)):
            shade = QColor(color)
            shade.setAlpha(color.alpha() * alpha // 255)
            painter.setBrush(shade)
            painter.drawRect(QRectF(1.5 + index * 3.4, 4.5, 2.8, 7))
    elif name in ("exposure_up", "exposure_down"):  # a sun with a plus or a minus
        painter.setPen(_pen(color, 1.2))
        painter.drawEllipse(QPointF(6.5, 9.5), 2.3, 2.3)
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (-0.7, -0.7), (0.7, 0.7), (-0.7, 0.7)):
            painter.drawLine(QPointF(6.5 + dx * 3.7, 9.5 + dy * 3.7), QPointF(6.5 + dx * 5, 9.5 + dy * 5))
        painter.setPen(_pen(color, 1.5))
        painter.drawLine(QPointF(10.5, 3.5), QPointF(14.5, 3.5))
        if name == "exposure_up":
            painter.drawLine(QPointF(12.5, 1.5), QPointF(12.5, 5.5))
    elif name == "channels":  # three overlapping rings
        painter.setPen(_pen(color, 1.3))
        for x, y in ((8, 5.6), (5.4, 10.2), (10.6, 10.2)):
            painter.drawEllipse(QPointF(x, y), 3.6, 3.6)
    elif name == "download":  # an arrow into a tray
        painter.drawLine(QPointF(8, 2), QPointF(8, 10))
        _line(painter, (4.5, 6.8), (8, 10.3), (11.5, 6.8))
        _line(painter, (2.5, 11), (2.5, 13.8), (13.5, 13.8), (13.5, 11))
    elif name == "embed":  # an arrow going into a box (the opposite of export)
        _line(painter, (7, 3), (3, 3), (3, 13), (13, 13), (13, 9))
        painter.drawLine(QPointF(13.5, 2.5), QPointF(7.8, 8.2))
        _line(painter, (7.8, 4), (7.8, 8.2), (12, 8.2))
    elif name == "link_file":  # a sheet with a chain link on it
        _page(painter)
        painter.setPen(_pen(color, 1.2))
        painter.drawRoundedRect(QRectF(4.8, 8, 4, 3), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(7.2, 8, 4, 3), 1.5, 1.5)
    elif name == "next_file":  # a sheet with an arrow to the next one
        _page(painter)
        _line(painter, (6.5, 7), (9.3, 9.6), (6.5, 12.2))
    elif name == "paper":  # a blank sheet
        _page(painter)
    elif name == "save_as":  # a floppy disk with an ellipsis: asks where
        painter.drawPolygon(QPolygonF([QPointF(2.5, 2.5), QPointF(11.5, 2.5), QPointF(13.5, 4.5),
                                       QPointF(13.5, 13.5), QPointF(2.5, 13.5)]))
        painter.drawRect(QRectF(5, 2.5, 5.5, 3.5))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        for x in (5.2, 8, 10.8):
            painter.drawEllipse(QPointF(x, 10.2), 0.95, 0.95)
    elif name == "images":  # a stack of pictures
        painter.drawRoundedRect(QRectF(4.5, 2, 10, 8), 1.2, 1.2)
        painter.setBrush(_faint(color))
        painter.drawRoundedRect(QRectF(1.5, 6, 10, 8), 1.2, 1.2)
    elif name == "history":  # a clock
        painter.drawEllipse(QPointF(8, 8), 6, 6)
        _line(painter, (8, 4.6), (8, 8), (10.6, 9.6))
    elif name in ("align_left", "align_right", "align_top", "align_bottom", "align_center_h", "align_center_v"):
        painter.save()
        if name in ("align_top", "align_bottom", "align_center_v"):  # the same drawing, turned
            painter.translate(8, 8)
            painter.rotate(90)
            painter.scale(1, -1)
            painter.translate(-8, -8)
        if name in ("align_right", "align_bottom"):
            painter.translate(16, 0)
            painter.scale(-1, 1)
        if name in ("align_center_h", "align_center_v"):
            painter.drawLine(QPointF(8, 1.5), QPointF(8, 14.5))
            bars = (QRectF(3, 4, 10, 3), QRectF(5, 9, 6, 3))
        else:
            painter.drawLine(QPointF(2.5, 1.5), QPointF(2.5, 14.5))
            bars = (QRectF(4.8, 4, 9, 3), QRectF(4.8, 9, 5.5, 3))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        for bar in bars:
            painter.drawRoundedRect(bar, 0.6, 0.6)
        painter.restore()
    elif name in ("distribute_h", "distribute_v"):  # a bar centred between two edges
        painter.save()
        if name == "distribute_v":
            painter.translate(8, 8)
            painter.rotate(90)
            painter.translate(-8, -8)
        painter.drawLine(QPointF(2.5, 2), QPointF(2.5, 14))
        painter.drawLine(QPointF(13.5, 2), QPointF(13.5, 14))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(6.2, 4, 3.6, 8), 0.6, 0.6)
        painter.restore()
    elif name in ("arrange_row", "arrange_column"):  # three items in a line
        painter.save()
        if name == "arrange_column":
            painter.translate(8, 8)
            painter.rotate(90)
            painter.scale(1, -1)
            painter.translate(-8, -8)
        painter.setPen(_pen(color, 1.2))
        for x, width in ((1.5, 3.6), (6.3, 3.4), (10.9, 3.6)):
            painter.drawRect(QRectF(x, 5, width, 6))
        painter.restore()
    elif name == "arrange_auto":  # items of different sizes packed together
        painter.setPen(_pen(color, 1.2))
        for rect in (QRectF(2, 2, 7, 6), QRectF(10.5, 2, 3.5, 6), QRectF(2, 9.5, 4, 4.5), QRectF(7.5, 9.5, 6.5, 4.5)):
            painter.drawRect(rect)
    elif name in ("size_height", "size_width"):  # a box and the dimension it is matched on
        painter.save()
        if name == "size_width":
            painter.translate(8, 8)
            painter.rotate(90)
            painter.scale(1, -1)
            painter.translate(-8, -8)
        painter.drawRoundedRect(QRectF(2.5, 3, 5, 10), 1, 1)
        painter.drawLine(QPointF(11.5, 3), QPointF(11.5, 13))
        _line(painter, (9.5, 5), (11.5, 3), (13.5, 5))
        _line(painter, (9.5, 11), (11.5, 13), (13.5, 11))
        painter.restore()
    elif name == "size_both":  # a box and a diagonal: its whole size
        painter.drawRoundedRect(QRectF(2, 6, 8, 8), 1, 1)
        painter.drawLine(QPointF(9, 7), QPointF(13.5, 2.5))
        _line(painter, (10, 2.5), (13.5, 2.5), (13.5, 6))
    elif name in ("to_front", "forward", "backward", "to_back"):  # an arrow up or down, to a limit or one step
        painter.save()
        if name in ("backward", "to_back"):
            painter.translate(0, 16)
            painter.scale(1, -1)
        painter.setPen(_pen(color, 1.6))
        if name in ("to_front", "to_back"):
            painter.drawLine(QPointF(3, 2.5), QPointF(13, 2.5))
            painter.drawLine(QPointF(8, 13.5), QPointF(8, 6))
            _line(painter, (4.8, 9.2), (8, 6), (11.2, 9.2))
        else:
            painter.drawLine(QPointF(8, 13), QPointF(8, 3.5))
            _line(painter, (4.5, 7), (8, 3.5), (11.5, 7))
        painter.restore()
    elif name == "ungroup":  # two items apart
        painter.drawRoundedRect(QRectF(2, 2, 5.5, 5.5), 1, 1)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(8.5, 8.5, 5.5, 5.5), 1, 1)
    elif name == "combine":  # two overlapping squares merged into one outline
        painter.drawPolygon(QPolygonF([QPointF(2, 2), QPointF(9, 2), QPointF(9, 7), QPointF(14, 7), QPointF(14, 14),
                                       QPointF(7, 14), QPointF(7, 9), QPointF(2, 9)]))
    elif name == "code":  # </>
        _line(painter, (5.2, 4.5), (1.8, 8), (5.2, 11.5))
        _line(painter, (10.8, 4.5), (14.2, 8), (10.8, 11.5))
        painter.drawLine(QPointF(9.2, 3), QPointF(6.8, 13))
    elif name == "checklist":
        _line(painter, (2, 4.8), (3.6, 6.4), (6.2, 3.2))
        painter.drawLine(QPointF(8.5, 5), QPointF(14, 5))
        painter.drawRect(QRectF(2.2, 9.5, 3.6, 3.6))
        painter.drawLine(QPointF(8.5, 11.3), QPointF(14, 11.3))
    elif name == "style":  # a paint brush
        painter.setPen(_pen(color, 1.9))
        painter.drawLine(QPointF(13.5, 2.5), QPointF(8, 8))
        _filled(painter, color, (8.6, 7), (9.6, 9), (7, 12.6), (2.2, 13.8), (4.2, 9.6), (6.4, 7.6))
    elif name == "goto":  # an arrow up to a mark
        painter.drawLine(QPointF(2, 8), QPointF(10, 8))
        _line(painter, (7, 5), (10, 8), (7, 11))
        painter.setPen(_pen(color, 1.8))
        painter.drawLine(QPointF(13, 3), QPointF(13, 13))
    elif name == "note":  # a sticky note
        painter.drawPolygon(QPolygonF([QPointF(2.5, 2.5), QPointF(13.5, 2.5), QPointF(13.5, 9.5), QPointF(9.5, 13.5),
                                       QPointF(2.5, 13.5)]))
        _line(painter, (13.5, 9.5), (9.5, 9.5), (9.5, 13.5))
    elif name == "label":  # a tag
        painter.drawPolygon(QPolygonF([QPointF(2.5, 2.5), QPointF(8, 2.5), QPointF(13.8, 8.3), QPointF(8.3, 13.8),
                                       QPointF(2.5, 8)]))
        painter.setBrush(color)
        painter.drawEllipse(QPointF(5.4, 5.4), 1.1, 1.1)
    elif name == "invert":  # a square, half filled along the diagonal
        painter.drawRect(QRectF(2.5, 2.5, 11, 11))
        _filled(painter, color, (2.5, 2.5), (13.5, 2.5), (2.5, 13.5))
    elif name == "straighten":  # a straight line between two ends
        painter.drawLine(QPointF(3.5, 12.5), QPointF(12.5, 3.5))
        painter.setBrush(color)
        painter.drawEllipse(QPointF(3.5, 12.5), 1.7, 1.7)
        painter.drawEllipse(QPointF(12.5, 3.5), 1.7, 1.7)
    elif name == "reverse":  # two arrows in opposite directions
        painter.drawLine(QPointF(2.5, 5.5), QPointF(13.5, 5.5))
        _line(painter, (10.8, 2.8), (13.5, 5.5), (10.8, 8.2))
        painter.drawLine(QPointF(13.5, 10.5), QPointF(2.5, 10.5))
        _line(painter, (5.2, 7.8), (2.5, 10.5), (5.2, 13.2))
    elif name in ("zoom_in", "zoom_out"):  # a magnifying glass with a plus or a minus
        painter.drawEllipse(QPointF(6.8, 6.8), 4.8, 4.8)
        painter.drawLine(QPointF(4.6, 6.8), QPointF(9, 6.8))
        if name == "zoom_in":
            painter.drawLine(QPointF(6.8, 4.6), QPointF(6.8, 9))
        painter.setPen(QPen(color, 2.2, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(10.4, 10.4), QPointF(13.8, 13.8))
    elif name in ("arrow_left", "arrow_right"):
        painter.save()
        if name == "arrow_left":
            painter.translate(16, 0)
            painter.scale(-1, 1)
        painter.setPen(_pen(color, 1.6))
        painter.drawLine(QPointF(2.5, 8), QPointF(13, 8))
        _line(painter, (9.2, 4.2), (13, 8), (9.2, 11.8))
        painter.restore()
    elif name == "ghost":
        body = QPainterPath(QPointF(3, 14))
        body.lineTo(3, 7.5)
        body.arcTo(QRectF(3, 2, 10, 11), 180, -180)
        body.lineTo(13, 14)
        body.lineTo(10.5, 12)
        body.lineTo(8, 14)
        body.lineTo(5.5, 12)
        body.closeSubpath()
        painter.drawPath(body)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(6.2, 7.5), 0.9, 0.9)
        painter.drawEllipse(QPointF(9.8, 7.5), 0.9, 0.9)
    elif name == "click_through":  # a pointer passing through a dashed window
        dashed = _pen(color, 1.3)
        dashed.setStyle(Qt.CustomDashLine)
        dashed.setDashPattern([1.6, 1.8])
        dashed.setCapStyle(Qt.FlatCap)
        painter.setPen(dashed)
        painter.drawRect(QRectF(2, 2, 12, 12))
        _filled(painter, color, (6, 5), (6, 12.6), (8, 10.7), (9.4, 13.3), (10.7, 12.7), (9.3, 10.1), (11.8, 10))
    elif name == "plugin":  # a plug
        painter.drawLine(QPointF(6, 2), QPointF(6, 5.5))
        painter.drawLine(QPointF(10, 2), QPointF(10, 5.5))
        painter.setBrush(color)
        body = QPainterPath(QPointF(3.5, 5.5))
        body.lineTo(12.5, 5.5)
        body.lineTo(12.5, 8)
        body.arcTo(QRectF(3.5, 3.5, 9, 9), 0, -180)
        body.closeSubpath()
        painter.drawPath(body)
        painter.drawLine(QPointF(8, 12.5), QPointF(8, 14.5))
    elif name == "settings":  # a cog
        painter.save()
        painter.translate(8, 8)
        painter.drawEllipse(QPointF(0, 0), 4.2, 4.2)
        painter.drawEllipse(QPointF(0, 0), 1.5, 1.5)
        painter.setPen(QPen(color, 2.1, Qt.SolidLine, Qt.FlatCap))
        for _tooth in range(8):
            painter.drawLine(QPointF(0, -4.6), QPointF(0, -6.6))
            painter.rotate(45)
        painter.restore()
    elif name == "shield":
        painter.drawPolygon(QPolygonF([QPointF(8, 1.8), QPointF(13.5, 3.8), QPointF(13.5, 8.2), QPointF(8, 14.4),
                                       QPointF(2.5, 8.2), QPointF(2.5, 3.8)]))
        _line(painter, (5.4, 7.6), (7.3, 9.5), (10.6, 5.8))
    elif name == "flag":
        painter.drawLine(QPointF(4, 2), QPointF(4, 14.5))
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QPointF(4, 2.8), QPointF(13.2, 2.8), QPointF(10.8, 6), QPointF(13.2, 9.2),
                                       QPointF(4, 9.2)]))
    elif name == "step_back":  # a backspace key
        painter.drawPolygon(QPolygonF([QPointF(5.5, 3.5), QPointF(14, 3.5), QPointF(14, 12.5), QPointF(5.5, 12.5),
                                       QPointF(1.5, 8)]))
        painter.drawLine(QPointF(8, 6.2), QPointF(11.6, 9.8))
        painter.drawLine(QPointF(11.6, 6.2), QPointF(8, 9.8))
    else:
        return False
    return True


@lru_cache(maxsize=None)
def icon(name: str, color: str = ICON_COLOR, alpha: int = 255) -> QIcon:
    """Icon by name: select, rect, arrow, pen, eraser, pick, image, text,
    layer, eye, eye_off, lock, lock_open, plus, split_right, split_down, rotate_split,
    blur, pixelate, step…"""
    qcolor = QColor(color)
    qcolor.setAlpha(alpha)
    pixmap, painter = _canvas()
    _draw(name, painter, qcolor)
    painter.end()
    pixmap.setDevicePixelRatio(SCALE)
    return QIcon(pixmap)


CHECKED_COLOR = "#5aa2ff"


@lru_cache(maxsize=None)
def toggle_icon(name: str, color: str = ICON_COLOR, checked_color: str = CHECKED_COLOR) -> QIcon:
    """The icon of a checkable action: drawn in the accent colour while the action is checked.
    (A menu shows an item's icon in place of its check mark, so the icon has to carry the state.)"""
    result = QIcon()
    for state, tint in ((QIcon.Off, color), (QIcon.On, checked_color)):
        pixmap, painter = _canvas()
        _draw(name, painter, QColor(tint))
        painter.end()
        pixmap.setDevicePixelRatio(SCALE)
        result.addPixmap(pixmap, QIcon.Normal, state)
    return result


def is_known(name: str) -> bool:
    """Whether an icon of this name is drawn (unknown names fall back to a dot)."""
    return icon_pixmap(name, 16, scale=1).toImage() != icon_pixmap("\0unknown", 16, scale=1).toImage()


def icon_pixmap(name: str, size: int, color: str = ICON_COLOR, scale: int = SCALE) -> QPixmap:
    """The icon drawn at exactly `size` logical pixels (`size * scale` device pixels)."""
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size * scale / 16.0, size * scale / 16.0)
    _draw(name, painter, QColor(color))
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


_FILE_DIR: list[Path] = []


def icon_file(name: str, size: int, color: str = ICON_COLOR) -> str:
    """The icon as a PNG file for stylesheets (``image: url(...)``): ``<file>.png`` at
    `size` px and ``<file>@2x.png`` for high-DPI screens, written once per run."""
    if not _FILE_DIR:
        import atexit
        import shutil
        import tempfile

        _FILE_DIR.append(Path(tempfile.mkdtemp(prefix="pixelref_ui_")))
        atexit.register(shutil.rmtree, _FILE_DIR[0], True)
    stem = f"{name}_{size}_{color.lstrip('#')}"
    base = _FILE_DIR[0] / f"{stem}.png"
    if not base.exists():
        icon_pixmap(name, size, color, 1).save(str(base))
        icon_pixmap(name, size, color, 2).save(str(_FILE_DIR[0] / f"{stem}@2x.png"))
    return base.as_posix()


RESOURCES = Path(__file__).resolve().parents[1] / "resources"
LOGO_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)


@lru_cache(maxsize=None)
def app_icon() -> QIcon:
    """The pixelRef logo, from pre-scaled PNGs (see tools/build_logo.py) so
    every size from title bar to taskbar stays crisp."""
    result = QIcon()
    for size in LOGO_SIZES:
        path = RESOURCES / f"logo_{size}.png"
        if path.exists():
            result.addFile(str(path), QSize(size, size))
    return result


def logo_pixmap(size: int, device_pixel_ratio: float = 1.0) -> QPixmap:
    """The logo at `size` logical pixels, sharp on high-DPI screens."""
    return app_icon().pixmap(QSize(size, size), device_pixel_ratio)
