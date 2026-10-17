"""Canvas views: one QGraphicsScene shown by any number of views.

A :class:`CanvasView` owns no interaction logic of its own. It converts Qt
events into pipeline events (see :mod:`.input`), draws the background and
the overlays plugins contribute, and manages its camera (pan / zoom /
rotation) and display settings. Every view of a session shares the same
scene, selection and document.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (QCoreApplication, QEvent, QLineF, QMimeData, QPoint, QPointF, QRect, QRectF, QSize, Qt,
                            QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QCursor, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView, QWidget

from ..icons import logo_pixmap
from ..theme import ACCENT, TEXT_LIGHT
from ..widgets import color_from
from .display import BackgroundSpec, DisplaySettings
from .gpu import make_viewport
from .input import DEFAULT, HANDLED, KeyEvent, PointerEvent, WheelEvent
from .items import NodeItemMixin
from .paint import paint_banner
from .tiles import TileCache, tiles_mode

if TYPE_CHECKING:
    from ..app import App

PATTERNS = ("lines", "dots", "crosses", "checker", "none")


class CanvasScene(QGraphicsScene):
    """The one item scene of a session (shared by all canvas views)."""

    EXTENT = 1_000_000.0

    # Scene areas whose pixels changed (what view tile caches drop). Like
    # ``changed``, except that removing items reports where they were: Qt
    # reports any removal as a change of the whole scene.
    content_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Huge rect so the canvas is effectively infinite in every direction.
        self.setSceneRect(-self.EXTENT, -self.EXTENT, 2 * self.EXTENT, 2 * self.EXTENT)
        self._removed: list[QRectF] = []
        self.changed.connect(self._on_changed)

    def remove_items(self, items: list[QGraphicsItem]) -> None:
        """Remove items, reporting only their areas as changed (not the whole scene)."""
        for item in items:
            rect = item.boundingRect() | item.childrenBoundingRect()
            self._removed.append(item.mapRectToScene(rect))
            self.removeItem(item)

    def flush_changes(self) -> None:
        """Report pending item changes now. Qt processes dirty items (and emits ``changed``)
        from queued calls, so a paint right after an edit would otherwise use stale tiles."""
        for _ in range(2):  # processing dirty items queues the emission of ``changed``
            QCoreApplication.sendPostedEvents(self, QEvent.MetaCall)

    def _on_changed(self, rects: list[QRectF]) -> None:
        removed, self._removed = self._removed, []
        if removed:
            whole = self.sceneRect()
            rects = [r for r in rects if not r.contains(whole)] + removed
        self.content_changed.emit(rects)


def pattern_color(spec: BackgroundSpec) -> QColor:
    """Pattern colour: custom, or a subtle contrast to the background."""
    if spec.pattern_color is not None:
        return QColor(*spec.pattern_color)
    bg = QColor(*spec.color)
    # Dots and crosses are small marks, so they need a little more contrast than lines.
    strong = spec.pattern in ("dots", "crosses")
    if bg.lightnessF() < 0.5:
        amount = 0.12 if strong else 0.06
        mix = lambda c: round(c + (255 - c) * amount)  # noqa: E731  slightly lighter
    else:
        amount = 0.78 if strong else 0.9
        mix = lambda c: round(c * amount)  # noqa: E731  slightly darker
    return QColor(mix(bg.red()), mix(bg.green()), mix(bg.blue()))


def draw_background(painter: QPainter, rect: QRectF, spec: BackgroundSpec) -> None:
    painter.fillRect(rect, QColor(*spec.color))
    pattern = spec.pattern if spec.pattern in PATTERNS else "lines"
    if pattern == "none":
        return
    # Keep the pattern at least ~16px apart on screen at any zoom level.
    transform = painter.worldTransform()
    zoom = math.hypot(transform.m11(), transform.m12()) or 1.0
    step = float(max(4, spec.grid_size))
    while step * zoom < (8.0 if pattern == "checker" else 16.0):
        step *= 2.0 if pattern == "checker" else 4.0
    color = pattern_color(spec)
    left = math.floor(rect.left() / step) * step
    top = math.floor(rect.top() / step) * step
    xs = [left + i * step for i in range(int((rect.right() - left) / step) + 2)]
    ys = [top + j * step for j in range(int((rect.bottom() - top) / step) + 2)]
    if pattern == "checker":
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRects([QRectF(x, y, step, step) for x in xs for y in ys
                           if (round(x / step) + round(y / step)) % 2])
        painter.restore()
        return
    if pattern == "lines":
        pen = QPen(color)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for x in xs:
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for y in ys:
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        return
    if pattern == "dots":
        pen = QPen(color, 2.5, Qt.SolidLine, Qt.RoundCap)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawPoints(QPolygonF([QPointF(x, y) for x in xs for y in ys]))
        return
    pen = QPen(color, 1.0)  # crosses
    pen.setCosmetic(True)
    painter.setPen(pen)
    arm = 4.0 / zoom
    # One batched call: thousands of per-cross drawLine calls are slow on every zoom step.
    lines = []
    for x in xs:
        for y in ys:
            lines.append(QLineF(x - arm, y, x + arm, y))
            lines.append(QLineF(x, y - arm, x, y + arm))
    painter.drawLines(lines)


class CanvasView(QGraphicsView):
    MIN_ZOOM = 0.01
    MAX_ZOOM = 64.0

    camera_changed = Signal()

    def __init__(self, app: "App", name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(app.canvas.scene, parent)
        self.app = app
        self.view_name = name
        self.display = DisplaySettings()
        # Views with the same link group share one camera ("" = not linked); see App.linked_views.
        self.link_group = ""
        # A widget a plugin shows on the empty board instead of the painted welcome card.
        self.welcome_widget: QWidget | None = None
        # Per-view settings of plugins, keyed by plugin id (saved with the tile, e.g. the minimap's corner).
        self.plugin_state: dict[str, Any] = {}
        self._hide_overlay = False
        self._drop_hint: str | None = None
        self._rendering_gray = False
        self._gray_cache: QImage | None = None
        self._gray_cache_key: tuple[int, int, float, float, float, float, float, float] | None = None
        self._animation: QVariantAnimation | None = None
        self._toast: dict[str, Any] | None = None
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._clear_toast)
        self._shortcuts: list[Any] = []
        self._camera_center = QPointF(0.0, 0.0)
        self._camera_zoom = 1.0
        self._camera_rotation = 0.0
        self._camera_flip_x = False
        self._camera_flip_y = False
        self._applying_camera = False
        # A view that hasn't been shown has no real size yet, so centring it is
        # meaningless: remember the camera and apply it once it is on screen.
        self._pending_camera: tuple[QPointF, float, float, bool, bool] | None = None
        self._current_marker: str | None = None
        self._glide_target: float | None = None
        self._glide_anchor = QPointF()
        self._glide_scene = QPointF()
        self._glide_clock = 0.0
        self._glide_timer = QTimer(self)
        self._glide_timer.setInterval(8)  # each step paints a frame; vsync paces them
        self._glide_timer.setTimerType(Qt.PreciseTimer)
        self._glide_timer.timeout.connect(self._glide_step)
        self._cameras_of: Any = None
        self._cameras: list[str] = []

        viewport = make_viewport()
        if viewport is not None:  # GPU drawing (see .gpu)
            self.setViewport(viewport)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        self.setCacheMode(QGraphicsView.CacheBackground)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.viewport()._pixelref_view = self  # items look the view up when painting
        self.horizontalScrollBar().valueChanged.connect(self._camera_moved)
        self.verticalScrollBar().valueChanged.connect(self._camera_moved)
        self.tiles = TileCache(self)
        # The greyscale cache is keyed on the camera only: drop it when any item repaints.
        app.canvas.scene.content_changed.connect(self._on_scene_changed)

    def _on_scene_changed(self, rects: list[QRectF]) -> None:
        self._invalidate_gray_cache()
        self.tiles.invalidate(rects)

    def _invalidate_gray_cache(self, *_args: Any) -> None:
        self._gray_cache = None
        self._gray_cache_key = None

    TILE_MIN_ITEMS = 300  # scenes with fewer items paint directly

    def uses_tiles(self) -> bool:
        """Paint from the tile cache (see .tiles): busy scenes, axis-aligned cameras."""
        mode = tiles_mode()
        if mode == "0" or self._hide_overlay:
            return False
        if mode != "1" and len(self.app.canvas.items) < self.TILE_MIN_ITEMS:
            return False
        return self.tiles.applicable()

    # ---- Coordinates -----------------------------------------------------------------

    def to_scene(self, pos: QPointF | QPoint) -> QPointF:
        inverse, _ok = self.viewportTransform().inverted()
        return inverse.map(QPointF(pos))

    def to_viewport(self, scene_point: QPointF) -> QPointF:
        return self.viewportTransform().map(scene_point)

    def to_scene_polygon(self, rect: QRectF) -> QPolygonF:
        inverse, _ok = self.viewportTransform().inverted()
        return inverse.map(QPolygonF(rect))

    def visible_scene_rect(self) -> QRectF:
        return self.to_scene_polygon(QRectF(self.viewport().rect())).boundingRect()

    def _pick_target(self, node: NodeItemMixin) -> NodeItemMixin:
        """A click on a child of a compound item (a stroke in a drawing) picks the compound."""
        target = node
        parent = node.parentItem()
        while parent is not None:
            if isinstance(parent, NodeItemMixin) and parent.captures_children and not getattr(parent, "editing", False):
                target = parent
            parent = parent.parentItem()
        return target

    def node_item_at(self, pos: QPoint) -> Any:
        """Top-most visible content item under a viewport position."""
        for item in self.items(pos):
            node = item
            while node is not None and not isinstance(node, NodeItemMixin):
                node = node.parentItem()
            if node is not None and node.is_content and node.isVisible() and self.display.shows(node.node_name):
                return self._pick_target(node)
        return None

    def content_items_in(self, viewport_rect: QRectF) -> set[Any]:
        """Selectable content items intersecting a viewport rectangle (rubber band)."""
        path = QPainterPath()
        path.addPolygon(self.to_scene_polygon(viewport_rect))
        hits = set()
        for item in self.scene().items(path, Qt.IntersectsItemShape):
            if (isinstance(item, NodeItemMixin) and item.is_content and item.flags() & QGraphicsItem.ItemIsSelectable
                    and item.isVisible() and self.display.shows(item.node_name)):
                hits.add(item)
        return hits

    # ---- Camera ------------------------------------------------------------------------------

    def zoom_level(self) -> float:
        t = self.transform()
        return math.hypot(t.m11(), t.m12())

    def rotation(self) -> float:
        t = self.transform()
        return math.degrees(math.atan2(t.m12(), t.m11()))

    def center(self) -> QPointF:
        if self._pending_camera is not None:
            return QPointF(self._pending_camera[0])
        # QRectF centre (w/2, h/2), not QRect's ((w-1)/2): reading the camera back
        # after set_camera must give the same point, or bookmarks would drift.
        return self.to_scene(QRectF(self.viewport().rect()).center())

    @property
    def linked(self) -> set[str]:
        """Names of the other views sharing this view's camera."""
        return {v.view_name for v in self.app.linked_views(self)}

    def camera(self) -> dict[str, Any]:
        return {"center": [round(self._camera_center.x(), 3), round(self._camera_center.y(), 3)],
                "zoom": round(self._camera_zoom, 6), "rotation": round(self._camera_rotation, 4),
                "flip_x": self._camera_flip_x, "flip_y": self._camera_flip_y}

    def set_camera(self, center: Any = None, zoom: float | None = None, rotation: float | None = None,
                   animate: bool = False, flip_x: bool | None = None, flip_y: bool | None = None) -> None:
        target_center = QPointF(float(center[0]), float(center[1])) if center is not None else self._camera_center
        target_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, float(zoom))) if zoom is not None else self._camera_zoom
        target_rotation = float(rotation) if rotation is not None else self._camera_rotation
        target_flip_x = self._camera_flip_x if flip_x is None else bool(flip_x)
        target_flip_y = self._camera_flip_y if flip_y is None else bool(flip_y)
        if not self.isVisible():
            self._pending_camera = (QPointF(target_center), target_zoom, target_rotation, target_flip_x, target_flip_y)
            self._apply_camera(target_center, target_zoom, target_rotation, target_flip_x, target_flip_y)
            return
        self._pending_camera = None
        if animate:
            self._animate_camera(target_center, target_zoom, target_rotation, target_flip_x, target_flip_y)
            return
        self._stop_animation()
        self._apply_camera(target_center, target_zoom, target_rotation, target_flip_x, target_flip_y)

    def flip(self, horizontal: bool = True, vertical: bool = False) -> dict[str, Any]:
        self._camera_flip_x = self._camera_flip_x ^ bool(horizontal)
        self._camera_flip_y = self._camera_flip_y ^ bool(vertical)
        self._apply_camera(self._camera_center, self._camera_zoom, self._camera_rotation,
                           self._camera_flip_x, self._camera_flip_y)
        return self.camera()

    def _apply_pending_camera(self, clear: bool = True) -> None:
        pending = self._pending_camera
        if pending is None or not self.isVisible():
            return
        if clear:
            self._pending_camera = None
        self._apply_camera(*pending)

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        if self._pending_camera is not None:
            self._apply_pending_camera(clear=False)
            QTimer.singleShot(0, self._apply_pending_camera)

    def _apply_camera(self, center: QPointF, zoom: float, rotation: float,
                      flip_x: bool = False, flip_y: bool = False) -> None:
        self._camera_center = QPointF(center)
        self._camera_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, float(zoom)))
        self._camera_rotation = float(rotation)
        self._camera_flip_x = bool(flip_x)
        self._camera_flip_y = bool(flip_y)
        transform = QTransform()
        transform.rotate(self._camera_rotation)
        transform.scale(
            self._camera_zoom * (-1.0 if self._camera_flip_x else 1.0),
            self._camera_zoom * (-1.0 if self._camera_flip_y else 1.0),
        )
        self._applying_camera = True
        self.setTransform(transform)
        self.centerOn(self._camera_center)
        self._applying_camera = False
        self._camera_moved()

    def _stop_animation(self) -> None:
        """A direct camera change wins over a running camera animation (its next step would undo it)."""
        if self._animation is not None:
            self._animation.stop()
        self._glide_timer.stop()
        self._glide_target = None

    def _animate_camera(self, center: QPointF, zoom: float, rotation: float,
                       flip_x: bool = False, flip_y: bool = False, duration: int = 280) -> None:
        start_center, start_zoom, start_rotation = self._camera_center, self._camera_zoom, self._camera_rotation
        if self._animation is not None:
            self._animation.stop()
            self._animation.deleteLater()  # parented to the view: would pile up one per camera jump
        animation = QVariantAnimation(self)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setDuration(duration)

        def step(value: float) -> None:
            t = float(value)
            ease = t * t * (3 - 2 * t)
            z = start_zoom * (zoom / start_zoom) ** ease if start_zoom > 0 else zoom
            c = start_center + (center - start_center) * ease
            r = start_rotation + (rotation - start_rotation) * ease
            self._apply_camera(c, z, r, flip_x, flip_y)

        animation.valueChanged.connect(step)
        animation.finished.connect(lambda: self._apply_camera(center, zoom, rotation, flip_x, flip_y))
        self._animation = animation
        animation.start()

    def zoom_by(self, factor: float, around_cursor: bool = True) -> None:
        current = self.zoom_level() or 1.0
        target = max(self.MIN_ZOOM, min(self.MAX_ZOOM, current * factor))
        if abs(target - current) < 1e-12:
            return
        self._stop_animation()
        if not around_cursor:
            self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.scale(target / current, target / current)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self._camera_moved(sync_state=True)

    # ---- Smooth (wheel) zoom ---------------------------------------------------------------
    #
    # A wheel notch moves a target zoom; the camera eases towards it a little
    # every frame, keeping the point under the cursor in place. Notches arriving
    # mid-glide add to the target, so spinning the wheel zooms in one flowing move.

    GLIDE_TIME = 0.05  # seconds: the time constant of the glide (≈ 150 ms to settle)

    def glide_zoom(self, factor: float, pos: QPointF | None = None) -> None:
        """Zoom by ``factor`` smoothly about ``pos`` (viewport coordinates; default: the centre)."""
        if self._animation is not None:
            self._animation.stop()
        start = self._glide_target if self._glide_target is not None else (self.zoom_level() or 1.0)
        self._glide_target = max(self.MIN_ZOOM, min(self.MAX_ZOOM, start * factor))
        self._glide_anchor = QPointF(pos) if pos is not None else QRectF(self.viewport().rect()).center()
        # The scene point to keep under the anchor, fixed for the glide: measuring it
        # again each step would let whole-pixel scrolling round it away.
        self._glide_scene = self.to_scene(self._glide_anchor)
        if not self._glide_timer.isActive():
            self._glide_clock = time.perf_counter()
            self._glide_timer.start()
            self._glide_step()

    def _glide_step(self) -> None:
        target = self._glide_target
        current = self.zoom_level() or 1.0
        if target is None:
            self._glide_timer.stop()
            return
        now = time.perf_counter()
        elapsed, self._glide_clock = now - self._glide_clock, now
        remaining = math.log(target / current)
        if abs(remaining) < 0.003:  # within 0.3%: land on the target
            step = remaining
        else:
            step = remaining * (1.0 - math.exp(-max(elapsed, 0.004) / self.GLIDE_TIME))
        anchor, scene_point = self._glide_anchor, self._glide_scene
        factor = math.exp(step)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.scale(factor, factor)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        drift = self.to_viewport(scene_point) - anchor
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        hbar.setValue(hbar.value() + round(drift.x()))
        vbar.setValue(vbar.value() + round(drift.y()))
        self._camera_moved(sync_state=True)
        if step == remaining:
            self._glide_timer.stop()
            self._glide_target = None

    def set_zoom(self, level: float) -> None:
        self.set_camera(zoom=level)

    def fit_rect(self, rect: QRectF, margin: float = 40.0) -> None:
        if rect.isNull() or rect.width() <= 0.0 or rect.height() <= 0.0:
            return
        self._stop_animation()
        # The stored rotation, not rotation(): a flipped transform reads back as
        # rotation + 180°, which would turn a mirror into an upside-down view.
        rotation = self._camera_rotation
        self.fitInView(rect.adjusted(-margin, -margin, margin, margin), Qt.KeepAspectRatio)
        zoom = self.zoom_level()
        if abs(rotation) > 1e-6 or zoom > self.MAX_ZOOM or zoom < self.MIN_ZOOM:
            self._apply_camera(rect.center(), max(self.MIN_ZOOM, min(self.MAX_ZOOM, zoom)), rotation,
                               self._camera_flip_x, self._camera_flip_y)
        self._camera_moved(sync_state=True)

    def pan_by(self, delta: QPointF) -> None:
        self._stop_animation()
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        hbar.setValue(hbar.value() - round(delta.x()))
        vbar.setValue(vbar.value() - round(delta.y()))
        self._camera_moved(sync_state=True)

    def _camera_moved(self, *_args: Any, sync_state: bool = False) -> None:
        if (sync_state or _args) and not self._applying_camera:
            # Keep stored values aligned with viewport interaction (scroll/pan/zoom).
            self._camera_center = self.center()
            self._camera_zoom = self.zoom_level()
        self._gray_cache = None
        self._gray_cache_key = None
        self.camera_changed.emit()

    def resizeEvent(self, event) -> None:  # noqa: ANN001
        super().resizeEvent(event)
        if self._pending_camera is not None:
            self._apply_pending_camera(clear=False)
        self._gray_cache = None
        self._gray_cache_key = None
        self._camera_moved()

    # ---- Background ---------------------------------------------------------------------------

    def background_spec(self) -> BackgroundSpec:
        spec = self.app.background_spec()
        if self.display.background is not None:
            spec = BackgroundSpec(color=tuple(self.display.background), pattern=spec.pattern,
                                  grid_size=spec.grid_size, pattern_color=spec.pattern_color)
        if not self.display.show_grid:
            spec = BackgroundSpec(color=spec.color, pattern="none", grid_size=spec.grid_size,
                                  pattern_color=spec.pattern_color)
        return spec

    def drawBackground(self, painter: QPainter, rect) -> None:  # noqa: ANN001
        draw_background(painter, QRectF(rect), self.background_spec())

    def refresh_background(self) -> None:
        self._invalidate_gray_cache()
        self.tiles.clear()
        self.resetCachedContent()
        self.viewport().update()

    # ---- Pointer --------------------------------------------------------------------------

    def _dispatch_pointer(self, kind: str, event: Any) -> None:
        e = PointerEvent(self, kind, event)
        with self.app.canvas.event_guard():
            result = self.app.input.pointer(e)
            if result == HANDLED:
                event.accept()
            elif result == DEFAULT or result is None:
                getattr(super(), {"press": "mousePressEvent", "move": "mouseMoveEvent", "hover": "mouseMoveEvent",
                                  "release": "mouseReleaseEvent", "double": "mouseDoubleClickEvent"}[e.kind])(event)
            if e.kind == "hover":
                cursor = e.cursor
                tool = self.app.tools.active
                if cursor is None:
                    cursor = tool.current_cursor() if tool is not None else Qt.ArrowCursor
                self.viewport().setCursor(cursor)
            elif kind == "release" and self.app.input.captured(self) is None:
                tool = self.app.tools.active
                self.viewport().setCursor(tool.current_cursor() if tool is not None else Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton and event.modifiers() & Qt.AltModifier:
            pos = event.position()
            if pos.x() <= 16 or pos.x() >= self.width() - 16:
                self.flip(horizontal=True, vertical=False)
                event.accept()
                return
            if pos.y() <= 16 or pos.y() >= self.height() - 16:
                self.flip(horizontal=False, vertical=True)
                event.accept()
                return
        self.app.wm.focus_view_widget(self)
        self._dispatch_pointer("press", event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001
        self._dispatch_pointer("move", event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001
        self._dispatch_pointer("release", event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: ANN001
        self._dispatch_pointer("double", event)

    def leaveEvent(self, event) -> None:  # noqa: ANN001
        super().leaveEvent(event)
        self.app.input.leave(self)

    # ---- Keyboard & wheel --------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        with self.app.canvas.event_guard():
            result = self.app.input.key(KeyEvent(self, "press", event))
        if result == HANDLED:
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: ANN001
        result = self.app.input.key(KeyEvent(self, "release", event))
        if result == HANDLED:
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: ANN001
        self.app.input.leave(self)
        super().focusOutEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: ANN001
        e = WheelEvent(self, event)
        if e.steps == 0.0:
            event.accept()
            return
        with self.app.canvas.event_guard():
            self.app.input.wheel(e)
        event.accept()

    # ---- Drag and drop ------------------------------------------------------------------------

    def _ingest(self) -> Any:
        return self.app.session.services.get("ingest")

    def describe_drop(self, mime_data: QMimeData) -> str:
        ingest = self._ingest()
        return ingest.describe(mime_data) if ingest is not None else ""

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001
        ingest = self._ingest()
        if ingest is not None and ingest.accepts(event.mimeData()):
            self._drop_hint = ingest.describe(event.mimeData())
            self.viewport().update()
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001
        self._drop_hint = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: ANN001
        ingest = self._ingest()
        if ingest is not None and ingest.accepts(event.mimeData()):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:  # noqa: ANN001
        ingest = self._ingest()
        if ingest is None or not ingest.accepts(event.mimeData()):
            event.ignore()
            return
        scene_pos = self.to_scene(event.position())
        self._drop_hint = None
        self.viewport().update()
        event.acceptProposedAction()
        ingest.drop(self, scene_pos, event.mimeData())

    # ---- Painting ------------------------------------------------------------------------------

    def grab_clean(self, rect: QRect | None = None) -> QImage:
        """The rendered canvas without overlays (what the colour picker samples)."""
        image = self.render_frame(overlays=False)
        if rect is None:
            return image
        ratio = image.devicePixelRatio()
        part = image.copy(QRect(round(rect.x() * ratio), round(rect.y() * ratio),
                                max(1, round(rect.width() * ratio)), max(1, round(rect.height() * ratio))))
        part.setDevicePixelRatio(ratio)
        return part

    def render_frame(self, overlays: bool = True) -> QImage:
        """What the viewport shows, painted in software (grabs: an OpenGL viewport's own
        grab doesn't run the view's painting reliably)."""
        size = self.viewport().size()
        ratio = self.devicePixelRatioF()
        image = QImage(max(1, round(size.width() * ratio)), max(1, round(size.height() * ratio)),
                       QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(QColor(*self.background_spec().color))
        painter = QPainter(image)
        try:
            if self.display.grayscale:
                painter.drawImage(QPointF(0, 0), self._gray_image())
            else:
                self._paint_scene(painter)
            if overlays and not self._hide_overlay:
                painter.setRenderHint(QPainter.Antialiasing, True)
                self._paint_overlays(painter)
        finally:
            painter.end()
        return image

    def _paint_scene(self, painter: QPainter) -> None:
        """Background and items, in viewport coordinates: from tiles when they apply."""
        if self.uses_tiles():
            self.app.canvas.scene.flush_changes()
            self.tiles.paint(painter)
            return
        size = self.viewport().size()
        painter.setRenderHints(self.renderHints(), True)
        self._rendering_gray = True  # render() must not re-enter the greyscale path
        try:
            self.render(painter, QRectF(0, 0, size.width(), size.height()), self.viewport().rect())
        finally:
            self._rendering_gray = False

    def _scene_image(self, gray: bool = False) -> QImage:
        size = self.viewport().size()
        ratio = self.devicePixelRatioF()
        image = QImage(QSize(max(1, round(size.width() * ratio)), max(1, round(size.height() * ratio))),
                       QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        try:
            self._paint_scene(painter)
        finally:
            painter.end()
        if gray:
            image = image.convertToFormat(QImage.Format_Grayscale8)
            image.setDevicePixelRatio(ratio)
        return image

    def paintEvent(self, event) -> None:  # noqa: ANN001
        if self.display.grayscale and not self._rendering_gray:
            self._paint_grayscale()
            painter = QPainter(self.viewport())
        elif not self._rendering_gray and self.uses_tiles():
            self.app.canvas.scene.flush_changes()
            painter = QPainter(self.viewport())
            self.tiles.paint(painter)
        else:
            super().paintEvent(event)
            painter = QPainter(self.viewport())
        if self._hide_overlay or self._rendering_gray:
            painter.end()
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            self._paint_overlays(painter)
        finally:
            painter.end()

    def _paint_grayscale(self) -> None:
        target = QPainter(self.viewport())
        try:
            target.drawImage(QPointF(0, 0), self._gray_image())
        finally:
            target.end()

    def _gray_image(self) -> QImage:
        """The scene in greyscale, cached until the camera moves or anything repaints."""
        size = self.viewport().size()
        transform = self.viewportTransform()
        key = (max(1, size.width()), max(1, size.height()), self.devicePixelRatioF(), transform.m11(),
               transform.m12(), transform.m21(), transform.m22(), transform.m31(), transform.m32())
        if self._gray_cache is None or self._gray_cache_key != key:
            self._gray_cache = self._scene_image(gray=True)
            self._gray_cache_key = key
        return self._gray_cache

    def _paint_overlays(self, painter: QPainter) -> None:
        tool = self.app.tools.active
        if tool is not None and tool.exclusive_overlay:
            tool.paint_overlay(self, painter)
            return
        if self._drop_hint:
            self._paint_drop_hint(painter)
        elif (tool is not None and tool.id == self.app.tools.default_id and self.app.canvas.scene_is_empty()
              and (self.welcome_widget is None or not self.welcome_widget.isVisible())):
            self._paint_welcome(painter)
        if self._toast is not None:
            self._paint_toast(painter)
        if self.display.show_markers:
            self._paint_view_markers(painter)
        if self.display.show_overlays:
            if tool is not None:
                text = tool.banner(self)
                if text:
                    paint_banner(painter, self.viewport().width(), text)
            for _order, painter_fn in self.app.overlay_painters():
                painter_fn(self, painter)
        if tool is not None:
            tool.paint_overlay(self, painter)

    def _camera_names(self) -> list[str]:
        """Camera nodes (view markers), found again only when the canvas refreshed: walking
        every node on every frame cost more than drawing the markers on a big board."""
        nodes = self.app.canvas.nodes  # a new dict after each refresh
        if self._cameras_of is not nodes:
            self._cameras_of = nodes
            self._cameras = [name for name, node in nodes.items() if node.get("type") == "camera"]
        return self._cameras

    def _paint_view_markers(self, painter: QPainter) -> None:
        if not self.display.show_markers:
            return
        scene = self.app.session.scene
        # Every marker in the scene, in its colour, labelled "N  name" (N = its Ctrl+N key);
        # the view's current marker (what Update overwrites) is larger, ringed and bold.
        for index, name in enumerate(self._camera_names()):
            if not scene.has(name):
                continue
            node = scene.get_node(name)
            if node.name in self.display.hidden:
                continue
            if not bool(self.app.session.compute(node.name, "world_visibility")):
                continue
            center = node.attrs.get("translate") or node.attrs.get("center") or [0.0, 0.0]
            try:
                world = QPointF(float(center[0]), float(center[1]))
            except (TypeError, ValueError, IndexError):
                continue
            pos = self.to_viewport(world)
            active = self._current_marker == node.name
            radius = 8.0 if active else 6.0
            locked = bool(node.attrs.get("locked", 0))
            color = color_from(node.attrs.get("color"), (96, 178, 255))
            fill = QColor(color)
            fill.setAlpha(230 if active else 170)
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(QPen(color.darker(160), 1.5))
            painter.setBrush(fill)
            painter.drawEllipse(QRectF(pos.x() - radius, pos.y() - radius, radius * 2.0, radius * 2.0))
            if active:
                painter.setPen(QPen(QColor("#f3f6ff"), 2.0))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QRectF(pos.x() - radius - 3.5, pos.y() - radius - 3.5,
                                          (radius + 3.5) * 2.0, (radius + 3.5) * 2.0))
            label = (f"{index + 1}  " if index < 9 else "") + node.name + (" (locked)" if locked else "")
            font = painter.font()
            font.setBold(active)
            painter.setFont(font)
            text_rect = QRectF(pos.x() + radius + 6.0, pos.y() - 10.0, 260.0, 20.0)
            painter.setPen(QColor(0, 0, 0, 160))
            painter.drawText(text_rect.translated(1.0, 1.0), Qt.AlignLeft | Qt.AlignVCenter, label)
            painter.setPen(QColor("#f3f6ff") if active else color.lighter(130))
            painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, label)
            painter.restore()

    def _paint_drop_hint(self, painter: QPainter) -> None:
        rect = QRectF(self.viewport().rect()).adjusted(10, 10, -10, -10)
        painter.save()
        painter.setPen(QPen(ACCENT, 2.0, Qt.DashLine))
        painter.setBrush(QColor(61, 142, 240, 28))
        painter.drawRoundedRect(rect, 12, 12)
        font = painter.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(TEXT_LIGHT)
        painter.drawText(rect, Qt.AlignCenter, self._drop_hint or "")
        painter.restore()

    def _paint_welcome(self, painter: QPainter) -> None:
        """Centered hint on an empty board."""
        lines = self.app.welcome_lines()
        painter.save()
        metrics = painter.fontMetrics()
        width = max(metrics.horizontalAdvance(line) for line in lines) + 48
        height = 64 + 22 + len(lines) * (metrics.height() + 6) + 24
        rect = QRectF((self.viewport().width() - width) / 2.0, (self.viewport().height() - height) / 2.0, width, height)
        painter.setPen(QPen(QColor("#2e3240"), 1.0))
        painter.setBrush(QColor(20, 22, 27, 210))
        painter.drawRoundedRect(rect, 12, 12)
        logo = logo_pixmap(48, self.devicePixelRatioF())
        if not logo.isNull():
            painter.drawPixmap(QPointF(rect.center().x() - 24, rect.top() + 18), logo)
        title_font = painter.font()
        title_font.setBold(True)
        title_font.setPointSizeF(title_font.pointSizeF() * 1.25)
        painter.setFont(title_font)
        painter.setPen(QColor("#e6e9ef"))
        y = rect.top() + 18 + 56
        painter.drawText(QRectF(rect.left(), y, rect.width(), 24), Qt.AlignCenter, "Your board is empty")
        painter.setFont(self.font())
        y += 30
        for index, line in enumerate(lines):
            painter.setPen(QColor("#aeb4bf") if index == 0 else QColor("#6f7787"))
            painter.drawText(QRectF(rect.left(), y, rect.width(), metrics.height()), Qt.AlignCenter, line)
            y += metrics.height() + 6
        painter.restore()

    # ---- Toasts --------------------------------------------------------------------------------

    def show_toast(self, text: str, color: QColor | None = None, duration_ms: int = 2600) -> None:
        self._toast = {"text": text, "color": QColor(color) if color is not None else None}
        self._toast_timer.start(duration_ms)
        self.viewport().update()

    def _clear_toast(self) -> None:
        self._toast = None
        self.viewport().update()

    def _paint_toast(self, painter: QPainter) -> None:
        toast = self._toast
        metrics = painter.fontMetrics()
        has_swatch = toast["color"] is not None
        height = metrics.height() + 16
        width = metrics.horizontalAdvance(toast["text"]) + 28 + (height if has_swatch else 0)
        rect = QRectF((self.viewport().width() - width) / 2.0, self.viewport().height() - height - 18, width, height)
        painter.save()
        painter.setPen(QPen(ACCENT, 1.0))
        painter.setBrush(QColor(20, 24, 32, 240))
        painter.drawRoundedRect(rect, 7, 7)
        text_rect = rect.adjusted(14, 0, -14, 0)
        if has_swatch:
            swatch = QRectF(rect.left() + 10, rect.top() + 6, height - 12, height - 12)
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.setBrush(toast["color"])
            painter.drawRoundedRect(swatch, 3, 3)
            text_rect.setLeft(swatch.right() + 8)
        painter.setPen(TEXT_LIGHT)
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, toast["text"])
        painter.restore()


def cursor_pos_in(view: CanvasView) -> QPoint | None:
    pos = view.viewport().mapFromGlobal(QCursor.pos())
    return pos if view.viewport().rect().contains(pos) else None


