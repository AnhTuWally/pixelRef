"""The node record stored in a scene."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .attributes import Attr
from .values import fast_copy


@dataclass
class Node:
    """One node of the scene graph.

    ``children`` is ordered back to front: the last child draws on top.
    ``dynamic`` holds attributes added to this node only (``add_attr``) and
    attributes in a file that the node type doesn't declare (e.g. its plugin is missing).
    """

    name: str
    node_type: str
    id: str = ""
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)
    dynamic: dict[str, Attr] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "name": self.name,
            "type": self.node_type,
            "parent": self.parent,
            "children": list(self.children),
            "attrs": fast_copy(self.attrs),
        }
        if self.dynamic:
            data["dynamic"] = {name: attr.to_dict() for name, attr in self.dynamic.items()}
        return data
