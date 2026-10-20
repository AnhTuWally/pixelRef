"""Backdrops: group by placement.

A *backdrop* is a coloured, titled rectangle behind a set of nodes. Drop
something on it and it becomes part of the backdrop (its *contents*); drag it
off and it leaves; move the backdrop and the contents move too. Membership is
plain hierarchy: contents are children of the backdrop, so moving, hiding,
locking, copying and undo all work through the tree.

Any node can act as a backdrop. ``backdrop_mode`` (on every canvas node) picks
how: ``off``, ``inside`` (join when most of the node is inside), ``overlap``
(any overlap) or ``touch`` (overlap with a small margin); ``auto`` uses the
node type's policy. In *strict* mode (the default) only backdrops, and nodes
switched on, act as backdrops; in *free* mode rectangles and images do by
default too. Types contribute a :class:`BackdropPolicy` to ``backdrop.policies``.

Settling (deciding membership) only ever happens on an explicit command or a
canvas gesture, never from a plain ``set_attr``. Backdrops always draw behind
their non-backdrop siblings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pixelref.plugin_api import Attr, CommandError, NodeType, xform

BACKDROP = "backdrop"
MODES = ("auto", "off", "inside", "overlap", "touch")
ACTIVE_MODES = ("inside", "overlap", "touch")
DEFAULT_SIZE = [400.0, 300.0]


@dataclass
class BackdropPolicy:
    """How one node type behaves as a backdrop (a ``backdrop.policies`` contribution)."""

    node_type: str
    strict_mode: str = "off"  # the mode when ``backdrop_mode`` is auto, in strict hosting
    free_mode: str = "inside"  # … and in free hosting
    join_threshold: float = 0.5  # share of the node inside (``inside`` mode) needed to join
    leave_threshold: float = 0.3  # share needed to stay: lower than joining, so no flicker
    accepts: Callable[[Any, str, str], bool] | None = None  # (session, backdrop, candidate)
    priority: float = 0.0  # tie-break between equally small backdrops


@dataclass
class HostInfo:
    name: str
    mode: str
    policy: BackdropPolicy
    rect: list[float]
    order: int


def _area(rect: list[float]) -> float:
    return max(0.0, rect[2]) * max(0.0, rect[3])


def overlap_area(a: list[float], b: list[float]) -> float:
    width = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    height = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return width * height if width > 0 and height > 0 else 0.0


def score(mode: str, host: list[float], node: list[float], margin: float = 0.0) -> float:
    """0..1: how much ``node`` is on ``host`` for this mode (axis-aligned world rectangles)."""
    if mode == "inside":
        area = _area(node)
        if area <= 1e-9:  # a line or point: its position decides
            inside = host[0] <= node[0] <= host[0] + host[2] and host[1] <= node[1] <= host[1] + host[3]
            return 1.0 if inside else 0.0
        return overlap_area(host, node) / area
    pad = margin if mode == "touch" else 0.0
    hit = (node[0] < host[0] + host[2] + pad and node[0] + node[2] > host[0] - pad
           and node[1] < host[1] + host[3] + pad and node[1] + node[3] > host[1] - pad)
    return 1.0 if hit else 0.0


class Backdrops:
    """The membership engine (also offered as the ``backdrops`` service for the UI's previews)."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.session = ctx.session
        self.scene = ctx.session.scene
        self._seen_structure = -1
        self._policies: dict[str, BackdropPolicy | None] = {}
        self._policy_key: tuple[int, int] = (-1, -1)

    # ---- Configuration ------------------------------------------------------------------------

    @property
    def free(self) -> bool:
        return bool(self.ctx.settings.value("free", False, bool))

    def policy(self, node_type: str, contributed: list[BackdropPolicy] | None = None) -> BackdropPolicy | None:
        contributed = self.ctx.contributions("backdrop.policies") if contributed is None else contributed
        key = (self.session.types.generation, len(contributed))
        if key != self._policy_key:
            self._policies.clear()
            self._policy_key = key
        if node_type in self._policies:
            return self._policies[node_type]
        best: BackdropPolicy | None = None
        for policy in contributed:
            if policy.node_type == node_type:
                best = policy
                break
            if best is None and self.session.types.inherits(node_type, policy.node_type):
                best = policy
        self._policies[node_type] = best
        return best

    def mode(self, name: str | None, free: bool | None = None, contributed: list[BackdropPolicy] | None = None) -> str:
        """Effective mode of a node: off, inside, overlap or touch."""
        if name is None:
            return "off"
        node = self.scene.get_node(name)
        mode = node.attrs.get("backdrop_mode", "auto")
        if mode in ACTIVE_MODES or mode == "off":
            return mode
        policy = self.policy(node.node_type, contributed)
        if policy is None:
            return "off"
        return policy.free_mode if (self.free if free is None else free) else policy.strict_mode

    def is_backdrop(self, name: str) -> bool:
        return self.session.types.inherits(self.scene.get_node(name).node_type, BACKDROP)

    def eligible(self, name: str) -> bool:
        """Whether a node's membership is up to settling: loose, or directly inside a backdrop
        (what a plain group or an image's annotations hold stays where it is)."""
        node = self.scene.get_node(name)
        if not self.session.types.is_canvas(node.node_type):
            return False
        return node.parent is None or self.mode(node.parent) != "off"

    # ---- Decisions ----------------------------------------------------------------------------

    def host_infos(self) -> dict[str, HostInfo]:
        infos: dict[str, HostInfo] = {}
        free = self.free
        contributed = self.ctx.contributions("backdrop.policies")
        canvas_types: dict[str, bool] = {}
        for order, node in enumerate(self.scene.iter_nodes()):
            is_canvas = canvas_types.get(node.node_type)
            if is_canvas is None:
                is_canvas = canvas_types[node.node_type] = self.session.types.is_canvas(node.node_type)
            if not is_canvas:
                continue
            mode = self.mode(node.name, free, contributed)
            if mode == "off":
                continue
            policy = self.policy(node.node_type, contributed)
            rect = self.session.world_bounds(node.name)
            if policy is None or rect is None:
                continue
            infos[node.name] = HostInfo(node.name, mode, policy, rect, order)
        return infos

    def decide(self, name: str, rect: list[float] | None = None, margin: float = 0.0,
               infos: dict[str, HostInfo] | None = None) -> str | None:
        """The backdrop ``name`` belongs on at ``rect`` (its world bounds by default), or None."""
        node = self.scene.get_node(name)
        rect = rect if rect is not None else self.session.world_bounds(name)
        if rect is None:
            return node.parent
        infos = infos if infos is not None else self.host_infos()
        banned = set(self.scene.descendants(name))
        banned.add(name)
        area = _area(rect)
        best: str | None = None
        best_key: tuple[float, float, int] | None = None
        for host, info in infos.items():
            if host in banned or _area(info.rect) + 1e-6 < area:
                continue
            policy = info.policy
            if policy.accepts is not None and not policy.accepts(self.session, host, name):
                continue
            needed = policy.leave_threshold if host == node.parent else policy.join_threshold
            if score(info.mode, info.rect, rect, margin) < needed:
                continue
            key = (_area(info.rect), -policy.priority, -info.order)  # smallest, then priority, then front-most
            if best_key is None or key < best_key:
                best, best_key = host, key
        return best

    def overlapping(self, host: str, rect: list[float], margin: float, infos: dict[str, HostInfo]) -> list[str]:
        """Nodes that may need re-settling because a backdrop now covers ``rect``: whatever it
        overlaps, and what it holds already (so shrinking it releases things)."""
        found = list(self.scene.get_node(host).children)
        banned = set(self.scene.ancestors(host)) | {host}
        # Eligible nodes are loose ones and the direct contents of backdrops.
        pool = list(self.scene.children_of(None))
        for other_host in infos:
            if other_host not in banned and other_host != host:
                pool.extend(self.scene.get_node(other_host).children)
        for name in pool:
            if name in banned or name in found or self.scene.get_node(name).parent == host:
                continue
            other = self.session.world_bounds(name)
            if other is not None and self.session.types.is_canvas(self.scene.get_node(name).node_type)                     and score("touch", rect, other, margin):
                found.append(name)
        return found

    # ---- Changes ------------------------------------------------------------------------------

    def settle(self, names: list[str], margin: float = 0.0) -> list[str]:
        """Re-evaluate membership for ``names`` (and what any of them, as a backdrop, covers)."""
        names = self.scene.top_level(names)
        infos = self.host_infos()
        candidates = list(names)
        for name in names:
            if name in infos:
                candidates.extend(self.overlapping(name, infos[name].rect, margin, infos))
        changed: list[str] = []
        parents: set[str | None] = set()
        for name in dict.fromkeys(candidates):
            if not self.scene.has(name) or not self.eligible(name):
                continue
            if self.session.compute(name, "world_locked"):
                continue  # a locked node neither joins nor leaves
            before = self.scene.get_node(name).parent
            target = self.decide(name, None, margin, infos)
            if target == before:
                continue
            if target is None:
                grand = self.scene.get_node(before).parent
                self.ctx.run("move_nodes", [name], grand, before, "above")
            else:
                self.ctx.run("move_nodes", [name], target, None, "into")
            changed.append(name)
            parents.update((before, target))
        self.enforce_order(parents)
        return changed

    def enforce_order(self, parents: Any) -> None:
        """Backdrops draw behind their non-backdrop siblings (their own order is kept)."""
        for parent in parents:
            if parent is not None and not self.scene.has(parent):
                continue
            siblings = self.scene.children_of(parent)
            wanted = [n for n in siblings if self.is_backdrop(n)] + [n for n in siblings if not self.is_backdrop(n)]
            if wanted != siblings:
                self.scene.restack(wanted)

    def tidy(self) -> None:
        """After a command that changed the tree: put backdrops at the back again."""
        if self.scene.structure_revision == self._seen_structure:
            return
        parents = {node.parent for node in self.scene.iter_nodes()
                   if self.session.types.inherits(node.node_type, BACKDROP)}
        self.enforce_order(parents)
        self._seen_structure = self.scene.structure_revision

    def world_union(self, names: list[str]) -> list[float] | None:
        rects = [r for r in (self.session.world_bounds(n) for n in names) if r is not None]
        if not rects:
            return None
        left = min(r[0] for r in rects)
        top = min(r[1] for r in rects)
        return [left, top, max(r[0] + r[2] for r in rects) - left, max(r[1] + r[3] for r in rects) - top]


def activate(ctx: Any) -> None:
    session = ctx.session
    scene = session.scene
    ctx.register_node_type(NodeType(
        BACKDROP, inherits="canvas_node", label="Backdrop",
        compute={"local_bounds": lambda _s, node: [0.0, 0.0, *[float(v) for v in node.attrs.get("size", DEFAULT_SIZE)]]},
        attrs=(
            Attr("size", "float2", list(DEFAULT_SIZE), group="Backdrop", minimum=1.0),
            Attr("title", "string", "Backdrop", group="Backdrop"),
            Attr("title_size", "float", 18.0, minimum=4.0, soft_min=8.0, soft_max=60.0, step=1.0, precision=0,
                 unit="px", group="Backdrop"),
            Attr("color", "color", [90, 140, 200], group="Backdrop"),
            Attr("fill_opacity", "float", 0.18, minimum=0.0, maximum=1.0, step=0.05, precision=0, unit="%",
                 display_scale=100.0, group="Backdrop", label="Fill alpha"),
        ),
        quick_attrs=("color", "fill_opacity", "title_size"),
        doc="A titled frame that carries what sits on it"))
    ctx.extend_node_type("canvas_node", (
        Attr("backdrop_mode", "enum", "auto", choices=MODES, group="Backdrop", label="Act as backdrop", hidden=True,
             doc="auto: the node type decides; off; inside / overlap / touch: what it takes to join it"),))
    ctx.declare_extension_point("backdrop.policies", "BackdropPolicy: how a node type acts as a backdrop")
    ctx.contribute("backdrop.policies", BackdropPolicy(BACKDROP, strict_mode="inside", free_mode="inside"))
    ctx.contribute("backdrop.policies", BackdropPolicy("annotation_rect"))
    ctx.contribute("backdrop.policies", BackdropPolicy("image"))

    engine = Backdrops(ctx)
    ctx.provide_service("backdrops", engine)
    ctx.track(session.commands.on_tidy(engine.tidy))

    def selection_or(nodes: Any) -> list[str]:
        if nodes is None:
            return session.selection.names
        return [scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]

    def settle(nodes: Any = None, margin: float = 0.0) -> list[str]:
        """Re-evaluate which backdrop each node belongs on (default: the selection).

        A backdrop in the list also settles everything it covers. Bounds-based: rotated nodes count
        by their axis-aligned bounds. Returns the nodes that joined or left."""
        return engine.settle(selection_or(nodes), float(margin))

    def adopt(host: str, nodes: Any = None) -> list[str]:
        """Put nodes onto a backdrop (whatever the rules say), keeping them in place."""
        target = scene.resolve(host)
        names = [n for n in scene.top_level(selection_or(nodes)) if n != target]
        if not names:
            raise CommandError("Nothing to put on the backdrop")
        old = {scene.get_node(n).parent for n in names}
        ctx.run("move_nodes", names, target, None, "into")
        engine.enforce_order(old | {target})
        return names

    def release(nodes: Any = None) -> list[str]:
        """Take nodes off the backdrop they are on, keeping them in place."""
        released: list[str] = []
        parents: set[str | None] = set()
        for name in scene.top_level(selection_or(nodes)):
            parent = scene.get_node(name).parent
            if parent is None or engine.mode(parent) == "off":
                continue
            ctx.run("move_nodes", [name], scene.get_node(parent).parent, parent, "above")
            released.append(name)
            parents.add(scene.get_node(name).parent)
        if not released:
            raise CommandError("Nothing to release: select something that sits on a backdrop")
        engine.enforce_order(parents)
        return released

    def contents(host: str) -> list[str]:
        """What sits on a backdrop, front-most first."""
        return list(reversed(scene.children_of(scene.resolve(host))))

    def make(rect: list[float], parent: str | None, title: str, base_name: str, attrs: dict[str, Any]) -> str:
        """Create a backdrop covering the world rectangle ``rect`` under ``parent``."""
        inverse = xform.parent_world_matrix(scene, parent).inverted()
        local = inverse.map_rect(rect) if inverse is not None else rect
        values = {"translate": [round(local[0], 3), round(local[1], 3)],
                  "size": [round(max(1.0, local[2]), 3), round(max(1.0, local[3]), 3)],
                  "title": title, **attrs}
        final = scene.unique_name(base_name)
        scene.create_node(BACKDROP, final, parent=parent, attrs=values)
        return final

    def wrap(nodes: Any = None, padding: float = 24.0, title: str = "Backdrop", base_name: str = "Backdrop",
             **attrs: Any) -> str:
        """Create a backdrop around nodes (default: the selection) and put them on it."""
        names = scene.top_level(selection_or(nodes))
        rect = engine.world_union(names)
        if not names or rect is None:
            raise CommandError("Select something to put a backdrop around")
        pad = max(0.0, float(padding))
        header = float(attrs.get("title_size", 18.0)) * 1.8
        rect = [rect[0] - pad, rect[1] - pad - header, rect[2] + 2 * pad, rect[3] + 2 * pad + header]
        parents = {scene.get_node(n).parent for n in names}
        parent = parents.pop() if len(parents) == 1 else None
        backdrop = make(rect, parent, title, base_name, attrs)
        ctx.run("move_nodes", names, backdrop, None, "into")
        engine.enforce_order({parent})
        session.selection.set([backdrop])
        return backdrop

    def create(x: float, y: float, width: float, height: float, title: str = "Backdrop", base_name: str = "Backdrop",
               margin: float = 0.0, **attrs: Any) -> str:
        """Draw a backdrop over a world rectangle; everything inside it joins (and it joins a larger one)."""
        rect = [float(x), float(y), abs(float(width)), abs(float(height))]
        backdrop = make(rect, None, title, base_name, attrs)
        engine.settle([backdrop], float(margin))
        engine.enforce_order({scene.get_node(backdrop).parent})
        session.selection.set([backdrop])
        return backdrop

    def toggle(nodes: Any = None, mode: str = "inside", on: bool | None = None) -> list[str]:
        """Turn nodes (default: the selection) into backdrops, or back into plain nodes.

        ``on`` forces it (True also re-applies ``mode``); left out, it switches off if every node
        already acts as a backdrop and on otherwise. Switching on captures whatever the nodes
        already cover; switching off puts their contents back where they were, as loose nodes
        next to them."""
        if mode not in ACTIVE_MODES:
            raise CommandError("mode must be inside, overlap or touch")
        names = scene.top_level(selection_or(nodes))
        capable = [n for n in names if engine.policy(scene.get_node(n).node_type) is not None]
        if not capable:
            raise CommandError("Select a rectangle, image or backdrop")
        turn_on = (not all(engine.mode(n) != "off" for n in capable)) if on is None else bool(on)
        if not turn_on:
            acting = [n for n in capable if engine.mode(n) != "off"]
            for name in acting:
                children = list(reversed(scene.children_of(name)))
                if children:
                    ctx.run("move_nodes", children, scene.get_node(name).parent, name, "above")
                ctx.run("set_attr", f"{name}.backdrop_mode", "off")
            engine.enforce_order({scene.get_node(n).parent for n in acting})
            return acting
        for name in capable:
            if scene.get_node(name).attrs.get("backdrop_mode", "auto") != mode:
                ctx.run("set_attr", f"{name}.backdrop_mode", mode)
        engine.settle(capable)
        return capable

    def set_mode(mode: str = "strict") -> str:
        """Hosting mode: ``strict`` (backdrops and nodes switched on) or ``free`` (rectangles and images too)."""
        if mode not in ("strict", "free"):
            raise CommandError("mode must be 'strict' or 'free'")
        ctx.settings.setValue("free", mode == "free")
        return mode

    def toggle_mode() -> str:
        """Switch between strict and free hosting; returns the new mode."""
        return set_mode("strict" if engine.free else "free")

    def get_mode() -> str:
        """The current hosting mode."""
        return "free" if engine.free else "strict"

    ctx.command("settle", settle, label="Settle backdrops")
    ctx.command("adopt", adopt, label="Put on backdrop")
    ctx.command("release", release, label="Release from backdrop")
    ctx.command("contents", contents, undoable=False)
    ctx.command("wrap", wrap, label="Wrap in backdrop")
    ctx.command("create", create, label="Draw backdrop")
    ctx.command("toggle", toggle, label=lambda nodes=None, mode="inside": "Act as backdrop")
    ctx.command("set_mode", set_mode, undoable=False)
    ctx.command("toggle_mode", toggle_mode, undoable=False)
    ctx.command("get_mode", get_mode, undoable=False)


def activate_ui(ui: Any) -> None:
    from .ui import activate_backdrops_ui

    activate_backdrops_ui(ui)
