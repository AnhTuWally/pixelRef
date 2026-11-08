"""Pages: frame parts of the board as numbered pages and export them as one PDF.

A ``page_frame`` node is a rectangle on the board that works like a camera:
it shows what lies under it but holds nothing (moving it never moves the
board). ``paper`` locks its shape to a paper size (``Free`` doesn't), and
``page_order`` numbers the pages. The PDF writer (Qt side, ``pages.export_pdf``)
renders each frame to one page, vector where the content is vector.
"""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import Attr, CommandError, NodeType

PAGE = "page_frame"
DEFAULT_SIZE = [1188.0, 840.0]  # A4 landscape at 4 scene px per mm
FREE = "Free"
# Paper sizes in millimetres, short side first (portrait). Screen ratios are given at a 297 mm long side.
PAPER: dict[str, tuple[float, float]] = {
    "A5": (148.0, 210.0),
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A2": (420.0, 594.0),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
    "Tabloid": (279.4, 431.8),
    "16:9": (297.0 * 9.0 / 16.0, 297.0),
    "4:3": (297.0 * 3.0 / 4.0, 297.0),
    "1:1": (297.0, 297.0),
}
PAPERS = (*PAPER, FREE)
ORIENTATIONS = ("landscape", "portrait")
FREE_LONG_SIDE_MM = 297.0


def paper_mm(paper: str, orientation: str, size: Any = None) -> tuple[float, float]:
    """Page size in millimetres (width, height). Free pages keep the frame's shape at a 297 mm long side."""
    if paper in PAPER:
        short, long = PAPER[paper]
        return (long, short) if orientation == "landscape" else (short, long)
    w, h = (float(v) for v in (size or DEFAULT_SIZE))
    w, h = max(w, 1e-6), max(h, 1e-6)
    scale = FREE_LONG_SIDE_MM / max(w, h)
    return w * scale, h * scale


def aspect(paper: str, orientation: str) -> float | None:
    """Width / height of the paper, or None for Free."""
    if paper not in PAPER:
        return None
    w, h = paper_mm(paper, orientation)
    return w / h


def fit_aspect(width: float, height: float, ratio: float | None, cover: bool = True) -> tuple[float, float]:
    """The box of ``ratio`` (w / h) that covers (or fits inside) a width × height box."""
    width, height = max(abs(width), 1.0), max(abs(height), 1.0)
    if ratio is None:
        return width, height
    if (width / height > ratio) == cover:
        return width, width / ratio
    return height * ratio, height


def page_names(scene: Any) -> list[str]:
    """Every page frame, in page order."""
    names = [n.name for n in scene.iter_nodes() if n.node_type == PAGE]
    return sorted(names, key=lambda n: (int(scene.get_node(n).attrs.get("page_order", 0)), n))


def activate(ctx: Any) -> None:
    session = ctx.session
    scene = session.scene
    ctx.register_node_type(NodeType(
        PAGE, inherits="canvas_node", label="Page",
        compute={"local_bounds": lambda _s, node: [0.0, 0.0, *[float(v) for v in node.attrs.get("size", DEFAULT_SIZE)]]},
        attrs=(
            Attr("size", "float2", list(DEFAULT_SIZE), group="Page", minimum=1.0),
            Attr("paper", "enum", "A4", choices=PAPERS, group="Page", doc="Paper size (Free: any shape)"),
            Attr("orientation", "enum", "landscape", choices=ORIENTATIONS, group="Page"),
            Attr("page_order", "int", 1, minimum=0, group="Page", label="Page number",
                 doc="Pages are exported in this order"),
            Attr("title", "string", "", group="Page", doc="Shown on the board only"),
            Attr("color", "color", [255, 170, 60], group="Page"),
            Attr("export", "bool", True, group="Page", label="Export", doc="Off: skipped by PDF export"),
        ),
        quick_attrs=("paper", "orientation", "color"),
        doc="A page of the PDF export: frames what lies under it"))

    def selection_or(nodes: Any) -> list[str]:
        if nodes is None:
            names = session.selection.names
        else:
            names = [scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        return [n for n in names if scene.has(n) and scene.get_node(n).node_type == PAGE]

    def next_order() -> int:
        return max((int(scene.get_node(n).attrs.get("page_order", 0)) for n in page_names(scene)), default=0) + 1

    def create(x: float, y: float, width: float, height: float, paper: str = "A4",
               orientation: str = "landscape", title: str = "", name: str = "Page", **attrs: Any) -> str:
        """A page frame at (x, y). With a paper size its height follows the width."""
        if paper not in PAPERS:
            raise CommandError(f"Unknown paper '{paper}' (one of {', '.join(PAPERS)})")
        if orientation not in ORIENTATIONS:
            raise CommandError("orientation must be 'landscape' or 'portrait'")
        width = max(abs(float(width)), 1.0)
        ratio = aspect(paper, orientation)
        height = width / ratio if ratio else max(abs(float(height)), 1.0)
        final = scene.unique_name(name)
        values = {"translate": [round(float(x), 3), round(float(y), 3)],
                  "size": [round(width, 3), round(height, 3)], "paper": paper, "orientation": orientation,
                  "title": str(title), "page_order": next_order(), **attrs}
        scene.create_node(PAGE, final, attrs=values)
        session.selection.set([final])
        return final

    def set_paper(nodes: Any = None, paper: str | None = None, orientation: str | None = None) -> list[str]:
        """Change pages' paper and/or orientation, keeping each centred where it was."""
        names = selection_or(nodes)
        if not names:
            raise CommandError("Select a page")
        if paper is not None and paper not in PAPERS:
            raise CommandError(f"Unknown paper '{paper}'")
        if orientation is not None and orientation not in ORIENTATIONS:
            raise CommandError("orientation must be 'landscape' or 'portrait'")
        for name in names:
            attrs = scene.get_node(name).attrs
            old_paper = str(attrs.get("paper", "A4"))
            old_orientation = str(attrs.get("orientation", "landscape"))
            new_paper = paper or old_paper
            new_orientation = orientation or old_orientation
            w, h = (float(v) for v in attrs.get("size", DEFAULT_SIZE))
            if new_orientation != old_orientation:
                w, h = h, w  # turn the page: same area, same centre
            ratio = aspect(new_paper, new_orientation)
            if ratio is not None:
                w, h = w, w / ratio
                if new_orientation != old_orientation:
                    w, h = h * ratio, h  # keep the turned page's height
            x, y = (float(v) for v in attrs.get("translate", [0.0, 0.0]))
            old_w, old_h = (float(v) for v in attrs.get("size", DEFAULT_SIZE))
            cx, cy = x + old_w / 2.0, y + old_h / 2.0
            ctx.run("set_attrs", name, {"paper": new_paper, "orientation": new_orientation,
                                        "size": [round(w, 3), round(h, 3)],
                                        "translate": [round(cx - w / 2.0, 3), round(cy - h / 2.0, 3)]})
        return names

    def rotate(nodes: Any = None) -> list[str]:
        """Turn pages between landscape and portrait."""
        names = selection_or(nodes)
        for name in names:
            current = str(scene.get_node(name).attrs.get("orientation", "landscape"))
            set_paper([name], None, "portrait" if current == "landscape" else "landscape")
        return names

    def reorder(names: Any) -> list[str]:
        """Put these pages first, in this order; the other pages follow in their current order."""
        chosen = [scene.resolve(n) for n in ([names] if isinstance(names, str) else names)]
        pages = page_names(scene)
        unknown = [n for n in chosen if n not in pages]
        if unknown:
            raise CommandError(f"Not a page: {', '.join(unknown)}")
        ordered = list(dict.fromkeys(chosen)) + [n for n in pages if n not in chosen]
        for index, name in enumerate(ordered, start=1):
            if int(scene.get_node(name).attrs.get("page_order", 0)) != index:
                ctx.run("set_attr", f"{name}.page_order", index)
        return ordered

    def move_page(name: str, step: int) -> list[str]:
        """Move a page earlier (negative ``step``) or later in the page order."""
        pages = page_names(scene)
        name = scene.resolve(name)
        if name not in pages:
            raise CommandError(f"Not a page: {name}")
        index = pages.index(name)
        target = max(0, min(len(pages) - 1, index + int(step)))
        pages.insert(target, pages.pop(index))
        return reorder(pages)

    def list_pages() -> list[dict[str, Any]]:
        """The pages in order: name, number, paper, orientation, title, export."""
        out = []
        for number, name in enumerate(page_names(scene), start=1):
            attrs = scene.get_node(name).attrs
            out.append({"name": name, "number": number, "paper": attrs.get("paper", "A4"),
                        "orientation": attrs.get("orientation", "landscape"), "title": attrs.get("title", ""),
                        "export": bool(attrs.get("export", True))})
        return out

    ctx.command("create", create, alias="create_page", label="Add page")
    ctx.command("set_paper", set_paper, label="Page size")
    ctx.command("rotate", rotate, label="Turn page")
    ctx.command("reorder", reorder, label="Reorder pages")
    ctx.command("move", move_page, label="Move page")
    ctx.command("list", list_pages, undoable=False)


def activate_ui(ui: Any) -> None:
    from .ui import activate_pages_ui

    activate_pages_ui(ui)
