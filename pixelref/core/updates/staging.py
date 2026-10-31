"""Verified download and safe staging (M3). Prepares a complete new version in an updater-owned folder.

Nothing here touches the active installation: every write goes under ``<root>/.pixelref-update/``, in a
fresh transaction folder that is deleted whole if anything fails or is cancelled. Cleanup only ever
removes folders this module made (transaction-id names), never unknown paths.

    staging/<transaction-id>/
        signed-metadata/manifest.json     the exact signed bytes that authorized this transaction
        package.zip                       the verified archive
        extracted/pixelRef/...            the inspected, extracted application files
        STAGED                            written last: JSON {"transaction", "manifest_digest"}
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import urllib.error
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable
from urllib.parse import urlparse

from .models import Platform
from .verification import Manifest, safe_relative_path, verify_artifact

UPDATE_DIR_NAME = ".pixelref-update"
TRANSACTION_ID = re.compile(r"^[0-9a-f]{32}$")
ALLOWED_DOWNLOAD_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
MAX_REDIRECTS = 5
CHUNK = 1 << 16
MAX_UNCOMPRESSED_BYTES = 2 * 1024 ** 3
MAX_ENTRIES = 20000
SPACE_MARGIN = 64 * 1024 ** 2
TOP_LEVEL = "pixelRef"
# Names that belong to the user or the updater: a release may never contain (or overwrite) them.
PROTECTED_NAMES = frozenset({"portable.toml", "user-data", UPDATE_DIR_NAME})
REQUIRED_LAYOUT = {"windows": ("pixelRef.exe", "pixelref-cli.exe", "_internal")}


class StagingError(Exception):
    """The update could not be staged safely. The live installation was not changed."""


class Cancelled(StagingError):
    """The user cancelled."""


Progress = Callable[[int, int], None]
Opener = Callable[[str], tuple[BinaryIO, str]]   # url -> (stream, final url after redirects)


@dataclass(frozen=True)
class StagedUpdate:
    transaction_id: str
    directory: Path
    manifest: Manifest
    application: Path   # extracted/pixelRef


# ---- Locations and cleanup ---------------------------------------------------------------------

def update_directory(install_root: Path) -> Path:
    return Path(install_root) / UPDATE_DIR_NAME


def staging_root(install_root: Path) -> Path:
    return update_directory(install_root) / "staging"


def _is_plain_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def remove_transaction(root: Path, transaction_id: str) -> None:
    """Delete one transaction folder. Refuses anything that isn't a plain folder with a valid id directly
    inside ``root``, so a forged id can never reach another path."""
    if not TRANSACTION_ID.match(transaction_id):
        raise StagingError("invalid transaction id")
    target = Path(root) / transaction_id
    if target.is_symlink():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target, ignore_errors=True)


def clean_staging(root: Path, keep: tuple[str, ...] = ()) -> list[str]:
    """Remove old transaction folders (not the ones in ``keep``). Unknown names and files are left alone."""
    removed = []
    if not _is_plain_dir(Path(root)):
        return removed
    for entry in Path(root).iterdir():
        if TRANSACTION_ID.match(entry.name) and entry.name not in keep and _is_plain_dir(entry):
            remove_transaction(root, entry.name)
            removed.append(entry.name)
    return removed


# ---- Accepted-metadata counter (anti-replay; local and best-effort, see docs/update-security.md) -----

def read_last_sequence(install_root: Path) -> int:
    try:
        value = json.loads((update_directory(install_root) / "accepted.json").read_text(encoding="utf-8"))["sequence"]
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def record_sequence(install_root: Path, sequence: int) -> None:
    directory = update_directory(install_root)
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / f"accepted.{uuid.uuid4().hex}.tmp"
    temp.write_text(json.dumps({"sequence": max(sequence, read_last_sequence(install_root))}), encoding="utf-8")
    os.replace(temp, directory / "accepted.json")


# ---- Download ------------------------------------------------------------------------------------

def check_download_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS or parsed.username or parsed.password:
        raise StagingError(f"download address is not allowed: {parsed.scheme}://{parsed.hostname}")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Every redirect hop must be an allowlisted https address (TLS certificates are verified by urllib)."""

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        try:
            check_download_url(newurl)
        except StagingError as exc:
            raise urllib.error.URLError(f"redirect refused: {exc}") from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_opener(url: str, timeout: float = 30.0) -> tuple[BinaryIO, str]:
    check_download_url(url)
    opener = urllib.request.build_opener(_CheckedRedirects)
    response = opener.open(urllib.request.Request(url, headers={"User-Agent": "pixelRef-update"}), timeout=timeout)
    return response, response.geturl()


def download_archive(url: str, destination: Path, manifest: Manifest, *, opener: Opener = default_opener,
                     progress: Progress | None = None, cancelled: Callable[[], bool] = lambda: False) -> None:
    """Fetch ``url`` into ``destination`` (a new file). Reads at most the signed size and then requires the
    signed SHA-256. On any failure the partial file is removed."""
    check_download_url(url)
    part = destination.with_name(destination.name + ".part")
    try:
        stream, final = opener(url)
        try:
            check_download_url(final)
            done = 0
            with part.open("xb") as out:
                while True:
                    if cancelled():
                        raise Cancelled("cancelled")
                    chunk = stream.read(CHUNK)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > manifest.artifact_size:
                        raise StagingError("download is larger than the signed size")
                    out.write(chunk)
                    if progress:
                        progress(done, manifest.artifact_size)
                out.flush()
                os.fsync(out.fileno())
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
        verify_artifact(part, manifest)  # exact size and SHA-256
        os.replace(part, destination)
    except (OSError, urllib.error.URLError) as exc:
        part.unlink(missing_ok=True)
        raise StagingError(f"download failed: {exc}") from exc
    except BaseException:
        part.unlink(missing_ok=True)
        raise


# ---- Archive inspection and extraction -----------------------------------------------------------

@dataclass(frozen=True)
class Member:
    info: zipfile.ZipInfo
    path: str          # normalized forward-slash path, no trailing slash
    is_dir: bool


def inspect_archive(archive: zipfile.ZipFile, manifest: Manifest, *, max_entries: int = MAX_ENTRIES,
                    max_bytes: int = MAX_UNCOMPRESSED_BYTES) -> list[Member]:
    """Check every entry before anything is extracted. Rejects path escapes (absolute, ``..``, backslash,
    drive/UNC), links and other non-regular entries, encrypted entries, duplicate or case-colliding
    names, reserved and user-owned names, files outside ``pixelRef/``, and oversized archives."""
    infos = archive.infolist()
    if not infos or len(infos) > max_entries:
        raise StagingError("archive is empty or has too many entries")
    members: list[Member] = []
    seen: set[str] = set()
    total = 0
    for info in infos:
        name = info.filename
        is_dir = name.endswith("/")
        path = name[:-1] if is_dir else name
        if not safe_relative_path(path):
            raise StagingError(f"unsafe path in archive: {name!r}")
        parts = path.split("/")
        if parts[0] != TOP_LEVEL:
            raise StagingError(f"archive entry outside {TOP_LEVEL}/: {name!r}")
        if len(parts) > 1 and parts[1].casefold() in {n.casefold() for n in PROTECTED_NAMES}:
            raise StagingError(f"archive contains a protected name: {name!r}")
        if path.casefold() in seen:
            raise StagingError(f"duplicate or case-colliding entry: {name!r}")
        seen.add(path.casefold())
        if info.flag_bits & 0x1:
            raise StagingError(f"encrypted entry: {name!r}")
        kind = stat.S_IFMT(info.external_attr >> 16)  # 0 when the archiver recorded no file type
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise StagingError(f"links and special files are not allowed: {name!r}")
        if is_dir and info.file_size:
            raise StagingError(f"directory entry with data: {name!r}")
        total += info.file_size
        if total > max_bytes:
            raise StagingError("archive expands beyond the allowed size")
        members.append(Member(info, path, is_dir))
    # A file and a folder can't share a path: "a" as a file with "a/b" below it.
    files = {m.path.casefold() for m in members if not m.is_dir}
    for m in members:
        parent = m.path.rpartition("/")[0]
        while parent:
            if parent.casefold() in files:
                raise StagingError(f"file and folder share a path: {m.path!r}")
            parent = parent.rpartition("/")[0]
    if manifest.files:  # a signed inventory must match the archive exactly
        expected = {f.path: f for f in manifest.files}
        actual = {m.path: m for m in members if not m.is_dir}
        if set(expected) != set(actual) or any(expected[p].size != actual[p].info.file_size for p in actual):
            raise StagingError("archive does not match the signed file inventory")
    return members


def _extract_file(archive: zipfile.ZipFile, member: Member, target: Path, expected_hash: str | None) -> None:
    limit = member.info.file_size
    digest = hashlib.sha256()
    written = 0
    with archive.open(member.info) as source, target.open("xb") as out:  # "x": never follows or reuses a path
        while chunk := source.read(CHUNK):
            written += len(chunk)
            if written > limit:  # the real data must not exceed the declared size
                raise StagingError(f"entry larger than declared: {member.path!r}")
            digest.update(chunk)
            out.write(chunk)
        out.flush()
        os.fsync(out.fileno())
    if written != limit or (expected_hash is not None and not hmac.compare_digest(digest.hexdigest(), expected_hash)):
        raise StagingError(f"file failed verification: {member.path!r}")
    if os.name == "posix":
        target.chmod(0o755 if (member.info.external_attr >> 16) & 0o111 else 0o644)


def extract_archive(archive: zipfile.ZipFile, members: list[Member], manifest: Manifest, destination: Path) -> None:
    """Extract into ``destination``, a folder this call creates (it must not exist). Every path is
    re-checked to stay inside it; per-file hashes are verified when the manifest has an inventory."""
    destination.mkdir(parents=True)
    root = destination.resolve()
    hashes = {f.path: f.sha256 for f in manifest.files}
    for member in members:
        target = (destination / member.path)
        if not target.resolve().is_relative_to(root):
            raise StagingError(f"path escapes the staging folder: {member.path!r}")
        if member.is_dir:
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        _extract_file(archive, member, target, hashes.get(member.path))


def validate_layout(application: Path, platform: Platform) -> None:
    required = REQUIRED_LAYOUT.get(platform.system)
    if required is None:
        raise StagingError(f"staging is not supported on {platform}")
    for name in required:
        if not (application / name).exists() or (application / name).is_symlink():
            raise StagingError(f"the release is missing {name}")


def _check_space(directory: Path, needed: int, free_space: Callable[[Path], int]) -> None:
    if free_space(directory) < needed + SPACE_MARGIN:
        raise StagingError("not enough free disk space to stage the update")
    probe = directory / f".probe-{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"x")
        probe.unlink()
    except OSError as exc:
        raise StagingError(f"the update folder is not writable: {exc}") from exc


# ---- The whole transaction -----------------------------------------------------------------------

def stage_update(manifest: Manifest, manifest_bytes: bytes, url: str, root: Path, *, platform: Platform,
                 opener: Opener = default_opener, progress: Progress | None = None,
                 cancelled: Callable[[], bool] = lambda: False,
                 free_space: Callable[[Path], int] = lambda p: shutil.disk_usage(p).free) -> StagedUpdate:
    """Download, verify, inspect and extract ``manifest``'s archive into a new transaction under ``root``
    (the staging folder). Any failure or cancellation removes the transaction and raises; the active
    installation is never touched. ``manifest`` must already be authenticated (``verify_release``)."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    needed = manifest.artifact_size + min(MAX_UNCOMPRESSED_BYTES, sum(f.size for f in manifest.files) or manifest.artifact_size * 3)
    _check_space(root, needed, free_space)
    transaction = uuid.uuid4().hex
    directory = root / transaction
    directory.mkdir()
    try:
        (directory / "signed-metadata").mkdir()
        (directory / "signed-metadata" / "manifest.json").write_bytes(manifest_bytes)
        package = directory / "package.zip"
        download_archive(url, package, manifest, opener=opener, progress=progress, cancelled=cancelled)
        if cancelled():
            raise Cancelled("cancelled")
        try:
            with zipfile.ZipFile(package) as archive:
                members = inspect_archive(archive, manifest)
                extract_archive(archive, members, manifest, directory / "extracted")
        except zipfile.BadZipFile as exc:
            raise StagingError(f"archive is not a valid ZIP: {exc}") from exc
        application = directory / "extracted" / TOP_LEVEL
        validate_layout(application, platform)
        (directory / "STAGED").write_text(json.dumps({"transaction": transaction, "manifest_digest": manifest.digest}),
                                          encoding="utf-8")
        return StagedUpdate(transaction, directory, manifest, application)
    except BaseException:
        remove_transaction(root, transaction)
        raise


def validate_staged(directory: Path, manifest: Manifest, platform: Platform) -> Path:
    """Re-check a staged transaction independently of how it was made (the helper does this before
    activating): the marker names this manifest, the archive still has the signed hash, the layout is
    intact and every inventoried file still has its signed hash. Returns the application folder."""
    directory = Path(directory)
    if directory.is_symlink() or not TRANSACTION_ID.match(directory.name):
        raise StagingError("not a staging transaction folder")
    try:
        marker = json.loads((directory / "STAGED").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StagingError("staged update is incomplete") from exc
    if not isinstance(marker, dict) or marker.get("manifest_digest") != manifest.digest or marker.get("transaction") != directory.name:
        raise StagingError("staged update belongs to a different manifest")
    verify_artifact(directory / "package.zip", manifest)
    application = directory / "extracted" / TOP_LEVEL
    validate_layout(application, platform)
    for entry in manifest.files:
        path = directory / "extracted" / entry.path
        if path.is_symlink() or not path.is_file() or path.stat().st_size != entry.size:
            raise StagingError(f"staged file changed: {entry.path}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry.sha256:
            raise StagingError(f"staged file changed: {entry.path}")
    return application
