"""Qt side of the model3d plugin: item factory, 3D View Mode interaction, quick bar,
gizmo viewport handles, Attribute Editor section, storage rows and file ingest."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QTransform
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from pixelref.plugin_api import differs
from pixelref.plugin_api.contracts import (
    IngestHandler, InspectorRows, InspectorSection, PaletteButton, QuickSection, SelectionInfo,
)
from pixelref.plugin_api.ui import CanvasView, HANDLED, InputHandler, KeyEvent, PointerEvent, WheelEvent, icon, theme
from pixelref.plugin_api.ui import widgets as w

from . import (
    DEFAULT_SIZE, PRESET_VIEWS, PRIMITIVE_LABELS, PRIMITIVES, PROJECTIONS, SHADING_LABELS,
    SHADING_MODES, SUPPORTED_MODEL_EXTS, SUPPORTED_TEXTURE_EXTS, TYPE,
)
from .items import Model3DItem, _rgb_tuple, _rgba_tuple
from .renderer import light_vector

_EDIT_3D_KEYS = frozenset({
    Qt.Key_Escape,
    Qt.Key_1,
    Qt.Key_3,
    Qt.Key_7,
    Qt.Key_0,
    Qt.Key_5,
    Qt.Key_R,
    Qt.Key_P,
    Qt.Key_T,
    Qt.Key_W,
    Qt.Key_Z,
})
_NON_KEYPAD_MODS = Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier | Qt.ShiftModifier


def _normalize_deg(angle: float) -> float:
    val = (angle + 180.0) % 360.0 - 180.0
    return round(val, 2)


class _Edit3DShortcutFilter(QObject):
    """Claims 3D View Mode single-key shortcuts on the canvas before window QShortcuts."""

    def __init__(self, service: "Model3DService") -> None:
        super().__init__()
        self.service = service

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.ShortcutOverride:
            return False
        if self.service.editing_item() is None:
            return False
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return False
        focused = QApplication.focusWidget()
        if focused is not None and not isinstance(focused, CanvasView):
            parent = focused.parentWidget()
            if not isinstance(parent, CanvasView):
                return False
        if not (event.modifiers() & _NON_KEYPAD_MODS) and event.key() in _EDIT_3D_KEYS:
            event.accept()
            return True
        return False


class Model3DService:
    """Tracks interactive 3D View Mode across item rebuilds and coordinates UI updates."""

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.session = ui.session
        self.editing_name: str | None = None
        self._shortcut_filter = _Edit3DShortcutFilter(self)
        self._filter_installed = False

    def _sync_shortcut_filter(self, active: bool) -> None:
        app = QApplication.instance()
        if app is None:
            return
        if active and not self._filter_installed:
            app.installEventFilter(self._shortcut_filter)
            self._filter_installed = True
        elif not active and self._filter_installed:
            app.removeEventFilter(self._shortcut_filter)
            self._filter_installed = False

    def item(self, name: str | None) -> Model3DItem | None:
        if not name:
            return None
        it = self.ui.canvas.item(name)
        return it if isinstance(it, Model3DItem) else None

    def selected_models(self) -> list[str]:
        scene = self.session.scene
        return [n for n in self.session.selection.names if scene.has(n) and scene.get_node(n).node_type == TYPE]

    def editing_item(self) -> Model3DItem | None:
        if not self.editing_name:
            return None
        if not self.session.scene.has(self.editing_name):
            self.editing_name = None
            self._sync_shortcut_filter(False)
            return None
        it = self.item(self.editing_name)
        if it is None or it.locked or not it.isVisible():
            self.editing_name = None
            self._sync_shortcut_filter(False)
            return None
        return it

    def set_editing(self, name: str | None, on: bool = True) -> bool:
        prev = self.editing_item()
        if not on or not name:
            self.editing_name = None
            self._sync_shortcut_filter(False)
            if prev is not None:
                prev.set_editing(False)
            self.ui.app.update_views()
            return False
        target = self.item(name)
        if target is None or target.locked:
            return False
        if prev is not None and prev is not target:
            prev.set_editing(False)
        self.editing_name = name
        target.set_editing(True)
        self._sync_shortcut_filter(True)
        if name not in self.session.selection.name_set:
            self.ui.app.run_command("select", [name])
        self.ui.status(
            "3D View Mode · Drag: orbit · Shift+Drag / Middle: pan · Alt+Drag: light · Wheel: zoom · Esc: exit"
        )
        self.ui.app.update_views()
        return True

    def cancel_editing(self) -> bool:
        if self.editing_item() is not None:
            self.set_editing(None, False)
            self.ui.status("Exited 3D View Mode")
            return True
        return False

    def toggle_editing(self, name: str | None = None) -> bool:
        target = name
        if target is None:
            models = self.selected_models()
            target = models[0] if models else None
        if not target:
            return False
        if self.editing_name == target and self.editing_item() is not None:
            return self.set_editing(None, False)
        return self.set_editing(target, True)


# ---- Interactive 3D View Mode Input Handler (priority 920) --------------------------------------


class Model3DInputHandler(InputHandler):
    """Handles orbit, pan, zoom, light rotation and shortcuts while a 3D item is in 3D View Mode."""

    def __init__(self, ui: Any, service: Model3DService) -> None:
        self.ui = ui
        self.service = service
        self.op: dict[str, Any] | None = None

    def _inside_item(self, view: Any, item: Model3DItem, viewport_pos: QPointF) -> bool:
        scene_pt = view.to_scene(viewport_pos)
        local_pt = item.mapFromScene(scene_pt)
        return item.content_rect().contains(local_pt)

    def pointer(self, e: PointerEvent) -> str | None:
        item = self.service.editing_item()
        if item is None:
            self.op = None
            return None

        if e.kind == "double" and e.button == Qt.LeftButton:
            if self._inside_item(e.view, item, e.pos):
                self.service.set_editing(None, False)
                self.ui.status("Exited 3D View Mode")
                return HANDLED
            return None

        if e.kind == "press":
            if not self._inside_item(e.view, item, e.pos):
                # Clicking outside the 3D item exits 3D View Mode and lets the click proceed.
                if e.button == Qt.LeftButton:
                    self.service.set_editing(None, False)
                return None

            attrs = item.current_attrs()
            if e.button == Qt.MiddleButton or (e.button == Qt.LeftButton and e.shift):
                mode = "pan"
                e.view.viewport().setCursor(Qt.SizeAllCursor)
            elif e.button == Qt.LeftButton and e.alt:
                mode = "light"
                e.view.viewport().setCursor(Qt.CrossCursor)
            elif e.button == Qt.RightButton or (e.button == Qt.LeftButton and e.ctrl):
                mode = "zoom"
                e.view.viewport().setCursor(Qt.SizeVerCursor)
            elif e.button == Qt.LeftButton:
                mode = "orbit"
                e.view.viewport().setCursor(Qt.ClosedHandCursor)
            else:
                return None

            self.op = {
                "mode": mode,
                "name": item.node_name,
                "start_pos": QPointF(e.pos),
                "start_attrs": dict(attrs),
                "updates": {},
                "button": e.button,
            }
            item.set_interactive_drag(True)
            return HANDLED

        op = self.op
        if op is None:
            return None

        if e.kind == "move":
            dx = e.pos.x() - op["start_pos"].x()
            dy = e.pos.y() - op["start_pos"].y()
            start = op["start_attrs"]
            mode = op["mode"]
            updates: dict[str, Any] = {}

            if mode == "orbit":
                yaw = _normalize_deg(float(start.get("yaw", 35.0)) - dx * 0.6)
                pitch = max(-89.5, min(89.5, round(float(start.get("pitch", 20.0)) + dy * 0.6, 2)))
                updates = {"yaw": yaw, "pitch": pitch}
                self.ui.status(f"3D Orbit: yaw {yaw:.0f}°, pitch {pitch:.0f}°")
            elif mode == "pan":
                dist = max(0.25, float(start.get("distance", 2.6)))
                scale = dist / max(120.0, min(item.content_rect().width(), item.content_rect().height()))
                t0 = start.get("target", [0.0, 0.0])
                tx = round(float(t0[0]) + dx * scale, 4)
                ty = round(float(t0[1]) - dy * scale, 4)
                updates = {"target": [tx, ty]}
                self.ui.status(f"3D Pan: {tx:.2f}, {ty:.2f}")
            elif mode == "light":
                az = _normalize_deg(float(start.get("light_azimuth", 45.0)) + dx * 0.7)
                el = max(-90.0, min(90.0, round(float(start.get("light_elevation", 35.0)) - dy * 0.7, 2)))
                updates = {"light_azimuth": az, "light_elevation": el}
                _lx, _ly, lz = light_vector(az, el)
                cue = "front" if lz >= 0.0 else "behind"
                self.ui.status(f"3D Key Light: azimuth {az:.0f}°, elevation {el:.0f}° ({cue})")
            elif mode == "zoom":
                d0 = max(0.25, float(start.get("distance", 2.6)))
                dist = max(0.25, min(30.0, round(d0 * (1.006 ** dy), 3)))
                updates = {"distance": dist}
                self.ui.status(f"3D Zoom distance: {dist:.2f}")

            op["updates"] = updates
            item.preview_attrs(updates, interactive=True)
            e.view.viewport().update()
            return HANDLED

        if e.kind == "release":
            self.op = None
            item.set_interactive_drag(False)
            e.view.viewport().setCursor(Qt.OpenHandCursor)
            updates = op.get("updates", {})
            start = op["start_attrs"]
            changed = {k: v for k, v in updates.items() if differs(start.get(k), v)}
            if changed:
                labels = {
                    "orbit": "Orbit 3D view",
                    "pan": "Pan 3D view",
                    "light": "Rotate 3D light",
                    "zoom": "Zoom 3D view",
                }
                self.ui.app.run_command("model3d.set_attrs", changed, [op["name"]], labels.get(op["mode"], "Edit 3D"))
            else:
                item.update()
            e.view.viewport().update()
            return HANDLED

        return None

    def hover(self, e: PointerEvent) -> None:
        item = self.service.editing_item()
        if item is not None and self._inside_item(e.view, item, e.pos):
            e.set_cursor(Qt.OpenHandCursor)

    def wheel(self, e: WheelEvent) -> str | None:
        item = self.service.editing_item()
        if item is None or not self._inside_item(e.view, item, e.pos):
            return None
        attrs = item.current_attrs()
        d0 = max(0.25, float(attrs.get("distance", 2.6)))
        dist = max(0.25, min(30.0, round(d0 * (0.88 ** e.steps), 3)))
        if differs(d0, dist):
            self.ui.canvas.run_coalesced(
                "Zoom 3D view",
                "model3d.set_attrs",
                {"distance": dist},
                [item.node_name],
                "Zoom 3D view",
            )
            self.ui.status(f"3D Zoom distance: {dist:.2f}")
        return HANDLED

    def key(self, e: KeyEvent) -> str | None:
        item = self.service.editing_item()
        if item is None or e.kind != "press":
            return None
        name = item.node_name
        if e.key == Qt.Key_Escape:
            self.service.cancel_editing()
            return HANDLED
        no_mods = not (e.modifiers & _NON_KEYPAD_MODS)
        preset_keys = {
            Qt.Key_1: "front",
            Qt.Key_3: "right",
            Qt.Key_7: "top",
            Qt.Key_0: "three_quarter",
            Qt.Key_R: "reset",
        }
        if e.key in preset_keys and no_mods:
            self.ui.app.run_command("model3d.set_preset_view", preset_keys[e.key], [name])
            return HANDLED
        if e.key in (Qt.Key_5, Qt.Key_P) and no_mods:
            self.ui.app.run_command("model3d.toggle_projection", [name])
            return HANDLED
        if e.key in (Qt.Key_W, Qt.Key_Z) and no_mods:
            self.ui.app.run_command("model3d.toggle_wireframe", [name])
            return HANDLED
        if e.key == Qt.Key_T and no_mods:
            self.ui.app.run_command("model3d.toggle_texture", [name])
            return HANDLED
        return None


# ---- Gizmo Viewport Box Resize Handles ----------------------------------------------------------


class Model3DHandles:
    """Edge handles resize the 3D viewport box; corner handles scale the item uniformly."""

    node_type = TYPE
    labels = {"model3d_size": "Resize 3D viewport"}

    def build(self, gizmo: Any, view: Any, item: Any, g: dict[str, Any]) -> dict[str, Any] | None:
        if getattr(item, "editing", False):
            return {"handles_vp": {}, "corners_vp": g["corners_vp"], "targets": g["targets"], "single": True}
        return None

    def start(self, gizmo: Any, handle: str, g: dict[str, Any], item: Any) -> str | None:
        if getattr(item, "editing", False):
            return None
        return "model3d_size" if handle in ("ml", "mr", "tm", "bm") else None

    def drag(self, gizmo: Any, op: dict[str, Any], m: QPointF, modifiers: Any, view: Any) -> None:
        item, s0, l0, _parent_inv, _rotation = op["targets"][0]
        start = op["start_rect"]
        m_local = s0.inverted()[0].map(m)
        handle = op["handle"]
        width, height = start.width(), start.height()
        shift_x, shift_y = 0.0, 0.0
        if handle == "mr":
            width = max(item.MIN_SIZE, m_local.x() - start.left())
        elif handle == "ml":
            width = max(item.MIN_SIZE, start.right() - m_local.x())
            shift_x = start.width() - width
        elif handle == "bm":
            height = max(item.MIN_SIZE, m_local.y() - start.top())
        elif handle == "tm":
            height = max(item.MIN_SIZE, start.bottom() - m_local.y())
            shift_y = start.height() - height
        item.set_viewport_size(width, height)
        if shift_x or shift_y:
            item.set_local_transform(QTransform.fromTranslate(shift_x, shift_y) * l0)
        item._notify_moved()
        gizmo.ui.status(f"3D viewport {width:.0f} × {height:.0f} px")


# ---- Floating Bar & Quick Editor Controls -------------------------------------------------------

BAR_BUTTONS: tuple[tuple[str, str, str], ...] = (
    ("orbit", "3D", "Enter / exit interactive 3D View Mode (double-click)"),
    ("front", "F", "Front view (1)"),
    ("right", "S", "Right side view (3)"),
    ("top", "T", "Top view (7)"),
    ("three_quarter", "3/4", "Three-quarter view (0)"),
    ("reset", "R", "Reset 3D camera (R)"),
    ("shading", "Shade", "Cycle 3D shading mode (Studio / Clay / MatCap / Flat / Normals / Wire / Silhouette)"),
    ("tex", "Tex", "Toggle 3D surface texture (T)"),
    ("wire", "Wire", "Toggle wireframe topology overlay (W)"),
    ("proj", "Ortho", "Toggle Perspective / Orthographic camera (P)"),
)


def _trigger_bar_button(ui: Any, service: Model3DService, key: str, names: list[str]) -> None:
    if not names:
        return
    if key == "orbit":
        service.toggle_editing(names[0])
    elif key in PRESET_VIEWS:
        ui.app.run_command("model3d.set_preset_view", key, names)
    elif key == "shading":
        attrs = ui.session.scene.get_node(names[0]).attrs
        cur = str(attrs.get("shading", "studio"))
        idx = (SHADING_MODES.index(cur) + 1) % len(SHADING_MODES) if cur in SHADING_MODES else 0
        ui.app.run_command("model3d.set_shading", SHADING_MODES[idx], names)
    elif key == "tex":
        ui.app.run_command("model3d.toggle_texture", names)
    elif key == "wire":
        ui.app.run_command("model3d.toggle_wireframe", names)
    elif key == "proj":
        ui.app.run_command("model3d.toggle_projection", names)


class Model3DQuickControls(QWidget):
    """Controls in the Quick Editor bar when 3D object(s) are selected."""

    def __init__(self, ui: Any, service: Model3DService, parent: QWidget) -> None:
        super().__init__(parent)
        self.ui = ui
        self.service = service
        self.names: list[str] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.buttons: dict[str, Any] = {}
        for key, text, tip in BAR_BUTTONS:
            checkable = key in ("orbit", "tex", "wire", "proj")
            width = 40 if len(text) >= 4 else 28
            btn = w.make_tool_button(text, tip, checkable=checkable, width=width, parent=self)
            btn.clicked.connect(lambda _c=False, k=key: _trigger_bar_button(ui, service, k, list(self.names)))
            layout.addWidget(btn)
            self.buttons[key] = btn

    def refresh(self, names: list[str]) -> None:
        scene = self.ui.session.scene
        self.names = [n for n in names if scene.has(n) and scene.get_node(n).node_type == TYPE]
        if not self.names:
            return
        attrs = scene.get_node(self.names[0]).attrs
        self.buttons["orbit"].setChecked(self.service.editing_name == self.names[0])
        self.buttons["tex"].setChecked(bool(attrs.get("show_texture", True)))
        self.buttons["wire"].setChecked(bool(attrs.get("wireframe_overlay", False)))
        self.buttons["proj"].setChecked(attrs.get("projection", "perspective") == "orthographic")
        cur_shade = str(attrs.get("shading", "studio"))
        self.buttons["shading"].setText(SHADING_LABELS.get(cur_shade, cur_shade).split()[0])


class Model3DControls(InputHandler):
    """Floating clickable bar below selected 3D items while the Quick Editor is off."""

    BTN_H = 24.0
    GAP = 10.0

    def __init__(self, ui: Any, service: Model3DService) -> None:
        self.ui = ui
        self.service = service
        self.hovered: tuple[Any, str, str] | None = None
        self.pressed: tuple[Any, str, str] | None = None

    def _in_quick_editor(self) -> bool:
        quick_edit = self.ui.service("quick_edit")
        return quick_edit is not None and quick_edit.enabled

    def _font(self) -> QFont:
        font = QFont()
        font.setPixelSize(11)
        return font

    def layout(self, view: Any) -> list[tuple[str, Model3DItem, dict[str, tuple[QRectF, str, bool]], QRectF]]:
        if not view.display.show_overlays or self._in_quick_editor():
            return []
        metrics = QFontMetricsF(self._font())
        bars = []
        viewport = QRectF(view.viewport().rect())
        for name in self.service.selected_models():
            item = self.service.item(name)
            if item is None or not item.isVisible() or not view.display.shows(name):
                continue
            try:
                rect = view.mapFromScene(item.sceneBoundingRect()).boundingRect()
            except RuntimeError:
                continue
            attrs = item.current_attrs()
            cur_shade = SHADING_LABELS.get(str(attrs.get("shading", "studio")), "Studio").split()[0]
            specs: list[tuple[str, str, bool, float]] = []
            for key, text, _tip in BAR_BUTTONS:
                label = cur_shade if key == "shading" else text
                active = (
                    (key == "orbit" and item.editing)
                    or (key == "tex" and bool(attrs.get("show_texture", True)) and item.has_texture)
                    or (key == "wire" and bool(attrs.get("wireframe_overlay", False)))
                    or (key == "proj" and attrs.get("projection", "perspective") == "orthographic")
                )
                w_px = max(24.0, metrics.horizontalAdvance(label) + 12.0)
                specs.append((key, label, active, w_px))
            total_w = sum(s[3] for s in specs) + 2.0 * (len(specs) - 1) + 8.0
            bar = QRectF(0.0, 0.0, total_w, self.BTN_H + 4.0)
            bar.moveTopLeft(QPointF(rect.center().x() - total_w * 0.5, rect.bottom() + self.GAP))
            if bar.bottom() > viewport.bottom() - 4.0:
                bar.moveBottom(min(viewport.bottom() - 4.0, max(rect.top() + bar.height(), viewport.top() + bar.height())))
            bar.moveLeft(max(viewport.left() + 4.0, min(bar.left(), viewport.right() - total_w - 4.0)))
            buttons: dict[str, tuple[QRectF, str, bool]] = {}
            x = bar.left() + 4.0
            for key, label, active, w_px in specs:
                buttons[key] = (QRectF(x, bar.top() + 2.0, w_px, self.BTN_H), label, active)
                x += w_px + 2.0
            bars.append((name, item, buttons, bar))
        return bars

    def _hit(self, view: Any, pos: QPointF) -> tuple[str, str] | bool | None:
        for name, _item, buttons, bar in self.layout(view):
            for key, (rect, _label, _active) in buttons.items():
                if rect.contains(pos):
                    return name, key
            if bar.contains(pos):
                return True
        return None

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind in ("press", "double") and e.button == Qt.LeftButton:
            hit = self._hit(e.view, e.pos)
            if hit is None:
                return None
            if isinstance(hit, tuple):
                self.pressed = (e.view, *hit)
                name, key = hit
                _trigger_bar_button(self.ui, self.service, key, [name])
                e.view.viewport().update()
            return HANDLED
        if e.kind == "release" and self.pressed is not None:
            self.pressed = None
            e.view.viewport().update()
            return HANDLED
        return None

    def hover(self, e: PointerEvent) -> None:
        hit = self._hit(e.view, e.pos)
        hovered = (e.view, *hit) if isinstance(hit, tuple) else None
        if hit is not None:
            e.set_cursor(Qt.PointingHandCursor if hovered else Qt.ArrowCursor)
            if hovered:
                tip = next(t for k, _l, t in BAR_BUTTONS if k == hovered[2])
                self.ui.status(tip)
        if hovered != self.hovered:
            self.hovered = hovered
            e.view.viewport().update()

    def leave(self, view: Any) -> None:
        if self.hovered is not None:
            self.hovered = None
            view.viewport().update()

    def paint(self, view: Any, painter: QPainter) -> None:
        bars = self.layout(view)
        if not bars:
            return
        font = self._font()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(font)
        for name, _item, buttons, bar in bars:
            painter.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
            painter.setBrush(QColor(20, 24, 32, 235))
            painter.drawRoundedRect(bar, 7.0, 7.0)
            for key, (rect, label, active) in buttons.items():
                state = (view, name, key)
                if self.pressed == state or active:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(theme.ACCENT))
                    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 5.0, 5.0)
                elif self.hovered == state:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(255, 255, 255, 40))
                    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 5.0, 5.0)
                painter.setPen(QColor("#ffffff") if (active or self.pressed == state) else QColor(theme.TEXT_LIGHT))
                painter.drawText(rect, Qt.AlignCenter, label)
        painter.restore()


# ---- Attribute Editor Section & Storage Rows ----------------------------------------------------


class Model3DSection(w.InspectorSection):
    """Attribute Editor controls for 3D camera, shading and lighting."""

    def __init__(self, ui: Any, service: Model3DService, parent: QWidget) -> None:
        super().__init__("model3d", "3D View & Shading", parent)
        self.ui = ui
        self.service = service
        self._updating = False
        self._names: list[str] = []
        body = self.body

        self.orbit_btn = w.make_tool_button(
            "3D Orbit Mode", "Enter interactive 3D View Mode on the canvas (double-click)", checkable=True,
            width=104, parent=body,
        )
        self.orbit_btn.setIcon(icon("orbit"))
        self.reset_btn = w.make_tool_button("Reset Camera", "Reset 3D camera orbit, pan and zoom", width=92, parent=body)
        self.form.addRow(w.hrow(body, self.orbit_btn, self.reset_btn))

        self.preset_btns: dict[str, Any] = {}
        for key, label, tip in (
            ("front", "Front", "Front view"),
            ("right", "Right", "Right side view"),
            ("top", "Top", "Top view"),
            ("three_quarter", "3/4", "Three-quarter view"),
            ("back", "Back", "Back view"),
        ):
            btn = w.make_tool_button(label, tip, width=42, parent=body)
            btn.clicked.connect(lambda _c=False, k=key: self._names and ui.app.run_command("model3d.set_preset_view", k, self._names))
            self.preset_btns[key] = btn
        self.form.addRow("View", w.hrow(body, *self.preset_btns.values()))

        self.shading = w.make_combo(tuple((k, SHADING_LABELS[k]) for k in SHADING_MODES), body)
        self.show_texture = QCheckBox("Show model texture", body)
        self.wireframe = QCheckBox("Wireframe topology overlay", body)
        self.projection = w.make_combo((("perspective", "Perspective"), ("orthographic", "Orthographic")), body)
        self.fov = w.make_spin(body, minimum=5.0, maximum=120.0, step=1.0, default=45.0, soft_range=(15.0, 90.0))
        self.distance = w.make_spin(body, minimum=0.25, maximum=30.0, step=0.1, default=2.6, soft_range=(0.8, 8.0))
        self.yaw = w.make_spin(body, minimum=-180.0, maximum=180.0, step=5.0, default=35.0, soft_range=(-180.0, 180.0))
        self.pitch = w.make_spin(body, minimum=-89.5, maximum=89.5, step=5.0, default=20.0, soft_range=(-89.5, 89.5))

        self.light_az = w.make_spin(body, minimum=-180.0, maximum=180.0, step=5.0, default=45.0, soft_range=(-180.0, 180.0))
        self.light_el = w.make_spin(body, minimum=-90.0, maximum=90.0, step=5.0, default=35.0, soft_range=(-90.0, 90.0))
        self.light_int = w.make_spin(body, minimum=0.0, maximum=3.0, step=0.05, default=1.0, soft_range=(0.0, 2.0))
        self.ambient = w.make_spin(body, minimum=0.0, maximum=1.0, step=0.05, default=0.25, soft_range=(0.0, 1.0))

        self.base_color_btn = w.make_tool_button("Surface…", "Pick surface / clay color", width=68, parent=body)
        self.bg_color_btn = w.make_tool_button("Backdrop…", "Pick viewport background color", width=72, parent=body)
        self.clear_bg_btn = w.make_tool_button("Cutout", "Transparent background (alpha = 0)", width=54, parent=body)
        self.load_tex_btn = w.make_tool_button("Load Texture…", "Load an image file as the 3D surface texture", width=96, parent=body)
        self.clear_tex_btn = w.make_tool_button("Clear Tex", "Clear custom texture override", width=68, parent=body)

        self.form.addRow("Shading", self.shading)
        self.form.addRow(self.show_texture)
        self.form.addRow(self.wireframe)
        self.form.addRow("Texture", w.hrow(body, self.load_tex_btn, self.clear_tex_btn))
        self.form.addRow("Colors", w.hrow(body, self.base_color_btn, self.bg_color_btn, self.clear_bg_btn))
        self.form.addRow("Camera", self.projection)
        self.form.addRow("FOV / Dist", w.hrow(body, self.fov, self.distance))
        self.form.addRow("Yaw / Pitch", w.hrow(body, self.yaw, self.pitch))
        self.form.addRow("Light Dir", w.hrow(body, self.light_az, self.light_el))
        self.form.addRow("Key / Amb", w.hrow(body, self.light_int, self.ambient))

        self.orbit_btn.clicked.connect(self._toggle_orbit)
        self.reset_btn.clicked.connect(lambda: self._names and ui.app.run_command("model3d.set_preset_view", "reset", self._names))
        self.shading.currentIndexChanged.connect(lambda _i: self._set({"shading": self.shading.currentData()}, "3D shading"))
        self.show_texture.toggled.connect(lambda on: self._set({"show_texture": on}, "3D texture"))
        self.wireframe.toggled.connect(lambda on: self._set({"wireframe_overlay": on}, "3D wireframe"))
        self.projection.currentIndexChanged.connect(lambda _i: self._set({"projection": self.projection.currentData()}, "3D projection"))
        self.fov.editingFinished.connect(lambda: self._set({"fov": round(self.fov.value(), 1)}, "3D FOV"))
        self.distance.editingFinished.connect(lambda: self._set({"distance": round(self.distance.value(), 3)}, "3D distance"))
        self.yaw.editingFinished.connect(lambda: self._set({"yaw": round(self.yaw.value(), 2)}, "3D yaw"))
        self.pitch.editingFinished.connect(lambda: self._set({"pitch": round(self.pitch.value(), 2)}, "3D pitch"))
        self.light_az.editingFinished.connect(lambda: self._set({"light_azimuth": round(self.light_az.value(), 2)}, "3D light"))
        self.light_el.editingFinished.connect(lambda: self._set({"light_elevation": round(self.light_el.value(), 2)}, "3D light"))
        self.light_int.editingFinished.connect(lambda: self._set({"light_intensity": round(self.light_int.value(), 2)}, "3D light"))
        self.ambient.editingFinished.connect(lambda: self._set({"ambient": round(self.ambient.value(), 2)}, "3D ambient"))
        self.base_color_btn.clicked.connect(self._pick_base_color)
        self.bg_color_btn.clicked.connect(self._pick_bg_color)
        self.clear_bg_btn.clicked.connect(lambda: self._set({"background_color": [24, 27, 34, 0]}, "Transparent 3D background"))
        self.load_tex_btn.clicked.connect(lambda: ui.app.trigger("model3d.set_texture_dialog"))
        self.clear_tex_btn.clicked.connect(lambda: ui.app.trigger("model3d.clear_texture_selected"))

    def _toggle_orbit(self) -> None:
        if self._names:
            on = self.service.toggle_editing(self._names[0])
            self.orbit_btn.setChecked(on)

    def _set(self, updates: dict[str, Any], label: str) -> None:
        if self._updating or not self._names:
            return
        self.ui.app.run_command("model3d.set_attrs", updates, self._names, label)

    def _pick_base_color(self) -> None:
        if not self._names:
            return
        attrs = self.ui.session.scene.get_node(self._names[0]).attrs
        r, g, b = _rgb_tuple(attrs.get("base_color", [196, 200, 208]))
        chosen = QColorDialog.getColor(QColor(r, g, b), self.ui.app.main_window(), "3D Surface Color")
        if chosen.isValid():
            self._set({"base_color": [chosen.red(), chosen.green(), chosen.blue()]}, "3D surface color")

    def _pick_bg_color(self) -> None:
        if not self._names:
            return
        attrs = self.ui.session.scene.get_node(self._names[0]).attrs
        r, g, b, a = _rgba_tuple(attrs.get("background_color", [24, 27, 34, 0]))
        chosen = QColorDialog.getColor(
            QColor(r, g, b, max(255 if a == 0 else a, 0)),
            self.ui.app.main_window(),
            "3D Viewport Background",
            QColorDialog.ShowAlphaChannel,
        )
        if chosen.isValid():
            self._set(
                {"background_color": [chosen.red(), chosen.green(), chosen.blue(), chosen.alpha()]},
                "3D background color",
            )

    def refresh(self, info: SelectionInfo) -> None:
        self._names = [n["name"] for n in info.nodes if n["type"] == TYPE]
        attrs = info.nodes[0]["attrs"] if info.nodes else {}
        self._updating = True
        try:
            self.orbit_btn.setChecked(bool(self._names and self.service.editing_name == self._names[0]))
            w.set_combo(self.shading, attrs.get("shading", "studio"))
            self.show_texture.setChecked(bool(attrs.get("show_texture", True)))
            self.clear_tex_btn.setEnabled(bool(attrs.get("texture_path") or attrs.get("texture_asset")))
            self.wireframe.setChecked(bool(attrs.get("wireframe_overlay", False)))
            w.set_combo(self.projection, attrs.get("projection", "perspective"))
            self.fov.setValue(float(attrs.get("fov", 45.0)))
            self.fov.setEnabled(attrs.get("projection", "perspective") == "perspective")
            self.distance.setValue(float(attrs.get("distance", 2.6)))
            self.yaw.setValue(float(attrs.get("yaw", 35.0)))
            self.pitch.setValue(float(attrs.get("pitch", 20.0)))
            self.light_az.setValue(float(attrs.get("light_azimuth", 45.0)))
            self.light_el.setValue(float(attrs.get("light_elevation", 35.0)))
            self.light_int.setValue(float(attrs.get("light_intensity", 1.0)))
            self.ambient.setValue(float(attrs.get("ambient", 0.25)))
        finally:
            self._updating = False


class Model3DStorageRows(QWidget):
    """Storage controls in the Attribute Editor (linked file vs embedded in .pxref)."""

    def __init__(self, ui: Any, parent: QWidget) -> None:
        super().__init__(parent)
        self.ui = ui
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        title = QLabel("3D Storage", self)
        title.setObjectName("fieldCaption")
        self.path_label = QLabel("", self)
        self.path_label.setObjectName("pathLabel")
        self.path_label.setWordWrap(True)
        self.embed_btn = QPushButton("Embed", self)
        self.embed_btn.setToolTip("Copy the linked 3D file into the scene file (.pxref)")
        self.extract_btn = QPushButton("Save as file", self)
        self.extract_btn.setToolTip("Write the embedded 3D model to disk and link to it")
        self.link_btn = QPushButton("Link…", self)
        self.link_btn.setToolTip("Point at a 3D file on disk (.obj, .glb, .gltf, .stl, .ply)")
        for key, button in (
            ("model3d.embed_selected", self.embed_btn),
            ("model3d.extract_dialog", self.extract_btn),
            ("model3d.link_dialog", self.link_btn),
        ):
            button.setObjectName("toolBtn")
            button.clicked.connect(lambda _checked=False, k=key: ui.app.trigger(k))
        layout.addWidget(title)
        layout.addWidget(self.path_label)
        layout.addWidget(w.hrow(self, self.embed_btn, self.extract_btn, self.link_btn))

    def refresh(self, info: SelectionInfo) -> None:
        attrs = info.attrs
        session = self.ui.session
        asset = session.scene.get_asset(str(attrs.get("asset", "") or ""))
        if asset is not None:
            label = f" · {asset.name}" if asset.name else ""
            text = f"Embedded in scene · {w.format_size(asset.size)}{label}"
        elif attrs.get("path"):
            path_value = str(attrs.get("path", ""))
            text = f"Linked file · {path_value}{session.links.note(path_value)}"
        else:
            prim = str(attrs.get("primitive", "") or "loomis_head")
            text = f"Built-in primitive · {PRIMITIVE_LABELS.get(prim, prim)}"
        self.path_label.setText(text)
        embedded = bool(attrs.get("asset"))
        self.embed_btn.setVisible(not embedded and bool(attrs.get("path")))
        self.extract_btn.setVisible(embedded)


# ---- Plugin UI Activation -----------------------------------------------------------------------


def activate_model3d_ui(ui: Any) -> None:
    session = ui.session
    canvas = ui.canvas
    app = ui.app
    api = session.services.require("models3d")
    service = Model3DService(ui)
    ui.provide_service("model3d", service)

    def factory(_canvas: Any, node: dict[str, Any]) -> Model3DItem:
        item = Model3DItem(node["name"], session)
        attrs = node.get("attrs", {})
        item.apply_attrs(attrs)
        item.cache_key = (attrs.get("asset") or attrs.get("path") or attrs.get("primitive"), TYPE)
        if service.editing_name == node["name"]:
            item.set_editing(True)
        return item

    def post_sync(item: Any, node: dict[str, Any]) -> None:
        if isinstance(item, Model3DItem):
            item.set_editing(service.editing_name == node["name"])

    def describe(name: str, node: dict[str, Any]) -> list[str]:
        attrs = node.get("attrs", {})
        parts: list[str] = []
        item = canvas.item(name)
        if isinstance(item, Model3DItem):
            if item.is_loading():
                progress = item.load_progress()
                parts.append(f"loading… {round(progress[0] * 100)}%" if progress else "loading…")
            elif item.is_missing():
                parts.append("missing")
            elif item.mesh is not None:
                parts.append(item.mesh.format_name)
                parts.append(f"{item.mesh.vertex_count:,} verts · {item.mesh.face_count:,} tris")
                if item.has_texture:
                    parts.append("textured")
        if attrs.get("asset"):
            parts.append("embedded")
        elif attrs.get("path"):
            parts.append("linked file")
        else:
            prim = str(attrs.get("primitive", "") or "loomis_head")
            parts.append(PRIMITIVE_LABELS.get(prim, prim))
        return parts

    def double_click(_view: Any, item: Any) -> bool:
        if not isinstance(item, Model3DItem) or item.locked:
            return False
        service.toggle_editing(item.node_name)
        return True

    ui.node_ui(
        TYPE,
        factory=factory,
        label="3D Object",
        icon="cube",
        badge=("#d48b5c", "3D"),
        describe=describe,
        post_sync=post_sync,
        double_click=double_click,
    )

    # Input handlers & overlays
    ui.input_handler(Model3DInputHandler(ui, service), 920)
    controls = Model3DControls(ui, service)
    ui.overlay(controls.paint, order=66)
    ui.input_handler(controls, 705)
    ui.contribute("gizmo.handles", Model3DHandles())

    ui.contribute("quick_edit.sections", QuickSection(
        key="model3d", title="3D Controls", order=11,
        applies=lambda names: any(
            session.scene.has(n) and session.scene.get_node(n).node_type == TYPE for n in names
        ),
        build=lambda parent: Model3DQuickControls(ui, service, parent),
    ))
    ui.contribute("inspector.sections", InspectorSection(
        key="model3d", title="3D View & Shading", order=30,
        applies=lambda info: bool(info.nodes) and all(n["type"] == TYPE for n in info.nodes),
        build=lambda parent, _inspector: Model3DSection(ui, service, parent),
    ))
    ui.contribute("inspector.rows", InspectorRows(TYPE, 50, lambda parent, _i: Model3DStorageRows(ui, parent)))

    # ---- File Ingest (Drag & Drop) --------------------------------------------------------------

    def local_paths(mime: Any) -> list[Path]:
        paths: list[Path] = []
        for url in mime.urls() if mime.hasUrls() else []:
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir():
                paths.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in SUPPORTED_MODEL_EXTS))
            elif path.suffix.lower() in SUPPORTED_MODEL_EXTS:
                paths.append(path)
        return paths

    def describe_files(mime: Any) -> str | None:
        if not mime.hasUrls():
            return None
        local = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        models = [f for f in local if Path(f).suffix.lower() in SUPPORTED_MODEL_EXTS]
        if models:
            mode = "embedded" if api.embed_new else "linked"
            return f"Drop to add {len(models)} 3D object{'s' if len(models) != 1 else ''} ({mode})"
        return None

    def ingest_files(_view: Any, pos: QPointF, mime: Any) -> Any:
        paths = local_paths(mime)
        if not paths:
            return None
        created = api.create_row(paths, pos.x(), pos.y())
        session.selection.set(created)
        return len(created), 0

    model_handler = IngestHandler("model3d.files", 16, lambda m: bool(m.hasUrls()), describe_files, ingest_files)
    ui.contribute("ingest.handlers", model_handler, order=model_handler.order)

    # ---- UI Dialogs, Commands & Actions ---------------------------------------------------------

    def insert_models_dialog() -> None:
        exts = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_MODEL_EXTS))
        paths, _ = QFileDialog.getOpenFileNames(
            app.main_window(), "Insert 3D Objects", str(session.base_dir()), f"3D Models ({exts})",
        )
        if not paths:
            return
        view = app.active_view()
        center = view.center() if view is not None else QPointF()
        names = app.run_command("model3d.import_models", paths, [center.x(), center.y()])
        if names:
            ui.status(f"Added {len(names)} 3D object(s) · double-click to orbit")

    def add_primitive_at_cursor(primitive: str = "loomis_head") -> str | None:
        pos = app.scene_pos_for_new_content()
        name = app.run_command("model3d.create_primitive", primitive, [pos.x(), pos.y()])
        if name:
            ui.status(f"Added {PRIMITIVE_LABELS.get(primitive, primitive)} · double-click to orbit")
        return name

    def link_dialog() -> None:
        models = service.selected_models()
        if len(models) != 1:
            ui.status("Select a single 3D object to link")
            return
        exts = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_MODEL_EXTS))
        path_str, _ = QFileDialog.getOpenFileName(
            app.main_window(), "Link 3D Object to File", str(session.base_dir()), f"3D Models ({exts})",
        )
        if path_str:
            app.run_command("model3d.link", models[0], path_str)

    def extract_dialog() -> None:
        models = service.selected_models()
        asset = (
            session.scene.get_asset(str(canvas.nodes[models[0]]["attrs"].get("asset", "") or ""))
            if len(models) == 1
            else None
        )
        if asset is None:
            ui.status("Select a single embedded 3D object")
            return
        default_name = asset.name or f"{models[0]}.obj"
        default_path = session.base_dir() / default_name
        path_str, _ = QFileDialog.getSaveFileName(app.main_window(), "Save 3D Object as File", str(default_path))
        if path_str and app.run_command("model3d.extract", models[0], path_str) is not None:
            ui.status(f"3D model saved to {path_str} and linked")

    def embed_selected() -> None:
        app.run_command("model3d.embed", service.selected_models())

    def toggle_embed_new() -> None:
        app.run_command("model3d.set_embed_new", not api.embed_new)
        app.actions.refresh_states()

    def set_texture_dialog() -> None:
        models = service.selected_models()
        if not models:
            ui.status("Select a 3D object to assign a texture")
            return
        exts = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_TEXTURE_EXTS))
        path_str, _ = QFileDialog.getOpenFileName(
            app.main_window(), "Load 3D Texture Image", str(session.base_dir()), f"Images ({exts})",
        )
        if path_str:
            app.run_command("model3d.set_texture", path_str, models)
            ui.status(f"Applied texture {Path(path_str).name}")

    def clear_texture_selected() -> None:
        models = service.selected_models()
        if models:
            app.run_command("model3d.clear_texture", models)
            ui.status("Cleared custom 3D texture override")

    ui.command("insert_dialog", insert_models_dialog, undoable=False, echo=False)
    ui.command("add_primitive", add_primitive_at_cursor, undoable=False, echo=False)
    ui.command("orbit_mode", lambda: service.toggle_editing(), undoable=False, echo=False)
    ui.command("link_dialog", link_dialog, undoable=False, echo=False)
    ui.command("extract_dialog", extract_dialog, undoable=False, echo=False)
    ui.command("embed_selected", embed_selected, undoable=False, echo=False)
    ui.command("toggle_embed_new", toggle_embed_new, undoable=False, echo=False)
    ui.command("set_texture_dialog", set_texture_dialog, undoable=False, echo=False)
    ui.command("clear_texture_selected", clear_texture_selected, undoable=False, echo=False)

    a = ui.action
    a("model3d.insert", "model3d.insert_dialog", "Insert 3D Objects…", icon="cube",
      keywords="add import 3d model mesh obj glb gltf stl ply reference", category="File")
    a("model3d.orbit", "model3d.orbit_mode", "3D Orbit View Mode", icon="orbit",
      keywords="rotate camera 3d orbit pan zoom light", category="3D",
      checked=lambda: service.editing_item() is not None)
    a("model3d.texture", "model3d.toggle_texture", "Toggle 3D Texture", icon="cube",
      keywords="texture uv image albedo diffuse 3d", category="3D")
    a("model3d.set_texture_dialog", "model3d.set_texture_dialog", "Load 3D Texture Image…", icon="image",
      keywords="texture uv image albedo diffuse load file 3d", category="3D")
    a("model3d.clear_texture_selected", "model3d.clear_texture_selected", "Clear Custom 3D Texture", icon="cube",
      keywords="texture clear remove 3d", category="3D")
    a("model3d.wireframe", "model3d.toggle_wireframe", "Toggle 3D Wireframe Overlay", icon="cube",
      keywords="edges topology mesh wireframe 3d", category="3D")
    a("model3d.projection", "model3d.toggle_projection", "Toggle 3D Perspective / Orthographic", icon="cube",
      keywords="camera ortho perspective fov 3d", category="3D")

    for prim in PRIMITIVES:
        label = PRIMITIVE_LABELS[prim]
        a(f"model3d.prim_{prim}", "model3d.add_primitive", f"Add 3D {label}", args=(prim,),
          keywords=f"3d primitive reference {label.lower()} mesh lighting anatomy", category="Create", icon="cube")

    for preset_key, preset_label in (
        ("front", "Front View"),
        ("right", "Right View"),
        ("left", "Left View"),
        ("top", "Top View"),
        ("bottom", "Bottom View"),
        ("three_quarter", "Three-Quarter View"),
        ("reset", "Reset 3D Camera"),
    ):
        a(f"model3d.view_{preset_key}", "model3d.set_preset_view", f"3D {preset_label}", args=(preset_key,),
          keywords=f"3d camera angle preset {preset_label.lower()}", category="3D", icon="orbit")

    for shade_key in SHADING_MODES:
        shade_label = SHADING_LABELS[shade_key]
        a(f"model3d.shade_{shade_key}", "model3d.set_shading", f"3D Shading: {shade_label}", args=(shade_key,),
          keywords=f"3d shading material {shade_label.lower()}", category="3D", icon="sun")

    a("model3d.embed_new", "model3d.toggle_embed_new", "Embed New 3D Models in Scene",
      keywords="package single file store 3d models", category="File", checked=lambda: api.embed_new, icon="embed")
    a("model3d.embed_all", "model3d.embed_all", "Embed All Linked 3D Models",
      keywords="package single file store 3d models", category="File", icon="embed")
    a("model3d.embed_selected", "model3d.embed_selected", "Embed 3D Model in Scene",
      category="3D", icon="embed")
    a("model3d.link_dialog", "model3d.link_dialog", "Link 3D Model to File…",
      category="3D", icon="link_file")
    a("model3d.extract_dialog", "model3d.extract_dialog", "Save 3D Model as File && Link…",
      category="3D", icon="save_as")

    ui.context_key("model3d.selected", lambda: bool(service.selected_models()))
    ui.context_key("model3d.editing", lambda: service.editing_item() is not None)
    ui.escape_handler(service.cancel_editing, order=0)
    ui.menu("canvas", "model3d.insert", path="File", group="file.import", order=3)
    ui.menu("canvas", "model3d.embed_new", path="File/Preferences", group="file.storage", order=1)
    ui.menu("canvas", "model3d.embed_all", path="File", group="file.import", order=11)

    for order, prim in enumerate(PRIMITIVES):
        ui.menu("canvas", f"model3d.prim_{prim}", path="3D Primitive", group="create.prim", order=order,
                when="selection.count == 0")
    ui.place_submenu("canvas", "3D Primitive", "create", 23, icon="cube", when="selection.count == 0")

    ui.menu("canvas", "model3d.orbit", path="3D Object", group="m3d.mode", order=0, when="model3d.selected")
    ui.menu("canvas", "model3d.texture", path="3D Object", group="m3d.mode", order=1, when="model3d.selected")
    ui.menu("canvas", "model3d.wireframe", path="3D Object", group="m3d.mode", order=2, when="model3d.selected")
    ui.menu("canvas", "model3d.projection", path="3D Object", group="m3d.mode", order=3, when="model3d.selected")
    ui.menu("canvas", "model3d.set_texture_dialog", path="3D Object", group="m3d.texture", order=0, when="model3d.selected")
    ui.menu("canvas", "model3d.clear_texture_selected", path="3D Object", group="m3d.texture", order=1, when="model3d.selected")
    for order, preset_key in enumerate(("front", "right", "left", "top", "bottom", "three_quarter", "reset")):
        ui.menu("canvas", f"model3d.view_{preset_key}", path="3D Object/Camera View", group="m3d.views", order=order,
                when="model3d.selected")
    for order, shade_key in enumerate(SHADING_MODES):
        ui.menu("canvas", f"model3d.shade_{shade_key}", path="3D Object/Shading", group="m3d.shading", order=order,
                when="model3d.selected")
    for order, key in enumerate(("model3d.embed_selected", "model3d.extract_dialog", "model3d.link_dialog")):
        ui.menu("canvas", key, path="3D Object", group="m3d.storage", order=order, when="model3d.selected")
    ui.place_submenu("canvas", "3D Object", "item", 2, icon="cube", when="model3d.selected")

    ui.contribute("palette.buttons", PaletteButton("model3d.insert", "3D", "cube", order=13))
