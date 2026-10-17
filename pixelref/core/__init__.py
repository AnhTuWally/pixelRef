"""pixelRef kernel core: the Qt-free half of the application.

Everything here runs headless (batch scripts, tests, ``python -m pixelref
--batch``): the node/attribute engine, the scene document and its file
format, undo, the command system behind ``pixelref.pxcmd``, selection, and the
plugin runtime. The Qt user interface (``pixelref.ui``) is built on top.
"""

from .attributes import Attr
from .errors import CommandError, PluginError, SceneError
from .nodetypes import NodeType, NodeTypeRegistry
from .scene import SceneGraph
from .session import Session, current_session

__all__ = [
    "Attr",
    "CommandError",
    "NodeType",
    "NodeTypeRegistry",
    "PluginError",
    "SceneError",
    "SceneGraph",
    "Session",
    "current_session",
]
