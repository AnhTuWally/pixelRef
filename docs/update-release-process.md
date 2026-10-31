# Release signing runbook

Step-by-step instructions for the maintainer. Background and threat model: [update-security.md](update-security.md).

Until step 1 and 2 are done, **no update can be verified** (the app ships with no trusted release key
and fails closed). Update *discovery* works without them.

## What you need

- A trusted, offline-capable machine (not a shared CI runner) with Python 3.11+ and `pip install cryptography`.
- A checkout of this repository.
- Somewhere safe for the private key: a password manager attachment or an encrypted drive, with a
  backup. If the key is lost you can still ship builds, but you must embed a new key (step 7).

## 1. Generate the release key (once)

Run this **outside the repository folder** so the key can't be committed by accident:

```bash
mkdir -p ~/pixelref-release-keys && cd ~/pixelref-release-keys
python /path/to/pixelRef/packaging/sign_release.py generate-key \
    --private release.key --public release.pub
```

It prints the public key (base64) and a key id. It refuses to overwrite an existing `release.key`.
`release.key` is created readable by you only (`0600`).

- Back up `release.key` now, to two places.
- **Never** commit, email, paste into chat, bundle in a build, or put it in a CI log.
- This key is for application releases only. Do not reuse a plugin-publisher key.

## 2. Embed the public key in the app

Open `pixelref/core/updates/verification.py` and replace the empty trust store:

```python
import base64   # add near the other imports if missing

RELEASE_TRUST = TrustStore(keys=(
    TrustedKey(base64.b64decode("PASTE_THE_BASE64_PUBLIC_KEY_FROM_STEP_1")),
))
```

Only the **public** key goes here (`release.pub`, 44 base64 characters). Run
`python tests/run_tests.py test_update_verification` and commit. Every build from this commit on can
verify releases signed with your key. Older builds (including v1.1.0) cannot, which is expected.

## 3. Build the release archive

```bash
python packaging/build_exe.py            # builds and smoke-tests dist/pixelRef/
```

Zip the folder so the archive is named exactly **`pixelRef-v<version>-<os>-<arch>.zip`**, for example
`pixelRef-v1.2.0-windows-x64.zip`. Rules the signer enforces:

- `<version>` equals `pixelref/__init__.py` `__version__` and the Git tag without the `v`.
- `<os>` is `windows`, `macos` or `linux`; `<arch>` is `x64` or `arm64`.
- A pre-release version (`1.3.0-beta.1`) must be signed with `--channel beta`, and a plain version
  with `--channel stable`.

`build_exe.py` writes `release-inventory.json` into the folder before zipping; installs use it to know which files belong to the release. Build and sign on each target OS separately; one manifest covers one archive.

## 4. Sign it

```bash
python packaging/sign_release.py sign \
    --private ~/pixelref-release-keys/release.key \
    --archive pixelRef-v1.2.0-windows-x64.zip \
    --release-id v1.2.0 \
    --sequence 1 \
    --channel stable \
    --valid-days 90 \
    --inventory \
    --out manifest-v1.2.0-windows-x64.json
```

| Option | Meaning |
|---|---|
| `--sequence` | A number that **must increase with every manifest you ever publish** (1, 2, 3…, across all platforms and channels). Installs refuse a manifest with a lower number than the last they accepted. Keep a note of the last one used. |
| `--valid-days` | How long the manifest is accepted (default 90). Shorter limits how long an attacker can replay old metadata, but you must re-sign before it lapses for the *current* release. |
| `--inventory` | Also signs a hash of every file in the ZIP. **Required for installable releases**: the update helper refuses a manifest without it. |
| `--private` | Path to the key. In CI you may set `PIXELREF_RELEASE_KEY_FILE` instead. |

It prints `signed … with key <id>`. The key id must equal the one from step 1. It refuses to write a
manifest the app would reject.

## 5. Check before publishing

Verify the manifest and archive the way a user's computer will:

```bash
python - <<'PY'
import base64, sys
from pathlib import Path
sys.path.insert(0, ".")
from pixelref.core.updates import Platform, Version
from pixelref.core.updates.verification import *

pub = base64.b64decode(Path("~/pixelref-release-keys/release.pub").expanduser().read_text())
m = verify_release(Path("manifest-v1.2.0-windows-x64.json").read_bytes(),
                   trust=TrustStore((TrustedKey(pub),)),
                   installed=Version(1, 1, 0), platform=Platform("windows", "x64"))
verify_artifact(Path("pixelRef-v1.2.0-windows-x64.zip"), m)
print("OK", m.version, m.sequence)
PY
```

Also run the new archive on a clean machine with no Python: extract, launch, open a board.

## 6. Publish on GitHub

1. Create the Git tag `v1.2.0` and a GitHub **Release** for it (tick *pre-release* for beta versions).
2. Attach **both** files: the ZIP and its `manifest-…json`. Keep the manifest's filename unchanged.
3. Write short release notes (users see them as plain text in the app).
4. Confirm the release page shows the assets, then download them again and repeat step 5 on the
   downloaded copies. Do not announce the release until this passes.

Note: today pixelRef only *tells* users a release exists; installing from it arrives in a later
milestone. Publishing now is safe.

## 7. Rotating or replacing the key

**Planned rotation** (the key is old, or a team member leaves):

1. Generate a new key (step 1, different filenames).
2. Ship a build whose `RELEASE_TRUST` lists **both** keys. Keep signing with the old key.
3. When most users run that build, start signing with the new key.
4. In a later build, remove the old key.

An individual key can also carry an end date: `TrustedKey(raw, expires="2027-06-30T00:00:00Z")`.

**Suspected compromise or lost key:**

1. Stop signing. Generate a new key.
2. In `RELEASE_TRUST`, list only the new key and add the old key id to `revoked=frozenset({"<old id>"})`.
3. Publish a new build and tell users, on the release page and wherever you announce releases, to
   **download it manually**. Builds already installed cannot learn about the revocation themselves.
4. Delete any manifests signed with the old key from the GitHub releases.

## Quick reference

- Never commit: `release.key`. `.gitignore` does not protect a key stored elsewhere, so keep it out of the repo tree.
- Always increase `--sequence`.
- Archive name = `pixelRef-v<version>-<os>-<arch>.zip`; tag = `v<version>`; code version = `<version>`.
- If signing or verification fails, **do not publish**. Fix the cause, never loosen the check.
