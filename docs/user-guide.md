# User guide

A tour of what you can do on the board. Press `F1` (or `?`) in the app for
every shortcut, and to change them. `Ctrl+K`, or right-click and type,
searches every command.

- [Canvas and navigation](#canvas-and-navigation)
- [Images and storage](#images-and-storage)
- [Annotations](#annotations)
- [Links](#links)
- [Groups, the Outliner and the Attribute Editor](#groups-the-outliner-and-the-attribute-editor)
- [Effects](#effects)
- [Views and window management](#views-and-window-management)
- [Animated images](#animated-images)
- [Contact sheet](#contact-sheet)
- [Crop and image tools](#crop-and-image-tools)
- [Arrange](#arrange)
- [Export](#export)
- [Files, autosave and recovery](#files-autosave-and-recovery)
- [Window modes](#window-modes)
- [Scripting](#scripting)
- [Keyboard shortcuts and macros](#keyboard-shortcuts-and-macros)
- [Plugins](#plugins)

## Canvas and navigation

- **Move around:** wheel zoom, middle- or right-drag to pan, `Space`+drag to
  pan. `F` frames the selection (or everything when nothing is selected),
  `Shift+F` frames all, and double-clicking an image zooms to it. Tap `Space`
  to maximise the view.
- **Zoom controls** in the bar: click the percentage for 100% (`Ctrl+0`), its
  arrow for presets, then − / + / Fit.
- **Select:** drag a rubber band on empty canvas; `Shift` or `Ctrl` adds to
  the selection.
- **Transform gizmo** for one or many items: corner handles scale (`Shift`
  free, `Alt` from the centre), edge handles stretch, and the top knob or just
  outside a corner rotates (`Shift` snaps to 15°). `Ctrl+Wheel` scales, and
  the arrow keys nudge (`Shift` ×10).
- **Background:** with nothing selected, the Attribute Editor shows the canvas
  settings: colour (picker or presets), pattern (grid lines, dots, crosses,
  checkerboard, plain), grid size and pattern colour. They are also under
  *View → Background*, and `Ctrl+'` toggles the pattern. The background is
  saved in the scene, and new scenes start with your last choice.
- **Undo / redo** covers every change to the document. Camera moves (pan, zoom,
  framing) are not undoable.

## Images and storage

- **Bring images in:** paste (`Ctrl+V`) or drag in image files, whole
  folders, GIFs, clipboard bitmaps, images dragged from a browser (downloaded
  automatically) and text.
- **Embedded by default.** Images are stored in the scene file, so one `.pxref`
  holds everything. Per image you can link to a file on disk instead
  (Attribute Editor → *Link…*), or use *Save as file* to write an embedded image
  out and link to it. Linked paths are stored relative to the scene.
- *File → Embed New Images in Scene* switches the default for new images, and
  *File → Embed All Linked Images* packs a scene into one self-contained file.
- **Copy, cut, paste and duplicate** work between scenes.

## Annotations

Drawing tools stay active until `Esc` or `V`. The shape you just drew can be
adjusted by its handles without leaving the tool.

| Tool | Key | Notes |
| --- | --- | --- |
| Text note | `T` | see [Text notes](#text-notes) |
| Rectangle | `R` | reshape points, a corner-radius handle, dashed/dotted lines, hatch fills |
| Arrow / line | `A` | a handle on each end (`Shift` snaps to 45°), arrowheads on either or both ends |
| Blur | `B` | blurs whatever is under it, see [Blur and pixelate](#blur-and-pixelate) |
| Pixelate | `X` | shows whatever is under it as blocks |
| Step | `N` | numbered markers, see [Steps](#steps) |
| Pen | `P` | see [Free drawing](#free-drawing) |
| Eraser | `E` | deletes pen strokes, rectangles, arrows, steps and blur/pixelate regions, never images or text |
| Colour picker | `I` | see [Colour picker](#colour-picker) |

### Blur and pixelate

Drag a region over a name, a face or anything else to hide (`Shift`: square,
`Alt`: from the centre). It has no pixels of its own: it shows whatever is
stacked below it, so it follows when you move the image or the region, and
exports and copies as you see it. Drag its corner and edge points to reshape
it. *Strength* (blur) and *Pixel size* (pixelate) are in the quick editor.

### Steps

Each click with the Step tool places the next number: one past the highest
on the board. Drag instead of clicking to give the marker a pointer: it sits
where the drag ends and points to where it began. A selected step has one
handle, for its pointer: drag it to aim the pointer, drop the tip on the marker
to remove it, or (on a step without one) pull the small dot by its edge to add
one. Change the size with *Size* in the quick editor. Markers can count
`1 2 3`, `A B C`, `a b c` or `I II III`, and be circles, rounded or square
boxes. **Renumber Steps** (right-click a step) closes the gaps left by deleted
ones.

### Free drawing

Each press of `P` starts a **Drawing**: every stroke until `Esc`/`V` goes into
it, and it then moves, scales, copies and groups as one object. Double-click a
drawing (or press `Shift+P`) to add or erase strokes later. `Ctrl+Shift+G`
separates a drawing into strokes, and `Ctrl+J` combines them. Strokes have
adjustable smoothing and optional arrowheads.

### Text notes

Font, size, bold/italic/underline/strikethrough, alignment, and text and
highlighter colours apply to the whole note, or to the selected words while
editing (`Ctrl+B` / `Ctrl+I` / `Ctrl+U`, `Ctrl+Shift+X`, `Ctrl+Shift+<` `>`).
The box has fill, border, corner radius, padding, presets (Title, Label,
Sticky note, Callout, Highlighter) and side handles to set its width
(double-click one to fit the text again).

### Colour picker

A magnifier loupe shows the exact pixel and its hex/RGB, with a live readout
in the tool options. A picked colour is copied, becomes the brush colour for
Pen/Rect/Arrow, joins a remembered *Recent* row and can be applied to the
selection. Picking from a drawing tool returns to it.

## Links

Links show what's connected to what. With the Link tool (`W`), drag from one
element to another, or from/to anywhere: an end on empty space stays a free
point. Ends snap to elements:

- at the middle of an element (the dashed ring at its centre), the end
  attaches to the whole element and meets the side facing the link;
- near or on an edge, it pins to that spot of the edge;
- anywhere else inside, it pins to that very spot of the content (point at a
  detail of an image). Pinned spots move and scale with the element;
- hold `Alt` for a free end, or switch *Snap to elements* off in the tool
  options.

Elements inside groups are linked directly. Hold `Ctrl` to attach to the group
around the element instead (`Ctrl+Shift`: the outermost group), or point at
empty space inside a group's frame. While you drag, the element the end will
attach to is outlined with its path (*Characters › Hero*), the groups around
it are dashed, and a hint names what the modifier keys would do. A selected
link labels its elements *from* and *to*, with solid dots for attached ends
and hollow dots for free ones.

- `Ctrl+L` links the selected elements in the order you selected them, and
  `Ctrl+Shift+L` selects everything linked to the selection.
- Links follow their elements as they move.
- **Waypoints:** drag a selected link's line (or the small circle halfway
  along a segment) to add a waypoint that pins the path there. Drag a waypoint
  to move it (`Shift` lines it up with its neighbours), double-click it to
  remove it; *Straighten Link* clears them all.
- **Ends:** every link end has a handle. Hover near one, even on a link that
  isn't selected, and drag it to re-attach or free it.
- **Style** in the Attribute Editor: straight / curved / elbow path, where each
  end meets its element (auto, centre, a side or a fixed spot), colour,
  thickness, dash, a cap at each end (arrow, triangle, circle, diamond, bar),
  cap size and a label.

## Groups, the Outliner and the Attribute Editor

**Groups.** `Ctrl+G` groups the selection. A group acts as one item: a click
anywhere on it selects it, and it moves, scales, copies and links as a unit.
Double-click a group to go inside and work on one member (a dashed frame shows
the open group); `Esc` or clicking outside goes back out. Selecting a member in
the Outliner opens its group. Turn on a group's **Pick members** attribute to
make it a plain folder whose members are clicked directly (`Ctrl+Shift+N` makes
an empty one). `Ctrl+Shift+G` ungroups. A group's position is relative to its
parent, so moving a group moves everything in it.

**Outliner.** Drag to reparent or reorder (front-most at the top), `F2`
renames, eye/lock columns, a name filter, `Ctrl+G` group / `Ctrl+Shift+G`
ungroup, `Ctrl+Shift+N` new group. Reparenting keeps items exactly where they
are on the board. The stacking keys are under [Arrange](#arrange).

**Filtering by type.** The **Types** menu next to the name filter has two
halves. *Show in Outliner* has a check box per node type in the scene, plus
collections such as *Annotations* (every rectangle, arrow, text note and pen
drawing). Unchecked types are left out of the list, but the board doesn't
change. Alt+click a type to list only that type. The button reads *Types 3/5*
while a filter is on, and the filter is remembered between sessions. Groups
that hold matching rows stay listed in italics so you can see where the rows
live. *On the Canvas* has a sub-menu per type and collection: **Show All**,
**Hide All**, **Isolate** (hide everything else), **Lock All**, **Unlock All**
and **Select All**. Each one is a single undo step, and `Alt+H` shows
everything again. The right-click menu's **Same Type** sub-menu does the same
for the types of the selected items. From scripts:
`pxcmd.outliner.isolate_types("image")` or
`pxcmd.outliner.set_type_flag("annotation_*", "visibility", 0)`.

**Attribute Editor.** A header card (rename, show/hide, lock, size and source)
over collapsible sections: Transform, Appearance, Image, Text, Text box,
Stroke, Align & arrange, and sections **generated from the node type's
attribute schema**. Plugin node types get an editor for free, and dynamic
attributes added with `pxcmd.add_attr` appear under *Extra attributes*. Drag a
field's label to scrub its value.

**Quick editor.** Select something on the canvas and a small bar appears
below it with the attributes you change most: colour, width and line style
for shapes, colour, size and alignment for text, opacity for images. Edits
apply to every selected element and undo as one step. The bar hides while you
drag the selection. Its **⋯** menu chooses the fields for each kind of element
(tick and drag to reorder; *Plugin default* puts back the plugin's choice).
Right-click a field to remove it. Turn the bar off or on with
**View ▸ Quick Attribute Editor**, or disable the *Quick Attribute Editor*
plugin.

The Tools, Outliner and Attribute Editor are **panels**: docked in the
sidebar, floating over the canvas, or hidden and popped up on demand (`Alt+T`,
`Alt+O`, `Alt+A`). See [Panels](window-manager.md#panels).

## Effects

Any element can have a stack of effects: grayscale (`Shift+G` on images),
invert, brightness/contrast, threshold, posterize, blur, edge detect, and more
from plugins. Add them in the Attribute Editor's *Effects* section or the
canvas menu's *Effects*. See [effects.md](effects.md).

## Views and window management

Any number of canvas views can show the same board, each with its own camera,
**display preset** (all, value check, images only, annotations only, clean),
isolation (`Shift+I`), value check (`Alt+V`), framing guides and **camera
bookmarks** saved with the scene (`Ctrl+1`…`Ctrl+9`). Views can be linked so
they pan and zoom together. Each canvas has a minimap.

Views are tiles in an i3-style tiling layout with splits, tabs, workspaces, a
scratchpad and tear-off windows, driven by the leader key `Ctrl+W`.
Workspaces are saved with the scene. The built-in ones are **Board**,
**Review** (colour and value views, linked) and **Present** (one clean
canvas).

All of this is covered in [window-manager.md](window-manager.md).

## Animated images

Animated GIFs and WebPs play on the board. Select one and press `K` to
play/pause, `,` / `.` to step a frame back/forward, and `Ctrl+,` / `Ctrl+.`
for the first/last frame. The quick editor bar under a selected animation
starts with previous frame, play/pause, next frame and the frame number; with
several animations selected they act on all of them. *Hide Playback Controls*
in the bar's ⋯ menu removes them; with the quick editor turned off, each
selected animation gets a small control bar of its own instead. The Attribute
Editor's *Playback* section has the same controls plus a scrub slider and a
go-to-frame field (also *Go to Frame…* in the right-click *Playback* menu).

A paused image keeps its frame when the scene is saved. Playback isn't
undoable, like camera moves.

## Contact sheet

The **Contact Sheet** view lists every image on the board as a grid of
thumbnails. Click to select (`Ctrl`/`Shift` extend), double-click to frame the
image in the active canvas; a slider sets the thumbnail size. Open it with
*New View…* (`Ctrl+W` `N`) or `pxcmd.wm.open_view("contact_sheet")`.

## Crop and image tools

| Key | Does |
| --- | --- |
| `C` / `Shift+C` | crop the selected image / reset its crop |
| `Shift+H` / `Shift+V` | flip horizontally / vertically |
| `Ctrl+R` / `Ctrl+Shift+R` | rotate 90° clockwise / counter-clockwise |
| `Shift+G` | toggle a grayscale effect |
| `Ctrl+Shift+0` | reset the transform |

## Arrange

| Key | Does |
| --- | --- |
| `Ctrl+P` | optimal pack |
| `Ctrl+Alt+H` / `Ctrl+Alt+V` / `Ctrl+Alt+G` | row / column / grid |
| `]` / `}` | bring to front / bring forward |
| `[` / `{` | send to back / send backward |

*Arrange* in the context menu also normalizes height, width or size and aligns
edges and centres. With fewer than two items selected, arranging works on
everything.

## Export

- `Ctrl+E` renders the selection (or the whole board) to PNG or JPEG at 1:1.
- `Ctrl+Shift+E` copies the original image files of the selection (or of every
  image) into a folder.

Exports wait for every effect result, so what you save is complete.

## Files, autosave and recovery

- Scenes are `.pxref` files (readable JSON; `.json` files open too). See
  [file-format.md](file-format.md).
- **Recent files and the start panel.** Every save stores a small picture of
  the board in the file. On an empty board (at start-up, or after *New*) each
  canvas shows a start panel with the pixelRef version, *Open…* and your recent
  files as cards with their thumbnails, names, folders and dates. Click a card
  to open it.
- **Autosave and recovery.** Unsaved work (images included) is written to a
  recovery copy every minute. After a crash, the next start offers to restore
  it.
- **About pixelRef…** in the canvas context menu shows the app, Python and Qt
  versions.

## Window modes

| Key | Does |
| --- | --- |
| `Ctrl+T` | keep the window on top of other applications |
| `F11` | frameless: drag an edge to resize, the bar or a view header to move |
| `G` | ghost mode: a translucent window to trace over other applications |

## Scripting

Everything you can do with the mouse is a command, so it can be scripted from
the Script Editor (``Ctrl+` ``), from scripts in your scripts folder, or in
batch mode without a window. See [scripting.md](scripting.md) and the
[command reference](commands.md).

## Your tools palette

The Tools view is yours to arrange, however many plugins add tools:

- **Right-click a button** to give it a key (*Set Shortcut…*: press the key;
  if something else has it you're asked before it moves), move it to another
  section, or hide it. Hidden tools keep their keys and come back from
  *Show Hidden*.
- **Add Command…** (right-click anywhere in the view) puts any action on the
  palette: a command, one of your scripts or a recorded macro.
- **Customize Palette…** (also at the bottom of the canvas *Tools* menu) lists
  every button: drag to reorder or move between sections, untick to hide,
  double-click a shortcut to change it, make your own sections.
- The canvas *Tools* menu follows the palette's order.
- A plugin installed later puts its tools in the *Tools* section (its buttons
  in *Add*) next to the ones they sit by in the default palette; the rest of
  your arrangement stays. *Reset Palette* goes back to the default (your keys
  stay).

The layout is kept in your settings; keys go to `keymap.toml` like every other
shortcut. Scripts can do the same: `pxcmd.tool_palette.move("tool.pen", "tools", 0)`,
`pxcmd.set_shortcut("tool.pen", "B")`.

## Keyboard shortcuts and macros

`F1` edits every shortcut. Any `.py` in the scripts folder becomes a command in
the *Scripts* menu that you can put on a key. *Record Macro* (`Ctrl+Alt+R`,
or the record button in the window bar) saves what you do as a script. `keymap.toml` can also bind command lists,
Python and your own key modes. See [keymap.md](keymap.md).

## Plugins

*Window → Plugins…* enables and disables plugins at runtime. Nodes of a
disabled plugin's types keep their data and show as placeholders. Plugins
that need Python packages install them from the same window. See
[plugins.md](plugins.md).
