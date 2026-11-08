"""EXR support: OpenEXR images (and EXR frames of image sequences) on the board.

The image plugin draws EXRs through an ``image.decoders`` contribution (see
``ui.py`` and ``reader.py``): an EXR is a linear, high-dynamic-range picture,
shown through a per-node *view*:

* ``exr_layer``: the layer (AOV) shown, "" for the beauty (``R G B A``);
* ``exposure``: stops, applied to the linear values;
* ``gamma``: applied after the sRGB curve (1 = plain sRGB);
* ``channel``: ``rgb``, or one of ``r g b a luma`` shown as grey;
* ``colorspace``: the OCIO input colour space when colour management is on
  (the ocio plugin, service ``"color"``), "" for the board's default.

The attributes are sparse (only stored once changed) and live on image and
sequence nodes. Reading EXRs needs the OpenEXR and numpy Python packages, which
the plugin declares (``plugin.toml``); without them the plugin is off and EXR
images show a card saying how to turn it on, at their real size (the size comes
from the header, read without OpenEXR; see ``core/imagesize.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pixelref.plugin_api import Attr, CommandError, differs

from . import decode

EXR_MIME = "image/x-exr"
VIEW_TYPES = ("image", "sequence")
EXPOSURE_RANGE = (-20.0, 20.0)

VIEW_ATTRS = (
    Attr("exr_layer", "string", "", group="EXR View", label="Layer", omit_default=True,
         doc="EXR layer (AOV) shown; empty for the beauty (RGBA)"),
    Attr("exposure", "float", 0.0, group="EXR View", minimum=EXPOSURE_RANGE[0], maximum=EXPOSURE_RANGE[1],
         soft_min=-10.0, soft_max=10.0, step=0.25, precision=2, unit="EV", omit_default=True,
         doc="Exposure in stops, applied to the linear values of an EXR"),
    Attr("gamma", "float", 1.0, group="EXR View", minimum=0.1, maximum=5.0, soft_min=0.2, soft_max=3.0,
         step=0.05, precision=2, omit_default=True, doc="Display gamma applied after the sRGB curve (1 = sRGB)"),
    Attr("channel", "enum", "rgb", choices=decode.CHANNELS, group="EXR View", omit_default=True,
         doc="Show the colour, or one channel (red, green, blue, alpha, luminance) as grey"),
    Attr("colorspace", "string", "", group="EXR View", label="Input", omit_default=True,
         doc="OCIO input colour space (colour management on); empty = the board default / the config's file rules"),
)
VIEW_DEFAULTS = {attr.name: attr.default for attr in VIEW_ATTRS}


class ExrApi:
    """EXR facts about nodes (shared by the commands and the UI)."""

    def __init__(self, session: Any) -> None:
        self.session = session

    def source(self, node: Any) -> Any:
        """The EXR a node shows: a file path (str), embedded bytes, or None (not an EXR)."""
        attrs = node.attrs
        if node.node_type == "image":
            asset_id = str(attrs.get("asset", "") or "")
            if asset_id:
                asset = self.session.scene.get_asset(asset_id)
                return asset.data if asset is not None and asset.mime == EXR_MIME else None
            path = str(attrs.get("path", "") or "")
            if path and Path(path).suffix.lower() == ".exr":
                return str(self.session.resolve_path(path))
            return None
        if node.node_type == "sequence":
            sequences = self.session.services.get("sequences")
            frame = sequences.first_file(attrs) if sequences is not None else None
            return str(frame) if frame is not None and frame.suffix.lower() == ".exr" else None
        return None

    def is_exr(self, node: Any) -> bool:
        return self.source(node) is not None

    def info(self, node: Any) -> Any:
        source = self.source(node)
        return decode.info_of(source) if source is not None else None

    def layers(self, node: Any) -> list[str]:
        info = self.info(node)
        return list(decode.layer_table(info)) if info is not None else []


def activate(ctx: Any) -> None:
    session = ctx.session
    scene = session.scene
    for type_name in VIEW_TYPES:
        ctx.extend_node_type(type_name, VIEW_ATTRS)
    api = ExrApi(session)
    ctx.provide_service("exr", api)
    images = session.services.get("images")
    if images is not None and hasattr(images, "register_ext"):
        ctx.track(images.register_ext(".exr", EXR_MIME))

    def targets(nodes: Any, quiet: bool = False) -> list[str]:
        names = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        found = [n for n in names if scene.get_node(n).node_type in VIEW_TYPES and api.is_exr(scene.get_node(n))]
        if not found and not quiet:
            raise CommandError("Select an EXR image or sequence")
        return found

    def put(name: str, attr: str, value: Any) -> None:
        if differs(scene.get_node(name).attrs.get(attr, VIEW_DEFAULTS[attr]), value):
            scene.set_attr(name, attr, value)

    def layers(node: str | None = None) -> list[str]:
        """The layers (AOVs) of an EXR image or sequence ("" is the beauty, RGBA)."""
        names = targets(node if node is not None else None)
        return api.layers(scene.get_node(names[0]))

    def set_view(nodes: Any = None, exposure: float | None = None, gamma: float | None = None,
                 layer: str | None = None, channel: str | None = None, colorspace: str | None = None) -> list[str]:
        """Change how EXR images are shown: exposure (stops), gamma, layer ("" = beauty), channel,
        and OCIO input colorspace ("" = the board default / the config's file rules)."""
        if channel is not None and channel not in decode.CHANNELS:
            raise CommandError(f"Channel must be one of {', '.join(decode.CHANNELS)}")
        if colorspace:
            color = session.services.get("color")
            config = color.config() if color is not None else None
            if config is not None and config.getColorSpace(colorspace) is None:
                raise CommandError(f"The OCIO config has no colour space '{colorspace}'")
        names = targets(nodes)
        for name in names:
            if exposure is not None:
                put(name, "exposure", round(max(EXPOSURE_RANGE[0], min(EXPOSURE_RANGE[1], float(exposure))), 4))
            if gamma is not None:
                put(name, "gamma", round(max(0.1, min(5.0, float(gamma))), 4))
            if layer is not None:
                if layer and layer not in api.layers(scene.get_node(name)):
                    raise CommandError(f"{name} has no EXR layer '{layer}'")
                put(name, "exr_layer", str(layer))
            if channel is not None:
                put(name, "channel", channel)
            if colorspace is not None:
                put(name, "colorspace", str(colorspace))
        return names

    def adjust_exposure(stops: float, nodes: Any = None) -> list[str]:
        """Brighten (positive stops) or darken the selected EXR images."""
        names = targets(nodes)
        for name in names:
            current = float(scene.get_node(name).attrs.get("exposure", 0.0) or 0.0)
            set_view([name], exposure=current + float(stops))
        return names

    def cycle_channel(nodes: Any = None) -> str:
        """Show the next channel (RGB → R → G → B → A → luminance → RGB). Returns it."""
        names = targets(nodes)
        current = str(scene.get_node(names[0]).attrs.get("channel", "rgb") or "rgb")
        following = decode.CHANNELS[(decode.CHANNELS.index(current) + 1) % len(decode.CHANNELS)
                                    if current in decode.CHANNELS else 0]
        set_view(names, channel=following)
        return following

    def reset_view(nodes: Any = None) -> list[str]:
        """Back to the plain view: beauty layer, exposure 0, gamma 1, RGB, the default input colour space."""
        return set_view(nodes, exposure=0.0, gamma=1.0, layer="", channel="rgb", colorspace="")

    ctx.command("layers", layers, alias="exr_layers", label="EXR layers", undoable=False)
    ctx.command("set_view", set_view, alias="exr_set_view", label="EXR view")
    ctx.command("adjust_exposure", adjust_exposure, label="Adjust exposure")
    ctx.command("cycle_channel", cycle_channel, label="Cycle EXR channel")
    ctx.command("reset_view", reset_view, label="Reset EXR view")


def activate_ui(ui: Any) -> None:
    from .ui import activate_exr_ui

    activate_exr_ui(ui)
