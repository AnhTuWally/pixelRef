"""Running package installs in the background (QProcess), for the Plugins window.

One runner per app, so a job carries on when the window is closed. Jobs come
from :class:`pixelref.core.packages.PackageManager` (``plan_install`` …); the
runner starts the job's pip command, streams its output and calls
``job.finish`` with the result, which switches plugins on or off.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from ..core.packages import InstallJob, pip_environment

if TYPE_CHECKING:
    from .app import App

logger = logging.getLogger(__name__)


class PackageJobRunner(QObject):
    started = Signal(str)  # description
    output = Signal(str)  # one line of pip's log
    finished = Signal(dict)  # the job's result (see PackageManager._finish_job)

    def __init__(self, app: "App") -> None:
        super().__init__()
        self.app = app
        self.job: InstallJob | None = None
        self.process: QProcess | None = None
        self.log: list[str] = []
        self.last_result: dict[str, Any] | None = None

    @property
    def busy(self) -> bool:
        return self.job is not None

    def start(self, plan: Callable[[], InstallJob]) -> bool:
        """Plan a job and run it. Planning errors become a failed result. False when busy."""
        if self.busy:
            return False
        try:
            job = plan()
        except Exception as exc:  # noqa: BLE001  (InstallError, a bad requirement…)
            self._report({"ok": False, "kind": "", "description": "", "restart": False,
                          "message": str(exc), "output": ""})
            return True
        self.job = job
        self.log = []
        self.started.emit(job.description)
        self.app.set_status(f"Installing Python packages: {job.description}…")
        if not job.command:
            self._done(0)
            return True
        process = QProcess(self)
        env = QProcessEnvironment()
        for key, value in pip_environment().items():
            env.insert(key, value)
        process.setProcessEnvironment(env)
        process.setProcessChannelMode(QProcess.MergedChannels)
        process.readyReadStandardOutput.connect(self._read)
        process.finished.connect(lambda code, _status: self._done(code))
        process.errorOccurred.connect(self._on_error)
        self.process = process
        self._line(" ".join(job.command))
        process.start(job.command[0], job.command[1:])
        return True

    def wait(self, msecs: int = 300_000) -> None:
        """Block until the running job finishes (tests, shutdown)."""
        if self.process is not None:
            self.process.waitForFinished(msecs)
            self._read()

    def cancel(self) -> None:
        if self.process is not None:
            self.process.kill()

    def _read(self) -> None:
        if self.process is None:
            return
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in data.splitlines():
            self._line(line)

    def _line(self, line: str) -> None:
        self.log.append(line)
        self.output.emit(line)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.FailedToStart:
            self._line(f"Could not start pip: {self.process.errorString() if self.process else error}")
            self._done(-1)

    def _done(self, code: int) -> None:
        job, self.job = self.job, None
        if job is None:
            return  # already finished (FailedToStart is followed by no 'finished')
        self._read_rest()
        self.process = None
        try:
            result = job.finish(int(code), "\n".join(self.log))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Finishing a package job failed")
            result = {"ok": False, "kind": job.kind, "description": job.description, "restart": False,
                      "message": f"{type(exc).__name__}: {exc}", "output": "\n".join(self.log)}
        self._report(result)

    def _read_rest(self) -> None:
        if self.process is not None:
            self._read()

    def _report(self, result: dict[str, Any]) -> None:
        self.last_result = result
        self.app.set_status(result["message"])
        self.finished.emit(result)


def check_packages_on_start(app: "App") -> None:
    """Once the window is up: carry the user's packages over to a new Python
    version (an app update), and say when plugins are waiting for packages."""
    from ..core.packages import is_frozen, pip_available

    session = app.session
    packages = session.packages
    if packages.swap_error:
        app.set_status(f"Couldn't finish changing Python packages ({packages.swap_error}); "
                       "close other pixelRef windows and restart.")
        return
    requirements = packages.adopt_other_versions()
    if requirements and (is_frozen() or pip_available()):
        package_runner(app).start(packages.plan_reinstall)
        app.set_status(f"Reinstalling {len(requirements)} Python package(s) for this version of pixelRef…")
        return
    needed = session.plugins.packages_needed()
    if needed:
        plugins = {p for ids in needed.values() for p in ids}
        app.set_status(f"{len(plugins)} plugin(s) need Python packages: Window → Plugins… → Install what's needed")


def package_runner(app: "App") -> PackageJobRunner:
    runner = getattr(app, "_package_runner", None)
    if runner is None:
        runner = PackageJobRunner(app)
        app._package_runner = runner  # type: ignore[attr-defined]
    return runner
