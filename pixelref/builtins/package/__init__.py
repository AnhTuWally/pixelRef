"""Packages: a board and copies of everything it links, verified; relinking missing files by content.

**Save Package** writes ``<name>.pxref`` plus a ``<name>.files/`` folder holding
a copy of every linked file (images, EXRs, movies, sequence frames, 3D models
with their ``.mtl`` / ``.bin`` / textures: the ``files.companions`` extension
point) and a ``manifest.json``::

    {"id": "7f3a9c…", "files": [{"path": "robot/robot.gltf", "size": 1234, "qh": "…", "sha1": "…"}, …]}

Each source folder becomes one subfolder, so companions stay beside their model
and same-named files from different folders never collide. Every copy is
hashed while it is written and checked against the source before it is put in
place; files already there with a matching manifest entry are skipped. The
manifest is written last, then the scene (both swapped in atomically), so an
interrupted run never leaves a manifest claiming files that are not there. The
scene written is a scratch copy with rewritten, plain relative paths and a
``package`` section carrying the manifest's id: the open board is untouched.

**Verify Package** re-checks every manifest entry (present, size, full SHA-1).

**Find Missing Files** relinks broken links from a folder: a package folder
with this scene's id is simply remembered (no scene edit); otherwise files are
matched by size, then fingerprint (whatever their names), falling back to the
file name only for links saved before fingerprints, and only when exactly one
candidate exists. Matches are remembered on this machine, so other boards
using the same files heal too (see ``core.links``).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pixelref import __version__
from pixelref.core import links
from pixelref.core.scene_file import SCENE_EXT, SceneParser
from pixelref.core.scene_merge import copy_scene
from pixelref.plugin_api import CommandError

COPY_CHUNK = 4 * 1024 * 1024
Progress = Callable[[int, int, str], None]  # (index, count, relative path); may raise to cancel


def safe_file_name(name: str) -> str:
    from ..export import safe_file_name as export_safe_name

    return export_safe_name(name)


@dataclass
class PackageFile:
    source: Path
    relative: str  # posix, inside the files folder
    fp: dict[str, Any] | None


@dataclass
class PackagePlan:
    """What Save Package will do (the pre-flight: nothing written yet)."""

    folder: Path
    scene_file: Path
    files_dir: Path
    package_id: str
    files: list[PackageFile] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # "node: stored path"
    old_manifest: dict[str, Any] | None = None
    snapshot: Any = None  # SceneSnapshot of the scratch scene

    @property
    def total_bytes(self) -> int:
        return sum(int(f.fp["size"]) for f in self.files if f.fp)

    def summary(self) -> dict[str, Any]:
        return {"scene": self.scene_file.as_posix(), "files_folder": self.files_dir.as_posix(),
                "files": len(self.files), "bytes": self.total_bytes, "missing": list(self.missing)}


def copy_verified(source: Path, dest: Path) -> str:
    """Copy ``source`` to ``dest`` (through ``dest.tmp``), hashing while copying and re-reading the
    copy to check it; returns the SHA-1. Raises OSError when the copy differs."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name(dest.name + ".tmp")
    digest = hashlib.sha1()
    try:
        with open(source, "rb") as src, open(temp, "wb") as out:
            while True:
                block = src.read(COPY_CHUNK)
                if not block:
                    break
                digest.update(block)
                out.write(block)
        sha1 = digest.hexdigest()
        if links.full_hash(temp) != sha1:
            raise OSError(f"The copy of {source.name} differs from the original")
        shutil.copystat(source, temp)
        os.replace(temp, dest)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return sha1


class PackageApi:
    """Save / verify packages and find missing files (shared by the commands and the UI)."""

    def __init__(self, session: Any) -> None:
        self.session = session

    # ---- Linked files ------------------------------------------------------------------------

    def companions(self, node: Any, attr: str, path: Path) -> list[Path]:
        found: list[Path] = []
        for provide in self.session.extensions.contributions("files.companions"):
            try:
                found.extend(Path(p) for p in provide(self.session, node, attr, path) or ())
            except Exception:  # noqa: BLE001  (one plugin's bug must not stop packaging)
                logging.getLogger(__name__).exception("files.companions failed for %s", path)
        return found

    def missing_links(self) -> list[tuple[str, str, str]]:
        """``(node, attribute, stored path)`` of every linked file that cannot be found."""
        resolver = self.session.links
        result = []
        for name, attr, value in resolver.path_attrs():
            path = resolver.resolve(value)
            if not resolver.present(path, resolver.record(value)):
                result.append((name, attr, value))
        return result

    # ---- Save Package -------------------------------------------------------------------------

    def plan(self, folder: str | Path, name: str | None = None) -> PackagePlan:
        """The pre-flight of Save Package: files to copy, total size, what is missing. Writes nothing."""
        session = self.session
        resolver = session.links
        scene = session.scene
        stem = safe_file_name(name or (session.scene_path.stem if session.scene_path else "Board"))
        if stem.lower().endswith(SCENE_EXT):
            stem = stem[:-len(SCENE_EXT)]
        folder = Path(folder)
        files_dir = folder / f"{stem}{links.FILES_SUFFIX}"
        if session.scene_path is not None and (folder / f"{stem}{SCENE_EXT}").resolve() == session.scene_path.resolve():
            raise CommandError("A package can't replace the open board's own file: choose another folder or name")
        old_manifest = links.read_manifest(files_dir)
        package_id = old_manifest["id"] if old_manifest else uuid.uuid4().hex
        plan = PackagePlan(folder, folder / f"{stem}{SCENE_EXT}", files_dir, package_id, old_manifest=old_manifest)

        subfolders: dict[str, str] = {}  # source folder (normcased) -> subfolder name
        taken: set[str] = set()

        def subfolder(source_dir: Path) -> str:
            key = os.path.normcase(str(source_dir))
            if key not in subfolders:
                base = safe_file_name(source_dir.name) if source_dir.name else "files"
                if base in ("", "image"):
                    base = "files"
                candidate, counter = base, 1
                while candidate.lower() in taken:
                    candidate = f"{base}_{counter}"
                    counter += 1
                taken.add(candidate.lower())
                subfolders[key] = candidate
            return subfolders[key]

        by_source: dict[str, PackageFile] = {}

        def add(source: Path, relative: str) -> str:
            key = os.path.normcase(str(source))
            if key not in by_source:
                by_source[key] = PackageFile(source, relative, links.fingerprint(source))
                plan.files.append(by_source[key])
            return by_source[key].relative

        new_values: dict[tuple[str, str], str] = {}
        records: dict[str, Any] = {}
        old_records = resolver.records()
        for node_name, attr, value in resolver.path_attrs():
            node = scene.nodes[node_name]
            source = resolver.resolve(value)
            try:
                source = source.resolve()
            except OSError:
                pass
            sequence = links.is_pattern(source)
            companions = self.companions(node, attr, source)
            if sequence:
                present = [p for p in companions if p.is_file()] or [p for _n, p in links.scan_frames(source)]
            else:
                present = [source] if source.is_file() else []
            if not present:
                plan.missing.append(f"{node_name}: {value}")
                keep = resolver.portable(source, folder)
                new_values[(node_name, attr)] = keep
                if isinstance(old_records.get(value), dict):
                    records[keep] = old_records[value]
                continue
            sub = subfolder(source.parent)
            if not sequence:
                add(source, f"{sub}/{source.name}")
            for companion in companions:
                try:
                    companion = companion.resolve()
                except OSError:
                    continue
                if not companion.is_file():
                    continue
                try:  # beside (or below) the model: same place relative to it in the package
                    add(companion, f"{sub}/{companion.relative_to(source.parent).as_posix()}")
                except ValueError:
                    add(companion, f"{subfolder(companion.parent)}/{companion.name}")
            new_value = f"{files_dir.name}/{sub}/{source.name}"
            new_values[(node_name, attr)] = new_value
            record = links.link_record(source)
            if record is not None:
                records[new_value] = record

        scratch = copy_scene(scene)
        for (node_name, attr), value in new_values.items():
            scratch.nodes[node_name].attrs[attr] = value
        if records:
            scratch.sections[links.LINKS_SECTION] = records
        else:
            scratch.sections.pop(links.LINKS_SECTION, None)
        scratch.sections[links.PACKAGE_SECTION] = {"id": package_id}
        plan.snapshot = SceneParser().snapshot(scratch)
        return plan

    def execute(self, plan: PackagePlan, progress: Progress | None = None) -> dict[str, Any]:
        """Copy the files, then write the manifest and the scene. Touches no live scene state, so it
        may run on a worker thread. Raises OSError (or whatever ``progress`` raises to cancel)."""
        files_dir = plan.files_dir
        files_dir.mkdir(parents=True, exist_ok=True)
        old_entries = {e["path"]: e for e in (plan.old_manifest or {}).get("files", [])}
        # A manifest only ever describes a finished package: the old one goes before the first write.
        (files_dir / links.MANIFEST_NAME).unlink(missing_ok=True)
        entries: list[dict[str, Any]] = []
        copied = skipped = 0
        count = len(plan.files)
        for index, item in enumerate(plan.files):
            if progress is not None:
                progress(index, count, item.relative)
            dest = links.safe_inside(files_dir, item.relative)
            if dest is None:
                raise OSError(f"Not a safe file name inside the package: {item.relative}")
            fp = links.fingerprint(item.source) or item.fp
            if fp is None:
                raise OSError(f"{item.source} could not be read")
            old = old_entries.get(item.relative)
            if (old is not None and old.get("sha1") and links.same_content(old, fp)
                    and links.same_content(links.fingerprint(dest), fp)):
                sha1 = str(old["sha1"])
                skipped += 1
            else:
                sha1 = copy_verified(item.source, dest)
                copied += 1
            entries.append({"path": item.relative, "size": int(fp["size"]), "qh": fp["qh"], "sha1": sha1})
        if progress is not None:
            progress(count, count, "")
        links.write_json_atomic(files_dir / links.MANIFEST_NAME,
                                {"id": plan.package_id, "pixelref_version": __version__, "files": entries})
        plan.snapshot.write(plan.scene_file)
        return {**plan.summary(), "copied": copied, "skipped": skipped, "id": plan.package_id}

    def finish(self, plan: PackagePlan) -> None:
        """After a package was written (on the UI thread): this machine remembers where it is."""
        self.session.links.remember_package(plan.package_id, plan.files_dir)

    # ---- Verify Package -----------------------------------------------------------------------

    def package_dir(self, folder: str | Path | None = None) -> Path:
        """The files folder to verify: ``folder`` (or a folder with a manifest inside it), else the
        open scene's package folder."""
        if folder is None:
            found = self.session.links.package_folder()
            if found is None:
                raise CommandError("This board has no package folder (use Save Package…, or name a folder)")
            return found
        folder = Path(folder)
        if links.read_manifest(folder) is not None:
            return folder
        try:
            inner = sorted(p for p in folder.iterdir() if p.is_dir() and links.read_manifest(p) is not None)
        except OSError:
            inner = []
        if not inner:
            raise CommandError(f"No package manifest in {folder}")
        return inner[0]

    def verify(self, folder: str | Path | None = None, progress: Progress | None = None) -> dict[str, Any]:
        """Every manifest entry checked: present, size, full SHA-1."""
        files_dir = self.package_dir(folder)
        manifest = links.read_manifest(files_dir)
        if manifest is None:
            raise CommandError(f"No package manifest in {files_dir}")
        problems: list[str] = []
        entries = manifest["files"]
        for index, entry in enumerate(entries):
            if progress is not None:
                progress(index, len(entries), entry["path"])
            path = links.safe_inside(files_dir, entry["path"])
            expected = links._int(entry.get("size"))
            if path is None or not path.is_file():
                problems.append(f"{entry['path']}: missing")
                continue
            size = path.stat().st_size
            if size != expected:
                problems.append(f"{entry['path']}: size {size:,}, expected {expected:,}")
                continue
            try:
                if entry.get("sha1") and links.full_hash(path) != entry["sha1"]:
                    problems.append(f"{entry['path']}: content differs from when it was packaged")
            except OSError as exc:
                problems.append(f"{entry['path']}: {exc}")
        total = len(entries)
        return {"folder": files_dir.as_posix(), "id": manifest["id"], "total": total,
                "ok": total - len(problems), "problems": problems}

    # ---- Find Missing Files ---------------------------------------------------------------------

    def find_missing(self, folder: str | Path) -> dict[str, Any]:
        """Relink missing files from ``folder``: by package id, else by content, else (links saved
        without fingerprints) by a unique file name. Changes the scene: run it as one command."""
        session = self.session
        resolver = session.links
        folder = Path(folder)
        if not folder.is_dir():
            raise CommandError(f"Not a folder: {folder}")
        before = self.missing_links()
        result: dict[str, Any] = {"missing": len(before), "relinked": 0, "package": False,
                                  "not_found": [], "ambiguous": [], "differs": []}
        if not before:
            return result
        package_id = resolver.package_id()
        if package_id is not None:
            for candidate in [folder, *self._subfolders(folder)]:
                manifest = links.read_manifest(candidate)
                if manifest is not None and manifest["id"] == package_id:
                    resolver.remember_package(package_id, candidate)
                    resolver.forget()
                    result["package"] = True
                    break
        missing = self.missing_links() if result["package"] else before

        wanted_sizes: set[int] = set()
        wanted_names: set[str] = set()
        for _node, _attr, value in missing:
            fp = links.record_fingerprint(resolver.record(value))
            if fp is not None:
                wanted_sizes.add(int(fp["size"]))
            wanted_names.add(Path(value).name.lower())
        by_size: dict[int, list[Path]] = {}
        all_files: list[Path] = []
        for root, _dirs, names in os.walk(folder):
            for file_name in names:
                path = Path(root) / file_name
                all_files.append(path)
                if not wanted_sizes:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size in wanted_sizes:
                    by_size.setdefault(size, []).append(path)

        relinked = 0
        scene = session.scene
        for node_name, attr, value in missing:
            label = f"{node_name}: {Path(value).name}"
            record = resolver.record(value)
            fp = links.record_fingerprint(record)
            sequence = links.is_pattern(value)
            found: Path | None = None
            if fp is not None:
                match = next((p for p in by_size.get(int(fp["size"]), [])
                              if links.same_content(links.fingerprint(p), fp)), None)
                if match is not None:
                    resolver.remember(fp, match)
                    found = links.pattern_beside(match, value) if sequence else match
                elif self._name_matches(value, all_files):
                    result["differs"].append(f"{label}: name matches, content differs")
                    continue
            else:
                candidates = self._name_matches(value, all_files)
                folders = {os.path.normcase(str(p.parent)) for p in candidates}
                if sequence and len(folders) == 1:
                    found = links.pattern_beside(candidates[0], value)
                elif not sequence and len(candidates) == 1:
                    found = candidates[0]
                elif candidates:
                    result["ambiguous"].append(f"{label}: {len(candidates)} files with this name")
                    continue
            if found is None:
                result["not_found"].append(label)
                continue
            new_value = session.to_scene_path(found)
            scene.set_attr(node_name, attr, new_value)
            if isinstance(record, dict):
                records = dict(resolver.records())
                records[new_value] = record
                scene.sections[links.LINKS_SECTION] = records
            relinked += 1
        resolver.forget()
        still = len(self.missing_links())
        result["relinked"] = len(before) - still if result["package"] else relinked
        if result["package"]:
            result["not_found"] = result["not_found"] or [f"{n}: {Path(v).name}" for n, _a, v in self.missing_links()]
        return result

    @staticmethod
    def _subfolders(folder: Path) -> list[Path]:
        try:
            return sorted(p for p in folder.iterdir() if p.is_dir())
        except OSError:
            return []

    @staticmethod
    def _name_matches(value: str, files: list[Path]) -> list[Path]:
        """Files named like a stored path (for a sequence pattern: like any of its frames)."""
        name = Path(value).name.lower()
        if not links.is_pattern(value):
            return [p for p in files if p.name.lower() == name]
        head, _sep, tail = name.rpartition("#")
        head = head.rstrip("#")
        result = []
        for path in files:
            low = path.name.lower()
            if low.startswith(head) and low.endswith(tail) and low[len(head):len(low) - len(tail)].isdigit():
                result.append(path)
        return result


def status_text(result: dict[str, Any]) -> str:
    """"Relinked 12 of 14 · 2 not found" for a Find Missing Files result."""
    if not result["missing"]:
        return "No missing files"
    text = f"Relinked {result['relinked']} of {result['missing']}"
    for key, label in (("not_found", "not found"), ("ambiguous", "ambiguous"), ("differs", "content differs")):
        if result[key]:
            text += f" · {len(result[key])} {label}"
    return text


def verify_text(result: dict[str, Any]) -> str:
    if not result["problems"]:
        return f"{result['ok']} of {result['total']} present and intact"
    return f"{result['ok']} of {result['total']} intact · " + "; ".join(result["problems"][:3]) + (
        " …" if len(result["problems"]) > 3 else "")


def activate(ctx: Any) -> None:
    session = ctx.session
    api = PackageApi(session)
    ctx.provide_service("packages", api)

    def save(folder: str, name: str | None = None) -> dict[str, Any]:
        """Save the board and a verified copy of every file it links (``<name>.files/``) in ``folder``.

        Writes ``<name>.pxref`` plus ``<name>.files/`` with a ``manifest.json``; the open board is
        untouched. Returns a summary."""
        plan = api.plan(folder, name)
        try:
            result = api.execute(plan)
        except OSError as exc:
            raise CommandError(f"Could not save the package: {exc}") from exc
        api.finish(plan)
        note = f" · {len(plan.missing)} missing file(s) left out" if plan.missing else ""
        session.events.emit("ui.status", {"text": f"Packaged {result['files']} file(s) → {plan.scene_file.name}{note}"})
        return result

    def preflight(folder: str, name: str | None = None) -> dict[str, Any]:
        """What Save Package would write to ``folder``: files, total bytes, missing links. Writes nothing."""
        return api.plan(folder, name).summary()

    def verify(folder: str | None = None) -> dict[str, Any]:
        """Check a package (default: the open board's): every file present, right size and content."""
        result = api.verify(folder)
        session.events.emit("ui.status", {"text": verify_text(result)})
        return result

    def find_missing(folder: str) -> dict[str, Any]:
        """Relink missing linked files from a folder (by package id, content, or a unique name)."""
        result = api.find_missing(folder)
        session.events.emit("ui.status", {"text": status_text(result)})
        return result

    def missing() -> list[str]:
        """The linked files of the open board that cannot be found ("node: path")."""
        return [f"{n}: {v}" for n, _a, v in api.missing_links()]

    def roots() -> dict[str, str]:
        """The named path roots (``${NAME}`` in stored paths), ``{NAME: folder}``."""
        return session.links.roots()

    def set_root(name: str, folder: str) -> dict[str, str]:
        """Add or change a named path root: files under ``folder`` are saved as ``${NAME}/…``."""
        try:
            session.links.set_root(name, folder)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        return session.links.roots()

    def remove_root(name: str) -> bool:
        """Remove a named path root."""
        return session.links.remove_root(name)

    def variables() -> dict[str, str]:
        """The board's own path variables, ``{NAME: folder}``."""
        return session.links.variables()

    def set_variables(values: Any = None) -> dict[str, str]:
        """Set the board's path variables (saved with the board): ``{"STUDIO_DRIVE": "W:/",
        "LINUX_PATH": "/mnt/drive"}``, or text with one ``NAME = folder`` per line.

        ``${NAME}/…`` in a linked path means that folder, files below one are saved that way, and a
        linked file that is not where its path says is looked for below every variable's folder (so
        the same board opens where the drive is ``W:/`` and where it is ``/mnt/drive``). A named
        root or environment variable of the same name on a machine wins over the board's value.
        An empty value clears them."""
        try:
            parsed = links.parse_variables(values)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        if parsed != session.links.variables():
            session.scene.set_plugin_data(links.SEARCH_KEY, {"vars": parsed} if parsed else None)
            session.links.forget()
        return parsed

    ctx.command("variables", variables, alias="path_variables", undoable=False, echo=False)
    ctx.command("set_variables", set_variables, alias="set_path_variables", label="Path variables")
    ctx.command("save", save, alias="save_package", undoable=False)
    ctx.command("preflight", preflight, undoable=False, echo=False)
    ctx.command("verify", verify, alias="verify_package", undoable=False)
    ctx.command("find_missing", find_missing, alias="find_missing_files", label="Find missing files")
    ctx.command("missing", missing, alias="missing_files", undoable=False, echo=False)
    ctx.command("roots", roots, undoable=False, echo=False)
    ctx.command("set_root", set_root, alias="set_path_root", undoable=False)
    ctx.command("remove_root", remove_root, alias="remove_path_root", undoable=False)


def activate_ui(ui: Any) -> None:
    from .ui import activate_package_ui

    activate_package_ui(ui)
