"""pixelRef: a plugin-first, node-based reference board.

* ``pixelref.core``     the Qt-free kernel (nodes, attributes, scene file, undo,
                         commands, plugin runtime)
* ``pixelref.pxcmd``    the command API scripts and the console use
* ``pixelref.pxnode``   an object-oriented convenience layer over pxcmd
* ``pixelref.plugin_api`` everything a plugin may import
* ``pixelref.ui``       the desktop app (window manager, canvas views, tools)
* ``pixelref/builtins`` the plugins that provide every feature
"""

__version__ = "1.0.1"
