"""The public plugin API (headless half — no Qt).

Plugins import from here (and from :mod:`pixelref.plugin_api.ui` for the Qt
half) and nothing else from pixelRef, so the kernel can change underneath
them. ``API_VERSION`` must match the ``api`` field of ``plugin.toml``.

A plugin package::

    # my_plugin/plugin.toml
    id = "my_plugin"
    name = "My plugin"
    version = "1.0.0"
    api = 1
    requires = ["brush"]

    # my_plugin/__init__.py
    from pixelref.plugin_api import Attr, NodeType

    def activate(ctx):                    # headless: node types, commands
        ctx.register_node_type(NodeType("my_node", inherits="canvas_node", attrs=(...)))
        ctx.command("do_thing", do_thing, alias="do_thing")

    def activate_ui(ui):                  # Qt: tools, views, actions, keys, menus
        from pixelref.plugin_api.ui import Tool
        ui.register_tool(MyTool(ui))
"""

from .. import __version__ as APP_VERSION
from ..core import perf, xform
from ..core.attributes import Attr
from ..core.errors import CommandError, PluginError, SceneError
from ..core.extensions import Disposable
from ..core.nodetypes import (
    CAP_COLORABLE,
    CAP_COMPOUND,
    CAP_CONTAINER,
    CAP_CONTENT,
    CAP_ERASABLE,
    NodeType,
    TypeExtension,
)
from ..core.scene_file import SCENE_EXT, SCENE_EXTS, read_thumbnail
from ..core.plugins import API_VERSION, PluginContext
from ..core.values import differs, fast_copy

__all__ = [
    "API_VERSION",
    "APP_VERSION",
    "Attr",
    "CAP_COLORABLE",
    "CAP_COMPOUND",
    "CAP_CONTAINER",
    "CAP_CONTENT",
    "CAP_ERASABLE",
    "CommandError",
    "Disposable",
    "NodeType",
    "PluginContext",
    "PluginError",
    "SceneError",
    "TypeExtension",
    "differs",
    "fast_copy",
    "read_thumbnail",
    "SCENE_EXT",
    "SCENE_EXTS",
    "perf",
    "xform",
]
