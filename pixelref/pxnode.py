"""``pixelref.pxnode`` — an object-oriented convenience layer over ``pxcmd``.

    from pixelref import pxnode

    img = pxnode.node("Image1")
    img.opacity = 0.5                 # pxcmd.set_attr("Image1.opacity", 0.5)
    img.translate                     # pxcmd.get_attr("Image1.translate")
    for n in pxnode.selected():
        n.tags = n.tags + ["review"]
    note = pxnode.create("annotation_text", parent=img, text="Fix")

Every attribute access is a ``pxcmd`` call, so it is undoable and echoed
like anything else.
"""

from __future__ import annotations

from typing import Any, Iterator

from .core.session import current_session


class Node:
    """A handle on a node by name (follows renames done through it)."""

    __slots__ = ("_name",)

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_name", current_session().scene.resolve(name))

    @property
    def name(self) -> str:
        return self._name

    @property
    def id(self) -> str:
        return current_session().scene.get_node(self._name).id

    @property
    def type(self) -> str:
        return current_session().scene.get_node(self._name).node_type

    @property
    def parent(self) -> "Node | None":
        parent = current_session().scene.get_node(self._name).parent
        return Node(parent) if parent else None

    @property
    def children(self) -> list["Node"]:
        return [Node(n) for n in current_session().scene.children_of(self._name)]

    def attrs(self) -> list[str]:
        return _run("attrs", self._name)

    def get(self, attr: str) -> Any:
        return _run("get_attr", self._name, attr)

    def set(self, attr: str, value: Any) -> None:
        _run("set_attr", self._name, attr, value)

    def rename(self, new_name: str) -> "Node":
        _run("rename", self._name, new_name)
        object.__setattr__(self, "_name", new_name)
        return self

    def delete(self) -> None:
        _run("delete", self._name)

    def select(self, add: bool = False) -> None:
        _run("select", self._name, add=add)

    def __getattr__(self, attr: str) -> Any:
        if attr.startswith("_"):
            raise AttributeError(attr)
        return _run("get_attr", self._name, attr)

    def __setattr__(self, attr: str, value: Any) -> None:
        if attr.startswith("_"):
            object.__setattr__(self, attr, value)
            return
        _run("set_attr", self._name, attr, value)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Node) and other._name == self._name

    def __hash__(self) -> int:
        return hash(self._name)

    def __repr__(self) -> str:
        return f"pxnode.node({self._name!r})"


def _run(command: str, *args: Any, **kwargs: Any) -> Any:
    return current_session().run(command, *args, **kwargs)


def node(name: str) -> Node:
    return Node(name)


def ls(**filters: Any) -> list[Node]:
    return [Node(n) for n in _run("ls", **filters)]


def selected() -> list[Node]:
    return [Node(n) for n in _run("selected")]


def create(node_type: str, name: str | None = None, parent: Any = None, **attrs: Any) -> Node:
    parent_name = parent.name if isinstance(parent, Node) else parent
    return Node(_run("create_node", node_type, name, parent=parent_name, **attrs))


def iter_nodes() -> Iterator[Node]:
    for name in list(current_session().scene.nodes):
        yield Node(name)
