"""Regenerate the README screenshots in docs/images/ from the sample board.

    python tools/gen_screenshots.py              # every shot
    python tools/gen_screenshots.py effects      # just some (names below)

Starts pixelRef offscreen (tests/harness.py), opens
examples/scenes/animals.pxref and renders each shot as a WebP. On Windows the
system fonts are used, so text renders as it does in the app.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))

from harness import APP, Harness  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QImage, QImageWriter, QPainter  # noqa: E402

SCENE = ROOT / "examples" / "scenes" / "animals.pxref"
OUT = ROOT / "docs" / "images"
SHOTS = ("overview", "annotations", "quick_edit", "effects", "links", "redact",
         "command_search", "script_editor", "review")


def settle(seconds: float = 1.0) -> None:
    """Let the canvas, background effect jobs and animations catch up."""
    end = time.time() + seconds
    while time.time() < end:
        APP.processEvents()
        time.sleep(0.01)


class Shots:
    def __init__(self) -> None:
        self.h = Harness()
        self.h.window.resize(1500, 900)
        self.h.run("file.open_path", str(SCENE), confirm=False)
        self.h.refresh()
        try:  # the sample's Board view has a scratch title
            self.run("wm.rename_view", "Main", "Main2")
        except Exception:  # noqa: BLE001
            pass
        settle(2.0)

    def run(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return self.h.app.run_command(*args, **kwargs)

    def view(self):  # noqa: ANN201
        return self.h.app.active_view()

    def frame(self, names: list[str], zoom_by: float | None = None) -> None:
        self.run("navigation.frame", names)
        settle(0.8)
        if zoom_by:
            self.run("navigation.zoom_by", zoom_by)
            settle(0.5)

    def save(self, name: str, image: QImage) -> None:
        writer = QImageWriter(str(OUT / f"{name}.webp"))
        writer.setFormat(b"webp")
        writer.setQuality(88)
        if not writer.write(image):
            raise RuntimeError(f"{name}: {writer.errorString()}")
        print("saved", name)

    def window(self, name: str, popups: tuple = ()) -> None:
        """The whole window, with top-level pop-ups (menus, floating views) painted in."""
        self.h.app.set_status("")
        settle(1.0)
        image = self.h.window.grab().toImage()
        painter = QPainter(image)
        for widget in popups:
            painter.drawPixmap(self.h.window.mapFromGlobal(widget.mapToGlobal(QPoint(0, 0))), widget.grab())
        painter.end()
        self.save(name, image)

    def canvas(self, name: str) -> None:
        settle(1.0)
        self.save(name, self.view().render_frame(overlays=True))

    # ---- The shots ------------------------------------------------------------------------

    def overview(self) -> None:
        self.run("select", clear=True)
        self.run("navigation.frame_all")
        self.window("overview")

    def annotations(self) -> None:
        self.run("select", clear=True)
        self.frame(["images", "Step", "Ct2Wgc9X63dUweHXKn4Rxa", "Step_1"])
        self.canvas("annotations")

    def quick_edit(self) -> None:
        self.run("select", ["Note"])
        self.frame(["Group_1"], 0.8)
        self.window("quick_edit")

    def effects(self) -> None:
        h = self.h
        self.run("select", ["zPtcQJsjyUG4nLJZ7YN2x6"])
        self.frame(["zPtcQJsjyUG4nLJZ7YN2x6", "collar-perro-nara-azul-cielo-caniche-frontal"], 0.9)
        self.run("panels.set_mode", "tool_palette", "hidden")
        self.run("panels.set_mode", "outliner", "hidden")
        settle(0.8)
        for key in ("transform", "appearance", "image"):
            section = h.inspector.section(key)
            if section is not None:
                section.set_expanded(False)
        settle(0.3)
        self.window("effects")
        for key in ("transform", "appearance", "image"):
            section = h.inspector.section(key)
            if section is not None:
                section.set_expanded(True)
        self.run("panels.set_mode", "tool_palette", "docked")
        self.run("panels.set_mode", "outliner", "docked")

    def links(self) -> None:
        self.run("select", ["Link"])
        self.frame(["Link"], 0.7)
        self.canvas("links")

    def redact(self) -> None:
        self.run("select", clear=True)
        self.frame(["Pixelate", "Blur"], 0.9)
        self.canvas("redact")

    def command_search(self) -> None:
        self.run("select", clear=True)
        self.run("navigation.frame_all")
        settle(0.5)
        view = self.view()
        viewport = view.viewport()
        menu = self.h.app.menus.build("canvas", view)
        menu.popup(viewport.mapToGlobal(viewport.rect().center() - QPoint(260, 200)))
        settle(0.5)
        menu.field.setText("grid")
        settle(0.8)
        self.window("command_search", (menu,))
        menu.close()
        settle(0.3)

    def script_editor(self) -> None:
        self.run("select", clear=True)
        self.run("navigation.frame_all")
        self.run("console.toggle")
        settle(0.8)
        console = self.h.service("console")
        console.set_echo(True)
        self.run("select", ["cat-cat-dance"])
        self.run("transform.flip", "h")
        console.run('len(pxcmd.ls(type="image"))')
        console.run('pxcmd.effects.list("zPtcQJsjyUG4nLJZ7YN2x6")[0]["type"]')
        console.run('for n in pxnode.ls(type="annotation_step"):\n    print(n.name, n.number)')
        floating = console.views[0].window()
        floating.resize(820, 430)
        floating.move(self.h.window.mapToGlobal(QPoint(150, 420)))
        settle(0.6)
        self.window("script_editor", (floating,))
        self.run("console.toggle")
        self.run("undo")
        settle(0.5)

    def review(self) -> None:
        self.run("select", clear=True)
        self.run("wm.workspace", 2)
        settle(1.0)
        for view in list(self.h.app.views):
            self.run("navigation.frame_all", view=view)
        settle(1.5)
        self.window("review")
        self.run("wm.workspace", 1)

    def close(self) -> None:
        self.h.session.mark_saved()
        self.h.close()


def main(names: list[str]) -> int:
    unknown = [n for n in names if n not in SHOTS]
    if unknown:
        print("unknown shots:", ", ".join(unknown), "| choose from:", ", ".join(SHOTS))
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    shots = Shots()
    try:
        for name in names or SHOTS:
            getattr(shots, name)()
    finally:
        shots.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
