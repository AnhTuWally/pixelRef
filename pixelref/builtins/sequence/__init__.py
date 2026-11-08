"""Image sequences: the ``sequence`` node type, and getting numbered frames onto the board.

A sequence node plays numbered image files (``shot.1001.exr`` … ``shot.1096.exr``,
any format the image plugin reads, EXR included with the exr plugin) like a
movie. Like a movie it is always *linked*: ``path`` is the pattern of its file
names (``shot.####.exr``, see ``seqscan``), relative to the scene when possible,
and ``first`` / ``last`` the frame range played. Frames missing on disk show the
nearest earlier frame (``missing = "hold"``) or nothing (``"black"``).

The playback plugin's transport, keys, filmstrip and per-frame annotations work
on sequences as on GIFs and movies (the item quacks the same; see ``items.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pixelref.plugin_api import CAP_CONTENT, Attr, CommandError, NodeType, differs

from . import seqscan

DEFAULT_SIZE = (480.0, 270.0)
ARRANGE_GAP = 8.0

SEQUENCE_TYPE = NodeType(
    name="sequence",
    inherits="canvas_node",
    label="Sequence",
    capabilities=frozenset({CAP_CONTENT}),
    attrs=(
        Attr("path", "path", "", group="Sequence",
             doc="Pattern of the frame files, # for each digit (shot.####.exr); always linked"),
        Attr("first", "int", 1, group="Sequence", doc="First frame played"),
        Attr("last", "int", 1, group="Sequence", doc="Last frame played"),
        Attr("fps", "float", 24.0, group="Sequence", minimum=0.1, maximum=240.0, soft_min=1.0, soft_max=60.0,
             step=1.0, precision=3, doc="Frames per second"),
        Attr("loop", "bool", True, group="Sequence", doc="Loop playback"),
        Attr("autoplay", "bool", False, group="Sequence", doc="Start playing when the board opens"),
        Attr("missing", "enum", "hold", choices=("hold", "black"), group="Sequence",
             doc="Frames with no file: show the nearest earlier frame (hold) or nothing (black)"),
    ),
)


def name_hint(pattern: str) -> str:
    """``shot_v003.####.exr`` -> ``shot_v003``."""
    head, _pad, _tail = seqscan.split(Path(pattern).name)
    return head.rstrip("._- ") or "Sequence"


class SequenceApi:
    """Sequences on disk and creating sequence nodes (shared by the commands and the UI)."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.session = ctx.session
        # Set by the UI (decodes the first frame's header); headless code uses image headers.
        self.sizer = self.headless_size

    @property
    def detect_on_drop(self) -> bool:
        """Whether dropped numbered frames (and folders of them) become sequences; remembered."""
        return bool(self.ctx.settings.value("detect_sequences", True, type=bool))

    @detect_on_drop.setter
    def detect_on_drop(self, value: bool) -> None:
        self.ctx.settings.setValue("detect_sequences", bool(value))

    # ---- Files ------------------------------------------------------------------------------

    def pattern(self, attrs: dict[str, Any]) -> str | None:
        """The node's pattern as an absolute path, or None."""
        value = str(attrs.get("path", "") or "")
        return str(self.session.resolve_path(seqscan.normalize(value))) if value else None

    def scan(self, attrs: dict[str, Any]) -> seqscan.SeqInfo:
        pattern = self.pattern(attrs)
        return seqscan.scan(pattern) if pattern else seqscan.SeqInfo("", [])

    def frame_file(self, attrs: dict[str, Any], frame: int) -> Path | None:
        pattern = self.pattern(attrs)
        return Path(seqscan.frame_path(pattern, frame)) if pattern else None

    def first_file(self, attrs: dict[str, Any]) -> Path | None:
        """The first frame file that exists (in the played range when it has one)."""
        first = self.frame_file(attrs, int(attrs.get("first", 1) or 1))
        if first is not None and first.is_file():
            return first
        info = self.scan(attrs)
        if not info.frames:
            return None
        low, high = int(attrs.get("first", 1) or 1), int(attrs.get("last", 1) or 1)
        inside = [n for n in info.frames if low <= n <= high] or info.frames
        return self.frame_file(attrs, inside[0])

    def headless_size(self, attrs: dict[str, Any]) -> tuple[float, float] | None:
        from pixelref.core.imagesize import file_image_size

        first = self.first_file(attrs)
        size = file_image_size(first) if first is not None else None
        return (float(size[0]), float(size[1])) if size else None

    def source_from_path(self, path: str | Path) -> dict[str, Any]:
        """Sequence attrs for a pattern or any one of its frame files; raises CommandError."""
        text = str(self.session.resolve_path(seqscan.normalize(str(path))))
        if seqscan.is_pattern(text):
            pattern = seqscan.normalize(text)
        else:
            try:
                pattern = seqscan.pattern_for(text)
            except ValueError:
                raise CommandError(f"Not a numbered frame file: {Path(text).name}") from None
            if seqscan.scan(pattern).first is None:
                # e.g. shot.0999.exr … shot.1000.exr: try the same name unpadded
                pattern = seqscan.pattern_for(text, padding=1)
        info = seqscan.scan(pattern)
        if not info.frames:
            raise CommandError(f"No frames found for {Path(pattern).name}")
        return {"path": self.session.to_scene_path(pattern), "first": info.first, "last": info.last}

    # ---- Nodes --------------------------------------------------------------------------------

    def create(self, source: dict[str, Any], name_hint_text: str, x: float, y: float, centered: bool = True,
               fps: float = 24.0, loop: bool = True, autoplay: bool = False, missing: str = "hold") -> str:
        scene = self.session.scene
        name = scene.unique_name(name_hint_text or "Sequence")
        size = self.sizer(source) or DEFAULT_SIZE
        if centered:
            x -= size[0] / 2.0
            y -= size[1] / 2.0
        attrs = {**source, "fps": float(fps), "loop": bool(loop), "autoplay": bool(autoplay), "missing": missing,
                 "translate": [round(x, 3), round(y, 3)], "scale": [1.0, 1.0], "rotate": 0.0}
        scene.create_node("sequence", name, attrs=attrs)
        scene.emit("media.added", {"name": name})  # a new file, not a paste or a loaded scene
        return name

    def create_from_path(self, path: str | Path, x: float, y: float, centered: bool = True, **options: Any) -> str:
        source = self.source_from_path(path)
        return self.create(source, name_hint(source["path"]), x, y, centered, **options)

    def create_row(self, patterns: list[str], x: float, y: float) -> list[str]:
        """Sequences side by side, centred on (x, y)."""
        sources = [self.source_from_path(p) for p in patterns]
        sizes = [self.sizer(s) or DEFAULT_SIZE for s in sources]
        total = sum(size[0] for size in sizes) + ARRANGE_GAP * max(0, len(sources) - 1)
        left = x - total / 2.0
        names = []
        for source, size in zip(sources, sizes):
            names.append(self.create(source, name_hint(source["path"]), left + size[0] / 2.0, y))
            left += size[0] + ARRANGE_GAP
        return names


def activate(ctx: Any) -> None:
    session = ctx.session
    scene = session.scene
    api = SequenceApi(ctx)

    def local_bounds(_sess: Any, node: Any) -> list[float] | None:
        size = api.headless_size(node.attrs) or DEFAULT_SIZE
        return [0.0, 0.0, float(size[0]), float(size[1])]

    SEQUENCE_TYPE.compute = {"local_bounds": local_bounds}
    ctx.register_node_type(SEQUENCE_TYPE)
    ctx.provide_service("sequences", api)

    def sequences(nodes: Any) -> list[str]:
        names = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        return [n for n in names if scene.get_node(n).node_type == "sequence"]

    def import_sequence(path: str, translate: Any = None, name: str | None = None, fps: float = 24.0,
                        centered: bool = True) -> str:
        """Add an image sequence from its pattern (shot.####.exr) or any one frame; the range is found on disk.

        ``%04d``, ``@@@@`` and ``$F4`` patterns work too. Sequences are always linked.
        Returns the node name."""
        x, y = (float(translate[0]), float(translate[1])) if translate is not None else (0.0, 0.0)
        source = api.source_from_path(path)
        created = api.create(source, name or name_hint(source["path"]), x, y, centered, fps=fps)
        session.selection.set([created])
        return created

    def import_sequences(paths: list[str], translate: Any = None) -> list[str]:
        """Add several image sequences side by side, centred on ``translate``."""
        x, y = (float(translate[0]), float(translate[1])) if translate is not None else (0.0, 0.0)
        names = api.create_row([str(p) for p in paths], x, y)
        session.selection.set(names)
        return names

    def rescan(nodes: Any = None) -> list[str]:
        """Update the frame range of sequences from the files on disk (a render still going)."""
        names = sequences(nodes)
        if not names:
            raise CommandError("Select an image sequence")
        changed = []
        for name in names:
            attrs = scene.get_node(name).attrs
            info = api.scan(attrs)
            if not info.frames:
                continue
            values = {"first": info.first, "last": info.last}
            if any(differs(attrs.get(k), v) for k, v in values.items()):
                scene.set_attrs(name, values)
                changed.append(name)
        frames = sum(len(api.scan(scene.get_node(n).attrs).frames) for n in names)
        session.events.emit("ui.status", {"text": f"Rescanned {len(names)} sequence(s): {frames} frame(s) on disk"})
        return changed

    def info(node: str | None = None) -> dict[str, Any]:
        """A sequence's files: pattern, range on disk, missing frames in the played range."""
        names = sequences(node if node is not None else None)
        if not names:
            raise CommandError("Select an image sequence")
        attrs = scene.get_node(names[0]).attrs
        found = api.scan(attrs)
        first, last = int(attrs.get("first", 1)), int(attrs.get("last", 1))
        return {"pattern": api.pattern(attrs), "first": first, "last": last, "on_disk": len(found.frames),
                "disk_first": found.first, "disk_last": found.last, "missing": found.missing(first, last)}

    def prepare_paths(sess: Any, scene_path: Path) -> None:
        """Make sequence patterns portable for a save: stored relative to the scene when possible."""
        new_dir = Path(scene_path).parent.resolve()
        for node in list(scene.iter_nodes()):
            if node.node_type != "sequence" or not node.attrs.get("path"):
                continue
            source = sess.resolve_path(str(node.attrs["path"]))
            try:
                value = source.resolve().relative_to(new_dir).as_posix()
            except (ValueError, OSError):
                value = source.as_posix()
            if value != node.attrs["path"]:
                scene.set_attr(node.name, "path", value)

    def set_detect(on: bool) -> bool:
        """Whether dropped numbered frames become image sequences (True) or separate images."""
        api.detect_on_drop = bool(on)
        mode = "become image sequences" if on else "are added as separate images"
        session.events.emit("ui.status", {"text": f"Dropped numbered frames {mode}"})
        return api.detect_on_drop

    ctx.command("set_detect", set_detect, alias="set_detect_sequences", undoable=False)
    ctx.command("import_sequence", import_sequence, alias="import_sequence", label="Insert image sequence")
    ctx.command("import_sequences", import_sequences, alias="import_sequences", label="Insert image sequences")
    ctx.command("rescan", rescan, alias="rescan_sequence", label="Rescan sequence")
    ctx.command("info", info, alias="sequence_info", label="Sequence info", undoable=False)
    ctx.contribute("scene.before_save", prepare_paths)


def activate_ui(ui: Any) -> None:
    from .ui import activate_sequence_ui

    activate_sequence_ui(ui)
