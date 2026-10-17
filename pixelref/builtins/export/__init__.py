"""Export: render to an image file, or copy the original image files out."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any


MIME_EXTENSIONS = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp", "image/bmp": ".bmp",
    "image/tiff": ".tif",
}


def safe_file_name(name: str) -> str:
    """A bare file name that is valid on Windows (no folders, no reserved characters)."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name.replace("\\", "/").rsplit("/", 1)[-1])
    return name.strip().rstrip(".") or "image"


def asset_file_name(node_name: str, asset: Any) -> str:
    # The stored name comes from the scene file (or a dropped URL): never let it
    # point outside the export folder or contain characters Windows rejects.
    return safe_file_name(asset.name or f"{node_name}{MIME_EXTENSIONS.get(asset.mime, '.png')}")


def free_file_name(path: Path) -> Path:
    candidate, counter = path, 1
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        counter += 1
    return candidate


def activate_ui(ui: Any) -> None:
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from pixelref.plugin_api import CommandError

    session = ui.session
    canvas = ui.canvas
    app = ui.app

    def items_for(nodes: Any) -> list[Any]:
        if nodes is None:
            return canvas.outline_items() or canvas.visible_content_items()
        names = [nodes] if isinstance(nodes, str) else list(nodes)
        return [i for i in (canvas.item(session.scene.resolve(n)) for n in names) if i is not None]

    def render(path: str, nodes: Any = None, opaque: bool | None = None) -> list[int]:
        """Render nodes (default: the selection, else everything) to PNG/JPEG at 1:1. Returns [w, h]."""
        canvas.flush_pending()
        items = items_for(nodes)
        if not items:
            raise CommandError("Nothing to export")
        if opaque is None:
            opaque = Path(path).suffix.lower() in (".jpg", ".jpeg")
        image = canvas.render_items(items, opaque=opaque)
        if image is None or not image.save(str(path)):
            raise CommandError(f"Could not write {path}")
        return [image.width(), image.height()]

    def export_dialog() -> None:
        """Ask where to save, then render the selection (or the whole board) to PNG/JPEG."""
        if not (canvas.outline_items() or canvas.visible_content_items()):
            ui.status("Nothing to export")
            return
        default = session.scene_path.with_suffix(".png") if session.scene_path else Path.cwd() / "export.png"
        path_str, selected_filter = QFileDialog.getSaveFileName(app.main_window(), "Export as Image", str(default),
                                                  "PNG (*.png);;JPEG (*.jpg *.jpeg)")
        if not path_str:
            return
        if not Path(path_str).suffix:
            path_str += ".jpg" if "JPEG" in selected_filter else ".png"  # QImage picks the format from it
        try:
            size = render(path_str)
        except CommandError as exc:
            QMessageBox.warning(app.main_window(), "Export failed", str(exc))
            return
        ui.status(f"Exported {size[0]}×{size[1]} → {Path(path_str).name}")

    def export_originals(folder: str, nodes: Any = None) -> int:
        """Copy the selected (or all) images' original files into a folder."""
        scene = session.scene
        names = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        # Images inside selected groups count too.
        chosen = {d for n in names if scene.has(n) for d in [n, *scene.descendants(n)]}
        # Node types derived from "image" (plugins may add some) have original files too.
        is_image = [n.name for n in scene.iter_nodes() if session.types.inherits(n.node_type, "image")]
        images = [n for n in is_image if n in chosen] or is_image
        Path(folder).mkdir(parents=True, exist_ok=True)
        copied = 0
        written: set[str] = set()  # one file per stored image, however many nodes show it
        for name in images:
            attrs = scene.get_node(name).attrs
            asset_id = str(attrs.get("asset", "") or "")
            asset = scene.get_asset(asset_id)
            if asset is not None:
                if asset_id in written:
                    continue
                written.add(asset_id)
                destination = free_file_name(Path(folder) / asset_file_name(name, asset))
                destination.write_bytes(asset.data)
                copied += 1
                continue
            source = session.resolve_path(str(attrs.get("path", "")))
            if source.is_file():
                if str(source.resolve()) in written:
                    continue
                written.add(str(source.resolve()))
                shutil.copy2(source, free_file_name(Path(folder) / source.name))
                copied += 1
        return copied

    def export_originals_dialog() -> None:
        """Ask for a folder, then write the original image files of the selection (or of every image)."""
        folder = QFileDialog.getExistingDirectory(app.main_window(), "Export Original Images To",
                                                  str(session.base_dir()))
        if not folder:
            return
        copied = app.run_command("export.originals", folder)
        if copied is not None:
            ui.status(f"Exported {copied} image file(s) to {folder}")

    ui.command("render", render, alias="render_image", undoable=False)
    ui.command("originals", export_originals, alias="export_original_images", undoable=False)
    ui.command("dialog", export_dialog, undoable=False, echo=False)
    ui.command("originals_dialog", export_originals_dialog, undoable=False, echo=False)
    ui.action("export.image", "export.dialog", "Export as Image…", keywords="png jpg jpeg render screenshot picture",
              category="File", icon="export")
    ui.action("export.originals", "export.originals_dialog", "Export Original Images…",
              keywords="extract originals files folder", category="File")
    ui.bind("Ctrl+E", "export.image")
    ui.bind("Ctrl+Shift+E", "export.originals")
    ui.menu("canvas", "export.image", path="File", group="file.export", order=0)
    ui.menu("canvas", "export.originals", path="File", group="file.export", order=1)
    ui.menu("canvas", "export.image", group="focus", order=20, when="selection.count > 0")
