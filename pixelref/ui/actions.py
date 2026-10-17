"""Actions, keybindings and menus — all contributed by plugins.

* An **action** is a QAction for one command call (``define``): text, icon,
  search keywords, checked/enabled state. Menus, buttons and the command
  search all show the same QAction objects.
* A **keybinding** maps a key sequence to an action (or a command) with an
  optional ``when`` condition and scope (``window`` or ``canvas``). One
  QShortcut per key per window dispatches to the first binding whose
  condition holds, so a key can mean different things in different contexts.
  Bindings can also belong to a *mode* (the window-manager mode entered with
  the leader key, ``Ctrl+W`` by default): mode keys are plain keys that are
  only live while the mode is.
* **Menu items** place actions (or dynamic sub-menus) into menus by path,
  group and order, with a ``when`` condition.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import QEvent, QKeyCombination, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from ..core.extensions import Disposable
from .command_search import CommandSearchMenu
from .icons import icon

if TYPE_CHECKING:
    from .app import App

logger = logging.getLogger(__name__)

Condition = str | Callable[[], bool] | None


@dataclass
class ActionSpec:
    key: str
    command: str
    text: str
    args: tuple = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    icon: str = ""
    tip: str = ""
    keywords: str = ""
    checkable: bool = False
    checked: Callable[[], bool] | None = None
    enabled: Callable[[], bool] | None = None
    dynamic_text: Callable[[], str] | None = None
    category: str = ""
    group: str = ""  # exclusive check group (radio)
    plugin: str | None = None


class ActionRegistry:
    def __init__(self, app: "App") -> None:
        self.app = app
        self.specs: dict[str, ActionSpec] = {}
        self.actions: dict[str, QAction] = {}
        self._groups: dict[str, QActionGroup] = {}
        self._listeners: list[Callable[[], None]] = []

    def define(self, key: str, command: str, text: str, *, args: tuple = (), kwargs: dict[str, Any] | None = None,
               icon_name: str = "", tip: str = "", keywords: str = "", checkable: bool = False,
               checked: Callable[[], bool] | None = None, enabled: Callable[[], bool] | None = None,
               dynamic_text: Callable[[], str] | None = None, category: str = "", group: str = "",
               plugin: str | None = None) -> Disposable:
        if key in self.specs:
            raise ValueError(f"Action '{key}' is already defined")
        spec = ActionSpec(key=key, command=command, text=text, args=tuple(args), kwargs=dict(kwargs or {}),
                          icon=icon_name, tip=tip, keywords=keywords, checkable=checkable or checked is not None,
                          checked=checked, enabled=enabled, dynamic_text=dynamic_text, category=category,
                          group=group, plugin=plugin)
        action = QAction(text, None)
        action.setCheckable(spec.checkable)
        action.setProperty("keywords", keywords)
        action.setProperty("action_key", key)
        # Windows hides shortcuts in context menus by default; always show them.
        action.setShortcutVisibleInContextMenu(True)
        action.setShortcutContext(Qt.WidgetShortcut)  # keys are dispatched by the keymap, not by actions
        if icon_name:
            action.setIcon(icon(icon_name))
        if tip:
            action.setStatusTip(tip)
            action.setToolTip(tip)
        if group:
            action_group = self._groups.setdefault(group, QActionGroup(None))
            action_group.addAction(action)
        action.triggered.connect(lambda *_a, k=key: self.app.trigger(k))
        self.specs[key] = spec
        self.actions[key] = action
        self.update_shortcut(key)
        self._changed()

        def remove() -> None:
            if self.specs.get(key) is spec:
                del self.specs[key]
                removed = self.actions.pop(key)
                group_obj = self._groups.get(group)
                if group_obj is not None:
                    group_obj.removeAction(removed)
                removed.deleteLater()
                self._changed()

        return Disposable(remove)

    def get(self, key: str) -> QAction | None:
        return self.actions.get(key)

    def __getitem__(self, key: str) -> QAction:
        return self.actions[key]

    def __contains__(self, key: object) -> bool:
        return key in self.actions

    def watch(self, callback: Callable[[], None]) -> Disposable:
        self._listeners.append(callback)
        return Disposable(lambda: callback in self._listeners and self._listeners.remove(callback))

    def _changed(self) -> None:
        if getattr(self.app, "closed", False):
            return  # shutting down: listeners (the tool palette…) would rebuild once per removed action
        for callback in list(self._listeners):
            callback()

    def update_shortcut(self, key: str) -> None:
        action = self.actions.get(key)
        if action is not None:
            action.setShortcuts(self.app.keymap.sequences_for(key))

    def update_all_shortcuts(self) -> None:
        for key in self.actions:
            self.update_shortcut(key)

    def refresh_states(self) -> None:
        for key, spec in self.specs.items():
            action = self.actions[key]
            if spec.checked is not None:
                try:
                    state = bool(spec.checked())
                except Exception:  # noqa: BLE001
                    state = False
                if action.isChecked() != state:
                    action.blockSignals(True)
                    action.setChecked(state)
                    action.blockSignals(False)
            if spec.enabled is not None:
                try:
                    action.setEnabled(bool(spec.enabled()))
                except Exception:  # noqa: BLE001
                    pass
            if spec.dynamic_text is not None:
                try:
                    action.setText(spec.dynamic_text())
                except Exception:  # noqa: BLE001
                    pass

    def shortcut_label(self, key: str, compact: bool = True) -> str:
        """Display text for an action's first shortcut, e.g. "Ctrl+I" or "Ctrl+⇧N"."""
        action = self.actions.get(key)
        if action is None or not action.shortcuts():
            return ""
        text = action.shortcuts()[0].toString(QKeySequence.SequenceFormat.NativeText)
        return text.replace("Shift+", "⇧") if compact else text


# ---- Keymap ------------------------------------------------------------------------------------


def normalize_sequence(sequence: str) -> str:
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.PortableText)


@dataclass
class Binding:
    sequence: str
    target: str  # action key or command name
    when: Condition = None
    scope: str = "window"  # window | canvas
    mode: str | None = None
    plugin: str | None = None
    user: bool = False
    serial: int = 0


_SHORTCUT_OVERRIDE, _KEY_PRESS = QEvent.ShortcutOverride, QEvent.KeyPress  # (enum lookups are slow per event)
_MODIFIER_KEYS = frozenset({Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr})


class _ModeFilter(QObject):
    def __init__(self, keymap: "Keymap") -> None:
        super().__init__()
        self.keymap = keymap

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if self.keymap.mode is None:
            return False
        kind = event.type()
        if kind != _KEY_PRESS and kind != _SHORTCUT_OVERRIDE:
            return False
        # A dialog or menu opened from the mode (mark prompt, new view menu) gets its own keys:
        # typing "q" into the mark prompt must not close a view.
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return False
        if kind == _SHORTCUT_OVERRIDE:
            event.accept()
            return True
        if event.key() in _MODIFIER_KEYS:
            return False
        return self.keymap.handle_mode_key(event)


class Keymap:
    MODE_TIMEOUT_MS = 2500
    LEADER_KEY = "keymap/leader"

    def __init__(self, app: "App") -> None:
        self.app = app
        self.bindings: list[Binding] = []
        self._serial = 0
        self._installed: list[tuple[QWidget, str]] = []
        self._shortcuts: dict[int, list[QShortcut]] = {}
        self.mode: str | None = None
        self._mode_filter = _ModeFilter(self)
        self._mode_timer = QTimer()
        self._mode_timer.setSingleShot(True)
        self._mode_timer.timeout.connect(self.exit_mode)
        self._rebuild_pending = False
        self.batching = True  # the app turns this off once startup is done
        self.mode_titles: dict[str, str] = {}
        self.persistent_modes: set[str] = set()  # modes that ignore the idle timeout (resize)
        self.leader_sequence = normalize_sequence(str(app.settings.value(self.LEADER_KEY, "Ctrl+W") or "Ctrl+W"))
        self._leader_binding: Disposable | None = None
        # Keys the user took away from built-in bindings: (key, mode, target or None for any).
        # Kept as a filter rather than deleting bindings, so a plugin enabled later (or
        # re-enabled) doesn't bring them back, and reloading the user file can undo them.
        self.removed: list[tuple[str, str | None, str | None]] = []
        self.user_file: Path | None = None
        self.user_targets: dict[str, int] = {}  # action key -> its [[bind]] index (macros, scripts…)
        self._user_items: list[Disposable] = []
        self._leader_before_file: str | None = None
        self._listeners: list[Callable[[], None]] = []

    # ---- Bindings ------------------------------------------------------------------------

    def bind(self, sequence: str, target: str, when: Condition = None, scope: str = "window",
             mode: str | None = None, plugin: str | None = None, user: bool = False) -> Disposable:
        self._serial += 1
        binding = Binding(sequence=normalize_sequence(sequence), target=target, when=when, scope=scope,
                          mode=mode, plugin=plugin, user=user, serial=self._serial)
        self.bindings.append(binding)
        self._changed(binding.target)

        def remove() -> None:
            if binding in self.bindings:
                self.bindings.remove(binding)
                self._changed(binding.target)

        return Disposable(remove)

    def unbind(self, sequence: str, mode: str | None = None, target: str | None = None) -> Disposable:
        """Take a key away (user keymap ``[[unbind]]``): from ``target`` only when given, else
        from everything. Built-in bindings stay hidden until the returned Disposable runs."""
        sequence = normalize_sequence(sequence)
        entry = (sequence, mode, target)
        affected = [b for b in self.bindings if b.sequence == sequence and b.mode == mode
                    and (target is None or b.target == target)]
        for binding in affected:
            if binding.user:
                self.bindings.remove(binding)
        self.removed.append(entry)
        for target_key in {b.target for b in affected}:
            self._changed(target_key)

        def restore() -> None:
            if entry in self.removed:
                self.removed.remove(entry)
                for target_key in {b.target for b in affected}:
                    self._changed(target_key)

        return Disposable(restore)

    def is_live(self, binding: Binding) -> bool:
        """False for a built-in binding the user removed."""
        if binding.user or not self.removed:
            return True
        return not any(sequence == binding.sequence and mode == binding.mode and target in (None, binding.target)
                       for sequence, mode, target in self.removed)

    def live_bindings(self) -> list[Binding]:
        return [b for b in self.bindings if self.is_live(b)]

    def default_sequences(self, target: str, mode: str | None = None) -> list[str]:
        """The built-in (plugin) keys of a target, whether or not the user removed them."""
        found = sorted((b for b in self.bindings if b.target == target and b.mode == mode and not b.user),
                       key=lambda b: b.serial)
        return list(dict.fromkeys(b.sequence for b in found))

    def targets_of(self, sequence: str, scope: str | None = None, mode: str | None = None) -> list[str]:
        """What a key runs without a condition (for conflict warnings), most important first."""
        return list(dict.fromkeys(b.target for b in self.bindings_for_sequence(sequence, scope, mode)
                                  if not b.when))

    def _changed(self, target: str | None = None) -> None:
        if target is not None:
            self.app.actions.update_shortcut(target)
        if self.batching:
            self._rebuild_pending = True
            return
        self.rebuild_shortcuts()

    def end_batch(self) -> None:
        self.batching = False
        if self._rebuild_pending:
            self._rebuild_pending = False
            self.rebuild_shortcuts()

    def sequences_for(self, target: str, mode: str | None = None) -> list[QKeySequence]:
        found = [b for b in self.bindings if b.target == target and b.mode == mode and self.is_live(b)]
        found.sort(key=lambda b: (not b.user, b.serial))
        seen: list[str] = []
        for binding in found:
            if binding.sequence not in seen:
                seen.append(binding.sequence)
        return [QKeySequence(s) for s in seen]

    def bindings_for_sequence(self, sequence: str, scope: str | None = None, mode: str | None = None) -> list[Binding]:
        sequence = normalize_sequence(sequence)
        found = [b for b in self.bindings if b.sequence == sequence and b.mode == mode
                 and (scope is None or b.scope == scope) and self.is_live(b)]
        # User bindings first, then the most recently registered.
        found.sort(key=lambda b: (not b.user, -b.serial))
        return found

    def conflicts(self) -> list[tuple[str, list[str]]]:
        """Keys bound to several targets without conditions telling them apart."""
        groups: dict[tuple[str, str, str | None], list[Binding]] = {}
        for binding in self.live_bindings():
            groups.setdefault((binding.sequence, binding.scope, binding.mode), []).append(binding)
        out = []
        for (sequence, _scope, mode), items in groups.items():
            unconditional = [b for b in items if not b.when]
            if len({b.target for b in unconditional}) > 1:
                label = f"{mode}: {sequence}" if mode else sequence
                out.append((label, sorted({b.target for b in unconditional})))
        return out

    # ---- Installing QShortcuts ---------------------------------------------------------------

    def install(self, widget: QWidget, scope: str) -> Disposable:
        """Give ``widget`` the shortcuts of ``scope`` (window: top-level windows; canvas: views)."""
        entry = (widget, scope)
        self._installed.append(entry)
        if not self.batching:
            self._create_shortcuts(widget, scope)
        else:
            self._rebuild_pending = True

        def remove() -> None:
            if entry in self._installed:
                self._installed.remove(entry)
            for shortcut in self._shortcuts.pop(id(widget), []):
                try:
                    shortcut.setEnabled(False)
                    shortcut.deleteLater()
                except RuntimeError:
                    pass

        return Disposable(remove)

    def rebuild_shortcuts(self) -> None:
        for widget, scope in list(self._installed):
            for shortcut in self._shortcuts.pop(id(widget), []):
                try:
                    shortcut.setEnabled(False)
                    shortcut.deleteLater()
                except RuntimeError:
                    pass
            try:
                self._create_shortcuts(widget, scope)
            except RuntimeError:  # widget already deleted
                self._installed.remove((widget, scope))
        for callback in list(self._listeners):
            callback()

    def watch(self, callback: Callable[[], None]) -> Disposable:
        """``callback()`` after the keys changed (once per batch of changes)."""
        self._listeners.append(callback)
        return Disposable(lambda: callback in self._listeners and self._listeners.remove(callback))

    def _create_shortcuts(self, widget: QWidget, scope: str) -> None:
        sequences = sorted({b.sequence for b in self.live_bindings() if b.scope == scope and b.mode is None})
        context = Qt.WindowShortcut if scope == "window" else Qt.WidgetWithChildrenShortcut
        made = []
        for sequence in sequences:
            shortcut = QShortcut(QKeySequence(sequence), widget)
            shortcut.setContext(context)
            shortcut.setAutoRepeat(True)
            shortcut.activated.connect(lambda s=sequence, sc=scope: self.dispatch(s, sc))
            made.append(shortcut)
        self._shortcuts[id(widget)] = made

    def condition_holds(self, when: Condition) -> bool:
        if when is None:
            return True
        if callable(when):
            try:
                return bool(when())
            except Exception:  # noqa: BLE001
                return False
        return self.app.session.context.evaluate(when)

    def dispatch(self, sequence: str, scope: str = "window") -> bool:
        if self.mode is not None:
            return False
        for binding in self.bindings_for_sequence(sequence, scope):
            if self.condition_holds(binding.when):
                self.app.trigger(binding.target)
                return True
        return False

    # ---- Modes (leader key) -------------------------------------------------------------

    def set_leader(self, sequence: str, mode: str = "window", persist: bool = True) -> None:
        self.leader_sequence = normalize_sequence(sequence)
        if persist:
            self.app.settings.setValue(self.LEADER_KEY, self.leader_sequence)
        self.install_leader(mode)

    def install_leader(self, mode: str = "window") -> None:
        if self._leader_binding is not None:
            self._leader_binding.dispose()
        self._leader_binding = self.bind(self.leader_sequence, f"keymap.mode.{mode}")

    def enter_mode(self, mode: str) -> None:
        if self.mode == mode:
            return
        if self.mode is None:
            QApplication.instance().installEventFilter(self._mode_filter)
        self.mode = mode
        self._restart_timer()
        self.app.on_mode_changed(mode)

    def exit_mode(self) -> None:
        if self.mode is None:
            return
        self.mode = None
        self._mode_timer.stop()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self._mode_filter)
        self.app.on_mode_changed(None)

    def _restart_timer(self) -> None:
        if self.mode in self.persistent_modes:
            self._mode_timer.stop()
        else:
            self._mode_timer.start(self.MODE_TIMEOUT_MS)

    def handle_mode_key(self, event: Any) -> bool:
        key = event.key()
        if key in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            self.exit_mode()
            return True
        combination = event.keyCombination()
        sequence = QKeySequence(combination).toString(QKeySequence.SequenceFormat.PortableText)
        candidates = [sequence]
        # Shift+1 arrives as Shift+! (the shifted character): also try the key itself, from the
        # Windows virtual key (0-9 and A-Z match Qt's key codes), so Shift+number bindings work.
        vk = event.nativeVirtualKey()
        if sys.platform == "win32" and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A) and vk != event.key():
            candidates.append(QKeySequence(QKeyCombination(combination.keyboardModifiers(), Qt.Key(vk)))
                              .toString(QKeySequence.SequenceFormat.PortableText))
        mode = self.mode
        for candidate in candidates:
            for binding in self.bindings_for_sequence(candidate, None, mode):
                if self.condition_holds(binding.when):
                    self._restart_timer()
                    self.app.trigger(binding.target)
                    return True
        self._restart_timer()
        self.app.set_status(f"{sequence} isn't bound in {mode} mode · Esc to leave")
        return True

    # ---- User keymap file ------------------------------------------------------------------

    def load_user_file(self, path: Path) -> list[str]:
        """Apply a ``keymap.toml`` (see ``keymap_file``), replacing what an earlier load of
        the user file added; returns the problems found (also logged)."""
        from . import keymap_file

        self.user_file = path
        batching, self.batching = self.batching, True
        try:
            self._clear_user_file()
            try:
                data = keymap_file.load(path)
            except ValueError as exc:
                return [str(exc)]
            problems = self._apply_user_data(data, path)
        finally:
            if not batching:
                self.end_batch()
        for problem in problems:
            logger.warning(problem)
        return problems

    def reload_user_file(self) -> list[str]:
        return self.load_user_file(self.user_file) if self.user_file is not None else []

    # ---- Lasting changes (written to keymap.toml) ---------------------------------------------
    # The shortcut editor, the tool palette and the keymap.* commands all edit keys this way.

    def user_path(self) -> Path:
        return self.user_file or self.app.config_dir() / "keymap.toml"

    def shortcuts(self, target: str) -> list[str]:
        """The live (global) keys of a target, normalized, most important first."""
        return list(dict.fromkeys(normalize_sequence(s.toString()) for s in self.sequences_for(target)))

    def changed(self, target: str) -> bool:
        """True when the user's keys for ``target`` differ from the built-in ones."""
        if target in self.user_targets:
            return True
        return sorted(self.shortcuts(target)) != sorted(self.default_sequences(target))

    def edit_user_file(self, change: Callable[[dict[str, Any]], None]) -> list[str]:
        """Apply ``change(data)`` to keymap.toml, save it and reload it; returns the problems
        found. Raises ValueError (and changes nothing) when the file can't be read."""
        from . import keymap_file

        path = self.user_path()
        data = keymap_file.load(path)
        change(data)
        keymap_file.save(path, data)
        return self.load_user_file(path)

    def assign(self, target: str, keys: list[str], take_from: Any = ()) -> list[str]:
        """Give ``target`` exactly ``keys`` (kept in keymap.toml); ``take_from`` lists
        (key, other target) pairs to take the key away from first."""
        from . import keymap_file

        keys = list(dict.fromkeys(normalize_sequence(k) for k in keys if k))

        def change(data: dict[str, Any]) -> None:
            for key, other in take_from:
                if other in self.user_targets:
                    rest = [k for k in self.shortcuts(other) if k != key]
                    keymap_file.set_entry_key(data, self.user_targets[other], rest)
                else:
                    keymap_file.remove_key(data, key, other, key in self.default_sequences(other))
            if target in self.user_targets:
                keymap_file.set_entry_key(data, self.user_targets[target], keys)
            else:
                keymap_file.set_shortcuts(data, target, keys, self.default_sequences(target))

        return self.edit_user_file(change)

    def set_shortcut(self, target: str, key: str, add: bool = False) -> list[str]:
        """Make ``key`` the shortcut of ``target`` (``add``: one more), taking it over from
        whatever else had it; an empty key removes every shortcut."""
        if not key:
            return self.assign(target, [])
        key = normalize_sequence(key)
        others = [t for t in self.targets_of(key) if t != target]
        keys = self.shortcuts(target) + [key] if add else [key]
        return self.assign(target, keys, [(key, other) for other in others])

    def reset_shortcuts(self, target: str) -> list[str]:
        """Back to the built-in keys of an action or command."""
        from . import keymap_file

        return self.edit_user_file(lambda data: keymap_file.reset(data, target))

    def _clear_user_file(self) -> None:
        while self._user_items:
            try:
                self._user_items.pop().dispose()
            except Exception:  # noqa: BLE001
                logger.exception("Removing a user keymap entry failed")
        self.user_targets = {}
        if self._leader_before_file is not None:
            self.set_leader(self._leader_before_file, persist=False)
            self._leader_before_file = None

    def _apply_user_data(self, data: dict[str, Any], path: Path) -> list[str]:
        problems: list[str] = []
        items = self._user_items
        leader = data.get("leader", {})
        if isinstance(leader, dict) and leader.get("window"):
            self._leader_before_file = self.leader_sequence
            self.set_leader(str(leader["window"]), persist=False)
        for entry in data.get("mode", []) or []:
            name = entry.get("name") if isinstance(entry, dict) else None
            if not name or not str(name).isidentifier():
                problems.append(f"{path}: every [[mode]] needs a name (letters, digits, _)")
                continue
            name = str(name)
            title = str(entry.get("title") or name.replace("_", " ").title())
            had_title = name in self.mode_titles
            self.mode_titles.setdefault(name, title)
            if entry.get("sticky"):
                self.persistent_modes.add(name)
            action_key = f"keymap.mode.{name}"
            if action_key not in self.app.actions:
                items.append(self.app.actions.define(action_key, "keymap.enter_mode", f"{title} Mode", args=(name,),
                                                     keywords=f"key mode chord {name}", category="Modes"))

            def forget(n: str = name, keep: bool = had_title, sticky: bool = bool(entry.get("sticky"))) -> None:
                if not keep:
                    self.mode_titles.pop(n, None)
                if sticky:
                    self.persistent_modes.discard(n)

            items.append(Disposable(forget))
            if entry.get("key"):
                items.append(self.bind(str(entry["key"]), action_key, user=True))
        for entry in data.get("unbind", []) or []:
            if isinstance(entry, dict) and entry.get("key"):
                items.append(self.unbind(str(entry["key"]), entry.get("mode"), entry.get("command")))
            else:
                problems.append(f"{path}: every [[unbind]] needs a key")
        taken: set[str] = set()
        for index, entry in enumerate(data.get("bind", []) or []):
            if not isinstance(entry, dict):
                problems.append(f"{path}: [[bind]] entries must be tables")
                continue
            kinds = [k for k in ("command", "commands", "script", "python") if entry.get(k)]
            if len(kinds) != 1:
                problems.append(f"{path}: [[bind]] {entry.get('key', '?')!r} needs one of command, commands, "
                                "script or python")
                continue
            if kinds[0] == "command":
                target = str(entry["command"])
            else:
                target = self._define_user_target(entry, kinds[0], index, taken)
                self.user_targets[target] = index
            if not entry.get("key"):
                continue  # a macro without a key: still searchable (Ctrl+K) and bindable in the editor
            items.append(self.bind(str(entry["key"]), target, when=entry.get("when"),
                                   scope=str(entry.get("scope", "window")), mode=entry.get("mode"), user=True))
        return problems

    def _define_user_target(self, entry: dict[str, Any], kind: str, index: int, taken: set[str]) -> str:
        """An action running a macro, script or Python from the user keymap (``user.<name>``)."""
        import re

        value = entry[kind]
        if kind == "commands":
            steps = [str(v) for v in (value if isinstance(value, list) else [value])]
            text = str(entry.get("name") or "Macro: " + ", ".join(steps))
            kwargs: dict[str, Any] = {"commands": steps}
        elif kind == "script":
            text = str(entry.get("name") or f"Run {Path(str(value)).name}")
            kwargs = {"script": str(value)}
        else:
            first = next((line.strip() for line in str(value).splitlines() if line.strip()), "")
            text = str(entry.get("name") or f"Python: {first[:40]}")
            kwargs = {"python": str(value)}
        kwargs["label"] = text
        slug = re.sub(r"[^a-z0-9]+", "_", str(entry.get("name") or text).lower()).strip("_") or f"entry_{index}"
        key = f"user.{slug}"
        number = 2
        while key in taken or key in self.app.actions:
            key, number = f"user.{slug}_{number}", number + 1
        taken.add(key)
        self._user_items.append(self.app.actions.define(
            key, "macros.run", text, kwargs=kwargs, keywords=f"macro user hotkey {kind}", category="User Hotkeys"))
        return key


# ---- Menus ---------------------------------------------------------------------------------------


# Where each group sits in a menu (groups not listed sit at 70, sorted by name). The context
# menu goes from what was clicked on (the selection, or creating things on an empty canvas),
# through selecting and showing everything, undo and the canvas-wide sub-menus (Tools, Arrange,
# Effects), to the app-wide sub-menus (File, View, Window, Scripts) and help. Nested groups
# order the View, File and Window sub-menus.
GROUP_ORDER = {
    "header": 0, "clipboard": 10, "create": 12, "state": 20, "transform": 30, "drawing": 32,
    "arrange": 40, "group": 42, "select": 45, "reveal": 47, "focus": 50, "history": 55, "tools": 58,
    "menus": 65, "view": 70, "file": 75, "window": 80, "app": 90,
    "view.frame": 71, "view.zoom": 72, "view.flip": 73, "view.display": 74, "view.background": 75,
    "file.import": 76, "file.export": 77, "file.storage": 78,
    "window.panels": 81, "w_modes": 82, "window.extras": 83,
}


@dataclass
class MenuItem:
    menu: str
    path: tuple[str, ...]
    action: str | None = None
    builder: Callable[[QMenu], None] | None = None
    text: str = ""  # for a dynamic sub-menu
    icon: str = ""  # for a dynamic sub-menu
    group: str = ""
    order: float = 0.0
    when: Condition = None
    plugin: str | None = None
    serial: int = 0


class MenuRegistry:
    def __init__(self, app: "App") -> None:
        self.app = app
        self.items: list[MenuItem] = []
        self._serial = 0
        # Where sub-menus sit: the first placement whose condition holds wins.
        self.submenu_order: dict[tuple[str, tuple[str, ...]], list[tuple[str, float, Condition]]] = {}
        self.submenu_icons: dict[tuple[str, tuple[str, ...]], str] = {}

    def add(self, menu: str, action: str | None = None, *, path: str | tuple[str, ...] = (), group: str = "",
            order: float = 0.0, when: Condition = None, builder: Callable[[QMenu], None] | None = None,
            text: str = "", icon: str = "", plugin: str | None = None) -> Disposable:
        self._serial += 1
        path_tuple = tuple(p for p in (path.split("/") if isinstance(path, str) else path) if p)
        item = MenuItem(menu=menu, path=path_tuple, action=action, builder=builder, text=text, icon=icon,
                        group=group, order=order, when=when, plugin=plugin, serial=self._serial)
        self.items.append(item)
        return Disposable(lambda: item in self.items and self.items.remove(item))

    def place_submenu(self, menu: str, path: str, group: str, order: float = 0.0, *, when: Condition = None,
                      icon: str = "") -> Disposable:
        """Where a sub-menu itself sits in its parent menu, and its icon. Placed again with a
        ``when``, it moves there while the condition holds (conditional placements win over
        unconditional ones)."""
        key = (menu, tuple(p for p in path.split("/") if p))
        placement = (group, order, when)
        placements = self.submenu_order.setdefault(key, [])
        placements.insert(0 if when is not None else len(placements), placement)
        if icon:
            self.submenu_icons[key] = icon

        def dispose() -> None:
            if placement in placements:
                placements.remove(placement)
            if icon and self.submenu_icons.get(key) == icon:
                self.submenu_icons.pop(key)
        return Disposable(dispose)

    def _placement(self, key: tuple[str, tuple[str, ...]]) -> tuple[str, float] | None:
        for group, order, when in self.submenu_order.get(key, ()):
            if self.app.keymap.condition_holds(when):
                return group, order
        return None

    def build(self, menu_id: str, parent: QWidget | None = None, header: str = "") -> CommandSearchMenu:
        self.app.actions.refresh_states()
        menu = CommandSearchMenu(parent, extra_actions=[
            action for key, action in self.app.actions.actions.items()
            if not self.app.actions.specs[key].category == "hidden"
        ])
        if header:
            menu.addSection(header)
        # Conditions are evaluated once per open, not again for every sub-menu level.
        items = [item for item in self.items if item.menu == menu_id and self._visible(item)]
        self._fill(menu, menu_id, (), items)
        menu.finish()
        return menu

    def _visible(self, item: MenuItem) -> bool:
        return self.app.keymap.condition_holds(item.when)

    def _fill(self, menu: QMenu, menu_id: str, path: tuple[str, ...], items: list[MenuItem]) -> bool:
        """Fill ``menu`` with ``items`` (the visible items at or below ``path``)."""
        entries: list[tuple[str, float, int, Any]] = []
        children: dict[str, list[MenuItem]] = {}
        for item in items:
            if len(item.path) > len(path):
                children.setdefault(item.path[len(path)], []).append(item)
                continue
            entries.append((item.group, item.order, item.serial, item))
        for title, items in children.items():
            group, order = self._placement((menu_id, path + (title,))) or (items[0].group, items[0].order)
            entries.append((group, order, min(i.serial for i in items), title))
        entries.sort(key=lambda e: (GROUP_ORDER.get(e[0], 70), e[0], e[1], e[2]))
        last_group = None
        added = False
        for group, _order, _serial, entry in entries:
            separator = None
            if last_group is not None and group != last_group and added:
                separator = menu.addSeparator()
            if isinstance(entry, str):
                sub = menu.addMenu(entry)
                if icon_name := self.submenu_icons.get((menu_id, path + (entry,))):
                    sub.setIcon(icon(icon_name))
                if not self._fill(sub, menu_id, path + (entry,), children[entry]):
                    menu.removeAction(sub.menuAction())
                    if separator is not None:
                        menu.removeAction(separator)
                    continue
            elif entry.builder is not None:
                if entry.text:
                    sub = menu.addMenu(entry.text)
                    if entry.icon:
                        sub.setIcon(icon(entry.icon))
                    entry.builder(sub)
                else:
                    count = len(menu.actions())
                    entry.builder(menu)
                    if len(menu.actions()) == count:  # the builder had nothing to add
                        if separator is not None:
                            menu.removeAction(separator)
                        continue
            else:
                action = self.app.actions.get(entry.action or "")
                if action is None:
                    if separator is not None:
                        menu.removeAction(separator)
                    continue
                menu.addAction(action)
            last_group = group
            added = True
        return added
