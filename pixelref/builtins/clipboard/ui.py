"""Qt side of the clipboard plugin: the system clipboard and the ingest service."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QMimeData, QPointF
from PySide6.QtWidgets import QApplication

from pixelref.plugin_api import CommandError

from . import NODES_MIME, instantiate, serialize_subtrees


class IngestService:
    """What canvas views call for drag-and-drop (and the clipboard for pastes)."""

    def __init__(self, ui: Any) -> None:
        self.ui = ui

    def handlers(self) -> list[Any]:
        return sorted(self.ui.contributions("ingest.handlers"), key=lambda h: h.order)

    def accepts(self, mime: QMimeData) -> bool:
        return mime.hasUrls() or mime.hasImage() or mime.hasText() or mime.hasHtml()

    def describe(self, mime: QMimeData) -> str:
        for handler in self.handlers():
            if handler.accepts(mime):
                text = handler.describe(mime)
                if text:
                    return text
        return ""

    def drop(self, view: Any, scene_pos: QPointF, mime: QMimeData) -> None:
        self.ingest("Drop", mime, scene_pos, view)

    def ingest(self, label: str, mime: QMimeData, scene_pos: QPointF, view: Any = None) -> list[str]:
        session = self.ui.session
        self.ui.canvas.flush_pending()
        before = set(session.scene.nodes)
        result: Any = None
        with session.undo.chunk(label):
            for handler in self.handlers():
                if not handler.accepts(mime):
                    continue
                result = handler.ingest(view, scene_pos, mime)
                if result is not None:
                    break
        session.notify_changed(label.lower())
        created = [name for name in session.scene.nodes if name not in before]
        if created:
            self.ui.app.run_command("select", created)
            images, notes = result if isinstance(result, tuple) else (0, 0)
            self.ui.status(f"{label}: {images} image(s), {notes} note(s)")
        elif result != "handled":
            self.ui.status(f"{label} ignored: no supported image/text content")
        return created


def activate_clipboard_ui(ui: Any) -> None:
    session = ui.session
    canvas = ui.canvas
    ingest = IngestService(ui)
    ui.provide_service("ingest", ingest)

    def copy() -> int:
        """Copy the selection (with embedded images) to the clipboard."""
        canvas.flush_pending()
        names = canvas.top_selected_names()
        if not names:
            raise CommandError("Nothing selected")
        payload = serialize_subtrees(session, names, scene_space=True)
        mime = QMimeData()
        mime.setData(NODES_MIME, json.dumps(payload).encode("utf-8"))
        for exporter in ui.contributions("clipboard.exporters"):
            exporter(names, mime)
        QApplication.clipboard().setMimeData(mime)
        ui.status(f"Copied {len(names)} item(s)")
        return len(names)

    def cut() -> None:
        """Copy the selection to the clipboard, then delete it."""
        if copy():
            ui.run("clipboard.delete")

    def paste_payload(payload: dict[str, Any], scene_pos: QPointF) -> list[str]:
        roots = instantiate(session, payload)
        canvas.refresh(force=True)  # the copies' bounds come from their items
        items = [canvas.item(n) for n in roots]
        items = [i for i in items if i is not None and i.is_content]
        bounds = canvas.scene_bounds(items)
        if not bounds.isNull():
            center = bounds.center()
            dx, dy = scene_pos.x() - center.x(), scene_pos.y() - center.y()
            for name in roots:
                node = session.scene.get_node(name)
                if "translate" in node.attrs:
                    session.run("set_transform", name, translate=[dx, dy], relative=True, world_space=True)
        session.selection.set(roots)
        canvas.refresh(force=True)
        return roots

    def paste() -> list[str]:
        """Paste nodes copied in pixelRef, or images/text from other apps."""
        mime = QApplication.clipboard().mimeData()
        if mime is None:
            return []
        position = ui.app.scene_pos_for_new_content()
        ui.app.menu_scene_pos = None
        if mime.hasFormat(NODES_MIME):
            try:
                payload = json.loads(bytes(mime.data(NODES_MIME)).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                payload = None
            if payload:
                return session.run("clipboard.paste_nodes", payload, [position.x(), position.y()])
        return ingest.ingest("Paste", mime, position)

    def paste_nodes(payload: dict[str, Any], position: Any) -> list[str]:
        """Add copied nodes (a clipboard payload) centred on ``position``; returns the new root names."""
        return paste_payload(payload, QPointF(float(position[0]), float(position[1])))

    ui.command("copy", copy, alias="copy", undoable=False)
    ui.command("cut", cut, alias="cut", undoable=False)
    ui.command("paste", paste, alias="paste", undoable=False)
    ui.command("paste_nodes", paste_nodes, label="Paste", echo=False)

    a = ui.action
    a("clipboard.copy", "clipboard.copy", "Copy", keywords="clipboard", category="Edit", icon="copy")
    a("clipboard.cut", "clipboard.cut", "Cut", keywords="clipboard remove", category="Edit", icon="cut")
    a("clipboard.paste", "clipboard.paste", "Paste", keywords="clipboard insert image", category="Edit",
      icon="paste")
    a("clipboard.duplicate", "clipboard.duplicate", "Duplicate", keywords="copy clone repeat", category="Edit",
      icon="duplicate")
    a("clipboard.delete", "clipboard.delete", "Delete", keywords="remove erase trash", category="Edit",
      icon="delete")
    for key, action in (("Ctrl+C", "clipboard.copy"), ("Ctrl+X", "clipboard.cut"), ("Ctrl+V", "clipboard.paste"),
                        ("Ctrl+D", "clipboard.duplicate"), ("Del", "clipboard.delete"),
                        ("Backspace", "clipboard.delete")):
        ui.bind(key, action)
    for order, key in enumerate(("clipboard.copy", "clipboard.cut", "clipboard.paste", "clipboard.duplicate",
                                 "clipboard.delete")):
        ui.menu("canvas", key, group="clipboard", order=order, when="selection.count > 0")
    ui.menu("canvas", "clipboard.paste", group="create", order=0, when="selection.count == 0")
