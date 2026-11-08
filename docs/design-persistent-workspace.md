# Design notes: persistent Files and workspace restoration

Status: **implemented, phases 1-5** (revised after auditing the code, 2026-11-01); phase 6
(cross-platform and portable verification) is still open. See the end of this file for limits. Supersedes the
"Session restore" rows of [design-multi-file.md](design-multi-file.md).

## Goal

The Files panel survives a restart: the open files, their order, colour labels and the
active board come back, but only the active board is loaded. Individual files can be
closed without leaving Recent Files. Unsaved work is never lost silently.

## Facts about the code that shape the design

| Fact | Consequence |
| --- | --- |
| A "window" is a separate OS process (`Open in New Window` spawns `python -m pixelref path`). The open-files registry is a per-process local. QSettings and the recovery folder are shared by all processes. | Persisted workspace state needs an explicit owner process. |
| Recovery is one slot (`recovery.pxref` + `recovery.json`) for the current document only. | Per-document recovery is a separate, later piece of work (Phase 5b). |
| Inactive boards live only as `OpenFile.text` in memory. | They need a quit prompt (Phase 1) until they have recovery snapshots. |
| With Session Resume on, closing skipped `confirm_close_all()`, so dirty inactive boards were dropped silently. | **Fixed in Phase 1**: resume mode now prompts, but only when dirty inactive boards exist. |
| `_capture_state` serializes the scene on every switch, and re-adds a missing registry entry. | Clean files skip serialization; closing a file must remove the entry *before* switching. |
| Untitled boards cannot be inactive (`_capture_state` ignores them). | Untitled is supported only as the active board, via the existing recovery slot. |
| `open_path` calls `add_recent`. | Restoring must not reorder Recent Files. |

## Decisions

1. **Startup preference** replaces the `session_resume` boolean with three modes,
   setting key `workspace_restore`:
   - `workspace`: Files list + order + colours; load only the active board.
   - `list`: restore the Files list, load nothing.
   - `empty`: start with no open files.

   Migration: the old checkbox, if the user ever set it, decides (`True` becomes `workspace`, `False`
   becomes `empty`); a profile that never chose gets `workspace.DEFAULT_MODE`, which is `workspace`. (An
   earlier build kept `empty` as the default, and reopening pixelRef then brought nothing back until
   the user found the checkbox; that was reversed.) The old key is left in place so downgrades keep working.
   The *Resume Last Session on Launch* menu item and start-panel checkbox toggle between
   `workspace` and `empty`.
2. **Ownership.** The first pixelRef process owns the manifest. Child processes spawned by
   *Open in New Window* receive `PIXELREF_WORKSPACE_CHILD=1` and never read or write it. A
   second independently launched process finds the manifest owned (a lock file holding the
   owner's PID, checked for liveness) and runs with an empty workspace, without persisting.
3. **Manifest.** `workspace_manifest` (JSON via `settings.set_json`, so portable mode just works):
   `{"version": 1, "active": <path|null>, "untitled": <bool>, "files": [{"path": ..., "camera": ...}]}`.
   Paths only, no scene data. Colours are **not** in the manifest: they live in one global
   path-keyed map (`file_colors`) so a colour survives closing and reopening a file.
4. **Quit prompt.** Dirty inactive boards always get the save/discard/cancel dialog on quit, in
   every restore mode. The active board keeps its current protection (recovery slot in resume mode).
5. **Untitled**: active board only, recorded as `"untitled": true`, backed by the recovery slot.

## Phases

| Phase | Content | Gate |
| --- | --- | --- |
| 1 | Quit-prompt fix; `confirm_close_all` takes a subset; `OpenFile` becomes dormant-capable; manifest write/read; restore list + active board; restore mode setting + migration; ownership lock | Files list survives restart; only the active board loads; no silent loss on quit |
| 2 | `close`, `close_others`, `close_all` commands; panel close button + context menu | Closing preserves every data-safety guarantee |
| 3 | Colour labels: global map, strip in the row, palette menu | Colours persist across restart and reopen |
| 4 | Skip serialization for clean files; evict clean text; benchmarks | Startup and memory stay flat with many boards |
| 5a | Missing-file indicator, *Locate File*, *Already open* marker on the start screen | Missing files never block startup |
| 5b | Multi-document recovery snapshots (separate project) | No crash path silently loses unsaved work |
| 6 | Docs, changelog, cross-platform and portable checks | Production-ready |

## Non-goals

Named workspace profiles, cloud sync, a second file lifecycle, a cache-eviction framework, a new
start screen. Multiple parked Untitled boards (needs Phase 5b).

## Phase 4 measurements (2026-11-01, Windows, one machine, noisy)

`python tests/bench_workspace.py`: boards of embedded noise photos, every board visited in turn.
`EVICT_CLEAN` (in `builtins/file/__init__.py`) switches the new policy off.

| Boards | Scene text retained (keep all -> evict clean) | Resident memory | Restore: scenes loaded |
| --- | --- | --- | --- |
| 10 x 2 images (1600x1000) | 122 MB -> 0 | 1061 -> 1075 MB | 1 of 10 |
| 30 x 2 images (1200x800) | 220 MB -> 0 | 1905 -> 1975 MB | 1 of 30 |

- Restoring a workspace loads exactly one scene however many files are listed.
- The retained text is gone. Total memory did not fall at first, because of a separate leak (see below).
- Switch time swung 2x between identical runs, so the measured difference between the policies is
  inside the noise; a switch to a parked clean board now reads the file from disk.

### Memory grew with every board switch: found and fixed

Resident memory climbed about 14 MB for every switch (1.4 GB after 90 switches of ten boards with two
1600x1000 photos, against 205 MB just after opening them), whatever the keep/evict setting.
- **Cause:** `image/source.py`'s `PictureStore` (the shared decoded-image budget, 1 GB by default) held
  strong references to image handles. Switching boards clears the image cache while decodes are still
  running on worker threads; each late result was delivered into its now-forgotten handle, which the store
  then kept alive for good, with its pixels and the embedded file bytes its reader closure holds.
- **Fix:** the store refers to handles weakly and drops the levels of handles that no longer exist.
- **Measured** (`tests/bench_workspace.py`, 9 rounds, event loop emulated): resident memory
  1376 / 1317 MB before (keep all / evict clean) and **239 / 169 MB after, flat across rounds**.
- With the leak gone, evicting clean boards does save memory (169 vs 239 MB) at the price of reading the
  file when switching (about 280 ms against 150 ms median here), so `EVICT_CLEAN` stays on.
- A false lead worth knowing: a benchmark without a running event loop never runs `deleteLater()`, so
  widgets pile up and look like a leak. The benchmark now flushes deferred deletes after each switch.

## Phase 5a (missing files, start screen)

Done: background availability checks (30 s throttle, forced checks queue a re-run), warning row state,
quiet failure when selecting a missing file, *Locate File...* (`file.relocate`), the "Already open" marker
on start-screen cards. Phase 5b (per-document recovery snapshots) is still to do.

## Phase 5b design: per-document recovery (built; decisions: quit option A, one crash dialog)

### Today

- One slot (`recovery/recovery.pxref` + `recovery.json`), written every 60 s for the *current* board only.
- A board parked with unsaved edits exists only as `OpenFile.text` in memory: a crash loses it. Phase 1 made
  quitting ask about such boards; nothing protects them from a crash.
- Switching boards does not clear or re-key the slot, so after a switch the slot can still describe the
  board you left (the slot's `scene_file` says which, but only one board can ever be offered back).

### Proposal

1. **One snapshot per saved board.** `recovery/docs/<id>.pxref` + `<id>.json`, `id` = hash of the
   normalised path. The current board autosaves to its own snapshot; the legacy slot stays for the
   Untitled board (and is still read, so nothing already on disk is orphaned).
2. **Parking writes a snapshot.** When a dirty board is parked, its already-serialized text is written
   atomically (temp file + replace) on a worker thread, and the entry is marked *committed* when it lands.
3. **Metadata** per snapshot: path, saved_at, item count, the original file's mtime and size at capture,
   writer pid and token, and a `clean` flag (written deliberately at quit). Corrupt or unreadable snapshots
   are validated before being offered, never deleted, and moved aside.
4. **Removal** only when the board is saved, or its edits are explicitly discarded (close, quit, or
   declining recovery). A missing, corrupt or stale snapshot never overwrites the original file.
5. **Crash recovery.** At launch every snapshot whose writer is no longer running is collected into
   **one** dialog listing the boards (checkbox each, "Restore selected" / "Discard all"). A board whose
   file changed on disk since the snapshot is marked *"file changed since"*: it is still restorable, as
   unsaved work, and saving goes through the existing external-change confirmation.
   Restored boards come back as parked dirty entries; the previously active one opens.
6. **Several processes.** Snapshots written by a process that is still running belong to that window and
   are skipped. Two windows editing the same file contend for one snapshot: the later writer wins and
   the first is told nothing, so this stays a documented limit.
7. **Not in this phase:** evicting a dirty board's text from memory once its snapshot is committed. It
   would save memory for dirty boards but adds a second load path; the committed flag is there for it.

### Decisions needed

- **Quit in Restore-Workspace mode.** Option A (recommended): parked dirty boards are snapshotted with
  `clean=True` and come back as unsaved work next launch with one "Recover unsaved changes?" dialog,
  exactly how Session Resume already treats the current board; no quit prompt. Option B: keep today's
  Save / Discard / Cancel prompt and use snapshots only for crashes.
- **Crash dialog:** one dialog for all boards (recommended) vs one prompt per board.

## Summary of architectural decisions

| Decision | Why |
| --- | --- |
| The registry stays the only source of open-file membership; the manifest only mirrors it | One owner of the list, no second file-management system |
| The saved workspace holds paths, order and a view only | Cheap to read and write, no scene data, safe to lose |
| Only the first process owns the saved workspace (lock file with pid and token) | Windows spawned by *Open in New Window* and a second launch can never overwrite it |
| Restored entries are dormant (`OpenFile.text is None`); only the active board loads | Startup cost does not grow with the number of files |
| Colour labels live in one global path-keyed map, not in the manifest or the file | A label survives close/reopen; membership and colour stay independent |
| Clean parked boards keep no text; dirty ones keep theirs plus a snapshot on disk | A dirty board is the only holder of its edits |
| One recovery snapshot per board, written atomically; the single slot stays for Untitled | Every open board is protected, and nothing an older version left behind is orphaned |
| Restoring never writes to the original file; unreadable copies are set aside, not deleted | Recovery can fail safe |
| The decoded-image store refers to handles weakly | A forgotten handle must not pin a whole board's pixels (the memory leak) |

## Known limitations and remaining risks

- **Two windows editing the same file** share one recovery snapshot; the later writer wins and neither is
  told. Rare, and limited to unsaved work in that one file.
- **Untitled boards** are supported only as the active board (kept in the single recovery slot); they cannot
  be parked. Leaving a dirty Untitled board for a file in the Files list now asks Save / Discard / Cancel
  (it used to replace the board silently), and discarding also removes its recovery copy.
- **Dirty boards keep their text in memory** even though a committed snapshot exists. Dropping it (the
  `committed` set is there for it) would save memory but needs a second load path.
- **Switching to a clean board reads the file** (about twice the time of restoring captured text on the
  benchmark machine). `EVICT_CLEAN = False` trades memory back for speed.
- **Missing-file detection** is by existence checks every 30 s on a worker thread; a file that is replaced
  by a different one at the same path is not noticed until it is opened.
- **Colour labels** from a window that is open at the same time as another labelling a file can be
  overwritten (each write re-reads first, but the two writes are not locked).
- **Not verified**: Linux and macOS, a portable build running the whole flow end to end, and behaviour with
  hundreds of open files (benchmarked to 30 boards).
- **`test_effects.py::test_slider_drags_process_only_the_last_value`** used to fail about half the time on a
  slower machine: its four simulated drag steps took as long as the 40 ms debounce window, which starts at
  the first request and does not restart. The test now pins a long window (the product behaviour is unchanged).
- **Restoring is on by default**, so closing with unsaved work in the current board no longer shows a
  save prompt (the work is kept and offered back next launch). Anyone who turned the checkbox off keeps it off.
