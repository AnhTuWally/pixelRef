"""OpenColorIO configs and processors (no Qt; safe on any thread).

What the board shows of a linear image is decided by a :class:`Display`: an
OCIO config plus the display, view and (optional) look to show through. It is
an immutable snapshot: the image plugin decodes on worker threads with the
snapshot taken when the image was opened, so changing the settings never
touches a decode in flight (it makes new decodes instead; see ``key``).

Configs are loaded once per (spec, modification time) and processors once per
(config, input colour space, display, view, look). Both are safe to share
across threads (OCIO 2 guarantees it for ``Config.getProcessor`` and
``CPUProcessor.apply*``).
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

BUILTIN = "ocio://default"  # OCIO's built-in ACES CG config (OCIO 2.2+)
BUILTIN_CONFIGS = (
    ("ocio://default", "ACES CG (built-in)"),
    ("ocio://studio-config-latest", "ACES Studio (built-in)"),
)
PROCESSORS = 64  # processors kept (each is a few KB… to a few MB with LUTs)


def available() -> bool:
    """Whether PyOpenColorIO and numpy can be imported (looked up, not imported)."""
    import importlib
    import importlib.util

    importlib.invalidate_caches()  # packages installed a moment ago, into the live folder
    return all(importlib.util.find_spec(name) is not None for name in ("PyOpenColorIO", "numpy"))


def env_config() -> str:
    """The ``$OCIO`` environment variable (a studio's config), or ""."""
    return os.environ.get("OCIO", "").strip()


# ---- Configs ------------------------------------------------------------------------------------


_configs: dict[str, tuple[Any, Any]] = {}  # spec -> (mtime, config)
_configs_lock = threading.Lock()


def _mtime(spec: str) -> Any:
    if spec.startswith("ocio://"):
        return 0
    try:
        return os.stat(spec).st_mtime_ns
    except OSError:
        return None


def load_config(spec: str) -> Any:
    """The OCIO config at ``spec`` (a path or ``ocio://…``), reloaded when the file changes.

    Raises ``ValueError`` with a readable message when it can't be loaded.
    """
    import PyOpenColorIO as ocio

    spec = str(spec or BUILTIN)
    mtime = _mtime(spec)
    if mtime is None:
        raise ValueError(f"OCIO config not found: {spec}")
    with _configs_lock:
        cached = _configs.get(spec)
        if cached is not None and cached[0] == mtime:
            return cached[1]
    try:
        config = ocio.Config.CreateFromFile(spec)
        config.validate()
    except Exception as exc:  # noqa: BLE001  (OCIO raises its own Exception type)
        raise ValueError(f"Couldn't load OCIO config {spec}: {exc}") from exc
    with _configs_lock:
        _configs[spec] = (mtime, config)
    return config


def forget_configs() -> None:
    with _configs_lock:
        _configs.clear()
    with _processors_lock:
        _processors.clear()


# ---- Facts about a config (for menus and validation) --------------------------------------------


def displays(config: Any) -> list[str]:
    return list(config.getDisplays())


def views(config: Any, display: str) -> list[str]:
    return list(config.getViews(display)) if display in displays(config) else []


def looks(config: Any) -> list[str]:
    return [look.getName() for look in config.getLooks()]


def colorspaces(config: Any) -> list[tuple[str, str]]:
    """(name, family) of the config's active colour spaces, in the config's order."""
    return [(cs.getName(), cs.getFamily() or "") for cs in config.getColorSpaces()]


def has_colorspace(config: Any, name: str) -> bool:
    return config.getColorSpace(name) is not None


def scene_linear(config: Any) -> str:
    """The config's ``scene_linear`` role (what CG renders are usually in), else its first space."""
    space = config.getColorSpace("scene_linear")
    if space is not None:
        return space.getName()
    names = list(config.getColorSpaceNames())
    return names[0] if names else ""


def input_from_rules(config: Any, path: str) -> str:
    """The colour space the config's file rules give ``path``; the scene_linear role when only
    the catch-all default rule matches (or there is no path, e.g. an embedded image)."""
    if path:
        try:
            if not config.filepathOnlyMatchesDefaultRule(path):
                found = config.getColorSpaceFromFilepath(path)
                name = found[0] if isinstance(found, tuple) else found  # (name, rule index) in newer bindings
                if name and config.getColorSpace(name) is not None:
                    return str(name)
        except Exception:  # noqa: BLE001
            log.debug("OCIO file rules failed for %s", path, exc_info=True)
    return scene_linear(config)


# ---- The display snapshot ------------------------------------------------------------------------


@dataclass(frozen=True)
class Display:
    """How to show linear pixels: config + display + view (+ look) and the board's default input.

    ``key`` identifies it for the image cache (different keys decode separately).
    """

    config_spec: str
    cache_id: str
    display: str
    view: str
    look: str = ""
    input: str = ""  # the board's default input colour space; "" = from the config's file rules
    config: Any = field(default=None, compare=False, hash=False, repr=False)

    @property
    def key(self) -> tuple:
        return ("ocio", self.cache_id, self.display, self.view, self.look, self.input)

    def input_for(self, colorspace: str, path: str = "") -> str:
        """The input colour space of an image: its own, else the board default, else the file rules."""
        for name in (colorspace, self.input):
            if name and has_colorspace(self.config, name):
                return name
        return input_from_rules(self.config, path)

    def luma_coefs(self) -> tuple[float, float, float]:
        coefs = list(self.config.getDefaultLumaCoefs())
        return (float(coefs[0]), float(coefs[1]), float(coefs[2])) if len(coefs) == 3 else (0.2126, 0.7152, 0.0722)

    def processor(self, input_space: str) -> Any:
        """The CPU processor taking ``input_space`` to this display/view (cached)."""
        return processor(self, input_space)

    def apply(self, rgba: Any, input_space: str) -> Any:
        """``rgba`` (H×W×4 float32, straight alpha) through the display transform, in place; returns it."""
        import numpy as np

        if rgba.dtype != np.float32 or not rgba.flags.c_contiguous:
            rgba = np.ascontiguousarray(rgba, dtype=np.float32)
        alpha = rgba[..., 3].copy()  # views can touch alpha; the board shows it raw
        self.processor(input_space).applyRGBA(rgba)
        rgba[..., 3] = alpha
        return rgba


def make_display(spec: str, display: str = "", view: str = "", look: str = "", input: str = "") -> Display:  # noqa: A002
    """A :class:`Display` for config ``spec``; an unknown display, view or look falls back to the
    config's defaults (a board made with another config still shows). Raises ``ValueError``
    when the config can't be loaded."""
    config = load_config(spec)
    all_displays = displays(config)
    if display not in all_displays:
        display = config.getDefaultDisplay()
    all_views = views(config, display)
    if view not in all_views:
        view = config.getDefaultView(display)
    if look and look not in looks(config):
        look = ""
    if input and not has_colorspace(config, input):
        input = ""  # noqa: A001
    return Display(str(spec or BUILTIN), str(config.getCacheID()), display, view, look, input, config)


# ---- Processors ----------------------------------------------------------------------------------


_processors: OrderedDict[tuple, Any] = OrderedDict()
_processors_lock = threading.Lock()


def processor(display: Display, input_space: str) -> Any:
    key = (display.cache_id, input_space, display.display, display.view, display.look)
    with _processors_lock:
        cpu = _processors.get(key)
        if cpu is not None:
            _processors.move_to_end(key)
            return cpu
    import PyOpenColorIO as ocio

    transform = ocio.DisplayViewTransform(src=input_space, display=display.display, view=display.view)
    if display.look:
        # The look goes on top of the view (what the "looks" override of a viewer does).
        viewing = ocio.LegacyViewingPipeline()
        viewing.setDisplayViewTransform(transform)
        viewing.setLooksOverride(display.look)
        viewing.setLooksOverrideEnabled(True)
        cpu = viewing.getProcessor(display.config).getDefaultCPUProcessor()
    else:
        cpu = display.config.getProcessor(transform).getDefaultCPUProcessor()
    with _processors_lock:
        _processors[key] = cpu
        while len(_processors) > PROCESSORS:
            _processors.popitem(last=False)
    return cpu
