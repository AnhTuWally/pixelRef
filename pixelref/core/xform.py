"""2D affine maths for the core transform attributes (no Qt needed).

Matrices follow Qt's ``QTransform`` convention so values can be passed
straight to Qt: ``(m11, m12, m21, m22, dx, dy)`` with::

    x' = m11 * x + m21 * y + dx
    y' = m12 * x + m22 * y + dy

and ``a @ b`` meaning "apply a, then b" (Qt's ``a * b``).

The local → parent transform of a canvas node is::

    parent_point = translate + pivot + R(rotate) · S(scale) · (local_point - pivot)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


def normalize_angle(angle: float) -> float:
    """Wrap an angle in degrees into (-180, 180]."""
    angle = math.fmod(angle, 360.0)
    if angle <= -180.0:
        angle += 360.0
    elif angle > 180.0:
        angle -= 360.0
    return angle + 0.0  # turn -0.0 into 0.0


@dataclass(frozen=True)
class Matrix:
    m11: float = 1.0
    m12: float = 0.0
    m21: float = 0.0
    m22: float = 1.0
    dx: float = 0.0
    dy: float = 0.0

    def __matmul__(self, b: "Matrix") -> "Matrix":
        a = self
        return Matrix(
            a.m11 * b.m11 + a.m12 * b.m21,
            a.m11 * b.m12 + a.m12 * b.m22,
            a.m21 * b.m11 + a.m22 * b.m21,
            a.m21 * b.m12 + a.m22 * b.m22,
            a.dx * b.m11 + a.dy * b.m21 + b.dx,
            a.dx * b.m12 + a.dy * b.m22 + b.dy,
        )

    def map(self, x: float, y: float) -> tuple[float, float]:
        return self.m11 * x + self.m21 * y + self.dx, self.m12 * x + self.m22 * y + self.dy

    def determinant(self) -> float:
        return self.m11 * self.m22 - self.m12 * self.m21

    def inverted(self) -> "Matrix | None":
        det = self.determinant()
        if abs(det) < 1e-12:
            return None
        m11, m12 = self.m22 / det, -self.m12 / det
        m21, m22 = -self.m21 / det, self.m11 / det
        dx = -(self.dx * m11 + self.dy * m21)
        dy = -(self.dx * m12 + self.dy * m22)
        return Matrix(m11, m12, m21, m22, dx, dy)

    def to_list(self) -> list[float]:
        return [self.m11, self.m12, self.m21, self.m22, self.dx, self.dy]

    @classmethod
    def from_list(cls, values: Iterable[float]) -> "Matrix":
        return cls(*[float(v) for v in values])

    @classmethod
    def translation(cls, x: float, y: float) -> "Matrix":
        return cls(1.0, 0.0, 0.0, 1.0, x, y)

    @classmethod
    def scaling(cls, sx: float, sy: float) -> "Matrix":
        return cls(sx, 0.0, 0.0, sy, 0.0, 0.0)

    @classmethod
    def rotation(cls, degrees: float) -> "Matrix":
        rad = math.radians(degrees)
        c, s = math.cos(rad), math.sin(rad)
        return cls(c, s, -s, c, 0.0, 0.0)

    def map_rect(self, rect: list[float]) -> list[float]:
        """Axis-aligned bounds [x, y, w, h] of a mapped rectangle."""
        x, y, w, h = rect
        corners = [self.map(px, py) for px, py in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))]
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


IDENTITY = Matrix()


def compose(
    tx: float, ty: float, sx: float, sy: float, rotation: float, px: float = 0.0, py: float = 0.0
) -> Matrix:
    """Local → parent transform from the core transform attribute values."""
    rad = math.radians(rotation)
    c, s = math.cos(rad), math.sin(rad)
    m11, m12, m21, m22 = c * sx, s * sx, -s * sy, c * sy
    dx = tx + px - (m11 * px + m21 * py)
    dy = ty + py - (m12 * px + m22 * py)
    return Matrix(m11, m12, m21, m22, dx, dy)


def decompose(
    m: Matrix, prev_sx: float = 1.0, prev_rotation: float = 0.0, px: float = 0.0, py: float = 0.0
) -> tuple[float, float, float, float, float]:
    """Split a transform into (tx, ty, sx, sy, rotation) for a given pivot.

    A mirrored transform can be written either as a flip on X or as a flip on
    Y plus 180°; pick whichever keeps the rotation closest to the previous one
    so flipping an item doesn't make its rotation jump.
    """
    det = m.determinant()
    magnitude = math.hypot(m.m11, m.m12) or 1e-9
    best: tuple[float, float, float, float] | None = None
    for sign in (1.0, -1.0):
        sx = sign * magnitude
        rotation = math.degrees(math.atan2(m.m12 / sx, m.m11 / sx))
        distance = abs(normalize_angle(rotation - prev_rotation))
        tie_break = 0.0 if (sx > 0) == (prev_sx > 0) else 1e-6  # keep the previous flip when equal
        score = distance + tie_break
        if best is None or score < best[0]:
            best = (score, sx, det / sx, normalize_angle(rotation))
    assert best is not None
    _score, sx, sy, rotation = best
    tx = m.dx - (px - (m.m11 * px + m.m21 * py))
    ty = m.dy - (py - (m.m12 * px + m.m22 * py))
    return tx, ty, sx, sy, rotation


def scaled_about(x: float, y: float, fx: float, fy: float) -> Matrix:
    return Matrix.translation(-x, -y) @ Matrix.scaling(fx, fy) @ Matrix.translation(x, y)


def rotated_about(x: float, y: float, degrees: float) -> Matrix:
    return Matrix.translation(-x, -y) @ Matrix.rotation(degrees) @ Matrix.translation(x, y)


def _pair(value: Any, default: tuple[float, float]) -> tuple[float, float]:
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError):
        return default


def transform_values(attrs: dict[str, Any]) -> tuple[float, float, float, float, float, float, float]:
    """(tx, ty, sx, sy, rotation, px, py) from a node's attributes."""
    tx, ty = _pair(attrs.get("translate"), (0.0, 0.0))
    sx, sy = _pair(attrs.get("scale"), (1.0, 1.0))
    px, py = _pair(attrs.get("pivot"), (0.0, 0.0))
    try:
        rotation = float(attrs.get("rotate", 0.0) or 0.0)
    except (TypeError, ValueError):
        rotation = 0.0
    return tx, ty, sx, sy, rotation, px, py


def local_matrix(attrs: dict[str, Any]) -> Matrix:
    if "translate" not in attrs and "scale" not in attrs and "rotate" not in attrs:
        return IDENTITY
    tx, ty, sx, sy, rotation, px, py = transform_values(attrs)
    return compose(tx, ty, sx, sy, rotation, px, py)


def world_matrix(scene: Any, name: str) -> Matrix:
    """Local → world transform of a node (its own transform, then each parent's)."""
    node = scene.get_node(name)
    result = local_matrix(node.attrs)
    parent = node.parent
    while parent is not None:
        parent_node = scene.get_node(parent)
        result = result @ local_matrix(parent_node.attrs)
        parent = parent_node.parent
    return result


def parent_world_matrix(scene: Any, parent: str | None) -> Matrix:
    return world_matrix(scene, parent) if parent is not None else IDENTITY


def attrs_for_matrix(
    m: Matrix, attrs: dict[str, Any], precision: tuple[int, int, int] = (3, 6, 4)
) -> dict[str, Any]:
    """Transform attribute values that produce local matrix ``m`` (keeping the pivot)."""
    _tx, _ty, sx, _sy, rotation, px, py = transform_values(attrs)
    tx, ty, nsx, nsy, nrot = decompose(m, sx, rotation, px, py)
    p_pos, p_scale, p_rot = precision
    return {
        "translate": [round(tx, p_pos) + 0.0, round(ty, p_pos) + 0.0],
        "scale": [round(nsx, p_scale) + 0.0, round(nsy, p_scale) + 0.0],
        "rotate": round(nrot, p_rot) + 0.0,
    }
