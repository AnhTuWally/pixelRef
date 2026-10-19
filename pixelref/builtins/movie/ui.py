"""The Qt side of the movie plugin.

Movies are inserted and dropped exactly like images, except there is no
embed option: a movie node's only source of pixels is its linked file
(``path``), so dropping or inserting a video always creates a *linked*
reference to that file on disk, never a copy stored in the scene.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QFileDialog

from pixelref.plugin_api.contracts import IngestHandler, PaletteButton

from . import SUPPORTED_MOVIE_EXTS
from .items import MovieItem


def activate_movie_ui(ui: Any) -> None:
    session = ui.session
    canvas = ui.canvas
    api = session.services.require("movies")
    ui.declare_extension_point("movie.started",
                               "callable(item): called when a movie item is (re)built — "
                               "analogous to image.movie_hooks, so the playback plugin's live "
                               "frame display and filmstrip playhead follow movies too")

    def factory(_canvas: Any, node: dict[str, Any]) -> MovieItem:
        attrs = node.get("attrs", {})
        path_value = str(attrs.get("path", "") or "")
        path = session.resolve_path(path_value) if path_value else None
        item = MovieItem(node["name"], path, muted=bool(attrs.get("muted", False)),
                         loop=bool(attrs.get("loop", True)), volume=float(attrs.get("volume", 1.0)),
                         autoplay=bool(attrs.get("autoplay", True)))
        item.cache_key = (path_value, "movie")
        for hook in ui.contributions("movie.started"):
            try:
                hook(item)
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).exception("movie.started contribution failed")
        return item

    def describe(name: str, node: dict[str, Any]) -> list[str]:
        attrs = node.get("attrs", {})
        parts = []
        item = canvas.item(name)
        if isinstance(item, MovieItem):
            if item.is_missing():
                parts.append("missing")
            else:
                size = item.native_size()
                if size.width() and size.height():
                    parts.append(f"{size.width()} × {size.height()} px")
                duration = item.duration_ms()
                if duration:
                    parts.append(f"{duration // 60000:d}:{(duration // 1000) % 60:02d}")
        if attrs.get("path"):
            parts.append("linked file")  # movies are never embedded — unlike images, no other option
        return parts

    def double_click(view: Any, item: Any) -> bool:
        bounds = canvas.scene_bounds([item])
        if not bounds.isNull():
            view.fit_rect(bounds)
        return True

    ui.node_ui("movie", factory=factory, label="Movie", icon="movie", badge=("#6ea8c9", "M"),
               describe=describe, double_click=double_click)

    # No dedicated inspector section: the generated "Attributes" editor already covers path,
    # muted, loop, volume and autoplay (changing any of them restarts the movie from its start).

    # ---- Ingest ------------------------------------------------------------------------------

    def local_paths(mime: Any) -> list[Path]:
        paths: list[Path] = []
        for url in mime.urls() if mime.hasUrls() else []:
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir():
                paths.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in SUPPORTED_MOVIE_EXTS))
            elif path.suffix.lower() in SUPPORTED_MOVIE_EXTS:
                paths.append(path)
        return paths

    def describe_files(mime: Any) -> str | None:
        if not mime.hasUrls():
            return None
        local = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        movies = [f for f in local if Path(f).suffix.lower() in SUPPORTED_MOVIE_EXTS]
        if movies:
            return f"Drop to add {len(movies)} movie{'s' if len(movies) != 1 else ''} (linked, not embedded)"
        return None

    def ingest_files(_view: Any, pos: QPointF, mime: Any) -> Any:
        paths = local_paths(mime)
        if not paths:
            return None
        return len(api.create_row(paths, pos.x(), pos.y())), 0

    # Checked before image.remote (order 50) claims every drag as a download attempt.
    movie_handler = IngestHandler("movie.files", 15, lambda m: bool(m.hasUrls()), describe_files, ingest_files)
    ui.contribute("ingest.handlers", movie_handler, order=movie_handler.order)

    # ---- Actions -----------------------------------------------------------------------------

    def insert_movies_dialog() -> None:
        """Choose video files and add them in a row at the view centre (always linked, never embedded)."""
        exts = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_MOVIE_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(ui.app.main_window(), "Insert Movies (linked, not embedded)", "",
                                                f"Movies ({exts})")
        if not paths:
            return
        view = ui.app.active_view()
        center = view.center() if view is not None else QPointF()
        names = ui.app.run_command("movie.import_movies", paths, [center.x(), center.y()])
        if names:
            ui.status(f"Added {len(names)} movie(s) (linked to their files, not embedded)")

    ui.command("insert_dialog", insert_movies_dialog, undoable=False)
    ui.action("movie.insert", "movie.insert_dialog", "Insert Movies… (linked, not embedded)", icon="movie",
              keywords="add import video file mp4 mov clip movie", category="File")
    # No dedicated shortcut (Insert Images already owns Ctrl+I): use the File menu, command
    # palette or drag-and-drop.

    ui.menu("canvas", "movie.insert", path="File", group="file.import", order=1)
    ui.contribute("palette.buttons", PaletteButton("movie.insert", "Movie", "movie", order=11))
