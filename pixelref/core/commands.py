"""The command system behind ``pixelref.pxcmd``.

Every user action is a named command. Buttons, menus, keyboard shortcuts,
canvas tools, scripts and the console all run commands through
:meth:`CommandRegistry.execute`, which

* opens one undo chunk around a top-level command (nested commands join it),
* rolls the document back if the command raises,
* echoes the equivalent ``pxcmd.…`` call to listeners (the console), and
* notifies listeners afterwards (the UI refreshes the canvas then).

Kernel commands have flat names (``create_node``, ``set_attr``…). Plugin
commands are namespaced by plugin (``pen.combine``) and may also
register a flat alias (``group``). Both work as ``pxcmd.<name>``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .errors import CommandError
from .extensions import Disposable

LabelSpec = str | Callable[..., str] | None


@dataclass
class Command:
    name: str
    fn: Callable[..., Any]
    label: LabelSpec = None
    undoable: bool = True
    echo: bool = True
    alias: str | None = None
    plugin: str | None = None
    doc: str = ""

    @property
    def display_name(self) -> str:
        return self.alias or self.name

    def undo_label(self, args: tuple, kwargs: dict) -> str:
        if callable(self.label):
            try:
                return str(self.label(*args, **kwargs))
            except Exception:  # noqa: BLE001
                pass
        if isinstance(self.label, str) and self.label:
            return self.label
        return self.display_name.rsplit(".", 1)[-1].replace("_", " ").capitalize()


def format_value(value: Any) -> str:
    if isinstance(value, float):
        text = f"{value:.6g}"
        return text if any(c in text for c in ".e") or text in ("inf", "-inf", "nan") else f"{text}.0"
    if isinstance(value, (list, tuple)):
        inner = ", ".join(format_value(v) for v in value)
        if isinstance(value, tuple):
            return f"({inner}{',' if len(value) == 1 else ''})"
        return f"[{inner}]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{format_value(k)}: {format_value(v)}" for k, v in value.items()) + "}"
    return repr(value)


def format_call(name: str, args: tuple, kwargs: dict) -> str:
    parts = [format_value(arg) for arg in args]
    parts += [f"{key}={format_value(value)}" for key, value in kwargs.items()]
    return f"pxcmd.{name}({', '.join(parts)})"


class CommandRegistry:
    def __init__(self, undo: Any) -> None:
        self._undo = undo
        self._commands: dict[str, Command] = {}
        self._aliases: dict[str, str] = {}
        self._depth = 0
        self._undoable_depth = 0
        # True once an undoable command ran during the current top-level command
        # (a UI wrapper that isn't undoable itself may still change the document).
        self.changed_document = False
        self._echo_listeners: list[Callable[[str], None]] = []
        self._before_listeners: list[Callable[[Command], None]] = []
        self._tidy_listeners: list[Callable[[], None]] = []
        self._after_listeners: list[Callable[[Command, Any, BaseException | None], None]] = []
        self._registry_listeners: list[Callable[[], None]] = []
        self.last_error: BaseException | None = None

    # ---- Registration ----------------------------------------------------------------

    def register(
        self,
        name: str,
        fn: Callable[..., Any],
        *,
        label: LabelSpec = None,
        undoable: bool = True,
        echo: bool = True,
        alias: str | None = None,
        plugin: str | None = None,
        doc: str = "",
        replace: bool = False,
    ) -> Disposable:
        """Register a command. A name (or alias) that is taken fails, naming its owner; with
        ``replace=True`` the new command stands in for an existing one of that name until
        disposed, when the original comes back."""
        if not re.fullmatch(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)*", name):
            raise CommandError(f"Invalid command name '{name}' (use snake_case, dots for namespaces)")
        previous = self._commands.get(name) if replace else None
        if previous is None and (name in self._commands or name in self._aliases):
            raise CommandError(f"Command '{name}' is already registered by {self._owner(name)}")
        if alias and alias != (previous.alias if previous else None) and (
                alias in self._commands or alias in self._aliases):
            raise CommandError(f"Command alias '{alias}' is already registered by {self._owner(alias)}")
        if previous is not None and previous.alias:
            self._aliases.pop(previous.alias, None)
        command = Command(name=name, fn=fn, label=label, undoable=undoable, echo=echo, alias=alias,
                          plugin=plugin, doc=doc or (fn.__doc__ or "").strip())
        self._commands[name] = command
        if alias:
            self._aliases[alias] = name
        self._changed()
        if previous is None:
            return Disposable(lambda: self.unregister(name))

        def restore() -> None:
            if self._commands.get(name) is not command:
                return
            self.unregister(name)
            if previous.alias and previous.alias in self._aliases:
                return  # its alias was taken in the meantime
            self._commands[name] = previous
            if previous.alias:
                self._aliases[previous.alias] = name
            self._changed()

        return Disposable(restore)

    def _owner(self, name: str) -> str:
        command = self.get(name)
        return f"plugin '{command.plugin}'" if command is not None and command.plugin else "pixelRef"

    def unregister(self, name: str) -> None:
        command = self._commands.pop(name, None)
        if command is not None and command.alias:
            self._aliases.pop(command.alias, None)
            self._changed()
        elif command is not None:
            self._changed()

    def watch(self, callback: Callable[[], None]) -> Disposable:
        self._registry_listeners.append(callback)
        return Disposable(lambda: callback in self._registry_listeners and self._registry_listeners.remove(callback))

    def _changed(self) -> None:
        for callback in list(self._registry_listeners):
            callback()

    # ---- Lookups ---------------------------------------------------------------------

    def get(self, name: str) -> Command | None:
        return self._commands.get(self._aliases.get(name, name))

    def resolve(self, name: str) -> Command:
        command = self.get(name)
        if command is None:
            raise CommandError(f"Unknown command '{name}'")
        return command

    def exists(self, name: str) -> bool:
        return self.get(name) is not None

    def names(self, include_aliases: bool = True) -> list[str]:
        names = set(self._commands)
        if include_aliases:
            names |= set(self._aliases)
        return sorted(names)

    def commands(self) -> list[Command]:
        return [self._commands[name] for name in sorted(self._commands)]

    def has_namespace(self, prefix: str) -> bool:
        prefix = prefix + "."
        return any(name.startswith(prefix) for name in self._commands)

    def children(self, prefix: str) -> list[str]:
        """Names directly below ``prefix`` ("" for top level)."""
        out: set[str] = set()
        start = prefix + "." if prefix else ""
        for name in list(self._commands) + list(self._aliases):
            if name.startswith(start):
                out.add(name[len(start):].split(".", 1)[0])
        return sorted(out)

    # ---- Listeners ----------------------------------------------------------------------

    def on_echo(self, callback: Callable[[str], None]) -> Disposable:
        self._echo_listeners.append(callback)
        return Disposable(lambda: callback in self._echo_listeners and self._echo_listeners.remove(callback))

    def on_before(self, callback: Callable[[Command], None]) -> Disposable:
        """Called before every top-level command (the UI flushes pending canvas edits)."""
        self._before_listeners.append(callback)
        return Disposable(lambda: callback in self._before_listeners and self._before_listeners.remove(callback))

    def on_tidy(self, callback: Callable[[], None]) -> Disposable:
        """Called at the end of every outermost undoable command, inside its undo
        step (e.g. the pen deletes drawings left without strokes)."""
        self._tidy_listeners.append(callback)
        return Disposable(lambda: callback in self._tidy_listeners and self._tidy_listeners.remove(callback))

    def tidy(self) -> None:
        for callback in list(self._tidy_listeners):
            callback()

    def on_after(self, callback: Callable[[Command, Any, BaseException | None], None]) -> Disposable:
        self._after_listeners.append(callback)
        return Disposable(lambda: callback in self._after_listeners and self._after_listeners.remove(callback))

    @property
    def depth(self) -> int:
        return self._depth

    # ---- Execution -------------------------------------------------------------------------

    def execute(self, name: str, *args: Any, **kwargs: Any) -> Any:
        command = self.resolve(name)
        top = self._depth == 0
        if top:
            self.changed_document = False
            for callback in list(self._before_listeners):
                callback(command)
        if top and command.echo and self._echo_listeners:
            text = format_call(command.display_name, args, kwargs)
            for callback in list(self._echo_listeners):
                callback(text)
        self._depth += 1
        error: BaseException | None = None
        result: Any = None
        try:
            if command.undoable:
                self.changed_document = True
                outermost = self._undoable_depth == 0
                self._undoable_depth += 1
                try:
                    with self._undo.chunk(command.undo_label(args, kwargs)):
                        result = command.fn(*args, **kwargs)
                        if outermost:
                            self.tidy()
                finally:
                    self._undoable_depth -= 1
            else:
                result = command.fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            error = exc
            raise
        finally:
            self._depth -= 1
            if top:
                self.last_error = error
                for callback in list(self._after_listeners):
                    callback(command, result, error)
        return result

    def proxy(self, name: str = "") -> "CommandProxy":
        return CommandProxy(self, name)


class CommandProxy:
    """``pxcmd.foo(...)`` / ``pxcmd.pen.combine(...)`` attribute access."""

    __slots__ = ("_registry", "_name")

    def __init__(self, registry: CommandRegistry, name: str) -> None:
        self._registry = registry
        self._name = name

    def __getattr__(self, item: str) -> "CommandProxy":
        if item.startswith("__"):
            raise AttributeError(item)
        name = f"{self._name}.{item}" if self._name else item
        if not self._registry.exists(name) and not self._registry.has_namespace(name):
            raise AttributeError(f"No command or command namespace '{name}'")
        return CommandProxy(self._registry, name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if not self._name or not self._registry.exists(self._name):
            raise CommandError(f"'{self._name or 'pxcmd'}' is a namespace, not a command")
        return self._registry.execute(self._name, *args, **kwargs)

    def __dir__(self) -> list[str]:
        return self._registry.children(self._name)

    def __repr__(self) -> str:
        command = self._registry.get(self._name) if self._name else None
        if command is not None:
            return f"<command pxcmd.{command.display_name}>"
        return f"<command namespace pxcmd{'.' + self._name if self._name else ''}>"

    @property
    def __doc__(self) -> str:  # type: ignore[override]
        command = self._registry.get(self._name) if self._name else None
        return command.doc if command else ""


# ---- Context keys and "when" conditions ------------------------------------------------------


class ContextKeys:
    """Named values describing the current state, tested by ``when`` conditions.

    Values are either set directly or computed on demand by a provider. A
    condition is a small expression: ``editing.text``, ``!editing.text``,
    ``selection.count > 1``, ``selection.types == annotation_drawing``,
    combined with ``&&`` and ``||`` (``&&`` binds tighter).
    """

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self._providers: dict[str, Callable[[], Any]] = {}

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value

    def provide(self, key: str, fn: Callable[[], Any]) -> Disposable:
        self._providers[key] = fn
        return Disposable(lambda: self._providers.get(key) is fn and self._providers.pop(key, None))

    def get(self, key: str, default: Any = None) -> Any:
        provider = self._providers.get(key)
        if provider is not None:
            try:
                return provider()
            except Exception:  # noqa: BLE001
                return default
        return self._values.get(key, default)

    def keys(self) -> list[str]:
        return sorted(set(self._values) | set(self._providers))

    def evaluate(self, expression: str | None) -> bool:
        if not expression or not expression.strip():
            return True
        return any(
            all(self._atom(atom.strip()) for atom in clause.split("&&"))
            for clause in expression.split("||")
        )

    _COMPARISON = re.compile(r"^([\w.]+)\s*(==|!=|>=|<=|>|<)\s*(.+)$")

    def _atom(self, atom: str) -> bool:
        if not atom:
            return True
        negate = atom.startswith("!")
        if negate:
            atom = atom[1:].strip()
        match = self._COMPARISON.match(atom)
        if match is None:
            result = bool(self.get(atom))
        else:
            key, op, literal = match.groups()
            result = _compare(self.get(key), op, _literal(literal.strip()))
        return not result if negate else result


def _literal(text: str) -> Any:
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _compare(value: Any, op: str, literal: Any) -> bool:
    if isinstance(value, (set, frozenset, list, tuple)) and isinstance(literal, str):
        wanted = {part.strip() for part in literal.split(",") if part.strip()}
        have = set(value)
        if op == "==":
            return have == wanted
        if op == "!=":
            return have != wanted
        return False
    try:
        if op == "==":
            return value == literal
        if op == "!=":
            return value != literal
        if op == ">":
            return value > literal
        if op == "<":
            return value < literal
        if op == ">=":
            return value >= literal
        if op == "<=":
            return value <= literal
    except TypeError:
        return False
    return False
