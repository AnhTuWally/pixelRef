"""Attribute Editor section for a node's ``tags``: chips, and a field that adds tags with completion.

It edits every selected node at once through the kernel's ``add_tags`` / ``remove_tags``, so a
change adds or removes tags and never replaces a node's other tags. A tag only some of the
selected nodes have shows dimmed with its count and a + that adds it to the rest. Clicking a
tag's name opens Find with everything carrying it (when the find plugin is on).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, QStringListModel, Qt
from PySide6.QtWidgets import QCompleter, QHBoxLayout, QLayout, QLineEdit, QToolButton, QWidget, QWidgetItem

from pixelref.plugin_api import clean_tags
from pixelref.plugin_api.contracts import SelectionInfo
from pixelref.plugin_api.ui import widgets as w

CHIP_STYLE = """
QWidget#tagChip { background: rgba(61, 142, 240, 60); border-radius: 9px; }
QWidget#tagChip[partial="true"] { background: rgba(138, 143, 153, 40); }
QWidget#tagChip QToolButton { border: none; background: transparent; padding: 1px 2px; }
QWidget#tagChip[partial="true"] QToolButton#tagLabel { color: #8a8f99; }
"""


def tag_counts(nodes: list[dict[str, Any]]) -> dict[str, int]:
    """How many of ``nodes`` (canvas node dicts) carry each tag, in first-seen order."""
    counts: dict[str, int] = {}
    for node in nodes:
        for tag in node.get("attrs", {}).get("tags") or []:
            counts[tag] = counts.get(tag, 0) + 1
    return counts


class FlowLayout(QLayout):
    """Lays widgets out left to right, wrapping to a new line when the width runs out."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 4) -> None:
        super().__init__(parent)
        self._items: list[Any] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item: Any) -> None:  # noqa: N802
        self._items.append(item)

    def addWidget(self, widget: QWidget) -> None:  # noqa: N802
        self.addChildWidget(widget)
        self.addItem(QWidgetItem(widget))

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Any:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> Any:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientations:  # noqa: N802
        return Qt.Orientations()

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._place(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._place(rect, move=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _place(self, rect: QRect, move: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        gap = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + gap, 0
            if move:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + gap
            line = max(line, hint.height())
        return y + line - rect.y()


class _TagCompleter(QCompleter):
    """Completes the tag after the last comma, keeping the ones typed before it."""

    def splitPath(self, path: str) -> list[str]:  # noqa: N802
        return [path.rsplit(",", 1)[-1].strip()]

    def pathFromIndex(self, index: Any) -> str:  # noqa: N802
        head = self.widget().text().rsplit(",", 1)
        prefix = head[0] + ", " if len(head) > 1 else ""
        return prefix + super().pathFromIndex(index)


class _FocusWatcher(QObject):
    def __init__(self, on_focus: Any, parent: QObject) -> None:
        super().__init__(parent)
        self.on_focus = on_focus

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.FocusIn:
            self.on_focus()
        return False


class TagsSection(w.InspectorSection):
    """The selection's tags as chips (✕ removes from every selected node), and a field to add some."""

    def __init__(self, ui: Any, parent: QWidget) -> None:
        super().__init__("tags", "Tags", parent)
        self.ui = ui
        self.names: list[str] = []
        self.counts: dict[str, int] = {}
        self._state: Any = None
        self.chips: dict[str, QWidget] = {}
        self.chips_box = QWidget(self.body)
        self.chips_box.setStyleSheet(CHIP_STYLE)
        self.flow = FlowLayout(self.chips_box)
        self.edit = QLineEdit(self.body)
        self.edit.setPlaceholderText("Add tags… (commas separate)")
        self.edit.setToolTip("Enter adds the tags to every selected item")
        self.edit.setClearButtonEnabled(True)
        self.model = QStringListModel(self.edit)
        completer = _TagCompleter(self.model, self.edit)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.edit.setCompleter(completer)
        self.edit.installEventFilter(_FocusWatcher(self._update_completions, self.edit))
        self.edit.returnPressed.connect(self._add_typed)
        self.form.addRow(self.chips_box)
        self.form.addRow(self.edit)

    # ---- Editing -------------------------------------------------------------------------------

    def _targets(self) -> list[str]:
        scene = self.ui.session.scene
        return [n for n in self.names if scene.has(n)]

    def _add_typed(self) -> None:
        tags, names = clean_tags(self.edit.text().split(",")), self._targets()
        if not tags or not names:
            return
        self.edit.clear()
        self.ui.app.run_command("add_tags", tags, names)

    def add_to_all(self, tag: str) -> None:
        names = self._targets()
        if names:
            self.ui.app.run_command("add_tags", [tag], names)

    def find(self, tag: str) -> None:
        if self.ui.session.commands.exists("find.show"):
            query = f'#"{tag}"' if any(c in tag for c in ' ",') else f"#{tag}"
            self.ui.app.run_command("find.show", query)

    def remove(self, tag: str) -> None:
        names = self._targets()
        if names:
            self.ui.app.run_command("remove_tags", [tag], names)

    def _update_completions(self) -> None:
        """Every tag in the scene, most used first, leaving out ones the whole selection has."""
        everywhere: dict[str, int] = tag_counts(list(self.ui.canvas.nodes.values()))
        total = len(self.names)
        known = [t for t in everywhere if self.counts.get(t, 0) < total]
        known.sort(key=lambda t: (-everywhere[t], t.lower()))
        self.model.setStringList(known)

    # ---- Chips ------------------------------------------------------------------------------------

    def _chip(self, tag: str, count: int, total: int) -> QWidget:
        chip = QWidget(self.chips_box)
        chip.setObjectName("tagChip")
        chip.setAttribute(Qt.WA_StyledBackground, True)
        partial = count < total
        chip.setProperty("partial", partial)
        layout = QHBoxLayout(chip)
        layout.setContentsMargins(6, 0, 2, 0)
        layout.setSpacing(0)
        label = QToolButton(chip)
        label.setObjectName("tagLabel")
        label.setText(f"{tag}  {count}/{total}" if partial else tag)
        label.setFocusPolicy(Qt.NoFocus)
        tip = f"On {count} of {total} selected items" if partial else tag
        if self.ui.session.commands.exists("find.show"):
            tip += "\nClick to find everything tagged with it"
            label.setCursor(Qt.PointingHandCursor)
            label.clicked.connect(lambda _c=False, t=tag: self.find(t))
        label.setToolTip(tip)
        layout.addWidget(label)
        if partial:
            add = QToolButton(chip)
            add.setObjectName("tagAdd")
            add.setText("+")
            add.setFocusPolicy(Qt.NoFocus)
            add.setToolTip(f"Add “{tag}” to all {total} selected items")
            add.clicked.connect(lambda _c=False, t=tag: self.add_to_all(t))
            layout.addWidget(add)
        remove = QToolButton(chip)
        remove.setObjectName("tagRemove")
        remove.setText("✕")
        remove.setFocusPolicy(Qt.NoFocus)
        remove.setToolTip(f"Remove “{tag}” from the selected item{'s' if total > 1 else ''}")
        remove.clicked.connect(lambda _c=False, t=tag: self.remove(t))
        layout.addWidget(remove)
        return chip

    def _rebuild(self) -> None:
        for chip in self.chips.values():
            chip.hide()
            chip.setParent(None)
            chip.deleteLater()
        while self.flow.count():
            self.flow.takeAt(0)
        self.chips = {}
        total = len(self.names)
        for tag, count in self.counts.items():
            chip = self._chip(tag, count, total)
            self.flow.addWidget(chip)
            self.chips[tag] = chip
        self.chips_box.setVisible(bool(self.chips))
        self.flow.invalidate()

    def refresh(self, info: SelectionInfo) -> None:
        self.names = list(info.names)
        self.counts = tag_counts(info.nodes)
        state = (tuple(self.names), tuple(self.counts.items()))
        if state != self._state:
            self._state = state
            self._rebuild()
        if self.edit.hasFocus():
            self._update_completions()
