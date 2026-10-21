"""The content and layout of examples/scenes/onboard.pxref (used by tools/gen_onboarding.py).

``Board`` lays the lessons out in a running pixelRef test harness: a welcome card (with a
"stay safe" warning panel), then a "Staying Safe" section and the "Basic", "Advanced" and
"New Features" sections, each a grid of cards. A card is a group holding a screenshot,
numbered callouts drawn on it with pixelRef's own rectangle and Step tools, and the
explanation under it. A callout can name a widget instead of giving a rectangle: the
screenshot step measured where it is (``<shot>.json``).
"""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "scenes" / "animals.pxref"
GIFS = ROOT / "examples" / "scenes" / "gifs.pxref"
SHOT_DIR = Path(tempfile.gettempdir()) / "pixelref_onboarding_shots"

CARD_W = 1500          # every screenshot is scaled to this width
PITCH_X = 1800         # card spacing, left to right
COLS = 3
DEMO_H = 820          # height of the live area on a card that has no screenshot
TITLE_SCALE = 1.6
BODY_SCALE = 2.3
BLUE = (61, 142, 240)
ORANGE = (255, 160, 60)
GREEN = (90, 205, 140)
RED = (235, 80, 70)
AMBER = (255, 190, 40)
CALLOUT = (255, 204, 0)

# Animal pictures reused from the sample board: wanted key -> node name in animals.pxref.
ANIMALS = {"leopard": "images", "tiger": "tiger_laying_hero_background", "cat": "cat-cat-dance",
           "poodle": "Ct2Wgc9X63dUweHXKn4Rxa", "fennec": "85120553", "jumper": "zPtcQJsjyUG4nLJZ7YN2x6",
           "brown_poodle": "960px-Miniature_Poodle_(Hungary)", "red_poodle": "collar-perro-nara-azul-cielo-caniche-frontal"}

# Each card is a lesson. A card with a "shot" is a screenshot with numbered yellow callouts ((x, y, w, h) in
# screenshot pixels, text): used only for interface that cannot be recreated on the canvas. A card with a "demo"
# is built from real canvas items you can move and edit; its marks are just the explanation texts, numbered to
# match the markers the demo places.
BASIC = [
    dict(title="Open the app and bring images in", shot="start",
         lead="A new board is empty, and the start panel is your way in.",
         marks=[((665, 420, 60, 34), "Open… (Ctrl+O) loads a board. Boards you saved show up here as recent files, with a thumbnail."),
                ((421, 401, 325, 95), "The start panel only shows on an empty board. F1 lists every shortcut."),
                ((1473, 5, 26, 290), "These tabs show or hide the Tools, Outliner and Attribute Editor panels.")],
         extra=["Add images: paste with Ctrl+V (clipboard pictures and screenshots too), drag in files, folders, GIFs or videos, or press Ctrl+I.",
                "Images are embedded, so one .pxref file holds the whole board. PureRef .pur boards open too (File → Open PureRef Board)."]),
    dict(title="Move around the canvas", shot="navigate",
         lead="The canvas is endless. Scroll to zoom, hold Space or the middle mouse button and drag to pan.",
         marks=[((931, 708, 220, 150), "The minimap shows where the view sits on the whole board."),
                ((1330, 878, 160, 18), "Zoom bar: click the % for 100% (Ctrl+0), the arrow for presets, Fit to see everything."),
                ((722, 3, 85, 20), "Markers save a view you can jump back to: Ctrl+1 … Ctrl+9."),
                ((5, 878, 165, 18), "Workspaces (1, 2, 3 …) each keep their own layout of views.")],
         extra=["F frames the selection, Shift+F frames everything, and double-clicking a picture zooms to it. Tap Space to maximise a view."]),
    dict(title="Select, move, scale and rotate", demo="select",
         lead="Try it on the two pictures in the frame. Click one, or drag a box around both. Shift or Ctrl adds to the selection.",
         marks=["Corner handles scale (Shift: free, Alt: from the centre). Edge handles stretch.",
                "The round knob above the selection rotates. Hold Shift for 15° steps.",
                "Drag to move. The Attribute Editor holds the exact numbers: position, size, scale and rotation.",
                "The arrow keys nudge (Shift: ×10). Ctrl+Z undoes any change, Ctrl+Shift+Z redoes it."],
         extra=["A small quick-edit bar floats under the selection with its most useful settings."]),
    dict(title="Add notes and shapes", demo="annotate",
         lead="Annotations are items too, so they move, scale and undo like pictures. Everything in the frame is live.",
         marks=["Press R and drag for a rectangle, O for an ellipse. Blue dots on a shape's corners set the corner radius.",
                "Press T and click to type a note. Double-click text to edit it, drag the side handles to set its width.",
                "Press A and drag for an arrow. The quick-edit bar sets colour, thickness and arrowheads.",
                "Pick a tool in the Tools panel or press its key. Drawing tools stay active until you press Esc or V."],
         extra=["A note made while one picture is selected is attached to it and travels with it."]),
    dict(title="Group and organise", demo="group",
         lead="G groups the selection, Ctrl+Shift+G ungroups. A group moves, scales and rotates as one.",
         marks=["This pair is already a group. Drag one picture and both move.",
                "These two are loose. Shift+click both and press G to group them.",
                "Everything is listed in the Outliner: drag rows to reparent, F2 renames, the eye hides and the lock protects an item."],
         extra=["Ctrl+Alt+G arranges the selection in a grid. Tab hides the side panels for a clean canvas.",
                "The lock in the Outliner is how this guide keeps its own text and backgrounds from moving while you edit."]),
    dict(title="Find any command", shot="command_search",
         lead="You never need to remember a menu. Press Ctrl+K, or right-click the canvas, and type.",
         marks=[((332, 235, 318, 28), "Type part of a command name, or what it does."),
                ((326, 288, 332, 102), "Results show their menu and their shortcut, so you learn the keys as you go.")],
         extra=["F1 (or ?) opens the full shortcut list, where you can rebind any key."]),
]

ADVANCED = [
    dict(title="Non-destructive effects", demo="effects", demo_h=520,
         lead="Any item can carry a stack of effects. The original picture is never changed.",
         marks=["The original, untouched.",
                "The same picture with an Edge Detect effect. Select it and open Attribute Editor → Effects to tune it.",
                "A Blur effect. Add effect offers grayscale, invert, threshold, posterize and more. Mix sets how strongly each applies."],
         extra=["Shift+G toggles grayscale on the selected pictures. Copy and Paste put one stack on other items."]),
    dict(title="Link related items", demo="links",
         lead="A link shows what connects to what, and follows the items when they move. Drag either picture.",
         marks=["Press W and drag from one item to another. Ends snap to an item, or pin to a spot on it.",
                "Style it in the Attribute Editor: colour, thickness, dash pattern, straight or curved, arrowheads.",
                "Drag the line itself to add waypoints."],
         extra=["Ctrl+L links the selected items in the order you picked them."]),
    dict(title="Blur and pixelate", demo="redact",
         lead="Hide a face, a name or a detail. These regions have no pixels of their own: they filter whatever is underneath.",
         marks=["Pixelate (X): drag a region, then change the block size. Move the region over the picture to try it.",
                "Blur (B): drag a region, then change the strength. Shift makes it square."],
         extra=["They follow the picture when you move it, and exports and copies show them exactly as you see them."]),
    dict(title="Number your notes with Steps", demo="steps",
         lead="Steps are numbered markers: ideal for review comments and tutorials like this one.",
         marks=["Press N and click. Each click places the next number.",
                "A tail can point at a detail. Drag the tail handle to aim it.",
                "Renumber from the Steps menu, or change the style to letters or roman numerals."],
         extra=["Pair each step with a text note, and your board reads like a checklist."]),
    dict(title="Several views and workspaces", shot="review",
         lead="Look at the same board in more than one way at once.",
         marks=[((0, 0, 550, 25), "Every view has its own camera, so one can stay zoomed out while another looks close."),
                ((556, 0, 610, 25), "Display presets such as Value check show the board in greyscale, to judge contrast."),
                ((556, 496, 610, 25), "A framing guide (16:9, 2.39:1 …) overlays the crop you are composing for."),
                ((5, 878, 165, 18), "Switch workspaces from here, or press Ctrl+W then a number.")],
         extra=["Ctrl+W starts the window manager: split, move and focus views from the keyboard."]),
    dict(title="Animated images and movies", demo="playback", demo_h=620,
         lead="GIFs and videos play right on the board. This one is already running.",
         marks=["Select it and press K to pause or play. Step with , and . and jump to the ends.",
                "Click or drag along the filmstrip under the picture to scrub to a moment.",
                "It keeps its annotations. To pin a note to one frame, see lesson N4."],
         extra=["Movies (mp4, mov, webm …) are always linked, never embedded: keep the video file next to the board."]),
    dict(title="Script it with Python", shot="script_editor",
         lead="Every action in pixelRef is a command, and the same commands can be called from Python.",
         marks=[((158, 452, 806, 215), "Results appear here. Echo shows the Python behind every click, so you can learn by watching."),
                ((158, 702, 806, 110), "Type pxcmd.… and press Ctrl+Enter. Tab completes."),
                ((838, 825, 128, 18), "Echo all commands turns your mouse work into a script you can reuse.")],
         extra=["Open it with Ctrl+W then - . Scripts can also run headless: python -m pixelref --batch script.py."]),
    dict(title="Make the keys yours", shot="shortcuts",
         lead="F1 opens every action with its keys. Search it, then rebind or add shortcuts.",
         marks=[((10, 34, 1080, 28), "Search by name, command or key, for example Ctrl+D."),
                ((10, 356, 322, 34), "Set, add or remove a shortcut. Changes are saved to keymap.toml, which also holds macros.")],
         extra=["The Mouse & Windows tab lists every mouse gesture."]),
]


# Plugins and scripts are code: shown right after the welcome, marked with the warning sign.
SAFETY = [
    dict(title="Plugins can do anything you can", shot="plugins_safety",
         lead="Plugins, scripts and Python packages are code. They run inside pixelRef with your permissions: "
              "they can read and change your files, use the network and start other programs.",
         marks=[("notice", "The first time you open Window → Plugins…, this notice says what plugins can do. "
                           "Read it, then click Got it."),
                ("banner", "This line turns amber whenever a plugin is waiting for your review."),
                ("attention", "Needs attention lists plugins that are new, changed, failing or doing more than "
                              "they declared."),
                ("trust", "Its trust sign: this one is “? Not reviewed”, so its code has never run (see S2)."),
                ("details", "Who made it, whether it is signed, and what it says it will do: here, use the "
                            "network. Read this before you approve anything."),
                ("approve", "Nothing runs until you approve it. The approval dialog asks once more, and "
                            "Cancel is the default.")],
         extra=["A plugin whose files change after you approved it stops until you approve it again.",
                "Scripts, userSetup.py and Python packages aren't checked like plugins: only run code you "
                "wrote or got from someone you trust."]),
    dict(title="Read the trust signs", demo="trust", demo_h=760,
         lead="Every third-party plugin in Window → Plugins… carries one of these signs.",
         marks=["Green and blue: allowed to run. Signed by a publisher you trust, or approved by you with "
                "exactly these files.",
                "Amber: waiting for you. Ask who made it, where you got it, and why it needs what it asks for.",
                "Red: blocked. The files don't match the publisher's signature, so someone may have changed "
                "them. Don't approve it."],
         extra=["Right-click the canvas → Staying Safe with Plugins & Scripts… opens the full guide: what "
                "pixelRef checks, what it can't, and what to do if something looks wrong."]),
]


GIF_ASSET = "90.gif"  # the eight-frame reaction GIF in gifs.pxref, used for the frame-notes lessons


# What changed most recently. Interface that cannot be recreated on the canvas is a screenshot; the rest is live.
NEWEST = [
    dict(title="Copy attributes between items", shot="paste_attrs",
         lead="Style one item, then give the same look to as many others as you like, and choose exactly what is copied.",
         marks=[((383, 205, 738, 28), "Search the list by attribute or group name, for example “color”."),
                ((383, 240, 135, 18), "Tick a whole group, or All attributes. Geometry (position, size, rotation) starts closed and unticked."),
                ((1008, 240, 112, 18), "Only differences hides values the items already share, so you see what will really change."),
                ((900, 265, 160, 320), "Value shows the current value, an arrow and the value it becomes. The canvas previews it as you tick."),
                ((1017, 690, 100, 30), "Paste is one undo step. Cancel puts everything back.")],
         extra=["Select the source item, press Ctrl+Alt+Shift+C, select the targets and press Ctrl+Alt+Shift+V. "
                "The buttons are also in Attribute Editor → Appearance.",
                "An item takes an attribute when it has the same name and type, so a rectangle's stroke can go onto an ellipse or an arrow. "
                "Paste Style Attributes pastes just the look, without the window."]),
    dict(title="Edit several items at once", demo="shared", demo_h=640,
         lead="Select more than one item and the Attribute Editor edits what they have in common. Try it on the three shapes.",
         marks=["Drag a box around all three. A Shared attributes section appears in the Attribute Editor.",
                "Change Thickness once and every shape follows. “mixed” means the values differ.",
                "The ⇉ button gives the others the value of the item named in Values from. Right-click a row to apply it to every item of that type.",
                "Or copy the thick one (Ctrl+Alt+Shift+C), select the others and press Ctrl+Alt+Shift+V to choose what to paste."],
         extra=["Here an attribute only needs the same name on the other items. A value that does not fit an item is skipped."]),
    dict(title="A better colour picker", shot="colour_picker",
         lead="Every colour swatch (Stroke, Fill, Text, Highlight, Border, the canvas background) opens a popover beside it.",
         marks=[((258, 619, 42, 28), "Click any colour swatch. Changes show on the canvas while you drag, and the whole edit is one undo step."),
                ((951, 160, 213, 210), "The square sets saturation and brightness, the ring sets the hue. Sliders and fields set alpha, RGB, HSV and HSL."),
                ((1030, 379, 134, 26), "Type or paste a hex code such as #E07828 or rgb(224, 120, 40)."),
                ((940, 161, 32, 30), "The eyedropper picks a colour from anywhere on your screen, even another application."),
                ((940, 515, 225, 22), "In this scene lists the colours already on your board, so a new item can match an old one.")],
         extra=["Prefer the classic dialog? Press Ctrl+K and type “picker” to switch back to the Qt colour dialog."]),
    dict(title="Notes on one frame of a GIF", demo="frames", demo_h=700,
         lead="Pin a note, arrow or drawing to a single frame of a GIF or movie, so it only appears while that frame shows.",
         marks=["Pause (K), step with , and . to a frame, then draw. Anything drawn on a paused frame is pinned to it. "
                "To pin an existing note, select it and press Alt+F.",
                "The filmstrip under the picture ticks each frame that has a note (amber) or a link (blue). Click a tick to jump there.",
                "A link drawn from a note onto the paused picture remembers the frame. Clicking that end jumps the GIF back to it.",
                "Ghost (Attribute Editor) also shows a note faintly on the frames either side. Show on All Frames un-pins it."],
         extra=["Press play and watch the red box and arrow appear only on their own frames. "
                "Copy to Frames puts a copy of a note on a range of frames, for example 2-5."]),
    dict(title="Make the board clickable", demo="interactions", demo_h=820,
         lead="Buttons, and any other item, can show, hide, move or change things when they're clicked. "
              "Everything in the frame is live: click the buttons.",
         marks=["Click it to show the answer, and again to hide it. It was made by dragging the round handle "
                "beside a selected button onto the note and choosing Show.",
                "A button runs only when it's locked. Unlocked, a click selects it for editing, and Run in the bar "
                "under it tries it. These are locked: unlock one in the Outliner to edit it.",
                "Tabs: select a few pictures, right-click → Click Action → Make Tabs from Selection. One shows at a time.",
                "Demonstrated, not programmed: select a button, choose Demonstrate…, make the board look the way it "
                "should after the click (here: the GIF on frame 3), then press Done. The board goes back by itself."],
         extra=["The Attribute Editor's Click Action section says what a click does in plain words (“Go to frame 3 · "
                "reaction”). Try it runs it, Reset puts things back, and × removes a step.",
                "Lock your buttons when the board is finished: that's what makes a plain click run them.",
                "An amber dashed outline means a step didn't run, for example because its item was deleted. "
                "Right-click → Click Action → Click Action Log… says why."]),
]


def asset_files(workdir: Path) -> dict[str, Path]:
    """The animal pictures embedded in animals.pxref, and the GIF from gifs.pxref, written out as files."""
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    by_node = {n["name"]: n["attrs"].get("asset") for n in data["nodes"] if n["type"] == "image"}
    files = {}
    for key, node in ANIMALS.items():
        asset = data["assets"][by_node[node]]
        path = workdir / f"{key}{Path(asset['name']).suffix}"
        path.write_bytes(base64.b64decode(asset["data"]))
        files[key] = path
    gifs = json.loads(GIFS.read_text(encoding="utf-8"))
    asset = next(a for a in gifs["assets"].values() if a["name"] == GIF_ASSET)
    files["reaction"] = workdir / "reaction.gif"
    files["reaction"].write_bytes(base64.b64decode(asset["data"]))
    return files


def warning_icon(path: Path, size: int = 512) -> Path:
    """The warning sign used on the board: an amber triangle with an exclamation mark (an image,
    so it looks the same whatever fonts the computer has)."""
    image = QImage(size, int(size * 0.9), QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    w, h, m = float(size), size * 0.9, size * 0.06
    triangle = QPainterPath()
    triangle.moveTo(w / 2, m)
    triangle.lineTo(w - m, h - m)
    triangle.lineTo(m, h - m)
    triangle.closeSubpath()
    pen = QPen(QColor(25, 25, 25), size * 0.035)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QColor(*AMBER))
    painter.drawPath(triangle)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(25, 25, 25))
    bar_w = size * 0.1
    painter.drawRoundedRect(QRectF(w / 2 - bar_w / 2, h * 0.32, bar_w, h * 0.34), bar_w / 2, bar_w / 2)
    painter.drawEllipse(QPointF(w / 2, h * 0.78), bar_w * 0.62, bar_w * 0.62)
    painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(path))
    return path


class Board:
    """Builds the onboarding scene in a running test harness."""

    def __init__(self, h: Any) -> None:
        self.h = h
        self.work = Path(tempfile.mkdtemp(prefix="pixelref_onboarding_"))
        self.animals = asset_files(self.work)
        self.markers: list[tuple[str, float, float, float, float]] = []  # name, x, y, width, height
        self.warning = warning_icon(self.work / "warning.png")

    # ---- Pieces -----------------------------------------------------------------------------

    def node(self, node_type: str, name: str, parent: str | None = None, **attrs: Any) -> str:
        return self.h.run("create_node", node_type, name, parent=parent, attrs=attrs)

    def text(self, text: str, x: float, y: float, scale: float, parent: str | None = None, width: float | None = None,
             name: str = "Note", **style: Any) -> str:
        attrs = {"text": text, "translate": [x, y], "scale": [scale, scale], **style}
        if width:
            attrs["text_width"] = width / scale
        return self.node("annotation_text", name, parent, **attrs)

    def rect(self, x: float, y: float, w: float, h: float, parent: str | None = None, name: str = "Rect",
             **style: Any) -> str:
        return self.node("annotation_rect", name, parent, translate=[x, y], size=[w, h], **style)

    def picture(self, path: Path, x: float, y: float, width: float, parent: str | None = None,
                rotate: float = 0.0) -> str:
        name = self.h.run("image.import_image", str(path), translate=[x, y], centered=False)
        if parent:
            self.h.run("parent", name, parent)
        factor = width / QImage(str(path)).width()
        self.h.run("set_attr", name, "scale", [factor, factor])
        if rotate:
            self.h.run("set_attr", name, "rotate", rotate)
        return name

    def group(self, name: str) -> str:
        return self.node("group", name, pick_members=1)

    def height(self, name: str) -> float:
        """How tall a node really is: the canvas lays text out, so ask it."""
        self.h.app.canvas.refresh(force=True)
        return float(self.h.run("get_attr", name, "world_bounds")[3])

    # ---- Locking and small helpers ------------------------------------------------------------

    def lock(self, *names: str) -> None:
        """Lock static content (backgrounds, text, screenshots) so it can't be dragged by accident.
        A locked group locks everything inside it, and a locked node can't be selected on the canvas."""
        for name in names:
            self.h.run("set_attr", name, "locked", 1)

    def pic(self, key: str, x: float, y: float, width: float, rotate: float = 0.0, parent: str | None = None) -> tuple[str, float]:
        """A live (unlocked) animal picture; returns its name and its height."""
        size = QImage(str(self.animals[key]))
        return self.picture(self.animals[key], x, y, width, parent, rotate), width * size.height() / size.width()

    def marker(self, parent: str, number: int, x: float, y: float, **style: Any) -> str:
        return self.node("annotation_step", f"Marker {number}", parent, translate=[x, y], number=number,
                         **{"radius": 26, "fill_color": list(CALLOUT), "text_color": [20, 20, 20],
                            "border_color": [20, 20, 20], "border": 2.0, **style})

    # ---- Live demos: the area is 1500 wide and DEMO_H tall; (cx, top) is its corner ----------------
    # Each returns the points where its numbered markers go and may add static labels to `static`.

    def demo_select(self, cx: float, top: float, static: str) -> list[tuple[float, float]]:
        _, th = self.pic("tiger", cx + 80, top + 90, 640, -3)
        _, ph = self.pic("leopard", cx + 820, top + 70, 560, 4)
        return [(cx + 70, top + 80), (cx + 400, top + 40), (cx + 1100, top + 70 + ph / 2), (cx + 750, top + self.demo_h - 60)]

    def demo_annotate(self, cx: float, top: float, static: str) -> list[tuple[float, float]]:
        self.pic("fennec", cx + 60, top + 60, 700)
        self.h.run("rect.create", cx + 140, top + 90, 480, 300, "Rect", stroke_color=[255, 80, 80], thickness=8,
                   corner_radius=30)
        self.h.run("rect.create_ellipse", cx + 1000, top + 470, 380, 230, "Ellipse", stroke_color=[90, 200, 255],
                   thickness=8)
        self.text("Try editing me!", cx + 900, top + 90, 1.8, None, name="Note", bold=True, font_size=32,
                  color=[255, 232, 120])
        self.h.run("arrow.create", cx + 1000, top + 260, cx + 640, top + 300, "Arrow", color=[255, 232, 120],
                   thickness=8)
        return [(cx + 130, top + 80), (cx + 890, top + 80), (cx + 840, top + 270), (cx + 990, top + 460)]

    def demo_group(self, cx: float, top: float, static: str) -> list[tuple[float, float]]:
        group = self.group("Pair (group)")
        self.pic("tiger", cx + 80, top + 90, 500, -2, group)
        self.pic("leopard", cx + 360, top + 400, 400, 4, group)
        self.pic("jumper", cx + 1000, top + 90, 420, 3)
        self.pic("brown_poodle", cx + 1040, top + 430, 380, -3)
        return [(cx + 60, top + 70), (cx + 990, top + 70), (cx + 1370, top + self.demo_h - 90)]

    def demo_effects(self, cx: float, top: float, static: str) -> list[tuple[float, float]]:
        for i, label in enumerate(("Original", "Edge Detect", "Blur")):
            x = cx + 20 + i * 500
            name, _h = self.pic("fennec", x, top + 160, 460)
            if i == 1:
                self.h.run("effects.add", "edge_detect", [name], strength=3.5, color=[255, 180, 40])
            elif i == 2:
                self.h.run("effects.add", "blur", [name], radius=45.0)
            self.text(label, x, top + 60, 1.6, static, name="Label", bold=True, font_size=32, color=[246, 247, 250])
        return [(cx + x, top + 160) for x in (30, 530, 1030)]

    def demo_links(self, cx: float, top: float, static: str) -> list[tuple[float, float]]:
        a, _ = self.pic("leopard", cx + 60, top + 100, 520, -3)
        b, _ = self.pic("tiger", cx + 900, top + 330, 520, 3)
        self.h.run("links.connect", a, b, color=[255, 204, 0], thickness=6)
        return [(cx + 600, top + 360), (cx + 700, top + 560), (cx + 1000, top + 300)]

    def demo_redact(self, cx: float, top: float, static: str) -> list[tuple[float, float]]:
        self.pic("brown_poodle", cx + 60, top + 80, 480)
        self.pic("jumper", cx + 800, top + 80, 640)
        self.node("annotation_pixelate", "Pixelate", translate=[cx + 170, top + 170], size=[220, 200], cell_size=20.0)
        self.node("annotation_blur", "Blur", translate=[cx + 1010, top + 110], size=[240, 220], radius=30.0)
        return [(cx + 160, top + 160), (cx + 1000, top + 100)]

    def demo_steps(self, cx: float, top: float, static: str) -> list[tuple[float, float]]:
        self.pic("fennec", cx + 60, top + 120, 720)
        self.pic("cat", cx + 900, top + 140, 520)
        points = []
        for i, (x, y, tail) in enumerate(((130, 160, [90, 120]), (640, 220, [-60, 110]), (1000, 220, [120, 120])), 1):
            self.node("annotation_step", f"Step {i}", translate=[cx + x, top + y], number=i, radius=40, tail=tail,
                      fill_color=[235, 64, 52], text_color=[255, 255, 255], border_color=[255, 255, 255], border=2.0)
            points.append((cx + x - 70, top + y - 70))
        self.text("Check the ears", cx + 60, top + 700, 2.2, static, name="Note", font_size=14, color=[226, 229, 235])
        return points[:3]

    def demo_trust(self, cx: float, top: float, static: str) -> list[tuple[float, float]]:
        """The trust signs of the Plugins window, as badges with what each means."""
        rows = (("✔ Verified", GREEN, "Signed by a publisher you trust. Loads without asking."),
                ("● Approved", BLUE, "You approved exactly these files."),
                ("? Not reviewed", AMBER, "Never approved: its code has not run."),
                ("⚠ Changed", AMBER, "Changed since you approved it: stopped."),
                ("✖ Invalid signature", RED, "Its files don't match the signature."))
        points = []
        for i, (badge, color, meaning) in enumerate(rows):
            y = top + 70 + i * 135
            self.rect(cx + 60, y, 560, 96, static, "Badge", stroke_color=list(color), thickness=5,
                      fill_color=list(color), fill_opacity=0.18, corner_radius=48)
            self.text(badge, cx + 100, y + 20, 1.0, static, name="Badge text", bold=True, font_size=32,
                      color=list(color))
            self.text(meaning, cx + 660, y + 22, 2.0, static, 520, name="Meaning", font_size=14,
                      color=[226, 229, 235])
            if i in (0, 2, 4):
                points.append((cx + 40, y - 10))
        self.picture(self.warning, cx + 1210, top + 230, 260, static)
        return points

    def demo_shared(self, cx: float, top: float, static: str) -> list[tuple[float, float]]:
        h = self.h
        h.run("rect.create", cx + 80, top + 110, 380, 260, "Rect A", stroke_color=[255, 99, 99], thickness=3,
              corner_radius=20)
        h.run("rect.create_ellipse", cx + 560, top + 90, 400, 280, "Ellipse B", stroke_color=[90, 200, 255],
              thickness=14)
        h.run("arrow.create", cx + 1100, top + 130, cx + 1400, top + 330, "Arrow C", color=[255, 232, 120],
              thickness=6)
        self.text("Select all three, then change Thickness in the Attribute Editor.", cx + 80, top + 470, 2.0, static,
                  name="Hint", font_size=14, color=[205, 210, 220], width=1300)
        return [(cx + 60, top + 90), (cx + 540, top + 70), (cx + 1080, top + 110), (cx + 80, top + 400)]

    def demo_frames(self, cx: float, top: float, static: str) -> list[tuple[float, float]]:
        h = self.h
        gif, gh = self.pic("reaction", cx + 60, top + 60, 640)
        h.refresh()
        h.run("playback.go_to_frame", 3, [gif])
        h.run("playback.pause", [gif])
        box = h.run("rect.create", cx + 330, top + 120, 240, 130, "Frame 3 box", stroke_color=[255, 70, 70],
                    thickness=8, corner_radius=14)
        h.run("frames.pin", [box], 3)
        h.run("playback.go_to_frame", 6, [gif])
        arrow = h.run("arrow.create", cx + 100, top + 480, cx + 300, top + 360, "Frame 6 arrow",
                      color=[255, 232, 120], thickness=9)
        h.run("frames.pin", [arrow], 6)
        note = self.text("Frame 3: the raised eyebrow", cx + 820, top + 140, 2.0, None, name="Frame note", bold=True,
                         font_size=28, color=[255, 232, 120], width=560)
        h.run("playback.go_to_frame", 3, [gif])
        link = h.run("links.connect", note, gif, color=[255, 204, 0], thickness=5)
        h.run("frames.link_frame", link, "target", 3)
        self.text("Press K to play: the box and the arrow only show on frames 3 and 6.", cx + 820, top + 560, 2.0, static,
                  name="Hint", font_size=14, color=[205, 210, 220], width=560)
        return [(cx + 310, top + 100), (cx + 60, top + 60 + gh + 20), (cx + 810, top + 120), (cx + 100, top + 345)]

    def demo_interactions(self, cx: float, top: float, static: str) -> list[tuple[float, float]]:
        """A quiz with a locked "Show the answer" button, tabs over three pictures, and a GIF with
        a demonstrated "Frame 3" button and a connected Play / Pause button."""
        h = self.h
        hint = dict(font_size=14, color=[205, 210, 220])
        # The quiz: a button connected to a note (Show hides the note until the click).
        self.text("Who has the biggest ears?", cx + 60, top + 40, 1.9, static, 420, name="Question", bold=True,
                  font_size=20, color=[246, 247, 250])
        _fox, fox_h = self.pic("fennec", cx + 60, top + 230, 400)
        answer = self.text("A fennec fox. The ears shed heat in the desert.", cx + 60, top + 250 + fox_h, 1.9, None,
                           400, name="Answer", bold=True, font_size=16, color=[255, 232, 120])
        quiz = h.run("interactions.create_button", cx + 60, top + 705, "Show the answer", "Show the answer",
                     size=[400.0, 76.0], fill_color=[46, 160, 110])
        h.run("interactions.connect", quiz, answer, "show")
        self.lock(quiz)
        # Tabs: three pictures in one place, one button each, one at a time.
        tabs_x = cx + 540
        names = [self.pic(key, tabs_x, top + 190, 400)[0] for key in ("tiger", "leopard", "cat")]
        buttons = h.run("interactions.make_tabs", names)
        self.lock(*buttons)
        for i, (button, label) in enumerate(zip(buttons, ("Tiger", "Leopard", "Cat"))):
            h.run("set_attrs", button, {"translate": [tabs_x + i * 146.0, top + 90.0], "size": [140.0, 64.0],
                                        "label": label, "font_size": 20.0})
        self.text("Tabs: one picture at a time.", tabs_x, top + 700, 2.0, static, 400, name="Hint", **hint)
        # The GIF: "Frame 3" was demonstrated; Play / Pause was connected.
        gif_x = cx + 1020
        gif, gif_h = self.pic("reaction", gif_x, top + 190, 420)
        h.refresh()
        frame3 = h.run("interactions.create_button", gif_x, top + 90, "Frame 3", "Frame 3",
                       size=[200.0, 64.0], font_size=22.0, fill_color=[150, 95, 220])
        h.run("interactions.demonstrate", frame3)
        h.run("playback.go_to_frame", 3, [gif])
        h.run("interactions.finish")
        play = h.run("interactions.create_button", gif_x + 220, top + 90, "Play / Pause", "Play / Pause",
                     size=[200.0, 64.0], font_size=22.0, fill_color=[150, 95, 220])
        h.run("interactions.connect", play, gif, "pause")
        self.lock(frame3, play)
        h.refresh()
        self.text("Frame 3 was demonstrated. Play / Pause was connected.", gif_x, top + 220 + gif_h, 2.0, static, 420,
                  name="Hint", **hint)
        h.run("select", clear=True)
        # Markers sit beside the buttons, never on them: a marker on top would take the click.
        return [(cx + 28, top + 743), (gif_x + 455, top + 122), (tabs_x - 24, top + 66), (gif_x - 24, top + 66)]

    def demo_playback(self, cx: float, top: float, static: str) -> list[tuple[float, float]]:
        self.pic("cat", cx + 440, top + 60, 620)
        return [(cx + 430, top + 50), (cx + 430, top + self.demo_h - 80), (cx + 1090, top + 60)]

    # ---- Cards ------------------------------------------------------------------------------

    def body(self, card: dict, parent: str, x: float, y: float) -> float:
        """The numbered explanations under a screenshot or demo; returns the height they use."""
        lines = [card["lead"], ""]
        lines += [f"{i}   {mark if isinstance(mark, str) else mark[1]}" for i, mark in enumerate(card["marks"], 1)]
        lines += [""] + list(card.get("extra", []))
        name = self.text("\n".join(lines), x, y, BODY_SCALE, parent, CARD_W, name="Explanation",
                         color=[226, 229, 235], font_size=14)
        return self.height(name) + 40

    def card(self, card: dict, number: str, cx: float, cy: float, accent: tuple[int, int, int]) -> float:
        """One lesson: title, a screenshot with numbered callouts or a live demo, explanation. Returns the height used.
        Static pieces sit in a locked group; the demo's own items are left unlocked on top of it."""
        demo = card.get("demo")
        if demo:
            scale, shot_h = 1.0, float(card.get("demo_h", DEMO_H))
            self.demo_h = shot_h
        else:
            image = QImage(str(SHOT_DIR / f"{card['shot']}.jpg"))
            scale = CARD_W / image.width()
            shot_h = image.height() * scale
            measured = SHOT_DIR / f"{card['shot']}.json"
            if measured.is_file():  # callouts that name a widget: where it was in the screenshot
                rects = json.loads(measured.read_text(encoding="utf-8"))
                card = {**card, "marks": [(tuple(rects[m[0]]) if isinstance(m[0], str) else m[0], m[1])
                                          for m in card["marks"]]}
        top = cy + 160
        group = self.group(f"{card['title']} (card)")
        backdrop = self.rect(cx - 60, cy - 50, CARD_W + 120, 10, group, "Card", stroke_color=list(accent),
                             thickness=3, fill_color=[30, 34, 41], fill_opacity=1.0, corner_radius=36)
        self.text(f"{number}  {card['title']}", cx, cy, TITLE_SCALE, group, name="Title", bold=True, font_size=32,
                  color=[246, 247, 250])
        if demo:
            self.rect(cx, top, CARD_W, shot_h, group, "Frame", stroke_color=[70, 76, 88], thickness=2,
                      fill_color=[22, 25, 30], fill_opacity=1.0)
        else:
            self.picture(SHOT_DIR / f"{card['shot']}.jpg", cx, top, CARD_W, group)
            self.rect(cx, top, CARD_W, shot_h, group, "Frame", stroke_color=[70, 76, 88], thickness=2)
        body_h = self.body(card, group, cx, top + shot_h + 40)
        height = 160 + shot_h + 40 + body_h
        self.h.run("set_attr", backdrop, "size", [CARD_W + 120, height + 50])
        points = getattr(self, f"demo_{demo}")(cx, top, group) if demo else []
        marks = self.group(f"{card['title']} (callouts)")  # above the demo so the numbers stay visible
        if demo:
            for i, (x, y) in enumerate(points, 1):
                self.marker(marks, i, x, y)
        else:
            for i, ((x, y, w, h), _text) in enumerate(card["marks"], 1):
                self.rect(cx + x * scale, top + y * scale, w * scale, h * scale, marks, f"Callout {i}",
                          stroke_color=list(CALLOUT), thickness=5, corner_radius=10)
                self.marker(marks, i, cx + x * scale, top + y * scale)
        self.lock(group, marks)
        self.markers.append((f"{number} {card['title']}", cx - 60, cy - 50, CARD_W + 120, height + 50))
        return height + 50

    def sandbox(self, title: str, tasks: list[str], pictures: list[str], cx: float, cy: float,
                accent: tuple[int, int, int]) -> float:
        """A card of loose animal pictures (unlocked) and things to try on them (locked text)."""
        group = self.group(f"Your turn: {title} (card)")
        backdrop = self.rect(cx - 60, cy - 50, CARD_W + 120, 10, group, "Card", stroke_color=list(accent),
                             thickness=3, fill_color=[30, 34, 41], fill_opacity=1.0, corner_radius=36)
        self.text(f"Your turn: {title}", cx, cy, TITLE_SCALE, group, name="Title", bold=True, font_size=32,
                  color=list(accent))
        slots = [(0, 160, 560, -4), (520, 150, 420, 5), (960, 170, 540, -3), (60, 650, 420, 4),
                 (520, 600, 480, -5), (1010, 640, 480, 3)]
        bottom = 0.0
        for key, (x, y, w, tilt) in zip(pictures, slots):
            _name, h = self.pic(key, cx + x, cy + y, w, tilt)
            bottom = max(bottom, y + h)
        lines = ["Everything on this card is yours to play with. Nothing you do here can break the guide."] + [""]
        lines += [f"{i}   {task}" for i, task in enumerate(tasks, 1)]
        note = self.text("\n".join(lines), cx, cy + bottom + 90, BODY_SCALE, group, CARD_W, name="Tasks",
                         color=[226, 229, 235], font_size=14)
        height = bottom + 90 + self.height(note) + 40
        self.h.run("set_attr", backdrop, "size", [CARD_W + 120, height + 50])
        self.lock(group)
        self.markers.append((f"Your turn: {title}", cx - 60, cy - 50, CARD_W + 120, height + 50))
        return height + 50

    # ---- Sections ---------------------------------------------------------------------------

    def section(self, title: str, subtitle: str, cards: list[dict], prefix: str, cx0: float, cy0: float,
                accent: tuple[int, int, int], sandbox: tuple[str, list[str], list[str]] | None,
                icon: bool = False) -> tuple[float, float]:
        """A banner over a grid of cards (and a "Your turn" card, if any); returns the section's right
        edge and bottom. ``icon`` puts the warning sign before the heading."""
        band = self.rect(cx0 - 260, cy0 - 120, 10, 10, None, f"{title} band", stroke_color=list(accent), thickness=6,
                         fill_color=list(accent), fill_opacity=0.07, corner_radius=80)
        heading_x = cx0
        if icon:
            self.lock(self.picture(self.warning, cx0, cy0 - 20, 300))
            heading_x = cx0 + 360
        heading = self.text(title.upper(), heading_x, cy0, 6.0, name=f"{title} heading", bold=True, font_size=32,
                            color=list(accent))
        gap = self.height(heading) + 30
        sub = self.text(subtitle, cx0, cy0 + gap, 3.0, name=f"{title} subtitle", font_size=14, color=[205, 210, 220],
                        width=PITCH_X * COLS - 400)
        y = cy0 + gap + self.height(sub) + 140
        row_h = 0.0
        bottom = y
        count = len(cards) + (1 if sandbox else 0)
        for i in range(count):
            col = i % COLS
            if col == 0 and i:
                y += row_h + 130
                row_h = 0.0
            cx = cx0 + col * PITCH_X
            if i < len(cards):
                used = self.card(cards[i], f"{prefix}{i + 1}", cx, y, accent)
            else:
                used = self.sandbox(*sandbox[:1], sandbox[1], sandbox[2], cx, y, accent)
            row_h = max(row_h, used)
            bottom = y + row_h
        right = cx0 + COLS * PITCH_X - 240
        self.h.run("set_attr", band, "size", [right - cx0 + 520, bottom - cy0 + 230])
        self.lock(band, heading, sub)
        return right, bottom
