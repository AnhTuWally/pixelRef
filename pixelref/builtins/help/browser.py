"""The Help browser: search every documentation entry (``session.docs``) as you type and read it.

Up/Down choose a result while typing, Enter runs it when it's a tool, a menu
item or a command without arguments, Ctrl+Enter copies its Python code. Links
between entries (``doc:<id>``) and between the guides open in place; Back
(Alt+Left) returns."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QFontMetrics, QGuiApplication, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pixelref.plugin_api.ui import icon, theme

ID_ROLE = Qt.UserRole + 1
MAX_RESULTS = 120
# The filter chips: (label, kinds). Kinds a plugin adds appear after these.
CHIPS = (("All", ()), ("Tools", ("tool",)), ("Menus", ("action",)), ("Commands", ("command",)),
         ("Nodes", ("node", "attr")), ("Guides", ("guide",)))
MUTED = "#8a93a3"


class ResultDelegate(QStyledItemDelegate):
    """Two lines per result: the title (with its kind and keys on the right) and the summary."""

    def __init__(self, browser: "DocsBrowser") -> None:
        super().__init__(browser)
        self.browser = browser

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), QFontMetrics(option.font).height() * 2 + 10)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        entry = self.browser.entry(index.data(ID_ROLE))
        if entry is None:
            return
        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, theme.ACCENT.darker(170))
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, Qt.GlobalColor.transparent)
        rect = option.rect.adjusted(8, 4, -8, -4)
        metrics = QFontMetrics(option.font)
        line = metrics.height()
        side = entry.kind_title + ("  " + "  ".join(entry.keys[:2]) if entry.keys else "")
        side_width = metrics.horizontalAdvance(side) + 8
        bold = QFont(option.font)
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(Qt.GlobalColor.white)
        title_rect = QRect(rect.left(), rect.top(), rect.width() - side_width, line)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         QFontMetrics(bold).elidedText(entry.title, Qt.ElideRight, title_rect.width()))
        painter.setFont(option.font)
        painter.setPen(theme.ACCENT if entry.keys else theme.MUTED)
        painter.drawText(QRect(rect.right() - side_width, rect.top(), side_width, line),
                         Qt.AlignRight | Qt.AlignVCenter, side)
        painter.setPen(theme.MUTED)
        summary = entry.summary or entry.name
        painter.drawText(QRect(rect.left(), rect.top() + line, rect.width(), line), Qt.AlignLeft | Qt.AlignVCenter,
                         metrics.elidedText(summary, Qt.ElideRight, rect.width()))
        painter.restore()


class DocsBrowser(QWidget):
    def __init__(self, ui: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ui = ui
        self.docs = ui.session.docs
        self.history: list[str] = []
        self.current: str | None = None
        self.kinds: tuple[str, ...] = ()
        self._stale = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(4)
        self.back_btn = QToolButton(self)
        self.back_btn.setIcon(icon("chevron_left", MUTED))
        self.back_btn.setToolTip("Back (Alt+Left)")
        self.back_btn.setAutoRaise(True)
        self.back_btn.clicked.connect(self.back)
        top.addWidget(self.back_btn)
        self.search = QLineEdit(self)
        self.search.setObjectName("docsSearch")
        self.search.setPlaceholderText("Search tools, commands, attributes, guides…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self.refresh())
        self.search.installEventFilter(self)
        top.addWidget(self.search, 1)
        layout.addLayout(top)

        self.chip_row = QHBoxLayout()
        self.chip_row.setSpacing(2)
        self.chips = QButtonGroup(self)
        self.chips.setExclusive(True)
        self.chips.idClicked.connect(lambda _id: self.refresh())
        layout.addLayout(self.chip_row)

        split = QSplitter(Qt.Vertical, self)
        split.setChildrenCollapsible(False)
        self.results = QListWidget(split)
        self.results.setObjectName("docsResults")
        self.results.setItemDelegate(ResultDelegate(self))
        self.results.setUniformItemSizes(True)
        self.results.setMouseTracking(True)
        self.results.currentItemChanged.connect(self._on_current)
        self.results.itemActivated.connect(lambda _item: self.run_current())
        self.view = QTextBrowser(split)
        self.view.setObjectName("docsView")
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._on_link)
        from pixelref.plugin_api import docs_folder

        folder = docs_folder()
        if folder is not None:
            self.view.setSearchPaths([str(folder)])  # the guides' images
        split.addWidget(self.results)
        split.addWidget(self.view)
        split.setSizes([200, 320])
        layout.addWidget(split, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(4)
        self.run_btn = QToolButton(self)
        self.run_btn.setText("Run")
        self.run_btn.setIcon(icon("play", MUTED))
        self.run_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.run_btn.clicked.connect(self.run_current)
        self.copy_btn = QToolButton(self)
        self.copy_btn.setText("Copy code")
        self.copy_btn.setIcon(icon("copy", MUTED))
        self.copy_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.copy_btn.setToolTip("Copy the Python code (Ctrl+Enter)")
        self.copy_btn.clicked.connect(self.copy_usage)
        self.id_label = QLabel("", self)
        self.id_label.setStyleSheet(f"color: {MUTED};")
        self.id_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        buttons.addWidget(self.run_btn)
        buttons.addWidget(self.copy_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.id_label)
        layout.addLayout(buttons)

        QShortcut(QKeySequence("Alt+Left"), self, self.back, context=Qt.WidgetWithChildrenShortcut)
        self._watch = self.docs.watch(self._on_index_changed)
        self.destroyed.connect(lambda *_: self._watch.dispose())
        self._build_chips()
        self.refresh()

    # ---- Lookups ----------------------------------------------------------------------------

    def entry(self, doc_id: Any) -> Any:
        return self.docs.entries().get(str(doc_id)) if doc_id else None

    def _build_chips(self) -> None:
        while self.chip_row.count():
            item = self.chip_row.takeAt(0)
            if item.widget() is not None:
                self.chips.removeButton(item.widget())
                item.widget().deleteLater()
        present = self.docs.kinds()
        known = {kind for _label, kinds in CHIPS for kind in kinds}
        chips = list(CHIPS) + [(present_kind.capitalize() + "s", (present_kind,)) for present_kind in present
                               if present_kind not in known and present_kind not in ("plugin",)]
        self.chip_kinds = []
        for index, (label, kinds) in enumerate(chips):
            if kinds and not any(k in present for k in kinds):
                continue
            chip = QToolButton(self)
            chip.setText(label)
            chip.setCheckable(True)
            chip.setAutoRaise(True)
            chip.setObjectName("docsChip")
            chip.setStyleSheet(f"QToolButton#docsChip {{ padding: 1px 6px; color: {MUTED}; }}"
                               f"QToolButton#docsChip:checked {{ color: white; background: {theme.ACCENT_HEX};"
                               " border-radius: 3px; }")
            count = sum(present.get(k, 0) for k in kinds) if kinds else sum(present.values())
            chip.setToolTip(f"{count} entries")
            self.chips.addButton(chip, len(self.chip_kinds))
            self.chip_kinds.append(kinds)
            self.chip_row.addWidget(chip)
            if index == 0:
                chip.setChecked(True)
        self.chip_row.addStretch(1)

    def selected_kinds(self) -> tuple[str, ...]:
        chip = self.chips.checkedId()
        return self.chip_kinds[chip] if 0 <= chip < len(self.chip_kinds) else ()

    # ---- Searching and showing ----------------------------------------------------------------

    def refresh(self) -> None:
        keep = self.current
        entries = self.docs.search(self.search.text(), self.selected_kinds() or None, MAX_RESULTS)
        self.results.blockSignals(True)
        self.results.clear()
        for entry in entries:
            item = QListWidgetItem()
            item.setData(ID_ROLE, entry.id)
            item.setToolTip(entry.summary)
            self.results.addItem(item)
        self.results.blockSignals(False)
        ids = [e.id for e in entries]
        if self.search.text().strip() and ids:
            self.results.setCurrentRow(0)  # the best match shows at once
        elif keep in ids:
            self.results.setCurrentRow(ids.index(keep))
        elif not self.search.text().strip() and keep:
            self.show_entry(keep, record=False)

    def _on_current(self, item: QListWidgetItem | None, _previous: Any = None) -> None:
        if item is not None:
            self.show_entry(item.data(ID_ROLE))

    def open(self, name: str) -> bool:
        """Show an entry by id or name; False if there is none (then ``name`` is searched)."""
        entry = self.docs.get(name)
        if entry is None:
            self.search.setText(name)
            return False
        self.show_entry(entry.id)
        return True

    def show_entry(self, doc_id: str, record: bool = True) -> None:
        entry = self.entry(doc_id)
        if entry is None:
            return
        if record and self.current and self.current != doc_id:
            self.history.append(self.current)
            del self.history[:-50]
        self.current = doc_id
        self.view.setHtml(render(self.docs.markdown(entry)))
        self.id_label.setText(entry.id)
        self.run_btn.setEnabled(self._runner(entry) is not None)
        self.copy_btn.setEnabled(bool(entry.usage))
        self.back_btn.setEnabled(bool(self.history))

    def back(self) -> None:
        if self.history:
            self.show_entry(self.history.pop(), record=False)
            self.back_btn.setEnabled(bool(self.history))

    def _on_link(self, url: QUrl) -> None:
        text = url.toString()
        if text.startswith("doc:"):
            self.show_entry(text[4:])
            return
        if url.scheme() in ("http", "https", "mailto", "file"):
            QDesktopServices.openUrl(url)
            return
        # A link between the Markdown guides: user-guide.md#links, #section, ../README.md
        current = self.entry(self.current)
        page = Path(url.path()).stem if url.path() else (current.data.get("page", "") if current else "")
        fragment = url.fragment()
        for candidate in (f"guide:{page}#{fragment}" if fragment else "", f"guide:{page}"):
            if candidate and self.entry(candidate) is not None:
                self.show_entry(candidate)
                return
        self.search.setText(fragment.replace("-", " ") or page)

    def _on_index_changed(self) -> None:
        if not self._stale:
            self._stale = True
            QTimer.singleShot(0, self._reload)

    def _reload(self) -> None:
        self._stale = False
        checked = self.chips.checkedId()
        self._build_chips()
        if 0 <= checked < len(self.chips.buttons()):
            self.chips.button(checked).setChecked(True)
        self.refresh()

    # ---- Running and copying -----------------------------------------------------------------

    def _runner(self, entry: Any) -> Any:
        app = self.ui.app
        if entry.kind == "tool":
            return lambda: app.trigger(f"tool.{entry.name}")
        if entry.kind == "action" and entry.name in app.actions.specs:
            action = app.actions.get(entry.name)
            if action is not None and not action.isEnabled():
                return None
            return lambda: app.trigger(entry.name)
        if entry.kind == "command":
            command = self.ui.session.commands.get(entry.name)
            if command is None or _needs_arguments(command.fn):
                return None
            return lambda: app.run_command(entry.name)
        return None

    def run_current(self) -> None:
        runner = self._runner(self.entry(self.current)) if self.current else None
        if runner is None:
            return
        popup = self.window()
        if popup is not None and popup.objectName() == "panelPopup":
            popup.close()  # so the command acts on the canvas, not on the pop-up
        runner()

    def copy_usage(self) -> None:
        entry = self.entry(self.current)
        if entry is not None and entry.usage:
            QGuiApplication.clipboard().setText(entry.usage)
            self.ui.status(f"Copied: {entry.usage}")

    # ---- Keyboard ---------------------------------------------------------------------------------

    def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent

        if obj is self.search and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                row = self.results.currentRow() + (1 if key == Qt.Key_Down else -1)
                self.results.setCurrentRow(max(0, min(row, self.results.count() - 1)))
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                if event.modifiers() & Qt.ControlModifier:
                    self.copy_usage()
                else:
                    self.run_current()
                return True
            if key in (Qt.Key_PageDown, Qt.Key_PageUp):
                bar = self.view.verticalScrollBar()
                bar.setValue(bar.value() + (bar.pageStep() if key == Qt.Key_PageDown else -bar.pageStep()))
                return True
        return super().eventFilter(obj, event)

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()


def render(markdown: str) -> str:
    """Markdown as HTML in the app's colours (Qt's own uses pure blue links and a 'monospace' font
    Windows doesn't have)."""
    from PySide6.QtGui import QTextDocument

    document = QTextDocument()
    document.setMarkdown(markdown)
    return (document.toHtml()
            .replace("color:#0000ff;", f"color:{theme.ACCENT_HEX};")
            .replace("font-family:'monospace';", "font-family:'Consolas','Menlo','DejaVu Sans Mono',monospace; "
                                                 "color:#e6c07b;")
            .replace("<pre style=\"", "<pre style=\" background-color:#1b2029;"))


def _needs_arguments(fn: Any) -> bool:
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(p.default is p.empty and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
               for p in parameters)
