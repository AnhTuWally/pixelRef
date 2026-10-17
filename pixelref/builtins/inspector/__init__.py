"""The Attribute Editor (inspector) view.

Shows a header card for the selection and the sections that apply to it.
Sections come from the ``inspector.sections`` extension point (this plugin
contributes Transform, Appearance and the generated attribute editors; node
type plugins contribute theirs). ``inspector.rows`` adds rows to another
plugin's section; ``inspector.empty_panels`` shows when nothing is selected.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from pixelref.plugin_api import CommandError
from pixelref.plugin_api.contracts import InspectorSection, SelectionInfo
from pixelref.plugin_api.ui import PanelType, icon
from pixelref.plugin_api.ui import widgets as w

from .sections import CORE_ATTRS, AppearanceSection, AttributeEditor, TransformSection

COLLAPSED_KEY = "collapsed"


class Inspector(QWidget):
    """One Attribute Editor tile."""

    def __init__(self, ui: Any) -> None:
        super().__init__()
        self.ui = ui
        stored = ui.settings.value(COLLAPSED_KEY, [])
        self.collapsed: set[str] = set([stored] if isinstance(stored, str) else (stored or []))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self.box = QWidget()
        self.box.setObjectName("inspector")
        self.layout_ = QVBoxLayout(self.box)
        self.layout_.setContentsMargins(6, 4, 6, 8)
        self.layout_.setSpacing(2)
        self.scroll.setWidget(self.box)

        # Header card: icon, editable name, visibility / lock, description.
        self.card = QWidget(self.box)
        self.card.setObjectName("inspectorCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(8, 8, 8, 8)
        card_layout.setSpacing(4)
        self.inspector_icon = QLabel(self.card)
        self.inspector_icon.setFixedSize(20, 20)
        self.name_edit = QLineEdit(self.card)
        self.name_edit.setObjectName("nameEdit")
        self.name_edit.setToolTip("Rename (Enter to apply)")
        self.name_edit.editingFinished.connect(self._on_name_edited)
        self.selection_label = QLabel("", self.card)
        self.selection_label.setObjectName("cardTitle")
        self.header_visible_btn = w.make_tool_button("", "Show / hide (H)", checkable=True, width=26, parent=self.card)
        self.header_lock_btn = w.make_tool_button("", "Lock / unlock (L)", checkable=True, width=26, parent=self.card)
        self.header_visible_btn.clicked.connect(lambda: ui.app.trigger("outliner.hide"))
        self.header_lock_btn.clicked.connect(lambda: ui.app.trigger("outliner.lock"))
        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(self.inspector_icon)
        top.addWidget(self.name_edit, 1)
        top.addWidget(self.selection_label, 1)
        top.addWidget(self.header_visible_btn)
        top.addWidget(self.header_lock_btn)
        card_layout.addLayout(top)
        self.type_label = QLabel("", self.card)
        self.type_label.setObjectName("cardSubtitle")
        self.type_label.setWordWrap(True)
        card_layout.addWidget(self.type_label)
        self.layout_.addWidget(self.card)

        self.sections_box = QWidget(self.box)
        self.sections_layout = QVBoxLayout(self.sections_box)
        self.sections_layout.setContentsMargins(0, 0, 0, 0)
        self.sections_layout.setSpacing(2)
        self.layout_.addWidget(self.sections_box)
        self.empty_box = QWidget(self.box)
        self.empty_layout = QVBoxLayout(self.empty_box)
        self.empty_layout.setContentsMargins(0, 0, 0, 0)
        self.layout_.addWidget(self.empty_box)
        self.layout_.addStretch()

        self.sections: dict[str, Any] = {}
        self._specs: dict[str, InspectorSection] = {}
        self.rows: dict[str, list[Any]] = {}
        self.empty_panels: list[Any] = []
        self.info: SelectionInfo | None = None
        self.rebuild()

    # ---- Building --------------------------------------------------------------------

    def rebuild(self) -> None:
        # hide() first: a widget just added to a layout has a queued show, which would
        # otherwise pop it up as a top-level window once it's unparented (e.g. at shutdown,
        # when every plugin's contributions are removed one by one).
        for section in self.sections.values():
            section.hide()
            section.setParent(None)
            section.deleteLater()
        for panel in self.empty_panels:
            panel.hide()
            panel.setParent(None)
            panel.deleteLater()
        self.sections, self._specs, self.rows, self.empty_panels = {}, {}, {}, []
        specs = sorted(self.ui.contributions("inspector.sections"), key=lambda s: s.order)
        for spec in specs:
            try:
                section = spec.build(self.sections_box, self)
            except Exception:  # noqa: BLE001
                self.ui.log.exception("Building inspector section %s failed", spec.key)
                continue
            if hasattr(section, "set_expanded"):
                section.set_expanded(spec.key not in self.collapsed)
                section.toggled.connect(self._on_section_toggled)
            self.sections_layout.addWidget(section)
            self.sections[spec.key] = section
            self._specs[spec.key] = spec
        for rows in sorted(self.ui.contributions("inspector.rows"), key=lambda r: r.order):
            section = self.sections.get(rows.section)
            if section is None:
                continue
            widget = rows.build(section.body, self)
            section.form.addRow(widget)
            self.rows.setdefault(rows.section, []).append(widget)
        for panel_spec in sorted(self.ui.contributions("inspector.empty_panels"), key=lambda p: p.order):
            panel = panel_spec.build(self.empty_box, self)
            self.empty_layout.addWidget(panel)
            self.empty_panels.append(panel)
        self.refresh()

    def section(self, key: str) -> Any:
        return self.sections.get(key)

    def _on_section_toggled(self, key: str, expanded: bool) -> None:
        if expanded:
            self.collapsed.discard(key)
        else:
            self.collapsed.add(key)
        self.ui.settings.setValue(COLLAPSED_KEY, sorted(self.collapsed))

    # ---- Refresh ---------------------------------------------------------------------------

    def selection_info(self) -> SelectionInfo | None:
        canvas = self.ui.canvas
        names = [n for n in self.ui.session.selection.names if n in canvas.nodes]
        if not names:
            return None
        nodes = [canvas.nodes[n] for n in names]
        item = canvas.item(names[0]) if len(names) == 1 else None
        return SelectionInfo(app=self.ui.app, names=names, nodes=nodes, types={n["type"] for n in nodes}, item=item)

    def refresh(self) -> None:
        info = self.selection_info()
        self.info = info
        if info is None:
            self.card.setVisible(False)
            self.sections_box.setVisible(False)
            self.empty_box.setVisible(bool(self.empty_panels))
            for panel in self.empty_panels:
                if hasattr(panel, "refresh"):
                    panel.refresh()
            return
        self.card.setVisible(True)
        self.sections_box.setVisible(True)
        self.empty_box.setVisible(False)
        self._populate_header(info)
        for key, section in self.sections.items():
            spec = self._specs[key]
            try:
                visible = bool(spec.applies(info))
            except Exception:  # noqa: BLE001
                visible = False
            section.setVisible(visible)
            if visible:
                section.refresh(info)
                for row in self.rows.get(key, []):
                    if hasattr(row, "refresh"):
                        row.refresh(info)

    def on_item_changed(self, item: Any) -> None:
        names = self.ui.session.selection.names
        if len(names) != 1 or names[0] != item.node_name:
            return
        for section in self.sections.values():
            if section.isVisible() and hasattr(section, "on_item_changed"):
                section.on_item_changed(item)

    def _describe(self, name: str) -> str:
        canvas = self.ui.canvas
        node = canvas.nodes[name]
        node_type = node["type"]
        attrs = node.get("attrs", {})
        parts = [canvas.friendly_type(node_type)]
        node_ui = canvas.node_ui(node_type)
        if node_ui is not None and node_ui.describe is not None:
            parts.extend(node_ui.describe(name, node))
        elif self.ui.session.types.has_capability(node_type, "container"):
            count = len(node.get("children", []))
            parts.append(f"{count} item{'s' if count != 1 else ''}")
        item = canvas.item(name)
        if item is not None and item.locked:
            parts.append("locked")
        if not bool(attrs.get("visibility", 1)):
            parts.append("hidden")
        return " · ".join(parts)

    def _populate_header(self, info: SelectionInfo) -> None:
        canvas = self.ui.canvas
        single = info.single
        self.name_edit.setVisible(single)
        self.selection_label.setVisible(not single)
        if single:
            node = info.node
            self.name_edit.setText(info.names[0])
            self.type_label.setText(self._describe(info.names[0]))
            node_ui = canvas.node_ui(node["type"])
            self.inspector_icon.setPixmap(icon(node_ui.icon if node_ui and node_ui.icon else "image").pixmap(18, 18))
        else:
            counts: dict[str, int] = {}
            for node in info.nodes:
                label = canvas.friendly_type(node["type"]).lower()
                counts[label] = counts.get(label, 0) + 1
            summary = ", ".join(f"{n} {label}{'s' if n > 1 and not label.endswith('s') else ''}"
                                for label, n in counts.items())
            self.selection_label.setText(f"{len(info.names)} items selected")
            self.type_label.setText(summary)
            self.inspector_icon.setPixmap(icon("select").pixmap(18, 18))
        all_visible = all(bool(n["attrs"].get("visibility", 1)) for n in info.nodes)
        any_locked = any(bool(n["attrs"].get("locked", 0)) for n in info.nodes)
        self.header_visible_btn.setChecked(not all_visible)
        self.header_visible_btn.setIcon(icon("eye") if all_visible else icon("eye_off", alpha=150))
        self.header_lock_btn.setChecked(any_locked)
        self.header_lock_btn.setIcon(icon("lock", "#e0a060") if any_locked else icon("lock_open", alpha=120))

    def _on_name_edited(self) -> None:
        names = self.ui.session.selection.names
        new_name = self.name_edit.text().strip()
        if len(names) != 1 or not new_name or new_name == names[0]:
            if len(names) == 1:
                self.name_edit.setText(names[0])
            return

        def rename() -> None:
            if self.ui.app.run_command("rename", names[0], new_name) is not None:
                self.ui.app.run_command("select", [new_name])

        QTimer.singleShot(0, rename)


class InspectorService:
    def __init__(self) -> None:
        self.panels: list[Inspector] = []

    def panel(self) -> Inspector | None:
        """The panel on screen (sidebar, floating card or pop-up), else any."""
        visible = [p for p in self.panels if p.isVisible()]
        return (visible or self.panels or [None])[0]


def activate_ui(ui: Any) -> None:
    for name, doc in (("inspector.sections", "InspectorSection contributions"),
                      ("inspector.rows", "InspectorRows: extra rows in another plugin's section"),
                      ("inspector.empty_panels", "EmptyPanel: shown when nothing is selected")):
        ui.declare_extension_point(name, doc)
    service = InspectorService()
    ui.provide_service("inspector", service)
    session = ui.session

    def set_opacity(nodes: Any, opacity: float) -> None:
        """Set the opacity of several nodes."""
        names = [nodes] if isinstance(nodes, str) else list(nodes)
        if not names:
            raise CommandError("Nothing selected")
        for name in names:
            session.scene.set_attr(session.scene.resolve(name), "opacity", float(opacity))

    ui.command("set_opacity", set_opacity, label=lambda nodes, opacity: "Set opacity" if not isinstance(nodes, str)
               and len(nodes) > 1 else f"Edit {nodes if isinstance(nodes, str) else nodes[0]}")

    def is_content(info: SelectionInfo) -> bool:
        item = info.item
        return info.single and item is not None and item.is_content

    def has_plugin_section(info: SelectionInfo) -> bool:
        for spec in ui.contributions("inspector.sections"):
            if spec.key in ("transform", "appearance", "attributes", "extra_attributes") or spec.generic:
                continue
            try:
                if spec.applies(info):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def generic_applies(info: SelectionInfo) -> bool:
        if not info.single or has_plugin_section(info):
            return False
        node_type = info.node["type"]
        if node_type not in session.types:
            return False
        attrs = session.types.attrs(node_type)
        return any(a.name not in CORE_ATTRS and not a.hidden and not a.computed for a in attrs.values())

    def dynamic_applies(info: SelectionInfo) -> bool:
        if not info.single or not session.scene.has(info.names[0]):
            return False
        return bool(session.scene.dynamic_attrs(session.scene.get_node(info.names[0])))

    ui.contribute("inspector.sections", InspectorSection(
        key="transform", title="Transform", order=0, applies=is_content,
        build=lambda parent, _i: TransformSection(ui, parent)))
    ui.contribute("inspector.sections", InspectorSection(
        key="appearance", title="Appearance", order=10,
        applies=lambda info: any("opacity" in n.get("attrs", {}) for n in info.nodes),
        build=lambda parent, _i: AppearanceSection(ui, parent)))
    ui.contribute("inspector.sections", InspectorSection(
        key="attributes", title="Attributes", order=80, applies=generic_applies,
        build=lambda parent, _i: AttributeEditor(ui, parent, "attributes", "Attributes", dynamic_only=False)))
    ui.contribute("inspector.sections", InspectorSection(
        key="extra_attributes", title="Extra attributes", order=90, applies=dynamic_applies,
        build=lambda parent, _i: AttributeEditor(ui, parent, "extra_attributes", "Extra attributes",
                                                 dynamic_only=True)))

    def make_panel(_parent: Any = None) -> Inspector:
        panel = Inspector(ui)
        service.panels.append(panel)
        return panel

    def dispose_panel(panel: QWidget) -> None:
        if panel in service.panels:
            service.panels.remove(panel)

    ui.panel(PanelType(id="inspector", title="Attribute Editor", icon="inspector", color="#f0b35a",
                       factory=make_panel, dispose=dispose_panel, order=20, popup_keys=("Alt+A", "Alt+Return"),
                       popup_anchor="selection", popup_size=(320, 520), float_size=(300, 460), stretch=3,
                       keywords="inspector properties attributes edit"))
    ui.action("inspector.edit_attributes", "panels.popup", "Edit Attributes…", args=("inspector",),
              icon="inspector", keywords="properties attribute editor inspector pop up", category="Edit")
    ui.menu("canvas", "inspector.edit_attributes", group="state", order=0, when="selection.count > 0")

    def refresh_all(_event: Any = None) -> None:
        if getattr(ui.app, "closed", False):
            return
        for panel in list(service.panels):
            panel.refresh()

    def rebuild_all() -> None:
        # At shutdown every contribution is removed one by one: rebuilding the panels
        # for each removal is wasted work (and briefly re-shows section widgets).
        if getattr(ui.app, "closed", False):
            return
        for panel in list(service.panels):
            panel.rebuild()

    def item_changed(event: Any) -> None:
        for panel in list(service.panels):
            panel.on_item_changed(event.payload["item"])

    ui.on_ui("canvas.refreshed", refresh_all)
    ui.on_ui("ui.selection_changed", refresh_all)
    ui.on_ui("item.geometry_changed", item_changed)
    for point in ("inspector.sections", "inspector.rows", "inspector.empty_panels"):
        ui.track(session.extensions.watch(point, rebuild_all))
