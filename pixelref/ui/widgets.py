"""Small widgets and helpers shared by the kernel UI and plugins."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import fields  # noqa: F401  (plugins: widgets.fields.option_editor…)
from .fields import (  # noqa: F401
    NumberField, bind_edits, drag_factor, editor_for, install_context_menu, pick_color_live,
)
from .icons import icon

LINE_STYLE_CHOICES = (
    ("solid", "Solid"),
    ("dash", "Dashed"),
    ("dot", "Dotted"),
    ("dashdot", "Dash-dot"),
    ("none", "None"),
)
FILL_STYLE_CHOICES = (
    ("solid", "Solid"),
    ("dense", "Dense"),
    ("sparse", "Sparse"),
    ("diagonal", "Diagonal hatch"),
    ("cross", "Cross hatch"),
    ("horizontal", "Horizontal lines"),
    ("vertical", "Vertical lines"),
    ("grid", "Grid"),
    ("none", "None"),
)


# ---- Colour helpers -----------------------------------------------------------------------


def rgb(color: QColor) -> list[int]:
    return [color.red(), color.green(), color.blue()]


def rgba(color: QColor) -> list[int]:
    return [color.red(), color.green(), color.blue(), color.alpha()]


def color_from(values: Any, default: tuple[int, ...] = (220, 220, 220)) -> QColor:
    try:
        parts = [int(v) for v in values]
    except (TypeError, ValueError):
        parts = list(default)
    while len(parts) < 3:
        parts.append(0)
    color = QColor(parts[0], parts[1], parts[2])
    if len(parts) > 3:
        color.setAlpha(parts[3])
    return color


def format_size(size: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


# ---- Factories ------------------------------------------------------------------------------


def make_spin(parent: QWidget | None = None, minimum: float = -1_000_000.0, maximum: float = 1_000_000.0,
              step: float = 1.0, default: float = 0.0, soft_range: tuple[float, float] | None = None) -> NumberField:
    """A draggable number field (see :class:`pixelref.ui.fields.NumberField`)."""
    # Otherwise the hint is sized for "-1000000.000" and the inspector overflows (min width 64).
    return NumberField(parent, minimum=minimum, maximum=maximum, step=step, precision=3, default=default,
                       soft_range=soft_range)


def compact_spin(spin: QAbstractSpinBox, decimals: int | None = None, suffix: str = "", width: int = 64) -> None:
    # Buttons are hidden by the stylesheet (#compactSpin): with NoButtons plus a
    # stylesheet Qt collapses the text field to 1px and the value disappears.
    spin.setObjectName("compactSpin")
    if decimals is not None and hasattr(spin, "setDecimals"):
        spin.setDecimals(decimals)
    if hasattr(spin, "setSuffix"):
        spin.setSuffix(suffix)
    spin.setMinimumWidth(width)
    spin.setAlignment(Qt.AlignRight | Qt.AlignVCenter)


def make_combo(choices: tuple[tuple[str, str], ...], parent: QWidget | None = None) -> QComboBox:
    combo = QComboBox(parent)
    for value, label in choices:
        combo.addItem(label, value)
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(6)
    return combo


def set_combo(combo: QComboBox, value: Any) -> None:
    index = combo.findData(value)
    combo.setCurrentIndex(index if index >= 0 else 0)


def make_tool_button(text: str, tip: str, checkable: bool = False, width: int = 28,
                     parent: QWidget | None = None) -> QPushButton:
    # NoFocus: clicking must not steal focus from a note being edited on the
    # canvas, otherwise the edit would end before the style is applied.
    button = QPushButton(text, parent)
    button.setObjectName("styleBtn")
    button.setCheckable(checkable)
    button.setFocusPolicy(Qt.NoFocus)
    button.setFixedSize(width, 26)
    button.setToolTip(tip)
    return button


def hrow(parent: QWidget | None, *widgets: QWidget | None) -> QWidget:
    """Horizontal row of widgets; ``None`` inserts a small gap."""
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(3)
    for widget in widgets:
        if widget is None:
            layout.addSpacing(8)
        else:
            layout.addWidget(widget)
    layout.addStretch()
    return row


def swatch(button: QPushButton, color: QColor, visible: bool = True, text: str | None = None) -> None:
    """Show ``color`` on a button; an 'off' colour shows a struck-through dark chip."""
    if text is not None:
        button.setText(text)
    if visible and color.alpha() > 0:
        light = color.lightnessF() > 0.55 or color.alpha() < 90
        fg = "#14161a" if light else "#ffffff"
        bg = f"rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()})"
        button.setStyleSheet(f"background: {bg}; color: {fg}; border: 1px solid #555b66; border-radius: 3px;")
    else:
        button.setStyleSheet("background: #14161a; color: #606878; border: 1px dashed #4a5160; border-radius: 3px;")


def section_header(parent: QWidget | None, title: str, trailing: QWidget | None = None) -> QWidget:
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(2, 6, 0, 2)
    layout.setSpacing(6)
    label = QLabel(title.upper(), row)
    label.setObjectName("sectionHeader")
    layout.addWidget(label)
    layout.addStretch()
    if trailing is not None:
        layout.addWidget(trailing)
    return row


def type_icon(color: str, glyph: str) -> QIcon:
    """Outliner badge: a coloured rounded square with a letter."""
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(1, 1, 14, 14), 3, 3)
    painter.setPen(QColor("#14161a"))
    font = QFont()
    font.setPixelSize(10)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QRectF(0, 0, 16, 16), Qt.AlignCenter, glyph)
    painter.end()
    return QIcon(pixmap)


# ---- Widgets ---------------------------------------------------------------------------------


class ShortcutButton(QPushButton):
    """Button that shows its keyboard shortcut, dimmed, on the right."""

    def __init__(self, text: str, shortcut: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._shortcut = shortcut
        self.setProperty("hasShortcut", bool(shortcut))

    def shortcut_text(self) -> str:
        return self._shortcut

    def set_shortcut_text(self, text: str) -> None:
        self._shortcut = text
        self.setProperty("hasShortcut", bool(text))
        self.updateGeometry()
        self.update()

    def _shortcut_font(self) -> QFont:
        font = QFont(self.font())
        # The stylesheet sizes button fonts in pixels, so pointSizeF() is -1 there.
        if font.pixelSize() > 0:
            font.setPixelSize(max(8, round(font.pixelSize() * 0.9)))
        else:
            font.setPointSizeF(max(7.0, font.pointSizeF() * 0.9))
        return font

    def _extra_width(self) -> int:
        if not self._shortcut:
            return 0
        return QFontMetrics(self._shortcut_font()).horizontalAdvance(self._shortcut) + 4

    def sizeHint(self) -> QSize:  # noqa: N802
        hint = super().sizeHint()
        return QSize(hint.width() + self._extra_width(), hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        hint = super().minimumSizeHint()
        return QSize(hint.width() + self._extra_width(), hint.height())

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)
        # Skip the hint rather than overlap the label when the button is squeezed.
        if not self._shortcut or self.width() < self.sizeHint().width():
            return
        painter = QPainter(self)
        painter.setFont(self._shortcut_font())
        painter.setPen(QColor("#a9c8f5") if self.isChecked() else QColor("#6f7787"))
        painter.drawText(self.rect().adjusted(0, 0, -5, 0), Qt.AlignRight | Qt.AlignVCenter, self._shortcut)
        painter.end()


class ScrubLabel(QLabel):
    """Field label you can drag left/right to change its number field, like the
    field itself (Shift 10× faster, Alt 10× finer, Ctrl snaps, Esc puts it back).
    ``step`` is the change per pixel for fields without a soft range."""

    def __init__(self, text: str, spin: QAbstractSpinBox, step: float, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._spin = spin
        self._step = step
        self._scrub: Any = None
        self._press_x: float | None = None  # plain spin boxes (not a NumberField)
        self._start = 0.0
        self._dragged = False
        if isinstance(spin, NumberField) and spin.scrub_step is None:
            spin.scrub_step = step
        self.setObjectName("scrubLabel")
        self.setCursor(Qt.SizeHorCursor)
        if len(text) <= 2:  # single-letter labels (X, Y, W, H) get a wider grab area
            self.setMinimumWidth(14)
            self.setAlignment(Qt.AlignCenter)
        self.setToolTip("Drag left/right to change · Shift faster · Alt finer · Ctrl snaps · Esc cancels")

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton and self._spin.isEnabled():
            if isinstance(self._spin, NumberField):
                self._scrub = self._spin.start_scrub(event.globalPosition())
            else:
                self._press_x = event.globalPosition().x()
                self._start = float(self._spin.value())
                self._dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        if self._scrub is not None:
            self._scrub.move(event.globalPosition(), event.modifiers())
            event.accept()
            return
        if self._press_x is None:
            return super().mouseMoveEvent(event)
        dx = event.globalPosition().x() - self._press_x
        if abs(dx) >= 2:
            self._dragged = True
        if not self._dragged:
            event.accept()  # a click with a pixel of jitter must not change the value
            return
        self._spin.setValue(self._start + dx * self._step * drag_factor(event.modifiers()))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton and self._scrub is not None:
            scrub, self._scrub = self._scrub, None
            scrub.release()
            event.accept()
            return
        if self._press_x is not None and event.button() == Qt.LeftButton:
            self._press_x = None
            if self._dragged:
                self._spin.editingFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class AttrLabel(QWidget):
    """An attribute's name with its symbol (see core.attr_symbols) in a fixed column, so names line up.
    A number field's name stays a ScrubLabel (drag to change)."""

    def __init__(self, spec: Any, editor: QWidget, symbol: str, reserve: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        if symbol or reserve:
            glyph = QLabel(symbol, self)
            glyph.setObjectName("attrSymbol")
            glyph.setFixedWidth(16)
            glyph.setAlignment(Qt.AlignCenter)
            glyph.setStyleSheet("color: #8a93a3;")
            layout.addWidget(glyph)
        if isinstance(editor, NumberField):
            text: QLabel = ScrubLabel(spec.nice_name, editor, editor.per_pixel(), self)
        else:
            text = QLabel(spec.nice_name, self)
        tip = spec.doc or spec.name
        if spec.unit:
            tip += f" ({spec.unit})"
        self.setToolTip(tip)
        text.setToolTip(tip)
        layout.addWidget(text, 1)
        self.text = text


def attr_label(session: Any, spec: Any, editor: QWidget, node_type: str = "", reserve: bool = False,
               parent: QWidget | None = None) -> AttrLabel:
    """The label for ``spec``'s row. ``reserve``: leave room for a symbol even if it has none."""
    from pixelref.core.attr_symbols import EXTENSION_POINT, symbol_for

    tables = list(reversed(session.extensions.contributions(EXTENSION_POINT))) if session is not None else []
    return AttrLabel(spec, editor, symbol_for(spec, node_type, tables), reserve, parent)


def attr_symbol(session: Any, spec: Any, node_type: str = "") -> str:
    from pixelref.core.attr_symbols import EXTENSION_POINT, symbol_for

    return symbol_for(spec, node_type, list(reversed(session.extensions.contributions(EXTENSION_POINT))))


def _chevron(expanded: bool) -> QIcon:
    return icon("chevron_down" if expanded else "chevron_right", "#8a93a3")


class InspectorSection(QWidget):
    """Collapsible group: a clickable title over a form."""

    toggled = Signal(str, bool)

    def __init__(self, key: str, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        # "&" would be read as a keyboard mnemonic by QPushButton.
        self._title = title.upper().replace("&", "&&")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 4)
        layout.setSpacing(4)
        self.header = QPushButton(self)
        self.header.setObjectName("sectionToggle")
        self.header.setCheckable(True)
        self.header.setChecked(True)
        self.header.setFocusPolicy(Qt.NoFocus)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setIconSize(QSize(12, 12))
        self.header.toggled.connect(self._on_toggled)
        layout.addWidget(self.header)
        self.body = QWidget(self)
        self.form = QFormLayout(self.body)
        self.form.setContentsMargins(4, 0, 2, 0)
        self.form.setHorizontalSpacing(8)
        self.form.setVerticalSpacing(6)
        self.form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        layout.addWidget(self.body)
        self._refresh_title()

    def _refresh_title(self) -> None:
        self.header.setText(" " + self._title)
        self.header.setIcon(_chevron(self.header.isChecked()))

    def _on_toggled(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self._refresh_title()
        self.toggled.emit(self.key, expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.header.setChecked(expanded)

    @property
    def expanded(self) -> bool:
        return self.header.isChecked()


class TextField(QPlainTextEdit):
    """Multi-line text box with a QLineEdit-like API."""

    editingFinished = Signal()

    def focusOutEvent(self, event) -> None:  # noqa: ANN001
        super().focusOutEvent(event)
        self.editingFinished.emit()

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, text: str) -> None:  # noqa: N802
        if text != self.toPlainText():
            self.setPlainText(text)


# ---- Shortcut capture (the shortcut editor, the tool palette…) --------------------------------


def action_label(app: Any, target: str) -> str:
    """An action's display text (its key when it isn't an action)."""
    action = app.actions.get(target)
    return action.text().replace("&&", "&").replace("…", "") if action is not None else target


class KeyCaptureDialog(QDialog):
    """Press a key combination; shows what already uses it."""

    def __init__(self, app: Any, label: str, target: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.target = target
        self.sequence = ""
        self.setWindowTitle("Set Shortcut")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Press the new shortcut for <b>{label}</b>", self))
        self.edit = QKeySequenceEdit(self)
        self.edit.setMaximumSequenceLength(1)
        self.edit.keySequenceChanged.connect(self._changed)
        layout.addWidget(self.edit)
        self.info = QLabel(" ", self)
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.ok = buttons.button(QDialogButtonBox.Ok)
        self.ok.setEnabled(False)
        layout.addWidget(buttons)
        self.edit.setFocus()

    def _changed(self, sequence: QKeySequence) -> None:
        from .actions import normalize_sequence

        self.sequence = normalize_sequence(sequence.toString()) if not sequence.isEmpty() else ""
        self.ok.setEnabled(bool(self.sequence))
        if not self.sequence:
            self.info.setText(" ")
            return
        others = [t for t in self.app.keymap.targets_of(self.sequence) if t != self.target]
        if others:
            names = ", ".join(action_label(self.app, t) for t in others)
            self.info.setText(f"⚠ Already used by {names}: OK asks before taking it over.")
        else:
            self.info.setText("Free.")


def capture_shortcut(app: Any, target: str, parent: QWidget | None = None, add: bool = False,
                     label: str = "") -> bool:
    """Ask for a key for ``target`` and keep it in keymap.toml (asking before taking it over
    from another action). ``add`` keeps the keys it has. True when the keys changed."""
    label = label or action_label(app, target)
    dialog = KeyCaptureDialog(app, label, target, parent)
    if dialog.exec() != QDialog.Accepted or not dialog.sequence:
        return False
    key = dialog.sequence
    others = [t for t in app.keymap.targets_of(key) if t != target]
    if others:
        names = ", ".join(action_label(app, t) for t in others)
        native = QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText)
        answer = QMessageBox.question(parent, "Set Shortcut", f"{native} is used by {names}.\nUse it for {label} instead?")
        if answer != QMessageBox.Yes:
            return False
    return run_keymap_edit(lambda: app.keymap.set_shortcut(target, key, add=add), parent)


def run_keymap_edit(edit: Any, parent: QWidget | None = None) -> bool:
    """Run a keymap.toml edit, reporting a broken file or problems; True when it was written."""
    try:
        problems = edit()
    except ValueError as exc:
        QMessageBox.warning(parent, "Keyboard Shortcuts", f"keymap.toml can't be read, so it wasn't changed:\n{exc}")
        return False
    if problems:
        QMessageBox.warning(parent, "Keyboard Shortcuts", "\n".join(problems))
    return True
