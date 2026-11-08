# Update system: handoff

Written at commit `bb2d7fa` (2026-10-31); §5, §6 item 9 and §7 updated 2026-11-02 after the Windows rehearsal.
Read this first when you pick the work up again.
Source plan: "pixelRef — Portable-First Secure Update System", v1.0 (not in the repo; its decisions UPD-001…012
are reflected below).

## 1. Where things stand

**Shippable today**
- **Update notification.** Once a day (jittered) a background thread reads the public GitHub release list and
  shows *Update available* in the status bar. It downloads and changes nothing. *Check for Updates…* (canvas menu),
  *Update Settings…* (command search), channel stable/beta, off switch. Works with no key, no signing: publish
  ordinary releases named `pixelRef-v<version>-<os>-<arch>.zip`. Builds older than this (v1.1.0 and earlier) never
  show it.
- **Fully portable mode.** Opt-in `portable.toml` beside the exe moves settings (INI), config, plugins, recovery,
  cache, managed packages and the default boards folder into `user-data/`.

**Built and tested, but switched off**
- Signed manifests, verified download, safe staging, activation helper, health check, recovery, manual rollback,
  update dialog stages, rollback command, startup gate, release workflows, frozen helper bundled in the app.
- The switch: `ASSISTED_INSTALL_ENABLED = False` in `pixelref/core/updates/install.py`. Independently,
  `RELEASE_TRUST` in `verification.py` is empty, so nothing can verify. Both must change before it works.

**Not ready to enable.** The Windows rehearsal (§5) passed, but the real key, the GitHub pipeline and code signing
have not been done. See §5 (what has never been seen) and §7 (ordered next steps).

## 2. Commits (at the time of writing all were local and unpushed; they have since been pushed and merged to `main`)

| Commit | What |
|---|---|
| `0487a4f` | M1 discovery: semver, GitHub release check, status-bar note, preferences |
| `960e58b` | M2 authenticity: Ed25519-signed canonical manifests, `packaging/sign_release.py`, `docs/update-security.md` |
| `0c61e94` | Release signing runbook (`docs/update-release-process.md`) |
| `6802813` | M3 verified download and safe staging |
| `868fb5d` | M4 activation helper (journal, inventory, ownership check, renames), flag off |
| `7994fbc` | M5 health check, recovery, bounded retries, manual rollback |
| `75cf696` | PR-09 CI + release workflows, `packaging/verify_release.py` |
| `8d55d0a` | PR-08 app integration (dialog stages, Update & Restart, rollback command, startup gate) |
| `89dd00e` | PR-01 portable mode |
| `e2042c8` | Status/go-live docs, user-guide "Updates" section |
| `b652088` | Step 4: helper frozen (`helper.spec`) and bundled; build smoke step |
| `bb2d7fa` | Restore CRLF line endings (see §6, item 2) |

## 3. How it fits together

```
app (running)                          helper (separate frozen app, copied to .pixelref-update/helper/<id>/)
 discovery ─ GitHub release list
 manifest + archive download ─ verify signature ─ stage (updater-owned folder) ─ preflight
 "Update & Restart": start helper (it only waits) ─ normal close flow (unsaved prompts)
                                       └─ if cancelled: stop helper, nothing changed
                                                     helper: lock ─ wait for app exit ─ re-verify everything from disk
                                                     ─ ownership check ─ journal ACTIVATING ─ rename old entries to backup/<id>,
                                                       new entries into place ─ journal PENDING_HEALTH_CHECK ─ launch new app
 new app: ~2 s after start: acknowledge_health(token) ─► journal COMMITTED, sequence recorded, old backups pruned
                                                     helper: no ack / exit / timeout ─► terminate child, roll back by renames,
                                                       relaunch previous version
```

Install layout: `<root>/.pixelref-update/{staging,backup,helper}/<transaction-id>/`, `journal.json`, `lock`,
`accepted.json` (last accepted metadata sequence), `installation-id`.

## 4. Code map

| Path | Purpose |
|---|---|
| `pixelref/core/updates/versions.py` `models.py` `discovery.py` `preferences.py` | M1: semver, models, GitHub discovery (finds the zip and its `manifest-…json`), schedule/settings |
| `…/verification.py` | M2: canonical bytes, trust store (`RELEASE_TRUST`, empty), `verify_release`, `verify_artifact` |
| `…/staging.py` | M3: allowlisted download, archive inspection, extraction, `validate_staged`, sequence counter, cleanup |
| `…/inventory.py` | `release-inventory.json` (written by `build_exe.py`): which files a release owns |
| `…/journal.py` | State machine, strict validation, durable writes, `journal_guard` lock. Stores **no paths** (by design) |
| `…/install.py` | M4: flag, lock, `plan_activation` (preflight), `activate`, `rollback`, `run_activation` (waits for health) |
| `…/recovery.py` | M5: `begin_startup`, `acknowledge_health`, `recover`, `plan_manual_rollback`, `rollback_previous` |
| `…/flow.py` | App side without Qt: `assist_status`, `prepare_update`, `launch_helper`, `startup_gate`, `last_update_result` |
| `pixelref/core/paths.py` | Portable mode resolution (+ `application_root`, shared with the updater) |
| `pixelref/builtins/updates/` | The UI plugin: status-bar note, dialog, settings, commands, startup ack/notice |
| `pixelref/__main__.py` | Calls `startup_gate()` for frozen builds before any UI |
| `packaging/updater/helper_main.py` `helper.spec` | The helper: commands `activate <tx>`, `recover`, `rollback`; exit 3 = disabled |
| `packaging/sign_release.py` `verify_release.py` `build_exe.py` `pixelref.spec` | Sign, verify, build (helper first), bundle |
| `.github/workflows/ci.yml` `release.yml` | CI (no secrets); release: build → sign (protected env) → draft → re-verify → publish |
| `docs/update-architecture.md` `update-security.md` `update-release-process.md` | Behaviour, threat model and per-milestone detail, maintainer runbook + go-live checklist |
| `tests/test_update*.py`, `test_workflows.py`, `test_portable.py` | 93 tests (counts: updates 9, verification 12, staging 13, install 14, recovery 14, flow 16, packaging 2, workflows 5, portable 8) |

## 5. What has been verified, and what has not

**Verified (automated, on Linux)**
- All 93 tests above pass (last run at `bb2d7fa`; the set also passed together with 10 neighbouring tests).
- Failure injection before every filesystem step restores the install exactly and loses no file; simulated power
  loss at every step loses nothing and recovers (also when recovery is itself interrupted and repeated).
- Hostile archives, forged journals and transaction ids, tampered staged files, wrong/expired/revoked keys,
  replay and downgrade are rejected. User files (`portable.toml`, `user-data/`, scripts) are never touched.
- The sign and verify steps of `release.yml` were run locally against a throwaway key. The frozen helper was built
  with PyInstaller on Linux, bundled, and run (in place and from an install-shaped folder).

**Verified on Windows 10 (2026-11-02, Python 3.14, throwaway key, test builds with the flag on)**
- The 95 update, portable and workflow tests pass on Windows (97 with the two new ones below).
- `build_exe.py` is **green on Windows**: the app, the frozen `pixelref-updater.exe`, and all six smoke steps,
  including 5/6 (the helper). Image formats tiff/webp are present there.
- **A real update with the real frozen helper:** beta.1 running, then verify, download, stage and preflight, then the
  helper waits for the app to exit, renames, the new app starts and acknowledges, and the journal reaches
  `COMMITTED`. `user-data/`, `portable.toml` and `scripts/` are byte-identical afterwards.
- **A new version that crashes at start** (`os._exit(1)` before the acknowledgment): the health check fails, the old
  version is restored (same exe hash) and relaunched, the journal says `ROLLED_BACK`, and the user is told why.
- **A file held open inside `_internal/`** (like an antivirus scan): the rename is refused with `WinError 5`, the
  update is undone, the install still starts, user files are untouched, and (after fix 2 below) the previous
  version is relaunched.
- **Manual rollback, then roll forward**: both `COMMITTED`, the exe hash alternates correctly, user files unchanged.
- **Helper killed mid-update** (`taskkill /F` at 15 moments across the 2.3 to 3.0 s window; the frozen helper takes
  about 2.5 s to start, so earlier kills change nothing): kills before the first rename left beta.1 whole; kills
  during `ACTIVATING` and `PENDING_HEALTH_CHECK` (a half-renamed install) were all recovered by the normal start-up
  gate to a whole beta.1 with `ROLLED_BACK`. No mixed install, no lost user file. A coarser 14-point sweep from 0
  to 4 s agreed.
- Found and fixed by this rehearsal (uncommitted when this was written; see §6, item 9):
  1. `os.replace` of `journal.json` / `accepted.json` fails with `PermissionError` on Windows while another process
     has the file open (the helper polls the journal while the new app commits). 43 of 300 writes failed under a
     polling reader; 0 of 300 after the retry (`journal.replace_file`).
  2. After an update was undone (rename refused), the helper left the user with **no app running**. It now
     relaunches the previous version (`install.run_activation`), as the health-check rollback already did.

**Never seen (do not claim these)**
1. **The GitHub workflows.** Parsed and statically tested only (`tests/test_workflows.py`). `actions/*` steps,
   the Windows CI build and `gh release` have never run. Actions are pinned to major versions (`@v4`), not SHAs.
2. **A removable drive pulled mid-update**, **power loss** on real hardware, and **antivirus** other than a plain
   held file handle. The kill test approximates the process dying, not the disk.
3. **The dialog and rollback UI by eye.** Button visibility and flows are tested with injected helpers and close
   handlers, not driven by hand. (The rehearsal drove the real flow functions and the frozen helper, not the dialog.)
4. **A real release key and a real GitHub release.** Everything above used a throwaway key and local files.
5. Authenticode signing of the executables: not done.
6. Any platform other than Windows x64.

## 6. Things to know

1. **Four tests already failed before this work** (and still do), plus one flaky: `test_effects`
   (`test_heavy_effects_run_in_the_background_and_are_kept_on_disk`,
   `test_processed_effects_are_saved_in_the_scene_and_reused`, `test_slider_drags_process_only_the_last_value`: flaky),
   `test_plugin_security.test_the_activity_monitor_reports_undeclared_activity`, and
   `test_docs.test_mcp_scene_tools_read_a_board_without_running_it` (needs pytest's `tmp_path`; the custom runner
   doesn't provide it). CI runs the full suite as non-blocking for this reason (`ci.yml`); remove
   `continue-on-error` once fixed. Full suite last run: 581/586 (before step 4), ~11 minutes.
2. **CRLF mix-up, fixed but visible in history.** Seven pre-existing files use CRLF (`ui/app.py`, `ui/__init__.py`,
   `builtins/file/__init__.py`, `builtins/effects/ui.py`, `packaging/build_exe.py`, `packaging/pixelref.spec`,
   `docs/packaging.md`). My edit scripts converted them to LF inside commits `89dd00e`, `868fb5d`, `b652088`;
   `bb2d7fa` restored CRLF. The net diff against the original is clean, but `git blame` for those files is noisy.
   Before the history was pushed one could have rewritten local history so those seven files only show real changes
   (e.g. an interactive rebase or `git filter-branch`/`git filter-repo` that re-applies CRLF). It is your call; I
   did not do it. **When editing these files, use an editor/tool that preserves line endings, or write in binary
   mode.** New files I added are LF.
3. **Test isolation.** `tests/harness.py` sets `PIXELREF_DISABLE_UPDATE_CHECK=1` so no test touches the network.
   Tests that flip `install.ASSISTED_INSTALL_ENABLED` must restore it (see `Env.close` in `test_update_flow.py`).
   `QStandardPaths` test mode and an INI settings default are set by the harness; portable tests use
   `paths.set_application_root_for_tests`.
4. **Discovery is on by default** (`updates/check_enabled`). It sends no user data, but it does contact GitHub
   after ~8 s; the docs say so. Disable with the setting or `PIXELREF_DISABLE_UPDATE_CHECK=1`.
5. **A user who starts a pending update by hand** (no token) gets two normal starts, then startup asks to recover
   and the update is rolled back. Documented in `update-security.md`.
6. **Helper size and independence.** The helper imports `pixelref.core.updates` (and so `pixelref.core`'s package
   `__init__`, which pulls in the session code, Qt-free). It is ~29 MB on Linux. Slimming it (split the update code
   out of `pixelref.core`) is optional future work; `tests/test_update_packaging.py` guards that it stays Qt-free.
7. **Sequence numbers** must only increase. `release.yml` uses `run_number + vars.PIXELREF_SEQUENCE_OFFSET`.
   If you sign anything by hand with a higher number, set the offset variable.
8. A throwaway venv with PyInstaller was used for experiments in the session scratchpad and is gone; recreate with
   `pip install -r packaging/requirements-build.txt`.
9. **How the Windows rehearsal was done**, to repeat it: clone the repo to a scratch folder; in the clone only, set
   `ASSISTED_INSTALL_ENABLED = True`, embed a throwaway public key in `RELEASE_TRUST`, set `__version__` to
   `1.2.0-beta.N`, and (because smoke step 5/6 asserts "disabled") accept exit 2 / "not inside" there. Build three
   versions with `build_exe.py` (a crashing one: `os._exit(1)` at the top of `acknowledge_startup`), sign the
   later two with `sign_release.py sign --inventory --channel beta` (sequences 1, 2). Extract beta.1 into an
   install folder with a `portable.toml`, `user-data/` and `scripts/`, run it, call `flow.prepare_update` with an
   opener that serves the local files and `flow.launch_helper(..., bundle=<install>/_internal/updater)`, close the
   app, wait for the helper to exit and read the journal **afterwards** (a Python reader holding `journal.json`
   open is exactly what triggered the replace race). `startup_gate` plus the helper's `recover` command exercise
   the kill-mid-update cases. The scripts lived in a session scratchpad and are not in the repo.

## 7. Next steps, in order

Do not skip ahead; each depends on the one before. (The same list is in `docs/update-release-process.md`.)

1. **Generate the release key (yours to do, offline)** and embed the public key. Exact commands:
   `docs/update-release-process.md` steps 1–2. Back the private key up twice; never commit it.
2. **GitHub setup:** environment `release` with required reviewers and the secret `PIXELREF_RELEASE_KEY`
   (environment-level, not repository-level), tag protection for `v*`, read-only default workflow permissions.
3. **First pipeline run with a pre-release tag** (`v1.2.0-beta.1`, after bumping `__version__` to match). Expect
   small fixes. This is also the first Windows build of the app *and* of `pixelref-updater.exe`; confirm
   `build_exe.py` is green there (step 5/6 is the helper check) and that the draft passes `verify_release.py`.
4. **Pin actions to commit SHAs** (needs network) and enable Dependabot for Actions.
5. **Real Windows test: largely done on a developer machine (see §5).** Still to do on a build from the real
   pipeline: the same list with the real key, plus a removable drive and real antivirus. The original list: with a
   test build that has the flag on and a test key: update from one beta to the next; a
   deliberately broken new version (health check must roll back and restart the old one); manual rollback and
   roll-forward; a file held open by another program or antivirus; a USB stick removed mid-update; confirm
   `user-data/`, plugins and scripts are untouched. Fix whatever differs from the simulations in
   `tests/test_update_recovery.py`.
6. **Authenticode-sign** the Windows executables (including `pixelref-updater.exe`).
7. Flip `ASSISTED_INSTALL_ENABLED = True` in a commit of its own, release it as a beta first, keep the notify-only
   path as the fallback.
8. Afterwards (not required for the first release): fix or quarantine the pre-existing failing tests and drop
   `continue-on-error` in CI; automatic download (the setting exists but is disabled); a real Settings page;
   macOS/Linux layouts (`REQUIRED_LAYOUT` and `HELPER_EXE` only know Windows); TUF if more publishers join.

## 8. Decisions made, and deviations from the plan

- **Signed canonical manifests, not TUF.** Documented limits in `update-security.md` (no threshold keys, no
  snapshot/freeze protection beyond `expires_at`, local anti-rollback counter only). Revisit if the project grows.
- **Journal stores no paths** (the plan listed directory fields); folders are derived from the transaction id, and
  operations name single top-level entries, so a forged journal cannot aim recovery elsewhere.
- **Signed file inventory is required for installation.** Sign with `--inventory`; the helper refuses otherwise.
- **Replacement unit is the top-level entry** (`_internal/`, each exe), via same-filesystem renames. Never described
  as atomic; the previous version stays whole in `backup/<id>/`.
- **Sequence is recorded only on a healthy start** (commit), not when staging.
- **Rollback restores program files only**, never boards/settings/plugins/scripts; the UI warns. A failed manual
  rollback is undone and can be retried; rolling forward is the same operation.
- **Helper before close, undone if cancelled** (spawn failure closes nothing; cancelling the unsaved-board prompt
  stops the waiting helper).
- **Settings is a dialog, not a page** (the app has no settings window); *Check for Updates…* is in the canvas
  menu (above Getting Started), not a separate Help menu.
- **Plan items not done:** automatic download, macOS/Linux support, Authenticode, SHA-pinned actions, Windows
  verification (§5).

## 9. Invariants to keep (they are what the tests protect)

- Fail closed: any verification, staging or preflight problem blocks installation.
- Never delete or overwrite a file the release doesn't own; unknown files block the update instead.
- The app never replaces its own files; only the helper does, after the app has exited.
- The helper trusts nothing the app computed: it re-verifies from disk.
- A source checkout (`.git`, `launch_pixelRef.py`) is never updated.
- The app must start normally if the updater is missing, broken or offline (`startup_gate` never raises).
- Never put a private key, or any `${{ }}` of untrusted input, into a workflow script.
