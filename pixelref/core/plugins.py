"""The plugin runtime: discovery, trust, conflicts, dependency resolution and lifecycle.

A plugin is a folder with a ``plugin.toml`` manifest and a Python package::

    my_plugin/
        plugin.toml        id, name, version, api, author, license, source, permissions…
                       [python] requires = ["numpy>=1.26"]  (Python packages)
        SIGNATURE.toml     optional: the publisher's signature (python -m pixelref plugin sign)
        __init__.py        def activate(ctx): …       (headless part, always)
                           def activate_ui(ui): …     (only when a Qt UI runs)
                           def deactivate(ctx): …     (optional)

Plugins are found in *sources* (:mod:`pixelref.core.plugin_sources`): the
built-in folder (``pixelref/builtins``), the user plugin folder, folders the
user added, ``$PIXELREF_PLUGIN_PATH`` and ``--plugin-path`` /
``--trust-plugin-path`` on the command line.

**Trust.** A third-party plugin's code isn't imported until the user trusts it
(:mod:`pixelref.core.plugin_trust`): it is signed by a publisher on the user's
list, the user approved exactly these files, or its source trusts everything
(developer folders). Approval is pinned to a hash of every file, checked again
right before the code runs; a changed plugin needs approving again.

**Conflicts.** When several sources hold a plugin with the same id, every copy
is a *candidate*; one is used (the user's choice, else the built-in one, else
the first source) and the others are listed as conflicts. A third-party plugin
can't take a built-in plugin's id unless it says ``replaces = "<id>"`` and the
user picks it; foundation plugins can't be replaced.

Plugins activate in dependency order; a plugin that fails — or whose
requirements are missing or disabled — is switched off and reported, and the
app keeps running. A plugin whose Python packages aren't installed is
*unavailable* (so are plugins that require it) until they are: the Plugins
window installs them (see :mod:`pixelref.core.packages`). Requirements are
checked without importing anything.

Everything a plugin registers goes through its context and is undone when it
is deactivated, so plugins can be enabled and disabled while the app runs.
Foundation plugins (the select tool, outliner, inspector…) can't be disabled.
Third-party plugins' activity is watched by :mod:`pixelref.core.plugin_audit`.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import logging
import os
import re
import sys
import tomllib
import traceback
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .. import __version__ as APP_VERSION
from .attributes import Attr
from .errors import PluginError
from .extensions import Disposable
from .packages import Requirement, parse_version, requirement_list
from .plugin_audit import KINDS as PERMISSIONS
from .plugin_audit import ActivityMonitor
from .plugin_sources import POLICIES, SOURCES_KEY, PluginSource, same_path
from .plugin_trust import (
    FileHasher,
    SignatureCheck,
    TrustStore,
    bytes_hash,
    changed_files,
    check_signature,
    folder_digest,
    missing_publish_fields,
)

if TYPE_CHECKING:
    from .session import Session

logger = logging.getLogger(__name__)

API_VERSION = 1
BUILTIN_DIR = Path(__file__).resolve().parents[1] / "builtins"
EXTERNAL_PACKAGE = "pixelref_plugins"
DISABLED_KEY = "plugins/disabled"
ENABLED_KEY = "plugins/enabled"
RESOLVED_KEY = "plugins/resolved"
MONITOR_KEY = "plugins/monitor"
ID_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,63}")
RESERVED_PREFIX = "pixelref"
OVERRIDABLE = ("command", "service")
README_LIMIT = 256 * 1024
KNOWN_KEYS = frozenset({
    "id", "name", "version", "api", "description", "requires", "optional", "python", "foundation",
    "enabled_by_default", "author", "license", "source", "readme", "category", "tags", "min_app_version",
    "permissions", "overrides", "replaces",
})

# Trust states. A plugin's code runs only in the first four.
TRUSTED = ("builtin", "verified", "trusted", "session")
TRUST_LABELS = {
    "builtin": "Built-in",
    "verified": "Verified publisher",
    "trusted": "Approved by you",
    "session": "Trusted folder",
    "unverified": "Not reviewed",
    "changed": "Changed since approved",
    "invalid": "Invalid signature",
    "blocked": "Blocked",
}


class PluginTrustError(PluginError):
    """The plugin's code may not run (not approved, or changed since)."""


@dataclass
class PluginManifest:
    id: str
    name: str
    version: str = "0.0.0"
    api: int = API_VERSION
    description: str = ""
    requires: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    python_requires: list[Requirement] = field(default_factory=list)
    python_optional: list[Requirement] = field(default_factory=list)
    foundation: bool = False
    enabled_by_default: bool = True
    path: Path | None = None
    builtin: bool = False
    # Provenance: who made it and where it comes from.
    author: dict[str, str] = field(default_factory=dict)  # name, email, organization
    license: str = ""
    homepage: str = ""
    repository: str = ""
    issues: str = ""
    readme: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    min_app_version: str = ""
    # What it says it does (the keys of pixelref.core.plugin_audit.KINDS).
    permissions: frozenset[str] = frozenset()
    overrides: list[str] = field(default_factory=list)  # "command:<name>" / "service:<name>"
    replaces: str = ""  # its own id, when it offers to replace the built-in plugin of that id
    warnings: list[str] = field(default_factory=list)
    publish_missing: list[str] = field(default_factory=list)  # provenance a publisher must still fill in

    @classmethod
    def load(cls, folder: Path, builtin: bool) -> "PluginManifest":
        manifest_path = folder / "plugin.toml"
        try:
            data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise PluginError(f"{manifest_path}: {exc}") from exc
        plugin_id = str(data.get("id") or folder.name)
        python = data.get("python", {})
        if not isinstance(python, dict):
            raise PluginError(f"{manifest_path}: [python] must be a table")
        warnings: list[str] = []
        if not builtin:
            if not ID_PATTERN.fullmatch(plugin_id):
                raise PluginError(f"{manifest_path}: id '{plugin_id}' must be snake_case "
                                  "(a-z, 0-9 and _, starting with a letter, 2-64 characters)")
            if plugin_id.startswith(RESERVED_PREFIX):
                raise PluginError(f"{manifest_path}: ids starting with '{RESERVED_PREFIX}' are reserved")
            unknown = sorted(set(data) - KNOWN_KEYS)
            if unknown:
                warnings.append("Unknown manifest keys: " + ", ".join(unknown))
        try:
            author = _author(data.get("author", {}))
            source = _table(data.get("source", {}), "source")
            for table, known in (("author", ("name", "email", "organization")),
                                 ("source", ("homepage", "repository", "issues"))):
                extra = sorted(set(data[table]) - set(known)) if isinstance(data.get(table), dict) else []
                if extra:
                    warnings.append(f"Unknown keys in [{table}]: {', '.join(extra)} (top-level keys must come "
                                    "before any [table] in plugin.toml)")
            permissions = _permissions(data.get("permissions", {}), warnings)
            overrides = _overrides(data.get("overrides", []))
            replaces = str(data.get("replaces", ""))
            if replaces and replaces != plugin_id:
                raise ValueError(f"replaces must be the plugin's own id ('{plugin_id}'), got '{replaces}'")
            foundation = bool(data.get("foundation", False))
            if foundation and not builtin:
                warnings.append("foundation is only for built-in plugins (ignored)")
                foundation = False
            for key in ("homepage", "repository", "issues"):
                url = str(source.get(key, ""))
                if url and not url.startswith(("https://", "http://")):
                    warnings.append(f"source.{key} isn't a web address: {url}")
            if author.get("email") and "@" not in author["email"]:
                warnings.append(f"author.email isn't an email address: {author['email']}")
            if builtin and not author:
                author = {"name": "pixelRef"}
            manifest = cls(
                id=plugin_id,
                name=str(data.get("name", plugin_id)),
                version=str(data.get("version", "0.0.0")),
                api=int(data.get("api", API_VERSION)),
                description=str(data.get("description", "")),
                requires=_id_list(data.get("requires", [])),
                optional=_id_list(data.get("optional", [])),
                python_requires=requirement_list(python.get("requires")),
                python_optional=requirement_list(python.get("optional")),
                foundation=foundation,
                enabled_by_default=bool(data.get("enabled_by_default", True)),
                path=folder,
                builtin=builtin,
                author=author,
                license=str(data.get("license", "")),
                homepage=str(source.get("homepage", "")),
                repository=str(source.get("repository", "")),
                issues=str(source.get("issues", "")),
                readme=str(data.get("readme", "")),
                category=str(data.get("category", "")),
                tags=_string_list(data.get("tags", []), "tags"),
                min_app_version=str(data.get("min_app_version", "")),
                permissions=permissions,
                overrides=overrides,
                replaces=replaces,
                warnings=warnings,
                publish_missing=[] if builtin else missing_publish_fields(data),
            )
        except (TypeError, ValueError) as exc:
            raise PluginError(f"{manifest_path}: {exc}") from exc
        if manifest.readme and not (folder / manifest.readme).resolve().is_relative_to(folder.resolve()):
            raise PluginError(f"{manifest_path}: readme must be a file inside the plugin folder")
        return manifest


def _id_set(settings: Any, key: str) -> set[str]:
    value = settings.value(key, [])
    return set([value] if isinstance(value, str) else (value or []))


def switched_on(settings: Any, manifest: PluginManifest, trust: str) -> bool:
    """The user's on/off choice for a plugin, as kept in the settings (foundation plugins are always on).

    Only plugins nobody needs to review switch themselves on (``enabled_by_default``); the
    others are on once the user turned them on. Tools outside the app (the MCP server) ask
    this too, so a plugin's switch means the same everywhere."""
    if manifest.foundation:
        return True
    if manifest.id in _id_set(settings, DISABLED_KEY):
        return False
    if manifest.enabled_by_default and trust in ("builtin", "verified", "session"):
        return True
    return manifest.id in _id_set(settings, ENABLED_KEY)


def _id_list(value: Any) -> list[str]:
    """``requires = "brush"`` means ["brush"], not one id per letter."""
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise TypeError(f"expected a list of plugin ids, got {value!r}")
    return [str(item) for item in value]


def _table(value: Any, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"[{key}] must be a table")
    return value


def _author(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        return {"name": value} if value else {}
    table = _table(value, "author")
    return {k: str(table[k]) for k in ("name", "email", "organization") if table.get(k)}


def _permissions(value: Any, warnings: list[str]) -> frozenset[str]:
    granted = set()
    for key, flag in _table(value, "permissions").items():
        if key not in PERMISSIONS:
            warnings.append(f"Unknown permission '{key}'")
            continue
        if not isinstance(flag, bool):
            raise TypeError(f"permissions.{key} must be true or false")
        if flag:
            granted.add(key)
    return frozenset(granted)


def _overrides(value: Any) -> list[str]:
    items = _string_list(value, "overrides")
    for item in items:
        kind, sep, name = item.partition(":")
        if not sep or kind not in OVERRIDABLE or not name:
            raise ValueError(f"overrides entries look like 'command:<name>' or 'service:<name>', got '{item}'")
    return items


def _string_list(value: Any, key: str) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list of strings")
    return [str(item) for item in value]


@dataclass
class PluginRecord:
    manifest: PluginManifest
    module: types.ModuleType | None = None
    state: str = "discovered"  # discovered, active, inactive, disabled, unavailable, untrusted, error
    error: str = ""
    missing_packages: list[str] = field(default_factory=list)  # unmet Python requirements (unavailable)
    context: Any = None
    ui_context: Any = None
    broken: bool = False  # the manifest itself could not be read
    source: PluginSource | None = None
    files: dict[str, str] = field(default_factory=dict)  # relative path -> sha256 (third-party plugins)
    digest: str = ""  # hash of ``files``
    signature: SignatureCheck = field(default_factory=SignatureCheck)
    trust: str = "unverified"  # see TRUST_LABELS
    trust_reason: str = ""
    conflicts: list["PluginRecord"] = field(default_factory=list)  # other copies with the same id
    blocked: str = ""  # why this copy can't be chosen

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def active(self) -> bool:
        return self.state == "active"

    @property
    def trusted(self) -> bool:
        return self.trust in TRUSTED


class PluginContext:
    """What a plugin's ``activate(ctx)`` receives (headless facilities).

    Every registration made through the context is recorded and undone by
    :meth:`dispose` when the plugin is deactivated.
    """

    def __init__(self, session: "Session", plugin_id: str, overrides: frozenset[str] = frozenset()) -> None:
        self.session = session
        self.plugin_id = plugin_id
        self._overrides = overrides
        self._disposables: list[Disposable] = []
        self.settings = session.settings.scoped(f"plugins/{plugin_id}")
        self.log = logging.getLogger(f"pixelref.plugin.{plugin_id}")

    # ---- Bookkeeping -------------------------------------------------------------

    def track(self, disposable: Disposable | Callable[[], None]) -> Disposable:
        if not isinstance(disposable, Disposable):
            disposable = Disposable(disposable)
        self._disposables.append(disposable)
        return disposable

    def dispose(self) -> None:
        while self._disposables:
            disposable = self._disposables.pop()
            try:
                disposable.dispose()
            except Exception:  # noqa: BLE001
                logger.exception("Error while disposing a contribution of plugin %s", self.plugin_id)

    # ---- Shortcuts to the session -----------------------------------------------------

    @property
    def scene(self):  # noqa: ANN201
        return self.session.scene

    @property
    def pxcmd(self):  # noqa: ANN201
        return self.session.commands.proxy()

    def run(self, command: str, *args: Any, **kwargs: Any) -> Any:
        return self.session.commands.execute(command, *args, **kwargs)

    # ---- Registrations -----------------------------------------------------------------

    def register_node_type(self, node_type: Any) -> Disposable:
        node_type.plugin = self.plugin_id
        return self.track(self.session.types.register(node_type))

    def extend_node_type(self, type_name: str, attrs: Any) -> Disposable:
        """Add attributes to a node type this plugin doesn't own (and its subtypes).

        ``ctx.extend_node_type("canvas_node", (Attr("effects", "any", [], hidden=True),))``.
        The attributes are sparse (stored only once set, so unused scenes don't
        change); values in files survive while the plugin is off.
        """
        from .nodetypes import TypeExtension

        attrs = (attrs,) if isinstance(attrs, Attr) else tuple(attrs)
        return self.track(self.session.types.extend(TypeExtension(type_name, attrs, plugin=self.plugin_id)))

    def register_attr_symbols(self, symbols: dict[str, str]) -> Disposable:
        """Symbols for the Attribute Editor: ``{"bevel": "◣", "my_node.depth": "⇕", "type:float2": "⌖"}``.

        Keys are ``"<node type>.<attribute>"``, ``"<attribute>"`` or ``"type:<attr type>"``; they override
        the global defaults. An attribute you declare can also carry ``Attr(symbol=...)``.
        """
        return self.contribute("attributes.symbols", dict(symbols))

    def command(self, name: str, fn: Callable[..., Any], *, alias: str | None = None, **options: Any) -> Disposable:
        """Register ``<plugin id>.<name>`` (plus an optional flat alias).

        A name another plugin already registered fails, unless the manifest lists
        ``overrides = ["command:<name>"]``: then this one replaces it while active."""
        if f"command:{name}" in self._overrides:
            full = name  # another plugin's command, replaced on purpose
        else:
            full = name if name.startswith(self.plugin_id + ".") else f"{self.plugin_id}.{name}"
        replace = f"command:{full}" in self._overrides
        return self.track(self.session.commands.register(full, fn, alias=alias, plugin=self.plugin_id,
                                                         replace=replace, **options))

    def declare_extension_point(self, name: str, doc: str = "", validator: Callable[[Any], None] | None = None
                                ) -> Disposable:
        return self.track(self.session.extensions.declare(name, owner=self.plugin_id, doc=doc, validator=validator))

    def contribute(self, point: str, value: Any, order: float = 0.0) -> Disposable:
        return self.track(self.session.extensions.contribute(point, value, plugin=self.plugin_id, order=order))

    def contributions(self, point: str) -> list[Any]:
        return self.session.extensions.contributions(point)

    def provide_service(self, name: str, service: Any) -> Disposable:
        """Provide a named service; one another plugin provides fails unless the manifest
        lists ``overrides = ["service:<name>"]``."""
        replace = f"service:{name}" in self._overrides
        return self.track(self.session.services.provide(name, service, plugin=self.plugin_id, replace=replace))

    def service(self, name: str, default: Any = None) -> Any:
        return self.session.services.get(name, default)

    def require(self, name: str) -> Any:
        return self.session.services.require(name)

    def on(self, event_type: str, callback: Callable[[Any], None]) -> Disposable:
        """Session events: document.changed, document.replaced, selection.changed, scene.*…"""
        return self.track(self.session.events.on(event_type, callback))

    def context_key(self, key: str, provider: Callable[[], Any]) -> Disposable:
        return self.track(self.session.context.provide(key, provider))


class PluginManager:
    def __init__(self, session: "Session", paths: list[Path] | tuple[Path, ...] = (), builtins: bool = True,
                 user_dir: Path | None = None, only: set[str] | None = None,
                 trusted_paths: list[Path] | tuple[Path, ...] = ()) -> None:
        self.session = session
        self.include_builtins = builtins
        self.user_dir = user_dir
        self.extra_paths = [Path(p) for p in paths]
        self.trusted_paths = [Path(p) for p in trusted_paths]  # --trust-plugin-path: trusted for this run
        self.only = only  # restrict activation to these ids (+ their requirements); tests use it
        self.records: dict[str, PluginRecord] = {}
        self.candidates: dict[str, list[PluginRecord]] = {}  # every copy of every id found
        self.ui_host: Any = None  # set by the Qt app; provides make_ui_context(record)
        self.trust = TrustStore(session.settings)
        self.hasher = FileHasher()
        self.monitor = ActivityMonitor(enabled=bool(session.settings.value(MONITOR_KEY, True, type=bool)))
        self.started = False
        self._listeners: list[Callable[[], None]] = []

    # ---- Sources -------------------------------------------------------------------------

    def sources(self) -> list[PluginSource]:
        """Where plugins are looked for, in precedence order."""
        stored = self._stored_sources()
        out: list[PluginSource] = []
        if self.include_builtins:
            out.append(PluginSource(BUILTIN_DIR, "builtin", policy="trust_all"))
        if self.user_dir is not None:
            saved = next((s for s in stored if s.kind == "user"), None)
            out.append(PluginSource(self.user_dir, "user", enabled=saved.enabled if saved else True,
                                    policy=saved.policy if saved else "ask"))
        out += [s for s in stored if s.kind == "added"]
        for entry in os.environ.get("PIXELREF_PLUGIN_PATH", "").split(os.pathsep):
            if entry.strip():
                out.append(PluginSource(Path(entry.strip()), "env"))
        out += [PluginSource(path, "cli") for path in self.extra_paths]
        out += [PluginSource(path, "cli", label="Trusted for this run", policy="trust_all")
                for path in self.trusted_paths]
        return out

    def search_paths(self) -> list[tuple[Path, bool]]:
        return [(s.path, s.kind == "builtin") for s in self.sources() if s.enabled]

    def _stored_sources(self) -> list[PluginSource]:
        data = self.session.settings.json_value(SOURCES_KEY, [])
        if not isinstance(data, list):
            return []
        return [PluginSource.from_dict(d) for d in data if isinstance(d, dict) and d.get("path")]

    def _save_sources(self, sources: list[PluginSource]) -> None:
        self.session.settings.set_json(SOURCES_KEY, [s.to_dict() for s in sources if s.editable])

    def source_info(self) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for group in self.candidates.values():
            for record in group:
                if record.source is not None:
                    key = os.path.normcase(str(record.source.path))
                    counts[key] = counts.get(key, 0) + 1
        return [s.info(counts.get(os.path.normcase(str(s.path)), 0)) for s in self.sources()]

    def _find_source(self, path: Path | str) -> PluginSource:
        source = next((s for s in self.sources() if same_path(s.path, path)), None)
        if source is None:
            raise PluginError(f"'{path}' isn't a plugin source")
        return source

    def add_source(self, path: Path | str, label: str = "", policy: str = "ask") -> PluginSource:
        """Add a folder of plugins (or one plugin folder); its plugins still need approving."""
        folder = Path(path).expanduser()
        if not folder.is_dir():
            raise PluginError(f"'{folder}' isn't a folder")
        if policy not in POLICIES:
            raise PluginError(f"Unknown policy '{policy}' (use one of {', '.join(POLICIES)})")
        if any(same_path(s.path, folder) for s in self.sources()):
            raise PluginError(f"'{folder}' is already a plugin source")
        source = PluginSource(folder.resolve(), "added", label=label, policy=policy)
        self._save_sources([s for s in self.sources() if s.editable] + [source])
        self.rescan()
        return source

    def remove_source(self, path: Path | str) -> None:
        source = self._find_source(path)
        if source.kind != "added":
            raise PluginError(f"The {source.title} source can't be removed")
        self._save_sources([s for s in self.sources() if s.editable and not same_path(s.path, path)])
        self.rescan()

    def update_source(self, path: Path | str, enabled: bool | None = None, policy: str | None = None,
                      label: str | None = None) -> PluginSource:
        source = self._find_source(path)
        if not source.editable:
            raise PluginError(f"The {source.title} source is fixed for this run")
        if policy is not None and policy not in POLICIES:
            raise PluginError(f"Unknown policy '{policy}' (use one of {', '.join(POLICIES)})")
        sources = [s for s in self.sources() if s.editable]
        for entry in sources:
            if same_path(entry.path, path):
                if enabled is not None:
                    entry.enabled = bool(enabled)
                if policy is not None:
                    entry.policy = policy
                if label is not None:
                    entry.label = label
                source = entry
        self._save_sources(sources)
        self.rescan()
        return source

    def move_source(self, path: Path | str, offset: int) -> None:
        """Move an added source up (-1) or down (+1): earlier sources win id conflicts."""
        sources = [s for s in self.sources() if s.editable]
        added = [s for s in sources if s.kind == "added"]
        index = next((i for i, s in enumerate(added) if same_path(s.path, path)), None)
        if index is None:
            raise PluginError(f"Only added sources can be moved ('{path}')")
        target = max(0, min(len(added) - 1, index + offset))
        added.insert(target, added.pop(index))
        self._save_sources([s for s in sources if s.kind != "added"] + added)
        self.rescan()

    # ---- Discovery -----------------------------------------------------------------------

    def discover(self) -> dict[str, PluginRecord]:
        """Find every plugin in every enabled source and choose one copy of each id."""
        candidates: dict[str, list[PluginRecord]] = {}
        for source in self.sources():
            if not source.enabled:
                continue
            for folder in source.plugin_folders():
                record = self._load_candidate(folder, source)
                candidates.setdefault(record.id, []).append(record)
        chosen: dict[str, PluginRecord] = {}
        for plugin_id, group in candidates.items():
            pick = self._choose(plugin_id, group)
            for record in group:
                record.conflicts = [other for other in group if other is not record]
            chosen[plugin_id] = pick
        self.candidates = candidates
        self._apply(chosen)
        packages = getattr(self.session, "packages", None)
        if packages is not None:
            packages.plugin_find_links = self.wheel_folders
        return self.records

    def rescan(self) -> None:
        """Look in the sources again: new plugins appear, removed ones go, changed ones are re-checked."""
        self.discover()
        if self.started:
            self.activate_all()
        else:
            self._changed()

    def _load_candidate(self, folder: Path, source: PluginSource) -> PluginRecord:
        builtin = source.kind == "builtin"
        try:
            if folder != source.path and not folder.resolve().is_relative_to(source.path.resolve()):
                raise PluginError(f"{folder} links to {folder.resolve()}, outside its source folder")
            manifest = PluginManifest.load(folder, builtin)
            record = PluginRecord(manifest, source=source)
            if not builtin:
                record.files = self.hasher.files(folder)
                record.digest = folder_digest(record.files)
                record.signature = check_signature(folder, manifest.id, manifest.version, record.files)
        except (PluginError, OSError) as exc:
            record = PluginRecord(PluginManifest(id=folder.name, name=folder.name, path=folder), source=source)
            record.state, record.error, record.broken = "error", str(exc), True
        record.trust, record.trust_reason = self._evaluate_trust(record)
        return record

    def _choose(self, plugin_id: str, group: list[PluginRecord]) -> PluginRecord:
        usable = [r for r in group if not r.broken] or group
        builtin = next((r for r in usable if r.manifest.builtin), None)
        for record in usable:
            record.blocked = ""
            if builtin is None or record is builtin:
                continue
            if record.manifest.replaces != plugin_id:
                record.blocked = (f"uses the id of the built-in plugin '{plugin_id}' (a replacement must say "
                                  f"replaces = \"{plugin_id}\" in plugin.toml)")
            elif builtin.manifest.foundation:
                record.blocked = f"'{plugin_id}' is a foundation plugin and can't be replaced"
        wanted = self._resolutions().get(plugin_id)
        if wanted:
            for record in usable:
                if same_path(str(record.manifest.path), wanted) and not record.blocked:
                    return record
        return builtin or usable[0]

    def _apply(self, chosen: dict[str, PluginRecord]) -> None:
        """Swap in the newly chosen copies, keeping records (and loaded code) that didn't change."""
        merged: dict[str, PluginRecord] = {}
        for plugin_id, new in chosen.items():
            old = self.records.get(plugin_id)
            if old is not None and not old.broken and not new.broken and same_path(
                    str(old.manifest.path), str(new.manifest.path)) and old.digest == new.digest:
                old.source, old.conflicts, old.blocked = new.source, new.conflicts, new.blocked
                old.signature = new.signature
                old.trust, old.trust_reason = self._evaluate_trust(old)
                # The candidate list must point at the record in use.
                group = self.candidates.get(plugin_id)
                if group is not None and new in group:
                    group[group.index(new)] = old
                    for record in group:
                        record.conflicts = [other for other in group if other is not record]
                merged[plugin_id] = old
                if old.active and not old.trusted:
                    self.deactivate(plugin_id, record_state="untrusted")
                    old.error = old.trust_reason
            else:
                if old is not None:
                    self._retire(old)
                merged[plugin_id] = new
        for plugin_id, old in self.records.items():
            if plugin_id not in chosen:
                self._retire(old)
        self.records = merged

    def _retire(self, record: PluginRecord) -> None:
        """A record that is no longer used (removed, replaced by another copy, or changed)."""
        if record.active:
            self.deactivate(record.id)
        self._forget_module(record)

    def _forget_module(self, record: PluginRecord) -> None:
        if record.module is not None:
            prefix = record.module.__name__
            for name in [n for n in sys.modules if n == prefix or n.startswith(prefix + ".")]:
                del sys.modules[name]
            record.module = None
        if not record.manifest.builtin:
            self.monitor.unwatch(record.id)

    def wheel_folders(self, plugin_ids: list[str] | None = None) -> list[Path]:
        """Bundled ``wheels/`` folders of these (trusted) plugins: their packages install without internet.

        Only the plugins a package is installed for are used, so one plugin can't
        supply the files of a package another plugin asked for."""
        folders = []
        for plugin_id in self.records if plugin_ids is None else plugin_ids:
            record = self.records.get(plugin_id)
            if record is None or not record.trusted:
                continue
            path = record.manifest.path
            if path is not None and path.is_dir() and (path / "wheels").is_dir():
                folders.append(path / "wheels")
        return folders

    def _resolutions(self) -> dict[str, str]:
        data = self.session.settings.json_value(RESOLVED_KEY, {})
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def resolve(self, plugin_id: str, path: Path | str) -> PluginRecord:
        """Use the copy of ``plugin_id`` in ``path`` (one of its candidates)."""
        group = self.candidates.get(plugin_id)
        if not group:
            raise PluginError(f"Unknown plugin '{plugin_id}'")
        target = next((r for r in group if same_path(str(r.manifest.path), path)), None)
        if target is None:
            raise PluginError(f"No copy of '{plugin_id}' in {path}")
        if target.broken:
            raise PluginError(f"That copy of '{plugin_id}' can't be read: {target.error}")
        if target.blocked:
            raise PluginError(f"That copy of '{plugin_id}' {target.blocked}")
        resolutions = self._resolutions()
        resolutions[plugin_id] = str(target.manifest.path)
        self.session.settings.set_json(RESOLVED_KEY, resolutions)
        self.rescan()
        return self.records[plugin_id]

    # ---- Trust -----------------------------------------------------------------------------

    def _evaluate_trust(self, record: PluginRecord) -> tuple[str, str]:
        manifest = record.manifest
        if manifest.builtin:
            return "builtin", ""
        if record.broken:
            return "blocked", record.error
        signature = record.signature
        if signature.status == "valid" and self.trust.trusts_key(signature.key_id, signature.public_key):
            publisher = self.trust.publisher(signature.key_id) or {}
            return "verified", f"signed by {publisher.get('name') or signature.signer}, a trusted publisher"
        source = record.source
        if source is not None and source.policy == "trust_all":
            return "session", f"everything in {source.path} is trusted"
        grant = self.trust.grants().get(manifest.id)
        granted = grant is not None and manifest.path is not None and same_path(grant.path, manifest.path)
        if signature.status == "invalid":
            if granted and grant.allow_invalid and grant.digest == record.digest:
                return "trusted", "approved by you although its signature is invalid"
            return "invalid", signature.problem
        if source is not None and source.policy == "signed_only":
            return "blocked", "its folder only allows plugins signed by a trusted publisher"
        if granted:
            if grant.digest == record.digest:
                return "trusted", f"approved by you on {grant.granted_at[:10]}"
            return "changed", "its files changed since you approved it"
        if signature.status == "valid":
            return "unverified", (f"signed by {signature.signer or signature.key_id}, who isn't one of "
                                  "your trusted publishers")
        return "unverified", "not reviewed yet"

    def refresh_trust(self) -> None:
        """Re-check every plugin (after the user's trust decisions or publishers changed)."""
        for group in self.candidates.values():
            for record in group:
                record.trust, record.trust_reason = self._evaluate_trust(record)
        for plugin_id in reversed(self.load_order()):
            record = self.records[plugin_id]
            if record.active and not record.trusted:
                self.deactivate(plugin_id, record_state="untrusted")
                record.error = record.trust_reason
        if self.started:
            self.activate_all()
        else:
            self._changed()

    def trust_plugin(self, plugin_id: str, allow_invalid: bool = False) -> PluginRecord:
        """Approve the plugin's current files (it may then be enabled)."""
        record = self._record(plugin_id)
        if record.manifest.builtin or record.trust in ("verified", "session"):
            return record
        if record.blocked or record.broken:
            raise PluginError(f"'{plugin_id}' can't be approved: {record.blocked or record.error}")
        if record.source is not None and record.source.policy == "signed_only":
            raise PluginError(f"'{plugin_id}' is in a folder that only allows plugins signed by a trusted publisher")
        assert record.manifest.path is not None
        self._rehash(record)
        if record.signature.status == "invalid" and not allow_invalid:
            raise PluginError(f"'{plugin_id}' has an invalid signature ({record.signature.problem}); "
                              "approving it anyway needs allow_invalid=True")
        self.trust.grant(plugin_id, record.manifest.path, record.files,
                         allow_invalid=record.signature.status == "invalid")
        record.trust, record.trust_reason = self._evaluate_trust(record)
        if record.state == "untrusted":
            record.state, record.error = "discovered", ""
        self._changed()
        return record

    def revoke_trust(self, plugin_id: str) -> None:
        """Forget the approval and switch the plugin off."""
        record = self._record(plugin_id)
        if record.manifest.builtin:
            raise PluginError(f"'{plugin_id}' is built in")
        self.trust.revoke(plugin_id)
        disabled = self._disabled_ids() | {plugin_id}
        self.session.settings.setValue(DISABLED_KEY, sorted(disabled))
        self.session.settings.setValue(ENABLED_KEY, sorted(self._enabled_ids() - {plugin_id}))
        self.deactivate(plugin_id, record_state="disabled")
        record.trust, record.trust_reason = self._evaluate_trust(record)
        self._changed()

    def add_publisher(self, name: str, public_key: str) -> dict[str, str]:
        entry = self.trust.add_publisher(name, public_key)
        self.refresh_trust()
        return entry

    def remove_publisher(self, key_id: str) -> bool:
        removed = self.trust.remove_publisher(key_id)
        self.refresh_trust()
        return removed

    def changed_files(self, plugin_id: str) -> list[str]:
        """What changed since the plugin was approved (``+ new``, ``- removed``, ``~ changed``)."""
        record = self._record(plugin_id)
        grant = self.trust.grants().get(plugin_id)
        if grant is None or not grant.files:
            return []
        return changed_files(grant.files, record.files)

    def needs_review(self) -> list[str]:
        """Third-party plugins waiting for the user: never reviewed (and not switched off) or changed."""
        disabled = self._disabled_ids()
        out = []
        for plugin_id, record in self.records.items():
            if record.manifest.builtin or record.broken:
                continue
            if record.trust == "changed" or (record.trust in ("unverified", "invalid") and plugin_id not in disabled):
                out.append(plugin_id)
        return sorted(out)

    def _rehash(self, record: PluginRecord) -> bool:
        """Hash the plugin's files again; True if they changed (trust is re-evaluated)."""
        path = record.manifest.path
        assert path is not None
        files = self.hasher.files(path)
        digest = folder_digest(files)
        if digest == record.digest:
            return False
        record.files, record.digest = files, digest
        record.signature = check_signature(path, record.id, record.manifest.version, files)
        record.trust, record.trust_reason = self._evaluate_trust(record)
        return True

    def set_monitor_enabled(self, enabled: bool) -> None:
        self.monitor.enabled = bool(enabled)
        self.session.settings.setValue(MONITOR_KEY, bool(enabled))
        self._changed()

    # ---- Python packages -------------------------------------------------------------------

    def missing_packages(self, plugin_id: str) -> list[str]:
        """The plugin's Python requirements that aren't met (checked without importing)."""
        record = self.records.get(plugin_id)
        packages = getattr(self.session, "packages", None)
        if record is None or packages is None:
            return []
        return [str(req) for req in record.manifest.python_requires if not packages.status(req).ok]

    def packages_needed(self) -> dict[str, list[str]]:
        """Every unmet requirement of an enabled, trusted plugin: {requirement: [plugin ids]}."""
        needed: dict[str, list[str]] = {}
        for plugin_id, record in self.records.items():
            if record.broken or not record.trusted or not self.is_enabled(plugin_id):
                continue
            for requirement in self.missing_packages(plugin_id):
                needed.setdefault(requirement, []).append(plugin_id)
        return needed

    def refresh_python_requirements(self) -> None:
        """After packages were installed or removed: activate the plugins that can
        now run, and switch off those that lost a package they need."""
        retry: set[str] = set()
        for plugin_id in self.load_order():
            record = self.records[plugin_id]
            missing = self.missing_packages(plugin_id) if record.active else []
            if missing:
                retry.update(self.dependents(plugin_id))  # switched off with it; retried below
                self.deactivate(plugin_id, record_state="unavailable")
                self._mark_unavailable(record, missing)
        for plugin_id in self.load_order():
            record = self.records[plugin_id]
            if (record.state == "unavailable" or plugin_id in retry) and not record.active \
                    and self.is_enabled(plugin_id):
                self.activate(plugin_id)
        self._changed()

    @staticmethod
    def _mark_unavailable(record: PluginRecord, missing: list[str], via: str = "") -> None:
        record.state, record.missing_packages = "unavailable", list(missing)
        packages = ", ".join(missing)
        if via:
            record.error = f"requires '{via}', which needs Python package{'s' if len(missing) > 1 else ''} {packages}"
        else:
            record.error = f"needs Python package{'s' if len(missing) > 1 else ''} {packages}"

    # ---- Enabled state -------------------------------------------------------------------

    def _disabled_ids(self) -> set[str]:
        return _id_set(self.session.settings, DISABLED_KEY)

    def _enabled_ids(self) -> set[str]:
        return _id_set(self.session.settings, ENABLED_KEY)

    def is_enabled(self, plugin_id: str) -> bool:
        record = self.records.get(plugin_id)
        if record is None or record.broken:
            return False
        if record.manifest.foundation:
            return True
        if self.only is not None and plugin_id not in self._required_closure(self.only):
            return False
        return switched_on(self.session.settings, record.manifest, record.trust)

    def _required_closure(self, ids: set[str]) -> set[str]:
        result: set[str] = set()
        stack = list(ids)
        while stack:
            plugin_id = stack.pop()
            if plugin_id in result:
                continue
            result.add(plugin_id)
            record = self.records.get(plugin_id)
            if record is not None:
                stack.extend(record.manifest.requires)
        # Foundation plugins are always part of the app.
        result |= {pid for pid, rec in self.records.items() if rec.manifest.foundation}
        return result

    # ---- Ordering -------------------------------------------------------------------------

    def load_order(self) -> list[str]:
        order: list[str] = []
        visiting: set[str] = set()

        def visit(plugin_id: str) -> None:
            if plugin_id in order or plugin_id not in self.records:
                return
            if plugin_id in visiting:
                # A dependency cycle: don't crash startup. Plugins in a cycle of
                # hard requirements fail to activate ("requires …") and are reported.
                logger.debug("Plugin dependency cycle involving '%s'", plugin_id)
                return
            visiting.add(plugin_id)
            manifest = self.records[plugin_id].manifest
            for dep in manifest.requires + manifest.optional:
                visit(dep)
            visiting.discard(plugin_id)
            order.append(plugin_id)

        # Foundation plugins first so feature plugins can rely on them, then the other built-in
        # ones: a third-party plugin can't take a name a built-in plugin registers.
        ids = sorted(self.records, key=lambda pid: (not self.records[pid].manifest.foundation,
                                                    not self.records[pid].manifest.builtin, pid))
        for plugin_id in ids:
            visit(plugin_id)
        return order

    def dependents(self, plugin_id: str) -> list[str]:
        """Active plugins that (transitively) require ``plugin_id``."""
        result: list[str] = []
        for other_id in self.load_order():
            record = self.records[other_id]
            if not record.active or other_id == plugin_id:
                continue
            if plugin_id in record.manifest.requires or any(dep in result for dep in record.manifest.requires):
                result.append(other_id)
        return result

    # ---- Lifecycle -------------------------------------------------------------------------

    def activate_all(self) -> None:
        self.started = True
        for plugin_id in self.load_order():
            record = self.records[plugin_id]
            if record.active or record.broken:
                continue
            if not self.is_enabled(plugin_id):
                record.state = "disabled"
                continue
            self.activate(plugin_id)
        self._changed()

    def activate(self, plugin_id: str) -> bool:
        record = self.records[plugin_id]
        if record.active:
            return True
        manifest = record.manifest
        if manifest.api != API_VERSION:
            record.state, record.error = "error", f"needs plugin API {manifest.api}, this pixelRef has {API_VERSION}"
            return False
        if manifest.min_app_version and parse_version(APP_VERSION) < parse_version(manifest.min_app_version):
            record.state, record.error = "error", f"needs pixelRef {manifest.min_app_version} or newer"
            return False
        if not record.trusted:
            record.state, record.error = "untrusted", record.trust_reason
            return False
        for dep in manifest.requires:
            dep_record = self.records.get(dep)
            if dep_record is None:
                record.state, record.error = "error", f"requires missing plugin '{dep}'"
                return False
            if dep_record.state == "unavailable":
                self._mark_unavailable(record, dep_record.missing_packages, via=dep)
                return False
            if not dep_record.active:
                record.state, record.error = "error", f"requires '{dep}', which is {dep_record.state}"
                return False
        missing = self.missing_packages(plugin_id)
        if missing:
            self._mark_unavailable(record, missing)
            return False
        record.missing_packages = []
        context = PluginContext(self.session, plugin_id, overrides=frozenset(manifest.overrides))
        try:
            module = record.module or self._import(record)
            record.module = module
            activate = getattr(module, "activate", None)
            if callable(activate):
                activate(context)
        except PluginTrustError as exc:
            context.dispose()
            record.state, record.error = "untrusted", str(exc)
            return False
        except Exception as exc:  # noqa: BLE001
            context.dispose()
            record.state, record.error = "error", f"{type(exc).__name__}: {exc}"
            logger.error("Plugin '%s' failed to activate:\n%s", plugin_id, traceback.format_exc())
            return False
        record.context = context
        record.state, record.error = "active", ""
        if record.trust in ("trusted", "verified") and self._rehash(record) and not record.trusted:
            # Files changed between the check and the end of activation: don't keep it.
            self.deactivate(plugin_id, record_state="untrusted")
            self._forget_module(record)
            record.error = "its files changed while it was loading"
            return False
        if self.ui_host is not None:
            self.activate_ui(plugin_id)
        return record.active  # False when the UI half failed and switched it off

    def activate_ui(self, plugin_id: str) -> bool:
        record = self.records[plugin_id]
        if not record.active or record.ui_context is not None or self.ui_host is None:
            return False
        activate_ui = getattr(record.module, "activate_ui", None)
        if not callable(activate_ui):
            return True
        ui_context = self.ui_host.make_ui_context(record)
        # The host may batch UI work around activation (e.g. rebuild keyboard
        # shortcuts once instead of once per binding) via a ``ui_batch()`` context manager.
        ui_batch = getattr(self.ui_host, "ui_batch", None)
        try:
            with ui_batch() if callable(ui_batch) else contextlib.nullcontext():
                activate_ui(ui_context)
        except Exception as exc:  # noqa: BLE001
            ui_context.dispose()
            logger.error("Plugin '%s' UI failed to activate:\n%s", plugin_id, traceback.format_exc())
            self.deactivate(plugin_id, record_state="error")
            record.error = f"UI: {type(exc).__name__}: {exc}"
            return False
        record.ui_context = ui_context
        return True

    def attach_ui(self, host: Any) -> None:
        """Called once by the Qt app: activate the UI half of every active plugin."""
        self.ui_host = host
        for plugin_id in self.load_order():
            if self.records[plugin_id].active:
                self.activate_ui(plugin_id)
        self._changed()

    def deactivate(self, plugin_id: str, record_state: str = "inactive") -> None:
        record = self.records.get(plugin_id)
        if record is None or not record.active:
            return
        for dependent in reversed(self.dependents(plugin_id)):
            self.deactivate(dependent)
        if record.ui_context is not None:
            record.ui_context.dispose()
            record.ui_context = None
        deactivate = getattr(record.module, "deactivate", None)
        if callable(deactivate) and record.context is not None:
            try:
                deactivate(record.context)
            except Exception:  # noqa: BLE001
                logger.exception("Plugin '%s' deactivate() failed", plugin_id)
        if record.context is not None:
            record.context.dispose()
            record.context = None
        record.state = record_state

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        record = self.records.get(plugin_id)
        if record is None:
            raise PluginError(f"Unknown plugin '{plugin_id}'")
        if record.manifest.foundation and not enabled:
            raise PluginError(f"'{plugin_id}' is a foundation plugin and can't be disabled")
        if enabled and not record.trusted:
            raise PluginError(f"'{plugin_id}' needs your approval before it can run ({record.trust_reason}): "
                              f"review it in Window → Plugins…, or run pxcmd.plugin_trust('{plugin_id}')")
        disabled = self._disabled_ids()
        enabled_ids = self._enabled_ids()
        if enabled:
            disabled.discard(plugin_id)
            enabled_ids.add(plugin_id)
        else:
            disabled.add(plugin_id)
            enabled_ids.discard(plugin_id)
        self.session.settings.setValue(DISABLED_KEY, sorted(disabled))
        self.session.settings.setValue(ENABLED_KEY, sorted(enabled_ids))
        if enabled:
            for dep in record.manifest.requires:
                if dep in self.records and not self.records[dep].active and self.is_enabled(dep):
                    self.activate(dep)
            if record.state in ("error", "unavailable", "untrusted"):
                record.state = "discovered"
            if self.activate(plugin_id):
                self._reactivate_dependents(plugin_id)
        else:
            self.deactivate(plugin_id, record_state="disabled")
            record.state = "disabled"
        self._changed()

    def _reactivate_dependents(self, plugin_id: str) -> None:
        """Bring back plugins that were switched off because ``plugin_id`` was."""
        for other_id in self.load_order():
            other = self.records[other_id]
            if other.active or other.broken or other_id == plugin_id or not self.is_enabled(other_id):
                continue
            waiting = other.state in ("inactive", "unavailable") or (
                other.state == "error" and other.error.startswith("requires "))
            if not waiting:
                continue
            needed: set[str] = set()
            stack = list(other.manifest.requires)
            while stack:
                dep = stack.pop()
                if dep not in needed:
                    needed.add(dep)
                    dep_record = self.records.get(dep)
                    if dep_record is not None:
                        stack.extend(dep_record.manifest.requires)
            if plugin_id in needed:
                self.activate(other_id)

    def reload(self, plugin_id: str) -> bool:
        """Developer convenience: deactivate, re-import the code, activate again.

        The files are checked again first: an approved plugin whose files changed
        needs approving again (a trusted developer folder doesn't)."""
        record = self._record(plugin_id)
        dependents = self.dependents(plugin_id)
        self.deactivate(plugin_id)
        self._forget_module(record)
        if not record.manifest.builtin:
            self._rehash(record)
        ok = self.activate(plugin_id)
        for dependent in dependents:
            self.activate(dependent)
        self._changed()
        return ok

    def _import(self, record: PluginRecord) -> types.ModuleType:
        manifest = record.manifest
        path = manifest.path
        assert path is not None
        if manifest.builtin:
            return importlib.import_module(f"pixelref.builtins.{path.name}")
        # Check the files right before running them: the code that runs is the code that was approved.
        self._rehash(record)
        if not record.trusted:
            raise PluginTrustError(record.trust_reason)
        init = path / "__init__.py"
        if "__init__.py" not in record.files:
            raise PluginError(f"{path} has no __init__.py")
        source = init.read_bytes()
        if bytes_hash(source) != record.files["__init__.py"]:
            raise PluginTrustError("__init__.py changed while it was being loaded")
        if EXTERNAL_PACKAGE not in sys.modules:
            package = types.ModuleType(EXTERNAL_PACKAGE)
            package.__path__ = []  # type: ignore[attr-defined]
            sys.modules[EXTERNAL_PACKAGE] = package
        module_name = f"{EXTERNAL_PACKAGE}.{manifest.id}"
        spec = importlib.util.spec_from_file_location(module_name, init, submodule_search_locations=[str(path)])
        if spec is None:
            raise PluginError(f"Cannot import plugin from {path}")
        module = importlib.util.module_from_spec(spec)
        self.monitor.watch(manifest.id, path, manifest.permissions)
        sys.modules[module_name] = module
        try:
            # Run the bytes that were just checked, not whatever the file holds a moment later.
            exec(compile(source, str(init), "exec", dont_inherit=True), module.__dict__)  # noqa: S102
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        return module

    # ---- Info --------------------------------------------------------------------------

    def _record(self, plugin_id: str) -> PluginRecord:
        record = self.records.get(plugin_id)
        if record is None:
            raise PluginError(f"Unknown plugin '{plugin_id}'")
        return record

    def get(self, plugin_id: str) -> PluginRecord | None:
        return self.records.get(plugin_id)

    def is_active(self, plugin_id: str) -> bool:
        record = self.records.get(plugin_id)
        return bool(record and record.active)

    def readme(self, plugin_id: str) -> str:
        """The plugin's long description (Markdown): its ``readme`` file, else README.md, else ''."""
        record = self._record(plugin_id)
        folder = record.manifest.path
        if folder is None:
            return ""
        names = [record.manifest.readme] if record.manifest.readme else []
        names += ["README.md", "readme.md"]
        for name in names:
            path = folder / name
            if path.is_file() and path.resolve().is_relative_to(folder.resolve()):
                try:
                    with open(path, encoding="utf-8", errors="replace") as handle:
                        return handle.read(README_LIMIT)
                except OSError:
                    return ""
        return ""

    def _candidate_info(self, record: PluginRecord) -> dict[str, Any]:
        source = record.source
        return {
            "path": str(record.manifest.path or ""),
            "version": record.manifest.version,
            "source": source.title if source else "",
            "trust": record.trust,
            "trust_label": TRUST_LABELS.get(record.trust, record.trust),
            "signer": record.signature.signer if record.signature.status == "valid" else "",
            "author": record.manifest.author.get("name", ""),
            "blocked": record.blocked,
            "broken": record.error if record.broken else "",
            "builtin": record.manifest.builtin,
        }

    def info(self, plugin_id: str, _review: list[str] | None = None) -> dict[str, Any]:
        record = self._record(plugin_id)
        manifest = record.manifest
        source = record.source
        chosen_path = str(manifest.path or "")
        return {
            "id": plugin_id,
            "name": manifest.name,
            "version": manifest.version,
            "status": record.state,
            "error": record.error,
            "foundation": manifest.foundation,
            "builtin": manifest.builtin,
            "requires": list(manifest.requires),
            "optional": list(manifest.optional),
            "python_requires": [str(r) for r in manifest.python_requires],
            "missing_packages": list(record.missing_packages),
            "enabled": self.is_enabled(plugin_id),
            "description": manifest.description,
            "path": chosen_path,
            "author": dict(manifest.author),
            "license": manifest.license,
            "homepage": manifest.homepage,
            "repository": manifest.repository,
            "issues": manifest.issues,
            "category": manifest.category,
            "tags": list(manifest.tags),
            "min_app_version": manifest.min_app_version,
            "permissions": sorted(manifest.permissions),
            "overrides": list(manifest.overrides),
            "replaces": manifest.replaces,
            "source": source.title if source else "",
            "source_kind": source.kind if source else "",
            "source_path": str(source.path) if source else "",
            "trust": record.trust,
            "trust_label": TRUST_LABELS.get(record.trust, record.trust),
            "trust_reason": record.trust_reason,
            "signature": record.signature.to_dict(),
            "content_hash": record.digest,
            "conflicts": [self._candidate_info(other) for other in record.conflicts],
            "warnings": list(manifest.warnings) + (source.warnings() if source else []),
            "publish_missing": list(manifest.publish_missing),
            "needs_review": plugin_id in (self.needs_review() if _review is None else _review),
            "activity": None if manifest.builtin else self.monitor.summary(plugin_id),
            "broken": record.broken,
        }

    def list_plugins(self) -> list[dict[str, Any]]:
        review = self.needs_review()
        return [self.info(plugin_id, review) for plugin_id in self.load_order()]

    def watch(self, callback: Callable[[], None]) -> Disposable:
        self._listeners.append(callback)
        return Disposable(lambda: callback in self._listeners and self._listeners.remove(callback))

    def _changed(self) -> None:
        for callback in list(self._listeners):
            callback()
        self.session.events.emit("plugins.changed", {})
