"""The layout tree: a second document built with the same node engine.

The window layout is kept in its own :class:`SceneGraph` (never part of the
scene's undo history) with these node types::

    wm_output      a top-level window; shows one workspace
    wm_workspace   a numbered/named workspace: one tiling root + floating tiles
    wm_split       children side by side (orientation h) or stacked (v), with sizes
    wm_tabbed      children as tabs
    wm_stacked     children as a vertical list of titles, one visible
    wm_tile        one view (canvas, outliner, console…) and its saved state
    wm_scratchpad  hidden tiles that pop up on demand (the console)

Scripts reach it through ``pxcmd.wm.*`` (``pxcmd.wm.ls(type="wm_tile")``,
``pxcmd.wm.set_attr("Main", "header", 0)``…). It is saved in the user settings;
scene files keep their workspaces (``WindowManager.workspaces_section``).
"""

from __future__ import annotations

from typing import Any

from ...core.attributes import Attr
from ...core.nodetypes import NodeType, NodeTypeRegistry, register_base_types
from ...core.scene import SceneGraph

CONTAINER_TYPES = ("wm_split", "wm_tabbed", "wm_stacked")

WM_TYPES: tuple[NodeType, ...] = (
    NodeType(
        name="wm_output",
        inherits="dg_node",
        label="Output",
        attrs=(
            Attr("workspace", "string", ""),
            Attr("title", "string", ""),
            Attr("primary", "flag", 0),
            Attr("frameless", "flag", 0),
            Attr("on_top", "flag", 0),
            Attr("opacity", "float", 1.0, minimum=0.05, maximum=1.0),
            Attr("geometry", "any", None, doc="[x, y, w, h] of the window"),
        ),
    ),
    NodeType(
        name="wm_workspace",
        inherits="dg_node",
        label="Workspace",
        attrs=(
            Attr("number", "int", 0),
            Attr("title", "string", ""),
            Attr("fullscreen", "string", "", doc="Name of the tile shown alone, or empty"),
            Attr("window_style", "any", None, doc='{"frameless": bool, "on_top": bool, "size": [w, h], "bar": bool, "panels": bool}'),
        ),
    ),
    NodeType(
        name="wm_split",
        inherits="dg_node",
        label="Split",
        attrs=(Attr("orientation", "enum", "h", choices=("h", "v")), Attr("sizes", "any", [])),
    ),
    NodeType(name="wm_tabbed", inherits="dg_node", label="Tabbed", attrs=(Attr("active", "int", 0),)),
    NodeType(name="wm_stacked", inherits="dg_node", label="Stacked", attrs=(Attr("active", "int", 0),)),
    NodeType(
        name="wm_tile",
        inherits="dg_node",
        label="Tile",
        attrs=(
            Attr("view_type", "string", ""),
            Attr("title", "string", ""),
            Attr("state", "any", {}),
            Attr("mark", "string", ""),
            Attr("header", "flag", 1),
            Attr("floating", "flag", 0),
            Attr("shown", "flag", 0, doc="Scratchpad tiles: currently popped up"),
            Attr("geometry", "any", None, doc="Floating tiles: [x, y, w, h]"),
        ),
    ),
    NodeType(name="wm_scratchpad", inherits="dg_node", label="Scratchpad"),
)


def layout_registry() -> NodeTypeRegistry:
    registry = NodeTypeRegistry()
    register_base_types(registry)
    for node_type in WM_TYPES:
        registry.register(node_type)
    return registry


class LayoutDocument:
    """The layout scene graph plus tree helpers."""

    SCRATCHPAD = "Scratchpad"

    def __init__(self) -> None:
        self.types = layout_registry()
        self.scene = SceneGraph(self.types)

    # ---- Queries ---------------------------------------------------------------------

    def node(self, name: str) -> Any:
        return self.scene.get_node(name)

    def has(self, name: str) -> bool:
        return self.scene.has(name)

    def type_of(self, name: str) -> str:
        return self.scene.get_node(name).node_type

    def attr(self, name: str, key: str, default: Any = None) -> Any:
        node = self.scene.nodes.get(name)
        return node.attrs.get(key, default) if node is not None else default

    def set(self, name: str, **values: Any) -> None:
        self.scene.set_attrs(name, values)

    def of_type(self, node_type: str) -> list[str]:
        return [node.name for node in self.scene.iter_nodes() if node.node_type == node_type]

    def outputs(self) -> list[str]:
        return self.of_type("wm_output")

    def primary_output(self) -> str | None:
        outputs = self.outputs()
        for name in outputs:
            if self.attr(name, "primary"):
                return name
        return outputs[0] if outputs else None

    def workspaces(self) -> list[str]:
        names = self.of_type("wm_workspace")
        return sorted(names, key=lambda n: (int(self.attr(n, "number", 0) or 0) or 999, n))

    def workspace_by_ref(self, ref: Any) -> str | None:
        """Workspace by name, number or title."""
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            number = int(ref)
            for name in self.workspaces():
                if int(self.attr(name, "number", 0) or 0) == number:
                    return name
            return None
        ref = str(ref)
        if self.has(ref) and self.type_of(ref) == "wm_workspace":
            return ref
        for name in self.workspaces():
            if self.attr(name, "title") == ref:
                return name
        return None

    def children(self, name: str) -> list[str]:
        return self.scene.children_of(name)

    def parent(self, name: str) -> str | None:
        return self.scene.get_node(name).parent

    def tiling_root(self, workspace: str) -> str | None:
        for child in self.children(workspace):
            if self.type_of(child) != "wm_tile" or not self.attr(child, "floating"):
                return child
        return None

    def floating(self, workspace: str) -> list[str]:
        return [c for c in self.children(workspace) if self.type_of(c) == "wm_tile" and self.attr(c, "floating")]

    def tiles(self, root: str | None = None) -> list[str]:
        if root is None:
            return self.of_type("wm_tile")
        names = [root] + self.scene.descendants(root)
        return [n for n in names if self.type_of(n) == "wm_tile"]

    def workspace_of(self, name: str) -> str | None:
        current: str | None = name
        while current is not None:
            if self.type_of(current) in ("wm_workspace", "wm_scratchpad"):
                return current
            current = self.parent(current)
        return None

    def output_showing(self, workspace: str) -> str | None:
        for output in self.outputs():
            if self.attr(output, "workspace") == workspace:
                return output
        return None

    def scratchpad(self) -> str:
        if not self.has(self.SCRATCHPAD):
            self.scene.create_node("wm_scratchpad", self.SCRATCHPAD)
        return self.SCRATCHPAD

    def find_mark(self, mark: str) -> str | None:
        for tile in self.tiles():
            if self.attr(tile, "mark") == mark:
                return tile
        return None

    def to_dict(self) -> dict[str, Any]:
        return self.scene.to_dict()

    def tree(self, name: str | None = None) -> Any:
        """Nested plain data of the layout (for ``pxcmd.wm.tree()``)."""
        def walk(node_name: str) -> dict[str, Any]:
            node = self.scene.get_node(node_name)
            entry: dict[str, Any] = {"name": node.name, "type": node.node_type}
            for key, value in node.attrs.items():
                if key != "tags" and value not in (None, "", [], {}):
                    entry[key] = value
            if node.children:
                entry["children"] = [walk(child) for child in node.children]
            return entry

        if name is not None:
            return walk(name)
        return [walk(root) for root in self.scene.roots]


def normalized_sizes(sizes: Any, count: int) -> list[float]:
    try:
        values = [max(0.0, float(v)) for v in (sizes or [])]
    except (TypeError, ValueError):
        values = []
    if len(values) != count or sum(values) <= 0:
        return [1.0 / count] * count if count else []
    total = sum(values)
    return [v / total for v in values]
