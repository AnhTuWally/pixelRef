"""Persistent user settings.

The UI uses a ``QSettings``-backed implementation (``pixelref.ui.settings``);
headless sessions use :class:`MemorySettings` (optionally saved as JSON). Plugins
get a :class:`ScopedSettings` view with their keys under ``plugins/<id>/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def convert(value: Any, kind: type | None) -> Any:
    if kind is None or value is None:
        return value
    if kind is bool:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if kind in (int, float):
        try:
            return kind(value)
        except (TypeError, ValueError):
            return kind()
    if kind is list:
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]
    if kind is str:
        return str(value)
    return value


class Settings:
    """Minimal key/value interface shared by every settings backend."""

    def value(self, key: str, default: Any = None, type: type | None = None) -> Any:  # noqa: A002
        raise NotImplementedError

    def setValue(self, key: str, value: Any) -> None:  # noqa: N802  (QSettings naming)
        raise NotImplementedError

    def remove(self, key: str) -> None:
        raise NotImplementedError

    def contains(self, key: str) -> bool:
        raise NotImplementedError

    def sync(self) -> None:
        return None

    def scoped(self, prefix: str) -> "ScopedSettings":
        return ScopedSettings(self, prefix)

    # Convenience for JSON-structured values.
    def json_value(self, key: str, default: Any = None) -> Any:
        raw = self.value(key, "")
        if not raw:
            return default
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return default

    def set_json(self, key: str, value: Any) -> None:
        self.setValue(key, json.dumps(value))


class MemorySettings(Settings):
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._data: dict[str, Any] = {}
        if self._path is not None and self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            self._data = data if isinstance(data, dict) else {}

    def value(self, key: str, default: Any = None, type: type | None = None) -> Any:  # noqa: A002
        return convert(self._data.get(key, default), type)

    def setValue(self, key: str, value: Any) -> None:  # noqa: N802
        self._data[key] = value
        self.sync()

    def remove(self, key: str) -> None:
        prefix = key.rstrip("/") + "/"
        for existing in [k for k in self._data if k == key or k.startswith(prefix)]:
            del self._data[existing]
        self.sync()

    def contains(self, key: str) -> bool:
        return key in self._data

    def sync(self) -> None:
        if self._path is not None:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            except OSError:
                pass


class ScopedSettings(Settings):
    def __init__(self, base: Settings, prefix: str) -> None:
        self._base = base
        self._prefix = prefix.rstrip("/") + "/"

    def _key(self, key: str) -> str:
        return self._prefix + key

    def value(self, key: str, default: Any = None, type: type | None = None) -> Any:  # noqa: A002
        return self._base.value(self._key(key), default, type)

    def setValue(self, key: str, value: Any) -> None:  # noqa: N802
        self._base.setValue(self._key(key), value)

    def remove(self, key: str) -> None:
        self._base.remove(self._key(key))

    def contains(self, key: str) -> bool:
        return self._base.contains(self._key(key))

    def sync(self) -> None:
        self._base.sync()
