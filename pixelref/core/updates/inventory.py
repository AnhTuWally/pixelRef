"""The release file-ownership inventory: which files of an install belong to the installed release.

``packaging/build_exe.py`` writes ``release-inventory.json`` into the standalone folder. The updater may
only replace or remove paths listed here (and only while they still have the listed hash); everything
else beside the program (``portable.toml``, ``user-data/``, scripts, boards, unknown files) is the
user's and is never touched. The file is local and unsigned: a local attacker can edit it, see
docs/update-security.md. Its content for a *new* release is covered by the signed manifest, because the
inventory file is itself a file of the release archive.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .versions import Version, try_parse_version
from .verification import safe_relative_path

INVENTORY_NAME = "release-inventory.json"
EXCLUDED_TOP_LEVEL = frozenset({"portable.toml", "user-data", ".pixelref-update", INVENTORY_NAME})
SCHEMA_VERSION = 1


class InventoryError(Exception):
    pass


@dataclass(frozen=True)
class Inventory:
    version: Version
    files: dict[str, tuple[int, str]]   # relative path -> (size, sha256)

    def top_levels(self) -> set[str]:
        return {path.split("/", 1)[0] for path in self.files} | {INVENTORY_NAME}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_inventory(app_dir: Path, version: str) -> dict:
    files = []
    for current, dirs, names in os.walk(app_dir):
        base = Path(current)
        if base == app_dir:
            dirs[:] = [d for d in dirs if d not in EXCLUDED_TOP_LEVEL]
        for name in sorted(names):
            path = base / name
            relative = path.relative_to(app_dir).as_posix()
            if base == app_dir and name in EXCLUDED_TOP_LEVEL:
                continue
            if path.is_symlink() or not safe_relative_path(relative):
                raise InventoryError(f"cannot inventory {relative!r}")
            files.append({"path": relative, "size": path.stat().st_size, "sha256": file_sha256(path)})
    files.sort(key=lambda f: f["path"])
    return {"schema_version": SCHEMA_VERSION, "version": version, "files": files}


def write_inventory(app_dir: Path, version: str) -> Path:
    target = Path(app_dir) / INVENTORY_NAME
    target.write_text(json.dumps(build_inventory(Path(app_dir), version), indent=1) + "\n", encoding="utf-8")
    return target


def load_inventory(root: Path) -> Inventory:
    try:
        data = json.loads((Path(root) / INVENTORY_NAME).read_text(encoding="utf-8"))
        version = try_parse_version(data["version"])
        if data["schema_version"] != SCHEMA_VERSION or version is None or not isinstance(data["files"], list):
            raise ValueError("unsupported inventory")
        files: dict[str, tuple[int, str]] = {}
        for entry in data["files"]:
            path, size, digest = entry["path"], entry["size"], entry["sha256"]
            if (not safe_relative_path(path) or path.split("/", 1)[0] in EXCLUDED_TOP_LEVEL or path in files
                    or isinstance(size, bool) or not isinstance(size, int) or size < 0
                    or not isinstance(digest, str) or len(digest) != 64):
                raise ValueError(f"bad entry {path!r}")
            files[path] = (size, digest)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise InventoryError(f"no usable {INVENTORY_NAME}: {exc}") from exc
    return Inventory(version, files)
