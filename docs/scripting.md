# Scripting: `pxcmd`, `pxnode`, the Script Editor and batch mode

Everything in pixelRef is a command, and the UI runs the same commands you
can call from Python. So anything you can do with the mouse, a script can do.
The full list is in the generated [command reference](commands.md);
`pxcmd.help("name")` describes one command and `pxcmd.list_commands("pen.*")`
finds them.

## `pixelref.pxcmd`

```python
from pixelref import pxcmd

# Nodes
img = pxcmd.import_image("refs/castle.jpg", translate=[0, 0])   # returns the node name
folder = pxcmd.create_node("group", "Lighting", pick_members=1)
pxcmd.parent(img, folder)                     # keeps it where it is on the board
note = pxcmd.create_node("annotation_text", parent=img, text="Rim light", translate=[20, -40])
pxcmd.rename(note, "RimNote")
pxcmd.duplicate(img, offset=(300, 0))
pxcmd.delete("RimNote")

# Attributes ("node.attr", or node and attr separately; short names work too)
pxcmd.set_attr(f"{img}.rotate", 15)
pxcmd.set_attr(img, "opacity", 0.5)
pxcmd.get_attr(f"{img}.t")                   # translate
pxcmd.get_attr(f"{img}.world_bounds")        # computed: [x, y, width, height]
pxcmd.attrs(img)                             # attribute names
pxcmd.attr_info(img, "opacity")              # type, range, default, doc…
pxcmd.add_attr(img, "rating", "int", 3, minimum=0, maximum=5)   # a dynamic attribute
pxcmd.set_transform(img, translate=[10, 0], relative=True)
pxcmd.get_transform(img, world_space=True)   # {"translate", "rotate", "scale", "world_bounds"}

# Finding things
pxcmd.ls(type="image")                       # type includes subtypes
pxcmd.ls(tags="lighting", visible=True)
pxcmd.ls(pattern="Shot_*", roots=True)
pxcmd.children(folder)                       # all_descendants=True, type="image"
pxcmd.parent_of(img)                         # None for a top-level node
pxcmd.select(pxcmd.ls(type="image"))
pxcmd.selected()

# Plugin commands are namespaced by plugin id; many have a flat alias
pxcmd.transform.flip("h")                    # == pxcmd.flip("h")
pxcmd.arrange.arrange("grid")                # == pxcmd.arrange(mode="grid")
pxcmd.pen.add_stroke([[0, 0], [50, 20], [100, 0]], color=[255, 64, 64])
pxcmd.canvas_background.set(background=[255, 255, 255], pattern="dots")
pxcmd.playback.go_to_frame(12, "dance")      # animated images (desktop app): play, pause, next_frame…

# Undo
with pxcmd.undo_chunk("Lay out refs"):       # one undo step for everything inside
    pxcmd.arrange(mode="grid")
    pxcmd.align("top")
pxcmd.undo()
pxcmd.undo_info()                            # {"undo": [labels…], "next_undo": …, "next_redo": …, …}

# Files
pxcmd.save_scene("board.pxref")
pxcmd.open_scene("other.pxref")
pxcmd.import_scene("more_refs.pxref")          # adds its nodes to this scene
```

Values are validated against the attribute's specification. Unknown
attributes are errors, and numbers are clamped to the attribute's range.
Failed commands raise `CommandError` or `SceneError`, and their changes are
rolled back.

Camera commands (`navigation.zoom`, `navigation.set_camera`,
`navigation.frame`, `views.look_through`…) are never undoable, just like
panning by hand. Creating and changing camera bookmarks
(`views.create_camera`, `views.update_camera`) is.

### Callbacks

```python
cid = pxcmd.callbacks.add("selection.changed", lambda event: print(event.payload["names"]))
pxcmd.callbacks.add("node.attr_changed", lambda e: print(e.payload["name"], e.payload["attr"]))
pxcmd.callbacks.remove(cid)
pxcmd.callbacks.clear()
```

Session events: `selection.changed`, `document.changed`, `document.replaced`,
`command.finished`, `scene.opened`, `scene.saved`, `revision.changed`,
`plugins.changed`. Document events: `node.created`, `node.deleted`,
`node.renamed`, `node.reparented`, `node.reordered`, `node.attr_changed`,
`node.attr_added`, `node.attr_deleted`, `asset.added`, `scene.loaded`.
`"node.*"` matches all of the node events.

## `pixelref.pxnode` — objects instead of names

```python
from pixelref import pxnode

img = pxnode.node("castle")
img.opacity = 0.5                  # pxcmd.set_attr("castle.opacity", 0.5)
img.translate                      # pxcmd.get_attr("castle.translate")
img.rename("castle_ref")           # the handle follows the rename
note = pxnode.create("annotation_text", parent=img, text="Fix")
for n in pxnode.ls(type="image"):
    n.tags = n.tags + ["review"]
[n.name for n in img.children]
```

Every `pxnode` access is a `pxcmd` call, so it is undoable and echoed like
anything else.

## The Script Editor

Toggle it with ``Ctrl+` `` or *Window → Script Editor*. It lives in the
window manager's scratchpad, so it floats over the board; move it into the
layout like any other view.

- The upper pane is the **output**: results, `print` output, tracebacks and,
  with **Echo all commands** on, the `pxcmd` call behind everything you do in
  the UI. Click around, then copy the echoed lines into a script.
- The lower pane has **tabs** of Python. `Ctrl+Enter`, keypad `Enter` or *Run*
  runs the selected text, or the whole tab when nothing is selected. A single
  expression prints its value.
- `Tab` or `Ctrl+Space` completes names, `pxcmd.` commands (including
  namespaces), node names inside quotes and `"node.` attributes.
  `Ctrl+↑` / `Ctrl+↓` step through the history.
- `pxcmd`, `pxnode`, `session`, `app` and `sel()` (the selected names) are
  predefined. Variables persist between runs.
- **Each run is one undo step.** If the code raises, the traceback is printed
  and what ran before the error is kept as one undo step.
- Tabs are saved as `console_tab_N.py` in the user script folder. Drop a
  `.py` file on the editor to run it, or use `pxcmd.console.run_file(path)`.

### `userSetup.py`

At startup the app runs `userSetup.py` from the user script folder and from
every folder on `$PIXELREF_SCRIPT_PATH`. Use it for helper functions,
callbacks or custom key bindings:

```python
# userSetup.py
from pixelref import pxcmd

def tidy():
    with pxcmd.undo_chunk("Tidy"):
        pxcmd.select(pxcmd.ls(type="image", roots=True))
        pxcmd.arrange(mode="optimal")

pxcmd.keymap.bind("Ctrl+Alt+T", "arrange.grid")       # any action or command (this session)
```

Other `.py` files in the scripts folder become commands in the *Scripts* menu
that you can bind to keys, and *Record Macro* writes new ones; see
[keymap.md](keymap.md).

The user script folder is `scripts/` in the user config folder (see
[keymap.md](keymap.md) for where that is).

## Batch mode

Run scripts without a window:

```bash
python -m pixelref --batch build_board.py                    # new scene
python -m pixelref --batch fix.py board.pxref --save fixed.pxref # open, run, save
python -m pixelref --batch report.py board.pxref --plugin-path my_plugins
```

The script runs with `pxcmd`, `pxnode` and `session` predefined. It sees every
plugin's headless half: node types, document commands, layout, transforms
and files. Commands that need a window (dialogs, views, tools) are not
available. Computed attributes such as `world_bounds` still work, because node
types compute their own bounds.

Or start a session yourself in any Python program:

```python
from pixelref.core.session import Session
from pixelref import pxcmd

Session().start()            # becomes the current session for pxcmd
pxcmd.open_scene("board.pxref")
print(len(pxcmd.ls(type="image")), "images")
```
