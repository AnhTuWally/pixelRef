"""The Qt side of colour management: the Colour panel (nothing selected), actions and the installer."""

from __future__ import annotations

from typing import Any

import os

from PySide6.QtWidgets import QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from pixelref.plugin_api.contracts import EmptyPanel
from pixelref.plugin_api.ui import widgets as w

from . import CHANGED
from . import config as cfg

PACKAGES = ("opencolorio>=2.2", "numpy>=1.26")  # plugin.toml's [python] optional
AUTO = ""  # the config combo's "$OCIO, else the built-in ACES config"
CUSTOM = "\0custom"  # the config combo's "Choose a file…"


class ColorPanel(QWidget):
    """Colour management settings, shown in the Attribute Editor when nothing is selected."""

    def __init__(self, ui: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self.ui = ui
        self.api = ui.session.services.require("color")
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(w.section_header(self, "Colour"))
        self.form = form = QFormLayout()
        form.setContentsMargins(2, 0, 2, 0)
        form.setSpacing(6)

        self.mode = w.make_combo((("builtin", "Built-in sRGB"), ("ocio", "OCIO")), self)
        self.mode.setToolTip("How EXR images are shown: pixelRef's plain sRGB curve, or an OCIO display and view")
        self.mode.currentIndexChanged.connect(lambda _i: self._set(mode=self.mode.currentData()))
        form.addRow("EXR view", self.mode)

        self.config_row = QWidget(self)
        config_layout = QHBoxLayout(self.config_row)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.setSpacing(4)
        self.config = w.make_combo((), self.config_row)
        self.config.setToolTip("The OCIO config: $OCIO (else OCIO's built-in ACES config), a built-in one, or a file")
        self.config.activated.connect(self._on_config)
        config_layout.addWidget(self.config, 1)
        self.browse = w.make_tool_button("Browse…", "Open an OCIO config file (.ocio / .ocioz)",
                                         width=70, parent=self.config_row)
        self.browse.clicked.connect(lambda: ui.app.trigger("ocio.pick_config"))
        config_layout.addWidget(self.browse)
        form.addRow("Config", self.config_row)

        self.display = w.make_combo((), self)
        self.display.currentIndexChanged.connect(lambda _i: self._set(display=self.display.currentData() or ""))
        form.addRow("Display", self.display)

        self.view = w.make_combo((), self)
        self.view.currentIndexChanged.connect(lambda _i: self._set(view=self.view.currentData() or ""))
        form.addRow("View", self.view)

        self.look = w.make_combo((), self)
        self.look.currentIndexChanged.connect(lambda _i: self._set(look=self.look.currentData() or ""))
        form.addRow("Look", self.look)

        self.input = w.make_combo((), self)
        self.input.setToolTip("The colour space of EXRs that don't pick their own (Auto: the config's file rules)")
        self.input.currentIndexChanged.connect(lambda _i: self._set(input=self.input.currentData() or ""))
        form.addRow("Default input", self.input)

        self.save_with_board = QCheckBox("Save with this board", self)
        self.save_with_board.setToolTip("Keep these colour settings in the board file (else your preferences apply)")
        self.save_with_board.toggled.connect(self._on_save_with_board)
        form.addRow("", self.save_with_board)

        self.install = w.make_tool_button("Install OCIO Support…", "Install the opencolorio and numpy packages",
                                          width=150, parent=self)
        self.install.clicked.connect(lambda: ui.app.trigger("ocio.install"))
        form.addRow("", self.install)
        layout.addLayout(form)

        self.status = QLabel(self)
        self.status.setObjectName("pathLabel")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    # ---- Edits -------------------------------------------------------------------------------

    def _set(self, **changes: Any) -> None:
        if not self._updating:
            self.ui.app.run_command("ocio.set", **changes)

    def _on_config(self, _index: int) -> None:
        if self._updating:
            return
        value = self.config.currentData()
        if value == CUSTOM:
            self.ui.app.trigger("ocio.pick_config")
            self._shown = None
            self.refresh()  # cancelled: back to the config in effect
            return
        if value != self.api.settings()["config"]:
            self._set(config=value)

    def _on_save_with_board(self, on: bool) -> None:
        if not self._updating:
            self.ui.app.run_command("ocio.save_with_board", on)

    # ---- Refresh -----------------------------------------------------------------------------

    def refresh(self) -> None:
        settings = self.api.settings()
        display = self.api.display()
        available = self.api.available()
        # The inspector refreshes its panels on every canvas refresh: rebuild the menus only on change.
        shown = (tuple(settings.values()), self.api.key(), available, self.api.saved_with_scene(), self.api.error)
        if shown == getattr(self, "_shown", None):
            return
        self._shown = shown
        managed = settings["mode"] == "ocio"
        config = self.api.config() if managed and available else None
        self._updating = True
        try:
            w.set_combo(self.mode, settings["mode"])
            self._fill_configs(settings["config"])
            shown_display = display.display if display is not None else settings["display"]
            shown_view = display.view if display is not None else settings["view"]
            self._fill(self.display, cfg.displays(config) if config else [], shown_display)
            self._fill(self.view, cfg.views(config, shown_display) if config else [], shown_view)
            self._fill(self.look, cfg.looks(config) if config else [], settings["look"], ("", "None"))
            spaces = [name for name, _family in cfg.colorspaces(config)] if config else []
            self._fill(self.input, spaces, settings["input"], ("", "Auto (file rules)"))
            self.save_with_board.setChecked(self.api.saved_with_scene())
        finally:
            self._updating = False
        for row in (self.config_row, self.display, self.view, self.look, self.input):
            self.form.setRowVisible(row, managed and available)
        self.form.setRowVisible(self.install, managed and not available)
        self.status.setText(self._status(settings, display))

    def _fill_configs(self, current: str) -> None:
        env = cfg.env_config()
        choices = [(AUTO, f"$OCIO ({env})" if env else "Default (built-in ACES CG)")]
        choices += [(spec, label) for spec, label in cfg.BUILTIN_CONFIGS]
        if current and current not in [spec for spec, _label in choices]:
            choices.append((current, current))
        choices.append((CUSTOM, "Choose a file…"))
        self.config.clear()
        for value, label in choices:
            self.config.addItem(label, value)
        w.set_combo(self.config, current)

    @staticmethod
    def _fill(combo: Any, names: list[str], current: str, first: tuple[str, str] | None = None) -> None:
        combo.clear()
        if first is not None:
            combo.addItem(first[1], first[0])
        for name in names:
            combo.addItem(name, name)
        if current and combo.findData(current) < 0:
            combo.addItem(f"{current} (missing)", current)
        w.set_combo(combo, current)

    def _status(self, settings: dict[str, str], display: Any) -> str:
        where = "Saved with this board" if self.api.saved_with_scene() else "Your preferences"
        if self.api.error:
            return f"⚠ {self.api.error}"
        if display is not None:
            return f"{where} · {display.config.getName() or display.config_spec}"
        if settings["mode"] == "ocio":
            return where
        return f"{where} · EXRs shown with the sRGB curve"


def activate_ocio_ui(ui: Any) -> None:
    session = ui.session
    api = session.services.require("color")
    panels: list[ColorPanel] = []

    def make_panel(parent: QWidget, _inspector: Any) -> ColorPanel:
        panel = ColorPanel(ui, parent)
        panels.append(panel)
        panel.destroyed.connect(lambda *_a, p=panel: p in panels and panels.remove(p))
        return panel

    ui.contribute("inspector.empty_panels", EmptyPanel("colour", 10, make_panel))

    state = {"error": ""}

    def sync(_event: Any = None) -> None:
        for panel in list(panels):
            try:
                panel.refresh()
            except RuntimeError:  # deleted with its inspector
                panels.remove(panel)
        api.display()  # brings api.error up to date
        if api.error and api.error != state["error"]:
            ui.status(f"Colour: {api.error}")
        state["error"] = api.error
        ui.app.actions.refresh_states()

    ui.on(CHANGED, sync)

    def toggle() -> str:
        """Switch EXR images between the built-in sRGB view and OCIO colour management."""
        mode = "builtin" if api.settings()["mode"] == "ocio" else "ocio"
        session.run("ocio.set", mode=mode)
        if mode == "ocio" and not api.available():
            ui.status("OCIO isn't installed: Image → Install OCIO Support…")
        return mode

    def start_folder() -> str:
        """Where the file dialog opens: the folder of the config in use, of $OCIO, or the last one picked."""
        for spec in (api.settings().get("config", ""), cfg.env_config(), api.prefs.value("last_config_dir", "")):
            spec = str(spec or "")
            if spec and not spec.startswith("ocio://"):
                folder = spec if os.path.isdir(spec) else os.path.dirname(spec)
                if os.path.isdir(folder):
                    return folder
        return ""

    def pick_config() -> str:
        """Open an OCIO config file (.ocio / .ocioz) and show EXRs through it."""
        if not api.available(recheck=True):
            ui.status("OCIO isn't installed: Image → Install OCIO Support…")
            return ""
        path, _filter = QFileDialog.getOpenFileName(ui.app.main_window(), "Open OCIO Config", start_folder(),
                                                    "OCIO configs (*.ocio *.ocioz);;All files (*)")
        if not path:
            return ""
        path = os.path.normpath(path)
        api.prefs.setValue("last_config_dir", os.path.dirname(path))
        if ui.app.run_command("ocio.set", config=path, mode="ocio") is None:
            return ""  # refused (not a valid config): run_command reported why
        display = api.display()
        name = display.config.getName() if display is not None else ""
        ui.status(f"OCIO config: {name or os.path.basename(path)}")
        return path

    def install_support() -> None:
        """Install the opencolorio and numpy Python packages colour management needs (in the background)."""
        if api.available(recheck=True):
            api.changed()
            ui.status("OCIO support is already installed")
            return
        from pixelref.ui.package_jobs import package_runner

        runner = package_runner(ui.app)

        def finished(result: dict[str, Any]) -> None:
            runner.finished.disconnect(finished)
            if result.get("ok") and api.available(recheck=True):
                api.changed()
                ui.status("OCIO support installed")
            elif result.get("ok"):
                ui.status("OCIO packages installed: restart pixelRef to use them")
            else:
                ui.status(f"Couldn't install OCIO support: {result.get('message', '')}")

        runner.finished.connect(finished)
        if not runner.start(lambda: session.packages.plan_install(list(PACKAGES), plugins=["ocio"])):
            runner.finished.disconnect(finished)
            ui.status("Another Python package install is running; try again when it's done")

    ui.command("toggle", toggle, undoable=False)
    ui.command("pick_config", pick_config, undoable=False, echo=False)
    ui.command("install_support", install_support, undoable=False)

    ui.action("ocio.toggle", "ocio.toggle", "Toggle OCIO Colour Management",
              keywords="color colour management ocio aces display view srgb exr", category="Image",
              checked=lambda: api.settings()["mode"] == "ocio")
    ui.action("ocio.pick_config", "ocio.pick_config", "Open OCIO Config…",
              keywords="color colour ocio config aces file open browse choose load", category="Image")
    ui.action("ocio.reload", "ocio.reload", "Reload OCIO Config",
              keywords="color colour ocio config reload refresh", category="Image")
    ui.action("ocio.install", "ocio.install_support", "Install OCIO Support…",
              keywords="opencolorio ocio aces colour color management package install", category="Image",
              enabled=lambda: not api.available())

    def selection_has_exr() -> bool:
        exr = session.services.get("exr")
        scene = session.scene
        return exr is not None and any(scene.has(n) and exr.is_exr(scene.get_node(n))
                                       for n in session.selection.names)

    ui.menu("canvas", "ocio.toggle", path="Image", group="exr", order=5, when=selection_has_exr)
    ui.menu("canvas", "ocio.pick_config", path="Image", group="exr", order=6, when=selection_has_exr)
    # Always reachable, EXR selected or not.
    ui.place_submenu("canvas", "File/Colour Management", group="file.storage", order=10)
    ui.menu("canvas", "ocio.pick_config", path="File/Colour Management", group="ocio", order=0)
    ui.menu("canvas", "ocio.reload", path="File/Colour Management", group="ocio", order=1)
    ui.menu("canvas", "ocio.toggle", path="File/Colour Management", group="ocio.mode", order=0)
    ui.menu("canvas", "ocio.install", path="Image", group="exr", order=11,
            when=lambda: selection_has_exr() and api.settings()["mode"] == "ocio" and not api.available())
