"""Batch mode: run pixelRef Python scripts without a window.

    python -m pixelref --batch script.py [scene.pxref] [--save out.pxref] [--trust-plugin-path DIR] [--no-user]

The plugins the user enabled and approved in the app load here too (from the
user plugin folder and the folders added in Window → Plugins… → Sources).
``--plugin-path DIR`` adds a folder whose plugins the user approved in the app;
``--trust-plugin-path DIR`` trusts every plugin in DIR for this run (only use it
for folders whose code you trust). ``--no-user`` leaves out the user's plugins.

The script runs with ``pxcmd`` (and ``pxnode``) already imported, against a
headless session with every plugin's headless half active. Commands that
need a window (dialogs, views) are not available; document, attribute,
transform, layout and file commands are.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

from .core.scene_file import SCENE_EXTS


def run_batch(argv: list[str]) -> int:
    from . import pxcmd, pxnode
    from .core.session import Session
    from .ui import _user_plugin_dir
    from .ui.settings import plugin_choices

    script: Path | None = None
    scene: Path | None = None
    save_to: Path | None = None
    plugin_paths: list[Path] = []
    trusted_paths: list[Path] = []
    args = iter(argv)
    for arg in args:
        if arg in ("--save", "--plugin-path", "--trust-plugin-path"):
            value = next(args, None)
            if value is None:
                print(f"pixelref --batch: {arg} needs a path", file=sys.stderr)
                return 2
            if arg == "--save":
                save_to = Path(value)
            elif arg == "--plugin-path":
                plugin_paths.append(Path(value))
            else:
                trusted_paths.append(Path(value))
        elif arg.endswith(".py") and script is None:
            script = Path(arg)
        elif Path(arg).suffix.lower() in SCENE_EXTS and scene is None:
            scene = Path(arg)
    from PySide6.QtCore import QCoreApplication

    from .core.settings import MemorySettings

    QCoreApplication.setOrganizationName("pixelRef")  # where the user's plugins and settings are
    QCoreApplication.setApplicationName("pixelRef")
    user = "--no-user" not in argv
    session = Session(settings=plugin_choices() if user else MemorySettings(), plugin_paths=plugin_paths,
                      trusted_plugin_paths=trusted_paths, user_plugin_dir=_user_plugin_dir() if user else None)
    session.start()
    try:
        if scene is not None:
            pxcmd.open_scene(str(scene))
        if script is not None:
            runpy.run_path(str(script), init_globals={"pxcmd": pxcmd, "pxnode": pxnode, "session": session},
                           run_name="__main__")
        if save_to is not None:
            pxcmd.save_scene(str(save_to))
    finally:
        session.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_batch(sys.argv[1:]))
