"""Crop geometry: rectangular and free-form (polygon, lasso, Bezier, boolean contours, knife cuts).

A node's ``crop`` attribute in source pixels can be:

* ``None`` — uncropped (full image).
* ``[x, y, w, h]`` — axis-aligned rectangular crop (4 numbers).
* ``[[x, y], ...]`` — a single closed polygon or Bezier contour (>= 3 vertices).
  Each vertex is ``[x, y]`` (sharp corner) or ``[x, y, ix, iy, ox, oy]`` (cubic
  Bezier anchor with incoming and outgoing control points in source pixels).
* ``{"contours": [{"points": [...], "subtract": False}, ...]}`` — one or more
  contours combined with boolean add (``subtract: False``) and subtract
  (``subtract: True``).

This module is pure Python at import time; Qt ``QPainterPath`` helpers import
``PySide6`` lazily when called from the UI.
"""

from __future__ import annotations

import math
from typing import Any

from .errors import SceneError


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def is_rect_crop(crop: Any) -> bool:
    """True when ``crop`` is the classic 4-number ``[x, y, w, h]`` rectangle."""
    return (
        isinstance(crop, (list, tuple))
        and len(crop) == 4
        and all(_is_num(v) for v in crop)
    )


def is_freeform_crop(crop: Any) -> bool:
    """True when ``crop`` is a non-rectangular polygon, Bezier or multi-contour mask."""
    return crop is not None and not is_rect_crop(crop)


def _validate_vertex(pt: Any) -> list[float]:
    if not isinstance(pt, (list, tuple)) or len(pt) not in (2, 6):
        raise SceneError(f"Crop vertex expects [x, y] or [x, y, ix, iy, ox, oy], got {pt!r}")
    out: list[float] = []
    for c in pt:
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            try:
                c = float(c)
            except (TypeError, ValueError):
                raise SceneError(f"Crop vertex expects numbers, got {pt!r}") from None
        val = float(c)
        if not math.isfinite(val):
            raise SceneError(f"Crop vertex expects finite numbers, got {pt!r}")
        out.append(round(val, 3) + 0.0)
    # Collapse degenerate Bezier handles that sit on the anchor back to [x, y].
    if len(out) == 6:
        x, y, ix, iy, ox, oy = out
        if math.hypot(ix - x, iy - y) < 1e-3 and math.hypot(ox - x, oy - y) < 1e-3:
            return [x, y]
    return out


def _validate_points(points: Any) -> list[list[float]]:
    if not isinstance(points, (list, tuple)) or len(points) < 3:
        raise SceneError(f"Crop contour needs at least 3 points, got {points!r}")
    return [_validate_vertex(pt) for pt in points]


def validate_crop(value: Any) -> Any:
    """Validate and canonicalize a ``crop`` attribute value."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        if len(value) == 4 and all(
            not isinstance(v, (list, tuple, dict, bool)) for v in value
        ):
            out: list[float] = []
            for v in value:
                try:
                    num = float(v)
                except (TypeError, ValueError):
                    raise SceneError(f"Attribute 'crop' expects 4 numbers, got {value!r}") from None
                if not math.isfinite(num):
                    raise SceneError(f"Attribute 'crop' expects finite numbers, got {value!r}")
                out.append(num)
            return out
        return _validate_points(value)
    if isinstance(value, dict):
        raw_contours = value.get("contours")
        if not isinstance(raw_contours, (list, tuple)) or not raw_contours:
            raise SceneError(f"Attribute 'crop' dict must contain non-empty 'contours', got {value!r}")
        contours: list[dict[str, Any]] = []
        for entry in raw_contours:
            if not isinstance(entry, dict) or "points" not in entry:
                raise SceneError(f"Crop contour expects dict with 'points', got {entry!r}")
            pts = _validate_points(entry["points"])
            sub = bool(entry.get("subtract", False))
            contours.append({"points": pts, "subtract": sub} if sub else {"points": pts})
        if len(contours) == 1 and not contours[0].get("subtract", False):
            return contours[0]["points"]
        return {"contours": contours}
    raise SceneError(f"Attribute 'crop' expects [x, y, w, h], points or contours dict, got {value!r}")


def rect_to_points(x: float, y: float, w: float, h: float) -> list[list[float]]:
    """Four corner vertices ``[[x, y], ...]`` for an axis-aligned rectangle."""
    x0, y0 = round(float(x), 3) + 0.0, round(float(y), 3) + 0.0
    x1, y1 = round(float(x + w), 3) + 0.0, round(float(y + h), 3) + 0.0
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def crop_contours(crop: Any) -> list[dict[str, Any]]:
    """Uniform list of ``{'points': [[...], ...], 'subtract': bool}`` for any valid crop."""
    if crop is None:
        return []
    if is_rect_crop(crop):
        x, y, w, h = float(crop[0]), float(crop[1]), float(crop[2]), float(crop[3])
        return [{"points": rect_to_points(x, y, w, h), "subtract": False}]
    if isinstance(crop, (list, tuple)):
        if len(crop) >= 3 and isinstance(crop[0], (list, tuple)):
            return [{"points": [list(pt) for pt in crop], "subtract": False}]
        return []
    if isinstance(crop, dict) and isinstance(crop.get("contours"), (list, tuple)):
        result: list[dict[str, Any]] = []
        for c in crop["contours"]:
            if isinstance(c, dict) and isinstance(c.get("points"), (list, tuple)) and len(c["points"]) >= 3:
                result.append({
                    "points": [list(pt) for pt in c["points"]],
                    "subtract": bool(c.get("subtract", False)),
                })
        return result
    return []


def serialize_contours(contours: list[dict[str, Any]], full_size: tuple[float, float] | None = None) -> Any:
    """Turn edited contours back into the compact canonical ``crop`` attribute value."""
    valid = [
        {"points": _validate_points(c["points"]), "subtract": bool(c.get("subtract", False))}
        for c in contours
        if len(c.get("points", ())) >= 3
    ]
    if not valid:
        return None
    if len(valid) == 1 and not valid[0]["subtract"]:
        pts = valid[0]["points"]
        if full_size is not None and _is_full_rect_points(pts, full_size[0], full_size[1]):
            return None
        return pts
    out = [{"points": c["points"], "subtract": True} if c["subtract"] else {"points": c["points"]} for c in valid]
    return {"contours": out}


def _is_full_rect_points(pts: list[list[float]], w: float, h: float, tol: float = 0.5) -> bool:
    if len(pts) != 4 or any(len(p) != 2 for p in pts):
        return False
    xs = sorted(p[0] for p in pts)
    ys = sorted(p[1] for p in pts)
    return (
        abs(xs[0]) <= tol and abs(xs[1]) <= tol
        and abs(xs[2] - w) <= tol and abs(xs[3] - w) <= tol
        and abs(ys[0]) <= tol and abs(ys[1]) <= tol
        and abs(ys[2] - h) <= tol and abs(ys[3] - h) <= tol
    )


# ---- Pure-Python Bezier & Bounding Box ---------------------------------------------------------


def eval_cubic(p0: float, c1: float, c2: float, p3: float, t: float) -> float:
    mt = 1.0 - t
    return mt * mt * mt * p0 + 3.0 * mt * mt * t * c1 + 3.0 * mt * t * t * c2 + t * t * t * p3


def _cubic_extrema_1d(p0: float, c1: float, c2: float, p3: float) -> list[float]:
    vals = [p0, p3]
    a = 3.0 * (-p0 + 3.0 * c1 - 3.0 * c2 + p3)
    b = 6.0 * (p0 - 2.0 * c1 + c2)
    c = 3.0 * (c1 - p0)
    if abs(a) < 1e-12:
        if abs(b) > 1e-12:
            t = -c / b
            if 0.0 < t < 1.0:
                vals.append(eval_cubic(p0, c1, c2, p3, t))
        return vals
    disc = b * b - 4.0 * a * c
    if disc < 0.0:
        return vals
    sqrt_d = math.sqrt(disc)
    for t in ((-b - sqrt_d) / (2.0 * a), (-b + sqrt_d) / (2.0 * a)):
        if 0.0 < t < 1.0:
            vals.append(eval_cubic(p0, c1, c2, p3, t))
    return vals


def _contour_extents(points: list[list[float]]) -> tuple[float, float, float, float] | None:
    if len(points) < 3:
        return None
    xs: list[float] = []
    ys: list[float] = []
    n = len(points)
    for i in range(n):
        p0 = points[i]
        p1 = points[(i + 1) % n]
        if len(p0) == 2 and len(p1) == 2:
            xs.append(float(p0[0]))
            ys.append(float(p0[1]))
        else:
            x0, y0 = float(p0[0]), float(p0[1])
            ox0, oy0 = (float(p0[4]), float(p0[5])) if len(p0) >= 6 else (x0, y0)
            x1, y1 = float(p1[0]), float(p1[1])
            ix1, iy1 = (float(p1[2]), float(p1[3])) if len(p1) >= 6 else (x1, y1)
            xs.extend(_cubic_extrema_1d(x0, ox0, ix1, x1))
            ys.extend(_cubic_extrema_1d(y0, oy0, iy1, y1))
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def crop_bounds(crop: Any, full_size: tuple[float, float] | None = None) -> list[float] | None:
    """Tight ``[x, y, w, h]`` bounding box in source pixels (pure Python, no Qt needed)."""
    if crop is None:
        return None
    if is_rect_crop(crop):
        x, y, w, h = float(crop[0]), float(crop[1]), float(crop[2]), float(crop[3])
        if full_size is not None:
            fw, fh = float(full_size[0]), float(full_size[1])
            x0, y0 = max(0.0, x), max(0.0, y)
            x1, y1 = min(fw, x + w), min(fh, y + h)
            w, h = x1 - x0, y1 - y0
            x, y = x0, y0
        return [x, y, w, h] if w >= 1.0 and h >= 1.0 else None

    contours = crop_contours(crop)
    if not contours:
        return None
    positive = [c for c in contours if not c.get("subtract", False)] or contours
    extents = [_contour_extents(c["points"]) for c in positive]
    valid = [e for e in extents if e is not None]
    if not valid:
        return None
    x0 = min(e[0] for e in valid)
    y0 = min(e[1] for e in valid)
    x1 = max(e[2] for e in valid)
    y1 = max(e[3] for e in valid)
    if full_size is not None:
        fw, fh = float(full_size[0]), float(full_size[1])
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(fw, x1), min(fh, y1)
    if x1 - x0 < 1.0 or y1 - y0 < 1.0:
        return None
    return [x0, y0, x1 - x0, y1 - y0]


# ---- Polyline Simplification (Ramer-Douglas-Peucker) & Flattening ------------------------------


def _point_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    len_sq = dx * dx + dy * dy
    if len_sq < 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / len_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _rdp(pts: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    if len(pts) <= 2:
        return list(pts)
    ax, ay = pts[0]
    bx, by = pts[-1]
    max_d = -1.0
    index = 0
    for i in range(1, len(pts) - 1):
        d = _point_segment_distance(pts[i][0], pts[i][1], ax, ay, bx, by)
        if d > max_d:
            max_d = d
            index = i
    if max_d > eps:
        left = _rdp(pts[: index + 1], eps)
        right = _rdp(pts[index:], eps)
        return left[:-1] + right
    return [pts[0], pts[-1]]


def simplify_polygon(raw_points: list[Any], tolerance: float = 2.0) -> list[list[float]]:
    """Simplify a freehand lasso stroke into a clean closed polygon of ``[x, y]`` vertices."""
    cleaned: list[tuple[float, float]] = []
    for p in raw_points:
        x, y = (float(p[0]), float(p[1])) if isinstance(p, (list, tuple)) else (float(p.x()), float(p.y()))
        if not cleaned or math.hypot(x - cleaned[-1][0], y - cleaned[-1][1]) >= 0.5:
            cleaned.append((x, y))
    if len(cleaned) > 1 and math.hypot(cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]) < 1.0:
        cleaned.pop()
    if len(cleaned) < 3:
        return [[round(x, 2) + 0.0, round(y, 2) + 0.0] for x, y in cleaned]

    # Split at the pair of points farthest from the start so closed loops don't collapse at the seam.
    x0, y0 = cleaned[0]
    far_idx = max(range(1, len(cleaned)), key=lambda i: math.hypot(cleaned[i][0] - x0, cleaned[i][1] - y0))
    half1 = _rdp(cleaned[: far_idx + 1], max(0.25, tolerance))
    half2 = _rdp(cleaned[far_idx:] + [cleaned[0]], max(0.25, tolerance))
    merged = half1[:-1] + half2[:-1]
    if len(merged) < 3:
        # Fallback if tolerance was too aggressive for a small shape
        step = max(1, len(cleaned) // 3)
        merged = [cleaned[0], cleaned[min(step, len(cleaned) - 1)], cleaned[min(step * 2, len(cleaned) - 1)]]
    return [[round(x, 2) + 0.0, round(y, 2) + 0.0] for x, y in merged]


def flatten_contour(points: list[list[float]], steps_per_curve: int = 20) -> list[tuple[float, float]]:
    """Sample a contour (with optional cubic Bezier segments) into a flat polygon."""
    out: list[tuple[float, float]] = []
    n = len(points)
    for i in range(n):
        p0 = points[i]
        p1 = points[(i + 1) % n]
        x0, y0 = float(p0[0]), float(p0[1])
        if len(p0) == 2 and len(p1) == 2:
            out.append((x0, y0))
        else:
            ox0, oy0 = (float(p0[4]), float(p0[5])) if len(p0) >= 6 else (x0, y0)
            x1, y1 = float(p1[0]), float(p1[1])
            ix1, iy1 = (float(p1[2]), float(p1[3])) if len(p1) >= 6 else (x1, y1)
            for s in range(steps_per_curve):
                t = s / steps_per_curve
                out.append((eval_cubic(x0, ox0, ix1, x1, t), eval_cubic(y0, oy0, iy1, y1, t)))
    return out


def polygon_area(points: list[tuple[float, float]] | list[list[float]]) -> float:
    """Unsigned area of a 2D polygon using the shoelace formula."""
    n = len(points)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x0, y0 = float(points[i][0]), float(points[i][1])
        x1, y1 = float(points[(i + 1) % n][0]), float(points[(i + 1) % n][1])
        total += x0 * y1 - x1 * y0
    return abs(total) * 0.5


# ---- Sutherland-Hodgman Half-Plane Clipping (Knife Line) ---------------------------------------


def clip_polygon_halfplane(
    poly: list[tuple[float, float]],
    ax: float,
    ay: float,
    bx: float,
    by: float,
    keep_sign: float = 1.0,
) -> list[list[float]]:
    """Clip a polygon against the line A -> B, keeping points where cross(B - A, P - A) * keep_sign >= 0."""
    dx, dy = bx - ax, by - ay
    if math.hypot(dx, dy) < 1e-6 or len(poly) < 3:
        return [[round(x, 2) + 0.0, round(y, 2) + 0.0] for x, y in poly]

    def signed_dist(px: float, py: float) -> float:
        return (dx * (py - ay) - dy * (px - ax)) * keep_sign

    output: list[tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        s_x, s_y = poly[i]
        e_x, e_y = poly[(i + 1) % n]
        s_in = signed_dist(s_x, s_y) >= -1e-6
        e_in = signed_dist(e_x, e_y) >= -1e-6
        if s_in and e_in:
            output.append((e_x, e_y))
        elif s_in and not e_in:
            sd = signed_dist(s_x, s_y)
            ed = signed_dist(e_x, e_y)
            denom = sd - ed
            t = sd / denom if abs(denom) > 1e-12 else 0.0
            output.append((s_x + t * (e_x - s_x), s_y + t * (e_y - s_y)))
        elif not s_in and e_in:
            sd = signed_dist(s_x, s_y)
            ed = signed_dist(e_x, e_y)
            denom = sd - ed
            t = sd / denom if abs(denom) > 1e-12 else 0.0
            output.append((s_x + t * (e_x - s_x), s_y + t * (e_y - s_y)))
            output.append((e_x, e_y))

    dedup: list[list[float]] = []
    for x, y in output:
        rx, ry = round(x, 2) + 0.0, round(y, 2) + 0.0
        if not dedup or math.hypot(rx - dedup[-1][0], ry - dedup[-1][1]) > 0.05:
            dedup.append([rx, ry])
    if len(dedup) > 1 and math.hypot(dedup[0][0] - dedup[-1][0], dedup[0][1] - dedup[-1][1]) <= 0.05:
        dedup.pop()
    return dedup if len(dedup) >= 3 else []


def knife_cut_contours(
    contours: list[dict[str, Any]],
    ax: float,
    ay: float,
    bx: float,
    by: float,
    flip: bool = False,
) -> tuple[list[dict[str, Any]], float]:
    """Slice ``contours`` with line A -> B.

    By default keeps the side with the larger remaining area (or flips to the
    opposite side when ``flip=True``). Returns ``(new_contours, chosen_sign)``.
    """
    positive = [c for c in contours if not c.get("subtract", False)]
    holes = [c for c in contours if c.get("subtract", False)]
    if not positive:
        return contours, 1.0

    flattened = [flatten_contour(c["points"]) for c in positive]
    plus_polys = [clip_polygon_halfplane(p, ax, ay, bx, by, 1.0) for p in flattened]
    minus_polys = [clip_polygon_halfplane(p, ax, ay, bx, by, -1.0) for p in flattened]
    area_plus = sum(polygon_area(p) for p in plus_polys if len(p) >= 3)
    area_minus = sum(polygon_area(p) for p in minus_polys if len(p) >= 3)

    default_sign = 1.0 if area_plus >= area_minus else -1.0
    chosen_sign = -default_sign if flip else default_sign
    chosen = plus_polys if chosen_sign > 0 else minus_polys
    kept = [{"points": p, "subtract": False} for p in chosen if len(p) >= 3]
    if not kept:
        # Line missed the shape or clipped everything: keep whichever side is non-empty
        fallback = plus_polys if area_plus > 0 else minus_polys
        kept = [{"points": p, "subtract": False} for p in fallback if len(p) >= 3]
        if not kept:
            return contours, chosen_sign
    return kept + holes, chosen_sign


# ---- Qt QPainterPath Construction (Lazy Import) ------------------------------------------------


def contour_to_qpath(points: list[list[float]]) -> Any:
    """Build a closed ``QPainterPath`` from a contour's vertex list."""
    from PySide6.QtGui import QPainterPath

    path = QPainterPath()
    n = len(points)
    if n < 3:
        return path
    path.moveTo(float(points[0][0]), float(points[0][1]))
    for i in range(n):
        p0 = points[i]
        p1 = points[(i + 1) % n]
        x1, y1 = float(p1[0]), float(p1[1])
        if len(p0) == 2 and len(p1) == 2:
            path.lineTo(x1, y1)
        else:
            ox0, oy0 = (float(p0[4]), float(p0[5])) if len(p0) >= 6 else (float(p0[0]), float(p0[1]))
            ix1, iy1 = (float(p1[2]), float(p1[3])) if len(p1) >= 6 else (x1, y1)
            path.cubicTo(ox0, oy0, ix1, iy1, x1, y1)
    path.closeSubpath()
    return path


def contours_to_qpath(contours: list[dict[str, Any]], full_rect: Any = None) -> Any:
    """Combine ``contours`` (with boolean add/subtract) into a ``QPainterPath``."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainterPath

    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)
    for contour in contours:
        pts = contour.get("points", ())
        if len(pts) < 3:
            continue
        sub = contour_to_qpath(pts)
        if contour.get("subtract", False):
            if not path.isEmpty():
                path = path.subtracted(sub)
        elif path.isEmpty():
            path = sub
        else:
            path = path.united(sub)
    if full_rect is not None and not path.isEmpty():
        clip = QPainterPath()
        clip.addRect(full_rect)
        path = path.intersected(clip)
    return path


def crop_to_qpath(crop: Any, full_rect: Any = None) -> Any:
    """Convert any ``crop`` value into a ``QPainterPath`` in source pixels (or ``None`` if uncropped)."""
    if crop is None:
        return None
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainterPath

    if is_rect_crop(crop):
        rect = QRectF(float(crop[0]), float(crop[1]), float(crop[2]), float(crop[3]))
        if full_rect is not None:
            rect = rect.intersected(full_rect)
        if rect.width() < 1.0 or rect.height() < 1.0:
            return None
        path = QPainterPath()
        path.addRect(rect)
        return path
    contours = crop_contours(crop)
    if not contours:
        return None
    path = contours_to_qpath(contours, full_rect=full_rect)
    return path if not path.isEmpty() else None
