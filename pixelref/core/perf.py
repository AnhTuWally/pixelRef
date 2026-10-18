"""Performance spans: time named steps of the app (opt-in, near free when off).

``PIXELREF_PERF=1`` turns timing on. Every ``with perf.span("label"):`` block is
then counted (calls, total, worst) and, when it takes longer than
``PIXELREF_PERF_SLOW`` ms (default 16: one frame), printed as it happens. A
summary table is printed when the process exits. Benchmarks call
``enable()``, ``reset()`` and ``report()`` directly.

Spans may run on worker threads (image decoding, mip chains): totals add up
time across threads, so a span's total can exceed the wall-clock time.
"""

from __future__ import annotations

import atexit
import functools
import os
import sys
import threading
import time
from contextlib import nullcontext
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_stats: dict[str, list[float]] = {}  # label -> [calls, total ms, worst ms]
_enabled = os.environ.get("PIXELREF_PERF", "").strip() not in ("", "0")
_slow_ms = float(os.environ.get("PIXELREF_PERF_SLOW", "16") or 16)
_NULL = nullcontext()


class _Span:
    __slots__ = ("label", "start")

    def __init__(self, label: str) -> None:
        self.label = label
        self.start = 0.0

    def __enter__(self) -> "_Span":
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        record(self.label, (time.perf_counter() - self.start) * 1000.0)


def enabled() -> bool:
    return _enabled


def enable(on: bool = True, slow_ms: float | None = None) -> None:
    """Turn timing on or off (``slow_ms``: print spans at least this long; 0 never prints)."""
    global _enabled, _slow_ms
    _enabled = on
    if slow_ms is not None:
        _slow_ms = slow_ms


def span(label: str) -> Any:
    """Context manager timing its block under ``label`` (does nothing when timing is off)."""
    return _Span(label) if _enabled else _NULL


def timed(label: str, fn: Callable[..., T]) -> Callable[..., T]:
    """``fn`` wrapped in ``span(label)`` (checked per call, so ``enable()`` later still counts)."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        if not _enabled:
            return fn(*args, **kwargs)
        with _Span(label):
            return fn(*args, **kwargs)

    return wrapper


def record(label: str, ms: float) -> None:
    """Count one ``ms``-long call of ``label`` (for times measured elsewhere)."""
    if not _enabled:
        return
    with _lock:
        entry = _stats.get(label)
        if entry is None:
            _stats[label] = [1, ms, ms]
        else:
            entry[0] += 1
            entry[1] += ms
            if ms > entry[2]:
                entry[2] = ms
    if _slow_ms and ms >= _slow_ms:
        print(f"[perf] {label}: {ms:.1f} ms", file=sys.stderr, flush=True)


def stats() -> dict[str, tuple[int, float, float]]:
    """``{label: (calls, total ms, worst ms)}`` so far."""
    with _lock:
        return {label: (int(c), t, w) for label, (c, t, w) in _stats.items()}


def reset() -> None:
    with _lock:
        _stats.clear()


def report() -> str:
    """The spans so far as a table, slowest total first."""
    rows = sorted(stats().items(), key=lambda kv: kv[1][1], reverse=True)
    if not rows:
        return "[perf] no spans recorded"
    width = max(len(label) for label, _ in rows)
    lines = [f"{'span':<{width}}  {'calls':>7}  {'total ms':>10}  {'mean ms':>8}  {'worst ms':>9}"]
    for label, (calls, total, worst) in rows:
        lines.append(f"{label:<{width}}  {calls:>7}  {total:>10.1f}  {total / calls:>8.2f}  {worst:>9.1f}")
    return "\n".join(lines)


def _report_at_exit() -> None:
    if _enabled and _stats:
        print(report(), file=sys.stderr, flush=True)


atexit.register(_report_at_exit)
