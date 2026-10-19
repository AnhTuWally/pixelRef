"""Qt side of the views plugin."""

from __future__ import annotations

import uuid
from typing import Any

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QMenu, QToolButton

from pixelref.plugin_api import CommandError
from pixelref.plugin_api.ui import ContainerItem, WorkspacePreset, icon, theme
from pixelref.plugin_api.ui import widgets

from . import BUILTIN_PRESETS, FRAME_GUIDES, MARKER_COLORS, next_marker_color

color_from = widgets.color_from

GUIDE_RATIOS = {"16:9": 16 / 9, "2.39:1": 2.39, "1:1": 1.0, "4:3": 4 / 3, "3:2": 3 / 2}

DEFAULT_WORKSPACES = (
    WorkspacePreset(1, "Board", {"tile": "canvas", "name": "Main", "title": "Main"}),
    WorkspacePreset(2, "Review", {"split": "h", "sizes": [0.5, 0.5], "children": [
        {"tile": "canvas", "name": "ReviewColor", "title": "Colour",
         "state": {"display": {"preset": "All"}, "link_group": "review"}},
        {"tile": "canvas", "name": "ReviewValue", "title": "Value check",
         "state": {"display": {"preset": "Value check", "grayscale": True}, "link_group": "review"}},
    ]}),
    WorkspacePreset(3, "Present", {"tile": "canvas", "name": "Present", "title": "Present", "header": 0,
                                   "state": {"display": {"preset": "Clean", "show_overlays": False,
                                                         "show_grid": False}}},
                    window_style={"panels": False}),
)


def activate_views_ui(ui: Any) -> None:
    session = ui.session
    scene = session.scene
    app = ui.app

    for preset in DEFAULT_WORKSPACES:
        ui.workspace(preset)

    def view_of(view: str | None) -> Any:
        found = app.active_view() if view is None else app.views.get(view)
        if found is None:
            raise CommandError(f"No canvas view '{view}'" if view else "No canvas view")
        return found

    def cameras() -> list[str]:
        """Every view marker in the scene, in Ctrl+1…9 order (markers belong to the scene, not a view)."""
        return [n.name for n in scene.iter_nodes() if n.node_type == "camera"]

    def marker_color(name: str) -> QColor:
        return color_from(scene.get_node(name).attrs.get("color"), (96, 178, 255))

    def swatch_icon(color: QColor, ring: bool = False) -> QIcon:
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor("#f3f6ff") if ring else color.darker(140), 1.5))
        painter.setBrush(color)
        painter.drawEllipse(QRectF(2.5, 2.5, 11.0, 11.0))
        painter.end()
        return QIcon(pixmap)

    def current_marker(target: Any) -> str | None:
        name = target._current_marker
        if name is not None and scene.has(name) and scene.get_node(name).node_type == "camera":
            return name
        return None

    def set_current(target: Any, name: str | None) -> None:
        target._current_marker = name
        target.viewport().update()
        app.events.emit("view.marker_changed", {"view": target.view_name})

    def marker_moved(target: Any, name: str) -> bool:
        """Whether the view no longer shows what marker ``name`` holds (so Update would change it)."""
        attrs = scene.get_node(name).attrs
        now = target.camera()
        center = marker_center(scene.get_node(name))
        zoom = float(attrs.get("zoom") or 1.0)
        return (abs(now["center"][0] - center[0]) > 0.5 / max(zoom, 1e-6)
                or abs(now["center"][1] - center[1]) > 0.5 / max(zoom, 1e-6)
                or abs(now["zoom"] - zoom) > zoom * 1e-3
                or abs(now["rotation"] - float(attrs.get("rotation") or 0.0)) > 0.01
                or bool(now.get("flip_x")) != bool(attrs.get("flip_x", 0))
                or bool(now.get("flip_y")) != bool(attrs.get("flip_y", 0)))

    def camera_factory(_doc: Any, node: dict[str, Any]) -> Any:
        # Camera markers are drawn by the overlay; this item exists so camera
        # nodes behave as regular canvas nodes instead of placeholder boxes.
        return ContainerItem(node["name"], "camera")

    ui.node_ui("camera", factory=camera_factory, label="View marker", icon="camera", badge=("#7ec8ff", "V"))

    def marker_center(node: Any) -> list[float]:
        translate = node.attrs.get("translate")
        if isinstance(translate, (list, tuple)) and len(translate) >= 2:
            try:
                return [float(translate[0]), float(translate[1])]
            except (TypeError, ValueError):
                pass
        center = node.attrs.get("center") or [0.0, 0.0]
        return [float(center[0]), float(center[1])]

    def presets() -> list[str]:
        return list(BUILTIN_PRESETS) + [n.name for n in scene.iter_nodes() if n.node_type == "display_preset"]

    # ---- Cameras -------------------------------------------------------------------------

    def camera_attrs(values: dict[str, Any]) -> dict[str, Any]:
        return {"center": values["center"], "translate": values["center"],
                "zoom": values["zoom"], "rotation": values["rotation"],
                "flip_x": 1 if values.get("flip_x") else 0, "flip_y": 1 if values.get("flip_y") else 0}

    def create_camera(name: str = "View marker", view: str | None = None, color: Any = None) -> str:
        """Save where a view looks as a new view marker (next unused colour), now the view's current one."""
        target = view_of(view)
        final = scene.unique_name(name)
        attrs = camera_attrs(target.camera())
        attrs["color"] = list(color) if color is not None else next_marker_color(scene)
        scene.create_node("camera", final, attrs=attrs)
        set_current(target, final)
        return final

    def update_camera(camera: str | None = None, view: str | None = None) -> str:
        """Overwrite a view marker (default: the view's current marker) with where the view looks now."""
        target = view_of(view)
        if camera is not None and str(camera).strip():
            name = scene.resolve(camera)
            if scene.get_node(name).node_type != "camera":
                raise CommandError(f"'{name}' is not a view marker")
        else:
            name = current_marker(target)
            if name is None:
                raise CommandError("No current view marker: look through one (or make one) first")
        if bool(scene.get_node(name).attrs.get("locked", 0)):
            raise CommandError(f"View marker '{name}' is locked")
        scene.set_attrs(name, camera_attrs(target.camera()))
        set_current(target, name)
        ui.status(f"Updated view marker: {name}")
        return name

    def set_marker_color(color: Any, camera: str | None = None, view: str | None = None) -> str:
        """Recolour a view marker (default: the view's current marker); ``color`` is [r, g, b]."""
        name = scene.resolve(camera) if camera else current_marker(view_of(view))
        if name is None:
            raise CommandError("No current view marker")
        scene.set_attrs(name, {"color": [int(c) for c in color][:3]})
        return name

    def look_through(camera: str, view: str | None = None, animate: bool = True) -> None:
        """Show a view marker in a view (smooth move unless animate=False)."""
        target = view_of(view)
        name = scene.resolve(camera)
        node = scene.get_node(name)
        if node.node_type != "camera":
            raise CommandError(f"'{name}' is not a view marker")
        attrs = node.attrs
        set_current(target, name)
        target.set_camera(marker_center(node), attrs.get("zoom"), attrs.get("rotation"),
                          animate=animate, flip_x=bool(attrs.get("flip_x", 0)),
                          flip_y=bool(attrs.get("flip_y", 0)))
        ui.status(f"View marker: {name}")
        target.show_toast(f"View marker · {name}", marker_color(name), duration_ms=1500)

    def bookmark(number: int, view: str | None = None) -> None:
        """Look through the n-th view marker (Ctrl+1 to Ctrl+9)."""
        names = cameras()
        if not 1 <= int(number) <= len(names):
            raise CommandError(f"No view marker {number} (there are {len(names)})")
        look_through(names[int(number) - 1], view)

    ui.command("create_camera", create_camera, alias="create_camera", label="New view marker")
    ui.command("update_camera", update_camera, alias="update_camera", label="Update view marker")
    ui.command("set_marker_color", set_marker_color, alias="set_marker_color", label="Set view marker colour")
    ui.command("look_through", look_through, alias="look_through", undoable=False)
    ui.command("bookmark", bookmark, undoable=False)

    # ---- Display presets -------------------------------------------------------------------

    def preset_values(preset: str) -> dict[str, Any]:
        if preset in BUILTIN_PRESETS:
            values = dict(BUILTIN_PRESETS["All"])
            values.update(BUILTIN_PRESETS[preset])
        else:
            node = scene.get_node(scene.resolve(preset))
            if node.node_type != "display_preset":
                raise CommandError(f"'{preset}' is not a display preset")
            values = {k: v for k, v in node.attrs.items() if k != "tags"}
            values["isolate"] = list(values.get("isolate") or [])
        values["preset"] = preset
        return values

    def set_display_preset(preset: str, view: str | None = None) -> str:
        """Show a view through a display preset (built-in: All, Value check, Images only,
        Annotations only, Clean; or a display_preset node)."""
        app.set_display(view_of(view), preset_values(preset))
        return preset

    def set_display(view: str | None = None, **settings: Any) -> dict[str, Any]:
        """Change one view's display settings (grayscale=…, show_types=…, frame_guide=…)."""
        target = view_of(view)
        allowed = set(target.display.to_dict())
        unknown = set(settings) - allowed
        if unknown:
            raise CommandError(f"Unknown display settings: {', '.join(sorted(unknown))}")
        app.set_display(target, settings)
        return target.display.to_dict()

    def isolate(nodes: Any = None, view: str | None = None) -> bool:
        """Show only these nodes (default: the selection) in a view; again (or ``[]``) to show everything."""
        target = view_of(view)
        if (target.display.isolate and nodes is None) or (nodes is not None and not isinstance(nodes, str)
                                                          and not list(nodes)):
            app.set_display(target, {"isolate": []})
            ui.status("Showing everything")
            return False
        names = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        if not names:
            raise CommandError("Select what to isolate")
        app.set_display(target, {"isolate": [scene.get_node(n).id for n in names]})
        ui.status(f"Isolated {len(names)} item(s) · again to show everything")
        return True

    def value_check(view: str | None = None) -> bool:
        """Toggle greyscale for a view (a painter's value check)."""
        target = view_of(view)
        state = not target.display.grayscale
        app.set_display(target, {"grayscale": state})
        return state

    def link_views(a: str, b: str, *more: str) -> str:
        """Link views' cameras: they all show what ``a`` shows from now on.

        Linking a view that is already linked to others joins the groups."""
        leader = view_of(a)
        others = [view_of(name) for name in (b, *more)]
        group = leader.link_group or uuid.uuid4().hex[:8]
        for view in [leader, *others]:
            old = view.link_group
            if old and old != group:
                for member in app.views.values():
                    if member.link_group == old:
                        member.link_group = group
            view.link_group = group
        app.sync_linked_cameras(leader)
        announce_links()
        return group

    def unlink_views(view: str | None = None, other: str | None = None) -> None:
        """Take a view out of its link group (or, with ``other``, take ``other`` out of it)."""
        target = view_of(other if other is not None else view)
        group = target.link_group
        target.link_group = ""
        members = [v for v in app.views.values() if group and v.link_group == group]
        if len(members) == 1:  # a group of one links nothing
            members[0].link_group = ""
        announce_links()

    def toggle_link(other: str, view: str | None = None) -> bool:
        """Link two views, or unlink them if they already are. Returns whether they're linked now."""
        target, partner = view_of(view), view_of(other)
        if target.link_group and target.link_group == partner.link_group:
            unlink_views(target.view_name, partner.view_name)
            return False
        link_views(target.view_name, partner.view_name)
        return True

    def link_workspace(view: str | None = None) -> list[str]:
        """Link every canvas view in a view's workspace to it."""
        target = view_of(view)
        workspace = app.wm.doc.workspace_of(target.view_name)
        names = [t for t in app.wm.tiles_of_type("canvas")
                 if t != target.view_name and t in app.views and app.wm.doc.workspace_of(t) == workspace]
        if not names:
            raise CommandError("No other canvas view in this workspace")
        link_views(target.view_name, *names)
        return names

    def linked_with(view: str | None = None) -> list[str]:
        """Names of the views whose camera follows this view's."""
        return sorted(v.view_name for v in app.linked_views(view_of(view)))

    def announce_links() -> None:
        ui.app.events.emit("view.links_changed", {})
        app.wm.schedule_save()

    def frame_guide(kind: str = "16:9", view: str | None = None) -> str:
        """Overlay a framing guide: none, 16:9, 2.39:1, 1:1, 4:3, 3:2."""
        if kind not in FRAME_GUIDES:
            raise CommandError(f"kind must be one of {', '.join(FRAME_GUIDES)}")
        app.set_display(view_of(view), {"frame_guide": kind})
        return kind

    def toggle_markers(view: str | None = None) -> bool:
        """Show or hide the saved view-marker dots in a view."""
        target = view_of(view)
        # Through set_display: it repaints the view and announces the change (header menus, saving).
        app.set_display(target, {"show_markers": not target.display.show_markers})
        return target.display.show_markers

    for name, fn, alias in (("set_display_preset", set_display_preset, "set_display_preset"),
                            ("set_display", set_display, "set_display"), ("isolate", isolate, "isolate"),
                            ("value_check", value_check, "value_check"), ("toggle_markers", toggle_markers, "toggle_markers"),
                            ("link", link_views, "link_views"), ("unlink", unlink_views, "unlink_views"),
                            ("toggle_link", toggle_link, None), ("link_workspace", link_workspace, None),
                            ("frame_guide", frame_guide, "frame_guide")):
        ui.command(name, fn, alias=alias, undoable=False)

    def recompute_presets(_event: Any = None) -> None:
        """A view showing a preset node follows edits of that node."""
        for view in app.views.values():
            name = view.display.preset
            if name not in BUILTIN_PRESETS and scene.has(name):
                try:
                    values = preset_values(name)
                except CommandError:
                    continue
                current = view.display.to_dict()
                if all(current[k] == v for k, v in values.items() if k in current):
                    continue  # unchanged: don't re-filter and repaint on every edit
                app.set_display(view, values)

    ui.on("document.changed", recompute_presets)
    ui.command("linked_with", linked_with, undoable=False, echo=False)

    # ---- Actions & keys -------------------------------------------------------------------

    a = ui.action
    a("views.new_bookmark", "views.create_camera", "New View Marker", keywords="view marker save position",
      category="View")
    def update_text() -> str:
        view = app.active_view()
        name = current_marker(view) if view is not None else None
        return f"Update View Marker “{name}”" if name else "Update Current View Marker"

    a("views.update_bookmark", "views.update_camera", "Update Current View Marker",
      keywords="save update current view marker", category="View", dynamic_text=update_text,
      enabled=lambda: bool(app.active_view() and current_marker(app.active_view())))
    a("views.isolate", "views.isolate", "Isolate Selection", keywords="solo focus hide others", category="View",
      icon="isolate")
    a("views.value_check", "views.value_check", "Value Check (Greyscale View)", keywords="grayscale greyscale values",
      category="View", checked=lambda: bool(app.active_view() and app.active_view().display.grayscale))
    a("views.toggle_markers", "views.toggle_markers", "Show View Markers",
      keywords="show hide marker dots view markers", category="View",
      checked=lambda: bool(app.active_view() and app.active_view().display.show_markers))
    ui.bind("Shift+I", "views.isolate")
    ui.bind("Alt+V", "views.value_check")
    ui.bind("Ctrl+Shift+M", "views.update_bookmark")
    ui.bind("Ctrl+M", "views.toggle_markers")
    for number in range(1, 10):
        a(f"views.bookmark_{number}", "views.bookmark", f"View Marker {number}", args=(number,), category="hidden")
        ui.bind(f"Ctrl+{number}", f"views.bookmark_{number}")
    for order, key in enumerate(("views.value_check", "views.isolate", "views.toggle_markers")):
        ui.menu("canvas", key, path="View", group="view.display", order=order)
    ui.menu("canvas", "views.isolate", group="focus", order=10, when="selection.count > 0")

    def fill_marker_menu(menu: QMenu, view: Any) -> None:
        """Every marker (its colour, Ctrl+N key, the view's current one checked), then what
        Update would do to the current one, New, and the current one's colour."""
        names = cameras()
        current = current_marker(view)
        show = menu.addAction("Show markers in canvas\tCtrl+M",
                              lambda: app.run_command("views.toggle_markers", view.view_name))
        show.setCheckable(True)
        show.setChecked(view.display.show_markers)
        if names:
            menu.addSection("Look through")
        for index, name in enumerate(names):
            action = menu.addAction(swatch_icon(marker_color(name), ring=name == current),
                                    f"{name}\tCtrl+{index + 1}" if index < 9 else name,
                                    lambda n=name: app.run_command("views.look_through", n, view.view_name))
            action.setCheckable(True)
            action.setChecked(name == current)
        menu.addSeparator()
        if current is not None:
            locked = bool(scene.get_node(current).attrs.get("locked", 0))
            moved = marker_moved(view, current)
            text = (f"“{current}” is locked" if locked else
                    f"Update “{current}” to this view" if moved else f"“{current}” matches this view")
            update = menu.addAction(swatch_icon(marker_color(current)), text,
                                    lambda: app.run_command("views.update_camera", current, view.view_name))
            update.setEnabled(moved and not locked)
        else:
            hint = menu.addAction("Update: look through a marker first")
            hint.setEnabled(False)
        menu.addAction("New marker from this view",
                       lambda: app.run_command("views.create_camera", "View marker", view.view_name))
        if current is not None:
            colours = menu.addMenu(swatch_icon(marker_color(current)), f"Colour of “{current}”")
            now = list(scene.get_node(current).attrs.get("color") or [])
            for label, rgb in MARKER_COLORS:
                action = colours.addAction(swatch_icon(QColor(*rgb)), label,
                                           lambda c=rgb: app.run_command("views.set_marker_color", c, current))
                action.setCheckable(True)
                action.setChecked(now == rgb)

    def cameras_menu(menu: QMenu) -> None:
        view = app.active_view()
        if view is not None:
            fill_marker_menu(menu, view)

    ui.menu("canvas", builder=cameras_menu, text="View Markers", icon="marker", path="View", group="view.display", order=10)

    # ---- Canvas tile header: marker, camera link and display menus ------------------------------

    def header_menu_button() -> QToolButton:
        """An icon, what the button is about, and a chevron: it opens its menu."""
        button = QToolButton()
        button.setObjectName("headerMenuButton")
        button.setFocusPolicy(Qt.NoFocus)
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setIconSize(QSize(14, 14))
        button.setMenu(QMenu(button))
        button.setPopupMode(QToolButton.InstantPopup)
        return button

    def header_camera_button(view: Any) -> QToolButton:
        """Shows the view's current marker (its colour and name, "•" once the view has moved
        away from it, i.e. Update would change it); the menu lists every marker."""
        button = header_menu_button()
        menu = button.menu()
        menu.aboutToShow.connect(lambda: (menu.clear(), fill_marker_menu(menu, view)))
        shown: list[Any] = [object()]  # nothing shown yet (None means "no marker")

        def update() -> None:
            try:
                name = current_marker(view)
                state = (name, marker_moved(view, name), tuple(scene.get_node(name).attrs.get("color") or ())) \
                    if name else None
                if state == shown[0]:
                    return  # camera.changed fires every frame of a pan
                shown[0] = state
                if name is None:
                    button.setIcon(icon("marker", "#8a93a3"))
                    button.setText("Markers")
                    button.setToolTip("View markers: saved camera positions to jump back to\n"
                                      "Look through one to make it this view's current marker")
                    return
                button.setIcon(swatch_icon(marker_color(name)))
                button.setText(f"{name}{' •' if state[1] else ''}")
                button.setToolTip(f"Current view marker: {name}"
                                  + (" · the view has moved: Update saves where it looks now" if state[1] else ""))
            except RuntimeError:
                pass

        update()

        def on_event(event: Any) -> None:
            if event.payload.get("view", view.view_name) == view.view_name:
                update()

        subscriptions = [ui.on_ui("view.marker_changed", on_event), ui.on_ui("camera.changed", on_event),
                         ui.on("document.changed", lambda _e: update())]
        button.destroyed.connect(lambda *_a: [s.dispose() for s in subscriptions])
        return button

    def header_preset_button(view: Any) -> QToolButton:
        button = header_menu_button()
        menu = button.menu()
        button.setIcon(icon("display", "#8a93a3"))

        def update_text() -> None:
            try:
                preset = view.display.preset
                button.setText(f"Show: {preset}")
                button.setToolTip(f"What this view shows: the “{preset}” display preset\n"
                                  "Also: framing guide, greyscale, view markers, isolate selection")
            except RuntimeError:
                pass

        def fill() -> None:
            menu.clear()
            for name in presets():
                action = menu.addAction(name, lambda n=name: app.run_command("views.set_display_preset", n,
                                                                             view.view_name))
                action.setCheckable(True)
                action.setChecked(view.display.preset == name)
            menu.addSeparator()
            guides = menu.addMenu("Framing guide")
            for kind in FRAME_GUIDES:
                action = guides.addAction(kind, lambda k=kind: app.run_command("views.frame_guide", k, view.view_name))
                action.setCheckable(True)
                action.setChecked(view.display.frame_guide == kind)
            gray = menu.addAction("Greyscale", lambda: app.run_command("views.value_check", view.view_name))
            gray.setCheckable(True)
            gray.setChecked(view.display.grayscale)
            markers = menu.addAction("View markers", lambda: app.run_command("views.toggle_markers", view.view_name))
            markers.setCheckable(True)
            markers.setChecked(view.display.show_markers)
            menu.addAction("Isolate selection", lambda: app.run_command("views.isolate", None, view.view_name))

        menu.aboutToShow.connect(fill)
        update_text()
        subscription = ui.on_ui("view.display_changed", lambda _e: update_text())
        button.destroyed.connect(lambda *_a: subscription.dispose())
        return button

    def view_label(name: str) -> str:
        return app.wm.title(name) if app.wm.doc.has(name) else name

    def fill_link_menu(menu: QMenu, view: Any) -> None:
        """Every other canvas view, checked when linked; this workspace's first."""
        menu.clear()
        wm = app.wm
        own = wm.doc.workspace_of(view.view_name)
        others = [t for t in wm.tiles_of_type("canvas") if t != view.view_name and t in app.views]
        here = [t for t in others if wm.doc.workspace_of(t) == own]
        elsewhere = [t for t in others if wm.doc.workspace_of(t) != own]
        if not others:
            menu.addAction("No other canvas views · split this one first").setEnabled(False)
        menu.addSection("Follow this view's camera")
        for name in here + elsewhere:
            text = view_label(name)
            workspace = wm.doc.workspace_of(name)
            if name in elsewhere and workspace:
                text += f"   ({wm._workspace_title(workspace)})"
            action = menu.addAction(text, lambda n=name: app.run_command("views.toggle_link", n, view.view_name))
            action.setCheckable(True)
            action.setChecked(bool(view.link_group) and app.views[name].link_group == view.link_group)
        menu.addSeparator()
        if len(here) > 0:
            menu.addAction("Link all views in this workspace",
                           lambda: app.run_command("views.link_workspace", view.view_name))
        unlink = menu.addAction("Unlink this view", lambda: app.run_command("views.unlink", view.view_name))
        unlink.setEnabled(bool(app.linked_views(view)))

    def header_link_button(view: Any) -> QToolButton:
        button = header_menu_button()
        menu = button.menu()
        button.setCheckable(True)
        menu.aboutToShow.connect(lambda: fill_link_menu(menu, view))

        def update() -> None:
            try:
                partners = app.linked_views(view)
                button.setChecked(bool(partners))
                button.setIcon(icon("chain" if partners else "chain_off", "#d5e8ff" if partners else "#8a93a3"))
                button.setText(f"Camera linked ({len(partners) + 1} views)" if partners else "Link camera")
                button.setToolTip("Pan and zoom together with: "
                                  + ", ".join(view_label(v.view_name) for v in partners)
                                  if partners else "Link this view's camera to other views\n"
                                                   "so they pan and zoom together")
            except RuntimeError:
                pass

        update()
        subscriptions = [ui.on_ui(event, lambda _e: update())
                         for event in ("view.links_changed", "view.created", "view.removed")]
        button.destroyed.connect(lambda *_a: [s.dispose() for s in subscriptions])
        return button

    ui.contribute("canvas.header_widgets", header_camera_button)
    ui.contribute("canvas.header_widgets", header_link_button)
    ui.contribute("canvas.header_widgets", header_preset_button)

    # ---- Framing guide overlay --------------------------------------------------------------------

    def paint_guide(view: Any, painter: QPainter) -> None:
        ratio = GUIDE_RATIOS.get(view.display.frame_guide)
        if ratio is None:
            return
        area = QRectF(view.viewport().rect()).adjusted(24, 24, -24, -24)
        width, height = area.width(), area.width() / ratio
        if height > area.height():
            height, width = area.height(), area.height() * ratio
        frame = QRectF(area.center().x() - width / 2, area.center().y() - height / 2, width, height)
        outside = QPainterPath()
        outside.addRect(QRectF(view.viewport().rect()))
        inner = QPainterPath()
        inner.addRect(frame)
        painter.fillPath(outside.subtracted(inner), QColor(0, 0, 0, 90))
        painter.setPen(QPen(theme.ACCENT, 1.0, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(frame)
        painter.setPen(theme.TEXT_LIGHT)
        painter.drawText(frame.topLeft() + QPointF(6, 16), view.display.frame_guide)

    ui.overlay(paint_guide, order=70)
