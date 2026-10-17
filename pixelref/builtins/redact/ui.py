"""Qt side of blur/pixelate: the region item, its reshape points and the two tools.

A region paints the items stacked below it into an offscreen image at a
resolution set by its strength (not the zoom), then draws that image blurred
or as blocks. The result is cached; the cache is dropped when the scene
reports a change under the region, or when what's below it moves.
"""

from __future__ import annotations

import math
import weakref
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem

from pixelref.plugin_api.ui import HANDLED, NodeItemMixin, PointerEvent, Tool, theme, view_for

from . import CELL_SIZE, RADIUS

MAX_SOURCE_PIXELS = 4_000_000  # the offscreen render of what's below
OVERSAMPLE = 4  # source pixels per result pixel (so thin lines and text average in, not drop out)

CORNERS = ("tl", "tr", "br", "bl")
EDGES = ("tm", "mr", "bm", "ml")
OPPOSITE = {"tl": "br", "tr": "bl", "br": "tl", "bl": "tr", "tm": "bm", "bm": "tm", "ml": "mr", "mr": "ml"}

# Every live region item, so scene changes can reach them.
_regions: "weakref.WeakSet[RegionItem]" = weakref.WeakSet()


def local_handle_points(r: QRectF) -> dict[str, QPointF]:
    cx, cy = r.center().x(), r.center().y()
    return {"tl": r.topLeft(), "tr": r.topRight(), "br": r.bottomRight(), "bl": r.bottomLeft(),
            "tm": QPointF(cx, r.top()), "bm": QPointF(cx, r.bottom()), "ml": QPointF(r.left(), cy),
            "mr": QPointF(r.right(), cy)}


def _transform_key(t: QTransform) -> tuple[float, ...]:
    return (round(t.m11(), 6), round(t.m12(), 6), round(t.m21(), 6), round(t.m22(), 6),
            round(t.dx(), 3), round(t.dy(), 3))


def _shrink(image: QImage, size: QSize) -> QImage:
    """``image`` averaged down to ``size`` (halving first: one big smooth scale skips pixels)."""
    while image.width() / 2 >= size.width() and image.height() / 2 >= size.height():
        image = image.scaled(max(1, image.width() // 2), max(1, image.height() // 2),
                             Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    if image.size() != size:
        image = image.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return image


class RegionItem(NodeItemMixin, QGraphicsItem):
    """A rectangle showing what's below it blurred (``mode="blur"``) or pixelated."""

    def __init__(self, node_name: str, node_type: str, mode: str) -> None:
        super().__init__()
        self._init_node(node_name, node_type, None)
        self.mode = mode
        self._size = QPointF(120.0, 80.0)
        self._amount = float(RADIUS.default if mode == "blur" else CELL_SIZE.default)
        self.revision = 0  # bumped when something under the region changed
        self._cache: tuple[Any, QImage, QRectF] | None = None  # (key, image, the local rect it covers)
        self._below_items: list[QGraphicsItem] | None = None  # None: find them again
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        _regions.add(self)

    def apply_attrs(self, attrs: dict[str, Any]) -> None:
        self.prepareGeometryChange()
        size = attrs.get("size", [120.0, 80.0])
        self._size = QPointF(max(1.0, float(size[0])), max(1.0, float(size[1])))
        if self.mode == "blur":
            self._amount = max(1.0, float(attrs.get("radius", RADIUS.default)))
        else:
            self._amount = max(2.0, float(attrs.get("cell_size", CELL_SIZE.default)))
        self._cache = None
        self._below_items = None
        self.update()

    is_rect = True

    def set_rect_size(self, width: float, height: float) -> None:
        self.prepareGeometryChange()
        self._size = QPointF(max(1.0, float(width)), max(1.0, float(height)))
        self._below_items = None
        self.update()
        self._notify_moved()

    def commit_values(self) -> dict[str, Any]:
        values = self.transform_attrs()
        values["size"] = [round(self._size.x(), 3), round(self._size.y(), 3)]
        return values

    def content_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, self._size.x(), self._size.y())

    def boundingRect(self) -> QRectF:
        return self.content_rect()

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addRect(self.content_rect())
        return path

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        if change in (QGraphicsItem.ItemPositionHasChanged, QGraphicsItem.ItemTransformHasChanged,
                      QGraphicsItem.ItemZValueHasChanged, QGraphicsItem.ItemParentHasChanged,
                      QGraphicsItem.ItemSceneHasChanged):
            self._below_items = None  # something else may be under it now
        return super().itemChange(change, value)

    # ---- What's below ------------------------------------------------------------------------
    #
    # Qt's item index can't be queried while the scene is painting (scene.render,
    # which exports and tile caches use, gets nothing back), so the items below
    # are found when the scene reports a change, outside painting. When that list
    # is out of date at paint time, it's found by scanning every item instead.

    def sample_rect(self) -> QRectF:
        """The local area whose content the region uses (a blur reaches past its edges)."""
        pad = 2.0 * self._amount if self.mode == "blur" else 0.0
        return self.content_rect().adjusted(-pad, -pad, pad, pad)

    def find_below(self, indexed: bool = True) -> list[QGraphicsItem]:
        """The node items stacked below this one that overlap it, bottom first."""
        scene = self.scene()
        if scene is None:
            return []
        area = self.sceneTransform().map(QPolygonF(self.sample_rect()))
        if indexed:
            candidates = scene.items(area, Qt.IntersectsItemBoundingRect, Qt.AscendingOrder)
        else:
            bounds = area.boundingRect()
            candidates = [item for item in scene.items(Qt.AscendingOrder)
                          if item is self or item.sceneBoundingRect().intersects(bounds)]
        below = []
        for other in candidates:
            if other is self:
                break
            if isinstance(other, NodeItemMixin):
                below.append(other)
        return below

    def shown_below(self, widget: Any) -> list[QGraphicsItem]:
        """The items below that are drawn (for the view being painted)."""
        if self._below_items is None:
            self._below_items = self.find_below(indexed=False)
        view = view_for(widget)
        shown = []
        for other in self._below_items:
            if other.scene() is not self.scene() or not other.isVisible() or                     other.flags() & QGraphicsItem.ItemHasNoContents or other.effectiveOpacity() <= 0.0:
                continue
            if view is not None and not view.display.shows(other.node_name):
                continue
            shown.append(other)
        return shown

    def changed_below(self, rects: list[QRectF]) -> None:
        """The scene reports ``rects`` changed: drop the cache if any of them is under the region."""
        area = self.mapRectToScene(self.sample_rect())
        partial = False
        hit = False
        for rect in rects:
            if not rect.intersects(area) or self._own_rect(rect):
                continue
            hit = True
            partial = partial or not rect.contains(area)
        if not hit:
            return
        self.revision += 1
        below = self.find_below()
        if partial or [id(i) for i in below] != [id(i) for i in self._below_items or ()]:
            # Views only repaint the changed area, but a blur or a block straddling
            # its edge changes outside it too; and new items under it change it all.
            self.update()
        self._below_items = below

    def _own_rect(self, rect: QRectF) -> bool:
        """``rect`` is this region's own repaint, or that of a region above it (neither
        changes what's below: ignoring them keeps overlapping regions from repainting
        each other forever)."""
        def same(a: QRectF, b: QRectF) -> bool:
            return a.contains(b) and b.adjusted(-3, -3, 3, 3).contains(a)

        if same(rect, self.sceneBoundingRect()):
            return True
        below = {id(item) for item in self._below_items or ()}
        return any(other is not self and id(other) not in below and other.scene() is self.scene()
                   and same(rect, other.sceneBoundingRect()) for other in list(_regions))

    # ---- Painting ----------------------------------------------------------------------------

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        below = self.shown_below(widget)
        rect = self.content_rect()
        if not below:
            self._cache = None
            if view_for(widget) is not None:  # on screen, show where the empty region is
                width = 1.0 / max(1e-6, math.hypot(painter.worldTransform().m11(), painter.worldTransform().m12()))
                painter.setPen(QPen(QColor(150, 150, 150, 160), width, Qt.DashLine))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(rect)
            return
        key = (self.revision, self.mode, self._amount, rect.width(), rect.height(),
               _transform_key(self.sceneTransform()),
               tuple((id(item), _transform_key(item.sceneTransform()), round(item.effectiveOpacity(), 4))
                     for item in below))
        if self._cache is None or self._cache[0] != key:
            image, target = self._render(below, widget)
            self._cache = (key, image, target)
        _key, image, target = self._cache
        painter.save()
        painter.setClipRect(rect, Qt.IntersectClip)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, self.mode == "blur")
        painter.drawImage(target, image)
        painter.restore()

    def _render(self, below: list[QGraphicsItem], widget: Any) -> tuple[QImage, QRectF]:
        """(result image, the local rect it covers)."""
        rect = self.content_rect()
        if self.mode == "blur":
            # Sample beyond the edges, so the blur there mixes in what's around.
            pad = 2.0 * self._amount
            source = rect.adjusted(-pad, -pad, pad, pad)
            scale = min(1.0, 2.0 / self._amount)  # result pixels per local unit
        else:
            cell = self._amount
            columns, rows = math.ceil(rect.width() / cell), math.ceil(rect.height() / cell)
            source = QRectF(0.0, 0.0, columns * cell, rows * cell)
            scale = 1.0 / cell
        size = QSize(max(1, math.ceil(source.width() * scale)), max(1, math.ceil(source.height() * scale)))
        render_scale = scale * OVERSAMPLE
        while source.width() * source.height() * render_scale * render_scale > MAX_SOURCE_PIXELS and \
                render_scale > scale:
            render_scale = max(scale, render_scale / 2.0)
        image = self._render_below(below, widget, source, render_scale)
        result = _shrink(image, size)
        if self.mode == "blur":
            # One more halving and back: the upscale when drawn spreads it into a smooth blur.
            half = _shrink(result, QSize(max(1, size.width() // 2), max(1, size.height() // 2)))
            result = half.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        # Whole result pixels: the rect is a hair bigger than ``source`` when it isn't a multiple.
        return result, QRectF(source.x(), source.y(), size.width() / scale, size.height() / scale)

    def _render_below(self, below: list[QGraphicsItem], widget: Any, source: QRectF, scale: float) -> QImage:
        width, height = max(1, math.ceil(source.width() * scale)), max(1, math.ceil(source.height() * scale))
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        to_image = QTransform.fromTranslate(-source.x(), -source.y()) * QTransform.fromScale(scale, scale)
        to_local = self.sceneTransform().inverted()[0]
        for item in below:
            painter.save()
            painter.setTransform(item.sceneTransform() * to_local * to_image)
            painter.setOpacity(item.effectiveOpacity())
            option = QStyleOptionGraphicsItem()
            option.exposedRect = item.boundingRect()
            item.paint(painter, option, widget)
            painter.restore()
        painter.end()
        return image


class RegionHandles:
    """A selected region's corner and edge points reshape it (rather than scale it)."""

    labels = {"region_resize": "Resize region"}

    def __init__(self, node_type: str) -> None:
        self.node_type = node_type

    def build(self, gizmo: Any, view: Any, item: Any, g: dict[str, Any]) -> None:
        st = item.sceneTransform()
        for name, point in local_handle_points(item.content_rect()).items():
            g["handles_vp"][name] = view.to_viewport(st.map(point))

    def start(self, gizmo: Any, handle: str, g: dict[str, Any], item: Any) -> str | None:
        return "region_resize" if handle in CORNERS + EDGES else None

    def drag(self, gizmo: Any, op: dict[str, Any], m: QPointF, modifiers: Any, view: Any) -> None:
        item, s0, l0, _parent_inv, _rotation = op["targets"][0]
        start = op["start_rect"]
        m_local = s0.inverted()[0].map(m)
        handle = op["handle"]
        about_center = bool(modifiers & Qt.AltModifier)
        anchor = start.center() if about_center else local_handle_points(start)[OPPOSITE[handle]]
        x, y = m_local.x(), m_local.y()
        if handle in CORNERS and modifiers & Qt.ShiftModifier:
            side = max(abs(x - anchor.x()), abs(y - anchor.y()))
            x = anchor.x() + math.copysign(side, (x - anchor.x()) or 1.0)
            y = anchor.y() + math.copysign(side, (y - anchor.y()) or 1.0)
        if handle in ("tm", "bm"):
            top, bottom = (2 * anchor.y() - y, y) if about_center else (anchor.y(), y)
            rect = QRectF(QPointF(start.left(), top), QPointF(start.right(), bottom)).normalized()
        elif handle in ("ml", "mr"):
            left, right = (2 * anchor.x() - x, x) if about_center else (anchor.x(), x)
            rect = QRectF(QPointF(left, start.top()), QPointF(right, start.bottom())).normalized()
        else:
            corner_a = QPointF(2 * anchor.x() - x, 2 * anchor.y() - y) if about_center else anchor
            rect = QRectF(corner_a, QPointF(x, y)).normalized()
        item.set_rect_size(max(1.0, rect.width()), max(1.0, rect.height()))
        item.set_local_transform(QTransform.fromTranslate(rect.left(), rect.top()) * l0)
        gizmo.ui.status(f"Region {rect.width():.0f} × {rect.height():.0f}px (Shift: square, Alt: from centre)")

    def paint(self, gizmo: Any, view: Any, painter: QPainter, g: dict[str, Any]) -> bool:
        painter.setPen(QPen(theme.ACCENT, 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(QPolygonF(g["corners_vp"]))
        painter.drawLine(g["top_mid_vp"], g["rotate_vp"])
        painter.setPen(QPen(theme.ACCENT, 1.5))
        painter.setBrush(QColor("#ffffff"))
        for name, point in g["handles_vp"].items():
            size = 5.0 if name in CORNERS else 4.0
            painter.drawEllipse(point, size, size)
        painter.drawEllipse(g["rotate_vp"], 5.0, 5.0)
        return True


class RegionTool(Tool):
    """Drag out a region (Shift: square, Alt: from the centre)."""

    cursor = Qt.CrossCursor
    gizmo = "handles"
    command = ""
    label = ""

    def __init__(self, ui: Any) -> None:
        super().__init__(ui)
        self.op: dict[str, Any] | None = None

    @property
    def busy(self) -> bool:
        return self.op is not None

    def cancel(self) -> bool:
        if self.op is not None:
            self.op = None
            return True
        return False

    def banner(self, view: Any) -> str | None:
        return f"{self.label} · drag over what to hide (Shift square, Alt from centre)   —   Esc / V to finish"

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind == "press" and e.button == Qt.LeftButton:
            self.op = {"start": e.scene_pos, "end": e.scene_pos, "origin": e.scene_pos, "view": e.view}
            return HANDLED
        if self.op is None:
            return None
        if e.kind == "move":
            origin = self.op["origin"]
            end = e.view.to_scene(e.pos)
            if e.shift:
                dx, dy = end.x() - origin.x(), end.y() - origin.y()
                side = max(abs(dx), abs(dy))
                end = QPointF(origin.x() + math.copysign(side, dx or 1.0), origin.y() + math.copysign(side, dy or 1.0))
            self.op["start"] = QPointF(2 * origin.x() - end.x(), 2 * origin.y() - end.y()) if e.alt else origin
            self.op["end"] = end
            e.view.viewport().update()
            return HANDLED
        if e.kind == "release":
            op, self.op = self.op, None
            view = op["view"]
            e.view.viewport().update()
            a, b = view.to_viewport(op["start"]), view.to_viewport(op["end"])
            if math.hypot(a.x() - b.x(), a.y() - b.y()) > 4.0:
                rect = QRectF(op["start"], op["end"]).normalized()
                self.app.run_command(self.command, rect.x(), rect.y(), rect.width(), rect.height(),
                                     **self.options.as_dict())
            return HANDLED
        return None

    def paint_overlay(self, view: Any, painter: QPainter) -> None:
        if self.op is None or self.op["view"] is not view:
            return
        rect = QRectF(view.to_viewport(self.op["start"]), view.to_viewport(self.op["end"])).normalized()
        painter.setPen(QPen(QColor(0, 0, 0, 160), 1.0))
        painter.setBrush(QColor(255, 255, 255, 40))
        painter.drawRect(rect)
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1.0, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)


class BlurTool(RegionTool):
    id = "blur"
    title = "Blur"
    action_text = "Blur Tool"
    icon = "blur"
    tooltip = "Blur a region (B) · hides what's under it"
    keywords = "redact obscure hide censor soften"
    shortcuts = ("B",)
    order = 24.0
    creates = "annotation_blur"
    command = "redact.blur"
    label = "Blur"
    option_specs = (RADIUS,)
    options_title = "Blur options"
    hint = "Drag to blur a region (Shift: square, Alt: from centre) · Esc / V to finish"


class PixelateTool(RegionTool):
    id = "pixelate"
    title = "Pixelate"
    action_text = "Pixelate Tool"
    icon = "pixelate"
    tooltip = "Pixelate a region (X) · hides what's under it with blocks"
    keywords = "redact obscure hide censor mosaic"
    shortcuts = ("X",)
    order = 25.0
    creates = "annotation_pixelate"
    command = "redact.pixelate"
    label = "Pixelate"
    option_specs = (CELL_SIZE,)
    options_title = "Pixelate options"
    hint = "Drag to pixelate a region (Shift: square, Alt: from centre) · Esc / V to finish"


def activate_redact_ui(ui: Any) -> None:
    def factory(node_type: str, mode: str) -> Any:
        def make(_canvas: Any, node: dict[str, Any]) -> RegionItem:
            item = RegionItem(node["name"], node_type, mode)
            item.apply_attrs(node.get("attrs", {}))
            return item

        return make

    ui.node_ui("annotation_blur", factory=factory("annotation_blur", "blur"), label="Blur", icon="blur",
               badge=("#7b9bd2", "B"))
    ui.node_ui("annotation_pixelate", factory=factory("annotation_pixelate", "pixelate"), label="Pixelate",
               icon="pixelate", badge=("#7b9bd2", "X"))
    ui.register_tool(BlurTool(ui))
    ui.register_tool(PixelateTool(ui))
    ui.contribute("gizmo.handles", RegionHandles("annotation_blur"))
    ui.contribute("gizmo.handles", RegionHandles("annotation_pixelate"))

    scene = ui.canvas.scene

    def on_changed(rects: list[QRectF]) -> None:
        for item in list(_regions):
            if item.scene() is scene:
                item.changed_below(rects)

    scene.content_changed.connect(on_changed)
    ui.track(lambda: scene.content_changed.disconnect(on_changed))
