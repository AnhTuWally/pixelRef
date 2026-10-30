"""Export: render to an image file, copy a flattened image to the clipboard, or copy the original image files out.

PNG exports also carry the exported part of the board as an embedded scene (see
``core.scene_embed``) unless the ``embed_scene_metadata`` setting is off, so
dropping the PNG back onto a board gives the editable content again.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any


EMBED_SETTING = "embed_scene_metadata"  # plugins/export/embed_scene_metadata, on by default

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
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

    from pixelref.plugin_api import CommandError

    session = ui.session
    canvas = ui.canvas
    app = ui.app

    def items_for(nodes: Any) -> list[Any]:
        if nodes is None:
            return canvas.outline_items() or canvas.visible_content_items()
        names = [nodes] if isinstance(nodes, str) else list(nodes)
        return [i for i in (canvas.item(session.scene.resolve(n)) for n in names) if i is not None]

    def export_image_text() -> str:
        return "Export Selected as Image…" if canvas.outline_items() else "Export as Image…"

    def copy_image_text() -> str:
        return "Copy Selected as Image" if canvas.outline_items() else "Copy as Image"

    def embed_enabled() -> bool:
        return bool(ui.settings.value(EMBED_SETTING, True, type=bool))

    def write_editable_png(path: str, image: Any, nodes: Any) -> None:
        """Write the PNG with the exported scene embedded; never falls back to a flat file."""
        from PySide6.QtCore import QBuffer, QIODevice

        from pixelref.core import png_meta, scene_embed
        from pixelref.core.scene_merge import extract_subset

        if nodes is None and not canvas.outline_items():
            names = None  # the whole board, hidden items included
        else:
            names = [i.node_name for i in items_for(nodes)]
        try:
            subset, warnings = extract_subset(session.scene, names, session.resolve_path)
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            if not image.save(buffer, "PNG"):
                raise CommandError(f"Could not write {path}")
            png_meta.write_atomic(path, scene_embed.embed_in_png(bytes(buffer.data()), subset))
        except (scene_embed.EmbedError, OSError) as exc:
            raise CommandError(f"Could not export {Path(path).name} with its scene: {exc}") from exc
        if warnings:
            ui.status(f"Exported; {len(warnings)} image(s) could not be embedded ({warnings[0]})")

    def render(path: str, nodes: Any = None, opaque: bool | None = None, embed_scene: bool | None = None) -> list[int]:
        """Render nodes (default: the selection, else everything) to PNG/JPEG at 1:1. Returns [w, h].

        PNGs carry the exported scene (editable on drop) when ``embed_scene`` is true; by default the
        ``embed_scene_metadata`` setting decides. JPEGs never do.
        """
        canvas.flush_pending()
        items = items_for(nodes)
        if not items:
            raise CommandError("Nothing to export")
        if opaque is None:
            opaque = Path(path).suffix.lower() in (".jpg", ".jpeg")
        image = canvas.render_items(items, opaque=opaque)
        if image is None:
            raise CommandError(f"Could not write {path}")
        if Path(path).suffix.lower() == ".png" and (embed_enabled() if embed_scene is None else embed_scene):
            write_editable_png(path, image, nodes)
        elif not image.save(str(path)):
            raise CommandError(f"Could not write {path}")
        return [image.width(), image.height()]

    def set_embed_scene_metadata(enabled: bool) -> bool:
        """Turn embedding the scene in exported PNGs on or off (a saved preference)."""
        ui.settings.setValue(EMBED_SETTING, bool(enabled))
        app.actions.refresh_states()
        return bool(enabled)

    def toggle_embed_scene_metadata() -> None:
        set_embed_scene_metadata(not embed_enabled())

    def export_dialog() -> None:
        """Ask where to save, then render the selection (or the whole board) to PNG/JPEG."""
        if not (canvas.outline_items() or canvas.visible_content_items()):
            ui.status("Nothing to export")
            return
        is_selection = bool(canvas.outline_items())
        title = "Export Selected as Image" if is_selection else "Export as Image"
        if session.scene_path is not None:
            default = session.scene_path.with_suffix(".png")
        else:
            from ..file import save_dir

            default = save_dir(ui.app.settings.scoped("plugins/file")) / "export.png"
        path_str, selected_filter = QFileDialog.getSaveFileName(app.main_window(), title, str(default),
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
        selected_note = "selected " if is_selection else ""
        ui.status(f"Exported {selected_note}{size[0]}×{size[1]} → {Path(path_str).name}")

    def copy_image(nodes: Any = None, opaque: bool = False) -> list[int]:
        """Flatten nodes (default: the selection, else everything) and put the image on the clipboard."""
        canvas.flush_pending()
        items = items_for(nodes)
        if not items:
            raise CommandError("Nothing to copy")
        image = canvas.render_items(items, opaque=opaque)
        if image is None:
            raise CommandError("Nothing to copy")
        QApplication.clipboard().setImage(image)
        return [image.width(), image.height()]

    def copy_image_ui() -> None:
        """Flatten the selection (or the whole board) and copy it to the clipboard as an image."""
        if not (canvas.outline_items() or canvas.visible_content_items()):
            ui.status("Nothing to copy")
            return
        is_selection = bool(canvas.outline_items())
        try:
            size = copy_image()
        except CommandError as exc:
            ui.status(str(exc))
            return
        selected_note = "selected " if is_selection else ""
        ui.status(f"Copied {selected_note}{size[0]}×{size[1]} image to clipboard")

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
    ui.command("set_embed_scene_metadata", set_embed_scene_metadata, undoable=False)
    ui.command("toggle_embed_scene_metadata", toggle_embed_scene_metadata, undoable=False, echo=False)
    ui.command("originals", export_originals, alias="export_original_images", undoable=False)
    ui.command("dialog", export_dialog, undoable=False, echo=False)
    ui.command("originals_dialog", export_originals_dialog, undoable=False, echo=False)
    ui.command("copy_image", copy_image, alias="copy_image_to_clipboard", undoable=False)
    ui.command("copy_image_ui", copy_image_ui, undoable=False, echo=False)
    ui.action("export.image", "export.dialog", "Export as Image…", dynamic_text=export_image_text,
              keywords="png jpg jpeg render screenshot picture selected", category="File", icon="export")
    ui.action("export.originals", "export.originals_dialog", "Export Original Images…",
              keywords="extract originals files folder", category="File")
    ui.action("export.copy_image", "export.copy_image_ui", "Copy as Image", dynamic_text=copy_image_text,
              keywords="clipboard flatten merge copy png screenshot picture selected", category="Edit", icon="copy")
    ui.action("export.embed_scene", "export.toggle_embed_scene_metadata", "Embed Editable Scene in PNG Exports",
              keywords="png metadata editable scene embed drag back reimport size", category="File",
              checked=embed_enabled)
    ui.bind("Ctrl+E", "export.image")
    ui.bind("Ctrl+Shift+E", "export.originals")
    ui.bind("Ctrl+Alt+C", "export.copy_image")
    ui.menu("canvas", "export.image", path="File", group="file.export", order=0)
    ui.menu("canvas", "export.originals", path="File", group="file.export", order=1)
    ui.menu("canvas", "export.copy_image", path="File", group="file.export", order=2)
    ui.menu("canvas", "export.embed_scene", path="File", group="file.export", order=3)
    ui.menu("canvas", "export.image", group="focus", order=20, when="selection.count > 0")
    ui.menu("canvas", "export.copy_image", group="focus", order=21, when="selection.count > 0")
