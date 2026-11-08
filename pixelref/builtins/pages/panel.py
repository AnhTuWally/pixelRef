"""The Pages panel: every page with a thumbnail, in page order. Drag to reorder,
double-click to fit the view to a page, and export the PDF from here."""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton,
                               QVBoxLayout, QWidget)

from pixelref.plugin_api.ui import PanelType

from . import FREE

THUMB = QSize(96, 68)
NAME_ROLE = Qt.UserRole + 1


def render_thumbnail(canvas: Any, item: Any, hide: list[Any], size: QSize = THUMB) -> QPixmap:
    """What the page shows, fitted into ``size`` (preview quality: never waits for slow effects)."""
    rect = item.scene_content_rect()
    pixmap = QPixmap(size)
    pixmap.fill(Qt.transparent)
    if rect.width() <= 0 or rect.height() <= 0:
        return pixmap
    scale = min(size.width() / rect.width(), size.height() / rect.height())
    w, h = max(1, round(rect.width() * scale)), max(1, round(rect.height() * scale))
    image = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor(*canvas.app.background_spec().color))
    with canvas.isolated_render(None, hide=hide, final=False):
        painter = QPainter(image)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        canvas.scene.render(painter, image.rect(), rect, Qt.KeepAspectRatio)
        painter.end()
    painter = QPainter(pixmap)
    x, y = (size.width() - w) // 2, (size.height() - h) // 2
    painter.drawImage(x, y, image)
    painter.setPen(QColor(item.color))
    painter.drawRect(x, y, w - 1, h - 1)
    painter.end()
    return pixmap


class PageList(QListWidget):
    """Internal drag and drop reorders the rows; ``on_reorder(names)`` hears the new order."""

    def __init__(self, parent: QWidget, on_reorder: Callable[[list[str]], None]) -> None:
        super().__init__(parent)
        self.on_reorder = on_reorder
        self.setIconSize(THUMB)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSpacing(2)

    def dropEvent(self, event) -> None:  # noqa: ANN001
        super().dropEvent(event)
        names = [self.item(i).data(NAME_ROLE) for i in range(self.count())]
        QTimer.singleShot(0, lambda: self.on_reorder(names))


class PagesPanel(QWidget):
    def __init__(self, ui: Any, frame_items: Callable[[], list[Any]], go_to: Callable[..., Any]) -> None:
        super().__init__()
        self.ui = ui
        self.frame_items = frame_items
        self.go_to = go_to
        self._syncing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.list = PageList(self, self._reorder)
        self.list.itemDoubleClicked.connect(lambda row: go_to(row.data(NAME_ROLE)))
        self.list.itemSelectionChanged.connect(self._select)
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        add = QPushButton("+ From View", self)
        add.setToolTip("Add a page framing what the view shows (Ctrl+Alt+P)")
        add.clicked.connect(lambda: ui.app.trigger("pages.add_from_view"))
        export = QPushButton("Export PDF…", self)
        export.setToolTip("Export the pages as one PDF (Ctrl+Shift+P)")
        export.clicked.connect(lambda: ui.app.trigger("pages.export_pdf"))
        buttons.addWidget(add)
        buttons.addWidget(export)
        layout.addLayout(buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    def schedule(self) -> None:
        if self.isVisible():
            self._timer.start()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        self.schedule()

    def refresh(self) -> None:
        canvas = self.ui.canvas
        items = self.frame_items()
        hide = list(items)
        selected = set(self.ui.session.selection.names)
        self._syncing = True
        try:
            self.list.clear()
            for number, item in enumerate(items, start=1):
                paper = "Free" if item.paper == FREE else f"{item.paper} {item.orientation}"
                text = f"{number}.  {item.title or item.node_name}\n{paper}"
                if not item.export:
                    text += " · not exported"
                row = QListWidgetItem(QIcon(render_thumbnail(canvas, item, hide)), text)
                row.setData(NAME_ROLE, item.node_name)
                row.setToolTip("Double-click: fit the view to this page · drag: change the page order")
                if not item.export:
                    row.setForeground(QColor("#7d8590"))
                self.list.addItem(row)
                row.setSelected(item.node_name in selected)
            if not items:
                hint = QListWidgetItem("No pages yet.\nAlt+P: draw a page\nCtrl+Alt+P: frame the view")
                hint.setFlags(Qt.NoItemFlags)
                self.list.addItem(hint)
        finally:
            self._syncing = False

    def sync_selection(self) -> None:
        selected = set(self.ui.session.selection.names)
        self._syncing = True
        try:
            for i in range(self.list.count()):
                row = self.list.item(i)
                name = row.data(NAME_ROLE)
                if name:
                    row.setSelected(name in selected)
        finally:
            self._syncing = False

    def _select(self) -> None:
        if self._syncing:
            return
        names = [row.data(NAME_ROLE) for row in self.list.selectedItems() if row.data(NAME_ROLE)]
        if names:
            self.ui.session.selection.set(names)
            self.ui.app.update_views()

    def _reorder(self, names: list[str]) -> None:
        names = [n for n in names if n]
        if names:
            self.ui.app.run_command("pages.reorder", names)


def activate_pages_panel(ui: Any, frame_items: Callable[[], list[Any]], go_to: Callable[..., Any]) -> None:
    panels: list[PagesPanel] = []

    def make_panel(_parent: Any = None) -> PagesPanel:
        panel = PagesPanel(ui, frame_items, go_to)
        panels.append(panel)
        return panel

    def dispose_panel(panel: QWidget) -> None:
        if panel in panels:
            panels.remove(panel)

    ui.panel(PanelType(id="pages", title="Pages", icon="frame", color="#ffaa3c", factory=make_panel,
                       dispose=dispose_panel, order=35, default_mode="hidden", popup_size=(280, 460),
                       float_size=(260, 420), stretch=2, keywords="pages pdf export print order slides"))
    ui.on_ui("canvas.refreshed", lambda _e: [p.schedule() for p in list(panels)])
    ui.on("selection.changed", lambda _e: [p.sync_selection() for p in list(panels)])
