"""The colour popover plugin: a colour picker for every swatch (see ``popover.py``).

Other plugins add pickers by contributing a ``ColorPickerProvider`` to ``color.pickers``;
"Choose Color Picker…" (or the ``colors/picker`` setting) selects which one the swatches
open. Qt's own dialog is the fallback when a picker is missing or fails.
"""

from __future__ import annotations

from typing import Any


def activate(ctx: Any) -> None:
    pass


def activate_ui(ui: Any) -> None:
    from .ui import activate_popover_ui

    activate_popover_ui(ui)
