"""Per-board recovery snapshots on disk (no Qt, no scene loading).

A board with a file gets its own snapshot, ``<folder>/docs/<id>.pxref`` with ``<id>.json`` beside
it, where ``id`` is a hash of the file's path. Unsaved work is therefore kept per board rather
than in one slot, and a board parked in the Files list is as protected as the current one.

The scene file is always written first and swapped in atomically; the metadata is written after it,
so a snapshot without metadata is an interrupted write and is ignored (never offered, never
overwriting anything).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .workspace import path_key

DOCS = "docs"
CORRUPT = "corrupt"

# Recovery tokens of the windows alive in *this* process: several app instances can share one pid
# (the tests do), so a pid alone cannot say whether a snapshot's writer is still running.
_LIVE_OWNERS: set[str] = set()


def register_owner(token: str) -> None:
    _LIVE_OWNERS.add(token)


def unregister_owner(token: str) -> None:
    _LIVE_OWNERS.discard(token)


def default_alive(pid: int, token: str) -> bool:
    if pid == os.getpid():
        return token in _LIVE_OWNERS
    from pixelref.core.updates.install import process_alive

    return process_alive(pid)


def doc_id(path: str) -> str:
    """Stable id of a board's snapshot: the same file always maps to the same one."""
    return hashlib.sha1(path_key(str(path)).encode("utf-8")).hexdigest()[:16]


def disk_stamp(path: str | None) -> dict[str, int]:
    """The original file's modification time and size, to tell later whether it changed."""
    if not path:
        return {}
    try:
        stat = Path(path).stat()
    except OSError:
        return {}
    return {"disk_mtime_ns": stat.st_mtime_ns, "disk_size": stat.st_size}


def changed_on_disk(meta: dict[str, Any]) -> bool:
    """Whether the original file is no longer what it was when the snapshot was taken."""
    path, mtime = meta.get("scene_file"), meta.get("disk_mtime_ns")
    if not path or mtime is None:
        return False
    current = disk_stamp(path)
    return bool(current) and (current["disk_mtime_ns"] != mtime or current["disk_size"] != meta.get("disk_size"))


def recency(meta: dict[str, Any]) -> tuple[int, str]:
    """Sort key: when a snapshot was last written (finer than saved_at's whole seconds)."""
    ns = meta.get("saved_ns")
    return (ns if isinstance(ns, int) else 0, str(meta.get("saved_at", "")))


@dataclass
class Snapshot:
    id: str
    scene_file: Path
    meta_file: Path
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def path(self) -> str | None:
        return self.meta.get("scene_file") or None

    @property
    def clean(self) -> bool:
        return bool(self.meta.get("clean"))

    @property
    def saved_at(self) -> str:
        return str(self.meta.get("saved_at", ""))

    def info(self) -> dict[str, Any]:
        """The dict shape the recovery service passes around (the legacy slot uses it too)."""
        return {"path": self.scene_file, "key": self.id, **self.meta}


class SnapshotStore:
    def __init__(self, folder: Path, alive: Callable[[int, str], bool] = default_alive) -> None:
        self.root = Path(folder)
        self.folder = self.root / DOCS
        self.alive = alive

    def files(self, path: str) -> tuple[Path, Path]:
        key = doc_id(path)
        return self.folder / f"{key}.pxref", self.folder / f"{key}.json"

    def files_for(self, key: str) -> tuple[Path, Path]:
        return self.folder / f"{key}.pxref", self.folder / f"{key}.json"

    def read(self, key: str) -> Snapshot | None:
        scene_file, meta_file = self.files_for(key)
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if not isinstance(meta, dict) or not meta.get("scene_file") or not scene_file.is_file():
                return None
        except (OSError, ValueError):
            return None
        return Snapshot(key, scene_file, meta_file, meta)

    def list(self) -> list[Snapshot]:
        """Every complete snapshot, oldest first."""
        try:
            keys = [p.stem for p in self.folder.glob("*.json")]
        except OSError:
            return []
        found = [snap for key in keys if (snap := self.read(key)) is not None]
        return sorted(found, key=lambda s: recency(s.meta))

    def written_by_live_window(self, snapshot: Snapshot, my_token: str) -> bool:
        """True if another window that is still running wrote it (not a leftover of a crash)."""
        token, pid = snapshot.meta.get("token"), snapshot.meta.get("pid")
        if not isinstance(token, str) or not isinstance(pid, int) or token == my_token:
            return False
        try:
            return bool(self.alive(pid, token))
        except Exception:  # noqa: BLE001  (an unanswerable question counts as "gone": offer, never lose)
            return False

    def remove(self, key: str) -> None:
        for path in self.files_for(key):
            for leftover in (path, path.with_suffix(".tmp")):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass

    def quarantine(self, key: str) -> Path | None:
        """Move an unreadable snapshot aside (kept, never deleted) so it stops being offered."""
        folder = self.root / CORRUPT
        stamp = time.strftime("%Y%m%d-%H%M%S")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for path in self.files_for(key):
                if path.exists():
                    shutil.move(str(path), str(folder / f"{stamp}-{path.name}"))
        except OSError:
            return None
        return folder
