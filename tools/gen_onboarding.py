"""Build the onboarding board, examples/scenes/onboard.pxref, with real screenshots of the app.

    python tools/gen_onboarding.py            # screenshots, then the board
    python tools/gen_onboarding.py shots      # only the screenshots (into a temp folder)
    python tools/gen_onboarding.py board      # only the board, from the screenshots already taken

Starts pixelRef offscreen (tests/harness.py), drives it with the same commands the
UI runs on examples/scenes/animals.pxref, grabs the window at each step, then lays
the shots out with notes and callouts as a new board that reuses the sample's animal
images. The board opens by itself the first time pixelRef starts and from
*Help → Getting Started*.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_screenshots as gs  # noqa: E402  (sets the font dir and import paths first)
from gen_screenshots import Shots, settle  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QImage, QImageWriter, QPainter  # noqa: E402

from onboarding_board import (  # noqa: E402
    ADVANCED,
    AMBER,
    BASIC,
    BLUE,
    GREEN,
    NEWEST,
    ORANGE,
    RED,
    SAFETY,
    SHOT_DIR,
    Board,
)

SAMPLE = gs.SCENE
TARGET = ROOT / "examples" / "scenes" / "onboard.pxref"
SHOT_NAMES = ("start", "navigate", "command_search", "review", "script_editor", "shortcuts", "paste_attrs", "colour_picker",
              "plugins_safety")  # only interface that can't be recreated on the canvas
# A made-up plugin "downloaded" for the Plugins window screenshot (build/ is not in git).
SAFETY_DOWNLOADS = ROOT / "build" / "onboarding" / "Downloads"
SAFETY_PLUGIN = """id = "palette_sync"
name = "Palette Sync"
version = "0.9.2"
api = 1
description = "Keeps your board's colours in step with an online palette."
license = "MIT"
tags = ["colour", "palette"]

[author]
name = "Someone Online"

[permissions]
network = true
"""


class OnboardingShots(Shots):
    """The sample board's shots, written as JPEGs for embedding."""

    def __init__(self) -> None:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        super().__init__()

    def save(self, name: str, image: QImage) -> None:
        writer = QImageWriter(str(SHOT_DIR / f"{name}.jpg"))
        writer.setFormat(b"jpg")
        writer.setQuality(90)
        if not writer.write(image.convertToFormat(QImage.Format_RGB32)):
            raise RuntimeError(f"{name}: {writer.errorString()}")
        print("shot", name)

    def start(self) -> None:
        """The empty board a new user sees: the start panel."""
        self.run("file.clear_recent")  # a new user has no recent boards yet
        self.run("file.new")
        settle(1.0)
        self.window("start")
        self.run("file.open_path", str(SAMPLE), confirm=False)
        self.h.refresh()
        settle(2.0)

    def title_view(self) -> None:
        """The sample's view has a scratch title; call it Board for the screenshots."""
        try:
            self.run("wm.rename_view", "Board", "Main2")
        except Exception:  # noqa: BLE001
            pass

    def navigate(self) -> None:
        self.title_view()
        self.run("select", clear=True)
        self.run("navigation.frame_all")
        self.window("navigate")

    def paste_attrs(self) -> None:
        """The Paste Attributes window: a styled rectangle copied onto two plainer items."""
        from PySide6.QtGui import QColor  # noqa: F401
        from pixelref.builtins.attr_transfer.dialog import PasteDialog
        from pixelref.core import transfer
        from pixelref.ui.app import UIContext

        self.run("select", clear=True)
        source = self.run("rect.create", 5000, 5000, 300, 200, "Styled", stroke_color=[255, 160, 60], thickness=9,
                          corner_radius=40, fill_color=[40, 90, 160], fill_opacity=0.6)
        a = self.run("rect.create", 5400, 5000, 300, 200, "Plain A", stroke_color=[200, 200, 200], thickness=2)
        b = self.run("rect.create_ellipse", 5800, 5000, 300, 200, "Plain B", stroke_color=[90, 200, 255], thickness=4)
        snap = transfer.snapshot(self.h.scene, source)
        ui = UIContext(self.h.app, self.h.session.plugins.get("attr_transfer"))
        dialog = PasteDialog(ui, snap, [a, b], self.h.window)
        dialog.resize(760, 560)
        dialog.show()
        dialog.move(self.h.window.mapToGlobal(QPoint(370, 170)))
        settle(1.0)
        self.window("paste_attrs", (dialog,))
        dialog.reject()
        settle(0.3)

    def colour_picker(self) -> None:
        """The colour popover opened from a swatch."""
        from PySide6.QtGui import QColor
        from pixelref.ui import color_dialog

        self.run("select", clear=True)
        rect = self.run("rect.create", 5000, 5000, 300, 200, "Swatch demo", stroke_color=[224, 120, 40], thickness=8,
                        fill_color=[40, 90, 160], fill_opacity=0.5)
        self.run("select", [rect])
        self.frame([rect], 0.5)
        dialog = color_dialog.create(self.h.window, QColor(224, 120, 40), "Stroke")
        dialog.show()
        dialog.move(self.h.window.mapToGlobal(QPoint(930, 150)))
        settle(1.0)
        self.window("colour_picker", (dialog,))
        dialog.reject()
        settle(0.3)

    def plugins_safety(self) -> None:
        """The Plugins window with a downloaded plugin waiting for review. The callouts are measured
        from the widgets and saved next to the shot (plugins_safety.json)."""
        import json
        import shutil

        from pixelref.ui.plugin_manager import NOTICE_KEY, PluginManagerDialog

        shutil.rmtree(SAFETY_DOWNLOADS, ignore_errors=True)
        folder = SAFETY_DOWNLOADS / "palette_sync"
        folder.mkdir(parents=True)
        (folder / "plugin.toml").write_text(SAFETY_PLUGIN, encoding="utf-8")
        (folder / "__init__.py").write_text("def activate(ctx):\n    pass\n", encoding="utf-8")
        (folder / "README.md").write_text("# Palette Sync\n\nFetches palettes from palettes.example.com.\n",
                                          encoding="utf-8")
        session = self.h.session
        session.settings.remove(NOTICE_KEY)  # a new user sees the first-time notice
        session.plugins.add_source(SAFETY_DOWNLOADS, label="Downloads")
        dialog = PluginManagerDialog(self.h.app, self.h.window, show="attention")
        try:
            dialog.resize(1400, 860)
            dialog.show()
            dialog.select_plugin("palette_sync")
            settle(1.0)

            def rect(widget, local=None) -> list[int]:  # noqa: ANN001
                area = local if local is not None else widget.rect()
                corner = widget.mapTo(dialog, area.topLeft())
                return [corner.x(), corner.y(), area.width(), area.height()]

            row = dialog.visible_ids().index("palette_sync")
            cell = dialog.table.visualRect(dialog.proxy.index(row, 4))
            rects = {"notice": rect(dialog.notice), "banner": rect(dialog.risk_banner),
                     "attention": rect(dialog.chip_buttons["attention"]), "trust": rect(dialog.table.viewport(), cell),
                     "details": rect(dialog.details), "approve": rect(dialog.trust_button)}
            self.save("plugins_safety", dialog.grab().toImage())
            (SHOT_DIR / "plugins_safety.json").write_text(json.dumps(rects), encoding="utf-8")
        finally:
            dialog.reject()
            session.plugins.remove_source(SAFETY_DOWNLOADS.resolve())
            session.settings.remove(NOTICE_KEY)
            shutil.rmtree(SAFETY_DOWNLOADS, ignore_errors=True)
        settle(0.3)

    def shortcuts(self) -> None:
        self.run("select", clear=True)
        self.run("help.show")
        settle(1.0)
        from PySide6.QtWidgets import QApplication, QDialog

        dialogs = [w for w in QApplication.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible()]
        dialogs[0].resize(1100, 400)
        dialogs[0].editor.search.setText("align")
        settle(0.8)
        image = dialogs[0].grab().toImage()
        # The dialog's footer prints the keymap file's path (a user folder): paint it out.
        painter = QPainter(image)
        painter.fillRect(0, image.height() - 62, image.width(), 18, image.pixelColor(300, image.height() - 70))
        painter.end()
        self.save("shortcuts", image)
        dialogs[0].close()



def take_shots(names: tuple[str, ...]) -> None:
    shots = OnboardingShots()
    try:
        for name in names:
            getattr(shots, name)()
    finally:
        shots.close()


WELCOME_BLOCKS = (
    ('How to read this guide',
     [
         '•  Scroll to zoom. Hold Space (or the middle mouse button) and drag to pan.',
         '•  Each lesson is a card. A screenshot has yellow numbered callouts, and a live card has numbered dots on real items. The numbers match the explanation under it.',
         '•  Pictures, notes and shapes on a “live” card or a “Your turn” card are real: drag, edit and break them. Text, backgrounds and screenshots are locked, so they stay put. This guide opens as an untitled copy, so nothing you do to it is kept unless you save it.',
     ]),
    ('Jump around',
     [
         "•  Ctrl+1 is this welcome, Ctrl+2 Staying Safe, Ctrl+3 the Basic Features, Ctrl+4 the Advanced Features and Ctrl+5 the New Features. The Markers menu in a view's header lists every lesson, and clicking one flies you there.",
         '•  Press Shift+F to see the whole guide at once, then scroll in on the card you want.',
     ]),
    ('Where to start',
     [
         '•  Adding plugins or running someone else’s scripts?  Read Staying Safe first (Ctrl+2): two short lessons.',
         '•  New here?  Basic Features, lessons B1 to B6, take about ten minutes.',
         '•  Know another reference tool?  Skim Advanced Features: effects, links, redaction, Steps, views, playback and scripts.',
         '•  Back after an update?  New Features covers copying attributes, editing several items at once, the colour picker, notes on single GIF frames and clickable buttons.',
     ]),
    ('Save and share',
     [
         '•  Ctrl+S saves the whole board, pictures included, as one .pxref file. Ctrl+E exports an image, Ctrl+Alt+C copies the board (or the selection) to the clipboard.',
     ]),
    ('Come back any time',
     [
         '•  Right-click the canvas and choose Getting Started…, press Ctrl+K and type “getting started”, or click Getting Started on the start panel of an empty board.',
         '•  The safety guide is in the same right-click menu: Staying Safe with Plugins & Scripts….',
     ]),
)


def build_board() -> None:
    from harness import Harness

    h = Harness()
    h.window.resize(1500, 900)
    h.run("new_scene")
    h.run("canvas_background.set", background=[23, 25, 29], pattern="dots", grid_size=40)
    board = Board(h)
    welcome(board)
    _safety_right, safety_bottom = board.section(
        "Staying Safe", "Plugins and scripts can do anything you can. Two lessons on what to check before you "
        "trust them.", SAFETY, "S", 0, board.welcome_bottom + 450, RED, None, icon=True)
    basic_right, basic_bottom = board.section(
        "Basic Features", "Everything you need on day one: bring pictures in, move around, arrange them and mark them up.",
        BASIC, "B", 0, safety_bottom + 450, BLUE,
        ("the basics", ["Scroll to zoom in on a picture, then press Shift+F to see them all again.",
                        "Click a picture, drag a corner handle to resize it and the round knob to rotate it.",
                        "Press T, click on the board and type a note. Press Esc when you are done.",
                        "Press R and drag a box around a face. Press V to go back to selecting.",
                        "Select two pictures with Shift+click and press G to group them. Ctrl+Shift+G ungroups.",
                        "Made a mess? Ctrl+Z undoes it."],
         ["leopard", "tiger", "cat", "poodle", "jumper", "brown_poodle"]))
    advanced_top = basic_bottom + 450
    advanced_right, advanced_bottom = board.section(
        "Advanced Features", "When the basics feel natural: effects, links, redaction, review views, playback and scripting.",
        ADVANCED, "A", 0, advanced_top, ORANGE,
        ("the advanced tools", ["Select a picture and add an Edge Detect effect in the Attribute Editor.",
                                "Press B and drag over a face to blur it. Press X to pixelate instead.",
                                "Press W and drag from one picture to another to link them.",
                                "Press N and click to drop numbered Steps on the details you want to point out.",
                                "Press Ctrl+W then N to open a second view of the same board.",
                                "Press Ctrl+K and type “script” to open the Script Editor."],
         ["fennec", "red_poodle", "jumper", "poodle", "leopard", "cat"]))
    board.section(
        "New Features", "Fresh in this version: copy styling between items, edit many items at once, pick colours "
        "properly, pin notes to single frames of a GIF and make boards clickable.",
        NEWEST, "N", 0, advanced_bottom + 450, GREEN,
        ("the new tools", ["Select a rectangle, press Ctrl+Alt+Shift+C, select a picture's note and press Ctrl+Alt+Shift+V.",
                           "Shift+click several pictures and look for Shared attributes in the Attribute Editor.",
                           "Press R, drag a box, then click its Stroke swatch to try the colour picker.",
                           "Click the GIF, press K to pause it, step with . and draw a box on one frame. Press K to play.",
                           "Select a picture, right-click → Click Action → Make Reveal Button, lock the new button in the Outliner and click it."],
         ["reaction", "fennec", "cat", "jumper"]))
    place_markers(board)
    h.run("select", clear=True)
    h.refresh()
    h.run("views.toggle_markers")  # the markers' dots would sit on top of the lessons
    h.run("views.look_through", "Welcome", animate=False)
    settle(1.0)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    if not h.run("file.save_to", str(TARGET)):
        raise SystemExit("could not save the board")
    print("saved", TARGET, f"{TARGET.stat().st_size / 1e6:.1f} MB")
    h.session.mark_saved()
    h.close()


def welcome(board: Board) -> None:
    group = board.group("Welcome (card)")
    backdrop = board.rect(-60, -50, 2820, 10, group, "Card", stroke_color=[230, 233, 240], thickness=3,
                          fill_color=[30, 34, 41], fill_opacity=1.0, corner_radius=36)
    title = board.text("Welcome to pixelRef", 0, 0, 5.5, group, name="Title", bold=True, font_size=32,
                       color=[246, 247, 250])
    y = board.height(title) + 40
    lead = board.text("A reference board for everything you are looking at. This guide is itself a pixelRef board: "
                      "pan, zoom and click around it just like you will your own.", 0, y, 3.0, group, 2600,
                      name="Lead", font_size=14, color=[205, 210, 220])
    y += board.height(lead) + 90
    y = safety_panel(board, group, y) + 110
    bottom = y
    for head, bullets in WELCOME_BLOCKS:
        title_node = board.text(head, 0, bottom, 3.0, group, 1550, name=f"{head} heading", bold=True, font_size=18,
                                color=[255, 204, 0])
        bottom += board.height(title_node) + 24
        body = board.text("\n\n".join(bullets), 0, bottom, 2.6, group, 1550, name=f"{head} text", font_size=14,
                          color=[226, 229, 235])
        bottom += board.height(body) + 90
    bottom -= 90
    for key, x, py, w, tilt in (("tiger", 1750, y, 520, -6), ("leopard", 2250, y - 40, 400, 5),
                                ("jumper", 1700, y + 470, 440, 4), ("cat", 2180, y + 430, 400, -4)):
        board.picture(board.animals[key], x, py, w, rotate=tilt)  # loose and unlocked: yours to drag around
    height = max(bottom, y + 900) + 60 - (-50)
    board.h.run("set_attr", backdrop, "size", [2820, height])
    board.lock(group)
    board.markers.append(("Welcome", -60, -50, 2820, height))
    board.welcome_bottom = height - 50


def safety_panel(board: Board, group: str, y: float) -> float:
    """The warning panel at the top of the welcome card; returns its bottom."""
    panel = board.rect(-10, y, 2720, 10, group, "Safety panel", stroke_color=list(AMBER), thickness=8,
                       fill_color=[58, 44, 18], fill_opacity=1.0, corner_radius=30)
    board.picture(board.warning, 40, y + 40, 300, group)
    icon_h = 300 * 0.9
    heading = board.text("Stay safe: plugins and scripts can do anything you can", 400, y + 40, 3.2, group, 2250,
                         name="Safety heading", bold=True, font_size=18, color=list(AMBER))
    body_y = y + 40 + board.height(heading) + 24
    body = board.text(
        "Third-party plugins, scripts and Python packages run with your permissions: they can read and change your "
        "files, use the network and start other programs. Only install them from people you trust.\n\n"
        "pixelRef won't run a new or changed plugin until you approve it in Window → Plugins….  "
        "Read Staying Safe (Ctrl+2) before you add one, or any time: right-click the canvas → "
        "Staying Safe with Plugins & Scripts….",
        400, body_y, 2.6, group, 2250, name="Safety text", font_size=14, color=[240, 232, 214])
    bottom = max(body_y + board.height(body), y + 40 + icon_h) + 40
    board.h.run("set_attr", panel, "size", [2720, bottom - y])
    return bottom


def place_markers(board: Board) -> None:
    """View markers: Welcome, the two sections, then one per lesson (Ctrl+1…9 reach the first nine)."""
    h = board.h
    boxes = {name: (x, y, w, hh) for name, x, y, w, hh in board.markers}
    for band in ("Staying Safe band", "Basic Features band", "Advanced Features band", "New Features band"):
        node = h.scene.nodes[band]
        x, y = node.attrs["translate"]
        w, hh = node.attrs["size"]
        boxes[band.replace(" band", "")] = (x, y, w, hh)
    order = (["Welcome", "Staying Safe", "Basic Features", "Advanced Features", "New Features"]
             + [n for n, *_ in board.markers if n != "Welcome"])
    for name in order:
        x, y, w, hh = boxes[name]
        zoom = round(min(1150 / w, 830 / hh) * 0.92, 4)
        color = list({"B": BLUE, "A": ORANGE, "N": GREEN, "S": RED}.get(name[0], BLUE) if name != "Welcome" else BLUE)
        h.run("create_node", "camera", name, attrs={"translate": [x + w / 2, y + hh / 2],
                                                      "center": [x + w / 2, y + hh / 2], "zoom": zoom, "color": color})


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "shots"):
        take_shots(SHOT_NAMES)
    if which in ("all", "board"):
        build_board()
