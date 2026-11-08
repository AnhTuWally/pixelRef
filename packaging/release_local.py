"""Build, sign and verify a release on this machine, ready to upload to GitHub by hand.

    python packaging/release_local.py --key C:\\path\\to\\release.key
    python packaging/release_local.py --key ... --sequence 5      # force a sequence number
    python packaging/release_local.py --key ... --skip-tests      # skip the update system tests

The key may also come from $PIXELREF_RELEASE_KEY_FILE. Run it with the venv's Python, from a clean checkout.

What it does, in the order of .github/workflows/release.yml (docs/update-manual-release-guide.md):

1. Checks before anything slow: the working tree is clean, the private key pairs with the public key
   embedded in RELEASE_TRUST, and picks the next sequence number (higher than any it can find).
2. Runs the update system tests (test_update, test_workflows).
3. Builds and smoke-tests the app (packaging/build_exe.py), which zips dist/pixelRef-v<ver>-windows-x64.zip.
4. Signs the manifest with --inventory (packaging/sign_release.py).
5. Verifies zip + manifest against the embedded key, the way a user's computer will (packaging/verify_release.py).
6. Records the sequence number next to the key and copies both files to dist/release-v<version>/.

It never tags, pushes or publishes anything: it prints the commands for that at the end.

The sequence number must only ever increase. The next one is 1 + the highest of:
  * the record kept next to the key (release-sequence.json, written after every successful signing),
  * the manifests already published on GitHub (read from the public releases API; skipped if offline),
  * any manifest-*.json left in dist/.
If none of these can be read, pass --sequence yourself.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402

from pixelref.core.updates.discovery import MANIFEST_PATTERN  # noqa: E402
from pixelref.core.updates.models import RELEASES_URL  # noqa: E402
from pixelref.core.updates.verification import RELEASE_TRUST, key_id  # noqa: E402

STATE_NAME = "release-sequence.json"


def step(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def fail(message: str) -> None:
    raise SystemExit(f"\nRELEASE STOPPED: {message}")


def run(*args: str, env: dict | None = None) -> None:
    print("> " + " ".join(args), flush=True)
    result = subprocess.run(args, cwd=ROOT, env=env)
    if result.returncode != 0:
        fail(f"command failed with exit code {result.returncode}")


def git(*args: str) -> str:
    return subprocess.run(("git", *args), cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def read_version() -> str:
    text = (ROOT / "pixelref" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text)[1]


def platform_suffix() -> str:
    # Same naming as packaging/build_exe.py package().
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    return f"{system}-{arch}"


def check_key(path: Path) -> str:
    if not path.is_file():
        fail(f"private key not found: {path}")
    try:
        path.resolve().relative_to(ROOT)
    except ValueError:
        pass
    else:
        fail(f"the private key is inside the repository ({path}). Move it out; it must never be committed.")
    key = serialization.load_pem_private_key(path.read_bytes(), None)
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    kid = key_id(raw)
    embedded = {k.id for k in RELEASE_TRUST.keys}
    if kid not in embedded:
        fail(f"this private key (id {kid}) does not pair with the key embedded in "
             f"pixelref/core/updates/verification.py ({', '.join(sorted(embedded)) or 'none'}). Wrong key?")
    return kid


def recorded_sequence(state: Path) -> int | None:
    try:
        return int(json.loads(state.read_text(encoding="utf-8"))["last_sequence"])
    except FileNotFoundError:
        return None
    except (ValueError, KeyError, TypeError) as exc:
        fail(f"cannot read {state}: {exc}")


def github_sequence() -> int | None:
    """Highest sequence among the manifests published on GitHub, or None if GitHub can't be read."""
    def get(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "pixelRef-release-script",
                                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read()

    try:
        releases = json.loads(get(RELEASES_URL + "?per_page=100"))
        best = 0
        for release in releases:
            for asset in release.get("assets", []):
                if MANIFEST_PATTERN.match(asset.get("name", "")):
                    signed = json.loads(get(asset["browser_download_url"]))["signed"]
                    best = max(best, int(signed["sequence"]))
                    print(f"  {release.get('tag_name')}: {asset['name']} has sequence {signed['sequence']}")
        return best
    except Exception as exc:  # offline, rate-limited, private repo...
        print(f"  could not read GitHub releases ({exc}); relying on the local record")
        return None


def dist_sequence() -> int | None:
    best = None
    for path in DIST.glob("**/manifest-*.json"):
        try:
            seq = int(json.loads(path.read_text(encoding="utf-8"))["signed"]["sequence"])
        except (ValueError, KeyError, TypeError, OSError):
            continue
        best = seq if best is None else max(best, seq)
    return best


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", type=Path, default=os.environ.get("PIXELREF_RELEASE_KEY_FILE"),
                        help="the release private key (or set PIXELREF_RELEASE_KEY_FILE)")
    parser.add_argument("--sequence", type=int, help="sequence number to sign with (default: next after the highest known)")
    parser.add_argument("--valid-days", type=int, default=90, help="how long the manifest is accepted (default 90)")
    parser.add_argument("--skip-tests", action="store_true", help="skip the update system tests")
    parser.add_argument("--allow-dirty", action="store_true", help="build even with uncommitted changes (not for real releases)")
    parser.add_argument("--no-github", action="store_true", help="don't read published manifests from GitHub")
    args = parser.parse_args(argv)

    # ---- 1. Checks before the slow part ----------------------------------------------------------------
    step("1/6 checks")
    if not args.key:
        fail("no private key: pass --key or set PIXELREF_RELEASE_KEY_FILE")
    key = Path(args.key).expanduser()
    kid = check_key(key)
    print(f"  key OK, id {kid}")

    version = read_version()
    tag = f"v{version}"
    channel = "beta" if "-" in version else "stable"
    name = f"pixelRef-{tag}-{platform_suffix()}"
    print(f"  version {version}, tag {tag}, channel {channel}")

    dirty = git("status", "--porcelain")
    if dirty and not args.allow_dirty:
        fail("the working tree has uncommitted changes. Commit them first: the build must match the commit you tag.\n"
             + dirty)
    if git("tag", "--list", tag):
        print(f"  note: tag {tag} already exists locally; make sure it points at {git('rev-parse', '--short', 'HEAD')}")

    state = key.resolve().parent / STATE_NAME
    known = {"local record": recorded_sequence(state), "dist/": dist_sequence()}
    if not args.no_github:
        known["GitHub"] = github_sequence()
    found = {k: v for k, v in known.items() if v is not None}
    highest = max(found.values(), default=None)
    print("  highest sequence known: " + (", ".join(f"{k} {v}" for k, v in found.items()) or "none"))
    if args.sequence is not None:
        if highest is not None and args.sequence <= highest:
            fail(f"--sequence {args.sequence} is not higher than {highest}, already used")
        sequence = args.sequence
    elif highest is None:
        fail("no earlier sequence number could be found. Pass --sequence (higher than any manifest you ever "
             "published; docs/update-manual-release-guide.md records the last one used).")
    else:
        sequence = highest + 1
    print(f"  will sign with sequence {sequence}")

    py = sys.executable

    # ---- 2. Tests ---------------------------------------------------------------------------------------
    step("2/6 update system tests")
    if args.skip_tests:
        print("  skipped (--skip-tests)")
    else:
        run(py, "tests/run_tests.py", "test_update", "test_workflows", env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})

    # ---- 3. Build ---------------------------------------------------------------------------------------
    step("3/6 build and smoke-test")
    archive = DIST / f"{name}.zip"
    archive.unlink(missing_ok=True)
    run(py, "packaging/build_exe.py")
    if not archive.is_file():
        fail(f"the build did not produce {archive}")

    # ---- 4. Sign ----------------------------------------------------------------------------------------
    step("4/6 sign")
    manifest = DIST / f"manifest-{tag}-{platform_suffix()}.json"
    manifest.unlink(missing_ok=True)
    run(py, "packaging/sign_release.py", "sign", "--private", str(key), "--archive", str(archive),
        "--release-id", tag, "--sequence", str(sequence), "--channel", channel,
        "--valid-days", str(args.valid_days), "--inventory", "--out", str(manifest))

    # ---- 5. Verify --------------------------------------------------------------------------------------
    step("5/6 verify with the embedded key")
    run(py, "packaging/verify_release.py", "--manifest", str(manifest), "--archive", str(archive))

    # ---- 6. Record and collect --------------------------------------------------------------------------
    step("6/6 record and collect")
    state.write_text(json.dumps({"last_sequence": sequence, "release": tag, "channel": channel,
                                 "signed_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                                indent=2) + "\n", encoding="utf-8")
    print(f"  recorded sequence {sequence} in {state}")
    out = DIST / f"release-{tag}"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    shutil.copy2(archive, out / archive.name)
    shutil.copy2(manifest, out / manifest.name)

    prerelease = " (tick 'Set as a pre-release')" if channel == "beta" else ""
    print(f"""
Release {tag} is built, signed (sequence {sequence}) and verified.

Upload both files from:
    {out}
        {archive.name}
        {manifest.name}

Then:
  1. Tag and push the commit you built:
         git tag {tag}
         git push upstream {tag}
  2. On GitHub: Releases > Draft a new release > tag {tag}{prerelease}.
     Attach BOTH files, names unchanged. Save as a draft.
  3. Download both files back from the draft into a new folder and check them:
         {Path(py).name} packaging/verify_release.py --manifest <folder>/{manifest.name} --archive <folder>/{archive.name}
     Publish only if it prints OK.
  4. Update "Last sequence used" in docs/update-manual-release-guide.md to {sequence}.
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
