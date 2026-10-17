"""The transform gizmo.

Draws handles around the selection and turns drags on them into scale /
rotate edits (priority 600 in the input pipeline, only while the active
tool's ``gizmo`` attribute allows it: ``"full"`` for Select, ``"handles"``
for drawing tools that keep the last shape adjustable).

Node types customise the gizmo of a single selected node by contributing a
*handle provider* to ``gizmo.handles``: an object with ``node_type`` and any
of these methods (all optional)::

    build(gizmo, view, item, g) -> dict | None   # adjust g, or return a replacement
    hit(gizmo, view, g, pos) -> str | None       # a handle name, "" for "none", None to defer
    start(gizmo, handle, g, item) -> str | None  # op kind for a handle it owns
    drag(gizmo, op, scene_point, modifiers, view)
    labels: dict[str, str]                        # op kind -> undo label
    cursor(gizmo, handle, g, view) -> cursor | None
    paint(gizmo, view, painter, g) -> bool        # True = painted, skip the default look
    double_click(gizmo, view, g, handle) -> bool
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QPainter, QPen, QPixmap, QPolygonF, QTransform

from pixelref.plugin_api.ui import HANDLED, InputHandler, PointerEvent, paint, rotated_about, scaled_about, theme

CORNERS = ("tl", "tr", "br", "bl")
EDGES = ("tm", "mr", "bm", "ml")
OPPOSITE = {"tl": "br", "tr": "bl", "br": "tl", "bl": "tr", "tm": "bm", "bm": "tm", "ml": "mr", "mr": "ml"}


def local_handle_points(r: QRectF) -> dict[str, QPointF]:
    cx, cy = r.center().x(), r.center().y()
    return {
        "tl": r.topLeft(), "tr": r.topRight(), "br": r.bottomRight(), "bl": r.bottomLeft(),
        "tm": QPointF(cx, r.top()), "bm": QPointF(cx, r.bottom()), "ml": QPointF(r.left(), cy),
        "mr": QPointF(r.right(), cy),
    }


def projection(m: QPointF, a: QPointF, h: QPointF) -> float:
    vx, vy = h.x() - a.x(), h.y() - a.y()
    length_sq = vx * vx + vy * vy
    if length_sq < 1e-9:
        return 1.0
    return ((m.x() - a.x()) * vx + (m.y() - a.y()) * vy) / length_sq


def distance(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


def resize_cursor_for(vector: QPointF) -> Qt.CursorShape:
    angle = math.degrees(math.atan2(vector.y(), vector.x())) % 180.0
    if angle < 22.5 or angle >= 157.5:
        return Qt.SizeHorCursor
    if angle < 67.5:
        return Qt.SizeFDiagCursor
    if angle < 112.5:
        return Qt.SizeVerCursor
    return Qt.SizeBDiagCursor


_ROTATE_CURSOR: QCursor | None = None


def rotate_cursor() -> QCursor:
    """Small circular-arrow cursor (Qt has no built-in rotate cursor)."""
    global _ROTATE_CURSOR
    if _ROTATE_CURSOR is None:
        pm = QPixmap(24, 24)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        for color, width in ((QColor("black"), 4.0), (QColor("white"), 2.0)):
            p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(5, 5, 14, 14), 30 * 16, 280 * 16)
            p.setBrush(color)
            p.drawPolygon(QPolygonF([QPointF(17, 2), QPointF(22, 8), QPointF(14, 9)]))
        p.end()
        _ROTATE_CURSOR = QCursor(pm, 12, 12)
    return _ROTATE_CURSOR


class GizmoService:
    """Gizmo geometry and hit testing (also used by the context menu and tests)."""

    HANDLE_SIZE = 8.0
    HANDLE_HIT = 8.0
    ROTATE_OFFSET = 26.0
    ROTATE_HIT = 9.0
    ROTATE_ZONE = 22.0  # rotate when dragging just outside a corner
    MIN_EDGE_HANDLE_SPAN = 28.0
    RADIUS_HANDLE_INSET = 14.0

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.op: dict[str, Any] | None = None

    # ---- Providers ---------------------------------------------------------------------

    def provider(self, item: Any) -> Any:
        for provider in self.ui.contributions("gizmo.handles"):
            if getattr(provider, "node_type", None) == item.node_type:
                return provider
        return None

    def _provider_for(self, g: dict[str, Any]) -> Any:
        return self.provider(g["targets"][0]) if g.get("single") else None

    # ---- Geometry ------------------------------------------------------------------------

    def gizmo(self, view: Any) -> dict[str, Any] | None:
        targets = self.ui.canvas.transform_targets()
        targets = [t for t in targets if view.display.shows(t.node_name)]
        if not targets:
            return None
        single = len(targets) == 1
        provider = self.provider(targets[0]) if single else None
        if single:
            item = targets[0]
            st = item.sceneTransform()
            local = local_handle_points(item.content_rect())
            corners_scene = [st.map(local[name]) for name in CORNERS]
            center_scene = st.map(item.content_rect().center())
        else:
            rect = QRectF()
            for item in targets:
                rect = rect.united(item.scene_content_rect())
            corners_scene = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]
            center_scene = rect.center()
        vp = [view.to_viewport(p) for p in corners_scene]
        handles = dict(zip(CORNERS, vp))
        span = min(distance(vp[0], vp[1]), distance(vp[1], vp[2]))
        if single and span >= self.MIN_EDGE_HANDLE_SPAN:
            for name, (a, b) in zip(EDGES, ((0, 1), (1, 2), (2, 3), (3, 0))):
                handles[name] = (vp[a] + vp[b]) / 2.0
        center_vp = view.to_viewport(center_scene)
        top_mid = (vp[0] + vp[1]) / 2.0
        direction = top_mid - center_vp
        length = math.hypot(direction.x(), direction.y())
        direction = direction / length if length > 2.0 else QPointF(0.0, -1.0)
        g = {
            "targets": targets,
            "single": single,
            "corners_scene": corners_scene,
            "center_scene": center_scene,
            "corners_vp": vp,
            "handles_vp": handles,
            "top_mid_vp": top_mid,
            "rotate_vp": top_mid + direction * self.ROTATE_OFFSET,
        }
        if provider is not None and hasattr(provider, "build"):
            replaced = provider.build(self, view, targets[0], g)
            if replaced is not None:
                g = replaced
        return g

    def hit(self, view: Any, pos: QPointF, g: dict[str, Any] | None = None, zones: bool = True) -> str | None:
        """Handle under ``pos``. ``zones=False`` ignores the invisible rotate zone
        outside the corners (drawing tools need that space to start new shapes)."""
        g = g if g is not None else self.gizmo(view)
        if g is None:
            return None
        provider = self._provider_for(g)
        if provider is not None and hasattr(provider, "hit"):
            found = provider.hit(self, view, g, pos)
            if found is not None:
                return found or None
        if "rotate_vp" in g and distance(pos, g["rotate_vp"]) <= self.ROTATE_HIT:
            return "rot"
        for name in CORNERS + EDGES:
            point = g["handles_vp"].get(name)
            if point is not None and distance(pos, point) <= self.HANDLE_HIT:
                return name
        polygon = QPolygonF(g["corners_vp"])
        if zones and not polygon.containsPoint(pos, Qt.OddEvenFill):
            for point in g["corners_vp"]:
                if distance(pos, point) <= self.ROTATE_ZONE:
                    return "rot"
        return None

    def contains(self, view: Any, pos: QPointF) -> bool:
        g = self.gizmo(view)
        return g is not None and QPolygonF(g["corners_vp"]).containsPoint(QPointF(pos), Qt.OddEvenFill)

    def cursor_for(self, handle: str, g: dict[str, Any], view: Any) -> QCursor | Qt.CursorShape:
        provider = self._provider_for(g)
        if provider is not None and hasattr(provider, "cursor"):
            cursor = provider.cursor(self, handle, g, view)
            if cursor is not None:
                return cursor
        if handle == "rot":
            return rotate_cursor()
        center = view.to_viewport(g["center_scene"])
        point = g["handles_vp"].get(handle)
        return resize_cursor_for(point - center) if point is not None else Qt.ArrowCursor

    # ---- Operations -------------------------------------------------------------------------

    def start(self, view: Any, handle: str, g: dict[str, Any], scene_pos: QPointF) -> None:
        targets = []
        for item in g["targets"]:
            parent = item.parentItem()
            parent_inv = parent.sceneTransform().inverted()[0] if parent is not None else QTransform()
            targets.append((item, item.sceneTransform(), item.local_transform(), parent_inv, item._rotation))
        kind = "rotate" if handle == "rot" else "scale"
        provider = self._provider_for(g)
        if provider is not None and hasattr(provider, "start"):
            custom = provider.start(self, handle, g, g["targets"][0])
            if custom:
                kind = custom
        self.op = {
            "kind": kind,
            "handle": handle,
            "single": g["single"],
            "targets": targets,
            "press": scene_pos,
            "corners_scene": g["corners_scene"],
            "center_scene": g["center_scene"],
            "start_rect": QRectF(g["targets"][0].content_rect()),
            "provider": provider,
            "view": view,
        }
        self.ui.canvas.begin_interaction()

    def drag(self, m: QPointF, modifiers: Any, view: Any) -> None:
        op = self.op
        if op is None:
            return
        if op["kind"] == "scale":
            self._apply_scale(m, modifiers)
        elif op["kind"] == "rotate":
            self._apply_rotation(m, modifiers)
        elif op["provider"] is not None:
            op["provider"].drag(self, op, m, modifiers, view)
        self.ui.app.update_views()

    def finish(self) -> None:
        op, self.op = self.op, None
        if op is None:
            return
        labels = {"scale": "Scale", "rotate": "Rotate"}
        provider = op["provider"]
        if provider is not None:
            labels.update(getattr(provider, "labels", {}))
        self.ui.canvas.end_interaction(labels.get(op["kind"], "Edit"))

    def cancel(self) -> bool:
        op, self.op = self.op, None
        if op is None:
            return False
        for item, _s0, l0, _parent_inv, _rotation in op["targets"]:
            try:
                item.set_local_transform(l0)
            except RuntimeError:
                pass
        self.ui.canvas.end_interaction("Cancelled")
        self.ui.canvas.request_full_refresh()
        return True

    def _apply_scale(self, m: QPointF, modifiers: Any) -> None:
        op = self.op
        handle = op["handle"]
        about_center = bool(modifiers & Qt.AltModifier)
        free = bool(modifiers & Qt.ShiftModifier)
        if op["single"]:
            item, s0, l0, _parent_inv, _rotation = op["targets"][0]
            points = local_handle_points(item.content_rect())
            h = points[handle]
            a = item.content_rect().center() if about_center else points[OPPOSITE[handle]]
            if handle in CORNERS and not free:
                fx = fy = projection(m, s0.map(a), s0.map(h))
            else:
                m_local = s0.inverted()[0].map(m)
                fx = fy = 1.0
                if handle not in ("tm", "bm") and abs(h.x() - a.x()) > 1e-9:
                    fx = (m_local.x() - a.x()) / (h.x() - a.x())
                if handle not in ("ml", "mr") and abs(h.y() - a.y()) > 1e-9:
                    fy = (m_local.y() - a.y()) / (h.y() - a.y())
            fx, fy = max(0.02, fx), max(0.02, fy)
            item.set_local_transform(_scaled_about_local(a, fx, fy) * l0)
            self.ui.status(f"Scale {fx * 100:.0f}% × {fy * 100:.0f}%")
        else:
            corners = op["corners_scene"]
            index = CORNERS.index(handle) if handle in CORNERS else 0
            h = corners[index]
            a = op["center_scene"] if about_center else corners[(index + 2) % 4]
            f = max(0.02, projection(m, a, h))
            delta = scaled_about(a, f, f)
            for item, s0, _l0, parent_inv, _rotation in op["targets"]:
                item.set_local_transform(s0 * delta * parent_inv)
            self.ui.status(f"Scale {f * 100:.0f}%")

    def _apply_rotation(self, m: QPointF, modifiers: Any) -> None:
        op = self.op
        c = op["center_scene"]
        start = op["press"]
        delta = math.degrees(math.atan2(m.y() - c.y(), m.x() - c.x()) - math.atan2(start.y() - c.y(), start.x() - c.x()))
        if modifiers & Qt.ShiftModifier:
            if op["single"]:
                base = op["targets"][0][4]
                # Under a mirrored parent a scene rotation turns the local angle the other way.
                sign = -1.0 if op["targets"][0][3].determinant() < 0 else 1.0
                delta = sign * (round((base + sign * delta) / 15.0) * 15.0 - base)
            else:
                delta = round(delta / 15.0) * 15.0
        rotation = rotated_about(c, delta)
        for item, s0, _l0, parent_inv, _rotation in op["targets"]:
            item.set_local_transform(s0 * rotation * parent_inv)
        if op["single"]:
            self.ui.status(f"Rotation {op['targets'][0][0]._rotation:.1f}°")
        else:
            self.ui.status(f"Rotate {delta:+.1f}°")

    # ---- Painting ---------------------------------------------------------------------------

    def paint(self, view: Any, painter: QPainter) -> None:
        tool = self.ui.tools.active
        if tool is None or not tool.gizmo or tool.busy:
            return
        g = self.gizmo(view)
        if g is None:
            return
        provider = self._provider_for(g)
        if provider is not None and hasattr(provider, "paint") and provider.paint(self, view, painter, g):
            return
        painter.setPen(QPen(theme.ACCENT, 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(QPolygonF(g["corners_vp"]))
        painter.drawLine(g["top_mid_vp"], g["rotate_vp"])
        for name, point in g["handles_vp"].items():
            if name in CORNERS or name in EDGES:
                paint.paint_handle(painter, point, self.HANDLE_SIZE)
        painter.setPen(QPen(theme.ACCENT, 1.2))
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(g["rotate_vp"], 5.0, 5.0)


def _scaled_about_local(point: QPointF, fx: float, fy: float) -> QTransform:
    return scaled_about(point, fx, fy)


class GizmoHandler(InputHandler):
    def __init__(self, ui: Any, service: GizmoService) -> None:
        self.ui = ui
        self.service = service

    def _mode(self) -> str | None:
        tool = self.ui.tools.active
        return tool.gizmo if tool is not None else None

    def pointer(self, e: PointerEvent) -> str | None:
        service = self.service
        if e.kind == "press":
            mode = self._mode()
            if not mode or e.button != Qt.LeftButton or e.alt and mode == "full":
                return None
            item = e.item
            if item is not None and getattr(item, "editing", False):
                return None
            g = service.gizmo(e.view)
            handle = service.hit(e.view, e.pos, g, zones=(mode == "full"))
            if handle is None or g is None:
                return None
            service.start(e.view, handle, g, e.scene_pos)
            return HANDLED
        if e.kind == "move":
            if service.op is None:
                return None
            service.drag(e.view.to_scene(e.pos), e.modifiers, e.view)
            return HANDLED
        if e.kind == "release":
            if service.op is None:
                return None
            service.finish()
            return HANDLED
        if e.kind == "double":
            if self._mode() != "full":
                return None
            g = service.gizmo(e.view)
            if g is None:
                return None
            handle = service.hit(e.view, e.pos, g)
            provider = service._provider_for(g)
            if handle and provider is not None and hasattr(provider, "double_click"):
                if provider.double_click(service, e.view, g, handle):
                    return HANDLED
        return None

    def hover(self, e: PointerEvent) -> None:
        mode = self._mode()
        if not mode:
            return
        g = self.service.gizmo(e.view)
        handle = self.service.hit(e.view, e.pos, g, zones=(mode == "full"))
        if handle is not None and g is not None:
            e.set_cursor(self.service.cursor_for(handle, g, e.view))


def activate_ui(ui: Any) -> None:
    service = GizmoService(ui)
    ui.declare_extension_point("gizmo.handles", "Handle providers for single selected nodes of a type")
    ui.provide_service("gizmo", service)
    ui.input_handler(GizmoHandler(ui, service), 600)
    ui.overlay(lambda view, painter: service.paint(view, painter), order=50)
    ui.escape_handler(service.cancel, order=10)
