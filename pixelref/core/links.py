"""Linked files: fingerprints, ``${NAME}`` path roots, packages and the resolve chain.

A node attribute of type ``path`` (a linked image, movie, sequence pattern, 3D
model or texture) names a file by *where* it is. This module also records
*what* it is: a **fingerprint** of the file (its size and the SHA-1 of its
first and last 64 KB, quick even for a 4 GB movie). The scene keeps one per
linked path in its top-level ``links`` section::

    "links": {
      "plates/clip.mp4": {"size": 7340032, "qh": "9b1câ€¦"},
      "renders/shot.####.exr": {"frames": 96, "frame": 1001, "first": {"size": 8841220, "qh": "â€¦"}}
    }

A stored path is one of: relative to the scene's folder, ``${NAME}/rest``
(``NAME`` a root the user named in settings, else an environment variable), or
absolute. :meth:`LinkResolver.resolve` turns it into a file with a fixed, short
chain and never searches the disk:

1. the stored path itself (one ``stat``); when it is not there, the part of it
   below one of the board's path variables is tried below each of them (the
   Canvas panel's "Path variables", scene plugin data ``path_search``: the
   board's own ``NAME = folder`` pairs, usable as ``${NAME}`` like a root);
2. the scene's package folder (``<scene>.files``, found by the id in the
   scene's ``package`` section), looked up by fingerprint in its manifest;
3. this machine's memory of earlier relinks (fingerprint -> path, in settings);
4. otherwise the stored path (the node shows its "missing" placeholder).

Steps 2 and 3 only ever accept a file whose fingerprint matches, so a file with
the right name but other content is never substituted. A file still found at
its stored path is used even when its content changed (editing a linked file
in place is normal); the record is refreshed on the next save.

Machine-side settings (JSON values): ``links/roots`` ({name: folder}),
``links/remembered`` ({"size:qh": path}) and ``links/packages`` ({id: folder}).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .session import Session

FINGERPRINT_BYTES = 64 * 1024
LINKS_SECTION = "links"
PACKAGE_SECTION = "package"
MANIFEST_NAME = "manifest.json"
FILES_SUFFIX = ".files"
ROOTS_KEY = "links/roots"
REMEMBERED_KEY = "links/remembered"
PACKAGES_KEY = "links/packages"
MAX_REMEMBERED = 20000
SEARCH_KEY = "path_search"  # scene plugin data: {"vars": {"STUDIO_DRIVE": "W:/", …}}, saved with the board

_TOKEN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}(?:[/\\](.*))?$", re.DOTALL)
_ROOT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HASHES = re.compile(r"#+")
_NUMBERED = re.compile(r"^(?P<prefix>.*?)(?P<digits>\d+)(?P<suffix>\.[A-Za-z0-9]+)$")

_FP_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
_FP_LOCK = threading.Lock()


def parse_variables(value: Any) -> dict[str, str]:
    """A board's path variables from a ``{NAME: folder}`` mapping or from what a user typed, one
    ``NAME = folder`` per line (or separated by ``;``): ``STUDIO_DRIVE = W:\\`` and
    ``LINUX_PATH = /mnt/drive``. Folders are kept with forward slashes. Raises ValueError."""
    if isinstance(value, dict):
        pairs = [(str(k), str(v)) for k, v in value.items()]
    else:
        pairs = []
        for line in re.split(r"[\r\n;]+", str(value or "")):
            if not line.strip():
                continue
            name, sep, folder = line.partition("=")
            if not sep:
                raise ValueError(f"Write each variable as NAME = folder (got '{line.strip()}')")
            pairs.append((name, folder))
    result: dict[str, str] = {}
    for name, folder in pairs:
        name = name.strip().strip("%").lstrip("$").strip("{}").strip()
        folder = folder.strip().strip('"').strip("'").strip()
        if not _ROOT_NAME.match(name):
            raise ValueError(f"Not a variable name: '{name}' (letters, digits and _, not starting with a digit)")
        if not folder:
            raise ValueError(f"{name} has no folder")
        result[name] = folder.replace("\\", "/")
    return result


def _below(text: str, folder: str) -> str | None:
    """What is left of ``text`` below ``folder``, compared as text (a ``W:/`` path means nothing to
    ``pathlib`` on Linux), ignoring case and slash direction; None when it is not below it."""
    path = text.replace("\\", "/")
    base = folder.replace("\\", "/").rstrip("/")
    if not base or not path.lower().startswith(base.lower()):
        return None
    rest = path[len(base):]
    if rest and not rest.startswith("/"):
        return None
    return rest.lstrip("/")


# ---- Fingerprints ---------------------------------------------------------------------------


def fingerprint(path: str | Path) -> dict[str, Any] | None:
    """``{"size", "qh"}`` of a file (None when it is not a readable file).

    ``qh`` is the SHA-1 of the first and last 64 KB. Results are cached by
    (path, size, modification time), so asking again costs one ``stat``.
    """
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    key = os.path.normcase(os.path.abspath(str(path)))
    with _FP_LOCK:
        cached = _FP_CACHE.get(key)
    if cached is not None and cached[0] == info.st_size and cached[1] == info.st_mtime_ns:
        return dict(cached[2])
    digest = hashlib.sha1()
    try:
        with open(path, "rb") as handle:
            digest.update(handle.read(FINGERPRINT_BYTES))
            if info.st_size > FINGERPRINT_BYTES:
                handle.seek(max(FINGERPRINT_BYTES, info.st_size - FINGERPRINT_BYTES))
                digest.update(handle.read(FINGERPRINT_BYTES))
    except OSError:
        return None
    record = {"size": int(info.st_size), "qh": digest.hexdigest()}
    with _FP_LOCK:
        _FP_CACHE[key] = (info.st_size, info.st_mtime_ns, record)
    return dict(record)


def full_hash(path: str | Path, chunk: int = 1024 * 1024) -> str:
    """The streamed SHA-1 of a whole file (packaging and Verify Package); raises OSError."""
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def same_content(a: Any, b: Any) -> bool:
    """Whether two fingerprints (``{"size", "qh"}``) describe the same content."""
    return (isinstance(a, dict) and isinstance(b, dict) and bool(a.get("qh"))
            and a.get("qh") == b.get("qh") and _int(a.get("size")) == _int(b.get("size")))


def fingerprint_key(fp: dict[str, Any]) -> str:
    """The text key of a fingerprint in lookup tables (``"size:qh"``)."""
    return f"{_int(fp.get('size'))}:{fp.get('qh', '')}"


def record_fingerprint(record: Any) -> dict[str, Any] | None:
    """The fingerprint inside a ``links`` record: the file's own, or a sequence's first frame's."""
    if not isinstance(record, dict):
        return None
    if isinstance(record.get("first"), dict):
        record = record["first"]
    return record if record.get("qh") and "size" in record else None


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


# ---- Sequence patterns (``shot.####.exr``; see builtins/sequence/seqscan for the full set) ---------


def is_pattern(text: str | Path) -> bool:
    """Whether a stored path is an image sequence pattern (``#`` in its file name)."""
    return "#" in Path(str(text)).name


def _split(pattern: str) -> tuple[str, int, str] | None:
    name = Path(pattern).name
    runs = list(_HASHES.finditer(name))
    if not runs:
        return None
    last = runs[-1]
    return name[:last.start()], len(last.group(0)), name[last.end():]


def frame_file(pattern: str | Path, frame: int) -> Path:
    """The file of one frame of a ``#`` pattern."""
    pattern = Path(pattern)
    parts = _split(str(pattern))
    if parts is None:
        return pattern
    head, pad, tail = parts
    number = f"-{abs(int(frame)):0{pad}d}" if frame < 0 else f"{int(frame):0{pad}d}"
    return pattern.with_name(f"{head}{number}{tail}")


def scan_frames(pattern: str | Path) -> list[tuple[int, Path]]:
    """``(frame, file)`` of every frame of a ``#`` pattern on disk, in frame order."""
    pattern = Path(pattern)
    parts = _split(str(pattern))
    if parts is None:
        return []
    head, pad, tail = parts
    try:
        names = os.listdir(pattern.parent)
    except OSError:
        return []
    low_head, low_tail = head.lower(), tail.lower()
    frames: dict[int, Path] = {}
    for name in names:
        low = name.lower()
        if len(name) <= len(head) + len(tail) or not (low.startswith(low_head) and low.endswith(low_tail)):
            continue
        digits = name[len(head):len(name) - len(tail)]
        if not digits.isdigit():
            continue
        fits = (digits == "0" or not digits.startswith("0")) if pad <= 1 else (
            len(digits) == pad or (len(digits) > pad and not digits.startswith("0")))
        if fits:
            frames.setdefault(int(digits), pattern.parent / name)
    return sorted(frames.items())


def pattern_beside(found: str | Path, pattern: str | Path) -> Path:
    """The pattern of the sequence ``found`` (one frame file) belongs to, with ``pattern``'s padding."""
    found = Path(found)
    parts = _split(str(pattern))
    match = _NUMBERED.match(found.name)
    if parts is None or match is None:
        return found
    pad = parts[1] if parts[1] > 1 else (len(match.group("digits")) if len(match.group("digits")) >= 4 else 1)
    return found.with_name(f"{match.group('prefix')}{'#' * pad}{match.group('suffix')}")


def link_record(path: str | Path) -> dict[str, Any] | None:
    """The ``links`` record of a linked file as it is now on disk (None when it is missing)."""
    if is_pattern(path):
        frames = scan_frames(path)
        if not frames:
            return None
        first = fingerprint(frames[0][1])
        if first is None:
            return None
        return {"frames": len(frames), "frame": frames[0][0], "first": first}
    return fingerprint(path)


# ---- Packages ----------------------------------------------------------------------------------


def read_manifest(folder: str | Path) -> dict[str, Any] | None:
    """A package folder's ``manifest.json`` (``{"id", "files": [{"path", "size", "qh", "sha1"}]}``), or None."""
    try:
        data = json.loads((Path(folder) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not isinstance(data.get("files"), list):
        return None
    data["files"] = [f for f in data["files"] if isinstance(f, dict) and isinstance(f.get("path"), str)]
    return data


def write_json_atomic(path: str | Path, data: Any) -> None:
    """Write JSON through a sibling ``.tmp`` file swapped in (an interrupted write leaves the old file)."""
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def safe_inside(folder: Path, relative: str) -> Path | None:
    """``folder / relative`` when it stays inside ``folder`` (manifest paths come from files)."""
    try:
        target = (folder / relative).resolve()
        target.relative_to(folder.resolve())
    except (ValueError, OSError):
        return None
    return target


# ---- The resolver ------------------------------------------------------------------------------


class LinkResolver:
    """Stored path <-> file on this machine, for one session (``session.links``)."""

    def __init__(self, session: "Session") -> None:
        self.session = session
        # (scene folder, stored value) -> a file found by step 2 or 3, written into the scene on save.
        self._healed: dict[tuple[str, str], str] = {}
        self._package: tuple[tuple[str, str], Path | None] | None = None
        self._manifests: dict[str, tuple[int, dict[str, str]]] = {}

    @property
    def settings(self) -> Any:
        return self.session.settings

    def forget(self) -> None:
        """Drop cached lookups (after relinking, or when package folders moved)."""
        self._healed.clear()
        self._package = None
        self._manifests.clear()

    # ---- Named roots -------------------------------------------------------------------------

    def roots(self) -> dict[str, str]:
        """The user's named roots, ``{NAME: folder}``."""
        value = self.settings.json_value(ROOTS_KEY, {})
        if not isinstance(value, dict):
            return {}
        return {str(k): str(v) for k, v in value.items() if _ROOT_NAME.match(str(k)) and v}

    def set_root(self, name: str, folder: str | Path) -> None:
        name = str(name).strip()
        if not _ROOT_NAME.match(name):
            raise ValueError(f"Not a valid root name: '{name}' (letters, digits and _, not starting with a digit)")
        roots = self.roots()
        roots[name] = Path(folder).as_posix()
        self.settings.set_json(ROOTS_KEY, roots)
        self.forget()

    def remove_root(self, name: str) -> bool:
        roots = self.roots()
        if roots.pop(str(name), None) is None:
            return False
        self.settings.set_json(ROOTS_KEY, roots)
        self.forget()
        return True

    # ---- The board's path variables -----------------------------------------------------------

    def variables(self) -> dict[str, str]:
        """The board's own path variables, ``{NAME: folder}`` (the Canvas panel's "Path variables";
        scene plugin data, saved with the board): ``STUDIO_DRIVE = W:/``, ``LINUX_PATH = /mnt/drive``."""
        data = self.session.scene.plugin_data.get(SEARCH_KEY)
        values = data.get("vars") if isinstance(data, dict) else None
        if not isinstance(values, dict):
            return {}
        return {k: v for k, v in values.items()
                if isinstance(k, str) and isinstance(v, str) and v and _ROOT_NAME.match(k)}

    def value_of(self, name: str) -> str | None:
        """What ``${name}`` stands for here: a named root of this machine, else an environment
        variable, else the board's own variable (the board's value is the default)."""
        return self.roots().get(name) or os.environ.get(name) or self.variables().get(name) or None

    def variable_folders(self) -> list[tuple[str, str]]:
        """``(NAME, folder)`` of the board's variables as they resolve on this machine."""
        return [(name, self.value_of(name) or folder) for name, folder in self.variables().items()]

    def search(self, text: str, record: dict[str, Any] | None = None) -> Path | None:
        """A stored path that is not where it says, found below one of the board's variables.

        The board's variables are treated as names for the same places on different machines
        (``W:/`` here, ``/mnt/drive`` there): the part of the path below a variable — after
        ``${NAME}/``, after a variable's folder in a full path, or a whole relative path — is tried
        below every variable's folder."""
        folders = self.variable_folders()
        if not text or not folders:
            return None
        match = _TOKEN.match(text)
        if match is not None:
            rest: str | None = match.group(2) or ""
        elif Path(text).is_absolute() or re.match(r"^[A-Za-z]:[/\\]", text):
            every = [folder for _name, folder in folders] + list(self.variables().values())
            rest = next((left for left in (_below(text, folder) for folder in every) if left), None)
        else:
            rest = text
        if not rest:
            return None
        rest = rest.replace("\\", "/")
        for _name, folder in folders:
            found = Path(folder) / rest
            if self.present(found, record):
                return found
        return None

    def expand(self, text: str) -> str | None:
        """``${NAME}/rest`` with NAME replaced (a named root, else an environment variable).
        Text without a token is returned as is; None when the name is not set."""
        match = _TOKEN.match(text)
        if match is None:
            return text
        name, rest = match.group(1), match.group(2) or ""
        base = self.value_of(name)
        if not base:
            return None
        return os.path.join(base, rest) if rest else base

    # ---- Stored value -> file -----------------------------------------------------------------

    def _base(self) -> Path:
        return self.session.base_dir()

    def candidate(self, value: str | Path) -> Path | None:
        """Step 1: where the stored value points (None for a ``${NAME}`` that is not set)."""
        expanded = self.expand(str(value))
        if expanded is None:
            return None
        path = Path(expanded)
        return path if path.is_absolute() else self._base() / path

    def records(self) -> dict[str, Any]:
        section = self.session.scene.sections.get(LINKS_SECTION)
        return section if isinstance(section, dict) else {}

    def record(self, value: str | Path) -> dict[str, Any] | None:
        records = self.records()
        if not records:
            return None
        text = str(value)
        record = records.get(text)
        if record is None and "\\" in text:
            record = records.get(text.replace("\\", "/"))
        return record if isinstance(record, dict) else None

    def resolve(self, value: str | Path) -> Path:
        """The file a stored path means on this machine (the chain in the module docstring)."""
        text = str(value)
        primary = self.candidate(text)
        record = self.record(text) if text else None
        searching = bool(text) and bool(self.variables())
        if record is None and not searching:
            return primary if primary is not None else self._base() / text
        if primary is not None and self.present(primary, record):
            return primary
        if searching:
            found = self.search(text, record)
            if found is not None:
                return found
        if record is None:
            return primary if primary is not None else self._base() / text
        key = (str(self._base()), text)
        healed = self._healed.get(key)
        if healed is not None and self.present(Path(healed), record):
            return Path(healed)
        found = self.substitute(text, record)
        if found is not None:
            self._healed[key] = str(found)
            return found
        return primary if primary is not None else self._base() / text

    def healed(self, value: str) -> bool:
        """Whether ``value`` currently resolves to a substitute (steps 2-3), not its stored path."""
        return (str(self._base()), str(value)) in self._healed

    @staticmethod
    def present(path: Path, record: dict[str, Any] | None = None) -> bool:
        if is_pattern(path):
            if record is not None and isinstance(record.get("frame"), int):
                if frame_file(path, record["frame"]).is_file():
                    return True
            return bool(scan_frames(path))
        return path.exists()

    def substitute(self, value: str, record: dict[str, Any]) -> Path | None:
        """Steps 2 and 3: a file elsewhere with the recorded content, or None."""
        fp = record_fingerprint(record)
        if fp is None:
            return None
        sequence = is_pattern(value)
        folder = self.package_folder()
        if folder is not None:
            relative = self._manifest_index(folder).get(fingerprint_key(fp))
            found = safe_inside(folder, relative) if relative else None
            if found is not None and same_content(fingerprint(found), fp):
                return pattern_beside(found, value) if sequence else found
        remembered = self.settings.json_value(REMEMBERED_KEY, {})
        if isinstance(remembered, dict):
            path = remembered.get(fingerprint_key(fp))
            if isinstance(path, str) and same_content(fingerprint(path), fp):
                return pattern_beside(path, value) if sequence else Path(path)
        return None

    def status(self, value: str) -> str:
        """``"ok"``, ``"changed"`` (found at its path, content differs from the record),
        ``"missing"`` or ``"unrecorded"`` (found; saved before fingerprints)."""
        path = self.resolve(value)
        record = self.record(value)
        if not self.present(path, record):
            return "missing"
        if record is None:
            return "unrecorded"
        current = link_record(path)
        return "ok" if same_content(record_fingerprint(current), record_fingerprint(record)) else "changed"

    def note(self, value: str) -> str:
        """What an inspector's Storage row adds after a linked path: missing, changed since the
        board was saved (the record follows on the next save), or found somewhere else."""
        status = self.status(value)
        if status == "missing":
            return "  (missing)"
        if status == "changed":
            return "  (changed)"
        if self.healed(value):
            return f"  (found at {self.resolve(value).as_posix()})"
        return ""

    # ---- File -> stored value ------------------------------------------------------------------

    def portable(self, path: str | Path, scene_dir: str | Path | None) -> str:
        """How to store ``path`` for a scene in ``scene_dir``: relative when inside it, else
        ``${ROOT}/â€¦`` under the longest matching named root, else absolute."""
        path = Path(path)
        if scene_dir is not None:
            try:
                return path.resolve().relative_to(Path(scene_dir).resolve()).as_posix()
            except (ValueError, OSError):
                pass
        # The board's variables count as roots here; a named root of the same name wins.
        named = {**dict(self.variable_folders()), **self.roots()}
        roots = sorted(named.items(), key=lambda item: len(item[1]), reverse=True)
        if roots:
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            for name, folder in roots:
                try:
                    rest = resolved.relative_to(Path(folder).resolve()).as_posix()
                except (ValueError, OSError):
                    continue
                return f"${{{name}}}" if rest in ("", ".") else f"${{{name}}}/{rest}"
        return path.as_posix()

    # ---- Packages ------------------------------------------------------------------------------

    def package_id(self) -> str | None:
        section = self.session.scene.sections.get(PACKAGE_SECTION)
        value = section.get("id") if isinstance(section, dict) else None
        return value if isinstance(value, str) and value else None

    def package_folder(self) -> Path | None:
        """The scene's package folder: ``<scene stem>.files`` beside it, else a sibling folder
        whose manifest has the scene's package id, else where this machine last saw that id."""
        package_id = self.package_id()
        if package_id is None:
            return None
        scene_path = self.session.scene_path
        key = (str(scene_path), package_id)
        if self._package is not None and self._package[0] == key:
            return self._package[1]
        found = self._find_package(package_id)
        self._package = (key, found)
        return found

    def _find_package(self, package_id: str) -> Path | None:
        def ours(folder: Path) -> bool:
            manifest = read_manifest(folder)
            return manifest is not None and manifest["id"] == package_id

        scene_path = self.session.scene_path
        if scene_path is not None:
            base = scene_path.parent
            standard = base / f"{scene_path.stem}{FILES_SUFFIX}"
            if ours(standard):
                return standard
            try:
                siblings = sorted(p for p in base.iterdir() if p.is_dir() and p != standard)
            except OSError:
                siblings = []
            for folder in siblings:
                if (folder / MANIFEST_NAME).is_file() and ours(folder):
                    return folder
        known = self.settings.json_value(PACKAGES_KEY, {})
        folder = known.get(package_id) if isinstance(known, dict) else None
        if isinstance(folder, str) and ours(Path(folder)):
            return Path(folder)
        return None

    def _manifest_index(self, folder: Path) -> dict[str, str]:
        """``{"size:qh": relative path}`` of a package folder's manifest (cached by its mtime)."""
        try:
            mtime = (folder / MANIFEST_NAME).stat().st_mtime_ns
        except OSError:
            return {}
        cached = self._manifests.get(str(folder))
        if cached is not None and cached[0] == mtime:
            return cached[1]
        manifest = read_manifest(folder) or {"files": []}
        index = {fingerprint_key(f): f["path"] for f in manifest["files"] if f.get("qh")}
        self._manifests[str(folder)] = (mtime, index)
        return index

    def remember_package(self, package_id: str, folder: str | Path) -> None:
        known = self.settings.json_value(PACKAGES_KEY, {})
        known = dict(known) if isinstance(known, dict) else {}
        known[str(package_id)] = Path(folder).as_posix()
        self.settings.set_json(PACKAGES_KEY, known)
        self._package = None

    # ---- This machine's memory -----------------------------------------------------------------

    def remember(self, fp: dict[str, Any], path: str | Path) -> None:
        """Remember where content with this fingerprint is on this machine (other boards heal too)."""
        if not fp or not fp.get("qh"):
            return
        remembered = self.settings.json_value(REMEMBERED_KEY, {})
        remembered = dict(remembered) if isinstance(remembered, dict) else {}
        key = fingerprint_key(fp)
        remembered.pop(key, None)  # re-inserted last: the oldest entries go first when full
        remembered[key] = Path(path).as_posix()
        while len(remembered) > MAX_REMEMBERED:
            remembered.pop(next(iter(remembered)))
        self.settings.set_json(REMEMBERED_KEY, remembered)

    # ---- Saving ----------------------------------------------------------------------------------

    def path_attrs(self) -> list[tuple[str, str, str]]:
        """``(node, attribute, stored value)`` of every linked path in the scene (attributes of type
        ``path``; a path whose matching asset attribute is set is embedded and skipped)."""
        scene = self.session.scene
        found = []
        for node in scene.iter_nodes():
            for key, value in node.attrs.items():
                if not isinstance(value, str) or not value:
                    continue
                spec = scene.attr_spec(node, key)
                if spec is None or spec.type != "path":
                    continue
                if key.endswith("path") and node.attrs.get(key[:-4] + "asset"):
                    continue  # path -> asset, texture_path -> texture_asset: embedded wins
                found.append((node.name, key, value))
        return found

    def prepare_save(self, target: str | Path) -> None:
        """The save-time pass: every linked path re-expressed for a scene at ``target`` (relative,
        ``${ROOT}/â€¦`` or absolute; healed links get their found path) and the ``links`` records
        refreshed. Runs while ``session.scene_path`` is still the old location."""
        scene = self.session.scene
        new_dir = Path(target).parent
        old_records = self.records()
        records: dict[str, Any] = {}
        changes: list[tuple[str, str, str]] = []
        for name, key, value in self.path_attrs():
            source = self.resolve(value)
            if _TOKEN.match(value) and not self.healed(value) and self.candidate(value) is not None:
                new_value = value  # a ${NAME} the user (or a pipeline) wrote and that still works: kept
            elif _TOKEN.match(value) and self.candidate(value) is None and not self.healed(value):
                new_value = value  # an unset variable: kept, so it works where it is set
            else:
                new_value = self.portable(source, new_dir)
            record = link_record(source) if self.present(source) else None
            if record is None:
                record = old_records.get(value)  # missing now: keep what it was, to heal it later
            if isinstance(record, dict):
                records[new_value] = record
            if new_value != value:
                changes.append((name, key, new_value))
        for name, key, new_value in changes:
            scene.set_attr(name, key, new_value)
        if records:
            scene.sections[LINKS_SECTION] = records
        else:
            scene.sections.pop(LINKS_SECTION, None)
        self._healed.clear()
