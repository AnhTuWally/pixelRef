"""Qt side of the canvas background plugin."""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QFormLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from pixelref.plugin_api.contracts import EmptyPanel
from pixelref.plugin_api.ui import BackgroundSpec, pattern_color
from pixelref.plugin_api.ui import widgets as w

from . import PATTERNS, PRESETS


class BackgroundService:
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.ctx = ui.headless
        self._last: Any = None

    def settings(self) -> dict[str, Any]:
        return self.ctx.current()

    def spec(self) -> BackgroundSpec:
        s = self.settings()
        color = tuple(int(c) for c in (s.get("background") or [23, 25, 29])[:3])
        custom = s.get("pattern_color")
        return BackgroundSpec(
            color=color,  # type: ignore[arg-type]
            pattern=s.get("pattern") if s.get("pattern") in [p for p, _l in PATTERNS] else "lines",
            grid_size=max(8, int(s.get("grid_size") or 64)),
            pattern_color=tuple(int(c) for c in custom[:3]) if custom else None,  # type: ignore[arg-type]
        )

    # Convenience accessors (the panel, tests and scripts).
    @property
    def background_color(self) -> QColor:
        return QColor(*self.spec().color)

    @property
    def pattern(self) -> str:
        return self.spec().pattern

    @property
    def grid_size(self) -> int:
        return self.spec().grid_size

    @property
    def grid_color(self) -> QColor:
        return pattern_color(self.spec())

    @property
    def custom_pattern_color(self) -> QColor | None:
        custom = self.spec().pattern_color
        return QColor(*custom) if custom else None

    def changed(self) -> bool:
        current = self.settings()
        if current != self._last:
            self._last = dict(current)
            return True
        return False


class CanvasPanel(QWidget):
    """Background settings, shown in the Attribute Editor when nothing is selected."""

    def __init__(self, ui: Any, service: BackgroundService, parent: QWidget) -> None:
        super().__init__(parent)
        self.ui = ui
        self.service = service
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(w.section_header(self, "Canvas"))
        form = QFormLayout()
        form.setContentsMargins(2, 0, 2, 0)
        form.setSpacing(6)
        run = ui.app.run_command
        self.bg_color_btn = w.make_tool_button("", "Background color…", parent=self)
        self.bg_color_btn.clicked.connect(lambda: ui.app.trigger("canvas_background.color"))
        self._bg_preset_btns = []
        for name, rgb in PRESETS:
            button = w.make_tool_button("", f"{name} background", width=20, parent=self)
            button.setFixedHeight(20)
            w.swatch(button, QColor(*rgb))
            button.clicked.connect(lambda _checked=False, c=rgb: run("canvas_background.set", background=list(c)))
            self._bg_preset_btns.append(button)
        form.addRow("Color", w.hrow(self, self.bg_color_btn, None, *self._bg_preset_btns))
        self.bg_pattern_combo = w.make_combo(PATTERNS, self)
        self.bg_pattern_combo.currentIndexChanged.connect(self._on_pattern)
        form.addRow("Pattern", self.bg_pattern_combo)
        self.bg_grid_spin = QSpinBox(self)
        self.bg_grid_spin.setRange(8, 512)
        self.bg_grid_spin.setSingleStep(8)
        self.bg_grid_spin.setSuffix(" px")
        self.bg_grid_spin.setKeyboardTracking(False)
        self.bg_grid_spin.valueChanged.connect(self._on_grid)
        form.addRow("Grid size", self.bg_grid_spin)
        self.bg_pattern_color_btn = w.make_tool_button("", "Pattern color…", parent=self)
        self.bg_pattern_color_btn.clicked.connect(lambda: ui.app.trigger("canvas_background.pattern_color"))
        self.bg_pattern_auto_btn = w.make_tool_button("Auto", "Derive the pattern color from the background", True, 44,
                                                      self)
        self.bg_pattern_auto_btn.clicked.connect(lambda: run("canvas_background.set", pattern_color=None))
        form.addRow("Pattern color", w.hrow(self, self.bg_pattern_color_btn, self.bg_pattern_auto_btn))
        layout.addLayout(form)
        self.form = form
        self.extra_rows: list[QWidget] = []
        self.rebuild_rows()
        hint = QLabel("Saved with the scene · new scenes start with your last choice", self)
        hint.setObjectName("pathLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.refresh()

    def rebuild_rows(self) -> None:
        """Rows other plugins add (``canvas_background.rows``), below the background settings."""
        for widget in self.extra_rows:
            self.form.removeRow(widget)
        self.extra_rows = []
        for row in sorted(self.ui.contributions("canvas_background.rows"), key=lambda r: r.order):
            try:
                widget = row.build(self)
            except Exception:  # noqa: BLE001
                self.ui.log.exception("Building canvas row %s failed", row.key)
                continue
            widget.setObjectName(f"canvasRow_{row.key}")
            self.form.addRow(row.label, widget)
            self.extra_rows.append(widget)

    def _on_pattern(self, _index: int) -> None:
        if not self._updating:
            self.ui.app.run_command("canvas_background.set", pattern=self.bg_pattern_combo.currentData())

    def _on_grid(self, value: int) -> None:
        if not self._updating:
            self.ui.app.run_command("canvas_background.set", grid_size=int(value))

    def refresh(self) -> None:
        spec = self.service.spec()
        self._updating = True
        try:
            w.set_combo(self.bg_pattern_combo, spec.pattern)
            self.bg_grid_spin.setValue(spec.grid_size)
            w.swatch(self.bg_color_btn, QColor(*spec.color))
            w.swatch(self.bg_pattern_color_btn, pattern_color(spec))
            self.bg_pattern_auto_btn.setChecked(spec.pattern_color is None)
        finally:
            self._updating = False
        for widget in self.extra_rows:
            widget.refresh()


def activate_background_ui(ui: Any) -> None:
    service = BackgroundService(ui)
    ui.provide_service("canvas_background", service)
    ui.contribute("canvas.background", service.spec)
    app = ui.app
    panels: list[CanvasPanel] = []

    def make_panel(parent: QWidget, _inspector: Any) -> CanvasPanel:
        panel = CanvasPanel(ui, service, parent)
        panels.append(panel)
        panel.destroyed.connect(lambda *_a, p=panel: p in panels and panels.remove(p))
        return panel

    # Declared before the panel exists: the inspector builds it as soon as it's contributed.
    ui.declare_extension_point("canvas_background.rows", "CanvasRow: settings rows in the Canvas panel")
    ui.contribute("inspector.empty_panels", EmptyPanel("canvas", 0, make_panel))

    def rebuild_rows() -> None:
        for panel in list(panels):
            try:
                panel.rebuild_rows()
            except RuntimeError:
                panels.remove(panel)

    ui.track(ui.session.extensions.watch("canvas_background.rows", rebuild_rows))

    def sync(_event: Any = None) -> None:
        if service.changed():
            app.refresh_backgrounds()
            for panel in list(panels):
                try:
                    panel.refresh()
                except RuntimeError:
                    panels.remove(panel)
            app.actions.refresh_states()

    ui.on("document.changed", sync)
    ui.on("document.replaced", sync)
    ui.track(lambda: app.refresh_backgrounds())
    sync()

    def pick_background() -> None:
        """Choose the background colour in a colour dialog."""
        w.pick_color_live(app, app.main_window(), service.background_color, "Background color",
                          lambda color: app.run_command("canvas_background.set", background=w.rgb(color)),
                          "Background color")

    def pick_pattern_color() -> None:
        """Choose the pattern colour in a colour dialog."""
        w.pick_color_live(app, app.main_window(), service.grid_color, "Pattern color",
                          lambda color: app.run_command("canvas_background.set", pattern_color=w.rgb(color)),
                          "Pattern color")

    ui.command("pick_color", pick_background, undoable=False, echo=False)
    ui.command("pick_pattern_color", pick_pattern_color, undoable=False, echo=False)
    a = ui.action
    a("canvas_background.grid", "canvas_background.toggle_pattern", "Show Background Pattern",
      keywords="pattern background lines", category="View", checked=lambda: service.pattern != "none")
    a("canvas_background.color", "canvas_background.pick_color", "Background Color…", keywords="canvas colour backdrop",
      category="View")
    a("canvas_background.pattern_color", "canvas_background.pick_pattern_color", "Pattern Color…",
      keywords="grid colour", category="View")
    a("canvas_background.auto_pattern_color", "canvas_background.set", "Auto Pattern Color",
      kwargs={"pattern_color": None}, category="View")
    ui.bind("Ctrl+'", "canvas_background.grid")
    menu_path = "View/Background"
    ui.place_submenu("canvas", menu_path, "view.background", 0)
    for order, (key, label) in enumerate(PATTERNS):
        a(f"canvas_background.pattern_{key}", "canvas_background.set", label, kwargs={"pattern": key},
          category="View", group="canvas_background.patterns", checked=lambda k=key: service.pattern == k)
        ui.menu("canvas", f"canvas_background.pattern_{key}", path=menu_path, group="bg1", order=order)
    ui.menu("canvas", "canvas_background.color", path=menu_path, group="bg2", order=0)
    for order, (name, rgb) in enumerate(PRESETS):
        a(f"canvas_background.preset_{name.lower()}", "canvas_background.set", name,
          kwargs={"background": list(rgb)}, category="View")
        ui.menu("canvas", f"canvas_background.preset_{name.lower()}", path=menu_path, group="bg2", order=order + 1)
    ui.menu("canvas", "canvas_background.pattern_color", path=menu_path, group="bg3", order=0)
    ui.menu("canvas", "canvas_background.auto_pattern_color", path=menu_path, group="bg3", order=1)
    ui.menu("canvas", "canvas_background.grid", path=menu_path, group="bg3", order=2)
