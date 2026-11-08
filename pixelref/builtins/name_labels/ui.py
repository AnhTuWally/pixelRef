"""Name labels on the canvas: exact label size, buttons on the selected item, menus and Shift+N."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QCheckBox, QFormLayout, QWidget

from pixelref.plugin_api.contracts import EmptyPanel

from pixelref.plugin_api.ui import HANDLED, InputHandler, PointerEvent, icon, theme

MAX_BUTTONS = 40  # a big selection doesn't sprout hundreds of buttons


class LabelButtons(InputHandler):
    """Buttons drawn on the selection (Select tool), like the lock and copy badges:

    * a **label** button in the top-right corner of a selected image, movie or
      sequence shows or hides its name label (lit while the label shows);
    * a **lock** button beside a showing label (when it or its item is
      selected) unlocks it so it can be dragged, or locks it back to the corner.
    """

    SIZE = 22.0
    INSET = 10.0  # clear of the gizmo's corner handles

    def __init__(self, ui: Any, service: Any) -> None:
        self.ui = ui
        self.service = service
        self.over: tuple[Any, str, str] | None = None  # (view, kind, node) under the pointer

    def shown_in(self, view: Any) -> bool:
        tool = self.ui.tools.active
        return tool is not None and tool.id == "select" and view.display.show_overlays

    def _view_rect(self, view: Any, name: str) -> QRectF | None:
        item = self.ui.canvas.item(name)
        if item is None or not item.isVisible():
            return None
        try:
            return view.mapFromScene(item.sceneBoundingRect()).boundingRect().toRectF()
        except RuntimeError:  # the item was replaced by a refresh
            return None

    def buttons(self, view: Any) -> list[tuple[QRectF, str, str]]:
        """(rect in viewport pixels, kind, node): kind is "label" (on media) or "lock" (beside a label)."""
        if not self.shown_in(view):
            return []
        scene = self.ui.session.scene
        service = self.service
        size, inset = self.SIZE, self.INSET
        found: list[tuple[QRectF, str, str]] = []
        labels: list[str] = []
        for name in self.ui.session.selection.names[:MAX_BUTTONS]:
            if not scene.has(name):
                continue
            if service.is_media(name):
                rect = self._view_rect(view, name)
                if rect is not None and rect.width() >= 2 * size + inset and rect.height() >= 2 * size + inset:
                    found.append((QRectF(rect.right() - size - inset, rect.top() + inset, size, size), "label", name))
                labels.extend(label for label in service.labels_of(name) if service.is_shown(label))
            elif service.is_label(name):
                labels.append(name)
        viewport = QRectF(view.viewport().rect())
        for label in dict.fromkeys(labels):
            rect = self._view_rect(view, label)
            if rect is None or not rect.intersects(viewport):
                continue
            found.append((QRectF(rect.right() + 10.0, rect.center().y() - size / 2, size, size), "lock", label))
        return found

    def _button_at(self, view: Any, pos: QPointF) -> tuple[QRectF, str, str] | None:
        return next((b for b in self.buttons(view) if b[0].contains(pos)), None)

    def _set_over(self, over: tuple[Any, str, str] | None) -> None:
        if over == self.over:
            return
        for view in {entry[0] for entry in (self.over, over) if entry is not None}:
            view.viewport().update()
        self.over = over

    def hover(self, e: PointerEvent) -> None:
        button = self._button_at(e.view, e.pos)
        if button is None:
            self._set_over(None)
            return
        _rect, kind, name = button
        self._set_over((e.view, kind, name))
        e.set_cursor(Qt.PointingHandCursor)
        if kind == "label":
            self.ui.status("Click to hide the name label" if self.service.shown_label(name)
                           else "Click to show the name label")
        else:
            pinned = self.ui.session.scene.get_node(name).attrs.get("label_pinned", True)
            self.ui.status("Click to unlock the label so it can be dragged" if pinned
                           else "Click to lock the label back to the corner")

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind != "press" or e.button != Qt.LeftButton:
            return None
        button = self._button_at(e.view, e.pos)
        if button is None:
            return None
        _rect, kind, name = button
        self._set_over(None)
        if kind == "label":
            self.ui.app.run_command("name_labels.toggle", [name])
        else:
            self.ui.app.run_command("name_labels.toggle_pin", [name])
        e.view.viewport().update()
        return HANDLED

    def leave(self, view: Any) -> None:
        if self.over is not None and self.over[0] is view:
            self._set_over(None)

    def paint(self, view: Any, painter: QPainter) -> None:
        buttons = self.buttons(view)
        if not buttons:
            return
        scene = self.ui.session.scene
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        for rect, kind, name in buttons:
            hovered = self.over is not None and self.over[0] is view and self.over[1:] == (kind, name)
            if kind == "label":
                on = self.service.shown_label(name) is not None
                glyph = "text"
            else:
                on = not scene.get_node(name).attrs.get("label_pinned", True)  # lit while free to drag
                glyph = "lock_open" if on else "lock"
            fill = QColor(theme.ACCENT)
            if hovered:
                fill = fill.lighter(125)
            elif not on:
                fill = QColor(20, 22, 27, 215)
            painter.setPen(QColor(0, 0, 0, 120))
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 5.0, 5.0)
            icon(glyph).paint(painter, rect.adjusted(4.0, 4.0, -4.0, -4.0).toRect())
        painter.restore()


class LabelNewRow(QWidget):
    """"Name labels: ☐ Show on new items", in the Canvas panel (Attribute Editor, nothing selected)."""

    def __init__(self, ui: Any, service: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self.service = service
        form = QFormLayout(self)
        form.setContentsMargins(2, 0, 2, 0)
        form.setSpacing(6)
        self.check = QCheckBox("Show on new items", self)
        self.check.setToolTip("Give every image, movie or sequence you add a name label (applies to all boards)")
        self.check.toggled.connect(lambda on: on != service.label_new()
                                   and ui.app.run_command("name_labels.set_label_new", on))
        form.addRow("Name labels", self.check)
        self.refresh()

    def refresh(self) -> None:
        self.check.blockSignals(True)
        self.check.setChecked(self.service.label_new())
        self.check.blockSignals(False)


def activate_name_labels_ui(ui: Any) -> None:
    from pixelref.builtins.text.items import TextItem

    session = ui.session
    scene = session.scene
    service = session.services.get("name_labels")

    # Place labels from the size the canvas will draw, not a guess. The scratch item never joins a scene.
    probe = TextItem("__name_label_probe__")

    def measure(attrs: dict[str, Any]) -> list[float]:
        probe.apply_attrs(attrs)
        rect = probe.content_rect()
        return [rect.x(), rect.y(), rect.width(), rect.height()]

    service.measure = measure

    buttons = LabelButtons(ui, service)
    ui.input_handler(buttons, 790)  # below the lock badge (800), above interactions (750) and the gizmo (600)
    ui.overlay(buttons.paint, order=95)

    # ---- "Show on new items", under the Canvas settings ------------------------------------------

    rows: list[LabelNewRow] = []

    def make_row(parent: QWidget, _inspector: Any) -> LabelNewRow:
        row = LabelNewRow(ui, service, parent)
        rows.append(row)
        row.destroyed.connect(lambda *_a, r=row: r in rows and rows.remove(r))
        return row

    def refresh_rows(_event: Any = None) -> None:
        for row in list(rows):
            try:
                row.refresh()
            except RuntimeError:
                rows.remove(row)

    ui.contribute("inspector.empty_panels", EmptyPanel("name_labels", 1, make_row))  # right after Canvas (0)
    ui.on("name_labels.label_new", refresh_rows)
    ui.action("name_labels.label_new", "name_labels.set_label_new", "Name Labels on New Items",
              keywords="name label caption filename new import drop default automatic", category="View",
              checked=lambda: service.label_new())

    # ---- Actions and menus ---------------------------------------------------------------------

    def count(test: Any) -> int:
        return sum(1 for n in session.selection.names if scene.has(n) and test(n))

    def pinned(name: str) -> bool:
        return bool(scene.get_node(name).attrs.get("label_pinned", True))

    ui.context_key("name_labels.showable", lambda: count(
        lambda n: service.is_media(n) and service.shown_label(n) is None))
    ui.context_key("name_labels.hideable", lambda: count(
        lambda n: (service.is_label(n) and service.is_shown(n))
        or (service.is_media(n) and service.shown_label(n) is not None)))
    ui.context_key("name_labels.pinned", lambda: count(lambda n: service.is_label(n) and pinned(n)))
    ui.context_key("name_labels.unpinned", lambda: count(lambda n: service.is_label(n) and not pinned(n)))
    ui.context_key("name_labels.labels", lambda: count(service.is_label))

    keywords = "name label caption filename title tag show"
    for order, (key, text, glyph, when) in enumerate((
        ("show", "Show Name Label", "text", "name_labels.showable > 0"),
        ("hide", "Hide Name Label", "eye_off", "name_labels.hideable > 0"),
        ("unpin", "Unlock Label Position", "lock_open", "name_labels.pinned > 0"),
        ("pin", "Lock Label to Corner", "lock", "name_labels.unpinned > 0"),
        ("reset", "Reset Label", "", "name_labels.labels > 0"),
    ), start=10):
        options = {"icon": glyph} if glyph else {}
        ui.action(f"name_labels.{key}", f"name_labels.{key}", text, keywords=keywords, category="Annotate", **options)
        ui.menu("canvas", f"name_labels.{key}", group="state", order=order, when=when)
    ui.action("name_labels.toggle", "name_labels.toggle", "Show / Hide Name Labels", keywords=keywords,
              category="Annotate", icon="text")
    ui.bind("Shift+N", "name_labels.toggle", when="selection.count > 0")
