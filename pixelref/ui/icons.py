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
    else:  # unknown icon: a small dot so buttons still show something
        painter.setBrush(color)
        painter.drawEllipse(QPointF(8, 8), 2.5, 2.5)


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
