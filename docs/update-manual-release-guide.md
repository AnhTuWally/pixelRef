# Manual release guide: build, sign, publish, and the road to auto-update

A hands-on companion to the [release signing runbook](update-release-process.md). It covers releasing **by hand on
Windows (PowerShell)** and what has to happen before pixelRef can update itself. Threat model:
[update-security.md](update-security.md). How the updater behaves: [update-architecture.md](update-architecture.md).

## Where things stand

| | State |
|---|---|
| Release key | Generated; the **public** key is embedded in `RELEASE_TRUST` (`pixelref/core/updates/verification.py`). The private key lives only with the maintainer. |
| Update notification | On by default. Builds from 1.2.0 show "Update available" and a download link. |
| In-app install | **Off.** `install.ASSISTED_INSTALL_ENABLED = False`. Nothing is downloaded or changed by the app. |
| GitHub workflows | Written, never run. Releases are made by hand with this guide. |
| Authenticode (exe) signing | Not done. |

Signing a release is safe to do before in-app install exists: until the flag is on, the signature is unused by
shipped builds, so a mistake cannot harm users. Do it anyway, because the first release that carries the key is
also the first that can ever verify a later one.

## What the signature is

Each release is two files:

- `pixelRef-v<version>-windows-x64.zip`, the program folder.
- `manifest-v<version>-windows-x64.json`, signed with Ed25519. It records the ZIP's size and SHA-256, the version,
  channel, an expiry, a sequence number and (with `--inventory`) a hash of every file in the ZIP.

The app embeds only the public key. Verification fails closed: any problem means "do not install".

## One-time: the key

```powershell
mkdir $env:USERPROFILE\pixelref-release-keys; cd $env:USERPROFILE\pixelref-release-keys
python D:\pixelRef\packaging\sign_release.py generate-key --private release.key --public release.pub
```

- Do it **outside the repository**. Back `release.key` up to two places. Never commit, email or paste it.
- Paste only the public key (44 base64 characters) into `RELEASE_TRUST`, keep the printed key id in the comment
  beside it, run `python tests/run_tests.py test_update_verification`, and commit. This must be in the commit you
  tag, because the build embeds it.
- Changing or revoking a key later is a rotation: see the runbook's section 7.

## Each release

### 1. Set the version

Edit `__version__` in `pixelref/__init__.py`, commit, and make sure `git status` is clean. The zip name, the tag
(`v<version>`) and the channel all follow from it: a version containing `-` (for example `1.3.0-beta.1`) is
`beta`, anything else is `stable`.

### 2. Test and build

```powershell
cd D:\pixelRef
.\.venv\Scripts\python.exe -m pip install -r packaging\requirements-build.txt   # once
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe tests\run_tests.py test_update test_workflows        # every line must PASS
.\.venv\Scripts\python.exe packaging\build_exe.py
```

`build_exe.py` builds the update helper (`dist\pixelref-updater\`), then the app (`dist\pixelRef\`, bundling the
helper under `_internal\updater\`), **smoke-tests the built executables** (plugins load, bundled pip, a package
install, plugin signing, the helper runs), writes `release-inventory.json` into the folder and zips it to
`dist\pixelRef-v<version>-windows-x64.zip`. It fails if any smoke step fails.

Always **rebuild after changing the embedded key or any code**: an older zip in `dist\` carries whatever was
embedded when it was built. Extract the zip somewhere else, launch `pixelRef.exe` and open a board. Do not modify
the build after this point; the signature covers exact bytes.

### 3. Sign

```powershell
$key = "C:\path\to\your\release.key"
.\.venv\Scripts\python.exe packaging\sign_release.py sign `
  --private $key `
  --archive dist\pixelRef-v1.2.0-windows-x64.zip `
  --release-id v1.2.0 `
  --sequence 1 `
  --channel stable `
  --valid-days 90 `
  --inventory `
  --out dist\manifest-v1.2.0-windows-x64.json
```

| Option | Meaning |
|---|---|
| `--sequence` | Must be higher than **every manifest you have ever published**, across channels and platforms. Installs refuse a lower number than the last they accepted. Keep a record. Last used: **1** (v1.2.0). |
| `--channel` | `stable`, or `beta` for a version containing `-`. |
| `--valid-days` | How long the manifest is accepted (default 90). Re-sign the *current* release before it lapses. |
| `--inventory` | Also signs every file's hash. **Required**: the update helper refuses a manifest without it. |

It prints `signed … with key <id>`. The id must equal the id of the key embedded in `verification.py`; if not, stop.

### 4. Verify the way a user's computer will

```powershell
.\.venv\Scripts\python.exe packaging\verify_release.py `
  --manifest dist\manifest-v1.2.0-windows-x64.json `
  --archive dist\pixelRef-v1.2.0-windows-x64.zip
echo "exit: $LASTEXITCODE"
```

Without `--public` it checks against the **embedded** key. Success prints
`OK: <zip> <version> (sequence N, <files> files, expires <date>)` and exits 0. That is not an error. If it fails,
do not publish; fix the cause and never loosen a check.

### 5. Publish

```powershell
git tag v1.2.0
git push origin v1.2.0
```

On GitHub: **Releases → Draft a new release**, choose the tag (tick *pre-release* for a beta version), attach
**both** the zip and the manifest (keep the manifest's filename unchanged), add short plain-text notes, and save as
a **draft**. Download both files back from the draft into a new folder and run `verify_release.py` on those copies.
Click **Publish release** only if the exit code is 0.

## What users see

| Installed | What happens |
|---|---|
| 1.1.0 and earlier | No update check at all. They replace the ZIP by hand once. |
| 1.2.0 and later | Quiet "Update available" note and a link to download the ZIP. Nothing is installed. |

A release is only visible as an update to builds that have the Updates plugin and are on a lower version.

## Road to auto-update

Whether a build can install updates is **compiled into that build** (`ASSISTED_INSTALL_ENABLED`). A new release
cannot switch it on for copies already installed. So:

1. Releases up to and including 1.2.0 can never install an update, whatever you publish after them.
2. The first release built with the flag on (say 1.3.0) must be installed by hand once.
3. From that version on, users see Download, then **Update & Restart**, for the next signed release.

Before flipping the flag (docs/update-release-process.md has the full checklist):

1. A real key is embedded and a signed release exists. **Done with 1.2.0.**
2. **Rehearse on Windows with the real key and a build you signed**, from one version to a newer one: a normal
   update, a deliberately failing new version (the health check must roll back and restart the old one), manual
   rollback, an open file or antivirus holding a file, a removable drive pulled mid-update, and `user-data/`, plugins
   and scripts untouched afterwards. The earlier rehearsal used a throwaway key.
3. **Authenticode-sign the executables.** The manifest signature does not stop SmartScreen or antivirus distrusting
   unsigned builds, and a blocked helper means no update.
4. Set `ASSISTED_INSTALL_ENABLED = True` in **a commit of its own** so it is easy to revert, and ship it as a
   **beta first** (`1.3.0-beta.1`, channel `beta`, next sequence number). This is the point where a beta is worth it,
   because a bad build can then rewrite users' installs.
5. Test the beta-to-beta update on a machine, then promote to stable.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `PyInstaller is missing` | `pip install -r packaging\requirements-build.txt`. |
| A smoke step fails | The message names the step; do not zip around it. |
| Antivirus quarantines `dist\` files | Common with unsigned PyInstaller output; exclude `dist\`. |
| Signing refuses to write | The signer checks the manifest against the app's own rules: version vs tag, channel, filename. |
| Verify: signature mismatch | The private key does not pair with the embedded public key. |
| Wrong key id printed when signing | Wrong private key. Stop. |
| Update test flakiness | The UI tests wait up to 30 s for the background download. A slower machine may need more. |
| Manifest about to expire | Re-sign the same archive with a higher `--sequence` and replace the manifest on the release. |

## Quick reference

- Archive: `pixelRef-v<version>-<os>-<arch>.zip`; tag: `v<version>`; code: `__version__ = "<version>"`.
- Sequence only goes up. Never commit `release.key`.
- Never publish if verification fails.
- Last sequence used: **1** (v1.2.0, stable). Update this line when you sign again.
