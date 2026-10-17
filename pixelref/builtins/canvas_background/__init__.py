"""Canvas background: colour + pattern, stored as scene-level plugin data."""

from __future__ import annotations

import json
from typing import Any

from pixelref.plugin_api import CommandError

KEY = "canvas_background"
PATTERNS = (
    ("lines", "Grid lines"),
    ("dots", "Dots"),
    ("crosses", "Crosses"),
    ("checker", "Checkerboard"),
    ("none", "Plain"),
)
PRESETS = (
    ("Dark", (23, 25, 29)),
    ("Charcoal", (43, 45, 49)),
    ("Grey", (128, 128, 128)),
    ("Light", (232, 232, 232)),
    ("White", (255, 255, 255)),
    ("Black", (0, 0, 0)),
)
DEFAULTS: dict[str, Any] = {"background": [23, 25, 29], "pattern": "lines", "grid_size": 64, "pattern_color": None}


def activate(ctx: Any) -> None:
    session = ctx.session
    state: dict[str, Any] = {"last_pattern": "lines", "defaults": None}

    def defaults() -> dict[str, Any]:
        """What new scenes start with: the user's last choice."""
        # Cached: views ask for the background on every paint, and reading
        # QSettings + parsing JSON each time is wasted work.
        cached = state["defaults"]
        if cached is None:
            cached = dict(DEFAULTS)
            stored = ctx.settings.value("canvas_default", "")
            if stored:
                try:
                    cached.update({k: v for k, v in json.loads(stored).items() if k in DEFAULTS})
                except (ValueError, TypeError, AttributeError):
                    pass
            state["defaults"] = cached
        return dict(cached)

    def current() -> dict[str, Any]:
        """The background settings in effect (scene values over your defaults)."""
        settings = defaults()
        data = session.scene.get_plugin_data(KEY, {}) or {}
        settings.update({k: v for k, v in data.items() if k in DEFAULTS})
        return settings

    def set_background(**changes: Any) -> dict[str, Any]:
        """Change the background: background=[r,g,b], pattern=…, grid_size=…, pattern_color=[r,g,b] or None."""
        unknown = set(changes) - set(DEFAULTS)
        if unknown:
            raise CommandError(f"Unknown background settings: {', '.join(sorted(unknown))}")
        if "pattern" in changes and changes["pattern"] not in [p for p, _l in PATTERNS]:
            raise CommandError(f"pattern must be one of {', '.join(p for p, _l in PATTERNS)}")
        settings = current()
        if all(settings.get(k) == v for k, v in changes.items()) and session.scene.get_plugin_data(KEY):
            return settings
        if settings["pattern"] != "none" and changes.get("pattern") == "none":
            state["last_pattern"] = settings["pattern"]
        settings.update(changes)
        # Store the full set so the file shows exactly what the user sees.
        session.scene.set_plugin_data(KEY, settings)
        ctx.settings.setValue("canvas_default", json.dumps(settings))
        state["defaults"] = None
        return settings

    def toggle_pattern() -> str:
        """Switch between a plain background and the last pattern."""
        settings = current()
        if settings["pattern"] != "none":
            state["last_pattern"] = settings["pattern"]
            set_background(pattern="none")
            return "none"
        set_background(pattern=state.get("last_pattern") or "lines")
        return current()["pattern"]

    def label(**changes: Any) -> str:
        if set(changes) == {"pattern"}:
            return "Background pattern"
        if set(changes) == {"pattern_color"}:
            return "Pattern color" if changes["pattern_color"] else "Auto pattern color"
        if set(changes) == {"grid_size"}:
            return "Grid size"
        if set(changes) == {"background"}:
            return "Background"
        return "Background"

    ctx.command("set", set_background, alias="set_background", label=label)
    ctx.command("toggle_pattern", toggle_pattern, alias="toggle_pattern", label="Background pattern")
    ctx.command("current", current, alias="background_settings", undoable=False, echo=False)

    def on_new(sess: Any) -> None:
        sess.scene.set_plugin_data(KEY, defaults())

    ctx.contribute("scene.after_new", on_new)
    if not session.scene.nodes and not session.scene.get_plugin_data(KEY):
        # A fresh scene records the background, so saved files show what you see.
        session.scene.set_plugin_data(KEY, defaults())
    ctx.current = current  # type: ignore[attr-defined]
    ctx.defaults = defaults  # type: ignore[attr-defined]


def activate_ui(ui: Any) -> None:
    from .ui import activate_background_ui

    activate_background_ui(ui)
