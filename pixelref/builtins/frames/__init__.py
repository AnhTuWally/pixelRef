"""Per-frame annotations: pin an annotation to one frame of an animated node.

Every canvas node gets a sparse ``frame`` attribute (an extension of
``canvas_node``): ``0`` (the default, so unused scenes don't change) means
"every frame"; ``n`` means frame ``n`` (numbered from 1, like the playback
plugin). It only has a meaning on a child of an animated node (a GIF, an
animated WebP or a movie with more than one frame): the child is drawn, and
can be clicked, only while its parent shows that frame. Anywhere else the
attribute is ignored and the node is shown as usual. A ``frame`` past the end
of a re-cut clip is kept, and the annotation simply never shows.

A **link** end can name a frame too: ``source_frame`` / ``target_frame`` (``0`` = the
whole element) on a link attached to an animation. A link made to or from a paused
animation anchors to the frame on show, clicking such an end (or selecting a pinned
note) jumps the animation there, and the filmstrip marks frames that carry notes
(amber) or links (blue). A link end re-attached to something that isn't an
animation forgets its frame.

**Ghost** (``ghost`` on the animation, a slider in the Attribute Editor, ``pxcmd.frames.set_ghost``):
``0`` shows a pinned annotation on its own frame only; ``n`` also shows it faded on the ``n``
frames before and after, fainter the further away, and the ghosts can't be clicked.

Nobody types the number. It is set by ordinary actions:

* Draw (rectangle, arrow, pen, step marker, ellipse) on an animation that is
  **paused**: the new node becomes a child of the animation, pinned to the
  frame on show. While it plays, drawing stays as it always was: an annotation
  on the board.
* ``Alt+F`` / *Pin to Current Frame*: pin the selection to the frame on show
  (``pxcmd.frames.pin``). An annotation that is not yet a child of an animation
  is adopted by the one beneath it (or the selected one).
* *Show on All Frames* (``pxcmd.frames.unpin``): back to ``0``.
* ``pxcmd.frames.copy_to("3-6")``: copies of the selection pinned to other frames.
* ``pxcmd.frames.link_frame(link, end, frame)`` / ``pxcmd.frames.clear_link_frame(link)``.
* ``pxcmd.frames.of(node)``: the frames that carry annotations, with counts.

Each change is one ordinary command, so undo, the console echo and scripts work
without extra code. See ``docs/design-per-frame-annotations.md``.
"""

from __future__ import annotations

from typing import Any

from pixelref.plugin_api import Attr

FRAME = Attr("frame", "int", 0, minimum=0, hidden=True, omit_default=True,
             doc="0 = every frame; n = only while the animated parent shows frame n")


GHOST = Attr("ghost", "int", 0, minimum=0, maximum=1000, hidden=True, omit_default=True,
             doc="On an animation: show pinned annotations this many frames before and after their own, fading out")


def _link_frame(end: str) -> Attr:
    label = "start" if end == "source" else "end"
    return Attr(f"{end}_frame", "int", 0, minimum=0, hidden=True, omit_default=True,
                doc=f"0 = the whole element; n = frame n of the animation the link's {label} is attached to")


LINK_FRAMES = (_link_frame("source"), _link_frame("target"))


def _link_frame(end: str) -> Attr:
    label = "start" if end == "source" else "end"
    return Attr(f"{end}_frame", "int", 0, minimum=0, hidden=True, omit_default=True,
                doc=f"0 = the whole element; n = frame n of the animation the link's {label} is attached to")


LINK_FRAMES = (_link_frame("source"), _link_frame("target"))


def activate(ctx: Any) -> None:
    ctx.extend_node_type("canvas_node", (FRAME, GHOST))
    ctx.extend_node_type("link", LINK_FRAMES)  # sparse: old files and the links plugin are untouched


def activate_ui(ui: Any) -> None:
    from .ui import activate_frames_ui

    activate_frames_ui(ui)
