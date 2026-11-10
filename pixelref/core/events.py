"""Synchronous event fan-out used by the document and the session."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from .extensions import Disposable


class SceneEvent:
    """One event: its type, a payload and when it happened (ISO time, UTC).

    The time is taken when the event is made and only turned into text when someone reads
    ``timestamp``: dragging a big selection emits thousands of events per mouse move, and
    formatting a date for each cost more than delivering it.
    """

    __slots__ = ("event_type", "payload", "_timestamp", "_time")

    def __init__(self, event_type: str, payload: dict[str, Any] | None = None, timestamp: str = "") -> None:
        self.event_type = event_type
        self.payload: dict[str, Any] = {} if payload is None else payload
        self._timestamp = timestamp
        self._time = 0.0 if timestamp else time.time()

    @property
    def timestamp(self) -> str:
        if not self._timestamp:
            self._timestamp = datetime.fromtimestamp(self._time, timezone.utc).isoformat(timespec="milliseconds")
        return self._timestamp

    @timestamp.setter
    def timestamp(self, value: str) -> None:
        self._timestamp = value

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.event_type, "payload": self.payload, "timestamp": self.timestamp}

    def __repr__(self) -> str:
        return f"SceneEvent(event_type={self.event_type!r}, payload={self.payload!r}, timestamp={self.timestamp!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SceneEvent):
            return NotImplemented
        return (self.event_type, self.payload, self.timestamp) == (other.event_type, other.payload, other.timestamp)

    __hash__ = None  # type: ignore[assignment]  (mutable, like the dataclass it replaced)


Listener = Callable[[SceneEvent], None]


class _Typed:
    """A listener for one event type (or a ``prefix*`` pattern): what ``EventStream.on`` subscribes."""

    __slots__ = ("pattern", "prefix", "callback")

    def __init__(self, pattern: str, callback: Listener) -> None:
        self.pattern = pattern
        self.prefix = pattern[:-1] if pattern.endswith("*") else None
        self.callback = callback

    def matches(self, event_type: str) -> bool:
        return event_type == self.pattern or (self.prefix is not None and event_type.startswith(self.prefix))

    def __call__(self, event: SceneEvent) -> None:
        if self.matches(event.event_type):
            self.callback(event)


class EventStream:
    """Listeners receive every event; ``on(type, fn)`` filters by type."""

    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        # event type -> the callbacks it reaches, in subscription order. Built on first use and
        # dropped when the listeners change, so an event is not offered to every listener of every
        # other type (the UI stream has a hundred of them).
        self._routes: dict[str, tuple[Listener, ...]] = {}

    @property
    def has_listeners(self) -> bool:
        return bool(self._listeners)

    def subscribe(self, callback: Listener) -> Disposable:
        self._listeners.append(callback)
        self._routes = {}
        return Disposable(lambda: self.unsubscribe(callback))

    def on(self, event_type: str, callback: Callable[[SceneEvent], None]) -> Disposable:
        return self.subscribe(_Typed(event_type, callback))

    def unsubscribe(self, callback: Listener) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass
        self._routes = {}

    def _route(self, event_type: str) -> tuple[Listener, ...]:
        route = tuple(listener.callback if isinstance(listener, _Typed) else listener
                      for listener in self._listeners
                      if not isinstance(listener, _Typed) or listener.matches(event_type))
        self._routes[event_type] = route
        return route

    def emit(self, event: SceneEvent | str, payload: dict[str, Any] | None = None) -> None:
        if not self._listeners:
            return
        if isinstance(event, str):
            event = SceneEvent(event_type=event, payload=payload or {})
        route = self._routes.get(event.event_type)
        if route is None:
            route = self._route(event.event_type)
        for callback in route:
            callback(event)
