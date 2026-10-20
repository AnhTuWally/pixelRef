"""The colour popover: a small non-modal picker that opens beside the swatch you clicked.

It follows the dialog protocol of ``ColorPickerProvider`` (``currentColorChanged``,
``currentColor()``, ``exec()``), so the canvas previews every change live. Enter or OK
keeps the colour; Esc or a click anywhere else cancels and the original comes back.
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable

from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QCursor,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pixelref.plugin_api.ui import icon
from pixelref.plugin_api.ui import widgets as w

WIDTH = 224
SQUARE_HEIGHT = 150
SLIDER_HEIGHT = 14
SWATCH = 18
MAX_SWATCHES = 12
FIELD_HEIGHT = 24
CODES = (
    ("rgb", ("Red", "Green", "Blue"), ((0, 255, ""), (0, 255, ""), (0, 255, ""))),
    ("hsv", ("Hue", "Saturation", "Value"), ((0, 359, "°"), (0, 100, "%"), (0, 100, "%"))),
    ("hsl", ("Hue", "Saturation", "Lightness"), ((0, 359, "°"), (0, 100, "%"), (0, 100, "%"))),
)

HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
FUNC = re.compile(r"^rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*(?:[,/]\s*([\d.]+%?)\s*)?\)$", re.I)


def parse_color(text: str) -> QColor | None:
    """``#rgb``, ``#rrggbb``, ``#rrggbbaa`` (with or without ``#``) or ``rgb(r, g, b[, a])``; else None."""
    text = text.strip()
    match = HEX.match(text)
    if match:
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(c * 2 for c in digits)
        color = QColor(f"#{digits[:6]}")
        if len(digits) == 8:
            color.setAlpha(int(digits[6:], 16))
        return color
    match = FUNC.match(text)
    if match:
        r, g, b = (int(match.group(i)) for i in (1, 2, 3))
        if max(r, g, b) > 255:
            return None
        color = QColor(r, g, b)
        alpha = match.group(4)
        if alpha:
            value = float(alpha[:-1]) / 100 if alpha.endswith("%") else float(alpha)
            color.setAlpha(round(max(0.0, min(1.0, value)) * 255))
        return color
    return None


def paint_checker(painter: QPainter, rect: QRectF, cell: int = 5) -> None:
    painter.save()
    painter.setClipRect(rect)
    painter.fillRect(rect, QColor("#ffffff"))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#c8c8c8"))
    rows, cols = int(rect.height() // cell) + 1, int(rect.width() // cell) + 1
    for row in range(rows):
        for col in range(cols):
            if (row + col) % 2:
                painter.drawRect(QRectF(rect.left() + col * cell, rect.top() + row * cell, cell, cell))
    painter.restore()


def paint_handle(painter: QPainter, centre: QPointF, radius: float = 6.0) -> None:
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(0, 0, 0, 200), 3))
    painter.drawEllipse(centre, radius, radius)
    painter.setPen(QPen(QColor("#ffffff"), 1.5))
    painter.drawEllipse(centre, radius, radius)
    painter.restore()


class _Dragger(QWidget):
    """A widget that reports mouse positions while the left button is down."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CrossCursor)
        self._down = False

    def moved_to(self, pos: QPointF) -> None:
        raise NotImplementedError

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._down = True
            self.moved_to(event.position())

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802
        if self._down:
            self.moved_to(event.position())

    def mouseReleaseEvent(self, _event: Any) -> None:  # noqa: N802
        self._down = False


class ColorWheel(_Dragger):
    """A hue ring with the saturation/brightness square inside it.

    Press on the ring to pick the hue (keep dragging anywhere: only the angle counts), press
    inside the square for saturation (right) and brightness (up).
    """

    hue_changed = Signal(float)
    sv_changed = Signal(float, float)

    RING = 20  # thickness of the hue ring
    HEIGHT = 208

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hue, self.sat, self.val = 0.0, 1.0, 1.0
        self._mode = ""  # "hue" or "sv" while dragging
        self.setFixedSize(WIDTH, self.HEIGHT)

    # -- geometry
    def centre(self) -> QPointF:
        return QPointF(self.width() / 2, self.height() / 2)

    def outer(self) -> float:
        return min(self.width(), self.height()) / 2 - 2

    def inner(self) -> float:
        return self.outer() - self.RING

    def square(self) -> QRectF:
        half = (self.inner() - 6) / 2 ** 0.5  # the largest square inside the ring, with a small gap
        c = self.centre()
        return QRectF(c.x() - half, c.y() - half, 2 * half, 2 * half)

    def set_state(self, hue: float, sat: float, val: float) -> None:
        self.hue, self.sat, self.val = hue, sat, val
        self.update()

    # -- input
    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        offset = event.position() - self.centre()
        distance = math.hypot(offset.x(), offset.y())
        if self.square().adjusted(-3, -3, 3, 3).contains(event.position()):
            self._mode = "sv"
        elif self.inner() - 4 <= distance <= self.outer() + 4:
            self._mode = "hue"
        else:
            return
        self._down = True
        self.moved_to(event.position())

    def mouseReleaseEvent(self, _event: Any) -> None:  # noqa: N802
        self._down = False
        self._mode = ""

    def moved_to(self, pos: QPointF) -> None:
        if self._mode == "hue":
            offset = pos - self.centre()
            self.hue = (math.atan2(-offset.y(), offset.x()) / (2 * math.pi)) % 1.0
            self.update()
            self.hue_changed.emit(self.hue)
        elif self._mode == "sv":
            square = self.square()
            self.sat = min(1.0, max(0.0, (pos.x() - square.left()) / square.width()))
            self.val = 1.0 - min(1.0, max(0.0, (pos.y() - square.top()) / square.height()))
            self.update()
            self.sv_changed.emit(self.sat, self.val)

    # -- painting
    def paintEvent(self, _event: Any) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        c, outer, inner = self.centre(), self.outer(), self.inner()
        ring = QPainterPath()
        ring.addEllipse(c, outer, outer)
        ring.addEllipse(c, inner, inner)  # odd-even fill leaves a hole
        ring.setFillRule(Qt.OddEvenFill)
        cone = QConicalGradient(c, 0)  # hue 0 at 3 o'clock, rising anticlockwise
        for i in range(13):
            cone.setColorAt(min(1.0, i / 12), QColor.fromHsvF(min(0.999, i / 12), 1.0, 1.0))
        painter.fillPath(ring, QBrush(cone))
        # Hue marker on the ring.
        angle = self.hue * 2 * math.pi
        mid = (outer + inner) / 2
        paint_handle(painter, QPointF(c.x() + mid * math.cos(angle), c.y() - mid * math.sin(angle)), 7.0)
        # Saturation / brightness square.
        rect = self.square()
        clip = QPainterPath()
        clip.addRoundedRect(rect, 3, 3)
        painter.save()
        painter.setClipPath(clip)
        painter.fillRect(rect, QColor.fromHsvF(min(0.999, self.hue), 1.0, 1.0))
        across = QLinearGradient(rect.topLeft(), rect.topRight())
        across.setColorAt(0.0, QColor(255, 255, 255, 255))
        across.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(rect, across)
        down = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        down.setColorAt(0.0, QColor(0, 0, 0, 0))
        down.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.fillRect(rect, down)
        painter.restore()
        paint_handle(painter, QPointF(rect.left() + self.sat * rect.width(), rect.top() + (1.0 - self.val) * rect.height()))


class _Slider(_Dragger):
    """A horizontal 0..1 slider; subclasses paint the track."""

    changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.fraction = 0.0
        self.setFixedSize(WIDTH, SLIDER_HEIGHT)

    def set_fraction(self, fraction: float) -> None:
        self.fraction = min(1.0, max(0.0, fraction))
        self.update()

    def moved_to(self, pos: QPointF) -> None:
        self.set_fraction(pos.x() / max(1, self.width() - 1))
        self.changed.emit(self.fraction)

    def paint_track(self, painter: QPainter, rect: QRectF) -> None:
        raise NotImplementedError

    def paintEvent(self, _event: Any) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0, 2, 0, -2)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 4, 4)
        painter.setClipPath(clip)
        self.paint_track(painter, rect)
        painter.setClipping(False)
        paint_handle(painter, QPointF(self.fraction * (self.width() - 1), self.height() / 2), 6.0)

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802
        step = 0.1 if event.modifiers() & Qt.ShiftModifier else 0.01
        if event.key() in (Qt.Key_Left, Qt.Key_Down):
            self.set_fraction(self.fraction - step)
        elif event.key() in (Qt.Key_Right, Qt.Key_Up):
            self.set_fraction(self.fraction + step)
        else:
            super().keyPressEvent(event)
            return
        self.changed.emit(self.fraction)


class AlphaSlider(_Slider):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.color = QColor(255, 0, 0)

    def set_color(self, color: QColor) -> None:
        self.color = QColor(color.red(), color.green(), color.blue())
        self.update()

    def paint_track(self, painter: QPainter, rect: QRectF) -> None:
        paint_checker(painter, rect, 4)
        gradient = QLinearGradient(rect.topLeft(), rect.topRight())
        gradient.setColorAt(0.0, QColor(self.color.red(), self.color.green(), self.color.blue(), 0))
        gradient.setColorAt(1.0, self.color)
        painter.fillRect(rect, gradient)


class Comparison(QWidget):
    """The colour you started with (left) next to the new one (right); click the old half to go back."""

    revert = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.old = QColor()
        self.new = QColor()
        self.setFixedSize(46, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Left: the colour you started with (click to go back) · right: the new colour")

    def paintEvent(self, _event: Any) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 4, 4)
        painter.setClipPath(clip)
        paint_checker(painter, rect, 5)
        half = rect.width() / 2
        painter.fillRect(QRectF(rect.left(), rect.top(), half, rect.height()), self.old)
        painter.fillRect(QRectF(rect.left() + half, rect.top(), half, rect.height()), self.new)
        painter.setClipping(False)
        painter.setPen(QPen(QColor("#555b66"), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, 4, 4)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and event.position().x() < self.width() / 2:
            self.revert.emit()


class ColorPopover(QDialog):
    """The picker. ``eyedropper(done)`` starts a screen pick and calls ``done(QColor | None)``."""

    currentColorChanged = Signal(QColor)  # noqa: N815  (Qt naming, like QColorDialog)

    def __init__(self, parent: QWidget | None, initial: QColor, title: str = "Color", alpha: bool = False,
                 recent: list[str] | None = None, scene: list[QColor] | None = None,
                 eyedropper: Callable[[Callable[[QColor | None], None]], bool] | None = None) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setObjectName("colorPopover")
        self.setWindowTitle(title)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self.alpha_enabled = alpha
        self.eyedropper = eyedropper
        self._picking = False
        self._loop: QEventLoop | None = None
        self._initial = QColor(initial)
        if not alpha:
            self._initial.setAlpha(255)
        self._h, self._s, self._v, self._a = 0.0, 0.0, 0.0, 255
        self._exact: QColor | None = None  # the RGB that was set, until the sliders move (HSV rounds)
        self._load(self._initial)
        self.setStyleSheet(
            "QDialog#colorPopover { background: #1b1e24; border: 1px solid #3a4050; border-radius: 8px; }"
            "QLabel#popoverHint { color: #8a8f99; font-size: 11px; }"
            "QLabel#popoverHeading { color: #8a8f99; font-size: 10px; }"
            "QLineEdit#popoverHex, QSpinBox { background: #14161a; color: #d5e8ff; border: 1px solid #343a46;"
            " border-radius: 4px; padding: 0px 4px; min-height: 0px; selection-background-color: #3d8ef0; }"
            "QLineEdit#popoverHex:focus, QSpinBox:focus { border: 1px solid #3d8ef0; }"
            "QSpinBox::up-button, QSpinBox::down-button { width: 0px; border: none; }"  # else they still take room
            # The editor inside a spin box is a QLineEdit too: no second border or padding there.
            "QSpinBox QLineEdit { background: transparent; border: none; padding: 0px; margin: 0px;"
            " min-height: 0px; color: #d5e8ff; }"
            "QPushButton#popoverLink { color: #8a8f99; border: none; background: transparent; padding: 0px 6px;"
            " min-height: 0px; font-size: 11px; }"
            "QPushButton#popoverLink:hover { color: #d5e8ff; }"
            "QPushButton#popoverOk { background: #3d8ef0; color: #ffffff; border: none; border-radius: 4px;"
            " padding: 0px 10px; min-height: 0px; }"
            "QPushButton#popoverOk:hover { background: #5aa0f5; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.wheel = ColorWheel(self)
        layout.addWidget(self.wheel)
        # The eyedropper sits in the wheel's top-left corner, which the ring leaves empty.
        self.eyedropper_btn = QPushButton("", self.wheel)
        self.eyedropper_btn.setFixedSize(30, 30)
        self.eyedropper_btn.move(0, 0)
        self.eyedropper_btn.setFocusPolicy(Qt.NoFocus)
        self.eyedropper_btn.setIcon(icon("pick"))
        self.eyedropper_btn.setToolTip("Pick a colour from anywhere on the screen (I)")
        self.eyedropper_btn.setVisible(eyedropper is not None)
        self.eyedropper_btn.clicked.connect(self.start_eyedropper)
        self.alpha = AlphaSlider(self)
        self.alpha.setVisible(alpha)
        layout.addWidget(self.alpha)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.compare = Comparison(self)
        self.compare.old = self._initial
        top.addWidget(self.compare)
        self.hex = QLineEdit(self)
        self.hex.setObjectName("popoverHex")
        self.hex.setMaxLength(9 if alpha else 7)
        self.hex.setToolTip("Hex colour — also accepts #rgb, #rrggbbaa and rgb(r, g, b)")
        self.hex.setFixedHeight(FIELD_HEIGHT)
        top.addWidget(self._labelled("HEX", self.hex), 1)
        layout.addLayout(top)

        # Every colour code at once, all editable: RGB, HSV, HSL (and alpha).
        self.spins: dict[str, list[QSpinBox]] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        for row, (key, names, limits) in enumerate(CODES):
            heading = QLabel(key.upper(), self)
            heading.setObjectName("popoverHeading")
            heading.setFixedWidth(30)
            grid.addWidget(heading, row, 0)
            boxes = []
            for col, (name, (low, high, suffix)) in enumerate(zip(names, limits)):
                spin = QSpinBox(self)
                spin.setRange(low, high)
                spin.setSuffix(suffix)
                spin.setAlignment(Qt.AlignCenter)
                spin.setFixedHeight(FIELD_HEIGHT)
                spin.setToolTip(name)
                boxes.append(spin)
                grid.addWidget(spin, row, col + 1)
            self.spins[key] = boxes
        self.alpha_spin = QSpinBox(self)
        self.alpha_spin.setRange(0, 100)
        self.alpha_spin.setSuffix("%")
        self.alpha_spin.setAlignment(Qt.AlignCenter)
        self.alpha_spin.setFixedHeight(FIELD_HEIGHT)
        self.alpha_spin.setToolTip("Alpha (opacity)")
        alpha_label = QLabel("ALPHA", self)
        alpha_label.setObjectName("popoverHeading")
        alpha_label.setFixedWidth(30)
        self.alpha_spin.setVisible(alpha)
        alpha_label.setVisible(alpha)
        grid.addWidget(alpha_label, len(CODES), 0)
        grid.addWidget(self.alpha_spin, len(CODES), 1)
        layout.addLayout(grid)

        self.recent_row = self._swatch_row(layout, "Recent", recent or [])
        self.scene_row = self._swatch_row(layout, "In this scene", scene or [])

        bottom = QHBoxLayout()
        hint = QLabel("Esc to cancel", self)
        hint.setObjectName("popoverHint")
        bottom.addWidget(hint)
        bottom.addStretch()
        self.picker_btn = QPushButton("Picker ▾", self)
        self.picker_btn.setObjectName("popoverLink")
        self.picker_btn.setFlat(True)
        self.picker_btn.setFocusPolicy(Qt.NoFocus)
        self.picker_btn.setCursor(Qt.PointingHandCursor)
        self.picker_btn.setToolTip("Switch to another colour picker (used from the next colour you open)")
        self.picker_btn.clicked.connect(self._choose_picker)
        bottom.addWidget(self.picker_btn)
        ok = QPushButton("OK", self)
        ok.setObjectName("popoverOk")
        ok.setDefault(True)
        ok.setFixedSize(56, 26)
        ok.clicked.connect(self.accept)
        bottom.addWidget(ok)
        layout.addLayout(bottom)
        self.setFixedWidth(WIDTH + 20)

        self.wheel.sv_changed.connect(self._square_changed)
        self.wheel.hue_changed.connect(self._hue_changed)
        self.alpha.changed.connect(self._alpha_changed)
        self.hex.textEdited.connect(self._hex_typed)
        self.hex.editingFinished.connect(self._sync)
        for key, boxes in self.spins.items():
            for spin in boxes:
                spin.valueChanged.connect(lambda _v, k=key: self._code_typed(k))
        self.alpha_spin.valueChanged.connect(self._alpha_typed)
        self.compare.revert.connect(lambda: self.set_color(self._initial))
        self._sync()
        self.finished.connect(lambda _result: self._loop is not None and self._loop.quit())

    # ---- The colour ---------------------------------------------------------------------------

    def _load(self, color: QColor) -> None:
        hue = color.hsvHueF()
        if hue >= 0:  # greys have no hue: keep the one the user last had
            self._h = hue
        self._s, self._v = color.hsvSaturationF(), color.valueF()
        self._a = color.alpha() if self.alpha_enabled else 255
        self._exact = QColor(color.red(), color.green(), color.blue())

    def currentColor(self) -> QColor:  # noqa: N802  (QColorDialog naming)
        color = QColor(self._exact) if self._exact is not None else QColor.fromHsvF(
            min(0.999999, self._h), self._s, self._v)
        color.setAlpha(self._a)
        return color

    def set_color(self, color: QColor, keep_alpha: bool = False) -> None:
        """Set the colour (from a swatch, the eyedropper, a revert) and tell the listeners."""
        alpha = self._a
        self._load(color)
        if keep_alpha:
            self._a = alpha
        self._sync()
        self._emit()

    def _emit(self) -> None:
        self.currentColorChanged.emit(self.currentColor())

    def _sync(self) -> None:
        """Show the current colour in every control (without feeding back into them)."""
        color = self.currentColor()
        self.wheel.set_state(self._h, self._s, self._v)
        self.alpha.set_color(color)
        self.alpha.set_fraction(self._a / 255)
        self.compare.new = color
        self.compare.update()
        text = color.name().upper()
        if self.alpha_enabled and self._a < 255:
            text += f"{self._a:02X}"
        if not self.hex.hasFocus() or not self.hex.isModified():
            self.hex.blockSignals(True)
            self.hex.setText(text)
            self.hex.blockSignals(False)
            self.hex.setModified(False)
        for key, boxes in self.spins.items():
            for spin, value in zip(boxes, self._code_values(key, color)):
                if spin.value() != value and not spin.hasFocus():
                    spin.blockSignals(True)
                    spin.setValue(value)
                    spin.blockSignals(False)
        if not self.alpha_spin.hasFocus():
            self.alpha_spin.blockSignals(True)
            self.alpha_spin.setValue(round(self._a / 255 * 100))
            self.alpha_spin.blockSignals(False)

    def _code_values(self, key: str, color: QColor) -> tuple[int, int, int]:
        """The numbers of one colour code (the hue stays put for greys, which have none)."""
        if key == "rgb":
            return color.red(), color.green(), color.blue()
        hue = round(self._h * 360) % 360
        if key == "hsv":
            return hue, round(color.hsvSaturationF() * 100), round(color.valueF() * 100)
        return hue, round(color.hslSaturationF() * 100), round(color.lightnessF() * 100)

    def _square_changed(self, sat: float, val: float) -> None:
        self._s, self._v = sat, val
        self._exact = None
        self._sync()
        self._emit()

    def _hue_changed(self, fraction: float) -> None:
        self._h = fraction
        self._exact = None
        self._sync()
        self._emit()

    def _alpha_changed(self, fraction: float) -> None:
        self._a = round(fraction * 255)
        self._sync()
        self._emit()

    def _hex_typed(self, text: str) -> None:
        color = parse_color(text)
        if color is None:
            return
        if not self.alpha_enabled or len(text.lstrip("#")) != 8:
            color.setAlpha(self._a)
        self._load(color)
        self._a = color.alpha() if self.alpha_enabled else 255
        self._sync()
        self._emit()

    def _code_typed(self, key: str) -> None:
        """A number of the RGB, HSV or HSL row was edited."""
        x, y, z = (spin.value() for spin in self.spins[key])
        if key == "rgb":
            color = QColor(x, y, z)
            self._load(color)
        else:
            hue = min(0.999999, x / 360)
            color = (QColor.fromHsvF(hue, y / 100, z / 100) if key == "hsv"
                     else QColor.fromHslF(hue, y / 100, z / 100))
            self._load(color)
            self._h = hue  # keep the typed hue even when the colour is grey
        self._sync()
        self._emit()

    def _alpha_typed(self, percent: int) -> None:
        self._a = round(percent / 100 * 255)
        self._sync()
        self._emit()

    def _labelled(self, text: str, widget: QWidget) -> QWidget:
        box = QWidget(self)
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        label = QLabel(text, box)
        label.setObjectName("popoverHeading")
        label.setFixedSize(30, FIELD_HEIGHT)
        label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        row.addWidget(label)
        row.addWidget(widget, 1)
        return box

    # ---- Swatch rows --------------------------------------------------------------------------

    def _swatch_row(self, layout: QVBoxLayout, heading: str, colors: list[Any]) -> QWidget | None:
        colors = [QColor(c) for c in colors][:MAX_SWATCHES]
        colors = [c for c in colors if c.isValid()]
        if not colors:
            return None
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)
        label = QLabel(heading, box)
        label.setObjectName("popoverHeading")
        column.addWidget(label)
        row = QHBoxLayout()
        row.setSpacing(4)
        for color in colors:
            button = QPushButton("", box)
            button.setFixedSize(SWATCH, SWATCH)
            button.setFocusPolicy(Qt.NoFocus)
            button.setToolTip(color.name().upper() + (f"  α{color.alpha()}" if color.alpha() < 255 else ""))
            w.swatch(button, color)
            button.clicked.connect(lambda _c=False, c=QColor(color): self.set_color(c, keep_alpha=c.alpha() == 255))
            row.addWidget(button)
        row.addStretch()
        column.addLayout(row)
        layout.addWidget(box)
        return box

    # ---- Switching pickers ----------------------------------------------------------------------

    def _choose_picker(self) -> None:
        from PySide6.QtGui import QActionGroup
        from PySide6.QtWidgets import QMenu

        from pixelref.plugin_api.ui import color_dialog

        menu = QMenu(self)
        group = QActionGroup(menu)
        active = color_dialog.active().id
        for provider in color_dialog.providers():
            action = menu.addAction(provider.title)
            action.setCheckable(True)
            action.setChecked(provider.id == active)
            action.setToolTip(provider.description)
            action.setData(provider.id)
            group.addAction(action)
        self._picking = True  # the menu takes focus: that isn't a click away
        try:
            chosen = menu.exec(self.picker_btn.mapToGlobal(QPoint(0, self.picker_btn.height())))
        finally:
            self._picking = False
        if chosen is not None and chosen.data() != active:
            color_dialog.set_preferred(str(chosen.data()))
            self.picker_btn.setText(chosen.text() + " next time")

    # ---- Eyedropper ---------------------------------------------------------------------------

    def start_eyedropper(self) -> None:
        if self.eyedropper is None or self._picking:
            return
        self._picking = True
        self.hide()
        QApplication.processEvents()  # the screen is captured next: the popover must be gone from it

        def done(color: QColor | None) -> None:
            self._picking = False
            self.show()
            self.raise_()
            self.activateWindow()
            if color is not None and color.isValid():
                self.set_color(color)

        if not self.eyedropper(done):
            done(None)

    # ---- Running ------------------------------------------------------------------------------

    def exec(self) -> int:  # noqa: A003
        """Show beside the cursor and wait. Returns 1 when kept (Enter / OK), 0 on cancel."""
        self.adjustSize()
        self.move(self._place())
        self._loop = QEventLoop(self)
        self.show()
        self.raise_()
        self.activateWindow()
        self._loop.exec()
        self._loop = None
        self.hide()
        accepted = self.result() == QDialog.DialogCode.Accepted
        self.deleteLater()
        return 1 if accepted else 0

    def _place(self) -> QPoint:
        cursor = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        size = self.sizeHint().expandedTo(QSize(self.width(), 0))
        x = min(cursor.x() + 14, area.right() - size.width())
        y = cursor.y() + 14
        if y + size.height() > area.bottom():
            y = cursor.y() - size.height() - 14
        return QPoint(max(area.left(), x), max(area.top(), y))

    def changeEvent(self, event: Any) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and not self.isActiveWindow() and not self._picking:
            QTimer.singleShot(0, self._clicked_away)

    def _clicked_away(self) -> None:
        if self.isVisible() and not self._picking and not self.isActiveWindow():
            self.reject()

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802
        if event.key() == Qt.Key_I and not event.modifiers() and self.eyedropper is not None:
            self.start_eyedropper()
            return
        super().keyPressEvent(event)
