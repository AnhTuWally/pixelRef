"""The text note item: rich text (per-character runs) inside a styleable box."""

from __future__ import annotations

import weakref
from typing import Any, Callable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QKeySequence, QPainter, QPen, QTextCharFormat, QTextCursor, QTextFormat, QTextOption,
)
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsTextItem

from pixelref.plugin_api.ui import NodeItemMixin, paint, unselected_option
from pixelref.plugin_api.ui import widgets as _widgets

color_from = _widgets.color_from

from . import TEXT_STYLE_KEYS

_ALIGNMENTS = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight, "justify": Qt.AlignJustify}

# Notes being edited (normally none or one), so finding the one being edited doesn't scan
# every canvas item before every command.
EDITING: "weakref.WeakSet[TextItem]" = weakref.WeakSet()


def _rgb_list(color: QColor) -> list[int]:
    return [color.red(), color.green(), color.blue()]


def _rgba_list(color: QColor) -> list[int]:
    return [color.red(), color.green(), color.blue(), color.alpha()]


def same_style_value(key: str, a: Any, b: Any) -> bool:
    """Compare style values; fully transparent highlights are all equal."""
    if key == "highlight_color":
        a_off = not a or len(a) < 4 or int(a[3]) == 0
        b_off = not b or len(b) < 4 or int(b[3]) == 0
        if a_off or b_off:
            return a_off and b_off
    if key == "font_size":
        return abs(float(a) - float(b)) < 1e-6
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return [int(v) for v in a] == [int(v) for v in b]
    return a == b


class TextItem(NodeItemMixin, QGraphicsTextItem):
    FORMAT_SHORTCUTS = {
        (Qt.Key_B, False): "bold",
        (Qt.Key_I, False): "italic",
        (Qt.Key_U, False): "underline",
        (Qt.Key_X, True): "strike",
        (Qt.Key_Greater, True): "grow",
        (Qt.Key_Period, True): "grow",
        (Qt.Key_Less, True): "shrink",
        (Qt.Key_Comma, True): "shrink",
    }

    def __init__(self, node_name: str) -> None:
        super().__init__("")
        self._init_node(node_name, "annotation_text", None)
        self.on_text_committed: Callable[[str, str, Any], None] | None = None
        self.on_format_changed: Callable[["TextItem"], None] | None = None
        self._style: dict[str, Any] = {
            "bold": False, "italic": False, "underline": False, "strike": False, "font_size": 14.0,
            "color": [220, 220, 220], "highlight_color": [255, 235, 59, 0],
        }
        self._bg_color = QColor(0, 0, 0, 0)
        self._fill_style = "solid"
        self._border_color = QColor(220, 220, 220, 255)
        self._border_width = 0.0
        self._border_style = "solid"
        self._corner_radius = 0.0
        self._text_width = 0.0
        self._editing = False
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setTextInteractionFlags(Qt.NoTextInteraction)

    # ---- Styling from node attributes ---------------------------------------------------

    def apply_attrs(self, attrs: dict[str, Any]) -> None:
        self.prepareGeometryChange()
        self._style = {
            "bold": bool(attrs.get("bold", False)),
            "italic": bool(attrs.get("italic", False)),
            "underline": bool(attrs.get("underline", False)),
            "strike": bool(attrs.get("strike", False)),
            "font_size": max(1.0, float(attrs.get("font_size", 14))),
            "color": list(attrs.get("color", [220, 220, 220]))[:3],
            "highlight_color": list(attrs.get("highlight_color", [255, 235, 59, 0])),
        }
        font = QFont()
        family = str(attrs.get("font_family", "") or "")
        if family:
            font.setFamily(family)
        font.setPointSizeF(self._style["font_size"])
        font.setBold(self._style["bold"])
        font.setItalic(self._style["italic"])
        font.setUnderline(self._style["underline"])
        font.setStrikeOut(self._style["strike"])
        self.setFont(font)
        self.setDefaultTextColor(color_from(self._style["color"]))
        self._bg_color = color_from(attrs.get("background_color", [0, 0, 0, 0]), (0, 0, 0, 0))
        self._fill_style = str(attrs.get("fill_style", "solid"))
        self._border_color = color_from(attrs.get("border_color", [220, 220, 220, 255]))
        self._border_width = max(0.0, float(attrs.get("border_width", 0.0)))
        self._border_style = str(attrs.get("border_style", "solid"))
        self._corner_radius = max(0.0, float(attrs.get("corner_radius", 0.0)))
        document = self.document()
        document.setDocumentMargin(max(0.0, float(attrs.get("padding", 4.0))))
        self.set_content(str(attrs.get("text", "")), attrs.get("runs"))
        option = document.defaultTextOption()
        option.setAlignment(_ALIGNMENTS.get(str(attrs.get("align", "left")), Qt.AlignLeft))
        # Break long words too, so a fixed-width box never overflows.
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(option)
        self.set_box_width(float(attrs.get("text_width", 0.0) or 0.0))
        self.update()

    # ---- Box width ------------------------------------------------------------------------

    def min_box_width(self) -> float:
        return 2.0 * self.document().documentMargin() + 12.0

    def box_width(self) -> float:
        """Fixed box width in local units, or 0.0 when the box grows with its text."""
        return self._text_width

    def set_box_width(self, width: float) -> None:
        """Fix the box width (text reflows inside it); 0 returns to auto width."""
        width = max(self.min_box_width(), float(width)) if width and width > 0 else 0.0
        self._text_width = width
        self.setTextWidth(width if width > 0 else -1)
        self.update()

    def style_value(self, key: str) -> Any:
        return self._style[key]

    def commit_values(self) -> dict[str, Any]:
        values = self.transform_attrs()
        values["text_width"] = round(self.box_width(), 2)
        return values

    @property
    def blocks_transform(self) -> bool:
        return self._editing

    def _char_format(self, overrides: dict[str, Any]) -> QTextCharFormat:
        fmt = QTextCharFormat()
        highlight = color_from(overrides.get("highlight_color", self._style["highlight_color"]), (0, 0, 0, 0))
        if highlight.alpha() > 0:
            fmt.setBackground(highlight)
        if "bold" in overrides:
            fmt.setFontWeight(QFont.Bold if overrides["bold"] else QFont.Normal)
        if "italic" in overrides:
            fmt.setFontItalic(bool(overrides["italic"]))
        if "underline" in overrides:
            fmt.setFontUnderline(bool(overrides["underline"]))
        if "strike" in overrides:
            fmt.setFontStrikeOut(bool(overrides["strike"]))
        if "font_size" in overrides:
            fmt.setFontPointSize(max(1.0, float(overrides["font_size"])))
        if "color" in overrides:
            fmt.setForeground(color_from(overrides["color"]))
        return fmt

    def set_content(self, text: str, runs: Any = None) -> None:
        document = self.document()
        # Loading isn't an edit: Ctrl+Z while editing must not undo it (emptying the note).
        document.setUndoRedoEnabled(False)
        document.clear()
        cursor = QTextCursor(document)
        if runs:
            for run in runs:
                overrides = {k: v for k, v in run.items() if k in TEXT_STYLE_KEYS}
                cursor.insertText(str(run.get("text", "")), self._char_format(overrides))
        else:
            cursor.insertText(text, self._char_format({}))
        document.setUndoRedoEnabled(True)

    def _overrides_from(self, fmt: QTextCharFormat) -> dict[str, Any]:
        """Style properties of ``fmt`` that differ from the note defaults."""
        found: dict[str, Any] = {}
        if fmt.hasProperty(QTextFormat.FontWeight):
            found["bold"] = fmt.fontWeight() >= QFont.DemiBold
        if fmt.hasProperty(QTextFormat.FontItalic):
            found["italic"] = fmt.fontItalic()
        if fmt.hasProperty(QTextFormat.TextUnderlineStyle) or fmt.hasProperty(QTextFormat.FontUnderline):
            found["underline"] = fmt.fontUnderline()
        if fmt.hasProperty(QTextFormat.FontStrikeOut):
            found["strike"] = fmt.fontStrikeOut()
        if fmt.hasProperty(QTextFormat.FontPointSize) and fmt.fontPointSize() > 0:
            found["font_size"] = round(fmt.fontPointSize(), 2)
        if fmt.hasProperty(QTextFormat.ForegroundBrush) and fmt.foreground().style() != Qt.NoBrush:
            found["color"] = _rgb_list(fmt.foreground().color())
        brush = fmt.background()
        if fmt.hasProperty(QTextFormat.BackgroundBrush) and brush.style() != Qt.NoBrush:
            found["highlight_color"] = _rgba_list(brush.color())
        else:
            found["highlight_color"] = [0, 0, 0, 0]
        return {k: v for k, v in found.items() if not same_style_value(k, v, self._style[k])}

    def extract_content(self) -> tuple[str, list[dict[str, Any]] | None]:
        """Plain text plus formatting runs (None when the text is uniform)."""
        runs: list[dict[str, Any]] = []

        def append(text: str, overrides: dict[str, Any]) -> None:
            if runs and {k: v for k, v in runs[-1].items() if k != "text"} == overrides:
                runs[-1]["text"] += text
            else:
                runs.append({"text": text, **overrides})

        block = self.document().begin()
        first = True
        while block.isValid():
            if not first:
                if runs:
                    runs[-1]["text"] += "\n"
                else:
                    append("\n", {})
            first = False
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    append(fragment.text(), self._overrides_from(fragment.charFormat()))
                iterator += 1
            block = block.next()
        text = self.document().toPlainText()
        if all(len(run) == 1 for run in runs):
            return text, None
        return text, runs

    # ---- Formatting while editing ---------------------------------------------------------

    def has_selection(self) -> bool:
        return self._editing and self.textCursor().hasSelection()

    def format_state(self, key: str) -> Any:
        """Current value of a style key at the cursor / selection."""
        overrides = self._overrides_from(self.textCursor().charFormat())
        return overrides.get(key, self._style[key])

    def merge_format(self, key: str, value: Any) -> None:
        """Apply a style to the selection (or the whole text if nothing is selected)."""
        fmt = QTextCharFormat()
        if key == "bold":
            fmt.setFontWeight(QFont.Bold if value else QFont.Normal)
        elif key == "italic":
            fmt.setFontItalic(bool(value))
        elif key == "underline":
            fmt.setFontUnderline(bool(value))
        elif key == "strike":
            fmt.setFontStrikeOut(bool(value))
        elif key == "font_size":
            fmt.setFontPointSize(max(1.0, float(value)))
        elif key == "color":
            fmt.setForeground(color_from(value))
        elif key == "highlight_color":
            color = color_from(value, (0, 0, 0, 0))
            fmt.setBackground(color if color.alpha() > 0 else QBrush(Qt.NoBrush))
        else:
            return
        cursor = self.textCursor()
        if not cursor.hasSelection():
            cursor = QTextCursor(self.document())
            cursor.select(QTextCursor.SelectionType.Document)
        cursor.mergeCharFormat(fmt)
        self._format_changed()

    def clear_formatting(self) -> None:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            cursor = QTextCursor(self.document())
            cursor.select(QTextCursor.SelectionType.Document)
        plain = self._char_format({})
        if not plain.hasProperty(QTextFormat.BackgroundBrush):
            plain.setBackground(QBrush(Qt.NoBrush))
        cursor.setCharFormat(plain)
        self._format_changed()

    def step_font_size(self, direction: int) -> float:
        current = float(self.format_state("font_size"))
        new_size = max(4.0, round(current * (1.15 if direction > 0 else 1 / 1.15)))
        if new_size == current:
            new_size = max(4.0, current + direction)
        self.merge_format("font_size", new_size)
        return new_size

    def _format_changed(self) -> None:
        self.update()
        if self.on_format_changed is not None:
            self.on_format_changed(self)

    # ---- Editing lifecycle ------------------------------------------------------------------

    @property
    def editing(self) -> bool:
        return self._editing

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged and not self._editing:
            self._notify_moved()
        return super().itemChange(change, value)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton and not self._locked and not self._editing:
            self.start_edit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: ANN001
        # Keep editing while a colour dialog or popup is open; those return focus here.
        if self._editing and event.reason() not in (Qt.ActiveWindowFocusReason, Qt.PopupFocusReason):
            self._commit()
        super().focusOutEvent(event)

    def _format_shortcut(self, event) -> str | None:  # noqa: ANN001
        modifiers = event.modifiers()
        if not modifiers & Qt.ControlModifier:
            return None
        return self.FORMAT_SHORTCUTS.get((event.key(), bool(modifiers & Qt.ShiftModifier)))

    def sceneEvent(self, event) -> bool:  # noqa: ANN001
        # Claim formatting shortcuts (Ctrl+B/I/U…) before window shortcuts see them.
        if self._editing and event.type() == QEvent.ShortcutOverride and (
            self._format_shortcut(event) or event.key() == Qt.Key_Escape
        ):
            event.accept()
            return True
        return super().sceneEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        if self._editing:
            command = self._format_shortcut(event)
            if command in ("grow", "shrink"):
                self.step_font_size(1 if command == "grow" else -1)
                event.accept()
                return
            if command is not None:
                self.merge_format(command, not self.format_state(command))
                event.accept()
                return
            if event.matches(QKeySequence.StandardKey.Paste):
                # Paste as plain text in the note's style: rich text from browsers brings
                # foreign fonts and backgrounds, and images that end up as U+FFFC.
                mime = QApplication.clipboard().mimeData()
                text = mime.text() if mime is not None and mime.hasText() else ""
                if text:
                    self.textCursor().insertText(text.replace("\r\n", "\n").replace("￼", ""))
                event.accept()
                return
            if event.key() == Qt.Key_Escape or (
                event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier
            ):
                self._commit()
                event.accept()
                return
        super().keyPressEvent(event)

    def start_edit(self) -> None:
        self._editing = True
        EDITING.add(self)
        self.setFlag(QGraphicsItem.ItemIsMovable, False)
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFocus(Qt.MouseFocusReason)
        cursor = self.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        self.setTextCursor(cursor)
        self._format_changed()

    def commit_edit(self) -> None:
        self._commit()

    def _commit(self) -> None:
        if not self._editing:
            return
        self._editing = False
        EDITING.discard(self)
        cursor = self.textCursor()
        cursor.clearSelection()
        self.setTextCursor(cursor)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setFlag(QGraphicsItem.ItemIsMovable, not self._locked)
        self.clearFocus()
        self.update()
        text, runs = self.extract_content()
        if self.on_format_changed is not None:
            self.on_format_changed(self)
        if self.on_text_committed is not None:
            self.on_text_committed(self.node_name, text, runs)

    # ---- Painting ----------------------------------------------------------------------------

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        rect = self.boundingRect()
        brush_style = paint.BRUSH_STYLES.get(self._fill_style, Qt.SolidPattern)
        has_fill = self._bg_color.alpha() > 0 and brush_style != Qt.NoBrush
        pen_style = paint.PEN_STYLES.get(self._border_style, Qt.SolidLine)
        has_border = self._border_width > 0 and self._border_color.alpha() > 0 and pen_style != Qt.NoPen
        if has_fill or has_border:
            inset = self._border_width / 2.0 if has_border else 0.0
            box = rect.adjusted(inset, inset, -inset, -inset)
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setBrush(QBrush(self._bg_color, brush_style) if has_fill else Qt.NoBrush)
            if has_border:
                pen = QPen(self._border_color, self._border_width, pen_style)
                pen.setJoinStyle(Qt.RoundJoin)
                painter.setPen(pen)
            else:
                painter.setPen(Qt.NoPen)
            radius = min(self._corner_radius, box.width() / 2.0, box.height() / 2.0)
            painter.drawRoundedRect(box, radius, radius)
            painter.restore()
        # Not super(): the mixin's paint() is next in line and would come back here.
        QGraphicsTextItem.paint(self, painter, unselected_option(option), widget)

    def paint_decorations(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        if self._editing:
            pen = QPen(QColor("#5a9acc"), 1.5)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect())
