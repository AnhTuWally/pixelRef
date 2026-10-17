"""Synchronous event fan-out used by the document and the session."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .extensions import Disposable


@dataclass
class SceneEvent:
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default="")

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.event_type, "payload": self.payload, "timestamp": self.timestamp}


Listener = Callable[[SceneEvent], None]


class EventStream:
    """Listeners receive every event; ``on(type, fn)`` filters by type."""

    def __init__(self) -> None:
        self._listeners: list[Listener] = []

    @property
    def has_listeners(self) -> bool:
        return bool(self._listeners)

    def subscribe(self, callback: Listener) -> Disposable:
        self._listeners.append(callback)
        return Disposable(lambda: self.unsubscribe(callback))

    def on(self, event_type: str, callback: Callable[[SceneEvent], None]) -> Disposable:
        def filtered(event: SceneEvent) -> None:
            if event.event_type == event_type or (event_type.endswith("*") and event.event_type.startswith(
                event_type[:-1])):
                callback(event)

        return self.subscribe(filtered)

    def unsubscribe(self, callback: Listener) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def emit(self, event: SceneEvent | str, payload: dict[str, Any] | None = None) -> None:
        if not self._listeners:
            return
        if isinstance(event, str):
            event = SceneEvent(event_type=event, payload=payload or {})
        for callback in list(self._listeners):
            callback(event)
