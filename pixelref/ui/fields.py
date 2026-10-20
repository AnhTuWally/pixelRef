"""Value editors: the draggable number field and editors generated from ``Attr`` specs.

:class:`NumberField` is a number box you drag left/right to change (click
without dragging to type a value). While dragging the cursor hides and wraps
at the screen edge; Shift is 10× faster, Alt 10× finer, Ctrl snaps to the
step; Esc puts the value back. How far one pixel goes: a field with a soft
range (``Attr.soft_min``/``soft_max``, else ``minimum``/``maximum``) covers
that range in about 300 px; a field without one moves a fixed step per pixel.

:func:`editor_for` makes the editor for an attribute spec. Every editor it
returns has the same small interface:

* ``read()`` / ``write(value)`` — the value in the attribute's own units
  (``write`` never emits anything);
* ``committed(value)`` — the user finished a change;
* number editors also have ``scrub_started``, ``previewed(value)`` and
  ``scrub_finished(cancelled)`` for live previews while dragging;
* ``context_provider`` — optional callable returning extra right-click menu
  entries as ``(text, callback)`` pairs (``(None, None)`` is a separator).

:func:`bind_edits` connects an editor to the document so a drag previews live
and becomes a single undo step.
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QWidget,
)

from . import color_dialog

if TYPE_CHECKING:
    from ..core.attributes import Attr
    from .app import App

SOFT_RANGE_PIXELS = 300.0  # a drag covers a field's soft range in about this many pixels
DRAG_THRESHOLD = 3.0  # pixels before a press becomes a drag (a click lets you type)
EDGE_MARGIN = 40  # the hidden cursor wraps back to the middle this close to the screen edge
UNBOUNDED = 1e9

MenuEntries = list[tuple[str | None, Callable[[], Any] | None]]


def nice_step(span: float | None) -> float:
    """A round increment, about 1/100 of ``span`` (0.1 without one)."""
    if not span or not math.isfinite(span) or span <= 0:
        return 0.1
    raw = span / 100.0
    power = 10.0 ** math.floor(math.log10(raw))
    for multiple in (1.0, 2.0, 5.0, 10.0):
        if raw <= multiple * power * (1 + 1e-9):
            return multiple * power
    return 10.0 * power


def decimals_for(step: float) -> int:
    """Decimals that show increments of ``step`` (0.05 → 2)."""
    if step <= 0 or not math.isfinite(step):
        return 2
    return max(0, min(6, -math.floor(math.log10(step) + 1e-9)))


def drag_factor(modifiers: Any) -> float:
    if modifiers & Qt.ShiftModifier:
        return 10.0
    if modifiers & Qt.AltModifier:
        return 0.1
    return 1.0


def show_menu(parent: QWidget, global_pos: QPoint, entries: MenuEntries) -> None:
    if not entries:
        return
    menu = QMenu(parent)
    for text, callback in entries:
        if text is None:
            menu.addSeparator()
            continue
        action = menu.addAction(text)
        if callback is None:
            action.setEnabled(False)
        else:
            action.triggered.connect(lambda _checked=False, c=callback: c())
    menu.exec(global_pos)


def install_context_menu(widget: QWidget, entries: Callable[[], MenuEntries]) -> None:
    """Right-click on ``widget`` shows ``entries()``."""
    widget.setContextMenuPolicy(Qt.CustomContextMenu)
    widget.customContextMenuRequested.connect(lambda pos: show_menu(widget, widget.mapToGlobal(pos), entries()))


def _provided(editor: Any) -> MenuEntries:
    provider = getattr(editor, "context_provider", None)
    return list(provider()) if callable(provider) else []


# ---- The number field ---------------------------------------------------------------------------------


class Scrub:
    """One press-and-drag on a number field (or a label that drives one)."""

    def __init__(self, field: "NumberField", press: QPointF) -> None:
        self.field = field
        self.press = QPointF(press)
        self.last = QPointF(press)
        self.start = field.value()
        self.raw = self.start
        self.low, self.high = field.drag_limits(self.start)
        self.per_pixel = field.per_pixel()
        self.active = False  # past the threshold: a drag, not a click
        self.done = False
        self.warped_to: QPoint | None = None  # after a warp: skip events queued from before it

    def move(self, pos: QPointF, modifiers: Any) -> None:
        if self.done:
            return
        if not self.active:
            if abs(pos.x() - self.press.x()) < DRAG_THRESHOLD:
                return  # a click with a pixel of jitter must not change the value
            self.active = True
            self.field.begin_scrub(self)
        if self.warped_to is not None:
            if abs(pos.x() - self.warped_to.x()) > EDGE_MARGIN:
                return  # queued before the cursor jumped: its position is stale
            self.warped_to = None
        dx = pos.x() - self.last.x()
        self.last = QPointF(pos)
        self.raw = min(self.high, max(self.low, self.raw + dx * self.per_pixel * drag_factor(modifiers)))
        value = self.raw
        step = self.field.singleStep()
        if modifiers & Qt.ControlModifier and step > 0:
            value = min(self.high, max(self.low, round(value / step) * step))
        self.field.scrub_to(value)
        self._wrap(pos)

    def _wrap(self, pos: QPointF) -> None:
        """Keep the hidden cursor away from the screen edge so a drag never runs out of room.
        Only when the real cursor is where the event says (synthetic events never warp)."""
        if QGuiApplication.platformName() in ("offscreen", "minimal"):
            return  # no real cursor
        cursor = QCursor.pos()
        if abs(cursor.x() - pos.x()) > 1 or abs(cursor.y() - pos.y()) > 1:
            return
        screen = QGuiApplication.screenAt(cursor)
        if screen is None:
            return
        area = screen.geometry()
        if area.left() + EDGE_MARGIN < cursor.x() < area.right() - EDGE_MARGIN:
            return
        target = QPoint(area.center().x(), cursor.y())
        QCursor.setPos(target)
        if QCursor.pos() == target:
            self.last = QPointF(target)
            self.warped_to = target

    def release(self) -> None:
        """Mouse up: finish a drag (a plain click is the caller's business)."""
        if self.active and not self.done:
            self.field.end_scrub(cancelled=False)

    def cancel(self) -> None:
        if self.active and not self.done:
            self.field.end_scrub(cancelled=True)


class NumberField(QDoubleSpinBox):
    """A number box you drag to change. See the module docstring.

    ``value()``/``setValue()`` are in display units (Qt's own API, used by the
    hand-built sections); ``read()``/``write()`` and the signals are in the
    attribute's units (``display_scale`` apart, e.g. opacity 0-1 shown as %).
    ``editingFinished`` is emitted after typing, after arrow-key/wheel steps
    and at the end of a drag, so code that listens to it commits all of them.
    """

    previewed = Signal(object)
    committed = Signal(object)
    scrub_started = Signal()
    scrub_finished = Signal(bool)  # True: cancelled with Esc (the value is back where it started)

    def __init__(self, parent: QWidget | None = None, *, minimum: float = -UNBOUNDED, maximum: float = UNBOUNDED,
                 step: float = 1.0, precision: int = 3, suffix: str = "",
                 soft_range: tuple[float, float] | None = None, scrub_step: float | None = None,
                 display_scale: float = 1.0, integer: bool = False, default: float | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("numberField")
        self.setKeyboardTracking(False)
        # Tab focus only: Qt focuses a click-focus widget *before* delivering the press, which
        # made every press look like a click in a field being typed in (text selection, no drag).
        # A press now drags; a click without dragging focuses the field for typing. (Not
        # WheelFocus either: scrolling the inspector must not grab fields.)
        self.setFocusPolicy(Qt.TabFocus)
        self.setDecimals(0 if integer else precision)
        self.setRange(minimum, maximum)
        self.setSingleStep(step)
        self.setSuffix(suffix)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setMinimumWidth(64)
        self.soft_range = soft_range  # display units
        self.scrub_step = scrub_step  # per pixel when there is no soft range (else singleStep)
        self.display_scale = display_scale or 1.0
        self.integer = integer
        self.context_provider: Callable[[], MenuEntries] | None = None
        self._press: Scrub | None = None  # a press on the text part (may become a drag)
        self._typing = False  # clicked to type: presses select text until Enter / Esc / focus leaves
        self._scrub: Scrub | None = None  # the drag in progress (from the field or a label)
        self._cursor_hidden = False
        if default is not None:
            self.setValue(default)
        self._baseline = self.value()
        edit = self.lineEdit()
        edit.setFocusPolicy(Qt.TabFocus)
        edit.installEventFilter(self)
        edit.setCursor(Qt.SizeHorCursor)
        self.editingFinished.connect(self._on_editing_finished)

    # ---- Values in attribute units ---------------------------------------------------------------

    def read(self) -> Any:
        value = self.value() / self.display_scale
        return int(round(value)) if self.integer else value

    def write(self, value: Any) -> None:
        if self._scrub is not None:
            return  # don't fight the drag (the document echoes what it previews)
        try:
            shown = float(value if value is not None else 0.0) * self.display_scale
        except (TypeError, ValueError):
            return
        self.blockSignals(True)
        self.setValue(shown)
        self.blockSignals(False)
        self._baseline = self.value()

    @property
    def scrubbing(self) -> bool:
        return self._scrub is not None

    # ---- Dragging --------------------------------------------------------------------------------

    def per_pixel(self) -> float:
        """How far one pixel of drag moves the (displayed) value, before modifiers."""
        if self.soft_range is not None:
            low, high = self.soft_range
            if high > low:
                return (high - low) / SOFT_RANGE_PIXELS
        if self.scrub_step:
            return self.scrub_step
        return self.singleStep() or 1.0

    def drag_limits(self, start: float) -> tuple[float, float]:
        """A drag stays in the soft range (stretched to include where it started) and the hard range."""
        low, high = self.minimum(), self.maximum()
        if self.soft_range is not None:
            low = max(low, min(self.soft_range[0], start))
            high = min(high, max(self.soft_range[1], start))
        return low, high

    def start_scrub(self, global_pos: QPointF) -> Scrub:
        return Scrub(self, global_pos)

    def begin_scrub(self, scrub: Scrub) -> None:
        self._scrub = scrub
        if self.lineEdit().hasFocus():
            self.lineEdit().deselect()
        if self.isVisible():
            QApplication.setOverrideCursor(Qt.BlankCursor)
            self._cursor_hidden = True
        # Keys go to the drag while it lasts: Esc cancels it instead of reaching the app's
        # keymap (which would deselect). The newest application filter runs first.
        QApplication.instance().installEventFilter(self)
        self.scrub_started.emit()

    def scrub_to(self, value: float) -> None:
        before = self.value()
        self.setValue(value)
        if self.value() != before:
            self.previewed.emit(self.read())

    def end_scrub(self, cancelled: bool) -> None:
        scrub, self._scrub = self._scrub, None
        if scrub is None:
            return
        scrub.done = True
        QApplication.instance().removeEventFilter(self)
        if self._cursor_hidden:
            self._cursor_hidden = False
            QApplication.restoreOverrideCursor()
            QCursor.setPos(scrub.press.toPoint())  # the cursor comes back where the drag began
        if cancelled:
            if self.value() != scrub.start:
                self.setValue(scrub.start)
                self.previewed.emit(self.read())
        else:
            self.editingFinished.emit()
        self.scrub_finished.emit(cancelled)

    def _on_editing_finished(self) -> None:
        if self._scrub is not None:
            return
        if self.value() != self._baseline:
            self._baseline = self.value()
            self.committed.emit(self.read())

    # ---- Events ------------------------------------------------------------------------------------

    # ---- Typing mode -------------------------------------------------------------------------------
    # Not tied to focus: Qt focuses fields on its own (window activation, after Enter), and a
    # focused field must still drag. Click (without dragging) or Tab to type; Enter, Esc or
    # leaving the field goes back to dragging.

    @property
    def typing(self) -> bool:
        return self._typing

    def start_typing(self) -> None:
        if not self._typing:
            self._typing = True
            self.lineEdit().setCursor(Qt.IBeamCursor)
            QApplication.instance().installEventFilter(self)  # Esc reverts here, not in the app keymap
        if not self.hasFocus():
            self.setFocus(Qt.MouseFocusReason)
        self.selectAll()

    def stop_typing(self, leave: bool = False) -> None:
        if self._typing:
            self._typing = False
            QApplication.instance().removeEventFilter(self)
            self.lineEdit().setCursor(Qt.SizeHorCursor)
            self.lineEdit().deselect()
        if leave and self.hasFocus():
            self.clearFocus()

    def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802
        if self._scrub is not None and event.type() in (QEvent.KeyPress, QEvent.ShortcutOverride):
            if event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                self._scrub.cancel()
            event.accept()
            return True
        if self._typing and event.type() in (QEvent.KeyPress, QEvent.ShortcutOverride)                 and event.key() == Qt.Key_Escape and self.hasFocus():
            if event.type() == QEvent.KeyPress:
                self.setValue(self._baseline)  # Esc: the typed text is dropped
                self.stop_typing(leave=True)
            event.accept()
            return True
        if obj is not self.lineEdit():
            return super().eventFilter(obj, event)
        kind = event.type()
        editing = self._typing
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton and not editing:
            if self.isEnabled() and not self.isReadOnly():
                self._press = self.start_scrub(event.globalPosition())
            return True
        if kind == QEvent.MouseMove and self._press is not None:
            self._press.move(event.globalPosition(), event.modifiers())
            return True
        if kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton and self._press is not None:
            press, self._press = self._press, None
            if press.active:
                press.release()
            elif not press.done:
                self.start_typing()  # a click: type a value
            return True
        if kind == QEvent.MouseButtonDblClick and not editing:
            self.start_typing()
            return True
        if kind == QEvent.ContextMenu:
            self._show_menu(event.globalPos())
            return True
        if kind == QEvent.FocusIn and event.reason() in (Qt.TabFocusReason, Qt.BacktabFocusReason):
            self.start_typing()
        elif kind == QEvent.FocusOut and event.reason() != Qt.PopupFocusReason:
            self.stop_typing()
        return super().eventFilter(obj, event)

    def contextMenuEvent(self, event: Any) -> None:  # noqa: N802
        self._show_menu(event.globalPos())

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802
        if self._scrub is not None:
            if event.key() == Qt.Key_Escape:
                self._scrub.cancel()
            event.accept()
            return
        super().keyPressEvent(event)
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.stop_typing(leave=True)  # Enter: the value is in, the field drags again

    def focusOutEvent(self, event: Any) -> None:  # noqa: N802
        super().focusOutEvent(event)
        if event.reason() != Qt.PopupFocusReason:  # (the right-click menu keeps you typing)
            self.stop_typing()

    def wheelEvent(self, event: Any) -> None:  # noqa: N802
        if not self.hasFocus():
            event.ignore()  # let the panel scroll
            return
        super().wheelEvent(event)

    def stepBy(self, steps: int) -> None:  # noqa: N802
        super().stepBy(steps)
        if self._scrub is None:
            self.editingFinished.emit()  # arrow keys / wheel: each step is a finished change

    def hideEvent(self, event: Any) -> None:  # noqa: N802
        super().hideEvent(event)
        if self._scrub is not None:
            # Panels hide and re-show sections while refreshing: only a field that stays
            # hidden ends its drag (never leave the cursor hidden).
            QTimer.singleShot(0, self._end_if_hidden)

    def _end_if_hidden(self) -> None:
        try:
            visible = self.isVisible()
        except RuntimeError:  # deleted meanwhile
            return
        if not visible and self._scrub is not None:
            self._scrub.release()
            self._press = None

    def _show_menu(self, global_pos: QPoint) -> None:
        entries = _provided(self)
        if entries:
            entries.append((None, None))
        entries += [("Copy value", self._copy), ("Paste value", self._paste if self._clipboard_number() is not None
                                                 else None)]
        show_menu(self, global_pos, entries)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.cleanText().strip())

    def _clipboard_number(self) -> float | None:
        text = QApplication.clipboard().text().strip().rstrip("%°").replace(self.suffix().strip(), "").strip()
        try:
            return float(text)
        except ValueError:
            return None

    def _paste(self) -> None:
        number = self._clipboard_number()
        if number is not None:
            self.setValue(number)
            self.editingFinished.emit()


# ---- Editors for attribute specs -------------------------------------------------------------------------


class CheckEditor(QCheckBox):
    committed = Signal(object)

    def __init__(self, flag: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.flag = flag
        self.context_provider: Callable[[], MenuEntries] | None = None
        self.toggled.connect(lambda _on: self.committed.emit(self.read()))
        install_context_menu(self, lambda: _provided(self))

    def read(self) -> Any:
        return int(self.isChecked()) if self.flag else self.isChecked()

    def write(self, value: Any) -> None:
        self.blockSignals(True)
        self.setChecked(bool(value))
        self.blockSignals(False)


class ComboEditor(QComboBox):
    committed = Signal(object)

    def __init__(self, choices: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.context_provider: Callable[[], MenuEntries] | None = None
        for choice in choices:
            self.addItem(choice.replace("_", " ").capitalize(), choice)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(6)
        self.currentIndexChanged.connect(lambda _i: self.committed.emit(self.read()))
        install_context_menu(self, lambda: _provided(self))

    def read(self) -> Any:
        return self.currentData()

    def write(self, value: Any) -> None:
        index = self.findData(value)
        self.blockSignals(True)
        self.setCurrentIndex(index if index >= 0 else 0)
        self.blockSignals(False)


class ColorEditor(QPushButton):
    """A swatch; click to choose a colour (``[r, g, b]``, or ``[r, g, b, a]`` with alpha)."""

    committed = Signal(object)
    previewed = Signal(object)
    scrub_started = Signal()
    scrub_finished = Signal(bool)  # True: the dialog was cancelled (value restored)

    def __init__(self, alpha: bool, title: str = "Color", parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.alpha = alpha
        self.title = title
        self.color = QColor(255, 255, 255)
        self.context_provider: Callable[[], MenuEntries] | None = None
        self.setObjectName("styleBtn")
        self.setFixedSize(40, 22)
        self.setFocusPolicy(Qt.NoFocus)
        self.clicked.connect(self.pick)
        install_context_menu(self, lambda: _provided(self))
        self._show()

    def read(self) -> Any:
        c = self.color
        return [c.red(), c.green(), c.blue(), c.alpha()] if self.alpha else [c.red(), c.green(), c.blue()]

    def write(self, value: Any) -> None:
        from .widgets import color_from

        self.color = color_from(value if value is not None else [255, 255, 255])
        self._show()

    def pick(self) -> None:
        """Open the colour dialog; the canvas follows the dialog live (one undo step, Cancel restores)."""
        original = QColor(self.color)
        dialog = color_dialog.create(self.window(), original, self.title, self.alpha)
        state = {"open": False}

        def preview(color: QColor) -> None:
            if not color.isValid() or color == self.color:
                return
            if not self.alpha:
                color.setAlpha(255)
            if not state["open"]:
                state["open"] = True
                self.scrub_started.emit()
            self.color = QColor(color)
            self._show()
            self.previewed.emit(self.read())

        dialog.currentColorChanged.connect(preview)
        accepted = bool(dialog.exec())
        if accepted:
            preview(dialog.currentColor())
        elif self.color != original:
            self.color = original
            self._show()
            self.previewed.emit(self.read())  # put the value back before closing the edit
        if state["open"]:
            self.scrub_finished.emit(not accepted)

    def _show(self) -> None:
        from .widgets import swatch

        swatch(self, self.color)
        self.setToolTip(f"{self.title} {self.color.name().upper()} — click to change")


class TextEditor(QLineEdit):
    committed = Signal(object)

    def __init__(self, as_list: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.as_list = as_list
        self.context_provider: Callable[[], MenuEntries] | None = None
        self._baseline = ""
        if as_list:
            self.setPlaceholderText("comma, separated")
        self.editingFinished.connect(self._finished)

    def _finished(self) -> None:
        if self.text() != self._baseline:
            self._baseline = self.text()
            self.committed.emit(self.read())

    def read(self) -> Any:
        if self.as_list:
            return [part.strip() for part in self.text().split(",") if part.strip()]
        return self.text()

    def write(self, value: Any) -> None:
        text = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value if value is not None else "")
        if text != self.text():
            self.setText(text)
        self._baseline = text


class VectorEditor(QWidget):
    """Number fields side by side (float2): drag each one; the value is the list."""

    previewed = Signal(object)
    committed = Signal(object)
    scrub_started = Signal()
    scrub_finished = Signal(bool)

    def __init__(self, fields: list[NumberField], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.fields = fields
        self.context_provider: Callable[[], MenuEntries] | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        for field in fields:
            field.setParent(self)
            field.setMinimumWidth(48)
            field.context_provider = lambda: _provided(self)
            layout.addWidget(field)
            field.previewed.connect(lambda _v: self.previewed.emit(self.read()))
            field.committed.connect(lambda _v: self.committed.emit(self.read()))
            field.scrub_started.connect(self.scrub_started.emit)
            field.scrub_finished.connect(self.scrub_finished.emit)

    def read(self) -> Any:
        return [field.read() for field in self.fields]

    def write(self, value: Any) -> None:
        parts = list(value) if isinstance(value, (list, tuple)) else []
        for field, part in zip(self.fields, parts):
            field.write(part)


class ValueLabel(QLabel):
    """Read-only view of a value nothing better edits (points, JSON data…)."""

    committed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.setObjectName("pathLabel")
        self.setWordWrap(True)
        self.context_provider: Callable[[], MenuEntries] | None = None
        self._value: Any = None

    def read(self) -> Any:
        return self._value

    def write(self, value: Any) -> None:
        self._value = value
        text = json.dumps(value)
        self.setText(text if len(text) < 200 else text[:200] + "…")


def number_field_for(attr: "Attr", parent: QWidget | None = None) -> NumberField:
    """A number field set up from an int/float attribute's range and editing hints."""
    scale = attr.display_scale or 1.0
    integer = attr.type == "int"
    low = attr.minimum * scale if attr.minimum is not None else -UNBOUNDED
    high = attr.maximum * scale if attr.maximum is not None else UNBOUNDED
    soft_low = attr.soft_min if attr.soft_min is not None else attr.minimum
    soft_high = attr.soft_max if attr.soft_max is not None else attr.maximum
    soft = None
    if soft_low is not None and soft_high is not None and soft_high > soft_low:
        soft = (soft_low * scale, soft_high * scale)
    if attr.step:
        step = attr.step * scale
    elif integer:
        step = 1.0
    else:
        step = nice_step(soft[1] - soft[0]) if soft else 0.1
    if integer:
        step = max(1.0, float(round(step)))
    if attr.precision is not None:
        precision = attr.precision
    else:
        precision = 0 if integer else max(1, decimals_for(step))
    suffix = f" {attr.unit}" if attr.unit else ""
    return NumberField(parent, minimum=low, maximum=high, step=step, precision=precision, suffix=suffix,
                       soft_range=soft, display_scale=scale, integer=integer)


def editor_for(attr: "Attr", parent: QWidget | None = None) -> QWidget:
    """The editor for an attribute spec (see the module docstring for its interface)."""
    kind = attr.type
    editor: QWidget
    if kind in ("bool", "flag"):
        editor = CheckEditor(kind == "flag", parent)
    elif kind in ("int", "float"):
        editor = number_field_for(attr, parent)
    elif kind == "float2":
        editor = VectorEditor([number_field_for(attr) for _ in range(2)], parent)
    elif kind in ("color", "color4"):
        editor = ColorEditor(kind == "color4", attr.nice_name, parent)
    elif kind == "enum":
        editor = ComboEditor(attr.choices, parent)
    elif kind in ("string", "text", "path", "asset", "node_ref"):
        editor = TextEditor(False, parent)
    elif kind in ("string_list", "node_refs"):
        editor = TextEditor(True, parent)
    else:
        editor = ValueLabel(parent)
    if attr.doc:
        editor.setToolTip(attr.doc)
    editor.write(attr.default_value())
    return editor


def is_live(editor: Any) -> bool:
    """True for editors that preview while dragging."""
    return hasattr(editor, "previewed") and hasattr(editor, "scrub_started")


def pick_color_live(app: "App", parent: QWidget | None, initial: QColor, title: str,
                    apply: Callable[[QColor], Any], label: str = "Edit", alpha: bool = False,
                    live: bool = True) -> QColor | None:
    """Colour dialog whose every change calls ``apply(color)`` live: one undo step, Cancel restores.

    ``live=False`` calls ``apply`` once, on OK. Returns the chosen colour, or ``None`` when
    cancelled (or nothing changed).
    """
    dialog = color_dialog.create(parent, QColor(initial), title, alpha)
    state = {"open": False, "last": QColor(initial)}

    def preview(color: QColor) -> None:
        if not color.isValid() or color == state["last"]:
            return
        if not live:
            state["last"] = QColor(color)
            return
        if not state["open"]:
            state["open"] = True
            app.canvas.begin_interaction()
        state["last"] = QColor(color)
        apply(QColor(color))

    dialog.currentColorChanged.connect(preview)
    accepted = bool(dialog.exec())
    if accepted:
        preview(dialog.currentColor())
        if not live and state["last"] != initial:
            apply(QColor(state["last"]))
    elif state["open"]:
        apply(QColor(initial))
        app.canvas.cancel_interaction()
        state["open"] = False
    if state["open"]:
        app.canvas.end_interaction(label)
    return QColor(state["last"]) if accepted and state["last"] != initial else None


def bind_edits(app: "App", editor: Any, apply: Callable[[Any], Any], label: str = "Edit",
               commit: bool = True) -> None:
    """Connect an editor to the document through ``apply(value)`` (which runs a command).

    A drag previews live: everything from press to release is one undo step
    named ``label`` (Esc puts the value back and records nothing). Other
    changes (typing, a checkbox, a colour) apply once each. ``commit=False``
    connects only the live preview (for sections that already commit on
    ``editingFinished``).
    """
    state = {"open": False}

    def started() -> None:
        app.canvas.begin_interaction()
        state["open"] = True

    def finished(cancelled: bool) -> None:
        if state["open"]:
            state["open"] = False
            if cancelled:
                app.canvas.cancel_interaction()  # exactly as before the drag, nothing to undo
            else:
                app.canvas.end_interaction(label)

    if commit:
        editor.committed.connect(apply)
    if is_live(editor):
        editor.scrub_started.connect(started)
        editor.previewed.connect(apply)
        editor.scrub_finished.connect(finished)


# ---- Tool options ----------------------------------------------------------------------------------------


def option_menu(tool: Any, name: str) -> MenuEntries:
    """Right-click entries for one tool option: make it the default, or reset it."""
    options = tool.options
    title = tool.title or tool.id

    def save() -> None:
        options.set_default(name, options.get(name))
        tool.ui.toast(f"{options.spec(name).nice_name}: saved as your default for {title}")

    return [
        ("Set as my default", save),
        ("Reset to my default", lambda: options.set(name, options.defaults()[name])),
        ("Reset to factory default", lambda: options.set(name, options.factory_defaults()[name])),
    ]


def bind_option(tool: Any, editor: Any, name: str) -> Any:
    """Keep ``editor`` and ``tool.options[name]`` in sync (both ways); returns the editor."""
    options = tool.options
    options.spec(name)
    editor.write(options.get(name))

    def apply(value: Any) -> None:
        try:
            options.set(name, value)
        except Exception as exc:  # noqa: BLE001  (a bad value: show it, keep the old one)
            tool.ui.toast(str(exc), QColor("#d27b7b"))
            editor.write(options.get(name))

    editor.committed.connect(apply)
    if is_live(editor):
        editor.previewed.connect(apply)
    handle = options.watch(lambda changed, value: changed == name and editor.write(value))
    editor.destroyed.connect(lambda *_args: handle.dispose())
    editor.context_provider = lambda: option_menu(tool, name)
    return editor


def option_editor(tool: Any, name: str, parent: QWidget | None = None) -> Any:
    """The editor for one of ``tool``'s options, bound to it (for hand-built option panels)."""
    return bind_option(tool, editor_for(tool.options.spec(name), parent), name)


def options_form(tool: Any, parent: QWidget | None = None, names: Any = None) -> QWidget:
    """A form with an editor per option (all, or ``names``); ``panel.editors[name]``."""
    from PySide6.QtWidgets import QFormLayout

    from .widgets import ScrubLabel

    panel = QWidget(parent)
    form = QFormLayout(panel)
    form.setContentsMargins(0, 0, 0, 0)
    form.setHorizontalSpacing(8)
    form.setVerticalSpacing(6)
    panel.editors = {}  # type: ignore[attr-defined]
    chosen = [n for n, s in tool.options.specs.items() if not s.hidden] if names is None else list(names)
    for name in chosen:
        spec = tool.options.spec(name)
        editor = option_editor(tool, name, panel)
        panel.editors[name] = editor  # type: ignore[attr-defined]
        if isinstance(editor, NumberField):
            label: QWidget = ScrubLabel(spec.nice_name, editor, editor.per_pixel(), panel)
        else:
            label = QLabel(spec.nice_name, panel)
        if spec.doc:
            label.setToolTip(spec.doc)
        form.addRow(label, editor)
    return panel
