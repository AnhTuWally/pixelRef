"""Qt side of the code plugin: the block item, edit dialog, copy badge, inspector section and actions."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QPainter, QTextCharFormat, QTextCursor,
                           QTextDocument, QTextOption, QTransform)
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QGraphicsItem, QLabel, QPlainTextEdit,
                               QVBoxLayout)

from pixelref.plugin_api import CommandError
from pixelref.plugin_api.contracts import InspectorSection, PaletteButton, SelectionInfo
from pixelref.plugin_api.ui import HANDLED, InputHandler, NodeItemMixin, PointerEvent, icon, theme
from pixelref.plugin_api.ui import widgets as w

from . import DEFAULT_WIDTH, HELLO_WORLD, LANGUAGE_CHOICES, LANGUAGES, THEMES, code_lines, tokenize

TYPE = "annotation_code"

# theme -> colours (r, g, b); "bg" and "fg" are the block and its plain text.
PALETTES: dict[str, dict[str, tuple[int, int, int]]] = {
    "dark": {"bg": (30, 34, 40), "fg": (212, 216, 222), "gutter": (96, 106, 120), "comment": (110, 120, 134),
             "string": (152, 195, 121), "number": (209, 154, 102), "keyword": (198, 120, 221),
             "tag": (224, 108, 117), "function": (97, 175, 239)},
    "light": {"bg": (250, 250, 250), "fg": (56, 58, 66), "gutter": (160, 161, 167), "comment": (150, 152, 160),
              "string": (80, 161, 79), "number": (152, 104, 1), "keyword": (166, 38, 164),
              "tag": (228, 86, 73), "function": (64, 120, 242)},
}


def mono_font(size: float) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    font.setStyleHint(QFont.Monospace)
    font.setPointSizeF(max(1.0, size))
    return font


class CodeItem(NodeItemMixin, QGraphicsItem):
    """A code block: rounded box, optional line-number gutter and (highlighted, wrapped) monospace text."""

    MIN_WIDTH = 60.0

    def __init__(self, node_name: str) -> None:
        super().__init__()
        self._init_node(node_name, TYPE, None)
        self._doc = QTextDocument()
        self._doc.setDocumentMargin(0.0)
        self._attrs: dict[str, Any] = {}
        self._wrap = True
        self._width = DEFAULT_WIDTH
        self._padding = 12.0
        self._radius = 6.0
        self._numbers = False
        self._gutter = 0.0
        self._palette = PALETTES["dark"]
        self._size = QPointF(DEFAULT_WIDTH, 40.0)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    # ---- Attributes -----------------------------------------------------------------------------

    def apply_attrs(self, attrs: dict[str, Any]) -> None:
        self._attrs = attrs
        self._wrap = bool(attrs.get("wrap", True))
        self._width = max(self.MIN_WIDTH, float(attrs.get("width", DEFAULT_WIDTH) or DEFAULT_WIDTH))
        self._padding = max(0.0, float(attrs.get("padding", 12.0)))
        self._radius = max(0.0, float(attrs.get("corner_radius", 6.0)))
        self._numbers = bool(attrs.get("line_numbers", False))
        self._palette = PALETTES.get(str(attrs.get("theme", "dark")), PALETTES["dark"])
        self._relayout()
        self.update()

    def _relayout(self) -> None:
        attrs = self._attrs
        font = mono_font(float(attrs.get("font_size", 13.0)))
        code = "\n".join(code_lines(attrs))
        palette = self._palette
        doc = self._doc
        doc.setDefaultFont(font)
        doc.setPlainText(code)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere if self._wrap else QTextOption.NoWrap)
        doc.setDefaultTextOption(option)
        metrics = QFontMetricsF(font)
        doc.setDefaultStyleSheet("")
        doc.setIndentWidth(metrics.horizontalAdvance(" ") * 4)
        # Tab stops: four columns, like most editors.
        option.setTabStopDistance(metrics.horizontalAdvance(" ") * 4)
        doc.setDefaultTextOption(option)

        base = QTextCharFormat()
        base.setForeground(QColor(*palette["fg"]))
        cursor = QTextCursor(doc)
        cursor.select(QTextCursor.Document)
        cursor.setCharFormat(base)
        if attrs.get("highlight", True):
            for start, end, kind in tokenize(code, str(attrs.get("language", "python"))):
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(*palette.get(kind, palette["fg"])))
                if kind == "comment":
                    fmt.setFontItalic(True)
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.KeepAnchor)
                cursor.mergeCharFormat(fmt)

        lines = doc.blockCount()
        self._gutter = (metrics.horizontalAdvance("0") * (len(str(lines)) + 1.5)) if self._numbers else 0.0
        pad = self._padding
        if self._wrap:
            doc.setTextWidth(max(1.0, self._width - 2 * pad - self._gutter))
            width = self._width
        else:
            doc.setTextWidth(-1)
            width = max(self.MIN_WIDTH, doc.idealWidth() + 2 * pad + self._gutter)
        self.prepareGeometryChange()
        self._size = QPointF(width, doc.size().height() + 2 * pad)

    # ---- Box width (word wrap) --------------------------------------------------------------------

    @property
    def wraps(self) -> bool:
        return self._wrap

    def min_box_width(self) -> float:
        return self.MIN_WIDTH

    def box_width(self) -> float:
        return self._size.x()

    def set_box_width(self, width: float) -> None:
        self._width = max(self.MIN_WIDTH, float(width))
        self._relayout()
        self.update()

    def commit_values(self) -> dict[str, Any]:
        values = self.transform_attrs()
        if self._wrap:
            values["width"] = round(self._width, 2)
        return values

    # ---- Geometry & painting ----------------------------------------------------------------------

    def content_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, self._size.x(), self._size.y())

    def boundingRect(self) -> QRectF:
        return self.content_rect()

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        return super().itemChange(change, value)

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        palette = self._palette
        rect = self.content_rect()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(*palette["bg"]))
        radius = min(self._radius, rect.width() / 2.0, rect.height() / 2.0)
        painter.drawRoundedRect(rect, radius, radius)
        painter.restore()

        pad = self._padding
        painter.save()
        painter.translate(pad + self._gutter, pad)
        self._doc.drawContents(painter)
        painter.restore()

        if self._numbers:
            painter.save()
            painter.setFont(self._doc.defaultFont())
            painter.setPen(QColor(*palette["gutter"]))
            layout = self._doc.documentLayout()
            block = self._doc.begin()
            number = 1
            right = pad + self._gutter - QFontMetricsF(self._doc.defaultFont()).horizontalAdvance("0")
            while block.isValid():
                top = layout.blockBoundingRect(block).top()
                height = QFontMetricsF(self._doc.defaultFont()).lineSpacing()
                painter.drawText(QRectF(0.0, pad + top, right, height), Qt.AlignRight | Qt.AlignVCenter, str(number))
                number += 1
                block = block.next()
            painter.restore()


class CodeHandles:
    """Gizmo provider: with word wrap on, the side handles set the block width."""

    node_type = TYPE
    labels = {"code_width": "Resize code block"}

    def build(self, gizmo: Any, view: Any, item: Any, g: dict[str, Any]) -> None:
        vp = g["corners_vp"]
        g["handles_vp"].pop("tm", None)
        g["handles_vp"].pop("bm", None)
        if item.wraps:
            g["handles_vp"]["mr"] = (vp[1] + vp[2]) / 2.0
            g["handles_vp"]["ml"] = (vp[3] + vp[0]) / 2.0
        else:
            g["handles_vp"].pop("mr", None)
            g["handles_vp"].pop("ml", None)
        return None

    def start(self, gizmo: Any, handle: str, g: dict[str, Any], item: Any) -> str | None:
        return "code_width" if handle in ("ml", "mr") and item.wraps else None

    def drag(self, gizmo: Any, op: dict[str, Any], m: QPointF, modifiers: Any, view: Any) -> None:
        item, s0, l0, _parent_inv, _rotation = op["targets"][0]
        start = op["start_rect"]
        m_local = s0.inverted()[0].map(m)
        width = m_local.x() - start.left() if op["handle"] == "mr" else start.right() - m_local.x()
        width = max(item.min_box_width(), width)
        item.set_box_width(width)
        if op["handle"] == "ml":
            shift = start.right() - width - start.left()
            item.set_local_transform(QTransform.fromTranslate(shift, 0.0) * l0)
        item._notify_moved()
        gizmo.ui.status(f"Code width {width:.0f}px")


class CodeDialog(QDialog):
    """Edit a block's code in a monospace editor (Ctrl+Enter saves)."""

    def __init__(self, parent: Any, code: str, language: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit code")
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit(self)
        self.editor.setFont(mono_font(11))
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.setTabStopDistance(QFontMetricsF(self.editor.font()).horizontalAdvance(" ") * 4)
        self.editor.setPlainText(code)
        hint = QLabel(f"{LANGUAGES.get(language, ('Plain text',))[0]} · Ctrl+Enter to save", self)
        hint.setObjectName("pathLabel")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(self.editor)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            self.accept()
            return
        super().keyPressEvent(event)


class CodeSection(w.InspectorSection):
    def __init__(self, ui: Any, parent: Any) -> None:
        super().__init__("code", "Code", parent)
        self.ui = ui
        self._updating = False
        self._names: list[str] = []
        body = self.body
        from PySide6.QtWidgets import QCheckBox
        self.language = w.make_combo(tuple((key, LANGUAGES[key][0]) for key in LANGUAGE_CHOICES), body)
        self.theme = w.make_combo(tuple((key, key.capitalize()) for key in THEMES), body)
        self.highlight = QCheckBox("Syntax highlight", body)
        self.wrap = QCheckBox("Word wrap", body)
        self.numbers = QCheckBox("Line numbers", body)
        self.size = w.make_spin(body, minimum=4.0, maximum=200.0, step=1.0, default=13.0, soft_range=(8.0, 32.0))
        self.width = w.make_spin(body, minimum=60.0, maximum=20000.0, step=10.0, default=DEFAULT_WIDTH,
                                 soft_range=(120.0, 1200.0))
        self.edit_btn = w.make_tool_button("Edit…", "Edit the code (double-click the block)", width=56, parent=body)
        self.copy_btn = w.make_tool_button("Copy", "Copy the code to the clipboard", width=56, parent=body)
        form = self.form
        form.addRow("Language", self.language)
        form.addRow("Theme", self.theme)
        form.addRow("Size", self.size)
        form.addRow("Width", self.width)
        form.addRow(self.highlight)
        form.addRow(self.wrap)
        form.addRow(self.numbers)
        form.addRow(w.hrow(body, self.edit_btn, self.copy_btn))
        self.language.currentIndexChanged.connect(lambda _i: self._set({"language": self.language.currentData()}))
        self.theme.currentIndexChanged.connect(lambda _i: self._set({"theme": self.theme.currentData()}))
        self.highlight.toggled.connect(lambda on: self._set({"highlight": on}))
        self.wrap.toggled.connect(lambda on: self._set({"wrap": on}))
        self.numbers.toggled.connect(lambda on: self._set({"line_numbers": on}))
        self.size.editingFinished.connect(lambda: self._set({"font_size": round(self.size.value(), 1)}))
        self.width.editingFinished.connect(lambda: self._set({"width": round(self.width.value(), 1)}))
        self.edit_btn.clicked.connect(lambda: ui.app.run_command("code.edit"))
        self.copy_btn.clicked.connect(lambda: ui.app.run_command("code.copy"))

    def _set(self, updates: dict[str, Any]) -> None:
        if self._updating or not self._names:
            return
        self.ui.app.run_command("code.set_attrs", updates, self._names, "Code block style")

    def refresh(self, info: SelectionInfo) -> None:
        self._names = [n["name"] for n in info.nodes if n["type"] == TYPE]
        attrs = info.nodes[0]["attrs"] if info.nodes else {}
        self._updating = True
        try:
            w.set_combo(self.language, attrs.get("language", "python"))
            w.set_combo(self.theme, attrs.get("theme", "dark"))
            self.highlight.setChecked(bool(attrs.get("highlight", True)))
            self.wrap.setChecked(bool(attrs.get("wrap", True)))
            self.numbers.setChecked(bool(attrs.get("line_numbers", False)))
            self.size.setValue(float(attrs.get("font_size", 13.0)))
            self.width.setValue(float(attrs.get("width", DEFAULT_WIDTH)))
            self.width.setEnabled(bool(attrs.get("wrap", True)))
        finally:
            self._updating = False


def activate_code_ui(ui: Any) -> None:
    app = ui.app
    session = ui.session
    canvas = ui.canvas

    def selected_blocks() -> list[str]:
        scene = session.scene
        return [n for n in session.selection.names if scene.has(n) and scene.get_node(n).node_type == TYPE]

    def factory(_canvas: Any, node: dict[str, Any]) -> CodeItem:
        item = CodeItem(node["name"])
        item.apply_attrs(node.get("attrs", {}))
        return item

    # ---- Edit ------------------------------------------------------------------------------------

    def edit_code(name: str | None = None) -> bool:
        names = [name] if name else selected_blocks()
        if not names:
            raise CommandError("Select a code block to edit")
        attrs = session.scene.get_node(names[0]).attrs
        dialog = CodeDialog(app.main_window(), str(attrs.get("code", "")), str(attrs.get("language", "python")))
        if dialog.exec() != QDialog.Accepted:
            return False
        app.run_command("code.set_code", names[0], dialog.editor.toPlainText())
        return True

    def double_click(_view: Any, item: Any) -> bool:
        if not isinstance(item, CodeItem) or item.locked:
            return False
        edit_code(item.node_name)
        return True

    ui.node_ui(TYPE, factory=factory, label="Code block", icon="text", badge=("#7aa2d6", "</>"),
               describe=lambda _name, node: [str(LANGUAGES.get(node.get("attrs", {}).get("language", "python"),
                                                                ("Plain text",))[0])],
               double_click=double_click)
    ui.contribute("gizmo.handles", CodeHandles())
    ui.contribute("inspector.sections", InspectorSection(
        "code", "Code", 31, lambda info: bool(info.nodes) and all(n["type"] == TYPE for n in info.nodes),
        lambda parent, _i: CodeSection(ui, parent)))

    # ---- Copy ------------------------------------------------------------------------------------

    def copy_code(names: list[str] | None = None) -> str:
        """Copy the selected code block's text to the clipboard (``names``: the block the copy badge is on)."""
        text = session.run("code.get_code", names or selected_blocks())
        QApplication.clipboard().setText(text)
        ui.status(f"Copied {len(text)} character(s) of code")
        ui.toast("Code copied")
        return text

    ui.command("edit", edit_code, undoable=False, echo=False)
    ui.command("copy", copy_code, undoable=False, echo=False)

    # ---- Add ---------------------------------------------------------------------------------------

    def add_code(from_clipboard: bool = False) -> str | None:
        """Add a block at the mouse with a Python hello world, or with the clipboard text when asked."""
        clip = QApplication.clipboard().text().strip("\n") if from_clipboard else ""
        if from_clipboard and not clip:
            ui.status("The clipboard has no text")
            return None
        position = ui.app.scene_pos_for_new_content()
        name = app.run_command("code.create", clip or HELLO_WORLD, [position.x(), position.y()])
        if name is None:
            return None
        canvas.refresh(force=True)
        return name

    ui.command("add", add_code, undoable=False, echo=False)
    ui.command("add_from_clipboard", lambda: add_code(True), undoable=False, echo=False)
    a = ui.action
    a("code.add", "code.add", "Add Code Block", keywords="source snippet program syntax hello world",
      category="Create", icon="code")
    a("code.add_clipboard", "code.add_from_clipboard", "Add Code Block from Clipboard",
      keywords="source snippet paste text", category="Create", icon="paste")
    a("code.edit", "code.edit", "Edit Code…", keywords="source snippet", category="Code", icon="pen")
    a("code.copy", "code.copy", "Copy Code", keywords="clipboard source snippet", category="Code", icon="copy")
    ui.context_key("code.selected", lambda: bool(selected_blocks()))
    ui.menu("canvas", "code.add", group="create", order=21, when="selection.count == 0")
    ui.menu("canvas", "code.add_clipboard", group="create", order=22, when="selection.count == 0")
    ui.menu("canvas", "code.edit", path="Code", group="code", order=0, when="code.selected")
    ui.menu("canvas", "code.copy", path="Code", group="code", order=1, when="code.selected")
    ui.place_submenu("canvas", "Code", "item", 2, icon="code", when="code.selected")
    ui.contribute("palette.buttons", PaletteButton("code.add", "Code", "code", order=21))

    # ---- Copy badge: one click on the block copies its code -------------------------------------

    class CopyBadge(InputHandler):
        SIZE = 22.0

        def __init__(self) -> None:
            self.hovered: tuple[Any, str] | None = None
            self.over = False

        def shown_in(self, view: Any) -> bool:
            return app.tools.active_id == "select" and view.display.show_overlays

        def badge_rect(self, view: Any, name: str) -> QRectF | None:
            item = canvas.item(name)
            if item is None or not item.isVisible():
                return None
            try:
                rect = view.mapFromScene(item.sceneBoundingRect()).boundingRect().toRectF()
            except RuntimeError:
                return None
            visible = rect.intersected(QRectF(view.viewport().rect()))
            if visible.isEmpty() or visible.width() < 3 * self.SIZE or visible.height() < 2 * self.SIZE:
                return None
            offset = 2 * self.SIZE + 12.0 if item.locked else self.SIZE + 6.0
            return QRectF(visible.right() - offset, visible.top() + 6.0, self.SIZE, self.SIZE)

        def _badge_at(self, view: Any, pos: QPointF) -> QRectF | None:
            if self.hovered is None or self.hovered[0] is not view:
                return None
            rect = self.badge_rect(view, self.hovered[1])
            return rect if rect is not None and rect.contains(pos) else None

        def _set_hovered(self, hovered: tuple[Any, str] | None, over: bool) -> None:
            if hovered == self.hovered and over == self.over:
                return
            for entry in (self.hovered, hovered):
                if entry is not None:
                    entry[0].viewport().update()
            self.hovered, self.over = hovered, over

        def hover(self, e: PointerEvent) -> None:
            if self._badge_at(e.view, e.pos) is not None:
                self._set_hovered(self.hovered, True)
                e.set_cursor(Qt.PointingHandCursor)
                ui.status("Click to copy the code")
                return
            item = e.item if self.shown_in(e.view) else None
            self._set_hovered((e.view, item.node_name) if isinstance(item, CodeItem) else None, False)

        def pointer(self, e: PointerEvent) -> str | None:
            if e.kind != "press" or e.button != Qt.LeftButton or self._badge_at(e.view, e.pos) is None:
                return None
            name = self.hovered[1]
            self._set_hovered(None, False)
            app.run_command("code.copy", [name])
            return HANDLED

        def leave(self, view: Any) -> None:
            if self.hovered is not None and self.hovered[0] is view:
                self._set_hovered(None, False)

        def paint(self, view: Any, painter: QPainter) -> None:
            if self.hovered is None or self.hovered[0] is not view or not self.shown_in(view):
                return
            rect = self.badge_rect(view, self.hovered[1])
            if rect is None:
                return
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QColor(0, 0, 0, 120))
            painter.setBrush(theme.ACCENT if self.over else QColor(20, 22, 27, 215))
            painter.drawRoundedRect(rect, 5.0, 5.0)
            icon("copy").paint(painter, rect.adjusted(4.0, 4.0, -4.0, -4.0).toRect())
            painter.restore()

    badge = CopyBadge()
    ui.input_handler(badge, 811)  # beside the text note's badge (810)
    ui.overlay(badge.paint, order=96)
