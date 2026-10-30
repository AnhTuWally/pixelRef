"""Finding the other end of a link.

* **Going there:** ``links.go_to`` glides a view to an element (keeping the zoom
  when it fits, zooming out when it doesn't) and selects it; ``links.follow``
  goes to one end of a link (double-click a link: its far end). Every jump is
  remembered per view: ``links.back`` / ``links.forward`` (Alt+Left / Alt+Right).
* **Edge chips:** the linked elements of the selection that are off-screen get a
  chip at the edge of the view, pointing at them; click one to go there.
* **Hover:** with the Select tool, hovering an element outlines its links and
  the elements at their other ends (off-screen ones get faded chips).
* **Badges:** a hovered or selected element with links shows its link count in
  its corner; click it for the *Linked To* menu.
* The *Linked To* sub-menu of the canvas menu and the *Links* section of the
  Attribute Editor list the same ends.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton, QVBoxLayout, QWidget

from pixelref.plugin_api import CommandError
from pixelref.plugin_api.ui import HANDLED, InputHandler, NodeItemMixin, PointerEvent, icon, theme
from pixelref.plugin_api.ui import widgets as w

from .ui import LinkItem

HOVER_SETTING = "hover_highlight"


@dataclass
class Target:
    """The far end of a link, seen from one of its elements (or from the link itself)."""

    link: str
    name: str | None   # the element there (None: a free end)
    point: QPointF     # where the end is (scene)
    role: str          # "to": it's the link's target · "from": its source

    @property
    def key(self) -> Any:
        return self.name or (round(self.point.x(), 1), round(self.point.y(), 1))

    @property
    def arrow(self) -> str:
        return "→" if self.role == "to" else "←"


class LinkNav:
    """The ``links.nav`` service: the far ends of links, and going there (with history)."""

    HISTORY = 50
    MARGIN = 60.0  # viewport px kept around an element we glide to

    def __init__(self, ui: Any, feedback: Any) -> None:
        self.ui = ui
        self.feedback = feedback
        self.history: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}  # view: (back, forward)
        self.hover_highlight = bool(ui.settings.value(HOVER_SETTING, True, type=bool))

    # ---- Ends -----------------------------------------------------------------------------

    def link_items(self) -> list[LinkItem]:
        return [item for item in list(self.ui.canvas.items.values()) if isinstance(item, LinkItem)]

    def _end(self, link: LinkItem, end: str, role: str) -> Target | None:
        try:
            points = link.route_points()
            transform = link.sceneTransform()
        except RuntimeError:  # replaced by a refresh
            return None
        if not points:
            return None
        name = link.end_names()[0 if end == "source" else 1]
        if name and self.ui.canvas.item(name) is None:
            name = None
        return Target(link.node_name, name, transform.map(points[0] if end == "source" else points[-1]), role)

    def targets(self, name: str) -> list[Target]:
        """The ends across ``name``'s links (for a link: its own two ends)."""
        item = self.ui.canvas.item(name)
        if item is None:
            return []
        if isinstance(item, LinkItem):
            found = [self._end(item, "source", "from"), self._end(item, "target", "to")]
            return [t for t in found if t is not None]
        found = []
        for link in self.link_items():
            source, target = link.end_names()
            if source == name:
                found.append(self._end(link, "target", "to"))
            elif target == name:
                found.append(self._end(link, "source", "from"))
        return [t for t in found if t is not None]

    def targets_of(self, names: list[str]) -> list[Target]:
        """The ends across the links of ``names``, once each, leaving out ``names`` themselves."""
        seen, found = set(names), []
        for name in names:
            for target in self.targets(name):
                if target.key in seen:
                    continue
                seen.add(target.key)
                found.append(target)
        return found

    def label(self, target: Target) -> str:
        return self.feedback.breadcrumb(target.name) if target.name else "free end"

    def rect(self, target: Target) -> QRectF:
        item = self.ui.canvas.item(target.name) if target.name else None
        if item is not None:
            try:
                return item.sceneBoundingRect()
            except RuntimeError:
                pass
        return QRectF(target.point.x() - 0.5, target.point.y() - 0.5, 1.0, 1.0)

    # ---- Going there --------------------------------------------------------------------------

    def view(self, view: str | None = None) -> Any:
        found = self.ui.app.views.get(view) if view else self.ui.app.active_view()
        if found is None:
            raise CommandError(f"No canvas view '{view}'" if view else "No canvas view")
        return found

    def remember(self, view: Any) -> None:
        back, forward = self.history.setdefault(view.view_name, ([], []))
        back.append(self._state(view))
        del back[:-self.HISTORY]
        forward.clear()

    def _state(self, view: Any) -> dict[str, Any]:
        return {"camera": view.camera(), "selection": list(self.ui.session.selection.names)}

    def glide(self, view: Any, rect: QRectF) -> None:
        """Centre ``rect`` in ``view``: same zoom when it fits, else zoomed out until it does."""
        room = QRectF(view.viewport().rect())
        room_w, room_h = max(room.width() - 2 * self.MARGIN, 40.0), max(room.height() - 2 * self.MARGIN, 40.0)
        zoom = view.zoom_level() or 1.0
        if rect.width() * zoom > room_w or rect.height() * zoom > room_h:
            zoom = min(room_w / max(rect.width(), 1e-6), room_h / max(rect.height(), 1e-6))
        center = rect.center()
        view.set_camera([center.x(), center.y()], zoom, animate=True)

    def go_to(self, target: Target, view: Any) -> None:
        self.remember(view)
        self.glide(view, self.rect(target))
        if target.name:
            self.ui.session.selection.set([target.name])
        self.ui.status(f"{target.arrow} {self.label(target)} · Alt+Left goes back")

    def step(self, view: Any, back: bool) -> None:
        past, future = self.history.setdefault(view.view_name, ([], []))
        source, dest = (past, future) if back else (future, past)
        if not source:
            raise CommandError("Nothing to go back to" if back else "Nothing to go forward to")
        dest.append(self._state(view))
        state = source.pop()
        camera = state["camera"]
        view.set_camera(camera["center"], camera["zoom"], animate=True)
        scene = self.ui.session.scene
        self.ui.session.selection.set([n for n in state["selection"] if scene.has(n)])


# ---- Canvas: edge chips, hover highlight, badges ---------------------------------------------------


@dataclass
class Chip:
    rect: QRectF
    direction: QPointF  # unit vector, viewport
    target: Target
    text: str


class LinkFinder(InputHandler):
    """Chips at the view's edge for the selection's off-screen link ends, the hover
    highlight, and the link-count badges (all painted by one overlay)."""

    MAX_SOURCES = 6   # more selected than this: no chips or badges (they'd be noise)
    MAX_CHIPS = 8
    INSET = 8.0
    BADGE_H = 18.0

    def __init__(self, ui: Any, nav: LinkNav) -> None:
        self.ui = ui
        self.nav = nav
        self.hovered: tuple[Any, str] | None = None             # (view, element or link)
        self.chips: dict[int, list[Chip]] = {}                  # per view: last painted (clickable) chips
        self.badges: dict[int, list[tuple[QRectF, str]]] = {}   # per view: last painted badges
        self.over: tuple[Any, str, int] | None = None           # (view, "chip" / "badge", index)
        self.pressed = False

    # ---- State -------------------------------------------------------------------------------

    def _tool(self) -> Any:
        return self.ui.tools.active

    def _selecting(self) -> bool:
        tool = self._tool()
        return tool is not None and tool.id == "select" and not getattr(tool, "busy", False)

    def _sources(self) -> list[str]:
        names = [n for n in self.ui.session.selection.names if self.ui.canvas.item(n) is not None]
        return names if len(names) <= self.MAX_SOURCES else []

    def _hover_target(self, item: Any) -> str | None:
        """The hovered item, or the first group around it, that has links."""
        node = item
        while node is not None:
            if isinstance(node, NodeItemMixin) and self.nav.targets(node.node_name):
                return node.node_name
            node = node.parentItem()
        return None

    def _set(self, hovered: tuple[Any, str] | None, over: tuple[Any, str, int] | None) -> None:
        if hovered == self.hovered and over == self.over:
            return
        for state in (self.hovered, hovered, self.over, over):
            if state is not None:
                try:
                    state[0].viewport().update()
                except RuntimeError:
                    pass
        self.hovered, self.over = hovered, over

    def _hit(self, view: Any, pos: QPointF) -> tuple[str, int] | None:
        for index, chip in enumerate(self.chips.get(id(view), [])):
            if chip.rect.contains(pos):
                return "chip", index
        for index, (rect, _name) in enumerate(self.badges.get(id(view), [])):
            if rect.contains(pos):
                return "badge", index
        return None

    # ---- Input -------------------------------------------------------------------------------

    def hover(self, e: PointerEvent) -> None:
        hit = self._hit(e.view, e.pos)
        if hit is not None:
            kind, index = hit
            e.set_cursor(Qt.PointingHandCursor)
            if kind == "chip":
                chip = self.chips[id(e.view)][index]
                self.ui.status(f"Go to {chip.text} (via {chip.target.link})")
            else:
                self.ui.status("Linked elements: click for the list")
            self._set(self.hovered, (e.view, kind, index))  # keep the hovered element: its badge may sit outside it
            return
        name = None
        if self._selecting() and self.nav.hover_highlight and e.item is not None:
            name = self._hover_target(e.item)
        self._set((e.view, name) if name else None, None)

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind == "press":
            self.pressed = False  # a menu opened from a badge may have taken the last release
        elif self.pressed:
            if e.kind == "release":
                self.pressed = False
            return HANDLED
        if e.kind != "press" or e.button != Qt.LeftButton:
            return None
        hit = self._hit(e.view, e.pos)
        if hit is None:
            return None
        kind, index = hit
        self.pressed = True
        self._set(None, None)
        if kind == "chip":
            self.nav.go_to(self.chips[id(e.view)][index].target, e.view)
        else:
            rect, name = self.badges[id(e.view)][index]
            menu = QMenu(e.view)
            fill_linked_menu(self.nav, menu, [name], e.view)
            menu.exec(e.view.viewport().mapToGlobal(rect.bottomLeft().toPoint()))
        return HANDLED

    def leave(self, view: Any) -> None:
        if (self.hovered is not None and self.hovered[0] is view) or (self.over is not None and self.over[0] is view):
            self._set(None, None)

    # ---- Painting ----------------------------------------------------------------------------

    @staticmethod
    def _font(bold: bool = False) -> QFont:
        font = QFont()
        font.setPixelSize(11)
        font.setBold(bold)
        return font

    def _on_screen(self, view: Any, target: Target, room: QRectF) -> bool:
        rect = view.mapFromScene(self.nav.rect(target)).boundingRect().toRectF()
        return rect.intersects(room) or room.contains(rect.center())

    def _layout(self, view: Any, targets: list[Target], taken: list[QRectF]) -> list[Chip]:
        """A chip on the view's edge, toward each off-screen target."""
        room = QRectF(view.viewport().rect())
        inner = room.adjusted(self.INSET, self.INSET, -self.INSET, -self.INSET)
        center = room.center()
        metrics = QFontMetricsF(self._font())
        chips: list[Chip] = []
        for target in targets:
            if len(chips) >= self.MAX_CHIPS:
                break
            if self._on_screen(view, target, room):
                continue
            delta = view.to_viewport(self.nav.rect(target).center()) - center
            length = math.hypot(delta.x(), delta.y())
            if length < 1e-6:
                continue
            direction = QPointF(delta.x() / length, delta.y() / length)
            text = self.nav.label(target)
            if len(text) > 32:
                text = text[:31] + "…"
            box = QRectF(0, 0, metrics.horizontalAdvance(text) + 32, metrics.height() + 8)
            # Where the line from the centre toward the target leaves the inner rectangle.
            sx = (inner.width() / 2 - box.width() / 2) / abs(direction.x()) if abs(direction.x()) > 1e-6 else math.inf
            sy = (inner.height() / 2 - box.height() / 2) / abs(direction.y()) if abs(direction.y()) > 1e-6 else math.inf
            box.moveCenter(center + direction * max(0.0, min(sx, sy)))
            vertical_edge = sx <= sy  # on the left / right edge: slide up or down to make room
            for _attempt in range(12):
                clash = next((r for r in taken if r.intersects(box)), None)
                if clash is None:
                    break
                if vertical_edge:
                    box.moveTop(clash.bottom() + 4)
                else:
                    box.moveLeft(clash.right() + 4)
            box.moveLeft(max(inner.left(), min(box.left(), inner.right() - box.width())))
            box.moveTop(max(inner.top(), min(box.top(), inner.bottom() - box.height())))
            taken.append(box)
            chips.append(Chip(box, direction, target, text))
        return chips

    def _paint_chip(self, painter: QPainter, chip: Chip, strong: bool, over: bool) -> None:
        rect = chip.rect
        alpha = 255 if strong else 150
        painter.setPen(QPen(QColor(theme.ACCENT) if strong else QColor(255, 255, 255, 70), 1.2))
        painter.setBrush(theme.ACCENT if over else QColor(20, 24, 32, 235 if strong else 170))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        # The arrow (shaft and head), pointing at the target.
        middle = QPointF(rect.left() + 13, rect.center().y())
        d = chip.direction
        n = QPointF(-d.y(), d.x())
        tip = middle + d * 6
        painter.setPen(QPen(QColor(255, 255, 255, alpha), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(middle - d * 6, tip)
        painter.drawPolyline(QPolygonF([tip - d * 4.5 + n * 4, tip, tip - d * 4.5 - n * 4]))
        painter.setFont(self._font())
        painter.setPen(QColor(255, 255, 255, alpha))
        painter.drawText(rect.adjusted(24, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, chip.text)

    def _badge_rect(self, view: Any, name: str, count: int) -> QRectF | None:
        item = self.ui.canvas.item(name)
        if item is None or not item.isVisible():
            return None
        try:
            rect = view.mapFromScene(item.sceneBoundingRect()).boundingRect().toRectF()
        except RuntimeError:
            return None
        room = QRectF(view.viewport().rect())
        if not rect.intersects(room):
            return None
        metrics = QFontMetricsF(self._font(True))
        width = metrics.horizontalAdvance(f"{count}") + 24
        badge = QRectF(0, 0, width, self.BADGE_H)
        if rect.width() >= 3 * width and rect.height() >= 3 * self.BADGE_H:
            badge.moveBottomRight(rect.bottomRight() - QPointF(6, 6))
        else:  # too small on screen: just outside its bottom-right corner
            badge.moveTopLeft(rect.bottomRight() + QPointF(2, 2))
        return badge

    def _paint_badge(self, painter: QPainter, rect: QRectF, count: int, over: bool) -> None:
        painter.setPen(QPen(QColor(0, 0, 0, 120), 1.0))
        painter.setBrush(theme.ACCENT if over else QColor(20, 22, 27, 215))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        icon("link").paint(painter, QRectF(rect.left() + 4, rect.top() + 3, 12, 12).toRect())
        painter.setFont(self._font(True))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(rect.adjusted(17, 0, -5, 0), Qt.AlignCenter, f"{count}")

    def _paint_hover(self, view: Any, painter: QPainter, name: str) -> list[Target]:
        """Outline the hovered element's links and what they lead to; returns its ends."""
        targets = self.nav.targets(name)
        transform = view.viewportTransform()
        canvas = self.ui.canvas
        links = {t.link for t in targets} if not isinstance(canvas.item(name), LinkItem) else {name}
        for link in links:
            item = canvas.item(link)
            path = item.outline_path() if isinstance(item, LinkItem) else None
            if path is not None:
                painter.setPen(QPen(QColor(61, 142, 240, 110), max(6.0, item.thickness * view.zoom_level() + 4),
                                    Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                painter.setBrush(Qt.NoBrush)
                painter.drawPath(transform.map(path))
        for target in targets:
            if target.name is None:
                point = view.to_viewport(target.point)
                painter.setPen(QPen(theme.ACCENT, 1.5))
                painter.setBrush(QColor("#ffffff"))
                painter.drawEllipse(point, 4.5, 4.5)
                continue
            item = canvas.item(target.name)
            if item is None or not item.isVisible():
                continue
            polygon = QPolygonF([view.to_viewport(p) for p in item.scene_polygon()])
            painter.setPen(QPen(theme.ACCENT, 1.5, Qt.DashLine))
            painter.setBrush(QColor(61, 142, 240, 22))
            painter.drawPolygon(polygon)
            if self._on_screen(view, target, QRectF(view.viewport().rect())):
                corner = polygon.boundingRect().topLeft()
                self.nav.feedback._tag(painter, corner, f"{target.arrow} {self.nav.label(target)}", False)
        return targets

    def paint(self, view: Any, painter: QPainter) -> None:
        key = id(view)
        self.chips[key], self.badges[key] = [], []
        if not view.display.show_overlays:
            return
        tool = self._tool()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            sources = self._sources() if tool is None or tool.show_selection else []
            taken: list[QRectF] = []
            hovered = self.hovered[1] if self.hovered is not None and self.hovered[0] is view else None
            if hovered is not None and self.ui.canvas.item(hovered) is None:
                hovered = None
            chips = self._layout(view, self.nav.targets_of(sources), taken)
            hover_chips: list[Chip] = []
            if hovered is not None and hovered not in sources and self._selecting():
                hover_targets = self._paint_hover(view, painter, hovered)
                shown = {c.target.key for c in chips}
                hover_chips = self._layout(view, [t for t in hover_targets if t.key not in shown], taken)
            for chip in hover_chips:
                self._paint_chip(painter, chip, strong=False, over=False)
            for index, chip in enumerate(chips):
                over = self.over is not None and self.over[0] is view and self.over[1:] == ("chip", index)
                self._paint_chip(painter, chip, strong=True, over=over)
            self.chips[key] = chips
            if self._selecting():
                owners = [n for n in sources if not isinstance(self.ui.canvas.item(n), LinkItem)]
                if hovered is not None and hovered not in owners and not isinstance(self.ui.canvas.item(hovered),
                                                                                     LinkItem):
                    owners.append(hovered)
                for name in owners:
                    count = len(self.nav.targets(name))
                    rect = self._badge_rect(view, name, count) if count else None
                    if rect is None:
                        continue
                    index = len(self.badges[key])
                    over = self.over is not None and self.over[0] is view and self.over[1:] == ("badge", index)
                    self._paint_badge(painter, rect, count, over)
                    self.badges[key].append((rect, name))
        finally:
            painter.restore()


# ---- The "Linked To" menu ---------------------------------------------------------------------------


def fill_linked_menu(nav: LinkNav, menu: QMenu, names: list[str], view: Any = None) -> None:
    """One entry per far end of ``names``' links: picking it goes there."""
    targets = nav.targets_of(names)
    for target in targets[:40]:
        text = f"{target.arrow}  {nav.label(target)}"
        if target.name is None:
            text += f"  ({target.link})"
        action = menu.addAction(text)
        action.setToolTip(f"Via {target.link}")
        action.triggered.connect(lambda _checked=False, t=target: nav.go_to(t, view or nav.view()))
    if len(targets) > 40:
        menu.addAction(f"… {len(targets) - 40} more").setEnabled(False)


# ---- Attribute Editor -------------------------------------------------------------------------------


class LinksSection(w.InspectorSection):
    """The far ends of the selected element's links (or of the selected link): click to go there."""

    def __init__(self, ui: Any, nav: LinkNav, parent: QWidget) -> None:
        super().__init__("links", "Links", parent)
        self.ui = ui
        self.nav = nav
        self.list = QWidget(self.body)
        self.rows = QVBoxLayout(self.list)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(2)
        self.form.addRow(self.list)
        self.buttons: list[QPushButton] = []

    def refresh(self, info: Any) -> None:
        while self.rows.count():
            child = self.rows.takeAt(0).widget()
            if child is not None:
                child.hide()
                child.deleteLater()
        self.buttons = []
        name = info.names[0] if info.single else None
        if name is None:
            return
        is_link = isinstance(self.ui.canvas.item(name), LinkItem)
        for target in self.nav.targets(name):
            row = QWidget(self.list)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)
            prefix = ("from" if target.role == "from" else "to") if is_link else target.arrow
            go = QPushButton(f"{prefix}  {self.nav.label(target)}", row)
            go.setFlat(True)
            go.setCursor(Qt.PointingHandCursor)
            go.setStyleSheet("text-align: left; padding: 2px 4px;")
            go.setToolTip(f"Go to {self.nav.label(target)} (Alt+Left comes back)")
            go.clicked.connect(lambda _c=False, t=target: self.nav.go_to(t, self.nav.view()))
            layout.addWidget(go, 1)
            self.buttons.append(go)
            if not is_link:
                via = QLabel(target.link, row)
                via.setObjectName("cardSubtitle")
                layout.addWidget(via)
                select = w.make_tool_button("", f"Select {target.link}", width=24, parent=row)
                select.setIcon(icon("link"))
                select.clicked.connect(lambda _c=False, link=target.link: self.ui.app.run_command("select", [link]))
                layout.addWidget(select)
            self.rows.addWidget(row)


# ---- Wiring ---------------------------------------------------------------------------------------


def activate_nav_ui(ui: Any, feedback: Any) -> LinkNav:
    from pixelref.plugin_api.contracts import InspectorSection

    nav = LinkNav(ui, feedback)
    session = ui.session
    scene = session.scene
    ui.provide_service("links.nav", nav)

    def go_to(node: Any, view: str | None = None) -> str | None:
        """Glide a view to an element (name or id) or a point [x, y] and select the element.
        The view keeps its zoom when the element fits. Alt+Left comes back."""
        if isinstance(node, (list, tuple)):
            target = Target("", None, QPointF(float(node[0]), float(node[1])), "to")
        else:
            name = scene.resolve(node)
            if ui.canvas.item(name) is None:
                raise CommandError(f"'{name}' isn't on the canvas")
            target = Target("", name, QPointF(), "to")
        nav.go_to(target, nav.view(view))
        return target.name

    def follow(link: str, end: str | None = None, view: str | None = None) -> str | None:
        """Go to one end ("source" / "target") of a link; by default the one farther from
        the view's centre. Returns the element there (None: a free end)."""
        target_view = nav.view(view)
        name = scene.resolve(link)
        ends = {t.role: t for t in nav.targets(name)} if isinstance(ui.canvas.item(name), LinkItem) else {}
        if not ends:
            raise CommandError(f"'{name}' isn't a link")
        if end is None:
            center = target_view.to_scene(QPointF(target_view.viewport().rect().center()))
            target = max(ends.values(), key=lambda t: math.hypot(t.point.x() - center.x(), t.point.y() - center.y()))
        elif end in ("source", "target"):
            target = ends.get("from" if end == "source" else "to")
            if target is None:
                raise CommandError(f"'{name}' has no {end} end")
        else:
            raise CommandError(f"end must be 'source' or 'target', got {end!r}")
        nav.go_to(target, target_view)
        return target.name

    def back(view: str | None = None) -> None:
        """Go back to where the view was before the last jump along a link."""
        nav.step(nav.view(view), back=True)

    def forward(view: str | None = None) -> None:
        """Redo a jump undone with back."""
        nav.step(nav.view(view), back=False)

    def set_hover_highlight(on: bool) -> None:
        """Whether hovering an element outlines its links and what they lead to; remembered."""
        nav.hover_highlight = bool(on)
        ui.settings.setValue(HOVER_SETTING, nav.hover_highlight)
        ui.app.actions.refresh_states()

    ui.command("go_to", go_to, alias="go_to_linked", undoable=False)
    ui.command("follow", follow, alias="follow_link", undoable=False)
    ui.command("back", back, alias="link_back", undoable=False)
    ui.command("forward", forward, alias="link_forward", undoable=False)
    ui.command("set_hover_highlight", set_hover_highlight, undoable=False)
    def toggle_hover_highlight() -> None:
        """Switch the hover highlight of links on or off."""
        set_hover_highlight(not nav.hover_highlight)

    ui.command("toggle_hover_highlight", toggle_hover_highlight, undoable=False, echo=False)

    finder = LinkFinder(ui, nav)
    ui.input_handler(finder, 820)  # above the lock badge (800): chips and badges sit on top of items
    ui.overlay(finder.paint, order=47)

    def canvas_view() -> Any:
        return ui.app.active_view()

    def history(back_: bool) -> bool:
        view = canvas_view()
        entry = nav.history.get(view.view_name) if view is not None else None
        return bool(entry and entry[0 if back_ else 1])

    a = ui.action
    a("links.back", "links.back", "Go Back", keywords="previous jump link history return",
      category="View", enabled=lambda: history(True))
    a("links.forward", "links.forward", "Go Forward", keywords="next jump link history",
      category="View", enabled=lambda: history(False))
    a("links.hover_highlight", "links.toggle_hover_highlight", "Highlight Links on Hover",
      keywords="show linked connected outline hover", category="View", checked=lambda: nav.hover_highlight)
    ui.bind("Alt+Left", "links.back", scope="canvas")
    ui.bind("Alt+Right", "links.forward", scope="canvas")
    for order, key in enumerate(("links.back", "links.forward", "links.hover_highlight")):
        ui.menu("canvas", key, path="View", group="view.links", order=order)

    def connected_count() -> int:
        names = session.selection.names
        return len(nav.targets_of(list(names))) if 0 < len(names) <= LinkFinder.MAX_SOURCES else 0

    ui.context_key("links.connected", connected_count)
    ui.menu("canvas", builder=lambda menu: fill_linked_menu(nav, menu, list(session.selection.names)),
            text="Linked To", icon="link", group="select", order=21, when="links.connected > 0")

    def has_links(info: Any) -> bool:
        return info.single and bool(nav.targets(info.names[0]))

    ui.contribute("inspector.sections", InspectorSection(
        key="links", title="Links", order=85, applies=has_links, generic=True,
        build=lambda parent, _i: LinksSection(ui, nav, parent)))
    return nav
