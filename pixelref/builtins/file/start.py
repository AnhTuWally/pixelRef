"""Scene thumbnails and the start panel.

Saving a scene renders the board into a small JPEG kept in the file's
``thumbnail`` section (near the top of the file, so it can be read without
loading the scene). On an empty board each canvas view shows the start panel:
Open, and the recent files with their thumbnails, names, folders and dates.
"""

from __future__ import annotations

import base64
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pixelref.plugin_api import APP_VERSION, read_thumbnail
from pixelref.plugin_api.ui import logo_pixmap

THUMBNAIL_SIZE = 320       # longest side of the stored thumbnail, in pixels
CARD_THUMB = QSize(176, 110)
MAX_CARDS = 6


# ---- Thumbnails ------------------------------------------------------------------------------------


def render_thumbnail(canvas: Any, max_side: int = THUMBNAIL_SIZE) -> dict[str, Any] | None:
    """The board as a thumbnail section, or None when it's empty."""
    items = [item for item in canvas.visible_content_items()
             if not any(getattr(p, "is_content", False) for p in _ancestors(item))]
    if not items:
        return None
    image = canvas.render_items(items, opaque=True, max_side=max_side, final=False)
    if image is None or image.isNull():
        return None
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.convertToFormat(QImage.Format_RGB32).save(buffer, "JPG", 82)
    buffer.close()
    return {"mime": "image/jpeg", "width": image.width(), "height": image.height(), "encoding": "base64",
            "data": base64.b64encode(bytes(data)).decode("ascii"),
            "updated": datetime.now().isoformat(timespec="seconds")}


def _ancestors(item: Any) -> list[Any]:
    found, parent = [], item.parentItem()
    while parent is not None:
        found.append(parent)
        parent = parent.parentItem()
    return found


def thumbnail_image(section: dict[str, Any] | None) -> QImage | None:
    if not section or section.get("encoding", "base64") != "base64":
        return None
    try:
        raw = base64.b64decode(section.get("data", ""), validate=True)
    except (ValueError, TypeError):
        return None
    image = QImage()
    return image if image.loadFromData(raw) and not image.isNull() else None


class ThumbnailCache:
    """Recent files' thumbnails, re-read only when a file changes."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, QImage | None]] = {}

    def get(self, path: str) -> QImage | None:
        try:
            mtime = Path(path).stat().st_mtime
        except OSError:
            return None
        cached = self._cache.get(path)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        image = thumbnail_image(read_thumbnail(path))
        self._cache[path] = (mtime, image)
        return image


# ---- The start panel -----------------------------------------------------------------------------------


class RecentCard(QFrame):
    """One recent file: thumbnail, name, folder and date. Click to open it."""

    clicked = Signal(str)

    def __init__(self, path: str, image: QImage | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = path
        self.setObjectName("recentCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"Open {path}")
        self.setFixedWidth(CARD_THUMB.width() + 16)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(3)
        self.thumb = QLabel(self)
        self.thumb.setObjectName("recentThumb")
        self.thumb.setFixedSize(CARD_THUMB)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setPixmap(_card_pixmap(image, self.devicePixelRatioF()))
        file = Path(path)
        self.name = QLabel(file.stem, self)
        self.name.setObjectName("recentName")
        self.folder = QLabel(self)
        self.folder.setObjectName("recentPath")
        metrics = self.folder.fontMetrics()
        self.folder.setText(metrics.elidedText(str(file.parent), Qt.ElideMiddle, CARD_THUMB.width()))
        try:
            when = datetime.fromtimestamp(file.stat().st_mtime).strftime("%d %b %Y, %H:%M")
        except OSError:
            when = ""
        self.date = QLabel(when, self)
        self.date.setObjectName("recentPath")
        for widget in (self.thumb, self.name, self.folder, self.date):
            layout.addWidget(widget)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        # Take the press: ignored, it would reach the canvas below, which would then
        # never see the matching release and act as if the button stayed down.
        event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802
        event.accept()
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            path = self.path
            # Open once this click is over (opening rebuilds the board under the pointer).
            QTimer.singleShot(0, lambda: self.clicked.emit(path))


class ResumeCard(QFrame):
    """The Untitled draft kept from the last close (Session Resume is off, so it
    wasn't reopened automatically). Click to bring it back."""

    clicked = Signal()

    def __init__(self, image: QImage | None, subtitle: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("resumeCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Resume the Untitled board you were working on last")
        self.setFixedWidth(CARD_THUMB.width() + 16)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(3)
        self.thumb = QLabel(self)
        self.thumb.setObjectName("recentThumb")
        self.thumb.setFixedSize(CARD_THUMB)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setPixmap(_card_pixmap(image, self.devicePixelRatioF()))
        self.name = QLabel("Resume Last Session", self)
        self.name.setObjectName("recentName")
        self.folder = QLabel("Untitled board · unsaved", self)
        self.folder.setObjectName("recentPath")
        self.date = QLabel(subtitle, self)
        self.date.setObjectName("recentPath")
        for widget in (self.thumb, self.name, self.folder, self.date):
            layout.addWidget(widget)

    def update_content(self, image: QImage | None, subtitle: str) -> None:
        self.thumb.setPixmap(_card_pixmap(image, self.devicePixelRatioF()))
        self.date.setText(subtitle)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802
        event.accept()
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            QTimer.singleShot(0, self.clicked.emit)


def _card_pixmap(image: QImage | None, ratio: float) -> QPixmap:
    """The thumbnail fitted into the card's frame (or a placeholder)."""
    size = CARD_THUMB * ratio
    pixmap = QPixmap(size)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(QColor(14, 16, 20))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
    if image is not None:
        scaled = image.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        painter.drawImage((CARD_THUMB.width() - scaled.width() / ratio) / 2,
                          (CARD_THUMB.height() - scaled.height() / ratio) / 2, scaled)
    else:
        painter.setPen(QColor("#5c6370"))
        painter.drawText(0, 0, CARD_THUMB.width(), CARD_THUMB.height(), Qt.AlignCenter, "No preview")
    painter.end()
    return pixmap


STYLE = """
#startPanel { background: rgba(20, 22, 27, 235); border: 1px solid #2e3240; border-radius: 12px; }
#startPanel QLabel, #startPanel QWidget#startGrid { background: transparent; }
#startTitle { color: #e6e9ef; font-size: 16px; font-weight: bold; }
#startHint { color: #8a93a3; }
#startVersion { color: #6f7787; font-size: 11px; }
#startSection { color: #aeb4bf; font-weight: bold; margin-top: 6px; }
#recentCard { background: transparent; border: 1px solid transparent; border-radius: 8px; }
#recentCard:hover { background: rgba(61, 142, 240, 30); border-color: #3d8ef0; }
#resumeCard { background: rgba(61, 142, 240, 20); border: 1px solid #3d8ef0; border-radius: 8px; }
#resumeCard:hover { background: rgba(61, 142, 240, 55); }
#recentThumb { border-radius: 4px; }
#recentName { color: #e6e9ef; font-weight: bold; }
#recentPath { color: #6f7787; font-size: 11px; }
#resumeCheckbox { color: #aeb4bf; }
"""


class StartPanel(QFrame):
    """Shown over an empty board: Open… and the recent files."""

    def __init__(self, ui: Any, view: Any, cache: ThumbnailCache) -> None:
        super().__init__(view.viewport())
        self.ui = ui
        self.view = view
        self.cache = cache
        self.setObjectName("startPanel")
        self.setStyleSheet(STYLE)
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(8)
        # The panel is always exactly as big as its contents (fonts from the style
        # sheet, cards added later): a size computed once goes stale and clips.
        layout.setSizeConstraint(QLayout.SetFixedSize)
        header = QHBoxLayout()
        header.setSpacing(12)
        logo = QLabel(self)
        logo.setPixmap(logo_pixmap(40, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = QLabel("Your board is empty", self)
        self.title.setObjectName("startTitle")
        self.hint = QLabel(self)
        self.hint.setObjectName("startHint")
        self.version = QLabel(f"pixelRef {APP_VERSION}", self)
        self.version.setObjectName("startVersion")
        titles.addWidget(self.title)
        titles.addWidget(self.hint)
        titles.addWidget(self.version)
        header.addWidget(logo, 0, Qt.AlignTop)
        header.addLayout(titles, 1)
        self.open_button = QPushButton("Open…", self)
        self.open_button.setFocusPolicy(Qt.NoFocus)
        self.open_button.clicked.connect(lambda: ui.app.run_command("file.open"))
        self.guide_button = QPushButton("Getting Started", self)
        self.guide_button.setObjectName("gettingStartedButton")
        self.guide_button.setToolTip("A short tour of pixelRef: the basic and advanced features")
        self.guide_button.setFocusPolicy(Qt.NoFocus)
        self.guide_button.clicked.connect(lambda: ui.app.run_command("help.getting_started"))
        header.addWidget(self.guide_button, 0, Qt.AlignTop)
        header.addWidget(self.open_button, 0, Qt.AlignTop)
        layout.addLayout(header)
        self.resume_checkbox = QCheckBox("Resume Last Session on Launch", self)
        self.resume_checkbox.setObjectName("resumeCheckbox")
        self.resume_checkbox.setFocusPolicy(Qt.NoFocus)
        self.resume_checkbox.setToolTip("Reopen this exact board automatically next time pixelRef starts")
        self.resume_checkbox.toggled.connect(lambda checked: ui.app.run_command("file.set_resume_enabled", checked))
        layout.addWidget(self.resume_checkbox)
        self.recent_title = QLabel("Recent", self)
        self.recent_title.setObjectName("startSection")
        layout.addWidget(self.recent_title)
        self.grid_host = QWidget(self)
        self.grid_host.setObjectName("startGrid")
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        layout.addWidget(self.grid_host)
        self.cards: list[RecentCard] = []
        self.resume_card: ResumeCard | None = None
        self._filter = _ResizeWatcher(self)
        view.viewport().installEventFilter(self._filter)
        self.hide()

    # The panel is a card over the board: clicks on it never reach the canvas.
    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        event.accept()

    def mouseReleaseEvent(self, event: Any) -> None:  # noqa: N802
        event.accept()

    def mouseDoubleClickEvent(self, event: Any) -> None:  # noqa: N802
        event.accept()

    def sync_resume_checkbox(self) -> None:
        """Reflect the Session Resume setting, and hide the checkbox when there was
        nothing on the board last time (nothing to resume either way)."""
        recovery = self.ui.service("recovery")
        if recovery is None:
            return
        self.resume_checkbox.setVisible(not recovery.last_session_is_empty())
        blocked = self.resume_checkbox.blockSignals(True)
        self.resume_checkbox.setChecked(recovery.resume_enabled)
        self.resume_checkbox.blockSignals(blocked)

    def _update_resume_card(self) -> None:
        """A kept Untitled draft, offered back when Session Resume itself is off
        (it reopens the draft automatically when the setting is on)."""
        recovery = self.ui.service("recovery")
        info = recovery.resumable_untitled() if recovery is not None else None
        if info is None:
            if self.resume_card is not None:
                self.layout().removeWidget(self.resume_card)
                self.resume_card.deleteLater()
                self.resume_card = None
            return
        image = thumbnail_image(read_thumbnail(str(info["path"])))
        when = str(info.get("saved_at", "")).replace("T", " ")
        subtitle = f"{info.get('items', '?')} items · autosaved {when}"
        if self.resume_card is None:
            self.resume_card = ResumeCard(image, subtitle, self)
            self.resume_card.clicked.connect(lambda: self.ui.app.run_command("file.resume_last_session"))
            self.layout().insertWidget(2, self.resume_card)  # under the header and the checkbox
        else:
            self.resume_card.update_content(image, subtitle)

    def reload(self) -> None:
        """Re-read the recent files (and any changed thumbnails), as many as fit."""
        for card in self.cards:
            self.grid.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards = []
        self.sync_resume_checkbox()
        self._update_resume_card()
        files = self.ui.service("files").recent_files()[:MAX_CARDS]
        lines = [line for line in self.ui.app.welcome_lines() if line != self.title.text()]
        self.hint.setText(" · ".join(lines) if files else "\n".join(lines))
        viewport = self.view.viewport().rect()
        columns = max(1, min(3, (viewport.width() - 60) // (CARD_THUMB.width() + 22)))
        # A small view gets fewer rows of cards (none if even one row won't fit). Worked
        # out up front: Qt keeps stale size hints for widgets just removed from a layout.
        self.recent_title.setVisible(bool(files))
        self.grid_host.setVisible(False)
        rows = 0
        if files:
            probe = RecentCard(files[0], None)
            probe.setStyleSheet(STYLE)
            probe.ensurePolished()
            card_height = probe.sizeHint().height()
            probe.deleteLater()
            room = viewport.height() - 16 - self._height() - self.layout().spacing()
            spacing = self.grid.spacing()
            rows = max(0, (room + spacing) // (card_height + spacing))
        files = files[:rows * columns]
        for index, path in enumerate(files):
            card = RecentCard(path, self.cache.get(path), self.grid_host)
            card.clicked.connect(lambda p: self.ui.app.run_command("file.open_path", p))
            self.grid.addWidget(card, index // columns, index % columns)
            # Now, not on the next event loop pass (the layout's default), so the panel's
            # size counts it straight away.
            card.show()
            self.cards.append(card)
        self.recent_title.setVisible(bool(self.cards))
        self.grid_host.setVisible(bool(self.cards))
        self.layout().activate()
        self.place()

    def _height(self) -> int:
        """The panel's height as laid out now (style sheet fonts included)."""
        self.ensurePolished()
        self.layout().invalidate()
        return self.layout().totalSizeHint().height()

    def place(self) -> None:
        viewport = self.view.viewport().rect()
        self.move(max(8, (viewport.width() - self.width()) // 2), max(8, (viewport.height() - self.height()) // 2))

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.place()  # stays centred as its contents settle


class _ResizeWatcher(QObject):
    def __init__(self, panel: StartPanel) -> None:
        super().__init__(panel)
        self.panel = panel

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        # The number of columns follows the width; a view coming into sight (another
        # workspace shown) may have a new size since the panel was laid out.
        if event.type() in (QEvent.Resize, QEvent.Show) and not self.panel.isHidden():
            self.panel.reload()
        return False


def activate_start_ui(ui: Any) -> None:
    session = ui.session
    canvas = ui.canvas
    app = ui.app
    cache = ThumbnailCache()
    panels: dict[str, StartPanel] = {}

    # ---- Thumbnail on save ----------------------------------------------------------------

    def store_thumbnail(scene_session: Any, _path: Any) -> None:
        """scene.before_save: keep a picture of the board in the file."""
        canvas.flush_pending(handlers=False)
        section = render_thumbnail(canvas)
        if section is None:
            scene_session.scene.sections.pop("thumbnail", None)
        else:
            scene_session.scene.sections["thumbnail"] = section

    ui.contribute("scene.before_save", store_thumbnail)

    def thumbnail(path: str | None = None, save_to: str | None = None) -> dict[str, Any] | None:
        """A scene file's thumbnail (default: the open scene's file): its size and date,
        and with ``save_to`` the picture written out as an image file."""
        source = path or (str(session.scene_path) if session.scene_path else None)
        section = read_thumbnail(source) if source else None
        if section is None:
            return None
        if save_to:
            image = thumbnail_image(section)
            if image is None or not image.save(str(save_to)):
                return None
        return {k: v for k, v in section.items() if k != "data"}

    ui.command("thumbnail", thumbnail, undoable=False, echo=False)

    # ---- Start panel on empty boards ------------------------------------------------------------

    def wanted(view: Any) -> bool:
        tool = app.tools.active
        return (canvas.scene_is_empty() and view.display.show_overlays
                and tool is not None and tool.id == app.tools.default_id)

    def attach(name: str) -> None:
        view = app.views.get(name)
        if view is None or name in panels:
            return
        panel = StartPanel(ui, view, cache)
        panels[name] = panel
        view.welcome_widget = panel
        update(name)

    def detach(name: str) -> None:
        panel = panels.pop(name, None)
        if panel is not None:
            view = app.views.get(name)
            if view is not None and view.welcome_widget is panel:
                view.welcome_widget = None
            panel.deleteLater()

    def update(name: str | None = None) -> None:
        for view_name in ([name] if name else list(panels)):
            panel, view = panels.get(view_name), app.views.get(view_name)
            if panel is None or view is None:
                continue
            # isHidden(), not isVisible(): views in other workspaces are hidden, so their
            # panels never count as visible, yet must still be shown or hidden.
            show = wanted(view)
            if show and panel.isHidden():
                panel.reload()
                panel.show()
                panel.raise_()
            elif not show and not panel.isHidden():
                panel.hide()
                view.viewport().update()

    for name in list(app.views):
        attach(name)
    ui.on_ui("view.created", lambda e: attach(e.payload["name"]))
    ui.on_ui("view.removed", lambda e: detach(e.payload["name"]))
    for event in ("canvas.refreshed", "tool.changed", "view.display_changed"):
        ui.on_ui(event, lambda _e: update())

    def on_command_finished(event: Any) -> None:
        # Keep a visible panel's checkbox in sync with the File menu's own toggle.
        if event.payload.get("command") in ("file.toggle_resume", "file.set_resume_enabled"):
            for panel in panels.values():
                panel.sync_resume_checkbox()

    ui.on_ui("command.finished", on_command_finished)

    def dispose() -> None:
        for name in list(panels):
            detach(name)

    ui.track(dispose)
    ui.provide_service("start_panel", type("StartPanels", (), {"panels": panels, "update": staticmethod(update)})())
