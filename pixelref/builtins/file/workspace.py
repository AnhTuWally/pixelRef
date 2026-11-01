"""The persistent workspace: which files a window holds open, in what order.

Pure data handling (no Qt, no scene loading) so it is cheap to run at startup and easy to test:
the versioned manifest (paths and camera only, never scene data), the startup-restore mode with
its migration from the old Session Resume checkbox, and the lock that gives one process
ownership of the saved workspace.
"""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Iterable
from pathlib import Path
from typing import Any

MANIFEST_VERSION = 1
MANIFEST_SETTING = "workspace_manifest"
MODE_SETTING = "workspace_restore"
LEGACY_RESUME_SETTING = "session_resume"
CHILD_ENV = "PIXELREF_WORKSPACE_CHILD"  # set on windows spawned by "Open in New Window"

MODE_WORKSPACE = "workspace"  # restore the Files list and load the active board
MODE_LIST = "list"            # restore the Files list only
MODE_EMPTY = "empty"          # start with nothing open
MODES = (MODE_WORKSPACE, MODE_LIST, MODE_EMPTY)
DEFAULT_MODE = MODE_WORKSPACE  # what a profile that never chose gets (an explicit choice always wins)


def resolve_mode(settings: Any) -> str:
    """The startup-restore mode: the explicit choice, else migrated from Session Resume."""
    value = settings.value(MODE_SETTING, "")
    if isinstance(value, str) and value in MODES:
        return value
    legacy = settings.value(LEGACY_RESUME_SETTING, None)
    if legacy is not None:  # the old Resume Last Session checkbox, set by the user either way
        return MODE_WORKSPACE if str(legacy).lower() in ("true", "1") else MODE_EMPTY
    return DEFAULT_MODE


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def clean_camera(value: Any) -> dict[str, Any] | None:
    """A camera dict in the shape ``view.camera()`` produces, or None if it isn't one."""
    if not isinstance(value, dict):
        return None
    center = value.get("center")
    if not (isinstance(center, (list, tuple)) and len(center) == 2 and all(_number(c) for c in center)):
        return None
    if not (_number(value.get("zoom")) and _number(value.get("rotation"))):
        return None
    if not (isinstance(value.get("flip_x"), bool) and isinstance(value.get("flip_y"), bool)):
        return None
    return {"center": [float(center[0]), float(center[1])], "zoom": float(value["zoom"]),
            "rotation": float(value["rotation"]), "flip_x": value["flip_x"], "flip_y": value["flip_y"]}


def path_key(path: str) -> str:
    """The form in which paths are compared and stored as keys (case and separators normalised)."""
    return os.path.normcase(os.path.normpath(path))


_key = path_key


# ---- colour labels --------------------------------------------------------------------------------
# A label belongs to a file's path, not to the workspace or the document: it is kept in one global
# map, so it is still there when the file is closed and opened again, and never enters a .pxref.

COLORS_SETTING = "file_colors"
PALETTE: dict[str, str] = {  # name -> swatch
    "blue": "#5b9bf0", "green": "#5fb878", "yellow": "#e3c04a",
    "orange": "#ee9a4d", "purple": "#a07be0", "pink": "#e879a8",
}
MAX_COLORS = 200  # past this, labels of files that are neither open nor recent are forgotten


def parse_colors(value: Any) -> dict[str, str]:
    """Validate a stored colour map; anything malformed or with an unknown colour is dropped."""
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and k and v in PALETTE}


def prune_colors(colors: dict[str, str], keep: Iterable[str]) -> dict[str, str]:
    """Keep the map modest: while over MAX_COLORS, forget the oldest labels not in ``keep``."""
    keep_keys = {path_key(p) for p in keep}
    result = dict(colors)
    for key in list(result):
        if len(result) <= MAX_COLORS:
            break
        if key not in keep_keys:
            del result[key]
    return result


def parse_manifest(value: Any, scene_exts: Iterable[str]) -> dict[str, Any]:
    """Validate a stored manifest. Anything malformed is dropped, never raised: a bad entry must
    not stop startup or take the other entries with it."""
    exts = tuple(e.lower() for e in scene_exts)
    result: dict[str, Any] = {"active": None, "untitled": False, "files": []}
    if not isinstance(value, dict) or value.get("version") != MANIFEST_VERSION:
        return result
    seen: set[str] = set()
    raw = value.get("files")
    for item in raw if isinstance(raw, list) else []:
        path = item.get("path") if isinstance(item, dict) else None
        if not isinstance(path, str) or not path or Path(path).suffix.lower() not in exts:
            continue
        if _key(path) in seen:
            continue
        seen.add(_key(path))
        result["files"].append({"path": path, "camera": clean_camera(item.get("camera"))})
    active = value.get("active")
    if isinstance(active, str) and _key(active) in seen:
        result["active"] = next(f["path"] for f in result["files"] if _key(f["path"]) == _key(active))
    result["untitled"] = value.get("untitled") is True and result["active"] is None
    return result


def build_manifest(entries: Iterable[tuple[str, dict[str, Any] | None]], active: str | None,
                   untitled: bool) -> dict[str, Any]:
    return {"version": MANIFEST_VERSION, "active": active, "untitled": bool(untitled and active is None),
            "files": [{"path": path, "camera": camera} for path, camera in entries]}


# Tokens of the workspaces alive in *this* process: several app instances can coexist in one
# process (the tests do this), and they all share one pid.
_LIVE_TOKENS: set[str] = set()


class OwnerLock:
    """Gives one process ownership of the saved workspace.

    The lock file holds ``pid:token``. A lock is stale (and taken over) when its process is gone.
    Windows spawned by "Open in New Window" never try to own it, and a second pixelRef launched
    on its own finds it held and runs without saving a workspace, so two processes can never
    overwrite each other's manifest.
    """

    def __init__(self, file: Path) -> None:
        self.file = file
        self.token = secrets.token_hex(8)
        self.owned = False

    @staticmethod
    def _alive(pid: int, token: str) -> bool:
        if pid == os.getpid():
            return token in _LIVE_TOKENS
        from pixelref.core.updates.install import process_alive

        return process_alive(pid)

    def acquire(self) -> bool:
        if self.owned:
            return True
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            for _ in range(2):
                try:
                    with open(self.file, "x", encoding="utf-8") as handle:
                        handle.write(f"{os.getpid()}:{self.token}")
                    break
                except FileExistsError:
                    if self._holder_alive():
                        return False
                    self.file.unlink(missing_ok=True)  # stale: its owner is gone
            else:
                return False
        except OSError:
            return False  # unwritable location: behave as a non-owner rather than risk sharing
        _LIVE_TOKENS.add(self.token)
        self.owned = True
        return True

    def _holder_alive(self) -> bool:
        try:
            pid_text, _, token = self.file.read_text(encoding="utf-8").partition(":")
            return self._alive(int(pid_text), token)
        except (OSError, ValueError):
            return False

    def release(self) -> None:
        if not self.owned:
            return
        self.owned = False
        _LIVE_TOKENS.discard(self.token)
        try:
            if self.file.read_text(encoding="utf-8").endswith(f":{self.token}"):
                self.file.unlink()
        except OSError:
            pass


def is_child_window() -> bool:
    return os.environ.get(CHILD_ENV) == "1"


def dumps(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, sort_keys=True)
