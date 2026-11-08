"""The Qt side of EXR support: the image decoder, the EXR View section, actions and keys."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel

from pixelref.plugin_api.contracts import ImageDecoder, InspectorSection, SelectionInfo
from pixelref.plugin_api.ui import widgets as w

from . import EXR_MIME, VIEW_ATTRS, VIEW_TYPES, decode
from .reader import ExrReader, view_of

EXPOSURE_STEP = 0.5  # stops per [ / ] press
PACKAGES = ("OpenEXR>=3.3", "numpy>=1.26")  # plugin.toml's [python] optional


def packages_available() -> bool:
    """Whether OpenEXR and numpy can be imported (looked up, not imported)."""
    import importlib
    import importlib.util

    importlib.invalidate_caches()  # packages installed a moment ago, into the live folder
    return all(importlib.util.find_spec(name) is not None for name in ("OpenEXR", "numpy"))


class ExrViewSection(w.InspectorSection):
    """Layer, channel, exposure and gamma of the selected EXR image or sequence."""

    def __init__(self, ui: Any, parent: Any) -> None:
        super().__init__("exr_view", "EXR View", parent)
        self.ui = ui
        self.api = ui.session.services.require("exr")
        self._name: str | None = None
        self._updating = False
        specs = {attr.name: attr for attr in VIEW_ATTRS}

        self.layer = w.make_combo((("", decode.BASE_LABEL),), self)
        self.layer.setToolTip("The EXR layer (AOV) shown")
        self.layer.currentIndexChanged.connect(self._on_layer)
        self.form.addRow("Layer", self.layer)

        self.channel = w.make_combo(tuple((c, decode.CHANNEL_LABELS[c]) for c in decode.CHANNELS), self)
        self.channel.setToolTip("Show the colour, or one channel as grey")
        self.channel.currentIndexChanged.connect(self._on_channel)
        self.form.addRow("Channel", self.channel)

        self.exposure = w.fields.editor_for(specs["exposure"], self)
        self.exposure.setToolTip("Exposure in stops ([ and ] step it by half a stop)")
        w.fields.bind_edits(ui.app, self.exposure, lambda v: self._set(exposure=v), label="EXR exposure")
        self.form.addRow("Exposure", self.exposure)

        self.gamma = w.fields.editor_for(specs["gamma"], self)
        self.gamma.setToolTip("Display gamma after the sRGB curve (1 = sRGB)")
        w.fields.bind_edits(ui.app, self.gamma, lambda v: self._set(gamma=v), label="EXR gamma")
        self.form.addRow("Gamma", self.gamma)

        reset = w.make_tool_button("Reset", "Beauty layer, exposure 0, gamma 1, RGB", width=56, parent=self)
        reset.clicked.connect(lambda: self._name and ui.app.run_command("exr.reset_view", [self._name]))
        self.info = QLabel(self)
        self.info.setObjectName("dimLabel")
        self.form.addRow(w.hrow(self, reset, None, self.info))

    def refresh(self, info: SelectionInfo) -> None:
        self._name = info.names[0] if info.single else None
        if self._name is None:
            return
        node = self.ui.session.scene.get_node(self._name)
        layer, exposure, gamma, channel = view_of(node.attrs)
        exr = self.api.info(node)
        layers = list(decode.layer_table(exr)) if exr is not None else []
        self._updating = True
        try:
            self.layer.clear()
            for name in layers or [""]:
                self.layer.addItem(decode.layer_label(name), name)
            if layer and layer not in layers:
                self.layer.addItem(f"{layer} (missing)", layer)
            w.set_combo(self.layer, layer)
            w.set_combo(self.channel, channel)
            self.exposure.write(exposure)
            self.gamma.write(gamma)
        finally:
            self._updating = False
        if exr is not None:
            width, height = exr.size or (0, 0)
            count = len(layers)
            parts = f" · {len(exr.parts)} parts" if len(exr.parts) > 1 else ""
            self.info.setText(f"{width} × {height} · {decode.pixel_type_label(exr)} · "
                              f"{count} layer{'s' if count != 1 else ''}{parts}")
        else:
            self.info.setText("")

    def _set(self, **view: Any) -> None:
        if not self._updating and self._name:
            self.ui.app.run_command("exr.set_view", [self._name], **view)

    def _on_layer(self, _index: int) -> None:
        self._set(layer=str(self.layer.currentData() or ""))

    def _on_channel(self, _index: int) -> None:
        self._set(channel=str(self.channel.currentData() or "rgb"))


def activate_exr_ui(ui: Any) -> None:
    session = ui.session
    api = session.services.require("exr")

    cache = session.services.require("image_cache")
    state: dict[str, Any] = {"decoder": None}

    def view_key(attrs: dict[str, Any]) -> tuple:
        return view_of(attrs)

    # Items showing EXRs were built before the decoder existed (a placeholder card): rebuild
    # them when it comes or goes (the refresh is deferred to the event loop, so on deactivation
    # it runs after this plugin's contributions are removed).
    def rebuild_if_exr() -> None:
        scene = session.scene
        if any(node.node_type in VIEW_TYPES and api.is_exr(node) for node in scene.nodes.values()):
            ui.canvas.request_full_refresh()

    def start_decoding() -> bool:
        """Contribute the decoder once OpenEXR and numpy can be imported. True when it is in."""
        if state["decoder"] is None and packages_available():
            state["decoder"] = ui.contribute("image.decoders", ImageDecoder(
                id="exr", exts=frozenset({".exr"}), mimes=frozenset({EXR_MIME}),
                open_reader=ExrReader, view_key=view_key, label="EXR"))
            rebuild_if_exr()
        return state["decoder"] is not None

    # Without the packages, EXR cards say how to get them (instead of "switch on the plugin").
    previous_hint = cache.hints.get(EXR_MIME)
    cache.hints[EXR_MIME] = ("OpenEXR", "Right-click → Image → Install EXR Support…")
    cache.forget_placeholders()

    def restore_hint() -> None:
        if previous_hint is not None:
            cache.hints[EXR_MIME] = previous_hint
        cache.forget_placeholders()

    ui.track(restore_hint)
    if not start_decoding():
        rebuild_if_exr()  # the cards now carry the install hint
    ui.track(rebuild_if_exr)

    def install_support() -> None:
        """Install the OpenEXR and numpy Python packages EXR images need (in the background)."""
        if start_decoding():
            ui.status("EXR support is already installed")
            return
        from pixelref.ui.package_jobs import package_runner

        runner = package_runner(ui.app)

        def finished(result: dict[str, Any]) -> None:
            runner.finished.disconnect(finished)
            if result.get("ok") and start_decoding():
                ui.status("EXR support installed: EXR images now show their pixels")
            elif result.get("ok"):
                ui.status("EXR packages installed: restart pixelRef to show EXR images")
            else:
                ui.status(f"Couldn't install EXR support: {result.get('message', '')}")

        runner.finished.connect(finished)
        if not runner.start(lambda: session.packages.plan_install(list(PACKAGES), plugins=["exr"])):
            runner.finished.disconnect(finished)
            ui.status("Another Python package install is running; try again when it's done")

    ui.command("install_support", install_support, undoable=False)
    ui.action("exr.install", "exr.install_support", "Install EXR Support…",
              keywords="openexr numpy package hdr exr install", category="Image",
              enabled=lambda: state["decoder"] is None)

    def selection_has_exr() -> bool:
        scene = session.scene
        return any(scene.has(n) and scene.get_node(n).node_type in VIEW_TYPES and api.is_exr(scene.get_node(n))
                   for n in session.selection.names)

    ui.contribute("inspector.sections", InspectorSection(
        key="exr_view", title="EXR View", order=31, generic=True,  # keeps a sequence's Attributes editor
        applies=lambda info: info.single and info.node["type"] in VIEW_TYPES
        and api.is_exr(session.scene.get_node(info.names[0])),
        build=lambda parent, _inspector: ExrViewSection(ui, parent),
    ))

    ui.action("exr.exposure_up", "exr.adjust_exposure", "Exposure Up (EXR)", args=[EXPOSURE_STEP],
              keywords="brighter stop hdr exr exposure", category="Image")
    ui.action("exr.exposure_down", "exr.adjust_exposure", "Exposure Down (EXR)", args=[-EXPOSURE_STEP],
              keywords="darker stop hdr exr exposure", category="Image")
    ui.action("exr.cycle_channel", "exr.cycle_channel", "Cycle Channel (EXR)",
              keywords="red green blue alpha luminance exr channel", category="Image")
    ui.action("exr.reset_view", "exr.reset_view", "Reset EXR View",
              keywords="exposure gamma layer channel exr default", category="Image")
    ui.bind("]", "exr.exposure_up", when=selection_has_exr, scope="canvas")
    ui.bind("[", "exr.exposure_down", when=selection_has_exr, scope="canvas")
    for order, key in enumerate(("exr.exposure_up", "exr.exposure_down", "exr.cycle_channel", "exr.reset_view")):
        ui.menu("canvas", key, path="Image", group="exr", order=order, when=selection_has_exr)
    ui.menu("canvas", "exr.install", path="Image", group="exr", order=10,
            when=lambda: state["decoder"] is None and selection_has_exr())
