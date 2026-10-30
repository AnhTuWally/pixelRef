"""Picking representative colors from pixels: pure Python, deterministic, no Qt.

``extract_palette`` takes RGBA bytes (already downsampled by the caller) and returns up to
``count`` integer RGB triples, heaviest first. Pixels are binned to 4 bits per channel, the
bins are clustered with a fixed-start weighted k-means, and each cluster reports the mean of
the real pixel values it holds, so the result never depends on the binning's rounding.
"""

from __future__ import annotations

SAMPLE_BUDGET = 16384  # pixels a caller should reduce an image to before extracting
MIN_ALPHA = 8  # pixels at or below this alpha (0-255) are ignored
MIN_DISTANCE = 24.0  # colors closer than this (RGB distance) count as one
_ITERATIONS = 10


def _dist2(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def _bins(rgba: bytes) -> dict[int, list[float]]:
    """key -> [weight, sum_r, sum_g, sum_b]; weight is alpha-proportional."""
    bins: dict[int, list[float]] = {}
    for i in range(0, len(rgba) - 3, 4):
        alpha = rgba[i + 3]
        if alpha <= MIN_ALPHA:
            continue
        r, g, b = rgba[i], rgba[i + 1], rgba[i + 2]
        weight = alpha / 255.0
        entry = bins.setdefault(((r >> 4) << 8) | ((g >> 4) << 4) | (b >> 4), [0.0, 0.0, 0.0, 0.0])
        entry[0] += weight
        entry[1] += r * weight
        entry[2] += g * weight
        entry[3] += b * weight
    return bins


def extract_palette(rgba: bytes, count: int = 5) -> list[tuple[int, int, int]]:
    """Up to ``count`` distinct representative colors, heaviest first; ``[]`` if every pixel is
    transparent. Fewer than ``count`` come back when the image has fewer distinct colors."""
    count = max(1, int(count))
    bins = _bins(rgba)
    if not bins:
        return []
    # Ties break on the bin key so the order never depends on hashing or input order.
    points = [(k, v[0], (v[1] / v[0], v[2] / v[0], v[3] / v[0])) for k, v in sorted(bins.items())]
    centers: list[tuple[float, float, float]] = [max(points, key=lambda p: (p[1], -p[0]))[2]]
    while len(centers) < count:
        far = max(points, key=lambda p: (min(_dist2(p[2], c) for c in centers) * p[1] ** 0.5, -p[0]))
        if min(_dist2(far[2], c) for c in centers) < MIN_DISTANCE ** 2:
            break
        centers.append(far[2])
    weights = [0.0] * len(centers)
    for _ in range(_ITERATIONS):
        sums = [[0.0, 0.0, 0.0, 0.0] for _ in centers]
        for _, weight, color in points:
            best = min(range(len(centers)), key=lambda i: (_dist2(color, centers[i]), i))
            s = sums[best]
            s[0] += weight
            s[1] += color[0] * weight
            s[2] += color[1] * weight
            s[3] += color[2] * weight
        centers = [(s[1] / s[0], s[2] / s[0], s[3] / s[0]) if s[0] else c for s, c in zip(sums, centers)]
        weights = [s[0] for s in sums]
    ranked = sorted(zip(weights, centers), key=lambda wc: (-wc[0], wc[1]))
    out: list[tuple[int, int, int]] = []
    for weight, color in ranked:
        if weight <= 0:
            continue
        rgb = (int(round(color[0])), int(round(color[1])), int(round(color[2])))
        if rgb not in out and all(_dist2(rgb, o) >= MIN_DISTANCE ** 2 for o in out):
            out.append(rgb)
    return out[:count]
