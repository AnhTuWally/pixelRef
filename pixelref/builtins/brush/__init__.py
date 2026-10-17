"""Brush: the colour and width the drawing tools use, and stroke styling.

Every drawing tool (``uses_brush_color``) keeps its own colour and width as
tool options (``color_option`` / ``width_option``), so Rect, Arrow and Pen
each remember theirs and each can save them as your default. The ``brush``
service is a view onto the drawing tool you're using (the active one, else the
last one used): ``brush.color`` / ``set_color`` read and change that tool's
colour, which is what the colour picker and plugins use. It also keeps the
recent colours and makes the rows tools put in their option panels.

This plugin also declares ``stroke.styles``: node-type plugins say which
stroke controls apply to their nodes, and the "Stroke" section of the
Attribute Editor edits them.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QCheckBox, QColorDialog, QLabel, QWidget, QHBoxLayout

from pixelref.plugin_api import differs
from pixelref.plugin_api.contracts import InspectorSection, SelectionInfo, StrokeStyle
from pixelref.plugin_api.ui import widgets as w

MAX_RECENT_COLORS = 12


class BrushService(QObject):
    changed = Signal()
    recent_changed = Signal()

    def __init__(self, ui: Any) -> None:
        super().__init__()
        self.ui = ui
        # For drawing tools that have no colour/width options of their own.
        self._color = QColor(255, 64, 64)
        self._width = 4.0
        self._last_tool: str | None = None
        self._watching: dict[str, Any] = {}
        stored = ui.settings.value("recent_colors", [])
        stored = [stored] if isinstance(stored, str) else list(stored or [])
        self.recent: list[str] = [c for c in stored if QColor(c).isValid()][:MAX_RECENT_COLORS]

    # ---- Which tool the brush is ------------------------------------------------------------

    def tool(self) -> Any:
        """The drawing tool the brush colour/width belong to: the active one, else the last used."""
        tools = self.ui.tools
        active = tools.active
        if active is not None and getattr(active, "uses_brush_color", False):
            return active
        last = tools.get(self._last_tool) if self._last_tool else None
        if last is not None:
            return last
        return next((t for t in tools.ordered() if getattr(t, "uses_brush_color", False)), None)

    @staticmethod
    def _option(tool: Any, kind: str) -> str:
        name = getattr(tool, "color_option" if kind == "color" else "width_option", "") if tool is not None else ""
        return name if name and name in tool.options else ""

    def on_tool_changed(self, _old: Any, new: Any) -> None:
        if new is not None and getattr(new, "uses_brush_color", False):
            self._last_tool = new.id
            self.changed.emit()

    def on_tools_registered(self) -> None:
        """Follow the colour/width options of every drawing tool."""
        tools = self.ui.tools.tools
        for tool_id in [t for t in self._watching if t not in tools]:
            self._watching.pop(tool_id).dispose()
        for tool in tools.values():
            if tool.id in self._watching or not getattr(tool, "uses_brush_color", False):
                continue
            names = {self._option(tool, "color"), self._option(tool, "width")} - {""}
            if names:
                self._watching[tool.id] = tool.options.watch(
                    lambda name, _value, n=names, t=tool: name in n and t is self.tool() and self.changed.emit())

    def dispose(self) -> None:
        while self._watching:
            self._watching.popitem()[1].dispose()

    # ---- Colour and width -----------------------------------------------------------------------

    @property
    def color(self) -> QColor:
        tool = self.tool()
        name = self._option(tool, "color")
        return w.color_from(tool.options.get(name)) if name else QColor(self._color)

    @property
    def width(self) -> float:
        tool = self.tool()
        name = self._option(tool, "width")
        return float(tool.options.get(name)) if name else self._width

    def set_color(self, color: QColor) -> None:
        color = QColor(color)
        color.setAlpha(255)
        tool = self.tool()
        name = self._option(tool, "color")
        if name:
            tool.options.set(name, w.rgb(color))
        else:
            self._color = color
        self.changed.emit()

    def set_width(self, width: float) -> None:
        width = round(float(width), 2)
        tool = self.tool()
        name = self._option(tool, "width")
        if name:
            tool.options.set(name, width)
        elif width != self._width:
            self._width = width
        self.changed.emit()

    def rgb(self) -> list[int]:
        return w.rgb(self.color)

    def add_recent(self, color: QColor) -> None:
        hex_value = QColor(color).name().upper()
        self.recent = ([hex_value] + [c for c in self.recent if c != hex_value])[:MAX_RECENT_COLORS]
        self.ui.settings.setValue("recent_colors", self.recent)
        self.recent_changed.emit()

    def use_recent(self, hex_value: str) -> None:
        color = QColor(hex_value)
        self.set_color(color)
        QApplication.clipboard().setText(hex_value)
        self.ui.toast(f"Brush color {hex_value} (copied)", color)

    def pick_color(self, parent: QWidget | None = None) -> None:
        color = QColorDialog.getColor(self.color, parent, "Brush color")
        if color.isValid():
            self.set_color(color)

    # ---- Widgets tools reuse in their option panels -------------------------------------

    def make_brush_row(self, parent: QWidget, tool: Any = None) -> "BrushRow":
        """Colour swatch + width for ``tool``'s own options (without a tool: the brush tool's)."""
        return BrushRow(self, parent, tool)

    def make_recent_row(self, parent: QWidget) -> "RecentRow":
        return RecentRow(self, parent)

    def make_tool_panel(self, tool: Any, parent: QWidget, names: Any = None, recent: bool = True) -> QWidget:
        """A drawing tool's option panel: its colour/width row, a form for its other
        options (``names``, default: all the rest) and the recent colours.
        ``panel.brush_row``, ``panel.form.editors[name]``, ``panel.recent_row``."""
        from PySide6.QtWidgets import QVBoxLayout

        panel = QWidget(parent)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        panel.brush_row = self.make_brush_row(panel, tool)  # type: ignore[attr-defined]
        layout.addWidget(panel.brush_row)
        brush_names = {getattr(tool, "color_option", ""), getattr(tool, "width_option", "")}
        if names is None:
            names = [n for n, spec in tool.options.specs.items() if n not in brush_names and not spec.hidden]
        panel.form = w.fields.options_form(tool, panel, names)  # type: ignore[attr-defined]
        layout.addWidget(panel.form)
        if recent:
            panel.recent_row = self.make_recent_row(panel)  # type: ignore[attr-defined]
            layout.addWidget(panel.recent_row)
        return panel


class BrushRow(QWidget):
    """Colour swatch + hex + width. With a tool, bound to that tool's colour/width
    options (right-click either one to make it your default); without, to the brush."""

    def __init__(self, brush: BrushService, parent: QWidget, tool: Any = None) -> None:
        super().__init__(parent)
        self.brush = brush
        self.tool = tool
        self._color_name = brush._option(tool, "color")
        self._width_name = brush._option(tool, "width")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.color_btn = w.make_tool_button("", "Color (I samples a color from the canvas)", parent=self)
        self.color_btn.clicked.connect(self._pick)
        self.hex_label = QLabel("", self)
        self.hex_label.setObjectName("hexLabel")
        self.hex_label.setToolTip("Color — set it with the picker (I) or a recent swatch")
        if self._width_name:
            self.width_spin = w.fields.option_editor(tool, self._width_name, self)
        else:
            self.width_spin = w.make_spin(self, minimum=0.5, maximum=200.0, step=0.5, default=brush.width,
                                          soft_range=(0.5, 40.0))
            self.width_spin.editingFinished.connect(lambda: brush.set_width(self.width_spin.value()))
        self.width_spin.setDecimals(1)
        self.width_spin.setSuffix(" px")
        self.width_spin.setToolTip("Line width")
        if self._color_name:
            w.install_context_menu(self.color_btn, lambda: w.fields.option_menu(tool, self._color_name))
        layout.addWidget(QLabel("Color", self))
        layout.addWidget(self.color_btn)
        layout.addWidget(self.hex_label)
        layout.addSpacing(8)
        layout.addWidget(w.ScrubLabel("Width", self.width_spin, 0.1, self))
        layout.addWidget(self.width_spin)
        layout.addStretch()
        if tool is not None and tool.options:
            handle = tool.options.watch(lambda _name, _value: self.sync())
            self.destroyed.connect(lambda *_a: handle.dispose())
        else:
            brush.changed.connect(self.sync)
        self.sync()

    def current_color(self) -> QColor:
        if self._color_name:
            return w.color_from(self.tool.options.get(self._color_name))
        return self.brush.color

    def _pick(self) -> None:
        color = QColorDialog.getColor(self.current_color(), self.window(), "Color")
        if not color.isValid():
            return
        if self._color_name:
            self.tool.options.set(self._color_name, w.rgb(color))
        else:
            self.brush.set_color(color)

    def sync(self) -> None:
        color = self.current_color()
        w.swatch(self.color_btn, color)
        self.hex_label.setText(color.name().upper())
        self.color_btn.setToolTip(f"Color {color.name().upper()} — click to change, right-click for defaults")
        if not self._width_name and abs(self.width_spin.value() - self.brush.width) > 1e-6:
            self.width_spin.blockSignals(True)
            self.width_spin.setValue(self.brush.width)
            self.width_spin.blockSignals(False)


class RecentRow(QWidget):
    def __init__(self, brush: BrushService, parent: QWidget) -> None:
        super().__init__(parent)
        self.brush = brush
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(3)
        brush.recent_changed.connect(self.rebuild)
        self.rebuild()

    def rebuild(self) -> None:
        while self._layout.count():
            child = self._layout.takeAt(0).widget()
            if child is not None:
                child.deleteLater()
        label = QLabel("Recent" if self.brush.recent else "Recent colors appear here", self)
        label.setObjectName("pathLabel")
        self._layout.addWidget(label)
        for hex_value in self.brush.recent:
            button = w.make_tool_button("", f"{hex_value} — click to use as the brush color", width=18, parent=self)
            button.setFixedHeight(18)
            w.swatch(button, QColor(hex_value))
            button.clicked.connect(lambda _checked=False, c=hex_value: self.brush.use_recent(c))
            self._layout.addWidget(button)
        self._layout.addStretch()


# ---- The Stroke section --------------------------------------------------------------------


class StrokeSection(w.InspectorSection):
    def __init__(self, ui: Any, parent: QWidget, inspector: Any) -> None:
        super().__init__("stroke", "Stroke", parent)
        self.ui = ui
        self.inspector = inspector
        self._updating = False
        self._names: list[str] = []
        self._style: StrokeStyle | None = None
        self._stroke = QColor(255, 0, 0)
        self._fill = QColor(0, 0, 0)
        body = self.body
        self.stroke_btn = w.make_tool_button("", "Stroke color", parent=body)
        self.stroke_btn.clicked.connect(self._pick_stroke)
        self.stroke_style_combo = w.make_combo(w.LINE_STYLE_CHOICES, body)
        self.thickness_spin = w.make_spin(body, minimum=0.5, maximum=200.0, step=0.5, default=2.0,
                                          soft_range=(0.5, 40.0))
        self.thickness_spin.setDecimals(1)
        self.thickness_spin.setSuffix(" px")
        self.fill_btn = w.make_tool_button("", "Fill color", parent=body)
        self.fill_btn.clicked.connect(self._pick_fill)
        self.shape_fill_style_combo = w.make_combo(w.FILL_STYLE_CHOICES, body)
        self.fill_opacity_spin = w.make_spin(body, minimum=0.0, maximum=100.0, step=5.0, default=0.0,
                                             soft_range=(0.0, 100.0))
        self.fill_opacity_spin.setDecimals(0)
        self.fill_opacity_spin.setSuffix(" %")
        self.shape_radius_spin = w.make_spin(body, minimum=0.0, maximum=1000.0, step=1.0, soft_range=(0.0, 100.0))
        self.shape_radius_spin.setDecimals(1)
        self.shape_radius_spin.setSuffix(" px")
        self.arrow_start_check = QCheckBox("Start arrow", body)
        self.arrow_check = QCheckBox("End arrow", body)
        self.smoothing_spin = w.NumberField(body, minimum=0.0, maximum=100.0, step=5.0, suffix=" %",
                                            soft_range=(0.0, 100.0), integer=True)
        self.default_btn = w.make_tool_button("Use as default", "", width=110, parent=body)
        self.default_btn.clicked.connect(self._save_as_default)

        form = self.form
        form.addRow("Color", w.hrow(body, self.stroke_btn, self.stroke_style_combo))
        form.addRow(w.ScrubLabel("Width", self.thickness_spin, 0.1, body), self.thickness_spin)
        self.fill_label = QLabel("Fill", body)
        self.fill_row = w.hrow(body, self.fill_btn, self.shape_fill_style_combo)
        form.addRow(self.fill_label, self.fill_row)
        self.fill_opacity_label = w.ScrubLabel("Fill alpha", self.fill_opacity_spin, 0.5, body)
        form.addRow(self.fill_opacity_label, self.fill_opacity_spin)
        self.radius_label = w.ScrubLabel("Radius", self.shape_radius_spin, 0.5, body)
        form.addRow(self.radius_label, self.shape_radius_spin)
        self.arrow_row = w.hrow(body, self.arrow_start_check, self.arrow_check)
        form.addRow(self.arrow_row)
        self.smoothing_label = w.ScrubLabel("Smoothing", self.smoothing_spin, 0.5, body)
        form.addRow(self.smoothing_label, self.smoothing_spin)
        form.addRow(w.hrow(body, self.default_btn))

        for spin in (self.thickness_spin, self.fill_opacity_spin, self.shape_radius_spin, self.smoothing_spin):
            spin.editingFinished.connect(self.apply)
            # Dragging previews on the canvas and is one undo step.
            w.bind_edits(ui.app, spin, lambda _value: self.apply(), "Edit stroke", commit=False)
        for check in (self.arrow_check, self.arrow_start_check):
            check.toggled.connect(lambda _on: self.apply())
        for combo in (self.stroke_style_combo, self.shape_fill_style_combo):
            combo.currentIndexChanged.connect(lambda _i: self.apply())

    # ---- Populate ------------------------------------------------------------------------

    def _styles(self) -> dict[str, StrokeStyle]:
        return {style.node_type: style for style in self.ui.contributions("stroke.styles")}

    def _reference_attrs(self, info: SelectionInfo, style: StrokeStyle) -> dict[str, Any]:
        if style.children_type is None:
            return info.attrs
        nodes = self.ui.canvas.nodes
        for child in info.node.get("children", []):
            node = nodes.get(child)
            if node is not None and node["type"] == style.children_type:
                return node["attrs"]
        brush = self.ui.service("brush")
        return {"color": brush.rgb(), "thickness": brush.width, "smoothing": 0.5}

    def refresh(self, info: SelectionInfo) -> None:
        style = self._styles().get(info.node["type"]) if info.single else None
        self._style = style
        self._names = list(info.names)
        if style is None:
            return
        attrs = self._reference_attrs(info, style)
        self._updating = True
        try:
            self._stroke = w.color_from(attrs.get(style.color_attr, [255, 0, 0]))
            self._fill = w.color_from(attrs.get("fill_color", [0, 0, 0]))
            for widget in (self.fill_label, self.fill_row, self.fill_opacity_spin, self.fill_opacity_label):
                widget.setVisible(style.fill)
            for widget in (self.shape_radius_spin, self.radius_label):
                widget.setVisible(style.radius)
            self.fill_opacity_spin.setValue(float(attrs.get("fill_opacity", 0.0)) * 100.0)
            self.thickness_spin.setValue(float(attrs.get("thickness", 2.0)))
            self.shape_radius_spin.setValue(float(attrs.get("corner_radius", 0.0)))
            w.set_combo(self.stroke_style_combo, attrs.get("stroke_style", "solid"))
            w.set_combo(self.shape_fill_style_combo, attrs.get("fill_style", "solid"))
            self.arrow_row.setVisible(style.arrows)
            self.arrow_check.setChecked(bool(attrs.get("arrow", 0)))
            self.arrow_start_check.setChecked(bool(attrs.get("arrow_start", 0)))
            for widget in (self.smoothing_label, self.smoothing_spin):
                widget.setVisible(style.smoothing)
            self.smoothing_spin.setValue(round(float(attrs.get("smoothing", 0.0)) * 100))
            w.swatch(self.stroke_btn, self._stroke)
            w.swatch(self.fill_btn, self._fill)
            self._show_default_button(info)
        finally:
            self._updating = False

    def _creator(self) -> tuple[str, Any]:
        if len(self._names) != 1:
            return "", None
        return self.ui.tools.creator_for(self.ui.session.scene, self._names[0])

    def _show_default_button(self, _info: SelectionInfo) -> None:
        _source, tool = self._creator()
        self.default_btn.setVisible(tool is not None)
        if tool is not None:
            self.default_btn.setToolTip(f"New {tool.title} drawings get this element's style "
                                        "(kept as your default, across restarts)")

    def _save_as_default(self) -> None:
        _source, tool = self._creator()
        if tool is None:
            return
        if self.ui.app.run_command("tools.save_defaults_from", self._names[0], tool.id) is not None:
            self.ui.toast(f"New {tool.title} drawings now use this style")

    def on_item_changed(self, item: Any) -> None:
        corner_radius = getattr(item, "corner_radius", None)
        if callable(corner_radius) and self._style is not None and self._style.radius:
            self.shape_radius_spin.blockSignals(True)
            self.shape_radius_spin.setValue(corner_radius())
            self.shape_radius_spin.blockSignals(False)

    # ---- Apply -----------------------------------------------------------------------------

    def wanted(self) -> dict[str, Any]:
        style = self._style
        assert style is not None
        values: dict[str, Any] = {
            style.color_attr: w.rgb(self._stroke),
            "stroke_style": self.stroke_style_combo.currentData(),
            "thickness": round(self.thickness_spin.value(), 3),
        }
        if style.fill:
            values.update({
                "fill_color": w.rgb(self._fill),
                "fill_opacity": round(self.fill_opacity_spin.value() / 100.0, 4),
                "fill_style": self.shape_fill_style_combo.currentData(),
            })
        if style.radius:
            values["corner_radius"] = round(self.shape_radius_spin.value(), 3)
        if style.arrows:
            values["arrow"] = int(self.arrow_check.isChecked())
            values["arrow_start"] = int(self.arrow_start_check.isChecked())
        if style.smoothing:
            values["smoothing"] = round(self.smoothing_spin.value() / 100.0, 2)
        return values

    def apply(self) -> None:
        if self._updating or self._style is None or len(self._names) != 1:
            return
        self.ui.app.run_command("brush.restyle", self._names[0], self.wanted())

    def _pick_stroke(self) -> None:
        color = QColorDialog.getColor(self._stroke, self.window(), "Stroke color")
        if color.isValid():
            self._stroke = color
            w.swatch(self.stroke_btn, color)
            self.apply()

    def _pick_fill(self) -> None:
        color = QColorDialog.getColor(self._fill, self.window(), "Fill color")
        if color.isValid():
            self._fill = color
            if self.fill_opacity_spin.value() == 0.0:
                self.fill_opacity_spin.setValue(35.0)
            w.swatch(self.fill_btn, color)
            self.apply()


def activate_ui(ui: Any) -> None:
    brush = BrushService(ui)
    ui.provide_service("brush", brush)
    ui.track(ui.tools.watch(brush.on_tool_changed))
    ui.track(ui.tools.watch_registry(brush.on_tools_registered))
    ui.track(brush.dispose)
    brush.on_tools_registered()
    ui.declare_extension_point("stroke.styles", "StrokeStyle: which stroke controls a node type has")

    def restyle(node: str, attrs: dict[str, Any]) -> list[str]:
        """Set stroke attributes on a node (a drawing passes them to all its strokes)."""
        scene = ui.session.scene
        name = scene.resolve(node)
        styles = {s.node_type: s for s in ui.contributions("stroke.styles")}
        target = scene.get_node(name)
        style = styles.get(target.node_type)
        targets = [name]
        if style is not None and style.children_type:
            targets = [c for c in target.children if scene.get_node(c).node_type == style.children_type]
            if targets:
                # Only what changed relative to the first stroke goes to every
                # stroke, so strokes keep their individual colours otherwise.
                reference = scene.get_node(targets[0]).attrs
                attrs = {k: v for k, v in attrs.items() if differs(reference.get(k), v)}
        changed = []
        for each in targets:
            stored = scene.get_node(each).attrs
            diff = {k: v for k, v in attrs.items() if differs(stored.get(k), v)}
            if diff:
                scene.set_attrs(each, diff)
                changed.append(each)
        return changed

    ui.command("restyle", restyle, label=lambda node, *a, **k: f"Edit {node}")

    def applies(info: SelectionInfo) -> bool:
        if not info.single:
            return False
        return any(s.node_type == info.node["type"] for s in ui.contributions("stroke.styles"))

    ui.contribute("inspector.sections", InspectorSection(
        key="stroke", title="Stroke", order=40, applies=applies,
        build=lambda parent, inspector: StrokeSection(ui, parent, inspector),
    ))


