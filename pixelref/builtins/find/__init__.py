"""Find elements on the board.

The ``find`` service is a :class:`~.search.SearchIndex` of the open scene: searchable text per
node, derived from the scene and rebuilt only where it changed. The commands work headless::

    pxcmd.find("#hero type:image")          # = find.search: [{name, type, score, fields, snippet, tags}]
    pxcmd.find.select("note:todo -#done")   # select every match

Query syntax: see :mod:`.search`. The Find panel (Ctrl+F, ``find.show``) and ``find.locate``
(go to an element and select it) are in :mod:`.ui`.
"""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CommandError

from .search import SearchIndex, parse_query


def activate(ctx: Any) -> None:
    session = ctx.session
    index = SearchIndex(session.scene, session.types)
    ctx.provide_service("find", index)

    def search(query: str, limit: int | None = 50, canvas_only: bool = True) -> list[dict[str, Any]]:
        """Elements matching a query (words, "phrases", #tag, type:, name:, note:, key:value), best first.

        Every term must match; ``-term`` leaves out what matches; case never matters. ``key:value``
        looks in the metadata. Returns {name, type, score, fields, snippet, tags} dicts."""
        return [m.to_dict() for m in index.search(str(query), limit or None, canvas_only)]

    def select(query: str, add: bool = False) -> list[str]:
        """Select every element matching a query (``add``: keep the current selection too).
        Returns the new selection."""
        if not parse_query(str(query)):
            raise CommandError("Type something to find")
        names = [m.name for m in index.search(str(query))]
        if add:
            session.selection.add(names)
        else:
            session.selection.set(names)
        session.events.emit("ui.status", {"text": f"Found {len(names)} item{'s' if len(names) != 1 else ''}"})
        return session.selection.names

    ctx.command("search", search, alias="find", undoable=False, echo=False)
    ctx.command("select", select, undoable=False)


def activate_ui(ui: Any) -> None:
    from .ui import activate_find_ui

    activate_find_ui(ui)
