"""Panels: Tools, Outliner, Attribute Editor — always at hand, outside the tiling layout.

A panel is a widget a plugin registers with :class:`PanelType`. Each panel has
a *mode*, chosen by the user and remembered:

* ``docked``   — a section of the **sidebar** next to the tiling area of the
  main window (the same in every workspace; drag its edge to resize, Tab hides).
* ``floating`` — a **card floating over the canvas**: drag its title to move
  it, its corner to resize, collapse it to its title.
* ``hidden``   — not on screen.

The **panel tabs** on the window's edge (next to the sidebar) show every panel:
click one to show or hide it, right-click for its modes.

Whatever the mode, a panel can **pop up** on demand (``Alt+T`` tools,
``Alt+O`` outliner, ``Alt+A`` / ``Alt+Enter`` attribute editor): at the
cursor, or next to the selection. A pop-up closes when you click back on the
canvas or press Esc; its pin button turns the panel into a floating card.

Workspaces whose ``window_style`` has ``"panels": false`` (Present)
show neither the sidebar nor floating cards — pop-ups still work there.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.extensions import Disposable
from .icons import ICON_COLOR, icon, icon_pixmap

if TYPE_CHECKING:
    from .app import App

logger = logging.getLogger(__name__)

MODES = ("docked", "floating", "hidden")
QWIDGETSIZE_MAX = 16777215
SIDEBAR_DEFAULT_WIDTH = 300
SIDEBAR_MIN_WIDTH = 220
SIDEBAR_MAX_WIDTH = 640
DEFAULT_SIDE = "right"


@dataclass
class PanelType:
    """A panel a plugin offers (see the module docstring)."""

    id: str
    title: str
    factory: Callable[[QWidget | None], QWidget]  # parent -> the panel widget; called once per host
    icon: str = ""
    color: str = ""  # accent of its header and tab, so each panel is recognisable at a glance
    order: float = 50.0  # top to bottom in the sidebar
    default_mode: str = "docked"
    dispose: Callable[[QWidget], None] | None = None
    popup_keys: tuple[str, ...] = ()  # default keys that pop the panel up
    popup_anchor: str = "cursor"  # "cursor" or "selection"
    popup_size: tuple[int, int] = (320, 440)
    popup_closes_on: tuple[str, ...] = ()  # UI events that close the pop-up (tools: "tool.changed")
    float_size: tuple[int, int] = (300, 380)
    stretch: int = 1  # share of the sidebar's height (0: as tall as its content needs)
    keywords: str = ""
    plugin: str | None = None


def _tool_button(icon_name: str, tip: str, parent: QWidget) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName("tileHeaderButton")
    button.setIcon(icon(icon_name, "#8a93a3"))
    button.setIconSize(QSize(14, 14))
    button.setToolTip(tip)
    button.setFocusPolicy(Qt.NoFocus)
    button.setAutoRaise(True)
    return button


def panel_color(ptype: PanelType) -> str:
    return ptype.color or ICON_COLOR


class PanelHeader(QFrame):
    """Title row shared by sidebar sections, floating cards and pop-ups: a chevron
    (collapse), the panel's icon and a stripe in its colour, its title, buttons."""

    def __init__(self, ptype: PanelType, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("tileHeader")
        self.setFixedHeight(26)
        if ptype.color:
            self.setStyleSheet(f"#tileHeader {{ border-left: 3px solid {ptype.color}; }}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 2, 0)
        layout.setSpacing(4)
        self.arrow = QLabel(self)
        self.arrow.setFixedSize(12, 12)
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(16, 16)
        if ptype.icon:
            self.icon_label.setPixmap(icon_pixmap(ptype.icon, 16, panel_color(ptype)))
        self.title = QLabel(ptype.title, self)
        self.title.setObjectName("tileTitle")
        layout.addWidget(self.arrow)
        layout.addWidget(self.icon_label)
        layout.addWidget(self.title)
        layout.addStretch(1)
        self.buttons = layout
        self.set_arrow(False)

    def set_arrow(self, collapsed: bool) -> None:
        self.arrow.setPixmap(icon_pixmap("chevron_right" if collapsed else "chevron_down", 12, "#8a93a3"))

    def add_button(self, icon_name: str, tip: str, callback: Callable[[], None]) -> QToolButton:
        button = _tool_button(icon_name, tip, self)
        button.clicked.connect(lambda _checked=False: callback())
        self.buttons.addWidget(button)
        return button


# ---- Sidebar ---------------------------------------------------------------------------------------


class PanelSection(QFrame):
    """One docked panel in the sidebar: a collapsible header over the panel."""

    def __init__(self, manager: "PanelManager", ptype: PanelType) -> None:
        super().__init__(None)
        self.manager = manager
        self.ptype = ptype
        self.setObjectName("panelSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = PanelHeader(ptype, self)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setToolTip("Click to collapse or expand")
        self.header.add_button("float", "Float over the canvas", lambda: manager.set_mode(ptype.id, "floating"))
        self.header.add_button("close", "Hide (its tab on the window edge brings it back)",
                               lambda: manager.set_mode(ptype.id, "hidden"))
        self.header.mousePressEvent = self._header_pressed  # type: ignore[method-assign]
        layout.addWidget(self.header)
        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.body, 1)
        self.widget: QWidget | None = None
        self.set_collapsed(ptype.id in manager.collapsed)

    def set_widget(self, widget: QWidget) -> None:
        if self.widget is widget:
            return
        self.widget = widget
        widget.setParent(self.body)
        self.body_layout.addWidget(widget)
        widget.show()

    def take_widget(self) -> QWidget | None:
        widget, self.widget = self.widget, None
        if widget is not None:
            self.body_layout.removeWidget(widget)
        return widget

    @property
    def collapsed(self) -> bool:
        return not self.body.isVisibleTo(self)

    def set_collapsed(self, collapsed: bool) -> None:
        self.body.setVisible(not collapsed)
        self.header.set_arrow(collapsed)
        self.setMaximumHeight(self.header.height() if collapsed else QWIDGETSIZE_MAX)

    def _header_pressed(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton:
            self.manager.set_collapsed(self.ptype.id, not self.collapsed)


class Sidebar(QFrame):
    """The panel column of the main window (outside the tiling area)."""

    def __init__(self, manager: "PanelManager") -> None:
        super().__init__(None)
        self.manager = manager
        self.setObjectName("panelSidebar")
        self.setMinimumWidth(SIDEBAR_MIN_WIDTH)
        self.setMaximumWidth(SIDEBAR_MAX_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Vertical, self)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.splitterMoved.connect(lambda *_a: manager.remember_section_sizes())
        layout.addWidget(self.splitter)
        self.sections: dict[str, PanelSection] = {}

    def set_sections(self, sections: list[PanelSection]) -> None:
        for index, section in enumerate(sections):
            if self.splitter.indexOf(section) != index:
                self.splitter.insertWidget(index, section)
            section.show()
        self.sections = {s.ptype.id: s for s in sections}
        for index, section in enumerate(sections):
            self.splitter.setStretchFactor(index, section.ptype.stretch)
        self.distribute()

    def distribute(self) -> None:
        """Remembered section heights, else: content-sized sections first, the rest by stretch."""
        sections = [s for s in self.sections.values() if self.splitter.indexOf(s) >= 0 and not s.isHidden()]
        sections.sort(key=lambda s: self.splitter.indexOf(s))
        total = self.splitter.height()
        if not sections or total < 100:
            return
        stored = self.manager.section_sizes()
        if stored and all(s.ptype.id in stored for s in sections):
            sizes = [int(stored[s.ptype.id]) for s in sections]
        else:
            fixed = {s.ptype.id: min(total // 3, s.sizeHint().height() if s.widget is None else
                                      s.header.height() + s.widget.sizeHint().height())
                     for s in sections if s.ptype.stretch == 0}
            rest = max(0, total - sum(fixed.values()))
            weights = sum(max(1, s.ptype.stretch) for s in sections if s.ptype.stretch) or 1
            sizes = [fixed[s.ptype.id] if s.ptype.id in fixed else rest * s.ptype.stretch // weights
                     for s in sections]
        self.splitter.setSizes(sizes)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        first = event.oldSize().height() < 100 <= event.size().height()
        super().resizeEvent(event)
        if first:
            self.distribute()


# ---- Floating cards ------------------------------------------------------------------------------------


class _Grip(QWidget):
    """Bottom-right resize handle of a floating card."""

    def __init__(self, card: "FloatCard") -> None:
        super().__init__(card)
        self.card = card
        self.setFixedSize(14, 14)
        self.setCursor(Qt.SizeFDiagCursor)
        self._start: tuple[QPointF, QSize] | None = None

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        from PySide6.QtGui import QColor, QPainter

        painter = QPainter(self)
        painter.setPen(QColor("#5a6272"))
        for offset in (4, 8, 12):
            painter.drawLine(self.width() - 1, self.height() - offset, self.width() - offset, self.height() - 1)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        self._start = (event.globalPosition(), self.card.size())

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._start is not None:
            start, size = self._start
            delta = event.globalPosition() - start
            self.card.resize(max(200, int(size.width() + delta.x())), max(80, int(size.height() + delta.y())))
            self.card.clamp()

    def mouseReleaseEvent(self, _event) -> None:  # noqa: ANN001, N802
        self._start = None
        self.card.manager.remember_card(self.card)


class FloatCard(QFrame):
    """A panel floating over the tiling area of the main window."""

    def __init__(self, manager: "PanelManager", ptype: PanelType, area: QWidget) -> None:
        super().__init__(area)
        self.manager = manager
        self.ptype = ptype
        self.setObjectName("floatCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.header = PanelHeader(ptype, self)
        self.header.setCursor(Qt.SizeAllCursor)
        self.header.setToolTip("Drag to move · double-click to collapse")
        self.header.add_button(f"dock_{manager.side}", "Dock in the sidebar",
                               lambda: manager.set_mode(ptype.id, "docked"))
        self.header.add_button("close", "Hide (its tab on the window edge brings it back)",
                               lambda: manager.set_mode(ptype.id, "hidden"))
        self.header.mousePressEvent = self._press  # type: ignore[method-assign]
        self.header.mouseMoveEvent = self._move  # type: ignore[method-assign]
        self.header.mouseReleaseEvent = self._release  # type: ignore[method-assign]
        self.header.mouseDoubleClickEvent = lambda _e: self.set_collapsed(not self.collapsed)  # type: ignore
        layout.addWidget(self.header)
        self.body = QWidget(self)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.body, 1)
        self.grip = _Grip(self)
        self.widget: QWidget | None = None
        self._drag: QPointF | None = None
        self._expanded_height = ptype.float_size[1]
        geometry = manager.card_geometry(ptype.id)
        if geometry:
            self.setGeometry(QRect(*geometry[:4]))
            self._expanded_height = max(80, geometry[3])
        else:
            index = sorted(manager.types).index(ptype.id) if ptype.id in manager.types else 0
            self.setGeometry(QRect(12 + 24 * index, 12 + 24 * index, *ptype.float_size))
        self.set_collapsed(ptype.id in manager.collapsed_cards)

    def set_widget(self, widget: QWidget) -> None:
        if self.widget is widget:
            return
        self.widget = widget
        widget.setParent(self.body)
        self.body_layout.addWidget(widget)
        widget.show()

    def take_widget(self) -> QWidget | None:
        widget, self.widget = self.widget, None
        if widget is not None:
            self.body_layout.removeWidget(widget)
        return widget

    @property
    def collapsed(self) -> bool:
        return not self.body.isVisibleTo(self)

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed and not self.collapsed:
            self._expanded_height = self.height()
        self.body.setVisible(not collapsed)
        self.grip.setVisible(not collapsed)
        self.header.set_arrow(collapsed)
        self.resize(self.width(), self.header.height() + 2 if collapsed else self._expanded_height)
        self.manager.set_card_collapsed(self.ptype.id, collapsed)
        self.clamp()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self.grip.move(self.width() - self.grip.width() - 1, self.height() - self.grip.height() - 1)
        self.grip.raise_()

    def clamp(self) -> None:
        area = self.parentWidget()
        if area is None:
            return
        x = max(0, min(self.x(), area.width() - min(self.width(), 80)))
        y = max(0, min(self.y(), area.height() - self.header.height()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _press(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton:
            self._drag = event.globalPosition() - QPointF(self.pos())
            self.raise_()

    def _move(self, event) -> None:  # noqa: ANN001
        if self._drag is not None:
            self.move((event.globalPosition() - self._drag).toPoint())
            self.clamp()

    def _release(self, _event) -> None:  # noqa: ANN001
        if self._drag is not None:
            self._drag = None
            self.manager.remember_card(self)


# ---- Panel tabs -------------------------------------------------------------------------------------


class PanelTab(QAbstractButton):
    """A vertical tab on the window edge: the panel's icon and its name, read top to bottom."""

    WIDTH = 26

    def __init__(self, ptype: PanelType, rail: "PanelRail") -> None:
        super().__init__(rail)
        self.ptype = ptype
        self.rail = rail
        self.setCheckable(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.WIDTH, 8 + 16 + 8 + self.fontMetrics().horizontalAdvance(self.ptype.title) + 10)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        checked, hover = self.isChecked(), self.underMouse()
        if checked or hover:
            painter.fillRect(rect, QColor("#1c2230" if checked else "#1d2027"))
        accent = QColor(panel_color(self.ptype))
        if checked:  # a bar on the side facing the panels
            x = 0 if self.rail.side == "right" else rect.width() - 3
            painter.fillRect(QRectF(x, 3, 3, rect.height() - 6), accent)
        else:
            accent.setAlpha(170 if hover else 120)
        painter.drawPixmap(QPointF((rect.width() - 16) / 2, 8),
                           icon_pixmap(self.ptype.icon or "window", 16, accent.name(QColor.HexArgb)))
        painter.setPen(QColor("#d5e8ff" if checked else "#c8cdd8" if hover else "#7d8594"))
        metrics = self.fontMetrics()
        # Rotated a quarter turn clockwise: the text reads top to bottom, centred in the tab.
        painter.translate(rect.width() / 2 - (metrics.ascent() - metrics.descent()) / 2, 8 + 16 + 8)
        painter.rotate(90)
        painter.drawText(QPointF(0, 0), self.ptype.title)


class PanelRail(QFrame):
    """The column of panel tabs on the window edge, beside the sidebar. The tabs scroll (mouse
    wheel) when the window is too short for them all, so they never set its minimum height."""

    def __init__(self, manager: "PanelManager") -> None:
        super().__init__(None)
        self.manager = manager
        self.setObjectName("panelRail")
        self.setFixedWidth(PanelTab.WIDTH + 1)
        self.side = manager.side
        self.setProperty("side", self.side)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea(self)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("background: transparent;")
        outer.addWidget(self.scroll)
        content = QWidget()
        self.scroll.setWidget(content)
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(0, 4, 0, 4)
        self._layout.setSpacing(2)
        self.tabs: dict[str, PanelTab] = {}
        self.rebuild()

    def set_side(self, side: str) -> None:
        if side != self.side:
            self.side = side
            self.setProperty("side", side)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def rebuild(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.tabs = {}
        app = self.manager.app
        for ptype in self.manager.ordered():
            tab = PanelTab(ptype, self)
            keys = ", ".join(ptype.popup_keys)
            tab.setToolTip(f"<b>{ptype.title}</b><br>Click to show or hide it"
                           + (f"<br>{keys} pops it up anywhere" if keys else "")
                           + "<br>Right-click: docked, floating or pop-up only")
            tab.clicked.connect(lambda _c=False, p=ptype.id: app.run_command("panels.toggle", p))
            tab.setContextMenuPolicy(Qt.CustomContextMenu)
            tab.customContextMenuRequested.connect(lambda pos, p=ptype.id, t=tab: self.show_menu(p, t, pos))
            self._layout.addWidget(tab)
            self.tabs[ptype.id] = tab
        self._layout.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        for panel_id, tab in self.tabs.items():
            tab.setChecked(self.manager.is_open(panel_id))

    def show_menu(self, panel_id: str, tab: QWidget, pos: QPoint) -> None:
        actions = self.manager.app.actions
        menu = QMenu(tab)
        menu.addAction(actions.get(f"panels.popup.{panel_id}"))
        menu.addSeparator()
        for mode in MODES:
            menu.addAction(actions.get(f"panels.mode.{panel_id}.{mode}"))
        menu.addSeparator()
        other = "left" if self.manager.side == "right" else "right"
        menu.addAction(icon(f"dock_{other}"), f"Move the Panels to the {other.title()}",
                       lambda: self.manager.app.run_command("panels.set_side", other))
        menu.exec(tab.mapToGlobal(pos))


# ---- Pop-ups ------------------------------------------------------------------------------------------------


class PanelPopup(QFrame):
    """A panel shown on demand next to the cursor or the selection."""

    def __init__(self, manager: "PanelManager", ptype: PanelType, owner: QWidget | None) -> None:
        super().__init__(owner, Qt.Tool | Qt.FramelessWindowHint)
        self.manager = manager
        self.ptype = ptype
        self.setObjectName("panelPopup")
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self.header = PanelHeader(ptype, self)
        self.header.arrow.hide()
        self.header.add_button("pin", "Keep it open: float it over the canvas",
                               lambda: manager.pin_popup(ptype.id))
        self.header.add_button("close", "Close (Esc)", self.close)
        layout.addWidget(self.header)
        self.widget = ptype.factory(self)
        layout.addWidget(self.widget, 1)
        self.resize(*ptype.popup_size)

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().closeEvent(event)
        self.manager.popup_closed(self)


_MOUSE_PRESS = QEvent.MouseButtonPress  # (enum attribute lookups are slow per event)


class _OutsideClickFilter(QObject):
    """Closes the open pop-up when the user clicks in a main window outside it."""

    def __init__(self, manager: "PanelManager") -> None:
        super().__init__()
        self.manager = manager

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        # Sees every event of the application: test the cheap condition first.
        if self.manager.open_popup is not None and event.type() == _MOUSE_PRESS:
            popup = self.manager.open_popup
            if isinstance(obj, QWidget):
                window = obj.window()
                if window is not popup and not popup.isAncestorOf(obj) and window in self.manager.app_windows():
                    popup.close()
        return False


# ---- The manager --------------------------------------------------------------------------------------------


class PanelManager:
    """Owns panel types, their modes and where their widgets live."""

    def __init__(self, app: "App") -> None:
        self.app = app
        self.settings = app.settings.scoped("panels")
        self.types: dict[str, PanelType] = {}
        self.mains: dict[str, QWidget] = {}  # the docked/floating instance of each panel
        self.sections: dict[str, PanelSection] = {}
        self.cards: dict[str, FloatCard] = {}
        self.popups: dict[str, PanelPopup] = {}
        self._popup_hooks: dict[str, list[Disposable]] = {}
        self.open_popup: PanelPopup | None = None
        self.window: Any = None  # the main OutputWindow
        self.sidebar: Sidebar | None = None
        self.rail: PanelRail | None = None
        self.hidden_all = False
        self.window_allows = True
        self._modes: dict[str, str] = self._json("modes", {})
        self.collapsed: set[str] = set(self._json("collapsed", []))
        self.collapsed_cards: set[str] = set(self._json("collapsed_cards", []))
        self._filter = _OutsideClickFilter(self)
        QApplication.instance().installEventFilter(self._filter)
        self._updating = False
        self._width_applied = False

    # ---- Settings helpers ---------------------------------------------------------------

    def _json(self, key: str, default: Any) -> Any:
        raw = self.settings.value(key, "")
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return default

    def _store(self, key: str, value: Any) -> None:
        self.settings.setValue(key, json.dumps(value))

    @property
    def side(self) -> str:
        side = str(self.settings.value("side", DEFAULT_SIDE) or DEFAULT_SIDE)
        return side if side in ("left", "right") else DEFAULT_SIDE

    @property
    def width(self) -> int:
        try:
            value = int(self.settings.value("width", SIDEBAR_DEFAULT_WIDTH))
        except (TypeError, ValueError):
            value = SIDEBAR_DEFAULT_WIDTH
        return max(SIDEBAR_MIN_WIDTH, min(SIDEBAR_MAX_WIDTH, value))

    def section_sizes(self) -> dict[str, int]:
        return self._json("section_sizes", {})

    def remember_section_sizes(self) -> None:
        if self.sidebar is None:
            return
        sizes = dict(self.section_sizes())
        for section in self.sidebar.sections.values():
            if not section.collapsed:
                sizes[section.ptype.id] = section.height()
        self._store("section_sizes", sizes)

    def card_geometry(self, panel_id: str) -> list[int] | None:
        value = self._json("cards", {}).get(panel_id)
        return [int(v) for v in value] if isinstance(value, list) and len(value) == 4 else None

    def remember_card(self, card: FloatCard) -> None:
        cards = self._json("cards", {})
        height = card.height() if not card.collapsed else card._expanded_height
        cards[card.ptype.id] = [card.x(), card.y(), card.width(), height]
        self._store("cards", cards)

    def set_card_collapsed(self, panel_id: str, collapsed: bool) -> None:
        (self.collapsed_cards.add if collapsed else self.collapsed_cards.discard)(panel_id)
        self._store("collapsed_cards", sorted(self.collapsed_cards))

    # ---- Registration -----------------------------------------------------------------------

    def register(self, ptype: PanelType) -> Disposable:
        if ptype.id in self.types:
            raise ValueError(f"Panel '{ptype.id}' is already registered")
        self.types[ptype.id] = ptype
        disposables = self._register_actions(ptype)
        self.update()
        self._refresh_bar()

        def remove() -> None:
            for disposable in reversed(disposables):
                disposable.dispose()
            if self.types.get(ptype.id) is not ptype:
                return
            self._drop_instances(ptype.id)
            del self.types[ptype.id]
            self.update()
            self._refresh_bar()

        return Disposable(remove)

    def _refresh_bar(self) -> None:
        """The panel tabs are built per panel type: rebuild them when the set changes."""
        if self.rail is not None:
            self.rail.rebuild()

    def _register_actions(self, ptype: PanelType) -> list[Disposable]:
        app = self.app
        a = app.actions.define
        items = [
            a(f"panels.popup.{ptype.id}", "panels.popup", f"{ptype.title}…", args=(ptype.id,), icon_name=ptype.icon,
              keywords=f"{ptype.keywords} panel pop up show open", category="Window", plugin=ptype.plugin),
            a(f"panels.toggle.{ptype.id}", "panels.toggle", f"Show {ptype.title}", args=(ptype.id,),
              icon_name=ptype.icon, keywords=f"{ptype.keywords} panel hide show",
              category="Window", checked=lambda p=ptype.id: self.mode(p) != "hidden", plugin=ptype.plugin),
        ]
        for mode, label in (("docked", "Docked in Sidebar"), ("floating", "Floating over Canvas"),
                            ("hidden", "Hidden (Pop-up Only)")):
            items.append(a(f"panels.mode.{ptype.id}.{mode}", "panels.set_mode", label, args=(ptype.id, mode),
                           category="hidden", group=f"panels.mode.{ptype.id}",
                           checked=lambda p=ptype.id, m=mode: self.mode(p) == m, plugin=ptype.plugin))
        for key in ptype.popup_keys:
            items.append(app.keymap.bind(key, f"panels.popup.{ptype.id}", plugin=ptype.plugin))
        return items

    def _drop_instances(self, panel_id: str) -> None:
        ptype = self.types[panel_id]
        for hook in self._popup_hooks.pop(panel_id, []):
            hook.dispose()
        popup = self.popups.pop(panel_id, None)
        if popup is not None:
            if self.open_popup is popup:
                self.open_popup = None
            self._dispose_widget(ptype, popup.widget)
            popup.hide()
            popup.deleteLater()
        section = self.sections.pop(panel_id, None)
        card = self.cards.pop(panel_id, None)
        for host in (section, card):
            if host is not None:
                host.take_widget()
                host.hide()
                host.setParent(None)
                host.deleteLater()
        widget = self.mains.pop(panel_id, None)
        if widget is not None:
            self._dispose_widget(ptype, widget)
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()

    @staticmethod
    def _dispose_widget(ptype: PanelType, widget: QWidget) -> None:
        if ptype.dispose is not None:
            try:
                ptype.dispose(widget)
            except Exception:  # noqa: BLE001
                logger.exception("Disposing panel %s failed", ptype.id)

    # ---- Modes ---------------------------------------------------------------------------------

    def ordered(self) -> list[PanelType]:
        return sorted(self.types.values(), key=lambda p: (p.order, p.id))

    def mode(self, panel_id: str) -> str:
        ptype = self.types.get(panel_id)
        default = ptype.default_mode if ptype is not None else "docked"
        mode = self._modes.get(panel_id, default)
        return mode if mode in MODES else default

    def set_mode(self, panel_id: str, mode: str) -> None:
        if panel_id not in self.types:
            raise KeyError(panel_id)
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        self._modes[panel_id] = mode
        self._store("modes", self._modes)
        if mode != "hidden":
            self.hidden_all = False
        self.update()
        self.app.actions.refresh_states()

    def set_collapsed(self, panel_id: str, collapsed: bool) -> None:
        section = self.sections.get(panel_id)
        if section is not None:
            section.set_collapsed(collapsed)
        (self.collapsed.add if collapsed else self.collapsed.discard)(panel_id)
        self._store("collapsed", sorted(self.collapsed))

    def toggle_all(self) -> bool:
        """Hide/show the sidebar and floating cards (Tab). Returns True if they are now shown."""
        self.hidden_all = not self.hidden_all
        self.update()
        return not self.hidden_all

    def set_side(self, side: str) -> None:
        if side not in ("left", "right"):
            raise ValueError("side must be 'left' or 'right'")
        self.settings.setValue("side", side)
        self.update()

    def set_width(self, width: int) -> int:
        width = max(SIDEBAR_MIN_WIDTH, min(SIDEBAR_MAX_WIDTH, int(width)))
        self.settings.setValue("width", width)
        self._apply_width()
        return width

    def sidebar_width(self) -> int:
        return self.sidebar.width() if self.sidebar is not None and self.sidebar.isVisible() else 0

    def is_open(self, panel_id: str) -> bool:
        """Whether the panel is meant to be on screen (its tab is lit)."""
        return self.mode(panel_id) != "hidden" and not self.hidden_all and self.window_allows

    def is_shown(self, panel_id: str) -> bool:
        """Whether the panel is visible in the main window (docked or floating)."""
        widget = self.mains.get(panel_id)
        return widget is not None and widget.isVisible()

    # ---- The main window ---------------------------------------------------------------------------

    def app_windows(self) -> list[QWidget]:
        wm = self.app.wm
        return list(wm.windows.values()) + list(wm.floating_windows.values())

    def attach_window(self, window: Any) -> None:
        """Called when the primary output window is created."""
        self.window = window
        self.sidebar = Sidebar(self)
        self.rail = PanelRail(self)
        window.body.splitterMoved.connect(lambda *_a: self._on_body_splitter_moved())
        area_filter = self._area_filter()
        window.area.installEventFilter(area_filter)
        window.body.installEventFilter(area_filter)
        self.update()

    def _area_filter(self) -> QObject:
        manager = self

        class AreaFilter(QObject):
            def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
                if event.type() == QEvent.Resize:
                    if manager.window is not None and obj is manager.window.body:
                        if not manager._width_applied:
                            manager._apply_width()
                    else:
                        for card in manager.cards.values():
                            card.clamp()
                return False

        self._area_filter_obj = AreaFilter()
        return self._area_filter_obj

    def detach_window(self) -> None:
        for panel_id in list(self.types):
            self._drop_instances(panel_id)
        if self.sidebar is not None:
            self.sidebar.hide()
            self.sidebar.setParent(None)
            self.sidebar.deleteLater()
        if self.rail is not None:
            self.rail.hide()
            self.rail.setParent(None)
            self.rail.deleteLater()
        self.sidebar = None
        self.rail = None
        self.window = None
        self._width_applied = False

    def set_window_allows(self, allowed: bool) -> None:
        if allowed != self.window_allows:
            self.window_allows = allowed
            self.update()

    def _main_widget(self, ptype: PanelType) -> QWidget:
        widget = self.mains.get(ptype.id)
        if widget is None:
            widget = ptype.factory(None)
            self.mains[ptype.id] = widget
        return widget

    def update(self) -> None:
        """Put every panel where its mode says (called after any change)."""
        if self.window is None or self._updating:
            return
        self._updating = True
        try:
            self._update()
        finally:
            self._updating = False

    def _update(self) -> None:
        window = self.window
        show = self.window_allows and not self.hidden_all
        docked: list[PanelSection] = []
        for ptype in self.ordered():
            mode = self.mode(ptype.id)
            widget = self._main_widget(ptype)
            section = self.sections.get(ptype.id)
            card = self.cards.get(ptype.id)
            if mode == "docked":
                if card is not None and card.widget is widget:
                    card.take_widget()
                if section is None:
                    section = self.sections[ptype.id] = PanelSection(self, ptype)
                section.set_widget(widget)
                docked.append(section)
            elif mode == "floating":
                if section is not None and section.widget is widget:
                    section.take_widget()
                if card is None:
                    card = self.cards[ptype.id] = FloatCard(self, ptype, window.area)
                card.set_widget(widget)
            else:
                for host in (section, card):
                    if host is not None and host.widget is widget:
                        host.take_widget()
                widget.hide()
                widget.setParent(None)
            if section is not None and mode != "docked":
                section.hide()  # a docked section is shown by sidebar.set_sections once it has a parent: showing it earlier flashes a stray window
            if card is not None:
                card.setVisible(mode == "floating" and show)
                if card.isVisible():
                    card.raise_()
                    card.clamp()
        sidebar = self.sidebar
        if sidebar is not None:
            for section in list(sidebar.sections.values()):
                if section not in docked:
                    section.hide()
            sidebar.set_sections(docked)
            body = window.body
            area_index = body.indexOf(window.area)
            current = body.indexOf(sidebar)
            left = self.side == "left"
            if current < 0 or (left and current > area_index) or (not left and current < area_index):
                body.insertWidget(0 if left else body.count(), sidebar)
            body.setCollapsible(body.indexOf(sidebar), False)
            body.setCollapsible(body.indexOf(window.area), False)
            sidebar.setVisible(bool(docked) and show)
            self._apply_width()
        rail = self.rail
        if rail is not None:  # the tabs sit on the window's outer edge, beyond the sidebar
            row = window.middle.layout()
            if row.indexOf(rail) >= 0:
                row.removeWidget(rail)
            row.insertWidget(0 if self.side == "left" else row.count(), rail)
            rail.set_side(self.side)
            rail.setVisible(self.window_allows)
            rail.refresh()

    def _apply_width(self) -> None:
        if self.window is None or self.sidebar is None or not self.sidebar.isVisible():
            return
        body = self.window.body
        body.setStretchFactor(body.indexOf(self.window.area), 1)
        body.setStretchFactor(body.indexOf(self.sidebar), 0)
        total = sum(body.sizes()) or body.width()
        width = self.width
        if total < width + 200:  # the window isn't laid out yet; its first resize applies it
            return
        self._width_applied = True
        index = body.indexOf(self.sidebar)
        sizes = [width, max(1, total - width)] if index == 0 else [max(1, total - width), width]
        body.setSizes(sizes)

    def _on_body_splitter_moved(self) -> None:
        if self.sidebar is not None and self.sidebar.isVisible():
            self.settings.setValue("width", self.sidebar.width())

    def raise_cards(self) -> None:
        for card in self.cards.values():
            if card.isVisible():
                card.raise_()

    # ---- Pop-ups ----------------------------------------------------------------------------------

    def popup(self, panel_id: str, at: QPoint | None = None) -> PanelPopup:
        ptype = self.types[panel_id]
        if self.open_popup is not None and self.open_popup.ptype.id != panel_id:
            self.open_popup.close()
        popup = self.popups.get(panel_id)
        if popup is None:
            popup = self.popups[panel_id] = PanelPopup(self, ptype, self.window)
            popup.setStyleSheet(self.app.stylesheet)
            # Unsubscribed in _drop_instances: the pop-up is deleted with its panel type.
            self._popup_hooks[panel_id] = [
                self.app.events.on(event, lambda _e, p=popup: p.isVisible() and p.close())
                for event in ptype.popup_closes_on
            ]
        position = at if at is not None else self._popup_position(ptype, popup)
        popup.move(self._fit_on_screen(position, popup.size()))
        self.open_popup = popup
        popup.show()
        popup.raise_()
        popup.activateWindow()
        return popup

    def _popup_position(self, ptype: PanelType, popup: QWidget) -> QPoint:
        cursor = QCursor.pos()
        if ptype.popup_anchor == "selection":
            view = self.app.active_view()
            items = self.app.canvas.outline_items() if view is not None else []
            if view is not None and items:
                bounds = self.app.canvas.scene_bounds(items)
                polygon = view.mapFromScene(bounds)
                rect = polygon.boundingRect()
                right = view.viewport().mapToGlobal(QPoint(rect.right() + 16, rect.top()))
                left = view.viewport().mapToGlobal(QPoint(rect.left() - 16 - popup.width(), rect.top()))
                screen = QGuiApplication.screenAt(right) or QGuiApplication.primaryScreen()
                if screen is not None and right.x() + popup.width() > screen.availableGeometry().right():
                    return left
                return right
        return QPoint(cursor.x() + 12, cursor.y() + 12)

    @staticmethod
    def _fit_on_screen(position: QPoint, size: QSize) -> QPoint:
        screen = QGuiApplication.screenAt(position) or QGuiApplication.primaryScreen()
        if screen is None:
            return position
        area = screen.availableGeometry()
        x = max(area.left(), min(position.x(), area.right() - size.width()))
        y = max(area.top(), min(position.y(), area.bottom() - size.height()))
        return QPoint(x, y)

    def popup_closed(self, popup: PanelPopup) -> None:
        if self.open_popup is popup:
            self.open_popup = None

    def pin_popup(self, panel_id: str) -> None:
        """Turn a pop-up into a floating card where it is."""
        popup = self.popups.get(panel_id)
        geometry = None
        if popup is not None and self.window is not None:
            top_left = self.window.area.mapFromGlobal(popup.pos())
            geometry = [top_left.x(), top_left.y(), popup.width(), popup.height()]
            popup.close()
        if geometry is not None:
            cards = self._json("cards", {})
            cards[panel_id] = geometry
            self._store("cards", cards)
            card = self.cards.get(panel_id)
            if card is not None:
                card.setGeometry(QRect(*geometry))
        self.set_mode(panel_id, "floating")

    # ---- Menus ---------------------------------------------------------------------------------------

    def fill_menu(self, menu: QMenu) -> None:
        """Window ▸ Panels: each panel's mode, pop-up, and the sidebar side."""
        actions = self.app.actions
        for ptype in self.ordered():
            sub = menu.addMenu(icon(ptype.icon) if ptype.icon else icon("window"), ptype.title)
            sub.addAction(actions.get(f"panels.popup.{ptype.id}"))
            sub.addSeparator()
            for mode in MODES:
                sub.addAction(actions.get(f"panels.mode.{ptype.id}.{mode}"))
        menu.addSeparator()
        menu.addAction(actions.get("panels.toggle_all"))
        right = menu.addAction("Sidebar on the Right", lambda: self.app.run_command(
            "panels.set_side", "left" if self.side == "right" else "right"))
        right.setCheckable(True)
        right.setChecked(self.side == "right")

    def shutdown(self) -> None:
        QApplication.instance().removeEventFilter(self._filter)
        for popup in list(self.popups.values()):
            popup.hide()
