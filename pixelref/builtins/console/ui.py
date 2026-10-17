"""The Script Editor view."""

from __future__ import annotations

import keyword
import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRegularExpression, QStringListModel, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QCompleter,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pixelref.plugin_api.ui import ViewType

from . import ConsoleEngine, user_setup_paths

MAX_HISTORY = 200
MAX_LINE = 10_000  # longer output lines are clipped: laying out a huge line freezes the UI
COLORS = {"output": "#c8cdd8", "error": "#ff8080", "echo": "#7fb2f0", "info": "#6f7787", "input": "#9ab87a"}


class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, document: Any) -> None:
        super().__init__(document)

        def fmt(color: str, bold: bool = False) -> QTextCharFormat:
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Bold)
            return f

        self.rules = [
            (QRegularExpression(r"\b(" + "|".join(keyword.kwlist) + r")\b"), fmt("#c586c0", True)),
            (QRegularExpression(r"\bcmds\b|\bpr\b|\bsel\b"), fmt("#4ec9b0")),
            (QRegularExpression(r"\b[0-9]+(\.[0-9]+)?\b"), fmt("#b5cea8")),
            (QRegularExpression(r"\"[^\"\\]*(\\.[^\"\\]*)*\"|'[^'\\]*(\\.[^'\\]*)*'"), fmt("#ce9178")),
            (QRegularExpression(r"#[^\n]*"), fmt("#6a9955")),
        ]

    def highlightBlock(self, text: str) -> None:  # noqa: N802
        for pattern, style in self.rules:
            iterator = pattern.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), style)


class ScriptEditor(QPlainTextEdit):
    """A tab of Python. Ctrl+Enter / keypad Enter runs; Tab or Ctrl+Space completes."""

    def __init__(self, console: "ConsoleView") -> None:
        super().__init__()
        self.console = console
        self.setObjectName("consoleInput")
        self.setTabStopDistance(32)
        self.setPlaceholderText("pxcmd.ls(type='image')   — Ctrl+Enter runs (selection or all) · Tab completes")
        PythonHighlighter(self.document())
        self.completer = QCompleter(self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.activated.connect(self.insert_completion)
        self.setAcceptDrops(True)

    # ---- Completion ------------------------------------------------------------------

    def _prefix(self) -> str:
        cursor = self.textCursor()
        line = cursor.block().text()[: cursor.positionInBlock()]
        match = re.search(r"[\w.\"']*$", line)
        return match.group(0) if match else ""

    def candidates(self, prefix: str) -> list[str]:
        session = self.console.ui.session
        stripped = prefix.lstrip("\"'")
        if prefix.startswith(("pxcmd.", "pixelref.pxcmd.")):
            base = prefix.split("pxcmd.", 1)[1]
            namespace, _dot, _part = base.rpartition(".")
            names = session.commands.children(namespace)
            # Keep what was typed before the command path ("pixelref.pxcmd.") so
            # the completer's prefix still matches.
            head = prefix[: len(prefix) - len(base)] + (namespace + "." if namespace else "")
            return [head + n for n in names]
        if prefix[:1] in ("'", '"'):
            if "." in stripped:
                node, _dot, _part = stripped.partition(".")
                if session.scene.has(node):
                    try:
                        attrs = session.run("attrs", node)
                    except Exception:  # noqa: BLE001
                        attrs = []
                    return [prefix[0] + f"{node}.{a}" for a in attrs]
            return [prefix[0] + n for n in session.scene.nodes]
        words = list(self.console.engine.namespace) + keyword.kwlist + ["pxcmd", "pxnode", "sel"]
        return sorted(set(w for w in words if not w.startswith("__")))

    def complete(self) -> bool:
        prefix = self._prefix()
        if not prefix:
            return False
        options = self.candidates(prefix)
        if not options:
            return False
        self.completer.setModel(QStringListModel(options, self.completer))
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount() == 0:
            return False
        rect = self.cursorRect()
        rect.setWidth(260)
        self.completer.complete(rect)
        return True

    def insert_completion(self, text: str) -> None:
        prefix = self.completer.completionPrefix()
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(prefix))
        cursor.insertText(text)
        self.setTextCursor(cursor)

    # ---- Keys --------------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        if self.completer.popup().isVisible() and event.key() in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Tab,
                                                                  Qt.Key_Escape):
            event.ignore()
            return
        run = (event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier) or (
            event.key() == Qt.Key_Enter and event.modifiers() & Qt.KeypadModifier)
        if run:
            self.console.run_editor(self)
            event.accept()
            return
        if event.key() == Qt.Key_Space and event.modifiers() & Qt.ControlModifier:
            self.complete()
            event.accept()
            return
        if event.key() == Qt.Key_Tab and not event.modifiers() and self._prefix() and self.complete():
            event.accept()
            return
        if event.modifiers() & Qt.ControlModifier and event.key() in (Qt.Key_Up, Qt.Key_Down):
            self.console.step_history(self, -1 if event.key() == Qt.Key_Up else 1)
            event.accept()
            return
        super().keyPressEvent(event)

    # ---- Drop a .py file to run it ------------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001
        if event.mimeData().hasUrls() and any(u.toLocalFile().endswith(".py") for u in event.mimeData().urls()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # noqa: ANN001
        scripts = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile().endswith(".py")] \
            if event.mimeData().hasUrls() else []
        if scripts:
            event.acceptProposedAction()
            for script in scripts:
                self.console.engine.run_file(script)
            return
        super().dropEvent(event)


class ConsoleView(QWidget):
    def __init__(self, ui: Any, service: "ConsoleService") -> None:
        super().__init__()
        self.ui = ui
        self.service = service
        self.engine = service.engine
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        splitter = QSplitter(Qt.Vertical, self)
        self.output = QPlainTextEdit(splitter)
        self.output.setObjectName("consoleOutput")
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(5000)
        self.tabs = QTabWidget(splitter)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        splitter.addWidget(self.output)
        splitter.addWidget(self.tabs)
        splitter.setSizes([300, 200])
        layout.addWidget(splitter, 1)
        row = QHBoxLayout()
        self.run_btn = QPushButton("Run (Ctrl+Enter)", self)
        self.run_btn.clicked.connect(lambda: self.run_editor(self.current_editor()))
        self.clear_btn = QPushButton("Clear output", self)
        self.clear_btn.clicked.connect(self.output.clear)
        self.new_tab_btn = QPushButton("New tab", self)
        self.new_tab_btn.clicked.connect(lambda: self.add_tab(""))
        self.echo_check = QCheckBox("Echo all commands", self)
        self.echo_check.setChecked(service.echo)
        self.echo_check.toggled.connect(service.set_echo)
        for widget in (self.run_btn, self.clear_btn, self.new_tab_btn):
            widget.setObjectName("toolBtn")
            row.addWidget(widget)
        row.addStretch()
        row.addWidget(self.echo_check)
        layout.addLayout(row)
        self.history_index = len(service.history)
        for text in service.saved_tabs() or [""]:
            self.add_tab(text)
        for text, kind in service.backlog[-300:]:
            self.append(text, kind)

    def add_tab(self, text: str) -> ScriptEditor:
        editor = ScriptEditor(self)
        editor.setPlainText(text)
        self.tabs.addTab(editor, f"Python {self.tabs.count() + 1}")
        self.tabs.setCurrentWidget(editor)
        return editor

    def _close_tab(self, index: int) -> None:
        if self.tabs.count() > 1:
            widget = self.tabs.widget(index)
            self.tabs.removeTab(index)
            widget.deleteLater()

    def current_editor(self) -> ScriptEditor:
        return self.tabs.currentWidget()

    def editors(self) -> list[ScriptEditor]:
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def append(self, text: str, kind: str = "output") -> None:
        text = _clip_long_lines(text)
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(COLORS.get(kind, COLORS["output"])))
        cursor.insertText(text, fmt)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    def run_editor(self, editor: ScriptEditor) -> None:
        cursor = editor.textCursor()
        source = cursor.selectedText().replace("\u2029", "\n") if cursor.hasSelection() else editor.toPlainText()
        self.service.run(source)
        self.history_index = len(self.service.history)

    def step_history(self, editor: ScriptEditor, step: int) -> None:
        history = self.service.history
        if not history:
            return
        self.history_index = max(0, min(len(history), self.history_index + step))
        editor.setPlainText(history[self.history_index] if self.history_index < len(history) else "")


def _clip_long_lines(text: str) -> str:
    if len(text) <= MAX_LINE:
        return text
    return "\n".join(line if len(line) <= MAX_LINE else
                     f"{line[:MAX_LINE]} … [{len(line) - MAX_LINE} more characters]" for line in text.split("\n"))


def _tab_index(path: Path) -> tuple[int, str]:
    """Numeric tab order: console_tab_10 comes after console_tab_9, not after _1."""
    digits = path.stem.rpartition("_")[2]
    return (int(digits) if digits.isdigit() else 0, path.name)


class ConsoleService:
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.engine = ConsoleEngine(ui.session, ui.app)
        self.views: list[ConsoleView] = []
        self.backlog: list[tuple[str, str]] = []
        stored = ui.settings.value("history", [])
        self.history: list[str] = [stored] if isinstance(stored, str) else list(stored or [])
        self.echo = bool(ui.settings.value("echo", True, type=bool))
        self.engine.writers.append(self.write)

    def write(self, text: str, kind: str = "output") -> None:
        self.backlog.append((text, kind))
        del self.backlog[:-1000]
        for view in list(self.views):
            try:
                view.append(text, kind)
            except RuntimeError:
                self.views.remove(view)

    def set_echo(self, on: bool) -> None:
        self.echo = bool(on)
        self.ui.settings.setValue("echo", self.echo)

    def on_echo(self, text: str) -> None:
        if self.echo and not self.engine.running:
            self.write(text + "\n", "echo")

    def run(self, source: str) -> Any:
        if not source.strip():
            return None
        self.write("".join(f">>> {line}\n" if i == 0 else f"... {line}\n"
                           for i, line in enumerate(source.rstrip().splitlines())), "input")
        if not self.history or self.history[-1] != source:
            self.history.append(source)
            del self.history[:-MAX_HISTORY]
            self.ui.settings.setValue("history", self.history)
        return self.engine.execute(source)

    # ---- Tabs persisted as files in the user script folder ------------------------------

    def scripts_dir(self) -> Path:
        return self.ui.app.config_dir() / "scripts"

    def saved_tabs(self) -> list[str]:
        if not getattr(self.ui.app, "load_user_files", True):
            return []
        folder = self.scripts_dir()
        files = sorted(folder.glob("console_tab_*.py"), key=_tab_index) if folder.is_dir() else []
        return [f.read_text(encoding="utf-8") for f in files]

    def save_tabs(self) -> None:
        if not self.views or not getattr(self.ui.app, "load_user_files", True):
            return
        folder = self.scripts_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.glob("console_tab_*.py"):
                old.unlink()
            for index, editor in enumerate(self.views[0].editors()):
                (folder / f"console_tab_{index + 1}.py").write_text(editor.toPlainText(), encoding="utf-8")
        except OSError:
            pass


def activate_console_ui(ui: Any) -> None:
    service = ConsoleService(ui)
    ui.provide_service("console", service)
    ui.track(ui.session.commands.on_echo(service.on_echo))

    def on_error(event: Any) -> None:
        error = event.payload.get("error")
        service.write(f"# {event.payload.get('command')}: {type(error).__name__}: {error}\n", "error")

    ui.on_ui("command.error", on_error)

    def make(_tile: str, _state: dict[str, Any]) -> ConsoleView:
        view = ConsoleView(ui, service)
        service.views.append(view)
        return view

    def dispose(widget: QWidget) -> None:
        if widget in service.views:
            service.save_tabs()
            service.views.remove(widget)

    ui.view_type(ViewType(id="console", title="Script Editor", icon="console", factory=make, dispose=dispose,
                          placement="scratchpad", min_width=320))
    ui.contribute("wm.scratchpad", {"tile": "console", "name": "Console", "title": "Script Editor"})
    ui.on_ui("app.closing", lambda _e: service.save_tabs())

    def toggle() -> None:
        """Show or hide the Script Editor (it lives in the scratchpad)."""
        tiles = ui.wm.tiles_of_type("console")
        if not tiles:
            ui.app.run_command("wm.open_view", "console", placement="scratchpad")
            return
        scratch = ui.wm.doc.scratchpad()
        if ui.wm.doc.workspace_of(tiles[0]) == scratch:
            ui.app.run_command("wm.scratchpad_show", tiles[0])
        else:
            ui.app.run_command("wm.focus", tile=tiles[0])

    def run_script(path: str) -> None:
        """Run a Python file in the console's namespace (one undo step)."""
        service.engine.run_file(path)

    ui.command("toggle", toggle, undoable=False, echo=False)
    ui.command("run_file", run_script, alias="run_script", undoable=False)
    ui.action("console.toggle", "console.toggle", "Script Editor", icon="console",
              keywords="python console script terminal command line", category="Window")
    ui.bind("Ctrl+`", "console.toggle")
    ui.menu("canvas", "console.toggle", path="Window", group="window.extras", order=0)

    def user_setup() -> None:
        if not getattr(ui.app, "load_user_files", True):
            return
        for path in user_setup_paths(ui.app.config_dir()):
            try:
                service.engine.run_file(path)
            except Exception as exc:  # noqa: BLE001
                service.write(f"# userSetup failed: {exc}\n", "error")

    QTimer.singleShot(0, user_setup)
