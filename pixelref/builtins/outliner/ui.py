"""The Outliner view (Qt)."""

from __future__ import annotations

import fnmatch
from typing import Any, Callable

from PySide6.QtCore import QItemSelection, QItemSelectionModel, QPoint, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QDrag, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from pixelref.plugin_api.ui import HANDLED, InputHandler, PanelType, PointerEvent, icon, theme
from pixelref.plugin_api.ui import widgets as w

from . import lock_owners

TYPE_ROLE = Qt.UserRole + 1

# Collections of node types offered in the Types menu: (label, type pattern).
TYPE_COLLECTIONS = (("Annotations", "annotation_*"),)


class StayOpenMenu(QMenu):
    """A menu that stays open while its check boxes are toggled.

    Alt+click on a check box calls ``on_solo(action)`` instead ("only this").
    """

    def __init__(self, parent: QWidget | None = None, on_solo: Callable[[Any], None] | None = None) -> None:
        super().__init__(parent)
        self._on_solo = on_solo

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        action = self.activeAction()
        if action is not None and action.isEnabled() and action.isCheckable() and action.menu() is None:
            if self._on_solo is not None and event.modifiers() & Qt.AltModifier:
                self._on_solo(action)
            else:
                action.trigger()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SceneTreeWidget(QTreeWidget):
    """Outliner with drag-and-drop reparenting and restacking.

    Rows are listed front-most first. Dropping onto a row parents the dragged
    nodes to it; dropping between rows makes them siblings at that position.
    """

    COL_NAME, COL_VISIBLE, COL_LOCKED = 0, 1, 2

    def __init__(self, on_drop: Callable[..., None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._on_drop = on_drop
        self.setColumnCount(3)
        self.setHeaderLabels(["Outliner", "", ""])
        header = self.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        for column in (self.COL_VISIBLE, self.COL_LOCKED):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.setColumnWidth(column, 24)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.setUniformRowHeights(True)
        self.setIconSize(QSize(14, 14))
        self.empty_hint = "Paste, drop or insert images to start\n(Ctrl+V · drag files here · Ctrl+I)"
        self._filter_text = ""
        self._hidden_types: frozenset[str] = frozenset()
        self.match_count = 0
        self._drag_names: list[str] = []

    def drawBranches(self, painter: QPainter, rect, index) -> None:  # noqa: ANN001, N802
        """Draw our own expand/collapse chevrons (the dark theme hides Qt's)."""
        if self.selectionModel().isSelected(index):
            painter.fillRect(rect, QColor("#1a3457"))
        if not self.model().hasChildren(index):
            return
        size = 4.0
        cx = rect.right() - self.indentation() / 2.0
        cy = rect.center().y()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#8a93a3"))
        if self.isExpanded(index):
            points = [QPointF(cx - size, cy - size / 2), QPointF(cx + size, cy - size / 2), QPointF(cx, cy + size / 2 + 1)]
        else:
            points = [QPointF(cx - size / 2, cy - size), QPointF(cx - size / 2, cy + size), QPointF(cx + size / 2 + 1, cy)]
        painter.drawPolygon(QPolygonF(points))
        painter.restore()

    def paintEvent(self, event) -> None:  # noqa: ANN001
        super().paintEvent(event)
        if self.topLevelItemCount() == 0 or not any(
            not self.topLevelItem(i).isHidden() for i in range(self.topLevelItemCount())
        ):
            painter = QPainter(self.viewport())
            painter.setPen(QColor("#5a606c"))
            if self._filter_text:
                text = f"Nothing matches “{self._filter_text}”"
            elif self._hidden_types and self.topLevelItemCount():
                text = "Every type in the scene is filtered out\n(Types menu above)"
            else:
                text = self.empty_hint
            painter.drawText(self.viewport().rect().adjusted(12, 12, -12, -12), Qt.AlignCenter | Qt.TextWordWrap, text)
            painter.end()

    def apply_filter(self, text: str | None = None, hidden_types: frozenset[str] | None = None) -> None:
        """Show rows whose name contains ``text`` and whose type isn't in
        ``hidden_types`` (``None`` keeps the current one). Ancestors of
        matching rows stay listed, in italics, to show where they are."""
        if text is not None:
            self._filter_text = text.strip()
        if hidden_types is not None:
            self._hidden_types = frozenset(hidden_types)
        needle = self._filter_text.lower()
        hidden = self._hidden_types
        filtering = bool(needle or hidden)
        count = 0

        def visit(item: QTreeWidgetItem) -> bool:
            nonlocal count
            child_match = False
            for i in range(item.childCount()):
                child_match = visit(item.child(i)) or child_match
            own = (not needle or needle in item.text(0).lower()) and item.data(0, TYPE_ROLE) not in hidden
            count += own
            item.setHidden(not (own or child_match))
            context_only = filtering and not own
            font = item.font(0)
            if font.italic() != context_only:
                font.setItalic(context_only)
                item.setFont(0, font)
            if filtering and child_match:
                item.setExpanded(True)
            return own or child_match

        for i in range(self.topLevelItemCount()):
            visit(self.topLevelItem(i))
        self.match_count = count
        self.viewport().update()

    @staticmethod
    def node_name(item: QTreeWidgetItem | None) -> str | None:
        return item.data(0, Qt.UserRole) if item is not None else None

    def dragged_names(self) -> list[str]:
        selected = set(id(item) for item in self.selectedItems())
        names: list[str] = []

        def walk(item: QTreeWidgetItem, ancestor_selected: bool) -> None:
            is_selected = id(item) in selected
            if is_selected and not ancestor_selected:
                names.append(self.node_name(item))
            for index in range(item.childCount()):
                walk(item.child(index), ancestor_selected or is_selected)

        for top in [self.topLevelItem(i) for i in range(self.topLevelItemCount())]:
            walk(top, False)
        return names

    def startDrag(self, supported_actions) -> None:  # noqa: ANN001
        """Run the drag ourselves instead of using QAbstractItemView.startDrag.

        Qt's version, once the drag reports MoveAction, removes the *currently
        selected* rows — by then the outliner was rebuilt from the scene, so
        it deleted the freshly reparented row. The tree is always rebuilt from
        the scene after a drop, so nothing has to happen when the drag ends.
        """
        items = [item for item in self.selectedItems() if item.flags() & Qt.ItemIsDragEnabled]
        if not items:
            return
        self._drag_names = self.dragged_names()
        mime = self.mimeData(items)
        if mime is None:
            return
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self._drag_pixmap(self._drag_names))
        try:
            drag.exec(Qt.MoveAction, Qt.MoveAction)
        finally:
            self._drag_names = []

    def _drag_pixmap(self, names: list[str]) -> QPixmap:
        label = names[0] if len(names) == 1 else f"{len(names)} items"
        metrics = self.fontMetrics()
        pixmap = QPixmap(metrics.horizontalAdvance(label) + 16, metrics.height() + 8)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QColor("#3d8ef0"))
        painter.setBrush(QColor(26, 52, 87, 220))
        painter.drawRoundedRect(QRectF(pixmap.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
        painter.setPen(QColor("#d5e8ff"))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, label)
        painter.end()
        return pixmap

    def dropEvent(self, event) -> None:  # noqa: ANN001
        if event.source() is not self:
            event.ignore()
            return
        names = list(self._drag_names or self.dragged_names())
        if not names:
            event.ignore()
            return
        target = self.itemAt(event.position().toPoint())
        indicator = self.dropIndicatorPosition()
        if target is None or indicator == QAbstractItemView.DropIndicatorPosition.OnViewport:
            new_parent, anchor, place = None, None, "end"
        elif indicator == QAbstractItemView.DropIndicatorPosition.OnItem:
            new_parent, anchor, place = self.node_name(target), None, "into"
        else:
            new_parent = self.node_name(target.parent())
            anchor = self.node_name(target)
            place = "above" if indicator == QAbstractItemView.DropIndicatorPosition.AboveItem else "below"
        probe = target
        while probe is not None:  # refuse drops onto a dragged row or its descendants
            if self.node_name(probe) in names:
                event.ignore()
                return
            probe = probe.parent()
        event.setDropAction(Qt.MoveAction)
        event.accept()
        # Defer the scene edit: rebuilding the tree inside the drop handler
        # would delete the rows Qt is still dispatching the event to.
        QTimer.singleShot(0, lambda: self._on_drop(names, new_parent, anchor, place))


class OutlinerPanel(QWidget):
    def __init__(self, ui: Any) -> None:
        super().__init__()
        self.ui = ui
        self.tree_items_by_name: dict[str, QTreeWidgetItem] = {}
        self._structure: list[Any] = []
        self._state: list[Any] = []
        self._badges: dict[tuple[str, str], QIcon] = {}
        self.syncing = False
        self._syncing_selection = False
        self._synced_names: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 4)
        layout.setSpacing(4)
        self.outliner_count = QLabel("", self)
        self.outliner_count.setObjectName("sectionHint")
        self.outliner_count.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.outliner_filter = QLineEdit(self)
        self.outliner_filter.setPlaceholderText("Filter by name…")
        self.outliner_filter.setClearButtonEnabled(True)
        self.hidden_types: set[str] = {str(t) for t in (ui.settings.value("hidden_types", []) or [])}
        self._type_rows: list[tuple[Any, list[str]]] = []  # Types menu check boxes: (action, types)
        self.type_button = QToolButton(self)
        self.type_button.setPopupMode(QToolButton.InstantPopup)
        self.type_button.setFocusPolicy(Qt.NoFocus)
        self.type_button.setToolTip("Filter the outliner by node type; show, hide, isolate, lock or select "
                                    "whole types on the canvas")
        self.type_menu = StayOpenMenu(self.type_button, on_solo=self._solo_types)
        self.type_menu.setToolTipsVisible(True)
        self.type_menu.aboutToShow.connect(self._build_type_menu)
        self.type_button.setMenu(self.type_menu)
        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(4)
        filter_row.addWidget(self.outliner_filter, 1)
        filter_row.addWidget(self.type_button)
        layout.addLayout(filter_row)
        self.tree = SceneTreeWidget(self.on_tree_drop, self)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.outliner_count)
        self.outliner_filter.textChanged.connect(lambda _text: self.apply_filters())
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.customContextMenuRequested.connect(self._show_menu)
        self.refresh()

    # ---- Building -------------------------------------------------------------------

    def _badge(self, node_type: str) -> QIcon:
        node_ui = self.ui.canvas.node_ui(node_type)
        color, glyph = node_ui.badge if node_ui is not None and node_ui.badge else ("#8a93a3", node_type[:1].upper())
        key = (color, glyph)
        if key not in self._badges:
            self._badges[key] = w.type_icon(color, glyph)
        return self._badges[key]

    def _type_color(self, node_type: str) -> QColor:
        node_ui = self.ui.canvas.node_ui(node_type)
        return QColor(node_ui.badge[0] if node_ui is not None and node_ui.badge else "#d5d8df")

    def refresh(self) -> None:
        nodes = list(self.ui.canvas.nodes.values())
        structure = [(n["name"], n["type"], n.get("parent")) for n in nodes]
        state = [(n["attrs"].get("visibility", 1), n["attrs"].get("locked", 0)) for n in nodes]
        self.syncing = True
        try:
            if structure != self._structure or len(self.tree_items_by_name) != len(nodes):
                self._rebuild(nodes)
                self._structure = structure
            elif state != self._state:
                self._restyle(nodes)
            self._state = state
        finally:
            self.syncing = False
        self.sync_selection()

    def _style_row(self, item: QTreeWidgetItem, node: dict[str, Any], hidden_ancestor: bool) -> None:
        attrs = node.get("attrs", {})
        visible = bool(attrs.get("visibility", 1))
        locked = bool(attrs.get("locked", 0))
        if "visibility" in attrs:
            item.setIcon(1, icon("eye") if visible else icon("eye_off", alpha=110))
        if "locked" in attrs:
            item.setIcon(2, icon("lock", "#e0a060") if locked else icon("lock_open", alpha=45))
        item.setForeground(0, QColor("#5a606c") if hidden_ancestor or not visible else self._type_color(node["type"]))

    def _restyle(self, nodes: list[dict[str, Any]]) -> None:
        hidden: dict[str, bool] = {}
        for node in nodes:  # parents come first
            parent = node.get("parent")
            hidden_ancestor = bool(parent) and hidden.get(parent, False)
            hidden[node["name"]] = hidden_ancestor or not bool(node["attrs"].get("visibility", 1))
            item = self.tree_item(node["name"])
            if item is not None:
                self._style_row(item, node, hidden_ancestor)

    def _rebuild(self, nodes: list[dict[str, Any]]) -> None:
        collapsed = {name for name, item in self.tree_items_by_name.items() if isValid(item) and not item.isExpanded()}
        scroll = self.tree.verticalScrollBar().value()
        self.tree.clear()
        self.tree_items_by_name.clear()
        children_by_parent: dict[str | None, list[dict[str, Any]]] = {}
        for node in nodes:
            children_by_parent.setdefault(node.get("parent"), []).append(node)

        def add_items(parent_name: str | None, parent_item: QTreeWidgetItem | None, hidden: bool) -> None:
            # Snapshot order is back-to-front; list front-most first.
            for node in reversed(children_by_parent.get(parent_name, [])):
                name, node_type = node["name"], node["type"]
                visible = bool(node.get("attrs", {}).get("visibility", 1))
                item = QTreeWidgetItem([name, "", ""])
                item.setData(0, Qt.UserRole, name)
                item.setData(0, TYPE_ROLE, node_type)
                item.setFlags(item.flags() | Qt.ItemIsEditable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
                self._style_row(item, node, hidden)
                item.setIcon(0, self._badge(node_type))
                item.setToolTip(0, f"{name}  [{node_type}]")
                item.setToolTip(1, "Click to toggle visibility")
                item.setToolTip(2, "Click to toggle lock")
                if parent_item is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)
                self.tree_items_by_name[name] = item
                add_items(name, item, hidden or not visible)

        add_items(None, None, False)
        self.tree.expandAll()
        self.apply_filters()
        for name in collapsed:
            tree_item = self.tree_item(name)
            if tree_item is not None:
                tree_item.setExpanded(False)
        self.tree.verticalScrollBar().setValue(scroll)

    # ---- Filtering -----------------------------------------------------------------------

    def apply_filters(self) -> None:
        self.tree.apply_filter(self.outliner_filter.text(), frozenset(self.hidden_types))
        total = len(self.ui.canvas.nodes)
        shown = self.tree.match_count
        if not total:
            text = ""
        elif shown == total:
            text = f"{total} item{'s' if total != 1 else ''}"
        else:
            text = f"{shown} of {total} items"
        self.outliner_count.setText(text)
        present = {node["type"] for node in self.ui.canvas.nodes.values()}
        filtered = self.hidden_types & present
        self.type_button.setText(f"Types {len(present) - len(filtered)}/{len(present)}" if filtered else "Types")
        self.type_button.setStyleSheet("color: #6ebeff;" if filtered else "")

    def set_hidden_types(self, types: Any) -> None:
        """Leave rows of these node types out of the outliner (the scene is unchanged)."""
        self.hidden_types = set(types)
        self.ui.settings.setValue("hidden_types", sorted(self.hidden_types))
        self.apply_filters()
        for action, covered in self._type_rows:  # keep an open Types menu in step
            if isValid(action):
                action.blockSignals(True)
                action.setChecked(not (self.hidden_types & set(covered)))
                action.blockSignals(False)

    def present_types(self) -> list[tuple[str, int]]:
        """(node type, count) of the scene's nodes, by friendly name."""
        counts: dict[str, int] = {}
        for node in self.ui.canvas.nodes.values():
            counts[node["type"]] = counts.get(node["type"], 0) + 1
        canvas = self.ui.canvas
        return sorted(counts.items(), key=lambda item: canvas.friendly_type(item[0]).lower())

    def _check_row(self, text: str, types: list[str], tip: str) -> None:
        # No icon: with the menu style sheet an icon replaces the check mark.
        action = self.type_menu.addAction(text)
        action.setCheckable(True)
        action.setToolTip(tip)
        action.setData(types)
        action.toggled.connect(lambda on, ts=types: self._show_types(ts, on))
        self._type_rows.append((action, types))

    def _show_types(self, types: list[str], shown: bool) -> None:
        self.set_hidden_types(self.hidden_types - set(types) if shown else self.hidden_types | set(types))

    def _solo_types(self, action: Any) -> None:
        types = action.data()
        if types:
            self.set_hidden_types({t for t, _count in self.present_types()} - set(types))

    def _menu_header(self, text: str) -> None:
        self.type_menu.addAction(text.upper()).setEnabled(False)

    def _build_type_menu(self) -> None:
        menu = self.type_menu
        menu.clear()
        self._type_rows = []
        canvas = self.ui.canvas
        present = self.present_types()
        names = [node_type for node_type, _count in present]
        if not present:
            menu.addAction("The scene is empty").setEnabled(False)
            return
        self._menu_header("Show in Outliner")
        self._check_row("All Types", names, "List every node type")
        collections = []
        for label, pattern in TYPE_COLLECTIONS:
            covered = [t for t in names if fnmatch.fnmatchcase(t, pattern)]
            if len(covered) > 1:
                collections.append((label, pattern))
                count = sum(c for t, c in present if t in covered)
                self._check_row(f"{label}   {count}", covered,
                                ", ".join(canvas.friendly_type(t) for t in covered) + " · Alt+click: only these")
        menu.addSeparator()
        for node_type, count in present:
            self._check_row(f"{canvas.friendly_type(node_type)}   {count}", [node_type],
                            f"{node_type} · Alt+click: only this type")
        self.set_hidden_types(self.hidden_types)  # sync the check boxes

        menu.addSeparator()
        self._menu_header("On the Canvas")
        run = self.ui.app.run_command
        targets = [(label, pattern, QIcon()) for label, pattern in collections]
        targets += [(canvas.friendly_type(t), t, self._badge(t)) for t in names]
        for label, pattern, row_icon in targets:
            sub = menu.addMenu(row_icon, label)
            for text, command, args in (
                ("Show All", "outliner.set_type_flag", ("visibility", 1)),
                ("Hide All", "outliner.set_type_flag", ("visibility", 0)),
                ("Isolate", "outliner.isolate_types", ()),
                (None, "", ()),
                ("Lock All", "outliner.set_type_flag", ("locked", 1)),
                ("Unlock All", "outliner.set_type_flag", ("locked", 0)),
                (None, "", ()),
                ("Select All", "outliner.select_types", ()),
            ):
                if text is None:
                    sub.addSeparator()
                else:
                    sub.addAction(text).triggered.connect(lambda _=False, c=command, p=pattern, a=args: run(c, p, *a))
        menu.addSeparator()
        menu.addAction("Show Everything").triggered.connect(lambda: run("outliner.show_all"))

    def tree_item(self, name: str) -> QTreeWidgetItem | None:
        """Row for ``name``; None if missing or already destroyed by Qt."""
        item = self.tree_items_by_name.get(name)
        if item is not None and not isValid(item):
            del self.tree_items_by_name[name]
            return None
        return item

    # ---- Selection ---------------------------------------------------------------------

    def sync_selection(self) -> None:
        names = list(self.ui.session.selection.names)
        # Only scroll when the selection itself changed: refreshes for other
        # edits (toggling an eye/lock column) must not jump the list around.
        scroll = names != self._synced_names
        self._synced_names = names
        self._syncing_selection = True
        try:
            # One selection-model update instead of a signal per row.
            selection = QItemSelection()
            last = None
            for name in names:
                item = self.tree_item(name)
                if item is not None:
                    index = self.tree.indexFromItem(item, 0)
                    selection.select(index, index.siblingAtColumn(self.tree.columnCount() - 1))
                    last = item
            self.tree.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            if last is not None:
                self.tree.setCurrentItem(last, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
                if scroll:
                    self.tree.scrollToItem(last)
        finally:
            self._syncing_selection = False

    def _on_tree_selection_changed(self) -> None:
        if self.syncing or self._syncing_selection:
            return
        chosen = {name for item in self.tree.selectedItems() if (name := item.data(0, Qt.UserRole))}
        names = [name for name in self.ui.canvas.nodes if name in chosen]
        if names != self.ui.session.selection.names:
            self.ui.app.run_command("select", names)

    # ---- Editing -------------------------------------------------------------------------

    def on_tree_drop(self, names: list[str], new_parent: str | None, anchor: str | None, place: str) -> None:
        session = self.ui.session
        names = [name for name in names if session.scene.has(name)]
        if not names:
            return
        self.ui.app.run_command("move_nodes", names, new_parent, anchor, place)
        self.ui.app.run_command("select", names)
        self.ui.status(f"Moved {', '.join(names)} → {new_parent or 'root'}")

    def on_tree_reparent(self, node_name: str, new_parent: str | None) -> None:
        """Parent ``node_name`` under ``new_parent`` (front-most), or to the root."""
        self.on_tree_drop([node_name], new_parent, None, "into" if new_parent else "end")

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self.syncing or column != SceneTreeWidget.COL_NAME:
            return
        old_name = item.data(0, Qt.UserRole)
        new_name = item.text(0).strip()
        if not old_name or new_name == old_name:
            return
        if not new_name:
            QTimer.singleShot(0, self._force_rebuild)
            return
        QTimer.singleShot(0, lambda: self._rename(old_name, new_name))

    def _force_rebuild(self) -> None:
        self._structure = []
        self.refresh()

    def _rename(self, old_name: str, new_name: str) -> None:
        if self.ui.app.run_command("rename", old_name, new_name) is None:
            self._force_rebuild()
            return
        if self.ui.session.scene.has(new_name):
            self.ui.app.run_command("select", [new_name])

    def edit_name(self, name: str) -> None:
        item = self.tree_item(name)
        if item is not None:
            self.tree.editItem(item, SceneTreeWidget.COL_NAME)

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        name = item.data(0, Qt.UserRole)
        if not name or column == SceneTreeWidget.COL_NAME:
            return
        attr = "visibility" if column == SceneTreeWidget.COL_VISIBLE else "locked"
        attrs = self.ui.canvas.nodes.get(name, {}).get("attrs", {})
        if attr not in attrs:
            return
        current = bool(attrs.get(attr, 1 if attr == "visibility" else 0))
        self.ui.app.run_command("outliner.set_flag", name, attr, 0 if current else 1)

    def _show_menu(self, pos: QPoint) -> None:
        item = self.tree.itemAt(pos)
        if item is not None and not item.isSelected():
            self.ui.app.run_command("select", [item.data(0, Qt.UserRole)])
        menu = self.ui.app.menus.build("canvas", self)
        menu.exec(self.tree.viewport().mapToGlobal(pos))


class OutlinerService:
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.panels: list[OutlinerPanel] = []

    def panel(self) -> OutlinerPanel | None:
        """The panel on screen (sidebar, floating card or pop-up), else any."""
        visible = [p for p in self.panels if p.isVisible()]
        return (visible or self.panels or [None])[0]


def activate_outliner_ui(ui: Any) -> None:
    service = OutlinerService(ui)
    ui.provide_service("outliner", service)

    def make_panel(_parent: Any = None) -> OutlinerPanel:
        panel = OutlinerPanel(ui)
        service.panels.append(panel)
        return panel

    def dispose_panel(panel: QWidget) -> None:
        if panel in service.panels:
            service.panels.remove(panel)

    ui.panel(PanelType(id="outliner", title="Outliner", icon="outliner", color="#4cc9a6", factory=make_panel,
                       dispose=dispose_panel, order=10, popup_keys=("Alt+O",), popup_size=(300, 420), stretch=2,
                       keywords="scene tree hierarchy layers"))

    def refresh_all(_event: Any = None) -> None:
        for panel in list(service.panels):
            panel.refresh()

    def sync_selection(_event: Any = None) -> None:
        for panel in list(service.panels):
            panel.sync_selection()

    ui.on_ui("canvas.refreshed", refresh_all)
    ui.on_ui("ui.selection_changed", sync_selection)

    def rename_selected() -> None:
        """Rename the selected node (in the outliner, or with a prompt)."""
        names = ui.session.selection.names
        if len(names) != 1:
            return
        panel = service.panel()
        if panel is None or not panel.isVisible():
            ui.app.run_command("panels.popup", "outliner")
            panel = service.panel()
        if panel is not None and panel.isVisible():
            panel.edit_name(names[0])
            return
        text = ui.app.run_command("ui.prompt_dialog", "New name:", "Rename", names[0])
        if text and text.strip() and text.strip() != names[0]:
            ui.app.run_command("rename", names[0], text.strip())

    ui.command("rename_selected", rename_selected, undoable=False, echo=False)
    a = ui.action
    a("outliner.lock", "outliner.toggle_lock", "Lock / Unlock", keywords="freeze pin protect", category="Arrange",
      icon="lock")
    a("outliner.hide", "outliner.toggle_visibility", "Hide / Show", keywords="visibility invisible show",
      category="Arrange", icon="eye_off")
    a("outliner.show_all", "outliner.show_all", "Show All", keywords="visibility unhide reveal", category="Arrange",
      icon="eye")
    a("outliner.unlock_all", "outliner.unlock_all", "Unlock All", keywords="unfreeze", category="Arrange", icon="lock_open")
    a("outliner.rename", "outliner.rename_selected", "Rename…", keywords="name title label", category="Edit", icon="text")
    for key, action in (("L", "outliner.lock"), ("H", "outliner.hide"), ("Alt+H", "outliner.show_all"),
                        ("Alt+L", "outliner.unlock_all"), ("F2", "outliner.rename")):
        ui.bind(key, action)
    a("outliner.select_same_type", "outliner.select_types", "Select All of Same Type",
      keywords="similar kind type select", category="Select")
    a("outliner.hide_same_type", "outliner.set_type_flag", "Hide All of Same Type", kwargs={"value": 0},
      keywords="similar kind type visibility invisible", category="Arrange")
    a("outliner.isolate_same_type", "outliner.isolate_types", "Isolate Type",
      keywords="similar kind type solo only visibility", category="Arrange")
    a("outliner.lock_same_type", "outliner.set_type_flag", "Lock All of Same Type",
      kwargs={"attr": "locked", "value": 1}, keywords="similar kind type freeze", category="Arrange")
    for order, key in enumerate(("outliner.rename", "outliner.lock", "outliner.hide"), start=1):
        ui.menu("canvas", key, group="state", order=order, when="selection.count > 0")
    for order, key in enumerate(("outliner.select_same_type", "outliner.hide_same_type",
                                 "outliner.isolate_same_type", "outliner.lock_same_type")):
        ui.menu("canvas", key, path="Same Type", group="state", order=order, when="selection.count > 0")
    ui.place_submenu("canvas", "Same Type", group="state", order=4)
    ui.menu("canvas", "outliner.show_all", group="reveal", order=0, when="selection.count == 0")
    ui.menu("canvas", "outliner.unlock_all", group="reveal", order=1, when="selection.count == 0")

    def unlock_menu(menu: Any) -> None:
        """Puts "Unlock ‹name›" at the top of the menu when what was right-clicked is locked."""
        owners: list[str] = []
        for name in ui.session.selection.names:
            owners += [owner for owner in lock_owners(ui.session.scene, name) if owner not in owners]
        if not owners:
            return
        action = menu.addAction(icon("lock_open"), f"Unlock {owners[0]}" if len(owners) == 1
                                else f"Unlock {len(owners)} Items")
        action.triggered.connect(lambda: ui.app.run_command("outliner.unlock"))

    ui.menu("canvas", builder=unlock_menu, group="header", order=1, when="selection.count > 0")
    badge = LockBadge(ui)
    ui.input_handler(badge, 800)  # above the interactions plugin (750): a locked button's click runs it
    ui.overlay(badge.paint, order=95)


class LockBadge(InputHandler):
    """A lock badge in the corner of the locked item under the pointer (Select tool).
    Clicking it unlocks the item (and the locked groups holding it) and selects it."""

    SIZE = 22.0

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.hovered: tuple[Any, str] | None = None  # (view, node name)
        self.over = False  # the pointer is on the badge itself

    def shown_in(self, view: Any) -> bool:
        tool = self.ui.tools.active
        return tool is not None and tool.id == "select" and view.display.show_overlays

    def badge_rect(self, view: Any, name: str) -> QRectF | None:
        item = self.ui.canvas.item(name)
        if item is None or not item.isVisible() or not item.locked:
            return None
        try:
            rect = view.mapFromScene(item.sceneBoundingRect()).boundingRect().toRectF()
        except RuntimeError:  # the item was replaced by a refresh
            return None
        visible = rect.intersected(QRectF(view.viewport().rect()))
        if visible.isEmpty():
            return None
        size = self.SIZE
        if visible.width() >= 3 * size and visible.height() >= 3 * size:
            return QRectF(visible.right() - size - 6.0, visible.top() + 6.0, size, size)
        return None  # too small on screen: no badge (it would jump out of the item's bounds)

    def _badge_at(self, view: Any, pos: QPointF) -> QRectF | None:
        if self.hovered is None or self.hovered[0] is not view:
            return None
        rect = self.badge_rect(view, self.hovered[1])
        return rect if rect is not None and rect.contains(pos) else None

    def _set_hovered(self, hovered: tuple[Any, str] | None, over: bool) -> None:
        if hovered == self.hovered and over == self.over:
            return
        for view in {entry[0] for entry in (self.hovered, hovered) if entry is not None}:
            view.viewport().update()
        self.hovered, self.over = hovered, over

    def hover(self, e: PointerEvent) -> None:
        if self._badge_at(e.view, e.pos) is not None:
            self._set_hovered(self.hovered, True)
            e.set_cursor(Qt.PointingHandCursor)
            owners = lock_owners(self.ui.session.scene, self.hovered[1])
            self.ui.status(f"Click to unlock {', '.join(owners) or self.hovered[1]}")
            return
        item = e.item if self.shown_in(e.view) else None
        self._set_hovered((e.view, item.node_name) if item is not None and item.locked else None, False)

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind != "press" or e.button != Qt.LeftButton or self._badge_at(e.view, e.pos) is None:
            return None
        name = self.hovered[1]
        self._set_hovered(None, False)
        self.ui.app.run_command("outliner.unlock", [name])
        self.ui.app.run_command("select", [name])
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
        icon("lock_open" if self.over else "lock").paint(painter, rect.adjusted(4.0, 4.0, -4.0, -4.0).toRect())
        painter.restore()
