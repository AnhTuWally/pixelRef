"""Qt side of image storage: dialogs, menu entries and the inspector rows."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFileDialog, QLabel, QPushButton, QVBoxLayout, QWidget

from pixelref.plugin_api.contracts import InspectorRows, SelectionInfo
from pixelref.plugin_api.ui import widgets as w

from . import asset_file_name


class StorageRows(QWidget):
    def __init__(self, ui: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self.ui = ui
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        title = QLabel("Storage", self)
        title.setObjectName("fieldCaption")
        self.path_label = QLabel("", self)
        self.path_label.setObjectName("pathLabel")
        self.path_label.setWordWrap(True)
        self.embed_btn = QPushButton("Embed", self)
        self.embed_btn.setToolTip("Copy the linked file into the scene file")
        self.extract_btn = QPushButton("Save as file", self)
        self.extract_btn.setToolTip("Write the embedded image to disk and link to it")
        self.link_btn = QPushButton("Link…", self)
        self.link_btn.setToolTip("Use an image file on disk (by path) instead")
        for key, button in (("image_storage.embed_selected", self.embed_btn),
                            ("image_storage.extract_dialog", self.extract_btn),
                            ("image_storage.link_dialog", self.link_btn)):
            button.setObjectName("toolBtn")
            button.clicked.connect(lambda _checked=False, k=key: ui.app.trigger(k))
        layout.addWidget(title)
        layout.addWidget(self.path_label)
        layout.addWidget(w.hrow(self, self.embed_btn, self.extract_btn, self.link_btn))

    def refresh(self, info: SelectionInfo) -> None:
        attrs = info.attrs
        session = self.ui.session
        asset = session.scene.get_asset(str(attrs.get("asset", "") or ""))
        if asset is not None:
            label = f" · {asset.name}" if asset.name else ""
            text = f"Embedded in scene · {w.format_size(asset.size)}{label}"
        else:
            path_value = str(attrs.get("path", ""))
            if not path_value:
                text = "No image"
            else:
                missing = "" if session.resolve_path(path_value).exists() else "  (missing)"
                text = f"Linked file · {path_value}{missing}"
        self.path_label.setText(text)
        embedded = bool(attrs.get("asset"))
        self.embed_btn.setVisible(not embedded and bool(attrs.get("path")))
        self.extract_btn.setVisible(embedded)


def activate_storage_ui(ui: Any) -> None:
    session = ui.session
    app = ui.app
    storage = session.services.require("image_storage")

    def selected_images() -> list[str]:
        return [n for n in session.selection.names if ui.canvas.nodes.get(n, {}).get("type") == "image"]

    def link_dialog() -> None:
        """Choose a file on disk for the selected image to show instead of its embedded data."""
        images = selected_images()
        if len(images) != 1:
            ui.status("Select a single image to link")
            return
        path_str, _ = QFileDialog.getOpenFileName(app.main_window(), "Link Image to File", str(session.base_dir()),
                                                  "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.tif *.tiff)")
        if path_str:
            app.run_command("image_storage.link", images[0], path_str)

    def extract_dialog() -> None:
        """Save the selected embedded image as a file and link to it."""
        images = selected_images()
        asset = session.scene.get_asset(str(ui.canvas.nodes[images[0]]["attrs"].get("asset", "") or "")) \
            if len(images) == 1 else None
        if asset is None:
            ui.status("Select a single embedded image")
            return
        default = session.base_dir() / asset_file_name(images[0], asset)
        path_str, _ = QFileDialog.getSaveFileName(app.main_window(), "Save Image as File", str(default))
        if path_str and app.run_command("image_storage.extract", images[0], path_str) is not None:
            ui.status(f"Image saved to {path_str} and linked")

    def embed_selected() -> None:
        """Embed the selected images' files in the scene."""
        app.run_command("image_storage.embed", selected_images())

    ui.command("link_dialog", link_dialog, undoable=False, echo=False)
    ui.command("extract_dialog", extract_dialog, undoable=False, echo=False)
    ui.command("embed_selected", embed_selected, undoable=False, echo=False)

    def toggle_embed_new() -> None:
        """Switch between embedding new images in the scene and linking them by path."""
        app.run_command("image_storage.set_embed_new", not storage.embed_new)
        app.actions.refresh_states()

    ui.command("toggle_embed_new", toggle_embed_new, undoable=False, echo=False)
    a = ui.action
    a("image_storage.embed_new", "image_storage.toggle_embed_new", "Embed New Images in Scene",
      keywords="package single file store images", category="File", checked=lambda: storage.embed_new)
    a("image_storage.embed_all", "image_storage.embed_all", "Embed All Linked Images",
      keywords="package single file store images", category="File")
    a("image_storage.embed_selected", "image_storage.embed_selected", "Embed in Scene", category="File")
    a("image_storage.link_dialog", "image_storage.link_dialog", "Link to File…", category="File")
    a("image_storage.extract_dialog", "image_storage.extract_dialog", "Save as File && Link…", category="File")

    def has_image() -> bool:
        return bool(selected_images())

    for order, key in enumerate(("image_storage.embed_selected", "image_storage.extract_dialog",
                                 "image_storage.link_dialog")):
        ui.menu("canvas", key, path="Image", group="storage", order=order, when=has_image)
    ui.menu("canvas", "image_storage.embed_new", path="File", group="file.storage", order=0)
    ui.menu("canvas", "image_storage.embed_all", path="File", group="file.storage", order=1)
    ui.contribute("inspector.rows", InspectorRows("image", 50, lambda parent, _i: StorageRows(ui, parent)))
