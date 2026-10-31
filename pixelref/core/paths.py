"""Fully portable mode: keep settings, plugins, boards' default folder, recovery files, caches and managed
Python packages in a folder beside the program instead of the operating system's per-user folders.

Opt-in. Put a ``portable.toml`` next to ``pixelRef.exe``::

    [portable]
    enabled = true
    data_directory = "user-data"     # optional; relative to this folder

Everything is resolved relative to the application folder, never the current directory. Without the file
(or with ``enabled = false``) nothing changes: the existing per-user locations are used. A copy running from
source has no application folder and is never portable. A broken ``portable.toml`` is reported (``problem``)
and the per-user locations are used, so a typo can't scatter data somewhere unexpected.

This module only answers "is it portable, and where". The existing providers (``App.config_dir``,
``App.data_dir``, the recovery, cache, save and package folders, ``QtSettings``) ask it first and keep their
own defaults otherwise. ``portable.toml`` and the data folder belong to the user: the updater never touches
them (docs/update-security.md).
"""

from __future__ import annotations

import logging
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

logger = logging.getLogger(__name__)

MARKER = "portable.toml"
DEFAULT_DATA_DIRECTORY = "user-data"
# Names the release owns or reserves: the data folder may not be one of them.
RESERVED = frozenset({".pixelref-update", "_internal", "pixelRef.exe", "pixelref-cli.exe", "release-inventory.json",
                      MARKER})


def application_root() -> Path | None:
    """The folder holding the standalone executable, or None when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


@dataclass(frozen=True)
class PortableLocations:
    root: Path        # the application folder
    data: Path        # <root>/<data_directory>

    @property
    def config(self) -> Path:       # keymap.toml
        return self.data / "config"

    @property
    def local_data(self) -> Path:   # user plugins, recovery files, plugin activity log
        return self.data / "data"

    @property
    def cache(self) -> Path:
        return self.data / "cache"

    @property
    def packages(self) -> Path:     # managed Python packages (one sub-folder per Python version and platform)
        return self.data / "python"

    @property
    def settings_file(self) -> Path:
        return self.data / "settings" / "pixelRef.ini"

    @property
    def boards(self) -> Path:       # default folder for new scenes
        return self.data / "boards"


class PortableError(ValueError):
    pass


def _data_directory(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PortableError("data_directory must be a folder name")
    text = value.strip()
    parts = PurePosixPath(text.replace("\\", "/")).parts
    if (PureWindowsPath(text).drive or PurePosixPath(text).is_absolute() or text.startswith(("/", "\\"))
            or not parts or any(p in ("..", ".") for p in parts) or ":" in text or "\x00" in text):
        raise PortableError("data_directory must be a plain relative folder inside the application folder")
    if parts[0].casefold() in {r.casefold() for r in RESERVED}:
        raise PortableError(f"data_directory can't be {parts[0]!r}: that name belongs to the program")
    return "/".join(parts)


def read_portable(root: Path | None) -> tuple[PortableLocations | None, str]:
    """(locations, problem). ``locations`` is None when not portable; ``problem`` explains a broken marker."""
    if root is None:
        return None, ""
    marker = Path(root) / MARKER
    if not marker.is_file():
        return None, ""
    try:
        data = tomllib.loads(marker.read_text(encoding="utf-8"))
        section = data.get("portable", {})
        if not isinstance(section, dict):
            raise PortableError("[portable] must be a table")
        enabled = section.get("enabled", True)
        if not isinstance(enabled, bool):
            raise PortableError("enabled must be true or false")
        if not enabled:
            return None, ""
        folder = _data_directory(section.get("data_directory", DEFAULT_DATA_DIRECTORY))
        return PortableLocations(Path(root), Path(root) / folder), ""
    except (OSError, ValueError) as exc:  # tomllib.TOMLDecodeError is a ValueError
        message = f"{MARKER} is not usable ({exc}); using the normal per-user folders."
        logger.warning(message)
        return None, message


_override: list[Path | None] = []   # tests only: pretend the application folder is this
_cache: dict[Path | None, tuple[PortableLocations | None, str]] = {}


def set_application_root_for_tests(root: Path | None) -> None:
    _override[:] = [root] if root is not None else []
    _cache.clear()


def _state() -> tuple[PortableLocations | None, str]:
    root = _override[0] if _override else application_root()
    if root not in _cache:
        _cache[root] = read_portable(root)
    return _cache[root]


def portable() -> PortableLocations | None:
    """The portable locations, or None for the normal per-user layout. Resolved once per process."""
    return _state()[0]


def problem() -> str:
    return _state()[1]
