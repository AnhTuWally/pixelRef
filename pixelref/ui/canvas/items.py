"""Base classes for the QGraphicsItems that draw nodes.

Every item uses the core transform model, so the canvas treats them
uniformly::

    parent_point = translate + pivot + R(rotate) · S(scale) · (local_point - pivot)

``translate`` is stored on the item as ``pos()`` and the rest as
``transform()``; QGraphicsItem's own rotation/scale properties stay at their
defaults. Node-type plugins subclass :class:`NodeItemMixin` (with a
QGraphicsItem class) to draw their content; the drawable must be rebuildable
from the node's attributes at any time.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsItem, QStyle, QStyleOptionGraphicsItem

from ...core import xform
from ...core.xform import normalize_angle

GeometryListener = Callable[[QGraphicsItem], None]


# ---- Transform helpers (Qt flavour of pixelref.core.xform) ------------------------------------


def to_qtransform(m: xform.Matrix) -> QTransform:
    return QTransform(m.m11, m.m12, m.m21, m.m22, m.dx, m.dy)


def from_qtransform(t: QTransform) -> xform.Matrix:
    return xform.Matrix(t.m11(), t.m12(), t.m21(), t.m22(), t.dx(), t.dy())


def compose_transform(x: float, y: float, sx: float, sy: float, rotation: float,
                      px: float = 0.0, py: float = 0.0) -> QTransform:
    """Local → parent transform for translate, scale, rotation (degrees) and pivot."""
    return to_qtransform(xform.compose(x, y, sx, sy, rotation, px, py))


def decompose_transform(t: QTransform, prev_sx: float = 1.0, prev_rotation: float = 0.0,
                        px: float = 0.0, py: float = 0.0) -> tuple[float, float, float, float, float]:
    """Split a transform into (x, y, sx, sy, rotation); see ``core.xform.decompose``."""
    return xform.decompose(from_qtransform(t), prev_sx, prev_rotation, px, py)


def scaled_about(point: QPointF, fx: float, fy: float) -> QTransform:
    return (
        QTransform.fromTranslate(-point.x(), -point.y())
        * QTransform.fromScale(fx, fy)
        * QTransform.fromTranslate(point.x(), point.y())
    )


def rotated_about(point: QPointF, degrees: float) -> QTransform:
    return (
        QTransform.fromTranslate(-point.x(), -point.y())
        * QTransform().rotate(degrees)
        * QTransform.fromTranslate(point.x(), point.y())
    )


def parent_scene_transform(item: QGraphicsItem) -> QTransform:
    parent = item.parentItem()
    return parent.sceneTransform() if parent is not None else QTransform()


# Views rendering offscreen (tile caches): scene.render paints with no widget,
# so items look the view up here to apply its display filters.
_rendering_views: list[Any] = []


@contextmanager
def rendering_for(view: Any) -> Iterator[None]:
    """Items painted inside this block (with no widget) are painted for ``view``."""
    _rendering_views.append(view)
    try:
        yield
    finally:
        _rendering_views.pop()


def view_for(widget: Any) -> Any:
    """The canvas view an item is being painted for, or None (export, thumbnails)."""
    if widget is not None:
        return getattr(widget, "_pixelref_view", None)
    return _rendering_views[-1] if _rendering_views else None


def unselected_option(option: QStyleOptionGraphicsItem) -> QStyleOptionGraphicsItem:
    """Copy of ``option`` without State_Selected (the canvas draws selection itself)."""
    opt = QStyleOptionGraphicsItem(option)
    opt.state = opt.state & ~QStyle.StateFlag.State_Selected & ~QStyle.StateFlag.State_HasFocus
    return opt


# ---- Base behaviour ----------------------------------------------------------------------------


class NodeItemMixin:
    """Shared behaviour for every item that mirrors a scene node.

    Subclasses may set:

    * ``is_content`` — False for pure containers (not selectable/transformable)
    * ``captures_children`` — clicks on children pick this item (pen drawings, groups)
    * ``opens_for_selection`` — with ``captures_children``: the canvas opens the item
      (``set_editing(True)``) while something inside it is selected (groups)
    """

    node_name: str
    node_type: str
    is_content = True
    captures_children = False
    opens_for_selection = False
    _listener: GeometryListener | None = None
    _sx = 1.0
    _sy = 1.0
    _rotation = 0.0
    _px = 0.0
    _py = 0.0
    _locked = False

    def _init_node(self, node_name: str, node_type: str, listener: GeometryListener | None) -> None:
        self.node_name = node_name
        self.node_type = node_type
        self._listener = listener
        self._sx, self._sy, self._rotation = 1.0, 1.0, 0.0
        self._px, self._py = 0.0, 0.0
        self._locked = False

    def set_listener(self, listener: GeometryListener | None) -> None:
        self._listener = listener

    # ---- Geometry ------------------------------------------------------------------

    def content_rect(self) -> QRectF:
        return self.boundingRect()  # type: ignore[attr-defined]

    def scene_polygon(self) -> QPolygonF:
        """The four content corners in scene coordinates (tl, tr, br, bl)."""
        # One map() of the rect's closed polygon (tl, tr, br, bl, tl) is ~3× faster than four point maps.
        polygon = self.sceneTransform().map(QPolygonF(self.content_rect()))  # type: ignore[attr-defined]
        polygon.removeLast()
        return polygon

    def scene_content_rect(self) -> QRectF:
        return self.scene_polygon().boundingRect()

    def outline_line(self) -> tuple[QPointF, QPointF] | None:
        """Lines are outlined as a line, not a box (scene coordinates)."""
        return None

    @property
    def blocks_transform(self) -> bool:
        """True while the item must not be moved by the gizmo (e.g. text being edited)."""
        return False

    # ---- Transform ------------------------------------------------------------------

    def logical_transform(self) -> tuple[float, float, float, float, float]:
        pos = self.pos()  # type: ignore[attr-defined]
        return pos.x(), pos.y(), self._sx, self._sy, self._rotation

    @property
    def pivot(self) -> tuple[float, float]:
        return self._px, self._py

    def local_transform(self) -> QTransform:
        x, y, sx, sy, rotation = self.logical_transform()
        return compose_transform(x, y, sx, sy, rotation, self._px, self._py)

    def apply_transform_values(self, x: float, y: float, sx: float, sy: float, rotation: float,
                               pivot: tuple[float, float] | None = None) -> None:
        if pivot is not None:
            self._px, self._py = float(pivot[0]), float(pivot[1])
        self._sx, self._sy, self._rotation = sx, sy, normalize_angle(rotation)
        self.setTransform(compose_transform(0.0, 0.0, sx, sy, self._rotation, self._px, self._py))  # type: ignore
        self.setPos(x, y)  # type: ignore[attr-defined]
        # A rotation or scale about the pivot can leave pos unchanged (no ItemPositionHasChanged),
        # so tell a compound parent its extent changed.
        parent = self.parentItem()  # type: ignore[attr-defined]
        if isinstance(parent, NodeItemMixin) and hasattr(parent, "children_changed"):
            parent.children_changed()  # type: ignore[attr-defined]

    def set_local_transform(self, t: QTransform) -> None:
        x, y, sx, sy, rotation = decompose_transform(t, self._sx, self._rotation, self._px, self._py)
        self.apply_transform_values(x, y, sx, sy, rotation)
        if self._listener is not None:
            self._listener(self)  # type: ignore[arg-type]

    def set_scene_transform(self, scene_t: QTransform) -> None:
        """Place the item so its full item → scene transform equals ``scene_t``."""
        inverse, ok = parent_scene_transform(self).inverted()  # type: ignore[arg-type]
        if not ok:
            return
        self.set_local_transform(scene_t * inverse)

    def transform_attrs(self) -> dict[str, Any]:
        """The core transform attribute values for the item's current placement."""
        x, y, sx, sy, rotation = self.logical_transform()
        return {
            "translate": [round(x, 3) + 0.0, round(y, 3) + 0.0],
            "scale": [round(sx, 6) + 0.0, round(sy, 6) + 0.0],
            "rotate": round(rotation, 4) + 0.0,
        }

    def commit_values(self) -> dict[str, Any]:
        """Attribute values to write back after a canvas edit (transform + type extras)."""
        return self.transform_attrs()

    # ---- State ------------------------------------------------------------------------

    @property
    def locked(self) -> bool:
        return self._locked

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        movable = not locked and self.is_content
        self.setFlag(QGraphicsItem.ItemIsMovable, movable)  # type: ignore[attr-defined]
        self.setFlag(QGraphicsItem.ItemIsSelectable, movable)  # type: ignore[attr-defined]

    def _notify_moved(self) -> None:
        parent = self.parentItem()  # type: ignore[attr-defined]
        if isinstance(parent, NodeItemMixin) and hasattr(parent, "children_changed"):
            parent.children_changed()  # type: ignore[attr-defined]
        if self._listener is not None:
            self._listener(self)  # type: ignore[arg-type]

    def paint_allowed(self, widget: Any) -> bool:
        """Per-view display filtering (a view can hide some nodes)."""
        view = view_for(widget)
        return view is None or view.display.shows(self.node_name)

    # ---- Painting ------------------------------------------------------------------
    #
    # Subclasses draw their content in ``paint_content`` (not ``paint``). The
    # content goes through the item's paint filters, which plugins install
    # (from a ``canvas.item_hooks`` contribution) to change how any node type
    # looks without knowing it — e.g. an effect stack rendering the content
    # offscreen and drawing a filtered copy. Child items (an image's
    # annotations) are separate items and never pass through the filters.
    # ``paint_decorations`` draws on top, unfiltered (text editing frame…).

    paint_filters: tuple[PaintFilter, ...] = ()

    def add_paint_filter(self, paint_filter: PaintFilter) -> Callable[[], None]:
        """Draw this item's content through ``paint_filter``; returns a remover.

        ``paint_filter(item, painter, option, widget, draw)`` paints instead of
        the content; ``draw(painter)`` paints the content (through the filters
        after this one) with any painter, e.g. one on an offscreen image.
        """
        self.paint_filters = (*self.paint_filters, paint_filter)
        self.update()  # type: ignore[attr-defined]

        def remove() -> None:
            if paint_filter in self.paint_filters:
                self.paint_filters = tuple(f for f in self.paint_filters if f is not paint_filter)
                self.update()  # type: ignore[attr-defined]

        return remove

    def paint(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        if not self.paint_allowed(widget):
            return
        if self.paint_filters:
            self._paint_filtered(0, painter, option, widget)
        else:
            self.paint_content(painter, option, widget)
        self.paint_decorations(painter, option, widget)

    def _paint_filtered(self, index: int, painter: QPainter, option: Any, widget: Any) -> None:
        filters = self.paint_filters
        if index >= len(filters):
            self.paint_content(painter, option, widget)
            return
        filters[index](self, painter, option, widget,
                       lambda target: self._paint_filtered(index + 1, target, option, widget))

    def paint_content(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        """Draw the node's own content in local coordinates (override this)."""

    def paint_decorations(self, painter: QPainter, option: Any, widget: Any = None) -> None:
        """Draw UI on top of the content that paint filters must not touch."""

    # Items whose content *is* a picture (images) can hand filters the original
    # pixels instead of a render at screen resolution: an effect then works on
    # every source pixel, independent of zoom, and its result can be cached by
    # ``key`` across sessions. Other items return None and get rendered.

    def effect_source(self) -> tuple[Any, Any] | None:
        """``(QPixmap, key)``: the content as source pixels (``key`` is stable for the
        same pixels, e.g. an asset hash plus the frame), or None."""
        return None

    def paint_source(self, painter: QPainter, pixmap: Any) -> None:
        """Draw ``pixmap`` (same size as ``effect_source``'s) the way the content is drawn."""
        self.paint_content(painter, None, None)


# paint_filter(item, painter, option, widget, draw) where draw(painter) paints the content
PaintFilter = Callable[[Any, QPainter, Any, Any, Callable[[QPainter], None]], None]


class ContainerItem(NodeItemMixin, QGraphicsItem):
    """Invisible parent (it has a transform, no content), e.g. a group that lets its members be picked."""

    is_content = False

    def __init__(self, node_name: str, node_type: str, listener: GeometryListener | None = None) -> None:
        super().__init__()
        self._init_node(node_name, node_type, listener)
        self.setFlag(QGraphicsItem.ItemHasNoContents, True)

    def boundingRect(self) -> QRectF:
        return QRectF()

    def content_rect(self) -> QRectF:
        return self.childrenBoundingRect()


class PlaceholderItem(NodeItemMixin, QGraphicsItem):
    """Stands in for nodes whose plugin isn't available (data is kept untouched)."""

    SIZE = QRectF(0.0, 0.0, 160.0, 90.0)

    def __init__(self, node_name: str, node_type: str, listener: GeometryListener | None = None) -> None:
        super().__init__()
        self._init_node(node_name, node_type, listener)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def boundingRect(self) -> QRectF:
        return QRectF(self.SIZE)

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        return super().itemChange(change, value)

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setPen(QPen(QColor("#6f7787"), 1.0, Qt.DashLine))
        painter.setBrush(QColor(30, 33, 40, 200))
        painter.drawRect(self.SIZE)
        painter.setPen(QColor("#aeb4bf"))
        painter.drawText(self.SIZE, Qt.AlignCenter | Qt.TextWordWrap,
                         f"{self.node_name}\nmissing plugin for '{self.node_type}'")
