"""The canvas input pipeline.

A canvas view turns Qt events into :class:`PointerEvent` / :class:`KeyEvent` /
:class:`WheelEvent` and offers them to a priority-ordered chain of
:class:`InputHandler` objects. Each handler returns

* ``HANDLED`` — consumed;
* ``DEFAULT`` — consumed, but let Qt's own QGraphicsView handling run too
  (moving selected items, editing text);
* ``None`` — not interested, offer it to the next handler.

A handler that takes a press *captures* the pointer: the following move and
release events go straight to it. Hover moves (no button held) go to every
handler; the first one to call :meth:`PointerEvent.set_cursor` picks the cursor.

Default chain (higher priority first)::

    900  navigation      pan, zoom, right-click menu         (navigation plugin)
    600  gizmo handles   when the active tool allows them    (gizmo plugin)
    500  active tool                                          (kernel)
    450  window drag     Alt+drag moves the window           (window_modes plugin)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QCursor

from ...core.extensions import Disposable

if TYPE_CHECKING:
    from .view import CanvasView

HANDLED = "handled"
DEFAULT = "default"

_MISSING = object()


class PointerEvent:
    """A mouse event on a canvas view (positions in viewport pixels)."""

    def __init__(self, view: "CanvasView", kind: str, qt_event: Any) -> None:
        self.view = view
        self.kind = kind  # press, move, release, double, hover
        self.qt = qt_event
        self.pos: QPointF = qt_event.position()
        self.global_pos: QPointF = qt_event.globalPosition()
        self.button = qt_event.button()
        self.buttons = qt_event.buttons()
        self.modifiers = qt_event.modifiers()
        self._scene_pos: QPointF | None = None
        self._item: Any = _MISSING
        self.cursor: QCursor | Qt.CursorShape | None = None

    @property
    def scene_pos(self) -> QPointF:
        if self._scene_pos is None:
            self._scene_pos = self.view.to_scene(self.pos)
        return self._scene_pos

    @property
    def item(self) -> Any:
        """Top-most visible node item under the pointer (or None)."""
        if self._item is _MISSING:
            self._item = self.view.node_item_at(self.pos.toPoint())
        return self._item

    @property
    def shift(self) -> bool:
        return bool(self.modifiers & Qt.ShiftModifier)

    @property
    def ctrl(self) -> bool:
        return bool(self.modifiers & Qt.ControlModifier)

    @property
    def alt(self) -> bool:
        return bool(self.modifiers & Qt.AltModifier)

    @property
    def left(self) -> bool:
        return self.button == Qt.LeftButton

    def set_cursor(self, cursor: QCursor | Qt.CursorShape) -> None:
        if self.cursor is None:
            self.cursor = cursor


class KeyEvent:
    def __init__(self, view: "CanvasView", kind: str, qt_event: Any) -> None:
        self.view = view
        self.kind = kind  # press, release
        self.qt = qt_event
        self.key = qt_event.key()
        self.modifiers = qt_event.modifiers()
        self.text = qt_event.text()
        self.auto_repeat = qt_event.isAutoRepeat()


class WheelEvent:
    def __init__(self, view: "CanvasView", qt_event: Any) -> None:
        self.view = view
        self.qt = qt_event
        self.pos: QPointF = qt_event.position()
        self.steps = qt_event.angleDelta().y() / 120.0
        self.modifiers = qt_event.modifiers()

    @property
    def ctrl(self) -> bool:
        return bool(self.modifiers & Qt.ControlModifier)


class InputHandler:
    """Override what you need; return HANDLED / DEFAULT / None."""

    def pointer(self, e: PointerEvent) -> str | None:
        return None

    def hover(self, e: PointerEvent) -> None:
        return None

    def key(self, e: KeyEvent) -> str | None:
        return None

    def wheel(self, e: WheelEvent) -> str | None:
        return None

    def leave(self, view: "CanvasView") -> None:
        return None


class InputPipeline:
    def __init__(self) -> None:
        self._handlers: list[tuple[float, int, InputHandler]] = []
        self._serial = 0
        self._capture: dict[int, InputHandler] = {}

    def add(self, handler: InputHandler, priority: float) -> Disposable:
        self._serial += 1
        entry = (priority, self._serial, handler)
        self._handlers.append(entry)
        self._handlers.sort(key=lambda e: (-e[0], e[1]))

        def remove() -> None:
            if entry in self._handlers:
                self._handlers.remove(entry)
            for key, captured in list(self._capture.items()):
                if captured is handler:
                    del self._capture[key]

        return Disposable(remove)

    def handlers(self) -> list[InputHandler]:
        return [h for _p, _s, h in self._handlers]

    def captured(self, view: "CanvasView") -> InputHandler | None:
        return self._capture.get(id(view))

    def release_capture(self, view: "CanvasView") -> None:
        self._capture.pop(id(view), None)

    def pointer(self, e: PointerEvent) -> str | None:
        key = id(e.view)
        captured = self._capture.get(key)
        if captured is not None and e.kind in ("move", "release", "press"):
            result = captured.pointer(e)
            if e.kind == "release" and e.buttons == Qt.NoButton:
                self._capture.pop(key, None)
            if result is not None or e.kind != "press":
                return result or HANDLED
            if e.buttons != e.button:
                # Another button is still held: the captured handler is mid-drag and
                # must keep the capture, or it would never get its release.
                return HANDLED
        if e.kind == "move" and e.buttons == Qt.NoButton:
            e.kind = "hover"
            for handler in self.handlers():
                handler.hover(e)
            return None
        for handler in self.handlers():
            result = handler.pointer(e)
            if result is not None:
                if e.kind == "press":
                    self._capture[key] = handler
                return result
        return None

    def key(self, e: KeyEvent) -> str | None:
        for handler in self.handlers():
            result = handler.key(e)
            if result is not None:
                return result
        return None

    def wheel(self, e: WheelEvent) -> str | None:
        for handler in self.handlers():
            result = handler.wheel(e)
            if result is not None:
                return result
        return None

    def leave(self, view: "CanvasView") -> None:
        for handler in self.handlers():
            handler.leave(view)
