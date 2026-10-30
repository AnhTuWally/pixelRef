# User guide

A tour of what you can do on the board. Press `F1` (or `?`) in the app for
every shortcut, and to change them. `Ctrl+K`, or right-click and type,
searches every command.

- [Canvas and navigation](#canvas-and-navigation)
- [Images and storage](#images-and-storage)
- [Annotations](#annotations)
- [Links](#links)
- [Groups, the Outliner and the Attribute Editor](#groups-the-outliner-and-the-attribute-editor)
- [Backdrops](#backdrops)
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
- **Snap to grid:** `Alt+'`, or the *Snap to grid* toggle (magnet icon) in the
  Canvas settings, turns snapping on or off. Dragged items then land
  with their nearest edges on the background grid, and scale handles snap to
  grid lines. Snapping follows the lines you see: the *Grid size*, or the
  coarser lines drawn when you zoom far out. Hold `Ctrl` while dragging to
  flip snapping for that drag.
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

## Movies

- **Bring videos in:** *File → Insert Movies… (linked, not embedded)*, or
  drag in video files (`.mp4`, `.mov`, `.m4v`, `.avi`, `.mkv`, `.webm`,
  `.wmv`).
- **Always linked, never embedded.** Unlike images, a movie is only ever a
  reference to its file on disk — there is no option to embed it in the
  scene, and no *Embed All Linked Images*-style command that packs it in
  either. If the video file moves, is renamed, or isn't there when you open
  the board (e.g. on another machine), the node shows a "Missing"
  placeholder, just like a missing linked image. To move a board with
  movies on it, copy the video files along with it.
- Playback controls are the same as for [animated images](#animated-images):
  `K` plays and pauses, `,` / `.` step, `Ctrl+,` / `Ctrl+.` jump to the start
  or end, with transport buttons and a **filmstrip** in the quick editor bar.
- Whether a particular video file plays depends on the codecs available on
  your system through Qt Multimedia; an unsupported file shows "Can't play"
  instead of a frame, rather than failing to add the node.
- The Attribute Editor's *Playback* section has the same filmstrip under the
  frame slider: a row of thumbnails sampled across the clip (decoded in the
  background). Click or drag across either filmstrip to scrub straight to a
  point in the video.

### PureRef boards

The **PureRef import** plugin reads PureRef `.pur` boards: the 1.x format
(PureRef 1.10 and 1.11) and the 2.x format (PureRef 2.0 and 2.1). Damaged files,
or files in a format version it doesn't know, are refused with a message, and
the open board stays as it was.

- **It's one way.** pixelRef reads PureRef boards but can't save them back as
  PureRef files: your work is saved as a pixelRef scene (`.pxref`), and the
  `.pur` file is left exactly as it was, so changes made in pixelRef don't
  show up in PureRef. Opening a board says so: in a notice the first time
  (tick *Don't show this again* to hide it), and in the status bar every time.
- *File → Open PureRef Board (Import Only)…* opens one as a new, unsaved
  scene. *Save* then asks for a `.pxref` name.
- *File → Import → PureRef Board…* adds one to the current board, centred in
  the view and put in a group named after the file. Dropping `.pur` files on
  the canvas does the same at the drop point. Either way it's one undo step.
- Images keep their place, size, rotation, mirroring, crop, opacity and lock.
  An image used several times is stored once. Images the board only linked to
  are linked again; when the stored path no longer exists, pixelRef looks for a
  file of that name next to the `.pur` file.
- Text becomes text notes with their colours and background. PureRef 2 notes
  keep their formatting too: font, size, bold, italic, underline, strike,
  colour and highlight, also for parts of the text. Text attached to an image
  becomes a child of that image, so it still moves with it.
- PureRef 2 groups become groups, and pen strokes become a drawing (it needs
  the *Pen* plugin).
- Not carried over: arrowheads on PureRef 2 pen strokes, group background
  colours, and animation playback settings. Free-form (non-rectangular) crops
  become their bounding rectangle. PureRef 1.x text has no font information,
  so it uses pixelRef's default size, scaled like the PureRef item.
- From scripts: `pxcmd.open_pureref(path)`, `pxcmd.import_pureref(path,
  translate=None, group=None)` and `pxcmd.pureref.inspect(path)`, which
  reports what a board holds without importing it.

The 1.x reader is a port of [FyorDev's PureRef-format](https://github.com/FyorDev/PureRef-format)
(MIT License), which worked out that file layout. The 2.x reader was written
for pixelRef from boards saved by PureRef 2.0.3 and 2.1.3.

## Annotations

Drawing tools stay active until `Esc` or `V`. The shape you just drew can be
adjusted by its handles without leaving the tool.

| Tool | Key | Notes |
| --- | --- | --- |
| Text note | `T` | see [Text notes](#text-notes) |
| Rectangle | `R` | reshape points, a corner-radius handle, dashed/dotted lines, hatch fills |
| Ellipse | `O` | `Shift` draws a circle; the same points, lines and fills as rectangles |
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

- **Links:** web addresses and file paths in a note (`https://…`, `www.…`,
  `/path/to/file.png`, `~/refs/board.pur`) become links. To link any words, or
  the whole note, use **Link…** (`Ctrl+K` while typing) or **Link to File…**.
  `Ctrl+click` a link to open it. Web pages open in the browser, `.pxref`
  scenes and PureRef boards open in pixelRef (it asks about unsaved changes
  first), and other files open in their usual app. A relative path is
  relative to the scene's folder.
- **Checklists:** **Checklist** puts a ☐ box in front of each line of the
  selected notes. Use it again to take the boxes away. With the Select tool,
  a click on a box ticks it (☑) without selecting or moving the note.
- **Copy and paste text style:** `Ctrl+Shift+C` copies a note's look (font,
  colours, box). While typing, it copies the formatting at the cursor
  instead. `Ctrl+Shift+V` pastes it onto the selected notes, or onto the
  selected words while typing.

These are in the **More** row of the Text section, and in **Text** when you
right-click a note.

### Copying attributes between items

Three ways to make items match without editing each one.

**Copy and paste attributes.** Select an item and choose **Copy Attributes**
(`Ctrl+Alt+Shift+C`, right-click → *Attributes*, or the button in the
Attribute Editor's Appearance section). Select the items to change and choose
**Paste Attributes…** (`Ctrl+Alt+Shift+V`). A window lists what was copied:

- Attributes are in **groups** (Stroke, Fill, Text…). Each group has its own
  checkbox, and the **All attributes** box at the top ticks everything,
  including the closed **Geometry** group (position, size, rotation, shape),
  which starts closed and unticked. A dash means "some".
- The **search** box filters the list by name or group. While a search is
  active, the group and master boxes only change the rows you can see.
  **Only differences** hides values the items already have.
- Click rows to select them (`Shift`/`Ctrl` for several). Ticking one
  selected row ticks or unticks all of them, `Space` does the same, and
  **Tick selected** / **Untick selected** do it in one click.
- The canvas shows the result while you tick. **Cancel** (or `Esc`) puts
  everything back, **Paste** is one undo step. Your ticks are remembered for
  that kind of item.
- An item takes an attribute when it has one with the **same name and the
  same type**, so a rectangle's stroke can go onto an ellipse or an arrow.
  The *Applies to* column says how many of the selected items can take each
  row. **Paste Style Attributes** pastes the style ones without asking.

**Several items selected.** The Attribute Editor shows a **Shared
attributes** section with the attributes the selected items have in common.
Editing a row sets it on every item that has it. *mixed* means they differ.
**⇉** gives the other items the value of the item named in *Values from*
(the one you selected last, or pick another). Right-click a row to apply it
to every item of that type in the scene. Here an attribute only needs the
same *name* on the other items, and a value that does not fit an item is
skipped.

**From a script.** `attr_transfer.copy("Rect")`, `attr_transfer.paste()`,
`attr_transfer.apply_attr("thickness")` and
`attr_transfer.set_values({"thickness": 4})`.

### Colour picker

A magnifier loupe shows the exact pixel and its hex, RGB and HSV values,
with a live readout in the tool options. A picked colour is copied as hex,
becomes the brush colour for Pen/Rect/Arrow, joins a remembered *Recent* row
and can be applied to the selection. The **HEX**, **RGB** and **HSV**
buttons copy it in the other formats. Picking from a drawing tool returns to
it.

**Pick from screen** (`Ctrl+Alt+I`, or the button in the tool options) picks
from anywhere on your screens, including other applications. The screens
freeze with the loupe on top: click to pick, or press `Esc` to cancel. On
Wayland the screen is captured through the desktop's screenshot portal
(xdg-desktop-portal); some desktops ask for permission the first time.

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

**Groups.** `G` groups the selection. A group acts as one item: a click
anywhere on it selects it, and it moves, scales, copies and links as a unit.
Double-click a group to go inside and work on one member (a dashed frame shows
the open group); `Esc` or clicking outside goes back out. Selecting a member in
the Outliner opens its group. Turn on a group's **Pick members** attribute to
make it a plain folder whose members are clicked directly (`Ctrl+Shift+N` makes
an empty one). `Ctrl+Shift+G` ungroups. A group's position is relative to its
parent, so moving a group moves everything in it.

**Outliner.** Drag to reparent or reorder (front-most at the top), `F2`
renames, eye/lock columns, a name filter, `G` group / `Ctrl+Shift+G`
ungroup, `Ctrl+Shift+N` new group. Reparenting keeps items exactly where they
are on the board. The stacking keys are under [Arrange](#arrange).

**Unlocking on the canvas.** A locked item can't be clicked to select it, but
hovering it with the Select tool shows a 🔒 badge in its corner: click the
badge to unlock the item and select it. Right-clicking a locked item also offers
**Unlock ‹name›** at the top of the menu. Both unlock the locked groups holding
the item too, so it really becomes editable. `Alt+L` unlocks everything.

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

Every node (one selected) also has a **Note** (free text, applied when you
click away) and **Metadata** (your own key / value fields: *+ Add field*, type
a key and a value, ✕ removes it). Values can be text, numbers or `true` /
`false`; put "quotes" around text that looks like a number. Keys you've used on
other nodes autocomplete. Both are saved with the scene and undoable; from
scripts they are the `note` and `metadata` (a dict) attributes.

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

## Backdrops

A **backdrop** is a coloured, titled frame that carries what sits on it, like a
stack of papers you move together. Drop something on it and it joins; drag it
off and it leaves; move the backdrop and everything on it moves too. Membership
is ordinary hierarchy (the contents are children of the backdrop), so the
Outliner shows the nesting, and hiding, locking, copying and undo all work on
the backdrop as a whole.

- `K` puts a backdrop around the selection and asks for its title. With nothing
  selected it starts the **Backdrop** tool: drag a frame around things and
  everything inside joins. The palette's **Backdrop** button does the same.
- Drag a node over a backdrop: the backdrop it will join lights up green with a
  *+ Join* chip; one it will leave lights up amber (*Leave*). Release to do it.
  The move and the membership change are **one undo step**. Hold `Alt` when you
  release to move without joining or leaving.
- Only the title bar and the border pick a backdrop, so you can click and
  rubber-band inside the frame. Double-click the title bar to rename it. The
  corner and edge points resize it (they change its size, not its scale, so its
  contents stay where they are). Select a backdrop to see its contents outlined.
- A node joins when at least half of it is inside, and stays until less than a
  third is. Where backdrops overlap, the smallest one that holds it wins, so
  backdrops nest. A backdrop never swallows something larger than itself.
  Locked nodes never join or leave.
- Backdrops always draw behind the other nodes next to them. Contents draw above
  their backdrop; *Bring to front* cannot lift a backdrop over its neighbours.

**Anything can be a backdrop.** Select a rectangle or image and choose
**Act as Backdrop** (right-click menu or command search): it captures what it
covers, and the same item switches it off again, putting its contents back
where they were. A hollow rectangle is moved by its border. The Attribute Editor's **Backdrop** section (shown for rectangles, images and
backdrops) has the same switch as a tick box, plus **Join when** to choose how
it takes things in and a count of what it holds. The setting is the node's
`backdrop_mode` attribute: `auto` (the type decides), `off`, `inside` (join when mostly
inside), `overlap` (any overlap) or `touch` (overlap with a small margin).
By default (**strict** hosting) only backdrops act as backdrops. In **free**
hosting, rectangles and images do too, so dropping a small image on a big one
stacks it. Switch with **View ▸ Free Backdrop Mode** (a tick; it is
remembered), or `pxcmd.backdrops.set_mode("free")` / `"strict"`; a node's own
setting always wins. **Settle Backdrops** (right-click menu, with something
selected) re-applies the rules to the selection, for example after changing a
node's setting or switching modes.

Scripting: `pxcmd.backdrops.wrap(nodes, padding, title)`, `.create(x, y, w, h)`,
`.settle(nodes)`, `.adopt(backdrop, nodes)`, `.release(nodes)` and
`.contents(backdrop)`. Setting an attribute never changes membership; only
these commands and canvas gestures do. Decisions use the nodes' axis-aligned
bounds, so rotated nodes count by their bounding box. Design notes:
[design-backdrop-hosting.md](design-backdrop-hosting.md).

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
starts with previous frame, play/pause, next frame and the frame number, with
a **filmstrip** — a row of thumbnails across the animation — underneath; with
several animations selected they act on all of them. Click or drag across the
filmstrip to jump straight to a frame. *Hide Playback Controls* in the bar's
⋯ menu removes them; with the quick editor turned off, each selected
animation gets a small control bar of its own instead (without a filmstrip).
The Attribute Editor's *Playback* section has the same controls plus a scrub
slider, a go-to-frame field (also *Go to Frame…* in the right-click
*Playback* menu), and its own filmstrip.

A paused image keeps its frame when the scene is saved. Playback isn't
undoable, like camera moves.

## Interactive boards

Any item can do something when it's clicked: show the answer to a question,
hide a draft, swap a before for an after, jump a GIF to frame 5 or fly the
view somewhere. That's its **click action**. Buttons (*Insert Button* in the
right-click menu) exist only to carry one, but a picture, a note or a shape
can have one too.

**Show it what to do (Demonstrate).** Select the item, then *Click Action ›
Demonstrate Click Action…* (or **Demonstrate…** in the Attribute Editor's
*Click Action* section). A green frame and banner appear. Change the board
the way it should look *after* the click: hide things, move them, fade them,
step a GIF to a frame, draw something new. Then press **Done** (`Ctrl+Enter`).
pixelRef works out what changed, saves it as the click action and puts the
board back as it was. Anything you drew meanwhile stays, hidden until the
click. Tick **Include this view** in the banner to fly the view to where it is
now as well. **Cancel** throws the demonstration away. Either way the whole
demonstration is one undo step.

**Or connect it to an item.** A selected button has a round handle beside
it. Drag the handle onto another item and choose what the click does to that
item: *Show it (hidden until the click)*, *Hide it*, *Go to the frame on show*,
*Play it*, *Pause it*, *Fly the view to it*, or *Run its click action*.
*Connect Click To…* (right-click menu, or **Connect…** in the Attribute
Editor) does the same thing with a click on the target instead of a drag.

**Or start from a template.** Select some items and use *Click Action*:

| Template | Makes |
| --- | --- |
| *Make Tabs from Selection* | one button per item above them; each shows its item and hides the others, like tabs |
| *Make Reveal Button* | a button that shows the selected items (hidden until clicked) |
| *Make Before / After Button* | for two items, a button that swaps which one shows |

**Running it.** On the canvas, only a **locked** item runs its click action:
a plain click on it runs it, and the pointer turns into a hand over it. An
unlocked item is one you're still editing, so a click selects it as usual.
Lock an item with the lock in the Outliner or the Attribute Editor; locking
your buttons is how you finish a board before sharing it. To edit a locked
button again, click the 🔒 badge in its corner (or right-click it → **Unlock**).

To try an unlocked item's action while you edit, select it and click **▶ Run**
in the quick-edit bar under it (**↺ Reset** puts things back), or **Try it**
in the Attribute Editor.

**The Click Action section** of the Attribute Editor lists the steps as
sentences ("Show Answer", "Go to frame 5 · walk_cycle"). Point at a sentence
to outline its item on the canvas, and click × to remove the step. **Try it**
runs the action, **Reset** puts back what it changed, and **Demonstrate
again…** starts from the action's result so you can adjust it.

- **Click again to put things back** (on by default): a second click undoes
  the first, so a reveal button hides the answer again.
- **One at a time with**: give several items the same set name (or use *Make
  One-at-a-Time Set*). Clicking one turns off the one that's on, like tabs. A
  button that's on draws an outline and a dot in its *When on* colour.

Steps refer to items by id, so renaming an item doesn't break them. A step
whose item was deleted is skipped.

**When something goes wrong.** An item whose click action has a problem gets
an amber dashed outline. That happens when its last run skipped or failed a
step, or when a step can't run at all (its item was deleted, or it needs a
plugin that's off). Pointing at the item says what's wrong in the status bar,
and **▶ Run** in its quick-edit bar turns amber. A click that goes wrong also
shows a toast with a **Show Log** button. In the Click Action section, each bad
step is amber with the reason under it, with a line such as "Last run 14:03:05
(on): 1 of 2 steps ran". The outline goes away after a run with no problems,
or once you remove or fix the bad step.

**The Click Action Log** (*Click Action › Click Action Log…*, the **Log…**
button in the section, or `Ctrl+K`) lists every click this session, newest
first: the time, the item (click its name to select it), on or off, how many
steps ran, and for each failed or skipped step, why. Tick *Only problems* to
hide the clicks that worked. The log isn't saved with the board; **Clear**
empties it.

Clicking is an ordinary command (`pxcmd.interactions.click("Reveal")`), so it can be undone.
A board can only run the steps pixelRef allows in click actions (show, hide,
set attributes, GIF frames, playing, flying the view, clicking another item),
never arbitrary scripts, so a board someone sends you is safe to click.

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
| `Ctrl+Left` / `Ctrl+Right` | align left / right edges |
| `Ctrl+Up` / `Ctrl+Down` | align top / bottom edges |
| `]` / `}` | bring to front / bring forward |
| `[` / `{` | send to back / send backward |

*Arrange* in the context menu also normalizes height, width or size and aligns
edges and centres (including horizontal/vertical centers, which have no
default shortcut). With fewer than two items selected, arranging works on
everything.

## Export

- `Ctrl+E` renders the selection (or the whole board) to PNG or JPEG at 1:1.
- `Ctrl+Alt+C` flattens the selection (or the whole board) and copies it to
  the clipboard as an image, ready to paste elsewhere.
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
- **Finding the file.** *File → Copy File Path* and *Copy Folder Path* put the
  open scene's full path, or its folder's, on the clipboard; *Open Containing
  Folder* shows that folder in your system file browser. They are greyed out
  until the scene has been saved.
- **Auto-refresh.** pixelRef watches the open file for changes made outside
  it (another copy, a sync tool, a script). *File → When the File Changes*
  picks the behaviour: *Notify When Changed* (default) shows a toast at the
  bottom of the board with a **Reload** button; *Reload Automatically* reloads
  at once; *Manual Only* turns watching off. *File → Reload from Disk* (**F5**)
  reloads on demand in any mode. Unsaved edits are never discarded silently:
  reloading asks first, and saving over a file that changed on disk asks too.
  If the file is deleted or moved, your open copy is kept and the toast offers
  **Save** to write it back.
- **Autosave and recovery.** Unsaved work (images included) is written to a
  recovery copy every minute. After a crash, the next start offers to restore
  it.
- **Session Resume.** *File → Resume Last Session on Launch*, or the checkbox
  on the start panel (off by default; the checkbox only shows once a previous
  session actually had something on the board), reopens your workspace
  exactly as you left it. With a saved file open, closing pixelRef skips the
  save prompt; next launch reopens that file's last saved version, and if you
  had unsaved edits you're asked whether to bring them back. With an Untitled
  board, your unsaved draft reopens automatically, no prompt needed.
- **Resuming an Untitled draft without turning Session Resume on.** Closing an
  Untitled board with unsaved work (even if you choose Discard) still keeps a
  thumbnailed copy of it. Next time you see the start panel, a *Resume Last
  Session* card with that thumbnail lets you jump straight back to it.
- **About pixelRef…** in the canvas context menu shows the app, Python and Qt
  versions.
- **Several open scenes.** The design for switching between open scenes,
  detaching them into their own windows and the double-click preference is in
  [design-multi-file.md](design-multi-file.md) (Files panel tab on the right
  edge; breadcrumb drawer as the alternative).

## Window modes

| Key | Does |
| --- | --- |
| `Ctrl+T` | keep the window on top of other applications |
| `F11` | frameless: drag an edge to resize, the bar or a view header to move |
| `Ctrl+G` | ghost mode: a translucent window to trace over other applications |
| `Ctrl+Shift+T` | click-through: mouse clicks pass through the window to the application below |

Turning click-through on also keeps the window on top. It turns off by
itself when you switch back to pixelRef (`Alt+Tab`, the taskbar), and the
tray icon turns it off too. Window → **Click-Through Turns Off When
Activated** changes that. Click-through is never saved with a scene.

**Image Info** (`Alt+I`, in View) shows, next to the cursor over an image,
the pixel under it, counted in the image's own pixels from its top-left
corner, wherever the image is cropped, flipped or rotated. It also shows the
image's size (and the size before cropping), how large it is shown, and its
file. When you zoom in far enough, the pixel under the cursor is outlined.

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

## Help and documentation

`Shift+F1` opens the Help browser. It searches every tool, menu item,
command, node type, attribute and section of these guides as you type, and
Enter runs what you found. Point at a tool button or an Attribute Editor row
and press `Shift+F1` to see what it does. In the command search (`Ctrl+K`),
`F1` explains the highlighted entry. Scripts, the terminal and AI agents can
search the same documentation. See [documentation.md](documentation.md).

## Keyboard shortcuts and macros

`F1` edits every shortcut. Any `.py` in the scripts folder becomes a command in
the *Scripts* menu that you can put on a key. *Record Macro* (`Ctrl+Alt+R`,
or the record button in the window bar) saves what you do as a script. `keymap.toml` can also bind command lists,
Python and your own key modes. See [keymap.md](keymap.md).

## Plugins

> **Plugins and scripts run with the same access as you.** They can read and
> change your files, use the network and start other programs. Only install
> them from people you trust. See
> [Staying safe with plugins and scripts](security.md), which is also the
> *Safety* tab of *Window → Plugins…*.

*Window → Plugins…* enables and disables plugins at runtime. Nodes of a
disabled plugin's types keep their data and show as placeholders. Plugins
that need Python packages install them from the same window.

- **Find a plugin** by typing in the search box: its name, author, a tag, or
  something it provides (a command, tool or node type). The buttons under it
  show only active, off, built-in or third-party plugins, or the ones that
  **need attention** (waiting for review, failed, conflicting, or doing
  things they didn't declare).
- **Read about a plugin** on its page beside the list: who made it and how
  pixelRef knows (signature, your approval), what it declares it does, what
  it provides, what it needs, what the activity monitor noticed it doing, and
  its own notes. *Open in Help* shows the same in the Help browser.
- **New plugins stay off** until you approve them. Ticking one opens the
  approval dialog, which shows what it says about itself. A plugin whose
  files changed since you approved it stops until you approve it again.
- **Add plugin folders** in the *Sources* tab: a studio share, a folder of
  downloaded plugins. Each folder's policy decides whether its plugins need
  approving, must be signed by a trusted publisher, or (for your own
  development folder) are all trusted. When two folders hold the same plugin,
  its page lists both copies and lets you choose.
- **Trusted publishers** (also in *Sources*): add a publisher's key once, and
  plugins they sign load without asking.

When new or changed plugins wait for review, pixelRef says so at startup.
*Help → Staying Safe with Plugins & Scripts…* opens the safety guide.
See [plugins.md](plugins.md) for writing plugins.
