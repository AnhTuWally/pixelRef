# Changelog

All notable changes to pixelRef are documented here.

## [Unreleased]

### Added — Name labels

- New **Name Labels** plugin (`name_labels`): a **label button** on the
  selected image, movie or sequence (or `Shift+N`, or *Show Name Label*)
  shows a caption with the item's name in its bottom-left corner. It is an
  ordinary text note parented to the item, so it can be restyled, and saving,
  undo and copy/paste need nothing new.
- Hiding a label only hides it: showing it again brings back its edited text,
  style and position. `Delete` removes it.
- Labels are **locked to the corner** by default: they can be selected but not
  dragged, and they move to the new corner when the item is cropped or
  resized. The **lock button** beside a selected label (or *Unlock Label
  Position*) lets you drag it, and clicking it again snaps it back.
- A new label's text is 5% of the item's height, so it scales with the
  item. Any resizing you do to it is kept. Labels follow renames (and a duplicate's new name) until their
  text is edited by hand. *Reset Label* restores the name.
- **Show on new items** (Canvas settings in the Attribute Editor, a
  preference for all boards): every image, movie or sequence you import, drop
  or paste gets a label straight away. Images, movies and sequences now emit
  a `media.added` scene event when created from a file or pasted data.
- `name_labels.*` commands: `show`, `hide`, `toggle`, `remove`, `pin`,
  `unpin`, `toggle_pin`, `reset`, `labels_of`, `label_new`, `set_label_new`.

## [1.4.0] - 2026-11-08

### Added — Colour management (OCIO)

- New **Colour Management (OCIO)** plugin (`ocio`): EXR images and EXR
  sequences can be shown through an OpenColorIO **display / view** (and an
  optional **look**) instead of the plain sRGB curve, so renders look as they
  do in Nuke, RV or the renderer's viewer.
- Config from **`$OCIO`** (the board then starts in OCIO mode), else OCIO's
  built-in **ACES CG** config; the built-in *ACES Studio* config can be
  picked, or any `.ocio` / `.ocioz` file opened with a file dialog
  (**Browse…** in the Colour panel, *File → Colour Management → Open OCIO
  Config…*); invalid files are refused. *Reload OCIO Config* re-reads an
  edited one.
- **Colour** panel in the Attribute Editor with nothing selected: mode
  (built-in sRGB / OCIO), config, display, view, look, the board's default
  input colour space, and **Save with this board**, which stores the settings
  in the scene (`plugins.ocio`, undoable) instead of your preferences. A
  board whose config is missing falls back with a warning.
- Per-image **Input** colour space (`colorspace` attribute) in the *EXR View*
  section; *Auto* uses the board default, then the config's file rules, then
  its `scene_linear` role. Exposure applies before OCIO, gamma after; alpha is
  shown raw.
- `ocio.*` commands: `set`, `save_with_board`, `toggle`, `info`, `displays`,
  `colorspaces`, `looks`, `reload`, `pick_config`, `install_support`;
  `exr.set_view(colorspace=…)`.
- The `opencolorio` and numpy packages are optional (*Image → Install OCIO
  Support…*); without them, or in built-in mode (the default without
  `$OCIO`), EXRs look exactly as before.

### Added — for plugin authors

- `ImageDecoder.snapshot(attrs)`: main-thread state handed to `open_reader`
  as a third argument when an image opens.
- `color` service (`display()`, `key()`, `settings()`).

## [1.3.0] - 2026-11-08

### Added — EXR images

- New **EXR Support** plugin (`exr`): OpenEXR images on the board through a
  per-image view: linear → sRGB with **exposure** (stops),
  **gamma**, **layer / AOV** (multi-part files included) and **channel**
  (RGB, R, G, B, A, luminance). *EXR View* section in the Attribute Editor,
  `[` / `]` for exposure, `exr.*` commands (`set_view`, `adjust_exposure`,
  `cycle_channel`, `reset_view`, `layers`).
- On by default. Showing pixels needs the **OpenEXR** and **numpy** Python
  packages, declared *optional* so they're never installed unasked: *Image →
  Install EXR Support…* (`exr.install_support`) installs them in the
  background and EXRs show without a restart. The app itself stays numpy-free. Without them an
  EXR shows a card at its real size explaining how to turn support on.
- **EXRs and numbered files are always linked** when added (`shot.0001.png`,
  `frame12.jpg`: render frames, including frames added as separate images
  with sequence detection off), whatever *Embed New Images in Scene* says, so
  boards stay small; *Embed in Scene* / `import_image(embed=True)` still embed
  one on purpose.
- EXR sizes and layers come from the file headers, read in pure Python
  (`core.imagesize.exr_info`), so headless bounds, batch and MCP tools work
  without OpenEXR. Embedded EXRs have the MIME type `image/x-exr`.
- Decoded float pixels are cached (`PIXELREF_EXR_MEMORY_MB`, default 512), so
  view changes only re-run the view transform; levels are box-filtered in
  linear light. The previous view stays on screen while a new one decodes.

### Added — Image sequences

- New **Image Sequences** plugin (`sequence`) and `sequence` node: numbered
  frames (`shot.####.exr`, any image format) played like a movie, always
  linked by pattern (`#`, `%04d`, `@@@@` and `$F4` accepted) with
  `first` / `last`, `fps`, `loop`, `autoplay` and `missing` (hold / black).
- Dropping a folder of frames or several frames makes one sequence (stills in
  the drop stay images); *File → Insert Image Sequence…*; *Show Sequence
  Folder*.
- **Refresh Frames** (`Shift+F5`, right-click *Image* menu, and a button in
  the new *Sequence* section of the Attribute Editor): picks up frames a
  render added since, extending the range and showing frames that appeared
  inside it; with nothing selected, every sequence on the board. Nothing is
  watched automatically. The *Sequence* section also lists what's on disk. Detection can be turned
  off (*File → Dropped Frames Become Sequences*).
- Works with the playback transport, filmstrip and per-frame annotations; the
  frame display shows the file's frame number too. Frames are decoded in the
  background and ahead while playing, and playback drops frames rather than
  slowing down. EXR frames use the EXR view.
- `sequence.*` commands: `import_sequence`, `import_sequences`, `rescan`,
  `info`, `set_detect`.

### Fixed

- Movies (and now image sequences) lost their generated *Attributes* editor
  (path, loop, volume…) because the Playback and Frame Notes sections replaced
  it; those sections now add to it (`generic=True`).

### Added — for plugin authors

- `image.decoders` extension point (`ImageDecoder`): teach the image plugin a
  format Qt can't read, with background decoding, levels and the memory budget
  for free. `ImageApi.register_ext()` / `supported_exts()`.
- `sequence.started` extension point (like `movie.started`).

## [1.2.0] - 2026-11-02

### Added — Code blocks

- **Code blocks** (new built-in plugin): a canvas item for showing source code, with a monospace font,
  syntax highlighting (Python, JavaScript, TypeScript, JSON, C, C++, C# and more), optional word wrap and
  line numbers, light and dark themes, width handles and one-click copy. A new block starts with a
  "Hello, World!" line to replace.

### Added — Pasted text and workspaces

- **Long pasted text wraps.** Text pasted onto the canvas wraps at a default width (400) instead of
  running on as one long line; short text keeps its natural size. Change the width with *Pasted Text
  Width…* in the canvas menu (Text submenu) or command search; 0 turns wrapping off. The setting is
  remembered.
- **Contact Sheet workspace.** A new default workspace pairs the canvas with a contact sheet. Existing
  boards keep the workspaces they already have.
- The quick-edit bar's dropdown lists now follow the dark theme instead of showing a white popup.

### Fixed — Updates (preparation; installing is still off)

- Found while rehearsing an update on Windows: the update journal could fail to save while another
  process had it open, and an update that was undone left no pixelRef running. Both are fixed. In-app
  installing remains disabled in this release; the status-bar "Update available" note is unchanged.


### Added — Progressive zoom rendering

- **Smooth zooming on busy boards**: while the zoom changes the canvas now shows a coarse overview of the
  board instead of drawing every zoom step in full, then sharpens it once the zoom stops. On the test
  boards the frame time while zooming fell from 60-155 ms to about 18-23 ms (5000-item board: 105-111 ms
  to 18-20 ms). The picture is briefly soft during a zoom, and the background grid may only show near the
  board until you stop. Turn it off in *View → Progressive Zoom Rendering* (remembered), or with
  `PIXELREF_TILE_PROGRESSIVE=0`. Memory use rises by roughly 10-50 MB while a busy board is open.
- Faster panning with linked views and with high-rate mice or trackpads: camera changes are reported once
  per step, the minimaps update once per pass, and pointer drags are applied once per event-loop pass.
- Zooming out on boards of thousands of items no longer stalls for ~40 ms per wheel notch.
  Measurements and method: [docs/performance/](docs/performance/README.md).

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
