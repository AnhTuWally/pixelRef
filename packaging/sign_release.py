"""Publisher tool for application release manifests. Not bundled in the app; run on a trusted machine.

    python packaging/sign_release.py generate-key  --private release.key --public release.pub
    python packaging/sign_release.py sign --private release.key --archive pixelRef-v1.2.0-windows-x64.zip \
        --release-id v1.2.0 --sequence 7 --channel stable --out manifest-v1.2.0-windows-x64.json

The private key file is created with owner-only permissions and must never enter the repository or a
build artifact. Keep it separate from the plugin-signing keys. ``sign`` reads the key from a path (or
$PIXELREF_RELEASE_KEY_FILE in CI) and prints the public key id it signed with.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from pixelref.core.updates.discovery import ASSET_PATTERN  # noqa: E402
from pixelref.core.updates.verification import SCHEMA_VERSION, key_id, parse_manifest, sign_manifest  # noqa: E402

TIME = "%Y-%m-%dT%H:%M:%SZ"


def generate_key(private: Path, public: Path) -> None:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)
    raw = key.public_key().public_bytes_raw()
    public.write_text(base64.b64encode(raw).decode() + "\n", encoding="ascii")
    print(f"public key (embed in RELEASE_TRUST): {base64.b64encode(raw).decode()}\nkey id: {key_id(raw)}")


def build_payload(archive: Path, release_id: str, sequence: int, channel: str, days: int, inventory: bool) -> dict:
    match = ASSET_PATTERN.match(archive.name)
    if not match:
        raise SystemExit(f"{archive.name} is not named pixelRef-v<version>-<os>-<arch>.zip")
    data = archive.read_bytes()
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    payload = {
        "schema_version": SCHEMA_VERSION, "release_id": release_id, "sequence": sequence,
        "version": match["version"], "channel": channel, "platform": match["system"],
        "architecture": match["arch"], "artifact_name": archive.name, "artifact_size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(), "package_format": "zip",
        "published_at": now.strftime(TIME), "expires_at": (now + dt.timedelta(days=days)).strftime(TIME),
        "min_updater_version": "1.0.0",
    }
    if inventory:
        with zipfile.ZipFile(archive) as z:
            payload["files"] = [{"path": i.filename, "size": i.file_size,
                                 "sha256": hashlib.sha256(z.read(i)).hexdigest()}
                                for i in z.infolist() if not i.is_dir()]
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate-key")
    g.add_argument("--private", type=Path, required=True)
    g.add_argument("--public", type=Path, required=True)
    s = sub.add_parser("sign")
    s.add_argument("--private", type=Path, default=os.environ.get("PIXELREF_RELEASE_KEY_FILE"))
    s.add_argument("--archive", type=Path, required=True)
    s.add_argument("--release-id", required=True)
    s.add_argument("--sequence", type=int, required=True)
    s.add_argument("--channel", choices=("stable", "beta"), required=True)
    s.add_argument("--valid-days", type=int, default=90)
    s.add_argument("--inventory", action="store_true", help="also sign a per-file hash inventory of the archive")
    s.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.cmd == "generate-key":
        generate_key(args.private, args.public)
        return 0
    if not args.private:
        raise SystemExit("no private key: pass --private or set PIXELREF_RELEASE_KEY_FILE")
    key = serialization.load_pem_private_key(Path(args.private).read_bytes(), None)
    payload = build_payload(args.archive, args.release_id, args.sequence, args.channel, args.valid_days, args.inventory)
    parse_manifest(payload)  # never publish something the app would reject
    envelope = sign_manifest(payload, key)
    args.out.write_text(json.dumps(envelope, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"signed {args.archive.name} with key {envelope['signatures'][0]['keyid']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
