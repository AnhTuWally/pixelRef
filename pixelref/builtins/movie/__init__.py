"""Movies: the ``movie`` node type, its item, and getting video files onto the board.

Unlike images, a movie node only ever points at a file on disk (``path``). It
has no ``asset`` attribute and there is no "embed" option — a movie is always
linked by file reference. If the file is moved or deleted, the node shows a
"Missing" placeholder, exactly like a missing linked image.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pixelref.plugin_api import CAP_CONTENT, Attr, CommandError, NodeType

SUPPORTED_MOVIE_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv"}
DEFAULT_SIZE = (480.0, 270.0)
ARRANGE_GAP = 8.0

MOVIE_TYPE = NodeType(
    name="movie",
    inherits="canvas_node",
    label="Movie",
    capabilities=frozenset({CAP_CONTENT}),
    attrs=(
        Attr("path", "path", "", group="Movie", doc="Linked video file — movies are always linked, never embedded"),
        Attr("muted", "bool", False, group="Movie", doc="Start muted"),
        Attr("loop", "bool", True, group="Movie", doc="Loop playback"),
        Attr("volume", "float", 1.0, group="Movie", doc="Playback volume (0-1)"),
        Attr("autoplay", "bool", True, group="Movie", doc="Start playing when the board opens"),
    ),
)


class MovieApi:
    """Creating movies (shared by the headless commands and the UI). Always linked by path."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.session = ctx.session
        # Set by the UI once a movie's real size is known; headless code gets a placeholder size.
        self.sizer = lambda attrs: None

    def source_from_file(self, path: Path) -> dict[str, Any]:
        return {"path": self.session.to_scene_path(path)}

    def create(self, source: dict[str, Any], name_hint: str, x: float, y: float, centered: bool = True,
              muted: bool = False, loop: bool = True, volume: float = 1.0, autoplay: bool = True) -> str:
        scene = self.session.scene
        name = scene.unique_name(name_hint or "Movie")
        size = self.sizer(source) or DEFAULT_SIZE
        if centered:
            x -= size[0] / 2.0
            y -= size[1] / 2.0
        attrs = {
            **source, "muted": bool(muted), "loop": bool(loop), "volume": float(volume), "autoplay": bool(autoplay),
            "translate": [round(x, 3), round(y, 3)], "scale": [1.0, 1.0], "rotate": 0.0,
        }
        scene.create_node("movie", name, attrs=attrs)
        return name

    def create_from_path(self, path: Path, x: float, y: float, centered: bool = True) -> str:
        return self.create(self.source_from_file(path), Path(path).stem or "Movie", x, y, centered)

    def create_row(self, paths: list[Path], x: float, y: float) -> list[str]:
        """Movies side by side, centred on (x, y)."""
        sources = [self.source_from_file(p) for p in paths]
        sizes = [self.sizer(s) or DEFAULT_SIZE for s in sources]
        total = sum(size[0] for size in sizes) + ARRANGE_GAP * max(0, len(paths) - 1)
        left = x - total / 2.0
        names = []
        for path, source, size in zip(paths, sources, sizes):
            names.append(self.create(source, Path(path).stem or "Movie", left + size[0] / 2.0, y))
            left += size[0] + ARRANGE_GAP
        return names


def activate(ctx: Any) -> None:
    session = ctx.session

    def local_bounds(_sess: Any, node: Any) -> list[float] | None:
        return [0.0, 0.0, DEFAULT_SIZE[0], DEFAULT_SIZE[1]]

    MOVIE_TYPE.compute = {"local_bounds": local_bounds}
    ctx.register_node_type(MOVIE_TYPE)
    api = MovieApi(ctx)
    ctx.provide_service("movies", api)

    def import_movie(path: str, translate: Any = None, name: str | None = None, centered: bool = True) -> str:
        """Add a video file by reference (movies are always linked, never embedded). Returns the node name."""
        file = session.resolve_path(path)
        if not file.is_file():
            raise CommandError(f"No such movie file: {path}")
        if file.suffix.lower() not in SUPPORTED_MOVIE_EXTS:
            raise CommandError(f"Not a supported movie file: {path}")
        x, y = (float(translate[0]), float(translate[1])) if translate is not None else (0.0, 0.0)
        created = api.create(api.source_from_file(file), name or file.stem, x, y, centered)
        session.selection.set([created])
        return created

    def import_movies(paths: list[str], translate: Any = None) -> list[str]:
        """Add several video files side by side, centred on ``translate``."""
        files = [session.resolve_path(p) for p in paths]
        missing = [str(f) for f in files if not f.is_file()]
        if missing:
            raise CommandError(f"No such movie file: {missing[0]}")
        x, y = (float(translate[0]), float(translate[1])) if translate is not None else (0.0, 0.0)
        names = api.create_row(files, x, y)
        session.selection.set(names)
        return names

    ctx.command("import_movie", import_movie, alias="import_movie", label="Insert movie")
    ctx.command("import_movies", import_movies, alias="import_movies", label="Insert movies")


def activate_ui(ui: Any) -> None:
    from .ui import activate_movie_ui

    activate_movie_ui(ui)
