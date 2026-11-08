"""Color palettes: pull an image's dominant colors onto the board, on demand.

``color_palette.extract`` makes a palette out of ordinary nodes: a titled ``backdrop`` holding
filled ``annotation_rect`` swatches, joined to the image by an ordinary ``link`` (image → backdrop).
The link carries a ``palette_role`` marker and the swatches a ``palette_swatch`` index (dynamic
attributes, saved with the scene).

The live, marked link is the only thing that ties a palette to an image. Extracting again on an
image that has one updates that palette in place; with none (never extracted, or the link was
deleted: the old palette is just canvas content now) it makes a new one. Nothing here ever runs
on its own: no watchers, no recomputing when images, effects or links change.

Colors come from the image's own pixels cut to its crop; effects are not applied.
"""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CommandError

from .extract import SAMPLE_BUDGET, extract_palette
from .source import sample_pixels

ROLE_ATTR = "palette_role"
ROLE = "source_palette"
SWATCH_ATTR = "palette_swatch"
DEFAULT_COUNT = 5
SWATCH = 70.0
GAP = 8.0
PADDING = 16.0
PLACE_GAP = 40.0
MAX_COUNT = 16


def activate(ctx: Any) -> None:
    session = ctx.session
    scene = session.scene

    def images_under(name: str) -> list[str]:
        """The node itself if it is an image, plus every image below it, recursively."""
        return [n for n in [name, *scene.descendants(name)] if scene.get_node(n).node_type == "image"]

    def target_image(image: Any) -> str:
        """The one node to extract from: an image, or a group/backdrop/image holding images."""
        if image is None:
            names = session.selection.names
            if len(names) != 1:
                raise CommandError("Select one image or group to extract a palette from" if not names
                                   else "Select a single image or group: palettes are extracted one source at a time")
            image = names[0]
        name = scene.resolve(image)
        if not images_under(name):
            raise CommandError(f"'{name}' isn't an image and holds no images")
        return name

    def palette_links(image: str) -> list[str]:
        """Marked links running from this image to a backdrop: the palettes it feeds."""
        image_id = scene.get_node(image).id
        found = []
        for node in scene.iter_nodes():
            if node.node_type != "link" or node.attrs.get(ROLE_ATTR) != ROLE or node.attrs.get("source") != image_id:
                continue
            target = scene.name_of_id(str(node.attrs.get("target") or ""))
            if target and scene.get_node(target).node_type == "backdrop":
                found.append(node.name)
        return found

    def swatches_of(backdrop: str) -> list[str]:
        found = [n for n in scene.get_node(backdrop).children
                 if scene.get_node(n).node_type == "annotation_rect" and SWATCH_ATTR in scene.get_node(n).attrs]
        return sorted(found, key=lambda n: (scene.get_node(n).attrs[SWATCH_ATTR], n))

    def swatch_attrs(color: tuple[int, int, int], index: int) -> dict[str, Any]:
        return {"fill_color": list(color), "fill_opacity": 1.0, "fill_style": "solid", "stroke_style": "none",
                SWATCH_ATTR: index}

    def hex_of(color: tuple[int, int, int]) -> str:
        return "#%02x%02x%02x" % color

    def colors_of(image: str, count: int) -> list[tuple[int, int, int]]:
        images = [n for n in images_under(image) if session.compute(n, "world_visibility")] or images_under(image)
        budget = max(1024, SAMPLE_BUDGET // len(images))
        pixels, failure = [], None
        for name in images:
            try:
                pixels.append(sample_pixels(session, scene.get_node(name), budget))
            except CommandError as exc:  # one bad image doesn't sink a group
                failure = failure or exc
        if not pixels and failure is not None:
            raise failure
        colors = extract_palette(b"".join(pixels), count)
        if not colors:
            raise CommandError(f"'{image}' has no visible pixels to take colors from")
        return colors

    def refresh(image: str, link: str, count: int | None) -> str:
        backdrop = scene.name_of_id(str(scene.get_node(link).attrs["target"]))
        existing = swatches_of(backdrop)
        wanted = count if count is not None else (len(existing) or DEFAULT_COUNT)
        colors = colors_of(image, wanted)  # before anything changes
        locked = [n for n in [backdrop, *existing] if session.compute(n, "world_locked")]
        if locked:
            raise CommandError(f"'{locked[0]}' is locked: unlock the palette to update it")
        for name, color in zip(existing, colors):
            scene.set_attr(name, "fill_color", list(color))
        for name in existing[len(colors):]:
            scene.delete_node(name)
        extra = colors[len(existing):]
        if extra:
            size = [float(v) for v in scene.get_node(existing[0]).attrs["size"]] if existing else [SWATCH, SWATCH]
            rects = [scene.get_node(n).attrs["translate"] for n in existing]
            x = max((r[0] for r in rects), default=PADDING - size[0] - GAP) + size[0] + GAP
            y = rects[-1][1] if rects else PADDING + 32.0
            for offset, color in enumerate(extra):
                index = len(existing) + offset
                name = scene.unique_name("Swatch")
                scene.create_node("annotation_rect", name, parent=backdrop, attrs={
                    "translate": [round(x, 3), round(y, 3)], "size": list(size), **swatch_attrs(color, index)},
                    dynamic=_dynamic({SWATCH_ATTR: "int"}))
                x += size[0] + GAP
            need = [x - GAP + PADDING, y + size[1] + PADDING]
            current = [float(v) for v in scene.get_node(backdrop).attrs["size"]]
            grown = [max(current[0], need[0]), max(current[1], need[1])]
            if grown != current:
                scene.set_attr(backdrop, "size", grown)
        session.selection.set([backdrop])
        return backdrop

    def create(image: str, count: int) -> str:
        colors = colors_of(image, count)
        left, top, width, _height = session.world_bounds(image) or [0.0, 0.0, 300.0, 200.0]
        x0, y0 = left + width + PLACE_GAP + PADDING, top + PADDING + 32.0
        names = []
        for index, color in enumerate(colors):
            name = ctx.run("rect.create", x0 + index * (SWATCH + GAP), y0, SWATCH, SWATCH, "Swatch")
            scene.add_attr(name, _dynamic({SWATCH_ATTR: "int"})[SWATCH_ATTR], index)
            scene.set_attrs(name, swatch_attrs(color, index))
            names.append(name)
        backdrop = ctx.run("backdrops.wrap", names, PADDING, "Color Palette", "Palette")
        link = ctx.run("links.connect", image, backdrop, "Palette link", color=[150, 150, 150], thickness=1.5,
                       stroke_style="dash")
        scene.add_attr(link, _dynamic({ROLE_ATTR: "string"})[ROLE_ATTR], ROLE)
        session.selection.set([backdrop])
        return backdrop

    def extract(image: Any = None, count: int | None = None) -> dict[str, Any]:
        """Extract the dominant colors of an image, or of a group/backdrop and every image inside it
        (recursively), as a palette on the board (one undo step). Hidden images are skipped.

        With a marked palette link from the source node to a backdrop, that palette is updated in place;
        otherwise a new backdrop of swatches is made and linked. ``count`` (1-16) defaults to the
        existing palette's size, or 5. Returns ``{"backdrop", "created", "colors"}``."""
        name = target_image(image)
        if count is not None and not 1 <= int(count) <= MAX_COUNT:
            raise CommandError(f"count must be between 1 and {MAX_COUNT}")
        count = int(count) if count is not None else None
        links = palette_links(name)
        if len(links) > 1:
            raise CommandError(f"'{name}' feeds {len(links)} palettes ({', '.join(links)}): "
                               "delete the extra palette links to choose which one to update")
        backdrop = refresh(name, links[0], count) if links else create(name, count or DEFAULT_COUNT)
        colors = [hex_of(tuple(scene.get_node(n).attrs["fill_color"])) for n in swatches_of(backdrop)]
        return {"backdrop": backdrop, "created": not links, "colors": colors}

    ctx.command("extract", extract, alias="extract_palette", label="Extract Color Palette")


def _dynamic(types: dict[str, str]) -> dict[str, Any]:
    from pixelref.plugin_api import Attr

    return {name: Attr(name, kind, "" if kind == "string" else 0, hidden=True) for name, kind in types.items()}


def activate_ui(ui: Any) -> None:
    from .ui import activate_color_palette_ui

    activate_color_palette_ui(ui)
