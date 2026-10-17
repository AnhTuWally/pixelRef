"""Colours and the dark stylesheet shared by every window."""

from __future__ import annotations

from PySide6.QtGui import QColor

ACCENT = QColor("#3d8ef0")
ACCENT_HEX = "#3d8ef0"
RUBBER_FILL = QColor(61, 142, 240, 40)
MUTED = QColor("#8a8f99")
TEXT_LIGHT = QColor("#d5e8ff")
PANEL_BG = QColor(20, 24, 32, 225)

STYLESHEET = """
QWidget {
    background-color: #1b1d22;
    color: #d5d8df;
    font-size: 12px;
}
QTreeWidget, QListWidget {
    background-color: #14161a;
    border: 1px solid #22252c;
    border-radius: 4px;
    show-decoration-selected: 1;
}
QTreeWidget::item {
    padding: 3px 2px;
    border-bottom: 1px solid #1c1e24;
    color: #c8cdd8;
}
QTreeWidget::item:selected, QListWidget::item:selected {
    background-color: #1a3457;
    color: #d5e8ff;
}
QTreeWidget::item:hover:!selected, QListWidget::item:hover:!selected { background-color: #1a2030; }
QHeaderView::section {
    background-color: #14161a;
    color: #606878;
    border: none;
    border-bottom: 1px solid #22252c;
    padding: 3px 4px;
    font-size: 10px;
}
QPushButton {
    background-color: #2a2e35;
    border: 1px solid #373d46;
    border-radius: 4px;
    padding: 7px;
    text-align: left;
}
QPushButton:hover { background-color: #323741; }
QPushButton:pressed, QPushButton:checked {
    background-color: #1a3457;
    border-color: #3d8ef0;
}
QLineEdit, QPlainTextEdit, QDoubleSpinBox, QSpinBox, QTextEdit {
    background-color: #14161a;
    border: 1px solid #2e3240;
    border-radius: 3px;
    padding: 2px 4px;
}
QLabel { color: #aeb4bf; }
#pathLabel { color: #606878; font-size: 10px; }
#toolBtn {
    background-color: #21242b;
    border: 1px solid #2e3240;
    border-radius: 3px;
    padding: 4px 8px;
    font-size: 11px;
    text-align: center;
}
#toolBtn:hover { background-color: #2b3040; }
#toolBtn:checked { background-color: #1a3457; border-color: #3d8ef0; }
#toolBtn[hasShortcut="true"] { text-align: left; padding-left: 6px; padding-right: 4px; }
#styleBtn {
    background-color: #21242b;
    border: 1px solid #2e3240;
    border-radius: 3px;
    padding: 0;
    font-size: 12px;
    text-align: center;
}
#styleBtn:hover { border-color: #4a5160; }
#styleBtn:checked { background-color: #1a3457; border-color: #3d8ef0; color: #ffffff; }
QComboBox {
    background-color: #14161a;
    border: 1px solid #2e3240;
    border-radius: 3px;
    padding: 2px 6px;
}
QComboBox QAbstractItemView { background-color: #1e2128; selection-background-color: #2e3440; }
QScrollArea { background: transparent; }
#menuSection {
    color: #6f7787;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
    padding: 6px 12px 2px 12px;
    background: transparent;
}
#commandSearch {
    background-color: #14161a;
    border: 1px solid #3d8ef0;
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}
#zoomButton, #barButton {
    background: transparent;
    color: #8a93a3;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 1px 6px;
    font-size: 11px;
}
#zoomButton:hover, #barButton:hover { background-color: #23262d; border-color: #2e3240; color: #d5d8df; }
#zoomButton::menu-button { border: none; width: 12px; }
#barButton:checked { background-color: #1a3457; border-color: #3d8ef0; color: #d5e8ff; }
#macroRec { background-color: #3a1619; border: 1px solid #d64545; border-radius: 4px; }
#macroRec[paused="true"] { background-color: #33290f; border-color: #d6a23d; }
#macroRecText, #macroRecButton {
    background: transparent;
    color: #f3d0d0;
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 1px 5px;
    font-size: 11px;
}
#macroRecText { font-weight: bold; }
#macroRecText::menu-indicator { image: none; width: 0; }
#macroRecText:hover, #macroRecButton:hover { background-color: rgba(255, 255, 255, 0.08); border-color: rgba(255, 255, 255, 0.16); }
QSplitter::handle { background-color: #15171b; }
QSplitter::handle:horizontal { width: 5px; }
QSplitter::handle:vertical { height: 5px; }
QSplitter::handle:hover { background-color: #3d8ef0; }
#sectionHeader {
    color: #6f7787;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
}
#sectionHint { color: #4f5562; font-size: 10px; }
#inspectorCard {
    background-color: #20232a;
    border: 1px solid #2a2e37;
    border-radius: 6px;
}
#inspectorCard QLabel { background: transparent; }
#nameEdit {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 3px;
    font-size: 13px;
    font-weight: bold;
    color: #e6e9ef;
    padding: 2px 4px;
}
#nameEdit:hover { border-color: #2e3240; }
#nameEdit:focus { border-color: #3d8ef0; background-color: #14161a; }
#cardTitle { color: #e6e9ef; font-size: 13px; font-weight: bold; }
#cardSubtitle { color: #7d8594; font-size: 11px; }
#sectionToggle {
    background: transparent;
    border: none;
    border-top: 1px solid #262a33;
    border-radius: 0;
    color: #8a93a3;
    font-size: 10px;
    font-weight: bold;
    letter-spacing: 1px;
    padding: 8px 2px 4px 2px;
    text-align: left;
}
#sectionToggle:hover { color: #d5d8df; }
#scrubLabel { color: #8a93a3; }
#compactSpin { padding: 2px 6px; }
#compactSpin::up-button, #compactSpin::down-button { width: 0px; border: none; }
#numberField { padding: 2px 6px; }
#numberField::up-button, #numberField::down-button { width: 0px; border: none; }
#numberField:hover, #compactSpin:hover { border-color: #4a5160; }
#scrubLabel:hover { color: #3d8ef0; }
#fieldCaption { color: #6f7787; font-size: 10px; font-weight: bold; }
#hexLabel { color: #c8cdd8; font-family: Consolas, "Courier New", monospace; font-size: 11px; }
#toolOptions { border: 1px solid #2a2f3a; border-radius: 5px; }
#toolOptions QLabel { color: #8a93a3; }
QLineEdit {
    background-color: #14161a;
    border: 1px solid #2e3240;
    border-radius: 3px;
    padding: 3px 6px;
}
QLineEdit:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QSpinBox:focus { border-color: #3d8ef0; }
QSlider::groove:horizontal { height: 4px; background: #2a2e35; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #3d8ef0; border-radius: 2px; }
QSlider::handle:horizontal { background: #d5d8df; width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #353a44; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #4a5160; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 0; }
QScrollBar::handle:horizontal { background: #353a44; border-radius: 4px; min-width: 24px; }
QGroupBox {
    border: 1px solid #262930;
    border-radius: 4px;
    margin-top: 4px;
    padding-top: 4px;
    font-size: 11px;
    color: #606878;
}
QGroupBox::title { subcontrol-position: top left; left: 6px; }
QMenu { background-color: #1e2128; border: 1px solid #32363f; padding: 2px; }
QMenu::item { padding: 5px 24px 5px 12px; color: #d5d8df; }
QMenu::item:selected { background-color: #2e3440; }
QMenu::item:disabled { color: #555b66; }
QMenu::separator { height: 1px; background: #32363f; margin: 2px 6px; }
QTabWidget::pane { border: none; }
QTabBar::tab {
    background: #16181c; color: #8a93a3; border: 1px solid #23262d; border-bottom: none;
    padding: 3px 10px; font-size: 11px;
}
QTabBar::tab:selected { background: #1f2229; color: #d5e8ff; border-color: #2e3240; }
/* Window manager */
#tileFrame { border: 1px solid #20232a; }
#tileFrame[focused="true"] { border: 1px solid #3d8ef0; }
#tileHeader { background-color: #16181c; border-bottom: 1px solid #22252c; }
#tileHeader[focused="true"] { background-color: #1a2233; }
#tileTitle { color: #8a93a3; font-size: 11px; font-weight: bold; background: transparent; }
#tileHeader[focused="true"] #tileTitle { color: #d5e8ff; }
#tileTitleEdit { font-size: 11px; font-weight: bold; padding: 0 4px; }
#tileHeaderButton {
    background: transparent; border: none; color: #6f7787; padding: 0 4px; font-size: 12px; text-align: center;
}
#tileHeaderButton:hover { color: #d5e8ff; }
#stackTitle {
    background-color: #16181c; border: none; border-bottom: 1px solid #22252c; border-radius: 0;
    color: #8a93a3; padding: 3px 8px; font-size: 11px; text-align: left;
}
#stackTitle:checked { background-color: #1a2233; color: #d5e8ff; }
#wmBar { background-color: #13151a; border-top: 1px solid #1d1f25; }
#wmBar QLabel { color: #6b7385; font-size: 11px; background: transparent; }
#statusLabel { color: #8a93a3; }
#modeIndicator { color: #13151a; background-color: #3d8ef0; font-weight: bold; padding: 0 6px; border-radius: 3px; }
#workspaceButton {
    background: transparent; color: #6f7787; border: 1px solid transparent; border-radius: 3px;
    padding: 1px 7px; font-size: 11px; text-align: center;
}
#workspaceButton:hover { color: #d5d8df; border-color: #2e3240; }
#workspaceButton:checked { background-color: #1a3457; color: #d5e8ff; border-color: #3d8ef0; }
#placeholderTile { color: #606878; }
#panelSidebar { background-color: #17191d; }
#panelSection { background-color: #17191d; border-bottom: 1px solid #22252c; }
#floatCard { background-color: rgba(23, 25, 29, 236); border: 1px solid #2c313b; border-radius: 6px; }
#floatCard #tileHeader { border-top-left-radius: 6px; border-top-right-radius: 6px; }
#panelPopup { background-color: #17191d; border: 1px solid #3d8ef0; }
#quickEditBar { background-color: rgba(23, 25, 29, 236); border: 1px solid #2c313b; border-radius: 6px; }
#quickEditBar QWidget { background-color: transparent; }
#quickEditBar #quickEditDivider { color: #2c313b; }
#quickEditBar QDoubleSpinBox, #quickEditBar QLineEdit, #quickEditBar QComboBox { background-color: #14161a; }
#consoleOutput, #consoleInput {
    font-family: Consolas, "Courier New", monospace; font-size: 12px; background-color: #121418;
}
/* Menu buttons in view headers: icon + what it does + one chevron */
#headerMenuButton {
    background: transparent; border: 1px solid transparent; border-radius: 3px;
    color: #9aa3b2; padding: 1px 16px 1px 4px; font-size: 11px;
}
#headerMenuButton:hover { background-color: #23262d; border-color: #2e3240; color: #d5e8ff; }
#headerMenuButton:checked { background-color: #1a3457; border-color: #3d8ef0; color: #d5e8ff; }
#tileHeaderButton:checked { color: #d5e8ff; }
/* Panel tabs on the window edge */
#panelRail { background-color: #13151a; }
#panelRail[side="right"] { border-left: 1px solid #1d1f25; }
#panelRail[side="left"] { border-right: 1px solid #1d1f25; }
"""

# Arrow images: Qt's own are a few pixels wide under a stylesheet. Filled in by stylesheet().
_ARROWS = """
QComboBox::drop-down { border: none; width: 18px; }
QComboBox::down-arrow { image: url(%(down12)s); width: 12px; height: 12px; }
QToolButton::menu-indicator, QPushButton::menu-indicator {
    image: url(%(down10)s); width: 10px; height: 10px;
    subcontrol-origin: padding; subcontrol-position: center right; right: 3px;
}
QToolButton::menu-indicator:pressed, QToolButton::menu-indicator:open { top: 0; left: 0; }
QToolButton[popupMode="2"] { padding-right: 16px; }
QMenu::right-arrow { image: url(%(right10)s); width: 10px; height: 10px; }
QTreeView::branch:has-children:closed { image: url(%(right10)s); }
QTreeView::branch:has-children:open { image: url(%(down10)s); }
QTreeView::branch:has-children:closed:hover { image: url(%(right10h)s); }
QTreeView::branch:has-children:open:hover { image: url(%(down10h)s); }
"""


def stylesheet() -> str:
    """The app's stylesheet, with the arrow images (needs a QGuiApplication)."""
    from .icons import icon_file

    muted, bright = "#8a93a3", "#d5e8ff"
    return STYLESHEET + _ARROWS % {
        "down12": icon_file("chevron_down", 12, muted),
        "down10": icon_file("chevron_down", 10, muted),
        "right10": icon_file("chevron_right", 10, muted),
        "down10h": icon_file("chevron_down", 10, bright),
        "right10h": icon_file("chevron_right", 10, bright),
    }
