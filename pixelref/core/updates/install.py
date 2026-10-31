"""Activation of a staged update (M4): runs in the independent helper, after the app has exited.

DISABLED BY DEFAULT. ``ASSISTED_INSTALL_ENABLED`` stays False until the recovery milestone's
fault-injection and manual upgrade/rollback gates pass (docs/update-security.md). Nothing in the app
calls this module yet.

What it does, in order (``run_activation``):

1. one lock per installation (refuses a second transaction);
2. waits for the app process(es) to exit. It never closes or kills them; if they don't exit it gives up
   and changes nothing;
3. re-verifies everything from disk: the signed manifest, the staged package and its per-file hashes,
   and that this is a standalone install (never a source checkout);
4. ownership check: only top-level entries made *entirely* of files listed in the installed release's
   inventory, with their listed hashes, may be replaced or removed; any unknown file inside, or an
   unknown file where the release wants to put something, blocks the update. ``portable.toml``,
   ``user-data/`` and every other unknown top-level entry are never touched;
5. journals ``ACTIVATING``, moves each owned top-level entry into ``backup/<id>/`` and moves the new one
   into place. All moves are same-filesystem renames; nothing is copied or deleted;
6. journals ``PENDING_HEALTH_CHECK`` and launches the new app with a one-time startup token.

Replacing several entries is NOT atomic. Each rename is journaled; on any error the moves are undone
by inspecting the real filesystem (idempotent), and the previous version stays complete in the backup.
Crash recovery, health acknowledgment and manual rollback are the next milestone.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .inventory import INVENTORY_NAME, EXCLUDED_TOP_LEVEL, Inventory, InventoryError, file_sha256, load_inventory
from .journal import (TERMINAL, TRANSACTION_ID, Journal, JournalError, installation_id, read_journal, write_journal)
from .models import Channel, Platform
from .staging import REQUIRED_LAYOUT, StagingError, read_last_sequence, staging_root, update_directory, validate_staged
from .verification import Manifest, TrustStore, VerificationError, verify_release

ASSISTED_INSTALL_ENABLED = False
BACKUP_DIR = "backup"
HELPER_DIR = "helper"
LOCK_NAME = "lock"
PREFIX = "pixelRef/"
SOURCE_MARKERS = (".git", "launch_pixelRef.py")


class InstallError(Exception):
    """The update was not installed (or was undone). The message says why."""


class RollbackError(InstallError):
    pass


# ---- Locks, processes ---------------------------------------------------------------------------

def process_alive(pid: int) -> bool:
    if os.name == "nt":  # pragma: no cover  (Windows adapter; exercised on Windows only)
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_exit(pids: Iterable[int], timeout: float, alive: Callable[[int], bool] = process_alive,
                  sleep: Callable[[float], None] = time.sleep) -> bool:
    deadline = time.monotonic() + timeout
    pending = [p for p in pids if p != os.getpid()]
    while pending:
        pending = [p for p in pending if alive(p)]
        if not pending:
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(0.1)
    return True


class InstallLock:
    """One lock file per installation, created exclusively. A lock whose owner has died is replaced."""

    def __init__(self, update_dir: Path, alive: Callable[[int], bool] = process_alive) -> None:
        self.path = Path(update_dir) / LOCK_NAME
        self.alive = alive

    def __enter__(self) -> "InstallLock":
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    owner = int(json.loads(self.path.read_text(encoding="utf-8"))["pid"])
                except (OSError, ValueError, KeyError, TypeError):
                    owner = -1
                if owner > 0 and self.alive(owner):
                    raise InstallError("another update is already in progress") from None
                self.path.unlink(missing_ok=True)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid()}, handle)
            return self
        raise InstallError("could not take the update lock")

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)


# ---- Locating the installation -------------------------------------------------------------------

def derive_install_root(helper_path: Path) -> Path:
    """The install root of a helper at ``<root>/.pixelref-update/helper/<transaction>/…``. The root is
    never taken from an argument; a helper anywhere else has no root."""
    parts = Path(helper_path).resolve().parts
    for i in range(len(parts) - 3, 0, -1):
        if parts[i] == ".pixelref-update" and parts[i + 1] == HELPER_DIR and TRANSACTION_ID.match(parts[i + 2]):
            return Path(*parts[:i])
    raise InstallError("the helper is not inside an installation's update folder")


def prepare_helper(root: Path, transaction: str, helper_bundle: Path) -> Path:
    """Copy the helper into ``.pixelref-update/helper/<transaction>/`` (outside the files it replaces)."""
    import shutil

    if not TRANSACTION_ID.match(transaction):
        raise InstallError("invalid transaction id")
    target = update_directory(root) / HELPER_DIR / transaction
    shutil.copytree(helper_bundle, target)
    return target


# ---- Planning (preflight) ------------------------------------------------------------------------

@dataclass(frozen=True)
class Operation:
    name: str
    action: str   # replace | add | remove


@dataclass(frozen=True)
class Plan:
    root: Path
    transaction: str
    directory: Path          # the staged transaction folder
    application: Path        # its extracted/pixelRef
    manifest: Manifest
    old_version: str
    operations: tuple[Operation, ...]


def _top(path: str) -> str:
    return path.split("/", 1)[0]


def _live_entries(root: Path, name: str) -> list[str]:
    """Relative posix paths of every file under ``root/name`` (or the file itself); links are refused."""
    base = root / name
    if base.is_symlink():
        raise InstallError(f"{name} is a link")
    if base.is_file():
        return [name]
    found: list[str] = []
    for current, dirs, files in os.walk(base):
        for entry in list(dirs) + files:
            if (Path(current) / entry).is_symlink():
                raise InstallError(f"{(Path(current) / entry).relative_to(root).as_posix()} is a link")
        found += [(Path(current) / f).relative_to(root).as_posix() for f in files]
    return found


def plan_activation(root: Path, transaction: str, *, trust: TrustStore, platform: Platform,
                    now=None) -> Plan:
    """Preflight. Everything is re-derived from disk; raises InstallError (or VerificationError) and
    changes nothing."""
    root = Path(root)
    if not TRANSACTION_ID.match(transaction):
        raise InstallError("invalid transaction id")
    if any((root / marker).exists() for marker in SOURCE_MARKERS):
        raise InstallError("this is a source checkout: it is never updated automatically")
    update = update_directory(root)
    if update.is_symlink() or not update.is_dir():
        raise InstallError("no update folder")
    directory = staging_root(root) / transaction
    try:
        installed = load_inventory(root)
        manifest_bytes = (directory / "signed-metadata" / "manifest.json").read_bytes()
        manifest = verify_release(manifest_bytes, trust=trust, installed=installed.version, platform=platform,
                                  channel=Channel.BETA, last_sequence=read_last_sequence(root), now=now)
        application = validate_staged(directory, manifest, platform)
    except (InventoryError, VerificationError, StagingError, OSError) as exc:
        raise InstallError(f"update cannot be installed: {exc}") from exc
    if not manifest.files:
        raise InstallError("the signed manifest has no file inventory; installation needs one")
    new_files = {f.path[len(PREFIX):]: f for f in manifest.files if f.path.startswith(PREFIX)}
    if INVENTORY_NAME not in new_files or any(_top(p).casefold() in {n.casefold() for n in EXCLUDED_TOP_LEVEL - {INVENTORY_NAME}}
                                              for p in new_files):
        raise InstallError("the release lacks its inventory or contains a protected name")
    problems: list[str] = []
    # Every file the installed release owns must still be exactly what it shipped.
    for path, (size, digest) in installed.files.items():
        live = root / path
        if live.is_symlink() or not live.is_file():
            problems.append(f"missing installed file {path}")
        elif live.stat().st_size != size or file_sha256(live) != digest:
            problems.append(f"modified installed file {path}")
    new_tops = {_top(p) for p in new_files}
    old_tops = installed.top_levels()
    owned = set(installed.files) | {INVENTORY_NAME}
    operations: list[Operation] = []
    for name in sorted(new_tops | old_tops):
        exists = (root / name).exists() or (root / name).is_symlink()
        if exists:
            try:
                unknown = [p for p in _live_entries(root, name) if p not in owned]
            except InstallError as exc:
                problems.append(str(exc))
                continue
            problems += [f"unknown file {p}" for p in unknown]
        if name in new_tops and name in old_tops:
            operations.append(Operation(name, "replace"))
        elif name in new_tops:
            if exists:
                problems.append(f"the release would overwrite unknown {name}")
            operations.append(Operation(name, "add"))
        elif exists:
            operations.append(Operation(name, "remove"))
    if problems:
        raise InstallError("update blocked, resolve these first: " + "; ".join(problems[:10])
                           + (f" (+{len(problems) - 10} more)" if len(problems) > 10 else ""))
    # Same filesystem (renames only), and the install folder really accepts changes.
    devices = {root.stat().st_dev, update.stat().st_dev, directory.stat().st_dev}
    if len(devices) != 1:
        raise InstallError("the update folder is on a different filesystem than the installation")
    probe = root / f".pixelref-probe-{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"x")
        probe.unlink()
    except OSError as exc:
        raise InstallError(f"the installation folder is not writable: {exc}") from exc
    return Plan(root, transaction, directory, application, manifest, str(installed.version), tuple(operations))


# ---- Activation and rollback ---------------------------------------------------------------------

def _exists(path: Path) -> bool:
    return os.path.lexists(path)


def _paths(plan_root: Path, transaction: str, name: str) -> tuple[Path, Path, Path]:
    update = update_directory(plan_root)
    return (plan_root / name, update / BACKUP_DIR / transaction / name,
            staging_root(plan_root) / transaction / "extracted" / "pixelRef" / name)


def rollback(root: Path, journal: Journal, fault: Callable[[str], None] = lambda point: None) -> None:
    """Undo the moves of ``journal`` by looking at the real filesystem, newest first. Only renames:
    nothing is deleted, so every file survives. Safe to run again after an interruption."""
    update = update_directory(root)
    if journal.state != "ROLLING_BACK":
        journal.transition("ROLLING_BACK")
        write_journal(update, journal)
    try:
        for op in reversed(journal.operations):
            live, backup, staged = _paths(root, journal.transaction_id, op["name"])
            fault(f"rollback:{op['name']}")
            if _exists(backup):
                if _exists(live):
                    if op["action"] == "remove" or _exists(staged):
                        raise RollbackError(f"both versions of {op['name']} exist")
                    os.rename(live, staged)
                os.rename(backup, live)
            elif op["action"] == "add" and _exists(live) and not _exists(staged):
                os.rename(live, staged)
            op["status"] = "restored"
            write_journal(update, journal)
    except BaseException as exc:
        if isinstance(exc, Exception):
            journal.transition("ROLLBACK_FAILED", str(exc))
            write_journal(update, journal)
        raise
    journal.transition("ROLLED_BACK")
    write_journal(update, journal)


def activate(plan: Plan, *, fault: Callable[[str], None] = lambda point: None, enabled: bool | None = None) -> Journal:
    """Move the new version into place (steps 5-6 above). On an ordinary error the moves are undone and
    InstallError is raised. ``fault`` is called before every filesystem step (failure injection)."""
    if not (ASSISTED_INSTALL_ENABLED if enabled is None else enabled):
        raise InstallError("assisted installation is disabled in this build")
    root, update = plan.root, update_directory(plan.root)
    existing = read_journal(update)
    if existing is not None and existing.state not in TERMINAL:
        raise InstallError(f"an earlier update is unfinished ({existing.state})")
    journal = Journal(plan.transaction, installation_id(update), plan.old_version, str(plan.manifest.version),
                      plan.manifest.digest, operations=[{"name": o.name, "action": o.action, "status": "pending"}
                                                         for o in plan.operations])
    write_journal(update, journal)
    journal.transition("ACTIVATING")
    write_journal(update, journal)
    try:
        fault("journal:activating")
        (update / BACKUP_DIR / plan.transaction).mkdir(parents=True)
        for op in journal.operations:
            live, backup, staged = _paths(root, plan.transaction, op["name"])
            if op["action"] in ("replace", "remove"):
                fault(f"{op['name']}:before_move_out")
                os.rename(live, backup)
                op["status"] = "moved_out"
                write_journal(update, journal)
            if op["action"] in ("replace", "add"):
                fault(f"{op['name']}:before_place")
                os.rename(staged, live)
            op["status"] = "in_place" if op["action"] != "remove" else "moved_out"
            write_journal(update, journal)
            fault(f"{op['name']}:after_place")
        journal.startup_token = uuid.uuid4().hex
        journal.transition("PENDING_HEALTH_CHECK")
        write_journal(update, journal)
        fault("journal:pending_health_check")
    except Exception as exc:  # an ordinary failure: undo. (BaseException = power loss: leave it for recovery.)
        try:
            rollback(root, journal)
        except Exception as rollback_exc:  # noqa: BLE001
            raise RollbackError(f"update failed ({exc}) and could not be undone ({rollback_exc}); "
                                f"recovery files are kept in {update}") from exc
        raise InstallError(f"update failed and was undone: {exc}") from exc
    return journal


def launch_application(root: Path, platform: Platform, token: str, popen=subprocess.Popen) -> None:
    """Start the new app, detached from the helper, with the one-time startup token."""
    exe = Path(root) / REQUIRED_LAYOUT[platform.system][0]
    options: dict = {"cwd": str(root), "close_fds": True, "env": {**os.environ, "PIXELREF_STARTUP_TOKEN": token}}
    if os.name == "nt":
        options["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    popen([str(exe)], **options)


def run_activation(root: Path, transaction: str, *, trust: TrustStore, platform: Platform, pids: Iterable[int] = (),
                   wait_timeout: float = 60.0, enabled: bool | None = None, fault: Callable[[str], None] = lambda p: None,
                   alive: Callable[[int], bool] = process_alive, launcher: Callable[..., None] = launch_application,
                   now=None) -> Journal:
    """The helper's whole job. Returns the journal in PENDING_HEALTH_CHECK."""
    if not (ASSISTED_INSTALL_ENABLED if enabled is None else enabled):
        raise InstallError("assisted installation is disabled in this build")
    root = Path(root)
    update = update_directory(root)
    if update.is_symlink() or not update.is_dir():
        raise InstallError("no update folder")
    with InstallLock(update, alive):
        if not wait_for_exit(pids, wait_timeout, alive):
            raise InstallError("pixelRef did not close; nothing was changed")
        plan = plan_activation(root, transaction, trust=trust, platform=platform, now=now)
        try:
            journal = activate(plan, fault=fault, enabled=True)
        except JournalError as exc:
            raise InstallError(str(exc)) from exc
        launcher(root, platform, journal.startup_token)
        return journal
