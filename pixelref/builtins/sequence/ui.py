"""The Qt side of image sequences: the item factory, drop handling, actions."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QLabel

from pixelref.plugin_api.contracts import IngestHandler, InspectorSection, PaletteButton, SelectionInfo
from pixelref.plugin_api.ui import widgets as w

from . import ARRANGE_GAP, DEFAULT_SIZE, seqscan
from .items import SequenceItem

VIEW_ATTRS = ("exr_layer", "exposure", "gamma", "channel")  # the exr plugin's view, applied to every frame
_SCANS: dict[tuple[str, int], seqscan.SeqInfo] = {}  # (pattern, folder mtime) -> frames on disk


def scan_cached(pattern: str) -> seqscan.SeqInfo:
    """``seqscan.scan``, again only when the folder changed (items are rebuilt on every edit)."""
    folder = os.path.dirname(pattern) or "."
    try:
        stamp = os.stat(folder).st_mtime_ns
    except OSError:
        return seqscan.SeqInfo(pattern, [])
    key = (pattern, stamp)
    info = _SCANS.get(key)
    if info is None:
        if len(_SCANS) > 256:
            _SCANS.clear()
        info = _SCANS[key] = seqscan.scan(pattern)
    return info


class SequenceSection(w.InspectorSection):
    """The selected sequence's files on disk, with Refresh Frames and Show Folder."""

    def __init__(self, ui: Any, parent: Any) -> None:
        super().__init__("sequence_files", "Sequence", parent)
        self.ui = ui
        self._name: str | None = None
        self.files = QLabel(self)
        self.files.setObjectName("dimLabel")
        self.files.setWordWrap(True)
        self.form.addRow(self.files)
        refresh = w.make_tool_button("Refresh Frames", "Look for new frames on disk (Shift+F5)", width=110,
                                     parent=self)
        refresh.clicked.connect(lambda: self._name and ui.app.run_command("sequence.refresh_frames", [self._name]))
        folder = w.make_tool_button("Show Folder", "Show the frames in the file browser", width=90, parent=self)
        folder.clicked.connect(lambda: ui.app.trigger("sequence.open_folder"))
        self.form.addRow(w.hrow(self, refresh, None, folder))

    def refresh(self, info: SelectionInfo) -> None:
        self._name = info.names[0] if info.single else None
        if self._name is None:
            return
        api = self.ui.session.services.require("sequences")
        attrs = self.ui.session.scene.get_node(self._name).attrs
        pattern = api.pattern(attrs)
        found = scan_cached(pattern) if pattern else seqscan.SeqInfo("", [])
        if not found.frames:
            self.files.setText(f"{Path(pattern).name if pattern else 'No pattern'} · no frames on disk")
            return
        first, last = int(attrs.get("first", 1)), int(attrs.get("last", 1))
        missing = len(found.missing(first, last))
        beyond = len([n for n in found.frames if n > last or n < first])
        text = f"{Path(pattern).name} · on disk {found.first}–{found.last} ({len(found.frames)} frames)"
        if missing:
            text += f" · {missing} missing in {first}–{last}"
        if beyond:
            text += f" · {beyond} outside the range"
        self.files.setText(text)


def activate_sequence_ui(ui: Any) -> None:
    session = ui.session
    canvas = ui.canvas
    api = session.services.require("sequences")
    images = session.services.require("images")
    cache = session.services.require("image_cache")
    ui.declare_extension_point("sequence.started",
                               "callable(item): called when a sequence item is (re)built, like "
                               "movie.started, so playback's frame display and filmstrip follow it")

    headless_sizer = api.sizer

    def ui_size(attrs: dict[str, Any]) -> tuple[float, float] | None:
        first = api.first_file(attrs)
        size = cache.size({"path": str(first)}) if first is not None else None
        return (float(size[0]), float(size[1])) if size else headless_sizer(attrs)

    api.sizer = ui_size
    ui.track(lambda: setattr(api, "sizer", headless_sizer))

    # ---- Items ---------------------------------------------------------------------------------

    def factory(_canvas: Any, node: dict[str, Any]) -> SequenceItem:
        attrs = node.get("attrs", {})
        pattern = api.pattern(attrs)
        info = scan_cached(pattern) if pattern else seqscan.SeqInfo("", [])
        view = {key: attrs[key] for key in VIEW_ATTRS if key in attrs}

        def frame_handle(frame: int) -> Any:
            return cache.shared_handle({"path": seqscan.frame_path(pattern, frame), **view})

        first = int(attrs.get("first", 1) or 1)
        last = int(attrs.get("last", first) or first)
        placeholder = None if info.frames else cache.placeholder(Path(pattern).name if pattern else "")
        item = SequenceItem(node["name"], frame_handle, first, last, info.frames, fps=float(attrs.get("fps", 24.0)),
                            loop=bool(attrs.get("loop", True)), missing=str(attrs.get("missing", "hold")),
                            placeholder=placeholder)
        item.cache_key = ("sequence", pattern, first, last, tuple(sorted(view.items())))
        item.final_render = lambda: canvas.rendering_final > 0
        for hook in ui.contributions("sequence.started"):
            try:
                hook(item)
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).exception("sequence.started contribution failed")
        if bool(attrs.get("autoplay", False)) and not item.is_playing():
            playback = session.services.get("playback")
            if playback is None or playback.paused_frame(node["name"]) is None:
                item.play()
        return item

    def describe(name: str, node: dict[str, Any]) -> list[str]:
        attrs = node.get("attrs", {})
        item = canvas.item(name)
        pattern = api.pattern(attrs)
        if pattern is None:
            return ["no file pattern"]
        first, last = int(attrs.get("first", 1)), int(attrs.get("last", 1))
        info = scan_cached(pattern)
        if not info.frames:
            return [Path(pattern).name, "missing"]
        missing = len(info.missing(first, last))
        parts = [f"{first}–{last}", f"{last - first + 1} frames"]
        if missing:
            parts.append(f"{missing} missing")
        parts.append(Path(pattern).suffix.lstrip(".").upper() or "image")
        if isinstance(item, SequenceItem) and not item.is_missing():
            size = item.image_size()
            parts.append(f"{size.width()} × {size.height()} px")
        parts.append(f"{float(attrs.get('fps', 24.0)):g} fps")
        return parts

    def double_click(view: Any, item: Any) -> bool:
        bounds = canvas.scene_bounds([item])
        if not bounds.isNull():
            view.fit_rect(bounds)
        return True

    ui.node_ui("sequence", factory=factory, label="Sequence", icon="movie", badge=("#9b6ec9", "S"),
               describe=describe, double_click=double_click)

    # ---- Drops -----------------------------------------------------------------------------------

    def dropped(mime: Any) -> tuple[list[Path], bool]:
        """The image files of a drop (folders give their images) and whether it held anything else."""
        exts = images.supported_exts()
        files: list[Path] = []
        other = False
        for url in mime.urls() if mime.hasUrls() else []:
            if not url.isLocalFile():
                other = True
                continue
            path = Path(url.toLocalFile())
            if path.is_dir():
                files.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in exts))
            elif path.suffix.lower() in exts:
                files.append(path)
            else:
                other = True
        return files, other

    def split_drop(mime: Any) -> tuple[list[seqscan.Group], list[Path]] | None:
        if not api.detect_on_drop or not mime.hasUrls():
            return None
        files, other = dropped(mime)
        if other or len(files) < 2:
            return None  # movies, single frames, …: the other handlers
        groups, singles = seqscan.group(files)
        return (groups, singles) if groups else None

    def describe_drop(mime: Any) -> str | None:
        found = split_drop(mime)
        if found is None:
            return None
        groups, singles = found
        text = f"Drop to add {len(groups)} image sequence{'s' if len(groups) != 1 else ''}"
        return text + (f" and {len(singles)} image{'s' if len(singles) != 1 else ''}" if singles else "")

    def ingest(_view: Any, pos: QPointF, mime: Any) -> Any:
        found = split_drop(mime)
        if found is None:
            return None
        groups, singles = found
        names = api.create_row([g.pattern for g in groups], pos.x(), pos.y())
        if singles:
            heights = [(api.sizer(session.scene.get_node(n).attrs) or DEFAULT_SIZE)[1] for n in names]
            below = pos.y() + max(heights) / 2.0 + ARRANGE_GAP * 4 + 100.0
            names += images.create_row(singles, pos.x(), below)
        session.selection.set(names)
        return len(names), 0

    # Before image.files (10): dropped frames become one sequence, not a row of images.
    handler = IngestHandler("sequence.files", 8, lambda m: bool(m.hasUrls()), describe_drop, ingest)
    ui.contribute("ingest.handlers", handler, order=handler.order)

    # ---- Actions ---------------------------------------------------------------------------------

    def selected_sequences() -> list[str]:
        return [n for n in session.selection.names if canvas.nodes.get(n, {}).get("type") == "sequence"]

    def insert_dialog() -> None:
        """Choose one frame of each sequence to add; the frame range is found on disk."""
        exts = " ".join(f"*{ext}" for ext in sorted(images.supported_exts()))
        paths, _ = QFileDialog.getOpenFileNames(ui.app.main_window(), "Insert Image Sequence (pick one frame)", "",
                                                f"Images ({exts})")
        if not paths:
            return
        patterns: list[str] = []
        for path in paths:
            try:
                pattern = api.source_from_path(path)["path"]
            except Exception as exc:  # noqa: BLE001  (CommandError: not numbered, no frames)
                ui.status(str(exc))
                continue
            if pattern not in patterns:
                patterns.append(pattern)
        if not patterns:
            return
        view = ui.app.active_view()
        center = view.center() if view is not None else QPointF()
        names = ui.app.run_command("sequence.import_sequences", patterns, [center.x(), center.y()])
        if names:
            ui.status(f"Added {len(names)} image sequence(s) (linked, not embedded)")

    def open_folder() -> None:
        """Show the selected sequence's folder in the file browser."""
        for name in selected_sequences()[:1]:
            pattern = api.pattern(session.scene.get_node(name).attrs)
            if pattern:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(pattern)))

    def refresh_frames(nodes: Any = None) -> int:
        """Look at the frame files again: new frames past the end extend the range, frames that
        appeared inside it show. Selected sequences, or every sequence when none is selected.
        Returns how many frames were found that weren't there before."""
        scene = session.scene
        if nodes is not None:
            names = [scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        else:
            names = selected_sequences() or [n.name for n in scene.iter_nodes() if n.node_type == "sequence"]
        names = [n for n in names if scene.get_node(n).node_type == "sequence"]
        if not names:
            ui.status("No image sequence on the board")
            return 0
        before = 0
        for name in names:
            item = canvas.item(name)
            if isinstance(item, SequenceItem):
                before += len(item.frames_on_disk())  # the frames it plays so far
            pattern = api.pattern(scene.get_node(name).attrs)
            for key in [k for k in _SCANS if k[0] == pattern]:
                del _SCANS[key]  # look again even if the folder's time didn't change (network drives)
        ui.app.run_command("sequence.rescan", names)
        after = sum(len(scan_cached(api.pattern(scene.get_node(n).attrs) or "").frames) for n in names)
        canvas.request_full_refresh()  # frames inside the range: the items list them when built
        added = max(0, after - before)
        what = f"{len(names)} sequence{'s' if len(names) != 1 else ''}"
        ui.status(f"Refreshed {what}: {after} frame(s) on disk" + (f", {added} new" if added else ", nothing new"))
        return added

    def toggle_detect() -> None:
        """Switch between dropped numbered frames becoming sequences and separate images."""
        ui.app.run_command("sequence.set_detect", not api.detect_on_drop)
        ui.app.actions.refresh_states()

    ui.command("insert_dialog", insert_dialog, undoable=False)
    ui.command("open_folder", open_folder, undoable=False, echo=False)
    ui.command("toggle_detect", toggle_detect, undoable=False, echo=False)
    ui.action("sequence.insert", "sequence.insert_dialog", "Insert Image Sequence…", icon="movie",
              keywords="add import frames render exr png numbered sequence", category="File")
    ui.command("refresh_frames", refresh_frames, undoable=False)
    ui.action("sequence.rescan", "sequence.refresh_frames", "Refresh Frames",
              keywords="rescan reload frames render update range new sequence", category="Image")
    ui.bind("Shift+F5", "sequence.rescan", scope="canvas")
    ui.action("sequence.open_folder", "sequence.open_folder", "Show Sequence Folder",
              keywords="reveal explorer finder frames sequence", category="Image")
    ui.action("sequence.detect", "sequence.toggle_detect", "Dropped Frames Become Sequences",
              keywords="detect numbered frames sequence drop", category="File", checked=lambda: api.detect_on_drop)

    def has_sequence() -> bool:
        return bool(selected_sequences())

    ui.menu("canvas", "sequence.insert", path="File", group="file.import", order=2)
    ui.menu("canvas", "sequence.detect", path="File", group="file.storage", order=5)
    for order, key in enumerate(("sequence.rescan", "sequence.open_folder")):
        ui.menu("canvas", key, path="Image", group="sequence", order=order, when=has_sequence)
    ui.contribute("palette.buttons", PaletteButton("sequence.insert", "Sequence", "movie", order=12))
    ui.contribute("inspector.sections", InspectorSection(
        key="sequence_files", title="Sequence", order=30, generic=True,  # keeps the Attributes editor
        applies=lambda info: info.single and info.node["type"] == "sequence",
        build=lambda parent, _inspector: SequenceSection(ui, parent),
    ))
