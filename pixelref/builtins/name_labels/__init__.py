"""Name labels: a caption showing an image's, movie's or sequence's name.

A label is an ordinary text note (``annotation_text``) parented to the media
node and marked with sparse attributes, so saving, copy/paste, undo and
deleting the media need no extra code:

* ``name_label``: marks the note as a label.
* ``label_pinned`` (default on): the label sits in the bottom-left corner of
  the media and can't be dragged. Cropping or resizing the media moves it to
  the new corner. Unpinned, it stays where the user dragged it.
* ``label_sync``: the text the label last copied from the media's name. While
  the text still equals it, the label follows renames (and the new name of a
  duplicate). Editing the text by hand stops the syncing ("" = not syncing).

A new label's text is sized to 5% of the media's height, so it scales with the
media like any child. Its own scale and box width are the user's: nothing
resets them. All of this is enforced by a tidy hook at the end of each
command, inside its undo step.

Hiding a label only turns its ``visibility`` off, so showing it again brings
back its text, style and position; ``remove`` deletes it.

With **label new items** on (``name_labels.set_label_new(True)``, a checkbox in
the Canvas panel; a preference kept across boards), every image, movie or
sequence added from a file, a drop or a paste gets a label straight away.
Opening a scene, pasting copied items or duplicating doesn't.

``pxcmd.name_labels.show()`` / ``.hide()`` / ``.toggle()`` / ``.remove()`` /
``.pin()`` / ``.unpin()`` / ``.toggle_pin()`` / ``.reset()`` act on the
selection (or the nodes given). On the canvas, a selected item gets a label
button in its corner, and its label a lock button beside it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

from pixelref.plugin_api import Attr, CommandError, differs

MEDIA_TYPES = ("image", "movie", "sequence")
LABEL_TYPE = "annotation_text"
TEXT_HEIGHT = 0.05  # a new label's text height, as a share of the media's height
MARGIN = 0.015  # gap between a pinned label and the media's edges, as a share of its height
PX_TO_PT = 72.0 / 96.0  # font sizes are points; the media's height is in pixels

NAME_LABEL = Attr("name_label", "bool", False, hidden=True, omit_default=True,
                  doc="This note shows its parent's name")
LABEL_PINNED = Attr("label_pinned", "bool", True, hidden=True, omit_default=True,
                    doc="Keep the name label in its parent's bottom-left corner")
LABEL_SYNC = Attr("label_sync", "string", "", hidden=True, omit_default=True,
                  doc="Text last copied from the parent's name; '' = the text was edited and no longer follows it")

LABEL_STYLE: dict[str, Any] = {
    "bold": False, "italic": False, "underline": False, "strike": False,
    "color": [240, 240, 240], "background_color": [0, 0, 0, 150], "fill_style": "solid",
    "border_width": 0.0, "text_width": 0.0, "runs": None, "align": "left",
}


def label_style(media_height: float) -> dict[str, Any]:
    """The look of a new label on media ``media_height`` pixels tall: text 5% of that height."""
    font_size = round(max(4.0, TEXT_HEIGHT * media_height * PX_TO_PT), 1)
    return {**LABEL_STYLE, "font_size": font_size, "padding": round(font_size * 0.35, 1),
            "corner_radius": round(font_size * 0.25, 1)}


def estimate_size(attrs: dict[str, Any]) -> list[float]:
    """Label box [x, y, w, h] in its own units, without Qt (the UI swaps in a measured one)."""
    from pixelref.builtins.text import estimate_bounds

    return estimate_bounds(None, SimpleNamespace(attrs=attrs))


class NameLabels:
    def __init__(self, ctx: Any) -> None:
        self.session = ctx.session
        self.scene = ctx.session.scene
        self.measure: Callable[[dict[str, Any]], list[float]] = estimate_size

    # ---- Queries ---------------------------------------------------------------------------

    def is_media(self, name: str | None) -> bool:
        if not name or not self.scene.has(name):
            return False
        node_type = self.scene.get_node(name).node_type
        return any(self.session.types.inherits(node_type, t) for t in MEDIA_TYPES if self.session.types.get(t))

    def is_label(self, name: str) -> bool:
        node = self.scene.get_node(name)
        return node.node_type == LABEL_TYPE and bool(node.attrs.get("name_label", False))

    def labels_of(self, media: str) -> list[str]:
        return [c for c in self.scene.get_node(media).children if self.is_label(c)]

    def resolve(self, nodes: Any) -> list[str]:
        if nodes is None:
            names = self.session.selection.names
        else:
            names = [self.scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        return [n for n in names if self.scene.has(n)]

    def selected_media(self, nodes: Any) -> list[str]:
        return [n for n in self.resolve(nodes) if self.is_media(n)]

    def selected_labels(self, nodes: Any, include_media: bool = True) -> list[str]:
        """Labels in ``nodes``, plus the labels of media in ``nodes``."""
        found: list[str] = []
        for name in self.resolve(nodes):
            if self.is_label(name):
                found.append(name)
            elif include_media and self.is_media(name):
                found.extend(self.labels_of(name))
        return list(dict.fromkeys(found))

    # ---- Placement ------------------------------------------------------------------------

    def media_bounds(self, media: str) -> list[float]:
        """The media's content rectangle from its attributes (the canvas item may not be rebuilt yet)."""
        node = self.scene.get_node(media)
        fn = self.session.types.compute_fn(node.node_type, "local_bounds")
        rect = fn(self.session, node) if fn is not None else self.session.local_bounds(media)
        return [float(v) for v in rect] if rect else [0.0, 0.0, 0.0, 0.0]

    def placement(self, label: str) -> dict[str, Any]:
        """Transform values that keep a pinned ``label`` in the media's bottom-left corner.
        The label's own scale is left alone (it is the user's), but counted in."""
        node = self.scene.get_node(label)
        if not node.attrs.get("label_pinned", True):
            return {}
        x0, y0, _w, h = self.media_bounds(node.parent)
        lsx, lsy = (float(v) for v in node.attrs.get("scale", [1.0, 1.0]))
        lx, ly, lw, lh = (float(v) for v in self.measure(node.attrs))
        # The label's box in the media's space spans translate + scale * [lx, lx + lw] (flips included).
        xs = (lsx * lx, lsx * (lx + lw))
        ys = (lsy * ly, lsy * (ly + lh))
        margin = MARGIN * h
        return {"translate": [round(x0 + margin - min(xs), 3), round(y0 + h - margin - max(ys), 3)],
                "rotate": 0.0, "pivot": [0.0, 0.0]}

    def settle(self, label: str) -> None:
        """Bring one label in line: text follows the name while synced, then place it."""
        node = self.scene.get_node(label)
        media = node.parent
        attrs = node.attrs
        updates: dict[str, Any] = {}
        synced = str(attrs.get("label_sync", "") or "")
        if synced:
            if attrs.get("text", "") != synced or attrs.get("runs"):
                updates["label_sync"] = ""  # edited by hand: stop following the name
            elif synced != media:
                updates["text"] = media
                updates["label_sync"] = media
        if updates:
            self.scene.set_attrs(label, updates)
        place = self.placement(label)
        change = {k: v for k, v in place.items() if differs(attrs.get(k), v)}
        if change:
            self.scene.set_attrs(label, change)

    def tidy(self) -> None:
        """After every command: unmark stray labels, follow renames, re-pin."""
        for node in list(self.scene.nodes.values()):
            if node.node_type != LABEL_TYPE or not node.attrs.get("name_label", False):
                continue
            if not self.is_media(node.parent):
                # Pasted or moved away from its media: it's an ordinary note now.
                self.scene.set_attrs(node.name, {"name_label": False, "label_pinned": True, "label_sync": ""})
                continue
            self.settle(node.name)

    # ---- Commands ------------------------------------------------------------------------------

    def is_shown(self, label: str) -> bool:
        return bool(self.scene.get_node(label).attrs.get("visibility", 1))

    def shown_label(self, media: str) -> str | None:
        return next((label for label in self.labels_of(media) if self.is_shown(label)), None)

    def show(self, nodes: Any = None) -> list[str]:
        """Show the name label of the selected images, movies and sequences.
        A label hidden earlier comes back as it was (text, style, position)."""
        media = self.selected_media(nodes)
        if not media:
            raise CommandError("Select an image, movie or sequence")
        shown = []
        for name in media:
            labels = self.labels_of(name)
            if labels:
                for label in labels:
                    if not self.is_shown(label):
                        self.scene.set_attrs(label, {"visibility": 1})
                        shown.append(label)
                continue
            label = self.scene.unique_name(f"{name}_label")
            attrs = {**label_style(self.media_bounds(name)[3]), "text": name, "name_label": True,
                     "label_sync": name}
            self.scene.create_node(LABEL_TYPE, label, parent=name, attrs=attrs)
            self.settle(label)
            shown.append(label)
        return shown

    def hide(self, nodes: Any = None) -> list[str]:
        """Hide the name labels of the selection (or the selected labels); they keep your changes."""
        labels = [label for label in self.selected_labels(nodes) if self.is_shown(label)]
        if not labels:
            raise CommandError("Nothing selected shows a name label")
        for label in labels:
            self.scene.set_attrs(label, {"visibility": 0})
        self.session.selection.remove(labels)
        return labels

    def remove(self, nodes: Any = None) -> list[str]:
        """Delete the name labels of the selection (or the selected labels), changes and all."""
        labels = self.selected_labels(nodes)
        if not labels:
            raise CommandError("Nothing selected has a name label")
        for label in labels:
            if self.scene.has(label):
                self.scene.delete_node(label)
        self.session.selection.remove(labels)
        return labels

    def toggle(self, nodes: Any = None) -> list[str]:
        """Show labels where none is showing; when every selected item shows one, hide them."""
        media = self.selected_media(nodes)
        if any(self.shown_label(n) is None for n in media):
            return self.show(media)
        return self.hide(nodes)

    def toggle_pin(self, nodes: Any = None) -> list[str]:
        """Unlock pinned labels so they can be dragged, or lock them back to the corner."""
        labels = self.selected_labels(nodes)
        if not labels:
            raise CommandError("Select a name label (or what it labels)")
        pinned = not all(self.scene.get_node(label).attrs.get("label_pinned", True) for label in labels)
        return self.set_pinned(pinned, labels)

    def set_pinned(self, pinned: bool, nodes: Any = None) -> list[str]:
        labels = self.selected_labels(nodes)
        if not labels:
            raise CommandError("Select a name label (or what it labels)")
        for label in labels:
            if differs(self.scene.get_node(label).attrs.get("label_pinned", True), pinned):
                self.scene.set_attrs(label, {"label_pinned": pinned})
            self.settle(label)
        return labels

    def reset(self, nodes: Any = None) -> list[str]:
        """Back to the media's name, pinned in the corner (keeps the label's style)."""
        labels = self.selected_labels(nodes)
        if not labels:
            raise CommandError("Select a name label (or what it labels)")
        for label in labels:
            media = self.scene.get_node(label).parent
            self.scene.set_attrs(label, {"text": media, "runs": None, "label_sync": media, "label_pinned": True,
                                         "visibility": 1})
            self.settle(label)
        return labels


def activate(ctx: Any) -> None:
    ctx.extend_node_type(LABEL_TYPE, (NAME_LABEL, LABEL_PINNED, LABEL_SYNC))
    service = NameLabels(ctx)
    ctx.provide_service("name_labels", service)
    ctx.track(ctx.session.commands.on_tidy(service.tidy))

    def label_new() -> bool:
        """Whether newly added images, movies and sequences get a name label."""
        value = ctx.settings.value("label_new", False)
        return value is True or str(value).lower() in ("true", "1")

    def set_label_new(on: bool | None = None) -> bool:
        """Give every newly added image, movie and sequence a name label (a preference, for all boards).
        ``None`` switches it."""
        if on is None:
            on = not label_new()
        ctx.settings.setValue("label_new", bool(on))
        ctx.session.events.emit("name_labels.label_new", {"on": bool(on)})
        return bool(on)

    def media_added(event: Any) -> None:
        name = event.payload.get("name")
        if label_new() and service.is_media(name) and not service.labels_of(name):
            service.show([name])

    service.label_new = label_new  # type: ignore[attr-defined]
    ctx.track(ctx.session.scene.event_stream.on("media.added", media_added))
    ctx.command("label_new", label_new, undoable=False, echo=False)
    ctx.command("set_label_new", set_label_new, undoable=False)

    ctx.command("show", service.show, alias="show_name_label", label="Show name label")
    ctx.command("hide", service.hide, alias="hide_name_label", label="Hide name label")
    ctx.command("remove", service.remove, alias="remove_name_label", label="Delete name label")
    ctx.command("toggle", service.toggle, label="Toggle name label")
    ctx.command("toggle_pin", service.toggle_pin, label="Lock / unlock name label")

    def pin(nodes: Any = None) -> list[str]:
        """Pin name labels to their media's bottom-left corner (they can't be dragged)."""
        return service.set_pinned(True, nodes)

    def unpin(nodes: Any = None) -> list[str]:
        """Let name labels be dragged anywhere; they keep following their media."""
        return service.set_pinned(False, nodes)

    def labels_of(node: str) -> list[str]:
        """The name labels of a node."""
        return service.labels_of(ctx.session.scene.resolve(node))

    ctx.command("pin", pin, label="Pin name label")
    ctx.command("unpin", unpin, label="Unpin name label")
    ctx.command("reset", service.reset, label="Reset name label")
    ctx.command("labels_of", labels_of, undoable=False, echo=False)


def activate_ui(ui: Any) -> None:
    from .ui import activate_name_labels_ui

    activate_name_labels_ui(ui)
