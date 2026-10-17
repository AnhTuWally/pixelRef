"""Picking a colour anywhere on the screen, not only on the canvas.

The screens are captured once and shown frozen, full screen, with the loupe: a
click picks, Esc cancels. Capturing:

* X11, Windows, macOS: ``QScreen.grabWindow(0)``.
* Wayland (and X11 apps under XWayland, which only see other X11 windows): the
  xdg-desktop-portal ``Screenshot`` call. Its reply only carries a file URI, which
  QtDBus hands to Python as a string. (The portal's ``PickColor`` replies with a
  ``(ddd)`` struct, which PySide6 cannot read.)
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QPoint, QRect, QTimer, QUrl, Qt, Slot
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QImage, QPainter
from PySide6.QtWidgets import QWidget

from pixelref.plugin_api.ui import paint

PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
PORTAL_TIMEOUT_MS = 30_000

Captured = list[tuple[Any, QImage]]  # (QScreen, its picture at device resolution)


def needs_portal() -> bool:
    platform = QGuiApplication.platformName()
    return platform == "wayland" or (platform == "xcb" and bool(os.environ.get("WAYLAND_DISPLAY")))


def grab_screens() -> Captured | None:
    """Every screen through Qt, or None where Qt can't see other applications."""
    captured: Captured = []
    for screen in QGuiApplication.screens():
        pixmap = screen.grabWindow(0)
        if pixmap.isNull():
            return None
        captured.append((screen, pixmap.toImage()))
    return captured or None


def split_desktop(image: QImage) -> Captured:
    """Cut a picture of the whole desktop (all screens) into one picture per screen."""
    screens = QGuiApplication.screens()
    union = QRect()
    for screen in screens:
        union = union.united(screen.geometry())
    if union.isEmpty() or image.isNull():
        return []
    sx, sy = image.width() / union.width(), image.height() / union.height()
    captured: Captured = []
    for screen in screens:
        g = screen.geometry()
        source = QRect(round((g.x() - union.x()) * sx), round((g.y() - union.y()) * sy),
                       round(g.width() * sx), round(g.height() * sy))
        captured.append((screen, image.copy(source)))
    return captured


class PortalScreenshot(QObject):
    """One non-interactive xdg-desktop-portal screenshot; calls ``done`` or ``failed`` once."""

    def __init__(self, done: Callable[[QImage], None], failed: Callable[[str], None]) -> None:
        super().__init__()
        self.done = done
        self.failed = failed
        self.path = ""
        self.finished = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.finish(error="The screenshot portal didn't answer"))

    def bus(self) -> Any:
        from PySide6.QtDBus import QDBusConnection

        return QDBusConnection.sessionBus()

    def listen(self, path: str) -> bool:
        if self.path:
            self.bus().disconnect(PORTAL, self.path, "org.freedesktop.portal.Request", "Response", self,
                                  "1response(uint,QVariantMap)")
        self.path = path
        return bool(path) and self.bus().connect(PORTAL, path, "org.freedesktop.portal.Request", "Response", self,
                                                 "1response(uint,QVariantMap)")

    def start(self) -> None:
        try:
            from PySide6.QtDBus import QDBusMessage
        except ImportError:
            self.finish(error="Screen capture needs Qt D-Bus support")
            return
        bus = self.bus()
        if not bus.isConnected():
            self.finish(error="No D-Bus session: can't reach the screenshot portal")
            return
        token = f"pixelref_{uuid.uuid4().hex[:12]}"
        sender = bus.baseService().lstrip(":").replace(".", "_")
        # Listen before asking: the answer may come before the call returns.
        self.listen(f"{PORTAL_PATH}/request/{sender}/{token}")
        message = QDBusMessage.createMethodCall(PORTAL, PORTAL_PATH, "org.freedesktop.portal.Screenshot",
                                                "Screenshot")
        message.setArguments(["", {"handle_token": token, "interactive": False, "modal": False}])
        reply = bus.call(message)
        if reply.type() != QDBusMessage.ReplyMessage:
            self.finish(error=f"The screenshot portal refused: {reply.errorMessage() or reply.errorName()}")
            return
        handle = reply.arguments()[0] if reply.arguments() else ""
        handle = handle.path() if hasattr(handle, "path") else str(handle)
        if handle and handle != self.path:  # portals older than the handle_token convention
            self.listen(handle)
        self.timer.start(PORTAL_TIMEOUT_MS)

    @Slot("uint", "QVariantMap")
    def response(self, code: int, results: dict[str, Any]) -> None:
        if code != 0:
            self.finish(error="Screen capture cancelled" if code == 1 else "Screen capture failed")
            return
        uri = str(results.get("uri", "") or "")
        path = QUrl(uri).toLocalFile() if uri else ""
        image = QImage(path) if path else QImage()
        if path:
            # The file exists only for this pick (some desktops write it to ~/Pictures).
            Path(path).unlink(missing_ok=True)
        if image.isNull():
            self.finish(error="The screenshot portal returned no picture")
            return
        self.finish(image=image)

    def finish(self, image: QImage | None = None, error: str = "") -> None:
        if self.finished:
            return
        self.finished = True
        self.timer.stop()
        if self.path:
            self.listen("")
        if image is not None:
            self.done(image)
        else:
            self.failed(error)
        self.deleteLater()


def capture_screens(done: Callable[[Captured], None], failed: Callable[[str], None]) -> None:
    """Capture every screen, now (Qt) or when the portal answers (Wayland)."""
    if not needs_portal():
        captured = grab_screens()
        if captured:
            done(captured)
            return
        if QGuiApplication.platformName() != "xcb":
            failed("This system doesn't let pixelRef capture the screen")
            return
    PortalScreenshot(lambda image: done(split_desktop(image)), failed).start()


class FrozenScreen(QWidget):
    """One screen's picture, full screen, with the loupe under the cursor."""

    def __init__(self, picker: "ScreenPicker", screen: Any, image: QImage) -> None:
        super().__init__(None, Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.picker = picker
        self.image = image
        self.pos: QPoint | None = None
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        # Image pixels per widget pixel (the screen's scale factor).
        image.setDevicePixelRatio(max(1e-6, image.width() / max(1, screen.geometry().width())))

    def color_at(self, pos: QPoint) -> QColor:
        ratio = self.image.devicePixelRatio()
        x = min(max(0, int(pos.x() * ratio)), self.image.width() - 1)
        y = min(max(0, int(pos.y() * ratio)), self.image.height() - 1)
        return self.image.pixelColor(x, y)

    def paintEvent(self, _event: Any) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.drawImage(QPoint(0, 0), self.image)
        paint.paint_banner(painter, self.width(), "◉  Pick a color from the screen · click to pick   —   Esc to cancel")
        if self.pos is not None:
            from .ui import paint_loupe

            paint_loupe(painter, self.image, self.pos, self.width(), self.height(), self.color_at(self.pos))
        painter.end()

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802
        self.pos = event.position().toPoint()
        self.picker.hovered(self.color_at(self.pos))
        self.update()

    def leaveEvent(self, _event: Any) -> None:  # noqa: N802
        self.pos = None
        self.update()

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.picker.finish(self.color_at(event.position().toPoint()))
        else:
            self.picker.finish(None)

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.picker.finish(None)


class ScreenPicker:
    """Capture, show the frozen screens, report the picked colour (or None)."""

    def __init__(self, picked: Callable[[QColor | None], None], hovered: Callable[[QColor], None],
                 failed: Callable[[str], None]) -> None:
        self.on_picked = picked
        self.on_hovered = hovered
        self.on_failed = failed
        self.windows: list[FrozenScreen] = []
        self.busy = False

    def start(self) -> bool:
        if self.busy:
            return False
        self.busy = True
        capture_screens(self.show, self.failed)
        return True

    def failed(self, message: str) -> None:
        self.busy = False
        self.on_failed(message)

    def show(self, captured: Captured) -> None:
        if not captured:
            self.failed("Couldn't capture the screen")
            return
        for screen, image in captured:
            window = FrozenScreen(self, screen, image)
            self.windows.append(window)
            window.showFullScreen()
        # Keyboard focus (Esc) on the screen under the cursor.
        under = QGuiApplication.screenAt(QCursor.pos())
        focus = next((window for window in self.windows if window.screen() is under), self.windows[-1])
        focus.raise_()
        focus.activateWindow()

    def hovered(self, color: QColor) -> None:
        self.on_hovered(color)

    def finish(self, color: QColor | None) -> None:
        self.close()
        self.on_picked(color)

    def close(self) -> None:
        windows, self.windows = self.windows, []
        for window in windows:
            window.close()
        self.busy = False
