# Changelog

All notable changes to pixelRef are documented here.

## [Unreleased]

### Added — Persistent Files list (in progress)

- **Restore Previous Workspace**: the Files list, its order and each file's last view are saved and
  come back on launch. Only the active board loads; the rest stay dormant until selected. Design and
  phases: [docs/design-persistent-workspace.md](docs/design-persistent-workspace.md).
- Startup restore now has three modes (`workspace`, `list`, `empty`). *Resume Last Session on Launch*
  maps to `workspace`; the default is unchanged.
- Only the first pixelRef window saves the workspace; windows from *Open in New Window* and a second
  launch never overwrite it.

- **Close files**: a small × on each Files row, plus Close, Close Others and Close All in the row's
  right-click menu and the File menu (`Ctrl+Shift+W` closes the current file). Closing only removes a
  file from the Files list (it stays in Recent Files); unsaved boards are asked about first, with
  one dialog for several, and Cancel or a failed save closes nothing.

- **Colour labels**: right-click a file in the Files panel > *Set Colour* (blue, green, yellow, orange,
  purple, pink) or *Remove Colour*; also in command search (*Colour File: ...*). A narrow strip on the
  row shows it, separate from the active highlight and the unsaved border. Labels are remembered per
  file path in your settings (never inside a `.pxref`), so they survive closing, reopening and restarts.

- **Lighter parked boards**: switching away from a board with no unsaved changes no longer serializes
  it or keeps a copy in memory (with embedded photos that was the whole file, per board); it reloads
  from disk when selected. Boards with unsaved changes still keep theirs. Switching between files in
  the list no longer reorders Recent Files.

- **Missing files**: a listed file that no longer exists (unplugged drive, moved folder) shows a warning
  sign and a muted name, stays in the list and in the saved workspace, and never blocks startup. Existence
  is checked in the background and not more than every 30 seconds. Selecting one says so quietly instead
  of showing an error. Right-click > *Locate File...* points the entry at the file's new location, keeping
  its place and colour.
- The start screen marks recent files that are **already open**; clicking one switches to it instead of
  opening it again.

- **Unsaved work in every open board is protected.** Each board with a file keeps its own recovery
  copy, so a board you switched away from with unsaved edits survives a crash too (before, only the
  board on screen did). After a crash, one dialog offers every board with recoverable work. With
  Session Resume on, quitting no longer asks about such boards: they are kept and offered back at the
  next launch. A file that changed on disk since is flagged and is never overwritten by a restore;
  an unreadable copy is set aside, not deleted.

### Fixed

- The *Resume Last Session on Launch* checkbox on the start panel is now always shown, so the setting
  can be found (and switched off) from the very first launch; it used to appear only after a session
  that had something on the board.
- Closing and reopening pixelRef brought back the open files only if you had found and ticked *Resume
  Last Session on Launch*. Restoring is now on by default (list, order, colours and the board you were
  on; only that board loads). If you had turned it off yourself it stays off. Because the board on screen
  is kept for you, closing with unsaved work no longer asks to save: it is offered back next launch.
- Clicking another file in the Files panel (or *Next Open File*, or a start-screen card for an open
  file) while on an Untitled board with work in it replaced the board without asking. It now asks
  Save / Discard / Cancel first.
- Memory no longer climbs every time you switch boards or open another file: decoded images of boards
  you had left stayed in memory (about 14 MB per switch with big photos, 1.4 GB after 90 switches in a
  test). With ten large boards it now stays around 240 MB.
- Quitting with Session Resume on silently dropped unsaved edits in boards parked in the Files list.
  It now asks (the current board is still kept by Session Resume without a prompt).

### Added — Fully portable mode

- A `portable.toml` beside `pixelRef.exe` keeps settings, plugins, recovery files, caches, managed
  Python packages and the default boards folder in `user-data/` next to the program. Opt-in; see
  [docs/packaging.md](docs/packaging.md#fully-portable-mode).

### Added — Update discovery

- **Quiet update check**: about once a day pixelRef reads GitHub's public release
  list in the background and shows *Update available* in the status bar when a
  newer version exists. *Check for Updates…* (canvas menu) checks now;
  *Update Settings…* (command search) sets the channel and turns checks off.
  Discovery only: nothing is downloaded or installed. See
  [docs/update-architecture.md](docs/update-architecture.md).

### Added — Tags and Find

- **Find panel** (`Ctrl+F`, *Find…* in the canvas menu): searches names, tags,
  types, notes, metadata and text as you type. `Enter` goes to the highlighted
  item (gliding; `Alt+Left` comes back), `Shift+Enter` selects every match. Query
  syntax: words, `"phrases"`, `#tag` (`#a,b` either, `#sky*` wildcards), `type:`,
  `name:`, `note:`, `key:value` (metadata), `-term`. With the field empty it lists
  the board's tags. *Find Similar (Same Tags)* in the canvas menu.
- **Tags section** in the Attribute Editor: tag chips with autocomplete, for one
  item or a whole selection (partial tags show a count and a **+**). Clicking a
  tag finds everything carrying it.
- Kernel commands `add_tags`, `remove_tags`, `set_tags` (one undo step each,
  touching only nodes that change) and `list_tags`; `find.search` (alias `find`),
  `find.select`, `find.locate`, `find.similar`, `find.show`; the `find` service
  (a headless search index); `pixelref.plugin_api.clean_tags`.

### Fixed

- The quick editor's *Tags* field, with several items selected, replaced every
  item's tags with the first item's. It now adds and removes only what you changed.

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
