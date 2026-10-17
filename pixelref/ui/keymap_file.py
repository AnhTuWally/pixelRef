"""The user keymap file (``keymap.toml`` in the user config folder): read, edit, write.

Hand-written and edited by the Keyboard Shortcuts editor alike, so both stay in
one place. The editor rewrites the file (comments are not kept), keeping every
entry it doesn't change::

    [leader]
    window = "Ctrl+W"            # the window-mode leader key

    [[bind]]                     # a key for an action or command…
    key = "Ctrl+Shift+D"
    command = "clipboard.duplicate"

    [[bind]]                     # …or a macro: commands run in order, one undo step
    key = "Ctrl+Alt+T"
    name = "Tidy board"
    commands = ["select.all", "arrange.grid", "navigation.frame_all"]

    [[bind]]                     # …or Python (pxcmd, pxnode, sel(), session, app are defined)
    key = "Ctrl+Alt+1"
    name = "Rotate 45"
    python = "pxcmd.set_transform(sel(), rotate=45, relative=True)"

    [[bind]]                     # …or a script file (relative paths: the user scripts folder)
    key = "F5"
    script = "tidy.py"

    [[unbind]]                   # remove a built-in key (only for that command when given)
    key = "Ctrl+D"
    command = "clipboard.duplicate"

    [[mode]]                     # a key mode, like the window mode: Ctrl+Space, then a key
    name = "macros"
    key = "Ctrl+Space"
    title = "Macros"
    # sticky = true              # stay in the mode until Esc (else it ends after a key)

    [[bind]]
    mode = "macros"
    key = "1"
    script = "tidy.py"

``when`` (e.g. ``"selection.count > 0"``) and ``scope`` (``window`` or
``canvas``) work on any ``[[bind]]``.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

HEADER = ("# pixelRef keyboard shortcuts. Edited by Keyboard Shortcuts (F1) and by hand;\n"
          "# the editor rewrites this file (comments are not kept). See docs/keymap.md.\n")
TARGET_KEYS = ("command", "commands", "script", "python")


def load(path: Path) -> dict[str, Any]:
    """The file's data (empty when missing); raises ValueError for a broken file."""
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    return data


def save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def dumps(data: dict[str, Any]) -> str:
    """TOML for keymap data (tables of plain values and arrays of tables)."""
    out = [HEADER]
    plain = {k: v for k, v in data.items()
             if not isinstance(v, dict) and (not isinstance(v, list) or _is_value_list(v))}
    for key, value in plain.items():
        out.append(f"{_key(key)} = {_value(value)}\n")
    for key, value in data.items():
        if isinstance(value, dict):
            out.append(f"\n[{_key(key)}]\n")
            for k, v in value.items():
                out.append(f"{_key(k)} = {_value(v)}\n")
    for key in ("mode", "unbind", "bind"):  # modes first: binds refer to them
        for entry in data.get(key, []) or []:
            if isinstance(entry, dict):
                out.append(f"\n[[{key}]]\n")
                for k, v in entry.items():
                    out.append(f"{_key(k)} = {_value(v)}\n")
    for key, value in data.items():
        if key not in ("mode", "unbind", "bind") and isinstance(value, list) and not _is_value_list(value):
            for entry in value:
                out.append(f"\n[[{_key(key)}]]\n")
                for k, v in entry.items():
                    out.append(f"{_key(k)} = {_value(v)}\n")
    return "".join(out)


def _is_value_list(value: list) -> bool:
    return not any(isinstance(v, dict) for v in value)


def _key(key: str) -> str:
    return key if key.replace("_", "").replace("-", "").isalnum() else _string(key)


def _string(text: str) -> str:
    escaped = (text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
               .replace("\r", "\\r"))
    return f'"{escaped}"'


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(v)}" for k, v in value.items()) + " }"
    return _string(str(value))


# ---- Edits made by the shortcut editor ------------------------------------------------------------


def _same(a: str, b: str) -> bool:
    from .actions import normalize_sequence

    return normalize_sequence(a) == normalize_sequence(b)


def _global(entry: dict[str, Any]) -> bool:
    return not entry.get("mode")


def set_shortcuts(data: dict[str, Any], target: str, keys: list[str], defaults: list[str]) -> None:
    """Make ``keys`` the (global) shortcuts of an action or command.

    ``defaults`` are its built-in keys: those not kept get an ``[[unbind]]``, new
    ones a ``[[bind]]``; the user's earlier entries for the target are replaced.
    """
    binds = [b for b in data.get("bind", []) if not (_global(b) and b.get("command") == target)]
    unbinds = [u for u in data.get("unbind", []) if not (_global(u) and u.get("command") == target)]
    for key in defaults:
        if not any(_same(key, k) for k in keys):
            unbinds.append({"key": key, "command": target})
    for key in keys:
        if not any(_same(key, d) for d in defaults):
            binds.append({"key": key, "command": target})
    _store(data, binds, unbinds)


def remove_key(data: dict[str, Any], key: str, target: str, is_default: bool) -> None:
    """Take one key away from one target (another target is getting it)."""
    binds = [b for b in data.get("bind", [])
             if not (_global(b) and _target_of(b) == target and _same(b.get("key", ""), key))]
    unbinds = list(data.get("unbind", []))
    if is_default and not any(_global(u) and u.get("command") == target and _same(u.get("key", ""), key)
                              for u in unbinds):
        unbinds.append({"key": key, "command": target})
    _store(data, binds, unbinds)


def set_entry_key(data: dict[str, Any], index: int, keys: list[str]) -> None:
    """Change the key of a macro / script / Python ``[[bind]]`` (the first key; extra
    keys become copies of the entry; no keys removes the key but keeps the entry)."""
    binds = list(data.get("bind", []))
    entry = dict(binds[index])
    copies = [dict(entry, key=k) for k in keys[1:]]
    if keys:
        entry["key"] = keys[0]
    else:
        entry.pop("key", None)
    binds[index:index + 1] = [entry] + copies
    _store(data, binds, list(data.get("unbind", [])))


def reset(data: dict[str, Any], target: str) -> None:
    """Back to the built-in keys of one action or command."""
    binds = [b for b in data.get("bind", []) if not (_global(b) and b.get("command") == target)]
    unbinds = [u for u in data.get("unbind", []) if not (_global(u) and u.get("command") == target)]
    _store(data, binds, unbinds)


def reset_all(data: dict[str, Any]) -> None:
    """Drop every change to built-in shortcuts (macros, scripts and modes stay)."""
    binds = [b for b in data.get("bind", []) if not (_global(b) and "command" in b)]
    unbinds = [u for u in data.get("unbind", []) if not _global(u)]
    _store(data, binds, unbinds)


def _target_of(entry: dict[str, Any]) -> str | None:
    return entry.get("command")


def _store(data: dict[str, Any], binds: list, unbinds: list) -> None:
    for key, value in (("bind", binds), ("unbind", unbinds)):
        if value:
            data[key] = value
        else:
            data.pop(key, None)
