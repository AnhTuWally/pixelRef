# PyInstaller spec for the update helper: a small, separate one-folder app.
#
#     python packaging/build_exe.py            (builds this first, then bundles it into the app)
#
# Output: dist/pixelref-updater/
#     pixelref-updater.exe    activates a staged update after pixelRef has exited (helper_main.py)
#     _internal/              its own Python runtime and the Qt-free update code
#
# It is copied by the app into <install>/.pixelref-update/helper/<id>/ and run from there, so it must not
# share a single file with the app it replaces: its own Python, its own DLLs. It needs no Qt, no pip and no
# plugins, so those are excluded; it imports only pixelref.core.updates (reached by import analysis).

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]  # noqa: F821  (SPECPATH is set by PyInstaller)

EXCLUDES = [
    "PySide6", "shiboken6", "pip", "tkinter", "turtle", "idlelib", "test", "unittest", "pydoc", "numpy", "PIL",
    "setuptools", "pkg_resources", "distutils",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "updater" / "helper_main.py")],
    pathex=[str(ROOT)],
    excludes=EXCLUDES,
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz, a.scripts, [], exclude_binaries=True, name="pixelref-updater",
    console=True,  # stdout carries the JSON result; the app starts it detached, without a window
)
coll = COLLECT(  # noqa: F821
    exe, a.binaries, a.datas, name="pixelref-updater",
)
