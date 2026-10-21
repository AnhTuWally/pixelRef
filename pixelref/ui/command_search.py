"""Right-click menu with a search field that finds any command.

Typing filters every action in the menu — including those nested in
submenus — plus any extra commands supplied by the window, matching on the
command name, where it lives ("View › Background"), its tooltip and a list
of synonyms. Results reuse the original QAction objects, so they keep their
shortcuts, checked state and enabled state. F1 shows the documentation of the
highlighted entry (``help_handler``).
"""

from __future__ import annotations

from typing import Callable, Iterable

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QMenu, QWidget, QWidgetAction

MAX_RESULTS = 24


def _clean(text: str) -> str:
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&").strip()


class CommandSearchMenu(QMenu):
    # Called with the highlighted action when F1 is pressed; True if it showed its documentation.
    help_handler: Callable[[QAction], bool] | None = None

    def __init__(self, parent: QWidget | None = None, extra_actions: Iterable[QAction] = ()) -> None:
        super().__init__(parent)
        self.setObjectName("commandMenu")
        self._extra = [a for a in extra_actions if a is not None]
        self._originals: list[QAction] = []
        self._results: list[QAction] = []
        self._headers: list[QAction] = []
        self._entries: list[tuple[str, QAction]] = []

        holder = QWidget(self)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(6, 6, 6, 4)
        self.field = QLineEdit(holder)
        self.field.setObjectName("commandSearch")
        self.field.setPlaceholderText("Search commands…   (Ctrl+K · F1 help)")
        self.field.setClearButtonEnabled(True)
        self.field.setMinimumWidth(260)
        self.field.textChanged.connect(self.search)
        self.field.installEventFilter(self)
        layout.addWidget(self.field)
        self._search_action = QWidgetAction(self)
        self._search_action.setDefaultWidget(holder)
        self._no_results = QAction("No matching commands", self)
        self._no_results.setEnabled(False)

    # ---- Setup ------------------------------------------------------------------------

    def finish(self) -> None:
        """Call after the normal menu items are added: inserts the search field."""
        actions = self.actions()
        first = actions[0] if actions else None
        self.insertAction(first, self._search_action)
        if first is not None:
            self.insertSeparator(first)
        self._originals = [a for a in self.actions() if a is not self._search_action]
        self._entries = self._collect()

    def _collect(self) -> list[tuple[str, QAction]]:
        entries: list[tuple[str, QAction]] = []
        seen: set[int] = set()

        def walk(menu: QMenu, path: str) -> None:
            for action in menu.actions():
                if action is self._search_action:
                    continue
                submenu = action.menu()
                if submenu is not None:
                    title = _clean(submenu.title() or action.text())
                    walk(submenu, f"{path} › {title}" if path else title)
                    continue
                if action.isSeparator() or not _clean(action.text()) or id(action) in seen:
                    continue
                seen.add(id(action))
                entries.append((path, action))

        walk(self, "")
        for action in self._extra:
            if id(action) not in seen and _clean(action.text()):
                seen.add(id(action))
                entries.append(("All commands", action))
        return entries

    # ---- Searching -----------------------------------------------------------------------

    @staticmethod
    def score(query: str, path: str, action: QAction) -> int:
        """0 = no match; higher = better. Every query word must match somewhere."""
        name = _clean(action.text()).lower()
        keywords = str(action.property("keywords") or "").lower()
        tip = (action.statusTip() or action.toolTip() or "").lower()
        haystack = " ".join((name, path.lower(), keywords, tip))
        total = 0
        for word in query.lower().split():
            if word in name:
                total += 30 if any(part.startswith(word) for part in name.split()) else 15
            elif word in keywords:
                total += 8
            elif word in haystack:
                total += 3
            else:
                return 0
        if name.startswith(query.lower()):
            total += 40
        return total

    def search(self, text: str) -> None:
        for action in self._results + self._headers + [self._no_results]:
            self.removeAction(action)
        for header in self._headers:
            header.deleteLater()
        self._results, self._headers = [], []
        query = text.strip()
        if not query:
            for action in self._originals:
                self.addAction(action)
            self.setActiveAction(None)
            return
        for action in self._originals:
            self.removeAction(action)
        ranked = sorted(
            ((self.score(query, path, action), index, path, action) for index, (path, action) in enumerate(self._entries)
             if action.isVisible()),
            key=lambda entry: (-entry[0], entry[1]),
        )
        matches = [(path, action) for score, _i, path, action in ranked if score > 0][:MAX_RESULTS]
        if not matches:
            self.addAction(self._no_results)
            return
        groups: dict[str, list[QAction]] = {}
        for path, action in matches:
            groups.setdefault(path or "Menu", []).append(action)
        for path, actions in groups.items():
            self.addAction(self._make_header(path))
            for action in actions:
                self.addAction(action)
                self._results.append(action)
        first_enabled = next((a for a in self._results if a.isEnabled()), None)
        if first_enabled is not None:
            self.setActiveAction(first_enabled)

    def _make_header(self, text: str) -> QWidgetAction:
        """Group title in the results (menu sections lose their text under stylesheets)."""
        label = QLabel(text.upper(), self)
        label.setObjectName("menuSection")
        header = QWidgetAction(self)
        header.setDefaultWidget(label)
        header.setEnabled(False)
        header.setProperty("header_text", text)
        self._headers.append(header)
        return header

    def results(self) -> list[QAction]:
        return list(self._results)

    def header_texts(self) -> list[str]:
        return [str(h.property("header_text")) for h in self._headers]

    # ---- Keyboard ---------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        QTimer.singleShot(0, self.field.setFocus)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.field and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.trigger_active()
                return True
            if key in (Qt.Key_Down, Qt.Key_Up):
                self._move(1 if key == Qt.Key_Down else -1)
                return True
            if key == Qt.Key_Escape and self.field.text():
                self.field.clear()
                return True
            if key == Qt.Key_F1:
                self.show_help()
                return True
        if obj is self.field and event.type() == QEvent.ShortcutOverride and event.key() == Qt.Key_F1:
            event.accept()  # F1 is ours here (not the Keyboard Shortcuts window's)
            return True
        return super().eventFilter(obj, event)

    def _move(self, step: int) -> None:
        candidates = [a for a in (self._results or self._originals) if a.isEnabled() and not a.isSeparator()]
        if not candidates:
            return
        current = self.activeAction()
        index = candidates.index(current) if current in candidates else (-1 if step > 0 else 0)
        self.setActiveAction(candidates[(index + step) % len(candidates)])

    def show_help(self) -> None:
        """Show the documentation of the highlighted entry (or the first result)."""
        action = self.activeAction()
        if action is None or action is self._search_action:
            action = next(iter(self._results), None)
        if action is None or self.help_handler is None:
            return
        self.close()
        self.help_handler(action)

    def trigger_active(self) -> None:
        action = self.activeAction()
        if action is None or action is self._search_action or not action.isEnabled():
            action = next((a for a in self._results if a.isEnabled()), None)
        if action is None:
            return
        self.close()
        if action.menu() is None:
            action.trigger()
