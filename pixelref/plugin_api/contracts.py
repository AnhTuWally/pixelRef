"""Contribution types for the extension points that foundation plugins declare.

Feature plugins contribute these objects instead of importing other plugins:

==========================  =================  =====================================
Extension point             Declared by        Contribution
==========================  =================  =====================================
``inspector.sections``      inspector          :class:`InspectorSection`
``inspector.rows``          inspector          :class:`InspectorRows`
``inspector.empty_panels``  inspector          :class:`EmptyPanel`
``quick_edit.sections``     quick_edit         :class:`QuickSection`
``ingest.handlers``         clipboard          :class:`IngestHandler`
``clipboard.exporters``     clipboard          callable(names, QMimeData) adding formats
``palette.buttons``         tool_palette       :class:`PaletteButton`
``stroke.styles``           brush              :class:`StrokeStyle`
``grouping.ungroup``        grouping           :class:`UngroupHandler`
``gizmo.handles``           gizmo              a handle provider (see the gizmo plugin)
``eraser.policy``           eraser             callable(item) -> bool (False vetoes)
``effects.types``           effects            :class:`EffectType`
``effects.processors``      effects            (effect name, fn(QImage, params, fx) -> QImage)
``color.pickers``           kernel (UI)        :class:`ColorPickerProvider`
==========================  =================  =====================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.attributes import Attr


@dataclass
class SelectionInfo:
    """What the inspector shows: the selection, resolved."""

    app: Any
    names: list[str]
    nodes: list[dict[str, Any]]
    types: set[str] = field(default_factory=set)
    item: Any = None  # the canvas item when exactly one node is selected

    @property
    def single(self) -> bool:
        return len(self.names) == 1

    @property
    def node(self) -> dict[str, Any] | None:
        return self.nodes[0] if self.nodes else None

    @property
    def attrs(self) -> dict[str, Any]:
        return self.nodes[0].get("attrs", {}) if self.nodes else {}


@dataclass
class InspectorSection:
    """A collapsible section of the Attribute Editor.

    ``build(parent, inspector)`` returns a widget (usually a
    ``widgets.InspectorSection``) with ``refresh(info)``; it may also have
    ``on_item_changed(item)`` (live update during canvas drags).

    A type-specific section replaces the generated "Attributes" section of
    its type. ``generic`` sections (they apply to any type, e.g. Effects)
    don't: types without a section of their own keep the generated one.
    """

    key: str
    title: str
    order: float
    applies: Callable[[SelectionInfo], bool]
    build: Callable[[Any, Any], Any]
    generic: bool = False


@dataclass
class InspectorRows:
    """Extra rows inside another plugin's section (e.g. crop buttons in "Image")."""

    section: str
    order: float
    build: Callable[[Any, Any], Any]  # -> widget with refresh(info)


@dataclass
class QuickSection:
    """Controls in the canvas quick editor bar, in front of the attribute fields.

    ``applies(names)`` says whether the section is for the selected nodes;
    ``build(parent)`` returns a widget with ``refresh(names)``, called whenever
    the bar is updated. The widget keeps itself current between those calls
    (e.g. playback's frame number) and should keep a fixed size, so the bar
    doesn't jump. ``title`` names it in the bar's menu, where the user can hide it.
    """

    key: str
    title: str
    order: float
    applies: Callable[[list[str]], bool]
    build: Callable[[Any], Any]


@dataclass
class EmptyPanel:
    """Shown in the Attribute Editor when nothing is selected (e.g. canvas background)."""

    key: str
    order: float
    build: Callable[[Any, Any], Any]  # -> widget with refresh()


@dataclass
class IngestHandler:
    """Turns dropped/pasted data into nodes.

    ``ingest(view, scene_pos, mime)`` returns ``(images, notes)`` created, or
    ``"handled"`` when it took care of the data some other way (opening a
    file, starting a download), or None when it can't handle it.
    """

    name: str
    order: float
    accepts: Callable[[Any], bool]
    describe: Callable[[Any], str | None]
    ingest: Callable[[Any, Any, Any], Any]


@dataclass
class PaletteButton:
    key: str  # action key
    label: str
    icon: str
    order: float = 50.0


@dataclass
class StrokeStyle:
    """Which stroke controls apply to a node type in the Stroke section."""

    node_type: str
    color_attr: str = "color"
    fill: bool = False
    radius: bool = False
    arrows: bool = False
    smoothing: bool = False
    children_type: str | None = None  # style the children instead (a drawing's strokes)


@dataclass
class UngroupHandler:
    """Lets "Ungroup" (Ctrl+Shift+G) dissolve a node type that isn't a group."""

    node_type: str
    label: str  # menu text when only these are selected, e.g. "Separate Strokes"
    done_message: Callable[[int], str] | None = None


COST_CHEAP = "cheap"  # fast enough to run while drawing, and on every frame of an animation
COST_HEAVY = "heavy"  # runs in the background; results are cached on disk and in the scene


@dataclass
class EffectType:
    """One kind of effect. Parameters are ``Attr`` specs (validation, defaults, UI)."""

    name: str
    label: str = ""
    params: tuple[Attr, ...] = ()
    category: str = "Adjust"
    cost: str = COST_CHEAP
    version: int = 1  # bump when the algorithm changes: cached results are recomputed
    doc: str = ""
    plugin: str | None = None

    @property
    def nice_name(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize()

    def param(self, name: str) -> Attr | None:
        return next((p for p in self.params if p.name == name), None)

    def defaults(self) -> dict[str, Any]:
        return {p.name: p.default_value() for p in self.params}

    def resolved(self, params: dict[str, Any]) -> dict[str, Any]:
        """Defaults overlaid with the stored parameters."""
        return {**self.defaults(), **{k: v for k, v in params.items() if self.param(k) is not None}}

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.nice_name, "category": self.category, "cost": self.cost,
                "version": self.version, "doc": self.doc,
                "params": [{"name": p.name, **p.to_dict()} for p in self.params]}


@dataclass
class ColorPickerProvider:
    """A colour picker the app can use for every colour swatch (extension point ``color.pickers``).

    ``create(parent, initial, title, alpha)`` returns a dialog-like object: a ``QWidget`` with
    ``currentColorChanged(QColor)`` (emitted as the user changes the colour: the canvas follows it
    live), ``currentColor()`` and ``exec()`` (blocks; truthy when the user accepted, falsy on cancel).
    ``QColorDialog`` already fits. A provider that raises from ``create`` is skipped.
    """

    id: str
    title: str
    create: Callable[..., Any]
    order: float = 50.0
    description: str = ""
