"""Release authenticity: Ed25519-signed canonical manifests (M2).

A release is installable only if a manifest, signed by a trusted *application release key*, names its
exact archive (size, SHA-256), target platform and version. GitHub's API answers authorize nothing.
This is a deliberately simple scheme, not TUF: it has no consistent snapshots, no threshold or
offline-root key hierarchy and only a local, best-effort anti-rollback counter. See
docs/update-security.md for what is and is not protected.

Everything fails closed: any problem raises :class:`VerificationError`.
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .discovery import ASSET_PATTERN
from .models import Channel, Platform
from .versions import Version, VersionError, try_parse_version

SCHEMA_VERSION = 1
DOMAIN = b"pixelref-update-manifest-v1\n"   # domain separation: a signature for anything else never verifies
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
MAX_INVENTORY_FILES = 20000
UPDATER_VERSION = Version(1, 0, 0)          # what this build can handle (min_updater_version)
PACKAGE_FORMATS = ("zip",)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.I)


class VerificationError(Exception):
    """The manifest, signature or archive is not acceptable. Never install."""


# ---- Canonical bytes ---------------------------------------------------------------------------

def _check_canonical(value: Any, depth: int = 0) -> None:
    if depth > 8:
        raise VerificationError("manifest nests too deeply")
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise VerificationError("manifest keys must be strings")
            _check_canonical(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _check_canonical(v, depth + 1)
    elif isinstance(value, bool) or isinstance(value, str):
        pass
    elif isinstance(value, int):
        if abs(value) > 2**53:
            raise VerificationError("integer out of range")
    else:  # floats and null are ambiguous across implementations
        raise VerificationError(f"unsupported value type {type(value).__name__}")


def canonical_bytes(signed: Mapping[str, Any]) -> bytes:
    """The exact bytes that are signed: domain prefix + JSON with sorted keys, no whitespace, ASCII
    escapes only, UTF-8. Only objects, arrays, strings, booleans and integers are allowed."""
    _check_canonical(dict(signed))
    return DOMAIN + json.dumps(signed, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                               allow_nan=False).encode("ascii")


# ---- Keys --------------------------------------------------------------------------------------

def key_id(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()


@dataclass(frozen=True)
class TrustedKey:
    public_key: bytes            # raw 32-byte Ed25519 public key
    expires: str = ""            # optional "YYYY-MM-DDTHH:MM:SSZ": no signature is accepted after it

    @property
    def id(self) -> str:
        return key_id(self.public_key)


@dataclass(frozen=True)
class TrustStore:
    """Keys that may authorize application releases. Never the plugin-publisher keys. Several keys may
    be listed during a rotation; ``revoked`` ids are refused even if listed."""

    keys: tuple[TrustedKey, ...] = ()
    revoked: frozenset[str] = frozenset()


# The embedded application release key(s): PUBLIC keys only, generated offline with
# ``python packaging/sign_release.py generate-key``. The private key is never in this repository.
# Key id 3787a25031bc4804656e3325bd66d6856ea3506caa5b9fdb775eb76a79f3b31a
RELEASE_TRUST = TrustStore(keys=(
    TrustedKey(base64.b64decode("Y4drb4xfxSNNe/sNYEeHjzsTTt/XuaPNtK6EpgTeprk=")),
))


# ---- Manifest ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    release_id: str
    sequence: int
    version: Version
    channel: Channel
    platform: Platform
    artifact_name: str
    artifact_size: int
    sha256: str
    package_format: str
    published_at: _dt.datetime
    expires_at: _dt.datetime
    min_updater_version: Version
    files: tuple[FileEntry, ...] = ()
    digest: str = ""             # SHA-256 of the signed canonical bytes (identifies this exact manifest)


def _parse_time(value: Any, name: str) -> _dt.datetime:
    if not isinstance(value, str) or not _TIME.match(value):
        raise VerificationError(f"{name} must be a UTC time like 2026-01-31T12:00:00Z")
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except ValueError as exc:
        raise VerificationError(f"{name} is not a valid time") from exc


def _int(signed: Mapping[str, Any], name: str, low: int, high: int) -> int:
    value = signed.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise VerificationError(f"{name} is missing or out of range")
    return value


def _str(signed: Mapping[str, Any], name: str, limit: int = 200) -> str:
    value = signed.get(name)
    if not isinstance(value, str) or not value or len(value) > limit:
        raise VerificationError(f"{name} is missing or invalid")
    return value


def safe_relative_path(path: str) -> bool:
    """A forward-slash relative path with no traversal, drive, UNC, control or reserved-name parts."""
    if not path or len(path) > 400 or "\\" in path or path.startswith("/") or ":" in path:
        return False
    if any(ord(c) < 32 or c in '<>"|?*' for c in path):
        return False
    parts = path.split("/")  # not PurePosixPath: it silently drops '.' and empty parts
    return all(p not in ("", ".", "..") and not p.endswith((".", " ")) and not _WINDOWS_RESERVED.match(p)
               for p in parts)


def parse_manifest(signed: Mapping[str, Any]) -> Manifest:
    """Validate every field of the *signed* payload. Unknown fields are rejected, so nothing the
    signature didn't cover can influence installation."""
    known = {"schema_version", "release_id", "sequence", "version", "channel", "platform", "architecture",
             "artifact_name", "artifact_size", "sha256", "package_format", "published_at", "expires_at",
             "min_updater_version", "files"}
    if not isinstance(signed, Mapping) or set(signed) - known:
        raise VerificationError("manifest has unknown or malformed fields")
    if signed.get("schema_version") != SCHEMA_VERSION:
        raise VerificationError("unsupported manifest schema")
    version = try_parse_version(_str(signed, "version"))
    minimum = try_parse_version(_str(signed, "min_updater_version"))
    if version is None or minimum is None or version.build or minimum.build:
        raise VerificationError("version fields are not valid semantic versions")
    channel_text = _str(signed, "channel")
    if channel_text not in ("stable", "beta"):
        raise VerificationError("unknown channel")
    channel = Channel.parse(channel_text)
    if (channel is Channel.STABLE) == version.is_prerelease:
        raise VerificationError("channel does not match the version (pre-releases are beta)")
    platform = Platform(_str(signed, "platform", 20), _str(signed, "architecture", 20))
    name = _str(signed, "artifact_name")
    match = ASSET_PATTERN.match(name)
    if (match is None or match["system"] != platform.system or match["arch"] != platform.arch
            or (try_parse_version(match["version"]) or Version(0, 0, 0)).same_precedence(version) is False):
        raise VerificationError("artifact_name does not match the version and platform")
    digest = _str(signed, "sha256", 64)
    if not _SHA256.match(digest):
        raise VerificationError("sha256 must be 64 lowercase hex digits")
    package_format = _str(signed, "package_format", 10)
    if package_format not in PACKAGE_FORMATS:
        raise VerificationError("unsupported package format")
    files: list[FileEntry] = []
    raw_files = signed.get("files", [])
    if not isinstance(raw_files, list) or len(raw_files) > MAX_INVENTORY_FILES:
        raise VerificationError("file inventory is malformed or too large")
    seen: set[str] = set()
    for entry in raw_files:
        if not isinstance(entry, dict) or set(entry) != {"path", "size", "sha256"}:
            raise VerificationError("file inventory entry is malformed")
        path, size, file_hash = entry["path"], entry["size"], entry["sha256"]
        if (not isinstance(path, str) or not safe_relative_path(path) or path.casefold() in seen
                or isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > MAX_ARTIFACT_BYTES
                or not isinstance(file_hash, str) or not _SHA256.match(file_hash)):
            raise VerificationError(f"file inventory entry is not acceptable: {path!r}")
        seen.add(path.casefold())
        files.append(FileEntry(path, size, file_hash))
    return Manifest(
        release_id=_str(signed, "release_id"), sequence=_int(signed, "sequence", 1, 2**53), version=version,
        channel=channel, platform=platform, artifact_name=name,
        artifact_size=_int(signed, "artifact_size", 1, MAX_ARTIFACT_BYTES), sha256=digest,
        package_format=package_format, published_at=_parse_time(signed.get("published_at"), "published_at"),
        expires_at=_parse_time(signed.get("expires_at"), "expires_at"), min_updater_version=minimum,
        files=tuple(files), digest=hashlib.sha256(canonical_bytes(signed)).hexdigest())


# ---- Signing and verifying ---------------------------------------------------------------------

def sign_manifest(signed: Mapping[str, Any], private_key: Ed25519PrivateKey) -> dict[str, Any]:
    """Publisher side: the envelope to publish. (Used by packaging/sign_release.py and the tests.)"""
    public = private_key.public_key().public_bytes_raw()
    signature = private_key.sign(canonical_bytes(signed))
    return {"signed": dict(signed), "signatures": [{"keyid": key_id(public),
                                                     "sig": base64.b64encode(signature).decode("ascii")}]}


def _now(now: _dt.datetime | None) -> _dt.datetime:
    return (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)


def verify_signature(envelope: Any, trust: TrustStore, now: _dt.datetime | None = None) -> dict[str, Any]:
    """The ``signed`` payload of ``envelope`` if at least one trusted, unrevoked, unexpired key signed
    its exact canonical bytes; otherwise VerificationError."""
    if not isinstance(envelope, dict) or set(envelope) != {"signed", "signatures"}:
        raise VerificationError("envelope is malformed")
    signed, signatures = envelope["signed"], envelope["signatures"]
    if not isinstance(signed, dict) or not isinstance(signatures, list) or not 0 < len(signatures) <= 8:
        raise VerificationError("envelope is malformed")
    if not trust.keys:
        raise VerificationError("no release key is trusted by this build")
    message = canonical_bytes(signed)
    moment = _now(now)
    for entry in signatures:
        if not isinstance(entry, dict) or not isinstance(entry.get("keyid"), str) or not isinstance(entry.get("sig"), str):
            continue
        for key in trust.keys:
            if not hmac.compare_digest(key.id, entry["keyid"]) or key.id in trust.revoked:
                continue
            if key.expires and moment > _parse_time(key.expires, "key expiry"):
                continue
            try:
                Ed25519PublicKey.from_public_bytes(key.public_key).verify(base64.b64decode(entry["sig"], validate=True), message)
                return signed
            except (InvalidSignature, ValueError, binascii.Error):
                continue
    raise VerificationError("no valid signature from a trusted release key")


def load_envelope(data: bytes) -> Any:
    if len(data) > MAX_MANIFEST_BYTES:
        raise VerificationError("manifest is too large")
    try:
        return json.loads(data.decode("utf-8"), parse_float=_reject, parse_constant=_reject)
    except (ValueError, UnicodeDecodeError) as exc:
        raise VerificationError(f"manifest is not valid JSON: {exc}") from exc


def _reject(_value: str) -> Any:
    raise ValueError("floats and non-finite numbers are not allowed")


def verify_release(data: bytes, *, trust: TrustStore, installed: Version, platform: Platform,
                   channel: Channel = Channel.STABLE, last_sequence: int = 0, now: _dt.datetime | None = None,
                   updater_version: Version = UPDATER_VERSION) -> Manifest:
    """Authenticate and vet a manifest for installation on this computer. Checks, in order: signature,
    schema, expiry, platform and architecture, channel eligibility, updater compatibility, that the
    version is newer than the installed one (no downgrade through the network path) and that the
    sequence is not older than the last accepted one (no replay of superseded metadata)."""
    manifest = parse_manifest(verify_signature(load_envelope(data), trust, now))
    moment = _now(now)
    if manifest.expires_at <= moment:
        raise VerificationError("metadata has expired")
    if manifest.published_at > moment + _dt.timedelta(minutes=10):
        raise VerificationError("metadata is dated in the future")
    if manifest.platform != platform:
        raise VerificationError(f"manifest is for {manifest.platform}, not {platform}")
    if channel is Channel.STABLE and manifest.channel is not Channel.STABLE:
        raise VerificationError("beta release is not eligible on the stable channel")
    if manifest.min_updater_version > updater_version:
        raise VerificationError("this release needs a newer updater: download it manually")
    if not manifest.version > installed:
        raise VerificationError("release is not newer than the installed version")
    if manifest.sequence < last_sequence:
        raise VerificationError("metadata is older than metadata already accepted (replay)")
    return manifest


def verify_artifact(path: Path, manifest: Manifest) -> None:
    """The downloaded archive must have exactly the signed size and SHA-256."""
    size = path.stat().st_size
    if size != manifest.artifact_size:
        raise VerificationError(f"archive size {size} does not match the signed size {manifest.artifact_size}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    if not hmac.compare_digest(digest.hexdigest(), manifest.sha256):
        raise VerificationError("archive SHA-256 does not match the signed value")
