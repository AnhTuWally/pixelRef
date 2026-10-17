"""QSettings-backed settings for the desktop app."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSettings

from ..core.settings import Settings, convert


class QtSettings(Settings):
    """User settings stored by Qt (registry on Windows, plist/ini elsewhere).

    Uses ``QSettings.defaultFormat()`` on purpose: the tests switch the default
    format to INI files in a temporary folder so they never touch the user's
    real settings.
    """

    def __init__(self) -> None:
        self._qsettings = QSettings(QSettings.defaultFormat(), QSettings.UserScope, "pixelRef", "pixelRef")

    @property
    def qsettings(self) -> QSettings:
        return self._qsettings

    def value(self, key: str, default: Any = None, type: type | None = None) -> Any:  # noqa: A002
        if type is not None and type in (bool, int, float, str):
            return self._qsettings.value(key, default, type=type)
        value = self._qsettings.value(key, default)
        if type is list:
            if value is None or value == "":
                return [] if default is None else list(default)
            return [value] if isinstance(value, str) else list(value)
        return convert(value, type)

    def setValue(self, key: str, value: Any) -> None:  # noqa: N802
        self._qsettings.setValue(key, value)

    def remove(self, key: str) -> None:
        self._qsettings.remove(key)

    def contains(self, key: str) -> bool:
        return self._qsettings.contains(key)

    def sync(self) -> None:
        self._qsettings.sync()
