"""The plugin runtime: discovery, dependency resolution and lifecycle.

A plugin is a folder with a ``plugin.toml`` manifest and a Python package::

    my_plugin/
        plugin.toml        id, name, version, api, requires, foundation…
                       [python] requires = ["numpy>=1.26"]  (Python packages)
        __init__.py        def activate(ctx): …       (headless part, always)
                           def activate_ui(ui): …     (only when a Qt UI runs)
                           def deactivate(ctx): …     (optional)

Plugins are found in the built-in folder (``pixelref/builtins``), the user
plugin folder, ``$PIXELREF_PLUGIN_PATH`` and any extra paths given on the
command line. They activate in dependency order; a plugin that fails — or
whose requirements are missing or disabled — is switched off and reported,
and the app keeps running. A plugin whose Python packages aren't installed is
*unavailable* (so are plugins that require it) until they are: the Plugins
window installs them (see :mod:`pixelref.core.packages`). Requirements are
checked without importing anything.

Everything a plugin registers goes through its context and is undone when it
is deactivated, so plugins can be enabled and disabled while the app runs.
Foundation plugins (the select tool, outliner, inspector…) can't be disabled.
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import logging
import os
import sys
import tomllib
import traceback
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .attributes import Attr
from .errors import PluginError
from .extensions import Disposable
from .packages import Requirement, requirement_list

if TYPE_CHECKING:
    from .session import Session

logger = logging.getLogger(__name__)

API_VERSION = 1
BUILTIN_DIR = Path(__file__).resolve().parents[1] / "builtins"
EXTERNAL_PACKAGE = "pixelref_plugins"
DISABLED_KEY = "plugins/disabled"
ENABLED_KEY = "plugins/enabled"


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

    @classmethod
    def load(cls, folder: Path, builtin: bool) -> "PluginManifest":
        manifest_path = folder / "plugin.toml"
        try:
            data = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise PluginError(f"{manifest_path}: {exc}") from exc
        plugin_id = str(data.get("id") or folder.name)
        python = data.get("python", {})
        if not isinstance(python, dict):
            raise PluginError(f"{manifest_path}: [python] must be a table")
        try:
            return cls(
                id=plugin_id,
                name=str(data.get("name", plugin_id)),
                version=str(data.get("version", "0.0.0")),
                api=int(data.get("api", API_VERSION)),
                description=str(data.get("description", "")),
                requires=_id_list(data.get("requires", [])),
                optional=_id_list(data.get("optional", [])),
                python_requires=requirement_list(python.get("requires")),
                python_optional=requirement_list(python.get("optional")),
                foundation=bool(data.get("foundation", False)),
                enabled_by_default=bool(data.get("enabled_by_default", True)),
                path=folder,
                builtin=builtin,
            )
        except (TypeError, ValueError) as exc:
            raise PluginError(f"{manifest_path}: {exc}") from exc


def _id_list(value: Any) -> list[str]:
    """``requires = "brush"`` means ["brush"], not one id per letter."""
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise TypeError(f"expected a list of plugin ids, got {value!r}")
    return [str(item) for item in value]


@dataclass
class PluginRecord:
    manifest: PluginManifest
    module: types.ModuleType | None = None
    state: str = "discovered"  # discovered, active, inactive, disabled, unavailable, error
    error: str = ""
    missing_packages: list[str] = field(default_factory=list)  # unmet Python requirements (unavailable)
    context: Any = None
    ui_context: Any = None
    broken: bool = False  # the manifest itself could not be read

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def active(self) -> bool:
        return self.state == "active"


class PluginContext:
    """What a plugin's ``activate(ctx)`` receives (headless facilities).

    Every registration made through the context is recorded and undone by
    :meth:`dispose` when the plugin is deactivated.
    """

    def __init__(self, session: "Session", plugin_id: str) -> None:
        self.session = session
        self.plugin_id = plugin_id
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

    def command(self, name: str, fn: Callable[..., Any], *, alias: str | None = None, **options: Any) -> Disposable:
        """Register ``<plugin id>.<name>`` (plus an optional flat alias)."""
        full = name if name.startswith(self.plugin_id + ".") else f"{self.plugin_id}.{name}"
        return self.track(self.session.commands.register(full, fn, alias=alias, plugin=self.plugin_id, **options))

    def declare_extension_point(self, name: str, doc: str = "", validator: Callable[[Any], None] | None = None
                                ) -> Disposable:
        return self.track(self.session.extensions.declare(name, owner=self.plugin_id, doc=doc, validator=validator))

    def contribute(self, point: str, value: Any, order: float = 0.0) -> Disposable:
        return self.track(self.session.extensions.contribute(point, value, plugin=self.plugin_id, order=order))

    def contributions(self, point: str) -> list[Any]:
        return self.session.extensions.contributions(point)

    def provide_service(self, name: str, service: Any) -> Disposable:
        return self.track(self.session.services.provide(name, service, plugin=self.plugin_id))

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
                 user_dir: Path | None = None, only: set[str] | None = None) -> None:
        self.session = session
        self.include_builtins = builtins
        self.user_dir = user_dir
        self.extra_paths = [Path(p) for p in paths]
        self.only = only  # restrict activation to these ids (+ their requirements); tests use it
        self.records: dict[str, PluginRecord] = {}
        self.ui_host: Any = None  # set by the Qt app; provides make_ui_context(record)
        self._listeners: list[Callable[[], None]] = []

    # ---- Discovery -----------------------------------------------------------------------

    def search_paths(self) -> list[tuple[Path, bool]]:
        paths: list[tuple[Path, bool]] = []
        if self.include_builtins:
            paths.append((BUILTIN_DIR, True))
        if self.user_dir is not None:
            paths.append((self.user_dir, False))
        for entry in os.environ.get("PIXELREF_PLUGIN_PATH", "").split(os.pathsep):
            if entry.strip():
                paths.append((Path(entry.strip()), False))
        paths.extend((path, False) for path in self.extra_paths)
        return paths

    def discover(self) -> dict[str, PluginRecord]:
        for folder, builtin in self.search_paths():
            if not folder.is_dir():
                continue
            for entry in sorted(folder.iterdir()):
                manifest: PluginManifest | None = None
                try:
                    if entry.is_dir() and (entry / "plugin.toml").exists():
                        manifest = PluginManifest.load(entry, builtin)
                except PluginError as exc:
                    record = PluginRecord(PluginManifest(id=entry.name, name=entry.name, path=entry))
                    record.state, record.error, record.broken = "error", str(exc), True
                    self.records.setdefault(entry.name, record)
                    continue
                if manifest is None:
                    continue
                if manifest.id in self.records:
                    existing = self.records[manifest.id]
                    if existing.manifest.path != manifest.path:
                        logger.warning("Plugin '%s' in %s ignored: already found in %s", manifest.id,
                                       manifest.path, existing.manifest.path)
                    continue
                self.records[manifest.id] = PluginRecord(manifest)
        packages = getattr(self.session, "packages", None)
        if packages is not None:
            packages.extra_find_links = self.wheel_folders()
        return self.records

    def wheel_folders(self) -> list[Path]:
        """Plugins' bundled ``wheels/`` folders: their packages install without internet."""
        folders = []
        for record in self.records.values():
            path = record.manifest.path
            if path is not None and path.is_dir() and (path / "wheels").is_dir():
                folders.append(path / "wheels")
        return folders

    # ---- Python packages -------------------------------------------------------------------

    def missing_packages(self, plugin_id: str) -> list[str]:
        """The plugin's Python requirements that aren't met (checked without importing)."""
        record = self.records.get(plugin_id)
        packages = getattr(self.session, "packages", None)
        if record is None or packages is None:
            return []
        return [str(req) for req in record.manifest.python_requires if not packages.status(req).ok]

    def packages_needed(self) -> dict[str, list[str]]:
        """Every unmet requirement of an enabled plugin: {requirement: [plugin ids]}."""
        needed: dict[str, list[str]] = {}
        for plugin_id, record in self.records.items():
            if record.broken or not self.is_enabled(plugin_id):
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
        value = self.session.settings.value(DISABLED_KEY, [])
        return set([value] if isinstance(value, str) else (value or []))

    def _enabled_ids(self) -> set[str]:
        value = self.session.settings.value(ENABLED_KEY, [])
        return set([value] if isinstance(value, str) else (value or []))

    def is_enabled(self, plugin_id: str) -> bool:
        record = self.records.get(plugin_id)
        if record is None:
            return False
        if record.manifest.foundation:
            return True
        if self.only is not None and plugin_id not in self._required_closure(self.only):
            return False
        if plugin_id in self._disabled_ids():
            return False
        if record.manifest.enabled_by_default:
            return True
        return plugin_id in self._enabled_ids()

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

        # Foundation plugins first so feature plugins can rely on them.
        ids = sorted(self.records, key=lambda pid: (not self.records[pid].manifest.foundation, pid))
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
        context = PluginContext(self.session, plugin_id)
        try:
            module = record.module or self._import(record)
            record.module = module
            activate = getattr(module, "activate", None)
            if callable(activate):
                activate(context)
        except Exception as exc:  # noqa: BLE001
            context.dispose()
            record.state, record.error = "error", f"{type(exc).__name__}: {exc}"
            logger.error("Plugin '%s' failed to activate:\n%s", plugin_id, traceback.format_exc())
            return False
        record.context = context
        record.state, record.error = "active", ""
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
            if record.state in ("error", "unavailable"):
                record.state = "discovered"
            if self.activate(plugin_id):
                self._reactivate_dependents(plugin_id)
        else:
            self.deactivate(plugin_id, record_state="disabled")
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
        """Developer convenience: deactivate, re-import the code, activate again."""
        record = self.records.get(plugin_id)
        if record is None:
            raise PluginError(f"Unknown plugin '{plugin_id}'")
        dependents = self.dependents(plugin_id)
        self.deactivate(plugin_id)
        if record.module is not None:
            prefix = record.module.__name__
            for name in [n for n in sys.modules if n == prefix or n.startswith(prefix + ".")]:
                del sys.modules[name]
            record.module = None
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
        if EXTERNAL_PACKAGE not in sys.modules:
            package = types.ModuleType(EXTERNAL_PACKAGE)
            package.__path__ = []  # type: ignore[attr-defined]
            sys.modules[EXTERNAL_PACKAGE] = package
        module_name = f"{EXTERNAL_PACKAGE}.{manifest.id}"
        spec = importlib.util.spec_from_file_location(
            module_name, path / "__init__.py", submodule_search_locations=[str(path)]
        )
        if spec is None or spec.loader is None:
            raise PluginError(f"Cannot import plugin from {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        return module

    # ---- Info --------------------------------------------------------------------------

    def get(self, plugin_id: str) -> PluginRecord | None:
        return self.records.get(plugin_id)

    def is_active(self, plugin_id: str) -> bool:
        record = self.records.get(plugin_id)
        return bool(record and record.active)

    def list_plugins(self) -> list[dict[str, Any]]:
        out = []
        for plugin_id in self.load_order():
            record = self.records[plugin_id]
            manifest = record.manifest
            out.append({
                "id": plugin_id,
                "name": manifest.name,
                "version": manifest.version,
                "status": record.state,
                "error": record.error,
                "foundation": manifest.foundation,
                "builtin": manifest.builtin,
                "requires": list(manifest.requires),
                "python_requires": [str(r) for r in manifest.python_requires],
                "missing_packages": list(record.missing_packages),
                "enabled": self.is_enabled(plugin_id),
                "description": manifest.description,
                "path": str(manifest.path) if manifest.path else "",
            })
        return out

    def watch(self, callback: Callable[[], None]) -> Disposable:
        self._listeners.append(callback)
        return Disposable(lambda: callback in self._listeners and self._listeners.remove(callback))

    def _changed(self) -> None:
        for callback in list(self._listeners):
            callback()
        self.session.events.emit("plugins.changed", {})
