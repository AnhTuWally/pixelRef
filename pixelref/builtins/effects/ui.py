"""Effects in the desktop app: drawing (engine.py), the Effects section of the
Attribute Editor, actions, menus and the cache commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pixelref.plugin_api import CAP_CONTAINER, Attr
from pixelref.plugin_api.contracts import InspectorSection, SelectionInfo
from pixelref.plugin_api.ui import widgets as w

from .engine import EffectEngine, EffectFilter
from .processors import BUILTIN_PROCESSORS

SECTION_ORDER = 35  # after Image (30), before the generated attributes


def cache_folder() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.CacheLocation) or str(Path.home() / ".cache" / "pixelRef")
    return Path(base) / "effects"


# ---- Attribute Editor ---------------------------------------------------------------------------------


MIX = Attr("mix", "float", 1.0, minimum=0.0, maximum=1.0, step=0.05, precision=0, unit="%", display_scale=100.0,
           doc="How much of this effect is mixed over what's under it")


class EffectRow(QFrame):
    """One effect of the stack: on/off, order, reset, remove, mix and its parameters.

    Dragging a parameter previews live and is one undo step. ⟲ resets to your
    default for this effect type; ⋯ saves this effect as your default or
    resets it to the factory defaults."""

    def __init__(self, section: "EffectsSection", entry: dict[str, Any], effect_type: Any, index: int,
                 count: int) -> None:
        super().__init__(section.body)
        self.section = section
        self.effect_id = entry["id"]
        self.effect_type = effect_type
        self.setObjectName("effectRow")
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(4)
        header = QHBoxLayout()
        label = effect_type.nice_name if effect_type is not None else f"{entry['type']} (plugin missing)"
        self.enabled = QCheckBox(label, self)
        self.enabled.setToolTip(effect_type.doc if effect_type is not None and effect_type.doc else "On / off")
        self.enabled.toggled.connect(lambda on: self.section.commit(self.effect_id, enabled=int(on)))
        header.addWidget(self.enabled, 1)
        for text, tip, target, enabled in (("↑", "Apply earlier", index - 1, index > 0),
                                           ("↓", "Apply later", index + 1, index < count - 1)):
            button = w.make_tool_button(text, tip, width=24, parent=self)
            button.setEnabled(enabled)
            button.clicked.connect(lambda _c=False, t=target: self.section.run("effects.move", self.effect_id, t))
            header.addWidget(button)
        reset = w.make_tool_button("⟲", "Reset to your default (⋯: factory defaults)", width=24, parent=self)
        reset.clicked.connect(lambda: self.section.run("effects.reset", self.effect_id))
        self.more = w.make_tool_button("⋯", "Save as your default · reset to the factory defaults", width=24,
                                       parent=self)
        self.more.clicked.connect(lambda: w.fields.show_menu(self.more, self.more.mapToGlobal(
            self.more.rect().bottomLeft()), self.menu_entries()))
        self.more.setEnabled(effect_type is not None)
        remove = w.make_tool_button("✕", "Remove this effect", width=24, parent=self)
        remove.clicked.connect(lambda: self.section.run("effects.remove", self.effect_id))
        header.addWidget(reset)
        header.addWidget(self.more)
        header.addWidget(remove)
        layout.addLayout(header)
        self.form = QFormLayout()
        self.form.setContentsMargins(18, 0, 0, 0)
        self.form.setVerticalSpacing(4)
        layout.addLayout(self.form)
        self.editors: dict[str, tuple[Any, QWidget]] = {}
        app = section.ui.app
        title = effect_type.nice_name if effect_type is not None else entry["type"]
        for spec in effect_type.params if effect_type is not None else ():
            editor = w.editor_for(spec, self)
            w.bind_edits(app, editor, lambda value, n=spec.name: self.section.commit(self.effect_id, **{n: value}),
                         f"Edit {title.lower()}")
            editor.context_provider = lambda n=spec.name: self.param_menu(n)
            self.editors[spec.name] = (spec, editor)
            self.form.addRow(self._label(spec, editor), editor)
        self.mix = w.editor_for(MIX, self)
        w.bind_edits(app, self.mix, lambda value: self.section.commit(self.effect_id, mix=value),
                     f"Edit {title.lower()}")
        self.mix.context_provider = lambda: self.param_menu("mix")
        self.form.addRow(self._label(MIX, self.mix), self.mix)
        self.update_values(entry)

    def _label(self, spec: Any, editor: QWidget) -> QWidget:
        return w.attr_label(self.section.ui.session, spec, editor, "", False, self)

    # ---- Defaults ----------------------------------------------------------------------------

    def _api(self) -> Any:
        return self.section._api()

    def menu_entries(self) -> list[tuple[Any, Any]]:
        name = self.effect_type.nice_name if self.effect_type is not None else "this effect"
        return [
            (f"Save as my default for new {name}", self.save_default),
            ("Reset to my default", lambda: self.section.run("effects.reset", self.effect_id)),
            ("Reset to factory defaults", lambda: self.section.run("effects.reset", self.effect_id, factory=True)),
            (None, None),
            ("Forget my default", self.forget_default),
        ]

    def param_menu(self, name: str) -> list[tuple[Any, Any]]:
        return [(f"Set {('mix' if name == 'mix' else self.editors[name][0].nice_name.lower())} as my default",
                 lambda: self.save_one(name))] + self.menu_entries()

    def save_default(self) -> None:
        if self.section.run("effects.save_default", self.effect_id) is not None:
            self.section.ui.toast(f"New {self.effect_type.nice_name} effects start like this one")

    def save_one(self, name: str) -> None:
        """Make one parameter's value (or the mix) your default, keeping the rest of your defaults."""
        api = self._api()
        defaults = api.user_defaults(self.effect_type.name)
        params, mix = dict(defaults["params"]), defaults["mix"]
        if name == "mix":
            mix = self.mix.read()
        else:
            params[name] = self.editors[name][1].read()
        api.save_user_defaults(self.effect_type.name, params, mix)
        self.section.ui.toast(f"{self.effect_type.nice_name}: saved as your default")

    def forget_default(self) -> None:
        if self.effect_type is not None:
            self.section.ui.app.run_command("effects.clear_default", self.effect_type.name)
            self.section.ui.toast(f"{self.effect_type.nice_name}: new effects use the factory defaults")

    def update_values(self, entry: dict[str, Any]) -> None:
        resolved = self.effect_type.resolved(entry.get("params") or {}) if self.effect_type is not None else {}
        self.enabled.blockSignals(True)
        self.enabled.setChecked(bool(entry.get("enabled", 1)))
        self.enabled.blockSignals(False)
        self.mix.write(float(entry.get("mix", 1.0)))
        for name, (_spec, editor) in self.editors.items():
            editor.write(resolved.get(name))


class EffectsSection(w.InspectorSection):
    def __init__(self, ui: Any, engine: EffectEngine, parent: QWidget) -> None:
        super().__init__("effects", "Effects", parent)
        self.ui = ui
        self.engine = engine
        self._names: list[str] = []
        self._signature: Any = None
        self._updating = False
        self.rows: dict[str, EffectRow] = {}
        top = QWidget(self.body)
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        self.add_button = QPushButton("Add effect", top)
        self.add_menu = QMenu(self.add_button)
        self.add_menu.aboutToShow.connect(self._fill_add_menu)
        self.add_button.setMenu(self.add_menu)
        self.copy_button = w.make_tool_button("Copy", "Copy this stack (to paste on other elements)", width=46,
                                              parent=top)
        self.copy_button.clicked.connect(lambda: self.run("effects.copy", node=self._names[0] if self._names else None))
        self.paste_button = w.make_tool_button("Paste", "Give the selection the copied stack", width=46, parent=top)
        self.paste_button.clicked.connect(lambda: self.run("effects.paste", nodes=list(self._names)))
        self.mute_button = w.make_tool_button("Mute", "Don't draw these effects (they're kept)", checkable=True,
                                              width=46, parent=top)
        self.mute_button.toggled.connect(self._on_mute)
        top_layout.addWidget(self.add_button, 1)
        top_layout.addWidget(self.mute_button)
        top_layout.addWidget(self.copy_button)
        top_layout.addWidget(self.paste_button)
        self.form.addRow(top)
        self.note = QLabel("", self.body)
        self.note.setWordWrap(True)
        self.note.setObjectName("pathLabel")
        self.form.addRow(self.note)
        self.list = QWidget(self.body)
        self.list_layout = QVBoxLayout(self.list)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.form.addRow(self.list)
        self.frames_check = QCheckBox("Process all frames (full quality while playing)", self.body)
        self.frames_check.setToolTip("Heavy effects are skipped while an animation plays. With this on, every "
                                     "frame is processed in the background and kept, so playback shows them.")
        self.frames_check.toggled.connect(self._on_frames)
        self.frames_label = QLabel("", self.body)
        self.frames_label.setObjectName("pathLabel")
        self.form.addRow(self.frames_check)
        self.form.addRow(self.frames_label)
        engine.frames_progress.connect(self._on_frames_progress)

    # ---- Refresh --------------------------------------------------------------------------------

    def refresh(self, info: SelectionInfo) -> None:
        self._names = list(info.names)
        node = info.node or {}
        stack = list(node.get("attrs", {}).get("effects") or [])
        types = self.engine.types()
        signature = (self._names[:1], tuple((e["id"], e["type"]) for e in stack), tuple(types))
        self._updating = True
        try:
            if signature != self._signature:
                self._rebuild(stack, types)
                self._signature = signature
            for entry in stack:
                row = self.rows.get(entry["id"])
                if row is not None:
                    row.update_values(entry)
            extra = len(self._names) - 1
            muted = bool(node.get("attrs", {}).get("effects_muted", 0))
            self.mute_button.setChecked(muted)
            self.mute_button.setEnabled(bool(stack))
            notes = []
            if extra:
                notes.append(f"Showing {node.get('name')}'s effects. Adding, pasting and muting apply to all "
                             f"{len(self._names)} selected.")
            elif not stack:
                notes.append("No effects. Add one to change how this looks.")
            if stack and muted:
                notes.append("Muted: the effects are kept but not drawn.")
            if stack and not self.engine.show_effects:
                notes.append("All effects are hidden (Effects → Show Effects).")
            self.note.setText(" ".join(notes))
            self.paste_button.setEnabled(bool(self._api().clipboard))
            self.copy_button.setEnabled(bool(stack) and len(self._names) == 1)
            animated = info.single and getattr(info.item, "animated", False)
            self.frames_check.setVisible(bool(animated and stack))
            self.frames_label.setVisible(bool(animated and stack))
            if animated:
                self.frames_check.setChecked(bool(node.get("attrs", {}).get("effects_all_frames", 0)))
                self._show_frames(node.get("name", ""))
        finally:
            self._updating = False

    def _rebuild(self, stack: list[dict[str, Any]], types: dict[str, Any]) -> None:
        for row in self.rows.values():
            row.setParent(None)
            row.deleteLater()
        self.rows = {}
        for index, entry in enumerate(stack):
            row = EffectRow(self, entry, types.get(entry["type"]), index, len(stack))
            self.list_layout.addWidget(row)
            self.rows[entry["id"]] = row

    def _fill_add_menu(self) -> None:
        self.add_menu.clear()
        by_category: dict[str, list[Any]] = {}
        for effect_type in self.engine.types().values():
            by_category.setdefault(effect_type.category or "Other", []).append(effect_type)
        for category in sorted(by_category):
            self.add_menu.addSection(category)
            for effect_type in by_category[category]:
                text = effect_type.nice_name + ("  (slow on big images)" if effect_type.cost == "heavy" else "")
                action = self.add_menu.addAction(text)
                if effect_type.doc:
                    action.setToolTip(effect_type.doc)
                action.triggered.connect(lambda _c=False, n=effect_type.name: self.run(
                    "effects.add", n, nodes=list(self._names)))

    # ---- Commits -----------------------------------------------------------------------------------

    def _api(self) -> Any:
        return self.ui.session.services.get("effects")

    def run(self, command: str, *args: Any, **kwargs: Any) -> Any:
        if command in ("effects.move", "effects.remove", "effects.reset", "effects.save_default"):
            if not self._names:
                return None
            node = self._names[0]
            if command == "effects.remove":
                return self.ui.app.run_command(command, args[0], nodes=node)
            return self.ui.app.run_command(command, node, *args, **kwargs)
        return self.ui.app.run_command(command, *args, **kwargs)

    def _on_mute(self, on: bool) -> None:
        if not self._updating and self._names:
            self.ui.app.run_command("effects.mute", list(self._names), on=on)

    def commit(self, effect_id: str, **values: Any) -> None:
        if self._updating or not self._names:
            return
        self.ui.app.run_command("effects.set", self._names[0], effect_id, **values)

    # ---- Animations ----------------------------------------------------------------------------------

    def _on_frames(self, on: bool) -> None:
        if not self._updating and self._names:
            self.ui.app.run_command("effects.all_frames", self._names[:1], on=on)

    def _on_frames_progress(self, name: str, done: int, total: int) -> None:
        if self._names[:1] == [name]:
            self._show_frames(name)

    def _show_frames(self, name: str) -> None:
        progress = self.engine.frames.get(name)
        if not self.frames_check.isChecked() or progress is None:
            self.frames_label.setText("")
            return
        done, total = progress
        self.frames_label.setText(f"All {total} frames ready" if done >= total else f"Processing frames: {done}/{total}")


# ---- Activation ----------------------------------------------------------------------------------------


def activate_effects_ui(ui: Any) -> None:
    session = ui.session
    canvas = ui.canvas
    engine = EffectEngine(ui, cache_folder())
    ui.track(engine.shutdown)
    ui.provide_service("effects_engine", engine)
    for name, processor in BUILTIN_PROCESSORS.items():
        ui.contribute("effects.processors", (name, processor))

    # ---- Drawing --------------------------------------------------------------------------------

    def install(item: Any, node: dict[str, Any]) -> None:
        attrs = node.get("attrs", {})
        stack = attrs.get("effects") or []
        if not engine.show_effects or attrs.get("effects_muted", 0):
            return  # muted: the stack is kept, the content drawn as it is
        if not any(entry.get("enabled", 1) for entry in stack):
            return
        if session.types.has_capability(node["type"], CAP_CONTAINER):
            return  # groups and layers draw nothing themselves (compositing them comes later)
        paint_filter = EffectFilter(engine, node)
        item.add_paint_filter(paint_filter)
        if paint_filter.all_frames and getattr(item, "animated", False):
            engine.prepare_frames(node["name"], item, paint_filter.stack)

    ui.contribute("canvas.item_hooks", install)
    for point in ("effects.types", "effects.processors"):
        ui.track(session.extensions.watch(point, canvas.request_full_refresh))

    def prune(_event: Any = None) -> None:
        for name in [n for n in engine.stale if n not in canvas.items]:
            engine.stale.pop(name, None)
            engine.frames.pop(name, None)

    ui.on_ui("canvas.refreshed", prune)
    ui.contribute("scene.before_save", lambda _session, _path: engine.write_scene_cache())

    # ---- Commands (cache) ------------------------------------------------------------------------

    def clear_cache(disk: bool = False) -> None:
        """Forget processed effects (memory and this scene's saved copies; ``disk`` also the disk cache)."""
        engine.clear(memory=True, scene=True, disk=disk)
        canvas.request_full_refresh()

    def cache_info() -> dict[str, Any]:
        """Effect cache sizes and counters (computed, from disk, from the scene…)."""
        section = session.scene.sections.get("effect_cache")
        return {"memory_items": len(engine.memory.items), "memory_mb": round(engine.memory.bytes / 1048576, 1),
                "scene_items": len(section) if isinstance(section, dict) else 0, "disk_folder": str(engine.disk.folder),
                "save_in_scene": engine.save_in_scene, **engine.stats}

    def save_in_scene(on: bool | None = None) -> bool:
        """Keep processed effects in the scene file (on by default); no argument toggles it."""
        state = (not engine.save_in_scene) if on is None else bool(on)
        ui.settings.setValue("save_in_scene", state)
        return state

    def show(on: bool | None = None) -> bool:
        """Show or hide every effect at once (a before/after check; not saved); no argument toggles."""
        engine.show_effects = (not engine.show_effects) if on is None else bool(on)
        canvas.request_full_refresh()
        ui.app.events.emit("effects.shown", {"on": engine.show_effects})
        return engine.show_effects

    ui.command("show", show, undoable=False)
    ui.command("clear_cache", clear_cache, undoable=False)
    ui.command("cache_info", cache_info, undoable=False, echo=False)
    ui.command("save_in_scene", save_in_scene, undoable=False)

    # ---- Attribute Editor -------------------------------------------------------------------------

    def applies(info: SelectionInfo) -> bool:
        if not info.nodes:
            return False
        return all(session.types.is_canvas(n["type"]) and not session.types.has_capability(n["type"], CAP_CONTAINER)
                   for n in info.nodes if n["type"] in session.types)

    ui.contribute("inspector.sections", InspectorSection(
        key="effects", title="Effects", order=SECTION_ORDER, applies=applies, generic=True,
        build=lambda parent, _inspector: EffectsSection(ui, engine, parent)))

    # ---- Actions and menus ----------------------------------------------------------------------------

    has_selection = "selection.count > 0"
    ui.place_submenu("canvas", "Effects", "transform", 2, icon="effects")
    ui.place_submenu("canvas", "Effects", "tools", 2, when="selection.count == 0")
    menu_disposables: list[Any] = []

    def add_type_actions() -> None:
        while menu_disposables:
            menu_disposables.pop().dispose()
        for order, effect_type in enumerate(engine.types().values()):
            key = f"effects.add.{effect_type.name}"
            # Not ui.action()/ui.menu(): these come and go with the effect types, so
            # they're disposed here (and once more, harmlessly, on deactivation).
            menu_disposables.append(ui.app.actions.define(
                key, "effects.add", f"Add Effect: {effect_type.nice_name}", plugin=ui.plugin_id,
                args=(effect_type.name,), category="Effects",
                keywords=f"effect filter {effect_type.name.replace('_', ' ')} {effect_type.doc}"))
            menu_disposables.append(ui.app.menus.add("canvas", key, plugin=ui.plugin_id,
                                                     path=f"Effects/Add/{effect_type.category}", group="effects",
                                                     order=order, when=has_selection))

    add_type_actions()
    ui.track(session.extensions.watch("effects.types", add_type_actions))
    ui.track(lambda: [d.dispose() for d in menu_disposables])
    for key, command, text, keywords, order in (
        ("effects.copy", "effects.copy", "Copy Effects", "effect stack copy", 10),
        ("effects.paste", "effects.paste", "Paste Effects", "effect stack paste apply", 11),
        ("effects.clear", "effects.clear", "Remove All Effects", "effect clear delete reset", 12),
        ("effects.mute", "effects.mute", "Mute Effects", "effect mute bypass disable off hide keep", 5),
    ):
        ui.action(key, command, text, keywords=keywords, category="Effects")
        ui.menu("canvas", key, path="Effects", group="stack", order=order, when=has_selection)
    ui.action("effects.show", "effects.show", "Show Effects", keywords="effect mute bypass before after compare",
              category="Effects", checked=lambda: engine.show_effects)
    ui.menu("canvas", "effects.show", path="Effects", group="show", order=0)
    ui.action("effects.save_in_scene", "effects.save_in_scene", "Keep Processed Effects in Scene File",
              keywords="effect cache save embed", category="Effects", checked=lambda: engine.save_in_scene)
    ui.action("effects.clear_cache", "effects.clear_cache", "Forget Processed Effects",
              keywords="effect cache clear smaller file", category="Effects")
    ui.menu("canvas", "effects.save_in_scene", path="Effects", group="cache", order=0)
    ui.menu("canvas", "effects.clear_cache", path="Effects", group="cache", order=1)
