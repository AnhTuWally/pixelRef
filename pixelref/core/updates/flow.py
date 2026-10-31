"""What the app does around an update, without Qt: decide if installing is offered, prepare (download,
verify, stage, preflight) an update, hand over to the helper, and the startup hooks.

Everything is gated by ``install.ASSISTED_INSTALL_ENABLED`` (off in this build), by running as a standalone
copy with the helper bundled, and by a release key being embedded. If any of that is missing the app only
*notifies* and links to the release page, exactly as in M1. An updater that fails or is absent never stops
pixelRef from starting: ``startup_gate`` swallows its own errors.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

from . import install, recovery
from .journal import JournalError, TERMINAL, read_journal
from .models import Channel, DiscoveryResult, InstallMode, Platform, application_root, current_platform, detect_install_mode
from .staging import (Cancelled, Opener, StagedUpdate, StagingError, check_download_url, clean_staging, default_opener,
                      read_last_sequence, stage_update, staging_root, update_directory)
from .verification import MAX_MANIFEST_BYTES, TrustStore, VerificationError, verify_release
from .versions import Version

HELPER_EXE = {"windows": "pixelref-updater.exe"}
HELPER_BUNDLE_NAME = "updater"
STARTUP_TOKEN_ENV = "PIXELREF_STARTUP_TOKEN"

# Filled in by ``startup_gate`` before the UI exists; the updates plugin reads it.
STARTUP: dict[str, Any] = {"state": recovery.NORMAL, "handed_over": False}


def helper_bundle() -> Path | None:
    """The helper's files shipped inside the frozen app (None when running from source)."""
    base = getattr(sys, "_MEIPASS", None)
    bundle = Path(base) / HELPER_BUNDLE_NAME if base and getattr(sys, "frozen", False) else None
    return bundle if bundle is not None and bundle.is_dir() else None


def assist_status(mode: InstallMode, root: Path | None, trust: TrustStore, platform: Platform,
                  enabled: bool | None = None, bundle: Path | None = None) -> tuple[bool, str]:
    """(available, reason it isn't). Every condition must hold for 'Download and install' to be offered."""
    if not (install.ASSISTED_INSTALL_ENABLED if enabled is None else enabled):
        return False, "Installing updates from inside pixelRef is not enabled in this build."
    if mode is not InstallMode.STANDALONE or root is None:
        return False, "This copy runs from source: update it with git."
    if platform.system not in HELPER_EXE:
        return False, f"Installing updates is not supported on {platform} yet."
    if not trust.keys:
        return False, "This build has no release key, so it cannot verify updates."
    folder = bundle or helper_bundle()
    if folder is None or not (folder / HELPER_EXE[platform.system]).is_file():
        return False, "The update helper is not part of this build."
    try:
        update_directory(root).mkdir(exist_ok=True)
        probe = update_directory(root) / f".probe-{uuid.uuid4().hex}"
        probe.write_bytes(b"x")
        probe.unlink()
    except OSError:
        return False, "pixelRef's folder is read-only: download the new version and extract it yourself."
    return True, ""


def _fetch_manifest(url: str, opener: Opener, cancelled: Callable[[], bool]) -> bytes:
    check_download_url(url)
    try:
        stream, final = opener(url)
        try:
            check_download_url(final)
            data = b""
            while chunk := stream.read(1 << 16):
                if cancelled():
                    raise Cancelled("cancelled")
                data += chunk
                if len(data) > MAX_MANIFEST_BYTES:
                    raise StagingError("the manifest is too large")
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
    except OSError as exc:
        raise StagingError(f"could not download the manifest: {exc}") from exc
    return data


def prepare_update(result: DiscoveryResult, *, root: Path, platform: Platform, installed: Version, channel: Channel,
                   trust: TrustStore, opener: Opener = default_opener,
                   progress: Callable[[int, int], None] | None = None, cancelled: Callable[[], bool] = lambda: False,
                   now=None, free_space=None) -> StagedUpdate:
    """Download the signed manifest and archive, verify, stage and preflight. Any failure leaves the
    installation untouched and removes what it staged. Raises StagingError, VerificationError or
    install.InstallError (a conflict the user must resolve, with the list in the message)."""
    if not result.can_install or result.asset is None or result.manifest_asset is None:
        raise StagingError("this release does not publish an installable package for this computer")
    root = Path(root)
    update = update_directory(root)
    update.mkdir(exist_ok=True)
    try:
        journal = read_journal(update)
    except JournalError as exc:
        raise StagingError(f"the update journal is damaged: {exc}") from exc
    if journal is not None and journal.state not in TERMINAL:
        raise StagingError(f"an earlier update is unfinished ({journal.state})")
    clean_staging(staging_root(root))
    raw = _fetch_manifest(result.manifest_asset.url, opener, cancelled)
    manifest = verify_release(raw, trust=trust, installed=installed, platform=platform, channel=channel,
                              last_sequence=read_last_sequence(root), now=now)
    if manifest.artifact_name != result.asset.name:
        raise VerificationError("the signed manifest is for a different file than the one offered")
    options = {"free_space": free_space} if free_space else {}
    staged = stage_update(manifest, raw, result.asset.url, staging_root(root), platform=platform, opener=opener,
                          progress=progress, cancelled=cancelled, **options)
    try:
        install.plan_activation(root, staged.transaction_id, trust=trust, platform=platform, now=now)
    except BaseException:
        from .staging import remove_transaction

        remove_transaction(staging_root(root), staged.transaction_id)
        raise
    return staged


def launch_helper(root: Path, command: str, pid: int, *, transaction: str | None = None, platform: Platform | None = None,
                  bundle: Path | None = None, popen: Callable[..., Any] = subprocess.Popen) -> Any:
    """Copy the helper bundle outside the replaced files and start it detached. The command line is fixed:
    ``<helper> <command> [<transaction>] --wait-pid <pid>``; the helper derives everything else itself."""
    platform = platform or current_platform()
    if command not in ("activate", "recover", "rollback") or (command == "activate") != bool(
            transaction and install.TRANSACTION_ID.match(transaction)):
        raise install.InstallError("bad helper request")
    source = bundle or helper_bundle()
    if source is None:
        raise install.InstallError("the update helper is not part of this build")
    folder = update_directory(root) / install.HELPER_DIR / (transaction or uuid.uuid4().hex)
    if not folder.exists():
        install.prepare_helper(root, folder.name, source)
    exe = folder / HELPER_EXE[platform.system]
    if not exe.is_file():
        raise install.InstallError("the update helper is incomplete")
    return _spawn(folder, command, pid, transaction, platform, popen)


def _spawn(folder: Path, command: str, pid: int, transaction: str | None, platform: Platform,
           popen: Callable[..., Any]) -> Any:
    args = [str(folder / HELPER_EXE[platform.system]), command] + ([transaction] if transaction else []) + [
        "--wait-pid", str(pid)]
    options: dict[str, Any] = {"cwd": str(folder), "close_fds": True, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    return popen(args, **options)


# ---- Startup hooks ---------------------------------------------------------------------------------

def startup_gate(root: Path | None = None, *, pid: int | None = None, platform: Platform | None = None,
                 popen: Callable[..., Any] = subprocess.Popen, bundle: Path | None = None) -> bool:
    """Call first thing at app start, before any UI. Returns True if it handed over to the recovery helper
    and the app should exit now. Never raises: an updater problem must not stop pixelRef from starting."""
    STARTUP.update(state=recovery.NORMAL, handed_over=False)
    try:
        root = root if root is not None else application_root()
        if root is None or not update_directory(root).is_dir():
            return False
        state = recovery.begin_startup(root)
        STARTUP["state"] = state
        if state != recovery.RECOVER:
            return False
        platform = platform or current_platform()
        journal = read_journal(update_directory(root))
        folder = update_directory(root) / install.HELPER_DIR / journal.transaction_id
        if (folder / HELPER_EXE.get(platform.system, "?")).is_file():
            _spawn(folder, "recover", pid or os.getpid(), None, platform, popen)  # the helper that ran the update
        elif (bundle or helper_bundle()) is not None:
            launch_helper(root, "recover", pid or os.getpid(), platform=platform, bundle=bundle, popen=popen)
        else:
            return False  # no helper: start normally and show the "recover manually" notice
        STARTUP["handed_over"] = True
        return True
    except Exception:  # noqa: BLE001
        return False


def last_update_result(root: Path) -> dict[str, str] | None:
    """The outcome of the most recent finished transaction, for a one-time message in the UI."""
    try:
        journal = read_journal(update_directory(root))
    except JournalError:
        return {"transaction": "", "state": "DAMAGED", "text": "The update journal is damaged. See the update documentation."}
    if journal is None or journal.state not in ("COMMITTED", "ROLLED_BACK", "RECOVERY_REQUIRED", "ROLLBACK_FAILED"):
        return None
    texts = {
        "COMMITTED": f"pixelRef {journal.new_version} is installed.",
        "ROLLED_BACK": f"The update to {journal.new_version} did not work"
                       + (f" ({journal.failure_reason})" if journal.failure_reason else "")
                       + f". pixelRef {journal.old_version} was restored.",
        "ROLLBACK_FAILED": "Restoring the previous version failed. Your files are kept; see the update documentation.",
        "RECOVERY_REQUIRED": "The previous version could not be restored automatically. Your files are kept; "
                             "see the update documentation to recover by hand.",
    }
    return {"transaction": journal.transaction_id, "state": journal.state, "text": texts[journal.state]}
