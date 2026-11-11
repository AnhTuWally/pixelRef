"""The crop tool: rectangular, polygonal, freehand lasso, Bezier pen, knife line, and boolean masks."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QWidget

from pixelref.plugin_api import Attr, crop as crop_geom, fast_copy
from pixelref.plugin_api.contracts import InspectorRows, SelectionInfo
from pixelref.plugin_api.ui import HANDLED, KeyEvent, PointerEvent, Tool, paint, theme
from pixelref.plugin_api.ui import widgets as w

CORNERS = ("tl", "tr", "br", "bl")
MODES = ("rect", "poly", "lasso", "bezier", "knife")
MODE_LABELS = {
    "rect": "1 Rect",
    "poly": "2 Poly",
    "lasso": "3 Lasso",
    "bezier": "4 Bezier",
    "knife": "5 Knife",
}
BOOL_OPS = ("replace", "add", "subtract")
BOOL_LABELS = {
    "replace": "Replace",
    "add": "+ Add",
    "subtract": "− Sub",
}
HANDLE_HIT = 8.0
EDGE_HIT = 6.0
TANGENT_HIT = 7.0
CENTER_HIT = 9.0


def local_handle_points(r: QRectF) -> dict[str, QPointF]:
    cx, cy = r.center().x(), r.center().y()
    return {
        "tl": r.topLeft(), "tr": r.topRight(), "br": r.bottomRight(), "bl": r.bottomLeft(),
        "tm": QPointF(cx, r.top()), "bm": QPointF(cx, r.bottom()),
        "ml": QPointF(r.left(), cy), "mr": QPointF(r.right(), cy),
    }


def resize_cursor_for(vector: QPointF) -> Qt.CursorShape:
    angle = math.degrees(math.atan2(vector.y(), vector.x())) % 180.0
    if angle < 22.5 or angle >= 157.5:
        return Qt.SizeHorCursor
    if angle < 67.5:
        return Qt.SizeFDiagCursor
    if angle < 112.5:
        return Qt.SizeVerCursor
    return Qt.SizeBDiagCursor


def _snap_angle(origin: QPointF, target: QPointF, step_deg: float = 45.0) -> QPointF:
    dx, dy = target.x() - origin.x(), target.y() - origin.y()
    dist = math.hypot(dx, dy)
    if dist < 1e-6:
        return QPointF(target)
    angle = math.atan2(dy, dx)
    step = math.radians(step_deg)
    snapped = round(angle / step) * step
    return QPointF(origin.x() + dist * math.cos(snapped), origin.y() + dist * math.sin(snapped))


def _clamp_point(pt: QPointF, bounds: QRectF) -> QPointF:
    return QPointF(
        max(bounds.left(), min(bounds.right(), pt.x())),
        max(bounds.top(), min(bounds.bottom(), pt.y())),
    )


class CropTool(Tool):
    id = "crop"
    title = "Crop"
    icon = "image"
    tooltip = "Crop and mask the selected image (C)"
    order = 200.0
    kind = "momentary"
    show_selection = False
    exclusive_overlay = True
    hint = (
        "1 Rect · 2 Poly · 3 Lasso · 4 Bezier · 5 Knife · "
        "Shift to add · Alt to subtract · Enter to apply · Esc to cancel"
    )
    hidden = True  # started with C on an image
    options_title = "Crop options"
    option_specs = (
        Attr("mode", "enum", "rect", choices=MODES, label="Mode",
             doc="Crop drawing mode: rect (1), poly (2), lasso (3), bezier (4), knife (5)"),
        Attr("boolean_op", "enum", "replace", choices=BOOL_OPS, label="Operation",
             doc="How new shapes combine with the current mask: replace, add (Shift), subtract (Alt)"),
        Attr("lasso_simplify", "float", 2.0, minimum=0.25, maximum=25.0, soft_min=0.5, soft_max=10.0,
             step=0.25, precision=1, unit="px", label="Lasso tolerance",
             doc="Simplification tolerance in screen pixels for Freehand Lasso"),
    )

    def __init__(self, ui: Any) -> None:
        super().__init__(ui)
        self.item: Any = None
        self.mode = "rect"
        self.boolean_op = "replace"
        self.rect = QRectF()
        self.contours: list[dict[str, Any]] = []
        self.has_custom_contours = False
        self.draft_points: list[list[float]] = []
        self.draft_boolean = "replace"
        self.hover_local: QPointF | None = None
        self.hover_vp: QPointF | None = None
        self.op: dict[str, Any] | None = None
        self.knife_flip = False
        self.last_knife: dict[str, Any] | None = None
        self._history: list[tuple[QRectF, list[dict[str, Any]], bool, str]] = []
        self._redo: list[tuple[QRectF, list[dict[str, Any]], bool, str]] = []
        self._hud_pills: list[tuple[QRectF, str, str]] = []
        self.apply_on_exit = False

    # ---- Lifecycle -------------------------------------------------------------------------

    def activate(self, **args: Any) -> None:
        item = args.get("item")
        if item is None:
            names = [n for n in self.ui.session.selection.names
                     if self.ui.canvas.nodes.get(n, {}).get("type") == "image"]
            item = self.ui.canvas.item(names[0]) if names else None
        self.item = item
        self.apply_on_exit = False
        self.op = None
        self.draft_points = []
        self.hover_local = None
        self.hover_vp = None
        self.knife_flip = False
        self.last_knife = None
        self._history.clear()
        self._redo.clear()
        self.boolean_op = "replace"
        self.options.set("boolean_op", "replace")

        requested_mode = args.get("mode")
        if item is not None:
            self.rect = QRectF(item.crop_rect())
            raw_crop = self.ui.canvas.nodes.get(item.node_name, {}).get("attrs", {}).get("crop")
            if crop_geom.is_freeform_crop(raw_crop):
                self.contours = crop_geom.crop_contours(raw_crop)
                self.has_custom_contours = True
                has_bezier = any(len(pt) >= 6 for c in self.contours for pt in c.get("points", ()))
                default_mode = "bezier" if has_bezier else "poly"
            else:
                self.contours = [{
                    "points": crop_geom.rect_to_points(
                        self.rect.x(), self.rect.y(), self.rect.width(), self.rect.height()
                    ),
                    "subtract": False,
                }]
                self.has_custom_contours = False
                default_mode = "rect"
            chosen = requested_mode if requested_mode in MODES else default_mode
            self._set_mode_internal(chosen)
            item.set_crop_preview(True)
        self.app.update_views()

    def deactivate(self) -> None:
        item, self.item = self.item, None
        self.op = None
        self.draft_points = []
        if item is None:
            return
        try:
            item.set_crop_preview(False)
        except RuntimeError:
            return
        if self.apply_on_exit:
            value = self._computed_crop_value(item)
            self.app.run_command("crop.set", item.node_name, value)
        self.app.update_views()

    def cancel(self) -> bool:
        if self.draft_points:
            self.draft_points = []
            self.op = None
            return True
        if self.op is not None:
            self.op = None
            return True
        return False

    def finish(self, apply: bool) -> None:
        if apply and len(self.draft_points) >= 3:
            self._commit_draft()
        self.apply_on_exit = apply
        self.app.tools.activate(self.app.tools.default_id)

    def _computed_crop_value(self, item: Any) -> Any:
        full = item.full_rect()
        if not self.has_custom_contours and self.mode == "rect":
            return None if self.rect.contains(full) else [
                self.rect.x(), self.rect.y(), self.rect.width(), self.rect.height()
            ]
        return crop_geom.serialize_contours(self.contours, full_size=(full.width(), full.height()))

    # ---- Mode & History Management ---------------------------------------------------------

    def _push_history(self) -> None:
        self._history.append((
            QRectF(self.rect),
            fast_copy(self.contours),
            self.has_custom_contours,
            self.mode,
        ))
        self._redo.clear()

    def _undo_internal(self) -> bool:
        if not self._history:
            return False
        self._redo.append((QRectF(self.rect), fast_copy(self.contours), self.has_custom_contours, self.mode))
        rect, contours, custom, mode = self._history.pop()
        self.rect = QRectF(rect)
        self.contours = fast_copy(contours)
        self.has_custom_contours = custom
        self._set_mode_internal(mode)
        self.last_knife = None
        self.app.update_views()
        return True

    def _redo_internal(self) -> bool:
        if not self._redo:
            return False
        self._history.append((QRectF(self.rect), fast_copy(self.contours), self.has_custom_contours, self.mode))
        rect, contours, custom, mode = self._redo.pop()
        self.rect = QRectF(rect)
        self.contours = fast_copy(contours)
        self.has_custom_contours = custom
        self._set_mode_internal(mode)
        self.last_knife = None
        self.app.update_views()
        return True

    def set_mode(self, mode: str) -> None:
        if mode not in MODES or mode == self.mode:
            return
        if len(self.draft_points) >= 3:
            self._commit_draft()
        else:
            self.draft_points = []
        self.op = None
        self._set_mode_internal(mode)
        self.ui.status(f"Crop mode: {MODE_LABELS[mode]} — Enter to apply, Esc to cancel")
        self.app.update_views()

    def _set_mode_internal(self, mode: str) -> None:
        if mode == "rect" and self.has_custom_contours and self.item is not None:
            full = self.item.full_rect()
            box = crop_geom.crop_bounds(
                crop_geom.serialize_contours(self.contours),
                (full.width(), full.height()),
            )
            if box is not None:
                self.rect = QRectF(round(box[0]), round(box[1]), round(box[2]), round(box[3]))
        elif mode != "rect" and not self.has_custom_contours:
            self.contours = [{
                "points": crop_geom.rect_to_points(
                    self.rect.x(), self.rect.y(), self.rect.width(), self.rect.height()
                ),
                "subtract": False,
            }]
        self.mode = mode
        if self.options.get("mode") != mode:
            self.options.set("mode", mode)

    def _sync_rect_from_contours(self) -> None:
        if self.item is None:
            return
        full = self.item.full_rect()
        box = crop_geom.crop_bounds(
            crop_geom.serialize_contours(self.contours),
            (full.width(), full.height()),
        )
        if box is not None:
            self.rect = QRectF(round(box[0]), round(box[1]), round(box[2]), round(box[3]))

    def _effective_boolean(self, modifiers: Qt.KeyboardModifiers) -> str:
        if modifiers & Qt.AltModifier:
            return "subtract"
        if modifiers & Qt.ShiftModifier:
            return "add"
        return self.boolean_op

    def _apply_contour_op(self, points: list[list[float]], boolean_op: str) -> None:
        if len(points) < 3:
            return
        self._push_history()
        if boolean_op == "replace" or not self.contours:
            self.contours = [{"points": points, "subtract": False}]
        elif boolean_op == "add":
            if not self.has_custom_contours:
                self.contours = [{
                    "points": crop_geom.rect_to_points(
                        self.rect.x(), self.rect.y(), self.rect.width(), self.rect.height()
                    ),
                    "subtract": False,
                }]
            self.contours.append({"points": points, "subtract": False})
        elif boolean_op == "subtract":
            if not self.has_custom_contours:
                self.contours = [{
                    "points": crop_geom.rect_to_points(
                        self.rect.x(), self.rect.y(), self.rect.width(), self.rect.height()
                    ),
                    "subtract": False,
                }]
            self.contours.append({"points": points, "subtract": True})
        self.has_custom_contours = True
        self._sync_rect_from_contours()

    def _commit_draft(self) -> bool:
        if len(self.draft_points) < 3:
            self.draft_points = []
            return False
        pts = fast_copy(self.draft_points)
        self.draft_points = []
        self._apply_contour_op(pts, self.draft_boolean)
        return True

    def _reset_to_full(self) -> None:
        if self.item is None:
            return
        self._push_history()
        full = self.item.full_rect()
        self.rect = QRectF(full)
        self.contours = [{
            "points": crop_geom.rect_to_points(full.x(), full.y(), full.width(), full.height()),
            "subtract": False,
        }]
        self.has_custom_contours = False
        self.draft_points = []
        self.last_knife = None
        self.app.update_views()

    # ---- Coordinate & Hit Helpers ----------------------------------------------------------

    def handles_vp(self, view: Any) -> dict[str, QPointF]:
        if self.item is None:
            return {}
        st = self.item.sceneTransform()
        return {name: view.to_viewport(st.map(p)) for name, p in local_handle_points(self.rect).items()}

    def _local_to_vp(self, view: Any, pt: QPointF | list[float] | tuple[float, float]) -> QPointF:
        p = pt if isinstance(pt, QPointF) else QPointF(float(pt[0]), float(pt[1]))
        return view.to_viewport(self.item.sceneTransform().map(p))

    def _in_rect(self, view: Any, pos: QPointF) -> bool:
        handles = self.handles_vp(view)
        if not handles:
            return False
        return QPolygonF([handles[name] for name in CORNERS]).containsPoint(pos, Qt.OddEvenFill)

    def _center_handle_local(self) -> QPointF | None:
        if not self.has_custom_contours or not self.contours:
            return None
        return self.rect.center()

    def _hit_vertex(self, view: Any, vp_pos: QPointF) -> tuple[int, int] | None:
        if not self.has_custom_contours:
            return None
        for ci, contour in enumerate(self.contours):
            for vi, pt in enumerate(contour.get("points", ())):
                v_vp = self._local_to_vp(view, pt)
                if math.hypot(vp_pos.x() - v_vp.x(), vp_pos.y() - v_vp.y()) <= HANDLE_HIT:
                    return ci, vi
        return None

    def _hit_tangent(self, view: Any, vp_pos: QPointF) -> tuple[int, int, str] | None:
        if not self.has_custom_contours:
            return None
        for ci, contour in enumerate(self.contours):
            for vi, pt in enumerate(contour.get("points", ())):
                if len(pt) < 6:
                    continue
                in_vp = self._local_to_vp(view, (pt[2], pt[3]))
                if math.hypot(vp_pos.x() - in_vp.x(), vp_pos.y() - in_vp.y()) <= TANGENT_HIT:
                    return ci, vi, "in"
                out_vp = self._local_to_vp(view, (pt[4], pt[5]))
                if math.hypot(vp_pos.x() - out_vp.x(), vp_pos.y() - out_vp.y()) <= TANGENT_HIT:
                    return ci, vi, "out"
        return None

    def _hit_edge(self, view: Any, vp_pos: QPointF, local_pos: QPointF) -> tuple[int, int, list[float]] | None:
        if not self.has_custom_contours:
            return None
        best_dist = EDGE_HIT
        best: tuple[int, int, list[float]] | None = None
        for ci, contour in enumerate(self.contours):
            pts = contour.get("points", ())
            n = len(pts)
            if n < 3:
                continue
            for i in range(n):
                p0 = pts[i]
                p1 = pts[(i + 1) % n]
                a = self._local_to_vp(view, p0)
                b = self._local_to_vp(view, p1)
                dx, dy = b.x() - a.x(), b.y() - a.y()
                len_sq = dx * dx + dy * dy
                if len_sq < 1e-6:
                    continue
                t = max(0.05, min(0.95, ((vp_pos.x() - a.x()) * dx + (vp_pos.y() - a.y()) * dy) / len_sq))
                proj_x, proj_y = a.x() + t * dx, a.y() + t * dy
                d = math.hypot(vp_pos.x() - proj_x, vp_pos.y() - proj_y)
                if d <= best_dist:
                    best_dist = d
                    lx = round(float(p0[0]) + t * (float(p1[0]) - float(p0[0])), 2) + 0.0
                    ly = round(float(p0[1]) + t * (float(p1[1]) - float(p0[1])), 2) + 0.0
                    best = (ci, i + 1, [lx, ly])
        return best

    def _hit_hud_pill(self, vp_pos: QPointF) -> tuple[str, str] | None:
        for rect, kind, val in self._hud_pills:
            if rect.contains(vp_pos):
                return kind, val
        return None

    def _toggle_smooth_vertex(self, ci: int, vi: int) -> None:
        pts = self.contours[ci]["points"]
        n = len(pts)
        if n < 3:
            return
        self._push_history()
        pt = pts[vi]
        if len(pt) >= 6:
            pts[vi] = [pt[0], pt[1]]
        else:
            prev_pt = pts[(vi - 1) % n]
            next_pt = pts[(vi + 1) % n]
            tx = (float(next_pt[0]) - float(prev_pt[0])) * 0.22
            ty = (float(next_pt[1]) - float(prev_pt[1])) * 0.22
            x, y = float(pt[0]), float(pt[1])
            pts[vi] = [
                x, y,
                round(x - tx, 2) + 0.0, round(y - ty, 2) + 0.0,
                round(x + tx, 2) + 0.0, round(y + ty, 2) + 0.0,
            ]
        self._sync_rect_from_contours()

    # ---- Pointer & Keyboard Input ----------------------------------------------------------

    def pointer(self, e: PointerEvent) -> str | None:
        item = self.item
        if item is None:
            if e.kind == "press":
                self.finish(apply=False)
                return HANDLED
            return None

        local = item.mapFromScene(e.scene_pos)
        full = item.full_rect()
        clamped = _clamp_point(local, full)

        # Right-click deletes a hovered vertex or pops the last draft point.
        if e.kind == "press" and e.button == Qt.RightButton:
            if self.draft_points:
                self.draft_points.pop()
                e.view.viewport().update()
                return HANDLED
            hit_v = self._hit_vertex(e.view, e.pos)
            if hit_v is not None:
                ci, vi = hit_v
                if len(self.contours[ci]["points"]) > 3:
                    self._push_history()
                    self.contours[ci]["points"].pop(vi)
                    self._sync_rect_from_contours()
                    e.view.viewport().update()
                return HANDLED
            return HANDLED

        # Double-click: close active draft, toggle smooth/sharp on vertex, or apply crop.
        if e.kind == "double" and e.button == Qt.LeftButton:
            if len(self.draft_points) >= 3:
                self._commit_draft()
                e.view.viewport().update()
                return HANDLED
            hit_v = self._hit_vertex(e.view, e.pos)
            if hit_v is not None and self.mode in ("bezier", "poly", "lasso"):
                self._toggle_smooth_vertex(hit_v[0], hit_v[1])
                e.view.viewport().update()
                return HANDLED
            self.finish(apply=True)
            return HANDLED

        if e.kind == "press" and e.button == Qt.LeftButton:
            # 1. In rect mode (when not holding Shift/Alt for boolean draw), check 8 resize handles first
            eff_bool = self._effective_boolean(e.modifiers)
            if self.mode == "rect" and eff_bool == "replace" and not self.has_custom_contours:
                for name, point in self.handles_vp(e.view).items():
                    if math.hypot(e.pos.x() - point.x(), e.pos.y() - point.y()) <= HANDLE_HIT:
                        self._push_history()
                        self.op = {"kind": "handle", "handle": name}
                        return HANDLED

            # 2. Check clickable HUD pills
            pill = self._hit_hud_pill(e.pos)
            if pill is not None:
                kind, val = pill
                if kind == "mode":
                    self.set_mode(val)
                elif kind == "bool":
                    self.boolean_op = val
                    self.options.set("boolean_op", val)
                    e.view.viewport().update()
                elif kind == "action":
                    if val == "reset":
                        self._reset_to_full()
                    elif val == "apply":
                        self.finish(apply=True)
                    elif val == "flip_knife":
                        self._flip_last_knife()
                return HANDLED

            # 3. Mode-specific press handling
            if self.mode == "rect":
                if eff_bool != "replace" or self.has_custom_contours:
                    for name, point in self.handles_vp(e.view).items():
                        if eff_bool == "replace" and math.hypot(e.pos.x() - point.x(), e.pos.y() - point.y()) <= HANDLE_HIT:
                            self._push_history()
                            self.has_custom_contours = False
                            self.op = {"kind": "handle", "handle": name}
                            return HANDLED
                    if eff_bool != "replace":
                        self.op = {
                            "kind": "rect_draw",
                            "start": clamped,
                            "current": clamped,
                            "boolean_op": eff_bool,
                        }
                        return HANDLED
                if self.rect.contains(local):
                    self._push_history()
                    self.op = {"kind": "move", "start": local, "rect": QRectF(self.rect)}
                    return HANDLED
                if not full.contains(local):
                    self.finish(apply=True)
                return HANDLED

            if self.mode in ("poly", "bezier"):
                return self._press_poly_or_bezier(e, local, clamped, full, eff_bool)

            if self.mode == "lasso":
                return self._press_lasso(e, local, clamped, full, eff_bool)

            if self.mode == "knife":
                self.knife_flip = False
                self.op = {
                    "kind": "knife",
                    "start": local,
                    "end": local,
                    "base_contours": fast_copy(self.contours),
                }
                e.view.viewport().update()
                return HANDLED

        if e.kind == "move" and self.op is not None:
            self._drag(e, e.view.to_scene(e.pos))
            e.view.viewport().update()
            return HANDLED

        if e.kind == "release" and self.op is not None:
            self._release(e)
            e.view.viewport().update()
            return HANDLED

        return None

    def _press_poly_or_bezier(
        self, e: PointerEvent, local: QPointF, clamped: QPointF, full: QRectF, eff_bool: str
    ) -> str:
        # While building a draft contour:
        if self.draft_points:
            first_vp = self._local_to_vp(e.view, self.draft_points[0])
            if len(self.draft_points) >= 3 and math.hypot(e.pos.x() - first_vp.x(), e.pos.y() - first_vp.y()) <= HANDLE_HIT + 2.0:
                self._commit_draft()
                e.view.viewport().update()
                return HANDLED
            pt = clamped
            if (e.modifiers & Qt.ShiftModifier) and self.draft_points:
                last = self.draft_points[-1]
                pt = _clamp_point(_snap_angle(QPointF(last[0], last[1]), clamped, 45.0), full)
            x, y = round(pt.x(), 2) + 0.0, round(pt.y(), 2) + 0.0
            self.draft_points.append([x, y])
            if self.mode == "bezier":
                self.op = {"kind": "draft_bezier_handle", "index": len(self.draft_points) - 1}
            e.view.viewport().update()
            return HANDLED

        # Not currently in a draft: check existing tangent handles, vertices, center move handle, edges
        if eff_bool == "replace" and self.has_custom_contours:
            hit_t = self._hit_tangent(e.view, e.pos)
            if hit_t is not None:
                self._push_history()
                self.op = {"kind": "move_tangent", "ci": hit_t[0], "vi": hit_t[1], "which": hit_t[2]}
                return HANDLED
            hit_v = self._hit_vertex(e.view, e.pos)
            if hit_v is not None:
                self._push_history()
                self.op = {"kind": "move_vertex", "ci": hit_v[0], "vi": hit_v[1]}
                return HANDLED
            center = self._center_handle_local()
            if center is not None:
                c_vp = self._local_to_vp(e.view, center)
                if math.hypot(e.pos.x() - c_vp.x(), e.pos.y() - c_vp.y()) <= CENTER_HIT:
                    self._push_history()
                    self.op = {
                        "kind": "move_contours",
                        "start": local,
                        "contours": fast_copy(self.contours),
                        "bounds": QRectF(self.rect),
                    }
                    return HANDLED
            hit_e = self._hit_edge(e.view, e.pos, local)
            if hit_e is not None:
                ci, insert_idx, new_pt = hit_e
                self._push_history()
                self.contours[ci]["points"].insert(insert_idx, new_pt)
                self.op = {"kind": "move_vertex", "ci": ci, "vi": insert_idx}
                e.view.viewport().update()
                return HANDLED

        if not full.contains(local):
            self.finish(apply=True)
            return HANDLED

        # Start a new draft polygon/Bezier contour
        self.draft_boolean = eff_bool
        x, y = round(clamped.x(), 2) + 0.0, round(clamped.y(), 2) + 0.0
        self.draft_points = [[x, y]]
        if self.mode == "bezier":
            self.op = {"kind": "draft_bezier_handle", "index": 0}
        e.view.viewport().update()
        return HANDLED

    def _press_lasso(
        self, e: PointerEvent, local: QPointF, clamped: QPointF, full: QRectF, eff_bool: str
    ) -> str:
        if eff_bool == "replace" and self.has_custom_contours:
            hit_v = self._hit_vertex(e.view, e.pos)
            if hit_v is not None:
                self._push_history()
                self.op = {"kind": "move_vertex", "ci": hit_v[0], "vi": hit_v[1]}
                return HANDLED
            center = self._center_handle_local()
            if center is not None:
                c_vp = self._local_to_vp(e.view, center)
                if math.hypot(e.pos.x() - c_vp.x(), e.pos.y() - c_vp.y()) <= CENTER_HIT:
                    self._push_history()
                    self.op = {
                        "kind": "move_contours",
                        "start": local,
                        "contours": fast_copy(self.contours),
                        "bounds": QRectF(self.rect),
                    }
                    return HANDLED
            hit_e = self._hit_edge(e.view, e.pos, local)
            if hit_e is not None:
                ci, insert_idx, new_pt = hit_e
                self._push_history()
                self.contours[ci]["points"].insert(insert_idx, new_pt)
                self.op = {"kind": "move_vertex", "ci": ci, "vi": insert_idx}
                e.view.viewport().update()
                return HANDLED

        if not full.contains(local):
            self.finish(apply=True)
            return HANDLED

        self.op = {
            "kind": "lasso_draw",
            "points": [[round(clamped.x(), 2) + 0.0, round(clamped.y(), 2) + 0.0]],
            "boolean_op": eff_bool,
            "view": e.view,
        }
        e.view.viewport().update()
        return HANDLED

    def _drag(self, e: PointerEvent, scene_pos: QPointF) -> None:
        item = self.item
        if item is None or self.op is None:
            return
        local = item.mapFromScene(scene_pos)
        full = item.full_rect()
        clamped = _clamp_point(local, full)
        op = self.op
        kind = op["kind"]

        if kind == "move":
            rect = QRectF(op["rect"])
            rect.translate(local - op["start"])
            rect.moveLeft(max(full.left(), min(rect.left(), full.right() - rect.width())))
            rect.moveTop(max(full.top(), min(rect.top(), full.bottom() - rect.height())))
            rect.moveTo(round(rect.left()), round(rect.top()))
            self.rect = rect
            self.contours = [{
                "points": crop_geom.rect_to_points(rect.x(), rect.y(), rect.width(), rect.height()),
                "subtract": False,
            }]
        elif kind == "handle":
            x, y = clamped.x(), clamped.y()
            rect = QRectF(self.rect)
            handle = op["handle"]
            minimum = 2.0
            if handle in ("tl", "ml", "bl"):
                rect.setLeft(min(x, rect.right() - minimum))
            if handle in ("tr", "mr", "br"):
                rect.setRight(max(x, rect.left() + minimum))
            if handle in ("tl", "tm", "tr"):
                rect.setTop(min(y, rect.bottom() - minimum))
            if handle in ("bl", "bm", "br"):
                rect.setBottom(max(y, rect.top() + minimum))
            self.rect = QRectF(round(rect.left()), round(rect.top()), round(rect.width()), round(rect.height()))
            self.contours = [{
                "points": crop_geom.rect_to_points(
                    self.rect.x(), self.rect.y(), self.rect.width(), self.rect.height()
                ),
                "subtract": False,
            }]
        elif kind == "rect_draw":
            op["current"] = clamped
            return
        elif kind == "draft_bezier_handle":
            idx = op["index"]
            if 0 <= idx < len(self.draft_points):
                pt = self.draft_points[idx]
                ax, ay = float(pt[0]), float(pt[1])
                ox, oy = round(local.x(), 2) + 0.0, round(local.y(), 2) + 0.0
                if math.hypot(ox - ax, oy - ay) >= 1.5:
                    if e.modifiers & Qt.AltModifier and len(pt) >= 6:
                        ix, iy = pt[2], pt[3]
                    else:
                        ix, iy = round(2.0 * ax - ox, 2) + 0.0, round(2.0 * ay - oy, 2) + 0.0
                    self.draft_points[idx] = [ax, ay, ix, iy, ox, oy]
            return
        elif kind == "move_vertex":
            ci, vi = op["ci"], op["vi"]
            pt = self.contours[ci]["points"][vi]
            nx, ny = round(clamped.x(), 2) + 0.0, round(clamped.y(), 2) + 0.0
            if len(pt) >= 6:
                dx, dy = nx - pt[0], ny - pt[1]
                self.contours[ci]["points"][vi] = [
                    nx, ny,
                    round(pt[2] + dx, 2) + 0.0, round(pt[3] + dy, 2) + 0.0,
                    round(pt[4] + dx, 2) + 0.0, round(pt[5] + dy, 2) + 0.0,
                ]
            else:
                self.contours[ci]["points"][vi] = [nx, ny]
            self._sync_rect_from_contours()
        elif kind == "move_tangent":
            ci, vi, which = op["ci"], op["vi"], op["which"]
            pt = list(self.contours[ci]["points"][vi])
            ax, ay = float(pt[0]), float(pt[1])
            hx, hy = round(local.x(), 2) + 0.0, round(local.y(), 2) + 0.0
            symmetric = not bool(e.modifiers & Qt.AltModifier)
            if which == "in":
                pt[2], pt[3] = hx, hy
                if symmetric:
                    pt[4], pt[5] = round(2.0 * ax - hx, 2) + 0.0, round(2.0 * ay - hy, 2) + 0.0
            else:
                pt[4], pt[5] = hx, hy
                if symmetric:
                    pt[2], pt[3] = round(2.0 * ax - hx, 2) + 0.0, round(2.0 * ay - hy, 2) + 0.0
            self.contours[ci]["points"][vi] = pt
            self._sync_rect_from_contours()
        elif kind == "move_contours":
            delta = local - op["start"]
            bounds: QRectF = op["bounds"]
            dx = max(full.left() - bounds.left(), min(delta.x(), full.right() - bounds.right()))
            dy = max(full.top() - bounds.top(), min(delta.y(), full.bottom() - bounds.bottom()))
            moved: list[dict[str, Any]] = []
            for c in op["contours"]:
                new_pts: list[list[float]] = []
                for p in c["points"]:
                    if len(p) >= 6:
                        new_pts.append([
                            round(p[0] + dx, 2) + 0.0, round(p[1] + dy, 2) + 0.0,
                            round(p[2] + dx, 2) + 0.0, round(p[3] + dy, 2) + 0.0,
                            round(p[4] + dx, 2) + 0.0, round(p[5] + dy, 2) + 0.0,
                        ])
                    else:
                        new_pts.append([round(p[0] + dx, 2) + 0.0, round(p[1] + dy, 2) + 0.0])
                moved.append({"points": new_pts, "subtract": c.get("subtract", False)})
            self.contours = moved
            self._sync_rect_from_contours()
        elif kind == "lasso_draw":
            pts = op["points"]
            nx, ny = round(clamped.x(), 2) + 0.0, round(clamped.y(), 2) + 0.0
            if not pts or math.hypot(nx - pts[-1][0], ny - pts[-1][1]) >= 1.0:
                pts.append([nx, ny])
            return
        elif kind == "knife":
            end_pt = _snap_angle(op["start"], local, 15.0) if (e.modifiers & Qt.ShiftModifier) else local
            op["end"] = end_pt
            return

        self.ui.status(f"Crop {self.rect.width():.0f} × {self.rect.height():.0f}px — Enter to apply, Esc to cancel")

    def _release(self, e: PointerEvent) -> None:
        op, self.op = self.op, None
        if op is None or self.item is None:
            return
        kind = op["kind"]
        if kind == "rect_draw":
            r = QRectF(op["start"], op["current"]).normalized().intersected(self.item.full_rect())
            if r.width() >= 2.0 and r.height() >= 2.0:
                pts = crop_geom.rect_to_points(round(r.x()), round(r.y()), round(r.width()), round(r.height()))
                self._apply_contour_op(pts, op["boolean_op"])
        elif kind == "lasso_draw":
            raw = op["points"]
            if len(raw) >= 3:
                st = self.item.sceneTransform()
                item_scale = max(1e-6, math.hypot(st.m11(), st.m12()) * e.view.zoom_level())
                tol_local = float(self.options.get("lasso_simplify") or 2.0) / item_scale
                simplified = crop_geom.simplify_polygon(raw, tolerance=max(0.25, tol_local))
                if len(simplified) >= 3 and crop_geom.polygon_area(simplified) >= 2.0:
                    self._apply_contour_op(simplified, op["boolean_op"])
        elif kind == "knife":
            start, end = op["start"], op["end"]
            if math.hypot(end.x() - start.x(), end.y() - start.y()) >= 3.0:
                base = op["base_contours"]
                cut, _ = crop_geom.knife_cut_contours(
                    base, start.x(), start.y(), end.x(), end.y(), flip=self.knife_flip
                )
                self._push_history()
                self.contours = cut
                self.has_custom_contours = True
                self.last_knife = {
                    "base": base,
                    "start": (start.x(), start.y()),
                    "end": (end.x(), end.y()),
                }
                self._sync_rect_from_contours()
                self.ui.status("Knife cut applied · Press Tab to flip kept side · Enter to apply")

    def _flip_last_knife(self) -> bool:
        if self.op is not None and self.op.get("kind") == "knife":
            self.knife_flip = not self.knife_flip
            self.app.update_views()
            return True
        if self.last_knife is not None:
            self.knife_flip = not self.knife_flip
            ax, ay = self.last_knife["start"]
            bx, by = self.last_knife["end"]
            cut, _ = crop_geom.knife_cut_contours(
                self.last_knife["base"], ax, ay, bx, by, flip=self.knife_flip
            )
            self.contours = cut
            self._sync_rect_from_contours()
            self.app.update_views()
            return True
        return False

    def hover(self, e: PointerEvent) -> None:
        if self.item is not None:
            self.hover_local = self.item.mapFromScene(e.scene_pos)
            self.hover_vp = QPointF(e.pos)
            if self.draft_points:
                e.view.viewport().update()

        if self._hit_hud_pill(e.pos) is not None:
            e.set_cursor(Qt.PointingHandCursor)
            return

        if self.mode == "rect" and not self.has_custom_contours:
            handles = self.handles_vp(e.view)
            for point in handles.values():
                if math.hypot(e.pos.x() - point.x(), e.pos.y() - point.y()) <= HANDLE_HIT:
                    middle = (handles["tl"] + handles["br"]) / 2.0
                    e.set_cursor(resize_cursor_for(point - middle))
                    return
            e.set_cursor(Qt.SizeAllCursor if self._in_rect(e.view, e.pos) else Qt.ArrowCursor)
            return

        if self.has_custom_contours and not self.draft_points:
            if self._hit_tangent(e.view, e.pos) is not None or self._hit_vertex(e.view, e.pos) is not None:
                e.set_cursor(Qt.CrossCursor)
                return
            center = self._center_handle_local()
            if center is not None and self.item is not None:
                c_vp = self._local_to_vp(e.view, center)
                if math.hypot(e.pos.x() - c_vp.x(), e.pos.y() - c_vp.y()) <= CENTER_HIT:
                    e.set_cursor(Qt.SizeAllCursor)
                    return
        e.set_cursor(Qt.CrossCursor)

    def key(self, e: KeyEvent) -> str | None:
        if e.kind != "press":
            return None
        if e.key in (Qt.Key_Return, Qt.Key_Enter):
            self.finish(apply=True)
            return HANDLED
        if e.key in (Qt.Key_Backspace, Qt.Key_Delete):
            if self.draft_points:
                self.draft_points.pop()
                self.app.update_views()
                return HANDLED
            return None
        if e.key in (Qt.Key_Tab, Qt.Key_X) and self.mode == "knife":
            if self._flip_last_knife():
                return HANDLED
        if e.modifiers & Qt.ControlModifier:
            if e.key == Qt.Key_Z:
                if (e.modifiers & Qt.ShiftModifier) and self._redo_internal():
                    return HANDLED
                if not (e.modifiers & Qt.ShiftModifier) and self._undo_internal():
                    return HANDLED
            elif e.key == Qt.Key_Y and self._redo_internal():
                return HANDLED
        if not (e.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            mode_keys = {
                Qt.Key_1: "rect", Qt.Key_R: "rect",
                Qt.Key_2: "poly", Qt.Key_P: "poly",
                Qt.Key_3: "lasso", Qt.Key_L: "lasso",
                Qt.Key_4: "bezier", Qt.Key_B: "bezier",
                Qt.Key_5: "knife", Qt.Key_K: "knife",
            }
            if e.key in mode_keys:
                self.set_mode(mode_keys[e.key])
                return HANDLED
        return None

    # ---- Overlay Painting ------------------------------------------------------------------

    def _active_crop_vp_path(self, view: Any) -> QPainterPath:
        item = self.item
        st = item.sceneTransform()
        full = item.full_rect()

        # Preview knife cut live while dragging
        if self.op is not None and self.op.get("kind") == "knife":
            start, end = self.op["start"], self.op["end"]
            if math.hypot(end.x() - start.x(), end.y() - start.y()) >= 2.0:
                preview_contours, _ = crop_geom.knife_cut_contours(
                    self.op["base_contours"], start.x(), start.y(), end.x(), end.y(), flip=self.knife_flip
                )
                local_path = crop_geom.contours_to_qpath(preview_contours, full_rect=full)
                return view.viewportTransform().map(st.map(local_path))

        if not self.has_custom_contours and self.mode == "rect":
            handles = self.handles_vp(view)
            crop_poly = QPolygonF([handles[name] for name in CORNERS])
            crop_path = QPainterPath()
            crop_path.addPolygon(crop_poly)
            crop_path.closeSubpath()
            return crop_path

        local_path = crop_geom.contours_to_qpath(self.contours, full_rect=full)
        return view.viewportTransform().map(st.map(local_path))

    def paint_overlay(self, view: Any, painter: QPainter) -> None:
        item = self.item
        if item is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        st = item.sceneTransform()
        full = item.full_rect()
        full_path = QPainterPath()
        full_path.addPolygon(QPolygonF([view.to_viewport(st.map(p)) for p in (
            full.topLeft(), full.topRight(), full.bottomRight(), full.bottomLeft(),
        )]))
        full_path.closeSubpath()

        crop_path = self._active_crop_vp_path(view)
        painter.fillPath(full_path.subtracted(crop_path), QColor(0, 0, 0, 160))

        # Active mask outline
        painter.setPen(QPen(theme.ACCENT, 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(crop_path)

        if self.mode == "rect" and not self.has_custom_contours:
            handles = self.handles_vp(view)
            painter.setPen(QPen(QColor(255, 255, 255, 70), 1.0))  # rule-of-thirds guides
            tl, tr, br, bl = (handles[name] for name in CORNERS)
            for t in (1.0 / 3.0, 2.0 / 3.0):
                painter.drawLine(tl + (tr - tl) * t, bl + (br - bl) * t)
                painter.drawLine(tl + (bl - tl) * t, tr + (br - tr) * t)
            for point in handles.values():
                paint.paint_handle(painter, point)
        else:
            self._paint_contours_handles(view, painter)

        self._paint_active_operation(view, painter)
        self._paint_hud(view, painter)
        painter.restore()

    def _paint_contours_handles(self, view: Any, painter: QPainter) -> None:
        if not self.has_custom_contours:
            return
        for contour in self.contours:
            sub = contour.get("subtract", False)
            accent = QColor("#e06c75") if sub else theme.ACCENT
            for pt in contour.get("points", ()):
                anchor_vp = self._local_to_vp(view, pt)
                if len(pt) >= 6:
                    in_vp = self._local_to_vp(view, (pt[2], pt[3]))
                    out_vp = self._local_to_vp(view, (pt[4], pt[5]))
                    painter.setPen(QPen(QColor(255, 255, 255, 150), 1.0, Qt.DashLine))
                    painter.drawLine(anchor_vp, in_vp)
                    painter.drawLine(anchor_vp, out_vp)
                    painter.setPen(QPen(accent, 1.2))
                    painter.setBrush(QColor("#282c34"))
                    painter.drawEllipse(in_vp, 3.5, 3.5)
                    painter.drawEllipse(out_vp, 3.5, 3.5)
                paint.paint_handle(painter, anchor_vp, size=7.0, accent=accent)

        center = self._center_handle_local()
        if center is not None and not self.draft_points:
            c_vp = self._local_to_vp(view, center)
            painter.setPen(QPen(theme.ACCENT, 1.4))
            painter.setBrush(QColor(30, 34, 42, 220))
            painter.drawEllipse(c_vp, 6.0, 6.0)
            painter.drawLine(QPointF(c_vp.x() - 3.5, c_vp.y()), QPointF(c_vp.x() + 3.5, c_vp.y()))
            painter.drawLine(QPointF(c_vp.x(), c_vp.y() - 3.5), QPointF(c_vp.x(), c_vp.y() + 3.5))

    def _paint_active_operation(self, view: Any, painter: QPainter) -> None:
        # Draft polygon / Bezier path
        if self.draft_points:
            color = QColor("#e06c75") if self.draft_boolean == "subtract" else QColor("#61afef")
            path = QPainterPath()
            first_vp = self._local_to_vp(view, self.draft_points[0])
            path.moveTo(first_vp)
            for i in range(len(self.draft_points) - 1):
                p0 = self.draft_points[i]
                p1 = self.draft_points[i + 1]
                v1 = self._local_to_vp(view, p1)
                if len(p0) == 2 and len(p1) == 2:
                    path.lineTo(v1)
                else:
                    c1 = self._local_to_vp(view, (p0[4], p0[5]) if len(p0) >= 6 else p0)
                    c2 = self._local_to_vp(view, (p1[2], p1[3]) if len(p1) >= 6 else p1)
                    path.cubicTo(c1, c2, v1)
            painter.setPen(QPen(color, 1.8))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

            if self.hover_vp is not None:
                last_vp = self._local_to_vp(view, self.draft_points[-1])
                painter.setPen(QPen(color, 1.0, Qt.DashLine))
                painter.drawLine(last_vp, self.hover_vp)
                if len(self.draft_points) >= 3:
                    painter.setPen(QPen(QColor(255, 255, 255, 90), 1.0, Qt.DotLine))
                    painter.drawLine(self.hover_vp, first_vp)

            for i, pt in enumerate(self.draft_points):
                v_vp = self._local_to_vp(view, pt)
                if len(pt) >= 6:
                    in_vp = self._local_to_vp(view, (pt[2], pt[3]))
                    out_vp = self._local_to_vp(view, (pt[4], pt[5]))
                    painter.setPen(QPen(QColor(255, 255, 255, 140), 1.0, Qt.DashLine))
                    painter.drawLine(v_vp, in_vp)
                    painter.drawLine(v_vp, out_vp)
                    painter.setBrush(color)
                    painter.drawEllipse(in_vp, 3.0, 3.0)
                    painter.drawEllipse(out_vp, 3.0, 3.0)
                paint.paint_handle(painter, v_vp, size=9.0 if i == 0 else 7.0, accent=color)

        op = self.op
        if op is None:
            return
        kind = op["kind"]
        if kind == "rect_draw":
            a = self._local_to_vp(view, op["start"])
            b = self._local_to_vp(view, op["current"])
            r = QRectF(a, b).normalized()
            color = QColor("#e06c75") if op["boolean_op"] == "subtract" else QColor("#98c379")
            painter.setPen(QPen(color, 1.5, Qt.DashLine))
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 45))
            painter.drawRect(r)
        elif kind == "lasso_draw" and len(op["points"]) >= 2:
            pts_vp = [self._local_to_vp(view, p) for p in op["points"]]
            poly = QPolygonF(pts_vp)
            color = QColor("#e06c75") if op["boolean_op"] == "subtract" else QColor("#61afef")
            painter.setPen(QPen(color, 1.8))
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 40))
            painter.drawPolygon(poly)
        elif kind == "knife":
            a = self._local_to_vp(view, op["start"])
            b = self._local_to_vp(view, op["end"])
            painter.setPen(QPen(QColor("#e5c07b"), 2.0, Qt.DashLine))
            painter.drawLine(a, b)
            paint.paint_handle(painter, a, size=7.0, accent=QColor("#e5c07b"))
            paint.paint_handle(painter, b, size=7.0, accent=QColor("#e5c07b"))

    def _paint_hud(self, view: Any, painter: QPainter) -> None:
        self._hud_pills.clear()
        vw = float(view.viewport().width())
        metrics = painter.fontMetrics()
        pill_h = metrics.height() + 10.0
        pad_x = 9.0
        gap = 4.0
        sep_gap = 10.0

        items: list[tuple[str, str, str, bool]] = []
        for m in MODES:
            items.append(("mode", m, MODE_LABELS[m], self.mode == m))
        items.append(("sep", "", "", False))
        if self.mode == "knife":
            items.append(("action", "flip_knife", "⇄ Flip (Tab)", self.knife_flip))
        else:
            for b in BOOL_OPS:
                items.append(("bool", b, BOOL_LABELS[b], self.boolean_op == b))
        items.append(("sep", "", "", False))
        items.append(("action", "reset", "Reset", False))
        items.append(("action", "apply", "Apply ↵", True))

        widths: list[float] = []
        for kind, _val, label, _active in items:
            if kind == "sep":
                widths.append(sep_gap)
            else:
                widths.append(metrics.horizontalAdvance(label) + pad_x * 2.0)

        total_w = sum(widths) + gap * max(0, len(items) - 1)
        bar_pad = 8.0
        bar_rect = QRectF((vw - total_w) / 2.0 - bar_pad, 8.0, total_w + bar_pad * 2.0, pill_h + 8.0)
        painter.setPen(QPen(QColor(255, 255, 255, 35), 1.0))
        painter.setBrush(theme.PANEL_BG)
        painter.drawRoundedRect(bar_rect, 7.0, 7.0)

        x = (vw - total_w) / 2.0
        y = 12.0
        for (kind, val, label, active), w_px in zip(items, widths):
            if kind == "sep":
                sx = x + w_px / 2.0
                painter.setPen(QPen(QColor(255, 255, 255, 45), 1.0))
                painter.drawLine(QPointF(sx, y + 3.0), QPointF(sx, y + pill_h - 3.0))
                x += w_px + gap
                continue
            r = QRectF(x, y, w_px, pill_h)
            self._hud_pills.append((r, kind, val))
            if active:
                fill = QColor("#e06c75") if (kind == "bool" and val == "subtract") else theme.ACCENT
                painter.setPen(Qt.NoPen)
                painter.setBrush(fill)
                painter.drawRoundedRect(r, 4.0, 4.0)
                painter.setPen(QColor("#ffffff"))
            else:
                painter.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
                painter.setBrush(QColor(255, 255, 255, 12))
                painter.drawRoundedRect(r, 4.0, 4.0)
                painter.setPen(theme.TEXT_LIGHT)
            painter.drawText(r, Qt.AlignCenter, label)
            x += w_px + gap


class CropRows(QWidget):
    def __init__(self, ui: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self.crop_btn = w.make_tool_button("Crop", "Crop / Mask (C)", width=48, parent=self)
        self.reset_crop_btn = w.make_tool_button("Reset crop", "Reset crop (Shift+C)", width=74, parent=self)
        self.crop_btn.clicked.connect(lambda: ui.app.trigger("crop.start"))
        self.reset_crop_btn.clicked.connect(lambda: ui.app.trigger("crop.reset"))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        layout.addWidget(self.crop_btn)
        layout.addWidget(self.reset_crop_btn)
        layout.addStretch()

    def refresh(self, info: SelectionInfo) -> None:
        self.reset_crop_btn.setEnabled(bool(info.attrs.get("crop")))


def activate_crop_ui(ui: Any) -> None:
    tool = CropTool(ui)
    ui.register_tool(tool)
    session = ui.session

    def start_crop(mode: str | None = None) -> None:
        """Crop the selected image (one unlocked image)."""
        from pixelref.plugin_api import CommandError

        # Flush first: a pending refresh may replace the image's item.
        ui.canvas.flush_pending()
        images = [ui.canvas.item(n) for n in session.selection.names
                  if ui.canvas.nodes.get(n, {}).get("type") == "image"]
        images = [i for i in images if i is not None and not i.locked]
        if len(images) != 1:
            raise CommandError("Select a single unlocked image to crop")
        ui.tools.activate("crop", item=images[0], mode=mode)

    def flip_knife() -> None:
        """Flip which side of the last knife cut is kept."""
        tool._flip_last_knife()

    ui.command("start", start_crop, undoable=False)
    ui.command("flip_knife", flip_knife, undoable=False)
    ui.action("crop.start", "crop.start", "Crop", keywords="trim cut mask lasso bezier knife polygon image",
              category="Transform", icon="crop")
    ui.action("crop.reset", "crop.reset", "Reset Crop", keywords="uncrop restore image", category="Transform",
              icon="reset")
    ui.bind("C", "crop.start")
    ui.bind("Shift+C", "crop.reset")
    ui.bind("Tab", "crop.flip_knife", scope="canvas",
            when=lambda: ui.tools.active_id == "crop" and tool.mode == "knife")

    def has_image() -> bool:
        return any(ui.canvas.nodes.get(n, {}).get("type") == "image" for n in session.selection.names)

    ui.menu("canvas", "crop.start", path="Image", group="image", order=0, when=has_image)
    ui.menu("canvas", "crop.reset", path="Image", group="image", order=1, when=has_image)
    ui.contribute("inspector.rows", InspectorRows("image", 0, lambda parent, _i: CropRows(ui, parent)))
