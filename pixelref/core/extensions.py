"""Extension points, services and disposables.

* An **extension point** is a named slot; plugins *contribute* objects to it
  and whoever declared it reads the contributions. The kernel declares a few
  (``node_types``, ``tools``…); plugins can declare their own (the gizmo
  declares ``gizmo.handles``, the inspector ``inspector.sections``…).
* A **service** is a named object one plugin provides and others use
  (``brush``, ``selection``…).
* Every registration returns a :class:`Disposable`; disposing it undoes the
  registration. A plugin's context disposes everything it registered when the
  plugin is deactivated, which is what makes plugins switchable at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


class Disposable:
    """Undo a registration exactly once."""

    __slots__ = ("_callback",)

    def __init__(self, callback: Callable[[], None] | None = None) -> None:
        self._callback = callback

    def dispose(self) -> None:
        callback, self._callback = self._callback, None
        if callback is not None:
            callback()

    @property
    def disposed(self) -> bool:
        return self._callback is None

    @staticmethod
    def combine(items: Iterable["Disposable"]) -> "Disposable":
        items = list(items)

        def dispose_all() -> None:
            for item in reversed(items):
                item.dispose()

        return Disposable(dispose_all)


@dataclass
class Contribution:
    point: str
    value: Any
    plugin: str | None
    order: float = 0.0
    serial: int = 0


@dataclass
class ExtensionPoint:
    name: str
    owner: str | None
    doc: str = ""
    validator: Callable[[Any], None] | None = None
    contributions: list[Contribution] = field(default_factory=list)
    listeners: list[Callable[[], None]] = field(default_factory=list)


class ExtensionRegistry:
    """Named extension points and their contributions."""

    def __init__(self) -> None:
        self._points: dict[str, ExtensionPoint] = {}
        # Contributions made before the point was declared (load order is
        # resolved from dependencies, but a soft contribution may come first).
        self._pending: dict[str, list[Contribution]] = {}
        self._serial = 0

    def declare(
        self, name: str, owner: str | None = None, doc: str = "", validator: Callable[[Any], None] | None = None
    ) -> Disposable:
        if name in self._points:
            raise ValueError(f"Extension point '{name}' is already declared by {self._points[name].owner}")
        point = ExtensionPoint(name=name, owner=owner, doc=doc, validator=validator)
        point.contributions.extend(self._pending.pop(name, []))
        self._points[name] = point
        return Disposable(lambda: self._undeclare(name))

    def _undeclare(self, name: str) -> None:
        point = self._points.pop(name, None)
        if point is not None and point.contributions:
            self._pending[name] = list(point.contributions)

    def is_declared(self, name: str) -> bool:
        return name in self._points

    def points(self) -> list[ExtensionPoint]:
        return list(self._points.values())

    def contribute(self, name: str, value: Any, plugin: str | None = None, order: float = 0.0) -> Disposable:
        self._serial += 1
        contribution = Contribution(point=name, value=value, plugin=plugin, order=order, serial=self._serial)
        point = self._points.get(name)
        if point is None:
            self._pending.setdefault(name, []).append(contribution)
        else:
            if point.validator is not None:
                point.validator(value)
            point.contributions.append(contribution)
            self._notify(point)
        return Disposable(lambda: self._remove(contribution))

    def _remove(self, contribution: Contribution) -> None:
        point = self._points.get(contribution.point)
        if point is not None and contribution in point.contributions:
            point.contributions.remove(contribution)
            self._notify(point)
            return
        pending = self._pending.get(contribution.point, [])
        if contribution in pending:
            pending.remove(contribution)

    def contributions(self, name: str) -> list[Any]:
        point = self._points.get(name)
        if point is None:
            return []
        ordered = sorted(point.contributions, key=lambda c: (c.order, c.serial))
        return [c.value for c in ordered]

    def contribution_records(self, name: str) -> list[Contribution]:
        point = self._points.get(name)
        return sorted(point.contributions, key=lambda c: (c.order, c.serial)) if point else []

    def watch(self, name: str, callback: Callable[[], None]) -> Disposable:
        """Call ``callback`` whenever contributions to ``name`` change."""
        point = self._points.get(name)
        if point is None:
            raise ValueError(f"Extension point '{name}' is not declared")
        point.listeners.append(callback)
        return Disposable(lambda: callback in point.listeners and point.listeners.remove(callback))

    @staticmethod
    def _notify(point: ExtensionPoint) -> None:
        for callback in list(point.listeners):
            callback()


class ServiceRegistry:
    """Named services provided by plugins (or the kernel)."""

    def __init__(self) -> None:
        self._services: dict[str, tuple[Any, str | None]] = {}

    def provide(self, name: str, service: Any, plugin: str | None = None, replace: bool = False) -> Disposable:
        """Provide a service. A name that is taken fails, unless ``replace``: then this one
        stands in for the existing service until disposed."""
        previous = self._services.get(name)
        if previous is not None and not replace:
            owner = f"plugin '{previous[1]}'" if previous[1] else "pixelRef"
            raise ValueError(f"Service '{name}' is already provided by {owner}")
        entry = (service, plugin)
        self._services[name] = entry

        def withdraw() -> None:
            if self._services.get(name) is entry:
                if previous is not None:
                    self._services[name] = previous
                else:
                    del self._services[name]

        return Disposable(withdraw)

    def get(self, name: str, default: Any = None) -> Any:
        entry = self._services.get(name)
        return entry[0] if entry is not None else default

    def require(self, name: str) -> Any:
        entry = self._services.get(name)
        if entry is None:
            raise LookupError(f"Service '{name}' is not available (is the plugin providing it enabled?)")
        return entry[0]

    def has(self, name: str) -> bool:
        return name in self._services

    def names(self) -> list[str]:
        return sorted(self._services)

    def provider(self, name: str) -> str | None:
        entry = self._services.get(name)
        return entry[1] if entry else None
