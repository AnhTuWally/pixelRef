# Changelog

All notable changes to pixelRef are documented here.

## [Unreleased]

### Added — Finding the other end of a link

- Off-screen linked elements of the selection get a **chip on the view's edge**
  pointing at them. Click it to glide there.
- **Double-click a link** to go to its far end, or a selected link's end handle
  to go to the other end.
- **Linked To** sub-menu in the canvas menu, a **Links** section in the
  Attribute Editor, and a **link-count badge** on selected and hovered elements.
  Each lists the elements at the other ends; pick one to go there.
- **Hover highlight** (Select tool): hovering an element outlines its links and
  what they lead to. Toggle it with *View → Highlight Links on Hover*.
- Jumps are remembered per view: **`Alt+Left` / `Alt+Right`** (*View → Go
  Back / Go Forward*) restore the camera and the selection.
- Commands: `links.go_to`, `links.follow`, `links.back`, `links.forward`,
  `links.set_hover_highlight`; context key `links.connected`.

### Added — Extract Color Palette

- **Extract Color Palette** (`color_palette` plugin; *Image* menu, `Ctrl+K`,
  `color_palette.extract(image=None, count=None)`) turns an image's dominant colors into
  a backdrop of rectangle swatches linked to the image. Extracting again updates the
  palette linked to that image; a palette whose link was deleted is ignored and a new one
  is made. Runs only on request, one undo step, works headless. See the user guide.

### Added — Editable PNG exports

- PNG exports embed the exported part of the board (a `.pxref` document, linked
  images included) in a private `pxRF` chunk, so dropping the PNG onto a board
  rebuilds the editable objects at the drop point as one undo step. `Shift`+drop
  imports the flat picture; ordinary PNGs are unaffected. Embedded images can make
  exports much larger: *File → Embed Editable Scene in PNG Exports* (setting
  `embed_scene_metadata`, on by default) turns it off, which gives the
  existing flattened export. `export.render` gained `embed_scene`.
- `import_scene` and the new merge code keep links, image references and click
  actions pointing at the copies when ids collide.

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
