"""Qt side of the Compare Viewer: canvas item rendering, wipe math, difference composite, and interactive controls."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import QGraphicsItem

from pixelref.plugin_api import crop as crop_geom
from pixelref.plugin_api.contracts import PaletteButton
from pixelref.plugin_api.ui import HANDLED, InputHandler, KeyEvent, NodeItemMixin, PointerEvent, theme, widgets as w
from . import COMPARE_TYPE_NAME, frame_size, input_sizes


def calculate_wipe_geometry(rect: QRectF, wipe_pos: float, wipe_angle_deg: float) -> tuple[QPainterPath, QPointF, QPointF, QPointF]:
    """Calculate clip path for Image B, divider line endpoints, and center handle position."""
    w = rect.width()
    h = rect.height()
    cx = rect.center().x()
    cy = rect.center().y()

    rad = math.radians(wipe_angle_deg)
    nx = math.cos(rad)
    ny = math.sin(rad)
    dx = -math.sin(rad)
    dy = math.cos(rad)

    proj = abs(w * nx) + abs(h * ny)
    dist = (wipe_pos - 0.5) * proj

    px = cx + dist * nx
    py = cy + dist * ny
    center_pt = QPointF(px, py)

    span = math.hypot(w, h) * 2.5
    p1 = QPointF(px - dx * span, py - dy * span)
    p2 = QPointF(px + dx * span, py + dy * span)

    # Polygon encompassing the B half
    p3 = QPointF(p2.x() + nx * span, p2.y() + ny * span)
    p4 = QPointF(p1.x() + nx * span, p1.y() + ny * span)

    poly_path = QPainterPath()
    poly_path.addPolygon(QPolygonF([p1, p2, p3, p4]))
    poly_path.closeSubpath()

    rect_path = QPainterPath()
    rect_path.addRect(rect)
    clipped_b = poly_path.intersected(rect_path)

    return clipped_b, p1, p2, center_pt


class CompareViewerItem(NodeItemMixin, QGraphicsItem):
    """Draws an A/B image comparison node with wipe, difference, mix, and toggle modes."""

    def __init__(self, node_name: str, session: Any, canvas: Any) -> None:
        super().__init__()
        self._init_node(node_name, COMPARE_TYPE_NAME, None)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.session = session
        self.canvas = canvas
        self.attrs: dict[str, Any] = {}

    def apply_attrs(self, attrs: dict[str, Any]) -> None:
        self.prepareGeometryChange()
        self.attrs = dict(attrs)
        self.update()

    def itemChange(self, change: Any, value: Any) -> Any:  # noqa: N802
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        return super().itemChange(change, value)

    def _get_image_data(self, ref_id: str) -> tuple[QPixmap | None, QRectF | None]:
        if not ref_id:
            return None, None
        name = self.session.scene.name_of_id(ref_id)
        if not name:
            return None, None

        # Check canvas items for an already instantiated item
        item = self.canvas.items.get(name)
        if item is not None and hasattr(item, "pixmap"):
            pix = item.pixmap()
            if pix and not pix.isNull():
                crop = getattr(item, "_crop", None)
                return pix, crop

        # Fallback to resolving directly from node
        try:
            node = self.session.scene.get_node(name)
        except Exception:
            return None, None

        asset_id = str(node.attrs.get("asset", "") or "")
        if asset_id:
            asset = self.session.scene.get_asset(asset_id)
            if asset is not None:
                pix = QPixmap()
                pix.loadFromData(asset.data)
                box = crop_geom.crop_bounds(node.attrs.get("crop"))
                crop_rect = QRectF(*box) if box else None
                return pix, crop_rect

        path = str(node.attrs.get("path", "") or "")
        if path:
            resolved = self.session.resolve_path(path)
            pix = QPixmap(str(resolved))
            if not pix.isNull():
                box = crop_geom.crop_bounds(node.attrs.get("crop"))
                crop_rect = QRectF(*box) if box else None
                return pix, crop_rect

        return None, None

    def content_rect(self) -> QRectF:
        w, h = frame_size(input_sizes(self.session, self.attrs), str(self.attrs.get("fit_mode", "fit")))
        return QRectF(0.0, 0.0, w, h)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return self.content_rect().adjusted(-20.0, -20.0, 20.0, 20.0)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.content_rect())
        return path

    def pill_rect(self) -> QRectF:
        """Top-right toggle pill [ A | B ] (compact)."""
        rect = self.content_rect()
        return QRectF(rect.right() - 58.0, rect.top() + 8.0, 50.0, 20.0)

    def _place_image(self, frame_rect: QRectF, pix: QPixmap, crop: QRectF | None,
                     offset: tuple[float, float] = (0.0, 0.0)) -> QRectF:
        """Where an input image is drawn inside the frame, per fit_mode (centered)."""
        src_w = float(crop.width() if crop is not None else pix.width())
        src_h = float(crop.height() if crop is not None else pix.height())
        fit_mode = self.attrs.get("fit_mode", "fit")
        ox, oy = offset

        if src_w <= 0 or src_h <= 0:
            return frame_rect

        if fit_mode == "stretch":
            return frame_rect.translated(ox, oy)

        if fit_mode == "one_to_one":
            return QRectF(
                frame_rect.center().x() - src_w / 2.0 + ox,
                frame_rect.center().y() - src_h / 2.0 + oy,
                src_w, src_h,
            )

        scale_w = frame_rect.width() / src_w
        scale_h = frame_rect.height() / src_h
        scale = min(scale_w, scale_h) if fit_mode == "fit" else max(scale_w, scale_h)

        final_w = src_w * scale
        final_h = src_h * scale
        return QRectF(
            frame_rect.center().x() - final_w / 2.0 + ox,
            frame_rect.center().y() - final_h / 2.0 + oy,
            final_w, final_h,
        )

    def paint_content(self, painter: QPainter, _option: Any, _widget: Any = None) -> None:
        rect = self.content_rect()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        pix_a, crop_a = self._get_image_data(str(self.attrs.get("input_a", "") or ""))
        pix_b, crop_b = self._get_image_data(str(self.attrs.get("input_b", "") or ""))

        # If neither input is connected, show an informative placeholder card
        if pix_a is None and pix_b is None:
            self._paint_placeholder(painter, rect)
            return

        # Frame background
        painter.fillRect(rect, QColor(20, 21, 24))

        # Both images are placed in the shared frame the same way; only B gets the manual offset.
        off_b = self.attrs.get("offset_b", [0.0, 0.0])
        rect_a = self._place_image(rect, pix_a, crop_a) if pix_a is not None else rect
        rect_b = (self._place_image(rect, pix_b, crop_b, (float(off_b[0]), float(off_b[1])))
                  if pix_b is not None else rect)

        # Nothing (1:1, fill, offsets) may spill outside the frame.
        painter.save()
        frame_clip = QPainterPath()
        frame_clip.addRect(rect)
        painter.setClipPath(frame_clip)

        def draw_img(p: QPainter, pix: QPixmap, crop: QRectF | None, dest: QRectF) -> None:
            src = crop if crop is not None else QRectF(pix.rect())
            p.drawPixmap(dest, pix, src)

        mode = self.attrs.get("mode", "wipe")

        if mode == "wipe":
            # 1. Base Image A
            if pix_a is not None:
                draw_img(painter, pix_a, crop_a, rect_a)

            # 2. Clipped Image B
            wipe_pos = max(0.0, min(1.0, float(self.attrs.get("wipe_pos", 0.5))))
            wipe_angle = float(self.attrs.get("wipe_angle", 0.0))
            clip_b, p1, p2, center_handle = calculate_wipe_geometry(rect, wipe_pos, wipe_angle)

            if pix_b is not None:
                painter.save()
                painter.setClipPath(clip_b, Qt.IntersectClip)
                painter.fillRect(rect, QColor(20, 21, 24))  # hide A where B's letterbox is empty
                draw_img(painter, pix_b, crop_b, rect_b)
                painter.restore()

            # 3. Divider Line
            div_col = w.color_from(self.attrs.get("divider_color", [255, 255, 255]))
            div_width = float(self.attrs.get("divider_width", 2.0))
            painter.setPen(QPen(div_col, div_width))
            painter.drawLine(p1, p2)

        elif mode == "difference":
            # Render composite onto an isolated offscreen QImage to guarantee hardware/platform compatibility
            w_px = max(1, int(rect.width()))
            h_px = max(1, int(rect.height()))
            diff_img = QImage(w_px, h_px, QImage.Format_ARGB32_Premultiplied)
            diff_img.fill(QColor(0, 0, 0, 255))

            p_diff = QPainter(diff_img)
            p_diff.setRenderHint(QPainter.SmoothPixmapTransform, True)
            sx, sy = w_px / rect.width(), h_px / rect.height()

            def to_img(r: QRectF) -> QRectF:
                return QRectF((r.left() - rect.left()) * sx, (r.top() - rect.top()) * sy,
                              r.width() * sx, r.height() * sy)

            if pix_a is not None:
                draw_img(p_diff, pix_a, crop_a, to_img(rect_a))
            if pix_b is not None:
                p_diff.setCompositionMode(QPainter.CompositionMode_Difference)
                draw_img(p_diff, pix_b, crop_b, to_img(rect_b))
            p_diff.end()

            # Faint difference amplification if diff_gain is specified
            gain = float(self.attrs.get("diff_gain", 1.0))
            if gain > 1.0:
                # Amplify pixels directly
                p_gain = QPainter(diff_img)
                p_gain.setCompositionMode(QPainter.CompositionMode_Plus)
                copies = int(min(gain, 10.0)) - 1
                for _ in range(copies):
                    p_gain.drawImage(0, 0, diff_img)
                p_gain.end()

            painter.drawImage(rect, diff_img)
            self._draw_mode_badge(painter, rect, f"DIFF {f'{gain}x' if gain > 1.0 else ''}".strip())

        elif mode == "mix":
            mix = max(0.0, min(1.0, float(self.attrs.get("mix", 0.5))))
            if pix_a is not None:
                painter.save()
                painter.setOpacity(1.0 - mix)
                draw_img(painter, pix_a, crop_a, rect_a)
                painter.restore()
            if pix_b is not None:
                painter.save()
                painter.setOpacity(mix)
                draw_img(painter, pix_b, crop_b, rect_b)
                painter.restore()
            self._draw_mode_badge(painter, rect, f"MIX {int(round(mix * 100))}%")

        elif mode == "toggle":
            active = self.attrs.get("active_input", "a")
            if active == "b" and pix_b is not None:
                draw_img(painter, pix_b, crop_b, rect_b)
            elif pix_a is not None:
                draw_img(painter, pix_a, crop_a, rect_a)

        painter.restore()  # frame clip

        # Handle drawn unclipped so it stays whole at the frame's edges
        if mode == "wipe":
            self._draw_wipe_handle(painter, center_handle, div_col)

        # Draw compact [ A | B ] toggle pill in top-right corner only in toggle mode
        if mode == "toggle":
            self._draw_toggle_pill(painter)

        # Outer border
        painter.setPen(QPen(QColor(60, 64, 76, 160), 1.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)

    def _draw_wipe_handle(self, painter: QPainter, pos: QPointF, color: QColor) -> None:
        """Draw circular grab handle with arrows ◀ ▶."""
        painter.save()
        radius = 13.0
        # Shadow / outer disc
        painter.setPen(QPen(color, 2.0))
        painter.setBrush(QBrush(QColor(24, 26, 32, 230)))
        painter.drawEllipse(pos, radius, radius)

        # Arrows indicator
        painter.setPen(QPen(color, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        # Left arrow <
        painter.drawLine(QPointF(pos.x() - 2.5, pos.y()), QPointF(pos.x() - 6.5, pos.y() - 3.5))
        painter.drawLine(QPointF(pos.x() - 2.5, pos.y()), QPointF(pos.x() - 6.5, pos.y() + 3.5))
        # Right arrow >
        painter.drawLine(QPointF(pos.x() + 2.5, pos.y()), QPointF(pos.x() + 6.5, pos.y() - 3.5))
        painter.drawLine(QPointF(pos.x() + 2.5, pos.y()), QPointF(pos.x() + 6.5, pos.y() + 3.5))
        painter.restore()

    def _draw_mode_badge(self, painter: QPainter, rect: QRectF, text: str) -> None:
        """Draw mode tag in top-left corner."""
        painter.save()
        font = QFont("sans-serif", 9, QFont.Bold)
        painter.setFont(font)
        badge_rect = QRectF(rect.left() + 10.0, rect.top() + 10.0, 95.0, 24.0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 24, 30, 200))
        painter.drawRoundedRect(badge_rect, 4.0, 4.0)

        painter.setPen(QPen(QColor("#70b0ff")))
        painter.drawText(badge_rect, Qt.AlignCenter, text)
        painter.restore()

    def _draw_toggle_pill(self, painter: QPainter) -> None:
        """Draw compact interactive [ A | B ] toggle pill in top-right corner."""
        painter.save()
        pill = self.pill_rect()
        active = self.attrs.get("active_input", "a")

        # Container pill
        painter.setPen(QPen(QColor(70, 75, 90, 180), 1.0))
        painter.setBrush(QColor(18, 20, 25, 220))
        painter.drawRoundedRect(pill, 3.0, 3.0)

        half_w = pill.width() / 2.0
        rect_a = QRectF(pill.left(), pill.top(), half_w, pill.height())
        rect_b = QRectF(pill.left() + half_w, pill.top(), half_w, pill.height())

        font = QFont("sans-serif", 8, QFont.Bold)
        painter.setFont(font)

        if active == "a":
            # A lit up
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#3a82f7"))
            painter.drawRoundedRect(rect_a.adjusted(1, 1, 0, -1), 2.5, 2.5)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(rect_a, Qt.AlignCenter, "A")
            painter.setPen(QColor(140, 145, 160))
            painter.drawText(rect_b, Qt.AlignCenter, "B")
        else:
            # B lit up
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#f59e0b"))
            painter.drawRoundedRect(rect_b.adjusted(0, 1, -1, -1), 2.5, 2.5)
            painter.setPen(QColor(140, 145, 160))
            painter.drawText(rect_a, Qt.AlignCenter, "A")
            painter.setPen(QColor("#ffffff"))
            painter.drawText(rect_b, Qt.AlignCenter, "B")

        painter.restore()

    def _paint_placeholder(self, painter: QPainter, rect: QRectF) -> None:
        """Placeholder card when no images are connected."""
        painter.fillRect(rect, QColor(26, 28, 34))
        painter.setPen(QPen(QColor(80, 85, 100), 1.5, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect.adjusted(6, 6, -6, -6), 6.0, 6.0)

        # A/B icon disc
        cx, cy = rect.center().x(), rect.center().y()
        disc_rect = QRectF(cx - 28, cy - 48, 56, 56)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(42, 48, 62))
        painter.drawEllipse(disc_rect)

        font_large = QFont("sans-serif", 13, QFont.Bold)
        painter.setFont(font_large)
        painter.setPen(QColor(110, 180, 255))
        painter.drawText(disc_rect, Qt.AlignCenter, "A | B")

        # Title
        font_title = QFont("sans-serif", 11, QFont.Bold)
        painter.setFont(font_title)
        painter.setPen(QColor(220, 225, 235))
        painter.drawText(QRectF(rect.left(), cy + 18, rect.width(), 24), Qt.AlignCenter, "Compare Viewer")

        # Subtitle
        font_sub = QFont("sans-serif", 9)
        painter.setFont(font_sub)
        painter.setPen(QColor(140, 145, 160))
        painter.drawText(
            QRectF(rect.left() + 20, cy + 44, rect.width() - 40, 40),
            Qt.AlignCenter | Qt.TextWordWrap,
            "Select 2 images and press Ctrl+Alt+B\nor set Input A & Input B in Attributes",
        )


class CompareWipeGrabber(InputHandler):
    """Interactive drag handle for the wipe divider line, A/B pill toggles, and hotkeys."""

    HANDLE_REACH = 16.0  # px radius for handle hit test

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.dragging: dict[str, Any] | None = None

    def _target_item_at(self, view: Any, e: PointerEvent) -> tuple[CompareViewerItem | None, QPointF | None]:
        for item in list(self.ui.canvas.items.values()):
            if isinstance(item, CompareViewerItem) and item.isVisible() and item.attrs.get("mode") == "wipe":
                rect = item.content_rect()
                wipe_pos = max(0.0, min(1.0, float(item.attrs.get("wipe_pos", 0.5))))
                wipe_angle = float(item.attrs.get("wipe_angle", 0.0))
                _clip, _p1, _p2, handle_pt = calculate_wipe_geometry(rect, wipe_pos, wipe_angle)

                # Distance in screen viewport pixels for consistent hit testing at any zoom
                scene_pt = item.sceneTransform().map(handle_pt)
                vp_pt = view.to_viewport(scene_pt)
                dist = math.hypot(e.pos.x() - vp_pt.x(), e.pos.y() - vp_pt.y())
                if dist <= self.HANDLE_REACH:
                    return item, handle_pt
        return None, None

    def _pill_target_at(self, e: PointerEvent) -> tuple[CompareViewerItem | None, str]:
        """Detect click on [ A | B ] toggle pill only when in toggle mode."""
        for item in list(self.ui.canvas.items.values()):
            if isinstance(item, CompareViewerItem) and item.isVisible() and item.attrs.get("mode") == "toggle":
                local_pos = item.mapFromScene(e.scene_pos)
                pill = item.pill_rect()
                if pill.contains(local_pos):
                    half_w = pill.width() / 2.0
                    return item, "a" if local_pos.x() <= pill.left() + half_w else "b"
        return None, ""

    def _set_active_input(self, item: CompareViewerItem, choice: str) -> None:
        """Switch active input and trigger canvas update."""
        item.attrs["active_input"] = choice
        self.ui.session.scene.set_attr(item.node_name, "active_input", choice)
        item.update()

    def hover(self, e: PointerEvent) -> None:
        if self.dragging is not None:
            return

        # Check pill hover
        item_pill, _ = self._pill_target_at(e)
        if item_pill is not None:
            e.cursor = Qt.PointingHandCursor
            return

        # Check wipe handle hover
        item, _pt = self._target_item_at(e.view, e)
        if item is not None:
            angle = float(item.attrs.get("wipe_angle", 0.0)) % 180.0
            if 45.0 <= angle <= 135.0:
                e.cursor = Qt.SplitVCursor
            else:
                e.cursor = Qt.SplitHCursor

    def key(self, e: KeyEvent) -> str | None:
        """Handle \\ and ` shortcuts to toggle A/B immediately when viewer is selected/under mouse."""
        if e.kind != "press":
            return None

        target_item = None
        for item in self.ui.canvas.items.values():
            if isinstance(item, CompareViewerItem) and (item.isSelected() or item.isUnderMouse()):
                target_item = item
                break
        if target_item is None:
            for name in self.ui.session.selection.names:
                cand = self.ui.canvas.items.get(name)
                if isinstance(cand, CompareViewerItem):
                    target_item = cand
                    break

        if target_item is not None:
            if e.key in (Qt.Key_Backslash, Qt.Key_QuoteLeft) or e.text in ("\\", "`"):
                cur = target_item.attrs.get("active_input", "a")
                nxt = "b" if cur == "a" else "a"
                self._set_active_input(target_item, nxt)
                return HANDLED

        return None

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind == "press" and e.button == Qt.LeftButton:
            # 1. Check A/B pill click
            pill_item, choice = self._pill_target_at(e)
            if pill_item is not None:
                self._set_active_input(pill_item, choice)
                return HANDLED

            # 2. Check wipe handle click
            item, handle_pt = self._target_item_at(e.view, e)
            if item is not None:
                self.dragging = {
                    "item": item,
                    "start_pos": float(item.attrs.get("wipe_pos", 0.5)),
                    "current_pos": float(item.attrs.get("wipe_pos", 0.5)),
                    "start_angle": float(item.attrs.get("wipe_angle", 0.0)),
                    "current_angle": float(item.attrs.get("wipe_angle", 0.0)),
                    "press_local": item.mapFromScene(e.scene_pos),
                }
                return HANDLED

            # 3. If in toggle mode, clicking inside viewer flips between A and B
            for cand in self.ui.canvas.items.values():
                if isinstance(cand, CompareViewerItem) and cand.attrs.get("mode") == "toggle":
                    local_pos = cand.mapFromScene(e.scene_pos)
                    if cand.content_rect().contains(local_pos):
                        cur = cand.attrs.get("active_input", "a")
                        nxt = "b" if cur == "a" else "a"
                        self._set_active_input(cand, nxt)
                        return HANDLED

        if self.dragging is None:
            return None

        item = self.dragging["item"]
        rect = item.content_rect()

        if e.kind == "move":
            local_pos = item.mapFromScene(e.scene_pos)
            w = rect.width()
            h = rect.height()
            if w <= 0 or h <= 0:
                return HANDLED

            if e.modifiers & Qt.ShiftModifier:
                # Rotate angle live with mouse position
                cx, cy = rect.center().x(), rect.center().y()
                angle = math.degrees(math.atan2(local_pos.y() - cy, local_pos.x() - cx))
                if e.modifiers & Qt.ControlModifier:
                    # Snap to nearest 15 degrees
                    angle = round(angle / 15.0) * 15.0
                angle_val = round(angle, 1)
                self.dragging["current_angle"] = angle_val
                item.attrs["wipe_angle"] = angle_val
                self.ui.session.scene.set_attr(item.node_name, "wipe_angle", angle_val)
            else:
                # Slide position along projection axis
                rad = math.radians(float(item.attrs.get("wipe_angle", 0.0)))
                nx, ny = math.cos(rad), math.sin(rad)
                proj = abs(w * nx) + abs(h * ny)
                delta_x = local_pos.x() - self.dragging["press_local"].x()
                delta_y = local_pos.y() - self.dragging["press_local"].y()
                delta_proj = delta_x * nx + delta_y * ny
                new_pos = max(0.0, min(1.0, self.dragging["start_pos"] + delta_proj / max(proj, 1.0)))
                if e.modifiers & Qt.ControlModifier:
                    # Snap to 25%, 50%, 75%
                    snaps = (0.0, 0.25, 0.5, 0.75, 1.0)
                    nearest = min(snaps, key=lambda s: abs(s - new_pos))
                    if abs(nearest - new_pos) < 0.05:
                        new_pos = nearest
                pos_val = round(new_pos, 4)
                self.dragging["current_pos"] = pos_val
                item.attrs["wipe_pos"] = pos_val
                self.ui.session.scene.set_attr(item.node_name, "wipe_pos", pos_val)

            item.update()
            return HANDLED

        if e.kind == "release":
            final_pos = self.dragging.get("current_pos", self.dragging["start_pos"])
            final_angle = self.dragging.get("current_angle", self.dragging["start_angle"])
            node_name = item.node_name
            self.dragging = None
            try:
                self.ui.session.run("set_attrs", node_name, {"wipe_pos": final_pos, "wipe_angle": final_angle})
            except Exception:
                pass
            return HANDLED

        return None


def activate_compare_ui(ui: Any) -> None:
    session = ui.session
    canvas = ui.canvas

    def factory(_cv: Any, node: dict[str, Any]) -> CompareViewerItem:
        item = CompareViewerItem(node["name"], session, canvas)
        item.apply_attrs(node.get("attrs", {}))
        return item

    ui.node_ui(
        COMPARE_TYPE_NAME,
        factory=factory,
        label="Compare Viewer",
        icon="split_h",
        badge=("#70b0ff", "C"),
    )

    # Interactive handle grabber and shortcuts
    grabber = CompareWipeGrabber(ui)
    ui.input_handler(grabber, 605)

    # Palette & Menu Actions
    ui.action(
        "compare.compare_selected",
        "compare.compare_selected",
        "Compare Selected Images (A/B Wipe)",
        icon="split_h",
        keywords="compare wipe difference mix ab nuke natron",
        category="Tools",
    )
    ui.action(
        "compare.create",
        "compare.create",
        "Create Compare Viewer",
        icon="split_h",
        keywords="compare viewer ab wipe",
        category="Create",
    )
    ui.action(
        "compare.toggle_active",
        "compare.toggle_active",
        "Toggle A/B Active Input",
        icon="view",
        keywords="toggle ab compare blink",
        category="Tools",
    )
    ui.action(
        "compare.swap_inputs",
        "compare.swap_inputs",
        "Swap Compare Inputs (A ↔ B)",
        icon="refresh",
        keywords="swap reverse compare ab",
        category="Tools",
    )

    # Shortcuts
    ui.bind("Ctrl+Alt+B", "compare.compare_selected", scope="canvas")
    ui.bind("\\", "compare.toggle_active", scope="canvas")

    # Context Menus
    ui.menu("canvas", "compare.compare_selected", path="Image", group="item", order=60, when="selection.count > 1")
    ui.menu("canvas", "compare.create", group="create", order=25, when="selection.count == 0")

    # Tool palette button
    ui.contribute("palette.buttons", PaletteButton("compare.compare_selected", "Compare", "split_h", order=35.0))
