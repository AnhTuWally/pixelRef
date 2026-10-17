"""Quick Attribute Editor: a bar next to the selection on the canvas.

It shows editors for a few attributes of the selected node type. Which ones
come from the type's ``NodeType.quick_attrs`` (inherited, so every canvas node
gets at least ``opacity``); the user can replace that list per type — the
choice is kept in user settings, not in the scene. Turn the bar off and on with
View ▸ Quick Attribute Editor (``quick_edit.toggle``).

Other plugins put their own controls in front of the fields through the
``quick_edit.sections`` extension point (:class:`QuickSection`), e.g.
playback's transport for animated images. The user can hide each one from the
bar's ⋯ menu (``quick_edit.show_section``).
"""

from __future__ import annotations

from typing import Any


def activate_ui(ui: Any) -> None:
    from .ui import activate_quick_edit_ui

    activate_quick_edit_ui(ui)
