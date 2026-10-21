"""Interactions: buttons and click actions that change the board.

Any canvas node can carry a **click action**: a list of steps saved in the scene, in sparse
attributes of ``canvas_node``:

* ``on_click``: the steps, ``[{"command": "set_attrs", "args": [<node id>, {"visibility": 1}],
  "back": [<steps that undo this one>]}, …]``. Steps name nodes by id, so renaming never
  breaks them, and a step whose node is gone is skipped;
* ``click_toggles`` (on by default): clicking again runs the steps' ``back`` steps, in
  reverse order, so a button that shows something hides it again;
* ``action_set``: items in the same set are "one at a time": clicking one turns the one
  that is on off first (tabs);
* ``action_on``: whether the action is on now (a button draws itself highlighted).

Only commands contributed to ``interactions.steps`` (a :class:`StepKind`) run from a click,
so a board someone sends you can't run arbitrary code.

Nobody types steps. They come from:

* **Demonstrate** (``interactions.demonstrate`` then ``interactions.finish``): arrange the
  board the way it should look after the click and press Done. What changed becomes steps
  that set absolute values (so a click gives the same result every time), the board goes
  back to how it was, and anything drawn meanwhile stays, hidden until the click. The whole
  demonstration is one undo step;
* **Connecting** an item to another (``interactions.connect``): Show, Hide, Go to frame,
  Play, Fly to… (``interactions.verbs``, a :class:`ClickVerb`);
* **Templates** made from the selection: tabs, a reveal button, a before / after button.

``interactions.click(node)`` runs a click as one undo step. On the canvas only a plain click on
a **locked** item runs its action: unlocked items are being edited, so a click selects them
(Run in the quick editor bar and Try it in the Attribute Editor run the action anyway).
"""

from __future__ import annotations

import json
import time
from typing import Any

from pixelref.plugin_api import CAP_COLORABLE, CAP_CONTENT, CAP_ERASABLE, Attr, CommandError, NodeType, SceneError
from pixelref.plugin_api.contracts import ClickVerb, StepKind

ACTION_ATTRS = ("on_click", "click_toggles", "action_set", "action_on")
STEPS_POINT = "interactions.steps"
VERBS_POINT = "interactions.verbs"
MAX_DEPTH = 8  # a click that clicks another item… stops here
LOG_SIZE = 200  # click log entries kept


def step(command: str, *args: Any, back: list[dict] | None = None, **kwargs: Any) -> dict[str, Any]:
    """One step: ``step("set_attrs", node_id, {"visibility": 1}, back=[…])``."""
    entry: dict[str, Any] = {"command": command, "args": list(args)}
    if kwargs:
        entry["kwargs"] = dict(kwargs)
    if back:
        entry["back"] = [dict(b) for b in back]
    return entry


def check_steps(value: Any, nested: bool = False) -> list[dict[str, Any]]:
    """Validator of ``on_click``: a list of ``{"command", "args", "kwargs", "back"}`` dicts."""
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise SceneError("A click action is a list of steps")
    steps = []
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("command"), str) or not entry["command"]:
            raise SceneError(f"Not a click step: {entry!r}")
        args, kwargs = entry.get("args", []), entry.get("kwargs", {})
        if not isinstance(args, list) or not isinstance(kwargs, dict):
            raise SceneError(f"Click step {entry['command']}: args must be a list and kwargs a dict")
        clean: dict[str, Any] = {"command": entry["command"], "args": list(args)}
        if kwargs:
            clean["kwargs"] = dict(kwargs)
        if entry.get("back") and not nested:
            clean["back"] = check_steps(entry["back"], nested=True)
        steps.append(clean)
    return steps


ACTION_SPECS = (
    Attr("on_click", "any", [], hidden=True, omit_default=True, transfer="never", validator=check_steps,
         doc="Click action: the steps a click runs (made by Demonstrate or by connecting items)"),
    Attr("click_toggles", "bool", True, hidden=True, omit_default=True, transfer="never",
         doc="Clicking again puts back what the click changed"),
    Attr("action_set", "string", "", hidden=True, omit_default=True, transfer="never",
         doc="Items in the same set are one at a time: clicking one turns the others off"),
    Attr("action_on", "bool", False, hidden=True, omit_default=True, transfer="never",
         doc="The click action is on (clicked once; clicking again puts things back)"),
)


def _button_bounds(_session: Any, node: Any) -> list[float]:
    width, height = node.attrs.get("size", [240.0, 72.0])
    return [0.0, 0.0, float(width), float(height)]


BUTTON_TYPE = NodeType(
    name="button",
    inherits="canvas_node",
    label="Button",
    color_attr="fill_color",
    capabilities=frozenset({CAP_CONTENT, CAP_ERASABLE, CAP_COLORABLE}),
    compute={"local_bounds": _button_bounds},
    attrs=(
        Attr("label", "string", "Button", group="Button"),
        Attr("size", "float2", [240.0, 72.0], group="Button"),
        Attr("font_size", "float", 26.0, minimum=4.0, maximum=400.0, soft_min=8.0, soft_max=80.0, step=1.0,
             precision=0, unit="px", group="Button"),
        Attr("corner_radius", "float", 18.0, minimum=0.0, soft_min=0.0, soft_max=60.0, step=1.0, precision=0,
             unit="px", group="Button"),
        Attr("fill_color", "color", [61, 142, 240], group="Look", label="Fill"),
        Attr("text_color", "color", [255, 255, 255], group="Look", label="Text"),
        Attr("active_color", "color", [110, 231, 160], group="Look", label="When on",
             doc="Outline and dot shown while the button's action is on"),
    ),
    quick_attrs=("label", "fill_color", "text_color"),
    doc="A clickable button: its click action shows, hides or changes other items",
)

# ---- Sentences ----------------------------------------------------------------------------------


def node_label(session: Any, ref: Any) -> str:
    """A node's name for a reference (an id or a name); "(deleted item)" when it's gone."""
    scene = session.scene
    if isinstance(ref, list):
        return ", ".join(node_label(session, r) for r in ref) or "(nothing)"
    ref = str(ref)
    if ref in scene.nodes:
        return ref
    return scene.name_of_id(ref) or "(deleted item)"


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def describe_set_attrs(session: Any, args: list, kwargs: dict) -> str:
    who = node_label(session, args[0]) if args else "(nothing)"
    values = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
    node = session.scene.nodes.get(session.scene.name_of_id(str(args[0])) or str(args[0])) if args else None
    phrases: list[tuple[str, str]] = []  # (verb before the name, rest after it)
    for key, value in sorted(values.items(), key=lambda kv: kv[0] != "visibility"):  # show / hide first
        if key == "visibility":
            phrases.append(("Show" if value else "Hide", ""))
        elif key == "opacity":
            phrases.append(("Fade", f"to {round(float(value) * 100)}%"))
        elif key == "translate":
            phrases.append(("Move", ""))
        elif key == "rotate":
            phrases.append(("Rotate", f"to {float(value):g}°"))
        elif key in ("scale", "size"):
            phrases.append(("Resize", ""))
        else:
            spec = session.scene.attr_spec(node, key) if node is not None else None
            label = spec.nice_name.lower() if spec is not None else key.replace("_", " ")
            phrases.append(("Change", f"{label}"))
    unique: list[tuple[str, str]] = []
    for phrase in phrases:
        if phrase not in unique:
            unique.append(phrase)
    if not unique:
        return f"Change nothing on {who}"
    if len(unique) == 1:
        verb, rest = unique[0]
        return f"Change {rest} of {who}" if verb == "Change" else f"{verb} {who}" + (f" {rest}" if rest else "")
    return f"{who}: " + _join([f"{v.lower()} {r}".strip() for v, r in unique])


def _arg(args: list, kwargs: dict, index: int, key: str, default: Any = None) -> Any:
    if key in kwargs:
        return kwargs[key]
    return args[index] if len(args) > index else default


def _refs(value: Any) -> list[str]:
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]


BUILTIN_STEPS = (
    StepKind("set_attrs", describe_set_attrs, lambda a, k: _refs(_arg(a, k, 0, "node")), icon="eye"),
    StepKind("playback.go_to_frame",
             lambda s, a, k: f"Go to frame {_arg(a, k, 0, 'frame')} · {node_label(s, _arg(a, k, 1, 'nodes'))}",
             lambda a, k: _refs(_arg(a, k, 1, "nodes")), icon="frame_next"),
    StepKind("playback.play", lambda s, a, k: f"Play {node_label(s, _arg(a, k, 0, 'nodes'))}",
             lambda a, k: _refs(_arg(a, k, 0, "nodes")), icon="play"),
    StepKind("playback.pause", lambda s, a, k: f"Pause {node_label(s, _arg(a, k, 0, 'nodes'))}",
             lambda a, k: _refs(_arg(a, k, 0, "nodes")), icon="pause"),
    StepKind("views.look_through", lambda s, a, k: f"Fly to view marker {node_label(s, _arg(a, k, 0, 'camera'))}",
             lambda a, k: _refs(_arg(a, k, 0, "camera")), icon="camera"),
    StepKind("interactions.fly_to", lambda s, a, k: f"Fly to {node_label(s, _arg(a, k, 0, 'node'))}",
             lambda a, k: _refs(_arg(a, k, 0, "node")), icon="camera"),
    StepKind("interactions.click", lambda s, a, k: f"Click {node_label(s, _arg(a, k, 0, 'node'))}",
             lambda a, k: _refs(_arg(a, k, 0, "node")), icon="play"),
)

# ---- Verbs: what connecting an item offers ----------------------------------------------------------


def _visible(session: Any, name: str) -> int:
    return 1 if bool(session.scene.get_node(name).attrs.get("visibility", 1)) else 0


def _animated(session: Any, name: str) -> Any:
    playback = session.services.get("playback")
    return playback.item(name) if playback is not None else None


def _has_command(session: Any, name: str) -> bool:
    return session.commands.exists(name)


def _show(session: Any, name: str) -> tuple[list, list]:
    node_id = session.scene.get_node(name).id
    return [step("set_attrs", node_id, {"visibility": 1}, back=[step("set_attrs", node_id, {"visibility": 0})])], []


def _hide(session: Any, name: str) -> tuple[list, list]:
    node_id = session.scene.get_node(name).id
    return [step("set_attrs", node_id, {"visibility": 0}, back=[step("set_attrs", node_id, {"visibility": 1})])], []


def _go_to_frame(session: Any, name: str) -> tuple[list, list]:
    node_id = session.scene.get_node(name).id
    frame = _animated(session, name).current_frame()
    return [step("playback.go_to_frame", frame, [node_id], back=[step("playback.play", [node_id])])], []


def _play(session: Any, name: str) -> tuple[list, list]:
    node_id = session.scene.get_node(name).id
    return [step("playback.play", [node_id], back=[step("playback.pause", [node_id])])], []


def _pause(session: Any, name: str) -> tuple[list, list]:
    node_id = session.scene.get_node(name).id
    return [step("playback.pause", [node_id], back=[step("playback.play", [node_id])])], []


def _fly_to(session: Any, name: str) -> tuple[list, list]:
    return [step("interactions.fly_to", session.scene.get_node(name).id)], []


def _click_other(session: Any, name: str) -> tuple[list, list]:
    return [step("interactions.click", session.scene.get_node(name).id)], []


def _hide_now(session: Any, name: str) -> None:
    session.scene.set_attr(name, "visibility", 0)


BUILTIN_VERBS = (
    ClickVerb("show", "Show it (hidden until the click)", lambda s, n: True, _show, prepare=_hide_now, order=10),
    ClickVerb("hide", "Hide it", lambda s, n: _visible(s, n) == 1, _hide, order=20),
    ClickVerb("frame", "Go to the frame on show",
              lambda s, n: _animated(s, n) is not None and _has_command(s, "playback.go_to_frame"), _go_to_frame,
              order=30),
    ClickVerb("play", "Play it", lambda s, n: _animated(s, n) is not None and _has_command(s, "playback.play"), _play,
              order=31),
    ClickVerb("pause", "Pause it", lambda s, n: _animated(s, n) is not None and _has_command(s, "playback.pause"),
              _pause, order=32),
    ClickVerb("fly_to", "Fly the view to it", lambda s, n: _has_command(s, "interactions.fly_to"), _fly_to, order=40),
    ClickVerb("click", "Run its click action",
              lambda s, n: bool(s.scene.get_node(n).attrs.get("on_click")), _click_other, order=50),
)

# ---- Demonstrate: before / after → steps ----------------------------------------------------------

_MISSING = object()


def _attr_default(session: Any, node_type: str, key: str) -> Any:
    spec = session.types.attr(node_type, key) if node_type in session.types else None
    return spec.default_value() if spec is not None else None


def demonstration_steps(session: Any, before_text: str, after_text: str) -> tuple[list[dict], list[dict]]:
    """The steps that turn the board in ``before_text`` into the one in ``after_text`` (undo
    snapshots), and the node entries made in between (kept, hidden until the click).

    Each changed node gets one ``set_attrs`` step with absolute values; a deleted node is
    hidden instead; a GIF paused on another frame (or started) gets a playback step. Every
    step's ``back`` puts the old values back. Reordering and reparenting aren't recorded.
    """
    before, after = json.loads(before_text), json.loads(after_text)
    old = {entry["id"]: entry for entry in before.get("nodes", [])}
    new = {entry["id"]: entry for entry in after.get("nodes", [])}

    def tracked(entry: dict) -> bool:
        node_type = entry.get("type", "")
        return node_type != "camera" and node_type in session.types and session.types.is_canvas(node_type)

    steps: list[dict] = []
    created: list[dict] = []
    for entry in after.get("nodes", []):
        node_id = entry["id"]
        if not tracked(entry):
            continue
        previous = old.get(node_id)
        if previous is None:
            created.append(entry)
            if entry.get("parent") not in {e["id"] for e in created[:-1]}:
                visible = 1 if entry["attrs"].get("visibility", 1) else 0
                steps.append(step("set_attrs", node_id, {"visibility": visible},
                                  back=[step("set_attrs", node_id, {"visibility": 0})]))
            continue
        forward: dict[str, Any] = {}
        backward: dict[str, Any] = {}
        a_attrs, b_attrs = previous.get("attrs", {}), entry.get("attrs", {})
        for key in list(dict.fromkeys([*a_attrs, *b_attrs])):
            if key in ACTION_ATTRS:
                continue
            a, b = a_attrs.get(key, _MISSING), b_attrs.get(key, _MISSING)
            if a == b:
                continue
            forward[key] = b if b is not _MISSING else _attr_default(session, entry["type"], key)
            backward[key] = a if a is not _MISSING else _attr_default(session, entry["type"], key)
        if forward:
            steps.append(step("set_attrs", node_id, forward, back=[step("set_attrs", node_id, backward)]))
    for entry in before.get("nodes", []):
        node_id = entry["id"]
        if node_id in new or not tracked(entry) or (entry.get("parent") in old and entry.get("parent") not in new):
            continue  # still there, untracked, or inside a deleted parent (hiding the parent is enough)
        visible = 1 if entry["attrs"].get("visibility", 1) else 0
        steps.append(step("set_attrs", node_id, {"visibility": 0},
                          back=[step("set_attrs", node_id, {"visibility": visible})]))
    paused_before = (before.get("plugins") or {}).get("playback") or {}
    paused_after = (after.get("plugins") or {}).get("playback") or {}
    for entry in after.get("nodes", []):
        node_id = entry["id"]
        a, b = paused_before.get(node_id), paused_after.get(node_id)
        if a == b or node_id not in old:
            continue

        def playback_step(state: Any) -> dict:
            if isinstance(state, dict):
                return step("playback.go_to_frame", int(state.get("frame", 1)), [node_id])
            return step("playback.play", [node_id])

        forward_step = playback_step(b)
        forward_step["back"] = [playback_step(a)]
        steps.append(forward_step)
    return steps, created


DIFFED_COMMANDS = ("set_attrs", "playback.go_to_frame", "playback.play")


class Interactions:
    """The headless half: running clicks, verbs, demonstrations (service ``interactions``)."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.session = ctx.session
        self.demo: dict[str, Any] | None = None  # the demonstration under way
        self._depth = 0
        # What clicks did, newest last (this session only, never saved), and each item's last run.
        self.log: list[dict[str, Any]] = []
        self.last: dict[str, dict[str, Any]] = {}

    # ---- Lookups -------------------------------------------------------------------------------

    @property
    def scene(self) -> Any:
        return self.session.scene

    def name(self, node: Any) -> str:
        if node is None:
            selected = self.session.selection.names
            if not selected:
                raise CommandError("Select an item first")
            return selected[0]
        return self.scene.resolve(str(node))

    def kinds(self) -> dict[str, StepKind]:
        return {kind.command: kind for kind in self.ctx.contributions(STEPS_POINT)}

    def verbs(self) -> list[ClickVerb]:
        return sorted(self.ctx.contributions(VERBS_POINT), key=lambda verb: verb.order)

    def steps(self, node: Any) -> list[dict]:
        return list(self.scene.get_node(self.name(node)).attrs.get("on_click") or [])

    def describe_step(self, entry: dict) -> str:
        kind = self.kinds().get(entry.get("command", ""))
        args, kwargs = list(entry.get("args", [])), dict(entry.get("kwargs", {}))
        if kind is None:
            return f"{entry.get('command')} (not allowed in click actions: skipped)"
        try:
            return kind.describe(self.session, args, kwargs)
        except Exception:  # noqa: BLE001  a sentence must never break the panel
            return entry.get("command", "")

    def targets(self, entry: dict) -> list[str]:
        kind = self.kinds().get(entry.get("command", ""))
        if kind is None:
            return []
        try:
            refs = kind.targets(list(entry.get("args", [])), dict(entry.get("kwargs", {})))
        except Exception:  # noqa: BLE001
            return []
        return [self.scene.name_of_id(r) or r for r in refs]

    def toggles(self, node: Any) -> bool:
        attrs = self.scene.get_node(self.name(node)).attrs
        return bool(attrs.get("click_toggles", True)) and any(s.get("back") for s in attrs.get("on_click") or [])

    def set_members(self, set_name: str) -> list[str]:
        return [n.name for n in self.scene.iter_nodes()
                if set_name and n.attrs.get("action_set") == set_name and n.attrs.get("on_click")]

    # ---- Running -------------------------------------------------------------------------------

    def blocker(self, entry: dict) -> str | None:
        """Why a step can't run now (None: it can): not allowed, its command is missing, or
        the item it acts on is gone."""
        command = entry.get("command", "")
        kind = self.kinds().get(command)
        if kind is None:
            return "this command isn't allowed in click actions"
        if not self.session.commands.exists(command):
            return "the plugin it needs is off (or this isn't the desktop app)"
        try:
            refs = kind.targets(list(entry.get("args", [])), dict(entry.get("kwargs", {})))
        except Exception:  # noqa: BLE001
            refs = []
        if any(r not in self.scene.nodes and r not in self.scene.by_id for r in refs):
            return "the item it acts on was deleted"
        return None

    def run_steps(self, steps: list[dict]) -> tuple[int, list[dict[str, Any]]]:
        """Run steps; returns how many ran and the problems: [{"step", "reason", "error"}].
        ``error`` is True when the step ran and failed, False when it was skipped."""
        ran, problems = 0, []
        for entry in steps:
            reason = self.blocker(entry)
            if reason is not None:
                problems.append({"step": self.describe_step(entry), "reason": reason, "error": False})
                continue
            try:
                self.session.commands.execute(entry["command"], *entry.get("args", []), **entry.get("kwargs", {}))
                ran += 1
            except Exception as exc:  # noqa: BLE001  one broken step doesn't stop the others
                problems.append({"step": self.describe_step(entry), "reason": str(exc) or type(exc).__name__,
                                 "error": True})
        return ran, problems

    def check(self, node: Any) -> list[dict[str, Any]]:
        """The steps of an item's action that can't run now: [{"index", "step", "reason"}]."""
        problems = []
        for index, entry in enumerate(self.steps(node)):
            reason = self.blocker(entry)
            if reason is None:
                reason = next((self.blocker(b) for b in entry.get("back") or [] if self.blocker(b)), None)
            if reason is not None:
                problems.append({"index": index, "step": self.describe_step(entry), "reason": reason})
        return problems

    # ---- The log -------------------------------------------------------------------------------

    def record(self, name: str, on: bool, ran: int, problems: list[dict]) -> dict[str, Any]:
        node = self.scene.get_node(name)
        entry = {"time": time.strftime("%H:%M:%S"), "node": name, "id": node.id, "on": on, "ran": ran,
                 "problems": problems, "status": "ok" if not problems else ("failed" if not ran else "partial"),
                 "action": repr(node.attrs.get("on_click"))}
        self.log.append(entry)
        del self.log[:-LOG_SIZE]
        self.last[node.id] = entry
        self.session.events.emit("interactions.ran", {"entry": entry, "nested": self._depth > 0})
        return entry

    def last_run(self, node: Any) -> dict[str, Any] | None:
        """The item's last run, unless its action changed since."""
        node = self.scene.get_node(self.name(node))
        entry = self.last.get(node.id)
        return entry if entry is not None and entry["action"] == repr(node.attrs.get("on_click")) else None

    def status(self, node: Any) -> str:
        """"ok", "partial" / "failed" (its last run had problems), "broken" (a step can't run
        now), or "" (no action)."""
        name = self.name(node)
        if not self.scene.get_node(name).attrs.get("on_click"):
            return ""
        last = self.last_run(name)
        if last is not None and last["status"] != "ok":
            return last["status"]
        return "broken" if self.check(name) else "ok"

    @staticmethod
    def back_steps(steps: list[dict]) -> list[dict]:
        return [b for entry in reversed(steps) for b in entry.get("back") or []]

    def click(self, node: Any = None, back: bool | None = None) -> dict[str, Any]:
        name = self.name(node)
        attrs = self.scene.get_node(name).attrs
        steps = list(attrs.get("on_click") or [])
        if not steps:
            raise CommandError(f"'{name}' has no click action")
        if self._depth >= MAX_DEPTH:
            raise CommandError("Click actions click each other in a loop")
        toggles = self.toggles(name)
        set_name = str(attrs.get("action_set") or "")
        turn_off = bool(back) if back is not None else (toggles and bool(attrs.get("action_on", 0)))
        self._depth += 1
        try:
            if turn_off:
                ran, problems = self.run_steps(self.back_steps(steps))
                on = False
            else:
                ran, problems = 0, []
                for other in self.set_members(set_name):
                    if other != name and self.scene.get_node(other).attrs.get("action_on"):
                        if self.toggles(other):
                            r, p = self.run_steps(self.back_steps(self.steps(other)))
                            ran, problems = ran + r, problems + p
                        self.scene.set_attr(other, "action_on", False)
                r, p = self.run_steps(steps)
                ran, problems = ran + r, problems + p
                on = toggles or bool(set_name)
            if self.scene.has(name):
                self.scene.set_attr(name, "action_on", on)
        finally:
            self._depth -= 1
        entry = self.record(name, on, ran, problems) if self.scene.has(name) else None
        return {"node": name, "on": on, "ran": ran, "problems": problems,
                "status": entry["status"] if entry else ("failed" if problems and not ran else "ok")}

    # ---- Editing -------------------------------------------------------------------------------

    def check_allowed(self, steps: list[dict]) -> list[dict]:
        steps = check_steps(steps)
        kinds = self.kinds()
        for entry in steps:
            for command in [entry["command"]] + [b["command"] for b in entry.get("back", [])]:
                if command not in kinds:
                    raise CommandError(f"'{command}' can't be used in a click action (allowed: "
                                       f"{', '.join(sorted(kinds))})")
        return steps

    def connect(self, node: Any, target: Any, verb: str = "show") -> list[dict]:
        name, target_name = self.name(node), self.scene.resolve(str(target))
        found = next((v for v in self.verbs() if v.id == verb), None)
        if found is None:
            raise CommandError(f"No verb '{verb}' (there are: {', '.join(v.id for v in self.verbs())})")
        if not found.applies(self.session, target_name):
            raise CommandError(f"'{found.label}' doesn't apply to {target_name}")
        new_steps, _ = found.build(self.session, target_name)
        new_steps = self.check_allowed(new_steps)
        if found.prepare is not None:
            found.prepare(self.session, target_name)
        self.scene.set_attrs(name, {"on_click": self.steps(name) + new_steps, "action_on": False})
        return new_steps

    # ---- Demonstrate ----------------------------------------------------------------------------

    def demonstrate(self, node: Any = None) -> str:
        if self.demo is not None:
            raise CommandError(f"Already demonstrating the click of {self.demo_name()}: press Done or Cancel")
        name = self.name(node)
        if not self.session.types.is_canvas(self.scene.get_node(name).node_type):
            raise CommandError(f"'{name}' isn't on the canvas")
        if self.scene.get_node(name).attrs.get("action_on"):
            self.session.run("interactions.click", name, back=True)  # start from "off"
        steps = self.steps(name)
        self.demo = {"node": self.scene.get_node(name).id, "before": self.session.dumps(),
                     "undo": len(self.session.undo.stack), "old_steps": steps}
        if steps:  # demonstrate again: start from the action's result
            self.run_steps(steps)
        self.session.events.emit("interactions.demonstrating", {"node": name})
        return name

    def demo_name(self) -> str | None:
        return self.scene.name_of_id(self.demo["node"]) if self.demo else None

    def finish(self, cancel: bool = False, view: dict[str, Any] | None = None) -> dict[str, Any]:
        demo, self.demo = self.demo, None
        if demo is None:
            raise CommandError("Not demonstrating: choose Demonstrate Click Action first")
        session, scene, undo = self.session, self.scene, self.session.undo
        after = session.dumps()
        result: dict[str, Any] = {"node": None, "steps": 0, "cancelled": bool(cancel)}
        try:
            steps, created = ([], []) if cancel else demonstration_steps(session, demo["before"], after)
            if not undo.in_chunk:
                undo.stack.truncate(demo["undo"])  # the demonstration's own steps
            session.loads(demo["before"])
            name = scene.name_of_id(demo["node"])
            result["node"] = name
            session.commands.changed_document = True
            if cancel or name is None:
                return result
            hidden = {entry["id"] for entry in created if entry.get("parent") not in {e["id"] for e in created}}
            for entry in created:
                parent = scene.name_of_id(entry["parent"]) if entry.get("parent") else None
                attrs = dict(entry.get("attrs", {}))
                if entry["id"] in hidden:
                    attrs["visibility"] = 0
                dynamic = {k: Attr.from_dict(k, v) for k, v in (entry.get("dynamic") or {}).items()}
                scene.create_node(entry["type"], scene.unique_name(entry["name"]), parent=parent, attrs=attrs,
                                  node_id=entry["id"], strict=False, dynamic=dynamic)
            kept = [s for s in demo["old_steps"] if s.get("command") not in DIFFED_COMMANDS
                    and s.get("command") != "views.look_through"]
            if view is not None and "camera" in session.types:
                camera = scene.unique_name(f"{name} view")
                node = scene.create_node("camera", camera, attrs={
                    "translate": list(view.get("center", [0.0, 0.0])), "center": list(view.get("center", [0.0, 0.0])),
                    "zoom": float(view.get("zoom", 1.0)), "rotation": float(view.get("rotation", 0.0)),
                    "visibility": 0})
                kept.append(step("views.look_through", node.id))
            final = steps + kept
            result["steps"] = len(final)
            result["created"] = [scene.name_of_id(entry["id"]) for entry in created]
            if final:
                scene.set_attrs(name, {"on_click": final, "action_on": False})
            if not undo.in_chunk and (final or created):
                undo.record(demo["before"], f"Click action of {name}")
            session.selection.set([name])
            return result
        finally:
            session.events.emit("interactions.demonstrating", {"node": None})


def activate(ctx: Any) -> None:
    ctx.extend_node_type("canvas_node", ACTION_SPECS)
    ctx.register_node_type(BUTTON_TYPE)
    ctx.declare_extension_point(STEPS_POINT, "StepKind: commands a click action may run, and their sentences")
    ctx.declare_extension_point(VERBS_POINT, "ClickVerb: what connecting a button to an item offers")
    for kind in BUILTIN_STEPS:
        ctx.contribute(STEPS_POINT, kind)
    for verb in BUILTIN_VERBS:
        ctx.contribute(VERBS_POINT, verb)
    api = Interactions(ctx)
    ctx.provide_service("interactions", api)
    session, scene = ctx.session, ctx.session.scene

    def label_of(node: Any = None, *_a: Any, **_k: Any) -> str:
        return node_label(session, node) if node is not None else "selection"

    def click(node: Any = None, back: bool | None = None) -> dict[str, Any]:
        """Run an item's click action (``back=True``: put back what it changed). One undo step."""
        return api.click(node, back)

    def check(node: Any = None) -> list[dict[str, Any]]:
        """The steps of an item's click action that can't run now: [{"index", "step", "reason"}]."""
        return api.check(node)

    def status(node: Any = None) -> str:
        """An item's click action: "ok", "partial" or "failed" (its last run had problems),
        "broken" (a step can't run now), or "" (it has none)."""
        return api.status(node)

    def log(node: Any = None, limit: int = 50, problems_only: bool = False) -> list[dict[str, Any]]:
        """What clicks did this session, newest last: time, node, on, ran, problems, status."""
        node_id = scene.get_node(api.name(node)).id if node is not None else None
        entries = [e for e in api.log if (node_id is None or e["id"] == node_id)
                   and (not problems_only or e["problems"])]
        return [{k: v for k, v in e.items() if k != "action"} for e in entries[-int(limit):]]

    def clear_log() -> None:
        """Forget the click log (and the failed state it gives items)."""
        api.log.clear()
        api.last.clear()
        session.events.emit("interactions.ran", {"entry": None})

    def set_action(node: Any, steps: list, toggles: bool | None = None, action_set: str | None = None) -> str:
        """Replace an item's click action with ``steps`` (see the plugin's docs for the format)."""
        name = api.name(node)
        values: dict[str, Any] = {"on_click": api.check_allowed(steps), "action_on": False}
        if toggles is not None:
            values["click_toggles"] = bool(toggles)
        if action_set is not None:
            values["action_set"] = str(action_set)
        scene.set_attrs(name, values)
        return name

    def add_step(node: Any, command: str, *args: Any, back: list | None = None, **kwargs: Any) -> int:
        """Append one step to an item's click action; returns how many steps it has."""
        name = api.name(node)
        steps = api.steps(name) + api.check_allowed([step(command, *args, back=back, **kwargs)])
        scene.set_attrs(name, {"on_click": steps, "action_on": False})
        return len(steps)

    def remove_step(index: int, node: Any = None) -> int:
        """Remove step ``index`` (from 0) of an item's click action; returns how many are left."""
        name = api.name(node)
        steps = api.steps(name)
        if not 0 <= int(index) < len(steps):
            raise CommandError(f"'{name}' has no step {index}")
        del steps[int(index)]
        scene.set_attrs(name, {"on_click": steps, "action_on": False})
        return len(steps)

    def clear(nodes: Any = None) -> list[str]:
        """Remove the click actions of items (default: the selection)."""
        names = session.selection.names if nodes is None else [scene.resolve(str(n)) for n in _refs(nodes)]
        for name in names:
            scene.set_attrs(name, {key: spec.default_value() for key, spec in zip(ACTION_ATTRS, ACTION_SPECS)})
        return names

    def set_toggles(toggles: bool, nodes: Any = None) -> list[str]:
        """Whether clicking again puts back what the click changed."""
        names = session.selection.names if nodes is None else [scene.resolve(str(n)) for n in _refs(nodes)]
        for name in names:
            scene.set_attr(name, "click_toggles", bool(toggles))
        return names

    def make_set(nodes: Any = None, name: str | None = None) -> str:
        """Make items one-at-a-time: clicking one turns the others off (default: the selection)."""
        names = session.selection.names if nodes is None else [scene.resolve(str(n)) for n in _refs(nodes)]
        names = [n for n in names if scene.get_node(n).attrs.get("on_click")]
        if not names:
            raise CommandError("Select items that have click actions")
        if name is None:
            used = {str(n.attrs.get("action_set")) for n in scene.iter_nodes() if n.attrs.get("action_set")}
            name = next(f"Set {i}" for i in range(1, len(used) + 2) if f"Set {i}" not in used)
        for member in names:
            scene.set_attrs(member, {"action_set": str(name), "action_on": False})
        return str(name)

    def connect(node: Any, target: Any, verb: str = "show") -> int:
        """Connect an item to another: its click does ``verb`` to it (show, hide, frame, play, pause,
        fly_to, click). Returns how many steps the action now has."""
        api.connect(node, target, verb)
        return len(api.steps(node))

    def verbs(target: Any) -> list[dict[str, str]]:
        """What connecting to ``target`` offers: [{"id", "label"}]."""
        name = scene.resolve(str(target))
        return [{"id": v.id, "label": v.label} for v in api.verbs() if v.applies(session, name)]

    def describe(node: Any = None) -> list[str]:
        """An item's click action as sentences ("Show answer", "Go to frame 5 · walk")."""
        return [api.describe_step(entry) for entry in api.steps(node)]

    def demonstrate(node: Any = None) -> str:
        """Start demonstrating an item's click: change the board the way it should look after the
        click, then ``interactions.finish()``. An item that already has an action starts from its result."""
        return api.demonstrate(node)

    def finish(cancel: bool = False, view: dict | None = None) -> dict[str, Any]:
        """Finish demonstrating: what changed becomes the click action and the board goes back
        (``cancel=True``: just go back). ``view`` ({"center", "zoom", "rotation"}) also flies there."""
        return api.finish(cancel, view)

    def demonstrating() -> str | None:
        """The item whose click is being demonstrated, or None."""
        return api.demo_name()

    def create_button(x: float = 0.0, y: float = 0.0, label: str = "Button", name: str | None = None,
                      **attrs: Any) -> str:
        """Place a button with its top-left corner at (x, y)."""
        final = scene.unique_name(name or label or "Button")
        scene.create_node("button", final, attrs={"translate": [float(x), float(y)], "label": str(label), **attrs})
        session.selection.set([final])
        return final

    def _bounds(names: list[str]) -> list[float]:
        rects = [r for r in (session.world_bounds(n) for n in names) if r is not None]
        if not rects:
            return [0.0, 0.0, 0.0, 0.0]
        left, top = min(r[0] for r in rects), min(r[1] for r in rects)
        return [left, top, max(r[0] + r[2] for r in rects) - left, max(r[1] + r[3] for r in rects) - top]

    def _members(nodes: Any, at_least: int) -> list[str]:
        names = session.selection.names if nodes is None else [scene.resolve(str(n)) for n in _refs(nodes)]
        tops = set(scene.top_level(names))  # in the order they were picked
        names = [n for n in names if n in tops and session.types.is_canvas(scene.get_node(n).node_type)]
        if len(names) < at_least:
            raise CommandError(f"Select at least {at_least} items")
        return names

    def _button_row(labels: list[str], bounds: list[float], prefix: str) -> list[str]:
        x, y = bounds[0], bounds[1] - 72.0 - 40.0
        made = []
        for label in labels:
            made.append(create_button(x, y, label, name=f"{prefix} {label}"))
            x += 240.0 + 24.0
        return made

    def make_tabs(nodes: Any = None) -> list[str]:
        """A row of buttons above the selected items, one per item: each shows its item and hides
        the others, one at a time like tabs. The first item shows; returns the buttons."""
        names = _members(nodes, 2)
        ids = [scene.get_node(n).id for n in names]
        buttons = _button_row([n[:18] for n in names], _bounds(names), "Tab")
        set_name = make_set_name("Tabs")
        for button, shown in zip(buttons, ids):
            steps = [step("set_attrs", shown, {"visibility": 1})]
            steps += [step("set_attrs", other, {"visibility": 0}) for other in ids if other != shown]
            scene.set_attrs(button, {"on_click": steps, "click_toggles": False, "action_set": set_name})
        for index, name in enumerate(names):
            scene.set_attr(name, "visibility", 1 if index == 0 else 0)
        scene.set_attr(buttons[0], "action_on", True)
        session.selection.set(buttons)
        return buttons

    def make_set_name(base: str) -> str:
        used = {str(n.attrs.get("action_set")) for n in scene.iter_nodes() if n.attrs.get("action_set")}
        return base if base not in used else next(f"{base} {i}" for i in range(2, len(used) + 3)
                                                  if f"{base} {i}" not in used)

    def make_reveal(nodes: Any = None) -> str:
        """A button above the selected items that shows them; they hide until it's clicked, and
        clicking again hides them. Returns the button."""
        names = _members(nodes, 1)
        button = _button_row(["Reveal"], _bounds(names), "Reveal")[0]
        steps = []
        for name in names:
            node_id = scene.get_node(name).id
            steps.append(step("set_attrs", node_id, {"visibility": 1},
                              back=[step("set_attrs", node_id, {"visibility": 0})]))
            scene.set_attr(name, "visibility", 0)
        scene.set_attrs(button, {"on_click": steps})
        return button

    def make_before_after(nodes: Any = None) -> str:
        """For two selected items (before, then after): a button that swaps which one shows; the
        second hides until it's clicked. Returns the button."""
        names = _members(nodes, 2)[:2]
        before_id, after_id = (scene.get_node(n).id for n in names)
        button = _button_row(["Before / After"], _bounds(names), "Compare")[0]
        steps = [step("set_attrs", before_id, {"visibility": 0}, back=[step("set_attrs", before_id, {"visibility": 1})]),
                 step("set_attrs", after_id, {"visibility": 1}, back=[step("set_attrs", after_id, {"visibility": 0})])]
        scene.set_attr(names[1], "visibility", 0)
        scene.set_attrs(button, {"on_click": steps})
        return button

    for name, fn, label, undoable in (
        ("click", click, lambda node=None, *a, **k: f"Click {label_of(node)}", True),
        ("set_action", set_action, "Set click action", True),
        ("add_step", add_step, "Add click step", True),
        ("remove_step", remove_step, "Remove click step", True),
        ("clear", clear, "Remove click action", True),
        ("set_toggles", set_toggles, "Click again to put back", True),
        ("make_set", make_set, "Make a set", True),
        ("connect", connect, lambda node=None, target=None, verb="show", **k: f"Connect {label_of(node)}", True),
        ("create_button", create_button, "Place button", True),
        ("make_tabs", make_tabs, "Make tabs", True),
        ("make_reveal", make_reveal, "Make reveal button", True),
        ("make_before_after", make_before_after, "Make before / after button", True),
        ("demonstrate", demonstrate, None, False),
        ("finish", finish, None, False),
    ):
        ctx.command(name, fn, label=label, undoable=undoable)
    for name, fn in (("verbs", verbs), ("describe", describe), ("demonstrating", demonstrating), ("check", check),
                     ("status", status), ("log", log), ("clear_log", clear_log)):
        ctx.command(name, fn, undoable=False, echo=False)
    ctx.context_key("interactions.demonstrating", lambda: api.demo is not None)

    def forget(_event: Any) -> None:
        if _event.payload.get("reason") in ("open", "new") and api.demo is not None:
            api.demo = None  # another board: the demonstration is gone with the old one
            session.events.emit("interactions.demonstrating", {"node": None})

    ctx.on("document.replaced", forget)


def activate_ui(ui: Any) -> None:
    from .ui import activate_interactions_ui

    activate_interactions_ui(ui)
