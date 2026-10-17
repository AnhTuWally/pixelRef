"""Qt side of links: the link item, end snapping, waypoint handles and the Link tool.

A link item draws from its ends' *current* canvas geometry, so it follows
them live while they're dragged, scaled or animated (it re-routes whenever
the scene changes). Waypoints and free ends live in the link's own space.

Picking an end (Link tool, or dragging a selected link's end):

* at the middle of an element (the ring shown at its centre): attach to the whole
  element, meeting it on the side facing the path;
* near or on its edge: pin to that spot of the edge (``fixed`` anchor);
* anywhere else inside it: pin to that very spot of its content (``fixed``, a spot
  inside), so a link can point at a detail of an image;
* elsewhere, with snapping off, or holding Alt: a free end;
* elements inside groups are picked directly; Ctrl attaches to the group
  around the element instead (Ctrl+Shift: the outermost group), and pointing at
  empty space inside a group's frame picks the group.

The element an end will attach to is outlined with its name (and the groups
around it dashed), so it's always clear what a link connects. A selected
link labels its ends' elements "from" and "to".

Every link end has a handle: hover near one (the link needn't be selected) and
drag it. Selected link: drag a white square (waypoint) to move it, double-click it to
remove it; drag the line, or the small circle halfway along a segment, to add
one; drag an end to re-attach or free it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QGraphicsItem, QWidget

from pixelref.plugin_api import Attr
from pixelref.plugin_api.ui import HANDLED, InputHandler, NodeItemMixin, PointerEvent, Tool, paint, theme, view_for
from pixelref.plugin_api.ui import widgets as w

from . import CAPS, LINK, ROUTINGS

color_from = w.color_from
CAP_SPREAD = math.radians(26.0)
SNAP_RADIUS = 14.0      # viewport px: how close to an element an end outside it still snaps to it
EDGE_SNAP = 10.0        # viewport px: an end inside an element this close to its edge pins to the edge
CENTER_RADIUS = 16.0    # viewport px around an element's centre: attach to the whole element ("auto")
EDGE_EPSILON = 1e-3     # a spot this close to 0 or 1 is on the edge, not inside the content
SIDES = {"top": (0, 1), "right": (1, 2), "bottom": (2, 3), "left": (3, 0)}


# ---- Geometry ----------------------------------------------------------------------------------


def _unit(v: QPointF) -> QPointF:
    length = math.hypot(v.x(), v.y())
    return QPointF(v.x() / length, v.y() / length) if length > 1e-9 else QPointF(0.0, 0.0)


def _center(poly: list[QPointF]) -> QPointF:
    return QPointF(sum(p.x() for p in poly) / len(poly), sum(p.y() for p in poly) / len(poly))


def _rotate(v: QPointF, angle: float) -> QPointF:
    c, s = math.cos(angle), math.sin(angle)
    return QPointF(v.x() * c - v.y() * s, v.x() * s + v.y() * c)


def _distance(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def uv_of(poly: list[QPointF], point: QPointF) -> tuple[float, float]:
    """``point`` as fractions (u across, v down) of an element's corners (tl, tr, br, bl)."""
    tl, tr, _br, bl = poly
    e1, e2, d = tr - tl, bl - tl, point - tl
    det = e1.x() * e2.y() - e1.y() * e2.x()
    if abs(det) < 1e-12:
        return 0.5, 0.5
    return (d.x() * e2.y() - d.y() * e2.x()) / det, (e1.x() * d.y() - e1.y() * d.x()) / det


def at_uv(poly: list[QPointF], u: float, v: float) -> QPointF:
    tl, tr, _br, bl = poly
    return tl + (tr - tl) * u + (bl - tl) * v


def nearest_edge_spot(poly: list[QPointF], point: QPointF) -> tuple[float, float]:
    """The (u, v) of the point on the element's edge nearest ``point``."""
    u, v = uv_of(poly, point)
    cu, cv = min(1.0, max(0.0, u)), min(1.0, max(0.0, v))
    candidates = [(cu, 0.0), (cu, 1.0), (0.0, cv), (1.0, cv)]
    return min(candidates, key=lambda c: _distance(at_uv(poly, *c), point))


def _edge_point(poly: list[QPointF], center: QPointF, toward: QPointF) -> QPointF:
    """Where the ray from ``center`` toward ``toward`` leaves the polygon."""
    direction = _unit(toward - center)
    if direction.isNull():
        return QPointF(center)
    ray = QLineF(center, center + direction * 1e7)
    best, best_distance = None, None
    for i in range(len(poly)):
        kind, point = ray.intersects(QLineF(poly[i], poly[(i + 1) % len(poly)]))
        if kind == QLineF.BoundedIntersection:
            distance = _distance(point, center)
            if best_distance is None or distance > best_distance:
                best, best_distance = point, distance
    return best if best is not None else QPointF(center)


def spot_inside(spot: Any) -> bool:
    """A fixed spot inside an element's content (not on its edge)."""
    u, v = float(spot[0]), float(spot[1])
    return min(u, 1.0 - u, v, 1.0 - v) > EDGE_EPSILON


def anchor_point(end: Any, anchor: str, spot: Any, toward: QPointF, gap: float) -> tuple[QPointF, QPointF]:
    """Where a link meets an end (an element's corners, or a free point) and the
    outward direction there."""
    if isinstance(end, QPointF):
        return QPointF(end), _unit(toward - end)
    poly = end
    center = _center(poly)
    if anchor == "center":
        return center, _unit(toward - center)
    if anchor in SIDES:
        a, b = SIDES[anchor]
        point = (poly[a] + poly[b]) / 2.0
        normal = _unit(point - center)
    elif anchor == "fixed":
        u, v = (float(spot[0]), float(spot[1])) if spot else (0.5, 0.5)
        point = at_uv(poly, u, v)
        if spot_inside((u, v)):
            # A spot inside the content: the link reaches exactly there (no gap).
            return point, _unit(toward - point)
        # On the edge: leave perpendicular to that side (elbows and curves start cleanly).
        side = min((u, "left"), (1.0 - u, "right"), (v, "top"), (1.0 - v, "bottom"))[1]
        a, b = SIDES[side]
        normal = _unit((poly[a] + poly[b]) / 2.0 - center)
    else:
        point = _edge_point(poly, center, toward)
        normal = _unit(point - center)
    if normal.isNull():
        normal = _unit(toward - center)
    return point + normal * gap, normal


def _elbow(path: QPainterPath, a: QPointF, b: QPointF, h_start: bool, h_end: bool) -> None:
    if h_start and h_end:
        mx = (a.x() + b.x()) / 2.0
        path.lineTo(QPointF(mx, a.y()))
        path.lineTo(QPointF(mx, b.y()))
    elif not h_start and not h_end:
        my = (a.y() + b.y()) / 2.0
        path.lineTo(QPointF(a.x(), my))
        path.lineTo(QPointF(b.x(), my))
    elif h_start:
        path.lineTo(QPointF(b.x(), a.y()))
    else:
        path.lineTo(QPointF(a.x(), b.y()))
    path.lineTo(b)


def route_segments(points: list[QPointF], routing: str, n0: QPointF, n1: QPointF) -> list[QPainterPath]:
    """One path per stretch between consecutive points (start, waypoints…, end)."""
    segments = []
    last = len(points) - 2
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        path = QPainterPath(a)
        if routing == "elbow":
            delta = b - a
            dominant = abs(delta.x()) >= abs(delta.y())
            h_start = abs(n0.x()) >= abs(n0.y()) if i == 0 and not n0.isNull() else dominant
            h_end = abs(n1.x()) >= abs(n1.y()) if i == last and not n1.isNull() else dominant
            _elbow(path, a, b, h_start, h_end)
        elif routing == "curved":
            reach = _distance(a, b)
            # Catmull-Rom through the waypoints; the ends leave along the anchor's direction.
            c1 = a + n0 * max(reach * 0.4, 1.0) if i == 0 else a + (b - points[i - 1]) / 6.0
            c2 = b + n1 * max(reach * 0.4, 1.0) if i == last else b - (points[i + 2] - a) / 6.0
            path.cubicTo(c1, c2, b)
        else:
            path.lineTo(b)
        segments.append(path)
    return segments


def _direction(path: QPainterPath, at_end: bool) -> QPointF:
    """Unit direction of travel at the start or the end of a path."""
    angle = math.radians(path.angleAtPercent(1.0 if at_end else 0.0))
    return QPointF(math.cos(angle), -math.sin(angle))


def cap_shape(kind: str, tip: QPointF, outward: QPointF, size: float) -> tuple[QPainterPath, bool]:
    """The cap at ``tip`` pointing along ``outward``; True when it's filled."""
    path = QPainterPath()
    back = outward * -1.0
    perp = QPointF(-outward.y(), outward.x())
    if kind in ("arrow", "triangle"):
        left = tip + _rotate(back, CAP_SPREAD) * size
        right = tip + _rotate(back, -CAP_SPREAD) * size
        if kind == "arrow":
            path.moveTo(left)
            path.lineTo(tip)
            path.lineTo(right)
            return path, False
        path.addPolygon(QPolygonF([tip, left, right, tip]))
        return path, True
    if kind == "circle":
        radius = size * 0.4
        path.addEllipse(tip + back * radius, radius, radius)
        return path, True
    if kind == "diamond":
        half = size * 0.5
        path.addPolygon(QPolygonF([tip, tip + back * half + perp * half * 0.85, tip + back * size,
                                   tip + back * half - perp * half * 0.85, tip]))
        return path, True
    if kind == "bar":
        path.moveTo(tip + perp * size * 0.45)
        path.lineTo(tip - perp * size * 0.45)
        return path, False
    return path, False


# ---- Choosing what an end attaches to -------------------------------------------------------------


@dataclass
class EndChoice:
    """What an end being placed attaches to (``name`` None: a free end)."""

    name: str | None
    point: QPointF               # scene point to show the end at
    anchor: str = "auto"
    spot: tuple[float, float] | None = None
    chain: list[str] = field(default_factory=list)  # the element, then the groups around it
    level: int = 0                                   # which of ``chain`` was picked
    refused: str = ""                                # why a hovered element can't be used


def _element_of(item: Any) -> Any:
    """The link target a hit item belongs to: itself, or the pen drawing (an
    indivisible compound) it's a stroke of. Groups are not crossed."""
    node = item
    while node is not None and not isinstance(node, NodeItemMixin):
        node = node.parentItem()
    if node is None:
        return None
    target, parent = node, node.parentItem()
    while parent is not None:
        if (isinstance(parent, NodeItemMixin) and parent.captures_children
                and not getattr(parent, "opens_for_selection", False)):
            target = parent
        parent = parent.parentItem()
    return target


def _group_chain(item: Any) -> list[Any]:
    chain, parent = [item], item.parentItem()
    while parent is not None:
        if isinstance(parent, NodeItemMixin) and getattr(parent, "opens_for_selection", False):
            chain.append(parent)
        parent = parent.parentItem()
    return chain


def _usable(view: Any, item: Any) -> bool:
    return (item is not None and isinstance(item, NodeItemMixin) and item.is_content and item.isVisible()
            and not isinstance(item, LinkItem) and view.display.shows(item.node_name))


def _segment_distance(p: QPointF, a: QPointF, b: QPointF) -> float:
    ab = b - a
    length2 = ab.x() ** 2 + ab.y() ** 2
    t = 0.0 if length2 < 1e-12 else max(0.0, min(1.0, ((p - a).x() * ab.x() + (p - a).y() * ab.y()) / length2))
    return _distance(p, a + ab * t)


def choose_end(canvas: Any, view: Any, scene_pos: QPointF, modifiers: Any, snap: bool = True,
               exclude: str | None = None) -> EndChoice:
    """What an end placed at ``scene_pos`` attaches to (see the module notes)."""
    free = EndChoice(None, QPointF(scene_pos))
    if not snap or modifiers & Qt.AltModifier:
        return free
    vp = view.to_viewport(scene_pos)
    reach = QRectF(vp.x() - SNAP_RADIUS, vp.y() - SNAP_RADIUS, 2 * SNAP_RADIUS, 2 * SNAP_RADIUS).toRect()
    inside: list[Any] = []
    near: list[tuple[float, Any]] = []
    seen: set[str] = set()
    for hit in view.items(reach):  # top-most first
        element = _element_of(hit)
        if not _usable(view, element) or getattr(element, "opens_for_selection", False):
            continue
        if element.node_name in seen:
            continue
        seen.add(element.node_name)
        poly = QPolygonF([view.to_viewport(p) for p in element.scene_polygon()])
        if poly.containsPoint(vp, Qt.OddEvenFill):
            inside.append(element)
        else:
            distance = min(_segment_distance(vp, poly[i], poly[(i + 1) % 4]) for i in range(4))
            if distance <= SNAP_RADIUS:
                near.append((distance, element))
    element = inside[0] if inside else (min(near, key=lambda e: e[0])[1] if near else None)
    if element is None:
        # Empty space inside a group's frame picks the group (the innermost one).
        frames = []
        for item in canvas.items.values():
            if getattr(item, "opens_for_selection", False) and _usable(view, item):
                poly = QPolygonF([view.to_viewport(p) for p in item.scene_polygon()])
                if poly.containsPoint(vp, Qt.OddEvenFill):
                    frames.append((len(_group_chain(item)), item))
        if not frames:
            return free
        element = max(frames, key=lambda f: f[0])[1]
    chain = _group_chain(element)
    level = 0
    if modifiers & Qt.ControlModifier and len(chain) > 1:
        level = len(chain) - 1 if modifiers & Qt.ShiftModifier else 1
    chosen = chain[level]
    names = [item.node_name for item in chain]
    if chosen.node_name == exclude:
        return EndChoice(None, QPointF(scene_pos), chain=names, level=level,
                         refused="Both ends can't attach to the same element")
    poly = list(chosen.scene_polygon())
    u, v = uv_of(poly, scene_pos)
    is_inside = 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0
    poly_vp = [view.to_viewport(p) for p in poly]
    to_edge = min(_segment_distance(vp, poly_vp[i], poly_vp[(i + 1) % 4]) for i in range(4))
    near_center = _distance(vp, view.to_viewport(_center(poly))) <= CENTER_RADIUS
    if level > 0 or (is_inside and near_center):  # the centre, or a group picked with Ctrl: the whole element
        return EndChoice(chosen.node_name, _center(poly), "auto", None, names, level)
    if not is_inside or to_edge <= EDGE_SNAP:
        spot = nearest_edge_spot(poly, scene_pos)
    else:  # inside the content: pin the end to this very spot
        spot = (u, v)
    spot = (round(spot[0], 4), round(spot[1], 4))
    return EndChoice(chosen.node_name, at_uv(poly, *spot), "fixed", spot, names, level)


# ---- The item ------------------------------------------------------------------------------------


class LinkItem(NodeItemMixin, QGraphicsItem):
    MIN_HIT_WIDTH = 12.0

    def __init__(self, node_name: str, resolve: Callable[[str], Any], id_of: Callable[[str], str | None],
                 item_of: Callable[[str], Any]) -> None:
        super().__init__()
        self._init_node(node_name, LINK, None)
        self._resolve = resolve  # node id -> canvas item (or None)
        self._id_of = id_of      # node name -> node id
        self._item_of = item_of  # node name -> canvas item
        self._attrs: dict[str, Any] = {}
        self._waypoints: list[QPointF] = []
        self._drag_end: tuple[str, EndChoice] | None = None
        self._route_key: Any = None
        self._points: list[QPointF] = []
        self._segments: list[QPainterPath] = []
        self._path = QPainterPath()
        self._caps: list[tuple[QPainterPath, bool]] = []
        self._label_rect = QRectF()
        self._ends: tuple[str | None, str | None] = (None, None)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    # ---- Attributes --------------------------------------------------------------------

    def apply_attrs(self, attrs: dict[str, Any]) -> None:
        self._attrs = dict(attrs)
        self._waypoints = [QPointF(float(p[0]), float(p[1])) for p in attrs.get("waypoints") or []]
        self._drag_end = None
        self.update_route(force=True)

    def commit_values(self) -> dict[str, Any]:
        values = self.transform_attrs()
        values["waypoints"] = [[round(p.x(), 3), round(p.y(), 3)] for p in self._waypoints]
        if self._drag_end is not None:
            end, choice = self._drag_end
            node_id = self._id_of(choice.name) if choice.name else None
            values[end] = node_id or ""
            if node_id:
                values[f"{end}_anchor"] = choice.anchor
                if choice.spot is not None:
                    values[f"{end}_spot"] = [choice.spot[0], choice.spot[1]]
            else:
                local = self.sceneTransform().inverted()[0].map(choice.point)
                values[f"{end}_point"] = [round(local.x(), 3), round(local.y(), 3)]
            self._attrs.update({k: v for k, v in values.items() if k.startswith(end)})
            self._drag_end = None
            self.update_route(force=True)
        self._attrs["waypoints"] = values["waypoints"]  # the item isn't rebuilt after a canvas edit
        return values

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        self.setFlag(QGraphicsItem.ItemIsMovable, False)  # dragging a link bends it instead
        self.setFlag(QGraphicsItem.ItemIsSelectable, not locked)

    def set_local_transform(self, t: Any) -> None:
        super().set_local_transform(t)
        if self._drag_end is not None:  # a cancelled drag (Esc): back to the stored ends
            self._drag_end = None
            self.update_route(force=True)

    # ---- Editing (gizmo handles) -------------------------------------------------------

    @property
    def waypoints(self) -> list[QPointF]:
        return [QPointF(p) for p in self._waypoints]

    def route_points(self) -> list[QPointF]:
        return [QPointF(p) for p in self._points]

    def segments(self) -> list[QPainterPath]:
        return list(self._segments)

    def set_waypoint(self, index: int, point: QPointF) -> None:
        self._waypoints[index] = QPointF(point)
        self.update_route()
        self._notify_moved()

    def insert_waypoint(self, index: int, point: QPointF) -> int:
        index = max(0, min(index, len(self._waypoints)))
        self._waypoints.insert(index, QPointF(point))
        self.update_route()
        self._notify_moved()
        return index

    def set_drag_end(self, end: str, choice: EndChoice) -> None:
        self._drag_end = (end, choice)
        self.update_route()
        self._notify_moved()

    def end_names(self) -> tuple[str | None, str | None]:
        """Names of the elements the ends are attached to (None: free or missing)."""
        return self._ends

    def is_free(self, end: str) -> bool:
        if self._drag_end is not None and self._drag_end[0] == end:
            return self._drag_end[1].name is None
        return not self._attrs.get(end)

    # ---- Routing ------------------------------------------------------------------------

    def _end(self, end: str) -> tuple[Any, str | None, str, Any]:
        """(element corners in link space or a free point, element name, anchor, spot)."""
        inverse, ok = self.sceneTransform().inverted()
        if not ok:
            return None, None, "auto", None
        if self._drag_end is not None and self._drag_end[0] == end:
            choice = self._drag_end[1]
            if choice.name is None:
                return inverse.map(choice.point), None, "auto", None
            item, anchor, spot = self._item_of(choice.name), choice.anchor, choice.spot
        else:
            ref = str(self._attrs.get(end) or "")
            anchor = str(self._attrs.get(f"{end}_anchor", "auto"))
            spot = self._attrs.get(f"{end}_spot")
            if not ref:
                point = self._attrs.get(f"{end}_point") or [0.0, 0.0]
                return QPointF(float(point[0]), float(point[1])), None, anchor, spot
            item = self._resolve(ref)
        if item is None or not item.isVisible():
            return None, None, anchor, spot
        try:
            polygon = [inverse.map(p) for p in item.scene_polygon()]
        except RuntimeError:
            return None, None, anchor, spot
        return polygon, item.node_name, anchor, spot

    def update_route(self, force: bool = False) -> bool:
        """Re-route if an end, a waypoint or the style changed. Returns whether it did."""
        source = self._end("source")
        target = self._end("target")

        def key_of(end: Any) -> Any:
            geometry = end[0]
            if geometry is None:
                return None
            if isinstance(geometry, QPointF):
                shape: Any = (round(geometry.x(), 3), round(geometry.y(), 3))
            else:
                shape = tuple((round(p.x(), 3), round(p.y(), 3)) for p in geometry)
            return shape, end[1], end[2], tuple(end[3]) if end[3] else None

        key = (key_of(source), key_of(target), tuple((p.x(), p.y()) for p in self._waypoints),
               tuple(sorted((k, repr(v)) for k, v in self._attrs.items() if k != "waypoints")))
        if not force and key == self._route_key:
            return False
        self._route_key = key
        self.prepareGeometryChange()
        self._ends = (source[1], target[1])
        self._build(source, target)
        self.update()
        return True

    def _build(self, source: Any, target: Any) -> None:
        self._points, self._segments, self._caps = [], [], []
        self._path = QPainterPath()
        self._label_rect = QRectF()
        if source[0] is None or target[0] is None:
            return
        a = self._attrs
        gap = float(a.get("gap", 6.0) or 0.0)

        def center_of(end: Any) -> QPointF:
            return end[0] if isinstance(end[0], QPointF) else _center(end[0])

        first = self._waypoints[0] if self._waypoints else center_of(target)
        start, n0 = anchor_point(source[0], source[2], source[3], first, gap)
        last = self._waypoints[-1] if self._waypoints else center_of(source)
        end, n1 = anchor_point(target[0], target[2], target[3], last, gap)
        self._points = [start, *self._waypoints, end]
        self._segments = route_segments(self._points, str(a.get("routing", "curved")), n0, n1)
        path = QPainterPath(start)
        for segment in self._segments:
            path.connectPath(segment)
        self._path = path
        size = self.cap_size()
        if self._segments and path.length() > 1e-6:
            for kind, tip, outward in (
                (str(a.get("start_cap", "none")), start, _direction(self._segments[0], False) * -1.0),
                (str(a.get("end_cap", "triangle")), end, _direction(self._segments[-1], True)),
            ):
                if kind != "none" and not outward.isNull():
                    self._caps.append(cap_shape(kind, tip, outward, size))
        label = str(a.get("label", "") or "")
        if label:
            metrics = QFontMetricsF(self._font())
            center = path.pointAtPercent(0.5)
            width, height = metrics.horizontalAdvance(label) + 12.0, metrics.height() + 4.0
            self._label_rect = QRectF(center.x() - width / 2.0, center.y() - height / 2.0, width, height)

    @property
    def thickness(self) -> float:
        return max(0.5, float(self._attrs.get("thickness", 2.0) or 2.0))

    def cap_size(self) -> float:
        return max(11.0, self.thickness * 4.5) * float(self._attrs.get("cap_size", 1.0) or 1.0)

    def _font(self) -> QFont:
        font = QFont()
        font.setPixelSize(max(1, int(round(float(self._attrs.get("label_size", 14.0) or 14.0)))))
        return font

    # ---- Geometry for the canvas ------------------------------------------------------

    def content_rect(self) -> QRectF:
        rect = self._path.boundingRect().united(self._label_rect)
        return rect if rect.isValid() else QRectF(0.0, 0.0, 1.0, 1.0)

    def boundingRect(self) -> QRectF:
        pad = max(self.thickness, self.MIN_HIT_WIDTH) + self.cap_size()
        return self.content_rect().adjusted(-pad, -pad, pad, pad)

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(max(self.thickness, self.MIN_HIT_WIDTH))
        stroker.setCapStyle(Qt.RoundCap)
        stroker.setJoinStyle(Qt.RoundJoin)
        shape = stroker.createStroke(self._path)
        for cap, _filled in self._caps:
            shape.addPath(stroker.createStroke(cap))
        if self._label_rect.isValid():
            shape.addRect(self._label_rect)
        return shape

    def hits_line(self, local: QPointF) -> bool:
        return self.shape().contains(local)

    def outline_path(self) -> QPainterPath | None:
        """Selection outline: the path itself, not a box (scene coordinates)."""
        if self._path.isEmpty():
            return None
        return self.sceneTransform().map(self._path)

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        return super().itemChange(change, value)

    # ---- Painting ----------------------------------------------------------------------

    def paint_allowed(self, widget: Any) -> bool:
        if not super().paint_allowed(widget) or self._path.isEmpty():
            return False
        view = view_for(widget)
        # An end filtered out of this view takes the link with it.
        return view is None or all(view.display.shows(n) for n in self._ends if n)

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        a = self._attrs
        color = color_from(a.get("color", [110, 190, 255]))
        thickness = self.thickness
        filled = [cap for cap, is_filled in self._caps if is_filled]
        painter.save()
        if filled:  # the shaft stops under filled caps, so thick links keep sharp tips
            clip = QPainterPath()
            clip.addRect(self.boundingRect())
            for cap in filled:
                clip = clip.subtracted(cap)
            painter.setClipPath(clip)
        painter.setPen(QPen(color, thickness, paint.PEN_STYLES.get(str(a.get("stroke_style", "solid")), Qt.SolidLine),
                            Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(self._path)
        painter.restore()
        for cap, is_filled in self._caps:
            painter.setPen(QPen(color, thickness if not is_filled else max(1.0, thickness * 0.5),
                                Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(color if is_filled else Qt.NoBrush)
            painter.drawPath(cap)
        label = str(a.get("label", "") or "")
        if label and self._label_rect.isValid():
            painter.setPen(QPen(color, 1.0))
            painter.setBrush(QColor(24, 26, 32, 225))
            radius = self._label_rect.height() / 2.0
            painter.drawRoundedRect(self._label_rect, radius, radius)
            painter.setFont(self._font())
            painter.setPen(QColor("#e8edf5"))
            painter.drawText(self._label_rect, Qt.AlignCenter, label)


# ---- Feedback: what an end attaches to ---------------------------------------------------------------


class LinkFeedback:
    """Paints the element (and groups) an end being placed would attach to, and the
    'from' / 'to' elements of selected links."""

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.snap = True              # the Link tool's "Snap to elements" option
        self.choice: EndChoice | None = None
        self.choice_view: Any = None
        self.owner = ""               # "tool" or "gizmo": who placed the current choice
        self.cursor = QPointF()

    def show(self, owner: str, view: Any, choice: EndChoice | None, cursor_scene: QPointF | None = None) -> None:
        self.owner, self.choice_view, self.choice = owner, view, choice
        self.cursor = QPointF(cursor_scene) if cursor_scene is not None else (choice.point if choice else QPointF())
        if view is not None:
            view.viewport().update()

    def clear(self, owner: str | None = None) -> None:
        if owner is None or self.owner == owner:
            view = self.choice_view
            self.choice, self.choice_view, self.owner = None, None, ""
            if view is not None:
                try:
                    view.viewport().update()
                except RuntimeError:
                    pass

    def _active(self) -> bool:
        if self.choice is None:
            return False
        if self.owner == "gizmo":
            op = self.ui.service("gizmo").op
            return bool(op and op.get("kind") == "link_end")
        tool = self.ui.tools.active
        return tool is not None and tool.id == "link"

    # ---- Painting helpers -------------------------------------------------------------------

    @staticmethod
    def _font() -> QFont:
        font = QFont()
        font.setPixelSize(11)
        return font

    def _polygon(self, view: Any, name: str) -> QPolygonF | None:
        item = self.ui.canvas.item(name)
        if item is None or not item.isVisible():
            return None
        try:
            return QPolygonF([view.to_viewport(p) for p in item.scene_polygon()])
        except RuntimeError:
            return None

    def breadcrumb(self, name: str) -> str:
        """``Group › Image`` for an element inside groups."""
        nodes = self.ui.canvas.nodes
        parts, parent = [name], nodes.get(name, {}).get("parent")
        while parent:
            if nodes.get(parent, {}).get("type") == "group":
                parts.append(parent)
            parent = nodes.get(parent, {}).get("parent")
        return " › ".join(reversed(parts))

    def _tag(self, painter: QPainter, anchor: QPointF, text: str, strong: bool) -> None:
        font = self._font()
        metrics = QFontMetricsF(font)
        box = QRectF(0, 0, metrics.horizontalAdvance(text) + 12, metrics.height() + 4)
        box.moveBottomLeft(anchor + QPointF(0, -4))
        painter.setPen(QPen(theme.ACCENT if strong else QColor(255, 255, 255, 60), 1.0))
        painter.setBrush(QColor(20, 24, 32, 235))
        painter.drawRoundedRect(box, 4, 4)
        painter.setFont(font)
        painter.setPen(QColor("#ffffff") if strong else QColor(theme.TEXT_LIGHT))
        painter.drawText(box, Qt.AlignCenter, text)

    def _outline(self, painter: QPainter, polygon: QPolygonF, strong: bool, color: QColor | None = None) -> None:
        pen = QPen(color or theme.ACCENT, 2.0 if strong else 1.0, Qt.SolidLine if strong else Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor(61, 142, 240, 28) if strong else Qt.NoBrush)
        painter.drawPolygon(polygon)

    @staticmethod
    def _top_left(polygon: QPolygonF) -> QPointF:
        rect = polygon.boundingRect()
        return QPointF(rect.left(), rect.top())

    # ---- Overlay ------------------------------------------------------------------------------

    def paint(self, view: Any, painter: QPainter) -> None:
        if not view.display.show_overlays:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            if self._active() and self.choice_view is view:
                self._paint_choice(view, painter, self.choice)
            else:
                self._paint_selected_links(view, painter)
        finally:
            painter.restore()

    def _paint_choice(self, view: Any, painter: QPainter, choice: EndChoice) -> None:
        # Groups around the element: dashed, named; the element picked: solid.
        for name in reversed(choice.chain):
            polygon = self._polygon(view, name)
            if polygon is None:
                continue
            picked = choice.name == name
            self._outline(painter, polygon, picked)
            if not picked:
                self._tag(painter, self._top_left(polygon), name, False)
        if choice.name:
            polygon = self._polygon(view, choice.name)
            if polygon is not None:
                self._tag(painter, self._top_left(polygon), "→ " + self.breadcrumb(choice.name), True)
        point = view.to_viewport(choice.point)
        if choice.name and choice.anchor != "auto":
            # Where to aim for the whole element: a faint ring at its centre.
            polygon = self._polygon(view, choice.name)
            if polygon is not None:
                painter.setPen(QPen(QColor(61, 142, 240, 150), 1.2, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(_center(list(polygon)), CENTER_RADIUS, CENTER_RADIUS)
        if choice.name and choice.anchor == "fixed" and choice.spot and spot_inside(choice.spot):
            painter.setPen(QPen(QColor("#ffffff"), 1.5))
            painter.setBrush(theme.ACCENT)
            painter.drawEllipse(point, 5.0, 5.0)
            painter.setPen(QPen(theme.ACCENT, 1.2))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                painter.drawLine(point + QPointF(dx * 8, dy * 8), point + QPointF(dx * 13, dy * 13))
        elif choice.name and choice.anchor == "auto":
            painter.setPen(QPen(theme.ACCENT, 2.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(point, 9.0, 9.0)
            painter.setBrush(theme.ACCENT)
            painter.drawEllipse(point, 2.5, 2.5)
        elif choice.name:
            painter.setPen(QPen(QColor("#ffffff"), 1.5))
            painter.setBrush(theme.ACCENT)
            painter.drawEllipse(point, 5.5, 5.5)
        else:
            painter.setPen(QPen(theme.ACCENT, 1.5))
            painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(view.to_viewport(self.cursor), 4.5, 4.5)
        # What the modifiers would do.
        hints = []
        if choice.refused:
            hints.append(choice.refused)
        elif choice.name and choice.anchor == "auto":
            hints.append("attach to the whole element · meets the side facing the link")
        elif choice.name and choice.spot and spot_inside(choice.spot):
            hints.append("pin inside the content here · centre ring: whole element · edge: snaps to it")
        elif choice.name:
            hints.append("pin to the edge here · centre ring: whole element")
        if len(choice.chain) > 1:
            if choice.level == 0:
                hints.append(f"Ctrl: the group ‘{choice.chain[1]}’")
            else:
                hints.append(f"release Ctrl: ‘{choice.chain[0]}’")
        hints.append("Alt: free end" if choice.name or choice.chain else "free end · snaps to elements nearby")
        self._hint(painter, view.to_viewport(self.cursor) + QPointF(16, 18), "  ·  ".join(hints))

    def _hint(self, painter: QPainter, at: QPointF, text: str) -> None:
        font = self._font()
        metrics = QFontMetricsF(font)
        box = QRectF(at.x(), at.y(), metrics.horizontalAdvance(text) + 12, metrics.height() + 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 24, 32, 215))
        painter.drawRoundedRect(box, 4, 4)
        painter.setFont(font)
        painter.setPen(QColor(theme.TEXT_LIGHT))
        painter.drawText(box, Qt.AlignCenter, text)

    def _paint_selected_links(self, view: Any, painter: QPainter) -> None:
        canvas = self.ui.canvas
        tool = self.ui.tools.active
        if tool is not None and not tool.show_selection:
            return
        links = [canvas.item(n) for n in self.ui.session.selection.names]
        links = [item for item in links if isinstance(item, LinkItem)]
        if len(links) > 3:  # a big selection would be all labels
            return
        for item in links:
            source, target = item.end_names()
            for label, name in (("from", source), ("to", target)):
                if not name or canvas.item(name) is None:
                    continue
                polygon = self._polygon(view, name)
                if polygon is None:
                    continue
                for group in _group_chain(canvas.item(name))[1:]:
                    outer = self._polygon(view, group.node_name)
                    if outer is not None:
                        self._outline(painter, outer, False, QColor(61, 142, 240, 150))
                self._outline(painter, polygon, False)
                self._tag(painter, self._top_left(polygon), f"{label}: {self.breadcrumb(name)}", True)


# ---- Gizmo handles --------------------------------------------------------------------------------


class LinkHandles:
    """Waypoints, segment midpoints and the two ends of a selected link."""

    node_type = LINK
    labels = {"link_point": "Move waypoint", "link_bend": "Bend link", "link_end": "Reconnect link"}

    def __init__(self, ui: Any, feedback: LinkFeedback) -> None:
        self.ui = ui
        self.feedback = feedback

    def build(self, gizmo: Any, view: Any, item: Any, g: dict[str, Any]) -> dict[str, Any]:
        st = item.sceneTransform()
        handles: dict[str, QPointF] = {}
        points = item.route_points()
        if points:
            handles["src"] = view.to_viewport(st.map(points[0]))
            handles["dst"] = view.to_viewport(st.map(points[-1]))
            for index, point in enumerate(item.waypoints):
                handles[f"wp:{index}"] = view.to_viewport(st.map(point))
            for index, segment in enumerate(item.segments()):
                handles[f"mid:{index}"] = view.to_viewport(st.map(segment.pointAtPercent(0.5)))
        rect = item.content_rect()
        corners_scene = [st.map(p) for p in (rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft())]
        return {
            "line": True,
            "targets": [item],
            "single": True,
            "corners_scene": corners_scene,
            "corners_vp": [view.to_viewport(p) for p in corners_scene],
            "center_scene": st.map(rect.center()),
            "handles_vp": handles,
        }

    def hit(self, gizmo: Any, view: Any, g: dict[str, Any], pos: QPointF) -> str:
        reach = gizmo.HANDLE_HIT + 2.0
        handles = g["handles_vp"]
        for prefix in ("wp:", "src", "dst", "mid:"):
            for name, point in handles.items():
                if name.startswith(prefix) and _distance(pos, point) <= reach:
                    return name
        item = g["targets"][0]
        local = item.sceneTransform().inverted()[0].map(view.to_scene(pos))
        if item.hits_line(local):
            return f"seg:{self._nearest_segment(item, local)}"
        return ""

    @staticmethod
    def _nearest_segment(item: Any, local: QPointF) -> int:
        best, best_distance = 0, None
        for index, segment in enumerate(item.segments()):
            for step in range(0, 33):
                distance = _distance(segment.pointAtPercent(step / 32.0), local)
                if best_distance is None or distance < best_distance:
                    best, best_distance = index, distance
        return best

    def start(self, gizmo: Any, handle: str, g: dict[str, Any], item: Any) -> str:
        if handle.startswith("wp:"):
            return "link_point"
        if handle in ("src", "dst"):
            return "link_end"
        return "link_bend"

    def cursor(self, gizmo: Any, handle: str, g: dict[str, Any], view: Any) -> Any:
        if handle.startswith("wp:"):
            return Qt.SizeAllCursor
        if handle in ("src", "dst"):
            return Qt.PointingHandCursor
        return Qt.CrossCursor

    def drag(self, gizmo: Any, op: dict[str, Any], m: QPointF, modifiers: Any, view: Any) -> None:
        item = op["targets"][0][0]
        handle = op["handle"]
        if handle in ("src", "dst"):
            end = "source" if handle == "src" else "target"
            other = item.end_names()[1 if handle == "src" else 0]
            choice = choose_end(self.ui.canvas, view, m, modifiers, self.feedback.snap, exclude=other)
            item.set_drag_end(end, choice)
            self.feedback.show("gizmo", view, choice, m)
            return
        local = item.sceneTransform().inverted()[0].map(m)
        if handle.startswith("wp:"):
            index = int(handle.split(":")[1])
        else:
            if "wp_index" not in op:
                op["wp_index"] = item.insert_waypoint(int(handle.split(":")[1]), local)
            index = op["wp_index"]
        if modifiers & Qt.ShiftModifier:  # line up with a neighbour
            points = item.route_points()
            neighbours = [points[index], points[index + 2]] if len(points) > index + 2 else []
            snap = 12.0 / max(view.zoom_level(), 1e-6)
            for neighbour in neighbours:
                if abs(local.x() - neighbour.x()) <= snap:
                    local.setX(neighbour.x())
                if abs(local.y() - neighbour.y()) <= snap:
                    local.setY(neighbour.y())
        item.set_waypoint(index, local)
        self.ui.status("Waypoint · Shift lines it up · double-click removes it")

    def double_click(self, gizmo: Any, view: Any, g: dict[str, Any], handle: str) -> bool:
        if handle.startswith("wp:"):
            item = g["targets"][0]
            self.ui.app.run_command("links.remove_waypoint", item.node_name, int(handle.split(":")[1]))
            return True
        return False

    def paint(self, gizmo: Any, view: Any, painter: QPainter, g: dict[str, Any]) -> bool:
        painter.setRenderHint(QPainter.Antialiasing, True)
        item = g["targets"][0]
        for name, point in g["handles_vp"].items():
            if name.startswith("mid:"):
                painter.setPen(QPen(theme.ACCENT, 1.2))
                painter.setBrush(QColor(255, 255, 255, 150))
                painter.drawEllipse(point, 3.5, 3.5)
            elif name.startswith("wp:"):
                painter.setPen(QPen(theme.ACCENT, 1.5))
                painter.setBrush(QColor("#ffffff"))
                painter.drawRect(QRectF(point.x() - 4.5, point.y() - 4.5, 9.0, 9.0))
            else:  # an end: solid when attached, hollow when free
                free = item.is_free("source" if name == "src" else "target")
                painter.setPen(QPen(theme.ACCENT if free else QColor("#ffffff"), 1.8))
                painter.setBrush(QColor("#ffffff") if free else theme.ACCENT)
                painter.drawEllipse(point, 5.5, 5.5)
        return True


# ---- Grabbing any link's end -------------------------------------------------------------------------


class EndGrabber(InputHandler):
    """Every link end has a handle: hovering near the end of a link shows it (the
    link needn't be selected), and dragging it re-attaches or frees that end."""

    REACH = 9.0  # viewport px

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.hovered: tuple[Any, Any, str] | None = None  # (view, link item, "src" / "dst")
        self.dragging = False

    def _enabled(self) -> bool:
        tool = self.ui.tools.active
        return tool is not None and bool(tool.gizmo) and not tool.busy

    def end_at(self, view: Any, pos: QPointF) -> tuple[Any, str] | None:
        """The unselected link end nearest ``pos`` (selected links have the gizmo's handles)."""
        best = None
        for item in list(self.ui.canvas.items.values()):
            if (not isinstance(item, LinkItem) or item.isSelected() or item.locked or not item.isVisible()
                    or not view.display.shows(item.node_name)):
                continue
            points = item.route_points()
            if not points:
                continue
            transform = item.sceneTransform()
            for handle, point in (("src", points[0]), ("dst", points[-1])):
                distance = _distance(view.to_viewport(transform.map(point)), pos)
                if distance <= self.REACH and (best is None or distance < best[0]):
                    best = (distance, item, handle)
        return (best[1], best[2]) if best else None

    def pointer(self, e: PointerEvent) -> str | None:
        gizmo = self.ui.service("gizmo")
        if e.kind == "press":
            if e.button != Qt.LeftButton or not self._enabled():
                return None
            found = self.end_at(e.view, e.pos)
            if found is None:
                return None
            item, handle = found
            self.ui.app.run_command("select", [item.node_name])
            g = gizmo.gizmo(e.view)
            if g is None or g.get("targets", [None])[0] is not item:
                return None
            gizmo.start(e.view, handle, g, e.scene_pos)
            self.dragging = True
            self._set_hovered(None)
            return HANDLED
        if not self.dragging:
            return None
        if e.kind == "move":
            gizmo.drag(e.view.to_scene(e.pos), e.modifiers, e.view)
            return HANDLED
        if e.kind == "release":
            self.dragging = False
            gizmo.finish()
            return HANDLED
        return None

    def hover(self, e: PointerEvent) -> None:
        found = self.end_at(e.view, e.pos) if self._enabled() else None
        self._set_hovered((e.view, *found) if found else None)
        if found:
            e.set_cursor(Qt.PointingHandCursor)
            self.ui.status("Drag the link's end to re-attach it (or free it)")

    def leave(self, view: Any) -> None:
        if self.hovered is not None and self.hovered[0] is view:
            self._set_hovered(None)

    def _set_hovered(self, hovered: tuple[Any, Any, str] | None) -> None:
        if hovered == self.hovered:
            return
        for state in (self.hovered, hovered):
            if state is not None:
                try:
                    state[0].viewport().update()
                except RuntimeError:
                    pass
        self.hovered = hovered

    def paint(self, view: Any, painter: QPainter) -> None:
        if self.hovered is None or self.hovered[0] is not view or not view.display.show_overlays:
            return
        _view, item, handle = self.hovered
        try:
            points = item.route_points()
            if not points:
                return
            point = view.to_viewport(item.sceneTransform().map(points[0] if handle == "src" else points[-1]))
            outline = item.outline_path()
        except RuntimeError:
            self.hovered = None
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        if outline is not None:
            painter.setPen(QPen(QColor(61, 142, 240, 140), 1.0, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(view.viewportTransform().map(outline))
        free = item.is_free("source" if handle == "src" else "target")
        painter.setPen(QPen(theme.ACCENT if free else QColor("#ffffff"), 1.8))
        painter.setBrush(QColor("#ffffff") if free else theme.ACCENT)
        painter.drawEllipse(point, 6.0, 6.0)
        painter.restore()


# ---- The Link tool ----------------------------------------------------------------------------------


class LinkTool(Tool):
    id = "link"
    title = "Link"
    action_text = "Link Tool"
    icon = "link"
    tooltip = "Link elements (W) · drag from one element (or anywhere) to another"
    keywords = "connect connector relation edge wire line between"
    shortcuts = ("W",)
    order = 22.0
    cursor = Qt.CrossCursor
    gizmo = "handles"
    uses_brush_color = True
    color_option = "color"
    width_option = "thickness"
    creates = LINK
    option_specs = (
        Attr("color", "color", [255, 64, 64], label="Color"),
        Attr("thickness", "float", 4.0, minimum=0.5, maximum=12.0, soft_min=0.5, soft_max=12.0, step=0.5,
             precision=1, unit="px", label="Width"),
        Attr("routing", "enum", "curved", choices=ROUTINGS, label="Path"),
        Attr("start_cap", "enum", "none", choices=CAPS, label="Start"),
        Attr("end_cap", "enum", "triangle", choices=CAPS, label="End"),
    )
    options_title = "Link options"
    hint = ("Drag between elements to link them (Ctrl: the group around one · Alt: free end) · "
            "drag a selected link to bend it · Esc / V to finish")

    def __init__(self, ui: Any, feedback: LinkFeedback) -> None:
        super().__init__(ui)
        self.feedback = feedback
        self.op: dict[str, Any] | None = None

    @property
    def routing(self) -> str:
        return str(self.options.get("routing"))

    @property
    def start_cap(self) -> str:
        return str(self.options.get("start_cap"))

    @property
    def end_cap(self) -> str:
        return str(self.options.get("end_cap"))

    @property
    def busy(self) -> bool:
        return self.op is not None

    def cancel(self) -> bool:
        if self.op is not None:
            self.op = None
            self.feedback.clear("tool")
            return True
        return False

    def deactivate(self) -> None:
        self.op = None
        self.feedback.clear("tool")

    def leave(self, view: Any) -> None:
        if self.op is None:
            self.feedback.clear("tool")

    def banner(self, view: Any) -> str | None:
        return "⤳  Link · drag between elements · Ctrl: group · Alt: free end   —   Esc / V to finish"

    def _choose(self, e: PointerEvent, exclude: str | None = None) -> EndChoice:
        return choose_end(self.ui.canvas, e.view, e.view.to_scene(e.pos), e.modifiers, self.feedback.snap, exclude)

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind == "press" and e.button == Qt.LeftButton:
            hit = e.view.itemAt(e.pos.toPoint())
            while hit is not None and not isinstance(hit, NodeItemMixin):
                hit = hit.parentItem()
            if isinstance(hit, LinkItem):
                self.app.run_command("select", [hit.node_name])
                return HANDLED
            start = self._choose(e)
            self.op = {"start": start, "end": start, "press": QPointF(e.pos), "view": e.view}
            self.feedback.show("tool", e.view, start, e.view.to_scene(e.pos))
            return HANDLED
        if self.op is None:
            return None
        if e.kind == "move":
            end = self._choose(e, exclude=self.op["start"].name)
            self.op["end"] = end
            self.feedback.show("tool", e.view, end, e.view.to_scene(e.pos))
            return HANDLED
        if e.kind == "release":
            op, self.op = self.op, None
            self.feedback.clear("tool")
            start, end = op["start"], op["end"]
            if _distance(op["press"], QPointF(e.pos)) < 6.0:
                self.ui.status("Drag from one element (or anywhere) to another to link them")
                return HANDLED
            if start.name and start.name == end.name:
                self.ui.status("A link needs two different elements")
                return HANDLED
            attrs: dict[str, Any] = self.options.as_dict()
            ends: list[Any] = []
            for key, choice in (("source", start), ("target", end)):
                if choice.name:
                    ends.append(choice.name)
                    attrs[f"{key}_anchor"] = choice.anchor
                    if choice.spot is not None:
                        attrs[f"{key}_spot"] = list(choice.spot)
                else:
                    ends.append([round(choice.point.x(), 3), round(choice.point.y(), 3)])
            self.app.run_command("links.connect", ends[0], ends[1], **attrs)
            return HANDLED
        return None

    def hover(self, e: PointerEvent) -> None:
        if self.op is None:
            self.feedback.show("tool", e.view, self._choose(e), e.view.to_scene(e.pos))

    def paint_overlay(self, view: Any, painter: QPainter) -> None:
        if self.op is None or self.op["view"] is not view:
            return
        start, end = self.op["start"], self.op["end"]
        color = w.color_from(self.options.get("color"))
        a = view.to_viewport(start.point)
        b = view.to_viewport(end.point if end.name else self.feedback.cursor)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(color, 2.0, Qt.DashLine, Qt.RoundCap))
        painter.drawLine(a, b)
        if start.name:  # keep the start's element outlined while dragging to the other end
            item = self.ui.canvas.item(start.name)
            if item is not None:
                painter.setPen(QPen(theme.ACCENT, 1.5))
                painter.setBrush(Qt.NoBrush)
                painter.drawPolygon(QPolygonF([view.to_viewport(p) for p in item.scene_polygon()]))
        painter.setPen(QPen(QColor("#ffffff"), 1.5))
        painter.setBrush(color)
        painter.drawEllipse(a, 4.0, 4.0)

    def options_panel(self, parent: QWidget) -> QWidget:
        panel = self.ui.service("brush").make_tool_panel(self, parent, recent=False)
        layout = panel.layout()
        panel.routing_combo = panel.form.editors["routing"]  # type: ignore[attr-defined]
        panel.start_combo = panel.form.editors["start_cap"]  # type: ignore[attr-defined]
        panel.end_combo = panel.form.editors["end_cap"]  # type: ignore[attr-defined]
        snap = QCheckBox("Snap to elements", panel)
        snap.setToolTip("Ends attach to the element under or near the pointer (hold Alt for a free end).\n"
                        "Off: every end is a free point.")
        snap.setChecked(self.feedback.snap)
        snap.toggled.connect(lambda on: setattr(self.feedback, "snap", on))
        panel.snap_check = snap  # type: ignore[attr-defined]
        layout.addWidget(snap)
        return panel


# ---- Wiring ---------------------------------------------------------------------------------------


def activate_links_ui(ui: Any) -> None:
    canvas = ui.canvas
    session = ui.session
    scene = session.scene
    feedback = LinkFeedback(ui)

    def resolve(node_id: str) -> Any:
        name = scene.name_of_id(node_id) if node_id else None
        return canvas.item(name) if name else None

    def id_of(name: str) -> str | None:
        node = canvas.nodes.get(name)
        return node.get("id") if node else None

    def factory(_canvas: Any, node: dict[str, Any]) -> LinkItem:
        item = LinkItem(node["name"], resolve, id_of, canvas.item)
        item.apply_attrs(node.get("attrs", {}))
        return item

    def post_sync(item: Any, _node: dict[str, Any]) -> None:
        if isinstance(item, LinkItem):
            item.update_route(force=True)  # its ends may have been rebuilt

    def describe(_name: str, node: dict[str, Any]) -> list[str]:
        attrs = node.get("attrs", {})
        names = [(scene.name_of_id(str(attrs.get(end))) or "?") if attrs.get(end) else "·"
                 for end in ("source", "target")]
        return [f"{names[0]} → {names[1]}"]

    ui.node_ui(LINK, factory=factory, label="Link", icon="link", badge=("#6ebeff", "↝"),
               post_sync=post_sync, describe=describe)
    ui.register_tool(LinkTool(ui, feedback))
    ui.contribute("gizmo.handles", LinkHandles(ui, feedback))
    ui.overlay(feedback.paint, order=45)
    grabber = EndGrabber(ui)
    ui.input_handler(grabber, 610)  # before the gizmo, which only knows the selected link
    ui.overlay(grabber.paint, order=46)
    ui.provide_service("links", feedback)

    def follow(_regions: Any = None) -> None:
        """Links follow their ends live (drags, nudges, animations). Re-routing only
        happens when something moved, so this settles after one round."""
        for item in list(canvas.items.values()):
            if isinstance(item, LinkItem):
                try:
                    item.update_route()
                except RuntimeError:
                    continue

    canvas.scene.changed.connect(follow)
    ui.headless.track(lambda: canvas.scene.changed.disconnect(follow))

    def selected_links() -> bool:
        return any(canvas.nodes.get(n, {}).get("type") == LINK for n in session.selection.names)

    ui.context_key("links.selected", selected_links)
    a = ui.action
    a("links.connect_selection", "links.connect_selection", "Link Selected Elements",
      keywords="connect connector relation chain", category="Create", icon="link")
    a("links.select_connected", "links.select_connected", "Select Linked Elements",
      keywords="connected related neighbours links", category="Edit", icon="link")
    a("links.clear_waypoints", "links.clear_waypoints", "Straighten Link", keywords="remove waypoints reset path",
      category="Edit")
    a("links.reverse", "links.reverse", "Reverse Link", keywords="swap direction flip ends", category="Edit")
    ui.bind("Ctrl+L", "links.connect_selection")
    ui.bind("Ctrl+Shift+L", "links.select_connected")
    ui.menu("canvas", "links.connect_selection", group="arrange", order=30, when="selection.count > 1")
    ui.menu("canvas", "links.select_connected", group="select", order=20, when="selection.count > 0")
    ui.menu("canvas", "links.clear_waypoints", group="transform", order=40, when="links.selected")
    ui.menu("canvas", "links.reverse", group="transform", order=41, when="links.selected")
