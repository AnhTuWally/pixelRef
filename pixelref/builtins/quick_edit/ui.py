"""Qt side of the quick editor: the service (which fields), the bar, the customise dialog."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from pixelref.plugin_api import CommandError
from pixelref.plugin_api.ui import widgets as w

ENABLED_KEY = "enabled"
FIELDS_KEY = "fields"
HIDDEN_SECTIONS_KEY = "hidden_sections"
SECTIONS = "quick_edit.sections"
# Attribute types the bar can't edit in one line (or shouldn't: references, raw data, multi-line text).
UNSUPPORTED = frozenset({"points", "any", "rect", "asset", "node_ref", "node_refs", "text"})
GAP = 14  # px between the selection and the bar
ABOVE_GAP = 40  # above the selection, clear of the gizmo's rotate handle
MARGIN = 8  # px kept free at the view's edges


class QuickEditService:
    """Which attributes the quick editor shows per node type, and whether it is on."""

    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.types = ui.session.types
        self.listeners: list[Any] = []

    # ---- On / off -------------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self.ui.settings.value(ENABLED_KEY, True, type=bool))

    def set_enabled(self, enabled: bool) -> None:
        self.ui.settings.setValue(ENABLED_KEY, bool(enabled))
        self._changed()

    # ---- Fields ------------------------------------------------------------------------------

    def editable(self, node_type: str) -> dict[str, Any]:
        """Attributes of ``node_type`` the bar can edit, by name."""
        if node_type not in self.types:
            return {}
        return {name: spec for name, spec in self.types.attrs(node_type).items()
                if not (spec.hidden or spec.computed or spec.readonly) and spec.type not in UNSUPPORTED}

    def default_fields(self, node_type: str) -> list[str]:
        """The plugin's choice (``NodeType.quick_attrs``)."""
        editable = self.editable(node_type)
        return [name for name in self.types.quick_attrs(node_type) if name in editable] if editable else []

    def _overrides(self) -> dict[str, list[str]]:
        stored = self.ui.settings.json_value(FIELDS_KEY, {})
        return stored if isinstance(stored, dict) else {}

    def is_customized(self, node_type: str) -> bool:
        return node_type in self._overrides()

    def fields(self, node_type: str) -> list[str]:
        """The user's list for the type if they made one, else the plugin's."""
        stored = self._overrides().get(node_type)
        if not isinstance(stored, list):
            return self.default_fields(node_type)
        editable = self.editable(node_type)  # attributes of a plugin that's off now are skipped
        return [name for name in stored if isinstance(name, str) and name in editable]

    def set_fields(self, node_type: str, names: list[str]) -> list[str]:
        if node_type not in self.types:
            raise CommandError(f"Unknown node type '{node_type}'")
        editable = self.editable(node_type)
        names = [names] if isinstance(names, str) else list(names)
        bad = [n for n in names if n not in editable]
        if bad:
            raise CommandError(f"{self.types.label(node_type)} has no quick-editable attribute "
                               f"{', '.join(repr(n) for n in bad)}")
        overrides = self._overrides()
        overrides[node_type] = list(dict.fromkeys(names))
        self.ui.settings.set_json(FIELDS_KEY, overrides)
        self._changed()
        return overrides[node_type]

    def reset_fields(self, node_type: str | None = None) -> None:
        overrides = self._overrides() if node_type is not None else {}
        overrides.pop(node_type, None)
        self.ui.settings.set_json(FIELDS_KEY, overrides)
        self._changed()

    # ---- Sections (quick_edit.sections contributions) ------------------------------------------

    def sections(self) -> list[Any]:
        return sorted(self.ui.contributions(SECTIONS), key=lambda s: s.order)

    def _hidden_sections(self) -> list[str]:
        stored = self.ui.settings.json_value(HIDDEN_SECTIONS_KEY, [])
        return [k for k in stored if isinstance(k, str)] if isinstance(stored, list) else []

    def section_shown(self, key: str) -> bool:
        return key not in self._hidden_sections()

    def set_section_shown(self, key: str, shown: bool) -> None:
        hidden = [k for k in self._hidden_sections() if k != key] + ([] if shown else [key])
        self.ui.settings.set_json(HIDDEN_SECTIONS_KEY, hidden)
        self._changed()

    def _changed(self) -> None:
        for callback in list(self.listeners):
            callback()


class QuickBar(QFrame):
    """The bar of editors floating over a canvas view."""

    def __init__(self, controller: "QuickEdit", view: Any) -> None:
        super().__init__(view)
        self.controller = controller
        self.view = view
        self.setObjectName("quickEditBar")
        self.setFocusPolicy(Qt.NoFocus)
        layout = QHBoxLayout(self)
        # The bar is always exactly as big as its fields need, also when their size hints
        # change after it was first shown (styling and fonts are applied on show).
        layout.setSizeConstraint(QLayout.SetFixedSize)
        layout.setContentsMargins(8, 4, 4, 4)
        layout.setSpacing(6)
        self.sections_box = QWidget(self)
        self.sections_layout = QHBoxLayout(self.sections_box)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(8)
        layout.addWidget(self.sections_box)
        self.divider = QFrame(self)
        self.divider.setFrameShape(QFrame.VLine)
        self.divider.setObjectName("quickEditDivider")
        layout.addWidget(self.divider)
        self.sections: dict[str, tuple[Any, QWidget]] = {}  # key -> (contribution, widget)
        self.fields_box = QWidget(self)
        self.fields_layout = QHBoxLayout(self.fields_box)
        self.fields_layout.setContentsMargins(0, 0, 0, 0)
        self.fields_layout.setSpacing(8)
        layout.addWidget(self.fields_box)
        self.more_btn = QToolButton(self)
        self.more_btn.setObjectName("tileHeaderButton")
        self.more_btn.setText("⋯")
        self.more_btn.setToolTip("Choose the fields shown here, or hide the quick editor")
        self.more_btn.setFocusPolicy(Qt.NoFocus)
        self.more_btn.setAutoRaise(True)
        self.more_btn.clicked.connect(
            lambda: w.fields.show_menu(self, self.more_btn.mapToGlobal(QPoint(0, self.more_btn.height())),
                                controller.menu_entries()))
        layout.addWidget(self.more_btn)
        w.install_context_menu(self, controller.menu_entries)
        self.editors: dict[str, Any] = {}
        self.signature: Any = None
        self.hide()

    def build(self, specs: list[Any]) -> None:
        while self.fields_layout.count():
            widget = self.fields_layout.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self.editors = {}
        for spec in specs:
            cell = QWidget(self.fields_box)
            row = QHBoxLayout(cell)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(3)
            editor = w.editor_for(spec, cell)
            tip = spec.nice_name + (f" — {spec.doc}" if spec.doc else "")
            if isinstance(editor, w.NumberField):
                editor.setMinimumWidth(0)
                editor.setFixedWidth(72)
                row.addWidget(w.ScrubLabel(spec.nice_name, editor, editor.per_pixel(), cell))
            elif spec.type in ("bool", "flag"):
                editor.setText(spec.nice_name)
            elif spec.type in ("float2", "string", "path", "string_list"):
                label = QLabel(spec.nice_name, cell)
                label.setObjectName("mutedLabel")
                row.addWidget(label)
                if spec.type == "float2":
                    for field in editor.fields:
                        field.setFixedWidth(56)
                else:
                    editor.setFixedWidth(110)
            row.addWidget(editor)
            cell.setToolTip(tip)
            editor.setToolTip(tip)
            w.bind_edits(self.controller.app, editor, lambda value, s=spec: self.controller.commit(s.name, value),
                         f"Set {spec.nice_name.lower()}")
            if w.fields.is_live(editor):
                editor.scrub_started.connect(lambda: self.controller.set_scrubbing(True))
                editor.scrub_finished.connect(lambda _cancelled: self.controller.set_scrubbing(False))
            editor.context_provider = lambda s=spec: self.controller.field_menu(s)
            self.fields_layout.addWidget(cell)
            self.editors[spec.name] = editor
        self.fields_box.setVisible(bool(specs))

    def show_sections(self, contributions: list[Any], registered: list[Any], names: list[str]) -> None:
        """Show these contributed sections (built once per bar), in order, refreshed for ``names``.

        Widgets of sections no longer ``registered`` (their plugin was turned off) are deleted.
        """
        for key, (spec, widget) in list(self.sections.items()):
            if not any(spec is s for s in registered):
                del self.sections[key]
                widget.hide()
                widget.deleteLater()
            elif not any(spec is s for s in contributions):
                widget.hide()
        for index, spec in enumerate(contributions):
            if spec.key not in self.sections:
                self.sections[spec.key] = (spec, spec.build(self.sections_box))
            widget = self.sections[spec.key][1]
            self.sections_layout.insertWidget(index, widget)
            widget.refresh(names)
            widget.show()
        self.sections_box.setVisible(bool(contributions))
        self.divider.setVisible(bool(contributions) and bool(self.editors))
    def fit(self) -> None:
        """Size the bar for its fields as they will look on screen (styled), before placing it."""
        for child in self.findChildren(QWidget):
            child.ensurePolished()
        self.ensurePolished()
        self.layout().invalidate()
        self.layout().activate()

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.isVisible() and event.oldSize() != event.size():
            self.controller.schedule()  # keep it centred on the selection

    def write(self, values: dict[str, Any]) -> None:
        for name, editor in self.editors.items():
            if name in values:
                editor.write(values[name])


class FieldsDialog(QDialog):
    """Tick and order the quick editor fields of one node type."""

    def __init__(self, service: QuickEditService, node_type: str, title: str, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.service = service
        self.node_type = node_type
        self.setWindowTitle(f"Quick editor — {title}")
        layout = QVBoxLayout(self)
        hint = QLabel(f"Fields shown on the canvas next to a selected {title.lower()}.\n"
                      "Tick to show, drag to reorder.", self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.list = QListWidget(self)
        self.list.setDragDropMode(QListWidget.InternalMove)
        self.list.setMinimumHeight(260)
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        self.default_btn = QPushButton("Plugin default", self)
        self.default_btn.setToolTip("Put back the fields the node type's plugin chose")
        buttons.addButton(self.default_btn, QDialogButtonBox.ResetRole)
        self.default_btn.clicked.connect(lambda: self.fill(service.default_fields(node_type)))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.fill(service.fields(node_type))

    def fill(self, chosen: list[str]) -> None:
        self.list.clear()
        editable = self.service.editable(self.node_type)
        for name in chosen + [n for n in editable if n not in chosen]:
            spec = editable[name]
            item = QListWidgetItem(f"{spec.nice_name}   ({name})" if spec.nice_name.lower() != name.replace("_", " ")
                                   else spec.nice_name)
            item.setData(Qt.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            item.setFlags(item.flags() & ~Qt.ItemIsDropEnabled)
            item.setCheckState(Qt.Checked if name in chosen else Qt.Unchecked)
            if spec.doc:
                item.setToolTip(spec.doc)
            self.list.addItem(item)

    def chosen(self) -> list[str]:
        items = (self.list.item(i) for i in range(self.list.count()))
        return [item.data(Qt.UserRole) for item in items if item.checkState() == Qt.Checked]


class _ViewWatcher(QObject):
    """Places the bar again when its view is resized."""

    def __init__(self, controller: "QuickEdit") -> None:
        super().__init__()
        self.controller = controller

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Resize:
            self.controller.schedule()
        return False


class QuickEdit:
    """Keeps one bar next to the selection in the active canvas view."""

    def __init__(self, ui: Any, service: QuickEditService) -> None:
        self.ui = ui
        self.app = ui.app
        self.canvas = ui.app.canvas
        self.service = service
        self.bar: QuickBar | None = None
        self.names: list[str] = []
        self.types: list[str] = []
        self.sections: list[Any] = []  # contributed sections for the selection, shown or hidden by the user
        self.scrubbing = False
        self.watcher = _ViewWatcher(self)
        self._watched: Any = None
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.update)

    def dispose(self) -> None:
        self._timer.stop()
        if self._watched is not None and isValid(self._watched):
            self._watched.removeEventFilter(self.watcher)
        if self.bar is not None and isValid(self.bar):
            self.bar.hide()
            self.bar.deleteLater()
        self.bar = None

    def schedule(self) -> None:
        if not self.scrubbing:
            self._timer.start(0)

    def set_scrubbing(self, scrubbing: bool) -> None:
        self.scrubbing = scrubbing
        if not scrubbing:
            self.schedule()

    # ---- What to show ----------------------------------------------------------------------

    def targets(self) -> tuple[list[str], list[Any]]:
        names, items = [], []
        for name in self.ui.session.selection.names:
            item = self.canvas.items.get(name)
            if item is not None and item.isVisible() and name in self.canvas.nodes:
                names.append(name)
                items.append(item)
        return names, items

    def fields_for(self, types: list[str]) -> list[Any]:
        """Specs to show: the first type's fields that every selected type also shows."""
        if not types:
            return []
        first = self.service.editable(types[0])
        names = self.service.fields(types[0])
        for other in types[1:]:
            theirs, editable = set(self.service.fields(other)), self.service.editable(other)
            names = [n for n in names if n in theirs and editable[n].type == first[n].type]
        return [first[n] for n in names]

    def _hidden_reason(self, view: Any) -> bool:
        if not self.service.enabled or view is None or not view.display.show_overlays:
            return True
        # A drag on the canvas (move, scale, a handle): the bar would be in the way.
        return self.canvas.interacting and QApplication.mouseButtons() != Qt.NoButton

    # ---- Updating -------------------------------------------------------------------------------

    def _bar_for(self, view: Any) -> QuickBar:
        if self.bar is None or not isValid(self.bar) or self.bar.view is not view:
            if self.bar is not None and isValid(self.bar):
                self.bar.hide()
                self.bar.deleteLater()
            self.bar = QuickBar(self, view)
            if self._watched is not None and isValid(self._watched):
                self._watched.removeEventFilter(self.watcher)
            view.installEventFilter(self.watcher)
            self._watched = view
        return self.bar

    def hide(self) -> None:
        if self.bar is not None and isValid(self.bar):
            self.bar.hide()

    def update(self) -> None:
        if self.scrubbing or getattr(self.app, "closed", False):
            return
        view = self.app.active_view()
        if self._hidden_reason(view):
            self.hide()
            return
        names, items = self.targets()
        types = list(dict.fromkeys(self.canvas.nodes[n]["type"] for n in names))
        specs = self.fields_for(types)
        registered = self.service.sections()
        self.sections = [s for s in registered if names and self._applies(s, names)]
        shown = [s for s in self.sections if self.service.section_shown(s.key)]
        if not specs and not shown:
            self.hide()
            return
        self.names, self.types = names, types
        bar = self._bar_for(view)
        signature = (tuple(types), tuple(s.name for s in specs))
        if signature != bar.signature:
            bar.build(specs)
            bar.signature = signature
        bar.show_sections(shown, registered, names)
        attrs = self.canvas.nodes[names[0]].get("attrs", {})
        bar.write({s.name: attrs.get(s.name, s.default_value()) for s in specs})
        self.place(bar, view, items)

    @staticmethod
    def _applies(section: Any, names: list[str]) -> bool:
        try:
            return bool(section.applies(names))
        except Exception:  # noqa: BLE001  (a broken contribution must not take the bar down)
            logging.getLogger(__name__).exception("quick_edit.sections: %s.applies failed", section.key)
            return False

    def place(self, bar: QuickBar, view: Any, items: list[Any]) -> None:
        bar.fit()
        area = view.viewport().geometry()  # in view coordinates
        bounds = view.mapFromScene(self.canvas.scene_bounds(items)).boundingRect().translated(area.topLeft())
        width, height = bar.width(), bar.height()
        x = bounds.center().x() - width // 2
        x = max(area.left() + MARGIN, min(x, area.right() - width - MARGIN))
        below = bounds.bottom() + GAP
        above = bounds.top() - ABOVE_GAP - height
        if below + height <= area.bottom() - MARGIN:
            y = below
        elif above >= area.top() + MARGIN:
            y = above
        else:  # the selection fills the view: stick to its bottom edge
            y = area.bottom() - height - MARGIN
        bar.move(int(x), int(max(area.top() + MARGIN, y)))
        bar.show()
        bar.raise_()

    # ---- Editing ---------------------------------------------------------------------------------

    def commit(self, attr: str, value: Any) -> None:
        names = [n for n in self.names if self.ui.session.scene.has(n)]
        if not names:
            return
        if len(names) == 1:
            self.app.run_command("set_attr", names[0], attr, value)
        else:
            self.app.run_command("quick_edit.set", names, attr, value)

    # ---- Menus ----------------------------------------------------------------------------------------

    def _title(self, node_type: str) -> str:
        return self.canvas.friendly_type(node_type)

    def customize(self, node_type: str) -> None:
        dialog = FieldsDialog(self.service, node_type, self._title(node_type),
                              self.bar.window() if self.bar is not None and isValid(self.bar) else None)
        if dialog.exec() != QDialog.Accepted:
            return
        chosen = dialog.chosen()
        if chosen == self.service.default_fields(node_type):
            self.app.run_command("quick_edit.reset_fields", node_type)  # follows the plugin from now on
        else:
            self.app.run_command("quick_edit.set_fields", node_type, chosen)

    def menu_entries(self) -> list[tuple[Any, Any]]:
        entries: list[tuple[Any, Any]] = []
        for section in self.sections:
            shown = self.service.section_shown(section.key)
            entries.append((f"{'Hide' if shown else 'Show'} {section.title}",
                            lambda k=section.key, s=shown: self.app.run_command("quick_edit.show_section", k, not s)))
        if entries:
            entries.append((None, None))
        for node_type in self.types:
            title = self._title(node_type)
            entries.append((f"Choose Fields for {title}…", lambda t=node_type: self.customize(t)))
            if self.service.is_customized(node_type):
                entries.append((f"Reset {title} Fields to Plugin Default",
                                lambda t=node_type: self.app.run_command("quick_edit.reset_fields", t)))
        entries += [(None, None), ("Hide Quick Editor", self.hide_by_user)]
        return entries

    def field_menu(self, spec: Any) -> list[tuple[Any, Any]]:
        def remove() -> None:
            for node_type in self.types:
                fields = self.service.fields(node_type)
                if spec.name in fields:
                    self.app.run_command("quick_edit.set_fields", node_type, [f for f in fields if f != spec.name])

        return [("Reset to default", lambda: self.commit(spec.name, spec.default_value())), (None, None),
                (f"Remove {spec.nice_name} from Quick Editor", remove), *self.menu_entries()]

    def hide_by_user(self) -> None:
        self.app.run_command("quick_edit.toggle", False)


def activate_quick_edit_ui(ui: Any) -> None:
    ui.declare_extension_point(SECTIONS, "QuickSection: controls in the quick editor bar, before the fields")
    service = QuickEditService(ui)
    ui.provide_service("quick_edit", service)
    session = ui.session
    controller = QuickEdit(ui, service)
    ui.track(controller.dispose)
    service.listeners.append(controller.schedule)

    # ---- Commands -------------------------------------------------------------------------------------

    def toggle(visible: bool | None = None) -> bool:
        """Show or hide the quick editor next to the selection (no argument toggles)."""
        state = (not service.enabled) if visible is None else bool(visible)
        service.set_enabled(state)
        ui.status("Quick editor on" if state else "Quick editor off · View ▸ Quick Attribute Editor brings it back")
        ui.app.actions.refresh_states()
        ui.app.update_views()  # sections' own on-canvas fallbacks (e.g. playback) show while it's off
        return state

    def show_section(key: str, visible: bool | None = None) -> bool:
        """Show or hide a contributed section of the quick editor, e.g. "playback" (no argument toggles)."""
        known = {s.key for s in service.sections()}
        if key not in known:
            raise CommandError(f"No quick editor section '{key}' (there are: {', '.join(sorted(known)) or 'none'})")
        state = (not service.section_shown(key)) if visible is None else bool(visible)
        service.set_section_shown(key, state)
        return state

    def fields(node_type: str) -> list[str]:
        """The attributes the quick editor shows for a node type."""
        if node_type not in session.types:
            raise CommandError(f"Unknown node type '{node_type}'")
        return service.fields(node_type)

    def set_fields(node_type: str, attrs: list[str]) -> list[str]:
        """Choose the attributes (in order) the quick editor shows for a node type (kept in your settings)."""
        return service.set_fields(node_type, attrs)

    def reset_fields(node_type: str | None = None) -> None:
        """Go back to the plugin's quick editor fields for a node type (no argument: for every type)."""
        service.reset_fields(node_type)

    def set_value(nodes: Any, attr: str, value: Any) -> None:
        """Set one attribute on several nodes (one undo step)."""
        names = [nodes] if isinstance(nodes, str) else list(nodes)
        if not names:
            raise CommandError("Nothing selected")
        for name in names:
            session.scene.set_attr(session.scene.resolve(name), attr, value)

    ui.command("toggle", toggle, alias="toggle_quick_edit", undoable=False)
    ui.command("fields", fields, alias="quick_edit_fields", undoable=False, echo=False)
    ui.command("set_fields", set_fields, alias="set_quick_edit_fields", undoable=False)
    ui.command("reset_fields", reset_fields, alias="reset_quick_edit_fields", undoable=False)
    ui.command("show_section", show_section, undoable=False)
    ui.command("set", set_value, label=lambda nodes, attr, value: f"Set {attr}")

    ui.action("quick_edit.toggle", "quick_edit.toggle", "Quick Attribute Editor", icon="inspector",
              keywords="quick edit attributes bar hud properties selection", category="View",
              checked=lambda: service.enabled)
    ui.menu("canvas", "quick_edit.toggle", path="View", group="view.display", order=4)

    # ---- Following the selection ----------------------------------------------------------------------

    def later(_event: Any = None) -> None:
        controller.schedule()

    for event in ("ui.selection_changed", "canvas.refreshed", "camera.changed", "item.geometry_changed",
                  "focus.changed", "view.created", "view.removed", "view.display_changed", "tool.changed"):
        ui.on_ui(event, later)
    ui.track(session.types.watch(lambda _name: controller.schedule()))
    ui.track(session.extensions.watch(SECTIONS, controller.schedule))
    controller.schedule()
