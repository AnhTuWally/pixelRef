# Views and the window manager

pixelRef shows the board in **views**. A view is a canvas with its own camera
and display settings. Canvases, the Script Editor, the Contact Sheet and any
view type a plugin adds are **tiles** in an
[i3](https://i3wm.org)-style tiling window manager. You can have as many canvas
views as you like, all showing the same document.

## The minimap

Every canvas view has its own **minimap**, an overview of the whole board
floating in one of the view's corners. It isn't a tile.

| To… | Do this |
| --- | --- |
| **Move around the board** | click or drag on the minimap; the wheel zooms the view |
| **Put it in another corner** | hover it, then drag its title bar: the four corners light up and it snaps to the nearest one |
| **Make it bigger or smaller** | the `S` / `M` / `L` button in its title bar cycles small, medium and large |
| **Hide it** | the `×` in its title bar, or the map button in the canvas header |
| **Show it again** | the map button in the canvas header, or *View ▸ Minimap* in the canvas menu |

Each view remembers its own minimap (corner, size, shown or hidden) with the
layout, and a split copies it. New views follow your last show/hide choice.
Views using the *Clean* display preset (Present) start without one,
and very small views hide it automatically. Scripts:
`pxcmd.minimap.toggle()`, `pxcmd.minimap.place("tl", "large")`.

The **Tools, Outliner and Attribute Editor are not tiles**: they are
[panels](#panels) that stay at hand whatever the layout.

## Workspaces

A new scene starts with three workspaces:

| # | Workspace | Layout |
| --- | --- | --- |
| 1 | **Board** | the *Main* canvas |
| 2 | **Review** | two canvases side by side: *Colour* and *Value check* (greyscale), linked so they pan and zoom together |
| 3 | **Present** | one canvas without header, grid, overlays or panels |

Switch with the workspace buttons in the bar, `Ctrl+W` then `1`–`9`, or
`pxcmd.wm.workspace(2)`. The **scratchpad** holds hidden tiles that pop up over
the board. The Script Editor lives there (``Ctrl+` ``).

**Workspaces are saved with the scene.** Saving a file stores its workspaces
(names, numbers, splits and tabs, each view's camera and display settings, the
window style); opening it brings them back. Windows themselves (their
positions, torn-off windows) belong to your machine and stay as they are.
A file without workspaces keeps the ones you have.

| To… | Do this |
| --- | --- |
| **Add a workspace** | the **+** at the end of the workspace buttons → *Blank* or a template; *Window → Workspaces → New Workspace*; or `Ctrl+W` then a number that isn't used yet |
| **Rename it** | right-click its button → *Rename…* |
| **Delete it** | right-click its button → *Delete…* (the last workspace can't be deleted) |
| **Save it as a template** | right-click its button → *Save as Template…* |
| **Turn it into a template's layout** | right-click its button → *Apply Template* → pick one (its number and name stay) |

New workspaces get the lowest free number from 1 to 9.

### Workspace templates

A template is a workspace layout you can reuse in any scene: its splits and
tabs, view types, display presets, linked views and window style (not where
each view looks, which belongs to one scene). Board, Review and Present are
built-in templates; yours are kept in your user settings and listed with them
(a template of yours with a built-in name replaces it). Use one to start a new
workspace or to reset the current one.

```python
pxcmd.wm.new_workspace("Paint-over")                     # blank
pxcmd.wm.new_workspace("Compare", template="Review")      # from a template
pxcmd.wm.rename_workspace("Compare", "Lighting")
pxcmd.wm.rename_view("Reference", "Main")                 # a view's title (refused if taken)
pxcmd.wm.save_workspace_template("Two up")                # the current workspace
pxcmd.wm.apply_workspace_template("Two up", "Lighting")
pxcmd.wm.workspace_templates()                            # [{'name': 'Board', 'builtin': True}, …]
pxcmd.wm.delete_workspace_template("Two up")
pxcmd.wm.delete_workspace("Lighting")
```

## Splitting and joining views with the mouse

Everything below works without a shortcut.

| To… | Do this |
| --- | --- |
| **Split a view** | click ◫ (split right) or ⬓ (split down) in its header. The new view is a copy: same camera, same display preset. |
| **Split at a chosen size** | drag one of the view's **corners** (the small marks) *into* the view. A line follows the cursor, and the new view takes the part between the line and the corner you grabbed. Drag mostly sideways for side by side, mostly up or down for stacked. |
| **Join two views** | drag a corner *onto the neighbouring view*. The neighbour darkens, and on release your view takes over its space (the neighbour closes). |
| **Cancel a drag** | `Esc` or right-click before releasing. |
| **Switch side by side ↔ stacked** | right-click the divider between the views and choose *Stack vertically* or *Place side by side*. The view's `⋯` menu has the same entry. |
| **Swap the two sides** | right-click the divider and choose *Swap left and right* (or *Swap top and bottom*). |
| **Even out the sizes** | double-click the divider. |
| **Open another kind of view next to this one** | `⋯` → *Split right with* → pick the view type. |
| **Rename a view** | double-click its title in the header (or a tab's title), type, `Enter` (`Esc` cancels). Also `⋯` → *Rename…* and `Ctrl+W` `Shift+R`. |

Views that float, fill their window (fullscreen) or have no header (the
Present workspace) don't show the corners or split buttons.

Scripts use the same commands: `pxcmd.wm.split_view("right", ratio=0.3)`,
`pxcmd.wm.rotate_split()`, `pxcmd.wm.swap_split("Split")`,
`pxcmd.wm.equalize_split("Split")` and `pxcmd.wm.join_view("Main2", into="Main")`.

## Window mode (the leader key)

Press **`Ctrl+W`**, then one or more of these keys. `Esc` leaves the mode, and
so does a couple of seconds without a key.

| Key | Does |
| --- | --- |
| `H` `J` `K` `L` / arrows | focus the view left / down / up / right |
| `Shift` + those | move the focused view in that direction |
| `S` / `V` | the next view opens beside / below the focused one |
| `N` | open a new view (pick its type) |
| `T` / `Shift+S` / `E` | tabbed / stacked / toggle the split's orientation |
| `F` | fullscreen the focused view within its window (tap `Space` on a canvas does the same) |
| `Space` | float / unfloat the focused view |
| `R` | **resize mode**: arrows or `H J K L` grow and shrink, `=` equalizes, `Esc` leaves |
| `=` | give every view of the workspace the same share |
| `Q` | close the focused view (the last canvas can't be closed) |
| `-` | show / hide the scratchpad |
| `O` | tear the view off into a window of its own |
| `M` / `'` | mark the view with a name / jump to a marked view |
| `Shift+R` | rename the focused view |
| `1`–`9` / `Shift+1`–`9` | show workspace *n* / move the focused view there |

Outside window mode: `Ctrl+Alt+arrows` move focus, and `Tab` on a canvas hides
or shows the panels. Each tile's header has split buttons and a `⋯` menu
(split, rotate, change view, float, fullscreen, mark, close…). Canvas headers also have the camera
and display-preset menus.

**Focus follows the mouse** by default: hovering a view focuses it, so keys
and commands act on the view under the cursor. Turn it off with
`pxcmd.wm.set_focus_follows_mouse(False)`. New views go where you said with
`S`/`V` (i3's manual placement). `pxcmd.wm.set_placement("auto")` alternates
splits instead, and `pxcmd.wm.set_rule("console", "floating")` gives a view
type a fixed placement.

## Panels

The Tools, Outliner and Attribute Editor live outside the tiling layout, so
they are there in every workspace and never take a tile's place. Each panel
has a mode, remembered between sessions:

| Mode | Where |
| --- | --- |
| **Docked** (default) | a section of the sidebar beside the tiles. Click a section's title to collapse it, drag between sections to share the height, drag the sidebar's edge to resize it (220–640 px). |
| **Floating** | a card over the canvas: drag its title to move it, its corner to resize, double-click the title to collapse it. |
| **Hidden** | not on screen; pop it up when you need it. |

In any mode a panel **pops up on demand**:

| Key | Pops up |
| --- | --- |
| `Alt+T` | Tools, at the cursor (closes once you pick a tool) |
| `Alt+O` | Outliner, at the cursor (`F2` also opens it to rename when the outliner isn't on screen) |
| `Alt+A`, `Alt+Enter` | Attribute Editor, next to the selection (also *Edit Attributes…* in the context menu) |

A pop-up closes when you click on the canvas or press `Esc`; its pin button
keeps it open as a floating card. `Tab` on the canvas hides the sidebar and
floating panels. Present doesn't show them at all (pop-ups still work
there).

Each panel has a colour (Tools blue, Outliner green, Attribute Editor amber)
shown on its header and on its **tab**: the tabs run down the window's outer
edge, beside the sidebar (on the right by default). Click a tab to show or
hide its panel; right-click it for the modes, the pop-up, or to move the
panels to the other side.

Change modes with the buttons in each panel's title (float, dock, hide), the
tabs, *Window ▸ Panels*, or from scripts:

```python
pxcmd.panels.set_mode("outliner", "floating")   # docked, floating, hidden
pxcmd.panels.popup("inspector")
pxcmd.panels.toggle("tool_palette")             # hide / show where it was
pxcmd.panels.toggle_all()                       # Tab
pxcmd.panels.set_side("right")
pxcmd.panels.set_width(360)
pxcmd.panels.list()
```

## Changing keys

The leader key is rebindable with `pxcmd.keymap.set_leader("Ctrl+Space")`
(remembered), or with `window = "…"` under `[leader]` in `keymap.toml`. Keys
inside window mode are `[[bind]]` entries with `mode = "window"`; see
[keymap.md](keymap.md#keymaptoml).

## Canvas views: cameras and display presets

Each canvas view has its own camera (centre, zoom, rotation) and display
settings:

- **Display presets**: *All*, *Value check* (greyscale), *Images only*,
  *Annotations only*, *Clean* (no grid, no overlays), plus your own saved as
  `display_preset` nodes with `pxcmd.views.create_display_preset("Lighting",
  show_tags=["lighting"])`. Presets can filter by type and tag, and set
  greyscale, a background override, the grid, overlays and a framing guide.
- **Isolate** (`Shift+I`) shows only the selection in that view; press again
  to show everything. **Value check** (`Alt+V`) toggles greyscale.
- **Framing guides** (16:9, 2.39:1, 1:1, 4:3, 3:2):
  `pxcmd.views.frame_guide("16:9")`.
- **Camera bookmarks** are `camera` nodes saved with the scene. Create one from
  the header's camera menu or with `pxcmd.views.create_camera("Overview")`;
  look through one with `Ctrl+1`…`Ctrl+9` or `pxcmd.views.look_through(...)`.
- **Linked views** share one camera: pan, zoom, rotate or flip one and the
  others follow. The header's **Link ▾** menu lists every other canvas view
  (this workspace's first). Tick one to link it, untick it to unlink it, or use
  *Link all views in this workspace*. The button reads *Linked n* while the
  view is in a group of n. Linking is transitive: linking a view that is
  already linked to others joins the groups. The other views jump to the view
  you linked from. Scripts:
  `pxcmd.views.link("ReviewColor", "ReviewValue")` (the second follows the
  first), `pxcmd.views.toggle_link(other, view)`, `pxcmd.views.unlink(view)`,
  `pxcmd.views.link_workspace()`, `pxcmd.views.linked_with(view)`.
  A tile's state stores its `link_group`. Splitting a view copies its camera
  but not its link, and each copy of a workspace template gets its own
  groups.

Camera moves are not undoable. Bookmarks are document data, so creating and
editing them is.

## Saving layouts

The whole layout (windows, workspaces, splits, tiles, each view's camera and
display settings) is saved in your user settings and restored at the next
start. Scene files keep their workspaces (see [Workspaces](#workspaces)).
*Window → Reset Layout* goes back to the built-in workspaces.
`pxcmd.wm.save_layout("Paint-over")` and `pxcmd.wm.load_layout("Paint-over")`
keep named snapshots of the whole layout, windows included.

Managing **several open scenes** at once — the Files panel tab on the right
edge, detaching a scene into its own window and the double-click
preference — is designed in [design-multi-file.md](design-multi-file.md).

## Scripting the layout

The layout is itself a small node graph with node types `wm_output` (a
window), `wm_workspace`, `wm_split`, `wm_tabbed`, `wm_stacked`, `wm_tile` and
`wm_scratchpad`. It is kept separate from the document and its undo history.

```python
pxcmd.wm.ls(type="wm_tile")                 # ['Main', 'ReviewColor', 'ReviewValue', 'Present', 'Console']
pxcmd.wm.tree()                             # the whole layout as nested data
pxcmd.wm.focus(tile="Main")
pxcmd.wm.split("v")
tile = pxcmd.wm.open_view("canvas", title="Detail")
pxcmd.wm.set_attr(tile, "header", 0)        # hide its header
pxcmd.wm.open_view("contact_sheet", placement="floating")
pxcmd.wm.mark("detail", tile)
pxcmd.wm.focus(mark="detail")
pxcmd.wm.tear_off(tile)                     # a second top-level window
pxcmd.wm.output_style(on_top=True, opacity=0.8)
```

Plugins add view types with `ui.view_type(ViewType(...))` and whole workspaces
with `ui.workspace(WorkspacePreset(...))`; see [plugins.md](plugins.md).
