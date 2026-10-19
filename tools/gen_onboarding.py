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
from PySide6.QtGui import QImage, QImageWriter, QPainter  # noqa: E402

from onboarding_board import ADVANCED, BASIC, BLUE, ORANGE, SHOT_DIR, Board  # noqa: E402

SAMPLE = gs.SCENE
TARGET = ROOT / "examples" / "scenes" / "onboard.pxref"
SHOT_NAMES = ("start", "navigate", "command_search", "review", "script_editor", "shortcuts")  # only interface that can't be recreated on the canvas


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


WELCOME_LINES = (
    "How to read this guide",
    "",
    "•  Scroll to zoom. Hold Space (or the middle mouse button) and drag to pan.",
    "•  Ctrl+1 shows this welcome, Ctrl+2 the Basic Features and Ctrl+3 the Advanced Features. "
    "The Markers menu in a view's header jumps to any lesson.",
    "•  Each lesson is a screenshot with yellow numbered callouts. The numbers match the explanation under it.",
    "•  The “Your turn” cards and the framed areas on the lessons hold real pictures, notes and shapes to practise on. "
    "Text, backgrounds and screenshots are locked, so they stay put while you edit. This guide opens as an untitled "
    "copy, so nothing you do to it is kept unless you save it.",
    "",
    "Save and share",
    "",
    "•  Ctrl+S saves the whole board, pictures included, as one .pxref file. Ctrl+E exports an image, "
    "Ctrl+Alt+C copies the board (or the selection) to the clipboard.",
    "",
    "Come back any time",
    "",
    "•  Right-click the canvas and choose Getting Started…, press Ctrl+K and type “getting started”, "
    "or click Getting Started on the start panel of an empty board.",
)


def build_board() -> None:
    from harness import Harness

    h = Harness()
    h.window.resize(1500, 900)
    h.run("new_scene")
    h.run("canvas_background.set", background=[23, 25, 29], pattern="dots", grid_size=40)
    board = Board(h)
    welcome(board)
    basic_right, basic_bottom = board.section(
        "Basic Features", "Everything you need on day one: bring pictures in, move around, arrange them and mark them up.",
        BASIC, "B", 0, board.welcome_bottom + 450, BLUE,
        ("the basics", ["Scroll to zoom in on a picture, then press Shift+F to see them all again.",
                        "Click a picture, drag a corner handle to resize it and the round knob to rotate it.",
                        "Press T, click on the board and type a note. Press Esc when you are done.",
                        "Press R and drag a box around a face. Press V to go back to selecting.",
                        "Select two pictures with Shift+click and press G to group them. Ctrl+Shift+G ungroups.",
                        "Made a mess? Ctrl+Z undoes it."],
         ["leopard", "tiger", "cat", "poodle", "jumper", "brown_poodle"]))
    advanced_top = basic_bottom + 450
    board.section(
        "Advanced Features", "When the basics feel natural: effects, links, redaction, review views, playback and scripting.",
        ADVANCED, "A", 0, advanced_top, ORANGE,
        ("the advanced tools", ["Select a picture and add an Edge Detect effect in the Attribute Editor.",
                                "Press B and drag over a face to blur it. Press X to pixelate instead.",
                                "Press W and drag from one picture to another to link them.",
                                "Press N and click to drop numbered Steps on the details you want to point out.",
                                "Press Ctrl+W then N to open a second view of the same board.",
                                "Press Ctrl+K and type “script” to open the Script Editor."],
         ["fennec", "red_poodle", "jumper", "poodle", "leopard", "cat"]))
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
    y += board.height(lead) + 120
    how = board.text("\n".join(WELCOME_LINES), 0, y, 2.6, group, 1550, name="How to read", font_size=14,
                     color=[226, 229, 235])
    bottom = y + board.height(how)
    for key, x, py, w, tilt in (("tiger", 1750, y, 520, -6), ("leopard", 2250, y - 40, 400, 5),
                                ("jumper", 1700, y + 470, 440, 4), ("cat", 2180, y + 430, 400, -4)):
        board.picture(board.animals[key], x, py, w, rotate=tilt)  # loose and unlocked: yours to drag around
    height = max(bottom, y + 900) + 60 - (-50)
    board.h.run("set_attr", backdrop, "size", [2820, height])
    board.lock(group)
    board.markers.append(("Welcome", -60, -50, 2820, height))
    board.welcome_bottom = height - 50


def place_markers(board: Board) -> None:
    """View markers: Welcome, the two sections, then one per lesson (Ctrl+1…9 reach the first nine)."""
    h = board.h
    boxes = {name: (x, y, w, hh) for name, x, y, w, hh in board.markers}
    for band in ("Basic Features band", "Advanced Features band"):
        node = h.scene.nodes[band]
        x, y = node.attrs["translate"]
        w, hh = node.attrs["size"]
        boxes[band.replace(" band", "")] = (x, y, w, hh)
    order = ["Welcome", "Basic Features", "Advanced Features"] + [n for n, *_ in board.markers if n != "Welcome"]
    for name in order:
        x, y, w, hh = boxes[name]
        zoom = round(min(1150 / w, 830 / hh) * 0.92, 4)
        color = list(BLUE if name.startswith("B") or name == "Welcome" else ORANGE)
        h.run("create_node", "camera", name, attrs={"translate": [x + w / 2, y + hh / 2],
                                                      "center": [x + w / 2, y + hh / 2], "zoom": zoom, "color": color})


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "shots"):
        take_shots(SHOT_NAMES)
    if which in ("all", "board"):
        build_board()
