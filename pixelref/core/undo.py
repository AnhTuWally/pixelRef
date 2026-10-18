"""Snapshot-based undo.

Each undo step stores the serialized document (without embedded image data)
from *before* the change. Changes are grouped into chunks: every top-level
command is one chunk; scripts run from the console and long canvas drags open
a chunk around everything they do, so they undo in one step.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator

from . import perf


class UndoStack:
    """Undo/redo stacks of (label, serialized state).

    ``checkpoint`` records the state *before* an edit. ``undo``/``redo`` take
    the current state so it can be restored by the opposite operation.
    """

    def __init__(self, max_size: int = 100) -> None:
        self._undo: list[tuple[str, str]] = []
        self._redo: list[tuple[str, str]] = []
        self._max = max_size

    def checkpoint(self, state: str, label: str = "") -> None:
        self._undo.append((label, state))
        if len(self._undo) > self._max:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self, current: str | None = None) -> str | None:
        if not self._undo:
            return None
        label, state = self._undo.pop()
        if current is not None:
            self._redo.append((label, current))
        return state

    def redo(self, current: str | None = None) -> str | None:
        if not self._redo:
            return None
        label, state = self._redo.pop()
        if current is not None:
            self._undo.append((label, current))
        return state

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1][0] if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1][0] if self._redo else ""

    def labels(self) -> list[str]:
        return [label for label, _state in self._undo]

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()


class UndoManager:
    """Chunks of document changes on top of an :class:`UndoStack`.

    ``dump``/``load`` serialize and restore the document. Chunks nest: only
    the outermost one records an undo step (if anything changed).

    ``revision`` (optional) returns a value that changes whenever the document
    does; with it an unchanged document is never serialized twice, so a command
    costs one serialization instead of two (and none if it changed nothing).
    """

    def __init__(self, dump: Callable[[], str], load: Callable[[str], None],
                 revision: Callable[[], Any] | None = None) -> None:
        self._dump_fn = perf.timed("undo.snapshot", dump)
        self._load = perf.timed("undo.restore", load)
        self._revision = revision
        self._cache: tuple[Any, str] | None = None  # (revision, dump at that revision)
        self._before_revision: Any = None
        self.stack = UndoStack()
        self._depth = 0
        self._before: str | None = None
        self._label = ""
        self._listeners: list[Callable[[str, str], None]] = []
        self.enabled = True

    def _dump(self) -> str:
        if self._revision is None:
            return self._dump_fn()
        revision = self._revision()
        if self._cache is not None and self._cache[0] == revision:
            return self._cache[1]
        text = self._dump_fn()
        self._cache = (revision, text)
        return text

    # ---- Listeners: (event, label) with event in "checkpoint", "undo", "redo", "reset" ----

    def subscribe(self, callback: Callable[[str, str], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: callback in self._listeners and self._listeners.remove(callback)

    def _notify(self, event: str, label: str) -> None:
        for callback in list(self._listeners):
            callback(event, label)

    # ---- Chunks --------------------------------------------------------------------------

    @property
    def in_chunk(self) -> bool:
        return self._depth > 0

    @property
    def chunk_label(self) -> str:
        return self._label

    def begin(self, label: str = "") -> None:
        if self._depth == 0:
            self._before = self._dump() if self.enabled else None
            self._before_revision = self._revision() if self._revision is not None else None
            self._label = label
        self._depth += 1

    def end(self, label: str | None = None) -> bool:
        """Close a chunk. Returns True if an undo step was recorded."""
        if self._depth == 0:
            return False
        if label:
            self._label = label
        self._depth -= 1
        if self._depth > 0:
            return False
        before, self._before = self._before, None
        if before is None:
            return False
        if self._revision is not None and self._revision() == self._before_revision:
            return False  # nothing changed
        try:
            after = self._dump()
        except BaseException:
            # The chunk stored something that can't be serialized (a set, an
            # object…): roll it back, or every later undo step and save fails.
            self._load(before)
            raise
        if after == before:
            return False
        self.stack.checkpoint(before, self._label)
        self._notify("checkpoint", self._label)
        return True

    def cancel(self) -> None:
        """Abort the outermost chunk, restoring the state from when it began."""
        before = self._before
        self._depth = 0
        self._before = None
        if before is not None:
            self._load(before)

    @contextmanager
    def chunk(self, label: str = "") -> Iterator[None]:
        """Group changes into one undo step; on an exception roll them back."""
        outermost = self._depth == 0
        self.begin(label)
        try:
            yield
        except BaseException:
            if outermost:
                self.cancel()
            else:
                self._depth -= 1
            raise
        self.end()

    def snapshot(self, label: str = "") -> None:
        """Record the current state as an undo step (for edits made outside a chunk)."""
        self.stack.checkpoint(self._dump(), label)
        self._notify("checkpoint", label)

    # ---- Undo / redo -------------------------------------------------------------------

    def undo(self) -> str | None:
        """Undo the last step. Returns its label, or None if there is nothing to undo."""
        if not self.stack.can_undo():
            return None
        label = self.stack.undo_label()
        state = self.stack.undo(self._dump())
        if state is not None:
            self._load(state)
        self._notify("undo", label)
        return label

    def redo(self) -> str | None:
        if not self.stack.can_redo():
            return None
        label = self.stack.redo_label()
        state = self.stack.redo(self._dump())
        if state is not None:
            self._load(state)
        self._notify("redo", label)
        return label

    def reset(self) -> None:
        self.stack.clear()
        self._depth = 0
        self._before = None
        self._notify("reset", "")
