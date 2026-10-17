"""Helpers for the JSON-like values stored in node attributes."""

from __future__ import annotations

import copy
from typing import Any

_SCALARS = (int, float, str, bool, type(None))


def fast_copy(value: Any) -> Any:
    """Deep copy for JSON-like data (lists/dicts of scalars).

    Several times faster than ``copy.deepcopy`` on the nested point lists of
    pen strokes, which matters because undo snapshots copy every node.
    """
    kind = type(value)
    if kind in _SCALARS:
        return value
    if kind is list:
        return [item if type(item) in _SCALARS else fast_copy(item) for item in value]
    if kind is dict:
        return {key: item if type(item) in _SCALARS else fast_copy(item) for key, item in value.items()}
    if kind is tuple:
        return tuple(fast_copy(item) for item in value)
    return copy.deepcopy(value)


def is_json(value: Any) -> bool:
    """True for plain JSON data (what scene files and undo snapshots can hold)."""
    if isinstance(value, _SCALARS):
        return True
    if isinstance(value, (list, tuple)):
        return all(is_json(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, _SCALARS) and is_json(item) for key, item in value.items())
    return False


def differs(a: Any, b: Any, tolerance: float = 1e-6) -> bool:
    """True when two attribute values differ (numbers compared with a tolerance)."""
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) != len(b) or any(differs(x, y, tolerance) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) > tolerance
    return a != b
