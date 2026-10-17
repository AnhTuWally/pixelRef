"""Qt side of grouping: groups act as one item on the canvas.

A click on anything in a group picks the group: it selects, moves, scales and
copies as a unit. Double-click a group to go inside and pick a member (the
group stays open while something in it is selected); Esc goes back out to the
group. Groups with ``pick_members`` on are plain containers instead: their
members are picked directly.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem

from pixelref.plugin_api.ui import ContainerItem, NodeItemMixin, theme


class GroupItem(NodeItemMixin, QGraphicsItem):
    """A group: draws nothing itself and is hit-tested on the union of its members."""

    captures_children = True
    opens_for_selection = True

    def __init__(self, node_name: str) -> None:
        super().__init__()
        self._init_node(node_name, "group", None)
        self._editing = False
        self._shape_cache: QPainterPath | None = None
        # childrenBoundingRect() walks every descendant, and Qt asks for the bounds
        # often, so nested groups were cubic in depth without this cache.
        self._bounds_cache: QRectF | None = None
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.ItemHasNoContents, True)

    @property
    def editing(self) -> bool:
        """True while the group is open (something inside it is selected)."""
        return self._editing

    def set_editing(self, editing: bool) -> None:
        self._editing = bool(editing)

    def children_changed(self) -> None:
        self._shape_cache = None
        if self._bounds_cache is None:
            # Not read since it was last invalidated, so the ancestors were invalidated
            # then too (reading theirs would have refilled this one).
            return
        self.prepareGeometryChange()  # reads the old bounds, so clear the cache after
        self._bounds_cache = None
        parent = self.parentItem()
        if isinstance(parent, NodeItemMixin) and hasattr(parent, "children_changed"):
            parent.children_changed()  # type: ignore[attr-defined]

    def content_rect(self) -> QRectF:
        rect = QRectF()
        for child in self.childItems():
            if child.isVisible():
                inner = child.content_rect() if isinstance(child, NodeItemMixin) else child.boundingRect()
                if not inner.isNull():
                    rect = rect.united(child.mapRectToParent(inner))
        return rect if rect.isValid() else QRectF(0.0, 0.0, 1.0, 1.0)

    def boundingRect(self) -> QRectF:
        if self._bounds_cache is None:
            self._bounds_cache = self.childrenBoundingRect()
        return QRectF(self._bounds_cache)

    def shape(self) -> QPainterPath:
        if self._shape_cache is None:
            path = QPainterPath()
            path.setFillRule(Qt.WindingFill)
            for child in self.childItems():
                if child.isVisible():
                    path.addPath(child.mapToParent(child.shape()))
            self._shape_cache = path
        return self._shape_cache

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        return super().itemChange(change, value)


def member_at(view: Any, group: GroupItem, scene_pos: QPointF) -> str | None:
    """The member of ``group`` (one level down) drawn at ``scene_pos``."""
    for hit in view.scene().items(scene_pos, Qt.IntersectsItemShape, Qt.DescendingOrder, view.transform()):
        node = hit
        while node is not None and not isinstance(node, NodeItemMixin):
            node = node.parentItem()
        if node is None or node is group or not node.is_content or not node.isVisible():
            continue
        if not view.display.shows(node.node_name):
            continue
        while node is not None and node.parentItem() is not group:
            node = node.parentItem()
        if node is not None:
            return node.node_name
    return None


def activate_grouping_ui(ui: Any) -> None:
    canvas = ui.canvas
    session = ui.session

    def group_factory(_canvas: Any, node: dict[str, Any]) -> Any:
        # Toggling pick_members recreates the item, so it can swap classes.
        if node.get("attrs", {}).get("pick_members"):
            return ContainerItem(node["name"], node["type"])
        return GroupItem(node["name"])

    def group_post_sync(item: Any, _node: dict[str, Any]) -> None:
        if isinstance(item, GroupItem):
            item.children_changed()

    def describe(_name: str, node: dict[str, Any]) -> list[str]:
        count = len(node.get("children", []))
        return [f"{count} item{'s' if count != 1 else ''}"]

    def enter_group(view: Any, item: Any, scene_pos: QPointF | None = None) -> bool:
        """Double-click: go inside the group and pick the member under the pointer."""
        if not isinstance(item, GroupItem) or item.locked:
            return False
        member = member_at(view, item, scene_pos) if scene_pos is not None else None
        if member is None:
            children = [c.node_name for c in item.childItems() if isinstance(c, NodeItemMixin) and c.isVisible()]
            member = children[-1] if children else None
        if member is None:
            return False
        ui.app.run_command("select", [member])
        ui.status(f"Inside {item.node_name} · Esc to go back to the group")
        return True

    ui.node_ui("group", factory=group_factory, label="Group", icon="layer", badge=("#8f86c4", "G"),
               post_sync=group_post_sync, describe=describe, double_click=enter_group)

    def select_parent_group() -> bool:
        """Esc inside a group selects the group again."""
        parents = []
        for name in session.selection.names:
            parent = canvas.nodes.get(name, {}).get("parent")
            if parent and canvas.nodes.get(parent, {}).get("type") == "group":
                parents.append(parent)
        if not parents:
            return False
        ui.app.run_command("select", list(dict.fromkeys(parents)))
        return True

    ui.escape_handler(select_parent_group, order=20)

    def paint_open_groups(view: Any, painter: QPainter) -> None:
        """A dashed frame around each open group, so it's clear where 'inside' ends."""
        if not view.display.show_overlays:
            return
        pad = 6.0 / max(view.zoom_level(), 1e-6)
        pen = QPen(theme.ACCENT, 1.0, Qt.DashLine)
        pen.setDashPattern([4.0, 4.0])
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        for item in canvas.open_compounds():
            try:
                if not item.isVisible() or not view.display.shows(item.node_name):
                    continue
                polygon = item.sceneTransform().map(QPolygonF(item.content_rect().adjusted(-pad, -pad, pad, pad)))
            except RuntimeError:
                continue
            painter.drawPolygon(QPolygonF([view.to_viewport(p) for p in polygon]))

    ui.overlay(paint_open_groups, order=41)
