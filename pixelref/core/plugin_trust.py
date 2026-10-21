"""Where a plugin comes from and whether the user trusts it.

Three things identify a plugin's code:

* its **content hash**: SHA-256 over every file in its folder (``__pycache__``,
  ``*.pyc`` and version-control folders excluded). Approving a plugin records
  this hash; when any file changes the approval no longer applies.
* an optional **signature** (``SIGNATURE.toml`` next to ``plugin.toml``): an
  Ed25519 signature by a publisher over the plugin's id, version and the hash
  of every file. ``python -m pixelref plugin sign`` writes it.
* the user's **trust decisions**, kept in pixelRef's settings (never in the
  plugin folder, so a plugin can't approve itself): approved plugins, pinned
  to their content hash, and *trusted publishers*, whose signed plugins load
  without asking.

Nothing here imports plugin code.
"""

from __future__ import annotations

import base64
import contextlib
import datetime as _dt
import hashlib
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import PluginError

if TYPE_CHECKING:
    from .settings import Settings

SIGNATURE_FILE = "SIGNATURE.toml"
EXCLUDED_DIRS = frozenset({"__pycache__", ".git", ".hg", ".svn", ".mypy_cache", ".pytest_cache", ".ruff_cache"})
EXCLUDED_SUFFIXES = (".pyc", ".pyo")
TRUST_KEY = "plugins/trust"
PUBLISHERS_KEY = "plugins/publishers"
ALGORITHM = "ed25519"

# What a plugin must say about itself before it can be signed (dotted manifest keys).
PUBLISH_FIELDS = ("author.name", "license")


# ---- Content hashes ------------------------------------------------------------------------------------------


def _excluded(relative: Path) -> bool:
    return any(part in EXCLUDED_DIRS for part in relative.parts[:-1]) or relative.name.endswith(EXCLUDED_SUFFIXES)


class FileHasher:
    """SHA-256 of every file of a plugin folder, re-reading only files whose size or time changed."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[int, int, str]] = {}

    def files(self, folder: Path) -> dict[str, str]:
        """``{"relative/posix/path": "sha256:…"}`` for every file (the signature file excluded).

        Raises :class:`PluginError` for a link that leads out of the folder."""
        root = folder.resolve()
        out: dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDED_DIRS)
            for name in sorted(filenames):
                path = Path(dirpath) / name
                relative = path.relative_to(folder)
                if _excluded(relative) or relative.as_posix() == SIGNATURE_FILE:
                    continue
                resolved = path.resolve()
                if not resolved.is_relative_to(root):
                    raise PluginError(f"{relative.as_posix()} links to {resolved}, outside the plugin folder")
                out[relative.as_posix()] = self.file_hash(path)
        return out

    def file_hash(self, path: Path) -> str:
        stat = path.stat()
        key = str(path)
        cached = self._cache.get(key)
        if cached is not None and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
            return cached[2]
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
        value = "sha256:" + digest.hexdigest()
        self._cache[key] = (stat.st_size, stat.st_mtime_ns, value)
        return value

    def digest(self, folder: Path) -> str:
        return folder_digest(self.files(folder))


def folder_digest(files: dict[str, str]) -> str:
    """One hash for a whole folder (from :meth:`FileHasher.files`)."""
    text = "\n".join(f"{name}\0{value}" for name, value in sorted(files.items()))
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def bytes_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def changed_files(old: dict[str, str], new: dict[str, str]) -> list[str]:
    """``["+ added.py", "- removed.py", "~ changed.py"]``."""
    out = [f"+ {name}" for name in sorted(set(new) - set(old))]
    out += [f"- {name}" for name in sorted(set(old) - set(new))]
    out += [f"~ {name}" for name in sorted(set(old) & set(new)) if old[name] != new[name]]
    return out


# ---- Signatures ----------------------------------------------------------------------------------------------


def key_id(public_raw: bytes) -> str:
    """A publisher key's fingerprint: ``sha256:`` + 32 hex digits."""
    return "sha256:" + hashlib.sha256(public_raw).hexdigest()[:32]


def canonical_payload(plugin_id: str, version: str, files: dict[str, str]) -> bytes:
    """The bytes a signature covers."""
    data = {"id": plugin_id, "version": version, "files": dict(sorted(files.items()))}
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass
class SignatureCheck:
    status: str = "unsigned"  # unsigned, valid, invalid
    key_id: str = ""
    signer: str = ""
    public_key: str = ""  # base64 raw Ed25519 public key
    signed_at: str = ""
    problem: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"status": self.status, "key_id": self.key_id, "signer": self.signer, "signed_at": self.signed_at,
                "problem": self.problem}


def _public_key(text: str) -> Any:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        raw = base64.b64decode(text, validate=True)
        return Ed25519PublicKey.from_public_bytes(raw), raw
    except (ValueError, TypeError) as exc:
        raise PluginError(f"not an Ed25519 public key: {exc}") from exc


def check_signature(folder: Path, plugin_id: str, version: str, files: dict[str, str]) -> SignatureCheck:
    """Does ``SIGNATURE.toml`` match these files (from :meth:`FileHasher.files`)?

    Says nothing about whether the signer is trusted: that's the user's list of publishers."""
    path = folder / SIGNATURE_FILE
    if not path.is_file():
        return SignatureCheck()
    check = SignatureCheck(status="invalid")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        check.problem = f"{SIGNATURE_FILE} can't be read: {exc}"
        return check
    check.signer = str(data.get("signer", ""))
    check.key_id = str(data.get("key_id", ""))
    check.public_key = str(data.get("public_key", ""))
    check.signed_at = str(data.get("signed_at", ""))
    if data.get("algorithm") != ALGORITHM:
        check.problem = f"unsupported signature algorithm {data.get('algorithm')!r}"
        return check
    signed = data.get("files")
    if not isinstance(signed, dict) or not all(isinstance(v, str) for v in signed.values()):
        check.problem = f"{SIGNATURE_FILE} has no file list"
        return check
    try:
        public, raw = _public_key(check.public_key)
    except PluginError as exc:
        check.problem = str(exc)
        return check
    if key_id(raw) != check.key_id:
        check.problem = "the key id doesn't match the public key"
        return check
    differences = changed_files(signed, files)
    if differences:
        shown = ", ".join(differences[:5]) + (" …" if len(differences) > 5 else "")
        check.problem = f"files differ from what was signed: {shown}"
        return check
    from cryptography.exceptions import InvalidSignature

    try:
        public.verify(base64.b64decode(str(data.get("signature", ""))), canonical_payload(plugin_id, version, signed))
    except (InvalidSignature, ValueError):
        check.problem = "the signature doesn't verify (wrong key, or the id, version or files were altered)"
        return check
    check.status = "valid"
    return check


def generate_key(private_path: Path, name: str, password: bytes | None = None) -> dict[str, str]:
    """Write a new signing key (PEM) and ``<stem>.pub.toml`` next to it; returns the public part."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if private_path.exists():
        raise PluginError(f"{private_path} already exists")
    key = Ed25519PrivateKey.generate()
    encryption = (serialization.BestAvailableEncryption(password) if password
                  else serialization.NoEncryption())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption)
    private_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(pem)
    with contextlib.suppress(OSError):
        os.chmod(private_path, 0o600)
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public = {"name": name, "key_id": key_id(raw), "public_key": base64.b64encode(raw).decode("ascii")}
    public_path = private_path.with_name(private_path.stem + ".pub.toml")
    public_path.write_text("".join(f'{k} = "{v}"\n' for k, v in public.items()), encoding="utf-8")
    return public


def read_public_key(path: Path) -> dict[str, str]:
    """A ``*.pub.toml`` file from :func:`generate_key`: ``{name, key_id, public_key}``."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PluginError(f"{path}: {exc}") from exc
    _public, raw = _public_key(str(data.get("public_key", "")))
    return {"name": str(data.get("name") or path.stem), "key_id": key_id(raw),
            "public_key": base64.b64encode(raw).decode("ascii")}


def missing_publish_fields(manifest_data: dict[str, Any]) -> list[str]:
    missing = []
    for dotted in PUBLISH_FIELDS:
        value: Any = manifest_data
        for part in dotted.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if not value:
            missing.append(dotted)
    source = manifest_data.get("source")
    if not (isinstance(source, dict) and (source.get("homepage") or source.get("repository"))):
        missing.append("source.homepage or source.repository")
    return missing


def sign_plugin(folder: Path, private_path: Path, signer: str, password: bytes | None = None,
                hasher: FileHasher | None = None) -> SignatureCheck:
    """Write ``SIGNATURE.toml`` for the plugin in ``folder``; returns the verified result."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    manifest_path = folder / "plugin.toml"
    try:
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PluginError(f"{manifest_path}: {exc}") from exc
    missing = missing_publish_fields(manifest)
    if missing:
        raise PluginError("plugin.toml must say who made it before it can be signed; missing: " + ", ".join(missing))
    plugin_id = str(manifest.get("id") or folder.name)
    version = str(manifest.get("version", "0.0.0"))
    try:
        key = serialization.load_pem_private_key(private_path.read_bytes(), password=password)
    except (OSError, ValueError, TypeError) as exc:
        raise PluginError(f"can't read the signing key {private_path}: {exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise PluginError(f"{private_path} is not an Ed25519 key")
    files = (hasher or FileHasher()).files(folder)
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    signature = key.sign(canonical_payload(plugin_id, version, files))
    lines = [
        "# Written by `python -m pixelref plugin sign`. Don't edit: any change to the plugin's files",
        "# (this one excluded) makes the signature invalid; sign again after changing them.",
        f'algorithm = "{ALGORITHM}"',
        f"signer = {json.dumps(signer)}",
        f'key_id = "{key_id(raw)}"',
        f'public_key = "{base64.b64encode(raw).decode("ascii")}"',
        f'signed_at = "{_now()}"',
        f'signature = "{base64.b64encode(signature).decode("ascii")}"',
        "",
        "[files]",
    ] + [f"{json.dumps(name)} = \"{value}\"" for name, value in sorted(files.items())]
    (folder / SIGNATURE_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return check_signature(folder, plugin_id, version, files)


# ---- The user's decisions --------------------------------------------------------------------------------------


@dataclass
class TrustGrant:
    """The user approved the plugin ``id`` in ``path`` with exactly these files."""

    path: str
    digest: str
    files: dict[str, str] = field(default_factory=dict)
    granted_at: str = ""
    allow_invalid: bool = False  # approved although its signature didn't verify

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"path": self.path, "digest": self.digest, "files": self.files,
                                "granted_at": self.granted_at}
        if self.allow_invalid:
            data["allow_invalid"] = True
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrustGrant":
        files = data.get("files") if isinstance(data.get("files"), dict) else {}
        return cls(path=str(data.get("path", "")), digest=str(data.get("digest", "")),
                   files={str(k): str(v) for k, v in files.items()}, granted_at=str(data.get("granted_at", "")),
                   allow_invalid=bool(data.get("allow_invalid", False)))


class TrustStore:
    """Approved plugins and trusted publishers, in the user's settings."""

    def __init__(self, settings: "Settings") -> None:
        self.settings = settings

    def grants(self) -> dict[str, TrustGrant]:
        data = self.settings.json_value(TRUST_KEY, {})
        if not isinstance(data, dict):
            return {}
        return {str(k): TrustGrant.from_dict(v) for k, v in data.items() if isinstance(v, dict)}

    def grant(self, plugin_id: str, path: Path, files: dict[str, str], allow_invalid: bool = False) -> TrustGrant:
        grants = self.grants()
        grant = TrustGrant(path=str(path), digest=folder_digest(files), files=dict(files), granted_at=_now(),
                           allow_invalid=allow_invalid)
        grants[plugin_id] = grant
        self._save_grants(grants)
        return grant

    def revoke(self, plugin_id: str) -> bool:
        grants = self.grants()
        if grants.pop(plugin_id, None) is None:
            return False
        self._save_grants(grants)
        return True

    def _save_grants(self, grants: dict[str, TrustGrant]) -> None:
        self.settings.set_json(TRUST_KEY, {k: g.to_dict() for k, g in sorted(grants.items())})

    def publishers(self) -> list[dict[str, str]]:
        data = self.settings.json_value(PUBLISHERS_KEY, [])
        return [dict(p) for p in data if isinstance(p, dict) and p.get("key_id")] if isinstance(data, list) else []

    def publisher(self, key: str) -> dict[str, str] | None:
        return next((p for p in self.publishers() if p["key_id"] == key), None)

    def add_publisher(self, name: str, public_key: str) -> dict[str, str]:
        _public, raw = _public_key(public_key)
        entry = {"name": name, "key_id": key_id(raw), "public_key": base64.b64encode(raw).decode("ascii"),
                 "added": _now()}
        publishers = [p for p in self.publishers() if p["key_id"] != entry["key_id"]] + [entry]
        self.settings.set_json(PUBLISHERS_KEY, publishers)
        return entry

    def remove_publisher(self, key: str) -> bool:
        publishers = self.publishers()
        kept = [p for p in publishers if p["key_id"] != key]
        self.settings.set_json(PUBLISHERS_KEY, kept)
        return len(kept) != len(publishers)

    def trusts_key(self, key: str, public_key: str) -> bool:
        """The publisher is on the list *with this exact key* (a matching id alone isn't enough)."""
        entry = self.publisher(key)
        return entry is not None and entry.get("public_key") == public_key


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()

