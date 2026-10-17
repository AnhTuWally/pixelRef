"""Pure layout helpers used by the canvas "Arrange" commands.

Every function takes a list of ``(x, y, w, h)`` rectangles and returns the new
top-left corner for each, in the same order. The arrangement is anchored at
the top-left of the original bounding box so items don't fly across the canvas.
"""

from __future__ import annotations

import math

Rect = tuple[float, float, float, float]
Point = tuple[float, float]


def bounding_box(rects: list[Rect]) -> Rect:
    left = min(r[0] for r in rects)
    top = min(r[1] for r in rects)
    right = max(r[0] + r[2] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    return left, top, right - left, bottom - top


def arrange_row(rects: list[Rect], gap: float = 0.0) -> list[Point]:
    if not rects:
        return []
    left, top, _w, _h = bounding_box(rects)
    order = sorted(range(len(rects)), key=lambda i: (rects[i][0], rects[i][1]))
    result: list[Point] = [(0.0, 0.0)] * len(rects)
    x = left
    for i in order:
        result[i] = (x, top)
        x += rects[i][2] + gap
    return result


def arrange_column(rects: list[Rect], gap: float = 0.0) -> list[Point]:
    if not rects:
        return []
    left, top, _w, _h = bounding_box(rects)
    order = sorted(range(len(rects)), key=lambda i: (rects[i][1], rects[i][0]))
    result: list[Point] = [(0.0, 0.0)] * len(rects)
    y = top
    for i in order:
        result[i] = (left, y)
        y += rects[i][3] + gap
    return result


def arrange_grid(rects: list[Rect], gap: float = 0.0, columns: int | None = None) -> list[Point]:
    """Uniform cells in reading order; each item is centred in its cell."""
    if not rects:
        return []
    left, top, _w, _h = bounding_box(rects)
    count = len(rects)
    cols = columns or max(1, math.ceil(math.sqrt(count)))
    cell_w = max(r[2] for r in rects)
    cell_h = max(r[3] for r in rects)
    order = sorted(range(count), key=lambda i: (rects[i][1], rects[i][0]))
    result: list[Point] = [(0.0, 0.0)] * count
    for slot, i in enumerate(order):
        row, col = divmod(slot, cols)
        cx = left + col * (cell_w + gap) + (cell_w - rects[i][2]) / 2.0
        cy = top + row * (cell_h + gap) + (cell_h - rects[i][3]) / 2.0
        result[i] = (cx, cy)
    return result


def _shelf_pack(rects: list[Rect], order: list[int], max_width: float, gap: float) -> tuple[list[Point], float, float]:
    placements: list[Point] = [(0.0, 0.0)] * len(rects)
    x = y = shelf_h = 0.0
    used_w = 0.0
    for i in order:
        w, h = rects[i][2], rects[i][3]
        if x > 0.0 and x + w > max_width:
            y += shelf_h + gap
            x = 0.0
            shelf_h = 0.0
        placements[i] = (x, y)
        x += w + gap
        used_w = max(used_w, x - gap)
        shelf_h = max(shelf_h, h)
    return placements, used_w, y + shelf_h


def arrange_optimal(rects: list[Rect], gap: float = 0.0) -> list[Point]:
    """Pack items into a compact, roughly square block (like PureRef's Ctrl+P)."""
    if not rects:
        return []
    left, top, _w, _h = bounding_box(rects)
    order = sorted(range(len(rects)), key=lambda i: (-rects[i][3], -rects[i][2]))
    total_area = sum((r[2] + gap) * (r[3] + gap) for r in rects)
    widest = max(r[2] for r in rects)
    candidates = {widest}
    base = math.sqrt(total_area)
    for factor in (0.6, 0.75, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0):
        candidates.add(max(widest, base * factor))
    # Breakpoints where the shelf layout actually changes.
    running = 0.0
    for i in order:
        running += rects[i][2] + gap
        if running - gap >= widest:
            candidates.add(running - gap)

    best: tuple[float, list[Point]] | None = None
    for width in sorted(candidates):
        placements, used_w, used_h = _shelf_pack(rects, order, width, gap)
        side = max(used_w, used_h)
        score = side * side + used_w * used_h * 0.25  # prefer square, then compact
        if best is None or score < best[0]:
            best = (score, placements)
    assert best is not None
    return [(left + px, top + py) for px, py in best[1]]
