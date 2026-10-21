"""Built-in Attribute Editor sections: Transform, Appearance, generated attribute editors."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget

from pixelref.plugin_api import xform
from pixelref.plugin_api.contracts import SelectionInfo
from pixelref.plugin_api.ui import compose_transform
from pixelref.plugin_api.ui import widgets as w

CORE_ATTRS = {"translate", "rotate", "scale", "pivot", "visibility", "opacity", "locked", "tags", "note", "metadata"}


class TransformSection(w.InspectorSection):
    def __init__(self, ui: Any, parent: QWidget) -> None:
        super().__init__("transform", "Transform", parent)
        self.ui = ui
        self._updating = False
        self._name: str | None = None
        body = self.body
        spin = lambda **k: w.make_spin(body, **k)  # noqa: E731
        self.pos_x_spin = spin()
        self.pos_y_spin = spin()
        for each in (self.pos_x_spin, self.pos_y_spin):
            w.compact_spin(each, 1)
        self.width_spin = spin(minimum=0.1, maximum=1_000_000.0)
        self.height_spin = spin(minimum=0.1, maximum=1_000_000.0)
        for each in (self.width_spin, self.height_spin):
            w.compact_spin(each, 1)
            each.setToolTip("Size on the canvas in px (the scale is changed, not the image)")
        self.scale_pct_spin = spin(minimum=0.1, maximum=100_000.0, step=5.0, default=100.0)
        w.compact_spin(self.scale_pct_spin, 1, " %", 70)
        self.scale_pct_spin.setToolTip("Uniform scale (100% = original size)")
        self.rotation_spin = spin(minimum=-360.0, maximum=360.0, step=15.0)  # Ctrl-drag snaps to 15°
        w.compact_spin(self.rotation_spin, 1, "°", 60)
        # Hidden: the canonical scale values the transform code reads (full
        # precision and range, so untouched values round-trip exactly).
        self.scale_x_spin = spin(minimum=-100_000.0, maximum=100_000.0, step=0.05, default=1.0)
        self.scale_y_spin = spin(minimum=-100_000.0, maximum=100_000.0, step=0.05, default=1.0)
        for each in (self.scale_x_spin, self.scale_y_spin):
            each.setDecimals(6)
            each.hide()
        self.aspect_lock_btn = w.make_tool_button("🔗", "Keep proportions when changing width/height", True, 26, body)
        self.aspect_lock_btn.setChecked(True)
        self.flip_h_btn = w.make_tool_button("⇋", "Flip horizontally (Shift+H)", True, 26, body)
        self.flip_v_btn = w.make_tool_button("⇵", "Flip vertically (Shift+V)", True, 26, body)
        self.rotate_ccw_btn = w.make_tool_button("⟲", "Rotate 90° counter-clockwise (Ctrl+Shift+R)", width=26, parent=body)
        self.rotate_cw_btn = w.make_tool_button("⟳", "Rotate 90° clockwise (Ctrl+R)", width=26, parent=body)
        self.reset_xform_btn = w.make_tool_button("Reset", "Reset scale, rotation and flips", width=44, parent=body)
        for button, action in ((self.flip_h_btn, "transform.flip_h"), (self.flip_v_btn, "transform.flip_v"),
                               (self.rotate_ccw_btn, "transform.rotate_ccw"),
                               (self.rotate_cw_btn, "transform.rotate_cw"),
                               (self.reset_xform_btn, "transform.reset")):
            button.clicked.connect(lambda _checked=False, a=action: self._trigger(a))
        scrub = lambda text, s, step: w.ScrubLabel(text, s, step, body)  # noqa: E731
        self.form.addRow("Position", w.hrow(body, scrub("X", self.pos_x_spin, 1.0), self.pos_x_spin,
                                            scrub("Y", self.pos_y_spin, 1.0), self.pos_y_spin))
        self.form.addRow("Size", w.hrow(body, scrub("W", self.width_spin, 1.0), self.width_spin,
                                        scrub("H", self.height_spin, 1.0), self.height_spin, self.aspect_lock_btn))
        self.form.addRow(scrub("Scale", self.scale_pct_spin, 0.5),
                         w.hrow(body, self.scale_pct_spin, None, self.flip_h_btn, self.flip_v_btn))
        self.form.addRow(scrub("Rotation", self.rotation_spin, 0.5),
                         w.hrow(body, self.rotation_spin, None, self.rotate_ccw_btn, self.rotate_cw_btn,
                                self.reset_xform_btn))
        for widget, doc in ((self.pos_x_spin, "canvas_node.translate"), (self.pos_y_spin, "canvas_node.translate"),
                            (self.scale_pct_spin, "canvas_node.scale"), (self.rotation_spin, "canvas_node.rotate"),
                            (self.flip_h_btn, "action:transform.flip_h"), (self.flip_v_btn, "action:transform.flip_v"),
                            (self.rotate_ccw_btn, "action:transform.rotate_ccw"),
                            (self.rotate_cw_btn, "action:transform.rotate_cw"),
                            (self.reset_xform_btn, "action:transform.reset")):
            widget.setProperty("doc", doc)  # context help (the help browser)
        for each in (self.pos_x_spin, self.pos_y_spin, self.rotation_spin):
            each.editingFinished.connect(self.apply)
        self.width_spin.editingFinished.connect(lambda: self._on_size_edited("w"))
        self.height_spin.editingFinished.connect(lambda: self._on_size_edited("h"))
        self.scale_pct_spin.editingFinished.connect(self._on_scale_pct_edited)
        # Dragging a field (or its label) moves the element live: one undo step per drag.
        for each, live in ((self.pos_x_spin, self.apply), (self.pos_y_spin, self.apply),
                           (self.rotation_spin, self.apply), (self.width_spin, lambda: self._on_size_edited("w")),
                           (self.height_spin, lambda: self._on_size_edited("h")),
                           (self.scale_pct_spin, self._on_scale_pct_edited)):
            w.bind_edits(ui.app, each, lambda _value, fn=live: fn(), "Transform", commit=False)

    def _trigger(self, action: str) -> None:
        if action in self.ui.app.actions:
            self.ui.app.trigger(action)

    def _item(self) -> Any:
        item = self.ui.canvas.item(self._name) if self._name else None
        return item if item is not None and item.is_content else None

    def refresh(self, info: SelectionInfo) -> None:
        self._name = info.names[0] if info.single else None
        item = self._item()
        if item is not None:
            self.on_item_changed(item)

    def on_item_changed(self, item: Any) -> None:
        if item is None or item.node_name != self._name:
            return
        x, y, sx, sy, rotation = item.logical_transform()
        rect = item.content_rect()
        uniform = abs(abs(sx) - abs(sy)) < 1e-6
        self._updating = True
        try:
            for spin, value in (
                (self.pos_x_spin, x), (self.pos_y_spin, y), (self.scale_x_spin, sx), (self.scale_y_spin, sy),
                (self.rotation_spin, rotation), (self.width_spin, rect.width() * abs(sx)),
                (self.height_spin, rect.height() * abs(sy)), (self.scale_pct_spin, abs(sx) * 100.0),
            ):
                spin.blockSignals(True)
                spin.setValue(float(value))
                spin.blockSignals(False)
            self.scale_pct_spin.setToolTip(
                "Uniform scale (100% = original size)" if uniform
                else f"Width {abs(sx) * 100:.1f}% × height {abs(sy) * 100:.1f}% — typing a value makes it uniform"
            )
            self.flip_h_btn.setChecked(sx < 0)
            self.flip_v_btn.setChecked(sy < 0)
        finally:
            self._updating = False

    def _on_size_edited(self, which: str) -> None:
        item = self._item()
        if item is None or self._updating:
            return
        rect = item.content_rect()
        _x, _y, sx, sy, _rotation = item.logical_transform()
        base_w, base_h = max(rect.width(), 1e-6), max(rect.height(), 1e-6)
        new_sx, new_sy = sx, sy
        if which == "w":
            new_sx = math.copysign(self.width_spin.value() / base_w, sx or 1.0)
            if self.aspect_lock_btn.isChecked():
                new_sy = sy * abs(new_sx / sx) if sx else sy
        else:
            new_sy = math.copysign(self.height_spin.value() / base_h, sy or 1.0)
            if self.aspect_lock_btn.isChecked():
                new_sx = sx * abs(new_sy / sy) if sy else sx
        self.scale_x_spin.setValue(new_sx)
        self.scale_y_spin.setValue(new_sy)
        self.apply()

    def _on_scale_pct_edited(self) -> None:
        item = self._item()
        if item is None or self._updating:
            return
        _x, _y, sx, sy, _rotation = item.logical_transform()
        factor = self.scale_pct_spin.value() / 100.0
        self.scale_x_spin.setValue(math.copysign(factor, sx or 1.0))
        self.scale_y_spin.setValue(math.copysign(factor, sy or 1.0))
        self.apply()

    def _transform_for(self, item: Any) -> tuple[float, float, float, float, float] | None:
        x, y, sx, sy, rotation = item.logical_transform()

        def edited(spin: Any, value: float) -> float | None:
            # The spins show rounded values (positions to 0.1 px): only a value
            # that differs from the current one *as displayed* was edited.
            if abs(spin.value() - value) <= 0.5 * 10 ** -spin.decimals() + 1e-9:
                return None
            return spin.value()

        ex, ey = edited(self.pos_x_spin, x), edited(self.pos_y_spin, y)
        esx, esy = edited(self.scale_x_spin, sx), edited(self.scale_y_spin, sy)
        erot = edited(self.rotation_spin, rotation)
        nx, ny = x if ex is None else ex, y if ey is None else ey
        nsx = sx if esx is None else (esx or 0.01)
        nsy = sy if esy is None else (esy or 0.01)
        nrot = rotation if erot is None else erot
        pos_changed = ex is not None or ey is not None
        shape_changed = esx is not None or esy is not None or erot is not None
        if not pos_changed and not shape_changed:
            return None
        if pos_changed or not shape_changed:
            return nx, ny, nsx if shape_changed else sx, nsy if shape_changed else sy, nrot if shape_changed else rotation
        # Scale/rotation edits pivot around the item's centre.
        px, py = item.pivot
        center = item.content_rect().center()
        old_center = item.local_transform().map(center)
        moved = compose_transform(0.0, 0.0, nsx, nsy, nrot, px, py).map(center)
        return old_center.x() - moved.x(), old_center.y() - moved.y(), nsx, nsy, nrot

    def apply(self) -> None:
        item = self._item()
        if item is None or self._updating:
            return
        values = self._transform_for(item)
        if values is None:
            return
        x, y, sx, sy, rotation = values
        attrs = {
            "translate": [round(x, 3) + 0.0, round(y, 3) + 0.0],
            "scale": [round(sx, 6) + 0.0, round(sy, 6) + 0.0],
            "rotate": round(xform.normalize_angle(rotation), 4) + 0.0,
        }
        self.ui.app.run_command("set_attrs", self._name, attrs)


class AppearanceSection(w.InspectorSection):
    def __init__(self, ui: Any, parent: QWidget) -> None:
        super().__init__("appearance", "Appearance", parent)
        self.ui = ui
        self._updating = False
        self._names: list[str] = []
        body = self.body
        self.opacity_spin = w.make_spin(body, minimum=0.0, maximum=100.0, step=5.0, default=100.0,
                                        soft_range=(0.0, 100.0))
        w.compact_spin(self.opacity_spin, 0, " %", 58)
        self.opacity_slider = QSlider(Qt.Horizontal, body)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(100)
        row = QWidget(body)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.opacity_slider, 1)
        layout.addWidget(self.opacity_spin)
        opacity_label = w.ScrubLabel("Opacity", self.opacity_spin, 0.5, body)
        for widget in (opacity_label, row, self.opacity_spin, self.opacity_slider):
            widget.setProperty("doc", "canvas_node.opacity")  # context help (the help browser)
        self.form.addRow(opacity_label, row)
        self.opacity_slider.valueChanged.connect(self._on_slider_moved)
        self.opacity_slider.sliderReleased.connect(self.apply)
        self.opacity_spin.editingFinished.connect(self.apply)
        self.opacity_spin.valueChanged.connect(self._on_spin_moved)
        w.bind_edits(ui.app, self.opacity_spin, lambda _value: self.apply(), "Set opacity", commit=False)

    def _on_spin_moved(self, value: float) -> None:
        self.opacity_slider.blockSignals(True)
        self.opacity_slider.setValue(round(value))
        self.opacity_slider.blockSignals(False)

    def refresh(self, info: SelectionInfo) -> None:
        self._names = [n["name"] for n in info.nodes if "opacity" in n.get("attrs", {})]
        opacities = {float(n["attrs"].get("opacity", 1.0)) for n in info.nodes if "opacity" in n.get("attrs", {})}
        self.set_display(opacities.pop() if len(opacities) == 1 else 1.0)

    def set_display(self, opacity: float) -> None:
        self._updating = True
        try:
            for widget, value in ((self.opacity_spin, opacity * 100.0), (self.opacity_slider, round(opacity * 100.0))):
                widget.blockSignals(True)
                widget.setValue(value)
                widget.blockSignals(False)
        finally:
            self._updating = False

    def _on_slider_moved(self, value: int) -> None:
        self.opacity_spin.blockSignals(True)
        self.opacity_spin.setValue(float(value))
        self.opacity_spin.blockSignals(False)
        if not self.opacity_slider.isSliderDown() and not self._updating:
            self.apply()  # keyboard / click on the track

    def apply(self) -> None:
        if self._updating or not self._names:
            return
        opacity = round(self.opacity_spin.value() / 100.0, 4)
        self.ui.app.run_command("inspector.set_opacity", self._names, opacity)


class AttributeEditor(w.InspectorSection):
    """Editors generated from attribute specs (for node types without their own
    section, and for dynamic attributes on any node)."""

    def __init__(self, ui: Any, parent: QWidget, key: str, title: str, dynamic_only: bool) -> None:
        super().__init__(key, title, parent)
        self.ui = ui
        self.dynamic_only = dynamic_only
        self._signature: Any = None
        self._name: str | None = None
        self._editors: dict[str, Any] = {}
        self._type = ""
        self._updating = False

    def specs(self, info: SelectionInfo) -> list[Any]:
        node = info.node
        session = self.ui.session
        scene_node = session.scene.get_node(node["name"])
        if self.dynamic_only:
            return list(session.scene.dynamic_attrs(scene_node).values())
        known = session.types.attrs(node["type"]) if node["type"] in session.types else {}
        return [a for a in known.values() if a.name not in CORE_ATTRS and not a.hidden and not a.computed]

    def refresh(self, info: SelectionInfo) -> None:
        self._name = info.names[0]
        self._type = info.node["type"]
        specs = self.specs(info)
        signature = (info.node["type"], tuple(s.name for s in specs))
        if signature != self._signature:
            self._build(specs)
            self._signature = signature
        attrs = info.attrs
        self._updating = True
        try:
            for spec in specs:
                self._set_editor(spec, attrs.get(spec.name))
        finally:
            self._updating = False

    def _build(self, specs: list[Any]) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self._editors = {}
        reserve = any(w.attr_symbol(self.ui.session, s, self._type) for s in specs)
        for spec in specs:
            editor = w.editor_for(spec, self.body)
            w.bind_edits(self.ui.app, editor, lambda value, s=spec: self._commit(s.name, value),
                         f"Set {spec.nice_name.lower()}")
            editor.context_provider = lambda s=spec: self._menu(s)
            self._editors[spec.name] = (spec, editor)
            self.form.addRow(w.attr_label(self.ui.session, spec, editor, self._type, reserve, self.body), editor)

    def _commit(self, attr: str, value: Any) -> None:
        if self._updating or self._name is None:
            return
        self.ui.app.run_command("set_attr", self._name, attr, value)

    def _menu(self, spec: Any) -> list[tuple[Any, Any]]:
        """Right-click: reset the value, or make it the default for new nodes of this kind."""
        entries: list[tuple[Any, Any]] = [("Reset to default", lambda: self._commit(spec.name, spec.default_value()))]
        tool = self.ui.tools.creator(self._type) if self._type else None
        if tool is not None and spec.name in tool.options:
            editor = self._editors[spec.name][1]

            def save() -> None:
                try:
                    tool.options.set_default(spec.name, editor.read())
                except Exception as exc:  # noqa: BLE001
                    self.ui.toast(str(exc))
                    return
                self.ui.toast(f"New {tool.title} drawings: {spec.nice_name.lower()} saved as your default")

            entries.append((f"Set as my default for new {tool.title}", save))
        return entries

    def _set_editor(self, spec: Any, value: Any) -> None:
        entry = self._editors.get(spec.name)
        if entry is not None:
            entry[1].write(value)
