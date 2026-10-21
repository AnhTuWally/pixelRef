"""The plugin activity monitor: what third-party plugins actually do.

One Python audit hook (:func:`sys.addaudithook`) is installed for the process
before any plugin is imported. For a handful of events it works out whether a
third-party plugin's code is on the call stack and, if so, records the event
against that plugin, comparing it with the ``[permissions]`` its manifest
declares:

* ``write_files``     a file opened for writing, deleted, renamed or truncated (the temp folder excepted)
* ``read_sensitive``  a file read in a place that holds secrets (SSH keys, browser profiles…)
* ``network``         a socket connecting or a host name looked up
* ``subprocess``      a program started
* ``native_code``     a native library loaded or a symbol looked up with ctypes
* ``dynamic_code``    code compiled or run from a string by the plugin itself

It **reports**; it never blocks. It isn't a sandbox: code that goes straight to
the operating system, runs on threads it started from strings, or hides its
frames can do things this never sees. Built-in plugins aren't watched. The
monitor can't be removed once installed (Python doesn't allow that); turning it
off makes the hook return at once.
"""

from __future__ import annotations

import collections
import json
import os
import sys
import tempfile
import threading
import time
import weakref
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

KINDS = {
    "write_files": "Wrote a file",
    "read_sensitive": "Read a sensitive file",
    "network": "Used the network",
    "subprocess": "Started a program",
    "native_code": "Used native code (ctypes)",
    "dynamic_code": "Ran code from a string",
}
NETWORK_EVENTS = frozenset({"socket.connect", "socket.getaddrinfo", "socket.sendto", "socket.bind"})
PROCESS_EVENTS = frozenset({"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
                            "os.startfile", "os.fork"})
NATIVE_EVENTS = frozenset({"ctypes.dlopen", "ctypes.dlsym", "ctypes.dlsym/handle"})
FILE_EVENTS = frozenset({"os.remove", "os.rename", "os.rmdir", "os.truncate", "shutil.rmtree"})
WATCHED = (frozenset({"open", "exec", "compile"}) | NETWORK_EVENTS | PROCESS_EVENTS | NATIVE_EVENTS
           | FILE_EVENTS)
BUFFER = 500  # entries kept per plugin
MAX_DEPTH = 80
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_PIXELREF_ROOT = os.path.normcase(str(Path(__file__).resolve().parents[1])) + os.sep


@dataclass
class ActivityEntry:
    time: float
    plugin: str
    kind: str
    event: str
    detail: str
    file: str
    line: int
    via_pixelref: bool  # pixelRef code did it, called (directly or not) by the plugin
    declared: bool  # the manifest's [permissions] allows it

    @property
    def violation(self) -> bool:
        return not self.declared and not self.via_pixelref

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "violation": self.violation, "label": KINDS.get(self.kind, self.kind)}


def sensitive_folders() -> list[str]:
    home = Path.home()
    names = [".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker", ".netrc", ".git-credentials", ".pypirc",
             ".config/gcloud", ".config/gh", ".mozilla", ".config/google-chrome", ".config/chromium",
             "Library/Keychains", "Library/Application Support/Google/Chrome", "Library/Application Support/Firefox",
             "Library/Cookies"]
    folders = [home / name for name in names]
    for variable, subpaths in (("LOCALAPPDATA", ("Google/Chrome/User Data", "Microsoft/Edge/User Data",
                                                 "Microsoft/Credentials", "BraveSoftware")),
                               ("APPDATA", ("Mozilla/Firefox", "Microsoft/Credentials", "Microsoft/Protect"))):
        base = os.environ.get(variable)
        if base:
            folders += [Path(base) / sub for sub in subpaths]
    return [os.path.normcase(str(f)) for f in folders]


class ActivityMonitor:
    """Records third-party plugins' activity (see the module docstring)."""

    def __init__(self, log_path: Path | None = None, enabled: bool = True) -> None:
        self.enabled = enabled
        self.log_path = log_path  # violations are appended here as JSON lines
        self._roots: list[tuple[str, str]] = []  # (normcased folder + sep, plugin id), longest first
        self._permissions: dict[str, frozenset[str]] = {}
        self._owners: dict[str, str] = {}  # code file -> "plugin id", "" (pixelRef) or "-" (anything else)
        self._entries: dict[str, collections.deque[ActivityEntry]] = {}
        self._counts: dict[str, collections.Counter[str]] = {}
        self._violations: dict[str, int] = {}
        self._lock = threading.Lock()
        self._local = threading.local()
        self._sensitive = sensitive_folders()
        self._home = os.path.normcase(str(Path.home()))
        # Writes here aren't reported (scratch files are everyday plugin business).
        self.quiet_folders = [os.path.normcase(os.path.realpath(tempfile.gettempdir())) + os.sep]
        self.revision = 0
        self._listeners: list[Callable[[ActivityEntry], None]] = []
        install(self)

    # ---- Plugins -----------------------------------------------------------------------------

    def watch(self, plugin_id: str, folder: Path, allowed: frozenset[str] | set[str] = frozenset()) -> None:
        root = os.path.normcase(str(Path(folder).resolve())) + os.sep
        with self._lock:
            self._roots = sorted([r for r in self._roots if r[1] != plugin_id] + [(root, plugin_id)],
                                 key=lambda r: -len(r[0]))
            self._permissions[plugin_id] = frozenset(allowed)
            self._owners.clear()

    def unwatch(self, plugin_id: str) -> None:
        with self._lock:
            self._roots = [r for r in self._roots if r[1] != plugin_id]
            self._permissions.pop(plugin_id, None)
            self._owners.clear()

    def watched(self) -> list[str]:
        return [plugin_id for _root, plugin_id in self._roots]

    @property
    def active(self) -> bool:
        return self.enabled and bool(self._roots)

    # ---- Results -----------------------------------------------------------------------------

    def entries(self, plugin_id: str) -> list[ActivityEntry]:
        with self._lock:
            return list(self._entries.get(plugin_id, ()))

    def summary(self, plugin_id: str) -> dict[str, Any]:
        with self._lock:
            counts = dict(self._counts.get(plugin_id, {}))
            violations = self._violations.get(plugin_id, 0)
        return {"watched": plugin_id in self.watched(), "enabled": self.enabled, "counts": counts,
                "violations": violations}

    def violations(self, plugin_id: str) -> int:
        return self._violations.get(plugin_id, 0)

    def clear(self, plugin_id: str | None = None) -> None:
        with self._lock:
            for store in (self._entries, self._counts, self._violations):
                if plugin_id is None:
                    store.clear()
                else:
                    store.pop(plugin_id, None)
            self.revision += 1

    def subscribe(self, callback: Callable[[ActivityEntry], None]) -> Callable[[], None]:
        """``callback(entry)`` for every recorded entry (on whatever thread caused it)."""
        self._listeners.append(callback)
        return lambda: callback in self._listeners and self._listeners.remove(callback)

    # ---- The hook ---------------------------------------------------------------------------------

    def on_event(self, event: str, args: tuple) -> None:
        if not self.enabled or not self._roots or getattr(self._local, "busy", False):
            return
        self._local.busy = True
        try:
            found = self._classify(event, args)
            if found is not None:
                self._attribute(event, *found)
        except Exception:  # noqa: BLE001  (a monitor bug must never break the call it watches)
            pass
        finally:
            self._local.busy = False

    def _classify(self, event: str, args: tuple) -> tuple[str, str] | None:
        if event == "open":
            path, mode, flags = (tuple(args) + (None, None, 0))[:3]
            if not isinstance(path, (str, bytes, os.PathLike)):
                return None  # a file descriptor
            if isinstance(mode, str):
                writing = any(c in mode for c in "wax+")
            else:
                writing = bool((flags or 0) & _WRITE_FLAGS)
            text = os.fsdecode(path)
            if writing:
                full = os.path.normcase(os.path.realpath(text))
                if any(full.startswith(folder) for folder in self.quiet_folders):
                    return None
                return "write_files", text
            normal = os.path.normcase(os.path.abspath(text))
            if normal.startswith(self._home) and any(normal == f or normal.startswith(f + os.sep)
                                                      for f in self._sensitive):
                return "read_sensitive", text
            return None
        if event in FILE_EVENTS:
            path = args[0] if args else None
            if not isinstance(path, (str, bytes, os.PathLike)):
                return None
            text = os.fsdecode(path)
            full = os.path.normcase(os.path.realpath(text))
            if any(full.startswith(folder) for folder in self.quiet_folders):
                return None
            return "write_files", f"{event.split('.')[-1]} {text}"
        if event in NETWORK_EVENTS:
            return "network", _short(args)
        if event in PROCESS_EVENTS:
            return "subprocess", _short(args)
        if event in NATIVE_EVENTS:
            return "native_code", _short(args)
        if event == "exec":
            code = args[0] if args else None
            filename = getattr(code, "co_filename", "")
            return ("dynamic_code", filename) if filename.startswith("<") else None
        if event == "compile":
            filename = args[1] if len(args) > 1 else ""
            filename = os.fsdecode(filename) if isinstance(filename, (bytes, os.PathLike)) else str(filename)
            return ("dynamic_code", filename) if filename.startswith("<") else None
        return None

    def _owner(self, filename: str) -> str:
        owner = self._owners.get(filename)
        if owner is None:
            normal = os.path.normcase(filename)
            owner = next((plugin_id for root, plugin_id in self._roots if normal.startswith(root)), None)
            if owner is None:
                owner = "" if normal.startswith(_PIXELREF_ROOT) else "-"
            self._owners[filename] = owner
        return owner

    def _attribute(self, event: str, kind: str, detail: str) -> None:
        frame = sys._getframe(3)  # on_event <- _hook <- (C) <- the code that caused the event
        via_pixelref = False
        first = True
        depth = 0
        while frame is not None and depth < MAX_DEPTH:
            owner = self._owner(frame.f_code.co_filename)
            if owner not in ("", "-"):
                if kind == "dynamic_code" and not first:
                    return  # the standard library (dataclasses, namedtuple…) compiling its own code
                self._record(ActivityEntry(
                    time=time.time(), plugin=owner, kind=kind, event=event, detail=detail[:300],
                    file=frame.f_code.co_filename, line=frame.f_lineno, via_pixelref=via_pixelref,
                    declared=kind in self._permissions.get(owner, frozenset())))
                return
            if owner == "":
                via_pixelref = True
            first = False
            frame = frame.f_back
            depth += 1

    def _record(self, entry: ActivityEntry) -> None:
        with self._lock:
            self._entries.setdefault(entry.plugin, collections.deque(maxlen=BUFFER)).append(entry)
            self._counts.setdefault(entry.plugin, collections.Counter())[entry.kind] += 1
            if entry.violation:
                self._violations[entry.plugin] = self._violations.get(entry.plugin, 0) + 1
            self.revision += 1
        if entry.violation and self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(entry.to_dict()) + "\n")
            except OSError:
                pass
        for callback in list(self._listeners):
            try:
                callback(entry)
            except Exception:  # noqa: BLE001
                pass


def _short(args: tuple) -> str:
    parts = []
    for value in args[:3]:
        text = repr(value) if not isinstance(value, str) else value
        parts.append(text if len(text) < 120 else text[:117] + "…")
    return ", ".join(parts)


# ---- The process-wide hook ---------------------------------------------------------------------------------

_MONITORS: "weakref.WeakSet[ActivityMonitor]" = weakref.WeakSet()
_INSTALLED = False


def _hook(event: str, args: tuple) -> None:
    if event not in WATCHED or not _MONITORS:
        return
    for monitor in list(_MONITORS):
        monitor.on_event(event, args)


def install(monitor: ActivityMonitor) -> None:
    global _INSTALLED
    _MONITORS.add(monitor)
    if not _INSTALLED:
        sys.addaudithook(_hook)
        _INSTALLED = True
