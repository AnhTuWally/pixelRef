"""Build the app icon set from the master logo artwork.

    python tools/build_logo.py [assets/pixelRef_logo.jpg]

The master is a JPEG rounded square on a white background. This script turns
the white corners transparent (with anti-aliased edges recovered from the
JPEG's own blend), then writes pre-downsampled PNGs plus a Windows .ico into
pixelref/resources/. Small sizes are produced by repeated halving so they stay
crisp instead of aliasing from a single large bilinear downscale.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QImageWriter

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "pixelRef_logo.jpg"
OUT_DIR = ROOT / "pixelref" / "resources"

PNG_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)  # subset of PNG_SIZES
# How far in from each corner the transparent cut-out can reach (fraction of size).
CORNER_FRACTION = 0.22
# Max per-channel distance from a flat fill colour treated as JPEG noise.
FILL_TOLERANCE = 8
# A colour must cover this fraction of an image to count as a flat fill.
FILL_MIN_SHARE = 0.002


def _cut_corners(img: QImage) -> QImage:
    """Make the white page outside the rounded square transparent.

    Inside each corner box the only content is the yellow background and the
    white page, so alpha is derived from the blue channel (white≈255, yellow≈130)
    and colour is reset to the background yellow — giving clean, smooth edges.
    """
    img = img.convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    bg = QColor(img.pixel(w // 2, int(h * 0.1)))  # flat yellow area, top centre
    bg_blue = bg.blue()
    span = max(1, 255 - bg_blue)
    box = int(min(w, h) * CORNER_FRACTION)
    corners = (
        (range(0, box), range(0, box)),
        (range(w - box, w), range(0, box)),
        (range(0, box), range(h - box, h)),
        (range(w - box, w), range(h - box, h)),
    )
    for xs, ys in corners:
        for y in ys:
            for x in xs:
                blue = QColor(img.pixel(x, y)).blue()
                alpha = round(255 * min(1.0, max(0.0, (255 - blue) / span)))
                if alpha >= 250:
                    continue
                img.setPixelColor(x, y, QColor(bg.red(), bg.green(), bg.blue(), alpha))
    return img


def _rgb(p: int) -> tuple[int, int, int]:
    return (p >> 16) & 0xFF, (p >> 8) & 0xFF, p & 0xFF


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


def _flatten_fills(img: QImage) -> QImage:
    """Snap opaque pixels near a dominant flat fill to that exact colour.

    The artwork is flat colour, but the JPEG master adds noise to every fill
    (background, fur, beret…), which PNG compresses terribly. Anti-aliased
    edges are far from any fill and are left untouched.
    """
    w, h = img.width(), img.height()
    counts: dict[int, int] = {}
    for y in range(h):
        for x in range(w):
            p = img.pixel(x, y)
            if p >> 24 == 0xFF:
                counts[p] = counts.get(p, 0) + 1
    min_count = max(4, int(w * h * FILL_MIN_SHARE))
    fills: list[tuple[int, tuple[int, int, int]]] = []
    for p, n in sorted(counts.items(), key=lambda item: -item[1]):
        if n < min_count:
            break
        rgb = _rgb(p)
        if all(_distance(rgb, f) > FILL_TOLERANCE for _, f in fills):
            fills.append((p, rgb))

    snap: dict[int, int] = {}
    for p in counts:
        rgb = _rgb(p)
        best = min(fills, key=lambda f: _distance(rgb, f[1]), default=None)
        if best is not None and best[0] != p and _distance(rgb, best[1]) <= FILL_TOLERANCE:
            snap[p] = best[0]
    for y in range(h):
        for x in range(w):
            target = snap.get(img.pixel(x, y))
            if target is not None:
                img.setPixel(x, y, target)
    return img


def _downscale(img: QImage, size: int) -> QImage:
    while img.width() >= size * 2:
        img = img.scaled(img.width() // 2, img.height() // 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    if img.width() != size:
        img = img.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return img


def build(source: Path = SOURCE, out_dir: Path = OUT_DIR) -> None:
    master = QImage(str(source))
    if master.isNull():
        raise SystemExit(f"Could not read {source}")
    if master.width() != master.height():
        side = min(master.width(), master.height())
        master = master.copy((master.width() - side) // 2, (master.height() - side) // 2, side, side)
    master = _cut_corners(master)

    out_dir.mkdir(parents=True, exist_ok=True)
    scaled = {size: _flatten_fills(_downscale(master, size)) for size in PNG_SIZES}
    for size in PNG_SIZES:
        path = out_dir / f"logo_{size}.png"
        writer = QImageWriter(str(path), b"png")
        writer.setCompression(100)  # Qt 6 PNG scale is 0–100, not zlib 0–9
        if not writer.write(scaled[size]):
            raise SystemExit(f"Failed to write {path}: {writer.errorString()}")

    # Qt's ICO writer only stores a single image, so assemble the multi-size
    # .ico by hand from the PNGs just written.
    ico_path = out_dir / "logo.ico"
    _write_ico(ico_path, [(size, (out_dir / f"logo_{size}.png").read_bytes()) for size in ICO_SIZES])
    print(f"Wrote {len(PNG_SIZES)} PNGs and {ico_path.name} to {out_dir}")


def _write_ico(path: Path, entries: list[tuple[int, bytes]]) -> None:
    """Write a PNG-compressed .ico (supported since Windows Vista)."""
    import struct

    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = len(header) + 16 * len(entries)
    directory, blobs = b"", b""
    for size, png in entries:
        dim = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    path.write_bytes(header + directory + blobs)


if __name__ == "__main__":
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE)
