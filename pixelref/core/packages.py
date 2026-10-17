"""Python packages that plugins need, and pixelRef's own folder for them.

A plugin declares the Python packages it needs in ``plugin.toml``::

    [python]
    requires = ["numpy>=1.26"]                    # can't activate without these
    optional = ["scipy"]                          # used when present
    # a table when the import name differs from the package name:
    # requires = [{ name = "opencv-python", import = "cv2", version = ">=4.8" }]

Checking a requirement never imports the package: it reads the installed
package's metadata (or, for a bundled copy without metadata, only looks the
module up), so a plugin that needs numpy costs nothing until it uses it.

Packages the user installs from the Plugins window live in a folder pixelRef
manages (one per Python version and platform, because compiled packages only
work with the Python they were built for)::

    <packages root>/py313-win-amd64/
        site-packages/          on sys.path (after the app's own packages)
        site-packages.new/      a rebuilt folder that replaces it at the next start
        wheels/                 wheel files the user installed from disk
        packages.json           what the user asked for, and why

``packages.json`` is the source of truth. Adding a package installs it into
the live folder (no restart). Removing or updating one rebuilds the whole
folder from the list into ``site-packages.new``; it is swapped in at once
when none of the packages is loaded, otherwise at the next start (Windows
can't replace a loaded compiled module).
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import site
import subprocess
import sys
import sysconfig
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .errors import PluginError

MANIFEST_VERSION = 1
READY_MARKER = ".pixelref-ready"
# Packages the app itself is built on: a managed copy would shadow or break them.
PROTECTED = frozenset({"pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6", "pixelref", "pip"})

_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
_SPEC_RE = re.compile(r"^\s*(~=|==|!=|<=|>=|<|>)\s*([A-Za-z0-9.*+!_-]+)\s*$")


def normalize(name: str) -> str:
    """PEP 503 name: ``Pillow`` and ``pillow``, ``opencv_python`` and ``opencv-python`` match."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_version(text: str) -> tuple[int, ...]:
    """The leading release numbers of a version: ``2.1.0rc1`` → (2, 1, 0)."""
    numbers = []
    for part in str(text).strip().lstrip("vV").split("."):
        match = re.match(r"\d+", part)
        if match is None:
            break
        numbers.append(int(match.group()))
        if match.end() != len(part):
            break  # a pre/post/dev suffix ends the release part
    return tuple(numbers)


def _compare(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    size = max(len(a), len(b))
    a, b = a + (0,) * (size - len(a)), b + (0,) * (size - len(b))
    return (a > b) - (a < b)


def _matches(version: str, op: str, wanted: str) -> bool:
    have = parse_version(version)
    if wanted.endswith(".*") and op in ("==", "!="):
        prefix = parse_version(wanted[:-2])
        same = have[: len(prefix)] == prefix
        return same if op == "==" else not same
    want = parse_version(wanted)
    order = _compare(have, want)
    if op == "==":
        return order == 0
    if op == "!=":
        return order != 0
    if op == ">=":
        return order >= 0
    if op == "<=":
        return order <= 0
    if op == ">":
        return order > 0
    if op == "<":
        return order < 0
    if op == "~=":  # compatible release: ~=1.4.2 means >=1.4.2, ==1.4.*
        return order >= 0 and have[: max(1, len(want) - 1)] == want[: max(1, len(want) - 1)]
    return False


@dataclass(frozen=True)
class Requirement:
    """One Python package a plugin needs, e.g. ``numpy>=1.26``."""

    name: str
    specifiers: tuple[tuple[str, str], ...] = ()
    import_name: str = ""
    extras: str = ""

    @property
    def key(self) -> str:
        return normalize(self.name)

    @property
    def module(self) -> str:
        """The module to look up when the package has no metadata (bundled copies)."""
        return self.import_name or self.name.replace("-", "_").lower()

    @property
    def version_text(self) -> str:
        return ",".join(op + version for op, version in self.specifiers)

    def __str__(self) -> str:
        return f"{self.name}{self.extras}{self.version_text}"

    def allows(self, version: str) -> bool:
        return all(_matches(version, op, wanted) for op, wanted in self.specifiers)

    @classmethod
    def parse(cls, value: Any) -> "Requirement":
        """From ``"numpy>=1.26"`` or ``{name = "opencv-python", import = "cv2", version = ">=4.8"}``."""
        import_name = ""
        if isinstance(value, dict):
            if not isinstance(value.get("name"), str):
                raise PluginError(f"Python requirement {value!r} needs a 'name'")
            import_name = str(value.get("import", "") or "")
            value = f"{value['name']}{value.get('version', '') or ''}"
        if not isinstance(value, str):
            raise PluginError(f"Python requirement must be text or a table, got {value!r}")
        text = value.split(";", 1)[0].strip()  # environment markers aren't supported: ignore them
        match = _NAME_RE.match(text)
        if match is None:
            raise PluginError(f"Bad Python requirement {value!r}")
        name, extras, rest = match.group(1), match.group(2) or "", match.group(3).strip()
        specifiers = []
        for part in filter(None, (p.strip() for p in rest.split(","))):
            spec = _SPEC_RE.match(part)
            if spec is None:
                raise PluginError(f"Bad version in Python requirement {value!r}: {part!r}")
            specifiers.append((spec.group(1), spec.group(2)))
        return cls(name=name, specifiers=tuple(specifiers), import_name=import_name, extras=extras)


@dataclass
class PackageStatus:
    requirement: Requirement
    installed: str | None  # version; "" = importable but its version is unknown
    ok: bool
    location: str = ""  # "managed", "app" (the app's own environment), or ""

    @property
    def problem(self) -> str:
        if self.ok:
            return ""
        if self.installed is None:
            return f"needs Python package {self.requirement}"
        return f"needs Python package {self.requirement} (version {self.installed} is installed)"


def requirement_list(value: Any) -> list[Requirement]:
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        value = [value]
    if not isinstance(value, list):
        raise PluginError(f"expected a list of Python requirements, got {value!r}")
    return [Requirement.parse(item) for item in value]


# ---- The managed package folder --------------------------------------------------------------


def environment_tag() -> str:
    """``py313-win-amd64``: compiled packages only work with this Python on this platform."""
    platform = sysconfig.get_platform().replace(".", "_").replace("-", "_").replace("_", "-")
    return f"py{sys.version_info.major}{sys.version_info.minor}-{platform}"


def default_packages_root() -> Path:
    """Where managed packages go: ``$PIXELREF_PACKAGES_DIR``, else the per-user local data folder.

    Compiled packages are machine-specific, so on Windows this is the local
    (not roaming) application data folder.
    """
    override = os.environ.get("PIXELREF_PACKAGES_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "pixelRef" / "python"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "pixelRef" / "python"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "pixelRef" / "python"


def is_frozen() -> bool:
    """True in the standalone executable (PyInstaller sets ``sys.frozen``)."""
    return bool(getattr(sys, "frozen", False))


@dataclass
class WantedPackage:
    requirement: str  # e.g. "numpy>=1.26", or the file name of a wheel in wheels/
    plugins: list[str] = field(default_factory=list)  # plugins it was installed for
    user: bool = False  # installed by hand (not only because a plugin needed it)
    file: str = ""  # a wheel installed from disk (kept in wheels/)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"requirement": self.requirement}
        if self.plugins:
            data["plugins"] = sorted(set(self.plugins))
        if self.user:
            data["user"] = True
        if self.file:
            data["file"] = self.file
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WantedPackage":
        return cls(requirement=str(data.get("requirement", "")), plugins=[str(p) for p in data.get("plugins", [])],
                   user=bool(data.get("user", False)), file=str(data.get("file", "") or ""))


class PackageStore:
    """The managed folder for one Python version and platform."""

    def __init__(self, root: Path | None = None, tag: str | None = None) -> None:
        self.base = Path(root) if root is not None else default_packages_root()
        self.tag = tag or environment_tag()
        self.root = self.base / self.tag
        self.site = self.root / "site-packages"
        self.staging = self.root / "site-packages.new"
        self.wheels = self.root / "wheels"
        self.manifest_path = self.root / "packages.json"
        self.activated = False
        self.swap_error = ""

    # ---- Startup -----------------------------------------------------------------------------

    def activate(self) -> None:
        """Finish a pending rebuild, then put the folder on ``sys.path`` (idempotent).

        Call before any plugin is imported. ``site.addsitedir`` appends (the
        app's own packages win) and runs the folder's ``.pth`` files.
        """
        if self.activated:
            return
        self.activated = True
        if self.pending_swap:
            self._swap()
        if self.site.is_dir():
            self._add_to_path()

    def _add_to_path(self) -> None:
        if str(self.site) not in sys.path:
            site.addsitedir(str(self.site))
        importlib.invalidate_caches()

    @property
    def pending_swap(self) -> bool:
        return (self.staging / READY_MARKER).exists()

    def _swap(self) -> bool:
        """Replace site-packages with the rebuilt folder. False if a file is in use."""
        old = self.root / f"site-packages.old-{int(time.time())}"
        try:
            if self.site.exists():
                os.replace(self.site, old)
            os.replace(self.staging, self.site)
        except OSError as exc:
            if not self.site.exists() and old.exists():
                os.replace(old, self.site)  # put the old one back rather than have none
            self.swap_error = str(exc)
            return False
        (self.site / READY_MARKER).unlink(missing_ok=True)
        self.swap_error = ""
        for leftover in self.root.glob("site-packages.old-*"):
            shutil.rmtree(leftover, ignore_errors=True)  # older failed cleanups too
        return True

    # ---- packages.json -----------------------------------------------------------------------

    def wanted(self) -> dict[str, WantedPackage]:
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        packages = data.get("packages") if isinstance(data, dict) else None
        if not isinstance(packages, dict):
            return {}
        return {str(k): WantedPackage.from_dict(v) for k, v in packages.items() if isinstance(v, dict)}

    def save_wanted(self, wanted: dict[str, WantedPackage]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"version": MANIFEST_VERSION, "python": self.tag,
                   "packages": {key: wanted[key].to_dict() for key in sorted(wanted)}}
        temp = self.manifest_path.with_name(self.manifest_path.name + ".tmp")
        temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.manifest_path)

    def other_versions(self) -> list[Path]:
        """packages.json of other Python versions (the app was updated to a newer Python)."""
        if not self.base.is_dir():
            return []
        return sorted(p / "packages.json" for p in self.base.iterdir()
                      if p.is_dir() and p.name != self.tag and (p / "packages.json").is_file())

    def adopt_other_versions(self) -> list[str]:
        """Copy the wish list of another Python version's folder into this one (when this
        one has none yet). Returns the requirements that now need installing."""
        if self.manifest_path.exists():
            return []
        merged: dict[str, WantedPackage] = {}
        for path in self.other_versions():
            other = PackageStore(self.base, tag=path.parent.name)
            for key, entry in other.wanted().items():
                if entry.file:
                    continue  # a wheel for another Python doesn't fit this one
                merged.setdefault(key, entry)
        if merged:
            self.save_wanted(merged)
        return [entry.requirement for entry in merged.values()]

    # ---- What's installed -------------------------------------------------------------------

    def installed(self) -> dict[str, str]:
        """Packages in the managed folder: {normalized name: version}."""
        if not self.site.is_dir():
            return {}
        result = {}
        for dist in importlib.metadata.distributions(path=[str(self.site)]):
            name = dist.metadata.get("Name") if dist.metadata else None
            if name:
                result[normalize(name)] = dist.version
        return result

    def loaded_modules(self) -> list[str]:
        """Modules imported from the managed folder (they block replacing it)."""
        prefix = os.path.normcase(str(self.site))
        loaded = []
        for name, module in list(sys.modules.items()):
            path = getattr(module, "__file__", None)
            if path and os.path.normcase(str(path)).startswith(prefix):
                loaded.append(name)
        return loaded

    def status(self, requirement: Requirement) -> PackageStatus:
        """Is ``requirement`` met (anywhere on sys.path)? Never imports the package."""
        version: str | None = None
        location = ""
        try:
            dist = importlib.metadata.distribution(requirement.name)
            version = dist.version
            dist_path = str(getattr(dist, "_path", "") or "")
            location = "managed" if dist_path and os.path.normcase(dist_path).startswith(
                os.path.normcase(str(self.site))) else "app"
        except importlib.metadata.PackageNotFoundError:
            try:
                found = importlib.util.find_spec(requirement.module) is not None
            except (ImportError, ValueError):
                found = False
            if found:
                version, location = "", "app"  # bundled without metadata: accept any version
        if version is None:
            return PackageStatus(requirement, None, False)
        ok = version == "" or requirement.allows(version)
        return PackageStatus(requirement, version, ok, location)


# ---- Running pip ---------------------------------------------------------------------------------


class InstallError(Exception):
    pass


def pip_available() -> bool:
    try:
        return importlib.util.find_spec("pip") is not None
    except (ImportError, ValueError):
        return False


CLI_NAME = "pixelref-cli"


def pip_command(args: list[str]) -> list[str]:
    """How to run pip with *this* Python, so wheels match it.

    The standalone executable has no ``python -m``: it runs its bundled pip
    itself when started with ``--run-pip`` (see ``pixelref.__main__``). The
    windowed ``pixelRef.exe`` has no console output, so the console twin next
    to it (``pixelref-cli.exe``, same libraries) runs pip when it's there.
    """
    if is_frozen():
        exe = Path(sys.executable)
        cli = exe.with_name(CLI_NAME + exe.suffix)
        return [str(cli if cli.is_file() else exe), "--run-pip", *args]
    return [sys.executable, "-m", "pip", *args]


def pip_environment() -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
        "PYTHONNOUSERSITE": "1",  # don't let the user's own site-packages satisfy requirements
        "PYTHONIOENCODING": "utf-8",
    })
    env.pop("PIP_TARGET", None)
    env.pop("PIP_USER", None)
    return env


@dataclass
class InstallJob:
    """One pip run and what to do with its result.

    The UI runs :attr:`command` in a QProcess (so the window stays responsive)
    and headless code with :meth:`run`; both then call :meth:`finish`.
    """

    store: "PackageManager"
    kind: str  # "install" (into the live folder) or "rebuild" (into site-packages.new)
    command: list[str]
    target: Path
    wanted: dict[str, WantedPackage]
    description: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    def run(self, on_output: Callable[[str], None] | None = None) -> dict[str, Any]:
        if not self.command:
            return self.finish(0)  # e.g. removing the last package: nothing for pip to do
        process = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   env=pip_environment(), text=True, encoding="utf-8", errors="replace",
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = []
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)
            if on_output is not None:
                on_output(line.rstrip("\n"))
        return self.finish(process.wait(), "".join(lines))

    def finish(self, returncode: int, output: str = "") -> dict[str, Any]:
        self.result = self.store._finish_job(self, returncode, output)
        return self.result


def explain_pip_failure(output: str) -> str:
    """A plain-language reason for a failed pip run (the log has the details)."""
    text = output.lower()
    if any(s in text for s in ("failed to establish a new connection", "getaddrinfo failed",
                               "temporary failure in name resolution", "network is unreachable",
                               "connection timed out", "read timed out", "proxyerror")):
        return "No internet connection (or a proxy is blocking it)."
    if "no matching distribution found" in text or "could not find a version that satisfies" in text:
        return "This package has no version for this computer or this version of pixelRef."
    if "is not a supported wheel on this platform" in text:
        return "That wheel file was built for a different computer or Python version."
    if "permission denied" in text or "access is denied" in text:
        return "pixelRef isn't allowed to write to its package folder (is a file open in another program?)."
    if "no module named pip" in text:
        return "pip is missing from this Python (run: python -m ensurepip)."
    if "resolutionimpossible" in text or "conflicting dependencies" in text:
        return "These packages need versions of each other that can't be installed together."
    return "The installation failed. See the details for pip's own message."


class PackageManager(PackageStore):
    """Installs, updates and removes managed packages (headless; the UI wraps it)."""

    def __init__(self, root: Path | None = None, tag: str | None = None) -> None:
        super().__init__(root, tag)
        self.extra_find_links: list[Path] = []  # plugin wheels/ folders (offline installs)
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    def watch(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """``callback(result)`` after every finished job."""
        self._listeners.append(callback)
        return lambda: callback in self._listeners and self._listeners.remove(callback)

    @property
    def restart_needed(self) -> bool:
        return self.pending_swap

    # ---- Planning ----------------------------------------------------------------------------

    def _check_name(self, requirement: str) -> Requirement:
        req = Requirement.parse(requirement)
        if req.key in PROTECTED:
            raise InstallError(f"'{req.name}' is part of pixelRef itself and can't be installed here")
        return req

    def _pip_install_args(self, target: Path, requirements: list[str]) -> list[str]:
        args = ["install", "--target", str(target), "--only-binary=:all:", "--no-warn-script-location",
                "--disable-pip-version-check", "--no-input", "--progress-bar", "off"]
        for folder in [self.wheels, *self.extra_find_links]:
            if folder.is_dir():
                args += ["--find-links", str(folder)]
        return args + requirements

    def plan_install(self, requirements: list[str], plugins: list[str] | None = None, user: bool = False
                     ) -> InstallJob:
        """Add packages (and their dependencies) to the live folder."""
        if not requirements:
            raise InstallError("Nothing to install")
        reqs = [self._check_name(text) for text in requirements]
        wanted = self.wanted()
        for req in reqs:
            entry = wanted.get(req.key) or WantedPackage(requirement=str(req))
            entry.requirement = str(req)
            entry.plugins = sorted(set(entry.plugins) | set(plugins or []))
            entry.user = entry.user or user
            entry.file = ""
            wanted[req.key] = entry
        # pip --target won't merge into an existing folder cleanly, so install
        # into a fresh scratch folder and move the new packages over afterwards.
        self.root.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="install-", dir=self.root))
        return InstallJob(self, "install", pip_command(self._pip_install_args(scratch, [str(r) for r in reqs])),
                          scratch, wanted, description=", ".join(str(r) for r in reqs))

    def plan_install_file(self, wheel: Path, plugins: list[str] | None = None) -> InstallJob:
        """Install a downloaded ``.whl`` file (offline). It is kept in wheels/ for rebuilds."""
        wheel = Path(wheel)
        if wheel.suffix.lower() != ".whl" or not wheel.is_file():
            raise InstallError(f"Not a wheel file: {wheel}")
        name = wheel.name.split("-", 1)[0]
        req = self._check_name(name)
        self.wheels.mkdir(parents=True, exist_ok=True)
        kept = self.wheels / wheel.name
        if kept.resolve() != wheel.resolve():
            shutil.copy2(wheel, kept)
        wanted = self.wanted()
        entry = wanted.get(req.key) or WantedPackage(requirement=req.name)
        entry.requirement, entry.file, entry.user = req.name, wheel.name, True
        entry.plugins = sorted(set(entry.plugins) | set(plugins or []))
        wanted[req.key] = entry
        self.root.mkdir(parents=True, exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix="install-", dir=self.root))
        return InstallJob(self, "install", pip_command(self._pip_install_args(scratch, [str(kept)])),
                          scratch, wanted, description=wheel.name)

    def plan_rebuild(self, wanted: dict[str, WantedPackage], description: str = "") -> InstallJob:
        """Build a fresh folder holding exactly ``wanted`` (removals and updates)."""
        shutil.rmtree(self.staging, ignore_errors=True)
        self.staging.mkdir(parents=True, exist_ok=True)
        requirements = [str(self.wheels / e.file) if e.file and (self.wheels / e.file).is_file() else e.requirement
                        for e in wanted.values()]
        if not requirements:
            command: list[str] = []  # nothing left: the empty folder is the result
        else:
            command = pip_command(self._pip_install_args(self.staging, requirements))
        return InstallJob(self, "rebuild", command, self.staging, wanted, description=description)

    def plan_remove(self, names: list[str]) -> InstallJob:
        wanted = self.wanted()
        keys = {normalize(n) for n in names}
        missing = sorted(keys - set(wanted))
        if missing:
            raise InstallError(f"Not installed by pixelRef: {', '.join(missing)}")
        for key in keys:
            entry = wanted.pop(key)
            if entry.file:
                (self.wheels / entry.file).unlink(missing_ok=True)
        return self.plan_rebuild(wanted, description="remove " + ", ".join(sorted(keys)))

    def plan_update(self, names: list[str] | None = None) -> InstallJob:
        """Reinstall (newest allowed versions of) everything, or of ``names``."""
        wanted = self.wanted()
        if names:
            missing = sorted({normalize(n) for n in names} - set(wanted))
            if missing:
                raise InstallError(f"Not installed by pixelRef: {', '.join(missing)}")
        if not wanted:
            raise InstallError("No packages to update")
        return self.plan_rebuild(wanted, description="update " + ", ".join(sorted(names or wanted)))

    def plan_reinstall(self) -> InstallJob:
        """Install everything packages.json lists (after a Python version change)."""
        wanted = self.wanted()
        if not wanted:
            raise InstallError("No packages to install")
        return self.plan_rebuild(wanted, description="reinstall for this version of pixelRef")

    # ---- Finishing ---------------------------------------------------------------------------

    def _finish_job(self, job: InstallJob, returncode: int, output: str) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": False, "kind": job.kind, "description": job.description,
                                  "restart": False, "message": "", "output": output}
        try:
            if returncode != 0:
                result["message"] = explain_pip_failure(output)
                if job.kind == "rebuild":
                    shutil.rmtree(self.staging, ignore_errors=True)
                return result
            if job.kind == "install":
                self._merge(job.target)
                self.save_wanted(job.wanted)
                self.activated = True
                self._add_to_path()
                result.update(ok=True, message=f"Installed {job.description}.")
            else:
                (self.staging / READY_MARKER).write_text("", encoding="utf-8")
                self.save_wanted(job.wanted)
                loaded = self.loaded_modules()
                if not loaded and self._swap():
                    self._purge_modules_cache()
                    if self.site.is_dir():
                        self._add_to_path()
                    result.update(ok=True, message=f"Done: {job.description}.")
                else:
                    result.update(ok=True, restart=True,
                                  message=f"Restart pixelRef to finish: {job.description}.")
        finally:
            if job.kind == "install":
                shutil.rmtree(job.target, ignore_errors=True)
        for callback in list(self._listeners):
            callback(result)
        return result

    def _merge(self, scratch: Path) -> None:
        """Move what pip put in ``scratch`` into the live folder.

        A package that is already there keeps its files (pip installed the same
        or a compatible version as a dependency); everything else is new.
        """
        self.site.mkdir(parents=True, exist_ok=True)
        present = self.installed()
        skip: set[str] = set()
        for dist in importlib.metadata.distributions(path=[str(scratch)]):
            name = dist.metadata.get("Name") if dist.metadata else None
            if name and normalize(name) in present:
                for file in dist.files or ():
                    skip.add(Path(str(file)).parts[0])
        for entry in scratch.iterdir():
            if entry.name in skip or entry.name == READY_MARKER:
                continue
            destination = self.site / entry.name
            if destination.exists():
                if entry.is_dir() and destination.is_dir():
                    _merge_tree(entry, destination)  # a namespace package shared with another one
                continue
            os.replace(entry, destination)

    def _purge_modules_cache(self) -> None:
        importlib.invalidate_caches()
        if str(self.site) in sys.path:
            sys.path.remove(str(self.site))


def _merge_tree(source: Path, destination: Path) -> None:
    for entry in source.iterdir():
        target = destination / entry.name
        if not target.exists():
            os.replace(entry, target)
        elif entry.is_dir() and target.is_dir():
            _merge_tree(entry, target)
