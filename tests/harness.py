"""Shared test setup: a headless pixelRef App and small helpers.

Importing this module configures Qt for tests (offscreen platform, test-mode
standard paths, INI settings in a temporary folder) before anything else
touches Qt.
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, QStandardPaths, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    _TMP = tempfile.TemporaryDirectory(prefix="pixelref_tests_", ignore_cleanup_errors=True)
except TypeError:
    _TMP = tempfile.TemporaryDirectory(prefix="pixelref_tests_")
TMP = Path(_TMP.name)
# Tests never touch the user's real Python package folder (core.packages).
os.environ["PIXELREF_PACKAGES_DIR"] = str(TMP / "python")
QStandardPaths.setTestModeEnabled(True)  # recovery files etc. go to a test location
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(TMP / "settings"))
APP = QApplication.instance() or QApplication([])
APP.setApplicationName("pixelRef")
APP.setOrganizationName("pixelRef")
# The offscreen platform's clipboard crashes Qt at interpreter exit if it still holds data
# (a PySide/Qt issue, not pixelRef's: the windows platform is fine), so empty it first.
atexit.register(lambda: QApplication.clipboard().clear())


def make_png(name: str, w: int, h: int, color: str = "#c04040") -> Path:
    path = TMP / name
    image = QImage(w, h, QImage.Format_ARGB32)
    image.fill(QColor(color))
    image.save(str(path))
    return path


def make_wheel(folder: Path, name: str, version: str, code: str = "VALUE = 1\n") -> Path:
    """A minimal pure-Python wheel (installs offline with pip)."""
    import base64
    import hashlib
    import zipfile

    module = name.replace("-", "_")
    info = f"{module}-{version}.dist-info"
    files = {
        f"{module}/__init__.py": code,
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: pixelref-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = []
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(text.encode())}")
    files[f"{info}/RECORD"] = "\n".join(record + [f"{info}/RECORD,,"]) + "\n"
    folder.mkdir(parents=True, exist_ok=True)
    wheel = folder / f"{module}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return wheel


def make_gif(name: str, w: int, h: int, colors: list[tuple[int, int, int]], delay_cs: int = 10) -> Path:
    """An animated GIF with one solid-colour frame per entry of ``colors`` (at most 4).

    Pixels are written as literal LZW codes with a clear code every two codes, so
    the code width stays at 3 bits (a valid, if uncompressed, GIF).
    """
    import struct

    palette = list(dict.fromkeys(colors))[:4]
    palette += [(0, 0, 0)] * (4 - len(palette))
    clear, end = 4, 5

    def lzw(index: int) -> bytes:
        codes = []
        for i in range(w * h):
            if i % 2 == 0:
                codes.append(clear)
            codes.append(index)
        codes.append(end)
        bits, value, out = 0, 0, bytearray()
        for code in codes:
            value |= code << bits
            bits += 3
            while bits >= 8:
                out.append(value & 0xFF)
                value >>= 8
                bits -= 8
        if bits:
            out.append(value & 0xFF)
        blocks = bytearray([2])  # minimum code size
        for start in range(0, len(out), 255):
            chunk = out[start:start + 255]
            blocks += bytes([len(chunk)]) + chunk
        return bytes(blocks + b"\x00")

    data = bytearray(b"GIF89a" + struct.pack("<HHBBB", w, h, 0xF1, 0, 0))
    for r, g, b in palette:
        data += bytes([r, g, b])
    data += b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00"  # loop forever
    for color in colors:
        data += b"\x21\xF9\x04\x00" + struct.pack("<H", delay_cs) + b"\x00\x00"
        data += b"\x2C" + struct.pack("<HHHHB", 0, 0, w, h, 0)
        data += lzw(palette.index(color))
    data += b"\x3B"
    path = TMP / name
    path.write_bytes(bytes(data))
    return path


def tmp_dir() -> Path:
    return Path(tempfile.mkdtemp(dir=TMP))


def approx(a: float, b: float, tol: float = 0.05) -> bool:
    return abs(a - b) <= tol


def corners_close(a: list, b: list, tol: float = 0.05) -> bool:
    return len(a) == len(b) and all(approx(p[0], q[0], tol) and approx(p[1], q[1], tol) for p, q in zip(a, b))


class Harness:
    """One running App (its own session) with the default Board workspace showing."""

    def __init__(self, plugin_paths: tuple = (), only: Any = None, fresh_panels: bool = True) -> None:
        from pixelref.ui import create_app

        if fresh_panels:  # every test starts with the default panel layout
            QSettings().remove("panels")
        work = tmp_dir()
        os.chdir(work)
        self.work = work
        self.app = create_app([], persist_layout=False, plugin_paths=plugin_paths, load_user_files=False)
        self.session = self.app.session
        self.window = self.app.wm.primary_window()
        self.window.resize(1400, 860)
        self.window.show()
        QTest.qWaitForWindowExposed(self.window)
        APP.processEvents()
        self.reset_view()

    # ---- Views --------------------------------------------------------------------------

    @property
    def view(self) -> Any:
        return self.app.views["Main"]

    def reset_view(self) -> None:
        self.app.run_command("wm.focus", tile="Main")
        self.view.set_camera([0.0, 0.0], 1.0, 0.0)
        self.view.centerOn(0.0, 0.0)

    def vp(self, x: float, y: float) -> QPoint:
        return self.view.to_viewport(QPointF(x, y)).toPoint()

    # ---- Scene ----------------------------------------------------------------------------

    @property
    def scene(self) -> Any:
        return self.session.scene

    def run(self, command: str, *args: Any, **kwargs: Any) -> Any:
        return self.session.run(command, *args, **kwargs)

    def trigger(self, action: str) -> Any:
        return self.app.trigger(action)

    def service(self, name: str) -> Any:
        return self.session.services.get(name)

    def attrs(self, name: str) -> dict[str, Any]:
        return self.scene.nodes[name].attrs

    def attr(self, name: str, key: str) -> Any:
        return self.scene.nodes[name].attrs.get(key)

    def has(self, name: str) -> bool:
        return self.scene.has(name)

    def parent_of(self, name: str) -> str | None:
        return self.scene.nodes[name].parent

    def type_of(self, name: str) -> str:
        return self.scene.nodes[name].node_type

    def nodes_of(self, node_type: str) -> list[str]:
        return [n.name for n in self.scene.iter_nodes() if n.node_type == node_type]

    def node_types(self) -> list[str]:
        return [n.node_type for n in self.scene.iter_nodes()]

    def create(self, node_type: str, name: str, parent: str | None = None, **attrs: Any) -> str:
        created = self.run("create_node", node_type, name, parent=parent, attrs=attrs)
        self.refresh()
        return created

    def add_image(self, name: str, x: float, y: float, w: int = 200, h: int = 100, **attrs: Any) -> str:
        path = make_png(f"{name}.png", w, h)
        parent = attrs.pop("parent", None)
        return self.create("image", name, parent=parent, path=path.as_posix(), translate=[x, y], **attrs)

    def refresh(self) -> None:
        self.app.canvas.refresh(force=True)
        APP.processEvents()

    def item(self, name: str) -> Any:
        return self.app.canvas.item(name)

    def corners(self, name: str) -> list[tuple[float, float]]:
        return [(round(p.x(), 3), round(p.y(), 3)) for p in self.item(name).scene_polygon()]

    # ---- Selection & undo -----------------------------------------------------------------

    def sel(self) -> list[str]:
        return list(self.session.selection.names)

    def select(self, names: list[str]) -> None:
        self.run("select", list(names)) if names else self.run("select", clear=True)
        APP.processEvents()

    def undo(self) -> None:
        self.app.run_command("undo")

    def redo(self) -> None:
        self.app.run_command("redo")

    def undo_label(self) -> str:
        return self.session.undo.stack.undo_label()

    # ---- Tools ------------------------------------------------------------------------------

    def tool(self) -> str | None:
        return self.app.tools.active_id

    def set_tool(self, tool_id: str) -> None:
        self.app.tools.activate(tool_id)
        APP.processEvents()

    def gizmo(self) -> dict[str, Any]:
        return self.service("gizmo").gizmo(self.view) or {}

    # ---- Panels -------------------------------------------------------------------------------

    @property
    def inspector(self) -> Any:
        return self.service("inspector").panel()

    @property
    def outliner(self) -> Any:
        return self.service("outliner").panel()

    @property
    def palette(self) -> Any:
        return self.service("tool_palette").palette()

    def section(self, key: str) -> Any:
        return self.inspector.section(key)

    def tool_panel(self, tool_id: str) -> Any:
        return self.palette.option_panels.get(tool_id)

    # ---- Input ----------------------------------------------------------------------------------

    def mouse(self, kind: str, pos: QPoint, button: Any = Qt.LeftButton, buttons: Any = None,
              modifiers: Any = Qt.NoModifier) -> None:
        viewport = self.view.viewport()
        event_type = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove,
                      "release": QEvent.MouseButtonRelease, "double": QEvent.MouseButtonDblClick}[kind]
        if buttons is None:
            buttons = Qt.NoButton if kind == "release" else button
        if kind == "move":
            button = Qt.NoButton
        event = QMouseEvent(event_type, QPointF(pos), QPointF(viewport.mapToGlobal(pos)), button, buttons, modifiers)
        QApplication.sendEvent(viewport, event)

    def drag(self, start: QPoint, end: QPoint, modifiers: Any = Qt.NoModifier, steps: int = 6,
             release: bool = True) -> None:
        self.mouse("press", start, modifiers=modifiers)
        for i in range(1, steps + 1):
            point = start + (end - start) * (i / steps)
            self.mouse("move", point, buttons=Qt.LeftButton, modifiers=modifiers)
        if release:
            self.mouse("release", end, modifiers=modifiers)

    def click(self, pos: QPoint, modifiers: Any = Qt.NoModifier) -> None:
        self.mouse("press", pos, modifiers=modifiers)
        self.mouse("release", pos, modifiers=modifiers)

    def scribble(self, x0: float, y0: float, length: float = 120, steps: int = 12, amplitude: float = 0.0) -> None:
        import math

        self.mouse("press", self.vp(x0, y0))
        for i in range(1, steps + 1):
            x = x0 + length * i / steps
            y = y0 + amplitude * math.sin(i / steps * math.pi * 2)
            self.mouse("move", self.vp(x, y), buttons=Qt.LeftButton)
        self.mouse("release", self.vp(x0 + length, y0))

    def key(self, key: Any, modifiers: Any = Qt.NoModifier, widget: Any = None) -> None:
        target = widget or self.view
        target.setFocus()
        QTest.keyClick(target, key, modifiers)
        APP.processEvents()

    def drop(self, scene_pos: QPointF, mime: Any) -> list[str]:
        return self.service("ingest").ingest("Drop", mime, scene_pos, self.view)

    def end_coalesced(self) -> None:
        self.app.canvas.end_coalesced()

    def grab(self, clean: bool = False) -> QImage:
        # render_frame, not viewport().grab(): an OpenGL viewport's grab isn't reliable.
        return self.view.render_frame(overlays=not clean)

    # ---- Files ------------------------------------------------------------------------------

    def save_to(self, path: Path) -> bool:
        return bool(self.run("file.save_to", str(path)))

    def open(self, path: Path) -> bool:
        ok = bool(self.run("file.open_path", str(path), confirm=False))
        self.refresh()
        self.reset_view()
        return ok

    def saved_json(self, path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    # ---- Teardown ---------------------------------------------------------------------------

    def close(self) -> None:
        if self.app.closed:
            return
        self.session.mark_saved()
        self.app.close(force=True)
        APP.processEvents()


def make(**options: Any) -> Harness:
    return Harness(**options)
