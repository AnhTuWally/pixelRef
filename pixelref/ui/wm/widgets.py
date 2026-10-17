"""Widgets the window manager builds from the layout tree."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import QEvent, QObject, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QSplitterHandle,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..icons import app_icon, icon

if TYPE_CHECKING:
    from .manager import WindowManager


@dataclass
class ViewType:
    """A kind of tile: canvas, console, contact sheet… (the Tools, Outliner and
    Attribute Editor are panels instead — see ui/panels.py; the minimap is a canvas overlay)."""

    id: str
    title: str
    factory: Callable[[str, dict[str, Any]], QWidget]  # (tile name, saved state) -> widget
    icon: str = ""
    save_state: Callable[[QWidget], dict[str, Any]] | None = None
    header_widgets: Callable[[QWidget], list[QWidget]] | None = None
    dispose: Callable[[QWidget], None] | None = None
    min_width: int = 0
    min_height: int = 0
    max_width: int = 0
    placement: str = "split"  # split, tabbed, floating, scratchpad
    plugin: str | None = None


class TileHeader(QFrame):
    def __init__(self, frame: "TileFrame") -> None:
        super().__init__(frame)
        self.setObjectName("tileHeader")
        self.setFixedHeight(24)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 2, 0)
        layout.setSpacing(4)
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(14, 14)
        self.title_label = QLabel("", self)
        self.title_label.setObjectName("tileTitle")
        self.extra = QWidget(self)
        self.extra_layout = QHBoxLayout(self.extra)
        self.extra_layout.setContentsMargins(0, 0, 0, 0)
        self.extra_layout.setSpacing(3)
        self.split_right_button = self._split_button("split_right", "Split right: open a copy of this view beside it")
        self.split_down_button = self._split_button("split_down", "Split down: open a copy of this view below it")
        self.menu_button = QToolButton(self)
        self.menu_button.setObjectName("tileHeaderButton")
        self.menu_button.setIcon(icon("more", "#8a93a3"))
        self.menu_button.setIconSize(QSize(14, 14))
        self.menu_button.setToolTip("View menu (view type, float, tear off, close…)")
        self.menu_button.setFocusPolicy(Qt.NoFocus)
        self.close_button = QToolButton(self)
        self.close_button.setObjectName("tileHeaderButton")
        self.close_button.setIcon(icon("close", "#8a93a3"))
        self.close_button.setIconSize(QSize(14, 14))
        self.close_button.setToolTip("Close this view (Ctrl+W, Q)")
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.title_label.setToolTip("Double-click to rename this view")
        self.title_label.installEventFilter(self)
        self.title_edit = QLineEdit(self)
        self.title_edit.setObjectName("tileTitleEdit")
        self.title_edit.setFixedHeight(20)
        self.title_edit.setMinimumWidth(120)
        self.title_edit.hide()
        self.title_edit.installEventFilter(self)
        self.title_edit.returnPressed.connect(lambda: self._finish_title_edit(True))
        self.title_edit.editingFinished.connect(lambda: self._finish_title_edit(True))
        layout.addWidget(self.icon_label)
        layout.addWidget(self.title_label)
        layout.addWidget(self.title_edit)
        layout.addStretch(1)
        layout.addWidget(self.extra)
        layout.addWidget(self.split_right_button)
        layout.addWidget(self.split_down_button)
        layout.addWidget(self.menu_button)
        layout.addWidget(self.close_button)

    def edit_title(self, current: str) -> None:
        """Rename in place: Enter or clicking away keeps the new name, Esc cancels."""
        self.title_label.hide()
        self.title_edit.setText(current)
        self.title_edit.show()
        self.title_edit.selectAll()
        self.title_edit.setFocus(Qt.OtherFocusReason)

    def _finish_title_edit(self, accept: bool) -> None:
        if self.title_edit.isHidden():
            return
        text = self.title_edit.text().strip()
        self.title_edit.hide()
        self.title_label.show()
        frame = self.parentWidget()
        if accept and text and text != self.title_label.text() and isinstance(frame, TileFrame):
            name, manager = frame.name, frame.manager
            # Renaming re-applies the layout, which re-parents this header: leave the event first.
            QTimer.singleShot(0, lambda: manager.run("wm.rename_view", text, name))

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.title_label and event.type() == QEvent.MouseButtonDblClick:
            frame = self.parentWidget()
            if isinstance(frame, TileFrame):
                frame.manager.rename_view_prompt(frame.name)
            return True
        if obj is self.title_edit and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self._finish_title_edit(False)
            return True
        return False

    def _split_button(self, icon_name: str, tip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("tileHeaderButton")
        button.setIcon(icon(icon_name))
        button.setIconSize(QSize(14, 14))
        button.setToolTip(f"{tip}\n(or drag a corner of the view inward)")
        button.setFocusPolicy(Qt.NoFocus)
        return button


CORNERS = ("tl", "tr", "bl", "br")


class CornerGrip(QWidget):
    """A corner of a tile: drag inward to split the tile, onto a neighbour to join (as in Blender)."""

    SIZE = 14

    def __init__(self, frame: "TileFrame", corner: str) -> None:
        super().__init__(frame)
        self.frame = frame
        self.corner = corner
        self.hover = False
        self.drag: Any = None
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip("Drag into the view to split it\nDrag onto a neighbouring view to join the two")
        self.setAttribute(Qt.WA_Hover, True)

    def enterEvent(self, event: QEvent) -> None:  # noqa: N802
        self.hover = True
        self.update()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        self.hover = False
        self.update()

    def paintEvent(self, _event: QEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(theme.ACCENT) if self.hover or self.drag is not None else QColor(255, 255, 255, 70)
        painter.setPen(QPen(color, 1.4, Qt.SolidLine, Qt.RoundCap))
        s = float(self.SIZE)
        # Two short diagonal strokes hugging the corner.
        right, bottom = self.corner.endswith("r"), self.corner.startswith("b")
        for inset in (4.0, 8.0):
            a = QPointF(s - 1.5 if right else 1.5, (s - 1.5 - inset) if bottom else 1.5 + inset)
            b = QPointF((s - 1.5 - inset) if right else 1.5 + inset, s - 1.5 if bottom else 1.5)
            painter.drawLine(a, b)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            from .interact import CornerDrag

            self.drag = CornerDrag(self.frame.manager, self.frame.name, self.corner,
                                   event.globalPosition().toPoint())
            self.grabKeyboard()
            event.accept()
        elif event.button() == Qt.RightButton and self.drag is not None:
            self._end(apply=False)
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.drag is not None:
            self.drag.update(event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.drag is not None:
            self._end(apply=True)

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape and self.drag is not None:
            self._end(apply=False)
        else:
            super().keyPressEvent(event)

    def _end(self, apply: bool) -> None:
        drag, self.drag = self.drag, None
        self.releaseKeyboard()
        self.hover = False
        self.update()
        if drag is not None:
            drag.finish(apply)


class TileFrame(QFrame):
    """A tile: header + the view widget. Created once per tile and moved around."""

    def __init__(self, manager: "WindowManager", name: str, body: QWidget, view_type: ViewType | None) -> None:
        super().__init__(None)
        self.manager = manager
        self.name = name
        self.body = body
        self.view_type = view_type
        self.setObjectName("tileFrame")
        self.setProperty("focused", False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = TileHeader(self)
        layout.addWidget(self.header)
        layout.addWidget(body, 1)
        self.header.close_button.clicked.connect(lambda: manager.run("wm.close_view", self.name))
        self.header.menu_button.clicked.connect(lambda: manager.show_tile_menu(self.name, self.header.menu_button))
        self.header.split_right_button.clicked.connect(lambda: manager.run("wm.split_view", "right", self.name))
        self.header.split_down_button.clicked.connect(lambda: manager.run("wm.split_view", "down", self.name))
        self.grips = [CornerGrip(self, corner) for corner in CORNERS]
        self.split_enabled = True
        if view_type is not None:
            if view_type.min_width:
                self.setMinimumWidth(view_type.min_width)
            if view_type.min_height:
                self.setMinimumHeight(view_type.min_height)
            if view_type.max_width:
                self.setMaximumWidth(view_type.max_width)
            if view_type.icon:
                self.header.icon_label.setPixmap(icon(view_type.icon).pixmap(14, 14))
            if view_type.header_widgets is not None:
                for widget in view_type.header_widgets(body):
                    self.header.extra_layout.addWidget(widget)
        self._watcher = _FocusWatcher(self)
        self.installEventFilter(self._watcher)
        body.installEventFilter(self._watcher)

    def set_title(self, title: str) -> None:
        self.header.title_label.setText(title)

    def set_header_visible(self, visible: bool) -> None:
        self.header.setVisible(visible)
        self._place_grips()

    def set_split_enabled(self, enabled: bool) -> None:
        """Tiled views can be split; floating, fullscreen and scratchpad ones can't."""
        self.split_enabled = enabled
        self.header.split_right_button.setVisible(enabled)
        self.header.split_down_button.setVisible(enabled)
        self._place_grips()

    def _place_grips(self) -> None:
        show = self.split_enabled and not self.header.isHidden()
        top = self.header.height() if not self.header.isHidden() else 0
        size = CornerGrip.SIZE
        right, bottom = self.width() - size - 1, self.height() - size - 1
        spots = {"tl": (1, top), "tr": (right, top), "bl": (1, bottom), "br": (right, bottom)}
        for grip in self.grips:
            grip.move(*spots[grip.corner])
            grip.setVisible(show)
            if show:
                grip.raise_()

    def resizeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._place_grips()

    def set_focused(self, focused: bool) -> None:
        if self.property("focused") == focused:
            return
        for widget in (self, self.header):
            widget.setProperty("focused", focused)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.header.title_label.style().unpolish(self.header.title_label)
        self.header.title_label.style().polish(self.header.title_label)


class _FocusWatcher(QObject):
    """Focus follows the mouse (and clicks)."""

    def __init__(self, frame: TileFrame) -> None:
        super().__init__(frame)
        self.frame = frame

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        kind = event.type()
        if kind == QEvent.Enter and self.frame.manager.focus_follows_mouse:
            self.frame.manager.focus_tile(self.frame.name, from_mouse=True)
        elif kind in (QEvent.MouseButtonPress, QEvent.FocusIn):
            self.frame.manager.focus_tile(self.frame.name)
        return False


class SplitHandle(QSplitterHandle):
    """The divider between two views: drag to resize, double-click to even out, right-click for options."""

    def __init__(self, orientation: Qt.Orientation, splitter: "SplitContainer") -> None:
        super().__init__(orientation, splitter)
        self.setToolTip("Drag to resize · double-click to even out\nRight-click: stack / side by side, swap…")

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        splitter = self.splitter()
        if isinstance(splitter, SplitContainer) and splitter.manager is not None:
            name, manager = splitter.name, splitter.manager
            QTimer.singleShot(0, lambda: manager.run("wm.equalize_split", name))

    def contextMenuEvent(self, event: Any) -> None:  # noqa: N802
        splitter = self.splitter()
        if isinstance(splitter, SplitContainer) and splitter.manager is not None:
            splitter.manager.show_split_menu(splitter.name, event.globalPos())


class SplitContainer(QSplitter):
    def __init__(self, name: str, orientation: str, manager: "WindowManager | None" = None) -> None:
        super().__init__(Qt.Horizontal if orientation == "h" else Qt.Vertical)
        self.name = name
        self.manager = manager
        self.setChildrenCollapsible(False)
        self.setHandleWidth(5)

    def createHandle(self) -> QSplitterHandle:  # noqa: N802
        return SplitHandle(self.orientation(), self)


class TabbedContainer(QTabWidget):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.setDocumentMode(True)


class StackedContainer(QWidget):
    """i3 'stacked': every child's title in a column, one child visible."""

    current_changed = Signal(int)

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.titles = QWidget(self)
        self.title_layout = QVBoxLayout(self.titles)
        self.title_layout.setContentsMargins(0, 0, 0, 0)
        self.title_layout.setSpacing(0)
        self.stack = QStackedWidget(self)
        layout.addWidget(self.titles)
        layout.addWidget(self.stack, 1)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.group.idClicked.connect(self.set_current)

    def add(self, widget: QWidget, title: str) -> None:
        index = self.stack.addWidget(widget)
        button = QPushButton(title, self.titles)
        button.setObjectName("stackTitle")
        button.setCheckable(True)
        button.setFocusPolicy(Qt.NoFocus)
        self.title_layout.addWidget(button)
        self.group.addButton(button, index)

    def set_current(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)
            button = self.group.button(index)
            if button is not None:
                button.setChecked(True)
            self.current_changed.emit(index)

    def count(self) -> int:
        return self.stack.count()


class Bar(QFrame):
    """Bottom bar of an output: workspaces, mode, status, plugin blocks."""

    def __init__(self, manager: "WindowManager", output: str) -> None:
        super().__init__()
        self.manager = manager
        self.output = output
        self.setObjectName("wmBar")
        self.setFixedHeight(26)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(4)
        self.workspaces = QWidget(self)
        self.workspace_layout = QHBoxLayout(self.workspaces)
        self.workspace_layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_layout.setSpacing(2)
        self.mode_label = QLabel("", self)
        self.mode_label.setObjectName("modeIndicator")
        self.mode_label.hide()
        self.status = QLabel("Ready", self)
        self.status.setObjectName("statusLabel")
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.blocks = QWidget(self)
        self.block_layout = QHBoxLayout(self.blocks)
        self.block_layout.setContentsMargins(0, 0, 0, 0)
        self.block_layout.setSpacing(2)
        layout.addWidget(self.workspaces)
        layout.addWidget(self.mode_label)
        layout.addWidget(self.status, 1)
        layout.addWidget(self.blocks)
        self._block_widgets: list[QWidget] = []

    def set_workspaces(self, entries: list[tuple[str, str, bool]]) -> None:
        while self.workspace_layout.count():
            item = self.workspace_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for name, label, current in entries:
            button = QPushButton(label, self.workspaces)
            button.setObjectName("workspaceButton")
            button.setCheckable(True)
            button.setChecked(current)
            button.setFocusPolicy(Qt.NoFocus)
            button.setToolTip(f"Workspace {label} (Ctrl+W, then its number)\n"
                              "Right-click: rename, templates, delete")
            button.clicked.connect(lambda _c=False, n=name: self.manager.run("wm.workspace", n))
            button.setContextMenuPolicy(Qt.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda _pos, n=name, b=button: self.manager.show_workspace_menu(n, b))
            self.workspace_layout.addWidget(button)
        add = QPushButton("+", self.workspaces)
        add.setObjectName("workspaceButton")
        add.setFocusPolicy(Qt.NoFocus)
        add.setToolTip("New workspace: blank, or from a template")
        add.clicked.connect(lambda _c=False, b=add: self.manager.show_new_workspace_menu(b))
        self.workspace_layout.addWidget(add)

    def set_mode(self, title: str | None) -> None:
        self.mode_label.setVisible(bool(title))
        self.mode_label.setText(f"-- {title.upper()} --" if title else "")

    def set_blocks(self, widgets: list[QWidget]) -> None:
        for widget in self._block_widgets:
            self.block_layout.removeWidget(widget)
            widget.deleteLater()
        self._block_widgets = list(widgets)
        for widget in widgets:
            self.block_layout.addWidget(widget)


# Event types the frameless resizer looks at (a frozenset: comparing against QEvent enum
# attributes on every event of the application is several times slower).
_RESIZER_EVENTS = frozenset({QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.Leave,
                             QEvent.Hide})
_MOUSE_MOVE, _DOUBLE_CLICK, _LEAVE, _HIDE = QEvent.MouseMove, QEvent.MouseButtonDblClick, QEvent.Leave, QEvent.Hide


class _FramelessResizer(QObject):
    """Edge resizing and moving for a frameless window (it has no system frame to grab).

    Watches the window's QWindow, which sees every mouse event whatever widget is under
    the cursor: a press near an edge starts the system resize, a press on a blank part of
    the bar or a view header starts the system move (so OS snapping still works), and a
    double-click there maximizes or restores the window.
    """

    BORDER = 6

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.window = window
        self.cursor_set = False
        QApplication.instance().installEventFilter(self)
        window.destroyed.connect(lambda *_a: self._release())

    def _release(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._set_cursor(None)

    def frameless(self) -> bool:
        return bool(self.window.windowFlags() & Qt.FramelessWindowHint)

    def edges_at(self, x: float, y: float) -> Qt.Edge:
        window = self.window
        if not self.frameless() or window.isMaximized() or window.isFullScreen():
            return Qt.Edge(0)
        b, w, h = self.BORDER, window.width(), window.height()
        edges = Qt.Edge(0)
        if x < b:
            edges |= Qt.LeftEdge
        elif x >= w - b:
            edges |= Qt.RightEdge
        if y < b:
            edges |= Qt.TopEdge
        elif y >= h - b:
            edges |= Qt.BottomEdge
        return edges

    @staticmethod
    def cursor_for(edges: Qt.Edge) -> Qt.CursorShape | None:
        if not edges:
            return None
        horizontal = bool(edges & (Qt.LeftEdge | Qt.RightEdge))
        vertical = bool(edges & (Qt.TopEdge | Qt.BottomEdge))
        if horizontal and vertical:
            main_diagonal = edges in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge)
            return Qt.SizeFDiagCursor if main_diagonal else Qt.SizeBDiagCursor
        return Qt.SizeHorCursor if horizontal else Qt.SizeVerCursor

    def _set_cursor(self, shape: Qt.CursorShape | None) -> None:
        if shape is None:
            if self.cursor_set:
                QApplication.restoreOverrideCursor()
                self.cursor_set = False
        elif not self.cursor_set:
            QApplication.setOverrideCursor(shape)
            self.cursor_set = True
        else:
            QApplication.changeOverrideCursor(shape)

    def move_handle_at(self, x: float, y: float) -> QWidget | None:
        """The bar or view header under (x, y) if the point is on a blank part of it."""
        if not self.frameless() or self.window.isFullScreen():
            return None
        widget = self.window.childAt(int(x), int(y))
        while widget is not None and widget is not self.window:
            if isinstance(widget, (Bar, TileHeader)):
                return widget
            if type(widget) not in (QWidget, QLabel, QFrame):
                return None  # a button or another control: it gets the click
            widget = widget.parentWidget()
        return None

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        kind = event.type()
        if kind not in _RESIZER_EVENTS:
            return False
        if kind == _HIDE:
            # Hidden (closed, minimized, frame flags changed) under a resize cursor: no Leave follows.
            if obj is self.window:
                self._set_cursor(None)
            return False
        try:
            handle = self.window.windowHandle()
        except RuntimeError:  # the window is being deleted
            return False
        if handle is None or obj is not handle:
            return False
        if kind == _LEAVE:
            self._set_cursor(None)
            return False
        pos = event.position()
        edges = self.edges_at(pos.x(), pos.y())
        if kind == _MOUSE_MOVE:
            if not event.buttons():
                self._set_cursor(self.cursor_for(edges))
            return False
        if event.button() != Qt.LeftButton:
            return False
        if edges:
            self._set_cursor(None)
            return bool(handle.startSystemResize(edges))
        target = self.move_handle_at(pos.x(), pos.y())
        if target is None:
            return False
        if kind == _DOUBLE_CLICK:
            if self.window.isMaximized():
                self.window.showNormal()
            else:
                self.window.showMaximized()
            return True
        if isinstance(target, TileHeader):
            frame = target.parentWidget()
            frame.manager.focus_tile(frame.name)
        return bool(handle.startSystemMove())


class OutputWindow(QWidget):
    """A top-level window showing one workspace (resizable from its edges when frameless)."""

    def __init__(self, manager: "WindowManager", name: str) -> None:
        super().__init__(None)
        self.manager = manager
        self.name = name
        self.setWindowIcon(app_icon() if not QIcon.themeName() else app_icon())
        self.resize(QSize(1280, 800))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.area = QWidget(self)
        self.area_layout = QVBoxLayout(self.area)
        self.area_layout.setContentsMargins(0, 0, 0, 0)
        self.area_layout.setSpacing(0)
        self.empty = QLabel("Empty workspace — Ctrl+W then N opens a view", self.area)
        self.empty.setObjectName("placeholderTile")
        self.empty.setAlignment(Qt.AlignCenter)
        self.area_layout.addWidget(self.empty)
        self.bar = Bar(manager, name)
        # The body holds the tiling area and, in the main window, the panel
        # sidebar next to it (outside the tiling layout; see ui/panels.py).
        self.body = QSplitter(Qt.Horizontal, self)
        self.body.setObjectName("windowBody")
        self.body.setChildrenCollapsible(False)
        self.body.addWidget(self.area)
        # The body, and in the main window the panel tabs on its outer edge.
        self.middle = QWidget(self)
        row = QHBoxLayout(self.middle)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.body, 1)
        layout.addWidget(self.middle, 1)
        layout.addWidget(self.bar)
        self.resizer = _FramelessResizer(self)
        self.content: QWidget | None = None
        self.style_applied: dict[str, Any] | None = None
        self.saved_geometry = None
        self.closing_allowed = False

    def set_content(self, widget: QWidget | None) -> None:
        if self.content is not None and self.content is not widget:
            self.area_layout.removeWidget(self.content)
        self.content = widget
        if widget is None:
            self.empty.show()
            return
        self.empty.hide()
        if self.area_layout.indexOf(widget) < 0:
            self.area_layout.addWidget(widget, 1)
        widget.show()

    def closeEvent(self, event) -> None:  # noqa: ANN001
        if self.closing_allowed or self.manager.on_output_close(self.name):
            event.accept()
            return
        event.ignore()


class FloatingWindow(QWidget):
    """A floating tile (or a scratchpad tile popped up)."""

    def __init__(self, manager: "WindowManager", tile: str, owner: QWidget | None) -> None:
        super().__init__(owner, Qt.Tool)
        self.manager = manager
        self.tile = tile
        self.setWindowTitle(tile)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.frame: TileFrame | None = None

    def set_frame(self, frame: TileFrame) -> None:
        # apply() unparents every frame first, so re-add it even when it's the same one.
        if self.frame is not frame or frame.parentWidget() is not self:
            self.layout().addWidget(frame)
            self.frame = frame
        frame.show()

    def closeEvent(self, event) -> None:  # noqa: ANN001
        if not self.manager.applying:
            self.manager.on_floating_closed(self.tile)
        event.accept()

    def moveEvent(self, event) -> None:  # noqa: ANN001
        super().moveEvent(event)
        self.manager.remember_floating_geometry(self.tile, self)

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        self.manager.remember_floating_geometry(self.tile, self)
