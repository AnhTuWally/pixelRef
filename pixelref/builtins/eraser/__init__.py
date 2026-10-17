"""The Eraser tool.

It deletes items of node types with the ``erasable`` capability that it is
dragged across (never images or text). Plugins can veto individual items
through ``eraser.policy`` (callables ``item -> bool``).
"""

from __future__ import annotations

from typing import Any


def activate_ui(ui: Any) -> None:
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap

    from pixelref.plugin_api import CAP_ERASABLE
    from pixelref.plugin_api.ui import HANDLED, NodeItemMixin, PointerEvent, Tool

    ui.declare_extension_point("eraser.policy", "callable(item) -> bool: False keeps an item from being erased")
    session = ui.session

    def ring_cursor() -> QCursor:
        pm = QPixmap(20, 20)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("black"), 3.0))
        p.drawEllipse(QRectF(3, 3, 14, 14))
        p.setPen(QPen(QColor("white"), 1.5))
        p.drawEllipse(QRectF(3, 3, 14, 14))
        p.end()
        return QCursor(pm, 10, 10)

    class EraserTool(Tool):
        id = "eraser"
        title = "Erase"
        action_text = "Eraser"
        icon = "eraser"
        tooltip = "Eraser (E) · drag across pen strokes, rectangles and arrows to delete them"
        keywords = "remove delete rub"
        shortcuts = ("E",)
        order = 40.0
        drawing_session = "start"
        hint = "Drag across pen strokes, rectangles or arrows to erase them · Esc or V to stop"

        def __init__(self) -> None:
            super().__init__(ui)
            self.op: dict[str, Any] | None = None
            self._cursor: QCursor | None = None

        def current_cursor(self) -> Any:
            if self._cursor is None:
                self._cursor = ring_cursor()
            return self._cursor

        @property
        def busy(self) -> bool:
            return self.op is not None

        def cancel(self) -> bool:
            if self.op is None:
                return False
            for item in self.op["hits"]:
                try:
                    item.setVisible(True)
                except RuntimeError:
                    pass
            self.op = None
            return True

        def banner(self, view: Any) -> str | None:
            return "⌫  Eraser · drag across pen strokes, rectangles and arrows   —   Esc / V to finish"

        def erasable(self, item: Any) -> bool:
            if not isinstance(item, NodeItemMixin) or not item.isVisible() or item.locked:
                return False
            if not session.types.has_capability(item.node_type, CAP_ERASABLE):
                return False
            return all(policy(item) for policy in ui.contributions("eraser.policy"))

        def erase_along(self, view: Any, a: QPointF, b: QPointF) -> None:
            """Hide erasable items touched between two viewport points; they are
            deleted (as one undo step) on release."""
            path = QPainterPath(a)
            path.lineTo(b + QPointF(0.01, 0.0))
            stroker = QPainterPathStroker()
            stroker.setWidth(12.0)
            stroker.setCapStyle(Qt.RoundCap)
            area = stroker.createStroke(path)
            for item in view.items(area.toFillPolygon().toPolygon()):
                if item not in self.op["hits"] and view.display.shows(getattr(item, "node_name", "")) \
                        and self.erasable(item):
                    self.op["hits"].add(item)
                    item.setVisible(False)

        def pointer(self, e: PointerEvent) -> str | None:
            if e.kind == "press" and e.button == Qt.LeftButton:
                self.op = {"hits": set(), "last": e.pos, "view": e.view}
                self.erase_along(e.view, e.pos, e.pos)
                return HANDLED
            if self.op is None:
                return None
            if e.kind == "move":
                self.erase_along(e.view, self.op["last"], e.pos)
                self.op["last"] = e.pos
                return HANDLED
            if e.kind == "release":
                op, self.op = self.op, None
                names = [item.node_name for item in op["hits"] if session.scene.has(item.node_name)]
                if names and ui.app.run_command("eraser.erase", names) is not None:
                    ui.status(f"Erased {len(names)} stroke(s)")
                    return HANDLED
                for item in op["hits"]:  # nothing was erased: show the hidden items again
                    try:
                        item.setVisible(True)
                    except RuntimeError:
                        pass
                return HANDLED
            return None

    def erase(nodes: list[str]) -> list[str]:
        """Delete erasable nodes (strokes, rectangles, arrows)."""
        erased = []
        for name in nodes:
            if session.scene.has(name) and session.types.has_capability(session.scene.get_node(name).node_type,
                                                                          CAP_ERASABLE):
                session.scene.delete_node(name)
                erased.append(name)
        session.selection.remove(erased)
        return erased

    ui.command("erase", erase, alias="erase", label=lambda nodes: f"Erase {len(nodes)} stroke(s)")
    ui.register_tool(EraserTool())
