"""Effects: a stack of effects (grayscale, blur, edge detect…) on any element.

Every canvas node gets an ``effects`` attribute (an extension of
``canvas_node``, so plugin types have it too). It's a list, applied in
order::

    [{"id": "fx_3k9a1c2e", "type": "edge_detect", "enabled": 1, "mix": 1.0,
      "params": {"threshold": 0.15}}]

* ``mix`` blends an effect's result over its input; ``enabled`` switches it off
  without losing its settings; ``params`` holds only what differs from the
  effect type's defaults.
* Your defaults: "Save as default" on an effect keeps its settings (in the
  user settings, across restarts) for every new effect of that type. They're
  written into the new effect's ``params`` when it's added, never looked up
  when drawing, so a scene looks the same on every machine.
* ``effects_muted`` (also on every canvas node) mutes the whole stack of a
  node: kept, saved, but not drawn. The app's "Show Effects" switch mutes
  every node at once, for a quick before/after (not saved, not undoable).
* Entries of effect types no active plugin provides stay in the stack (and
  in files), skipped when drawing.

Effect *types* are contributed to ``effects.types`` (an :class:`EffectType`:
name, parameters as ``Attr`` specs, cost); the pixel work is contributed to
``effects.processors`` by the Qt half, so other plugins add effects the same
way (an ``effects_numpy`` plugin can bring heavier filters). This headless
half validates stacks and provides the commands; the Qt half (``ui.py``)
draws, caches and processes in the background.

Images also get ``effects_all_frames``: process every frame of an animation
with the whole stack (otherwise only cheap effects run while it plays).
"""

from __future__ import annotations

import uuid
from typing import Any

from pixelref.plugin_api import Attr, CommandError, SceneError, fast_copy
from pixelref.plugin_api.contracts import COST_CHEAP, COST_HEAVY, EffectType

__all__ = ["BUILTIN_TYPES", "COST_CHEAP", "COST_HEAVY", "EffectType", "EffectsApi"]

BUILTIN_TYPES: tuple[EffectType, ...] = (
    EffectType("grayscale", "Grayscale", (
        Attr("amount", "float", 1.0, minimum=0.0, maximum=1.0, step=0.05, precision=0, unit="%",
             display_scale=100.0, doc="100 % = no colour left"),
    ), category="Color", doc="Remove colour: judge values and shapes"),
    EffectType("invert", "Invert", (), category="Color", doc="Negative of the colours"),
    EffectType("brightness_contrast", "Brightness / Contrast", (
        Attr("brightness", "float", 0.0, minimum=-1.0, maximum=1.0, step=0.05, precision=2),
        Attr("contrast", "float", 0.0, minimum=-1.0, maximum=1.0, step=0.05, precision=2),
    ), category="Adjust"),
    EffectType("threshold", "Threshold", (
        Attr("level", "float", 0.5, minimum=0.0, maximum=1.0, step=0.05, precision=2,
             doc="Lighter becomes white, darker black"),
    ), category="Adjust", doc="Black and white: see the big shapes"),
    EffectType("posterize", "Posterize", (
        Attr("levels", "int", 4, minimum=2, maximum=32, doc="Steps per colour channel"),
    ), category="Adjust", doc="Fewer tones: group values into flat areas"),
    EffectType("blur", "Blur", (
        Attr("radius", "float", 4.0, minimum=0.0, maximum=500.0, soft_min=0.0, soft_max=60.0, step=1.0,
             precision=1, unit="px", doc="In canvas pixels (zoom independent)"),
    ), category="Blur", cost=COST_HEAVY, doc="Soften detail: squint at the big masses"),
    EffectType("edge_detect", "Edge Detect", (
        Attr("strength", "float", 2.0, minimum=0.5, maximum=10.0, step=0.5, precision=1),
        Attr("threshold", "float", 0.1, minimum=0.0, maximum=1.0, step=0.05, precision=2,
             doc="Ignore edges weaker than this"),
        Attr("style", "enum", "lines", choices=("lines", "glow", "overlay"),
             doc="lines: dark lines on white · glow: light edges on black · overlay: lines over the picture"),
        Attr("color", "color", [0, 0, 0], doc="Line colour (lines and overlay)"),
    ), category="Stylize", cost=COST_HEAVY, doc="Outlines of the shapes: study the drawing"),
)


def new_id() -> str:
    return "fx_" + uuid.uuid4().hex[:8]


class EffectsApi:
    """Effect types, stacks and the effects clipboard (the ``effects`` service)."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.session = ctx.session
        self.settings = ctx.settings
        self.clipboard: list[dict[str, Any]] = []

    # ---- Your defaults ---------------------------------------------------------------------------

    def user_defaults(self, name: str) -> dict[str, Any]:
        """Your saved defaults for an effect type: {"params": {...}, "mix": m} (nothing saved: {}, 1.0).
        Saved values that no longer fit the type (renamed parameters, junk) are skipped."""
        effect_type = self.type(name)
        stored = self.settings.json_value(f"defaults/{name}", {}) if effect_type is not None else {}
        stored = stored if isinstance(stored, dict) else {}
        params: dict[str, Any] = {}
        raw = stored.get("params")
        for key, value in (raw.items() if isinstance(raw, dict) else ()):
            spec = effect_type.param(key) if effect_type is not None else None
            if spec is None:
                continue
            try:
                params[key] = spec.validate(value)
            except SceneError:
                continue
        try:
            mix = min(1.0, max(0.0, float(stored.get("mix", 1.0))))
        except (TypeError, ValueError):
            mix = 1.0
        return {"params": params, "mix": mix}

    def save_user_defaults(self, name: str, params: dict[str, Any], mix: float = 1.0) -> dict[str, Any]:
        """Keep ``params``/``mix`` as your defaults for new ``name`` effects (only what differs
        from the factory defaults is kept); returns what was saved."""
        effect_type = self.type(name)
        if effect_type is None:
            raise CommandError(f"Unknown effect '{name}'")
        factory = effect_type.defaults()
        kept = {k: fast_copy(v) for k, v in effect_type.resolved(params).items() if v != factory.get(k)}
        data: dict[str, Any] = {"params": kept}
        if mix != 1.0:
            data["mix"] = mix
        if kept or mix != 1.0:
            self.settings.set_json(f"defaults/{name}", data)
        else:
            self.settings.remove(f"defaults/{name}")
        return {"params": kept, "mix": mix}

    def clear_user_defaults(self, name: str) -> None:
        self.settings.remove(f"defaults/{name}")

    def new_entry(self, name: str, mix: float | None = None, enabled: bool = True,
                  params: dict[str, Any] | None = None) -> dict[str, Any]:
        """A new stack entry: your defaults for the type, then ``params``."""
        defaults = self.user_defaults(name)
        return {"id": new_id(), "type": name, "enabled": 1 if enabled else 0,
                "mix": defaults["mix"] if mix is None else mix, "params": {**defaults["params"], **(params or {})}}

    # ---- Types ------------------------------------------------------------------------------

    def types(self) -> dict[str, EffectType]:
        found: dict[str, EffectType] = {}
        for effect_type in self.session.extensions.contributions("effects.types"):
            found.setdefault(effect_type.name, effect_type)  # the first contribution of a name wins
        return found

    def type(self, name: str) -> EffectType | None:
        return self.types().get(name)

    # ---- Stacks -----------------------------------------------------------------------------

    def normalize(self, value: Any) -> list[dict[str, Any]]:
        """The canonical stack (the ``effects`` attribute's validator)."""
        if value is None:
            return []
        if not isinstance(value, list):
            raise SceneError("effects must be a list of effects")
        types = self.types()
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in value:
            if isinstance(raw, str):
                raw = {"type": raw}
            if not isinstance(raw, dict) or not isinstance(raw.get("type"), str) or not raw["type"]:
                raise SceneError(f"Each effect needs a 'type', got {raw!r}")
            effect_id = str(raw.get("id") or "") or new_id()
            while effect_id in seen:
                effect_id = new_id()
            seen.add(effect_id)
            params = raw.get("params") or {}
            if not isinstance(params, dict):
                raise SceneError(f"Effect '{raw['type']}': params must be a dict")
            effect_type = types.get(raw["type"])
            clean: dict[str, Any] = {}
            for key, param_value in params.items():
                spec = effect_type.param(key) if effect_type is not None else None
                if spec is not None:
                    clean[key] = spec.validate(param_value)
                elif effect_type is None:
                    clean[key] = fast_copy(param_value)  # unknown type: keep its data as it is
                else:
                    raise SceneError(f"Effect '{raw['type']}' has no parameter '{key}'")
            try:
                mix = min(1.0, max(0.0, float(raw.get("mix", 1.0))))
            except (TypeError, ValueError):
                raise SceneError(f"Effect '{raw['type']}': mix must be a number") from None
            out.append({"id": effect_id, "type": raw["type"], "enabled": 1 if raw.get("enabled", 1) else 0,
                        "mix": mix, "params": clean})
        return out

    def stack(self, name: str) -> list[dict[str, Any]]:
        node = self.session.scene.get_node(name)
        return fast_copy(node.attrs.get("effects") or [])

    def index_of(self, stack: list[dict[str, Any]], effect: Any) -> int:
        """An effect by id, by type name (the first one) or by index."""
        if isinstance(effect, bool):
            raise CommandError(f"No effect {effect!r}")
        if isinstance(effect, int):
            if -len(stack) <= effect < len(stack):
                return effect % len(stack)
            raise CommandError(f"No effect at index {effect} (the stack has {len(stack)})")
        for index, entry in enumerate(stack):
            if entry["id"] == effect:
                return index
        for index, entry in enumerate(stack):
            if entry["type"] == effect:
                return index
        raise CommandError(f"No effect '{effect}' in the stack")

    def canvas_names(self, nodes: Any) -> list[str]:
        scene = self.session.scene
        names = list(self.session.selection.names) if nodes is None else [
            scene.resolve(str(n)) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        types = self.session.types
        result = [n for n in names if types.is_canvas(scene.get_node(n).node_type)]
        if not result:
            raise CommandError("Select something on the canvas to give it effects")
        return result


def activate(ctx: Any) -> None:
    session = ctx.session
    api = EffectsApi(ctx)

    def check_type(value: Any) -> None:
        if not isinstance(value, EffectType):
            raise TypeError("effects.types takes EffectType objects")

    ctx.declare_extension_point("effects.types", "EffectType: kinds of effect (parameters, cost)", check_type)
    ctx.declare_extension_point(
        "effects.processors",
        "(effect name, fn(QImage, params, fx) -> QImage): the pixel work, contributed by a Qt half")
    for effect_type in BUILTIN_TYPES:
        effect_type.plugin = ctx.plugin_id
        ctx.contribute("effects.types", effect_type)
    ctx.extend_node_type("canvas_node", (
        Attr("effects", "any", [], hidden=True, group="Effects", validator=api.normalize,
             doc="The effect stack, applied in order"),
        Attr("effects_muted", "flag", 0, hidden=True, group="Effects",
             doc="Don't draw the effect stack (it's kept as it is)"),
    ))
    ctx.extend_node_type("image", (
        Attr("effects_all_frames", "flag", 0, hidden=True, group="Effects",
             doc="Process every frame of an animation with the whole stack (cached)"),
    ))
    ctx.provide_service("effects", api)
    scene = session.scene

    def write(name: str, stack: list[dict[str, Any]]) -> None:
        scene.set_attr(name, "effects", stack)

    # ---- Commands ---------------------------------------------------------------------------

    def types_cmd() -> list[dict[str, Any]]:
        """The effect types: name, label, category, cost ("cheap"/"heavy") and parameters."""
        return [t.describe() for t in api.types().values()]

    def list_cmd(node: str | None = None) -> list[dict[str, Any]]:
        """A node's effect stack (the selected node by default)."""
        return api.stack(api.canvas_names(node)[0])

    def add(effect: str, nodes: Any = None, index: int | None = None, mix: float | None = None,
            enabled: bool = True, **params: Any) -> list[str]:
        """Add an effect (``"blur"``, radius=8) to the selected nodes; returns the new effect ids.
        It starts with your saved defaults for that effect (``save_default``), then ``params``."""
        if api.type(effect) is None:
            raise CommandError(f"Unknown effect '{effect}' (available: {', '.join(api.types())})")
        ids = []
        for name in api.canvas_names(nodes):
            stack = api.stack(name)
            entry = api.new_entry(effect, mix, enabled, params)
            stack.insert(len(stack) if index is None else max(0, min(int(index), len(stack))), entry)
            write(name, stack)
            ids.append(entry["id"])
        return ids

    def remove(effect: Any, nodes: Any = None) -> int:
        """Remove an effect (by id, type or index) from the selected nodes; returns how many were removed."""
        removed = 0
        for name in api.canvas_names(nodes):
            stack = api.stack(name)
            try:
                index = api.index_of(stack, effect)
            except CommandError:
                if nodes is not None and not isinstance(nodes, (list, tuple)):
                    raise
                continue  # several nodes: those without it are fine
            del stack[index]
            write(name, stack)
            removed += 1
        if not removed:
            raise CommandError(f"No effect '{effect}' to remove")
        return removed

    def set_cmd(node: str, effect: Any, **values: Any) -> None:
        """Change an effect: its parameters, ``mix`` or ``enabled`` (``set("Img", "blur", radius=12)``)."""
        name = api.canvas_names(node)[0]
        stack = api.stack(name)
        entry = stack[api.index_of(stack, effect)]
        for key, value in values.items():
            if key in ("mix", "enabled"):
                entry[key] = value
            else:
                entry["params"][key] = value
        write(name, stack)

    def reset(node: str, effect: Any, factory: bool = False) -> None:
        """Put an effect's settings back to your defaults (``factory=True``: to the factory defaults)."""
        name = api.canvas_names(node)[0]
        stack = api.stack(name)
        entry = stack[api.index_of(stack, effect)]
        defaults = {"params": {}, "mix": 1.0} if factory else api.user_defaults(entry["type"])
        entry["params"], entry["mix"] = defaults["params"], defaults["mix"]
        write(name, stack)

    def save_default(node: str, effect: Any) -> dict[str, Any]:
        """Keep an effect's settings as your default for new effects of its type (across restarts)."""
        name = api.canvas_names(node)[0]
        stack = api.stack(name)
        entry = stack[api.index_of(stack, effect)]
        return api.save_user_defaults(entry["type"], entry["params"], entry["mix"])

    def clear_default(effect: str) -> None:
        """Forget your defaults for an effect type (``"blur"``): new ones use the factory defaults."""
        if api.type(effect) is None:
            raise CommandError(f"Unknown effect '{effect}'")
        api.clear_user_defaults(effect)

    def defaults_cmd(effect: str) -> dict[str, Any]:
        """An effect type's defaults: {"factory": {...}, "user": {"params": {...}, "mix": m}}."""
        effect_type = api.type(effect)
        if effect_type is None:
            raise CommandError(f"Unknown effect '{effect}'")
        return {"factory": effect_type.defaults(), "user": api.user_defaults(effect)}

    def move(node: str, effect: Any, index: int) -> None:
        """Move an effect to another place in the stack (0 = applied first)."""
        name = api.canvas_names(node)[0]
        stack = api.stack(name)
        entry = stack.pop(api.index_of(stack, effect))
        stack.insert(max(0, min(int(index), len(stack))), entry)
        write(name, stack)

    def toggle(effect: Any, nodes: Any = None) -> int:
        """Switch an effect on or off without losing its settings; returns the new state."""
        state = None
        for name in api.canvas_names(nodes):
            stack = api.stack(name)
            try:
                entry = stack[api.index_of(stack, effect)]
            except CommandError:
                continue
            if state is None:
                state = 0 if entry["enabled"] else 1
            entry["enabled"] = state
            write(name, stack)
        if state is None:
            raise CommandError(f"No effect '{effect}' to switch")
        return state

    def toggle_type(effect: str, nodes: Any = None) -> int:
        """Add an effect type to the selection, or remove it when every node has it already."""
        if api.type(effect) is None:
            raise CommandError(f"Unknown effect '{effect}'")
        names = api.canvas_names(nodes)
        have = all(any(e["type"] == effect for e in api.stack(n)) for n in names)
        for name in names:
            stack = api.stack(name)
            if have:
                stack = [e for e in stack if e["type"] != effect]
            elif not any(e["type"] == effect and e["enabled"] for e in stack):
                found = next((e for e in stack if e["type"] == effect), None)
                if found is not None:
                    found["enabled"] = 1
                else:
                    stack.append(api.new_entry(effect))
            write(name, stack)
        return 0 if have else 1

    def clear(nodes: Any = None) -> None:
        """Remove every effect from the selected nodes."""
        for name in api.canvas_names(nodes):
            if api.stack(name):
                write(name, [])

    def copy(node: str | None = None) -> int:
        """Remember a node's effect stack for ``paste``; returns how many effects."""
        api.clipboard = api.stack(api.canvas_names(node)[0])
        return len(api.clipboard)

    def paste(nodes: Any = None, append: bool = False) -> None:
        """Give the selected nodes the copied effect stack (``append`` adds it after their own)."""
        if not api.clipboard:
            raise CommandError("Copy a node's effects first")
        for name in api.canvas_names(nodes):
            copied = [{**fast_copy(e), "id": new_id()} for e in api.clipboard]
            write(name, (api.stack(name) if append else []) + copied)

    def mute(nodes: Any = None, on: bool | None = None) -> int:
        """Mute (or unmute) the selected nodes' effects: kept with their settings, not drawn.

        Without ``on`` it toggles: muted unless every node is muted already. Returns the new state."""
        names = api.canvas_names(nodes)
        state = (not all(scene.get_node(n).attrs.get("effects_muted", 0) for n in names)) if on is None else bool(on)
        for name in names:
            if bool(scene.get_node(name).attrs.get("effects_muted", 0)) != state:
                scene.set_attr(name, "effects_muted", 1 if state else 0)
        return 1 if state else 0

    def all_frames(nodes: Any = None, on: bool = True) -> None:
        """Process every frame of animated images with the whole stack (cached), not only cheap effects."""
        images = [n for n in api.canvas_names(nodes) if scene.get_node(n).node_type == "image"]
        if not images:
            raise CommandError("Select animated images")
        for name in images:
            scene.set_attr(name, "effects_all_frames", 1 if on else 0)

    for name, fn, options in (
        ("types", types_cmd, {"undoable": False, "echo": False}),
        ("list", list_cmd, {"undoable": False, "echo": False}),
        ("add", add, {"label": lambda effect, *a, **k: f"Add {effect.replace('_', ' ')}"}),
        ("remove", remove, {"label": "Remove effect"}),
        ("set", set_cmd, {"label": "Edit effect"}),
        ("reset", reset, {"label": "Reset effect"}),
        ("save_default", save_default, {"undoable": False}),
        ("clear_default", clear_default, {"undoable": False}),
        ("defaults", defaults_cmd, {"undoable": False, "echo": False}),
        ("move", move, {"label": "Reorder effects"}),
        ("toggle", toggle, {"label": "Switch effect"}),
        ("toggle_type", toggle_type, {"label": lambda effect, *a, **k: f"Toggle {effect.replace('_', ' ')}"}),
        ("clear", clear, {"label": "Remove effects"}),
        ("copy", copy, {"undoable": False}),
        ("paste", paste, {"label": "Paste effects"}),
        ("mute", mute, {"label": "Mute effects"}),
        ("all_frames", all_frames, {"label": "Process all frames"}),
    ):
        ctx.command(name, fn, **options)


def activate_ui(ui: Any) -> None:
    from .ui import activate_effects_ui

    activate_effects_ui(ui)
