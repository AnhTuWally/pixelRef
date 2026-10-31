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
controls the network or GitHub, not one who controls the machine. The counter's storage lands with
staging (M3). A *user-requested* rollback to a locally kept, previously verified version is a separate
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
