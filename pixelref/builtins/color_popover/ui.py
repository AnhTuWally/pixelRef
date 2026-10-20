"""Qt side of the colour popover: the provider, the eyedropper hookup and the picker choice."""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QInputDialog

from pixelref.plugin_api import CommandError
from pixelref.plugin_api.contracts import ColorPickerProvider
from pixelref.plugin_api.ui import color_dialog
from pixelref.plugin_api.ui import widgets as w

from .popover import MAX_SWATCHES, ColorPopover

PROVIDER_ID = "popover"
SCAN_LIMIT = 5000


def scene_colors(ui: Any, limit: int = MAX_SWATCHES) -> list[QColor]:
    """The colours nodes in the open scene use, most used first."""
    session = ui.session
    counts: Counter[tuple[int, ...]] = Counter()
    for index, node in enumerate(session.scene.iter_nodes()):
        if index >= SCAN_LIMIT:
            break
        if node.node_type not in session.types:
            continue
        for name, spec in session.types.attrs(node.node_type).items():
            if spec.type in ("color", "color4"):
                value = node.attrs.get(name)
                if isinstance(value, (list, tuple)) and len(value) >= 3:
                    counts[tuple(int(v) for v in value[:4])] += 1
    return [w.color_from(list(key)) for key, _n in counts.most_common(limit)]


def activate_popover_ui(ui: Any) -> None:
    holder: list[Any] = []  # the running screen pick, kept alive until it finishes

    def eyedropper(done: Callable[[QColor | None], None]) -> bool:
        from pixelref.builtins.color_picker.screen import ScreenPicker

        def finished(color: QColor | None) -> None:
            holder.clear()
            done(color)

        def failed(message: str) -> None:
            ui.toast(message)
            finished(None)

        picker = ScreenPicker(finished, lambda _color: None, failed)
        holder.append(picker)
        if not picker.start():
            holder.clear()
            return False
        return True

    try:
        import pixelref.builtins.color_picker.screen  # noqa: F401
        can_pick = True
    except ImportError:
        can_pick = False

    def create(parent: Any, initial: QColor, title: str, alpha: bool = False) -> ColorPopover:
        brush = ui.service("brush")
        recent = list(brush.recent) if brush is not None else []
        dialog = ColorPopover(parent, initial, title, alpha, recent, scene_colors(ui),
                              eyedropper if can_pick else None)
        if brush is not None:
            dialog.accepted.connect(lambda: brush.add_recent(dialog.currentColor()))
        return dialog

    ui.contribute("color.pickers", ColorPickerProvider(
        PROVIDER_ID, "Color popover", create, order=0.0,
        description="Square, hue/alpha sliders, hex, eyedropper, recent and scene colours"))

    def use_picker(picker: str) -> str:
        """Use a colour picker for every swatch: its id (see ``colors list``), or "default"."""
        known = {p.id: p for p in color_dialog.providers()}
        if picker == "default":
            color_dialog.set_preferred("")
        elif picker in known:
            color_dialog.set_preferred(picker)
        else:
            raise CommandError(f"No colour picker '{picker}' (have: {', '.join(known)})")
        return color_dialog.active().id

    def choose_picker() -> str | None:
        """Choose which colour picker the swatches open."""
        available = color_dialog.providers()
        titles = [p.title for p in available]
        current = titles.index(color_dialog.active().title)
        title, ok = QInputDialog.getItem(ui.app.main_window(), "Color picker", "Open colour swatches with:",
                                         titles, current, False)
        if not ok:
            return None
        chosen = available[titles.index(title)]
        return ui.app.run_command("color_popover.use_picker", chosen.id)

    ui.command("use_picker", use_picker, undoable=False)
    ui.command("choose_picker", choose_picker, undoable=False, echo=False)
    ui.action("color_popover.choose", "color_popover.choose_picker", "Choose Color Picker…",
              keywords="colour color picker dialog popover qt swatch", category="Preferences")
    ui.menu("canvas", "color_popover.choose", path="Window", group="window.extras", order=50)
