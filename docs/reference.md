# pixelRef reference (for people and AI agents)

This page covers the whole of pixelRef 1.0.0 in one place: what it can do, how
to use each feature, the data model, the scripting API, and recipes for
scripts, macros, plugins and effects. It's written so that a person or an AI
agent can read it once and then answer questions or build extensions without
searching the code.

Each section ends with links to the detailed page. When this page and the code
disagree, the code wins. Two sources are generated from the code and are
always current:

- [commands.md](commands.md): every command with its signature, alias,
  whether it can be undone, and a one-line description.
- In the running app: `pxcmd.help("name")`, `pxcmd.list_commands("pen.*")`,
  `pxcmd.attr_info(node, attr)`, `pxcmd.keymap.list()` and
  `pxcmd.list_plugins()`.

**Contents**

1. [How to use this page](#1-how-to-use-this-page)
2. [The mental model](#2-the-mental-model)
3. [Feature catalogue](#3-feature-catalogue)
4. [Default keys](#4-default-keys)
5. [Data model](#5-data-model)
6. [Scripting API](#6-scripting-api)
7. [Recipes](#7-recipes)
8. [Rules for code changes](#8-rules-for-code-changes)
9. [Where things live](#9-where-things-live)
10. [Frequently asked questions](#10-frequently-asked-questions)

## 1. How to use this page

| You want to… | Go to |
| --- | --- |
| explain how to do something in the app | [3. Feature catalogue](#3-feature-catalogue) and [4. Default keys](#4-default-keys) |
| write a script or a one-off automation | [6. Scripting API](#6-scripting-api) and [7.1](#71-a-script-in-the-script-editor-or-batch-mode) |
| put an automation on a key | [7.2](#72-a-script-file-that-becomes-a-command), [7.3](#73-a-macro-in-keymaptoml) |
| add a command, node type, tool, effect or panel | [7.4](#74-a-plugin-with-commands-keys-and-menus) to [7.10](#710-other-extension-points) |
| change pixelRef's own code | [8. Rules for code changes](#8-rules-for-code-changes) and [9. Where things live](#9-where-things-live) |

**For AI agents.**

- Give users the shortest path first: a key or menu item. Offer a script only
  when they want automation.
- Run commands rather than editing the scene graph directly. The UI, macros
  and the Script Editor all do this, and it's what makes changes undoable.
- Check a command's exact name and arguments in [commands.md](commands.md)
  before you use it. Don't guess them.
- Attribute names and types are listed in [5.2](#52-node-types). In a running
  session, `pxcmd.attrs(node)` and `pxcmd.attr_info(node, attr)` are the
  definitive source.

## 2. The mental model

- **The board is a tree of nodes.** Every element (an image, a note, a link,
  a group) is one node with a unique name, a permanent id, a type and
  attributes. Sibling order is draw order: the first sibling is drawn at the
  back.
- **Everything is a command.** Buttons, keys, menus, tools, scripts and macros
  all run named commands through one registry. Each top-level command is one
  undo step and is echoed as a `pxcmd.…(…)` line.
- **Everything is a plugin.** The kernel holds the document, commands, undo
  and layout. Every feature, from images to the Outliner, is a plugin in
  `pixelref/builtins/`. Third-party plugins use the same API.
- **Plugins have two halves.** `activate(ctx)` is headless: node types and
  commands that change data. It also runs in batch mode and in tests.
  `activate_ui(ui)` is the Qt half: drawing, tools, keys, menus and panels.
- **Views are separate from the document.** Any number of canvas views show
  the same board, each with its own camera and display settings. Views are
  tiles in an i3-style window manager. Camera moves are never undoable.
- **One file holds everything.** A `.pxref` scene is readable JSON that holds
  the nodes, the embedded images, the workspaces and cached effect results.

## 3. Feature catalogue

Each entry lists how to use the feature, its keys, the commands behind it and
the plugin that provides it.

### Canvas and navigation (`navigation`, `minimap`, `canvas_background`)

- Zoom with the wheel. Pan with a middle- or right-drag, or `Space`+drag.
- `F` frames the selection (or everything), `Shift+F` or `Home` frames
  everything, and double-clicking an image zooms to it. Tapping `Space`
  maximises the view under the mouse.
- The zoom box in the bottom bar: click the percentage for 100% (`Ctrl+0`),
  and use its arrow for presets.
- Each canvas has a **minimap**. Click or drag it to move the view, drag its
  title bar to another corner, and use `S`/`M`/`L` to resize it.
- **Background**: with nothing selected, the Attribute Editor shows the
  colour, pattern (grid lines, dots, crosses, checkerboard, plain), grid size
  and pattern colour. `Ctrl+'` toggles the pattern. The background is saved
  with the scene.
- Commands: `navigation.frame`, `frame_all`, `frame_selected`, `zoom`,
  `zoom_by`, `pan`, `set_camera`, `flip`; `minimap.toggle`, `minimap.place`;
  `canvas_background.set(background=[r, g, b], pattern="dots")`.

### Selecting and transforming (`select`, `gizmo`, `transform`, `crop`, `order`)

- Click, rubber-band, and `Shift`/`Ctrl`-click to add. `Ctrl+A` selects all.
- **Gizmo**: corner handles scale (`Shift` scales freely, `Alt` scales from
  the centre), edge handles stretch, and the top knob or just outside a corner
  rotates (`Shift` snaps to 15°). `Ctrl+Wheel` scales. The arrow keys nudge,
  and `Shift` makes the nudge 10 times larger.
- `Shift+H` / `Shift+V` flip, `Ctrl+R` / `Ctrl+Shift+R` rotate by 90°, and
  `Ctrl+Shift+0` resets the transform.
- `C` crops the selected image and `Shift+C` resets the crop.
- `]` brings to front, `}` brings forward, `{` sends backward and `[` sends to
  back.
- Commands: `select`, `set_transform`, `transform.flip`, `transform.rotate`,
  `crop.set(node, [x, y, w, h])`, `crop.reset`, `order.selection(mode)`,
  `reorder`.

### Images (`image`, `image_storage`, `clipboard`, `playback`, `contact_sheet`)

- Paste (`Ctrl+V`) or drag in files, folders, GIFs and WebPs, clipboard
  bitmaps and browser images. `Ctrl+I` opens *Insert Images…*.
- Images are **embedded** in the scene by default, and identical files are
  stored once. Per image, the Attribute Editor offers *Link…* (use a file on
  disk) and *Save as file*. *File → Embed All Linked Images* packs a scene
  into one file, and *File → Embed New Images in Scene* sets the default.
- Copy, cut, paste and duplicate (`Ctrl+D`) work between scenes.
- **Animated GIFs and WebPs** play on the board. `K` plays and pauses, `,` /
  `.` step one frame, and `Ctrl+,` / `Ctrl+.` jump to the first or last frame.
  Controls appear in the quick editor bar. A paused frame is saved with the
  scene.
- The **Contact Sheet** view shows every image as a thumbnail grid
  (`pxcmd.wm.open_view("contact_sheet")`).
- Commands: `import_image(path, translate=…, embed=…)`, `import_images`,
  `image.toggle_grayscale`, `image_storage.*`, `clipboard.*`,
  `playback.play/pause/toggle/go_to_frame/next_frame`.

### Annotations (`text`, `rect`, `arrow`, `pen`, `eraser`, `steps`, `redact`, `color_picker`, `brush`)

Drawing tools stay active until `Esc` or `V`. The shape you just drew can be
adjusted by its handles without leaving the tool.

| Tool | Key | Node type | Notes |
| --- | --- | --- | --- |
| Text note | `T` | `annotation_text` | rich text per word (`Ctrl+B/I/U`), box fill, border, padding, presets (Title, Label, Sticky note, Callout, Highlighter), width handles |
| Rectangle | `R` | `annotation_rect` | corner radius handle, dashed/dotted strokes, hatch fills |
| Arrow / line | `A` | `annotation_line` | a handle on each end (`Shift` snaps to 45°), arrowheads at either or both ends |
| Pen | `P` | `annotation_drawing` holding `annotation_path` strokes | one drawing per tool session. Double-click a drawing or press `Shift+P` to edit it, `Ctrl+Shift+G` separates it into strokes, `Ctrl+J` combines strokes |
| Step | `N` | `annotation_step` | counts up (1 2 3, A B C, a b c, I II III). Drag while placing to add a pointer. *Renumber Steps* closes gaps |
| Blur | `B` | `annotation_blur` | blurs whatever is under it, live |
| Pixelate | `X` | `annotation_pixelate` | shows whatever is under it as blocks, live |
| Eraser | `E` | — | deletes erasable nodes (strokes, rects, arrows, steps, blur/pixelate). Never deletes images or text |
| Colour picker | `I` | — | loupe with hex/RGB. The picked colour becomes the brush colour of the drawing tool and is added to *Recent* |

- Each drawing tool keeps its own colour, width and options. **★ Save as
  default** in the Tools panel keeps them across sessions. In the Attribute
  Editor, *Use as default* copies a node's look into its tool.
- Commands: `text.create`, `rect.create`, `arrow.create`, `pen.add_stroke`,
  `pen.combine`, `steps.create`, `steps.renumber`, `redact.blur`,
  `redact.pixelate`, `tools.set_option(tool, name, value)`.

### Links (`links`)

- The Link tool (`W`): drag from one element to another, or to or from empty
  space (that end stays a free point). Where you drop an end decides what it
  attaches to:
  - an element's centre ring attaches to the whole element;
  - a point on or near an edge pins to that spot of the edge;
  - any other point inside pins to that exact spot of the content, and moves
    and scales with it.
  - `Alt` makes the end free. `Ctrl` attaches to the enclosing group, and
    `Ctrl+Shift` to the outermost group.
- `Ctrl+L` links the selected elements in the order you selected them.
  `Ctrl+Shift+L` selects everything linked to the selection.
- Drag a selected link's line to add a **waypoint**. Double-click a waypoint
  to remove it. *Straighten Link* removes them all.
- Style it in the Attribute Editor: `routing` (straight, curved, elbow), the
  anchors, colour, thickness, dash, start and end caps (arrow, triangle,
  circle, diamond, bar) and a label.
- Commands: `links.connect(source, target, **attrs)` (each end is a node name
  or a free point `[x, y]`), `links.connect_selection`, `links.connected`.

### Groups, Outliner, Attribute Editor, quick editor (`grouping`, `outliner`, `inspector`, `quick_edit`)

- **Groups** (`Ctrl+G`; `Ctrl+Shift+G` ungroups) act as one item. Double-click
  a group to work inside it, and press `Esc` to leave. With `pick_members` on,
  a group is a plain folder whose members you click directly
  (`Ctrl+Shift+N` creates an empty one).
- **Outliner**: drag to reparent or reorder (front-most at the top), `F2`
  renames, and there are eye and lock columns and a name filter. The **Types**
  menu shows or hides types in the list, and its *On the Canvas* half has
  Show All, Hide All, Isolate, Lock All, Unlock All and Select All for each
  type. `Alt+H` shows everything again.
- **Attribute Editor**: a header card (name, visibility, lock, size and
  source) above collapsible sections. Plugin types get a generated section
  from their attribute schema, and dynamic attributes appear under *Extra
  attributes*. Drag a number field left or right to scrub it: `Shift` is
  faster, `Alt` finer, `Ctrl` snaps to the step and `Esc` reverts.
- **Quick editor**: a bar under the selection with the most-used attributes
  of each type. Its **⋯** menu chooses the fields for each type. Turn it on
  or off with *View ▸ Quick Attribute Editor*.
- **Panels** (Tools, Outliner, Attribute Editor) can be docked, floating or
  hidden. `Alt+T`, `Alt+O` and `Alt+A` pop them up, and `Tab` on the canvas
  hides all of them.
- Commands: `grouping.group/ungroup/add_group`, `parent`, `rename`,
  `outliner.isolate_types`, `outliner.set_type_flag`,
  `quick_edit.set_fields(type, [attrs])`, `panels.set_mode/popup/toggle`.

### Effects (`effects`)

- Any element can have an **effect stack**. The built-in effects are
  grayscale, invert, brightness/contrast, threshold, posterize, blur and edge
  detect. Effects apply from top to bottom, and each has a **Mix** amount.
- Add them from the Attribute Editor's *Effects* section or the canvas menu's
  *Effects*. `Ctrl+K` also finds *Add Effect: …*. `Shift+G` toggles grayscale
  on the selected images.
- Copy and paste stacks between elements. *Mute* keeps a stack but doesn't
  draw it. *Effects → Show Effects* hides every effect at once for a
  before-and-after comparison.
- An image's annotations are never affected by its effects. Groups can't have
  effects yet.
- Heavy effects run in the background and are cached in memory, on disk and
  in the scene file. Exports wait for every effect to finish.
- Commands: `effects.add(effect, nodes, **params)`, `effects.set`,
  `effects.move`, `effects.toggle`, `effects.mute`, `effects.copy/paste`,
  `effects.list`, `effects.save_default`, `effects.reset`. See
  [effects.md](effects.md).

### Views, markers and workspaces (`views`, window manager)

- Any number of canvas views show the same board. Split from a view's header
  (◫ splits right, ⬓ splits down) or by dragging a view's corner into it.
  Drag a corner onto a neighbouring view to join the two.
- **Display presets**: *All*, *Value check* (greyscale), *Images only*,
  *Annotations only*, *Clean*, plus your own (`views.create_display_preset`,
  which can filter by type and tag). `Shift+I` isolates the selection in one
  view, and `Alt+V` toggles value check.
- **Framing guides**: 16:9, 2.39:1, 1:1, 4:3 and 3:2
  (`views.frame_guide("16:9")`).
- **View markers** (`camera` nodes) are colour-coded places on the board that
  every view can see. The header's *Markers* menu creates, updates and
  recolours them. `Ctrl+1`…`Ctrl+9` fly to the markers in scene order. Each
  view shows its current marker in its header.
- **Linked views** share one camera. Use the header's *Link camera* menu, or
  `views.link(a, b)`.
- **Workspaces** are saved with the scene. The built-in ones are Board,
  Review (colour and value-check views, linked) and Present (one clean
  canvas). Workspaces can be saved as templates.
- Window mode: press `Ctrl+W`, then a key (see [4. Default keys](#4-default-keys)).
  The **scratchpad** holds hidden tiles such as the Script Editor.
- See [window-manager.md](window-manager.md).

### Arrange (`arrange`)

- `Ctrl+P` packs. `Ctrl+Alt+H` / `V` / `G` lay out in a row, a column or a
  grid. The *Arrange* menu also normalizes height, width or size and aligns
  edges and centres. With fewer than two items selected, arranging works on
  everything.
- Commands: `arrange(mode="optimal"|"horizontal"|"vertical"|"grid", nodes,
  gap)`, `align(edge)`, `normalize(mode)`.

### Files, export and recovery (`file`, `export`)

- `Ctrl+N`, `Ctrl+O`, `Ctrl+S` (new, open, save). An empty board shows a
  start panel with your recent files as thumbnails. Autosave writes a
  recovery copy every minute and offers it back after a crash.
- `Ctrl+E` renders the selection (or the whole board) to PNG or JPEG at 1:1.
  `Ctrl+Shift+E` copies the original image files into a folder.
- Commands: `file.open_path(path, confirm=False)`, `file.save_to(path)`,
  `save_scene`, `open_scene`, `import_scene`, `export.render(path, nodes)`,
  `export.originals(folder)`.

### Window modes (`window_modes`)

`Ctrl+T` keeps the window on top, `F11` makes it frameless, `G` turns on ghost
mode (a translucent window for tracing over other applications), and
`Alt`+drag moves the window.

### Automation (`console`, `macros`, `help`, `tool_palette`)

- **Script Editor** (``Ctrl+` ``): Python with `pxcmd`, `pxnode`, `sel()`,
  `session` and `app` predefined. Each run is one undo step. With *Echo all
  commands* on, every UI action is printed as its `pxcmd` call.
- **Scripts folder**: every `.py` file becomes a `script.<file>` action in
  the *Scripts* menu and in `Ctrl+K`, and can be given a key.
  `userSetup.py` runs at startup.
- **Record Macro** (`Ctrl+Alt+R`, or the record button in the bottom bar)
  saves what you do as a script.
- **Keyboard Shortcuts** (`F1`): change any key. Changes are written to
  `keymap.toml`, which can also bind command lists, Python and key modes.
- **Tools panel**: right-click a button to set its shortcut, move it or hide
  it. *Add Command…* puts any action on the palette, and *Customize Palette…*
  rearranges everything.
- **Command search** (`Ctrl+K`, or right-click and type) finds every action.
- See [keymap.md](keymap.md) and [scripting.md](scripting.md).

### Plugins and packages

*Window → Plugins…* enables and disables plugins while the app runs. Nodes of
a disabled plugin's types keep their data and show as placeholders. Plugins
that need Python packages are listed as *unavailable* until those packages
are installed, which the same window does (the *Python packages* tab). See
[plugins.md](plugins.md) and [packaging.md](packaging.md).

## 4. Default keys

Every key can be changed in `F1`. In a running app, `pxcmd.keymap.list()`
shows the actual bindings.

| Area | Keys |
| --- | --- |
| Everywhere | `Ctrl+K` command search · `F1` shortcuts · `Ctrl+Z` / `Ctrl+Y` undo / redo · `Esc` cancel or leave |
| Files | `Ctrl+N` new · `Ctrl+O` open · `Ctrl+S` save · `Ctrl+E` export image · `Ctrl+Shift+E` export originals · `Ctrl+I` insert images |
| Edit | `Ctrl+C` / `Ctrl+X` / `Ctrl+V` · `Ctrl+D` duplicate · `Del` delete · `Ctrl+A` select all |
| Navigate | wheel zoom · middle/right-drag or `Space`+drag pan · `F` frame selection · `Shift+F` / `Home` frame all · `Ctrl+0` 100% · tap `Space` maximise view · `Ctrl+1`–`9` view markers |
| Tools | `V` select · `T` text · `R` rect · `A` arrow · `P` pen · `Shift+P` edit drawing · `E` eraser · `I` colour picker · `W` link · `N` step · `B` blur · `X` pixelate · `C` crop |
| Transform | arrows nudge (`Shift` ×10) · `Ctrl+Wheel` scale · `Shift+H` / `Shift+V` flip · `Ctrl+R` / `Ctrl+Shift+R` rotate 90° · `Ctrl+Shift+0` reset · `Shift+C` reset crop |
| Arrange | `Ctrl+P` pack · `Ctrl+Alt+H` / `V` / `G` row / column / grid · `]` `}` `{` `[` stacking |
| Organise | `Ctrl+G` group · `Ctrl+Shift+G` ungroup or separate a drawing · `Ctrl+Shift+N` new group · `Ctrl+J` combine strokes · `F2` rename · `Alt+H` show all |
| Links | `Ctrl+L` link the selection · `Ctrl+Shift+L` select linked |
| Look | `Shift+G` grayscale effect · `Alt+V` value check · `Shift+I` isolate in view · `Ctrl+'` background pattern |
| Animation | `K` play/pause · `,` / `.` step · `Ctrl+,` / `Ctrl+.` first / last frame |
| Panels | `Alt+T` Tools · `Alt+O` Outliner · `Alt+A` or `Alt+Enter` Attribute Editor · `Tab` hide panels |
| Window | ``Ctrl+` `` Script Editor · `Ctrl+Alt+R` record macro · `Ctrl+T` on top · `F11` frameless · `G` ghost mode · `Ctrl+Alt+arrows` move focus |
| Window mode (`Ctrl+W`, then) | `H` `J` `K` `L` / arrows focus · `Shift`+those move · `S` / `V` next split beside / below · `N` new view · `T` tabbed · `Shift+S` stacked · `E` toggle orientation · `F` fullscreen · `Space` float · `R` resize mode · `=` equalize · `Q` close · `-` scratchpad · `O` tear off · `M` / `'` mark / jump · `Shift+R` rename · `1`–`9` workspace · `Shift+1`–`9` move view to workspace |

## 5. Data model

### 5.1 Nodes, attributes and transforms

- A node has a unique `name` (free-form, and images are named after their
  file), a permanent `id` (a UUID that references use, so renaming never
  breaks them), a `type`, `attrs`, optional `dynamic` attributes and ordered
  children.
- Attribute types: `bool`, `flag` (0/1), `int`, `float`, `float2`, `color`
  `[r, g, b]` with values 0–255, `color4` (with alpha), `string`, `text`,
  `enum`, `path`, `asset`, `node_ref`, `node_refs`, `string_list`, `rect`
  `[x, y, w, h]`, `points`, `any`. Values are validated and clamped to the
  attribute's range. Short names work (`t` for `translate`).
- Every type that is drawn inherits `canvas_node`:
  `tags`, `translate`, `rotate` (degrees), `scale`, `pivot`, `visibility`,
  `opacity` (0–1), `locked`, plus the computed `local_bounds`,
  `local_matrix`, `world_matrix`, `world_bounds`, `world_visibility`,
  `world_opacity` and `world_locked`. The effects plugin adds `effects` and
  `effects_muted` to every `canvas_node`, and `effects_all_frames` to images.
- The transform, as implemented in `pixelref/core/xform.py`:
  `parent_point = translate + pivot + R(rotate) · S(scale) · (local_point − pivot)`.
  A child's transform is relative to its parent.
- Abstract base types: `node` (only `tags`), `canvas_node`, `container`
  (holds children) and `dg_node` (data that isn't drawn).
- Capabilities let generic features work with any type: `content`,
  `container`, `compound`, `erasable` (the eraser can delete it) and
  `colorable` (the colour picker recolours its `color_attr`).

### 5.2 Node types

The attributes each built-in type adds to `canvas_node`. Hidden attributes,
such as link endpoints, are left out. Enum choices are in brackets.

| Type (plugin) | Attributes | Quick editor fields |
| --- | --- | --- |
| `image` (image) | `asset` (embedded file hash), `path` (linked file), `crop` | opacity |
| `group` (grouping) | `pick_members` | opacity |
| `annotation_text` (text) | `text`, `color`, `font_family`, `font_size`, `bold`, `italic`, `underline`, `strike`, `highlight_color`, `align` (left/center/right/justify), `background_color`, `fill_style`, `border_color`, `border_width`, `border_style`, `corner_radius`, `padding`, `text_width` | color, font_size, bold, italic, align, background_color |
| `annotation_rect` (rect) | `size`, `stroke_color`, `fill_color`, `fill_opacity`, `fill_style` (solid/dense/sparse/diagonal/cross/horizontal/vertical/grid/none), `thickness`, `stroke_style` (solid/dash/dot/dashdot/none), `corner_radius` | stroke_color, thickness, stroke_style, fill_color, fill_opacity, corner_radius |
| `annotation_line` (arrow) | `start`, `end`, `color`, `thickness`, `stroke_style`, `arrow`, `arrow_start` | color, thickness, stroke_style, arrow_start, arrow |
| `annotation_drawing` (pen) | none: a container of strokes | opacity |
| `annotation_path` (pen) | `color`, `thickness`, `stroke_style`, `smoothing`, `arrow`, `arrow_start` (and its points) | color, thickness, stroke_style, opacity |
| `annotation_step` (steps) | `number`, `label_style` (number/upper/lower/roman), `radius`, `shape` (circle/rounded/square), `tail`, `fill_color`, `text_color`, `border_color`, `border` | number, fill_color, text_color, radius, label_style |
| `annotation_blur` (redact) | `size`, `radius` | radius |
| `annotation_pixelate` (redact) | `size`, `cell_size` | cell_size |
| `link` (links) | `source_anchor` / `target_anchor` (auto/center/top/right/bottom/left/fixed), `routing` (straight/curved/elbow), `gap`, `color`, `thickness`, `stroke_style`, `start_cap` / `end_cap` (none/arrow/triangle/circle/diamond/bar), `cap_size`, `label`, `label_size` | color, thickness, stroke_style, routing, end_cap |
| `camera` (views): a view marker | `color`, `center`, `zoom`, `rotation`, `flip_x`, `flip_y` | — |
| `display_preset` (views; a `dg_node`) | `show_types`, `hide_types`, `show_tags`, `hide_tags`, `isolate`, `grayscale`, `background`, `show_grid`, `show_overlays`, `frame_guide` (none/16:9/2.39:1/1:1/4:3/3:2) | — |

Nodes of a type no active plugin provides keep their data, show as
placeholders and are saved unchanged.

### 5.3 Effect stacks

A node's `effects` attribute is a list of
`{"id", "type", "enabled", "mix", "params"}`. The built-in types and their
parameters:

| Effect | Parameters | Cost |
| --- | --- | --- |
| `grayscale` | `amount` 0–1 | cheap |
| `invert` | — | cheap |
| `brightness_contrast` | `brightness` −1–1, `contrast` −1–1 | cheap |
| `threshold` | `level` 0–1 | cheap |
| `posterize` | `levels` 2–32 | cheap |
| `blur` | `radius` (canvas pixels) | heavy |
| `edge_detect` | `strength` 0.5–10, `threshold` 0–1, `style` (lines/glow/overlay), `color` | heavy |

Cheap effects run while drawing, and on every frame of a playing animation.
Heavy ones run on worker threads and are cached in memory, on disk and in the
scene's `effect_cache`. Effects of an unknown type stay in the list and are
skipped when drawing.

### 5.4 The scene file

A `.pxref` file is JSON with these keys: `pixelref_version`, `schema` (1),
`thumbnail`, `types` (the schema version of each type), `plugins` (scene data
owned by plugins, such as the background and paused animation frames),
`nodes` (parents before children, in draw order), `workspaces`,
`effect_cache` and `assets` (embedded files keyed by SHA-1). Files with a
newer schema are refused, and a file that fails to load leaves the open scene
untouched. See [file-format.md](file-format.md).

## 6. Scripting API

```python
from pixelref import pxcmd, pxnode

# Create and find
img = pxcmd.import_image("refs/a.jpg", translate=[0, 0])     # returns the node name
note = pxcmd.create_node("annotation_text", parent=img, text="Rim light", translate=[20, -40])
rect = pxcmd.rect.create(0, 0, 300, 200, stroke_color=[255, 64, 64], thickness=6)
link = pxcmd.links.connect(note, rect, routing="curved", end_cap="arrow")
pxcmd.ls(type="image")                    # includes subtypes; also tags=, pattern=, visible=, roots=
pxcmd.children(img, all_descendants=True)
pxcmd.selected(); pxcmd.select([img, note]); pxcmd.select(clear=True)

# Attributes ("node.attr", or node and attr separately)
pxcmd.set_attr(f"{img}.rotate", 15)
pxcmd.set_attr(img, "opacity", 0.5)
pxcmd.set_attrs(img, {"opacity": 0.5, "rotate": 0})       # several at once
pxcmd.get_attr(f"{img}.world_bounds")                      # [x, y, w, h]
pxcmd.attr_info(img, "opacity")                            # type, range, default, doc
pxcmd.add_attr(img, "rating", "int", 3, minimum=0, maximum=5)   # a dynamic attribute
pxcmd.set_transform(img, translate=[10, 0], relative=True)

# Structure
pxcmd.parent(note, None)                  # reparent, keeping the board position
pxcmd.rename(note, "RimNote"); pxcmd.duplicate(img, offset=(300, 0)); pxcmd.delete("RimNote")
pxcmd.group([img, rect], "Lighting")

# Plugin commands are namespaced; many also have a flat alias
pxcmd.arrange(mode="grid")                # == pxcmd.arrange.arrange("grid")
pxcmd.effects.add("blur", img, radius=8)
pxcmd.steps.create(100, 100)
pxcmd.views.create_camera("Overview")     # a view marker

# Undo
images = pxcmd.ls(type="image", roots=True)
with pxcmd.undo_chunk("Lay out refs"):    # everything inside is one undo step
    pxcmd.arrange(mode="optimal", nodes=images)
    pxcmd.align("top", images)            # most commands act on the selection when nodes are left out
pxcmd.undo(); pxcmd.undo_info()

# Files
pxcmd.save_scene("board.pxref"); pxcmd.open_scene("other.pxref"); pxcmd.import_scene("more.pxref")
pxcmd.export.render("board.png")          # the desktop app only

# Objects instead of names
n = pxnode.node(img); n.opacity = 0.5; n.rename("castle"); [c.name for c in n.children]

# Callbacks
cid = pxcmd.callbacks.add("selection.changed", lambda e: print(e.payload["names"]))
pxcmd.callbacks.remove(cid)
```

- **Errors.** A failing command raises `CommandError` or `SceneError`, and its
  changes are rolled back. Unknown attributes are errors.
- **Undo.** Each top-level command, Script Editor run, script file or macro
  is one undo step. Camera commands (`navigation.*`, `views.look_through`)
  are never undoable.
- **Events.** `selection.changed`, `document.changed`, `document.replaced`,
  `command.finished`, `scene.opened`, `scene.saved`, `revision.changed`,
  `plugins.changed`, and the document events `node.created`, `node.deleted`,
  `node.renamed`, `node.reparented`, `node.reordered`, `node.attr_changed`,
  `node.attr_added`, `node.attr_deleted`, `asset.added` and `scene.loaded`
  (`"node.*"` matches all of the node events).
- **Batch mode** has no window. It runs every plugin's headless half: node
  types, document commands, layout, transforms and files. Commands that need
  a window (dialogs, views, tools, `export.render`) aren't available.

  ```bash
  python -m pixelref --batch build.py board.pxref --save out.pxref --plugin-path my_plugins
  ```

  ```python
  from pixelref.core.session import Session      # or in any Python program
  Session().start(); pxcmd.open_scene("board.pxref")
  ```

See [scripting.md](scripting.md) and [commands.md](commands.md).

## 7. Recipes

User folders on Windows, with the equivalent Qt locations elsewhere:

| What | Where |
| --- | --- |
| scripts, `userSetup.py`, `keymap.toml` | `%LOCALAPPDATA%\pixelRef\pixelRef\` (`scripts\` and `keymap.toml`) |
| user plugins | `%APPDATA%\pixelRef\pixelRef\plugins\` |
| more plugins / scripts | `$PIXELREF_PLUGIN_PATH`, `--plugin-path DIR` / `$PIXELREF_SCRIPT_PATH` |

### 7.1 A script in the Script Editor or batch mode

```python
"""Put every image in a grid, tag the large ones, and frame the result"""
images = pxcmd.ls(type="image", roots=True)
with pxcmd.undo_chunk("Tidy images"):
    pxcmd.arrange(mode="grid", nodes=images, gap=24)
    for name in images:
        x, y, w, h = pxcmd.get_attr(f"{name}.world_bounds")
        if w * h > 1_000_000:
            pxcmd.set_attr(name, "tags", pxcmd.get_attr(name, "tags") + ["large"])
pxcmd.navigation.frame(images)        # skip this line in batch mode: there are no views
```

### 7.2 A script file that becomes a command

Save a `.py` file in the scripts folder (*Scripts → Open Scripts Folder*). It
becomes the action `script.<file>`, its docstring's first line becomes its
title, and you can give it a key in `F1`. `pxcmd`, `pxnode`, `sel()`,
`session` and `app` are predefined. The run is one undo step, and an
exception undoes it. Files whose names start with `_` aren't listed, so use
them for shared helpers.

```python
"""Stamp a numbered step on each selected image"""
for name in sel():
    x, y, w, h = pxcmd.get_attr(f"{name}.world_bounds")
    pxcmd.steps.create(x + 24, y + 24)
```

*Record Macro* (`Ctrl+Alt+R`) writes a file like this from what you do.
Recorded macros select nodes by name, so remove those `pxcmd.select([...])`
lines to make a macro act on the current selection.

### 7.3 A macro in `keymap.toml`

```toml
[[bind]]                          # a list of commands, as one undo step
key = "Ctrl+Alt+T"
name = "Tidy board"
commands = ["select.all", "arrange.grid", "navigation.frame_all"]

[[bind]]                          # Python
key = "Ctrl+Alt+1"
name = "Rotate 45"
python = "pxcmd.set_transform(sel(), rotate=45, relative=True)"
when = "selection.count > 0 && !editing.text"

[[mode]]                          # a key mode: Ctrl+Space, then 1
name = "macros"
key = "Ctrl+Space"

[[bind]]
mode = "macros"
key = "1"
script = "stamp_steps.py"
```

The context keys for `when` are `selection.count`, `selection.types`,
`tool.active`, `editing.text`, `canvas.focused`, `wm.mode`,
`wm.focused_view_type`, `pen.session_active`, `links.selected` and
`playback.selected`. They combine with `== != < > <= >= && || !`. From
scripts, use `pxcmd.set_shortcut("tool.pen", "B")`,
`pxcmd.bind_key(key, action)` (this session only) and
`pxcmd.run_macro(commands=[…])`. See [keymap.md](keymap.md).

### 7.4 A plugin with commands, keys and menus

```text
my_tidy/
  plugin.toml
  __init__.py
```

```toml
id = "my_tidy"          # snake_case: the namespace of its commands
name = "Tidy"
version = "1.0.0"
api = 1
description = "Snap the selection's rotation to the nearest 15 degrees."
```

```python
from pixelref.plugin_api import CommandError


def activate(ctx):                                    # headless: also runs in batch mode
    def snap_rotation(step: float = 15.0) -> list[str]:
        """Round the rotation of the selected items to a multiple of ``step``."""
        names = ctx.session.selection.names
        if not names:
            raise CommandError("Nothing selected")
        for name in names:
            angle = ctx.run("get_attr", f"{name}.rotate")
            ctx.run("set_attr", f"{name}.rotate", round(angle / step) * step)
        return names

    ctx.command("snap_rotation", snap_rotation, alias="snap_rotation", label="Snap Rotation")


def activate_ui(ui):                                  # Qt: the desktop app only
    ui.action("my_tidy.snap", "my_tidy.snap_rotation", "Snap Rotation", category="Transform",
              keywords="angle straighten round")
    ui.bind("Ctrl+Alt+S", "my_tidy.snap", when="selection.count > 0")
    ui.menu("canvas", "my_tidy.snap", path="Transform", group="transform", when="selection.count > 0")
```

Load it with `python -m pixelref --plugin-path DIR`, or copy it into the user
plugin folder. `pxcmd.reload_plugin("my_tidy")` re-imports it while you're
developing it. Nested `ctx.run` calls join the outer command's undo step.

### 7.5 A plugin with a node type and a tool

Copy [`examples/plugins/ellipse`](../examples/plugins/ellipse/__init__.py).
It shows each layer:

1. `NodeType(name, inherits="canvas_node", attrs=(Attr(...), …),
   capabilities=…, color_attr=…, quick_attrs=(…), compute={"local_bounds": fn})`,
   registered in `activate` with `ctx.register_node_type`. `local_bounds`
   makes framing, arranging and `world_bounds` work without the UI.
2. A creating command (`ellipse.create`) that the tool and scripts both call.
3. In `activate_ui`, a `QGraphicsItem` subclass that also inherits
   `NodeItemMixin`. It draws in `paint_content` (not `paint`), rebuilds itself
   from attributes in `apply_attrs`, and returns its local rectangle from
   `content_rect`. Register it with
   `ui.node_ui(type, factory=…, label=…, icon=…, badge=(colour, letter))`.
4. A `Tool` subclass (`id`, `title`, `icon`, `shortcuts`, `pointer(e)`,
   `paint_overlay`, `option_specs` for its options) that runs the command on
   release. Register it with `ui.register_tool(...)`.

The Attribute Editor section is generated from the attribute schema, so it
needs no code. Editing hints such as `soft_min`, `soft_max`, `step`,
`precision`, `unit` and `display_scale` shape its fields. See
[plugins.md](plugins.md#node-types).

### 7.6 An effect type

```python
# my_effects/__init__.py  (plugin.toml: id = "my_effects", api = 1)
from pixelref.plugin_api import Attr
from pixelref.plugin_api.contracts import COST_CHEAP, EffectType

TINT = EffectType("tint", "Tint", (
    Attr("tone", "color", [255, 214, 160], doc="Colour multiplied over the greyscale image"),
), category="Color", cost=COST_CHEAP, version=1)


def activate(ctx):
    ctx.contribute("effects.types", TINT)             # data: also known in batch mode


def activate_ui(ui):
    from PySide6.QtGui import QColor, QImage, QPainter

    def tint(image: QImage, params: dict, fx) -> QImage:   # ARGB32 in, QImage out, on a worker thread
        out = image.convertToFormat(QImage.Format_Grayscale8).convertToFormat(QImage.Format_ARGB32_Premultiplied)
        painter = QPainter(out)
        painter.setCompositionMode(QPainter.CompositionMode_Multiply)
        painter.fillRect(out.rect(), QColor(*params["tone"]))
        painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        painter.drawImage(0, 0, image)                # keep the original transparency
        painter.end()
        return out

    ui.contribute("effects.processors", ("tint", tint))
```

- Processors run on worker threads. Use only `QImage` and `QPainter`, never
  `QPixmap` or widgets. Sizes are in canvas pixels, so multiply them by
  `fx.pixels_per_unit`. `fx.playing` is true during animation playback.
- Every effect gets a **Mix** amount for free, so a strength parameter is
  only needed when it changes the algorithm.
- Use `cost=COST_HEAVY` for anything slow. Heavy effects run in the
  background, are cached on disk and in scenes, and are skipped while an
  animation plays. Bump `version` whenever the algorithm changes.
- Pixel loops in Python are slow. Use `QPainter` composition modes as above,
  or numpy in a plugin with `[python] requires = ["numpy"]`. **Import numpy
  inside the processor** so that loading the plugin stays free. The *Plugins*
  window installs missing packages.
- Once it's loaded, the effect appears under *Add effect* and in `Ctrl+K`, and
  `pxcmd.effects.add("tint", node, tone=[160, 200, 255])` adds it from
  scripts. See [effects.md](effects.md).

### 7.7 Attributes on types you don't own

```python
def activate(ctx):
    ctx.extend_node_type("image", (Attr("rating", "int", 0, minimum=0, maximum=5, group="Review"),))
```

Extension attributes are sparse: a node stores one only after it's set, so
scenes that never use it are saved unchanged. They're inherited by subtypes,
and their values are kept while the plugin is off. Without a custom section
they appear in the generated *Attributes* section. Pass `hidden=True` if you
build your own UI for them.

### 7.8 Attribute Editor and quick editor sections

```python
def activate_ui(ui):
    from PySide6.QtWidgets import QLabel

    from pixelref.plugin_api.contracts import InspectorSection
    from pixelref.plugin_api.ui import widgets as w

    class SizeSection(w.InspectorSection):            # a collapsible section with a form
        def __init__(self, parent):
            super().__init__("pixel_size", "Pixel size", parent)
            self.label = QLabel(self.body)
            self.form.addRow("Total", self.label)

        def refresh(self, info):                      # info: SelectionInfo (names, nodes, types, attrs, single)
            total = sum(ui.session.run("get_attr", f"{n}.world_bounds")[2]
                        * ui.session.run("get_attr", f"{n}.world_bounds")[3] for n in info.names)
            self.label.setText(f"{total / 1e6:.2f} megapixels on the board")

    ui.contribute("inspector.sections", InspectorSection(
        key="pixel_size", title="Pixel size", order=60,
        applies=lambda info: bool(info.names) and info.types == {"image"},
        build=lambda parent, inspector: SizeSection(parent),
        generic=True))                                # True: keep the type's generated section too
```

- `widgets.editor_for(attr, parent)` builds the same editors the Attribute
  Editor uses. `widgets.bind_edits(app, editor, apply, label)` connects one to
  a command, so that a drag previews live and becomes one undo step.
- `QuickSection(key, title, order, applies(names), build(parent))` adds
  controls to the canvas quick editor bar. The widget needs a
  `refresh(names)` method and a fixed size.
- `inspector.rows` adds rows inside another plugin's section, and
  `inspector.empty_panels` adds panels shown when nothing is selected.

### 7.9 Views and panels

```python
from pixelref.plugin_api.ui import PanelType, ViewType

ui.view_type(ViewType(id="stats", title="Statistics", icon="inspector",
                      factory=lambda tile, state: StatsWidget(ui, state),
                      save_state=lambda widget: widget.state(), placement="tabbed"))

ui.panel(PanelType(id="swatches", title="Swatches", icon="pick",
                   factory=lambda parent: SwatchPanel(ui), default_mode="docked",
                   popup_keys=("Alt+S",), order=30))
```

A **view** is a tile in the window manager. Users open it with `Ctrl+W` `N`,
and scripts with `pxcmd.wm.open_view("stats")`. A **panel** sits beside the
tiles and is used to edit the board, like the Outliner. A panel's factory can
be called more than once, so keep state in a service, not in the widget.

### 7.10 Other extension points

| Point | Contribute | Use it to |
| --- | --- | --- |
| `ingest.handlers` | `IngestHandler(name, order, accepts(mime), describe(mime), ingest(view, pos, mime))` | turn drops and pastes into nodes (for example, URLs or a new file type) |
| `clipboard.exporters` | `fn(names, QMimeData)` | add clipboard formats when copying |
| `canvas.item_hooks` | `fn(item, node)` | add paint filters that change how any node is drawn |
| `gizmo.handles` | a handle provider for one type | add handles of its own to a type (see `rect/ui.py`) |
| `eraser.policy` | `fn(item) -> bool` | protect items from the eraser |
| `grouping.ungroup` | `UngroupHandler(node_type, label, done_message)` | define what *Ungroup* means for a type |
| `palette.buttons` | `PaletteButton(key, label, icon, order)` | add a button to the Tools panel's *Add* row |
| `stroke.styles` | `StrokeStyle(node_type, color_attr, …)` | choose which Stroke-section controls a type has |
| `scene.before_save`, `scene.after_new` | `fn(session, path)`, `fn(session)` | update plugin data when a scene is saved or created |
| `canvas.overlays`, `canvas.header_widgets`, `wm.bar_blocks` | painters and widgets | paint over views, or add widgets to view headers and the bottom bar |
| `app.close_handlers`, `escape` | `fn() -> bool` | veto closing, or handle `Esc` first |

Plugins also share **services**: `ctx.provide_service(name, obj)` and
`ctx.service(name)`. Built-in services include `brush`, `images`,
`transform`, `ingest`, `gizmo`, `inspector`, `outliner`, `links`, `effects`,
`playback` and `console`. You can declare your own extension points with
`ctx.declare_extension_point(name, doc)`. See
[plugins.md](plugins.md#cooperating-with-other-plugins).

### 7.11 Testing a plugin

```python
from pixelref.core.session import Session

session = Session(plugin_paths=["path/to/plugins"]).start()   # headless
session.run("my_tidy.snap_rotation")
```

For tests of the UI, `tests/harness.py` starts the whole app offscreen
(`Harness(plugin_paths=…)`, with helpers such as `add_image`, `drag`, `key`
and `grab`). `tests/test_kernel.py::test_external_plugin_example_ellipse` is a
worked example. Run the tests with `python tests/run_tests.py [name]`.

## 8. Rules for code changes

These apply to changes to pixelRef itself and to its built-in plugins.

- **Plugins import only** `pixelref.plugin_api` and `pixelref.plugin_api.ui`,
  never another plugin. They cooperate through services, extension points,
  events and context keys, so that each plugin can be disabled on its own.
- **`core/` never imports Qt.** Data goes in `activate`, and anything visual
  goes in `activate_ui`.
- **Tools and widgets run commands.** They don't change the scene directly.
  This keeps every change undoable, echoed and scriptable. Interactive edits
  commit through `ui.canvas.commit_dirty()` or a command.
- **Canvas items hold no state of their own.** They're rebuilt from node
  attributes whenever the node changes.
- **Register everything through `ctx` or `ui`.** Then disabling a plugin
  removes it cleanly.
- **Import heavy packages inside the functions** that use them.
- **There is no legacy layer.** pixelRef 1.0 reads only `.pxref` schema 1. Do
  not add compatibility shims for older formats or APIs. Node types that
  change their stored data raise `schema_version` and provide `upgrade()`.
- **Naming.** The public API is `pixelref.pxcmd` and `pixelref.pxnode`.
  Plugin commands are `<plugin_id>.<name>`, with an optional flat alias.
  Scenes use the `.pxref` extension. The version is defined once, in
  `pixelref/__init__.py`.
- **After changing commands**, run `python tools/gen_command_reference.py`.
  After changing how the UI looks, run `python tools/gen_screenshots.py` to
  refresh the README images. Run `python tests/run_tests.py` before sending
  a change.

## 9. Where things live

| Path | Holds |
| --- | --- |
| `pixelref/core/` | the headless kernel: `session.py`, `scene.py` (the scene graph), `nodetypes.py`, `attributes.py`, `commands.py`, `undo`, `plugins.py`, `scene_file.py`, `xform.py`, `packages.py` |
| `pixelref/plugin_api/` | the plugin API: `__init__.py` (headless), `ui.py` (Qt) and `contracts.py` (InspectorSection, QuickSection, IngestHandler, EffectType…) |
| `pixelref/ui/` | the Qt kernel: `app.py` (App, UIContext, actions, menus), `canvas/` (document, views, items, tools, input), `wm/` (window manager), `panels.py`, `widgets.py`, `command_search.py` |
| `pixelref/builtins/<plugin>/` | one folder per feature. Each has `plugin.toml` and `__init__.py`, and often a `ui.py` |
| `pixelref/pxcmd.py`, `pxnode.py`, `batch.py` | the scripting API and batch mode |
| `examples/plugins/ellipse/` | the commented example plugin |
| `examples/scenes/animals.pxref` | the sample board |
| `tests/` | headless end-to-end tests (`harness.py`, `run_tests.py`) |
| `tools/` | generators for `docs/commands.md`, the README screenshots and the logo |
| `packaging/` | the PyInstaller build and its smoke test |

Which built-in plugin provides each feature:

| Plugin | Feature |
| --- | --- |
| `arrange` | pack, row, column, grid, normalize, align |
| `arrow`, `rect`, `text`, `pen`, `steps`, `redact` | the annotation types and their tools |
| `brush` | per-tool colour and width, recent colours, the Stroke section |
| `canvas_background` | background colour and pattern |
| `clipboard` | copy, paste, duplicate, delete; ingesting drops and pastes |
| `color_picker`, `eraser`, `crop` | the colour picker, eraser and crop tools |
| `console` | the Script Editor |
| `contact_sheet`, `minimap` | the contact sheet view and the minimap |
| `effects` | effect stacks, the effect engine and caches |
| `export`, `file`, `image_storage` | export, scene files, autosave and recovery, embedding and linking |
| `gizmo`, `select`, `transform`, `order`, `navigation` | selection, the gizmo, transforms, stacking, the camera |
| `grouping`, `outliner`, `inspector`, `quick_edit`, `tool_palette` | groups and the panels |
| `help` | `F1` keyboard shortcuts and About |
| `image`, `playback` | image nodes, GIF/WebP playback |
| `links` | links |
| `macros` | the scripts folder, Record Macro and `keymap.toml` macros |
| `views` | view markers, display presets, linked views, framing guides, the built-in workspaces |
| `window_modes` | always on top, frameless, ghost mode |

## 10. Frequently asked questions

**How do I make a board that's one portable file?** Images are embedded by
default. For images that are linked, use *File → Embed All Linked Images*,
then save.

**How do I compare values without colour?** Press `Alt+V` in a view, or
switch to the *Review* workspace, which has a colour view and a value-check
view linked side by side.

**How do I hide a face or a name in a screenshot?** Use the blur (`B`) or
pixelate (`X`) tool, and drag over the area. The region follows the image and
exports exactly as you see it.

**How do I point at a detail inside an image?** With the Link tool (`W`),
drop the end inside the image, away from its centre and edges. The end pins
to that spot.

**How do I number the steps of a process?** Use the Step tool (`N`) and click
in order. *Renumber Steps* closes the gaps after deletions.

**How do I go back to the same place on the board?** Use the header's
*Markers* menu to save a view marker, and `Ctrl+1`…`Ctrl+9` to return to it.

**How do I present without the UI?** Switch to the *Present* workspace (the
workspace buttons, or `Ctrl+W` then `3`), or press `Tab` to hide the panels.

**How do I trace over another application?** Press `G` for ghost mode
(translucent), and `Ctrl+T` to keep the window on top.

**How do I see the command behind a button?** Open the Script Editor
(``Ctrl+` ``) with *Echo all commands* on, then click the button.

**How do I run something on every image in many files?** Use batch mode with
a script, for example
`python -m pixelref --batch fix.py board.pxref --save board.pxref`, in a shell
loop over the files.

**How do I give a script a key?** Put the script in the scripts folder, then
give it a key in `F1` (category *Scripts*), or bind it in `keymap.toml` with
`script = "file.py"`.

**Why is a plugin "unavailable"?** It needs Python packages that aren't
installed yet. Open *Window → Plugins…* and click *Install what's needed*.

**Why does an element show as a placeholder?** Its type comes from a plugin
that is disabled or missing. The element's data is kept. Enable the plugin to
see it again.
