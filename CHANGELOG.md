# Changelog

All notable changes to pixelRef are documented here.

## [Unreleased]

### Added — Snap to grid

- **Snap to grid** (`snap` plugin) — `Alt+'`, *View → Background → Snap to
  Grid*, or the *Snap to grid* toggle (magnet icon) in the Attribute Editor's
  Canvas panel. Dragging snaps the selection's nearest edges to the background grid
  (its *Grid size*), moving a multi-selection as one block; dashed guides show
  the grid lines it snapped to. Zoomed far out, it snaps to the coarser lines
  the background draws there, so items always land on a visible line. Gizmo scaling puts the dragged handle on a grid
  line. Hold `Ctrl` while dragging to flip snapping for that drag.
- **Snap Selection to Grid** (*Transform* menu, `snap.to_grid`) lines each
  item up on the grid; works headless too.
- Plugins can add their own settings rows to the Canvas panel through the new
  `canvas_background.rows` extension point (`CanvasRow`).

## [1.1.0] - 2026-10-24

### Added — Files panel (multi-file management)

A new **Files** tab on the right-hand panel bar (next to the existing panels)
for switching between files deliberately opened in the current window,
Notepad++-style — not a recent-files list.

- **Per-window open-files registry** — only files opened in *this* window are
  listed (plugin opens, startup arguments, and session resume all register).
- **Fixed order, user-reorderable** — drag-and-drop rows to reorder; the order
  is preserved for the session.
- **Seamless switching with resume** — switching files captures the current
  file's scene text and camera state, so you resume exactly where you left off
  (including unsaved edits, which are a temporary state — no prompt on switch).
- **Unsaved-changes indicator** — files with unsaved changes get an orange
  border; the active file is highlighted with the accent colour.
- **Double-click preference** — *Settings → Open files on double-click*:
  **Current Window** (default) or **New Window** (spawns a separate pixelRef
  process).
- **Multi-file close prompt** — closing with unsaved work in several files
  shows a checkbox dialog to pick exactly which files to save; a single dirty
  file keeps the classic Save / Discard / Cancel prompt.
- **Right-click actions** — Open in New Window, Copy Path, Open Containing
  Folder.

### Fixed

- `QMimeData` import error on plugin activation (moved to `PySide6.QtCore`).
- `AttributeError: 'str' object has no attribute 'path'` in the Files panel's
  unsaved-state sync (new `open_files_state()` accessor returning
  `(path, unsaved)` pairs).
- Newly opened files not appearing in the Files panel (a `scene.opened` hook
  now registers every open path, covering plugin opens, startup args, and
  session resume).
- Test-suite hang: the multi-file close dialog blocked offscreen tests; the
  single-file path now uses the mockable `QMessageBox` prompt.

## [1.0.2]

- Previous release (see git history for details).
