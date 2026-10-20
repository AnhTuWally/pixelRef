"""Symbols shown beside attribute names in the Attribute Editor.

A symbol is a short glyph that hints at what an attribute does (◐ opacity, ◫ fill,
↻ rotation…). Resolution, most specific first:

1. ``Attr.symbol``: the plugin that declares an attribute can name its symbol directly.
2. ``"<node type>.<attribute>"`` entries, then ``"<attribute>"`` entries, then
   ``"type:<attr type>"`` entries, from plugin contributions to the
   ``attributes.symbols`` extension point (``ctx.register_attr_symbols({...})``).
3. The same keys in the global tables below (plugins win over them at each key).

Attributes with no match get no symbol; the editor does not invent one.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

EXTENSION_POINT = "attributes.symbols"

# Global definitions of the basic attributes (by attribute name). Chosen from
# glyphs that Segoe UI Symbol / DejaVu / Apple Symbols all draw as plain text.
NAME_SYMBOLS: dict[str, str] = {
    # transform
    "translate": "✥", "position": "✥", "rotate": "↻", "rotation": "↻", "scale": "⤢", "pivot": "⊕",
    "size": "⇔", "start": "◌", "end": "◍", "center": "⊕", "crop": "⛶", "flip_x": "⇋", "flip_y": "⇵",
    # display
    "visibility": "◉", "shown": "◉", "opacity": "◐", "fill_opacity": "◐", "locked": "⚿", "mix": "◒",
    "muted": "⊘", "effects_muted": "⊘", "on_top": "⇧", "ghost": "◌",
    # colour and stroke
    "color": "●", "fill_color": "◼", "stroke_color": "○", "border_color": "▢", "text_color": "A",
    "background_color": "▨", "background": "▨", "highlight_color": "✦",
    "thickness": "≡", "border": "▢", "border_width": "▢", "stroke_style": "┅", "border_style": "┅",
    "fill_style": "▦", "radius": "◜", "corner_radius": "◜", "smoothing": "∿", "cap_size": "▸",
    "start_cap": "◂", "end_cap": "▸", "arrow": "→", "arrow_start": "←", "routing": "⌐", "waypoints": "⋯",
    # text
    "text": "¶", "font_family": "Aa", "font_size": "Aa", "bold": "B", "italic": "I", "underline": "U",
    "strike": "S", "title": "T", "title_size": "T", "label": "T", "label_size": "T", "mark": "⌗",
    # image adjustments
    "brightness": "☼", "contrast": "◑", "grayscale": "◍", "threshold": "⊥", "levels": "▤", "strength": "⚡",
    "amount": "∑", "level": "▤",
    # organisation
    "tags": "#", "show_tags": "#", "hide_tags": "#", "note": "✎", "metadata": "ⓘ", "isolate": "◎",
    "padding": "⊡", "gap": "↔", "cell_size": "▦", "number": "№", "frame": "▶", "frame_guide": "▭",
    # media
    "path": "⌂", "asset": "▣", "loop": "↺", "autoplay": "▶", "volume": "♪", "zoom": "⌕",
}

# Fallbacks by attribute type (only where the type alone says something).
TYPE_SYMBOLS: dict[str, str] = {
    f"type:{kind}": glyph for kind, glyph in {
        "path": "⌂", "asset": "▣", "node_ref": "⛓", "node_refs": "⛓", "string_list": "#", "points": "⋯",
        "rect": "▭", "float2": "⌖", "text": "¶",
    }.items()
}


def _table(mapping: Mapping[str, Any]) -> dict[str, str]:
    return {str(k): str(v) for k, v in mapping.items() if isinstance(v, str) and v}


def check_contribution(value: Any) -> None:
    """Extension-point validator: a ``{key: glyph}`` mapping."""
    if not isinstance(value, Mapping) or not all(isinstance(v, str) for v in value.values()):
        raise TypeError("attributes.symbols contributions must be a {name: glyph} mapping")


def symbol_for(attr: Any, node_type: str = "", contributions: Iterable[Mapping[str, str]] = ()) -> str:
    """The symbol for ``attr`` (an :class:`Attr`) on ``node_type``, or ``""``.

    ``contributions`` are the plugin tables, highest priority first.
    """
    own = getattr(attr, "symbol", "")
    if own:
        return own
    tables = [_table(t) for t in contributions] + [NAME_SYMBOLS]
    type_tables = [_table(t) for t in contributions] + [TYPE_SYMBOLS]
    keys = ([f"{node_type}.{attr.name}"] if node_type else []) + [attr.name]
    for key in keys:
        for table in tables:
            if key in table:
                return table[key]
    for table in type_tables:
        if f"type:{attr.type}" in table:
            return table[f"type:{attr.type}"]
    return ""
