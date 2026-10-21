"""Folders pixelRef looks in for plugins ("sources").

* ``builtin``  pixelref/builtins (always trusted)
* ``user``     the user plugin folder
* ``added``    folders the user added in Window → Plugins… → Sources (kept in settings)
* ``env``      folders on ``$PIXELREF_PLUGIN_PATH`` (this run only)
* ``cli``      ``--plugin-path`` / ``--trust-plugin-path`` (this run only)

A source is either a folder of plugin folders or one plugin folder (it has a
``plugin.toml``). Each has a trust policy for the plugins found in it:

* ``ask``          every plugin needs the user's approval (unless signed by a trusted publisher)
* ``signed_only``  only plugins signed by a trusted publisher can load
* ``trust_all``    everything in it loads (developer folders, ``--trust-plugin-path``)
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SOURCES_KEY = "plugins/sources"
POLICIES = ("ask", "signed_only", "trust_all")
POLICY_LABELS = {
    "ask": "Ask before loading",
    "signed_only": "Only trusted publishers",
    "trust_all": "Trust everything (developer)",
}
KIND_LABELS = {"builtin": "Built-in", "user": "User plugins", "added": "Added", "env": "PIXELREF_PLUGIN_PATH",
               "cli": "Command line"}
EDITABLE_KINDS = ("user", "added")  # the others are fixed for this run


@dataclass
class PluginSource:
    path: Path
    kind: str
    label: str = ""
    enabled: bool = True
    policy: str = "ask"

    @property
    def title(self) -> str:
        return self.label or KIND_LABELS.get(self.kind, self.kind)

    @property
    def editable(self) -> bool:
        return self.kind in EDITABLE_KINDS

    def plugin_folders(self) -> list[Path]:
        """Plugin folders in this source (a source that is itself a plugin is one)."""
        if not self.path.is_dir():
            return []
        if (self.path / "plugin.toml").is_file():
            return [self.path]
        try:
            entries = sorted(self.path.iterdir())
        except OSError:
            return []
        return [entry for entry in entries if entry.is_dir() and (entry / "plugin.toml").is_file()]

    def warnings(self) -> list[str]:
        return folder_warnings(self.path) if self.kind != "builtin" else []

    def to_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "kind": self.kind, "label": self.label, "enabled": self.enabled,
                "policy": self.policy}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PluginSource":
        policy = str(data.get("policy", "ask"))
        return cls(path=Path(str(data.get("path", ""))), kind=str(data.get("kind", "added")),
                   label=str(data.get("label", "")), enabled=bool(data.get("enabled", True)),
                   policy=policy if policy in POLICIES else "ask")

    def info(self, plugin_count: int = 0) -> dict[str, Any]:
        return {**self.to_dict(), "title": self.title, "editable": self.editable, "exists": self.path.is_dir(),
                "warnings": self.warnings(), "plugins": plugin_count}


def same_path(a: Path | str, b: Path | str) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def is_network_path(path: Path) -> bool:
    text = str(path)
    return text.startswith(("\\\\", "//"))


def folder_warnings(path: Path) -> list[str]:
    """Why a plugin folder could let someone else put code on this computer."""
    out = []
    if not path.exists():
        out.append("The folder doesn't exist.")
        return out
    if is_network_path(path):
        out.append("It's on a network share: anyone who can write there can change what pixelRef runs.")
    if sys.platform != "win32":
        try:
            mode = path.stat().st_mode
        except OSError:
            return out
        if mode & stat.S_IWOTH:
            out.append("Every user of this computer can write to it.")
        elif mode & stat.S_IWGRP:
            out.append("Other users in its group can write to it.")
    return out
