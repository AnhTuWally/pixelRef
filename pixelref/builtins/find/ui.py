"""The Find panel (Ctrl+F) and going to what it finds.

Typing searches as you go (see :mod:`.search` for the query syntax). In the field, Up / Down
move through the results, Enter goes to the highlighted one (and selects it), Shift+Enter
selects every match. A click on a result goes there too.

Going there uses the links plugin's glide when it is on (the view keeps its zoom when the
element fits, and Alt+Left comes back), otherwise the view frames the element.
"""

from __future__ import annotations

import html
from typing import Any

from PySide6.QtCore import QEvent, QModelIndex, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QIcon, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from pixelref.plugin_api import CommandError
from pixelref.plugin_api.ui import PanelType, icon, theme

from .search import parse_query, tag_query

LIMIT = 200  # results listed (a count says how many more there are)
EMPTY_TAGS = 30  # tags offered (most used first) while the field is empty
DEBOUNCE_MS = 120
NAME_ROLE = Qt.UserRole
DETAIL_ROLE = Qt.UserRole + 1
HIDDEN_ROLE = Qt.UserRole + 2
HINT = ("Name, #tag (#a,b: either), type:image, note:text, key:value (metadata); -word leaves out.\n"
        "Enter goes to the highlighted item · Shift+Enter selects every match")


class ResultDelegate(QStyledItemDelegate):
    """Two lines per result: the name, and under it the type, where it matched and its tags."""

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        line = QFontMetrics(option.font).height()
        return QSize(option.rect.width(), line * 2 + 8)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        style = option.widget.style() if option.widget else None
        if style is not None:
            plain = QStyleOptionViewItem(option)
            plain.text = ""
            plain.icon = QIcon()
            style.drawControl(QStyle.CE_ItemViewItem, plain, painter, option.widget)
        rect = option.rect.adjusted(6, 4, -6, -4)
        decoration = index.data(Qt.DecorationRole)
        if decoration is not None:
            decoration.paint(painter, QRect(rect.left(), rect.top() + 1, 16, 16))
            rect.setLeft(rect.left() + 22)
        hidden = bool(index.data(HIDDEN_ROLE))
        bold = QFont(option.font)
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(theme.MUTED if hidden else option.palette.text().color())
        line = QFontMetrics(bold).height()
        name = QFontMetrics(bold).elidedText(str(index.data(NAME_ROLE)), Qt.ElideRight, rect.width())
        painter.drawText(QRect(rect.left(), rect.top(), rect.width(), line), Qt.AlignLeft | Qt.AlignVCenter, name)
        painter.setFont(option.font)
        painter.setPen(theme.MUTED)
        detail = QFontMetrics(option.font).elidedText(str(index.data(DETAIL_ROLE)), Qt.ElideRight, rect.width())
        painter.drawText(QRect(rect.left(), rect.top() + line, rect.width(), line),
                         Qt.AlignLeft | Qt.AlignVCenter, detail)
        painter.restore()


class FindPanel(QWidget):
    def __init__(self, ui: Any, finder: "Finder") -> None:
        super().__init__()
        self.ui = ui
        self.finder = finder
        self.total = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        self.field = QLineEdit(self)
        self.field.setObjectName("findField")
        self.field.setPlaceholderText("Find: name, #tag, type:image, key:value…")
        self.field.setClearButtonEnabled(True)
        self.field.addAction(icon("search"), QLineEdit.LeadingPosition)
        self.field.setToolTip(HINT)
        layout.addWidget(self.field)
        self.results = QListWidget(self)
        self.results.setObjectName("findResults")
        self.results.setItemDelegate(ResultDelegate(self.results))
        self.results.setUniformItemSizes(True)
        self.results.setFocusPolicy(Qt.NoFocus)  # the field keeps the keyboard; Up / Down move the highlight
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results.itemClicked.connect(self._go_to_item)
        self.tags_label = QLabel(self)
        self.tags_label.setObjectName("findTags")
        self.tags_label.setWordWrap(True)
        self.tags_label.setTextFormat(Qt.RichText)
        self.tags_label.setToolTip("Click a tag to find everything tagged with it")
        self.tags_label.linkActivated.connect(lambda tag: self.focus_field(tag_query([tag])))
        layout.addWidget(self.tags_label)
        layout.addWidget(self.results, 1)
        bottom = QHBoxLayout()
        bottom.setSpacing(4)
        self.status = QLabel("", self)
        self.status.setObjectName("mutedLabel")
        self.status.setWordWrap(True)
        self.select_btn = QPushButton("Select All", self)
        self.select_btn.setToolTip("Select every match (Shift+Enter)")
        self.select_btn.setFocusPolicy(Qt.NoFocus)
        self.select_btn.clicked.connect(self.select_all)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.select_btn)
        layout.addLayout(bottom)

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(DEBOUNCE_MS)
        self.timer.timeout.connect(self.run)
        self.field.textChanged.connect(lambda _text: self.timer.start())
        self.field.returnPressed.connect(self._on_return)
        self.field.installEventFilter(self)
        self.run()

    # ---- Searching ---------------------------------------------------------------------------------

    def query(self) -> str:
        return self.field.text()

    def run(self) -> None:
        """Search for what the field says, keeping the highlighted result when it is still found."""
        self.timer.stop()
        current = self.current_name()
        query = self.query()
        results = self.finder.index.search(query) if parse_query(query) else []
        self.total = len(results)
        shown = results[:LIMIT]
        self.results.setUpdatesEnabled(False)
        self.results.clear()
        for match in shown:
            self.results.addItem(self._item(match))
        self.results.setUpdatesEnabled(True)
        names = [m.name for m in shown]
        if names:
            self.results.setCurrentRow(names.index(current) if current in names else 0)
        self.select_btn.setEnabled(bool(results))
        self._show_board_tags(not parse_query(query))
        if not parse_query(query):
            self.status.setText("Type to find items on the board.")
        elif not results:
            self.status.setText("Nothing matches.")
        elif self.total > LIMIT:
            self.status.setText(f"{self.total} found · first {LIMIT} listed")
        else:
            self.status.setText(f"{self.total} found")

    def _show_board_tags(self, empty: bool) -> None:
        """While the field is empty: the tags on the board, most used first, to click."""
        counts: dict[str, int] = {}
        if empty:
            for entry in self.finder.index.entries():
                if entry.on_canvas:
                    for tag in entry.tags:
                        counts[tag] = counts.get(tag, 0) + 1
        tags = sorted(counts, key=lambda t: (-counts[t], t.lower()))[:EMPTY_TAGS]
        self.tags_label.setVisible(bool(tags))
        if tags:
            links = " &nbsp; ".join(
                f'<a href="{html.escape(t, quote=True)}" style="color:{theme.ACCENT_HEX}; text-decoration:none">'
                f'#{html.escape(t)}</a>&nbsp;<span style="color:#8a8f99">{counts[t]}</span>' for t in tags)
            self.tags_label.setText(f'<span style="color:#8a8f99">Tags on this board:</span><br>{links}')

    def _item(self, match: Any) -> QListWidgetItem:
        canvas = self.ui.canvas
        item = QListWidgetItem()
        item.setData(NAME_ROLE, match.name)
        parts = [canvas.friendly_type(match.node_type)]
        if match.snippet:
            parts.append(match.snippet)
        if match.tags:
            parts.append(" ".join(f"#{t}" for t in match.tags))
        item.setData(DETAIL_ROLE, " · ".join(parts))
        node_ui = canvas.node_ui(match.node_type)
        if node_ui is not None and node_ui.icon:
            item.setData(Qt.DecorationRole, icon(node_ui.icon))
        hidden = not self.finder.on_screen(match.name)
        item.setData(HIDDEN_ROLE, hidden)
        where = ", ".join(match.fields)
        item.setToolTip(f"{match.name}\nMatched: {where}" + ("\nHidden" if hidden else ""))
        return item

    def refresh(self) -> None:
        """The board changed: search again (cheap: only changed nodes are indexed again)."""
        if self.isVisible() and parse_query(self.query()):
            self.timer.start()

    # ---- Acting on results ---------------------------------------------------------------------------

    def current_name(self) -> str | None:
        item = self.results.currentItem()
        return item.data(NAME_ROLE) if item is not None else None

    def _go_to_item(self, item: QListWidgetItem) -> None:
        self.results.setCurrentItem(item)
        self.go_to_current()

    def go_to_current(self) -> None:
        if self.timer.isActive():
            self.run()  # Enter right after typing: act on what was typed
        name = self.current_name()
        if name is not None:
            self.ui.app.run_command("find.locate", name)

    def select_all(self) -> None:
        if self.timer.isActive():
            self.run()
        if parse_query(self.query()):
            self.ui.app.run_command("find.select", self.query())

    def _on_return(self) -> None:
        self.go_to_current()  # Shift+Enter never gets here: the key filter selects every match

    def step(self, step: int) -> None:
        count = self.results.count()
        if count:
            row = max(0, min(count - 1, self.results.currentRow() + step))
            self.results.setCurrentRow(row)

    def eventFilter(self, obj: Any, event: Any) -> bool:  # noqa: N802
        if obj is self.field and event.type() == QEvent.KeyPress:
            key = event.key()
            page = max(1, self.results.height() // max(1, self.results.sizeHintForRow(0) or 1) - 1)
            steps = {Qt.Key_Down: 1, Qt.Key_Up: -1, Qt.Key_PageDown: page, Qt.Key_PageUp: -page}
            if key in steps:
                self.step(steps[key])
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ShiftModifier:
                self.select_all()
                return True
        return super().eventFilter(obj, event)

    def focus_field(self, query: str | None = None) -> None:
        if query is not None:
            self.field.setText(query)
            self.run()
        self.field.setFocus(Qt.ShortcutFocusReason)
        self.field.selectAll()


class Finder:
    """The Find panels and going to elements."""

    def __init__(self, ui: Any, index: Any) -> None:
        self.ui = ui
        self.index = index
        self.panels: list[FindPanel] = []

    def panel(self) -> FindPanel | None:
        visible = [p for p in self.panels if p.isVisible()]
        return (visible or [None])[-1]

    def on_screen(self, name: str) -> bool:
        item = self.ui.canvas.item(name)
        return item is not None and bool(self.ui.session.compute(name, "world_visibility"))

    def locate(self, name: str, view: str | None = None) -> str:
        session = self.ui.session
        name = session.scene.resolve(name)
        if self.ui.canvas.item(name) is None:
            raise CommandError(f"'{name}' isn't on the canvas")
        if not session.compute(name, "world_visibility"):
            self.ui.toast(f"'{name}' is hidden (Alt+H shows everything)")
        if session.commands.exists("links.go_to"):
            session.run("links.go_to", name, view)  # glides, selects, and remembers for Alt+Left
        else:
            session.run("navigation.frame", [name], view)
            session.selection.set([name])
        return name


def activate_find_ui(ui: Any) -> None:
    session = ui.session
    finder = Finder(ui, session.services.get("find"))
    app = ui.app

    def locate(node: str, view: str | None = None) -> str:
        """Go to an element: glide a view to it (or frame it) and select it. Returns its name."""
        return finder.locate(node, view)

    def show(query: str | None = None) -> None:
        """Open the Find panel (or focus it), optionally with a query typed in."""
        panel = finder.panel()
        if panel is None:
            app.run_command("panels.popup", "find")
            panel = finder.panel() or (finder.panels[-1] if finder.panels else None)
        if panel is None:
            return
        panel.window().activateWindow()
        panel.focus_field(query)

    ui.command("locate", locate, alias="find_locate", undoable=False)
    ui.command("show", show, undoable=False, echo=False)

    def selection_tags(nodes: Any = None) -> list[str]:
        names = list(session.selection.names) if nodes is None else \
            [session.scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        tags: list[str] = []
        for name in names:
            for tag in session.scene.get_node(name).attrs.get("tags") or []:
                if tag not in tags:
                    tags.append(tag)
        return tags

    def similar(nodes: Any = None) -> str:
        """Open Find with everything sharing a tag with nodes (default: the selection).
        Returns the query (``#a,b``)."""
        tags = selection_tags(nodes)
        if not tags:
            raise CommandError("The selection has no tags")
        query = tag_query(tags)
        show(query)
        return query

    ui.command("similar", similar, undoable=False)
    ui.context_key("find.selection_tagged", lambda: bool(selection_tags()))

    def make_panel(_parent: Any = None) -> FindPanel:
        panel = FindPanel(ui, finder)
        finder.panels.append(panel)
        return panel

    def dispose_panel(panel: QWidget) -> None:
        if panel in finder.panels:
            finder.panels.remove(panel)

    ui.panel(PanelType(id="find", title="Find", icon="search", color="#6ebeff", factory=make_panel,
                       dispose=dispose_panel, order=40, default_mode="hidden", popup_size=(340, 460),
                       float_size=(320, 420), stretch=2,
                       keywords="find search filter locate go to tag tags note metadata name"))
    ui.action("find.show", "find.show", "Find…", icon="search", category="Edit",
              keywords="find search locate go to element item tag note metadata filter")
    ui.bind("Ctrl+F", "find.show", when="!editing.text")
    ui.menu("canvas", "find.show", group="select", order=0)
    ui.action("find.similar", "find.similar", "Find Similar (Same Tags)", icon="search", category="Edit",
              keywords="find similar same tags related matching tagged like this")
    ui.menu("canvas", "find.similar", group="select", order=0.5, when="find.selection_tagged")

    def refresh(_event: Any = None) -> None:
        if getattr(app, "closed", False):
            return
        for panel in list(finder.panels):
            panel.refresh()

    ui.on_ui("canvas.refreshed", refresh)
