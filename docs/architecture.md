# Architecture

pixelRef is a small **kernel** plus **plugins**. The kernel knows how to hold
a document, run commands, undo, load plugins, route input and lay out views.
It does not know what an image, a pen stroke or the outliner is. Every
feature comes from a plugin in `pixelref/builtins/`, and third-party plugins
use exactly the same API.

```text
            ┌──────────────── plugins (pixelref/builtins, your plugins) ───────────────┐
            │ node types · commands · tools · views · actions · keys · menus · services │
            └──────────────┬──────────────────────────────────────┬────────────────────┘
                           │ PluginContext (headless)             │ UIContext (Qt)
  ┌────────────────────────▼─────────────┐   ┌────────────────────▼──────────────────────────┐
  │ core — the headless kernel           │   │ ui — the Qt kernel                             │
  │ Session: SceneGraph · NodeTypeRegistry│   │ App: ActionRegistry · Keymap · MenuRegistry    │
  │ CommandRegistry · UndoManager         │◄──┤ InputPipeline · ToolManager · CanvasDocument   │
  │ Selection · ContextKeys · events      │   │ CanvasView(s) · WindowManager (layout graph)   │
  │ ExtensionRegistry · ServiceRegistry   │   └────────────────────────────────────────────────┘
  │ PluginManager · SceneParser (.pxref)  │
  └──────────────▲────────────────────────┘
                 │ pixelref.pxcmd / pixelref.pxnode — used by the UI, scripts, the console, batch mode
```

The `core` package never imports Qt. A `Session` with its plugins' headless
halves runs anywhere: in batch mode, in tests, or from a plain Python script.

## The document: nodes and attributes

The document is a tree of **nodes**. There is one node per
element on the board (no transform/shape split). Each node has:

- a globally **unique name** (`snake_case` attributes, free-form names; images
  are named after their file), plus a permanent **id** (UUID) that references
  use, so renaming never breaks them;
- a **type**, registered by a plugin as a `NodeType`;
- **attributes**: plain JSON-friendly values, validated against the type's
  `Attr` specs (`bool`, `flag`, `int`, `float`, `float2`, `color`, `color4`,
  `string`, `text`, `enum`, `path`, `asset`, `node_ref`, `node_refs`,
  `string_list`, `rect`, `points`, `any`). Values are clamped to `minimum` /
  `maximum` and enums are checked;
- optional **dynamic attributes**, added per node with `pxcmd.add_attr` and
  saved with the scene;
- an ordered list of **children**. Sibling order *is* the draw order (back
  first). There is no `z_index`.

Node types inherit. The kernel defines the abstract bases:

| Type | Attributes | Meaning |
| --- | --- | --- |
| `node` | `tags` | everything |
| `canvas_node` | `translate`, `rotate`, `scale`, `pivot`, `visibility`, `opacity`, `locked` + computed `local_bounds`, `local_matrix`, `world_matrix`, `world_bounds`, `world_visibility`, `world_opacity`, `world_locked` | anything drawn on the board |
| `container` | — | a `canvas_node` that holds children (groups) |
| `dg_node` | — | data that isn't drawn (cameras, display presets) |

Every element on the canvas uses one transform model:

```text
parent_point = translate + pivot + R(rotate) · S(scale) · (local_point − pivot)
```

`pixelref.core.xform` implements it, and the Qt items use the same maths.
Computed attributes such as `world_bounds` are answered by the kernel. They use
the type's `compute` functions, or the canvas when it knows an item's drawn
extent better. So scripts can measure things in batch mode too.

Built-in types: `group` (grouping), `link` (links), `image` (image),
`annotation_text` (text), `annotation_rect` (rect), `annotation_line` (arrow),
`annotation_path` and `annotation_drawing` (pen), `camera` and
`display_preset` (views).

Capabilities on a type (`content`, `container`, `compound`, `erasable`,
`colorable`) let generic features work with types they have never heard of.
The eraser deletes anything `erasable`. The colour picker recolours anything
`colorable` through the type's `color_attr`.

**Unknown types.** A file may contain nodes of a type no active plugin
provides (the plugin is missing or disabled). Those nodes keep all their data
and their type's schema version, show as placeholders on the canvas, and save
unchanged.

## Commands, undo and the console

Every user action is a **command**: a Python function registered under a name.
Kernel commands have flat names (`create_node`, `set_attr`, `parent`…).
Plugin commands are namespaced by plugin id (`transform.flip`, `pen.combine`)
and may have a flat alias (`flip`). Buttons, menus, keys, tools, scripts and
the console all run commands through `CommandRegistry.execute`, which:

- opens an **undo chunk** around a top-level command. Nested commands join it,
  and a failing command is rolled back;
- runs **tidy hooks** at the end of the outermost undoable command, inside the
  same undo step. For example, the pen removes drawings left without strokes;
- **echoes** the equivalent `pxcmd.…(…)` call. The Script Editor prints these
  with *Echo all commands*;
- notifies listeners afterwards. The session emits `document.changed`
  whenever an undoable command ran, even inside a non-undoable UI wrapper, and
  the canvas, outliner and Attribute Editor refresh.

Undo is **snapshot based**. Each step stores the serialized document, without
image data, from before the change. Scripts (`with pxcmd.undo_chunk():`, every
console run), long canvas drags and coalesced repeats (arrow-key nudges,
Ctrl+wheel scaling) are one step each. Camera moves (pan, zoom, framing) are
never undoable. Creating or editing camera bookmarks is.

`pixelref.pxcmd` is the command registry of the *current* session, exposed as
module attributes. `pixelref.pxnode` wraps it in node objects
(`pxnode.node("Img").opacity = 0.5`). See [scripting.md](scripting.md).

## Plugins

A plugin is a folder with a `plugin.toml` and an `__init__.py` that defines
`activate(ctx)` (headless) and/or `activate_ui(ui)` (Qt). Everything a plugin
registers goes through its context and is **disposable**. Disabling a plugin
at runtime removes its node types, commands, tools, views, actions, keys,
menus, services and contributions, and re-enabling it brings them back.

Plugins cooperate through:

- **extension points**: named lists that one plugin declares and others
  contribute to. For example, `ingest.handlers` turns drops and pastes into
  nodes, `inspector.sections` adds Attribute Editor sections, `gizmo.handles`
  adds per-type handles, and `grouping.ungroup` lets a type define what
  ungrouping it means;
- **services**: named objects one plugin provides and others use (`brush`,
  `images`, `transform`, `ingest`, `gizmo`…);
- **events** and **context keys**, such as `selection.count` and
  `editing.text`, used in `when` conditions of key bindings and menus.

Foundation plugins (select, navigation, gizmo, outliner, inspector,
tool_palette, file, clipboard, brush, views, console) can't be disabled. The
others can, in *Window → Plugins…*. The full guide is in [plugins.md](plugins.md).

Plugins can also add **sparse attributes to types they don't own**
(`ctx.extend_node_type("canvas_node", …)`). They can change how *any* node is
drawn through **paint filters** installed from a `canvas.item_hooks`
contribution: items draw their content in `paint_content`, and the filters
wrap it. Child items are never filtered with their parent. Effect stacks are
built this way.

A plugin may need **Python packages** (`[python] requires` in its manifest).
Until they're installed it is *unavailable*, not broken. The Plugins window
installs them into a managed folder that goes on `sys.path` before plugins
load (`core/packages.py`, [packaging.md](packaging.md)).

## The Qt side

`pixelref.ui.App` owns the UI registries:

- **ActionRegistry**: one `QAction` per action key (`transform.flip_h`). Its
  text, icon, keywords for command search and checked/enabled state are shared
  by menus, buttons and the search.
- **Keymap**: key bindings to actions or commands, with a `scope` (window or
  canvas) and `when` conditions (`selection.count > 0 && !editing.text`).
  *Modes* (window, resize) take over the keyboard until `Esc`, i3-style.
  `keymap.toml` in the user config folder adds, removes and rebinds keys
  (`ui/keymap_file.py` reads and edits it; the `help` plugin's shortcut
  editor writes it). A key the user removes is filtered out rather than
  deleted, so plugins re-enabled later can't bring it back. Binds that run
  command lists, Python or scripts become `user.*` actions run by the
  `macros` plugin, which also turns the scripts folder into `script.*`
  actions and records macros. See [keymap.md](keymap.md).
- **MenuRegistry**: the canvas context menu, built from contributions with
  paths (`View/Background`), groups and `when` conditions. Typing in it
  searches every action (command search, `Ctrl+K`).
- **InputPipeline**: canvas pointer, key and wheel events go to handlers by
  priority, highest first: Ctrl+wheel scaling 950, navigation 900, playback
  controls 700, the link grabber 610, gizmo 600, the active tool 500, window
  dragging 450.
- **ToolManager**: tools are `Tool` subclasses registered by plugins. Each one
  has an id, shortcut, cursor, options panel, overlay painting and pointer and
  key handling.
- **CanvasDocument**: one `QGraphicsScene` mirroring the node tree. It updates
  incrementally: only nodes whose type, parent, attributes or dynamic
  attributes changed get new items. Every `CanvasView` shows this scene with
  its own camera and display settings.
- **PanelManager**: the Tools, Outliner and Attribute Editor (and plugin
  panels) outside the tiling layout: docked in the sidebar, floating over the
  canvas, or popped up on demand.
- **WindowManager**: the i3-style tiling layout of views. The layout is
  itself a small node graph (`wm_output`, `wm_workspace`, `wm_split`,
  `wm_tabbed`, `wm_stacked`, `wm_tile`, `wm_scratchpad`) saved in the user
  settings, and optionally in the scene. See [window-manager.md](window-manager.md).

Plugins draw nodes by registering a **node UI** for their type: a factory that
builds a `QGraphicsItem` (subclassing `NodeItemMixin`) from the node's
attributes. Items are always rebuildable from attributes; they hold no state
of their own. When the user drags or edits on the canvas, the item reports
new attribute values, and the canvas commits them with `set_attrs` commands.

## Files

Scenes are readable JSON `.pxref` files (schema 1) with embedded images,
written and read by `core/scene_file.py` (`SceneParser`). `.json` files open as
scenes too. See [file-format.md](file-format.md).

## Environment variables

| Variable | Effect |
| --- | --- |
| `PIXELREF_PLUGIN_PATH` | extra plugin folders, separated like `PATH` ([plugins.md](plugins.md#layout-and-loading)) |
| `PIXELREF_USER_PLUGINS` | replaces the user plugin folder |
| `PIXELREF_SCRIPT_PATH` | extra folders for scripts and `userSetup.py` ([scripting.md](scripting.md#usersetuppy), [keymap.md](keymap.md#scripts)) |
| `PIXELREF_PACKAGES_DIR` | replaces the managed Python package folder ([packaging.md](packaging.md#the-managed-package-folder)) |
| `PIXELREF_TILES` | the canvas tile cache: `1` always on, `0` off, unset: scenes of 10+ items |

## Goals and non-goals

A REST/WebSocket server is not a goal; integrations use the in-process Python
API (`pxcmd`), the console, or batch mode.
