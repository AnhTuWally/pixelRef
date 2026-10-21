"""``python -m pixelref plugin …``: tools for people who publish plugins.

    python -m pixelref plugin keygen ~/keys/studio.pem --name "Example Studio"
    python -m pixelref plugin sign path/to/my_plugin --key ~/keys/studio.pem
    python -m pixelref plugin verify path/to/my_plugin [--publisher studio.pub.toml]
    python -m pixelref plugin hash path/to/my_plugin

``keygen`` writes a private signing key (keep it secret; ``--password`` encrypts
it) and ``<name>.pub.toml`` next to it, the public half users add under
Window → Plugins… → Sources → Trusted publishers. ``sign`` writes
``SIGNATURE.toml`` into the plugin folder; sign again after changing any file.
``verify`` checks a signature the way pixelRef does when it loads the plugin.
``hash`` prints the content hash an approval is pinned to.

The standalone build runs the same as ``pixelref-cli plugin …``.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import tomllib
from pathlib import Path

from .core.errors import PluginError
from .core.plugin_trust import (
    FileHasher,
    check_signature,
    folder_digest,
    generate_key,
    missing_publish_fields,
    read_public_key,
    sign_plugin,
)


def _manifest(folder: Path) -> dict:
    try:
        return tomllib.loads((folder / "plugin.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PluginError(f"{folder / 'plugin.toml'}: {exc}") from exc


def _password(args: argparse.Namespace, confirm: bool = False) -> bytes | None:
    if not args.password:
        return None
    first = getpass.getpass("Key password: ")
    if confirm and getpass.getpass("Again: ") != first:
        raise PluginError("The passwords don't match")
    return first.encode("utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pixelref plugin", description="Sign and check pixelRef plugins.")
    sub = parser.add_subparsers(dest="action", required=True)
    keygen = sub.add_parser("keygen", help="make a signing key (and its .pub.toml)")
    keygen.add_argument("key", help="where to write the private key, e.g. studio.pem")
    keygen.add_argument("--name", required=True, help="the publisher's name users will see")
    keygen.add_argument("--password", action="store_true", help="encrypt the key (asks for a password)")
    sign = sub.add_parser("sign", help="write SIGNATURE.toml for a plugin folder")
    sign.add_argument("folder")
    sign.add_argument("--key", required=True, help="the private key from keygen")
    sign.add_argument("--signer", help="the name in the signature (default: the key's .pub.toml name)")
    sign.add_argument("--password", action="store_true", help="the key is encrypted (asks for its password)")
    verify = sub.add_parser("verify", help="check a plugin's signature")
    verify.add_argument("folder")
    verify.add_argument("--publisher", help="a .pub.toml: also check that this publisher signed it")
    verify.add_argument("--json", action="store_true")
    hashing = sub.add_parser("hash", help="print a plugin's content hash and files")
    hashing.add_argument("folder")
    hashing.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "keygen":
            public = generate_key(Path(args.key), args.name, _password(args, confirm=True))
            print(f"Private key: {args.key}  (keep it secret)")
            print(f"Public key:  {Path(args.key).with_name(Path(args.key).stem + '.pub.toml')}  (give this to users)")
            print(f"Key id:      {public['key_id']}")
            return 0
        folder = Path(args.folder)
        if args.action == "sign":
            signer = args.signer
            if not signer:
                pub = Path(args.key).with_name(Path(args.key).stem + ".pub.toml")
                signer = read_public_key(pub)["name"] if pub.is_file() else ""
            if not signer:
                raise PluginError("Say who signs it: --signer \"Name\"")
            check = sign_plugin(folder, Path(args.key), signer, _password(args))
            print(f"Signed {folder} as {check.signer} ({check.key_id})")
            return 0
        hasher = FileHasher()
        files = hasher.files(folder)
        manifest = _manifest(folder)
        if args.action == "hash":
            if args.json:
                print(json.dumps({"digest": folder_digest(files), "files": files}, indent=2))
            else:
                print(folder_digest(files))
                for name, value in files.items():
                    print(f"  {value}  {name}")
            return 0
        check = check_signature(folder, str(manifest.get("id") or folder.name), str(manifest.get("version", "0.0.0")),
                                files)
        result = {**check.to_dict(), "missing_fields": missing_publish_fields(manifest)}
        ok = check.status == "valid"
        if args.publisher and ok:
            expected = read_public_key(Path(args.publisher))
            result["publisher_matches"] = expected["key_id"] == check.key_id
            ok = result["publisher_matches"]
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if check.status == "valid":
                print(f"Valid signature by {check.signer} ({check.key_id}), signed {check.signed_at}")
            elif check.status == "unsigned":
                print("Not signed")
            else:
                print(f"INVALID: {check.problem}")
            if result.get("publisher_matches") is False:
                print("…but not by the publisher given with --publisher")
            if result["missing_fields"]:
                print("plugin.toml is missing: " + ", ".join(result["missing_fields"]))
        return 0 if ok else 1
    except PluginError as exc:
        print(f"pixelref plugin: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
