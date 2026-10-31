# Release signing runbook

Step-by-step instructions for the maintainer. Background and threat model: [update-security.md](update-security.md).

Until step 1 and 2 are done, **no update can be verified** (the app ships with no trusted release key
and fails closed). Update *discovery* works without them.

## Status and go-live checklist

*Last updated with PR-01, PR-08 and PR-09.*

| Capability | State | Safe to ship? |
|---|---|---|
| **Update notification** (status-bar note, *Check for Updates…*, channel, off switch) | Built, tested, on by default | **Yes.** It only reads GitHub's public release list; it downloads and changes nothing. |
| Fully portable mode (`portable.toml`) | Built, tested, opt-in | **Yes.** Nothing changes without the file. |
| Signed manifests, verified download, staging | Built, tested | Library only: nothing is reachable until installing is enabled. |
| Activation helper (frozen, bundled in the app), health check, recovery, rollback | Built, tested with injected faults; frozen helper run on Linux only | **No.** Disabled by `ASSISTED_INSTALL_ENABLED = False`. |
| Release workflows (`.github/workflows`) | Written, parsed, partly dry-run locally | **Never run on GitHub.** Try with a `v…-beta.N` tag first. |

**What users get today:** a quiet "Update available" note and a link to download the new ZIP by hand. There
is no in-app install. Releases need no signing for that: you can publish ordinary GitHub releases
(`pixelRef-v<version>-<os>-<arch>.zip`) and the note appears for users on a build that has the Updates plugin.

**Older builds (v1.1.0 and earlier) never show the note.** They need a one-time manual ZIP replacement.

### Before in-app installing can be turned on

All of these, in order. Do not skip one because a later one passed.

1. **Release key.** Generate it and embed the public key (steps 1 and 2 below). Without it nothing can verify.
2. **GitHub setup.** The `release` environment with required reviewers and the `PIXELREF_RELEASE_KEY`
   secret, tag protection for `v*`, read-only default token (see *Automated releases*).
3. **First pipeline run** with a pre-release tag (`v1.2.0-beta.1`). Fix whatever the first real run shows,
   and confirm the draft's files pass `packaging/verify_release.py`.
4. **Update helper: built and bundled; Windows build not yet seen.** `packaging/updater/helper.spec` builds
   `dist/pixelref-updater/` (its own Python and `cryptography`, no Qt), `pixelref.spec` bundles it as plain files in
   `_internal/updater/`, and `build_exe.py` builds the helper first and fails the build if it is missing or
   doesn't run (smoke step 5/6). On Linux the frozen helper was built, bundled and run (see *Verified*); the Windows
   `.exe` has not been built, so the first Windows build (CI or by hand) is the check. `assist_status` still reports
   installing unavailable until the flag is on and a release key is embedded.
5. **Test on a real Windows machine**, from a v1.2.0-beta build to a newer beta, with the flag on in a test
   build: update, a deliberately failing new version (the health check must roll back and restart the
   old one), manual rollback, an open file or antivirus holding a file, a USB stick removed mid-update, and
   `user-data/` plus your own plugins and scripts untouched afterwards. Only simulated file locks have been
   run so far.
6. **Code-sign the Windows executables** (Authenticode). Not done; SmartScreen and antivirus distrust
   unsigned builds regardless of the manifest signature.
7. Only then set `ASSISTED_INSTALL_ENABLED = True` in a commit of its own, and ship it in a beta first.

Until step 7, **everything above step 1 is optional for a normal release**: ship releases as before and the
notification does the rest.

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
python packaging/verify_release.py --manifest manifest-v1.2.0-windows-x64.json \
    --archive pixelRef-v1.2.0-windows-x64.zip --public ~/pixelref-release-keys/release.pub --installed 1.1.0
```

Leave out `--public` once the key is embedded: it then checks against the key users have. It exits 0 only if
the signature, schema, expiry, size, SHA-256 and file inventory all check out.

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

## Automated releases (GitHub Actions)

`.github/workflows/` holds `ci.yml` (tests on pull requests and `main`; no secrets, read-only) and
`release.yml` (builds, signs and publishes when you push a `v*` tag). The manual steps above still work and
are the fallback.

### One-time setup (in GitHub, not in the repo)

1. Do steps 1 and 2 above (generate the key, embed the **public** key, commit). Releases fail closed
   without an embedded key.
2. **Settings → Environments → New environment `release`.** Add *required reviewers* (you), and restrict
   it to protected tags if offered. Add the secret **`PIXELREF_RELEASE_KEY`** *to this environment*, not to
   the repository: paste the entire contents of `release.key` (the PEM). Only the `sign` job uses the
   environment, so pull requests and the other jobs can never read it.
3. **Settings → Rules → Tag protection / rulesets:** protect the pattern `v*` so only maintainers can
   create those tags (pushing a tag starts a release).
4. **Settings → Actions → General:** set workflow permissions to *read* by default, and require approval
   for workflows from outside collaborators.
5. Optional: a repository variable `PIXELREF_SEQUENCE_OFFSET` if you ever signed a manifest by hand with a
   higher sequence number than the workflow's run number (the sequence must only increase).

### What happens on `git tag v1.2.0 && git push origin v1.2.0`

1. **build** (Windows x64, no secrets): checks the tag equals `pixelref.__version__`, runs the update tests,
   builds, smoke-tests the *built* executables (`build_exe.py`) and zips the folder with its inventory.
2. **sign** (waits for your approval of the `release` environment): signs a manifest with
   `--inventory`, using the run number (plus offset) as the sequence, `beta` if the tag has a `-`. It then
   verifies the result with the key embedded in that commit, which is what users have.
3. **publish**: creates a **draft** release with the ZIP and manifest, downloads them back *from GitHub*,
   verifies them again with a job that has no secrets, and only then publishes. If verification fails the
   draft stays unpublished for you to delete.

`tests/test_workflows.py` checks the safety properties statically: no `pull_request_target`, a read-only
default token, the key referenced only by the `sign` job (inside the `release` environment), write access
only in `publish`, no `${{ }}` inside scripts, and draft → verify → publish order.

### Not done / unverified

- **These workflows have never run.** I had no GitHub access: the YAML is parsed and the sign/verify shell
  steps were executed locally against a throwaway key and archive, but `actions/*` steps, the Windows build
  in CI and `gh release` are untested. Expect to fix small things on the first (pre-release) run: push a
  `v…-beta.N` tag first.
- **Actions are pinned to major versions** (`@v4`), not commit SHAs. Pin them to SHAs (and enable
  Dependabot for Actions) when you have network access.
- **Windows executables are not code-signed** (Authenticode). Signing the manifest does not replace it;
  SmartScreen and antivirus will still distrust unsigned builds.
- Only **Windows x64** is built. macOS and Linux need their own layouts defined first.
- The full test suite is non-blocking in CI until the four tests that already failed are fixed.
