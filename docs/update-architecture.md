# Updates

pixelRef can tell you when a newer version exists. **In the app today that is all it does**: it never
downloads, installs or changes any file. Release verification (M2) and verified download and staging
(M3) exist as tested library code (see [update-security.md](update-security.md)) but are not connected
to the UI; installation (M4–M5) is not built and stays off until its recovery tests pass.

## What runs now (M1: discovery)

- About once a day (with jitter) a background thread reads the public release list of
  `AnhTuWally/pixelRef` on GitHub. The request is a plain HTTPS GET: no user identity, document
  names or board content, and a generic `User-Agent`.
- When a newer eligible release exists the status bar shows **Update available: x.y.z**. Click it
  for the notes, a link to the release page, **Later** and **Skip This Version**.
- **Help → Check for Updates…** checks now, even if automatic checks are off.
  **Update Settings…** (command search) holds the automatic-check switch, the Stable/Beta channel and
  the last successful check.
- Failures are reported as what they are (offline, rate-limited, unreadable answer). They are
  never shown as "up to date", never interrupt your work and never open a dialog by themselves.
- `PIXELREF_DISABLE_UPDATE_CHECK=1` disables the automatic check. The app works the same with no
  network, or with the Updates plugin disabled in the Plugins window.

## Rules

- Versions are compared as semantic versions (`1.10.0` > `1.9.0`; `1.0.0-beta.2` < `1.0.0`).
- Stable users see stable releases only. Beta users also see pre-releases.
- A release that publishes `pixelRef-v<version>-<os>-<arch>.zip` archives but none for this
  computer is skipped. A release with no archives is offered as a link only.
- A copy running from source (`python -m pixelref`) is discovery-only. Nothing ever rewrites it.
- Release metadata from GitHub is untrusted: only `https://github.com` / `api.github.com` URLs are
  accepted, notes are shown as plain text, and sizes and counts are bounded.

## Code

`pixelref/core/updates/` is Qt-free (`versions`, `models`, `discovery`, `preferences`); the
plugin `pixelref/builtins/updates/` is the UI. Settings keys: `updates/check_enabled`,
`updates/check_interval`, `updates/channel`, `updates/auto_download`,
`updates/last_successful_check`, `updates/last_attempt`, `updates/dismissed_version`. They are
conveniences, never security or transaction state.
