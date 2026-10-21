# PyInstaller spec for the standalone pixelRef build (one folder, not one file).
#
#     python packaging/build_exe.py            (wraps: pyinstaller packaging/pixelref.spec)
#
# Output: dist/pixelRef/
#     pixelRef.exe        the app (windowed)
#     pixelref-cli.exe    the same app with a console: --batch scripts, and
#                         --run-pip for the package manager (pip needs stdout)
#     _internal/          Python, Qt and pixelRef, shared by both
#
# Choices (see docs/packaging.md):
# * one folder: a one-file exe unpacks itself to a temp folder on every launch
#   (slow starts, antivirus false positives);
# * the whole standard library is bundled: packages installed later from the
#   Plugins window (numpy…) import stdlib modules pixelRef itself never uses;
# * pip is bundled (with its certificates) for the package manager, as plain
#   files rather than inside the archive (its vendored distlib needs that);
# * Qt modules pixelRef doesn't use are left out (most of PySide6's size).

import importlib.util
import pkgutil
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821  (SPECPATH is set by PyInstaller)

# Never bundled: GUI toolkits and dev tools a reference board doesn't need.
STDLIB_SKIP = {
    "tkinter", "turtle", "turtledemo", "idlelib", "test", "lib2to3", "ensurepip", "venv", "pydoc_data",
    "antigravity", "this", "__phello__", "_tkinter", "sqlite3.test", "msilib",
}
QT_EXCLUDES = [
    f"PySide6.{name}" for name in (
        # QtMultimedia / QtMultimediaWidgets are kept: the movie plugin plays video with them.
        "Qt3DAnimation", "Qt3DCore", "Qt3DExtras", "Qt3DInput", "Qt3DLogic", "Qt3DRender", "QtBluetooth",
        "QtCharts", "QtDataVisualization", "QtDesigner", "QtGraphs", "QtHelp", "QtHttpServer", "QtLocation",
        "QtNfc", "QtPdf", "QtPdfWidgets", "QtPositioning", "QtQml",
        "QtQuick", "QtQuick3D", "QtQuickControls2", "QtQuickWidgets", "QtRemoteObjects", "QtScxml",
        "QtSensors", "QtSerialBus", "QtSerialPort", "QtSpatialAudio", "QtSql", "QtStateMachine", "QtSvgWidgets",
        "QtTest", "QtTextToSpeech", "QtUiTools", "QtWebChannel", "QtWebEngineCore", "QtWebEngineQuick",
        "QtWebEngineWidgets", "QtWebSockets", "QtWebView", "QtXml", "QtAxContainer",
    )
]


def stdlib_modules() -> list[str]:
    """Every standard library module that exists on this machine (minus STDLIB_SKIP)."""
    names = []
    for top in sorted(sys.stdlib_module_names):
        if top in STDLIB_SKIP:
            continue
        try:
            spec = importlib.util.find_spec(top)
        except (ImportError, ValueError):
            continue
        if spec is None:
            continue  # e.g. posix-only modules on Windows
        names.append(top)
        if spec.submodule_search_locations:
            for info in pkgutil.walk_packages(spec.submodule_search_locations, prefix=top + "."):
                parts = info.name.split(".")
                if any(p in ("test", "tests", "idle_test") or info.name in STDLIB_SKIP for p in parts):
                    continue
                names.append(info.name)
    return names


hiddenimports = (
    collect_submodules("pixelref")  # plugins are imported by name at runtime
    + collect_submodules("pip")
    + stdlib_modules()
)
datas = (
    collect_data_files("pixelref", includes=["**/*.toml", "**/*.png", "**/*.ico", "**/*.svg"])
    + collect_data_files("pip")  # certificates (cacert.pem) and vendored data
    # the Getting Started board (builtins/help looks for it under _internal/examples/scenes)
    + [(str(ROOT / "examples" / "scenes" / "onboard.pxref"), "examples/scenes")]
    # the guides the Help browser and `pixelref-cli docs` / `mcp` search (core.docs looks under _internal/docs)
    + [(str(path), "docs") for path in sorted((ROOT / "docs").glob("*.md"))]
    + [(str(path), "docs/images") for path in sorted((ROOT / "docs" / "images").glob("*.webp"))]
)

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=QT_EXCLUDES + sorted(STDLIB_SKIP),
    # pip as plain files on disk: its vendored distlib can't find its resources
    # inside PyInstaller's archive ("Unable to locate finder for 'pip._vendor.distlib'").
    module_collection_mode={"pip": "py"},
    noarchive=False,
)
# Qt libraries that come along with plugins pixelRef never loads: the software
# OpenGL fallback, Qt Quick/QML (only the virtual keyboard plugin uses them),
# Qt PDF (the PDF image-format plugin) and Qt's own translations (the UI is
# English). The smoke test in build_exe.py runs the real UI to catch a mistake here.
DROP_BINARIES = ("opengl32sw", "qt6quick", "qt6qml", "qt6pdf", "qt6virtualkeyboard", "qtvirtualkeyboardplugin",
                 "qpdf")


def wanted_binary(entry) -> bool:
    return not Path(entry[0]).name.lower().startswith(DROP_BINARIES)


a.binaries = [entry for entry in a.binaries if wanted_binary(entry)]
a.datas = [entry for entry in a.datas if not entry[0].replace("\\", "/").startswith("PySide6/translations/")]
pyz = PYZ(a.pure)  # noqa: F821

icon = ROOT / "pixelref" / "resources" / "logo.ico"
icon_args = {"icon": str(icon)} if icon.is_file() else {}

app_exe = EXE(  # noqa: F821
    pyz, a.scripts, [], exclude_binaries=True, name="pixelRef", console=False, **icon_args,
)
cli_exe = EXE(  # noqa: F821
    pyz, a.scripts, [], exclude_binaries=True, name="pixelref-cli", console=True, **icon_args,
)
coll = COLLECT(  # noqa: F821
    app_exe, cli_exe, a.binaries, a.datas, name="pixelRef",
)
