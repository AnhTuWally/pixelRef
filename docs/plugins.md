# Writing plugins

Every feature of pixelRef is a plugin, and the built-in ones in
`pixelref/builtins/` use the same API as yours. They make good examples: the
`rect` plugin is a complete node type with a tool, and `order` is a few
commands with keys and menus. [`examples/plugins/ellipse`](../examples/plugins/ellipse/__init__.py)
is a commented example to copy.

## Layout and loading

> Plugins run with the user's full permissions, so pixelRef won't run yours
> until the user approves it, and asks again whenever its files change. Say
> who you are, declare what your plugin does, and sign your releases: see
> [Staying safe with plugins and scripts](security.md) for what users see.

```text
my_plugin/
  plugin.toml
  __init__.py      # activate(ctx) and/or activate_ui(ui); may import sibling modules
  README.md        # optional: the long description on the plugin's page (and in Help)
  SIGNATURE.toml   # optional: written by `python -m pixelref plugin sign`
  wheels/          # optional: wheels of the Python packages it needs (offline installs)
```

```toml
id = "my_plugin"            # snake_case, 2-64 characters; also the namespace of its commands
name = "My plugin"
version = "1.0.0"
api = 1                     # must equal pixelref.plugin_api.API_VERSION
description = "One line shown in the plugin manager."
requires = ["brush"]        # plugins that must be active first
optional = ["image"]        # loaded first if present, not required
enabled_by_default = true   # only for signed plugins from a publisher the user trusts
license = "MIT"             # SPDX identifier
readme = "README.md"        # default: README.md if there is one
category = "Drawing"        # groups and searches in the plugin manager
tags = ["shapes", "vector"]
min_app_version = "1.0.0"   # the oldest pixelRef it works with
overrides = []              # names it takes over from other plugins (see below)
# replaces = "my_plugin"    # only when it is a drop-in replacement of the built-in plugin with this id

[author]                    # who made it
name = "Jo Maker"
email = "jo@example.com"
organization = "Example Studio"

[source]                    # where it comes from
homepage = "https://example.com/my-plugin"
repository = "https://github.com/example/my-plugin"
issues = "https://github.com/example/my-plugin/issues"

[permissions]               # what it does beyond drawing on boards (all false by default)
network = false             # connects to other computers
subprocess = false          # starts other programs
native_code = false         # uses ctypes / loads native libraries
write_files = false         # writes, deletes or renames files (pixelRef saving for it doesn't count)
read_sensitive = false      # reads SSH keys, browser profiles, cloud credentials…
dynamic_code = false        # exec()/eval()/compile() of strings

[python]                    # Python packages (optional section)
requires = ["numpy>=1.26"]  # the plugin can't activate without them
optional = ["scipy"]        # used when installed
# when the import name differs from the package name:
# requires = [{ name = "opencv-python", import = "cv2", version = ">=4.8" }]
```

Top-level keys must come before the first `[table]`: TOML puts everything
after `[source]` into `[source]`. The plugin manager lists unknown keys as
warnings on the plugin's page. `author.name`, `license` and
`source.homepage` (or `source.repository`) are required to sign a plugin.

### Where plugins are found

Plugins are found in *sources*, in this order:

1. `pixelref/builtins/` (the built-ins);
2. the user plugin folder. On Windows this is
   `%APPDATA%\pixelRef\pixelRef\plugins`; elsewhere, Qt's `AppDataLocation`
   plus `/plugins`. `$PIXELREF_USER_PLUGINS` points it somewhere else;
3. folders the user added in *Window → Plugins… → Sources*
   (`pxcmd.add_plugin_source(path)`), in the order they set;
4. folders on `$PIXELREF_PLUGIN_PATH` (separated like `PATH`);
5. `--plugin-path DIR` and `--trust-plugin-path DIR` on the command line (repeatable).

A source is a folder of plugin folders, or a single plugin folder. Only
folders with a `plugin.toml` are plugins; loose `.py` files are ignored.

**Same id in two places.** Every copy is kept as a *candidate*. One is used:
the copy the user picked on the plugin's page (`pxcmd.plugin_resolve(id, path)`),
else the built-in one, else the first source's. The others are listed on the
plugin's page with their version, folder and trust. A third-party plugin
can't use a built-in plugin's id unless its manifest says
`replaces = "<that id>"` and the user picks it; foundation plugins can't be
replaced. Ids starting with `pixelref` are reserved.

### Approval and trust

A third-party plugin's code isn't imported until it is trusted:

| Trust | When | Runs? |
|---|---|---|
| Verified | signed by a publisher on the user's trusted list, files match | yes (on by default) |
| Approved | the user approved exactly these files | yes, when switched on |
| Trusted folder | its source is set to *Trust everything*, or `--trust-plugin-path` | yes (on by default) |
| Not reviewed | none of the above | no |
| Changed | approved, but a file changed since | no, until approved again |
| Invalid signature | `SIGNATURE.toml` doesn't match the files | no (the user must override explicitly) |
| Blocked | its folder only allows trusted publishers, or its id is taken | no |

The approval is pinned to a SHA-256 hash of every file in the plugin folder
(`__pycache__`, `*.pyc` and `.git` excluded). pixelRef checks the files
again right before running `__init__.py`, and runs exactly the bytes it
checked. Scripts can do the same as the window: `pxcmd.plugin_trust(id)`,
`pxcmd.plugin_revoke_trust(id)`, `pxcmd.plugins_needing_review()`,
`pxcmd.plugin_changes(id)`.

**While developing**, start pixelRef with `--trust-plugin-path your/folder`,
or set your folder's policy to *Trust everything* in the Sources tab, so
every edit doesn't need approving again.

### Permissions and the activity monitor

`[permissions]` says what your plugin does. Users see it in the approval
dialog. The *activity monitor* (a Python audit hook) watches third-party
plugins and lists anything a plugin does that it didn't declare on the
plugin's page:

- files opened for writing, deleted, renamed or truncated (the temp folder
  excepted) → `write_files`;
- files read in places that hold secrets → `read_sensitive`;
- sockets connecting and host names looked up → `network`;
- programs started → `subprocess`;
- native libraries loaded or symbols looked up with ctypes → `native_code`;
- code compiled or run from a string by your code → `dynamic_code` (the
  standard library compiling its own code, like `dataclasses`, doesn't count).

Things pixelRef does for you (saving a board through `ctx.run("save_scene")`)
are recorded as "through pixelRef", not as violations. Declare what you
actually do: an honest manifest is what makes the monitor useful to your users.
`pxcmd.plugin_activity(id)` lists what was recorded.

### Names: commands, services and overrides

Commands are namespaced by your plugin id (`my_plugin.do_thing`). Flat
aliases, extension points and services share one namespace. Registering a
name that is taken fails, and the error names the owner. Your plugin is then
switched off and reported. Built-in plugins activate before third-party ones,
so a built-in name is always taken.

To take over another plugin's command or service on purpose, declare it:

```toml
overrides = ["command:pen.combine", "service:color_picker"]
```

Then `ctx.command("pen.combine", fn)` and `ctx.provide_service("color_picker", obj)`
replace the original while your plugin is active, and the original comes back
when it is switched off. The plugin's page lists its overrides.

### Signing and publishing

Users trust a publisher by adding its public key once; plugins it signs then
load without asking.

```text
python -m pixelref plugin keygen ~/keys/studio.pem --name "Example Studio"   # once; --password encrypts it
python -m pixelref plugin sign path/to/my_plugin --key ~/keys/studio.pem     # after every change
python -m pixelref plugin verify path/to/my_plugin --publisher ~/keys/studio.pub.toml
python -m pixelref plugin hash path/to/my_plugin                             # the hash approvals pin
```

`keygen` writes the private key (keep it secret, out of version control) and
`studio.pub.toml`, which you give to users (*Sources → Trusted publishers →
Add publisher key…*, or `pxcmd.add_trusted_publisher("studio.pub.toml")`).
`sign` writes `SIGNATURE.toml`: an Ed25519 signature over the plugin's id,
version and the hash of every file. **Sign last**: any change to any file
afterwards (a stray `.DS_Store` included) makes the signature invalid, and
pixelRef refuses to run the plugin.

### Running

They activate in dependency order: foundation plugins first, then the other
built-in ones, then third-party ones. A plugin that raises during
activation, or whose requirements are missing or disabled, is switched off
and reported in *Window → Plugins…*. Everything it registered so far is
undone, and the app keeps running. Users enable and disable plugins there at
runtime (remembered between sessions); `pxcmd.load_plugin(id)`,
`pxcmd.unload_plugin(id)` and `pxcmd.reload_plugin(id)` do the same from
scripts. `reload_plugin` re-imports the code (after checking the files
again), which helps while developing. Foundation plugins
(`foundation = true`, built-in only) can't be disabled.

### Python packages

A plugin whose `[python] requires` aren't installed is **unavailable**, and
so is every plugin that requires it. It stays switched on, and its code isn't
even imported. *Window → Plugins…* says what it's waiting for and has an
**Install what's needed** button. The **Python packages** tab installs,
updates and removes packages without a terminal. Packages go into a folder
pixelRef manages, separate from any Python on the computer (see
[packaging.md](packaging.md)), and the plugin activates as soon as the
install finishes.

- Requirements are checked from package metadata, without importing anything.
  So **import heavy packages inside the functions that use them**, not at the
  top of your module. Then enabling your plugin costs nothing at startup, and
  nothing at all until someone uses the feature:

  ```python
  def edge_detect(image, params, fx):
      import numpy as np          # first use only; runs on a worker thread
      ...
  ```
- Versions use the usual operators: `==`, `!=`, `>=`, `<=`, `>`, `<`, `~=`
  and `==1.*`.
- Ship wheels in a `wheels/` folder next to `plugin.toml`. They install from
  there without internet (offices behind firewalls). They are only used for
  your plugin's own packages, and only once the user trusts your plugin.
- Scripts and batch mode: `pxcmd.packages.needed()`,
  `pxcmd.packages.install_missing()`, `pxcmd.packages.install("numpy")`,
  `pxcmd.packages.install_file("numpy-….whl")`, `pxcmd.packages.list()`,
  `pxcmd.packages.update()`, `pxcmd.packages.remove("numpy")`.

Import only from `pixelref.plugin_api` (headless) and `pixelref.plugin_api.ui`
(Qt). Besides the classes used below, `pixelref.plugin_api` exports
`API_VERSION`, `APP_VERSION` (the app's version, `"1.0.0"`), `SCENE_EXT`
(`".pxref"`), `SCENE_EXTS` (every extension opened as a scene: `.pxref`, `.json`)
and `read_thumbnail(path)`. Don't import other plugins. Use their
**services** and **extension points** instead, so each plugin can be disabled
on its own.

## The two halves

```python
def activate(ctx):        # headless: runs in the app, in batch mode and in tests
    ...

def activate_ui(ui):      # Qt: runs only in the desktop app, after activate()
    ...

def deactivate(ctx):      # optional; contributions are removed automatically anyway
    ...
```

Put anything that defines *data* in `activate`: node types, commands that
change the document, headless services. Put anything visual in `activate_ui`:
how nodes are drawn, tools, views, actions, keys, menus and panels. Then
scripts and batch mode can create and edit your nodes without a window.

Everything registered through `ctx` or `ui` returns a `Disposable` and is
removed when the plugin is disabled.

### `ctx` — `PluginContext`

| Member | What it does |
| --- | --- |
| `ctx.session`, `ctx.scene`, `ctx.settings` | the session, its scene graph, this plugin's settings (`plugins/<id>/…`) |
| `ctx.register_node_type(NodeType(...))` | add a node type (see below) |
| `ctx.extend_node_type(type, attrs)` | add attributes to a type you don't own, and its subtypes (see below) |
| `ctx.command(name, fn, alias=None, label=None, undoable=True, echo=True)` | register `<id>.<name>`; `label` (text or `fn(*args) -> text`) names the undo step |
| `ctx.run(name, *args, **kw)` / `ctx.pxcmd` | run commands |
| `ctx.declare_extension_point(name, doc)` / `ctx.contribute(point, value, order=0)` / `ctx.contributions(point)` | extension points |
| `ctx.provide_service(name, obj)` / `ctx.service(name)` / `ctx.require(name)` | services |
| `ctx.on(event, callback)` | session events (`document.changed`, `selection.changed`, `scene.opened`…) |
| `ctx.context_key(key, provider)` | a value for `when` conditions |
| `ctx.track(disposable_or_callable)` | dispose something of your own on deactivation |

### `ui` — `UIContext`

| Member | What it does |
| --- | --- |
| `ui.app`, `ui.canvas`, `ui.tools`, `ui.wm`, `ui.session`, `ui.headless` (the `ctx`) | the kernel objects |
| `ui.node_ui(type, factory=…, label=…, icon=…, badge=(colour, letter), describe=…, post_sync=…, double_click=…)` | how a node type is drawn and described |
| `ui.register_tool(MyTool(ui))` | a canvas tool (adds its palette button, Tools-menu entry, `tool.<id>` action and shortcuts) |
| `ui.view_type(ViewType(id, title, factory, …))` | a kind of tile for the window manager |
| `ui.panel(PanelType(id, title, factory, …))` | a panel outside the tiling layout (docked, floating or popped up) |
| `ui.action(key, command, text, args=(), kwargs={}, icon=…, keywords=…, category=…, checked=…, enabled=…, dynamic_text=…)` | a named, searchable action |
| `ui.bind(key, action_or_command, when=None, scope="window", mode=None)` | a default key binding (`scope="canvas"`: only while a canvas has focus) |
| `ui.menu("canvas", action, path="View/Background", group=…, order=…, when=…)` / `ui.menu("canvas", builder=fn)` / `ui.place_submenu("canvas", "Arrange", group, order, icon="grid", when=…)` | context-menu entries; a sub-menu's place and icon (a `when` placement wins while it holds) |
| `ui.input_handler(handler, priority)` | raw canvas input (higher priority sees events first) |
| `ui.overlay(painter, order)` | paint over every canvas view |
| `ui.escape_handler(fn, order)` | something `Esc` should cancel first |
| `ui.bar_block(factory, order)` | a widget in each window's bar |
| `ui.workspace(WorkspacePreset(...))` | a built-in workspace |
| `ui.command(...)`, `ui.contribute(...)`, `ui.provide_service(...)`, `ui.on(...)`, `ui.on_ui(event, cb)`, `ui.context_key(...)`, `ui.settings`, `ui.status(text)`, `ui.toast(text)` | as on `ctx`, plus UI events (`canvas.refreshed`, `tool.changed`, `camera.changed`, `view.created`, `focus.changed`, `command.error`…) |

The context menu goes from what was clicked to app-wide sub-menus; pick the
`group` that matches (separators fall between groups, `order` sorts within one):
`clipboard` (copy, paste…), `create` (new items, empty canvas), `state` (edit
attributes, rename, lock, hide), `transform` (Transform / Image / Playback
sub-menus and type-specific edits), `arrange` (Arrange, Order, grouping),
`select` (select / show / unlock all), `focus` (frame, isolate, export the
selection), `history`, then the View, File and Window sub-menus. Inside them:
`view.frame`, `view.zoom`, `view.flip`, `view.display`, `view.background`;
`file`, `file.import`, `file.export`, `file.storage`; `window`,
`window.panels`, `w_modes`, `window.extras`.

## Node types

```python
from pixelref.plugin_api import CAP_COLORABLE, CAP_CONTENT, CAP_ERASABLE, Attr, NodeType

STICKY = NodeType(
    name="sticky",
    inherits="canvas_node",                 # gets translate/rotate/scale/visibility/…
    label="Sticky",
    schema_version=1,
    capabilities=frozenset({CAP_CONTENT, CAP_ERASABLE, CAP_COLORABLE}),
    color_attr="paper",                     # what the colour picker recolours
    quick_attrs=("paper", "status"),        # the canvas quick editor's default fields
    compute={"local_bounds": lambda session, node: [0, 0, *node.attrs["size"]]},
    attrs=(
        Attr("size", "float2", [160.0, 160.0], group="Shape"),
        Attr("paper", "color", [255, 226, 110], group="Look"),
        Attr("votes", "int", 0, minimum=0, doc="Thumbs up"),
        Attr("status", "enum", "open", choices=("open", "done")),
    ),
)
```

- `Attr(name, type, default, short=None, minimum=None, maximum=None,
  choices=(), storable=True, hidden=False, computed=False, readonly=False,
  label="", group="", doc="", step=None, precision=None, soft_min=None,
  soft_max=None, unit="", display_scale=1.0, validator=None)`. `validator(value) -> value`
  runs after the type's own checks when a value is set, and raises
  `SceneError` for bad data. Types: `bool`, `flag`, `int`, `float`,
  `float2`, `color`, `color4`, `string`, `text`, `enum`, `path`, `asset`,
  `node_ref`, `node_refs`, `string_list`, `rect`, `points`, `any`. Attributes
  can be read and written by name or by short name (`translate` / `t`).
- `compute` gives values for computed attributes. Provide `local_bounds` so
  `world_bounds`, arranging and framing work without the UI.
- `upgrade(attrs, from_version) -> attrs` converts files written with an older
  `schema_version` of your type.
- Without a custom Attribute Editor section, your type's non-core attributes
  appear in a generated **Attributes** section: number fields, checkboxes,
  colour buttons, enum combos and text fields.
- `quick_attrs` names the attributes the **quick editor** shows. That's the
  bar next to the selection on the canvas. Leave it empty and you inherit the
  parent type's list: every `canvas_node` gets `("opacity",)`. Users can
  replace the list per type (the bar's ⋯ menu, or
  `pxcmd.quick_edit.set_fields("sticky", [...])`). Their choice is kept in user
  settings and wins over yours until they pick *Plugin default* again. The bar
  can't edit hidden, computed or read-only attributes, or the types `points`,
  `any`, `rect`, `asset`, `node_ref(s)` and `text`. Your editing hints
  (`soft_min`, `unit`…) shape its number fields.

**Editing hints** shape the number fields; they never limit what a value may
be (that's `minimum`/`maximum`):

| Hint | Meaning |
|---|---|
| `soft_min`, `soft_max` | The usual range. Dragging a field covers it in about 300 px and stops at its ends; typing can go beyond, up to `minimum`/`maximum`. Without a soft range the hard range is used; with neither, a drag moves `step` per pixel. |
| `step` | Arrow-key / wheel increment, and what Ctrl snaps to while dragging. |
| `precision` | Decimals shown. |
| `unit` | Suffix, e.g. `"px"`, `"%"`, `"°"`. |
| `display_scale` | Shown value = stored × scale (opacity stored 0–1, shown 0–100 %). |

```python
Attr("radius", "float", 4.0, minimum=0.0, maximum=500.0, soft_min=0.0, soft_max=60.0,
     step=1.0, precision=1, unit="px")
Attr("amount", "float", 1.0, minimum=0.0, maximum=1.0, display_scale=100.0, unit="%", precision=0)
```

Number fields are dragged left/right (click without dragging to type). Shift is
10× faster, Alt 10× finer, Ctrl snaps to the step and Esc puts the value back.
A drag previews live and is one undo step.

### Extending other types

A plugin can add attributes to node types it doesn't own (extension
attributes). For example, an effect stack on everything drawn on the
canvas:

```python
ctx.extend_node_type("canvas_node", (
    Attr("effects", "any", [], hidden=True, validator=check_stack),
))
```

- Subtypes inherit them: here `image`, `annotation_text`, plugin types…
  The target type doesn't have to be registered yet.
- They are **sparse**. A node stores the attribute only once it's set, and
  reading an unset one gives the default. Scenes that never use your feature
  are saved byte for byte as before.
- Names can't clash with an attribute of the type, of its ancestors, of its
  subtypes, or of another extension.
- While your plugin is off, values in files are kept, and saved as dynamic
  attributes. When it's on again, its spec takes over.
- Pass `hidden=True` and give the attribute its own `inspector.sections` UI,
  or it shows up in every type's generated **Attributes** section.

## Drawing nodes

```python
from pixelref.plugin_api.ui import NodeItemMixin
from PySide6.QtWidgets import QGraphicsItem

class StickyItem(NodeItemMixin, QGraphicsItem):
    def __init__(self, name):
        super().__init__()
        self._init_node(name, "sticky", None)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def apply_attrs(self, attrs): ...        # read everything you draw from attrs
    def content_rect(self): ...              # local rectangle (gizmo, framing, export)
    def boundingRect(self): ...
    def paint_content(self, painter, option, widget=None):
        ...                                  # draw the node's own content

ui.node_ui("sticky", factory=lambda canvas, node: make_item(node), label="Sticky",
           icon="rect", badge=("#e0c050", "S"))
```

Draw in `paint_content`, not `paint`. The mixin's `paint` skips nodes a view
filters out (`paint_allowed`), runs the item's **paint filters**, then draws
`paint_decorations` on top: UI such as the text-editing frame, which filters
must not touch. Don't override `paint`: paint filters can't reach it.

Paint filters change how *any* node type looks, without knowing the type.
That's what an effect stack builds on. Contribute a hook to
`canvas.item_hooks`; it runs for every item the canvas builds:

```python
def install(item, node):
    if node["attrs"].get("effects"):
        item.add_paint_filter(effect_filter)

def effect_filter(item, painter, option, widget, draw):
    ...                      # e.g. draw(offscreen_painter), filter the pixels,
    ...                      # then painter.drawImage(...)

ui.contribute("canvas.item_hooks", install)
```

`draw(painter)` paints the content (through the filters after yours) with
any painter. Child items, such as the annotations on an image, are separate
items and never pass through their parent's filters. Contributing or removing
a hook rebuilds every item.

The canvas sets the item's transform from the core attributes and rebuilds the
item whenever the node's data changes. Never keep document state in the item.
Interactive edits change the item and then commit through
`ui.canvas.commit_dirty()` or a command, so they are undoable and echoed.
Override `commit_values()` to return extra attributes an interaction changed
(see `RectItem`).

Items that contain other items can act as one:

- `captures_children = True`: a click on a child picks this item, and while
  it's closed (`editing` is false) the canvas makes its children ignore clicks
  so Qt selects and drags the item instead. Pen drawings work like this.
- `opens_for_selection = True` (with `captures_children`): the canvas calls
  `set_editing(True)` while something inside the item is selected, so its
  members can be picked. Groups (`grouping/ui.py`) work like this.
- `outline_path()` returning a scene `QPainterPath` replaces the selection box
  with that outline (links use it).

The `double_click` passed to `ui.node_ui` may take a third argument, the
clicked scene point: `double_click(view, item, scene_pos)`.

Per-type gizmo handles (rectangle corners, line end points, text box width)
are contributed to the `gizmo.handles` extension point; see `rect/ui.py`
(`RectHandles`).

## Tools

```python
from pixelref.plugin_api.ui import HANDLED, Tool

class StampTool(Tool):
    id = "stamp"; title = "Stamp"; icon = "image"; shortcuts = ("S",)
    tooltip = "Stamp stickies (S)"; hint = "Click to stamp · Esc to finish"
    order = 30

    def pointer(self, e):                    # e.kind: press/move/release/double
        if e.kind == "press":
            self.app.run_command("sticky.create", e.scene_pos.x(), e.scene_pos.y())
            return HANDLED
        return None
```

`order` and `shortcuts` are only defaults: users rearrange the palette, hide
tools and change keys (*Customize Palette…*). A tool added to a palette the
user already arranged lands in the Tools section next to its default
neighbours. Pick a `shortcuts` key that's free; `pxcmd.keymap.list()` shows
what's taken.

Tools should **run commands** rather than edit the scene directly. Then what a
tool does is undoable, echoed in the Script Editor and scriptable. Other hooks:
`activate(**args)`, `deactivate()`, `cancel()` (Esc), `busy`, `key(e)`,
`hover(e)`, `banner(view)`, `paint_overlay(view, painter)` and
`options_panel(parent)` (shown in the Tools view while active).

### Tool options and your defaults

Declare a tool's options as `Attr` specs; read them from `self.options`:

```python
class StampTool(Tool):
    ...
    creates = "sticky"                       # the node type it makes
    option_specs = (
        Attr("paper", "color", [255, 226, 110], label="Paper"),
        Attr("size", "float2", [160.0, 160.0]),
    )

    def pointer(self, e):
        if e.kind == "press":
            self.app.run_command("sticky.create", e.scene_pos.x(), e.scene_pos.y(), **self.options.as_dict())
            return HANDLED
```

Each value comes from three layers: the factory default (the spec's
`default`), the user's default and this session's value. The Tools view shows a
form generated from the specs (override `options_panel` for your own layout;
`widgets.fields.option_editor(tool, name, parent)` gives a bound editor for one
option), with **★ Save as default** and **⟲ Reset** (to your default; its menu
also resets to the factory defaults or forgets your defaults). Right-click a
field to do the same for that one option. Saved defaults go to the user
settings (`tools/<id>/defaults`) and are kept across restarts.

Name options after the attributes they give new nodes and set `creates`: then
"Use as default" in the Attribute Editor (and right-click → *Set as my default
for new …* on a generated field) fills the tool's options from a selected node.
`options.get/set/update/as_dict`, `defaults()`, `save_defaults(names=None)`,
`set_default(name, value)`, `reset(factory=False)`, `clear_defaults()` and
`watch(fn)` are the API; `pxcmd.tools.options`, `set_option`, `set_options`,
`save_defaults`, `reset_options`, `clear_defaults`, `option_defaults` and
`save_defaults_from(node)` script it.

Drawing tools (`uses_brush_color = True`) name the options holding their
colour and width in `color_option` / `width_option`. Each tool keeps its own;
the `brush` service reads and sets the ones of the drawing tool in use (the
colour picker uses it). `brush.make_tool_panel(tool, parent)` builds the usual
panel: colour/width row, the other options, recent colours.

### Editors for your own panels

`widgets.editor_for(attr, parent)` makes the editor for a spec (the same ones
the Attribute Editor generates); each has `read()`, `write(value)` and a
`committed(value)` signal, and number editors also `previewed(value)` while
dragging. `widgets.bind_edits(app, editor, apply, label)` connects one to the
document: `apply(value)` runs your command; a drag is previewed live and
becomes one undo step (Esc rolls it back). `widgets.make_spin(...)` returns the
same draggable `NumberField`.

## Views

```python
from pixelref.plugin_api.ui import ViewType

ui.view_type(ViewType(
    id="stats", title="Statistics", icon="inspector",
    factory=lambda tile, state: StatsWidget(ui, state),   # state: what save_state returned
    save_state=lambda widget: widget.state(),             # kept in the layout
    placement="tabbed",                                    # split, h, v, tabbed, stacked, floating, scratchpad
))
```

Users open it from *New View…* (`Ctrl+W` `N`) or `pxcmd.wm.open_view("stats")`.

## Panels

Things you edit the board *with*, like the Tools, Outliner and Attribute
Editor, are better as panels than as tiles. Panels sit outside the tiling
layout, docked in the sidebar or floating over the canvas, and pop up on
demand.

```python
from pixelref.plugin_api.ui import PanelType

ui.panel(PanelType(
    id="swatches", title="Swatches", icon="pick",
    factory=lambda parent: SwatchPanel(ui),     # called once per place it is shown (sidebar/card, pop-up)
    dispose=lambda widget: ...,                 # optional clean-up
    order=30,                                   # position in the sidebar
    default_mode="docked",                      # docked, floating or hidden
    popup_keys=("Alt+S",),                      # keys that pop it up
    popup_anchor="cursor",                      # or "selection"
    popup_closes_on=("tool.changed",),          # UI events that close the pop-up
    stretch=1,                                  # share of the sidebar height (0: its content's height)
))
```

The factory may be called more than once (the docked/floating instance and
the pop-up are separate widgets), so keep state in services, not in the
widget.

## Cooperating with other plugins

Extension points declared by the built-ins (contribution types are in
`pixelref.plugin_api.contracts`):

| Extension point | Declared by | Contribute |
| --- | --- | --- |
| `inspector.sections` | inspector | `InspectorSection(key, title, order, applies(info), build(parent, inspector), generic=False)`; `generic=True` for sections that apply to every type (they don't replace a type's generated Attributes section) |
| `inspector.rows` | inspector | `InspectorRows(section, order, build)`: rows inside another plugin's section |
| `inspector.empty_panels` | inspector | `EmptyPanel(key, order, build)`: shown when nothing is selected |
| `quick_edit.sections` | quick_edit | `QuickSection(key, title, order, applies(names), build(parent))`: controls in the canvas quick editor bar, before the fields; the widget has `refresh(names)` and keeps a fixed size (playback's transport is one) |
| `ingest.handlers` | clipboard | `IngestHandler(name, order, accepts(mime), describe(mime), ingest(view, pos, mime))` |
| `clipboard.exporters` | clipboard | `fn(names, QMimeData)` adding formats when copying |
| `palette.buttons` | tool_palette | `PaletteButton(key, label, icon, order)` in the Tools view's Add row |
| `stroke.styles` | brush | `StrokeStyle(...)`: which Stroke-section controls a type has |
| `grouping.ungroup` | grouping | `UngroupHandler(node_type, label, done_message)` |
| `gizmo.handles` | gizmo | a handle provider for one node type |
| `eraser.policy` | eraser | `fn(item) -> bool`; False keeps an item from being erased |
| `effects.types` / `effects.processors` | effects | `EffectType(name, label, params, category, cost, version)` / `(name, fn(QImage, params, fx) -> QImage)`; see [effects.md](effects.md) |
| `color.pickers` | kernel (UI) | `ColorPickerProvider(id, title, create, order, description)`: a colour picker for every swatch (see below) |
| `canvas.item_hooks` | kernel (UI) | `fn(item, node)` after every canvas item is built (paint filters) |
| `image.movie_hooks` | image | `fn(item)` when an animated image's item starts its movie (playback restores paused frames with it) |
| `interactions.steps` | interactions | `StepKind(command, describe(session, args, kwargs), targets(args, kwargs), icon)`: a command click actions may run, and its sentence. Only these run from a board |
| `interactions.verbs` | interactions | `ClickVerb(id, label, applies(session, target), build(session, target) -> (steps, back), prepare=None, order)`: what connecting a button to an item offers |
| `scene.before_save` / `scene.after_new` | kernel | `fn(session, path)` / `fn(session)` |
| `wm.layouts`, `wm.scratchpad`, `wm.bar_blocks` | kernel (UI) | workspaces, scratchpad tiles, bar widgets |
| `canvas.overlays`, `canvas.header_widgets`, `canvas.background` | kernel (UI) | painting over views, widgets in canvas tile headers, the background |
| `app.close_handlers`, `app.welcome_lines`, `escape` | kernel (UI) | `fn() -> bool` to veto closing, empty-board text, Esc handlers |

Declare your own with `ctx.declare_extension_point("my_plugin.things", doc)`.
Contributions made before the point exists are kept and attached when it is
declared.

Services provided by the built-ins include `brush` (the colour and width of
the drawing tool in use, recent colours, rows and panels for tool options), `images` (create image nodes from
files or bytes), `image_cache`, `transform` (targets and world-space
transforms), `ingest`, `gizmo`, `inspector`, `outliner`, `tool_palette`,
`drawing_session`, `notes`, `text`, `recovery`, `files`, `image_storage`,
`canvas_background`, `console`, `playback`, `playback.controls`, `links`,
`minimaps`, `contact_sheets`, `start_panel`, `effects` and `effects_engine`.

## Testing a plugin

```python
from pixelref.core.session import Session

session = Session(trusted_plugin_paths=["path/to/plugins"]).start()     # headless, trusted for this run
session.run("my_plugin.do_thing")
```

For UI tests, `tests/harness.py` starts the whole app offscreen. See
`test_external_plugin_example_ellipse` in `tests/test_kernel.py`.

## Colour pickers

Every colour swatch (the attribute editor, Stroke/Fill, text colours, the canvas background…) opens
the colour picker chosen in **Choose Color Picker…** (setting `colors/picker`). The built-in
`color_popover` plugin is the default: a popover beside the swatch with a saturation/brightness
square, hue and alpha sliders, hex and RGB fields, a screen eyedropper (`I`), recent colours and the
colours used in the scene. Qt's own dialog is the fallback: it is used when chosen, when no other
picker is installed and when a picker fails to open.

To add your own, contribute a `ColorPickerProvider` to `color.pickers`:

```python
from pixelref.plugin_api.contracts import ColorPickerProvider

def create(parent, initial, title, alpha):
    return MyPicker(parent, initial, title, alpha)   # a QWidget, see below

ui.contribute("color.pickers", ColorPickerProvider("my_picker", "My picker", create, order=10))
```

`create(parent, initial: QColor, title: str, alpha: bool)` returns a dialog-like widget:

| Member | Meaning |
| --- | --- |
| `currentColorChanged(QColor)` | emit it whenever the user changes the colour: the canvas previews every change live, as one undo step |
| `currentColor() -> QColor` | the colour now |
| `exec() -> int` | show it and block; return truthy when the user accepted, falsy on cancel (the original colour is restored) |

`alpha` says whether the colour has an alpha channel (otherwise keep it opaque). Anything that fits
`QColorDialog`'s interface works. The user picks the provider from "Choose Color Picker…", or scripts
`color_popover.use_picker <id>` (`default` = the lowest `order`).

## Attribute symbols

The Attribute Editor shows a small glyph beside attribute names where one applies (◐ opacity, ↻ rotate…).
Defaults live in `pixelref/core/attr_symbols.py` (by attribute name, then by type). A plugin can:

- name the symbol on its own attribute: `Attr("bevel", "float", symbol="◣")`;
- register symbols for any attributes, overriding the defaults:
  `ctx.register_attr_symbols({"bevel": "◣", "my_node.depth": "⇕", "type:float2": "⌖"})`
  (keys: `"<node type>.<attr>"`, `"<attr>"`, `"type:<attr type>"`).

Attributes without a match get no symbol.
