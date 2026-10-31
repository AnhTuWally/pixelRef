"""Update discovery (M1). Reports that a newer pixelRef exists; never downloads or modifies anything.

The check runs on a worker thread, at most about once a day, and every failure is shown as what it
is (offline, rate-limited, unreadable), never as "up to date". Set ``PIXELREF_DISABLE_UPDATE_CHECK=1``
to turn off the automatic check entirely (the tests do)."""

from __future__ import annotations

import os
import time
from datetime import datetime
from html import escape
from typing import Any, Callable

from pixelref.core.updates import (Channel, DiscoveryResult, UpdatePreferences, UpdateState, check_for_updates,
                                   default_fetch, detect_install_mode, InstallMode)
from pixelref.core.updates.models import RELEASES_PAGE
from pixelref.plugin_api import APP_VERSION

STARTUP_DELAY_MS = 8000
POLL_MS = 30 * 60 * 1000

MESSAGES = {
    UpdateState.UP_TO_DATE: "pixelRef is up to date.",
    UpdateState.OFFLINE: "Couldn't check for updates: no connection to GitHub.",
    UpdateState.RATE_LIMITED: "Couldn't check for updates: GitHub's rate limit was reached. Try again later.",
    UpdateState.INVALID_METADATA: "Couldn't check for updates: GitHub's answer wasn't readable.",
    UpdateState.UNSUPPORTED_PLATFORM: "Update checks aren't available for this platform.",
}


def activate_ui(ui: Any) -> None:
    from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                                   QPushButton, QTextBrowser, QVBoxLayout)

    app = ui.app
    prefs = UpdatePreferences(app.settings)
    mode = detect_install_mode()

    class Worker(QRunnable):
        def __init__(self, service: "Service", channel: Channel) -> None:
            super().__init__()
            self.service, self.channel = service, channel

        def run(self) -> None:
            result = check_for_updates(APP_VERSION, self.channel, fetch=self.service.fetch, mode=mode)
            self.service.done.emit(result)

    class Service(QObject):
        done = Signal(object)
        changed = Signal()

        def __init__(self) -> None:
            super().__init__()
            self.state = UpdateState.IDLE
            self.result: DiscoveryResult | None = None
            self.fetch: Callable = default_fetch
            self.manual = False
            self.done.connect(self._finish, Qt.QueuedConnection)

        def check(self, manual: bool = False) -> bool:
            """Start a check on a worker thread; False when one is already running."""
            if self.state is UpdateState.CHECKING:
                return False
            self.manual = manual
            self.state = UpdateState.CHECKING
            self.changed.emit()
            QThreadPool.globalInstance().start(Worker(self, prefs.channel))
            return True

        def _finish(self, result: DiscoveryResult) -> None:
            self.result, self.state = result, result.state
            prefs.record_attempt(not result.is_failure)
            self.changed.emit()
            if self.manual:
                if result.state is UpdateState.AVAILABLE:
                    show_available()
                else:
                    ui.toast(result.message or MESSAGES.get(result.state, ""))

        @property
        def available(self) -> DiscoveryResult | None:
            r = self.result
            return r if r is not None and r.state is UpdateState.AVAILABLE else None

    service = Service()

    def open_url(url: str) -> None:
        QDesktopServices.openUrl(QUrl(url))

    # ---- The status-bar note: only when a newer version exists, never animated, never steals focus ----

    def make_indicator(_app: Any, _output: str) -> Any:
        button = QPushButton("")
        button.setObjectName("updateIndicator")
        button.setFlat(True)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet("QPushButton#updateIndicator{color:#e0b341;border:none;padding:0 6px;}"
                             "QPushButton#updateIndicator:hover{text-decoration:underline;}")
        button.clicked.connect(show_available)

        def refresh() -> None:
            found = service.available
            show = found is not None and str(found.release.version) != prefs.dismissed_version
            button.setVisible(show)
            if show:
                button.setText(f"Update available: {found.release.version}")
                button.setToolTip("A newer pixelRef is available. Click for details.")

        service.changed.connect(refresh)
        button.destroyed.connect(lambda *_: _disconnect(refresh))
        refresh()
        return button

    def _disconnect(slot: Callable) -> None:
        try:
            service.changed.disconnect(slot)
        except (RuntimeError, TypeError):
            pass

    ui.bar_block(make_indicator, order=90)

    # ---- Dialogs ---------------------------------------------------------------------------------

    held: dict[str, Any] = {}

    def show_available() -> None:
        found = service.available
        if found is None:
            return
        release = found.release
        dialog = QDialog(app.main_window())
        dialog.setWindowTitle("pixelRef — Update available")
        dialog.resize(520, 420)
        dialog.setStyleSheet(app.stylesheet)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"<b>pixelRef {escape(str(release.version))}</b> is available "
                                f"(you have {escape(APP_VERSION)})."))
        notes = QTextBrowser(dialog)
        notes.setOpenLinks(False)
        notes.setPlainText(release.notes or "No release notes.")  # plain text: notes are untrusted
        layout.addWidget(notes, 1)
        if mode is InstallMode.SOURCE:
            layout.addWidget(QLabel("This copy runs from source. Update it with git; pixelRef never changes it itself."))
        else:
            layout.addWidget(QLabel("Download the new ZIP and extract it over a fresh folder. "
                                    "Your boards and settings are not touched."))
        buttons = QDialogButtonBox(dialog)
        page = buttons.addButton("Release Notes && Download", QDialogButtonBox.AcceptRole)
        later = buttons.addButton("Later", QDialogButtonBox.RejectRole)
        skip = buttons.addButton("Skip This Version", QDialogButtonBox.ActionRole)
        page.clicked.connect(lambda: open_url(release.html_url))
        skip.clicked.connect(lambda: (setattr(prefs, "dismissed_version", str(release.version)),
                                      service.changed.emit(), dialog.close()))
        later.clicked.connect(dialog.close)
        layout.addWidget(buttons)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
        held["available"] = dialog

    def show_settings() -> None:
        dialog = QDialog(app.main_window())
        dialog.setWindowTitle("pixelRef — Update Settings")
        dialog.setStyleSheet(app.stylesheet)
        form = QFormLayout(dialog)
        auto = QCheckBox("Check for updates automatically (about once a day)", dialog)
        auto.setChecked(prefs.check_enabled)
        auto.toggled.connect(lambda v: setattr(prefs, "check_enabled", v))
        channel = QComboBox(dialog)
        channel.addItems(["Stable", "Beta (includes pre-releases)"])
        channel.setCurrentIndex(0 if prefs.channel is Channel.STABLE else 1)
        channel.currentIndexChanged.connect(
            lambda i: setattr(prefs, "channel", Channel.STABLE if i == 0 else Channel.BETA))
        download = QCheckBox("Download updates automatically (not available yet)", dialog)
        download.setEnabled(False)
        last = QLabel(dialog)
        status = QLabel(dialog)

        def refresh() -> None:
            stamp = prefs.last_successful_check
            last.setText(datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M") if stamp else "never")
            status.setText("Checking…" if service.state is UpdateState.CHECKING
                           else MESSAGES.get(service.state, "") if service.state is not UpdateState.IDLE else "")

        check_now = QPushButton("Check Now", dialog)
        check_now.clicked.connect(lambda: run_manual_check())
        service.changed.connect(refresh)
        dialog.finished.connect(lambda *_: _disconnect(refresh))
        form.addRow(auto)
        form.addRow("Channel", channel)
        form.addRow(download)
        form.addRow("Last successful check", last)
        form.addRow(check_now, status)
        refresh()
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()
        held["settings"] = dialog

    # ---- Commands ----------------------------------------------------------------------------------

    def run_manual_check() -> str:
        """Check for a newer pixelRef now (works even when automatic checks are off)."""
        if not service.check(manual=True):
            ui.toast("Already checking for updates…")
        return service.state.value

    ui.provide_service("updates", service)
    ui.command("check", run_manual_check, undoable=False, echo=False)
    ui.command("settings", show_settings, undoable=False, echo=False)
    ui.command("state", lambda: service.state.value, undoable=False, echo=False)
    ui.action("updates.check", "updates.check", "Check for Updates…", keywords="update upgrade version new release download",
              category="Window", icon="info")
    ui.action("updates.settings", "updates.settings", "Update Settings…", keywords="update upgrade automatic channel beta stable",
              category="Preferences", icon="info")
    ui.menu("canvas", "updates.check", group="app", order=-2)

    # ---- Periodic check: a small delay after start, then re-evaluated every half hour ----------------

    def periodic() -> None:
        if not os.environ.get("PIXELREF_DISABLE_UPDATE_CHECK") and prefs.check_due():
            service.check()

    QTimer.singleShot(STARTUP_DELAY_MS, periodic)
    timer = QTimer(service)
    timer.timeout.connect(periodic)
    timer.start(POLL_MS)
    ui.track(lambda: (timer.stop(), service.deleteLater()))
