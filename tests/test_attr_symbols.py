"""Attribute symbols: global defaults, plugin overrides, Attr.symbol."""
from pixelref.core.attr_symbols import symbol_for
from pixelref.core.attributes import Attr


def test_resolution_order():
    opacity = Attr("opacity", "float")
    assert symbol_for(opacity) == "◐"
    assert symbol_for(Attr("whatever", "float")) == ""
    assert symbol_for(Attr("whatever", "path")) == "⌂"
    assert symbol_for(Attr("opacity", "float", symbol="Z")) == "Z"
    plugin = [{"opacity": "P", "box.opacity": "B", "type:float": "F"}]
    assert symbol_for(opacity, "", plugin) == "P"
    assert symbol_for(opacity, "box", plugin) == "B"
    assert symbol_for(Attr("whatever", "float"), "box", plugin) == "F"
    assert symbol_for(Attr("x", "float", symbol="Z"), "box", plugin) == "Z"


def test_dynamic_attr_roundtrip():
    attr = Attr("depth", "float", symbol="⇕")
    assert Attr.from_dict("depth", attr.to_dict()).symbol == "⇕"
