"""Health acknowledgment, startup checks, crash recovery and manual rollback (M5).

The new app proves it started by calling :func:`acknowledge_health` with the one-time token the helper
passed it, after its essential initialization. Until then the journal says ``PENDING_HEALTH_CHECK`` and
the helper (or ``recover``) may restore the previous version. Nothing here deletes a user file: recovery
only renames entries between the install, the backup and the staging folder.

Loops are bounded: a pending update gets ``MAX_STARTUP_ATTEMPTS`` launches, a failing rollback is retried
``MAX_ROLLBACK_ATTEMPTS`` times, and then the journal becomes ``RECOVERY_REQUIRED``: it needs a person and
everything is kept as it is. A healthy committed update is never rolled back automatically.
"""

from __future__ import annotations

import hmac
import os
import shutil
import uuid
from pathlib import Path
from typing import Callable, Iterable

from .install import (BACKUP_DIR, HELPER_DIR, InstallError, InstallLock, Plan, _check_filesystem, _operations,
                      activate, launch_application, process_alive, rollback, wait_for_exit)
from .inventory import INVENTORY_NAME, InventoryError, file_sha256, load_inventory
from .journal import (TERMINAL, TRANSACTION_ID, Journal, JournalError, journal_guard, read_journal, write_journal)
from .models import Platform
from .staging import read_last_sequence, record_sequence, remove_transaction, staging_root, update_directory

MAX_STARTUP_ATTEMPTS = 2
MAX_ROLLBACK_ATTEMPTS = 3

# What the app should do at startup (``begin_startup``).
NORMAL = "normal"          # no update in flight
PENDING = "pending"        # a new version is awaiting its acknowledgment: carry on and acknowledge
RECOVER = "recover"        # restore the previous version (run the helper's ``recover``) instead of starting
MANUAL = "manual"          # automatic recovery cannot be proven safe: tell the user, change nothing


def begin_startup(root: Path) -> str:
    """Call very early in app start. Counts launches of a pending update so a crashing new version cannot
    be relaunched forever."""
    update = update_directory(root)
    if not update.is_dir():
        return NORMAL
    try:
        with journal_guard(update):
            journal = read_journal(update)
            if journal is None or journal.state in ("COMMITTED", "ROLLED_BACK", "CANCELLED", "FAILED", "IDLE"):
                return NORMAL
            if journal.state == "RECOVERY_REQUIRED":
                return MANUAL
            if journal.state != "PENDING_HEALTH_CHECK":
                return RECOVER  # interrupted activation or rollback
            journal.startup_attempts += 1
            write_journal(update, journal)
            return RECOVER if journal.startup_attempts > MAX_STARTUP_ATTEMPTS else PENDING
    except JournalError:
        return MANUAL


def acknowledge_health(root: Path, token: str) -> bool:
    """Called by the new app once it is really up. Commits the update (journal COMMITTED, the accepted
    metadata sequence is recorded, old backups and staging are pruned). False if the token is wrong or the
    update is no longer pending (for example it was already rolled back)."""
    update = update_directory(root)
    try:
        with journal_guard(update):
            journal = read_journal(update)
            if (journal is None or journal.kind != "install" or journal.state != "PENDING_HEALTH_CHECK"
                    or not token or not hmac.compare_digest(token, journal.startup_token)):
                return False
            journal.transition("COMMITTED")
            write_journal(update, journal)
    except JournalError:
        return False
    record_sequence(root, journal.sequence)
    prune(root, journal)
    return True


def prune(root: Path, journal: Journal) -> None:
    """After a commit keep only this transaction's backup (the previous version, for a manual rollback)
    and remove other transactions' backups, helper copies and staging. Only transaction-id-named plain
    folders are removed; failures are ignored (a running helper's folder can't be deleted on Windows)."""
    update = update_directory(root)
    for parent in (update / BACKUP_DIR, update / HELPER_DIR, staging_root(root)):
        if not parent.is_dir() or parent.is_symlink():
            continue
        for entry in parent.iterdir():
            keep = entry.name == journal.transaction_id and parent.name == BACKUP_DIR
            if TRANSACTION_ID.match(entry.name) and not keep and entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)


def recover(root: Path, *, alive: Callable[[int], bool] = process_alive, fault: Callable[[str], None] = lambda p: None,
            pids: Iterable[int] = (), wait_timeout: float = 30.0) -> Journal | None:
    """Restore the previous version after an interrupted or failed update (idempotent). Does nothing for
    a committed or finished transaction. Raises InstallError when it cannot, keeping all files."""
    root = Path(root)
    update = update_directory(root)
    if update.is_symlink() or not update.is_dir():
        return None
    with InstallLock(update, alive):
        if not wait_for_exit(pids, wait_timeout, alive):
            raise InstallError("pixelRef is still running; close it first")
        with journal_guard(update):
            try:
                journal = read_journal(update)
            except JournalError as exc:
                raise InstallError(f"the update journal is damaged; recovery needs a person: {exc}") from exc
            if journal is None or journal.state in TERMINAL - {"ROLLBACK_FAILED", "RECOVERY_REQUIRED"}:
                return journal
            if journal.state == "RECOVERY_REQUIRED":
                raise InstallError(f"recovery is required but cannot be done safely automatically; "
                                   f"the previous version is in {update / BACKUP_DIR / journal.transaction_id}")
            failures = sum(1 for h in journal.history if h["state"] == "ROLLBACK_FAILED")
            if journal.state == "ROLLBACK_FAILED" and failures >= MAX_ROLLBACK_ATTEMPTS:
                journal.transition("RECOVERY_REQUIRED", f"rollback failed {failures} times")
                write_journal(update, journal)
                raise InstallError(f"rollback failed {failures} times; manual recovery is required. "
                                   f"Files are kept in {update}")
            if journal.state != "ROLLING_BACK":
                journal.transition("ROLLING_BACK", journal.failure_reason or "recovering an interrupted update")
                write_journal(update, journal)
        rollback(root, journal, fault)
        return read_journal(update)


def plan_manual_rollback(root: Path) -> Plan:
    """Preflight for restoring the version saved by the last committed update. Everything is checked from
    disk: the backup must contain every file of the previous release with its recorded hash, and the
    current release must be unmodified."""
    root = Path(root)
    update = update_directory(root)
    try:
        journal = read_journal(update)
    except JournalError as exc:
        raise InstallError(f"the update journal is damaged: {exc}") from exc
    if journal is not None and journal.state == "COMMITTED":
        backup_id = journal.transaction_id          # the update (or rollback) that is live now
    elif journal is not None and journal.kind == "rollback" and journal.state == "ROLLED_BACK":
        backup_id = journal.source_transaction      # a rollback that failed and was undone: it can be retried
    else:
        raise InstallError("there is no committed update to roll back")
    backup = update / BACKUP_DIR / backup_id
    if backup.is_symlink() or not backup.is_dir():
        raise InstallError("the previous version's backup is missing")
    try:
        previous = load_inventory(backup)
        current = load_inventory(root)
    except InventoryError as exc:
        raise InstallError(f"cannot verify the backup: {exc}") from exc
    for path, (size, digest) in previous.files.items():
        saved = backup / path
        if saved.is_symlink() or not saved.is_file() or saved.stat().st_size != size or file_sha256(saved) != digest:
            raise InstallError(f"the backup is damaged ({path}); it cannot be used")
    operations = _operations(root, current, {p.split("/", 1)[0] for p in previous.files} | {INVENTORY_NAME})
    _check_filesystem(root, update, backup)
    return Plan(root, uuid.uuid4().hex, "rollback", backup, backup_id, str(current.version),
                str(previous.version), file_sha256(backup / INVENTORY_NAME), 0, tuple(operations))


def rollback_previous(root: Path, *, platform: Platform, pids: Iterable[int] = (), wait_timeout: float = 60.0,
                      enabled: bool | None = None, fault: Callable[[str], None] = lambda p: None,
                      alive: Callable[[int], bool] = process_alive,
                      launcher: Callable[..., object] = launch_application) -> Journal:
    """The user's explicit rollback to the previous version, through the same journaled activation. It
    restores only the release's own files; boards, settings, plugins and scripts are untouched (and may
    have been changed by the newer version: see the warning in the UI/docs). The version it replaces is
    kept in the new backup, so rolling forward is the same operation. This is a user decision, so the
    downgrade rule that applies to network metadata does not apply."""
    from . import install as _install

    if not (_install.ASSISTED_INSTALL_ENABLED if enabled is None else enabled):
        raise InstallError("assisted installation is disabled in this build")
    root = Path(root)
    update = update_directory(root)
    if update.is_symlink() or not update.is_dir():
        raise InstallError("no update folder")
    with InstallLock(update, alive):
        if not wait_for_exit(pids, wait_timeout, alive):
            raise InstallError("pixelRef did not close; nothing was changed")
        plan = plan_manual_rollback(root)
        journal = activate(plan, fault=fault, enabled=True)
        with journal_guard(update):  # no health protocol: the user chose this version and can re-update
            journal = read_journal(update) or journal
            journal.transition("COMMITTED")
            write_journal(update, journal)
        prune(root, journal)
        launcher(root, platform, "")
        return journal
