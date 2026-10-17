"""What you see of macro recording: the record button / REC pill in each window's bar, the
red frame around every canvas while recording, and the dialog that saves the macro."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pixelref.plugin_api.ui import icon

RED = "#e04848"
RED_DIM = "#6e2a2c"
AMBER = "#d6a23d"
STEPS_IN_MENU = 20


def elapsed_text(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def steps_text(count: int) -> str:
    return f"{count} step" if count == 1 else f"{count} steps"


class MacroBlock(QWidget):
    """A record button in the window bar; while recording, a REC pill with the time, the step
    count (click for the steps), pause, stop & save and discard."""

    def __init__(self, ui: Any, recording: Callable[[], Any]) -> None:
        super().__init__()
        self.ui = ui
        self.recording = recording
        self.blink = False
        self.setObjectName("macroBlock")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.record_btn = QToolButton(self)
        self.record_btn.setObjectName("barButton")
        self.record_btn.setIcon(icon("record", "#c96a6a"))
        self.record_btn.setToolTip("Record a macro (Ctrl+Alt+R)\nEverything you do is saved as a script you can "
                                   "replay from the Scripts menu or a key")
        self.record_btn.clicked.connect(lambda: self.run("macros.record"))

        self.pill = QFrame(self)
        self.pill.setObjectName("macroRec")
        pill = QHBoxLayout(self.pill)
        pill.setContentsMargins(6, 0, 1, 0)
        pill.setSpacing(0)
        self.dot = QLabel(self.pill)
        self.dot.setFixedSize(10, 10)
        self.text_btn = QToolButton(self.pill)
        self.text_btn.setObjectName("macroRecText")
        self.text_btn.setPopupMode(QToolButton.InstantPopup)
        self.text_btn.setToolTip("Recording a macro: click to see the recorded steps")
        menu = QMenu(self.text_btn)
        menu.aboutToShow.connect(lambda: self.fill_menu(menu))
        self.text_btn.setMenu(menu)
        self.pause_btn = self.pill_button("pause", "Pause recording: what you do now is not recorded",
                                          "macros.pause")
        self.stop_btn = self.pill_button("stop", "Stop and save the macro (Ctrl+Alt+R)", "macros.record")
        self.stop_btn.setText("Save")
        self.stop_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.discard_btn = self.pill_button("close", "Discard the recording", None)
        self.discard_btn.clicked.connect(self.discard)
        pill.addWidget(self.dot)
        for widget in (self.text_btn, self.pause_btn, self.stop_btn, self.discard_btn):
            widget.setFocusPolicy(Qt.NoFocus)
            pill.addWidget(widget)
        self.record_btn.setFocusPolicy(Qt.NoFocus)
        layout.addWidget(self.record_btn)
        layout.addWidget(self.pill)
        self.refresh()

    def pill_button(self, icon_name: str, tip: str, command: str | None) -> QToolButton:
        button = QToolButton(self.pill)
        button.setObjectName("macroRecButton")
        button.setIcon(icon(icon_name, "#f3d0d0"))
        button.setToolTip(tip)
        if command:
            button.clicked.connect(lambda: self.run(command))
        return button

    def run(self, command: str) -> None:
        self.ui.app.run_command(command)

    def discard(self) -> None:
        rec = self.recording()
        if rec is None:
            return
        if rec.lines:
            answer = QMessageBox.question(self.window(), "Discard Macro",
                                          f"Throw away the {steps_text(len(rec.lines))} recorded so far?")
            if answer != QMessageBox.Yes:
                return
        self.run("macros.discard")

    def fill_menu(self, menu: QMenu) -> None:
        menu.clear()
        rec = self.recording()
        if rec is None:
            return
        lines = rec.lines
        if not lines:
            menu.addAction("Nothing recorded yet: work as usual").setEnabled(False)
        else:
            hidden = len(lines) - STEPS_IN_MENU
            if hidden > 0:
                menu.addAction(f"… {hidden} earlier").setEnabled(False)
            mono = QFont("Consolas")
            mono.setStyleHint(QFont.Monospace)
            for number, line in list(enumerate(lines, start=1))[-STEPS_IN_MENU:]:
                text = line if len(line) <= 70 else line[:67] + "…"
                action = menu.addAction(f"{number}.  {text}")
                action.setFont(mono)
                action.setEnabled(False)
            menu.addSeparator()
            menu.addAction(icon("undo"), "Remove Last Step", lambda: self.run("macros.remove_last"))
        menu.addAction(icon("play" if rec.paused else "pause"), "Resume" if rec.paused else "Pause",
                       lambda: self.run("macros.pause"))
        menu.addAction(icon("stop"), "Stop && Save…", lambda: self.run("macros.record"))
        menu.addAction(icon("close"), "Discard…", self.discard)

    def refresh(self) -> None:
        rec = self.recording()
        self.record_btn.setVisible(rec is None)
        self.pill.setVisible(rec is not None)
        if rec is None:
            return
        if self.pill.property("paused") != rec.paused:
            self.pill.setProperty("paused", rec.paused)
            self.pill.style().unpolish(self.pill)
            self.pill.style().polish(self.pill)
        self.pause_btn.setIcon(icon("play" if rec.paused else "pause", "#f3d0d0"))
        self.pause_btn.setToolTip("Resume recording" if rec.paused
                                  else "Pause recording: what you do now is not recorded")
        self.tick()

    def tick(self) -> None:
        """Called twice a second while recording: blink the dot, update the time."""
        rec = self.recording()
        if rec is None:
            return
        self.blink = not self.blink
        color = AMBER if rec.paused else (RED if self.blink else RED_DIM)
        self.dot.setPixmap(icon("isolate" if rec.paused else "record", color).pixmap(10, 10))
        label = "PAUSED" if rec.paused else "REC"
        self.text_btn.setText(f"{label}  {elapsed_text(rec.elapsed())} · {steps_text(len(rec.lines))}")


def paint_recording(view: Any, painter: QPainter, rec: Any) -> None:
    """A frame around the canvas and a small label in its corner while a macro is recorded."""
    if rec is None:
        return
    viewport = view.viewport()
    width, height = viewport.width(), viewport.height()
    color = QColor(AMBER if rec.paused else RED)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(color, 2.0)
    if rec.paused:
        pen.setStyle(Qt.DashLine)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    painter.drawRect(QRectF(1.0, 1.0, width - 2.0, height - 2.0))

    text = f"Paused · {steps_text(len(rec.lines))}" if rec.paused else f"Recording macro · {steps_text(len(rec.lines))}"
    metrics = painter.fontMetrics()
    rect = QRectF(10.0, height - metrics.height() - 22.0, metrics.horizontalAdvance(text) + 32.0, metrics.height() + 10.0)
    painter.setPen(QPen(color, 1.0))
    background = QColor(color)
    background.setAlpha(50)
    painter.setBrush(QColor(20, 22, 28, 220))
    painter.drawRoundedRect(rect, 5, 5)
    painter.setBrush(background)
    painter.drawRoundedRect(rect, 5, 5)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(rect.left() + 12.0 - 4.0, rect.center().y() - 4.0, 8.0, 8.0)
    painter.setPen(QColor("#f3e3e3"))
    painter.drawText(rect.adjusted(22.0, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
    painter.restore()


class SaveMacroDialog(QDialog):
    """Name the macro, untick steps you don't want, optionally give it a key."""

    KEEP = 2  # result code: go on recording

    def __init__(self, lines: list[str], name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Save Macro")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Name"))
        self.name_edit = QLineEdit(name, self)
        self.name_edit.selectAll()
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel(f"Recorded steps ({len(lines)}): untick any you don't want"))
        self.list = QListWidget(self)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.list.setFont(mono)
        for line in lines:
            item = QListWidgetItem(line, self.list)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setToolTip(line)
        self.list.itemChanged.connect(lambda _item: self.update_save())
        layout.addWidget(self.list, 1)

        self.shortcut_box = QCheckBox("Give it a keyboard shortcut after saving", self)
        layout.addWidget(self.shortcut_box)

        buttons = QDialogButtonBox(self)
        self.save_btn = buttons.addButton("Save", QDialogButtonBox.AcceptRole)
        keep = buttons.addButton("Keep Recording", QDialogButtonBox.ActionRole)
        discard = buttons.addButton("Discard", QDialogButtonBox.DestructiveRole)
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self.accept)
        keep.clicked.connect(lambda: self.done(self.KEEP))
        discard.clicked.connect(self.confirm_discard)
        layout.addWidget(buttons)
        self.name_edit.textChanged.connect(lambda _t: self.update_save())
        self.update_save()

    def reject(self) -> None:  # Esc / the window's close button keep the recording going
        self.done(self.KEEP)

    def confirm_discard(self) -> None:
        answer = QMessageBox.question(self, "Discard Macro", "Throw away this recording?")
        if answer == QMessageBox.Yes:
            self.done(QDialog.Rejected)

    def update_save(self) -> None:
        self.save_btn.setEnabled(bool(self.name().strip()) and bool(self.lines()))

    def name(self) -> str:
        return self.name_edit.text()

    def lines(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]
