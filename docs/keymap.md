# Keyboard shortcuts, scripts and macros

Every key in pixelRef can be changed, and keys can run more than built-in
commands: lists of commands (macros), Python, your own script files and key
modes (chords such as `Ctrl+Space`, then `1`).

There are three ways in, from easiest to most flexible:

| Way | Best for |
| --- | --- |
| **Keyboard Shortcuts** (`F1`, or *Scripts → Keyboard Shortcuts…*) | changing or adding keys for anything in the app, including your scripts and macros |
| **Scripts folder** and **Record Macro** (`Ctrl+Alt+R`) | automating a sequence of steps, without writing code |
| **`keymap.toml`** | macros and Python one-liners on keys, key modes, sharing a setup |

All three end up in the same place: `keymap.toml` and the `scripts/` folder in
the user config folder (on Windows
`%LOCALAPPDATA%\pixelRef\pixelRef\`; elsewhere Qt's `AppConfigLocation`).
Copy those two to another machine to take your setup with you.

## The Keyboard Shortcuts editor

`F1` lists every action by category with its keys. Type in the search box to
filter by name, command or key (`frame`, `Ctrl+D`).

- **Set Shortcut…** (or double-click a row): press the new key. If another
  action already has it, you're asked before it is taken over.
- **Add Shortcut…** keeps the existing keys and adds another.
- **Remove** takes every key away from the action; **Reset** goes back to the
  built-in keys; **Reset All…** undoes every change to built-in keys (your
  macros, scripts and modes stay).
- Changed rows are **bold**; hover the key to see the built-in one.
- Key-mode keys (`Ctrl+W`, then `H`…) are listed at the end, read-only: change
  them in `keymap.toml`.

You can also right-click a button in the Tools view and choose *Set Shortcut…*.

Changes apply immediately and are written to `keymap.toml`. Your changes
survive turning a plugin off and on again: a built-in key you removed stays
removed.

## Scripts

Every `.py` file in the `scripts/` folder (*Scripts → Open Scripts Folder*)
and in folders on `$PIXELREF_SCRIPT_PATH` becomes an action `script.<file>`:

- it is in the **Scripts** menu (right-click the canvas) and in the command
  search (`Ctrl+K`);
- the first line of its docstring is its title;
- give it a key in the shortcut editor (category *Scripts*).

Scripts run like the Script Editor: `pxcmd`, `pxnode`, `sel()`, `session` and `app`
are defined, and a run is **one undo step**. If a script raises, everything it
did is undone, the error is shown and the traceback goes to the Script Editor
(`` Ctrl+` ``).

```python
"""Grid the selection and frame it"""
pxcmd.arrange("grid", sel())
pxcmd.navigation.frame_selected()
```

`userSetup.py`, the console's `console_tab_*.py` and files starting with `_`
are not listed (use `_helpers.py` for shared code). The folder is watched, so
new or edited files show up at once (*Scripts → Reload Scripts* forces it).

## Recording macros

Click the **record button** (red ring) in the window bar, or use *Scripts →
Record Macro* (`Ctrl+Alt+R`), and work as usual. While recording:

- the button becomes a red **REC pill** showing the time and the number of
  steps. Click the text to see the steps so far (and take the last one back);
  the pill also has **pause** (what you do while paused isn't recorded),
  **Save** and **discard** buttons;
- every canvas gets a red frame and a *Recording macro* label (amber and
  dashed while paused).

**Save** (or `Ctrl+Alt+R` again) opens the save dialog: name the macro, untick
any steps you don't want, and optionally give it a shortcut straight away.
*Keep Recording* (or Esc) goes back to recording without losing anything.

The macro is saved as a script in the scripts folder, made of the `pxcmd`
calls of what you did (the same lines the Script Editor echoes), so it shows
up in the Scripts menu straight away and can get a key like any script.
Commands that failed are left out. `macros.pause`, `macros.remove_last` and
`macros.discard` are commands too, so they can be bound to keys.

A recorded macro replays exactly what you did, including which items were
selected by name. Open the file to generalise it, e.g. replace
`pxcmd.select(['Img', 'Img1'])` with nothing to act on the current selection.

## `keymap.toml`

The editor writes this file, and you can edit it by hand (*Open keymap.toml*
in the editor, then *Reload*). The editor rewrites it when you change a key,
so comments are not kept. Each `[[bind]]` needs a `key` and exactly one of
`command`, `commands`, `script` or `python`. Optional on every bind: `name`
(the title in menus and search), `when` (a condition such as
`"selection.count > 0"`), `scope` (`window`, the default, or `canvas`: only
while a canvas has focus) and `mode`.

```toml
[leader]
window = "Ctrl+W"                 # the window-mode leader key

[[bind]]                          # any action or command
key = "Ctrl+Shift+D"
command = "clipboard.duplicate"

[[bind]]                          # a macro: commands in order, one undo step
key = "Ctrl+Alt+T"
name = "Tidy board"
commands = ["select.all", "arrange.grid", "navigation.frame_all"]

[[bind]]                          # Python
key = "Ctrl+Alt+1"
name = "Rotate 45"
python = "pxcmd.set_transform(sel(), rotate=45, relative=True)"

[[bind]]                          # a script (relative: the scripts folder)
key = "F5"
script = "tidy.py"

[[unbind]]                        # free a built-in key (just for that command)
key = "Ctrl+D"
command = "clipboard.duplicate"

[[mode]]                          # your own key mode: Ctrl+Space, then a key
name = "macros"
key = "Ctrl+Space"
title = "Macros"
# sticky = true                   # stay in the mode until Esc

[[bind]]
mode = "macros"
key = "1"
script = "tidy.py"
```

- `commands` takes action IDs (the editor's *ID* column, e.g.
  `transform.rotate_cw`, or `script.tidy`) or command names (`frame_all`). If a
  step fails, the whole macro is undone.
- A `[[bind]]` with `commands`, `python` or `script` but no `key` still
  appears in `Ctrl+K` and in the editor (category *User Hotkeys*), where you
  can give it a key later.
- Leave out `command` in `[[unbind]]` to free a key from everything.
- Problems (a bind without a target, a mode without a name) are reported when
  the file is loaded or reloaded; the rest of the file still applies.
- Keys inside the built-in window mode (`Ctrl+W`, then a key) are binds with
  `mode = "window"`, e.g. `key = "X"`, `command = "wm.close_view"`. The
  `[leader]` table's `window` sets the leader key itself.

### Conditions (`when`)

`when` conditions use **context keys**:

| Key | Value |
| --- | --- |
| `selection.count` | how many nodes are selected |
| `selection.types` | the node types of the selection (`selection.types == "image"`; a comma-separated list compares the whole set) |
| `tool.active` | the id of the active tool |
| `editing.text` | true while typing in a text note |
| `canvas.focused` | true while a canvas view has focus |
| `wm.mode` | the active key mode (`window`, `resize`…), none outside one |
| `wm.focused_view_type` | the view type of the focused tile (`canvas`, `console`…) |
| `pen.session_active` | true while a pen drawing is open |
| `links.selected` | the selected links (true when there are any) |
| `playback.selected` | how many animated images are selected |

Combine them with `==`, `!=`, `<`, `>`, `<=`, `>=`, `&&`, `||` and `!`
(`selection.count > 0 && !editing.text`). `&&` binds tighter than `||`, and
there are no parentheses. Plugins can add their own keys
(`ctx.context_key(...)`). `pxcmd.keymap.list()` shows every binding with its
condition.

## From scripts and plugins

```python
pxcmd.set_shortcut("tool.pen", "B")                 # kept in keymap.toml (takes B over)
pxcmd.keymap.reset_shortcut("tool.pen")             # back to the built-in key
pxcmd.bind_key("Ctrl+Alt+G", "script.grid_it")      # this session only
pxcmd.keymap.unbind("Ctrl+D", target="clipboard.duplicate")
pxcmd.keymap.reload()                               # re-read keymap.toml
pxcmd.keymap.list()                                 # every live binding
pxcmd.run_macro(commands=["select.all", "frame_all"])
pxcmd.run_macro(python="print(sel())")
pxcmd.record_macro()                                # start; call again to stop and save
pxcmd.list_scripts()
```

Plugins add keys with `ui.bind(key, action, when=…, scope=…, mode=…)` and key
modes by binding `keymap.enter_mode` (see [plugins.md](plugins.md)). User
changes always win over plugin defaults.
