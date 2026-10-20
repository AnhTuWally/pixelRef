"""Attribute specifications.

Every node type declares its attributes with :class:`Attr`. The spec drives
default values, light validation when a value is set, the generated Attribute
Editor, and the file format (only ``storable`` attributes are saved).

Values are always plain JSON data (numbers, strings, lists, dicts, None) so
they can be saved, copied, undone and echoed to the console unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .errors import SceneError
from .values import fast_copy, is_json

# Supported attribute types. The value representation for each:
#   bool        True / False
#   flag        0 / 1 (integer switch, e.g. visibility)
#   int, float  numbers
#   float2      [x, y]
#   color       [r, g, b] 0-255 (an optional 4th alpha entry is kept)
#   color4      [r, g, b, a] 0-255
#   string      text; "text" is the same but edited in a multi-line field
#   enum        one of ``choices``
#   path        file path (relative to the scene file when possible)
#   asset       id of an embedded asset in the scene file
#   node_ref    UUID of another node (or "")
#   node_refs   list of node UUIDs
#   string_list list of strings (e.g. tags)
#   rect        [x, y, w, h] or None
#   points      [[x, y], ...]
#   any         anything JSON-like (undeclared attributes from files, text runs…)
ATTR_TYPES = frozenset(
    {
        "bool", "flag", "int", "float", "float2", "color", "color4", "string", "text", "enum", "path",
        "asset", "node_ref", "node_refs", "string_list", "rect", "points", "any",
    }
)


@dataclass(frozen=True)
class Attr:
    """One attribute of a node type (or a dynamic attribute on one node)."""

    name: str
    type: str = "any"
    default: Any = None
    short: str = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    storable: bool = True
    hidden: bool = False
    computed: bool = False
    readonly: bool = False
    label: str = ""
    group: str = ""
    doc: str = ""
    # Editing hints for number fields (they never change what a value may be):
    # ``step`` is the increment (arrow keys, Ctrl-snapping while dragging);
    # ``precision`` the decimals shown; ``soft_min``/``soft_max`` the usual
    # range, which a drag covers in a fixed distance (typing can go beyond it,
    # up to minimum/maximum); ``unit`` a suffix such as "px" or "%";
    # ``display_scale`` multiplies the value for display (opacity 0-1 shown as 0-100 %).
    step: float | None = None
    precision: int | None = None
    soft_min: float | None = None
    soft_max: float | None = None
    unit: str = ""
    display_scale: float = 1.0
    # Sparse (extension) attributes only: setting the default removes the value, so files stay small.
    omit_default: bool = False
    # Copy / paste attributes: "style" (offered and ticked), "geometry" (offered, unticked), "never"
    # (not offered). Empty: inferred from the type and name (see core.transfer).
    transfer: str = ""
    # A glyph shown beside the name in the Attribute Editor (see core.attr_symbols); empty: the global default.
    symbol: str = ""
    extra: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)
    # Extra check after the type's own coercion: validator(value) -> canonical
    # value, raising SceneError for bad data (e.g. an effect stack's entries).
    validator: Callable[[Any], Any] | None = field(default=None, compare=False, hash=False)

    def __post_init__(self) -> None:
        if self.type not in ATTR_TYPES:
            raise SceneError(f"Attribute '{self.name}': unknown type '{self.type}'")
        if self.transfer not in ("", "style", "geometry", "never"):
            raise SceneError(f"Attribute '{self.name}': transfer must be style, geometry or never")

    @property
    def nice_name(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize()

    def default_value(self) -> Any:
        return fast_copy(self.default)

    def validate(self, value: Any) -> Any:
        """Return ``value`` in its canonical form, or raise SceneError."""
        if self.readonly or self.computed:
            raise SceneError(f"Attribute '{self.name}' is read-only")
        value = coerce(self, value)
        if self.validator is not None:
            value = self.validator(value)
        return value

    # ---- (De)serialisation of dynamic attribute specs ------------------------------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type}
        if self.default is not None:
            data["default"] = fast_copy(self.default)
        for key in ("short", "label", "group", "doc", "symbol"):
            if getattr(self, key):
                data[key] = getattr(self, key)
        if self.minimum is not None:
            data["min"] = self.minimum
        if self.maximum is not None:
            data["max"] = self.maximum
        if self.choices:
            data["choices"] = list(self.choices)
        if self.hidden:
            data["hidden"] = True
        for key in ("step", "precision", "soft_min", "soft_max"):
            if getattr(self, key) is not None:
                data[key] = getattr(self, key)
        if self.unit:
            data["unit"] = self.unit
        if self.display_scale != 1.0:
            data["display_scale"] = self.display_scale
        if self.transfer:
            data["transfer"] = self.transfer
        return data

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "Attr":
        attr_type = str(data.get("type", "any"))
        choices = data.get("choices", ())
        return cls(
            name=name,
            type=attr_type if attr_type in ATTR_TYPES else "any",
            default=fast_copy(data.get("default")),
            short=str(data.get("short", "")),
            minimum=_limit(data.get("min")),
            maximum=_limit(data.get("max")),
            choices=tuple(str(c) for c in choices) if isinstance(choices, (list, tuple)) else (),
            hidden=bool(data.get("hidden", False)),
            label=str(data.get("label", "")),
            group=str(data.get("group", "")),
            doc=str(data.get("doc", "")),
            symbol=str(data.get("symbol", "")),
            step=_limit(data.get("step")),
            precision=_precision(data.get("precision")),
            soft_min=_limit(data.get("soft_min")),
            soft_max=_limit(data.get("soft_max")),
            unit=str(data.get("unit", "")),
            display_scale=_limit(data.get("display_scale")) or 1.0,
            transfer=str(data.get("transfer", "")) if data.get("transfer") in ("style", "geometry", "never") else "",
        )


def _limit(value: Any) -> float | None:
    """A min/max read from a file: a number or None (junk would break every set)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _precision(value: Any) -> int | None:
    """Decimals read from a file: 0-6, or None for junk."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(6, int(value)))


def _round(attr: Attr, value: float) -> int:
    try:
        return int(round(value))
    except (OverflowError, ValueError):  # inf / nan
        raise SceneError(f"Attribute '{attr.name}' expects a finite number, got {value!r}") from None


def _number(attr: Attr, value: Any, integer: bool = False) -> int | float:
    if isinstance(value, bool):
        value = int(value)
    if not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise SceneError(f"Attribute '{attr.name}' expects a number, got {value!r}") from None
    if integer:
        value = _round(attr, value)
    else:
        value = float(value)
    if attr.minimum is not None and value < attr.minimum:
        value = type(value)(attr.minimum)
    if attr.maximum is not None and value > attr.maximum:
        value = type(value)(attr.maximum)
    return value


def _vector(attr: Attr, value: Any, size: int, integer: bool = False) -> list:
    if not isinstance(value, (list, tuple)) or len(value) < size:
        raise SceneError(f"Attribute '{attr.name}' expects {size} numbers, got {value!r}")
    out: list = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            try:
                item = float(item)
            except (TypeError, ValueError):
                raise SceneError(f"Attribute '{attr.name}' expects numbers, got {value!r}") from None
        out.append(_round(attr, item) if integer else item)
    return out


def _items(attr: Attr, value: Any) -> list:
    try:
        return list(value)
    except TypeError:
        raise SceneError(f"Attribute '{attr.name}' expects a list, got {value!r}") from None


def coerce(attr: Attr, value: Any) -> Any:
    """Light validation: keeps the JSON representation the rest of the app uses."""
    kind = attr.type
    if value is None:
        if attr.default is None or kind in ("any", "rect", "node_ref", "color", "color4"):
            return None
        raise SceneError(f"Attribute '{attr.name}' cannot be None")
    if kind == "any":
        if not is_json(value):  # a set or an object would break saving and every undo step
            raise SceneError(f"Attribute '{attr.name}' expects JSON data (numbers, text, lists, dicts), got {value!r}")
        return fast_copy(list(value) if isinstance(value, tuple) else value)
    if kind == "bool":
        return bool(value)
    if kind == "flag":
        return 1 if bool(value) else 0
    if kind == "int":
        return _number(attr, value, integer=True)
    if kind == "float":
        return _number(attr, value)
    if kind == "float2":
        return _vector(attr, value, 2)[:2]
    if kind in ("color", "color4"):
        if isinstance(value, str) and value.startswith("#"):
            text = value.lstrip("#")
            try:
                parts = [int(text[i : i + 2], 16) for i in range(0, len(text), 2)]
            except ValueError:
                raise SceneError(f"Attribute '{attr.name}': bad colour {value!r}") from None
            value = parts
        parts = _vector(attr, value, 3, integer=True)
        parts = [max(0, min(255, part)) for part in parts]
        if kind == "color4" and len(parts) == 3:
            parts.append(255)
        return parts[:4]  # rgb(a): an alpha entry is kept, anything beyond is dropped
    if kind in ("string", "text", "path", "asset", "node_ref"):
        return str(value)
    if kind == "enum":
        text = str(value)
        if attr.choices and text not in attr.choices:
            raise SceneError(f"Attribute '{attr.name}' must be one of {', '.join(attr.choices)}, got {text!r}")
        return text
    if kind in ("string_list", "node_refs"):
        if isinstance(value, str):
            value = [value]
        return [str(item) for item in _items(attr, value)]
    if kind == "rect":
        return _vector(attr, value, 4)[:4]
    if kind == "points":
        return [_vector(attr, point, 2)[:2] for point in _items(attr, value)]
    return fast_copy(value)
