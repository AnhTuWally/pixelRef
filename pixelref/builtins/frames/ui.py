"""Frame notes: the visibility rule, pinning commands, drawing on a paused frame and the badge."""

from __future__ import annotations

import re
from typing import Any

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPen, QTransform

from pixelref.plugin_api import CommandError
from PySide6.QtWidgets import QLabel, QSlider, QWidget

from pixelref.plugin_api.contracts import InspectorSection
from pixelref.plugin_api.ui import InputHandler, theme
from pixelref.plugin_api.ui import widgets as w

LINK = "link"
ENDS = ("source", "target")
LINK_FLOOR = 0.15  # how faint a link end gets when its frame isn't showing
NOTE_COLOR = "#ffb347"
LINK_COLOR = "#6ebeff"

# Drawing with these commands on a paused animation pins the result to its frame.
DRAW_COMMANDS = frozenset({"rect.create", "rect.create_ellipse", "arrow.create", "steps.create",
                           "pen.add_stroke", "text.create", "links.connect", "links.connect_selection"})


def frame_aware(item: Any) -> bool:
    """An animated item (GIF, WebP, movie) with more than one frame."""
    return item is not None and bool(getattr(item, "animated", False)) and item.frame_count() > 1


def parse_frames(spec: Any) -> list[int]:
    """``3``, ``[3, 5]``, ``"3-6"`` or ``"2,5,7-9"`` as a sorted list of frame numbers."""
    if isinstance(spec, int):
        return [spec]
    if isinstance(spec, (list, tuple, set)):
        return sorted({int(f) for f in spec})
    frames: set[int] = set()
    for part in re.split(r"[,\s]+", str(spec).strip()):
        if not part:
            continue
        match = re.fullmatch(r"(\d+)(?:\s*[-–:]\s*(\d+))?", part)
        if match is None:
            raise CommandError(f"Not a frame or range: '{part}' (use 3, 3-6 or 2,5,7-9)")
        first, last = int(match.group(1)), int(match.group(2) or match.group(1))
        frames.update(range(min(first, last), max(first, last) + 1))
    return sorted(frames)


class FramesService:
    def __init__(self, ui: Any) -> None:
        self.ui = ui
        self.session = ui.session
        self.scene = ui.session.scene
        self.canvas = ui.canvas
        self.created: list[str] = []
        self.running: str | None = None
        self.relinked: set[str] = set()
        self._counts: tuple[Any, ...] = ()  # (revision, {host: {frame: [notes, links]}})

    # ---- Link ends ---------------------------------------------------------------------------

    def link_ends(self, link: str) -> list[tuple[str, str | None, int]]:
        """``(end, element name or None, frame)`` for a link's two ends."""
        if not self.scene.has(link):
            return []
        attrs = self.scene.get_node(link).attrs
        return [(end, self.scene.name_of_id(str(attrs.get(end) or "")), int(attrs.get(f"{end}_frame", 0) or 0))
                for end in ENDS]

    def clear_stale_link_frames(self) -> None:
        """Tidy hook: a link end re-attached to something that isn't an animation forgets its frame."""
        links, self.relinked = self.relinked, set()
        for link in links:
            if not self.scene.has(link):
                continue
            for end, element, frame in self.link_ends(link):
                if frame and not frame_aware(self.canvas.items.get(element or "")):
                    self.scene.set_attr(link, f"{end}_frame", 0)

    def link_attr_changed(self, event: Any) -> None:
        name = event.payload["name"]
        if event.payload["attr"] in ENDS and self.scene.has(name) and self.scene.get_node(name).node_type == LINK:
            self.relinked.add(name)

    # ---- Which frames carry notes ---------------------------------------------------------------

    def tick_counts(self, host: str) -> dict[int, list[int]]:
        """``{frame: [notes, links]}`` for an animation (cached until the scene changes)."""
        if not self._counts or self._counts[0] != self.scene.revision:
            table: dict[str, dict[int, list[int]]] = {}
            linked: dict[str, list[str]] = {}
            for node in self.scene.iter_nodes():
                if node.node_type == LINK:
                    for _end, element, frame in self.link_ends(node.name):
                        if frame and element:
                            table.setdefault(element, {}).setdefault(frame, [0, 0])[1] += 1
                            linked.setdefault(element, []).append(node.name)
                elif node.parent and node.attrs.get("frame"):
                    table.setdefault(node.parent, {}).setdefault(int(node.attrs["frame"]), [0, 0])[0] += 1
            self._counts = (self.scene.revision, table, linked)
        return self._counts[1].get(host, {})

    def links_on(self, host: str) -> list[str]:
        """Links with an end anchored to a frame of ``host``."""
        self.tick_counts(host)
        return self._counts[2].get(host, [])

    def link_alphas(self, link: str) -> list[float]:
        """Opacity of each end of a link: faint where it points at a frame the animation isn't showing."""
        return [max(LINK_FLOOR, self.fade(element, frame)) if element and frame else 1.0
                for _end, element, frame in self.link_ends(link)]

    def paint_link(self, item: Any, painter: QPainter, _option: Any, _widget: Any, draw: Any) -> None:
        """Paint filter: a link whose end points at a frame that isn't showing fades out towards that end."""
        alphas = self.link_alphas(item.node_name)
        points = item.route_points()
        if not alphas or min(alphas) >= 1.0 or not points:
            draw(painter)
            return
        t = painter.worldTransform()
        device = painter.device()
        rect = t.mapRect(item.boundingRect()).toAlignedRect().intersected(
            QRect(0, 0, device.width(), device.height()))
        if rect.isEmpty():
            return
        layer = QImage(rect.size(), QImage.Format_ARGB32_Premultiplied)
        layer.fill(0)
        inner = QPainter(layer)
        inner.setRenderHints(painter.renderHints())
        inner.setWorldTransform(t * QTransform.fromTranslate(-rect.x(), -rect.y()))
        draw(inner)
        inner.resetTransform()
        inner.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        start = t.map(points[0]) - rect.topLeft()
        end = t.map(points[-1]) - rect.topLeft()
        gradient = QLinearGradient(start, end)
        if abs(alphas[0] - alphas[1]) < 1e-3:
            gradient.setColorAt(0.0, QColor.fromRgbF(0, 0, 0, alphas[0]))
            gradient.setColorAt(1.0, QColor.fromRgbF(0, 0, 0, alphas[0]))
        else:  # hold each end's strength for a while, then ramp through the middle
            gradient.setColorAt(0.0, QColor.fromRgbF(0, 0, 0, alphas[0]))
            gradient.setColorAt(0.25, QColor.fromRgbF(0, 0, 0, alphas[0]))
            gradient.setColorAt(0.75, QColor.fromRgbF(0, 0, 0, alphas[1]))
            gradient.setColorAt(1.0, QColor.fromRgbF(0, 0, 0, alphas[1]))
        inner.fillRect(layer.rect(), gradient)
        inner.end()
        painter.save()
        painter.resetTransform()
        painter.drawImage(rect.topLeft(), layer)
        painter.restore()

    def paint_ticks(self, painter: QPainter, host: str, total: int, width: int, height: int) -> None:
        """Playback's filmstrip: a marker under each frame that carries notes (amber) or links (blue)."""
        counts = self.tick_counts(host)
        if not counts:
            return
        painter.save()
        painter.setPen(Qt.NoPen)
        for frame, (notes, links) in counts.items():
            if frame > total:
                continue
            x = (frame - 1) / (total - 1) * width if total > 1 else 0.0
            x = max(3.0, min(width - 3.0, x))
            for index, (count, color) in enumerate(((notes, NOTE_COLOR), (links, LINK_COLOR))):
                if count:
                    painter.setBrush(QColor(color))
                    painter.drawRect(QRectF(x - 2.5 + index * 3.0, height - 5.0, 5.0, 5.0))
        painter.restore()

    def describe_frame(self, host: str, frame: int) -> str:
        notes, links = self.tick_counts(host).get(frame, [0, 0])
        text = f"Frame {frame}"
        if notes:
            text += f" · {notes} note{'s' if notes != 1 else ''}"
        if links:
            text += f" · {links} link{'s' if links != 1 else ''}"
        return text

    def show_frame(self, host: str, frame: int) -> None:
        item = self.canvas.items.get(host)
        if frame and frame_aware(item) and item.current_frame() != frame:
            self.ui.app.run_command("playback.go_to_frame", frame, [host])

    # ---- The visibility rule -----------------------------------------------------------

    def host_item(self, name: str) -> Any:
        """The animated parent of a node, when it has one."""
        node = self.scene.nodes.get(name)
        item = self.canvas.items.get(node.parent) if node is not None and node.parent else None
        return item if frame_aware(item) else None

    def frame_of(self, name: str) -> int:
        node = self.scene.nodes.get(name)
        return int(node.attrs.get("frame", 0) or 0) if node is not None else 0

    def ghost_of(self, host: str) -> int:
        node = self.scene.nodes.get(host)
        return int(node.attrs.get("ghost", 0) or 0) if node is not None else 0

    def fade(self, parent: str, frame: int) -> float:
        """How much of a pinned child shows: 1 on its frame, less on the ghost frames around it, 0 elsewhere."""
        host = self.canvas.items.get(parent)
        if not frame_aware(host):
            return 1.0
        distance = abs(frame - host.current_frame())
        ghost = self.ghost_of(parent)
        return 1.0 if distance == 0 else (1.0 - distance / (ghost + 1.0) if distance <= ghost else 0.0)

    def shown(self, parent: str, frame: int) -> bool:
        return self.fade(parent, frame) > 0.0

    def apply_fade(self, item: Any, node: Any, parent: str, frame: int) -> None:
        """Ghosts are faint and can't be clicked."""
        fade = self.fade(parent, frame)
        base = float(node.get("attrs", {}).get("opacity", 1.0)) if isinstance(node, dict) else             float(node.attrs.get("opacity", 1.0))
        opacity = base * fade if fade > 0.0 else base
        if item.opacity() != opacity:
            item.setOpacity(opacity)
        ghost = 0.0 < fade < 1.0
        if item.isEnabled() == ghost:
            item.setEnabled(not ghost)

    def install(self, item: Any, node: dict[str, Any]) -> None:
        """canvas.item_hooks: a pinned child asks its animated parent before it shows."""
        frame = int(node.get("attrs", {}).get("frame", 0) or 0)
        parent = node.get("parent")
        if frame and parent:
            item.visible_rule = lambda: self.shown(parent, frame)
            self.apply_fade(item, node, parent, frame)
        attrs = node.get("attrs", {})
        if node.get("type") == LINK and (attrs.get("source_frame") or attrs.get("target_frame")):
            item.add_paint_filter(self.paint_link)

    def refresh(self, host: str) -> None:
        """The host changed frame: show the children pinned to it, hide the others."""
        if not self.scene.has(host):
            return
        for link in self.links_on(host):
            item = self.canvas.items.get(link)
            if item is not None:
                item.update()
        for child in self.scene.children_of(host):
            frame = self.frame_of(child)
            item = self.canvas.items.get(child)
            if not frame or item is None:
                continue
            want = self.shown(host, frame) and bool(self.session.compute(child, "world_visibility"))
            self.apply_fade(item, self.scene.get_node(child), host, frame)
            if item.isVisible() != want:
                item.setVisible(want)

    # ---- Finding the animation ------------------------------------------------------------

    def hosts(self, paused_only: bool = False) -> list[tuple[str, Any]]:
        return [(name, item) for name, item in self.canvas.items.items()
                if frame_aware(item) and not (paused_only and item.is_playing())]

    def host_under(self, name: str, paused_only: bool = False) -> tuple[str, Any] | None:
        """The front-most animation whose picture holds the centre of ``name``."""
        bounds = self.session.compute(name, "world_bounds")
        if bounds is None:
            return None
        x, y = bounds[0] + bounds[2] / 2.0, bounds[1] + bounds[3] / 2.0
        found = [(n, i) for n, i in self.hosts(paused_only) if n != name and i.sceneBoundingRect().contains(x, y)]
        return max(found, key=lambda pair: pair[1].zValue(), default=None)

    def host_for(self, name: str) -> tuple[str, Any]:
        """Where ``name`` is pinned: its animated parent, else the animation beneath it or selected."""
        node = self.scene.get_node(name)
        parent = self.canvas.items.get(node.parent) if node.parent else None
        if frame_aware(parent):
            return node.parent, parent
        under = self.host_under(name)
        if under is not None:
            return under
        for selected in self.session.selection.names:
            item = self.canvas.items.get(selected)
            if selected != name and frame_aware(item):
                return selected, item
        raise CommandError(f"'{name}' isn't on an animation (GIF or movie)")

    def targets(self, nodes: Any) -> list[str]:
        refs = self.session.selection.names if nodes is None else ([nodes] if isinstance(nodes, str) else list(nodes))
        names = self.scene.top_level([self.scene.resolve(n) for n in refs])
        names = [n for n in names if self.session.types.is_canvas(self.scene.get_node(n).node_type)
                 and not frame_aware(self.canvas.items.get(n))]
        if not names:
            raise CommandError("Select an annotation to pin to a frame")
        return names

    # ---- Drawing on a paused frame ------------------------------------------------------------

    def command_started(self, command: Any) -> None:
        self.running = getattr(command, "name", None)
        self.created = []

    def node_created(self, event: Any) -> None:
        if self.running in DRAW_COMMANDS:
            self.created.append(event.payload["node"]["name"])

    def stamp_created(self) -> None:
        """Tidy hook: what a drawing command just made goes onto the paused frame under it."""
        created, self.created = self.created, []
        if self.running not in DRAW_COMMANDS:
            return
        commands = self.session.commands
        for name in created:
            if not self.scene.has(name) or self.frame_of(name):
                continue
            node = self.scene.get_node(name)
            if node.node_type == LINK:  # an end on a paused animation anchors to the frame on show
                for end, element, frame in self.link_ends(name):
                    host = self.canvas.items.get(element or "")
                    if element and not frame and frame_aware(host) and not host.is_playing():
                        self.scene.set_attr(name, f"{end}_frame", host.current_frame())
                continue
            if node.parent:
                host = self.canvas.items.get(node.parent)
                if frame_aware(host) and not host.is_playing():
                    self.scene.set_attr(name, "frame", host.current_frame())
                continue
            under = self.host_under(name, paused_only=True)
            if under is not None:
                commands.execute("parent", [name], under[0])
                self.scene.set_attr(name, "frame", under[1].current_frame())


class GhostSection(w.InspectorSection):
    """A slider for the animation's ghost frames (0 = off)."""

    def __init__(self, ui: Any, service: FramesService, parent: QWidget) -> None:
        super().__init__("frame_ghost", "Frame Notes", parent)
        self.ui = ui
        self.service = service
        self._name: str | None = None
        self._updating = False
        self.slider = QSlider(Qt.Horizontal, self.body)
        self.slider.setMinimum(0)
        self.slider.setToolTip("Show each frame's notes faded this many frames before and after it (0 = off)")
        self.slider.valueChanged.connect(self._changed)
        self.slider.sliderReleased.connect(lambda: self._commit(self.slider.value()))
        self.label = QLabel("Off", self.body)
        self.label.setObjectName("hexLabel")
        self.label.setMinimumWidth(70)
        self.form.addRow("Ghost", w.hrow(self.body, self.slider, self.label))

    def refresh(self, info: Any) -> None:
        self._name = info.names[0] if info.single else None
        item = self.service.canvas.items.get(self._name) if self._name else None
        if item is None:
            return
        self._updating = True
        try:
            self.slider.setMaximum(max(1, min(item.frame_count() - 1, 30)))
            value = self.service.ghost_of(self._name)
            self.slider.setValue(value)
            self._text(value)
        finally:
            self._updating = False

    def _text(self, value: int) -> None:
        self.label.setText("Off" if value == 0 else f"± {value} frame{'s' if value != 1 else ''}")

    def _changed(self, value: int) -> None:
        self._text(value)
        if not self._updating and not self.slider.isSliderDown():  # keys and clicks; a drag commits on release
            self._commit(value)

    def _commit(self, value: int) -> None:
        if self._name and not self._updating and value != self.service.ghost_of(self._name):
            self.ui.app.run_command("frames.set_ghost", value, [self._name])


def activate_frames_ui(ui: Any) -> None:
    service = FramesService(ui)
    session = ui.session
    scene = session.scene
    ui.provide_service("frames", service)
    ui.contribute("canvas.item_hooks", service.install)

    playback = ui.service("playback")
    if playback is not None:
        playback.frame_changed.connect(service.refresh)
        ui.track(lambda: playback.frame_changed.disconnect(service.refresh))
    ui.track(session.commands.on_before(service.command_started))
    ui.track(scene.event_stream.on("node.created", service.node_created))
    ui.track(session.commands.on_tidy(service.stamp_created))
    ui.track(scene.event_stream.on("node.attr_changed", service.link_attr_changed))
    ui.track(session.commands.on_tidy(service.clear_stale_link_frames))

    def on_selection(event: Any) -> None:
        """Select a note pinned to another frame (outliner, search…): the animation jumps to it."""
        names = event.payload.get("names") or []
        if len(names) == 1 and scene.has(names[0]):
            node = scene.get_node(names[0])
            frame = int(node.attrs.get("frame", 0) or 0)
            if frame and node.parent:
                service.show_frame(node.parent, frame)

    ui.on("selection.changed", on_selection)

    def ghost_changed(event: Any) -> None:
        if event.payload["attr"] == "ghost":
            service.refresh(event.payload["name"])

    ui.track(scene.event_stream.on("node.attr_changed", ghost_changed))

    # ---- Commands ----------------------------------------------------------------------------

    def pin(nodes: Any = None, frame: int | None = None) -> list[str]:
        """Pin annotations to a frame (default: the one on show) of the animation they sit on."""
        pinned = []
        for name in service.targets(nodes):
            host, item = service.host_for(name)
            if scene.get_node(name).parent != host:
                session.commands.execute("parent", [name], host)
            scene.set_attr(name, "frame", int(frame) if frame else item.current_frame())
            pinned.append(name)
        return pinned

    def unpin(nodes: Any = None) -> list[str]:
        """Show annotations on every frame again."""
        refs = session.selection.names if nodes is None else ([nodes] if isinstance(nodes, str) else list(nodes))
        names = [scene.resolve(n) for n in refs]
        names = [n for n in names if service.frame_of(n)]
        if not names:
            raise CommandError("Select an annotation pinned to a frame")
        for name in names:
            scene.set_attr(name, "frame", 0)
        return names

    def copy_to(frames: Any, nodes: Any = None) -> list[str]:
        """Copy annotations onto other frames: ``3``, ``"3-6"`` or ``"2,5,7-9"``."""
        wanted = parse_frames(frames)
        copies: list[str] = []
        for name in service.targets(nodes):
            host, item = service.host_for(name)
            if scene.get_node(name).parent != host:
                session.commands.execute("parent", [name], host)
            count = item.frame_count()
            todo = [f for f in wanted if 1 <= f <= count and f != service.frame_of(name)]
            if not todo:
                raise CommandError(f"No frame to copy '{name}' to (the animation has {count})")
            for frame in todo:
                copy = session.commands.execute("duplicate", [name])[0]
                scene.set_attr(copy, "frame", frame)
                copies.append(copy)
        session.selection.set(copies)
        return copies

    def of(node: Any = None) -> dict[int, int]:
        """The frames of an animation that carry annotations, with how many each."""
        name = scene.resolve(node) if node else next(
            (n for n in session.selection.names if scene.get_node(n).children), None)
        if name is None:
            raise CommandError("Select an animation")
        counts: dict[int, int] = {}
        for child in scene.children_of(name):
            frame = service.frame_of(child)
            if frame:
                counts[frame] = counts.get(frame, 0) + 1
        return dict(sorted(counts.items()))

    def link_end(link: str, end: str = "target", frame: int | None = None) -> str:
        """Anchor one end of a link to a frame (default: the one on show) of the animation it is attached to."""
        if end not in ENDS:
            raise CommandError("end must be 'source' or 'target'")
        name = scene.resolve(link)
        if scene.get_node(name).node_type != LINK:
            raise CommandError("Select a link")
        element = service.link_ends(name)[ENDS.index(end)][1]
        item = service.canvas.items.get(element or "")
        if not frame_aware(item):
            raise CommandError(f"The {end} of '{name}' isn't attached to an animation")
        scene.set_attr(name, f"{end}_frame", int(frame) if frame else item.current_frame())
        return name

    def clear_link_frame(links: Any = None, end: str | None = None) -> list[str]:
        """Send link ends back to the whole element (both ends unless ``end`` is given)."""
        refs = session.selection.names if links is None else ([links] if isinstance(links, str) else list(links))
        done = []
        for name in (scene.resolve(r) for r in refs):
            if scene.has(name) and scene.get_node(name).node_type == LINK:
                for each in ENDS if end is None else (end,):
                    if service.link_ends(name)[ENDS.index(each)][2]:
                        scene.set_attr(name, f"{each}_frame", 0)
                        if name not in done:
                            done.append(name)
        if not done:
            raise CommandError("Select a link that is anchored to a frame")
        return done

    def set_ghost(frames: int, nodes: Any = None) -> list[str]:
        """Ghost mode of animations: show pinned annotations faded this many frames either side (0 = off)."""
        refs = session.selection.names if nodes is None else ([nodes] if isinstance(nodes, str) else list(nodes))
        names = [scene.resolve(r) for r in refs]
        names = [n for n in names if scene.has(n) and frame_aware(service.canvas.items.get(n))]
        if not names:
            raise CommandError("Select an animation (GIF or movie)")
        for name in names:
            scene.set_attr(name, "ghost", max(0, int(frames)))
        return names

    ui.command("set_ghost", set_ghost, alias="set_ghost", label="Ghost frames")
    ui.command("link_frame", link_end, alias="link_to_frame", label="Anchor link end to frame")
    ui.command("clear_link_frame", clear_link_frame, label="Clear link frame")
    ui.command("pin", pin, alias="pin_to_frame", label="Pin to frame")
    ui.command("unpin", unpin, alias="unpin_from_frame", label="Show on all frames")
    ui.command("copy_to", copy_to, alias="copy_to_frames", label="Copy to frames")
    ui.command("of", of, alias="annotated_frames", undoable=False, echo=False)

    ui.contribute("inspector.sections", InspectorSection(
        key="frame_ghost", title="Frame Notes", order=32,
        applies=lambda info: info.single and frame_aware(info.item),
        build=lambda parent, _inspector: GhostSection(ui, service, parent)))

    # ---- Actions, keys, menus ------------------------------------------------------------------

    def selected(test: Any) -> int:
        return sum(1 for n in session.selection.names if scene.has(n) and test(n))

    ui.context_key("frames.pinnable", lambda: selected(
        lambda n: session.types.is_canvas(scene.get_node(n).node_type)
        and not frame_aware(service.canvas.items.get(n))))
    ui.context_key("frames.pinned", lambda: selected(lambda n: bool(service.frame_of(n))))

    def copy_dialog() -> None:
        """Ask for frames and copy the selection onto them."""
        text = ui.app.run_command("ui.prompt_dialog", "Frames (3, 3-6 or 2,5,7-9):", "Copy to Frames", "")
        if text and text.strip():
            ui.app.run_command("frames.copy_to", text.strip())

    ui.command("copy_dialog", copy_dialog, undoable=False, echo=False)
    for key, command, text, keywords, keys, when in (
        ("frames.pin", "frames.pin", "Pin to Current Frame", "annotation note frame gif movie only show",
         ("Alt+F",), "frames.pinnable > 0"),
        ("frames.unpin", "frames.unpin", "Show on All Frames", "annotation note frame gif movie unpin global",
         (), "frames.pinned > 0"),
        ("frames.copy", "frames.copy_dialog", "Copy to Frames…", "annotation note frame gif movie duplicate",
         (), "frames.pinnable > 0"),
    ):
        ui.action(key, command, text, keywords=keywords, category="Playback")
        for sequence in keys:
            ui.bind(sequence, key, when=when)
    for order, (key, when) in enumerate((("frames.pin", "frames.pinnable > 0"), ("frames.unpin", "frames.pinned > 0"),
                                         ("frames.copy", "frames.pinnable > 0"))):
        ui.menu("canvas", key, path="Frame Notes", group="frames", order=order, when=when)
    ui.context_key("frames.link_framed", lambda: selected(
        lambda n: scene.get_node(n).node_type == LINK and any(f for _e, _el, f in service.link_ends(n))))
    ui.action("frames.clear_link", "frames.clear_link_frame", "Clear Link Frame",
              keywords="link end frame anchor whole element", category="Playback")
    ui.menu("canvas", "frames.clear_link", path="Frame Notes", group="frames", order=3,
            when="frames.link_framed > 0")
    ui.place_submenu("canvas", "Frame Notes", "transform", 4, icon="play")

    # ---- Clicking a framed link end jumps the animation to that frame -----------------------------

    class EndJump(InputHandler):
        """Press and release on a link end anchored to a frame, without dragging: go to that frame.
        It never takes the press, so the link grabber still drags the end."""

        REACH = 14.0

        def __init__(self) -> None:
            self.press: tuple[str, int, QPointF] | None = None

        def framed_end_at(self, view: Any, pos: QPointF) -> tuple[str, int] | None:
            for name, item in list(service.canvas.items.items()):
                if not hasattr(item, "route_points") or not item.isVisible() or not view.display.shows(name):
                    continue
                ends = service.link_ends(name)
                points = item.route_points()
                if not points or not any(f for _e, _el, f in ends):
                    continue
                transform = item.sceneTransform()
                for (_end, element, frame), point in zip(ends, (points[0], points[-1])):
                    if frame and element:
                        spot = view.to_viewport(transform.map(point))
                        if (spot - pos).manhattanLength() <= self.REACH:
                            return element, frame
            return None

        def pointer(self, e: Any) -> str | None:
            if e.kind == "press" and e.button == Qt.LeftButton:
                found = self.framed_end_at(e.view, e.pos)
                self.press = (*found, QPointF(e.pos)) if found else None
            elif e.kind == "release" and self.press is not None:
                element, frame, start = self.press
                self.press = None
                if (e.pos - start).manhattanLength() < 4.0:
                    service.show_frame(element, frame)
            return None

    ui.input_handler(EndJump(), 620)  # above the link grabber (610), which keeps the drag

    # ---- Badge: "f3" (or "all") on the selected annotations of an animation ----------------------

    def paint_badges(view: Any, painter: QPainter) -> None:
        if not view.display.show_overlays:
            return
        font = QFont()
        font.setPixelSize(10)
        metrics = QFontMetricsF(font)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(font)
        for name in session.selection.names:
            item = service.canvas.items.get(name)
            if item is None or not item.isVisible() or not view.display.shows(name):
                continue
            if service.host_item(name) is None:
                continue
            frame = service.frame_of(name)
            text = f"f{frame}" if frame else "all"
            try:
                rect = view.mapFromScene(item.sceneBoundingRect()).boundingRect()
            except RuntimeError:
                continue
            badge = QRectF(0.0, 0.0, metrics.horizontalAdvance(text) + 10.0, 16.0)
            badge.moveBottomLeft(rect.topLeft())
            painter.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
            painter.setBrush(QColor(theme.ACCENT) if frame else QColor(20, 24, 32, 235))
            painter.drawRoundedRect(badge, 5, 5)
            painter.setPen(QColor("#ffffff") if frame else QColor(theme.TEXT_LIGHT))
            painter.drawText(badge, Qt.AlignCenter, text)
        painter.restore()

    ui.overlay(paint_badges, order=66)

    def paint_link_badges(view: Any, painter: QPainter) -> None:
        """"f3" at each link end that is anchored to a frame."""
        if not view.display.show_overlays:
            return
        font = QFont()
        font.setPixelSize(10)
        metrics = QFontMetricsF(font)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(font)
        for name, item in list(service.canvas.items.items()):
            if not hasattr(item, "route_points") or not item.isVisible() or not view.display.shows(name):
                continue
            ends = service.link_ends(name)
            points = item.route_points()
            if not points or not any(frame for _e, _el, frame in ends):
                continue
            transform = item.sceneTransform()
            for (_end, _element, frame), point in zip(ends, (points[0], points[-1])):
                if not frame:
                    continue
                text = f"f{frame}"
                badge = QRectF(0.0, 0.0, metrics.horizontalAdvance(text) + 10.0, 16.0)
                badge.moveCenter(view.to_viewport(transform.map(point)) + QPointF(0.0, -14.0))
                painter.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
                painter.setBrush(QColor(LINK_COLOR))
                painter.drawRoundedRect(badge, 5, 5)
                painter.setPen(QColor("#10141c"))
                painter.drawText(badge, Qt.AlignCenter, text)
        painter.restore()

    ui.overlay(paint_link_badges, order=67)
