"""Which colour picker the app opens for a colour swatch.

Plugins contribute ``ColorPickerProvider`` objects to ``color.pickers``; the one chosen in
the settings (``colors/picker``) is used, otherwise the first contributed (lowest ``order``).
Qt's own ``QColorDialog`` is always there as the last resort: it is used when it is chosen,
when nothing else is installed and when a provider fails to build its dialog.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QWidget

from ..plugin_api.contracts import ColorPickerProvider

if TYPE_CHECKING:
    from .app import App

logger = logging.getLogger(__name__)

QT_ID = "qt"
SETTING = "colors/picker"
POINT = "color.pickers"

_app: "App | None" = None


def _qt_dialog(parent: QWidget | None, initial: QColor, title: str, alpha: bool = False) -> QColorDialog:
    dialog = QColorDialog(QColor(initial), parent)
    dialog.setWindowTitle(title)
    if alpha:
        dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel)
    return dialog


QT_PROVIDER = ColorPickerProvider(QT_ID, "Qt color dialog", _qt_dialog, order=1e9,
                                  description="The standard Qt dialog (always available)")


def attach(app: "App") -> None:
    global _app
    _app = app


def providers() -> list[ColorPickerProvider]:
    """Every provider in the order they are tried by default; Qt's dialog is last."""
    found = list(_app.session.extensions.contributions(POINT)) if _app is not None else []
    found = [p for p in found if isinstance(p, ColorPickerProvider) and p.id != QT_ID]
    return sorted(found, key=lambda p: p.order) + [QT_PROVIDER]


def preferred_id() -> str:
    """The chosen provider id ('' = the default one)."""
    return str(_app.settings.value(SETTING, "") or "") if _app is not None else ""


def set_preferred(provider_id: str) -> None:
    if _app is not None:
        _app.settings.setValue(SETTING, provider_id)


def active() -> ColorPickerProvider:
    """The provider a swatch opens right now."""
    available = providers()
    chosen = preferred_id()
    return next((p for p in available if p.id == chosen), available[0])


def create(parent: QWidget | None, initial: QColor, title: str, alpha: bool = False) -> Any:
    """A colour dialog from the active provider, falling back to Qt's if it fails."""
    available = providers()
    first = active()
    for provider in [first] + [p for p in available if p is not first and p.id == QT_ID]:
        try:
            return provider.create(parent, QColor(initial), title, alpha)
        except Exception:  # noqa: BLE001  (a broken third-party picker must not stop colour editing)
            logger.exception("Colour picker %r failed; using the next one", provider.id)
    return _qt_dialog(parent, initial, title, alpha)
