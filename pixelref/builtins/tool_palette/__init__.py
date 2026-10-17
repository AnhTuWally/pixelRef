"""The Tools view: the tools and add-buttons plugins registered, arranged by the user.

Plugins give the defaults (tools in the Tools section, ``palette.buttons`` in the
Add section, both by ``order``); the user can reorder buttons, move them into
their own sections, hide them, add any other action (a command, a script, a
macro) and give any button a key: right-click a button, or *Customize Palette…*.
See :mod:`.layout` for how a plugin added later fits into a customized palette.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pixelref.plugin_api import CommandError
from pixelref.plugin_api.ui import PanelType, icon
from pixelref.plugin_api.ui import widgets as w

from .layout import HOME_SECTIONS, PaletteLayout, Section


class ToolPalette(QWidget):
    def __init__(self, ui: Any, service: "PaletteService") -> None:
        super().__init__()
        self.ui = ui
        self.service = service
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        # Buttons reflow into fewer columns instead of scrolling sideways.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll = scroll
        outer.addWidget(scroll)
        content = QWidget()
        content.setContextMenuPolicy(Qt.CustomContextMenu)
        content.customContextMenuRequested.connect(lambda _pos: self.show_menu(None))
        scroll.setWidget(content)
        self.content = content
        self.layout_ = QVBoxLayout(content)
        self.layout_.setContentsMargins(8, 2, 6, 6)
        self.layout_.setSpacing(4)

        self.tool_options = QWidget(content)
        self.tool_options.setObjectName("toolOptions")
        options_layout = QVBoxLayout(self.tool_options)
        options_layout.setContentsMargins(8, 6, 8, 8)
        options_layout.setSpacing(6)
        header = QWidget(self.tool_options)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(3)
        self.tool_options_title = QLabel("", header)
        self.tool_options_title.setObjectName("sectionHeader")
        header_layout.addWidget(self.tool_options_title, 1)
        # Save the options as your default (kept across restarts) / reset them.
        self.save_defaults_btn = w.make_tool_button("★", "Save these options as your default for this tool",
                                                    width=26, parent=header)
        self.save_defaults_btn.clicked.connect(self.save_defaults)
        self.reset_btn = w.make_tool_button("⟲", "Reset to your default (⋯: factory defaults)", width=26,
                                            parent=header)
        self.reset_btn.clicked.connect(lambda: self.reset_options(factory=False))
        self.more_btn = w.make_tool_button("⋯", "Reset to the factory defaults · forget your defaults", width=26,
                                           parent=header)
        self.more_btn.clicked.connect(lambda: w.fields.show_menu(
            self.more_btn, self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft()), [
                ("Save as my default", self.save_defaults),
                ("Reset to my default", lambda: self.reset_options(factory=False)),
                ("Reset to factory defaults", lambda: self.reset_options(factory=True)),
                (None, None),
                ("Forget my defaults", self.forget_defaults)]))
        header_layout.addWidget(self.save_defaults_btn)
        header_layout.addWidget(self.reset_btn)
        header_layout.addWidget(self.more_btn)
        options_layout.addWidget(header)
        self.options_stack = QStackedWidget(self.tool_options)
        options_layout.addWidget(self.options_stack)
        self.tool_options.setVisible(False)
        self.layout_.addWidget(self.tool_options)
        self.layout_.addStretch()

        self.buttons: dict[str, w.ShortcutButton] = {}  # action key -> button
        self.section_of: dict[str, str] = {}  # action key -> section id
        self.section_widgets: list[tuple[str, QWidget, QGridLayout, list[w.ShortcutButton]]] = []
        self.option_panels: dict[str, QWidget | None] = {}
        self.rebuild()

    @property
    def tool_buttons(self) -> dict[str, w.ShortcutButton]:
        """The buttons of tools, by tool id."""
        return {key[5:]: b for key, b in self.buttons.items() if key.startswith("tool.") and key[5:] in self.ui.tools.tools}

    def rebuild(self) -> None:
        for _sid, box, _grid, _buttons in self.section_widgets:
            self.layout_.removeWidget(box)
            box.deleteLater()
        self.section_widgets = []
        self.buttons = {}
        self.section_of = {}
        specs = self.service.button_specs()
        for section in self.service.sections():
            box = QWidget(self.content)
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 0, 0, 0)
            box_layout.setSpacing(4)
            header = w.section_header(box, section.title)
            header.setContextMenuPolicy(Qt.CustomContextMenu)
            header.customContextMenuRequested.connect(lambda _pos, sid=section.id: self.show_menu(None, sid))
            box_layout.addWidget(header)
            grid_widget = QWidget(box)
            grid = QGridLayout(grid_widget)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(3)
            box_layout.addWidget(grid_widget)
            buttons = [self._make_button(key, specs.get(key), grid_widget) for key in section.items]
            for key, button in zip(section.items, buttons):
                self.buttons[key] = button
                self.section_of[key] = section.id
            self.section_widgets.append((section.id, box, grid, buttons))
            self.layout_.insertWidget(self.layout_.count() - 1, box)
        self._place_buttons()
        # Option panels of tools that went away.
        for tool_id in list(self.option_panels):
            if tool_id not in self.ui.tools.tools:
                panel = self.option_panels.pop(tool_id)
                if panel is not None:
                    self.options_stack.removeWidget(panel)
                    panel.deleteLater()
        self.sync()

    def _make_button(self, key: str, spec: Any, parent: QWidget) -> w.ShortcutButton:
        app = self.ui.app
        action = app.actions[key]
        tool = self.ui.tools.get(key[5:]) if key.startswith("tool.") else None
        if tool is not None:
            label, icon_name, tip = tool.title, tool.icon, tool.tooltip or tool.title
        elif spec is not None:
            label, icon_name, tip = spec.label, spec.icon, w.action_label(app, key)
        else:
            label, icon_name, tip = w.action_label(app, key), "", action.toolTip() or w.action_label(app, key)
        button = w.ShortcutButton(label, app.actions.shortcut_label(key), parent)
        button.setObjectName("toolBtn")
        if icon_name:
            button.setIcon(icon(icon_name))
        elif not action.icon().isNull():
            button.setIcon(action.icon())
        button.setCheckable(action.isCheckable())
        button.setProperty("baseTip", tip)
        button.setProperty("tipShowsKey", tool is None)  # a tool's key is on its button only
        self._set_tip(key, button)
        button.clicked.connect(lambda _checked=False, k=key: self._clicked(k))
        button.setContextMenuPolicy(Qt.CustomContextMenu)
        button.customContextMenuRequested.connect(lambda _pos, k=key: self.show_menu(k))
        return button

    def refresh_shortcuts(self) -> None:
        """Show the buttons' keys again (after the keymap changed)."""
        actions = self.ui.app.actions
        for key, button in self.buttons.items():
            button.set_shortcut_text(actions.shortcut_label(key))
            self._set_tip(key, button)
        self._place_buttons()

    def _set_tip(self, key: str, button: w.ShortcutButton) -> None:
        tip = button.property("baseTip")
        full = self.ui.app.actions.shortcut_label(key, compact=False) if button.property("tipShowsKey") else ""
        button.setToolTip(f"{tip} ({full})" if full else tip)

    def _clicked(self, key: str) -> None:
        self.ui.app.trigger(key)
        self.sync()  # a checkable action that isn't a tool: show its new state

    # ---- Layout: as many columns (up to 3) as the view's width allows ------------------------

    def _fitting_columns(self, buttons: list[Any]) -> int:
        if not buttons:
            return 3
        widest = max(max(b.sizeHint().width(), b.minimumSizeHint().width()) for b in buttons)
        available = self.scroll.viewport().width() - 14  # the content margins
        for columns in (3, 2):
            if columns * widest + (columns - 1) * 3 <= available:
                return columns
        return 1

    def _place_buttons(self, force: bool = True) -> None:
        # One column count for every section, so the buttons line up from section to section.
        columns = self._fitting_columns(list(self.buttons.values()))
        for _sid, _box, grid, buttons in self.section_widgets:
            if not force and grid.property("columns") == columns:
                continue
            grid.setProperty("columns", columns)
            for button in buttons:
                grid.removeWidget(button)
            for index, button in enumerate(buttons):
                grid.addWidget(button, index // columns, index % columns)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._place_buttons(force=False)

    def sync(self) -> None:
        tool = self.ui.tools.active
        self.ui.app.actions.refresh_states()
        for key, button in self.buttons.items():
            if button.isCheckable():
                button.setChecked(self.ui.app.actions[key].isChecked())
        # The options sit under the section holding the active tool (else under the first one).
        section = self.section_of.get(f"tool.{tool.id}") if tool is not None else None
        boxes = [box for sid, box, _g, _b in self.section_widgets]
        anchor = next((box for sid, box, _g, _b in self.section_widgets if sid == section), boxes[0] if boxes else None)
        self.layout_.removeWidget(self.tool_options)
        self.layout_.insertWidget(self.layout_.indexOf(anchor) + 1 if anchor is not None else 0, self.tool_options)
        if tool is None:
            self.tool_options.setVisible(False)
            return
        if tool.id not in self.option_panels:
            panel = tool.options_panel(self.options_stack)
            self.option_panels[tool.id] = panel
            if panel is not None:
                self.options_stack.addWidget(panel)
        panel = self.option_panels.get(tool.id)
        self.tool_options.setVisible(panel is not None)
        if panel is not None:
            self.options_stack.setCurrentWidget(panel)
            self.tool_options_title.setText((tool.options_title or f"{tool.title} options").upper())
        has_options = bool(getattr(tool, "options", None))
        for button in (self.save_defaults_btn, self.reset_btn, self.more_btn):
            button.setVisible(has_options)

    # ---- Customizing (right-click) -----------------------------------------------------------

    def show_menu(self, key: str | None, section_id: str | None = None) -> None:
        menu = self.build_menu(key, section_id)
        menu.exec(QCursor.pos())

    def build_menu(self, key: str | None, section_id: str | None = None) -> QMenu:
        """The right-click menu of a button (``key``), or of a section / the empty space."""
        app, service = self.ui.app, self.service
        menu = QMenu(self)
        if key is not None:
            section_id = self.section_of.get(key)
            label = w.action_label(app, key)
            menu.addSection(label)
            menu.addAction("Set Shortcut…", lambda: w.capture_shortcut(app, key, self, label=label))
            if app.keymap.shortcuts(key):
                menu.addAction("Add Another Shortcut…", lambda: w.capture_shortcut(app, key, self, add=True,
                                                                                   label=label))
                menu.addAction("Remove Shortcut", lambda: w.run_keymap_edit(lambda: app.keymap.assign(key, []), self))
            if app.keymap.changed(key) and key not in app.keymap.user_targets:
                menu.addAction("Reset Shortcut", lambda: w.run_keymap_edit(lambda: app.keymap.reset_shortcuts(key),
                                                                           self))
            menu.addSeparator()
            move = menu.addMenu("Move to")
            for section in service.all_sections()[0]:
                action = move.addAction(section.title or section.id,
                                        lambda sid=section.id: service.move(key, sid))
                action.setEnabled(section.id != section_id)
            move.addSeparator()
            move.addAction("New Section…", lambda: self._new_section(key))
            if service.is_default(key):
                menu.addAction("Hide from Palette", lambda: service.hide(key))
            else:
                menu.addAction("Remove from Palette", lambda: service.remove(key))
        else:
            menu.addSection("Tools palette")
        menu.addAction("Add Command…", lambda: self.add_command(section_id))
        hidden = [k for k in service.all_sections()[1] if k in app.actions]
        if hidden:
            show = menu.addMenu("Show Hidden")
            for hidden_key in hidden:
                show.addAction(w.action_label(app, hidden_key), lambda k=hidden_key: service.show(k))
        menu.addSeparator()
        menu.addAction("Customize Palette…", self.customize)
        reset = menu.addAction("Reset Palette", service.reset)
        reset.setEnabled(service.layout.customized())
        return menu

    def _new_section(self, key: str | None = None) -> str | None:
        title, ok = QInputDialog.getText(self, "New Section", "Section name:")
        if not ok or not title.strip():
            return None
        sid = self.service.add_section(title.strip())
        if key is not None:
            self.service.move(key, sid)
        return sid

    def add_command(self, section_id: str | None = None) -> None:
        from .customize import pick_action

        key = pick_action(self.ui.app, self, exclude=set(self.service.all_keys()))
        if key:
            self.service.add(key, section_id or "tools")

    def customize(self) -> None:
        from .customize import CustomizeDialog

        CustomizeDialog(self.ui, self.service, self.window()).exec()

    # ---- Defaults ----------------------------------------------------------------------------

    def save_defaults(self) -> None:
        tool = self.ui.tools.active
        if tool is not None and self.ui.app.run_command("tools.save_defaults", tool.id) is not None:
            self.ui.toast(f"{tool.title}: these options are now your default")

    def reset_options(self, factory: bool) -> None:
        tool = self.ui.tools.active
        if tool is not None and self.ui.app.run_command("tools.reset_options", tool.id, factory=factory) is not None:
            self.ui.toast(f"{tool.title}: options reset to {'the factory' if factory else 'your'} defaults")

    def forget_defaults(self) -> None:
        tool = self.ui.tools.active
        if tool is not None:
            self.ui.app.run_command("tools.clear_defaults", tool.id)
            self.ui.toast(f"{tool.title}: your defaults are forgotten (Reset uses the factory ones)")


class PaletteService:
    """The palette's layout (shared by every Tools view and the Tools menu) and its edits."""

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.palettes: list[ToolPalette] = []
        self.layout = PaletteLayout(ui.settings)

    def palette(self) -> ToolPalette | None:
        """The panel on screen (sidebar, floating card or pop-up), else any."""
        visible = [p for p in self.palettes if p.isVisible()]
        return (visible or self.palettes or [None])[0]

    # ---- Reading -----------------------------------------------------------------------------

    def button_specs(self) -> dict[str, Any]:
        return {spec.key: spec for spec in self.ui.contributions("palette.buttons")}

    def defaults(self) -> dict[str, list[str]]:
        """Home section -> its default buttons in order (what plugins registered)."""
        return {
            "tools": [f"tool.{t.id}" for t in self.ui.tools.ordered() if not t.hidden],
            "add": [spec.key for spec in sorted(self.ui.contributions("palette.buttons"), key=lambda b: b.order)],
        }

    def is_default(self, key: str) -> bool:
        return any(key in keys for keys in self.defaults().values())

    def all_sections(self) -> tuple[list[Section], set[str]]:
        """Every section with every key (hidden ones and those whose plugin is off), and the hidden keys."""
        return self.layout.resolve(self.defaults())

    def all_keys(self) -> list[str]:
        return [key for section in self.all_sections()[0] for key in section.items]

    def sections(self) -> list[Section]:
        """What the palette shows: the sections with their available, shown keys (empty ones left out)."""
        sections, hidden = self.all_sections()
        actions = self.ui.app.actions
        shown = [Section(s.id, s.title, [k for k in s.items if k not in hidden and k in actions]) for s in sections]
        return [s for s in shown if s.items]

    def tool_keys(self) -> list[str]:
        """The shown tools' action keys in palette order (the Tools menu)."""
        tools = self.ui.tools.tools
        return [k for s in self.sections() for k in s.items if k.startswith("tool.") and k[5:] in tools]

    # ---- Editing (each edit is kept in the user settings) ------------------------------------

    def edit(self, change: Any) -> Any:
        sections, hidden = self.all_sections()
        result = change(sections, hidden)
        self.layout.save(sections, hidden)
        self.changed()
        return result

    def changed(self) -> None:
        for palette in list(self.palettes):
            palette.rebuild()

    def _section(self, sections: list[Section], section_id: str) -> Section:
        section = next((s for s in sections if s.id == section_id), None)
        if section is None:
            known = ", ".join(s.id for s in sections)
            raise CommandError(f"No palette section '{section_id}' (sections: {known})")
        return section

    @staticmethod
    def _take(sections: list[Section], key: str) -> None:
        for section in sections:
            if key in section.items:
                section.items.remove(key)

    def add(self, key: str, section: str = "tools", index: int = -1) -> str:
        """Put any action on the palette (or move it there, shown)."""
        if key not in self.ui.app.actions:
            raise CommandError(f"No action '{key}' (the ID column of Keyboard Shortcuts, F1)")
        return self.move(key, section, index)

    def move(self, key: str, section: str, index: int = -1) -> str:
        """Move a button to a section (``index``: its place there, -1 at the end); shows it."""

        def change(sections: list[Section], hidden: set[str]) -> str:
            target = self._section(sections, section)
            self._take(sections, key)
            target.items.insert(len(target.items) if index < 0 else min(index, len(target.items)), key)
            hidden.discard(key)
            return key

        return self.edit(change)

    def hide(self, key: str) -> None:
        """Hide a button (it keeps its place; ``show`` brings it back)."""
        if key not in self.all_keys():
            raise CommandError(f"'{key}' isn't on the palette")
        self.edit(lambda _sections, hidden: hidden.add(key))

    def show(self, key: str) -> None:
        """Show a hidden button again."""
        self.edit(lambda _sections, hidden: hidden.discard(key))

    def remove(self, key: str) -> None:
        """Take a button off the palette: the default ones are hidden, others removed."""
        if self.is_default(key):
            self.hide(key)
        else:
            self.edit(lambda sections, hidden: (self._take(sections, key), hidden.discard(key)))

    def add_section(self, title: str, index: int = -1) -> str:
        """A new (empty) section; returns its id."""

        def change(sections: list[Section], _hidden: set[str]) -> str:
            ids = {s.id for s in sections}
            number = 1
            while f"section{number}" in ids:
                number += 1
            section = Section(f"section{number}", title)
            sections.insert(len(sections) if index < 0 else min(index, len(sections)), section)
            return section.id

        return self.edit(change)

    def rename_section(self, section: str, title: str) -> None:
        """Give a section (yours, or Tools / Add) another title."""
        self.edit(lambda sections, _hidden: setattr(self._section(sections, section), "title", title))

    def remove_section(self, section: str) -> None:
        """Remove one of your sections; its default buttons go back home (hidden), others go."""
        if section in HOME_SECTIONS:
            raise CommandError(f"The {HOME_SECTIONS[section]} section can be renamed and emptied, not removed")
        defaults = self.defaults()

        def change(sections: list[Section], hidden: set[str]) -> None:
            removed = self._section(sections, section)
            sections.remove(removed)
            for home, keys in defaults.items():
                home_section = next((s for s in sections if s.id == home), None)
                for key in removed.items:
                    if key in keys and home_section is not None:
                        home_section.items.append(key)
                        hidden.add(key)

        self.edit(change)

    def set_layout(self, sections: list[dict[str, Any]], hidden: Any = ()) -> None:
        """Replace the whole layout: ``[{"id", "title", "items": [action keys]}]`` and the hidden keys."""
        parsed = [Section(str(s["id"]), str(s.get("title") or s["id"]), [str(k) for k in s.get("items", [])])
                  for s in sections]
        self.layout.save(parsed, set(hidden))
        self.changed()

    def reset(self) -> None:
        """Back to the plugins' default palette."""
        self.layout.reset()
        self.changed()


def activate_ui(ui: Any) -> None:
    ui.declare_extension_point("palette.buttons", "PaletteButton: buttons in the Add row of the Tools view")
    service = PaletteService(ui)
    ui.provide_service("tool_palette", service)

    def make(_parent: Any = None) -> ToolPalette:
        palette = ToolPalette(ui, service)
        service.palettes.append(palette)
        return palette

    def dispose(widget: QWidget) -> None:
        if widget in service.palettes:
            service.palettes.remove(widget)

    ui.panel(PanelType(id="tool_palette", title="Tools", icon="tools", color="#6ea8fe", factory=make,
                       dispose=dispose, order=0, popup_keys=("Alt+T",), popup_size=(300, 360),
                       popup_closes_on=("tool.changed",), float_size=(300, 300), stretch=0,
                       keywords="tools palette brush options"))

    def layout() -> dict[str, Any]:
        """The palette: its sections (id, title, action keys in order) and the hidden keys."""
        sections, hidden = service.all_sections()
        return {"sections": [s.as_dict() for s in sections], "hidden": sorted(hidden)}

    def customize() -> None:
        """Open Customize Palette: order, sections, hidden buttons and keys."""
        palette = service.palette()
        if palette is None:
            palette = make()
        palette.customize()

    for name, fn in (("layout", layout), ("set_layout", service.set_layout), ("add", service.add),
                     ("move", service.move), ("hide", service.hide), ("show", service.show),
                     ("remove", service.remove), ("add_section", service.add_section),
                     ("rename_section", service.rename_section), ("remove_section", service.remove_section),
                     ("reset", service.reset), ("customize", customize)):
        ui.command(name, fn, undoable=False, echo=name not in ("layout", "customize"))
    ui.action("tool_palette.customize", "tool_palette.customize", "Customize Tools Palette…", icon="tools",
              keywords="tools palette buttons order hide sections shortcut keys", category="Tools")
    ui.menu("canvas", "tool_palette.customize", path="Tools", group="tools.customize", when="selection.count == 0")

    def sync(*_args: Any) -> None:
        for palette in list(service.palettes):
            palette.sync()

    def refresh_shortcuts() -> None:
        for palette in list(service.palettes):
            palette.refresh_shortcuts()

    ui.track(ui.tools.watch_registry(service.changed))
    ui.track(ui.app.actions.watch(service.changed))
    ui.track(ui.session.extensions.watch("palette.buttons", service.changed))
    ui.track(ui.app.keymap.watch(refresh_shortcuts))
    ui.on_ui("tool.changed", sync)
