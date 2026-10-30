"""Snap to grid: the grid is the canvas background's (``grid_size``, lines at multiples of it).

Drags snap to the lines the view draws: the grid size, or a coarser multiple
of it when zoomed out far enough that the background spreads its lines out
(``grid_step``). Commands without a view use the grid size itself.

Snapping on/off is your preference, not part of the scene. The ``snap``
service does the maths; the Select tool and the gizmo ask it while dragging
(Ctrl flips snapping for that drag) and work as before when this plugin is off.
"""

from __future__ import annotations

from typing import Any, Callable

from pixelref.plugin_api import xform

DEFAULT_STEP = 64.0
EPSILON = 1e-6


class SnapApi:
    def __init__(self, ctx: Any) -> None:
        self.session = ctx.session
        self.settings = ctx.settings
        # The UI swaps in the spacing a canvas view draws its grid lines at.
        self.view_step: Callable[[Any], float] | None = None
        self.listeners: list[Callable[[], None]] = []  # told when snapping is turned on or off

    @property
    def enabled(self) -> bool:
        return bool(self.settings.value("enabled", False, type=bool))

    def set_enabled(self, on: bool) -> None:
        if bool(on) == self.enabled:
            return
        self.settings.setValue("enabled", bool(on))
        for listener in list(self.listeners):
            listener()

    def active(self, invert: bool = False) -> bool:
        """Whether a drag snaps right now (*invert*: the drag's flip modifier is held)."""
        return self.enabled != bool(invert)

    def grid_size(self) -> float:
        """The background's grid size, as it draws it: the scene's value over your default, at least 8."""
        command = self.session.commands.get("canvas_background.current")
        settings = command.fn() if command is not None else {}
        try:
            return float(max(8, int(settings.get("grid_size") or DEFAULT_STEP)))
        except (TypeError, ValueError):
            return DEFAULT_STEP

    def step(self, view: Any = None) -> float:
        """The spacing of the grid lines to snap to: those *view* draws, else the grid size."""
        if view is not None and self.view_step is not None:
            return self.view_step(view)
        return self.grid_size()

    def value(self, v: float, view: Any = None, step: float | None = None) -> float:
        step = step or self.step(view)
        return round(v / step) * step + 0.0

    def on_grid(self, v: float, view: Any = None) -> bool:
        return abs(self.value(v, view) - v) < EPSILON

    def point(self, x: float, y: float, view: Any = None) -> tuple[float, float]:
        step = self.step(view)
        return self.value(x, step=step), self.value(y, step=step)

    def delta(self, rect: tuple[float, float, float, float], dx: float, dy: float,
              view: Any = None) -> tuple[float, float]:
        """Adjust a move of *rect* (left, top, width, height) so its nearest edge on each axis lands on the grid."""
        left, top, width, height = rect
        step = self.step(view)
        return self._axis(left, width, dx, step), self._axis(top, height, dy, step)

    def _axis(self, start: float, size: float, d: float, step: float) -> float:
        best = None
        for edge in (start + d, start + size + d):
            correction = self.value(edge, step=step) - edge
            if best is None or abs(correction) < abs(best):
                best = correction
        return d + best

    def factor(self, anchor: float, offset: float, f: float, view: Any = None) -> float:
        """Scale factor that puts a handle on the grid: the handle is at ``anchor + offset * f`` on one axis."""
        if abs(offset) < EPSILON:
            return f
        snapped = (self.value(anchor + offset * f, view) - anchor) / offset
        return snapped if snapped > 0.02 else f


def activate(ctx: Any) -> None:
    session = ctx.session
    api = SnapApi(ctx)
    ctx.provide_service("snap", api)

    def set_enabled(on: bool = True) -> bool:
        """Turn snapping to the grid on or off (a preference: it isn't saved with the scene)."""
        api.set_enabled(on)
        return api.enabled

    def toggle() -> bool:
        """Turn snapping to the grid on or off."""
        return set_enabled(not api.enabled)

    def to_grid(nodes: Any = None) -> list[str]:
        """Move each item so its nearest edges sit on grid lines."""
        transform = session.services.require("transform")
        names = transform.targets(nodes)
        for name in names:
            bounds = session.world_bounds(name)
            if bounds is None:
                continue
            dx, dy = api.delta(tuple(bounds), 0.0, 0.0)
            if abs(dx) > EPSILON or abs(dy) > EPSILON:
                transform.apply_world(name, xform.Matrix.translation(dx, dy))
        return names

    def enabled() -> bool:
        """Whether drags snap to the grid."""
        return api.enabled

    ctx.command("set_enabled", set_enabled, undoable=False)
    ctx.command("toggle", toggle, alias="toggle_snap", undoable=False)
    ctx.command("enabled", enabled, undoable=False, echo=False)
    ctx.command("to_grid", to_grid, alias="snap_to_grid", label="Snap to grid")


def activate_ui(ui: Any) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QCheckBox

    from pixelref.plugin_api.contracts import CanvasRow
    from pixelref.plugin_api.ui import grid_step, icon

    api = ui.service("snap")
    app = ui.app
    api.view_step = lambda view: grid_step(view.background_spec(), view.zoom_level())
    ui.track(lambda: setattr(api, "view_step", None))
    rows: list[QCheckBox] = []

    class SnapRow(QCheckBox):
        """The "Snap to grid" toggle in the Attribute Editor's Canvas panel."""

        def __init__(self, parent: Any) -> None:
            super().__init__(parent)
            self.setIcon(icon("snap"))
            self.setFocusPolicy(Qt.NoFocus)  # don't take focus from the canvas
            self.setToolTip("Snap moves and scaling to the background grid (Alt+') · Ctrl while dragging flips it")
            self.toggled.connect(self._on_toggled)
            rows.append(self)
            self.destroyed.connect(lambda *_a, r=self: r in rows and rows.remove(r))
            self.refresh()

        def _on_toggled(self, on: bool) -> None:
            if on != api.enabled:
                ui.run("snap.set_enabled", on)

        def refresh(self) -> None:
            self.blockSignals(True)
            self.setChecked(api.enabled)
            self.blockSignals(False)

    def on_changed() -> None:
        """Snapping was turned on or off (toggle, menu, checkbox or script): show it everywhere."""
        for row in list(rows):
            try:
                row.refresh()
            except RuntimeError:  # deleted with its panel
                rows.remove(row)
        app.actions.refresh_states()

    api.listeners.append(on_changed)
    ui.track(lambda: on_changed in api.listeners and api.listeners.remove(on_changed))
    ui.contribute("canvas_background.rows", CanvasRow("snap", "Snap to grid", 10, SnapRow))

    def toggle() -> None:
        """Turn snapping to the grid on or off, and say so."""
        on = ui.run("snap.toggle")
        ui.status(f"Snap to grid {'on' if on else 'off'} · grid {api.grid_size():.0f}px · Ctrl while dragging flips it")

    ui.command("toggle_ui", toggle, undoable=False, echo=False)
    a = ui.action
    a("snap.toggle", "snap.toggle_ui", "Snap to Grid", keywords="grid align magnet", category="View",
      icon="snap", checked=lambda: api.enabled)
    a("snap.to_grid", "snap.to_grid", "Snap Selection to Grid", keywords="align grid line up", category="Transform",
      icon="snap")
    ui.bind("Alt+'", "snap.toggle")
    ui.menu("canvas", "snap.toggle", path="View/Background", group="bg3", order=3)
    ui.menu("canvas", "snap.to_grid", path="Transform", group="transform", order=10, when="selection.count > 0")
