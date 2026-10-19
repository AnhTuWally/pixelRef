"""The content and layout of examples/scenes/onboard.pxref (used by tools/gen_onboarding.py).

``Board`` lays the lessons out in a running pixelRef test harness: a welcome card, then a
"Basic Features" and an "Advanced Features" section, each a grid of cards. A card is a
group holding a screenshot, numbered callouts drawn on it with pixelRef's own rectangle
and Step tools, and the explanation under it.
"""

from __future__ import annotations

import base64
import json
import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtGui import QImage

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "scenes" / "animals.pxref"
SHOT_DIR = Path(tempfile.gettempdir()) / "pixelref_onboarding_shots"

CARD_W = 1500          # every screenshot is scaled to this width
PITCH_X = 1800         # card spacing, left to right
COLS = 3
DEMO_H = 820          # height of the live area on a card that has no screenshot
TITLE_SCALE = 1.6
BODY_SCALE = 2.3
BLUE = (61, 142, 240)
ORANGE = (255, 160, 60)
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
                "It keeps its annotations: add a note or a frame and it stays put while the picture plays."],
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


def asset_files(workdir: Path) -> dict[str, Path]:
    """The animal pictures embedded in animals.pxref, written out as files."""
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    by_node = {n["name"]: n["attrs"].get("asset") for n in data["nodes"] if n["type"] == "image"}
    files = {}
    for key, node in ANIMALS.items():
        asset = data["assets"][by_node[node]]
        path = workdir / f"{key}{Path(asset['name']).suffix}"
        path.write_bytes(base64.b64decode(asset["data"]))
        files[key] = path
    return files


class Board:
    """Builds the onboarding scene in a running test harness."""

    def __init__(self, h: Any) -> None:
        self.h = h
        self.work = Path(tempfile.mkdtemp(prefix="pixelref_onboarding_"))
        self.animals = asset_files(self.work)
        self.markers: list[tuple[str, float, float, float, float]] = []  # name, x, y, width, height

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
                accent: tuple[int, int, int], sandbox: tuple[str, list[str], list[str]]) -> tuple[float, float]:
        """A banner over a grid of cards; returns the section's right edge and bottom."""
        band = self.rect(cx0 - 260, cy0 - 120, 10, 10, None, f"{title} band", stroke_color=list(accent), thickness=6,
                         fill_color=list(accent), fill_opacity=0.07, corner_radius=80)
        heading = self.text(title.upper(), cx0, cy0, 6.0, name=f"{title} heading", bold=True, font_size=32,
                            color=list(accent))
        gap = self.height(heading) + 30
        sub = self.text(subtitle, cx0, cy0 + gap, 3.0, name=f"{title} subtitle", font_size=14, color=[205, 210, 220],
                        width=PITCH_X * COLS - 400)
        y = cy0 + gap + self.height(sub) + 140
        row_h = 0.0
        bottom = y
        count = len(cards) + 1
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
