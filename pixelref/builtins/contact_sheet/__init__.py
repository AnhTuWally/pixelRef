"""Contact sheet: every image on the board as a grid of thumbnails.

Click selects (Ctrl/Shift extend), double-click frames the image in the
active canvas view. The grid follows the scene and the selection.
"""

from __future__ import annotations

from typing import Any

THUMB = 128


def activate_ui(ui: Any) -> None:
    from PySide6.QtCore import QSize, Qt, QTimer
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem, QSlider, QVBoxLayout, QWidget

    from pixelref.plugin_api.ui import ViewType

    session = ui.session

    class ContactSheet(QWidget):
        def __init__(self) -> None:
            super().__init__()
            layout = QVBoxLayout(self)
            layout.setContentsMargins(4, 4, 4, 4)
            self.grid = QListWidget(self)
            self.grid.setObjectName("contactSheet")
            self.grid.setViewMode(QListWidget.IconMode)
            self.grid.setResizeMode(QListWidget.Adjust)
            self.grid.setMovement(QListWidget.Static)
            self.grid.setUniformItemSizes(True)
            self.grid.setSpacing(6)
            self.grid.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.grid.itemSelectionChanged.connect(self._on_grid_selection)
            self.grid.itemDoubleClicked.connect(self._on_double_click)
            self.size_slider = QSlider(Qt.Horizontal, self)
            self.size_slider.setRange(48, 256)
            self.size_slider.setValue(int(ui.settings.value("thumb_size", THUMB, type=int)))
            self.size_slider.valueChanged.connect(self._on_size)
            layout.addWidget(self.grid, 1)
            layout.addWidget(self.size_slider)
            self.items: dict[str, QListWidgetItem] = {}
            self._syncing = False
            self._signature: list[tuple[str, str, str]] = []
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.timeout.connect(self.refresh)
            self._on_size(self.size_slider.value(), save=False)
            self.refresh()

        def schedule(self) -> None:
            self._timer.start(60)

        def _on_size(self, value: int, save: bool = True) -> None:
            self.grid.setIconSize(QSize(value, value))
            self.grid.setGridSize(QSize(value + 16, value + 34))
            if save:
                ui.settings.setValue("thumb_size", int(value))
            self._signature = []
            self.schedule()

        def image_nodes(self) -> list[str]:
            return [n.name for n in session.scene.iter_nodes() if session.types.inherits(n.node_type, "image")]

        def refresh(self) -> None:
            cache = ui.service("image_cache")
            names = self.image_nodes()
            signature = [(name, str(session.scene.get_attr(name, "asset") or ""),
                          str(session.scene.get_attr(name, "path") or "")) for name in names]
            if signature != self._signature:
                self._signature = signature
                self._syncing = True
                self.grid.clear()
                self.items.clear()
                size = self.size_slider.value()
                for name in names:
                    item = QListWidgetItem(name)
                    item.setData(Qt.UserRole, name)
                    item.setToolTip(name)
                    if cache is not None:
                        pixmap = cache.thumbnail(session.scene.nodes[name].attrs, size)
                        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
                            item.setIcon(QIcon(pixmap.scaled(size, size, Qt.KeepAspectRatio,
                                                             Qt.SmoothTransformation)))
                    self.grid.addItem(item)
                    self.items[name] = item
                self._syncing = False
            self.sync_selection()

        def sync_selection(self) -> None:
            self._syncing = True
            try:
                selected = set(session.selection.names)
                for name, item in self.items.items():
                    item.setSelected(name in selected)
            finally:
                self._syncing = False

        def _on_grid_selection(self) -> None:
            if self._syncing:
                return
            names = [item.data(Qt.UserRole) for item in self.grid.selectedItems()]
            ui.app.run_command("select", names)

        def _on_double_click(self, item: QListWidgetItem) -> None:
            name = item.data(Qt.UserRole)
            if session.commands.exists("navigation.frame"):
                ui.app.run_command("navigation.frame", [name])

    sheets: list[ContactSheet] = []

    def make(_tile: str, _state: dict[str, Any]) -> ContactSheet:
        sheet = ContactSheet()
        sheets.append(sheet)
        return sheet

    def dispose(widget: Any) -> None:
        if widget in sheets:
            sheets.remove(widget)

    def on_change(_event: Any) -> None:
        for sheet in sheets:
            sheet.schedule()

    def on_selection(_event: Any) -> None:
        for sheet in sheets:
            sheet.sync_selection()

    for event in ("document.changed", "document.replaced", "scene.opened"):
        ui.on(event, on_change)
    ui.on("selection.changed", on_selection)
    ui.view_type(ViewType(id="contact_sheet", title="Contact Sheet", icon="image", factory=make, dispose=dispose,
                          placement="tabbed", min_width=160))
    ui.provide_service("contact_sheets", sheets)
