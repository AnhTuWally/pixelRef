"""``pixelref.pxcmd`` — the pixelRef command API.

Every action in pixelRef is a command, and the UI itself runs them, so
anything you can do with the mouse you can do from a script or the Python
console::

    from pixelref import pxcmd

    img = pxcmd.import_image("refs/castle.jpg", translate=[0, 0])
    note = pxcmd.create_node("annotation_text", parent=img, text="Fix this edge")
    pxcmd.set_attr(f"{note}.translate", [40, -60])
    pxcmd.select(pxcmd.ls(type="image", tags="lighting"))
    with pxcmd.undo_chunk("Grid layout"):
        pxcmd.arrange(mode="grid")
    pxcmd.get_attr("Image1.world_bounds")

Commands run against the current session (``pixelref.core.current_session``).
Plugin commands live in namespaces named after the plugin
(``pxcmd.pen.combine()``); many also have a flat alias
(``pxcmd.group()``). ``dir(pxcmd)`` lists what's available, and
``pxcmd.help("set_attr")`` describes a command.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator

from .core.extensions import Disposable
from .core.session import current_session

_SCENE_EVENTS = ("node.", "asset.", "plugin_data.", "scene.loaded", "scene.cleared")


def __getattr__(name: str) -> Any:
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(current_session().commands.proxy(), name)


def __dir__() -> list[str]:
    try:
        names = current_session().commands.children("")
    except RuntimeError:
        names = []
    return sorted(set(names) | {"undo_chunk", "callbacks"})


@contextmanager
def undo_chunk(label: str = "Script") -> Iterator[None]:
    """Group everything inside the ``with`` block into one undo step."""
    session = current_session()
    with session.undo.chunk(label):
        yield
    session.notify_changed("undo_chunk")


class _Callbacks:
    """Event callbacks: run a function when something happens::

        cid = pxcmd.callbacks.add("selection.changed", lambda event: print(event.payload))
        pxcmd.callbacks.remove(cid)

    Events: ``selection.changed``, ``document.changed``, ``document.replaced``,
    ``command.finished``, ``scene.opened``, ``scene.saved``, ``revision.changed``,
    ``plugins.changed`` and, from the document, ``node.created``,
    ``node.deleted``, ``node.renamed``, ``node.reparented``,
    ``node.attr_changed``… (``"node.*"`` matches all of those).
    """

    def __init__(self) -> None:
        self._next = 0
        self._registered: dict[int, Any] = {}

    def add(self, event: str, callback: Callable[[Any], None]) -> int:
        session = current_session()
        # The document emits node.*, asset.*, plugin_data.changed, scene.loaded
        # and scene.cleared; everything else comes from the session.
        if event.startswith(_SCENE_EVENTS):
            disposable = session.scene.event_stream.on(event, callback)
        elif event.endswith("*") and any(e.startswith(event[:-1]) for e in _SCENE_EVENTS):
            # A wildcard such as "scene.*" or "*" spans both streams.
            disposable = Disposable.combine([session.scene.event_stream.on(event, callback),
                                             session.events.on(event, callback)])
        else:
            disposable = session.events.on(event, callback)
        self._next += 1
        self._registered[self._next] = disposable
        return self._next

    def remove(self, callback_id: int) -> bool:
        disposable = self._registered.pop(callback_id, None)
        if disposable is None:
            return False
        disposable.dispose()
        return True

    def clear(self) -> None:
        for callback_id in list(self._registered):
            self.remove(callback_id)


callbacks = _Callbacks()
