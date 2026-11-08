"""Color palettes: extraction engine, and the linked-refresh / unlinked-new lifecycle."""

from __future__ import annotations

from harness import make_png, tmp_dir  # noqa: F401
from PySide6.QtGui import QColor, QImage

from harness import TMP
from pixelref.builtins.color_palette.extract import extract_palette
from pixelref.plugin_api import CommandError


def headless():
    from pixelref.core.session import Session

    return Session().start()


def _stripes(name, colors, w=100, h=40, alpha_rows=0):
    image = QImage(w * len(colors), h, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    for i, c in enumerate(colors):
        for x in range(i * w, (i + 1) * w):
            for y in range(alpha_rows, h):
                image.setPixelColor(x, y, QColor(*c))
    path = TMP / name
    image.save(str(path))
    return path


def _image(s, path, name="Pic"):
    return s.run("image.import_image", str(path), [0, 0], name)


def _palette(s, img):
    return s.run("color_palette.extract", img)


def _rgba(colors):
    return b"".join(bytes([*c, 255]) * 50 for c in colors)


def _kind(s, kind):
    return [n.name for n in s.scene.iter_nodes() if n.node_type == kind]


def _fails(s, *args, **kw):
    try:
        s.run(*args, **kw)
    except CommandError as exc:
        return str(exc)
    raise AssertionError("expected a CommandError")


# ---- engine ---------------------------------------------------------------------------------

def test_engine_is_deterministic_ordered_and_handles_degenerate_input():
    px = _rgba([(255, 0, 0)] * 3 + [(0, 255, 0)] * 2 + [(0, 0, 255)])
    assert extract_palette(px, 5) == extract_palette(px, 5) == [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    assert extract_palette(_rgba([(10, 20, 30)]), 5) == [(10, 20, 30)], "flat: one color, no crash"
    assert extract_palette(b"", 5) == [] and extract_palette(bytes([9, 9, 9, 0]) * 10, 5) == []
    assert extract_palette(bytes([5, 6, 7, 255]), 5) == [(5, 6, 7)], "one pixel"
    assert len(extract_palette(_rgba([(i * 20, 0, 0) for i in range(12)] + [(0, i * 20, 200) for i in range(12)]), 3)) <= 3


def test_transparent_pixels_are_ignored():
    px = bytes([255, 0, 0, 0]) * 500 + bytes([0, 0, 255, 255]) * 10
    assert extract_palette(px, 5) == [(0, 0, 255)]


# ---- lifecycle ------------------------------------------------------------------------------

def test_first_extract_makes_backdrop_swatches_and_marked_link():
    s = headless()
    img = _image(s, _stripes("p1.png", [(255, 0, 0), (0, 255, 0), (0, 0, 255), (250, 250, 0), (0, 250, 250)]))
    r = _palette(s, img)
    assert r["created"] and len(r["colors"]) == 5
    back = r["backdrop"]
    assert s.scene.get_node(back).node_type == "backdrop" and s.scene.get_node(back).attrs["title"] == "Color Palette"
    kids = s.scene.get_node(back).children
    assert len(kids) == 5 and all(s.scene.get_node(k).node_type == "annotation_rect" for k in kids)
    (link,) = _kind(s, "link")
    n = s.scene.get_node(link)
    assert n.attrs["source"] == s.scene.get_node(img).id and n.attrs["target"] == s.scene.get_node(back).id
    assert n.attrs["palette_role"] == "source_palette"
    assert s.run("links.ends", link) == [img, back]
    assert s.selection.names == [back]
    ib, bb = s.world_bounds(img), s.world_bounds(back)
    assert bb[0] >= ib[0] + ib[2], "placed beside the image"
    s.run("undo")
    assert _kind(s, "backdrop") == _kind(s, "link") == _kind(s, "annotation_rect") == [], "one undo step"
    s.run("redo")
    assert len(_kind(s, "annotation_rect")) == 5


def test_linked_palette_refreshes_in_place_and_ignores_unrelated_children():
    s = headless()
    path = _stripes("p2.png", [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
    img = _image(s, path)
    r = _palette(s, img)
    back = r["backdrop"]
    (link,) = _kind(s, "link")
    ids = [s.scene.get_node(n).id for n in s.scene.get_node(back).children]
    mine = s.run("rect.create", 5, 5, 10, 10, "Mine")
    s.run("backdrops.adopt", back, [mine])
    s.run("set_attr", f"{mine}.fill_color", [1, 2, 3])
    s.run("set_attr", f"{back}.translate", [900.0, 50.0])
    s.run("set_attr", f"{link}.label", "src")
    s.run("rename", img, "Renamed")
    # Swatch recolored by hand, then the source changes on disk-equivalent: swap the asset.
    first = s.scene.get_node(back).children[0]
    s.run("set_attr", f"{first}.fill_color", [9, 9, 9])
    before = s.scene.revision if hasattr(s.scene, "revision") else None  # noqa: F841
    assert s.scene.get_node(first).attrs["fill_color"] == [9, 9, 9], "no automatic refresh"
    r2 = s.run("color_palette.extract", "Renamed")
    assert not r2["created"] and r2["backdrop"] == back and len(_kind(s, "backdrop")) == 1 and len(_kind(s, "link")) == 1
    fills = sorted(s.scene.get_node(n).attrs["fill_color"] for n in _palette_swatches(s, back))
    assert fills == [[0, 0, 255], [0, 255, 0], [255, 0, 0]], "swatches recolored, hand edit overwritten"
    assert [s.scene.get_node(n).id for n in s.scene.get_node(back).children if n != mine] == ids
    assert s.scene.get_node(mine).attrs["fill_color"] == [1, 2, 3]
    assert s.scene.get_node(back).attrs["translate"] == [900.0, 50.0]
    assert s.scene.get_node(link).attrs["label"] == "src"
    s.run("undo")
    assert s.scene.get_node(first).attrs["fill_color"] == [9, 9, 9], "refresh is one undo step"


def test_refresh_changes_swatch_count_and_keeps_user_children():
    s = headless()
    img = _image(s, _stripes("p3.png", [(255, 0, 0), (0, 255, 0), (0, 0, 255), (250, 250, 0), (0, 250, 250)]))
    back = _palette(s, img)["backdrop"]
    mine = s.run("rect.create", 5, 5, 10, 10, "Mine")
    s.run("backdrops.adopt", back, [mine])
    assert len(_palette_swatches(s, back)) == 5
    s.run("color_palette.extract", img, count=2)
    assert len(_palette_swatches(s, back)) == 2 and mine in s.scene.get_node(back).children
    s.run("color_palette.extract", img, count=4)
    sw = _palette_swatches(s, back)
    assert len(sw) == 4
    box = s.world_bounds(back)
    for n in sw:
        r = s.world_bounds(n)
        assert r[0] + r[2] <= box[0] + box[2] + 1e-6, "new swatches stay inside the backdrop"


def _palette_swatches(s, back):
    return [n for n in s.scene.get_node(back).children if "palette_swatch" in s.scene.get_node(n).attrs]


def test_deleted_link_orphans_the_old_palette_and_extract_makes_a_new_one():
    s = headless()
    img = _image(s, _stripes("p4.png", [(255, 0, 0), (0, 255, 0)]))
    old = _palette(s, img)["backdrop"]
    (link,) = _kind(s, "link")
    s.run("delete", [link])
    s.run("set_attr", f"{old}.title", "Renamed palette")
    new = _palette(s, img)
    assert new["created"] and new["backdrop"] != old
    assert len(_kind(s, "backdrop")) == 2 and len(_kind(s, "link")) == 1
    assert len(s.scene.get_node(old).children) == 2, "old palette untouched"


def test_unmarked_wrong_direction_and_wrong_target_links_never_qualify():
    s = headless()
    img = _image(s, _stripes("p5.png", [(255, 0, 0), (0, 255, 0)]))
    back = s.run("backdrops.create", 500, 0, 300, 200, "Plain")
    plain = s.run("links.connect", img, back)  # unmarked
    assert _palette(s, img)["created"], "unmarked link is not enough"
    rect = s.run("rect.create", 0, 400, 50, 50, "R")
    marked_wrong = s.run("links.connect", img, rect)
    s.run("add_attr", marked_wrong, "palette_role", "string", value="source_palette")
    s.run("delete", [n for n in _kind(s, "link") if s.scene.get_node(n).attrs.get("palette_role") == "source_palette"
                     and n != marked_wrong])
    n = len(_kind(s, "backdrop"))
    assert _palette(s, img)["created"] and len(_kind(s, "backdrop")) == n + 1, "marked link to a non-backdrop"
    # A marked link drawn the wrong way round (backdrop -> image) isn't a source link either.
    s2 = headless()
    img2 = _image(s2, _stripes("p5b.png", [(255, 0, 0)]))
    b2 = _palette(s2, img2)["backdrop"]
    (link,) = _kind(s2, "link")
    s2.run("links.reverse", [link])
    assert _palette(s2, img2)["created"]
    assert plain  # silence linters


def test_restored_marked_link_is_eligible_again_and_two_palettes_are_ambiguous():
    s = headless()
    img = _image(s, _stripes("p6.png", [(255, 0, 0), (0, 255, 0)]))
    back = _palette(s, img)["backdrop"]
    (link,) = _kind(s, "link")
    s.run("links.set_end", link, "target", None)  # detached: free end
    assert _palette(s, img)["created"]
    s.run("links.set_end", link, "target", back)  # reattached, marker kept
    for extra in [n for n in _kind(s, "link") if n != link]:
        s.run("delete", [extra])
    assert not _palette(s, img)["created"]
    # Second qualifying link -> ambiguity error, nothing changes.
    other = s.run("backdrops.create", 2000, 0, 100, 100, "Other")
    second = s.run("links.connect", img, other)
    s.run("add_attr", second, "palette_role", "string", value="source_palette")
    snap = {n.name: dict(n.attrs) for n in s.scene.iter_nodes()}
    assert "palettes" in _fails(s, "color_palette.extract", img)
    assert {n.name: dict(n.attrs) for n in s.scene.iter_nodes()} == snap


def test_deleting_source_or_palette_prunes_the_link_only():
    s = headless()
    img = _image(s, _stripes("p7.png", [(255, 0, 0), (0, 255, 0)]))
    back = _palette(s, img)["backdrop"]
    s.run("delete", [img])
    assert _kind(s, "link") == [] and s.scene.has(back) and len(s.scene.get_node(back).children) == 2
    s.run("undo")
    s.run("delete", [back])
    assert _kind(s, "link") == [] and s.scene.has(img)


def test_errors_leave_the_scene_untouched_and_locked_palettes_are_refused():
    s = headless()
    missing = s.run("image.import_image", str(_stripes("p8.png", [(1, 2, 3)])), [0, 0], "Gone", embed=False)
    (TMP / "p8.png").write_bytes(b"not an image")
    before = len(s.scene.nodes)
    assert "can't be read" in _fails(s, "color_palette.extract", missing)
    assert len(s.scene.nodes) == before
    assert "no images" in _fails(s, "color_palette.extract", s.run("rect.create", 0, 0, 5, 5))
    s.selection.set([])
    assert "Select" in _fails(s, "color_palette.extract")
    img = _image(s, _stripes("p9.png", [(255, 0, 0), (0, 255, 0)]))
    back = _palette(s, img)["backdrop"]
    first = s.scene.get_node(back).children[0]
    s.run("set_attr", f"{back}.locked", 1)
    s.run("set_attr", f"{first}.fill_color", [9, 9, 9])  # attr set still allowed headless
    assert "locked" in _fails(s, "color_palette.extract", img)
    assert s.scene.get_node(first).attrs["fill_color"] == [9, 9, 9]


def test_flat_transparent_and_cropped_images():
    s = headless()
    flat = _image(s, _stripes("f.png", [(40, 90, 200)]), "Flat")
    assert _palette(s, flat)["colors"] == ["#285ac8"]
    clear = QImage(10, 10, QImage.Format_ARGB32)
    clear.fill(QColor(0, 0, 0, 0))
    clear.save(str(TMP / "clear.png"))
    assert "visible" in _fails(s, "color_palette.extract", _image(s, TMP / "clear.png", "Clear"))
    two = _image(s, _stripes("two.png", [(255, 0, 0), (0, 0, 255)], w=50), "Two")
    s.run("set_attr", f"{two}.crop", [50.0, 0.0, 50.0, 40.0])
    assert _palette(s, two)["colors"] == ["#0000ff"], "extracts from the cropped area"


def test_save_and_reload_keeps_the_association():
    s = headless()
    img = _image(s, _stripes("p10.png", [(255, 0, 0), (0, 255, 0)]))
    back = _palette(s, img)["backdrop"]
    path = TMP / "pal.pxref"
    s.run("save_scene", str(path))
    s2 = headless()
    s2.run("open_scene", str(path)) if s2.commands.exists("open_scene") else s2.run("load_scene", str(path))
    r = s2.run("color_palette.extract", img)
    assert not r["created"] and r["backdrop"] == back and len(_kind(s2, "backdrop")) == 1


def test_group_extracts_from_every_image_recursively_and_refreshes_on_the_group():
    s = headless()
    a = _image(s, _stripes("ga.png", [(255, 0, 0)]), "A")
    b = _image(s, _stripes("gb.png", [(0, 0, 255)]), "B")
    inner = s.run("grouping.group", [b])
    c = _image(s, _stripes("gc.png", [(0, 200, 0)]), "C")
    outer = s.run("grouping.group", [a, inner, c])
    r = s.run("color_palette.extract", outer)
    assert r["created"] and sorted(r["colors"]) == ["#0000ff", "#00c800", "#ff0000"]
    (link,) = _kind(s, "link")
    assert s.run("links.ends", link) == [outer, r["backdrop"]]
    s.run("set_attr", f"{b}.visibility", 0)
    r2 = s.run("color_palette.extract", outer)
    assert not r2["created"] and sorted(r2["colors"]) == ["#00c800", "#ff0000"], "hidden images are skipped"
    s.selection.set([outer])
    assert not s.run("color_palette.extract")["created"]
    # An image with an image child counts the child too.
    d = _image(s, _stripes("gd.png", [(250, 250, 0)]), "D")
    e = _image(s, _stripes("ge.png", [(0, 250, 250)]), "E")
    s.run("backdrops.adopt", d, [e])
    assert sorted(s.run("color_palette.extract", d)["colors"]) == ["#00fafa", "#fafa00"]
    assert "holds no images" in _fails(s, "color_palette.extract", s.run("rect.create", 0, 0, 5, 5, "Plain"))
