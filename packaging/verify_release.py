"""Verify a published release the way a user's computer will: signature, schema, size, SHA-256, inventory.

    python packaging/verify_release.py --manifest manifest-v1.2.0-windows-x64.json \
        --archive pixelRef-v1.2.0-windows-x64.zip [--public release.pub] [--installed 1.1.0]

Without ``--public`` it uses the key embedded in the app (``RELEASE_TRUST``), which is what users have.
With it, the given base64 public key is trusted instead (for the first release, before the key is embedded).
Exit 0 only if everything checks out. Used by the release workflow before a draft release is published.
"""

from __future__ import annotations

import argparse
import base64
import io
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pixelref.core.updates import Channel, Platform, Version  # noqa: E402
from pixelref.core.updates.verification import (RELEASE_TRUST, TrustedKey, TrustStore, VerificationError,  # noqa: E402
                                                verify_artifact, verify_release)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--public", type=Path, help="base64 public key file to trust instead of the embedded key")
    parser.add_argument("--installed", default="0.0.0", help="installed version to check 'newer than' against")
    args = parser.parse_args(argv)
    trust = TrustStore((TrustedKey(base64.b64decode(args.public.read_text().strip())),)) if args.public else RELEASE_TRUST
    try:
        raw = args.manifest.read_bytes()
        import json

        signed = json.loads(raw)["signed"]
        platform = Platform(signed["platform"], signed["architecture"])
        manifest = verify_release(raw, trust=trust, installed=Version.parse(args.installed), platform=platform,
                                  channel=Channel.BETA)
        verify_artifact(args.archive, manifest)
        if args.archive.name != manifest.artifact_name:
            raise VerificationError(f"archive is named {args.archive.name}, the manifest says {manifest.artifact_name}")
        if not manifest.files:
            raise VerificationError("manifest has no signed file inventory (build with --inventory)")
        with zipfile.ZipFile(args.archive) as z:
            names = {i.filename for i in z.infolist() if not i.is_dir()}
        if names != {f.path for f in manifest.files}:
            raise VerificationError("the archive's files differ from the signed inventory")
    except (VerificationError, KeyError, ValueError, OSError, zipfile.BadZipFile) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {manifest.artifact_name} {manifest.version} (sequence {manifest.sequence}, "
          f"{len(manifest.files)} files, expires {manifest.expires_at:%Y-%m-%d})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
