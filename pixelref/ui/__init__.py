"""The pixelRef desktop application (PySide6).

``python -m pixelref`` (or ``python -m pixelref.ui``) starts it; pass a
``.pxref`` file to open it. See ``pixelref.ui.app.App`` for the kernel UI and
``pixelref/builtins`` for the plugins that provide every feature.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .. import __version__ as APP_VERSION
from ..core.scene_file import SCENE_EXTS


def create_app(argv: list[str] | None = None, persist_layout: bool = True, plugin_paths: tuple = (),
               load_user_files: bool = True):  # noqa: ANN201
    """Create the session and the App (without entering the Qt event loop)."""
    from PySide6.QtWidgets import QApplication

    from ..core.session import Session
    from .app import App
    from .icons import app_icon
    from .settings import QtSettings

    qapp = QApplication.instance() or QApplication(list(argv or sys.argv))
    qapp.setApplicationName("pixelRef")
    qapp.setApplicationVersion(APP_VERSION)
    qapp.setOrganizationName("pixelRef")
    qapp.setWindowIcon(app_icon())
    session = Session(settings=QtSettings(), plugin_paths=tuple(Path(p) for p in plugin_paths),
                      user_plugin_dir=_user_plugin_dir())
    session.start()
    return App(session, persist_layout=persist_layout, load_user_files=load_user_files)


def _user_plugin_dir() -> Path | None:
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    return Path(base) / "plugins" if base else None


def run(argv: list[str] | None = None) -> int:
    from PySide6.QtWidgets import QApplication

    argv = list(sys.argv if argv is None else argv)
    if sys.platform == "win32":
        # Give the process its own taskbar identity so Windows shows the
        # pixelRef logo instead of python.exe's icon.
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("pixelRef.pixelRef")
        except (AttributeError, OSError):
            pass
    plugin_paths = []
    scene_args: list[Path] = []
    args = iter(argv[1:])
    for arg in args:
        if arg == "--plugin-path":
            plugin_paths.append(next(args, ""))
        elif Path(arg).suffix.lower() in SCENE_EXTS:
            scene_args.append(Path(arg))
    app = create_app(argv, plugin_paths=tuple(p for p in plugin_paths if p))
    restored = False
    recovery = app.session.services.get("recovery")
    if recovery is not None:
        # Session Resume (if turned on) reopens the last board; offer() is the
        # separate crash-recovery safety net, which still applies either way.
        restored = recovery.resume() or recovery.offer()
    if not restored and scene_args and scene_args[0].exists():
        app.run_command("open_scene", str(scene_args[0]))
    return QApplication.instance().exec()


def main() -> None:
    raise SystemExit(run())
