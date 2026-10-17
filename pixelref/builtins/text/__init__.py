"""Text notes: the ``annotation_text`` node type and its commands.

Mixed formatting is stored as readable ``runs`` next to the plain ``text``:
``[{"text": "hello "}, {"text": "world", "bold": true}]``. Styles shared by
all visible text are promoted to note attributes instead.
"""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CAP_COLORABLE, CAP_CONTENT, Attr, CommandError, NodeType, SceneError, differs, fast_copy

TEXT_STYLE_KEYS = ("bold", "italic", "underline", "strike", "font_size", "color", "highlight_color")
NO_HIGHLIGHT = [255, 235, 59, 0]
ALIGNMENTS = ("left", "center", "right", "justify")
# What "Use this style for new notes" keeps (the note's look, not its text).
DEFAULT_STYLE_KEYS = (
    "font_family", "font_size", "bold", "italic", "underline", "strike", "color", "highlight_color", "align",
    "background_color", "fill_style", "border_color", "border_width", "border_style", "corner_radius", "padding",
    "text_width",
)
FILL_STYLES = ("solid", "dense", "sparse", "diagonal", "cross", "horizontal", "vertical", "grid", "none")
LINE_STYLES = ("solid", "dash", "dot", "dashdot", "none")

TEXT_PRESETS: dict[str, dict[str, Any]] = {
    "Plain": {
        "font_size": 14, "bold": False, "italic": False, "underline": False, "strike": False,
        "color": [220, 220, 220], "highlight_color": list(NO_HIGHLIGHT), "background_color": [0, 0, 0, 0],
        "border_width": 0.0, "corner_radius": 0.0, "padding": 4.0, "runs": None,
    },
    "Title": {
        "font_size": 32, "bold": True, "color": [240, 242, 246], "background_color": [0, 0, 0, 0],
        "border_width": 0.0, "padding": 4.0,
    },
    "Label": {
        "font_size": 12, "bold": True, "color": [16, 18, 22], "background_color": [61, 142, 240, 255],
        "fill_style": "solid", "border_width": 0.0, "corner_radius": 4.0, "padding": 6.0,
    },
    "Sticky note": {
        "font_size": 14, "bold": False, "color": [48, 40, 16], "background_color": [255, 226, 110, 255],
        "fill_style": "solid", "border_color": [196, 160, 52, 255], "border_width": 1.0, "border_style": "solid",
        "corner_radius": 2.0, "padding": 12.0, "text_width": 220.0,
    },
    "Callout": {
        "font_size": 15, "bold": True, "color": [255, 96, 96], "background_color": [255, 96, 96, 36],
        "fill_style": "solid", "border_color": [255, 96, 96, 255], "border_width": 2.0, "border_style": "dash",
        "corner_radius": 10.0, "padding": 10.0,
    },
    "Highlighter": {
        "color": [20, 20, 20], "highlight_color": [255, 235, 59, 220], "background_color": [0, 0, 0, 0],
        "border_width": 0.0,
    },
}


def estimate_bounds(_session: Any, node: Any) -> list[float]:
    """Rough text box size without Qt (headless scripts); the canvas knows better."""
    attrs = node.attrs
    size = float(attrs.get("font_size", 14) or 14)
    padding = float(attrs.get("padding", 4.0) or 0.0)
    lines = str(attrs.get("text", "")).split("\n") or [""]
    width = float(attrs.get("text_width", 0.0) or 0.0) or max(len(line) for line in lines) * size * 0.6 + 2 * padding
    return [0.0, 0.0, max(width, 12.0), len(lines) * size * 1.45 + 2 * padding]


TEXT_TYPE = NodeType(
    name="annotation_text",
    inherits="canvas_node",
    label="Text note",
    color_attr="color",
    capabilities=frozenset({CAP_CONTENT, CAP_COLORABLE}),
    compute={"local_bounds": estimate_bounds},
    attrs=(
        Attr("text", "text", "", group="Text"),
        Attr("color", "color", [220, 220, 220], group="Text"),
        Attr("runs", "any", None, hidden=True, group="Text",
             doc="[{text, <style overrides>}] for mixed formatting, or None"),
        Attr("font_family", "string", "", group="Text"),
        Attr("font_size", "float", 14.0, minimum=1.0, soft_min=6.0, soft_max=120.0, step=1.0, precision=0, unit="pt",
             group="Text"),
        Attr("bold", "bool", False, group="Text"),
        Attr("italic", "bool", False, group="Text"),
        Attr("underline", "bool", False, group="Text"),
        Attr("strike", "bool", False, group="Text"),
        Attr("highlight_color", "color4", list(NO_HIGHLIGHT), group="Text", doc="Marker behind the glyphs; alpha 0 = none"),
        Attr("align", "enum", "left", choices=ALIGNMENTS, group="Text"),
        Attr("background_color", "color4", [0, 0, 0, 0], group="Box"),
        Attr("fill_style", "enum", "solid", choices=FILL_STYLES, group="Box"),
        Attr("border_color", "color4", [220, 220, 220, 255], group="Box"),
        Attr("border_width", "float", 0.0, minimum=0.0, group="Box"),
        Attr("border_style", "enum", "solid", choices=LINE_STYLES, group="Box"),
        Attr("corner_radius", "float", 0.0, minimum=0.0, group="Box"),
        Attr("padding", "float", 4.0, minimum=0.0, group="Box"),
        Attr("text_width", "float", 0.0, minimum=0.0, group="Box", doc="0 = grow with the text, else wrap at this width"),
    ),
    quick_attrs=("color", "font_size", "bold", "italic", "align", "background_color"),
)
TEXT_TYPE_DEFAULTS = {attr.name: attr.default for attr in TEXT_TYPE.attrs}


def strip_runs(runs: list[dict[str, Any]] | None, keys: tuple[str, ...]) -> list[dict[str, Any]] | None:
    """Remove style ``keys`` from formatting runs, merging runs that become equal."""
    if not runs:
        return None
    merged: list[dict[str, Any]] = []
    for run in runs:
        cleaned = {k: v for k, v in run.items() if k not in keys}
        if merged and {k: v for k, v in merged[-1].items() if k != "text"} == {
            k: v for k, v in cleaned.items() if k != "text"
        }:
            merged[-1]["text"] += cleaned.get("text", "")
        else:
            merged.append(dict(cleaned))
    if all(len(run) == 1 for run in merged):
        return None
    return merged


def normalize_runs(runs: list[dict[str, Any]] | None) -> tuple[dict[str, Any], list[dict[str, Any]] | None]:
    """Promote styles shared by all visible text to note attributes."""
    if not runs:
        return {}, None
    runs = [dict(run) for run in runs if run.get("text")]
    visible = [run for run in runs if run["text"].strip()]
    promoted: dict[str, Any] = {}
    if visible:
        missing = object()
        for key in TEXT_STYLE_KEYS:
            values = [run.get(key, missing) for run in visible]
            if values[0] is not missing and all(v == values[0] for v in values):
                promoted[key] = values[0]
    return promoted, strip_runs(runs, tuple(promoted))


def activate(ctx: Any) -> None:
    ctx.register_node_type(TEXT_TYPE)
    session = ctx.session
    scene = session.scene

    specs = {attr.name: attr for attr in TEXT_TYPE.attrs}

    def default_style() -> dict[str, Any]:
        """Your style for new notes (saved values that no longer fit are skipped)."""
        stored = ctx.settings.json_value("default_style", {})
        style: dict[str, Any] = {}
        for key, value in (stored.items() if isinstance(stored, dict) else ()):
            if key in DEFAULT_STYLE_KEYS:
                try:
                    style[key] = specs[key].validate(value)
                except SceneError:
                    continue
        return style

    class Notes:
        def create(self, text: str, x: float, y: float, parent: str | None = None) -> str:
            name = scene.unique_name("Note")
            attrs = {"color": [220, 220, 220], **default_style(), "text": text,
                     "translate": [round(x, 3), round(y, 3)]}
            scene.create_node("annotation_text", name, parent=parent, attrs=attrs)
            return name

    notes = Notes()
    ctx.provide_service("notes", notes)

    def text_names(nodes: Any) -> list[str]:
        names = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        return [n for n in names if scene.get_node(n).node_type == "annotation_text"]

    def create_note(text: str = "Text", translate: Any = None, parent: str | None = None) -> str:
        """Create a text note at ``translate`` (world units if no parent)."""
        x, y = (float(translate[0]), float(translate[1])) if translate is not None else (0.0, 0.0)
        name = notes.create(text, x, y)
        if parent:
            ctx.run("parent", name, parent)
        session.selection.set([name])
        return name

    def set_text_attrs(updates: dict[str, Any], nodes: Any = None, strip: tuple[str, ...] | list[str] = (),
                       label: str = "Edit text") -> list[str]:
        """Set note attributes on the selected notes; ``strip`` removes those styles
        from per-character runs so a whole-note change isn't masked by older overrides."""
        names = text_names(nodes)
        if not names:
            raise CommandError("Select a text note")
        changed = []
        for name in names:
            attrs = scene.get_node(name).attrs
            change = {k: v for k, v in updates.items() if differs(attrs.get(k), v)}
            runs = attrs.get("runs")
            if runs and strip:
                stripped = strip_runs(runs, tuple(strip))
                if stripped != runs:
                    change["runs"] = stripped
            if change:
                scene.set_attrs(name, change)
                changed.append(name)
        return changed

    def commit_text(name: str, text: str, runs: list[dict[str, Any]] | None = None) -> None:
        """Store text (and per-character formatting runs) edited on the canvas."""
        name = scene.resolve(name)
        attrs = scene.get_node(name).attrs
        promoted, runs = normalize_runs(runs)
        wanted = {"text": text, "runs": runs, **promoted}
        updates = {k: v for k, v in wanted.items() if differs(attrs.get(k), v)}
        if updates:
            scene.set_attrs(name, updates)

    def apply_preset(preset: str, nodes: Any = None) -> list[str]:
        """Apply a style preset (Plain, Title, Label, Sticky note, Callout, Highlighter)."""
        if preset not in TEXT_PRESETS:
            raise CommandError(f"Unknown preset '{preset}' ({', '.join(TEXT_PRESETS)})")
        values = dict(TEXT_PRESETS[preset])
        strip = tuple(key for key in TEXT_STYLE_KEYS if key in values)
        return set_text_attrs(values, nodes, strip)

    def clear_formatting(nodes: Any = None) -> list[str]:
        """Remove character formatting from text notes (keeps the text)."""
        return set_text_attrs({"runs": None, "bold": False, "italic": False, "underline": False, "strike": False,
                               "highlight_color": list(NO_HIGHLIGHT)}, nodes)

    def save_default_style(node: str | None = None) -> dict[str, Any]:
        """Use a note's style (font, colours, box…) for every new note; kept across restarts."""
        names = text_names(node)
        if not names:
            raise CommandError("Select a text note")
        attrs = scene.get_node(names[0]).attrs
        defaults = TEXT_TYPE_DEFAULTS
        style = {k: fast_copy(attrs[k]) for k in DEFAULT_STYLE_KEYS if k in attrs and attrs[k] != defaults.get(k)}
        if style:
            ctx.settings.set_json("default_style", style)
        else:
            ctx.settings.remove("default_style")
        return style

    def clear_default_style() -> None:
        """New notes go back to the plain style."""
        ctx.settings.remove("default_style")

    ctx.command("create", create_note, alias="create_note", label="Add text")
    ctx.command("save_default_style", save_default_style, undoable=False)
    ctx.command("clear_default_style", clear_default_style, undoable=False)
    ctx.command("default_style", default_style, undoable=False, echo=False)
    ctx.command("set_attrs", set_text_attrs, label=lambda updates, nodes=None, strip=(), label="Edit text": label)
    ctx.command("commit", commit_text, label="Edit text")
    ctx.command("apply_preset", apply_preset, label=lambda preset, nodes=None: f"Style preset: {preset}")
    ctx.command("clear_formatting", clear_formatting, label="Clear formatting")


def activate_ui(ui: Any) -> None:
    from .ui import activate_text_ui

    activate_text_ui(ui)
