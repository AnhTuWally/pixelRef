"""Playback: commands, keys, the Attribute Editor section, the quick editor section and its fallback."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QSpinBox, QVBoxLayout, QWidget

from pixelref.plugin_api import CommandError
from pixelref.plugin_api.contracts import InspectorSection, QuickSection, SelectionInfo
from pixelref.plugin_api.ui import HANDLED, InputHandler, PointerEvent, icon, theme
from pixelref.plugin_api.ui import widgets as w

KEY = "playback"  # scene plugin data: {node id: {"frame": n}} for paused images


class PlaybackService(QObject):
    """Finds animated items, applies remembered state, reports frame changes."""

    frame_changed = Signal(str)  # node name

    def __init__(self, ui: Any) -> None:
        super().__init__()
        self.ui = ui
        self.session = ui.session
        self.last_frames: dict[str, int] = {}  # node id -> frame, to survive item rebuilds while playing

    # ---- Items -------------------------------------------------------------------------

    def item(self, name: str) -> Any:
        item = self.ui.canvas.item(name)
        return item if getattr(item, "animated", False) else None

    def animated_selection(self) -> list[str]:
        return [n for n in self.session.selection.names if self.item(n) is not None]

    def targets(self, nodes: Any = None) -> list[str]:
        if nodes is None:
            names = self.animated_selection()
        else:
            refs = [nodes] if isinstance(nodes, str) else list(nodes)
            names = [self.session.scene.resolve(n) for n in refs]
            names = [n for n in names if self.item(n) is not None]
        if not names:
            raise CommandError("Select an animated image (GIF or WebP)")
        return names

    # ---- Remembered state ----------------------------------------------------------------

    def _node_id(self, name: str) -> str:
        return self.session.scene.get_node(name).id

    def paused_frame(self, name: str) -> int | None:
        data = self.session.scene.get_plugin_data(KEY, {}) or {}
        entry = data.get(self._node_id(name))
        return int(entry.get("frame", 1)) if isinstance(entry, dict) else None

    def remember(self, name: str, paused_frame: int | None) -> None:
        """Store (or forget) a paused frame in the scene."""
        data = dict(self.session.scene.get_plugin_data(KEY, {}) or {})
        node_id = self._node_id(name)
        entry = {"frame": int(paused_frame)} if paused_frame is not None else None
        if data.get(node_id) == entry:
            return
        if entry is None:
            data.pop(node_id, None)
        else:
            data[node_id] = entry
        live = set(self.session.scene.by_id)
        data = {k: v for k, v in data.items() if k in live}
        self.session.scene.set_plugin_data(KEY, data or None)
        self.session.mark_unsaved()

    def on_movie(self, item: Any) -> None:
        """image.movie_hooks / movie.started: a (re)built item started its movie —
        registers for its live frame updates so the frame display, scrub slider and
        filmstrip playhead follow it while it plays, not just when it's stepped."""
        name = item.node_name
        if not self.session.scene.has(name):
            return
        item.add_frame_listener(self._on_frame)
        paused = self.paused_frame(name)
        if paused is not None:
            item.pause()
            item.go_to_frame(paused)
            return
        last = self.last_frames.get(self._node_id(name))
        if last and last > 1:
            # Carry on playing where it was before the rebuild (go_to_frame would
            # pause it, and decode every frame).
            item.play_from(last)

    def _on_frame(self, item: Any) -> None:
        name = item.node_name
        if self.session.scene.has(name):
            self.last_frames[self._node_id(name)] = item.current_frame()
            self.frame_changed.emit(name)


class FilmstripWidget(QWidget):
    """A row of thumbnails across the selected animation or movie, with a playhead;
    click or drag across it to scrub (works the same for GIFs/WebPs and movies — see
    ``PixmapItem.filmstrip`` and ``MovieItem.filmstrip``)."""

    HEIGHT = 40

    def __init__(self, service: "PlaybackService", ui: Any, parent: QWidget | None = None,
                height: int = HEIGHT) -> None:
        super().__init__(parent)
        self.service = service
        self.ui = ui
        self.name: str | None = None
        self._listening: Any = None  # the item currently registered with, to avoid double listeners
        self.setFixedHeight(height)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def set_target(self, name: str | None) -> None:
        self.name = name
        item = self.service.item(name) if name else None
        if item is not self._listening:
            self._listening = item
            if item is not None and hasattr(item, "add_filmstrip_listener"):
                item.add_filmstrip_listener(lambda _item: self.update())
        self.setVisible(item is not None and hasattr(item, "filmstrip"))
        self.update()

    def _item(self) -> Any:
        return self.service.item(self.name) if self.name else None

    def _scrub_to(self, x: float) -> None:
        item = self._item()
        if item is None:
            return
        total = max(1, item.frame_count())
        fraction = max(0.0, min(1.0, x / max(1.0, float(self.width()))))
        frame = round(fraction * (total - 1)) + 1
        self.ui.app.run_command("playback.go_to_frame", frame, [self.name])

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._scrub_to(event.position().x())

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton:
            self._scrub_to(event.position().x())

    def paintEvent(self, _event: Any) -> None:  # noqa: N802
        item = self._item()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101217"))
        frames = item.filmstrip() if item is not None and hasattr(item, "filmstrip") else []
        if not frames:
            painter.setPen(QColor(theme.TEXT_LIGHT))
            painter.drawText(self.rect(), Qt.AlignCenter, "Decoding…" if item is not None else "")
            return
        slot = self.width() / len(frames)
        for index, pixmap in enumerate(frames):
            painter.drawPixmap(QRectF(index * slot, 0.0, slot, float(self.height())), pixmap,
                               QRectF(pixmap.rect()))
        total = max(1, item.frame_count())
        fraction = (item.current_frame() - 1) / (total - 1) if total > 1 else 0.0
        x = fraction * self.width()
        painter.setPen(QPen(QColor(theme.ACCENT), 2))
        painter.drawLine(QPointF(x, 0.0), QPointF(x, float(self.height())))


class PlaybackSection(w.InspectorSection):
    """Transport, frame display, scrub slider and go-to-frame for one animated image."""

    def __init__(self, ui: Any, service: PlaybackService, parent: QWidget) -> None:
        super().__init__("playback", "Playback", parent)
        self.ui = ui
        self.service = service
        self._name: str | None = None
        self._updating = False
        body = self.body
        run = ui.app.run_command

        def button(icon_name: str, tip: str, command: str, *args: Any) -> Any:
            b = w.make_tool_button("", tip, width=30, parent=body)
            b.setIcon(icon(icon_name))
            b.clicked.connect(lambda _c=False: self._name and run(command, *args, nodes=[self._name]))
            return b

        self.first_btn = button("frame_first", "First frame (Ctrl+,)", "playback.first_frame")
        self.prev_btn = button("frame_prev", "Previous frame (,)", "playback.prev_frame")
        self.play_btn = w.make_tool_button("", "Play / pause (K)", width=36, parent=body)
        self.play_btn.setIcon(icon("play"))
        self.play_btn.setCheckable(True)
        self.play_btn.clicked.connect(lambda _c=False: self._name and run("playback.toggle", [self._name]))
        self.next_btn = button("frame_next", "Next frame (.)", "playback.next_frame")
        self.last_btn = button("frame_last", "Last frame (Ctrl+.)", "playback.last_frame")
        self.frame_label = QLabel("", body)
        self.frame_label.setObjectName("hexLabel")
        self.frame_label.setMinimumWidth(70)
        self.form.addRow(w.hrow(body, self.first_btn, self.prev_btn, self.play_btn, self.next_btn, self.last_btn,
                                None, self.frame_label))
        self.frame_spin = QSpinBox(body)
        self.frame_spin.setMinimum(1)
        self.frame_spin.setKeyboardTracking(False)
        self.frame_spin.setToolTip("Go to frame (Enter)")
        self.frame_spin.editingFinished.connect(self._go_to_spin)
        self.slider = QSlider(Qt.Horizontal, body)
        self.slider.setMinimum(1)
        self.slider.setToolTip("Drag to scrub through the frames")
        self.slider.sliderMoved.connect(self._scrub)
        self.form.addRow("Frame", w.hrow(body, self.frame_spin, self.slider))
        self.filmstrip = FilmstripWidget(service, ui, body)
        self.form.addRow(self.filmstrip)
        service.frame_changed.connect(self._on_frame_changed)

    def refresh(self, info: SelectionInfo) -> None:
        self._name = info.names[0] if info.single else None
        self.update_display()

    def _on_frame_changed(self, name: str) -> None:
        if name == self._name and self.isVisible():
            self.update_display()

    def update_display(self) -> None:
        self.filmstrip.set_target(self._name)
        item = self.service.item(self._name) if self._name else None
        if item is None:
            return
        self._updating = True
        try:
            count, frame = item.frame_count(), item.current_frame()
            maximum = max(count, frame, 1)
            self.frame_label.setText(f"{frame} / {count}" if count else f"{frame}")
            playing = item.is_playing()
            self.play_btn.setIcon(icon("pause" if playing else "play"))
            self.play_btn.setChecked(playing)
            self.play_btn.setToolTip(("Pause" if playing else "Play") + " (K)")
            for widget in (self.frame_spin, self.slider):
                widget.setMaximum(maximum)
            if not self.frame_spin.hasFocus():
                self.frame_spin.setValue(frame)
            if not self.slider.isSliderDown():
                self.slider.setValue(frame)
        finally:
            self._updating = False

    def _go_to_spin(self) -> None:
        if not self._updating and self._name:
            self.ui.app.run_command("playback.go_to_frame", self.frame_spin.value(), [self._name])

    def _scrub(self, value: int) -> None:
        if not self._updating and self._name:
            self.ui.app.run_command("playback.go_to_frame", value, [self._name])


def activate_playback_ui(ui: Any) -> None:
    service = PlaybackService(ui)
    ui.provide_service("playback", service)
    ui.contribute("image.movie_hooks", service.on_movie)
    ui.contribute("movie.started", service.on_movie)

    def apply_to(names: list[str], fn: Any) -> list[str]:
        for name in names:
            fn(name, service.item(name))
            service.frame_changed.emit(name)  # play state changed: refresh displays
        ui.app.update_views()
        return names

    def play(nodes: Any = None) -> list[str]:
        """Play animated images (default: the selected ones)."""
        def run(name: str, item: Any) -> None:
            item.play()
            service.remember(name, None)
        return apply_to(service.targets(nodes), run)

    def pause(nodes: Any = None) -> list[str]:
        """Pause animated images on their current frame (remembered in the scene)."""
        def run(name: str, item: Any) -> None:
            item.pause()
            service.remember(name, item.current_frame())
        return apply_to(service.targets(nodes), run)

    def toggle(nodes: Any = None) -> bool:
        """Play / pause (K). Returns True when they are now playing."""
        names = service.targets(nodes)
        if any(service.item(n).is_playing() for n in names):
            pause(names)
            return False
        play(names)
        return True

    def show_frame(names: list[str], how: Any) -> int:
        shown = 1
        for index, name in enumerate(names):
            item = service.item(name)
            item.pause()
            frame = how(item)
            service.remember(name, frame)
            if index == 0:
                shown = frame
        return shown

    def go_to_frame(frame: int, nodes: Any = None) -> int:
        """Pause on a frame (numbered from 1, clamped to the last frame). Returns the frame shown."""
        return show_frame(service.targets(nodes), lambda item: item.go_to_frame(int(frame)))

    def next_frame(nodes: Any = None, count: int = 1) -> int:
        """Pause and step forward (.), wrapping from the last frame to the first."""
        return show_frame(service.targets(nodes), lambda item: item.step_frame(int(count)))

    def prev_frame(nodes: Any = None, count: int = 1) -> int:
        """Pause and step back (,), wrapping from the first frame to the last."""
        return show_frame(service.targets(nodes), lambda item: item.step_frame(-int(count)))

    def first_frame(nodes: Any = None) -> int:
        """Pause on the first frame (Ctrl+,)."""
        return show_frame(service.targets(nodes), lambda item: item.go_to_frame(1))

    def last_frame(nodes: Any = None) -> int:
        """Pause on the last frame (Ctrl+.)."""
        return show_frame(service.targets(nodes), lambda item: item.go_to_frame(max(1, item.frame_count())))

    def info(node: Any = None) -> dict[str, Any]:
        """Frame, frame count and play state of an animated image."""
        name = service.targets(node)[0]
        item = service.item(name)
        return {"node": name, "frame": item.current_frame(), "frames": item.frame_count(),
                "playing": item.is_playing()}

    def go_to_dialog() -> None:
        """Ask for a frame number and go there."""
        names = service.targets(None)
        item = service.item(names[0])
        text = ui.app.run_command("ui.prompt_dialog", f"Frame (1–{item.frame_count()}):", "Go to Frame",
                                  str(item.current_frame()))
        if text and text.strip().isdigit():
            ui.app.run_command("playback.go_to_frame", int(text.strip()), names)

    for name, fn, alias, echo in (
        ("play", play, "play", True), ("pause", pause, "pause", True),
        ("toggle", toggle, "toggle_playback", True), ("go_to_frame", go_to_frame, "go_to_frame", True),
        ("next_frame", next_frame, "next_frame", True), ("prev_frame", prev_frame, "prev_frame", True),
        ("first_frame", first_frame, None, True), ("last_frame", last_frame, None, True),
        ("info", info, "playback_info", False), ("go_to_dialog", go_to_dialog, None, False),
    ):
        ui.command(name, fn, alias=alias, undoable=False, echo=echo)

    ui.context_key("playback.selected", lambda: len(service.animated_selection()))
    when = "playback.selected > 0"
    a = ui.action
    for key, command, text, keywords, keys in (
        ("playback.toggle", "playback.toggle", "Play / Pause", "start stop animation gif movie", ("K",)),
        ("playback.next", "playback.next_frame", "Next Frame", "step forward animation gif", (".",)),
        ("playback.prev", "playback.prev_frame", "Previous Frame", "step back animation gif", (",",)),
        ("playback.first", "playback.first_frame", "First Frame", "start rewind animation gif", ("Ctrl+,",)),
        ("playback.last", "playback.last_frame", "Last Frame", "end animation gif", ("Ctrl+.",)),
        ("playback.go_to", "playback.go_to_dialog", "Go to Frame…", "jump frame number animation gif", ()),
    ):
        a(key, command, text, keywords=keywords, category="Playback")
        for sequence in keys:
            ui.bind(sequence, key, when=when)
    for order, key in enumerate(("playback.toggle", "playback.prev", "playback.next", "playback.first",
                                 "playback.last", "playback.go_to")):
        ui.menu("canvas", key, path="Playback", group="playback", order=order, when=when)
    ui.place_submenu("canvas", "Playback", "transform", 3, icon="play")

    def applies(info: SelectionInfo) -> bool:
        return info.single and getattr(info.item, "animated", False)

    ui.contribute("inspector.sections", InspectorSection(
        key="playback", title="Playback", order=31, applies=applies,
        build=lambda parent, _inspector: PlaybackSection(ui, service, parent)))

    # ---- Controls under selected animated images ---------------------------------------------
    #
    # Previous frame · play/pause · next frame, then "frame / count", right under the
    # selected GIFs and WebPs. Clicking them runs the same commands as the keys (, K .).
    # They live in the quick editor's bar; while that is off (or not loaded), each
    # selected animation gets a painted bar of its own instead.

    ui.contribute("quick_edit.sections", QuickSection(
        key="playback", title="Playback Controls", order=10,
        applies=lambda names: any(service.item(n) is not None for n in names),
        build=lambda parent: PlaybackQuickControls(ui, service, parent)))

    controls = PlaybackControls(ui, service)
    ui.overlay(controls.paint, order=65)
    ui.input_handler(controls, 700)  # above the gizmo: its rotate zone reaches under corners
    ui.provide_service("playback.controls", controls)


BUTTONS = (("prev", "frame_prev", "playback.prev_frame", "Previous frame (,)"),
           ("toggle", None, "playback.toggle", "Play / pause (K)"),
           ("next", "frame_next", "playback.next_frame", "Next frame (.)"))


class PlaybackQuickControls(QWidget):
    """The transport in the quick editor bar; acts on every selected animated image."""

    FILMSTRIP_WIDTH = 168
    FILMSTRIP_HEIGHT = 24

    def __init__(self, ui: Any, service: PlaybackService, parent: QWidget) -> None:
        super().__init__(parent)
        self.ui = ui
        self.service = service
        self.names: list[str] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.buttons: dict[str, Any] = {}
        for key, icon_name, command, tip in BUTTONS:
            button = w.make_tool_button("", tip, checkable=key == "toggle", width=28, parent=row)
            button.setIcon(icon(icon_name or "play"))
            button.clicked.connect(lambda _c=False, c=command: self.names and ui.app.run_command(c, list(self.names)))
            layout.addWidget(button)
            self.buttons[key] = button
        self.frame_label = QLabel("", row)
        self.frame_label.setObjectName("hexLabel")
        self.frame_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        layout.addSpacing(4)
        layout.addWidget(self.frame_label)
        outer.addWidget(row)
        self.filmstrip = FilmstripWidget(service, ui, self, height=self.FILMSTRIP_HEIGHT)
        self.filmstrip.setFixedWidth(self.FILMSTRIP_WIDTH)
        outer.addWidget(self.filmstrip)
        service.frame_changed.connect(self._on_frame_changed)

    def refresh(self, names: list[str]) -> None:
        self.names = [n for n in names if self.service.item(n) is not None]
        self.update_display()

    def _on_frame_changed(self, name: str) -> None:
        if self.names and name == self.names[0] and self.isVisible():
            self.update_display()

    def update_display(self) -> None:
        name = self.names[0] if self.names else None
        self.filmstrip.set_target(name)
        item = self.service.item(name) if name else None
        if item is None:
            return
        count, frame = item.frame_count(), item.current_frame()
        # As wide as the longest "frame / count", so the bar doesn't wobble (it re-centres on resize).
        widest = f"{count} / {count}" if count else "0000"
        self.frame_label.ensurePolished()  # measure in the styled font
        self.frame_label.setFixedWidth(int(self.frame_label.fontMetrics().horizontalAdvance(widest)) + 4)
        self.frame_label.setText(f"{frame} / {count}" if count else f"{frame}")
        playing = item.is_playing()
        toggle = self.buttons["toggle"]
        toggle.setIcon(icon("pause" if playing else "play"))
        toggle.setChecked(playing)
        toggle.setToolTip(("Pause" if playing else "Play") + " (K)")


class PlaybackControls(InputHandler):
    """A small clickable transport bar under each selected animated image, while the quick editor is off."""

    BUTTON = 24.0
    GAP = 10.0

    def __init__(self, ui: Any, service: PlaybackService) -> None:
        self.ui = ui
        self.service = service
        self.hovered: tuple[Any, str, str] | None = None  # (view, node, button)
        self.pressed: tuple[Any, str, str] | None = None

    def in_quick_editor(self) -> bool:
        """The quick editor is on, so the transport is in its bar (or the user hid it there)."""
        quick_edit = self.ui.service("quick_edit")
        return quick_edit is not None and quick_edit.enabled

    def _font(self) -> QFont:
        font = QFont()
        font.setPixelSize(11)
        return font

    def layout(self, view: Any) -> list[tuple[str, Any, dict[str, QRectF], QRectF]]:
        """(node, item, button rects, whole bar) for each selected animated image in a view."""
        if not view.display.show_overlays or self.in_quick_editor():
            return []
        metrics = QFontMetricsF(self._font())
        bars = []
        viewport = QRectF(view.viewport().rect())
        for name in self.service.animated_selection():
            item = self.service.item(name)
            if item is None or not item.isVisible() or not view.display.shows(name):
                continue
            try:
                rect = view.mapFromScene(item.sceneBoundingRect()).boundingRect()
            except RuntimeError:
                continue
            count = item.frame_count()
            text = f"{count} / {count}" if count else "000"
            width = self.BUTTON * len(BUTTONS) + metrics.horizontalAdvance(text) + 18.0
            bar = QRectF(0.0, 0.0, width, self.BUTTON + 4.0)
            bar.moveTopLeft(QPointF(rect.center().x() - width / 2.0, rect.bottom() + self.GAP))
            # Keep it on screen when the image's bottom edge is out of view.
            if bar.bottom() > viewport.bottom() - 4.0:
                bar.moveBottom(min(viewport.bottom() - 4.0, max(rect.top() + bar.height(), viewport.top() + bar.height())))
            bar.moveLeft(max(viewport.left() + 4.0, min(bar.left(), viewport.right() - width - 4.0)))
            buttons = {}
            for index, (key, *_rest) in enumerate(BUTTONS):
                buttons[key] = QRectF(bar.left() + 2.0 + index * self.BUTTON, bar.top() + 2.0, self.BUTTON, self.BUTTON)
            bars.append((name, item, buttons, bar))
        return bars

    def _hit(self, view: Any, pos: QPointF) -> tuple[str, str] | None | bool:
        """(node, button) under pos; True when on a bar but not a button; None elsewhere."""
        for name, _item, buttons, bar in self.layout(view):
            for key, rect in buttons.items():
                if rect.contains(pos):
                    return name, key
            if bar.contains(pos):
                return True
        return None

    # ---- Input ----------------------------------------------------------------------------

    def pointer(self, e: PointerEvent) -> str | None:
        if e.kind in ("press", "double") and e.button == Qt.LeftButton:
            hit = self._hit(e.view, e.pos)
            if hit is None:
                return None
            if hit is not True:
                self.pressed = (e.view, *hit)
                name, key = hit
                command = next(c for k, _i, c, _t in BUTTONS if k == key)
                self.ui.app.run_command(command, [name])
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
                tip = next(t for k, _i, _c, t in BUTTONS if k == hovered[2])
                self.ui.status(tip)
        if hovered != self.hovered:
            self.hovered = hovered
            e.view.viewport().update()

    def leave(self, view: Any) -> None:
        if self.hovered is not None:
            self.hovered = None
            view.viewport().update()

    # ---- Painting ---------------------------------------------------------------------------

    def paint(self, view: Any, painter: QPainter) -> None:
        bars = self.layout(view)
        if not bars:
            return
        font = self._font()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(font)
        for name, item, buttons, bar in bars:
            painter.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
            painter.setBrush(QColor(20, 24, 32, 235))
            painter.drawRoundedRect(bar, 7, 7)
            for key, icon_name, _command, _tip in BUTTONS:
                rect = buttons[key]
                state = (view, name, key)
                if self.pressed == state or self.hovered == state:
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(theme.ACCENT) if self.pressed == state else QColor(255, 255, 255, 40))
                    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 5, 5)
                if key == "toggle":
                    icon_name = "pause" if item.is_playing() else "play"
                color = "#ffffff" if key == "toggle" else theme.TEXT_LIGHT.name()
                pixmap = icon(icon_name, color).pixmap(14, 14)
                painter.drawPixmap(QPointF(rect.center().x() - 7, rect.center().y() - 7), pixmap)
            count = item.frame_count()
            text = f"{item.current_frame()}" + (f" / {count}" if count else "")
            painter.setPen(QColor(theme.TEXT_LIGHT))
            text_rect = QRectF(buttons["next"].right() + 6, bar.top(), bar.right() - buttons["next"].right() - 10,
                               bar.height())
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
        painter.restore()
