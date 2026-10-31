# Updates

pixelRef can tell you when a newer version exists. **In the app today that is all it does**: it never
downloads, installs or changes any file. Release verification (M2) and verified download and staging
(M3) exist as tested library code (see [update-security.md](update-security.md)) but are not connected
to the UI; the activation helper (M4) exists but is disabled behind a build flag. Health checks, crash recovery and manual rollback (M5) exist as tested library code. Nothing is enabled: the real-Windows and manual upgrade/rollback gates have not been run, and the app doesn't call any of it yet.

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

## Installing from inside pixelRef (PR-08, off in this build)

The update dialog can offer **Download**, then **Update & Restart**, but only when every condition in
`flow.assist_status` holds: the feature flag `install.ASSISTED_INSTALL_ENABLED` is on, this is a standalone
copy (never source), a release key is embedded, the update helper is bundled in the app, the platform is
supported (Windows x64) and pixelRef's folder is writable. Today the flag is off and the helper is not
bundled, so the dialog offers exactly what it did before: the notes and a link to download by hand. The
reason is shown in the dialog and under *Update Settings…*.

With everything enabled the stages are:

1. **Available**: notes (plain text), *Download*, *Later*, *Skip This Version*, and the manual link, always.
2. **Downloading**: progress and *Cancel Download*; the UI stays responsive (a worker thread). The signed
   manifest is fetched first and verified, then the archive is downloaded, verified, staged and
   *preflighted*, which includes the ownership check. A conflict (an unknown file in a program folder)
   is reported in words and nothing is left staged.
3. **Ready**: shown only after all of that succeeded. *Update & Restart*.
4. **Update & Restart**: re-runs the preflight, starts the helper (which only waits for pixelRef to exit),
   then runs the app's normal close flow, including the unsaved-board prompts. If you cancel that prompt the
   helper is stopped and nothing has changed. The app never closes or restarts by itself.
5. **After the restart**: the new version acknowledges itself (commit) about two seconds after start. A
   one-time message says it was installed, or that it did not work and the previous version was restored.

**Roll Back to Previous Version…** (command search) restores the previous release from the backup the
last update kept. It warns that only the program is rolled back, never boards, settings, plugins or scripts.

**At every start** a frozen app first calls `flow.startup_gate`, before any UI. With no unfinished update it
does nothing. With an update awaiting acknowledgment it lets the app start (twice at most); an interrupted
or repeatedly failing one hands over to the helper's `recover` and exits. If no helper is available it starts
normally and shows how to recover by hand. It never raises: a broken updater cannot stop pixelRef starting.
