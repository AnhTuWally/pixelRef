"""Qt side of the text plugin: item factory, inspector sections, width handles, actions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QTimer, QUrl, Qt
from PySide6.QtGui import QColor, QDesktopServices, QFont, QTransform
from PySide6.QtWidgets import QColorDialog, QComboBox, QFileDialog, QFontComboBox, QInputDialog, QLabel

from pixelref.plugin_api import CommandError, fast_copy
from pixelref.plugin_api.contracts import IngestHandler, InspectorSection, PaletteButton, SelectionInfo
from pixelref.plugin_api.ui import HANDLED, InputHandler, PointerEvent
from pixelref.plugin_api.ui import widgets as w

from . import DEFAULT_STYLE_KEYS, NO_HIGHLIGHT, TEXT_PRESETS, TEXT_STYLE_KEYS
from .items import EDITING, TextItem

# What Copy Text Style takes from a whole note: its look, not its size or text.
COPIED_NOTE_KEYS = tuple(key for key in DEFAULT_STYLE_KEYS if key != "text_width")


class TextService:
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.sections: list[Any] = []
        self.copied_style: dict[str, Any] | None = None

    def editing_item(self) -> TextItem | None:
        """The note being edited on the canvas, if any (tracked, not the scene's focus
        item: that is cleared while a colour dialog is open, but editing continues)."""
        items = self.ui.canvas.items
        for item in list(EDITING):
            try:
                if item.editing and items.get(item.node_name) is item:
                    return item
            except RuntimeError:  # its Qt object is gone
                EDITING.discard(item)
        return None

    def commit_editing(self) -> bool:
        editor = self.editing_item()
        if editor is not None:
            editor.commit_edit()
            return True
        return False

    def format_changed(self, item: TextItem) -> None:
        for section in list(self.sections):
            try:
                section.on_format_changed(item)
            except RuntimeError:
                self.sections.remove(section)


class TextSection(w.InspectorSection):
    def __init__(self, ui: Any, service: TextService, parent: Any) -> None:
        super().__init__("text", "Text", parent)
        self.ui = ui
        self.service = service
        self._updating = False
        self._names: list[str] = []
        body = self.body
        self.text_edit = w.TextField(body)
        self.text_edit.setFixedHeight(56)
        self.text_preset_combo = QComboBox(body)
        self.text_preset_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.text_preset_combo.setMinimumContentsLength(8)
        self.text_preset_combo.addItem("Apply a style preset…")
        for name in TEXT_PRESETS:
            self.text_preset_combo.addItem(name)
        self.font_combo = QFontComboBox(body)
        self.font_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.font_combo.setMinimumContentsLength(8)
        self.font_size_spin = w.make_spin(body, minimum=4.0, maximum=400.0, step=1.0, default=14.0,
                                          soft_range=(6.0, 96.0))
        self.font_size_spin.setDecimals(1)
        tb = lambda text, tip, checkable=False, width=28: w.make_tool_button(text, tip, checkable, width, body)  # noqa: E731
        self.font_smaller_btn = tb("A−", "Smaller text (Ctrl+Shift+<)")
        self.font_bigger_btn = tb("A+", "Bigger text (Ctrl+Shift+>)")
        self.bold_btn = tb("B", "Bold (Ctrl+B)", True)
        self.italic_btn = tb("I", "Italic (Ctrl+I while editing)", True)
        self.underline_btn = tb("U", "Underline (Ctrl+U)", True)
        self.strike_btn = tb("S", "Strikethrough (Ctrl+Shift+X)", True)
        for button, attr in ((self.bold_btn, "setBold"), (self.italic_btn, "setItalic"),
                             (self.underline_btn, "setUnderline"), (self.strike_btn, "setStrikeOut")):
            font = QFont()
            getattr(font, attr)(True)
            button.setFont(font)
        self.align_btns = {key: tb(glyph, tip, True) for key, glyph, tip in
                           (("left", "⇤", "Align left"), ("center", "↔", "Center"), ("right", "⇥", "Align right"))}
        self._text_color = QColor(220, 220, 220)
        self._highlight_color = QColor(255, 235, 59, 160)
        self.text_color_btn = tb("A", "Text color")
        self.highlight_btn = tb("ab", "Highlight color (marker behind the text)")
        self.clear_highlight_btn = tb("⌀", "Remove highlight")
        self.clear_format_btn = tb("Tx", "Clear formatting", width=32)
        self.text_scope_label = QLabel("", body)
        self.text_scope_label.setObjectName("pathLabel")
        self.text_scope_label.setWordWrap(True)
        self.default_style_btn = w.make_tool_button(
            "Use as default", "New notes get this note's style (font, colours, box) — kept across restarts. "
            "Right-click: back to the plain style", width=110, parent=body)
        self.default_style_btn.clicked.connect(self._save_default_style)
        w.install_context_menu(self.default_style_btn, lambda: [
            ("Use this note's style for new notes", self._save_default_style),
            ("New notes: plain style", self._clear_default_style)])
        form = self.form
        form.addRow(self.text_preset_combo)
        form.addRow(self.text_edit)
        form.addRow("Font", self.font_combo)
        form.addRow("Size", w.hrow(body, self.font_size_spin, self.font_smaller_btn, self.font_bigger_btn))
        form.addRow("Style", w.hrow(body, self.bold_btn, self.italic_btn, self.underline_btn, self.strike_btn, None,
                                    *self.align_btns.values()))
        form.addRow("Color", w.hrow(body, self.text_color_btn, self.highlight_btn, self.clear_highlight_btn, None,
                                    self.clear_format_btn))
        self.link_btn = tb("🔗", "Link the selected text, or the whole note, to a web page or file (Ctrl+K while "
                           "typing) · Ctrl+click a link to open it", width=32)
        self.checklist_btn = tb("☑", "Checklist: a box in front of each line · click a box to tick it", width=32)
        self.copy_style_btn = tb("Copy", "Copy text style (Ctrl+Shift+C)", width=44)
        self.paste_style_btn = tb("Paste", "Paste text style onto the selected text or notes (Ctrl+Shift+V)", width=48)
        form.addRow("More", w.hrow(body, self.link_btn, self.checklist_btn, None, QLabel("Style", body),
                                   self.copy_style_btn, self.paste_style_btn))
        form.addRow(self.text_scope_label)
        form.addRow(w.hrow(body, self.default_style_btn))
        run = ui.app.run_command
        self.link_btn.clicked.connect(lambda: run("text.link_dialog"))
        self.checklist_btn.clicked.connect(lambda: run("text.toggle_checklist"))
        self.copy_style_btn.clicked.connect(lambda: run("text.copy_style"))
        self.paste_style_btn.clicked.connect(lambda: run("text.paste_style"))

        self.text_edit.editingFinished.connect(self._on_text_field_edited)
        self.text_preset_combo.activated.connect(self._apply_preset)
        self.font_combo.currentFontChanged.connect(self._on_font_family_changed)
        self.font_size_spin.editingFinished.connect(self._on_font_size_edited)
        # Dragging the size previews live (not while typing in a note: that applies on release).
        w.bind_edits(ui.app, self.font_size_spin,
                     lambda _value: self.editor() is None and self._on_font_size_edited(), "Text size", commit=False)
        self.font_smaller_btn.clicked.connect(lambda: self._step_size(-1))
        self.font_bigger_btn.clicked.connect(lambda: self._step_size(1))
        for key, button in (("bold", self.bold_btn), ("italic", self.italic_btn), ("underline", self.underline_btn),
                            ("strike", self.strike_btn)):
            button.clicked.connect(lambda _checked=False, k=key: self.toggle_style(k))
        for key, button in self.align_btns.items():
            button.clicked.connect(lambda _checked=False, k=key: self.set_box({"align": k}, "Align text"))
        self.text_color_btn.clicked.connect(self._pick_text_color)
        self.highlight_btn.clicked.connect(self._pick_highlight)
        self.clear_highlight_btn.clicked.connect(
            lambda: self.apply_format("highlight_color", list(NO_HIGHLIGHT), "Remove highlight"))
        self.clear_format_btn.clicked.connect(self._clear_formatting)
        service.sections.append(self)

    # ---- Helpers -----------------------------------------------------------------------

    def editor(self) -> TextItem | None:
        return self.service.editing_item()

    def set_attrs(self, updates: dict[str, Any], label: str, strip: tuple[str, ...] = ()) -> None:
        if not self._names:
            return
        self.ui.app.run_command("text.set_attrs", updates, self._names, list(strip), label)

    def set_box(self, updates: dict[str, Any], label: str) -> None:
        if self._updating:
            return
        self.set_attrs(updates, label)

    def apply_format(self, key: str, value: Any, label: str) -> None:
        """Character styles go to the selected words while editing, else to the whole note."""
        if self._updating:
            return
        editor = self.editor()
        if editor is not None:
            editor.merge_format(key, value)
            return
        self.set_attrs({key: value}, label, strip=(key,))

    def toggle_style(self, key: str) -> None:
        editor = self.editor()
        if editor is not None:
            value = not bool(editor.format_state(key))
        else:
            nodes = self.ui.canvas.nodes
            if not self._names:
                return
            value = not all(bool(nodes[n]["attrs"].get(key, False)) for n in self._names if n in nodes)
        self.apply_format(key, value, f"{'Set' if value else 'Clear'} {key}")
        {"bold": self.bold_btn, "italic": self.italic_btn, "underline": self.underline_btn,
         "strike": self.strike_btn}[key].setChecked(value)

    def _step_size(self, direction: int) -> None:
        editor = self.editor()
        if editor is not None:
            editor.step_font_size(direction)
            return
        if not self._names:
            return
        current = float(self.ui.canvas.nodes[self._names[0]]["attrs"].get("font_size", 14))
        new_size = max(4.0, round(current * (1.15 if direction > 0 else 1 / 1.15)))
        if new_size == current:
            new_size = max(4.0, current + direction)
        self.set_attrs({"font_size": new_size}, "Text size", strip=("font_size",))

    def _save_default_style(self) -> None:
        if self._names and self.ui.app.run_command("text.save_default_style", self._names[0]) is not None:
            self.ui.toast("New notes now use this note's style")

    def _clear_default_style(self) -> None:
        self.ui.app.run_command("text.clear_default_style")
        self.ui.toast("New notes use the plain style")

    def _on_font_size_edited(self) -> None:
        if self._updating:
            return
        value = round(self.font_size_spin.value(), 1)
        editor = self.editor()
        if editor is not None:
            editor.merge_format("font_size", value)
            return
        self.set_attrs({"font_size": value}, "Text size", strip=("font_size",))

    def _on_font_family_changed(self, font: QFont) -> None:
        if not self._updating:
            self.set_attrs({"font_family": font.family()}, "Font")

    def _on_text_field_edited(self) -> None:
        if self._updating or len(self._names) != 1:
            return
        text = self.text_edit.text()
        attrs = self.ui.canvas.nodes.get(self._names[0], {}).get("attrs", {})
        if text == attrs.get("text", ""):
            return
        if not text.strip():
            self.ui.app.run_command("delete", self._names)
            return
        # Retyping the whole text from the inspector drops per-word formatting.
        self.set_attrs({"text": text, "runs": None}, "Edit text")

    def _apply_preset(self, index: int) -> None:
        self.text_preset_combo.setCurrentIndex(0)
        if index <= 0 or not self._names:
            return
        name = self.text_preset_combo.itemText(index)
        self.ui.app.run_command("text.apply_preset", name, self._names)

    def _clear_formatting(self) -> None:
        editor = self.editor()
        if editor is not None:
            editor.clear_formatting()
            return
        if self._names:
            self.ui.app.run_command("text.clear_formatting", self._names)

    def _pick_text_color(self) -> None:
        color = QColorDialog.getColor(self._text_color, self.window(), "Text color")
        if color.isValid():
            self._text_color = color
            self._refresh_swatches(True)
            self.apply_format("color", w.rgb(color), "Text color")

    def _pick_highlight(self) -> None:
        start = QColor(self._highlight_color)
        if start.alpha() == 0:
            start.setAlpha(160)
        color = QColorDialog.getColor(start, self.window(), "Highlight color",
                                      QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if color.isValid():
            if color.alpha() == 0:
                color.setAlpha(160)
            self._highlight_color = color
            self._refresh_swatches(True)
            self.apply_format("highlight_color", w.rgba(color), "Highlight color")

    def _refresh_swatches(self, highlight_on: bool) -> None:
        w.swatch(self.text_color_btn, self._text_color)
        w.swatch(self.highlight_btn, self._highlight_color, highlight_on)

    # ---- Populate ------------------------------------------------------------------------

    def refresh(self, info: SelectionInfo) -> None:
        self._names = [n["name"] for n in info.nodes if n["type"] == "annotation_text"]
        multi = len(self._names) > 1
        attrs = info.nodes[0]["attrs"] if info.nodes else {}
        self._updating = True
        try:
            self.text_edit.setEnabled(not multi)
            self.text_edit.setText("" if multi else str(attrs.get("text", "")))
            family = str(attrs.get("font_family", "") or "")
            self.font_combo.setCurrentFont(QFont(family) if family else QFont())
            self.font_size_spin.setValue(float(attrs.get("font_size", 14)))
            self.bold_btn.setChecked(bool(attrs.get("bold", False)))
            self.italic_btn.setChecked(bool(attrs.get("italic", False)))
            self.underline_btn.setChecked(bool(attrs.get("underline", False)))
            self.strike_btn.setChecked(bool(attrs.get("strike", False)))
            align = str(attrs.get("align", "left"))
            for key, button in self.align_btns.items():
                button.setChecked(key == align)
            self._text_color = w.color_from(attrs.get("color", [220, 220, 220]))
            highlight = w.color_from(attrs.get("highlight_color", list(NO_HIGHLIGHT)), tuple(NO_HIGHLIGHT))
            if highlight.alpha() > 0:
                self._highlight_color = highlight
            self._refresh_swatches(highlight.alpha() > 0)
            self._update_scope_label()
        finally:
            self._updating = False

    def _update_scope_label(self) -> None:
        editor = self.editor()
        if editor is None:
            hint = "Styles apply to the whole note · double-click a note to style selected words"
        elif editor.has_selection():
            hint = "Styling the selected text · Esc to finish editing"
        else:
            hint = "Editing: select words to style them, or styles apply to all text"
        self.text_scope_label.setText(hint)

    def on_format_changed(self, item: TextItem) -> None:
        if self._names != [item.node_name]:
            return
        self._updating = True
        try:
            for key, button in (("bold", self.bold_btn), ("italic", self.italic_btn),
                                ("underline", self.underline_btn), ("strike", self.strike_btn)):
                button.setChecked(bool(item.format_state(key) if item.editing else item.style_value(key)))
            size = item.format_state("font_size") if item.editing else item.style_value("font_size")
            self.font_size_spin.setValue(float(size))
        finally:
            self._updating = False
        self._update_scope_label()


class TextBoxSection(w.InspectorSection):
    def __init__(self, ui: Any, text_section_getter: Any, parent: Any) -> None:
        super().__init__("text_box", "Text box", parent)
        self.ui = ui
        self._updating = False
        self._names: list[str] = []
        body = self.body
        self._bg = QColor(0, 0, 0, 0)
        self._border = QColor(220, 220, 220)
        tb = lambda text, tip, checkable=False, width=28: w.make_tool_button(text, tip, checkable, width, body)  # noqa: E731
        self.text_bg_btn = tb("", "Box background color")
        self.text_border_btn = tb("", "Border color")
        self.text_fill_style_combo = w.make_combo(w.FILL_STYLE_CHOICES, body)
        self.text_border_style_combo = w.make_combo(w.LINE_STYLE_CHOICES, body)
        self.text_border_width_spin = w.make_spin(body, minimum=0.0, maximum=100.0, step=0.5, soft_range=(0.0, 20.0))
        self.text_radius_spin = w.make_spin(body, minimum=0.0, maximum=500.0, step=1.0, soft_range=(0.0, 60.0))
        self.text_padding_spin = w.make_spin(body, minimum=0.0, maximum=500.0, step=1.0, default=4.0,
                                             soft_range=(0.0, 60.0))
        self.text_width_spin = w.make_spin(body, minimum=0.0, maximum=20000.0, step=10.0, soft_range=(0.0, 1200.0))
        self.text_width_spin.setSpecialValueText("Auto")
        self.text_width_spin.setToolTip("Fixed box width — text wraps inside it. Drag a note's side handles to resize.")
        self.text_auto_width_btn = tb("Auto", "Fit the box to the text", width=40)
        form = self.form
        form.addRow("Width", w.hrow(body, self.text_width_spin, self.text_auto_width_btn))
        form.addRow("Fill", w.hrow(body, self.text_bg_btn, self.text_fill_style_combo))
        form.addRow("Border", w.hrow(body, self.text_border_btn, self.text_border_style_combo))
        form.addRow(w.ScrubLabel("Thickness", self.text_border_width_spin, 0.1, body), self.text_border_width_spin)
        form.addRow(w.ScrubLabel("Radius", self.text_radius_spin, 0.5, body), self.text_radius_spin)
        form.addRow(w.ScrubLabel("Padding", self.text_padding_spin, 0.5, body), self.text_padding_spin)
        self.text_bg_btn.clicked.connect(self._pick_bg)
        self.text_border_btn.clicked.connect(self._pick_border)
        self.text_fill_style_combo.currentIndexChanged.connect(
            lambda _i: self._set({"fill_style": self.text_fill_style_combo.currentData()}, "Fill style"))
        self.text_border_style_combo.currentIndexChanged.connect(
            lambda _i: self._set({"border_style": self.text_border_style_combo.currentData()}, "Border style"))
        for spin, attr, label in ((self.text_border_width_spin, "border_width", "Border width"),
                                  (self.text_radius_spin, "corner_radius", "Corner radius"),
                                  (self.text_padding_spin, "padding", "Padding"),
                                  (self.text_width_spin, "text_width", "Text box width")):
            spin.editingFinished.connect(lambda s=spin, a=attr, lbl=label: self._set({a: round(s.value(), 3)}, lbl))
            w.bind_edits(ui.app, spin, lambda _v, s=spin, a=attr, lbl=label: self._set({a: round(s.value(), 3)}, lbl),
                         label, commit=False)
        self.text_auto_width_btn.clicked.connect(lambda: self._set({"text_width": 0.0}, "Auto text width"))

    def _set(self, updates: dict[str, Any], label: str) -> None:
        if self._updating or not self._names:
            return
        self.ui.app.run_command("text.set_attrs", updates, self._names, [], label)

    def _pick_bg(self) -> None:
        start = QColor(self._bg)
        if start.alpha() == 0:
            start = QColor(40, 44, 52, 220)
        color = QColorDialog.getColor(start, self.window(), "Box background color",
                                      QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if color.isValid():
            self._bg = color
            updates: dict[str, Any] = {"background_color": w.rgba(color)}
            if self.text_fill_style_combo.currentData() == "none":
                updates["fill_style"] = "solid"
            self._set(updates, "Box background")

    def _pick_border(self) -> None:
        color = QColorDialog.getColor(self._border, self.window(), "Border color",
                                      QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if color.isValid():
            self._border = color
            updates: dict[str, Any] = {"border_color": w.rgba(color)}
            if self.text_border_width_spin.value() <= 0:
                updates["border_width"] = 1.5
            if self.text_border_style_combo.currentData() == "none":
                updates["border_style"] = "solid"
            self._set(updates, "Border color")

    def refresh(self, info: SelectionInfo) -> None:
        self._names = [n["name"] for n in info.nodes if n["type"] == "annotation_text"]
        attrs = info.nodes[0]["attrs"] if info.nodes else {}
        self._updating = True
        try:
            self._bg = w.color_from(attrs.get("background_color", [0, 0, 0, 0]), (0, 0, 0, 0))
            self._border = w.color_from(attrs.get("border_color", [220, 220, 220, 255]))
            w.set_combo(self.text_fill_style_combo, attrs.get("fill_style", "solid"))
            w.set_combo(self.text_border_style_combo, attrs.get("border_style", "solid"))
            self.text_border_width_spin.setValue(float(attrs.get("border_width", 0.0)))
            self.text_radius_spin.setValue(float(attrs.get("corner_radius", 0.0)))
            self.text_padding_spin.setValue(float(attrs.get("padding", 4.0)))
            self.text_width_spin.setValue(float(attrs.get("text_width", 0.0) or 0.0))
            w.swatch(self.text_bg_btn, self._bg, text="" if self._bg.alpha() else "∅")
            border_on = self.text_border_width_spin.value() > 0
            w.swatch(self.text_border_btn, self._border, border_on, text="" if border_on else "∅")
        finally:
            self._updating = False

    def on_item_changed(self, item: Any) -> None:
        if isinstance(item, TextItem):
            self.text_width_spin.blockSignals(True)
            self.text_width_spin.setValue(item.box_width())
            self.text_width_spin.blockSignals(False)


class TextHandles:
    """Gizmo provider: a note's side handles set its box width (text reflows)."""

    node_type = "annotation_text"
    labels = {"text_width": "Resize text box"}

    def build(self, gizmo: Any, view: Any, item: Any, g: dict[str, Any]) -> None:
        vp = g["corners_vp"]
        g["handles_vp"].pop("tm", None)
        g["handles_vp"].pop("bm", None)
        g["handles_vp"]["mr"] = (vp[1] + vp[2]) / 2.0
        g["handles_vp"]["ml"] = (vp[3] + vp[0]) / 2.0
        return None

    def start(self, gizmo: Any, handle: str, g: dict[str, Any], item: Any) -> str | None:
        return "text_width" if handle in ("ml", "mr") else None

    def drag(self, gizmo: Any, op: dict[str, Any], m: QPointF, modifiers: Any, view: Any) -> None:
        """Width is measured in the note's own (unscaled) units, so it works for
        rotated and scaled notes; dragging the left handle keeps the right edge."""
        item, s0, l0, _parent_inv, _rotation = op["targets"][0]
        start = op["start_rect"]
        m_local = s0.inverted()[0].map(m)
        width = m_local.x() - start.left() if op["handle"] == "mr" else start.right() - m_local.x()
        width = max(item.min_box_width(), width)
        item.set_box_width(width)
        if op["handle"] == "ml":
            shift = start.right() - width - start.left()
            item.set_local_transform(QTransform.fromTranslate(shift, 0.0) * l0)
        item._notify_moved()
        gizmo.ui.status(f"Text box width {width:.0f}px · double-click the handle for auto width")

    def double_click(self, gizmo: Any, view: Any, g: dict[str, Any], handle: str) -> bool:
        if handle not in ("ml", "mr"):
            return False
        item = g["targets"][0]
        canvas = gizmo.ui.canvas
        canvas.begin_interaction()
        item.set_box_width(0.0)
        item._notify_moved()
        canvas.end_interaction("Auto text width")
        view.viewport().update()
        return True


def activate_text_ui(ui: Any) -> None:
    session = ui.session
    canvas = ui.canvas
    service = TextService(ui)
    ui.provide_service("text", service)

    def committed(name: str, text: str, runs: Any) -> None:
        if not session.scene.has(name):
            return
        if not text.strip():
            # An emptied note is removed, like most canvas apps do.
            QTimer.singleShot(0, lambda: session.scene.has(name) and ui.app.run_command(
                "clipboard.delete", [name]))
            return
        ui.app.run_command("text.commit", name, text, runs)

    def factory(_canvas: Any, node: dict[str, Any]) -> TextItem:
        item = TextItem(node["name"])
        item.apply_attrs(node.get("attrs", {}))
        item.on_text_committed = committed
        item.on_format_changed = service.format_changed
        item.on_link_requested = lambda _item: ui.app.run_command("text.link_dialog")
        return item

    ui.node_ui("annotation_text", factory=factory, label="Text note", icon="text", badge=("#9ab87a", "T"),
               double_click=lambda view, item: "default")
    ui.contribute("gizmo.handles", TextHandles())
    ui.context_key("editing.text", lambda: service.editing_item() is not None)
    ui.escape_handler(service.commit_editing, order=0)

    def flush() -> None:
        service.commit_editing()

    ui.flush_handler(flush)

    def text_selected(info: SelectionInfo) -> bool:
        return bool(info.nodes) and all(n["type"] == "annotation_text" for n in info.nodes)

    sections: dict[str, Any] = {}

    def make_text(parent: Any, _inspector: Any) -> TextSection:
        section = TextSection(ui, service, parent)
        sections["text"] = section
        return section

    ui.contribute("inspector.sections", InspectorSection("text", "Text", 32, text_selected, make_text))
    ui.contribute("inspector.sections", InspectorSection(
        "text_box", "Text box", 34, text_selected, lambda parent, _i: TextBoxSection(ui, lambda: sections, parent)))

    # ---- Ingest: plain text becomes a note ---------------------------------------------------

    def ingest_text(_view: Any, pos: QPointF, mime: Any) -> Any:
        text = mime.text().strip() if mime.hasText() else ""
        if not text:
            return None
        session.services.require("notes").create(text, pos.x(), pos.y())
        return 0, 1

    ui.contribute("ingest.handlers", IngestHandler("text.note", 90, lambda m: m.hasText(),
                                                   lambda m: "Drop to add a text note", ingest_text))

    def export_text(names: list[str], mime: Any) -> None:
        texts = [str(canvas.nodes[n]["attrs"].get("text", "")) for n in names
                 if canvas.nodes.get(n, {}).get("type") == "annotation_text"]
        if texts:
            mime.setText("\n\n".join(texts))

    ui.contribute("clipboard.exporters", export_text)

    # ---- Add text (T) ------------------------------------------------------------------------

    def add_note(text: str = "Text") -> str | None:
        """Create a note at the mouse and start editing it; with one image selected
        the note is anchored (parented) to it."""
        names = session.selection.names
        anchor = names[0] if len(names) == 1 and canvas.nodes.get(names[0], {}).get("type") == "image" else None
        position = ui.app.scene_pos_for_new_content()
        note = ui.app.run_command("text.create", text, [position.x(), position.y()], anchor)
        if note is None:
            return None
        canvas.refresh(force=True)
        item = canvas.item(note)
        if isinstance(item, TextItem):
            view = ui.app.active_view()
            if view is not None:
                view.setFocus()
            item.start_edit()
            ui.status("Type your note · Esc or click outside to finish")
        return note

    ui.command("add", add_note, undoable=False, echo=False)
    ui.action("text.add", "text.add", "Add Text", icon="text", keywords="note label type write annotation",
              category="Create")
    ui.bind("T", "text.add")
    ui.menu("canvas", "text.add", group="create", order=20, when="selection.count == 0")
    ui.contribute("palette.buttons", PaletteButton("text.add", "Text", "text", order=20))

    activate_note_extras(ui, service)


def selected_notes(ui: Any) -> list[str]:
    nodes = ui.canvas.nodes
    return [n for n in ui.session.selection.names if nodes.get(n, {}).get("type") == "annotation_text"]


def activate_note_extras(ui: Any, service: TextService) -> None:
    """Links (open, set), checklist boxes and copy / paste text style."""
    app = ui.app
    session = ui.session

    # ---- Links ----------------------------------------------------------------------------

    def resolve_link(href: str) -> QUrl | Path:
        """A web address, or the file a link points to (relative to the scene's folder)."""
        text = href.strip()
        if text.lower().startswith("www."):
            text = "https://" + text
        url = QUrl(text)
        scheme = url.scheme().lower()
        if len(scheme) > 1 and scheme != "file":  # "c" of "C:\\…" is a drive, not a scheme
            return url
        path = Path(url.toLocalFile()) if scheme == "file" else Path(os.path.expanduser(text))
        return path if path.is_absolute() else Path(session.resolve_path(str(path)))

    def open_board(path: Path) -> bool:
        files = session.services.get("files")
        if path.suffix.lower() == ".pxref":
            if files is None:
                raise CommandError("Opening scenes needs the Files plugin")
            return bool(files.open_path(str(path)))
        if session.commands.get("pureref.open") is None:
            raise CommandError("Opening PureRef boards needs the PureRef plugin")
        if files is not None and not files.may_discard():
            return False
        if app.run_command("pureref.open", str(path)) is None:
            return False
        QTimer.singleShot(0, lambda: app.run_command("navigation.frame_all"))
        return True

    def open_link(href: str) -> bool:
        """Open a link: web pages in the browser, scenes and PureRef boards here, other files in their app."""
        target = resolve_link(str(href))
        if isinstance(target, QUrl):
            opened = QDesktopServices.openUrl(target)
            ui.status(f"Opened {target.toString()}" if opened else f"Couldn't open {target.toString()}")
            return opened
        if not target.exists():
            raise CommandError(f"Not found: {target}")
        if target.suffix.lower() in (".pxref", ".pur"):
            return open_board(target)
        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        ui.status(f"Opened {target}" if opened else f"Couldn't open {target}")
        return opened

    def current_link() -> str:
        editor = service.editing_item()
        if editor is not None:
            return editor.link_at_cursor()
        for name in selected_notes(ui):
            for run in ui.canvas.nodes[name]["attrs"].get("runs") or ():
                if run.get("link"):
                    return str(run["link"])
        return ""

    def apply_link(href: str) -> bool:
        editor = service.editing_item()
        if editor is not None:
            editor.set_link(href)
            ui.status(f"Linked to {href}" if href else "Link removed")
            return True
        names = selected_notes(ui)
        if not names:
            raise CommandError("Select a text note, or select text while typing in one")
        return app.run_command("text.set_link", href, names) is not None

    def link_dialog() -> bool:
        """Link the selected text (while typing, Ctrl+K) or the selected notes to a web address or file."""
        if service.editing_item() is None and not selected_notes(ui):
            raise CommandError("Select a text note, or select text while typing in one")
        href, ok = QInputDialog.getText(app.main_window(), "Link",
                                        "Web address or file path (empty removes the link):", text=current_link())
        return apply_link(href.strip()) if ok else False

    def link_file_dialog() -> bool:
        """Link the selected text or notes to a file: a scene or PureRef board opens here, others in their app."""
        if service.editing_item() is None and not selected_notes(ui):
            raise CommandError("Select a text note, or select text while typing in one")
        path, _ = QFileDialog.getOpenFileName(app.main_window(), "Link to File", str(session.base_dir()),
                                              "Boards (*.pxref *.pur);;All files (*)")
        return apply_link(path) if path else False

    for name, fn in (("open_link", open_link), ("link_dialog", link_dialog), ("link_file_dialog", link_file_dialog)):
        ui.command(name, fn, undoable=False, echo=False)

    # ---- Copy / paste text style --------------------------------------------------------------

    def copy_style() -> dict[str, Any]:
        """Copy the text style: at the cursor while typing, else the selected note's look (Ctrl+Shift+C)."""
        editor = service.editing_item()
        if editor is not None:
            style = {key: fast_copy(editor.format_state(key)) for key in TEXT_STYLE_KEYS}
            family = ui.canvas.nodes.get(editor.node_name, {}).get("attrs", {}).get("font_family")
            if family:
                style["font_family"] = family
        else:
            names = selected_notes(ui)
            if not names:
                raise CommandError("Select a text note to copy its style")
            attrs = ui.canvas.nodes[names[0]]["attrs"]
            style = {key: fast_copy(attrs[key]) for key in COPIED_NOTE_KEYS if key in attrs}
        service.copied_style = style
        ui.status("Text style copied · Ctrl+Shift+V pastes it onto selected text or notes")
        return style

    def paste_style() -> bool:
        """Paste the copied text style onto the selected text (while typing) or the selected notes (Ctrl+Shift+V)."""
        style = service.copied_style
        if not style:
            raise CommandError("Copy a text style first (Ctrl+Shift+C)")
        editor = service.editing_item()
        if editor is not None and editor.has_selection():
            for key in TEXT_STYLE_KEYS:
                if key in style:
                    editor.merge_format(key, style[key])
            ui.status("Text style pasted onto the selection")
            return True
        if editor is not None:
            service.commit_editing()
            names = [editor.node_name] if session.scene.has(editor.node_name) else []
        else:
            names = selected_notes(ui)
        if not names:
            raise CommandError("Select text notes to paste the style onto")
        strip = tuple(key for key in TEXT_STYLE_KEYS if key in style)
        return app.run_command("text.set_attrs", fast_copy(style), names, strip, "Paste text style") is not None

    ui.command("copy_style", copy_style, alias="copy_text_style", undoable=False, echo=False)
    ui.command("paste_style", paste_style, alias="paste_text_style", undoable=False, echo=False)

    a = ui.action
    a("text.link", "text.link_dialog", "Link…", keywords="hyperlink url web address file open note", category="Text")
    a("text.link_file", "text.link_file_dialog", "Link to File…", keywords="hyperlink file board scene pur note",
      category="Text")
    a("text.checklist", "text.toggle_checklist", "Checklist", keywords="todo check box tick list note",
      category="Text")
    a("text.copy_style", "text.copy_style", "Copy Text Style", keywords="format formatting font", category="Text")
    a("text.paste_style", "text.paste_style", "Paste Text Style", keywords="format formatting font", category="Text")
    ui.bind("Ctrl+Shift+C", "text.copy_style")
    ui.bind("Ctrl+Shift+V", "text.paste_style")
    ui.context_key("text.selected", lambda: bool(selected_notes(ui)))
    for order, key in enumerate(("text.link", "text.link_file", "text.checklist", "text.copy_style",
                                 "text.paste_style")):
        ui.menu("canvas", key, path="Text", group="text.extras", order=order, when="text.selected")
    ui.place_submenu("canvas", "Text", "transform", 2, icon="text", when="text.selected")

    # ---- Clicking boxes and links on the canvas --------------------------------------------------

    class NoteClicks(InputHandler):
        """With the Select tool: a click on a checklist box ticks it, Ctrl+click opens a link."""

        def __init__(self) -> None:
            self.pressed = False
            self.hint = ""

        def note_at(self, e: PointerEvent) -> TextItem | None:
            if app.tools.active_id != "select":
                return None
            item = e.item
            return item if isinstance(item, TextItem) and not item.editing else None

        def hover(self, e: PointerEvent) -> None:
            item = self.note_at(e)
            hint = ""
            if item is not None:
                local = item.mapFromScene(e.scene_pos)
                if item.checkbox_at(local) is not None and not item.locked:
                    e.set_cursor(Qt.PointingHandCursor)
                else:
                    href = item.link_at(local)
                    if href:
                        hint = f"Ctrl+click to open {href}"
                        if e.ctrl:
                            e.set_cursor(Qt.PointingHandCursor)
            if hint != self.hint:
                self.hint = hint
                if hint:
                    ui.status(hint)

        def pointer(self, e: PointerEvent) -> str | None:
            if self.pressed:  # swallow the rest of a click we took
                if e.kind == "release":
                    self.pressed = False
                return HANDLED
            # A quick second click on a box arrives as "double": tick again, don't start typing.
            if e.kind not in ("press", "double") or e.button != Qt.LeftButton:
                return None
            item = self.note_at(e)
            if item is None:
                return None
            local = item.mapFromScene(e.scene_pos)
            line = item.checkbox_at(local)
            if line is not None and not item.locked and not (e.ctrl or e.shift):
                app.run_command("text.toggle_check", item.node_name, line)
                self.pressed = True
                return HANDLED
            href = item.link_at(local) if e.ctrl and e.kind == "press" else ""
            if href:
                # After this click: opening a board replaces the scene.
                QTimer.singleShot(0, lambda: app.run_command("text.open_link", href))
                self.pressed = True
                return HANDLED
            return None

    ui.input_handler(NoteClicks(), 520)
