"""The selection: an ordered list of node names shared by every view."""

from __future__ import annotations

from typing import Callable, Iterable

from .extensions import Disposable


class Selection:
    def __init__(self, exists: Callable[[str], bool], removals: Callable[[], int] | None = None) -> None:
        self._exists = exists
        # A counter that changes whenever names may have gone away; prune() is free otherwise.
        self._removals = removals
        self._pruned_at: int | None = None
        self._names: list[str] = []
        self._listeners: list[Callable[[list[str]], None]] = []

    @property
    def names(self) -> list[str]:
        return list(self._names)

    def __len__(self) -> int:
        return len(self._names)

    def __contains__(self, name: object) -> bool:
        return name in self._names

    def subscribe(self, callback: Callable[[list[str]], None]) -> Disposable:
        self._listeners.append(callback)
        return Disposable(lambda: callback in self._listeners and self._listeners.remove(callback))

    def _apply(self, names: Iterable[str]) -> bool:
        seen: set[str] = set()
        cleaned = []
        for name in names:
            if name and name not in seen and self._exists(name):
                seen.add(name)
                cleaned.append(name)
        if cleaned == self._names:
            return False
        self._names = cleaned
        for callback in list(self._listeners):
            callback(list(self._names))
        return True

    def set(self, names: Iterable[str]) -> bool:
        return self._apply(list(names))

    def add(self, names: Iterable[str]) -> bool:
        return self._apply(self._names + list(names))

    def remove(self, names: Iterable[str]) -> bool:
        drop = set(names)
        return self._apply(n for n in self._names if n not in drop)

    def toggle(self, names: Iterable[str]) -> bool:
        names = list(names)
        current = list(self._names)
        for name in names:
            if name in current:
                current.remove(name)
            else:
                current.append(name)
        return self._apply(current)

    def clear(self) -> bool:
        return self._apply([])

    def prune(self) -> bool:
        """Drop names that no longer exist."""
        if self._removals is not None:
            current = self._removals()
            if current == self._pruned_at:
                return False
            self._pruned_at = current
        exists = self._exists
        if all(exists(name) for name in self._names):  # the usual case, after every command
            return False
        return self._apply(list(self._names))

    def rename(self, old: str, new: str) -> None:
        if old in self._names:
            self._names = [new if n == old else n for n in self._names]
            for callback in list(self._listeners):
                callback(list(self._names))
