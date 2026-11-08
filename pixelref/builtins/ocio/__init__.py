"""Colour management: show EXR images through an OpenColorIO display/view.

Without it (or in the *built-in* mode) EXRs are shown with pixelRef's plain
linear-to-sRGB curve. In the *OCIO* mode the EXR plugin hands the linear pixels
to the :class:`~.config.Display` this plugin provides (service ``"color"``):

    input colour space  →  display / view (+ look)

Where the settings come from, first found wins:

1. the board itself, when it was saved with its colour settings
   ("Save with this board": scene plugin data ``"ocio"``);
2. your preferences (``plugins/ocio/*``);
and, for the config of either when left empty, ``$OCIO`` (a studio's config),
else OCIO's built-in ACES config (``ocio://default``).

The mode starts as OCIO when ``$OCIO`` is set, else built-in, so boards keep
their look until you turn OCIO on. Each image can pick its own input colour
space (the EXR plugin's ``colorspace`` attribute); the board has a default
(``input``); left empty, the config's file rules decide (else its scene_linear role).

Needs the ``opencolorio`` and ``numpy`` Python packages (``plugin.toml``):
Image → Install OCIO Support… installs them.
"""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import CommandError

from . import config as cfg

KEY = "ocio"  # scene plugin data
MODES = ("builtin", "ocio")
FIELDS = ("mode", "config", "display", "view", "look", "input")
CHANGED = "ocio.changed"  # session event: the display settings may have changed


def default_settings() -> dict[str, str]:
    return {"mode": "ocio" if cfg.env_config() else "builtin", "config": "", "display": "", "view": "",
            "look": "", "input": ""}


class ColorApi:
    """The board's colour settings and the display to show EXRs through (service ``"color"``)."""

    def __init__(self, session: Any, settings: Any) -> None:
        self.session = session
        self.prefs = settings  # ctx.settings (plugins/ocio/…)
        self._prefs: dict[str, str] | None = None  # cached: read on every image lookup
        self._memo: tuple[tuple, Any, str] | None = None  # (settings, Display | None, error)
        self._available: bool | None = None
        self.error = ""  # why the OCIO display couldn't be made ("" when fine)

    # ---- Settings ------------------------------------------------------------------------

    def preferences(self) -> dict[str, str]:
        if self._prefs is None:
            prefs = default_settings()
            for name in FIELDS:
                stored = self.prefs.value(name, None)
                if stored is not None:
                    prefs[name] = str(stored)
            if prefs["mode"] not in MODES:
                prefs["mode"] = "builtin"
            self._prefs = prefs
        return dict(self._prefs)

    def scene_settings(self) -> dict[str, str] | None:
        """The board's own colour settings, or None when it uses your preferences."""
        data = self.session.scene.plugin_data.get(KEY)
        if not isinstance(data, dict):
            return None
        settings = default_settings()
        settings.update({k: str(v) for k, v in data.items() if k in FIELDS and v is not None})
        if settings["mode"] not in MODES:
            settings["mode"] = "builtin"
        return settings

    def saved_with_scene(self) -> bool:
        return self.scene_settings() is not None

    def settings(self) -> dict[str, str]:
        """The settings in effect: the board's when it has its own, else your preferences."""
        return self.scene_settings() or self.preferences()

    @staticmethod
    def config_spec(settings: dict[str, str]) -> str:
        """What the ``config`` setting points at: itself, else ``$OCIO``, else the built-in config."""
        return settings.get("config") or cfg.env_config() or cfg.BUILTIN

    # ---- The display -----------------------------------------------------------------------

    def available(self, recheck: bool = False) -> bool:
        """Whether OpenColorIO can be used. Looked up once (the lookup clears Python's import
        caches, too slow for every refresh); ``recheck`` after installing the packages."""
        if recheck or self._available is None:
            self._available = cfg.available()
        return self._available

    def display(self) -> cfg.Display | None:
        """The OCIO display EXRs are shown through, or None (built-in sRGB view)."""
        settings = self.settings()
        memo_key = tuple(settings[k] for k in FIELDS) + (cfg.env_config(),)
        if self._memo is not None and self._memo[0] == memo_key:
            self.error = self._memo[2]
            return self._memo[1]
        display, error = None, ""
        if settings["mode"] == "ocio":
            if not self.available():
                error = "OpenColorIO isn't installed (Image → Install OCIO Support…)"
            else:
                display, error = self._make(settings)
        self._memo = (memo_key, display, error)
        self.error = error
        return display

    def _make(self, settings: dict[str, str]) -> tuple[cfg.Display | None, str]:
        spec = self.config_spec(settings)
        try:
            return cfg.make_display(spec, settings["display"], settings["view"], settings["look"],
                                    settings["input"]), ""
        except ValueError as exc:
            error = str(exc)
        # A board saved with a config this machine doesn't have: your config, then the built-in one.
        for fallback in (self.config_spec(self.preferences()), cfg.BUILTIN):
            if fallback == spec:
                continue
            try:
                return cfg.make_display(fallback, settings["display"], settings["view"], settings["look"],
                                        settings["input"]), f"{error} (showing {fallback} instead)"
            except ValueError:
                continue
        return None, error

    def key(self) -> tuple:
        """Identifies the current display for the image cache: a change decodes EXRs again."""
        display = self.display()
        return display.key if display is not None else ("builtin",)

    def config(self) -> Any:
        """The OCIO config in effect (even in the built-in mode, for menus), or None."""
        display = self.display()
        if display is not None:
            return display.config
        if not self.available():
            return None
        try:
            return cfg.load_config(self.config_spec(self.settings()))
        except ValueError:
            return None

    # ---- Changes ---------------------------------------------------------------------------

    def changed(self) -> None:
        self._memo = None
        self.session.events.emit(CHANGED, {})

    def write(self, changes: dict[str, str]) -> None:
        """Change the settings in effect: the board's when it has its own (undoable), else your
        preferences (also the starting point of boards saved with their settings later)."""
        scene = self.scene_settings()
        if scene is not None:
            scene.update(changes)
            self.session.scene.set_plugin_data(KEY, scene)
        else:
            for name, value in changes.items():
                self.prefs.setValue(name, value)
            self._prefs = None
        self.changed()

    def save_with_scene(self, on: bool) -> None:
        if on and not self.saved_with_scene():
            self.session.scene.set_plugin_data(KEY, self.preferences())
        elif not on and self.saved_with_scene():
            self.session.scene.set_plugin_data(KEY, None)
        self.changed()


def activate(ctx: Any) -> None:
    session = ctx.session
    api = ColorApi(session, ctx.settings)
    # Tracked first, so disposed last: once the service is gone, EXRs go back to plain sRGB.
    ctx.track(lambda: session.events.emit(CHANGED, {}))
    ctx.provide_service("color", api)

    def need_config() -> Any:
        config = api.config()
        if config is None:
            raise CommandError(api.error or "OpenColorIO isn't installed (Image → Install OCIO Support…)")
        return config

    def set_color(mode: str | None = None, config: str | None = None, display: str | None = None,
                  view: str | None = None, look: str | None = None, input: str | None = None  # noqa: A002
                  ) -> dict[str, str]:
        """Change how EXRs are shown: mode ("builtin" sRGB or "ocio"), config (a path, "ocio://…",
        or "" for $OCIO / the built-in ACES config), display, view, look ("" = none) and the board's
        default input colour space ("" = from the config's file rules)."""
        changes: dict[str, str] = {}
        if mode is not None:
            if mode not in MODES:
                raise CommandError(f"mode must be one of {', '.join(MODES)}")
            changes["mode"] = mode
        if config is not None:
            config = str(config).strip()
            if config:
                if not api.available():
                    raise CommandError("OpenColorIO isn't installed (Image → Install OCIO Support…)")
                try:
                    cfg.load_config(config)
                except ValueError as exc:
                    raise CommandError(str(exc)) from exc
            changes["config"] = config
            # Another config: its own display and view defaults unless given too.
            changes.setdefault("display", "")
            changes.setdefault("view", "")
            changes.setdefault("look", "")
            changes.setdefault("input", "")
        if any(v is not None for v in (display, view, look, input)):
            current = dict(api.settings(), **changes)
            ocio = need_config() if config is None else cfg.load_config(api.config_spec(current))
            if display is not None:
                if display and display not in cfg.displays(ocio):
                    raise CommandError(f"No display '{display}' (displays: {', '.join(cfg.displays(ocio))})")
                changes["display"] = display
                if view is None:
                    changes["view"] = ""  # that display's default view
            if view is not None:
                shown = changes.get("display", current["display"]) or ocio.getDefaultDisplay()
                if view and view not in cfg.views(ocio, shown):
                    raise CommandError(f"Display '{shown}' has no view '{view}' "
                                       f"(views: {', '.join(cfg.views(ocio, shown))})")
                changes["view"] = view
            if look is not None:
                if look and look not in cfg.looks(ocio):
                    raise CommandError(f"No look '{look}'")
                changes["look"] = look
            if input is not None:
                if input and not cfg.has_colorspace(ocio, input):
                    raise CommandError(f"No colour space '{input}'")
                changes["input"] = input
        if changes:
            api.write(changes)
        return api.settings()

    def save_with_board(on: bool = True) -> bool:
        """Save the colour settings with this board (on) or use your preferences (off)."""
        api.save_with_scene(bool(on))
        return api.saved_with_scene()

    def info() -> dict[str, Any]:
        """The colour settings in effect, where they come from, and the display shown."""
        settings = api.settings()
        display = api.display()
        return {
            **settings,
            "source": "board" if api.saved_with_scene() else "preferences",
            "config_used": display.config_spec if display is not None else "",
            "display_used": display.display if display is not None else "",
            "view_used": display.view if display is not None else "",
            "available": api.available(),
            "error": api.error,
        }

    def colorspaces() -> list[str]:
        """The colour spaces of the OCIO config in effect."""
        return [name for name, _family in cfg.colorspaces(need_config())]

    def displays() -> dict[str, list[str]]:
        """The displays of the OCIO config in effect and their views."""
        ocio = need_config()
        return {name: cfg.views(ocio, name) for name in cfg.displays(ocio)}

    def looks() -> list[str]:
        """The looks of the OCIO config in effect."""
        return cfg.looks(need_config())

    def reload() -> None:
        """Read the OCIO config files again (after editing them)."""
        cfg.forget_configs()
        api.changed()

    labels = {"mode": "Colour mode", "config": "OCIO config", "display": "Display", "view": "View",
              "look": "Look", "input": "Input colour space"}

    def set_label(*args: Any, **kwargs: Any) -> str:
        return labels.get(next(iter(kwargs)), "Colour settings") if len(kwargs) == 1 and not args \
            else "Colour settings"

    ctx.command("set", set_color, alias="color_set", label=set_label)
    ctx.command("save_with_board", save_with_board, label="Save colour settings with board")
    ctx.command("info", info, alias="color_info", undoable=False, echo=False)
    ctx.command("colorspaces", colorspaces, undoable=False, echo=False)
    ctx.command("displays", displays, undoable=False, echo=False)
    ctx.command("looks", looks, undoable=False, echo=False)
    ctx.command("reload", reload, undoable=False)

    # Opening a board, undo and redo can change its colour settings.
    ctx.on("document.replaced", lambda _e: api.changed())


def activate_ui(ui: Any) -> None:
    from .ui import activate_ocio_ui

    activate_ocio_ui(ui)
