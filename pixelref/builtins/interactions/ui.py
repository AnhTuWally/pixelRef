"""Qt side of interactions: the button item, clicking on the canvas, Demonstrate's banner,
connecting by dragging, the Attribute Editor's Click Action section, menus and keys."""

from __future__ import annotations

import html
import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGraphicsItem,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from pixelref.plugin_api import CommandError
from pixelref.plugin_api.contracts import InspectorSection, QuickSection, SelectionInfo
from pixelref.plugin_api.ui import HANDLED, InputHandler, NodeItemMixin, PointerEvent, icon, theme
from pixelref.plugin_api.ui import widgets as w

from . import BUTTON_TYPE

color_from = w.color_from
DEFAULTS = {spec.name: spec.default for spec in BUTTON_TYPE.attrs}
GREEN = QColor(90, 205, 140)
AMBER = QColor(255, 176, 32)
PROBLEMS = ("partial", "failed", "broken")


# ---- The button ---------------------------------------------------------------------------------


class ButtonItem(NodeItemMixin, QGraphicsItem):
    """A rounded button with a label; outlined in its "when on" colour while its action is on."""

    def __init__(self, node_name: str) -> None:
        super().__init__()
        self._init_node(node_name, "button", None)
        self._values: dict[str, Any] = dict(DEFAULTS)
        self._on = False
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def apply_attrs(self, attrs: dict[str, Any]) -> None:
        self.prepareGeometryChange()
        self._values = {name: attrs.get(name, default) for name, default in DEFAULTS.items()}
        self._on = bool(attrs.get("action_on", False))
        self.update()

    def size(self) -> tuple[float, float]:
        width, height = self._values["size"]
        return max(1.0, float(width)), max(1.0, float(height))

    def content_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, *self.size())

    def boundingRect(self) -> QRectF:
        return self.content_rect().adjusted(-6.0, -6.0, 6.0, 6.0)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        radius = float(self._values["corner_radius"])
        path.addRoundedRect(self.content_rect(), radius, radius)
        return path

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        return super().itemChange(change, value)

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        rect = self.content_rect()
        radius = min(float(self._values["corner_radius"]), rect.height() / 2.0, rect.width() / 2.0)
        fill = color_from(self._values["fill_color"])
        active = color_from(self._values["active_color"])
        if self._on:
            painter.setPen(QPen(active, 5.0))
        else:
            painter.setPen(QPen(fill.darker(135), 2.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, radius, radius)
        text = str(self._values["label"])
        font = QFont(painter.font())
        font.setBold(True)
        font.setPixelSize(max(1, round(float(self._values["font_size"]))))
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        text_rect = rect.adjusted(14.0, 0.0, -14.0, 0.0)
        if self._on:
            dot = min(10.0, rect.height() / 6.0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(active)
            painter.drawEllipse(QPointF(rect.left() + 12.0 + dot, rect.center().y()), dot, dot)
            text_rect.adjust(2.0 * dot + 6.0, 0.0, 0.0, 0.0)
        painter.setPen(color_from(self._values["text_color"]))
        painter.drawText(text_rect, Qt.AlignCenter, metrics.elidedText(text, Qt.ElideRight, text_rect.width()))


# ---- Clicking, Demonstrate's banner, connecting --------------------------------------------------


class ClickController(InputHandler):
    """Everything the plugin does with the pointer on the canvas.

    * A plain click on a locked item that has a click action runs it. Unlocked items are for
      editing: their click selects (Run in the quick editor bar tries the action).
    * An item whose click action has a problem gets an amber dashed outline.
    * The ⚡ handle beside a selected button (or an item with an action): drag it onto another
      item and pick what the click does to it. "Connect…" does the same with a click.
    * While demonstrating, the banner's Done / Cancel / "Include this view".
    """

    HANDLE = 11.0

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.session = ui.session
        self.api = ui.service("interactions")
        self.include_view = False
        self.picking: str | None = None  # Connect…: the item whose target the next click picks
        self.wire: dict[str, Any] | None = None  # dragging from the ⚡ handle: {"node", "view", "pos"}
        self.highlight: list[str] = []  # the Attribute Editor's hovered step: its targets
        self.pressed: str | None = None  # the locked item a press ran, until the release
        self._problems: tuple[Any, list[str]] = (None, [])

    # ---- Geometry ---------------------------------------------------------------------------------

    def has_action(self, name: str) -> bool:
        node = self.session.scene.nodes.get(name)
        return node is not None and bool(node.attrs.get("on_click"))

    def viewport_rect(self, view: Any, name: str) -> QRectF | None:
        item = self.ui.canvas.item(name)
        if item is None or not item.isVisible() or not view.display.shows(name):
            return None
        try:
            return view.mapFromScene(item.sceneBoundingRect()).boundingRect().toRectF()
        except RuntimeError:
            return None

    def clickable_at(self, view: Any, pos: QPointF) -> str | None:
        """The locked item with a click action under ``pos`` (what a plain click runs)."""
        if self.api.demo is not None:
            return None  # while demonstrating, the board is edited, not clicked
        name = self.action_item_at(view, pos)
        return name if name is not None and getattr(self.ui.canvas.item(name), "locked", False) else None

    def handle_center(self, view: Any) -> tuple[str, QPointF] | None:
        """The ⚡ handle: beside the one selected button, or item with an action."""
        if self.api.demo is not None or not view.display.show_overlays:
            return None
        names = self.session.selection.names
        if len(names) != 1:
            return None
        name = names[0]
        node = self.session.scene.nodes.get(name)
        if node is None or (node.node_type != "button" and not node.attrs.get("on_click")):
            return None
        rect = self.viewport_rect(view, name)
        if rect is None:
            return None
        return name, QPointF(rect.right() + 26.0, rect.center().y())

    def action_item_at(self, view: Any, pos: QPointF) -> str | None:
        """The item with a click action under ``pos``: the top-most item there, or an ancestor of it."""
        item = view.node_item_at(pos.toPoint())
        hits = [i for i in view.items(pos.toPoint()) if isinstance(i, NodeItemMixin)]
        for candidate in ([item] if item is not None else []) + hits[:1]:
            current = candidate
            while current is not None:
                if isinstance(current, NodeItemMixin) and self.has_action(current.node_name):
                    return current.node_name
                current = current.parentItem()
        return None

    def target_at(self, view: Any, pos: QPointF, exclude: str | None) -> str | None:
        """The item to connect to under ``pos``: what a click would pick there, else the top-most
        item whose box contains it (an unfilled rectangle is only hit on its outline)."""
        item = view.node_item_at(pos.toPoint())
        if item is not None and item.node_name != exclude:
            return item.node_name
        point = view.to_scene(pos)
        for found in view.scene().items(point, Qt.IntersectsItemBoundingRect, Qt.DescendingOrder):
            if (isinstance(found, NodeItemMixin) and found.is_content and found.isVisible()
                    and found.node_name != exclude and view.display.shows(found.node_name)):
                return found.node_name
        return None

    # ---- Demonstrate's banner ------------------------------------------------------------------

    def banner_layout(self, view: Any) -> dict[str, QRectF] | None:
        if self.api.demo is None:
            return None
        metrics = QFontMetricsF(self._font())
        title = self.banner_text()
        widths = {"view": metrics.horizontalAdvance("Include this view") + 34.0,
                  "done": metrics.horizontalAdvance("Done") + 28.0,
                  "cancel": metrics.horizontalAdvance("Cancel") + 28.0}
        text_w = metrics.horizontalAdvance(title)
        width = 16.0 + text_w + 16.0 + sum(widths.values()) + 3 * 8.0 + 8.0
        viewport = view.viewport().rect()
        width = min(width, viewport.width() - 16.0)
        bar = QRectF((viewport.width() - width) / 2.0, 10.0, width, 40.0)
        rects = {"bar": bar}
        x = bar.right() - 8.0
        for key in ("cancel", "done", "view"):
            x -= widths[key]
            rects[key] = QRectF(x, bar.top() + 7.0, widths[key], bar.height() - 14.0)
            x -= 8.0
        rects["text"] = QRectF(bar.left() + 16.0, bar.top(), max(0.0, x - bar.left() - 16.0), bar.height())
        return rects

    def banner_text(self) -> str:
        return f"Demonstrating {self.api.demo_name()}: make the board look the way it should after the click"

    @staticmethod
    def _font() -> QFont:
        font = QFont()
        font.setPixelSize(12)
        return font

    # ---- Input ------------------------------------------------------------------------------

    def pointer(self, e: PointerEvent) -> str | None:
        if self.wire is not None:
            return self._wire_event(e)
        if e.kind == "release" and self.pressed is not None:
            self.pressed = None
            return HANDLED
        if e.kind not in ("press", "double") or e.button != Qt.LeftButton:
            return None
        banner = self.banner_layout(e.view)
        if banner is not None and banner["bar"].contains(e.pos):
            if banner["done"].contains(e.pos):
                self.ui.app.run_command("interactions.finish_ui")
            elif banner["cancel"].contains(e.pos):
                self.ui.app.run_command("interactions.finish", cancel=True)
            elif banner["view"].contains(e.pos):
                self.include_view = not self.include_view
            e.view.viewport().update()
            return HANDLED
        if self.picking is not None:
            source, self.picking = self.picking, None
            target = self.target_at(e.view, e.pos, source)
            self._refresh_all()
            if target is None:
                self.ui.status("Connect cancelled: click an item to connect it")
                return HANDLED
            self.offer_verbs(source, target, e.global_pos)
            return HANDLED
        handle = self.handle_center(e.view)
        if handle is not None and _distance(handle[1], e.pos) <= self.HANDLE + 3.0:
            self.wire = {"node": handle[0], "view": e.view, "pos": e.pos}
            return HANDLED
        name = self.clickable_at(e.view, e.pos)
        if name is not None:
            self.pressed = name
            self.ui.app.run_command("interactions.click", name)
            return HANDLED
        return None

    def _wire_event(self, e: PointerEvent) -> str:
        wire = self.wire
        if e.kind == "move":
            wire["pos"] = e.pos
            wire["view"].viewport().update()
            return HANDLED
        if e.kind == "release":
            self.wire = None
            wire["view"].viewport().update()
            target = self.target_at(wire["view"], e.pos, wire["node"])
            if target is not None:
                self.offer_verbs(wire["node"], target, e.global_pos)
            elif _distance(e.pos, self.handle_center(wire["view"])[1] if self.handle_center(wire["view"]) else e.pos) < 4:
                self.ui.status("Drag the handle onto an item to choose what the click does to it")
            return HANDLED
        return HANDLED

    def hover(self, e: PointerEvent) -> None:
        if self.wire is not None:
            return
        banner = self.banner_layout(e.view)
        if banner is not None and banner["bar"].contains(e.pos):
            if any(banner[k].contains(e.pos) for k in ("done", "cancel", "view")):
                e.set_cursor(Qt.PointingHandCursor)
            return
        if self.picking is not None:
            e.set_cursor(Qt.CrossCursor)
            self.ui.status("Click the item this click should act on · Esc cancels")
            return
        handle = self.handle_center(e.view)
        if handle is not None and _distance(handle[1], e.pos) <= self.HANDLE + 3.0:
            e.set_cursor(Qt.OpenHandCursor)
            self.ui.status("Drag onto an item to choose what clicking this does to it")
            return
        name = self.clickable_at(e.view, e.pos)
        if name is not None:
            e.set_cursor(Qt.PointingHandCursor)
            problem = self.problem_text(name)
            self.ui.status(f"Click to run {name}'s click action" + (f" · ⚠ {problem}" if problem else ""))
        elif self.api.demo is None:
            name = self.action_item_at(e.view, e.pos)
            problem = self.problem_text(name) if name is not None else ""
            if problem:
                self.ui.status(f"{name}: ⚠ {problem}")

    def key(self, e: Any) -> str | None:
        return None

    # ---- Connecting -------------------------------------------------------------------------

    def start_picking(self, node: str) -> None:
        self.picking = node
        self.ui.status(f"Click the item that clicking {node} should act on · Esc cancels")
        self._refresh_all()

    def offer_verbs(self, source: str, target: str, global_pos: QPointF) -> None:
        verbs = self.session.run("interactions.verbs", target)
        menu = QMenu(self.ui.app.main_window())
        header = menu.addAction(f"When {source} is clicked, {target}…")
        header.setEnabled(False)
        menu.addSeparator()
        for verb in verbs:
            action = menu.addAction(verb["label"])
            action.triggered.connect(lambda _c=False, v=verb["id"]: self._connect(source, target, v))
        menu.exec(global_pos.toPoint())

    def _connect(self, source: str, target: str, verb: str) -> None:
        if self.ui.app.run_command("interactions.connect", source, target, verb) is not None:
            self.session.selection.set([source])
            self.ui.app.run_command("select", [source])
            self.ui.toast(f"{source} → {self.api.describe_step(self.api.steps(source)[-1])}. Try it with Run in the "
                          "bar under it; lock it to make a plain click run it.", GREEN)

    def cancel(self) -> bool:
        if self.picking is not None or self.wire is not None:
            self.picking, self.wire = None, None
            self._refresh_all()
            return True
        return False

    def _refresh_all(self) -> None:
        for view in self.ui.app.views.values():
            view.viewport().update()

    # ---- Painting ---------------------------------------------------------------------------

    def paint(self, view: Any, painter: QPainter) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            self._paint_highlight(view, painter)
            if view.display.show_overlays and self.api.demo is None:
                for name in self.problem_items():
                    self._paint_problem(view, painter, name)
                self._paint_handle(view, painter)
            self._paint_wire(view, painter)
            self._paint_modes(view, painter)
        finally:
            painter.restore()

    def _paint_highlight(self, view: Any, painter: QPainter) -> None:
        for name in self.highlight:
            rect = self.viewport_rect(view, name)
            if rect is None:
                continue
            painter.setPen(QPen(theme.ACCENT, 2.0, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-4, -4, 4, 4), 6, 6)

    def _paint_problem(self, view: Any, painter: QPainter, name: str) -> None:
        """An amber dashed outline: its last run failed, or a step can't run. It hugs the item at any zoom."""
        rect = self.viewport_rect(view, name)
        if rect is None:
            return
        painter.setPen(QPen(AMBER, 2.0, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect.adjusted(-3, -3, 3, 3), 5, 5)

    # ---- Problems ---------------------------------------------------------------------------

    def status(self, name: str) -> str:
        try:
            return self.api.status(name)
        except Exception:  # noqa: BLE001  a deleted node mid-paint
            return ""

    def problem_items(self) -> list[str]:
        """Items whose click action has a problem; worked out again only when the board or the log changes."""
        key = (self.session.scene.revision, len(self.api.log), id(self.api.log[-1]) if self.api.log else None)
        if self._problems[0] != key:
            names = [n.name for n in self.session.scene.iter_nodes() if n.attrs.get("on_click")]
            self._problems = (key, [n for n in names if self.status(n) in PROBLEMS])
        return self._problems[1]

    def problem_text(self, name: str) -> str:
        """One line on what's wrong with an item's click action (empty: nothing)."""
        last = self.api.last_run(name)
        if last is not None and last["problems"]:
            first = last["problems"][0]
            more = f" (+{len(last['problems']) - 1} more)" if len(last["problems"]) > 1 else ""
            return (f"Last run at {last['time']}: {last['ran']} step{'s' if last['ran'] != 1 else ''} ran · "
                    f"{first['step']}: {first['reason']}{more}")
        problems = self.api.check(name)
        if problems:
            return f"Won't fully run: {problems[0]['step']}: {problems[0]['reason']}"
        return ""

    def _paint_handle(self, view: Any, painter: QPainter) -> None:
        handle = self.handle_center(view)
        if handle is None:
            return
        name, center = handle
        rect = self.viewport_rect(view, name)
        if rect is not None:
            painter.setPen(QPen(theme.ACCENT, 1.5, Qt.DotLine))
            painter.drawLine(QPointF(rect.right() + 2.0, center.y()), QPointF(center.x() - self.HANDLE, center.y()))
        painter.setPen(QPen(QColor("#ffffff"), 1.5))
        painter.setBrush(theme.ACCENT)
        painter.drawEllipse(center, self.HANDLE, self.HANDLE)
        pixmap = icon("link", "#ffffff").pixmap(14, 14)
        painter.drawPixmap(QPointF(center.x() - 7.0, center.y() - 7.0), pixmap)

    def _paint_wire(self, view: Any, painter: QPainter) -> None:
        if self.wire is None or self.wire["view"] is not view:
            return
        handle = self.handle_center(view)
        start = handle[1] if handle is not None else self.wire["pos"]
        end = self.wire["pos"]
        target = self.target_at(view, end, self.wire["node"])
        if target is not None:
            rect = self.viewport_rect(view, target)
            if rect is not None:
                painter.setPen(QPen(theme.ACCENT, 2.5))
                painter.setBrush(QColor(61, 142, 240, 40))
                painter.drawRoundedRect(rect.adjusted(-4, -4, 4, 4), 6, 6)
        painter.setPen(QPen(theme.ACCENT, 2.5, Qt.DashLine, Qt.RoundCap))
        painter.drawLine(start, end)
        painter.setBrush(theme.ACCENT)
        painter.drawEllipse(end, 5.0, 5.0)

    def _paint_modes(self, view: Any, painter: QPainter) -> None:
        viewport = view.viewport().rect()
        painter.setFont(self._font())
        banner = self.banner_layout(view)
        if banner is not None:
            painter.setPen(QPen(GREEN, 3.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(1.5, 1.5, viewport.width() - 3.0, viewport.height() - 3.0))
            painter.setPen(QPen(GREEN, 1.0))
            painter.setBrush(QColor(20, 30, 26, 240))
            painter.drawRoundedRect(banner["bar"], 8, 8)
            painter.setPen(QColor("#e8f5ee"))
            metrics = painter.fontMetrics()
            painter.drawText(banner["text"], Qt.AlignVCenter | Qt.AlignLeft,
                             metrics.elidedText(self.banner_text(), Qt.ElideRight, int(banner["text"].width())))
            for key, text, filled in (("view", "Include this view", self.include_view), ("done", "Done", True),
                                      ("cancel", "Cancel", False)):
                rect = banner[key]
                painter.setPen(QPen(GREEN if key != "cancel" else QColor(200, 205, 215), 1.0))
                painter.setBrush(GREEN.darker(140) if key == "done" else QColor(255, 255, 255, 18))
                painter.drawRoundedRect(rect, 5, 5)
                label = rect
                if key == "view":
                    box = QRectF(rect.left() + 8.0, rect.center().y() - 6.0, 12.0, 12.0)
                    painter.setBrush(GREEN if filled else Qt.NoBrush)
                    painter.drawRect(box)
                    label = rect.adjusted(24.0, 0.0, 0.0, 0.0)
                painter.setPen(QColor("#ffffff"))
                painter.drawText(label, Qt.AlignCenter, text)
            return
        if self.picking is not None:
            text = "Click the item to connect · Esc cancels"
            metrics = painter.fontMetrics()
            rect = QRectF(10.0, viewport.height() - metrics.height() - 22.0, metrics.horizontalAdvance(text) + 32.0,
                          metrics.height() + 10.0)
            painter.setPen(QPen(GREEN, 1.0))
            painter.setBrush(QColor(20, 30, 26, 230))
            painter.drawRoundedRect(rect, 5, 5)
            painter.setPen(Qt.NoPen)
            painter.setBrush(GREEN)
            painter.drawEllipse(QPointF(rect.left() + 12.0, rect.center().y()), 4.0, 4.0)
            painter.setPen(QColor("#e8f5ee"))
            painter.drawText(rect.adjusted(22.0, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)


def _distance(a: QPointF, b: QPointF) -> float:
    return math.hypot(a.x() - b.x(), a.y() - b.y())


# ---- The quick editor bar -----------------------------------------------------------------------


class ClickQuickControls(QWidget):
    """▶ Run and ↺ Reset in the quick editor bar under a selected item that has a click action."""

    def __init__(self, ui: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self.ui = ui
        self.names: list[str] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.run_button = w.make_tool_button("Run", "Run the click action (one undo step)", width=58, parent=self)
        self.run_button.setIcon(icon("play", "#9fd8b8"))
        self.run_button.clicked.connect(lambda: self._click(None))
        self.reset_button = w.make_tool_button("", "Put back what the click changed", width=28, parent=self)
        self.reset_button.setIcon(icon("undo"))
        self.reset_button.clicked.connect(lambda: self._click(True))
        layout.addWidget(self.run_button)
        layout.addWidget(self.reset_button)

    def _click(self, back: bool | None) -> None:
        for name in list(self.names):
            self.ui.app.run_command("interactions.click", name, back=back)

    def refresh(self, names: list[str]) -> None:
        scene = self.ui.session.scene
        self.names = [n for n in names if scene.has(n) and scene.get_node(n).attrs.get("on_click")]
        self.reset_button.setEnabled(any(scene.get_node(n).attrs.get("action_on") for n in self.names))
        controller = self.ui.service("interactions.canvas")
        problems = [controller.problem_text(n) for n in self.names if controller.status(n) in PROBLEMS]
        self.run_button.setIcon(icon("play", AMBER.name() if problems else "#9fd8b8"))
        self.run_button.setToolTip("Run the click action (one undo step)"
                                   + (f"\n⚠ {problems[0]}\nClick Action Log… has the details" if problems else ""))


# ---- The Attribute Editor -----------------------------------------------------------------------


class StepRow(QWidget):
    """One step, as a sentence: hover to see its item on the canvas, × to remove it."""

    def __init__(self, section: "ClickActionSection", index: int, text: str, icon_name: str, targets: list[str],
                 back: bool, problem: str | None = None) -> None:
        super().__init__(section.list)
        self.section = section
        self.targets = targets
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        glyph = QLabel(self)
        glyph.setPixmap(icon(icon_name, AMBER.name() if problem else "#9fd8b8").pixmap(14, 14))
        glyph.setFixedWidth(16)
        label = QLabel(html.escape(text) + (f"<br><span style='color:{AMBER.name()}'>⚠ {html.escape(problem)}</span>"
                                            if problem else ""), self)
        label.setWordWrap(True)
        label.setToolTip(text + ("" if back else "\n(clicking again doesn't undo this step)")
                         + (f"\n⚠ {problem}" if problem else ""))
        remove = w.make_tool_button("", "Remove this step", width=22, parent=self)
        remove.setIcon(icon("close"))
        remove.clicked.connect(lambda: section.run("interactions.remove_step", index, node=section.name))
        layout.addWidget(glyph)
        layout.addWidget(label, 1)
        layout.addWidget(remove)

    def enterEvent(self, event: Any) -> None:  # noqa: N802
        self.section.set_highlight(self.targets)
        super().enterEvent(event)

    def leaveEvent(self, event: Any) -> None:  # noqa: N802
        self.section.set_highlight([])
        super().leaveEvent(event)


class ClickActionSection(w.InspectorSection):
    """What clicking the item does: the steps as sentences, Try it, Reset, Demonstrate, Connect."""

    def __init__(self, ui: Any, controller: ClickController, parent: QWidget) -> None:
        super().__init__("click_action", "Click Action", parent)
        self.ui = ui
        self.api = ui.service("interactions")
        self.controller = controller
        self.name: str | None = None
        self._signature: Any = None
        body = self.body
        self.note = QLabel("", body)
        self.note.setWordWrap(True)
        self.note.setObjectName("pathLabel")
        self.form.addRow(self.note)
        self.list = QWidget(body)
        self.list_layout = QVBoxLayout(self.list)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(4)
        self.form.addRow(self.list)

        def button(text: str, icon_name: str, tip: str, slot: Any) -> QPushButton:
            b = QPushButton(text, body)
            b.setIcon(icon(icon_name))
            b.setToolTip(tip)
            b.clicked.connect(slot)
            return b

        self.try_button = button("Try it", "play", "Run the click action now (one undo step)",
                                 lambda: self.run("interactions.click", self.name))
        self.reset_button = button("Reset", "undo", "Put back what the click changed",
                                   lambda: self.run("interactions.click", self.name, back=True))
        self.demo_button = button("Demonstrate…", "record",
                                  "Make the board look the way it should after the click, then press Done",
                                  lambda: self.run("interactions.demonstrate", self.name))
        self.connect_button = button("Connect…", "link",
                                     "Click another item, then choose what the click does to it (or drag the "
                                     "handle beside the item onto it)",
                                     lambda: self.name and self.controller.start_picking(self.name))
        self.form.addRow(w.hrow(body, self.try_button, self.reset_button))
        self.form.addRow(w.hrow(body, self.demo_button, self.connect_button))
        self.toggle_check = QCheckBox("Click again to put things back", body)
        self.toggle_check.setToolTip("A second click undoes the first: shows what it hid, hides what it showed")
        self.toggle_check.toggled.connect(self._on_toggle)
        self.form.addRow(self.toggle_check)
        self.set_field = QLineEdit(body)
        self.set_field.setPlaceholderText("none")
        self.set_field.setToolTip("Items in the same set are one at a time, like tabs: clicking one turns the "
                                  "one that's on off")
        self.set_field.editingFinished.connect(self._on_set)
        self.form.addRow("One at a time with", self.set_field)
        self.last_label = QLabel("", body)
        self.last_label.setWordWrap(True)
        self.last_label.setObjectName("pathLabel")
        self.log_button = button("Log…", "inspector", "What this item's clicks did, and why steps failed",
                                 lambda: self.run("interactions.show_log", self.name))
        self.form.addRow(w.hrow(body, self.last_label, self.log_button))
        self._updating = False

    def run(self, command: str, *args: Any, **kwargs: Any) -> Any:
        return self.ui.app.run_command(command, *args, **kwargs)

    def set_highlight(self, names: list[str]) -> None:
        self.controller.highlight = list(names)
        self.controller._refresh_all()

    def refresh(self, info: SelectionInfo) -> None:
        self.name = info.names[0] if info.single else None
        if self.name is None or not self.ui.session.scene.has(self.name):
            return
        attrs = self.ui.session.scene.get_node(self.name).attrs
        steps = list(attrs.get("on_click") or [])
        last = self.api.last_run(self.name)
        # Nodes deleted, or the item ran again: its steps' problems may have changed.
        signature = (self.name, repr(steps), len(self.ui.session.scene.nodes), id(last))
        self._updating = True
        try:
            if signature != self._signature:
                self._signature = signature
                self._rebuild(steps, last)
            has = bool(steps)
            self.log_button.setVisible(has)
            self.last_label.setVisible(has)
            demo = self.api.demo is not None
            for b in (self.try_button, self.reset_button):
                b.setVisible(has)
                b.setEnabled(not demo)
            self.reset_button.setEnabled(has and not demo and bool(attrs.get("action_on")))
            self.demo_button.setText("Demonstrate again…" if has else "Demonstrate…")
            self.demo_button.setEnabled(not demo)
            self.connect_button.setEnabled(not demo)
            any_back = any(s.get("back") for s in steps)
            self.toggle_check.setVisible(has)
            self.toggle_check.setEnabled(any_back)
            self.toggle_check.setChecked(any_back and bool(attrs.get("click_toggles", True)))
            self.set_field.setEnabled(has)
            if not self.set_field.hasFocus():
                self.set_field.setText(str(attrs.get("action_set") or ""))
            if not has:
                self.note.setText("Make this do something when it's clicked: Demonstrate shows the result once, "
                                  "Connect points it at another item.")
            elif attrs.get("locked"):
                self.note.setText("Locked: a plain click on the canvas runs it.")
            else:
                self.note.setText("Unlocked, so a click selects it for editing. Lock it (Outliner or Attribute "
                                  "Editor) and a plain click runs it. Try it runs it now.")
        finally:
            self._updating = False

    def _rebuild(self, steps: list[dict], last: dict[str, Any] | None) -> None:
        while self.list_layout.count():
            child = self.list_layout.takeAt(0).widget()
            if child is not None:
                child.deleteLater()
        kinds = self.api.kinds()
        now = {p["index"]: p["reason"] for p in self.api.check(self.name)}  # can't run now
        failed = {p["step"]: p["reason"] for p in (last or {}).get("problems", [])}  # failed last time
        for index, entry in enumerate(steps):
            kind = kinds.get(entry.get("command", ""))
            text = self.api.describe_step(entry)
            targets = [t for t in self.api.targets(entry) if self.ui.session.scene.has(t)]
            problem = now.get(index) or (f"last run: {failed[text]}" if text in failed else None)
            self.list_layout.addWidget(StepRow(self, index, text, kind.icon if kind is not None else "close",
                                               targets, bool(entry.get("back")), problem))
        if last is None:
            count = len(now)
            self.last_label.setText("Not run yet." + (f" ⚠ {count} step{'s' if count != 1 else ''} can't run."
                                                      if count else ""))
        else:
            total = last["ran"] + len(last["problems"])
            text = f"Last run {last['time']} ({'on' if last['on'] else 'off'}): {last['ran']} of {total} steps ran"
            self.last_label.setText(text + (f". ⚠ {len(last['problems'])} failed or skipped." if last["problems"]
                                            else "."))
        self.last_label.setStyleSheet(f"color: {AMBER.name()}" if now or (last and last["problems"]) else "")

    def _on_toggle(self, checked: bool) -> None:
        if not self._updating and self.name:
            self.run("interactions.set_toggles", checked, [self.name])

    def _on_set(self) -> None:
        if self._updating or not self.name:
            return
        text = self.set_field.text().strip()
        current = str(self.ui.session.scene.get_node(self.name).attrs.get("action_set") or "")
        if text == current:
            return
        if text:
            self.run("interactions.make_set", [self.name], text)
        else:
            self.run("set_attr", self.name, "action_set", "")


# ---- The log ------------------------------------------------------------------------------------


class ClickLogDialog(QDialog):
    """What clicks did this session, newest first: each run's item, direction and steps, with
    the reason for every step that failed or was skipped. Updates as clicks run."""

    def __init__(self, ui: Any) -> None:
        super().__init__(ui.app.main_window())
        self.ui = ui
        self.api = ui.service("interactions")
        self.node_id: str | None = None
        self.setWindowTitle("Click Action Log")
        self.resize(620, 440)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.only_problems = QCheckBox("Only problems", self)
        self.only_problems.toggled.connect(self.refresh)
        self.only_item = QCheckBox("", self)
        self.only_item.toggled.connect(self.refresh)
        clear = QPushButton("Clear", self)
        clear.setToolTip("Forget the log (and the amber ! it puts on items)")
        clear.clicked.connect(lambda: ui.app.run_command("interactions.clear_log"))
        top.addWidget(self.only_problems)
        top.addWidget(self.only_item)
        top.addStretch(1)
        top.addWidget(clear)
        layout.addLayout(top)
        self.text = QTextBrowser(self)
        self.text.setOpenLinks(False)
        self.text.anchorClicked.connect(self._select)
        layout.addWidget(self.text, 1)

    def show_for(self, node_id: str | None) -> None:
        self.node_id = node_id
        name = self.ui.session.scene.name_of_id(node_id) if node_id else None
        self.only_item.setText(f"Only {name}" if name else "")
        self.only_item.setVisible(bool(name))
        self.only_item.setChecked(bool(name))
        self.refresh()
        self.show()
        self.raise_()

    def refresh(self) -> None:
        entries = list(reversed(self.api.log))
        if self.only_item.isChecked() and self.node_id:
            entries = [e for e in entries if e["id"] == self.node_id]
        if self.only_problems.isChecked():
            entries = [e for e in entries if e["problems"]]
        if not entries:
            self.text.setHtml("<p style='color:#9aa3b2'>Nothing yet. Every click on an item with a click action "
                              "is listed here, with anything that went wrong.</p>")
            return
        colors = {"ok": GREEN.name(), "partial": AMBER.name(), "failed": "#ef6b5b"}
        rows = []
        for e in entries:
            name = self.ui.session.scene.name_of_id(e["id"]) or e["node"]
            total = e["ran"] + len(e["problems"])
            label = {"ok": "ok", "partial": "partly failed", "failed": "failed"}[e["status"]]
            rows.append(f"<p style='margin:6px 0 2px 0'><span style='color:#9aa3b2'>{e['time']}</span> &nbsp;"
                        f"<a href='{e['id']}' style='color:#e8eaf0'><b>{html.escape(name)}</b></a> "
                        f"{'on' if e['on'] else 'off'} &nbsp;<span style='color:{colors[e['status']]}'>"
                        f"{label}: {e['ran']} of {total} steps ran</span></p>")
            for problem in e["problems"]:
                kind = "failed" if problem["error"] else "skipped"
                rows.append(f"<p style='margin:0 0 0 18px; color:{AMBER.name()}'>⚠ {html.escape(problem['step'])} "
                            f"<span style='color:#9aa3b2'>({kind}: {html.escape(problem['reason'])})</span></p>")
        self.text.setHtml("".join(rows))

    def _select(self, url: Any) -> None:
        name = self.ui.session.scene.name_of_id(url.toString())
        if name:
            self.ui.app.run_command("select", [name])
            self.ui.app.run_command("navigation.frame", [name])


# ---- Wiring it up -------------------------------------------------------------------------------


def activate_interactions_ui(ui: Any) -> None:
    session, scene, app = ui.session, ui.session.scene, ui.app
    api = ui.service("interactions")

    def factory(_canvas: Any, node: dict[str, Any]) -> ButtonItem:
        item = ButtonItem(node["name"])
        item.apply_attrs(node.get("attrs", {}))
        return item

    ui.node_ui("button", factory=factory, label="Button", icon="play", badge=("#3d8ef0", "B"))

    controller = ClickController(ui)
    ui.input_handler(controller, 750)  # above playback controls (700), the link grabber and the gizmo
    ui.overlay(controller.paint, order=90)
    ui.provide_service("interactions.canvas", controller)

    ui.escape_handler(controller.cancel, order=5)  # Connect… / a drag from the handle

    def view_of(view: str | None) -> Any:
        found = app.views.get(view) if view else app.active_view()
        if found is None:
            raise CommandError(f"No canvas view '{view}'" if view else "No canvas view")
        return found

    def fly_to(node: str, view: str | None = None) -> bool:
        """Fly a view to an item (smoothly), fitting it in the view."""
        target = view_of(view)
        item = ui.canvas.item(scene.resolve(str(node)))
        if item is None:
            return False
        rect = ui.canvas.scene_bounds([item])
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return False
        margin = 60.0
        viewport = target.viewport().rect()
        zoom = min((viewport.width() - 2 * margin) / rect.width(), (viewport.height() - 2 * margin) / rect.height())
        target.set_camera([rect.center().x(), rect.center().y()], max(0.01, zoom), animate=True)
        return True

    def finish_ui() -> dict[str, Any]:
        """Done: finish the demonstration (with this view if "Include this view" is ticked)."""
        view = app.active_view()
        camera = view.camera() if controller.include_view and view is not None else None
        result = session.run("interactions.finish", view=camera)
        controller.include_view = False
        name = result.get("node")
        if result.get("steps"):
            sentences = session.run("interactions.describe", name)
            created = result.get("created") or []
            extra = f" {len(created)} new item{'s' if len(created) != 1 else ''} hidden until the click." if created else ""
            ui.toast(f"{name}: {len(sentences)} step{'s' if len(sentences) != 1 else ''} saved.{extra} "
                     "Lock it to make a plain click run it.", GREEN, 4200,
                     actions=[("Try it", lambda: app.run_command("interactions.click", name))])
        else:
            ui.toast("Nothing changed, so no click action was made", None, 3000)
        return result

    def place_button(label: str = "Button") -> str:
        """Place a button in the middle of the view and select it."""
        view = app.active_view()
        center = view.to_scene(QPointF(view.viewport().width() / 2.0, view.viewport().height() / 2.0)) \
            if view is not None else QPointF(0.0, 0.0)
        return session.run("interactions.create_button", center.x() - 120.0, center.y() - 36.0, label)

    def pick_target(node: str | None = None) -> None:
        """Connect…: the next click on the canvas picks the item the click acts on."""
        name = scene.resolve(str(node)) if node else (session.selection.names or [None])[0]
        if name is None:
            raise CommandError("Select the item to make clickable first")
        controller.start_picking(name)

    log_dialog: list[ClickLogDialog] = []

    def show_log(node: str | None = None) -> None:
        """Open the Click Action Log (``node``: only that item's runs, to start with)."""
        if not log_dialog:
            log_dialog.append(ClickLogDialog(ui))
            ui.track(lambda: log_dialog and log_dialog.pop().deleteLater())
        log_dialog[0].show_for(scene.get_node(scene.resolve(str(node))).id if node else None)

    for name, fn, undoable, echo in (
        ("show_log", show_log, False, False),
        ("fly_to", fly_to, False, True),
        ("finish_ui", finish_ui, False, False), ("place_button", place_button, True, True),
        ("pick_target", pick_target, False, False),
    ):
        ui.command(name, fn, undoable=undoable, echo=echo, label="Place button" if name == "place_button" else None)

    def selected_one() -> bool:
        names = session.selection.names
        return len(names) == 1 and session.types.is_canvas(scene.get_node(names[0]).node_type)

    def selected_actions() -> int:
        return sum(1 for n in session.selection.names if scene.has(n) and scene.get_node(n).attrs.get("on_click"))

    ui.context_key("interactions.one", lambda: 1 if selected_one() else 0)
    ui.context_key("interactions.actions", selected_actions)

    a = ui.action
    not_demo = "!interactions.demonstrating"
    for key, command, text, keywords, icon_name, when in (
        ("interactions.demonstrate", "interactions.demonstrate", "Demonstrate Click Action…",
         "button interactive click show hide reveal record result state", "record",
         f"interactions.one > 0 && {not_demo}"),
        ("interactions.connect", "interactions.pick_target", "Connect Click To…",
         "button interactive click link show hide frame fly target", "link", f"interactions.one > 0 && {not_demo}"),
        ("interactions.try", "interactions.click", "Run Click Action", "button interactive try test play", "play",
         f"interactions.actions > 0 && {not_demo}"),
        ("interactions.clear", "interactions.clear", "Remove Click Action", "button interactive delete", "delete",
         f"interactions.actions > 0 && {not_demo}"),
        ("interactions.make_set", "interactions.make_set", "Make One-at-a-Time Set", "tabs radio exclusive group",
         "select", f"interactions.actions > 1 && {not_demo}"),
        ("interactions.tabs", "interactions.make_tabs", "Make Tabs from Selection",
         "button interactive tabs switch one at a time", "rect", f"selection.count > 1 && {not_demo}"),
        ("interactions.reveal", "interactions.make_reveal", "Make Reveal Button",
         "button interactive reveal show hide answer quiz", "eye", f"selection.count > 0 && {not_demo}"),
        ("interactions.before_after", "interactions.make_before_after", "Make Before / After Button",
         "button interactive compare swap toggle", "split_right", f"selection.count > 1 && {not_demo}"),
    ):
        a(key, command, text, keywords=keywords, category="Interactions", icon=icon_name)
    for order, key in enumerate(("interactions.demonstrate", "interactions.connect", "interactions.try",
                                 "interactions.clear", "interactions.make_set")):
        when = {"interactions.demonstrate": f"interactions.one > 0 && {not_demo}",
                "interactions.connect": f"interactions.one > 0 && {not_demo}",
                "interactions.make_set": f"interactions.actions > 1 && {not_demo}"}.get(
            key, f"interactions.actions > 0 && {not_demo}")
        ui.menu("canvas", key, path="Click Action", group="interact", order=order, when=when)
    for order, (key, when) in enumerate((("interactions.tabs", f"selection.count > 1 && {not_demo}"),
                                         ("interactions.reveal", f"selection.count > 0 && {not_demo}"),
                                         ("interactions.before_after", f"selection.count > 1 && {not_demo}"))):
        ui.menu("canvas", key, path="Click Action", group="make", order=order, when=when)
    a("interactions.log", "interactions.show_log", "Click Action Log…",
      keywords="button click failed error skipped problems history log", category="Interactions", icon="inspector")
    ui.menu("canvas", "interactions.log", path="Click Action", group="log", order=0)
    ui.place_submenu("canvas", "Click Action", "state", 8, icon="play")

    a("interactions.place_button", "interactions.place_button", "Insert Button",
      keywords="button interactive click create new", category="Interactions", icon="play")
    ui.menu("canvas", "interactions.place_button", group="create", order=40, when=not_demo)
    a("interactions.done", "interactions.finish_ui", "Finish Demonstrating", keywords="done save click action",
      category="Interactions", icon="record")
    a("interactions.cancel", "interactions.finish", "Cancel Demonstrating", kwargs={"cancel": True},
      keywords="abort discard click action", category="Interactions", icon="close")
    ui.bind("Ctrl+Return", "interactions.done", when="interactions.demonstrating", scope="canvas")
    ui.menu("canvas", "interactions.done", group="state", order=0, when="interactions.demonstrating")
    ui.menu("canvas", "interactions.cancel", group="state", order=1, when="interactions.demonstrating")

    ui.contribute("inspector.sections", InspectorSection(
        key="click_action", title="Click Action", order=80, generic=True,
        applies=lambda info: info.single and session.types.is_canvas((info.node or {}).get("type", "")),
        build=lambda parent, _inspector: ClickActionSection(ui, controller, parent)))

    def has_actions(names: list[str]) -> bool:
        return api.demo is None and any(scene.has(n) and scene.get_node(n).attrs.get("on_click") for n in names)

    ui.contribute("quick_edit.sections", QuickSection(
        key="click_action", title="Click Action Controls", order=5, applies=has_actions,
        build=lambda parent: ClickQuickControls(ui, parent)))

    def on_demonstrating(event: Any) -> None:
        name = event.payload.get("node")
        controller._refresh_all()
        inspector = ui.service("inspector")
        for panel in list(getattr(inspector, "panels", [])):
            panel.refresh()
        if name:
            ui.toast(f"Demonstrating {name}: change the board the way it should look after the click, then "
                     "press Done (Ctrl+Enter)", GREEN, 5000)

    ui.on("interactions.demonstrating", on_demonstrating)

    def on_ran(event: Any) -> None:
        """After a click: refresh the problem outlines, the bar and the log; say so when something went wrong."""
        controller._refresh_all()
        if log_dialog and log_dialog[0].isVisible():
            log_dialog[0].refresh()
        entry = event.payload.get("entry")
        if entry is None or event.payload.get("nested") or not entry["problems"]:
            return
        first = entry["problems"][0]
        total = entry["ran"] + len(entry["problems"])
        what = "failed" if entry["status"] == "failed" else f"{len(entry['problems'])} of {total} steps didn't run"
        ui.status(f"{entry['node']}: {what} · {first['step']}: {first['reason']}")
        ui.toast(f"{entry['node']}: {what}. {first['step']}: {first['reason']}.", AMBER, 5000,
                 actions=[("Show Log", lambda: app.run_command("interactions.show_log", entry["id"]))])

    ui.on("interactions.ran", on_ran)
