"""PureRef import: open ``.pur`` boards, or add them to the current board.

``purformat`` reads PureRef 1.x files (a port of FyorDev's PureRef-format
reader, see its docstring for the credit and licence) and ``pur2format``
PureRef 2 files. This module maps what they read onto pixelRef nodes:

- each image item becomes an ``image`` node with the image embedded (or
  linked, when the board only referenced the file). Images used several
  times are stored once in the scene too;
- PureRef's item matrix (Qt ``QTransform``, as pixelRef's) becomes
  translate / rotate / scale, mirroring included. PureRef centres an image on
  its item origin, pixelRef puts pixel (0, 0) there, so that placement is
  folded into the node's transform;
- crops become the image's ``crop`` rectangle (the bounding box, for
  PureRef's free-form crops);
- text items become text notes (with their formatting, from PureRef 2);
  attached ones become children of their image or note so they still move
  with it;
- PureRef 2 groups become groups, pen strokes a drawing (pen plugin);
- z order becomes sibling order; opacity and locking are kept.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pixelref.plugin_api import CommandError, xform

from .purformat import PurBoard, PurDrawing, PureRefError, PurGroup, PurImage, PurItem, PurText, read_board

PUR_EXT = ".pur"
CROP_TOLERANCE = 0.5  # px: a crop this close to the whole image is no crop
PT_PER_PX = 0.75  # pixelRef note sizes are points (96 dpi), PureRef 2's are pixels
TEXT_STYLES = ("bold", "italic", "underline", "strike", "font_size", "color", "highlight_color")


def item_matrix(item: PurItem) -> xform.Matrix:
    """Item coordinates → PureRef parent coordinates."""
    m11, m12, m21, m22 = item.matrix
    return xform.Matrix(m11, m12, m21, m22, item.x, item.y)


def pixel_matrix(image: PurImage) -> xform.Matrix:
    """Image pixels → item coordinates."""
    a, b, c, d = image.inner
    return xform.Matrix(a, b, c, d, *image.offset)


def image_matrix(image: PurImage) -> xform.Matrix:
    """Image pixels → PureRef parent coordinates."""
    return pixel_matrix(image) @ item_matrix(image)


def node_style(run: dict[str, Any]) -> dict[str, Any]:
    """A PureRef text run's style as text note attributes."""
    style: dict[str, Any] = {}
    for key in ("bold", "italic", "underline", "strike"):
        if key in run:
            style[key] = bool(run[key])
    if run.get("font_px"):
        style["font_size"] = round(float(run["font_px"]) * PT_PER_PX, 2)
    if "color" in run:
        style["color"] = list(run["color"][:3])
    if "highlight" in run:
        style["highlight_color"] = list(run["highlight"])
    return style


def note_formatting(runs: list[dict[str, Any]], color: list[int]) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    """Note attributes for the styles all the text shares, and runs for the rest.

    ``color`` is the note's colour when the text doesn't share one."""
    styled = [(run["text"], node_style(run)) for run in runs if run.get("text")]
    visible = [style for text, style in styled if text.strip()] or [style for _text, style in styled]
    shared: dict[str, Any] = {}
    for key in TEXT_STYLES:
        values = [style.get(key) for style in visible]
        if values and values[0] is not None and all(v == values[0] for v in values):
            shared[key] = values[0]
    families = [run.get("font_family") for run in runs if run.get("text", "").strip() and run.get("font_family")]
    if families:
        shared["font_family"] = families[0]
    note = {"bold": False, "italic": False, "underline": False, "strike": False, "color": color, **shared}
    pieces = []
    for text, style in styled:
        own = {k: v for k, v in style.items() if note.get(k) != v}
        if pieces and {k: v for k, v in pieces[-1].items() if k != "text"} == own:
            pieces[-1]["text"] += text
        else:
            pieces.append({"text": text, **own})
    return shared, pieces if any(len(piece) > 1 for piece in pieces) else None


class PureRefImporter:
    """Builds pixelRef nodes from a parsed board (shared by the commands and the UI)."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.session = ctx.session
        self.last_report: dict[str, Any] = {}

    # ---- Reading -------------------------------------------------------------------------------

    def read(self, path: str | Path) -> tuple[Path, PurBoard]:
        file = self.session.resolve_path(path)
        if not file.is_file():
            raise CommandError(f"No such PureRef file: {path}")
        try:
            return file, read_board(file)
        except PureRefError as exc:
            raise CommandError(f"{file.name}: {exc}") from exc

    # ---- Building ------------------------------------------------------------------------------

    def build(self, file: Path, board: PurBoard, translate: Any = None, group: bool = False) -> list[str]:
        """Create the board's nodes in the current scene; returns the top-level names."""
        scene = self.session.scene
        self.types = {name: scene.types.get(name) is not None
                      for name in ("annotation_text", "group", "annotation_drawing", "annotation_path")}
        report = {"file": file.as_posix(), "version": board.version, "images": 0, "linked": 0, "missing": 0,
                  "notes": 0, "drawings": 0, "groups": 0, "skipped": {}, "warnings": list(board.warnings)}
        self.report, self.file, self.board = report, file, board
        parent = None
        if group and self.types["group"]:
            parent = scene.unique_name(file.stem or "PureRef")
            scene.create_node("group", parent)
        made = self.add_items(board.items, parent, xform.IDENTITY)
        roots = [parent] if parent is not None else made
        if parent is not None and not scene.children_of(parent):
            scene.delete_node(parent)
            roots = []
        if translate is not None and roots:
            self.move_to(roots, float(translate[0]), float(translate[1]))
        for kind, count in report["skipped"].items():
            report["warnings"].append(f"{count} {kind}(s) skipped: the plugin for them is off")
        self.last_report = report
        self.session.selection.set(roots)
        return roots

    def move_to(self, roots: list[str], x: float, y: float) -> None:
        """Centre the imported nodes on (x, y)."""
        bounds = [b for b in (self.session.world_bounds(name) for name in roots) if b]
        if not bounds:
            return
        left = min(b[0] for b in bounds)
        top = min(b[1] for b in bounds)
        right = max(b[0] + b[2] for b in bounds)
        bottom = max(b[1] + b[3] for b in bounds)
        dx, dy = x - (left + right) / 2.0, y - (top + bottom) / 2.0
        for name in roots:
            node = self.session.scene.get_node(name)
            tx, ty = node.attrs.get("translate") or [0.0, 0.0]
            self.session.scene.set_attr(name, "translate", [round(tx + dx, 3), round(ty + dy, 3)])

    def skip(self, kind: str, item: PurItem) -> None:
        count = 1 + len([c for c in self.walk(item.children)])
        self.report["skipped"][kind] = self.report["skipped"].get(kind, 0) + count

    @staticmethod
    def walk(items: list[PurItem]) -> list[PurItem]:
        found = []
        for item in items:
            found.append(item)
            found += PureRefImporter.walk(item.children)
        return found

    def add_items(self, items: list[PurItem], parent: str | None, to_parent: xform.Matrix) -> list[str]:
        """Create items (back to front) under ``parent``; ``to_parent`` maps PureRef parent
        coordinates to pixelRef ones. Returns the names created at this level."""
        made: list[str] = []
        for item in sorted(items, key=lambda i: i.z):  # sibling order is draw order
            matrix = item_matrix(item) @ to_parent
            if isinstance(item, PurImage):
                name = self.add_image(item, parent, matrix)
                inside = pixel_matrix(item).inverted() or xform.IDENTITY
            elif isinstance(item, PurText):
                name, inside = self.add_text(item, parent, matrix), xform.IDENTITY
            elif isinstance(item, PurDrawing):
                name, inside = self.add_drawing(item, parent, matrix), xform.IDENTITY
            elif isinstance(item, PurGroup):
                if not self.types["group"]:  # keep the members, without their group
                    made += self.add_items(item.children, parent, matrix)
                    continue
                name, inside = self.session.scene.unique_name(item.name or "Group"), xform.IDENTITY
                self.create("group", name, parent, item, matrix, {})
                self.report["groups"] += 1
            else:
                continue
            if name is None:
                continue
            made.append(name)
            self.add_items(item.children, name, inside)
            if isinstance(item, PurGroup) and not self.session.scene.children_of(name):
                self.session.scene.delete_node(name)
                made.remove(name)
                self.report["groups"] -= 1
        return made

    def create(self, node_type: str, name: str, parent: str | None, item: PurItem, matrix: xform.Matrix,
               attrs: dict[str, Any]) -> None:
        attrs.update(xform.attrs_for_matrix(matrix, {}))
        if item.opacity < 1.0:
            attrs["opacity"] = round(item.opacity, 4)
        if item.locked:
            attrs["locked"] = 1
        self.session.scene.create_node(node_type, name, parent=parent, attrs=attrs)

    def add_image(self, image: PurImage, parent: str | None, matrix: xform.Matrix) -> str | None:
        scene = self.session.scene
        report = self.report
        local_source = image.source if image.source != "BruteForceLoaded" and "://" not in image.source else ""
        source_name = local_source.replace("\\", "/").rsplit("/", 1)[-1]
        if image.data is not None:
            attrs: dict[str, Any] = {"asset": scene.add_asset(image.data, name=source_name), "path": ""}
            report["images"] += 1
        else:
            linked = self.linked_file(image)
            if linked is None:
                report["missing"] += 1
                report["warnings"].append(f"Image {image.name or image.id} has no data and no file to link to")
                return None
            attrs = {"asset": "", "path": self.session.to_scene_path(linked)}
            report["linked"] += 1
            if not linked.is_file():
                report["missing"] += 1
        crop = self.crop_for(image, attrs)
        if crop is not None:
            attrs["crop"] = crop
        name = scene.unique_name(image.name or Path(source_name).stem or "Image")
        self.create("image", name, parent, image, pixel_matrix(image) @ matrix, attrs)
        return name

    def linked_file(self, image: PurImage) -> Path | None:
        """The file a linked image points to: as stored, or next to the board."""
        source, file = image.source, self.file
        if not source or source == "BruteForceLoaded" or "://" in source:
            return None
        path = Path(source.replace("\\", "/"))
        candidates = [path] if path.is_absolute() else [Path(self.board.folder) / path, file.parent / path]
        candidates.append(file.parent / path.name)  # the board was moved together with its images
        return next((c for c in candidates if c.is_file()), candidates[0])

    def crop_for(self, image: PurImage, attrs: dict[str, Any]) -> list[float] | None:
        box = image.crop_box
        if box is None:
            return None
        x, y, w, h = box
        images = self.session.services.get("images")
        size = images.sizer(attrs) if images is not None else None
        if size is not None:
            iw, ih = float(size[0]), float(size[1])
            if abs(x) <= CROP_TOLERANCE and abs(y) <= CROP_TOLERANCE and abs(w - iw) <= CROP_TOLERANCE \
                    and abs(h - ih) <= CROP_TOLERANCE:
                return None
            x0, y0 = max(0.0, x), max(0.0, y)
            x1, y1 = min(iw, x + w), min(ih, y + h)
            if x1 - x0 < 1.0 or y1 - y0 < 1.0:
                return None
            x, y, w, h = x0, y0, x1 - x0, y1 - y0
        elif w < 1.0 or h < 1.0:
            return None
        return [round(x, 3), round(y, 3), round(w, 3), round(h, 3)]

    def add_text(self, text: PurText, parent: str | None, matrix: xform.Matrix) -> str | None:
        if not self.types["annotation_text"]:
            self.skip("text note", text)
            return None
        r, g, b, _a = text.color
        attrs: dict[str, Any] = {
            "text": text.text,
            "color": [r, g, b],
            "background_color": list(text.background),
            "fill_style": "solid",
        }
        if text.runs:
            shared, runs = note_formatting(text.runs, [r, g, b])
            attrs.update(shared)
            if runs:
                attrs["runs"] = runs
        if text.width > 0:
            attrs["text_width"] = round(text.width, 3)
        name = self.session.scene.unique_name(text.name or "Note")
        self.create("annotation_text", name, parent, text, matrix, attrs)
        self.report["notes"] += 1
        return name

    def add_drawing(self, drawing: PurDrawing, parent: str | None, matrix: xform.Matrix) -> str | None:
        if not (self.types["annotation_drawing"] and self.types["annotation_path"]):
            self.skip("drawing", drawing)
            return None
        strokes = [s for s in drawing.strokes if s.points]
        if not strokes:
            return None
        scene = self.session.scene
        name = scene.unique_name(drawing.name or "Drawing")
        self.create("annotation_drawing", name, parent, drawing, matrix, {})
        for stroke in strokes:
            left = min(p[0] for p in stroke.points)
            top = min(p[1] for p in stroke.points)
            attrs: dict[str, Any] = {
                "translate": [round(left, 3), round(top, 3)],
                "points": [[round(x - left, 3), round(y - top, 3)] for x, y in stroke.points],
                "color": list(stroke.color[:3]),
                "thickness": round(max(0.1, stroke.width), 3),
            }
            if stroke.color[3] < 255:
                attrs["opacity"] = round(stroke.color[3] / 255.0, 4)
            scene.create_node("annotation_path", scene.unique_name("Stroke"), parent=name, attrs=attrs)
        self.report["drawings"] += 1
        return name


def activate(ctx: Any) -> None:
    session = ctx.session
    importer = PureRefImporter(ctx)
    ctx.provide_service("pureref", importer)

    def import_file(path: str, translate: Any = None, group: bool | None = None) -> list[str]:
        """Add a PureRef board (.pur) to this one; returns the new top-level nodes.

        ``translate`` centres it there (default: PureRef's own coordinates);
        ``group`` puts it in a group named after the file (default: only when
        the board isn't empty)."""
        file, board = importer.read(path)
        if group is None:
            group = bool(session.scene.nodes)
        return importer.build(file, board, translate, group)

    def open_board(path: str) -> list[str]:
        """Open a PureRef board (.pur) as a new, unsaved scene."""
        file, board = importer.read(path)  # a bad file leaves the open scene as it was
        session.new_document()
        roots = importer.build(file, board)
        session.undo.reset()  # the imported board is the starting point, not an undo step
        session.mark_unsaved()
        session.events.emit("document.replaced", {"reason": "open"})
        return roots

    def inspect(path: str) -> dict[str, Any]:
        """What a PureRef board holds (version, images, notes) without importing it."""
        file, board = importer.read(path)
        return {
            "file": file.as_posix(), "version": board.version, "app_version": board.app_version,
            "images": sum(1 for i in board.images if i.data is not None),
            "linked": sum(1 for i in board.images if i.data is None),
            "notes": len(board.texts), "drawings": len(board.drawings), "warnings": list(board.warnings),
        }

    ctx.command("import_file", import_file, alias="import_pureref", label="Import PureRef board")
    ctx.command("open", open_board, alias="open_pureref", undoable=False)
    ctx.command("inspect", inspect, undoable=False, echo=False)


def activate_ui(ui: Any) -> None:
    from .ui import activate_pureref_ui

    activate_pureref_ui(ui)
