"""Release authenticity (M2): signed canonical manifests fail closed. No network, no Qt."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from harness import ROOT, tmp_dir
from pixelref.core.updates import Channel, Platform, Version
from pixelref.core.updates.verification import (RELEASE_TRUST, TrustedKey, TrustStore, VerificationError,
                                                canonical_bytes, parse_manifest, safe_relative_path, sign_manifest,
                                                verify_artifact, verify_release, verify_signature)

WIN = Platform("windows", "x64")
NOW = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
KEY = Ed25519PrivateKey.generate()
OTHER = Ed25519PrivateKey.generate()
TRUST = TrustStore((TrustedKey(KEY.public_key().public_bytes_raw()),))


def payload(**changes) -> dict:
    p = {"schema_version": 1, "release_id": "v1.2.0", "sequence": 5, "version": "1.2.0", "channel": "stable",
         "platform": "windows", "architecture": "x64", "artifact_name": "pixelRef-v1.2.0-windows-x64.zip",
         "artifact_size": 100, "sha256": "ab" * 32, "package_format": "zip",
         "published_at": "2026-10-31T00:00:00Z", "expires_at": "2027-01-31T00:00:00Z", "min_updater_version": "1.0.0"}
    p.update(changes)
    return p


def blob(envelope) -> bytes:
    return json.dumps(envelope).encode()


def verify(envelope, **kw):
    args = dict(trust=TRUST, installed=Version(1, 1, 0), platform=WIN, now=NOW)
    args.update(kw)
    return verify_release(blob(envelope), **args)


def fails(fn, text=""):
    try:
        fn()
    except VerificationError as exc:
        assert text in str(exc), str(exc)
        return
    raise AssertionError("expected VerificationError")


def test_valid_manifest_verifies():
    m = verify(sign_manifest(payload(), KEY))
    assert str(m.version) == "1.2.0" and m.sha256 == "ab" * 32 and m.platform == WIN and len(m.digest) == 64


def test_canonical_bytes_are_stable_and_domain_separated():
    a = canonical_bytes({"b": 1, "a": ["é", True]})
    assert a == canonical_bytes({"a": ["é", True], "b": 1}) and a.startswith(b"pixelref-update-manifest-v1\n")
    assert a.endswith(b'{"a":["\\u00e9",true],"b":1}')
    for bad in ({"x": 1.5}, {"x": None}, {1: 2}, {"x": 2**60}):
        fails(lambda bad=bad: canonical_bytes(bad))
    sig = KEY.sign(b"something else")  # a signature over other data is useless
    env = sign_manifest(payload(), KEY)
    env["signatures"][0]["sig"] = __import__("base64").b64encode(sig).decode()
    fails(lambda: verify(env), "signature")


def test_any_tampered_field_breaks_the_signature():
    env = sign_manifest(payload(), KEY)
    for name, value in (("sha256", "cd" * 32), ("artifact_size", 101), ("version", "9.0.0"),
                        ("platform", "linux"), ("expires_at", "2099-01-01T00:00:00Z"), ("sequence", 6)):
        bad = copy.deepcopy(env)
        bad["signed"][name] = value
        fails(lambda bad=bad: verify(bad), "signature")
    bad = copy.deepcopy(env)
    bad["signed"]["files"] = []  # adding even an empty unsigned field changes the bytes
    fails(lambda: verify(bad), "signature")


def test_wrong_missing_revoked_and_expired_keys_fail():
    fails(lambda: verify(sign_manifest(payload(), OTHER)), "signature")
    fails(lambda: verify(sign_manifest(payload(), KEY), trust=RELEASE_TRUST), "no release key")
    revoked = TrustStore(TRUST.keys, frozenset({TRUST.keys[0].id}))
    fails(lambda: verify(sign_manifest(payload(), KEY), trust=revoked), "signature")
    old = TrustStore((TrustedKey(KEY.public_key().public_bytes_raw(), "2026-06-01T00:00:00Z"),))
    fails(lambda: verify(sign_manifest(payload(), KEY), trust=old), "signature")
    rotated = TrustStore(TRUST.keys + (TrustedKey(OTHER.public_key().public_bytes_raw()),))
    verify(sign_manifest(payload(), OTHER), trust=rotated)  # either key during a rotation
    for broken in ({}, {"signed": {}, "signatures": []}, {"signed": [], "signatures": [1]}, [], "x"):
        fails(lambda broken=broken: verify(broken))


def test_context_checks_fail_closed():
    env = sign_manifest(payload(), KEY)
    fails(lambda: verify(env, platform=Platform("linux", "x64")), "not")
    fails(lambda: verify(env, platform=Platform("windows", "arm64")), "not")
    fails(lambda: verify(env, now=dt.datetime(2027, 2, 1, tzinfo=dt.timezone.utc)), "expired")
    fails(lambda: verify(env, now=dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc)), "future")
    fails(lambda: verify(env, installed=Version(1, 2, 0)), "not newer")
    fails(lambda: verify(env, installed=Version(2, 0, 0)), "not newer")
    fails(lambda: verify(env, updater_version=Version(0, 9, 0)), "newer updater")
    beta = sign_manifest(payload(version="1.3.0-beta.1", channel="beta", release_id="v1.3.0-beta.1",
                                 artifact_name="pixelRef-v1.3.0-beta.1-windows-x64.zip"), KEY)
    fails(lambda: verify(beta), "stable channel")
    verify(beta, channel=Channel.BETA)


def test_replay_and_downgrade():
    old = sign_manifest(payload(sequence=3), KEY)
    verify(old)  # nothing accepted yet
    fails(lambda: verify(old, last_sequence=5), "replay")
    verify(sign_manifest(payload(sequence=5), KEY), last_sequence=5)
    # a validly signed *older release* can't be pushed to a newer install
    fails(lambda: verify(sign_manifest(payload(version="1.0.5", release_id="v1.0.5",
                                              artifact_name="pixelRef-v1.0.5-windows-x64.zip"), KEY)), "not newer")


def test_schema_is_strict_and_cannot_name_destinations_or_commands():
    for extra in ({"install_path": "C:/Windows"}, {"command": "calc.exe"}, {"url": "https://evil.example/a.zip"}):
        fails(lambda extra=extra: parse_manifest(payload(**extra)), "unknown")
    for bad in (dict(artifact_name="pixelRef-v1.2.0-linux-x64.zip"), dict(artifact_name="../pixelRef-v1.2.0-windows-x64.zip"),
                dict(artifact_name="pixelRef-v1.2.1-windows-x64.zip"), dict(sha256="AB" * 32), dict(sha256="ab"),
                dict(artifact_size=0), dict(artifact_size=True), dict(package_format="exe"), dict(channel="nightly"),
                dict(channel="beta"), dict(version="1.2"), dict(expires_at="tomorrow"), dict(schema_version=2),
                dict(sequence=0), dict(release_id="")):
        fails(lambda bad=bad: parse_manifest(payload(**bad)))


def test_file_inventory_paths_are_validated():
    good = {"path": "_internal/base.dll", "size": 3, "sha256": "cd" * 32}
    assert parse_manifest(payload(files=[good])).files[0].path == "_internal/base.dll"
    for path in ("../x", "/etc/passwd", "C:/x", "a\\b", "a/../b", "", "a/./b", "a//b", "a/", "con.txt", "x/nul", "a/b.", "a\x00b"):
        assert not safe_relative_path(path), path
        fails(lambda path=path: parse_manifest(payload(files=[dict(good, path=path)])))
    fails(lambda: parse_manifest(payload(files=[good, dict(good, path="_INTERNAL/BASE.DLL")])))  # case collision
    fails(lambda: parse_manifest(payload(files=[dict(good, extra=1)])))


def test_artifact_hash_and_size_are_checked():
    folder = tmp_dir()
    archive = folder / "pixelRef-v1.2.0-windows-x64.zip"
    archive.write_bytes(b"x" * 100)
    good = parse_manifest(payload(sha256=hashlib.sha256(b"x" * 100).hexdigest()))
    verify_artifact(archive, good)
    archive.write_bytes(b"y" * 100)
    fails(lambda: verify_artifact(archive, good), "SHA-256")
    archive.write_bytes(b"x" * 99)
    fails(lambda: verify_artifact(archive, good), "size")


def test_json_edge_cases_are_rejected():
    for text in (b"\xff", b"{", b'{"signed": {"x": 1.5}, "signatures": []}', b'{"a": NaN}', b"x" * (1024 * 1024 + 1)):
        fails(lambda text=text: verify_release(text, trust=TRUST, installed=Version(1, 1, 0), platform=WIN, now=NOW))


def test_publisher_tool_round_trip_and_keeps_private_key_out_of_output():
    folder = tmp_dir()
    tool = str(ROOT / "packaging" / "sign_release.py")
    run = lambda *a: subprocess.run([sys.executable, "-I", tool, *a], capture_output=True, text=True, check=True).stdout
    run("generate-key", "--private", str(folder / "k.key"), "--public", str(folder / "k.pub"))
    assert oct((folder / "k.key").stat().st_mode & 0o777) == "0o600" or sys.platform == "win32"
    archive = folder / "pixelRef-v1.2.0-windows-x64.zip"
    import zipfile
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("pixelRef/pixelRef.exe", b"exe")
    run("sign", "--private", str(folder / "k.key"), "--archive", str(archive), "--release-id", "v1.2.0",
        "--sequence", "1", "--channel", "stable", "--inventory", "--out", str(folder / "m.json"))
    import base64
    public = base64.b64decode((folder / "k.pub").read_text())
    manifest = verify_release((folder / "m.json").read_bytes(), trust=TrustStore((TrustedKey(public),)),
                              installed=Version(1, 1, 0), platform=WIN)
    assert manifest.files[0].path == "pixelRef/pixelRef.exe"
    verify_artifact(archive, manifest)
    assert b"PRIVATE" not in (folder / "m.json").read_bytes()
