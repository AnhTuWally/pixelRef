"""Qt side of pages: the frame item, its resize points, the Page tool, the page tabs,
page navigation and the PDF export.

A page frame draws only on screen (a dashed outline), never into exports or
thumbnails. Its number tab above the top-left corner is painted as an overlay at
a fixed screen size: click it to select the page, drag it to move the page,
double-click it to name the page. The body lets clicks through to the board.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetricsF, QPainter, QPainterPath, \
    QPainterPathStroker, QPen, QTransform
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGraphicsItem, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QRadioButton, QVBoxLayout, QWidget)

from pixelref.plugin_api import Attr, CommandError
from pixelref.plugin_api.ui import HANDLED, InputHandler, NodeItemMixin, PointerEvent, Tool, theme, view_for
from pixelref.plugin_api.ui import widgets as _widgets

from . import DEFAULT_SIZE, FREE, PAGE, PAPERS, aspect, fit_aspect, page_names, paper_mm
from .pdf import PdfPage, parse_range, write_pdf

CORNERS = ("tl", "tr", "br", "bl")
EDGES = ("tm", "mr", "bm", "ml")
OPPOSITE = {"tl": "br", "tr": "bl", "br": "tl", "bl": "tr", "tm": "bm", "bm": "tm", "ml": "mr", "mr": "ml"}
MIN_HIT = 8.0  # border pick width (scene units)
TOP_Z = 1.0e6  # page frames draw above their siblings
TAB_HEIGHT = 20.0
DPI_CHOICES = (150, 300, 600)
BACKGROUNDS = (("board", "Board colour"), ("white", "White"), ("none", "Transparent"))


def local_handle_points(r: QRectF) -> dict[str, QPointF]:
    cx, cy = r.center().x(), r.center().y()
    return {"tl": r.topLeft(), "tr": r.topRight(), "br": r.bottomRight(), "bl": r.bottomLeft(),
            "tm": QPointF(cx, r.top()), "bm": QPointF(cx, r.bottom()), "ml": QPointF(r.left(), cy),
            "mr": QPointF(r.right(), cy)}


def orientation_symbol(orientation: str) -> str:
    return "▭" if orientation == "landscape" else "▯"


# ---- The item ------------------------------------------------------------------------------------


class PageFrameItem(NodeItemMixin, QGraphicsItem):
    """A page's outline. It holds nothing: the board under it is what the page shows."""

    in_exports = False  # image export / copy as image skip it

    def __init__(self, node_name: str) -> None:
        super().__init__()
        self._init_node(node_name, PAGE, None)
        self._size = QPointF(*DEFAULT_SIZE)
        self.paper = "A4"
        self.orientation = "landscape"
        self.title = ""
        self.export = True
        self.color = QColor(255, 170, 60)
        self.number = 0  # position in the page order (set after every refresh)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def apply_attrs(self, attrs: dict[str, Any]) -> None:
        self.prepareGeometryChange()
        size = attrs.get("size", DEFAULT_SIZE)
        self._size = QPointF(max(1.0, float(size[0])), max(1.0, float(size[1])))
        self.paper = str(attrs.get("paper", "A4"))
        self.orientation = str(attrs.get("orientation", "landscape"))
        self.title = str(attrs.get("title", ""))
        self.export = bool(attrs.get("export", True))
        self.color = _widgets.color_from(attrs.get("color", [255, 170, 60]))
        self.update()

    def label(self) -> str:
        paper = self.paper if self.paper == FREE else f"{self.paper} {orientation_symbol(self.orientation)}"
        parts = [str(self.number or "?"), paper]
        if self.title:
            parts.append(self.title)
        text = "  ·  ".join(parts)
        return text if self.export else text + "  (not exported)"

    # ---- Geometry -------------------------------------------------------------------------

    def content_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, self._size.x(), self._size.y())

    def boundingRect(self) -> QRectF:
        pad = MIN_HIT / 2.0 + 1.0
        return self.content_rect().adjusted(-pad, -pad, pad, pad)

    def shape(self) -> QPainterPath:
        """Only the border picks the page: clicks inside reach the board."""
        border = QPainterPath()
        border.addRect(self.content_rect())
        stroker = QPainterPathStroker()
        stroker.setWidth(MIN_HIT)
        return stroker.createStroke(border)

    def set_rect_size(self, width: float, height: float) -> None:
        self.prepareGeometryChange()
        self._size = QPointF(max(1.0, float(width)), max(1.0, float(height)))
        self.update()
        self._notify_moved()

    def commit_values(self) -> dict[str, Any]:
        values = self.transform_attrs()
        values["size"] = [round(self._size.x(), 3), round(self._size.y(), 3)]
        values["paper"] = self.paper
        values["orientation"] = self.orientation
        return values

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        return super().itemChange(change, value)

    # ---- Painting -------------------------------------------------------------------------

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        if view_for(widget) is None:
            return  # exports, thumbnails, the minimap: the page shows the board, not itself
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(self.color)
        if not self.export:
            color.setAlpha(110)
        fill = QColor(color)
        fill.setAlpha(14)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawRect(self.content_rect())
        pen = QPen(color, 1.6, Qt.DashLine)
        pen.setCosmetic(True)
        pen.setDashPattern([6.0, 4.0])
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.content_rect())


# ---- Resize points ---------------------------------------------------------------------------


class PageHandles:
    """Corners and edges resize the page; its paper keeps the shape (Shift: free shape, Alt: from centre)."""

    node_type = PAGE
    labels = {"page_resize": "Resize page"}

    def build(self, gizmo: Any, view: Any, item: Any, g: dict[str, Any]) -> None:
        st = item.sceneTransform()
        for name, point in local_handle_points(item.content_rect()).items():
            g["handles_vp"][name] = view.to_viewport(st.map(point))
        return None

    def hit(self, gizmo: Any, view: Any, g: dict[str, Any], pos: QPointF) -> str | None:
        for name in CORNERS + EDGES:
            point = g["handles_vp"].get(name)
            if point is not None and math.hypot(pos.x() - point.x(), pos.y() - point.y()) <= gizmo.HANDLE_HIT:
                return name
        return ""  # no rotate zone: pages stay upright

    def start(self, gizmo: Any, handle: str, g: dict[str, Any], item: Any) -> str | None:
        if handle in CORNERS + EDGES:
            return "page_resize"
        return None

    def drag(self, gizmo: Any, op: dict[str, Any], m: QPointF, modifiers: Any, view: Any) -> None:
        item, s0, l0, _parent_inv, _rotation = op["targets"][0]
        start = op["start_rect"]
        start_paper = op.setdefault("start_paper", item.paper)
        m_local = s0.inverted()[0].map(m)
        handle = op["handle"]
        about_center = bool(modifiers & Qt.AltModifier)
        if modifiers & Qt.ShiftModifier:
            item.paper = FREE
        else:
            item.paper = start_paper
        ratio = aspect(item.paper, item.orientation)
        points = local_handle_points(start)
        anchor = start.center() if about_center else points[OPPOSITE[handle]]
        x, y = m_local.x(), m_local.y()
        k = 2.0 if about_center else 1.0
        if handle in CORNERS:
            w, h = abs(x - anchor.x()) * k, abs(y - anchor.y()) * k
            if ratio is not None:
                w = max(w, h * ratio)
                h = w / ratio
            sx = 1.0 if x >= anchor.x() else -1.0
            sy = 1.0 if y >= anchor.y() else -1.0
            if about_center:
                rect = QRectF(anchor.x() - w / 2.0, anchor.y() - h / 2.0, w, h)
            else:
                rect = QRectF(QPointF(anchor.x(), anchor.y()), QPointF(anchor.x() + sx * w, anchor.y() + sy * h))
        elif handle in ("tm", "bm"):
            h = abs(y - anchor.y()) * k
            w = h * ratio if ratio is not None else start.width()
            top = anchor.y() - h / 2.0 if about_center else (anchor.y() if y >= anchor.y() else anchor.y() - h)
            rect = QRectF(start.center().x() - w / 2.0, top, w, h)
        else:
            w = abs(x - anchor.x()) * k
            h = w / ratio if ratio is not None else start.height()
            left = anchor.x() - w / 2.0 if about_center else (anchor.x() if x >= anchor.x() else anchor.x() - w)
            rect = QRectF(left, start.center().y() - h / 2.0, w, h)
        rect = rect.normalized()
        rect.setWidth(max(1.0, rect.width()))
        rect.setHeight(max(1.0, rect.height()))
        item.set_rect_size(rect.width(), rect.height())
        item.set_local_transform(QTransform.fromTranslate(rect.left(), rect.top()) * l0)
        shape = "free shape" if item.paper == FREE else f"{item.paper} kept · Shift: free shape"
        gizmo.ui.status(f"Page {rect.width():.0f} × {rect.height():.0f}  ({shape}, Alt: from centre)")

    def paint(self, gizmo: Any, view: Any, painter: QPainter, g: dict[str, Any]) -> bool:
        painter.setPen(QPen(theme.ACCENT, 1.5))
        painter.setBrush(QColor("#ffffff"))
        for name, point in g["handles_vp"].items():
            size = 5.0 if name in CORNERS else 4.0
            painter.drawEllipse(point, size, size)
        return True


# ---- The Page tool ---------------------------------------------------------------------------


def drag_box(start: QPointF, end: QPointF, paper: str, free: bool) -> tuple[QRectF, str]:
    """The page box a drag from ``start`` to ``end`` makes (scene units) and its orientation."""
    dx, dy = end.x() - start.x(), end.y() - start.y()
    orientation = "landscape" if abs(dx) >= abs(dy) else "portrait"
    if free or paper == FREE:
        return QRectF(start, end).normalized(), orientation
    w, h = fit_aspect(dx, dy, aspect(paper, orientation), cover=True)
    x = start.x() if dx >= 0 else start.x() - w
    y = start.y() if dy >= 0 else start.y() - h
    return QRectF(x, y, w, h), orientation


class PageTool(Tool):
    """Drag a box: a page appears. Its paper keeps the shape; the drag's direction picks landscape or portrait."""

    id = "page"
    title = "Page"
    action_text = "Page Tool"
    icon = "frame"
    tooltip = "Frame a page for PDF export (Alt+P) · drag a box, or click to drop one"
    keywords = "page pdf print frame paper a4 letter export slide layout"
    shortcuts = ("Alt+P",)
    order = 28.0  # after the annotation tools, before Pen
    cursor = Qt.CrossCursor
    gizmo = "handles"
    hint = "Drag to frame a page (Shift: free shape) · click to drop one · Esc / V to finish"
    options_title = "New pages"
    option_specs = (
        Attr("paper", "enum", "A4", choices=PAPERS, doc="Paper size of new pages (Free: any shape)"),
    )
    creates = PAGE

    def __init__(self, ui: Any) -> None:
        super().__init__(ui)
        self.op: dict[str, Any] | None = None

    @property
    def busy(self) -> bool:
        return self.op is not None

    def cancel(self) -> bool:
        if self.op is not None:
            self.op = None
            self.app.update_views()
            return True
        return False

    def banner(self, view: Any) -> str | None:
        return f"▭  Page · {self.options.get('paper')} · drag to frame a page, click to drop one   —   Esc / V to finish"

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind == "press" and e.button == Qt.LeftButton:
            self.op = {"start": e.scene_pos, "end": e.scene_pos, "view": e.view, "free": e.shift}
            return HANDLED
        if self.op is None:
            return None
        if e.kind == "move":
            self.op["end"] = e.view.to_scene(e.pos)
            self.op["free"] = e.shift
            e.view.viewport().update()
            return HANDLED
        if e.kind == "release":
            op, self.op = self.op, None
            view = op["view"]
            view.viewport().update()
            paper = str(self.options.get("paper"))
            a, b = view.to_viewport(op["start"]), view.to_viewport(op["end"])
            if math.hypot(a.x() - b.x(), a.y() - b.y()) > 8.0:
                rect, orientation = drag_box(op["start"], op["end"], paper, op["free"])
            else:
                # A click: drop a page half as wide as the view, centred on the click.
                visible = view.visible_scene_rect()
                orientation = "landscape" if visible.width() >= visible.height() else "portrait"
                w, h = fit_aspect(visible.width() * 0.5, visible.height() * 0.5, aspect(paper, orientation),
                                  cover=False)
                rect = QRectF(op["start"].x() - w / 2.0, op["start"].y() - h / 2.0, w, h)
            self.app.run_command("pages.create", rect.x(), rect.y(), rect.width(), rect.height(), paper,
                                 orientation)
            return HANDLED
        return None

    def paint_overlay(self, view: Any, painter: QPainter) -> None:
        if self.op is None or self.op["view"] is not view:
            return
        rect, orientation = drag_box(self.op["start"], self.op["end"], str(self.options.get("paper")),
                                     self.op["free"])
        box = QRectF(view.to_viewport(rect.topLeft()), view.to_viewport(rect.bottomRight())).normalized()
        color = QColor(255, 170, 60)
        painter.setPen(QPen(color, 1.5, Qt.DashLine))
        fill = QColor(color)
        fill.setAlphaF(0.12)
        painter.setBrush(fill)
        painter.drawRect(box)
        paper = str(self.options.get("paper"))
        text = "Free" if self.op["free"] or paper == FREE else f"{paper} {orientation}"
        _draw_tab(painter, box.topLeft(), text, color)


# ---- Page tabs (overlay + input) -------------------------------------------------------------


def _tab_font() -> QFont:
    font = QFont()
    font.setPixelSize(11)
    font.setBold(True)
    return font


def _tab_rect(top_left: QPointF, text: str) -> QRectF:
    width = QFontMetricsF(_tab_font()).horizontalAdvance(text) + 14.0
    return QRectF(top_left.x(), top_left.y() - TAB_HEIGHT, width, TAB_HEIGHT)


def _draw_tab(painter: QPainter, top_left: QPointF, text: str, color: QColor, selected: bool = False) -> QRectF:
    rect = _tab_rect(top_left, text)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QPen(theme.ACCENT, 1.5) if selected else Qt.NoPen)
    painter.setBrush(color)
    path = QPainterPath()
    path.addRoundedRect(rect.adjusted(0, 0, 0, 4), 4.0, 4.0)
    clip = QPainterPath()
    clip.addRect(rect)
    painter.drawPath(path.intersected(clip))
    painter.setFont(_tab_font())
    painter.setPen(QColor(20, 24, 32) if color.lightnessF() > 0.55 else QColor(255, 255, 255))
    painter.drawText(rect, Qt.AlignCenter, text)
    return rect


class PageTabs(InputHandler):
    """Draws each page's number tab and lets it be clicked (select), dragged (move) and double-clicked (name)."""

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.drag: dict[str, Any] | None = None

    def frames(self) -> list[PageFrameItem]:
        canvas = self.ui.canvas
        return list(canvas.cached("pages.frames", lambda: [
            i for i in canvas.items.values() if isinstance(i, PageFrameItem) and i.isVisible()]))

    def tabs(self, view: Any) -> list[tuple[QRectF, PageFrameItem]]:
        out = []
        for item in self.frames():
            if not view.display.shows(item.node_name):
                continue
            try:
                polygon = item.scene_polygon()
            except RuntimeError:
                continue
            corner = min((view.to_viewport(p) for p in polygon), key=lambda p: (p.y(), p.x()))
            out.append((_tab_rect(corner, item.label()), item))
        return out

    def tab_at(self, view: Any, pos: QPointF) -> PageFrameItem | None:
        for rect, item in reversed(self.tabs(view)):
            if rect.contains(pos):
                return item
        return None

    def paint(self, view: Any, painter: QPainter) -> None:
        if not view.display.show_overlays:
            return
        selected = set(self.ui.session.selection.names)
        for rect, item in self.tabs(view):
            color = QColor(item.color)
            if not item.export:
                color.setAlpha(140)
            _draw_tab(painter, rect.bottomLeft(), item.label(), color, item.node_name in selected)

    def hover(self, e: PointerEvent) -> None:
        if self.tab_at(e.view, e.pos) is not None:
            e.set_cursor(Qt.SizeAllCursor)

    def pointer(self, e: PointerEvent) -> str | None:
        canvas = self.ui.canvas
        if self.drag is not None:
            if e.kind == "move":
                scene_pos = e.view.to_scene(e.pos)
                delta = scene_pos - self.drag["last"]
                if not self.drag["moved"]:
                    a, b = self.drag["press_vp"], e.pos
                    if math.hypot(a.x() - b.x(), a.y() - b.y()) < 3.0:
                        return HANDLED
                    self.drag["moved"] = True
                    canvas.begin_interaction()
                canvas.translate_items([self.drag["item"]], delta.x(), delta.y())
                self.drag["last"] = scene_pos
                self.ui.app.update_views()
                return HANDLED
            if e.kind == "release":
                drag, self.drag = self.drag, None
                if drag["moved"]:
                    canvas.end_interaction("Move page")
                return HANDLED
            return HANDLED
        if e.kind not in ("press", "double") or e.button != Qt.LeftButton:
            return None
        item = self.tab_at(e.view, e.pos)
        if item is None:
            return None
        if e.kind == "double":
            rename_page(self.ui, item.node_name)
            return HANDLED
        session = self.ui.session
        names = list(session.selection.names)
        if e.shift or e.ctrl:
            names = [n for n in names if n != item.node_name] if item.node_name in names else names + [item.node_name]
        else:
            names = [item.node_name]
        session.selection.set(names)
        if not item.locked and not e.shift and not e.ctrl:
            self.drag = {"item": item, "last": e.scene_pos, "press_vp": e.pos, "moved": False}
        self.ui.app.update_views()
        return HANDLED

    def cancel(self) -> bool:
        if self.drag is None:
            return False
        drag, self.drag = self.drag, None
        if drag["moved"]:
            self.ui.canvas.cancel_interaction()
        return True


def rename_page(ui: Any, name: str) -> None:
    session = ui.session
    if not session.scene.has(name):
        return
    current = str(session.scene.get_node(name).attrs.get("title", ""))
    text, ok = QInputDialog.getText(ui.app.main_window(), "Page title", "Title (shown on the board):", text=current)
    if ok:
        ui.app.run_command("set_attr", f"{name}.title", text.strip())


# ---- PDF export dialog -----------------------------------------------------------------------


class PdfExportDialog(QDialog):
    def __init__(self, parent: QWidget | None, total: int, exported: int, selected: int, settings: Any) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Pages as PDF")
        self.settings = settings
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        pages_box = QWidget(self)
        pages_layout = QVBoxLayout(pages_box)
        pages_layout.setContentsMargins(0, 0, 0, 0)
        self.group = QButtonGroup(self)
        skipped = total - exported
        all_text = f"All pages ({exported})" + (f" — {skipped} switched off" if skipped else "")
        self.all = QRadioButton(all_text, pages_box)
        self.selected = QRadioButton(f"Selected pages ({selected})", pages_box)
        self.selected.setEnabled(selected > 0)
        range_row = QHBoxLayout()
        self.range = QRadioButton("Pages", pages_box)
        self.range_edit = QLineEdit(pages_box)
        self.range_edit.setPlaceholderText(f"e.g. 1-{max(1, total)}, or 1,3,5")
        self.range_edit.textEdited.connect(lambda _t: self.range.setChecked(True))
        range_row.addWidget(self.range)
        range_row.addWidget(self.range_edit, 1)
        for index, button in enumerate((self.all, self.selected, self.range)):
            self.group.addButton(button, index)
        pages_layout.addWidget(self.all)
        pages_layout.addWidget(self.selected)
        pages_layout.addLayout(range_row)
        (self.selected if selected > 0 else self.all).setChecked(True)
        form.addRow("Export", pages_box)

        self.dpi = QComboBox(self)
        for dpi in DPI_CHOICES:
            self.dpi.addItem(f"{dpi} dpi" + {150: " (smaller file)", 300: " (print)", 600: " (high)"}[dpi], dpi)
        stored_dpi = int(settings.value("dpi", 300, int))
        self.dpi.setCurrentIndex(max(0, self.dpi.findData(stored_dpi)))
        self.dpi.setToolTip("Resolution images are embedded at (text and shapes stay sharp at any zoom)")
        form.addRow("Image quality", self.dpi)

        self.background = QComboBox(self)
        for key, label in BACKGROUNDS:
            self.background.addItem(label, key)
        self.background.setCurrentIndex(max(0, self.background.findData(str(settings.value("background", "board")))))
        form.addRow("Background", self.background)

        self.open_after = QCheckBox("Open the PDF when done", self)
        self.open_after.setChecked(bool(settings.value("open_after", True, bool)))
        form.addRow("", self.open_after)

        self.error = QLabel(self)
        self.error.setStyleSheet("color: #d27b7b;")
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.button(QDialogButtonBox.Ok).setText("Export…")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.total = total

    def choice(self) -> str:
        return ("all", "selected", "range")[max(0, self.group.checkedId())]

    def _accept(self) -> None:
        if self.choice() == "range":
            try:
                if not parse_range(self.range_edit.text(), self.total):
                    raise ValueError(f"No pages between 1 and {self.total}")
            except ValueError as exc:
                self.error.setText(str(exc))
                self.error.show()
                return
        self.settings.setValue("dpi", int(self.dpi.currentData()))
        self.settings.setValue("background", str(self.background.currentData()))
        self.settings.setValue("open_after", self.open_after.isChecked())
        self.accept()


# ---- Activation ------------------------------------------------------------------------------


def activate_pages_ui(ui: Any) -> None:
    canvas = ui.canvas
    session = ui.session
    scene = session.scene
    app = ui.app

    def factory(_canvas: Any, node: dict[str, Any]) -> PageFrameItem:
        item = PageFrameItem(node["name"])
        item.apply_attrs(node.get("attrs", {}))
        return item

    def post_sync(item: Any, _node: dict[str, Any]) -> None:
        if item.zValue() != TOP_Z:
            item.setZValue(TOP_Z)
        order = page_names(scene)
        number = order.index(item.node_name) + 1 if item.node_name in order else 0
        if number != item.number:
            item.number = number
            item.update()

    def describe(name: str, node: dict[str, Any]) -> list[str]:
        attrs = node.get("attrs", {})
        w_mm, h_mm = paper_mm(str(attrs.get("paper", "A4")), str(attrs.get("orientation", "landscape")),
                              attrs.get("size"))
        order = page_names(scene)
        number = order.index(name) + 1 if name in order else 0
        return [f"Page {number} of {len(order)}", f"{w_mm:.0f} × {h_mm:.0f} mm"]

    def double_click(view: Any, item: Any, scene_pos: QPointF | None = None) -> bool:
        if not isinstance(item, PageFrameItem) or item.locked:
            return False
        rename_page(ui, item.node_name)
        return True

    ui.node_ui(PAGE, factory=factory, label="Page", icon="frame", badge=("#ffaa3c", "P"), describe=describe,
               post_sync=post_sync, double_click=double_click)
    ui.register_tool(PageTool(ui))
    ui.contribute("gizmo.handles", PageHandles())
    tabs = PageTabs(ui)
    ui.overlay(tabs.paint, order=40)
    ui.input_handler(tabs, 620)  # above the gizmo (600) and the active tool (500)
    ui.escape_handler(tabs.cancel, order=-10)

    def tool_paper() -> str:
        tool = app.tools.get("page")
        return str(tool.options.get("paper")) if tool is not None else "A4"

    def frame_items() -> list[PageFrameItem]:
        return [canvas.items[n] for n in page_names(scene) if isinstance(canvas.items.get(n), PageFrameItem)]

    # ---- Making pages ---------------------------------------------------------------------------

    def add_rect(rect: QRectF, paper: str, cover: bool) -> str:
        orientation = "landscape" if rect.width() >= rect.height() else "portrait"
        w, h = fit_aspect(rect.width(), rect.height(), aspect(paper, orientation), cover=cover)
        center = rect.center()
        return app.run_command("pages.create", center.x() - w / 2.0, center.y() - h / 2.0, w, h, paper, orientation)

    def add_from_view(paper: str | None = None) -> str | None:
        """Add a page framing what the active view shows (the Page tool's paper size)."""
        view = app.active_view()
        if view is None:
            raise CommandError("No canvas view")
        visible = view.visible_scene_rect()
        inset = 0.05 * min(visible.width(), visible.height())
        name = add_rect(visible.adjusted(inset, inset, -inset, -inset), paper or tool_paper(), cover=False)
        ui.status(f"Added page {len(page_names(scene))} from the view · drag its tab to move it")
        return name

    def add_around_selection(paper: str | None = None) -> str | None:
        """Add a page around the selection (the Page tool's paper size)."""
        items = [i for i in canvas.outline_items() if not isinstance(i, PageFrameItem)]
        if not items:
            raise CommandError("Select something to frame")
        bounds = canvas.scene_bounds(items)
        pad = 0.04 * max(bounds.width(), bounds.height())
        return add_rect(bounds.adjusted(-pad, -pad, pad, pad), paper or tool_paper(), cover=True)

    ui.command("add_from_view", add_from_view, undoable=False)
    ui.command("add_around_selection", add_around_selection, undoable=False)

    # ---- Going through the pages ----------------------------------------------------------------

    def current_index(view: Any, items: list[PageFrameItem]) -> int:
        """The page the view is showing (its centre closest to the view's), or -1."""
        if not items:
            return -1
        center = view.visible_scene_rect().center()
        best, best_distance = -1, math.inf
        for index, item in enumerate(items):
            c = item.scene_content_rect().center()
            d = math.hypot(c.x() - center.x(), c.y() - center.y())
            if d < best_distance:
                best, best_distance = index, d
        rect = items[best].scene_content_rect()
        return best if best_distance <= 0.25 * max(rect.width(), rect.height()) else -1

    def go_to(page: Any = None, step: int = 0) -> str | None:
        """Fit the active view to a page (name or number), or step to the next (1) / previous (-1) one."""
        view = app.active_view()
        items = frame_items()
        if view is None or not items:
            ui.status("No pages yet · Alt+P draws one, Ctrl+Alt+P frames the view")
            return None
        if page is not None:
            if isinstance(page, int) or str(page).isdigit():
                index = int(page) - 1
            else:
                names = [i.node_name for i in items]
                resolved = scene.resolve(str(page))
                if resolved not in names:
                    raise CommandError(f"Not a page: {page}")
                index = names.index(resolved)
        else:
            current = current_index(view, items)
            index = (0 if step >= 0 else len(items) - 1) if current < 0 else (current + step) % len(items)
        index = max(0, min(len(items) - 1, index))
        rect = items[index].scene_content_rect()
        view.fit_rect(rect, margin=0.03 * max(rect.width(), rect.height()))
        ui.status(f"Page {index + 1} of {len(items)}")
        return items[index].node_name

    ui.command("go_to", go_to, undoable=False, echo=False)
    ui.command("next", lambda: go_to(step=1), undoable=False, echo=False)
    ui.command("previous", lambda: go_to(step=-1), undoable=False, echo=False)

    # ---- PDF export -----------------------------------------------------------------------------

    def background_color(background: str) -> QColor | None:
        if background == "white":
            return QColor(255, 255, 255)
        if background == "none":
            return None
        return QColor(*app.background_spec().color)

    def export_pdf(path: str, pages: Any = None, dpi: int = 300, background: str = "board",
                   title: str | None = None) -> int:
        """Write pages to one PDF, one page each, in page order; returns the page count.

        ``pages``: None (every page switched on for export), a list of page names, or
        a range like "1-3,5". With no pages on the board, the active view becomes one page."""
        if background not in dict(BACKGROUNDS):
            raise CommandError("background must be 'board', 'white' or 'none'")
        items = frame_items()
        if pages is None:
            chosen = [i for i in items if i.export]
        elif isinstance(pages, str) and not scene.has(pages):
            try:
                chosen = [items[i] for i in parse_range(pages, len(items))]
            except ValueError as exc:
                raise CommandError(str(exc)) from exc
        else:
            wanted = [scene.resolve(n) for n in ([pages] if isinstance(pages, str) else pages)]
            by_name = {i.node_name: i for i in items}
            missing = [n for n in wanted if n not in by_name]
            if missing:
                raise CommandError(f"Not a page: {', '.join(missing)}")
            chosen = [by_name[n] for n in wanted]
        if items:
            if not chosen:
                raise CommandError("No pages to export")
            pdf_pages = [PdfPage(i.scene_content_rect(), paper_mm(i.paper, i.orientation,
                                                                  [i.content_rect().width(),
                                                                   i.content_rect().height()]))
                         for i in chosen]
        else:
            view = app.active_view()
            if view is None:
                raise CommandError("No pages and no view to export")
            visible = view.visible_scene_rect()
            pdf_pages = [PdfPage(visible, paper_mm(FREE, "landscape", [visible.width(), visible.height()]))]
        if title is None:
            title = session.scene_path.stem if session.scene_path is not None else "pixelRef"
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            return write_pdf(canvas, pdf_pages, str(path), dpi=dpi, background=background_color(background),
                             title=title, hide=[i for i in canvas.items.values() if isinstance(i, PageFrameItem)])
        except OSError as exc:
            raise CommandError(str(exc)) from exc

    def export_dialog() -> None:
        """Ask what to export and where, then write the PDF."""
        items = frame_items()
        parent = app.main_window()
        if not items:
            answer = QMessageBox.question(
                parent, "Export as PDF",
                "There are no pages on this board yet.\n\nExport what the view shows as a single page?\n\n"
                "(To make pages: Alt+P draws one, Ctrl+Alt+P frames the view.)",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Yes)
            if answer != QMessageBox.Yes:
                return
        selected_names = [n for n in session.selection.names if n in {i.node_name for i in items}]
        settings = ui.settings
        dialog = PdfExportDialog(parent, len(items), sum(1 for i in items if i.export), len(selected_names),
                                 settings)
        dialog.setStyleSheet(app.stylesheet)  # the dark theme is set per window, not app-wide
        if items and dialog.exec() != QDialog.Accepted:
            return
        choice = dialog.choice() if items else "all"
        pages: Any = None
        if choice == "selected":
            pages = [i.node_name for i in items if i.node_name in selected_names]
        elif choice == "range":
            pages = dialog.range_edit.text()
        if session.scene_path is not None:
            default = session.scene_path.with_suffix(".pdf")
        else:
            from ..file import save_dir

            default = save_dir(app.settings.scoped("plugins/file")) / "pages.pdf"
        path_str, _filter = QFileDialog.getSaveFileName(parent, "Export Pages as PDF", str(default), "PDF (*.pdf)")
        if not path_str:
            return
        if Path(path_str).suffix.lower() != ".pdf":
            path_str += ".pdf"
        dpi = int(settings.value("dpi", 300, int))
        background = str(settings.value("background", "board"))
        ui.status("Writing PDF…")
        app.main_window().repaint()
        try:
            count = export_pdf(path_str, pages, dpi, background)
        except CommandError as exc:
            QMessageBox.warning(parent, "PDF export failed", str(exc))
            return
        ui.status(f"Exported {count} page{'s' if count != 1 else ''} → {Path(path_str).name}")
        if bool(settings.value("open_after", True, bool)):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path_str))

    ui.command("export_pdf", export_pdf, alias="export_pdf", undoable=False)
    ui.command("export_dialog", export_dialog, undoable=False, echo=False)

    # ---- Context ---------------------------------------------------------------------------------

    def selected_pages() -> list[str]:
        # Asked by a dozen menu conditions on every selection change: once per change is enough.
        return list(ui.canvas.cached("pages.selected", lambda: [
            n for n in session.selection.names if scene.has(n) and scene.get_node(n).node_type == PAGE]))

    ui.context_key("pages.selected", lambda: bool(selected_pages()))
    ui.context_key("pages.single", lambda: len(selected_pages()) == 1)

    def move_selected(step: int) -> None:
        names = selected_pages()
        if len(names) == 1:
            app.run_command("pages.move", names[0], step)

    def toggle_export() -> None:
        names = selected_pages()
        if not names:
            return
        turn_on = not all(bool(scene.get_node(n).attrs.get("export", True)) for n in names)
        for name in names:
            app.run_command("set_attr", f"{name}.export", turn_on)

    def fly_to_selected() -> None:
        names = selected_pages()
        if names:
            go_to(names[0])

    ui.command("move_earlier", lambda: move_selected(-1), undoable=False, echo=False)
    ui.command("move_later", lambda: move_selected(1), undoable=False, echo=False)
    ui.command("toggle_export", toggle_export, undoable=False, echo=False)
    ui.command("fly_to_selected", fly_to_selected, undoable=False, echo=False)
    ui.command("rename_selected", lambda: [rename_page(ui, n) for n in selected_pages()[:1]],
               undoable=False, echo=False)

    # ---- Actions, keys, menus -------------------------------------------------------------------

    ui.action("pages.export_pdf", "pages.export_dialog", "Export Pages as PDF…", icon="export", category="File",
              keywords="pdf print pages document multi page export slides handout")
    ui.action("pages.add_from_view", "pages.add_from_view", "Add Page from View", icon="frame", category="Create",
              keywords="page pdf frame view camera capture print")
    ui.action("pages.add_around_selection", "pages.add_around_selection", "Add Page Around Selection",
              icon="frame", category="Create", keywords="page pdf frame selection print")
    ui.action("pages.next", "pages.next", "Next Page", category="View", keywords="page pdf present slide forward")
    ui.action("pages.previous", "pages.previous", "Previous Page", category="View",
              keywords="page pdf present slide back")
    ui.action("pages.move_earlier", "pages.move_earlier", "Move Page Earlier", category="Arrange",
              keywords="page order number", icon="chevron_up")
    ui.action("pages.move_later", "pages.move_later", "Move Page Later", category="Arrange",
              keywords="page order number", icon="chevron_down")
    ui.action("pages.toggle_export", "pages.toggle_export", "Include in PDF Export", category="File",
              keywords="page skip exclude pdf", icon="export",
              checked=lambda: bool(selected_pages()) and all(bool(scene.get_node(n).attrs.get("export", True))
                                                             for n in selected_pages()))
    ui.action("pages.fly_to", "pages.fly_to_selected", "Fit View to Page", category="View",
              keywords="page frame zoom fit", icon="frame")
    ui.action("pages.rename", "pages.rename_selected", "Rename Page…", category="Edit", keywords="page title name",
              icon="text")
    ui.action("pages.rotate", "pages.rotate", "Turn Page (Landscape / Portrait)", category="Arrange",
              keywords="page orientation landscape portrait rotate", icon="rotate_cw")
    ui.bind("Ctrl+Shift+P", "pages.export_pdf")
    ui.bind("Ctrl+Alt+P", "pages.add_from_view")
    ui.bind("PgDown", "pages.next", scope="canvas")
    ui.bind("PgUp", "pages.previous", scope="canvas")

    ui.menu("canvas", "pages.export_pdf", path="File", group="file.export", order=3)
    ui.menu("canvas", "pages.add_from_view", group="create", order=36, when="selection.count == 0")  # after Backdrop
    ui.menu("canvas", "pages.add_around_selection", group="group", order=40,  # with Group, Backdrop and Link
            when=lambda: bool(session.selection.names) and not selected_pages())
    for order, key in enumerate(("pages.fly_to", "pages.rename", "pages.rotate", "pages.move_earlier",
                                 "pages.move_later", "pages.toggle_export")):
        ui.menu("canvas", key, path="Page", group="page", order=order, when="pages.selected")
    ui.place_submenu("canvas", "Page", "item", 5, icon="frame")
    ui.place_submenu("canvas", "Page/Paper", "page.paper", 0, icon="paper")

    def set_paper_action(paper: str) -> None:
        names = selected_pages()
        if names:
            app.run_command("pages.set_paper", names, paper)

    for order, paper in enumerate(PAPERS):
        key = f"pages.paper.{paper}"
        ui.command(f"paper_{order}", lambda p=paper: set_paper_action(p), undoable=False, echo=False)
        ui.action(key, f"pages.paper_{order}", paper, category="hidden",
                  checked=lambda p=paper: bool(selected_pages()) and all(
                      scene.get_node(n).attrs.get("paper") == p for n in selected_pages()))
        ui.menu("canvas", key, path="Page/Paper", group="paper", order=order, when="pages.selected")

    from .panel import activate_pages_panel

    activate_pages_panel(ui, frame_items, go_to)
