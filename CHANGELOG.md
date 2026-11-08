# Changelog

All notable changes to pixelRef are documented here.

## [1.3.0] - 2026-11-08

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

## [1.2.0] - 2026-11-08

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
