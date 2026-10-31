# Update security: release authenticity (M2)

## Decision: signed canonical manifests, not TUF

pixelRef authenticates releases with **Ed25519-signed canonical JSON manifests**. We chose this over
a TUF client (python-tuf / tufup) because it is small enough to review and test completely, reuses the
`cryptography` package the app already ships, and needs no repository-metadata hosting. We give up
what TUF adds, and **this scheme must not be described as TUF**:

| TUF protection | Here |
|---|---|
| Offline root keys, role separation, threshold signing | One online-or-offline release key (several keys allowed for rotation); no thresholds |
| Key-compromise recovery with a root-key chain | Manual: ship a new build embedding a new key (see below) |
| Snapshot / consistent-snapshot metadata | None: one manifest per archive |
| Freeze and mix-and-match resistance | Expiry (`expires_at`) bounds freezes; no mix-and-match defence |
| Rollback resistance | A per-install `sequence` counter, local and best-effort |

Revisit TUF if the project gains more publishers, or needs compromise recovery without a manual
re-download.

## Threat model

- **Untrusted:** GitHub's API answers, release assets, CDN and network, release notes, everything in
  the portable folder.
- **Trusted:** the release public key embedded in the app (`RELEASE_TRUST` in
  `pixelref/core/updates/verification.py`) and the code that checks it.
- **Not defended:** a local attacker who can edit the app's own files or settings can also swap the
  embedded key, the counter or the updater. Application-level checks cannot stop them, and we claim
  nothing stronger. A compromised release key lets an attacker sign malicious releases until builds
  with a new key reach users.

## The signed manifest

An envelope `{"signed": {...}, "signatures": [{"keyid", "sig"}]}`. The signature covers the exact bytes
`b"pixelref-update-manifest-v1\n"` + JSON of `signed` with sorted keys, no whitespace, ASCII escapes,
UTF-8. Only objects, arrays, strings, booleans and integers are allowed (no floats or null, which
different JSON libraries encode differently). The prefix means a signature made for anything else,
such as a plugin, never verifies here. `keyid` is the SHA-256 of the raw public key.

Signed fields: `schema_version`, `release_id`, `sequence`, `version`, `channel`, `platform`,
`architecture`, `artifact_name`, `artifact_size`, `sha256`, `package_format`, `published_at`,
`expires_at`, `min_updater_version`, and an optional `files` inventory (`path`, `size`, `sha256`).

- **Unknown fields are rejected.** Nothing outside the signature can influence installation. The schema
  has no field for a destination path, URL or command, so a manifest cannot authorize one.
- `artifact_name` must be `pixelRef-v<version>-<os>-<arch>.zip` and agree with `version`, `platform`
  and `architecture`; the channel must agree with the version (pre-releases are beta).
- Inventory paths must be forward-slash relative paths with no `..`, `.`, empty, drive, UNC, control or
  Windows-reserved parts, and no case-insensitive duplicates.

## What `verify_release` checks (fail closed, in this order)

1. A trusted, unrevoked, unexpired key signed the exact canonical bytes. With no trusted key, nothing
   verifies.
2. The schema is valid.
3. `expires_at` is in the future; `published_at` is not (10 minutes of clock skew allowed).
4. Platform and architecture match this computer.
5. The channel is eligible (stable users never get beta).
6. `min_updater_version` is not newer than this build's updater.
7. The version is **newer than the installed one**. An older release is never installed through this
   path, even with a valid signature.
8. `sequence` is not lower than the last accepted one (replay of superseded metadata).

`verify_artifact` then requires the archive's size and SHA-256 to equal the signed values exactly.

## Rollback and replay: what is and isn't protected

Network metadata is only accepted if newer than the installed version and not older (by `sequence`)
than the last accepted manifest. Both comparisons use local state, so they stop an attacker who only
controls the network or GitHub, not one who controls the machine. The counter is stored in
`.pixelref-update/accepted.json` (written atomically, never lowered; a corrupt file reads as 0). A *user-requested* rollback to a locally kept, previously verified version is a separate
path (M5) and does not go through this check. Freeze attacks (serving old, still-valid metadata) are
limited only by the `expires_at` window; use short validity (the tool defaults to 90 days).

## Key lifecycle

- The release key is **separate from plugin-publisher keys** and from any other key.
- Generate it offline with `python packaging/sign_release.py generate-key`; the private key file is
  created owner-only and must never be committed, bundled or logged. CI signs only in a protected
  environment, from protected tags, never for pull requests (M7).
- **Embedding:** paste the public key into `RELEASE_TRUST`. It is empty today, so **assisted updates
  cannot verify anything until a key is generated and embedded**.
- **Rotation:** ship a build whose `RELEASE_TRUST` lists old and new keys; sign with the new key once
  that build is common; later drop the old key. An optional per-key `expires` ends trust automatically.
- **Compromise:** add the key id to `revoked`, ship a build with a new key, and tell users to download
  it manually from the release page. Older builds cannot learn of the revocation by themselves: that
  is the main thing TUF would improve.

## Publishing

`python packaging/sign_release.py sign --archive … --release-id … --sequence N --channel stable --out …`
builds the manifest from the archive's real size and hash, refuses to emit anything the app would
reject, and signs it. Increase `--sequence` with every published manifest.

Signing manifests does not replace OS code signing of the executables (Windows Authenticode, macOS
notarization); do both.

## Tests

`tests/test_update_verification.py`: tampering with each signed field, wrong, missing, revoked and
expired keys, rotation, platform/arch/channel/expiry/updater/version checks, replay and downgrade,
strict schema, hostile inventory paths, archive hash and size, JSON edge cases, and a round trip
through the publisher tool.

## Download and staging (M3)

`pixelref/core/updates/staging.py`. Staging prepares a complete new version in
`<install>/.pixelref-update/staging/<transaction-id>/` and **never touches the live installation**.

- **Download:** HTTPS only, hosts `github.com`, `objects.githubusercontent.com`,
  `release-assets.githubusercontent.com`, no embedded credentials. Every redirect hop is re-checked;
  TLS certificates are verified by Python's default context. Reads stop at the signed size, then the
  exact size and SHA-256 must match the signed manifest. The URL itself is untrusted: the signed hash
  is what authorizes the bytes.
- **Inspection before extraction:** rejects absolute, `..`, backslash, drive and UNC paths; symlinks and
  other non-regular entries; encrypted entries; duplicate or case-colliding names; a file sharing a path
  with a folder; Windows-reserved names; anything outside `pixelRef/`; the user-owned names
  `portable.toml`, `user-data` and `.pixelref-update`; more than 20,000 entries or 2 GiB expanded. With
  a signed file inventory, the archive must match it exactly.
- **Extraction:** into a folder the call creates, with exclusive-create writes (never following or
  reusing an existing path), each path re-resolved to stay inside it, bytes counted against the declared
  size and per-file hashes checked against the inventory.
- **Layout:** Windows requires `pixelRef.exe`, `pixelref-cli.exe` and `_internal/`. Other platforms
  refuse to stage until their layout is defined.
- **Space and permissions:** free space is checked, and a probe file proves the folder is writable.
- **Failure and cancel:** the whole transaction folder is removed. Cleanup only deletes
  transaction-id-named plain folders (never symlinks' targets or unknown names).
- `validate_staged` re-checks a staged transaction independently (marker, archive hash, layout, every
  inventoried file) so the helper doesn't have to trust the process that staged it.

Not yet done: the transaction journal (M5), and the helper that activates a staged version (M4).
`stage_update` is not connected to any UI; nothing downloads automatically.

## Activation by the helper (M4)

`pixelref/core/updates/install.py`, `inventory.py`, `journal.py` and `packaging/updater/helper_main.py`.

**Disabled.** `ASSISTED_INSTALL_ENABLED` is `False`, the helper exits with code 3 (except `recover`, which only
undoes moves), and nothing in the app calls any of this. It may only be switched on after the recovery milestone's fault-injection tests and a
manual upgrade/rollback demonstration pass on the target platform.

- **Independent helper.** It runs from a copy in `.pixelref-update/helper/<transaction>/`, outside the
  files being replaced, and *derives* the install root from its own location. Its only operation is
  `activate <transaction-id> [--wait-pid N …]`: no source, destination or executable arguments.
  Everything is re-read from disk and re-verified: the signed manifest (same checks as the app, trust
  store from the helper's own build), the staged package and every inventoried file, and the install.
- **One transaction per install.** An exclusive lock file (a dead owner's lock is replaced); an earlier
  unfinished journal blocks a new transaction.
- **Never closes the app.** The helper only waits for the app's process ids to exit. If they don't
  exit in time it changes nothing. (The app's own close flow, with its unsaved-board prompts, is
  what asks the user; that UI is a later PR.)
- **Ownership.** `release-inventory.json` (written by `build_exe.py`) lists the files of the installed
  release. A top-level entry may be replaced or removed only if every file in it is listed *and* still has
  its listed hash. An unknown file inside such a folder, a modified or missing installed file, a link, or
  an unknown entry where the release wants to put something **blocks the update** and lists what to
  resolve. `portable.toml`, `user-data/` and all other unknown top-level entries are never touched.
  The inventory is local and unsigned (a local attacker can edit it); a new release's inventory is
  covered by the signed manifest.
- **Source checkouts** (`.git` or `launch_pixelRef.py` present) are refused.
- **Manifest inventory is required.** Installation needs the signed per-file inventory (publish with
  `sign_release.py --inventory`), so staged files can be checked individually.
- **Activation is not atomic.** Replacing several top-level entries is a sequence of same-filesystem
  renames, each journaled: old entry to `backup/<id>/`, new entry into place. Nothing is copied or
  deleted, so the previous version stays complete in the backup. On an ordinary error the moves are undone
  by inspecting the real filesystem (idempotent; safe to repeat after an interruption).
- **Journal.** `journal.json` follows the plan's state machine with validated transitions, atomic durable
  writes and strict loading. It never stores a path: staging and backup folders are derived from the
  transaction id and operations name single top-level entries, so a forged journal cannot aim recovery
  at another location. (This differs from the plan's schema, which listed directory fields.)

### Tested

`tests/test_update_install.py`: success path with the user's files byte-identical; failure injected
before every filesystem step restores the tree exactly and loses no file; simulated power loss at every
step loses nothing and the leftover journal rolls back (also when the rollback is itself interrupted,
and when repeated); each ownership conflict; source checkout and missing inventory; bad key, expiry,
tampered staged file, replayed sequence; forged transaction ids and journals; lock handling; waiting for
the app; helper-root derivation.

### Not done / not verified

- **Not tested on Windows.** File-lock behaviour (a rename failing because a DLL is open), the Windows
  process-liveness check and detached launch are written but only the POSIX paths ran. The helper has not
  been built as a frozen executable (`helper.spec` does not exist yet), and the helper would currently pull
  in `pixelref.core` as a whole; slimming that is future work.
- The helper is copied from the installed app: it is as trustworthy as the install itself.
- macOS bundles and Linux layouts are not defined.

## Health checks, recovery and rollback (M5)

`pixelref/core/updates/recovery.py` and the helper's `recover` / `rollback` commands. Still disabled with
the flag above (`recover` aside), and still not wired to the app.

**Health protocol.** After activation the helper launches the new app with a one-time token in
`PIXELREF_STARTUP_TOKEN` and waits. The new app calls `acknowledge_health(root, token)` after its essential
initialization; that commits the journal (`COMMITTED`), records the accepted metadata `sequence`, and prunes
old backups, helper copies and staging, keeping only this update's backup (the previous version). If the app
exits first, or does not acknowledge before the timeout (default 120 s), the helper terminates its own
unhealthy child, rolls back, and starts the previous version again. The acknowledgment and the timeout are
serialized by a short journal lock, so a late acknowledgment and a rollback can never both win. The sequence
is therefore only accepted for updates that actually started.

**Never automatic after a healthy start.** A `COMMITTED` update is not rolled back because the user quit,
and `recover` ignores it.

**Crash recovery.** `begin_startup(root)` (to be called first thing at app start) returns `normal`, `pending`
(carry on and acknowledge), `recover` (run the helper's `recover` instead of starting) or `manual`.
`recover` restores an interrupted activation, a failed health check or an interrupted rollback by inspecting
the real filesystem, only renaming: it is idempotent, can itself be interrupted and repeated, and never
deletes a file. It refuses while the app is still running, and refuses a damaged journal (a person must look).

**No loops.** A pending update gets 2 launches; after that startup says `recover`. A rollback that keeps
failing (a file still locked, a drive missing) is retried up to 3 times, after which the journal becomes
`RECOVERY_REQUIRED` and stays there: nothing is changed further, the files stay where they are, and the
message names the folder holding the previous version.

**Manual rollback.** `rollback_previous` (helper command `rollback`) restores the previous release from the
backup through the same journaled mechanism. Preflight requires: every file of the previous release is in
the backup with its recorded hash, and the current release is unmodified and has no unknown files in its
folders. The release being replaced is kept as the new backup, so rolling forward is the same operation. It
is the user's decision, so the "newer than installed" rule for network metadata does not apply. A rollback
that fails part-way is undone and can be retried.

**Data policy.** Rollback restores program files only. Boards (`.pxref`), settings, plugins and scripts are
never reverted with the program, and a newer version may have changed them (scene or settings formats may
not be readable by the older version). The UI that offers rollback must say so; it does not exist yet.

### Tested

`tests/test_update_recovery.py`: healthy commit and pruning; silent, crashing and wrong-token starts roll back
and relaunch the previous version, without accepting the sequence; the acknowledgment/timeout race;
bounded startup attempts; recovery after power loss at every step, itself interrupted and repeated;
recovery refusing while the app runs and with a damaged journal; Windows-style locked files and a vanishing
drive, injected into the renames during activation and during rollback (nothing lost, retried, then
`RECOVERY_REQUIRED`); manual rollback and roll-forward; rollback blocked by a tampered or missing backup,
an added user file or no committed update; a failed rollback undone at every step and then retried; the
journal lock; the helper's `recover` command.

### Still not done

- **No real Windows run.** The locks are simulated with injected `PermissionError`/`OSError`. Real
  behaviour (antivirus holding a file, an open DLL, `os.rename` on directories) needs a manual test.
- **The manual upgrade/rollback demonstration** the release gate requires has not been done: no frozen
  helper exists, and no release key is embedded.
- The app does not call `begin_startup` or `acknowledge_health` yet (PR-08), and shows no recovery or
  rollback result UI.
- A user who starts a pending update by hand (no token) gets 2 starts before startup asks to recover; the
  update then cannot be acknowledged and is rolled back.
- Same-account local attackers can edit the journal, inventory or trust key: unchanged limitation.

## App integration (PR-08)

`pixelref/core/updates/flow.py` and the `updates` plugin. See [update-architecture.md](update-architecture.md)
for the user-visible flow. Security-relevant points:

- **The app never replaces files.** It downloads into the update folder, stages, preflights and starts the
  helper. The helper's command line is fixed (`<helper> activate <transaction> --wait-pid <pid>`,
  `recover`, `rollback`); `launch_helper` rejects any other combination and a malformed transaction id.
- **Helper before close, undone if cancelled.** The helper is started first (so a spawn failure closes
  nothing), then the normal close flow runs. If the user cancels it, the helper (which has only been
  waiting) is terminated. It cannot act while pixelRef is still running.
- **Everything is re-verified.** The app verifies the manifest and archive, and preflights, but the helper
  repeats all of it from disk and trusts nothing the app computed. The app's preflight is only to give
  an early, readable answer.
- **Gated.** Installing is offered only when the flag, install mode, release key, bundled helper, platform
  and writability all allow it (`assist_status`). Any failure falls back to notify-and-link.
- **The startup token** is read from the environment and removed at once, so nothing the app launches
  inherits it.
- **No automatic action.** Nothing downloads without a click (auto-download stays unavailable), nothing
  closes without the close flow, and no dialog opens by itself except the one-time result of a failed or
  stuck update.

### Not done

- The helper **is now built and bundled** (`packaging/updater/helper.spec`, `_internal/updater/`), and the build
  fails without it. What stands between this and enabling the flag is a Windows build, the manual upgrade and
  rollback demonstration on Windows, and an embedded release key. In a build without a key, or with the flag
  off, `assist_status` still reports installing unavailable.
- Automatic download, a settings page, and a progress/Result UI for the helper phase itself (the helper
  runs without a window; the user only sees the app close and reopen).

## The frozen helper (step 4)

- **Independent by construction.** Its own PyInstaller one-folder app (own Python, own libraries). It imports
  only `pixelref.core.updates` and two light shared modules; a test fails if any of that starts importing Qt, pip,
  plugins or the UI.
- **Checked on every build.** `build_exe.py` runs the bundled helper in place (outside an install it must refuse
  `activate` with exit 3 while installing is disabled) and from an install-shaped folder (it must derive its root
  from its location and `recover` must report nothing to do, with a scrubbed environment).
- **Verified on Linux, not on Windows.** The frozen helper was built, bundled and run on Linux; a missing helper
  is caught. The Linux app build then failed an unrelated image-format check of the existing smoke test (the
  distro's Qt has no tiff/webp plugins), so a full green `build_exe.py` run has **not** been seen on any platform
  in this work. Check the first Windows build.
- **Not exercised frozen:** a real activation. The flag is compiled in as off, the release key is empty, and
  staging only supports Windows layouts, so the frozen helper can't be driven through an update on Linux.
- Release inventory covers the helper's files like any other program file (71 files checked), so an update
  replaces them as part of `_internal/`.
