"""Update discovery, and (when enabled) verified download and install-on-restart.

By default (and in this build) it only reports that a newer pixelRef exists and links to the release page.
Downloading and installing from inside pixelRef is offered only when ``flow.assist_status`` says so: the
feature flag is on, this is a standalone copy with the helper bundled, a release key is embedded and the
folder is writable. The app itself never replaces a file: it stages a verified update and hands over to the
independent helper, after the normal close flow (unsaved-board prompts) has agreed.

The check runs on a worker thread, at most about once a day, and every failure is shown as what it
is (offline, rate-limited, unreadable), never as "up to date". Set ``PIXELREF_DISABLE_UPDATE_CHECK=1``
to turn off the automatic check entirely (the tests do)."""

from __future__ import annotations

import os
import time
from datetime import datetime
from html import escape
from typing import Any, Callable

from pixelref.core.updates import (Channel, DiscoveryResult, UpdatePreferences, UpdateState, Version, application_root,
                                   check_for_updates, current_platform, default_fetch, detect_install_mode, InstallMode)
from pixelref.core.updates import flow, install, recovery
from pixelref.core.updates.staging import Cancelled, default_opener
from pixelref.core.updates.verification import RELEASE_TRUST
from pixelref.plugin_api import APP_VERSION

STARTUP_DELAY_MS = 8000
ACK_DELAY_MS = 2000
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
    from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                                   QMessageBox, QProgressBar, QPushButton, QTextBrowser, QVBoxLayout)

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

    class PrepareWorker(QRunnable):
        """Download, verify, stage and preflight off the UI thread. Touches only updater-owned folders."""

        def __init__(self, service: "Service", result: DiscoveryResult) -> None:
            super().__init__()
            self.service, self.result = service, result

        def run(self) -> None:
            s = self.service
            try:
                staged = flow.prepare_update(
                    self.result, root=s.root, platform=s.platform, installed=Version.parse(APP_VERSION),
                    channel=prefs.channel, trust=s.trust, opener=s.opener,
                    progress=lambda done, total: s.progress.emit(done, total), cancelled=lambda: s.cancel_flag)
                s.prepared.emit(staged, "")
            except Cancelled:
                s.prepared.emit(None, "cancelled")
            except Exception as exc:  # noqa: BLE001  (VerificationError, StagingError, InstallError, OSError…)
                s.prepared.emit(None, str(exc) or exc.__class__.__name__)

    class Service(QObject):
        done = Signal(object)
        changed = Signal()
        progress = Signal(int, int)
        prepared = Signal(object, str)
        prep_changed = Signal()

        def __init__(self) -> None:
            super().__init__()
            self.state = UpdateState.IDLE
            self.result: DiscoveryResult | None = None
            self.fetch: Callable = default_fetch
            self.manual = False
            # Everything below can be replaced by tests.
            self.opener: Callable = default_opener
            self.trust = RELEASE_TRUST
            self.mode = mode
            self.root = application_root()
            self.platform = current_platform()
            self.bundle = None
            self.launch: Callable = flow.launch_helper
            self.request_close: Callable[[], bool] = app.request_close
            self.quit: Callable[[], None] = lambda: QApplication.quit()
            self.confirm: Callable[[str, str], bool] = lambda title, text: QMessageBox.question(
                app.main_window(), title, text) == QMessageBox.Yes
            self.warn: Callable[[str, str], None] = lambda title, text: QMessageBox.warning(app.main_window(), title, text)
            self.prep_state = "idle"          # idle | preparing | ready | failed
            self.staged = None
            self.prep_error = ""
            self.cancel_flag = False
            self.done.connect(self._finish, Qt.QueuedConnection)
            self.prepared.connect(self._prepared, Qt.QueuedConnection)

        # ---- discovery ----

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
            if result.state is not UpdateState.AVAILABLE or self.prep_state != "ready":
                self.prep_state, self.staged, self.prep_error = "idle", None, ""
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

        # ---- installing (only when assist() says so) ----

        def assist(self) -> tuple[bool, str]:
            return flow.assist_status(self.mode, self.root, self.trust, self.platform, bundle=self.bundle)

        def can_offer_install(self) -> bool:
            found = self.available
            return found is not None and found.can_install and self.assist()[0]

        def prepare(self) -> bool:
            found = self.available
            if not self.can_offer_install() or self.prep_state == "preparing":
                return False
            self.cancel_flag = False
            self.prep_state, self.staged, self.prep_error = "preparing", None, ""
            self.prep_changed.emit()
            QThreadPool.globalInstance().start(PrepareWorker(self, found))
            return True

        def cancel_prepare(self) -> None:
            self.cancel_flag = True

        def _prepared(self, staged: Any, error: str) -> None:
            if staged is not None:
                self.prep_state, self.staged, self.prep_error = "ready", staged, ""
            else:
                self.prep_state, self.staged, self.prep_error = ("idle", None, "") if error == "cancelled" else (
                    "failed", None, error)
            self.prep_changed.emit()

        def restart_into_update(self) -> bool:
            """Update & Restart: re-check, start the helper (it only waits), then run the normal close flow.
            If the user cancels the unsaved-board prompt, the helper is stopped and nothing has changed."""
            staged = self.staged
            if self.prep_state != "ready" or staged is None:
                return False
            ok, why = self.assist()
            if not ok:
                self.warn("Update", why)
                return False
            try:
                install.plan_activation(self.root, staged.transaction_id, trust=self.trust, platform=self.platform)
                helper = self.launch(self.root, "activate", os.getpid(), transaction=staged.transaction_id,
                                     platform=self.platform)
            except Exception as exc:  # noqa: BLE001
                self.warn("Update", f"The update can't be installed right now:\n\n{exc}")
                return False
            if not self.request_close():
                helper.terminate()
                return False
            self.quit()
            return True

        def roll_back(self) -> bool:
            """Restore the previous version from the backup the last update kept (the user's decision)."""
            ok, why = self.assist()
            if not ok:
                self.warn("Roll back", why)
                return False
            try:
                plan = recovery.plan_manual_rollback(self.root)
            except install.InstallError as exc:
                self.warn("Roll back", f"There is no previous version to go back to:\n\n{exc}")
                return False
            if not self.confirm("Roll back pixelRef",
                                f"Go back to pixelRef {plan.new_version} (you have {plan.old_version})?\n\n"
                                "Only the program changes. Your boards, settings, plugins and scripts are not "
                                "reverted, and the older version may not read files the newer one saved. "
                                "pixelRef will close and restart."):
                return False
            try:
                helper = self.launch(self.root, "rollback", os.getpid(), platform=self.platform)
            except Exception as exc:  # noqa: BLE001
                self.warn("Roll back", f"Can't start the update helper:\n\n{exc}")
                return False
            if not self.request_close():
                helper.terminate()
                return False
            self.quit()
            return True

        # ---- startup ----

        def acknowledge_startup(self) -> bool | None:
            """Tell the helper this version really started (commits the update). None: nothing to do."""
            token = os.environ.pop(flow.STARTUP_TOKEN_ENV, "")  # not inherited by anything we launch
            if not token or self.root is None:
                return None
            return recovery.acknowledge_health(self.root, token)

        def startup_notice(self) -> None:
            """One message about what happened to the last update, and about recovery that needs a person."""
            if self.root is None:
                return
            state = flow.STARTUP.get("state")
            if state in (recovery.MANUAL, recovery.RECOVER):
                self.warn("pixelRef update", "An update did not finish and pixelRef could not restore the previous "
                          "version by itself. Your files were not deleted. See docs/update-security.md "
                          "(Health checks, recovery and rollback) to recover by hand.")
                return
            outcome = flow.last_update_result(self.root)
            if outcome and outcome["transaction"] != app.settings.value("updates/last_reported", "", type=str):
                app.settings.setValue("updates/last_reported", outcome["transaction"])
                if outcome["state"] == "COMMITTED":
                    ui.toast(outcome["text"])
                else:
                    self.warn("pixelRef update", outcome["text"])

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
        button.destroyed.connect(lambda *_: _disconnect(service.changed, refresh))
        refresh()
        return button

    def _disconnect(signal: Any, slot: Callable) -> None:
        try:
            signal.disconnect(slot)
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
        dialog.resize(540, 460)
        dialog.setStyleSheet(app.stylesheet)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"<b>pixelRef {escape(str(release.version))}</b> is available "
                                f"(you have {escape(APP_VERSION)})."))
        notes = QTextBrowser(dialog)
        notes.setOpenLinks(False)
        notes.setPlainText(release.notes or "No release notes.")  # plain text: notes are untrusted
        layout.addWidget(notes, 1)
        status = QLabel(dialog)
        status.setWordWrap(True)
        bar = QProgressBar(dialog)
        bar.setTextVisible(True)
        layout.addWidget(status)
        layout.addWidget(bar)
        buttons = QDialogButtonBox(dialog)
        download = buttons.addButton("Download", QDialogButtonBox.AcceptRole)
        install_button = buttons.addButton("Update && Restart", QDialogButtonBox.AcceptRole)
        cancel = buttons.addButton("Cancel Download", QDialogButtonBox.ActionRole)
        page = buttons.addButton("Release Notes && Manual Download", QDialogButtonBox.HelpRole)
        later = buttons.addButton("Later", QDialogButtonBox.RejectRole)
        skip = buttons.addButton("Skip This Version", QDialogButtonBox.ActionRole)
        layout.addWidget(buttons)
        page.clicked.connect(lambda: open_url(release.html_url))
        skip.clicked.connect(lambda: (setattr(prefs, "dismissed_version", str(release.version)),
                                      service.changed.emit(), dialog.close()))
        later.clicked.connect(dialog.close)
        download.clicked.connect(service.prepare)
        cancel.clicked.connect(service.cancel_prepare)
        install_button.clicked.connect(lambda: dialog.close() if service.restart_into_update() else None)

        def refresh() -> None:
            ok, why = service.assist()
            offered = found.can_install and ok
            state = service.prep_state
            download.setVisible(offered and state in ("idle", "failed"))
            cancel.setVisible(state == "preparing")
            install_button.setVisible(state == "ready")
            bar.setVisible(state == "preparing")
            if state == "preparing":
                status.setText("Downloading and verifying…")
            elif state == "ready":
                status.setText(f"Verified. pixelRef will close (asking about unsaved boards) and restart as "
                               f"{release.version}. Your boards and settings are not touched.")
            elif state == "failed":
                status.setText(f"The update could not be prepared and nothing was changed:\n{service.prep_error}")
            elif service.mode is InstallMode.SOURCE:
                status.setText("This copy runs from source. Update it with git; pixelRef never changes it itself.")
            elif not offered:
                status.setText(f"Download the new ZIP and extract it to a fresh folder. {why}".strip())
            else:
                status.setText("")

        def on_progress(done: int, total: int) -> None:
            bar.setRange(0, max(total, 1))
            bar.setValue(done)

        service.prep_changed.connect(refresh)
        service.progress.connect(on_progress)
        dialog.finished.connect(lambda *_: (_disconnect(service.prep_changed, refresh),
                                            _disconnect(service.progress, on_progress)))
        refresh()
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
        install_note = QLabel(dialog)
        install_note.setWordWrap(True)
        ok, why = service.assist()
        install_note.setText("Installing from inside pixelRef: available." if ok else f"Installing from inside pixelRef: {why}")

        def refresh() -> None:
            stamp = prefs.last_successful_check
            last.setText(datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M") if stamp else "never")
            status.setText("Checking…" if service.state is UpdateState.CHECKING
                           else MESSAGES.get(service.state, "") if service.state is not UpdateState.IDLE else "")

        check_now = QPushButton("Check Now", dialog)
        check_now.clicked.connect(lambda: run_manual_check())
        service.changed.connect(refresh)
        dialog.finished.connect(lambda *_: _disconnect(service.changed, refresh))
        form.addRow(auto)
        form.addRow("Channel", channel)
        form.addRow(download)
        form.addRow("Last successful check", last)
        form.addRow(check_now, status)
        form.addRow(install_note)
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
    ui.command("roll_back", service.roll_back, undoable=False, echo=False)
    ui.action("updates.check", "updates.check", "Check for Updates…", keywords="update upgrade version new release download",
              category="Window", icon="info")
    ui.action("updates.settings", "updates.settings", "Update Settings…", keywords="update upgrade automatic channel beta stable",
              category="Preferences", icon="info")
    ui.action("updates.roll_back", "updates.roll_back", "Roll Back to Previous Version…",
              keywords="update downgrade previous version restore undo update rollback", category="Preferences", icon="info")
    ui.menu("canvas", "updates.check", group="app", order=-2)

    # ---- Periodic check: a small delay after start, then re-evaluated every half hour ----------------

    def periodic() -> None:
        if not os.environ.get("PIXELREF_DISABLE_UPDATE_CHECK") and prefs.check_due():
            service.check()

    QTimer.singleShot(STARTUP_DELAY_MS, periodic)
    timer = QTimer(service)
    timer.timeout.connect(periodic)
    timer.start(POLL_MS)
    # Once the event loop is running the window is up: this version really started. (None, and no effect,
    # unless the helper launched us with a startup token.)
    QTimer.singleShot(ACK_DELAY_MS, service.acknowledge_startup)
    QTimer.singleShot(ACK_DELAY_MS + 1500, service.startup_notice)
    ui.track(lambda: (timer.stop(), service.deleteLater()))
