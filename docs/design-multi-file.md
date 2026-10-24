# Design notes: multi-file management (open scenes across windows)

Status: **decisions made 2026-10-23; not yet implemented.** The chosen design is a
**right-side "Files" panel tab** — a new tab on the right edge alongside Tools /
Outliner / Attribute Editor — listing recent files, with an **orange border**
marking any file that has unsaved changes. Concept C (breadcrumb stack + popout
drawer) is documented as the fallback.

## Decisions (2026-10-23)

| Topic | Decision |
| --- | --- |
| Concept | **Right-side "Files" panel tab.** A new tab on the right edge (with Tools / Outliner / Attribute Editor) opens a panel listing recent files. No new left rail — it reuses the existing panel-tab pattern, so it adds no new chrome vocabulary. |
| Unsaved indicator | A file with unsaved changes gets an **orange border** around its entry in the Files list. The active file is highlighted with the accent color. |
| List contents | Recent files, most recent first, each with the 320 px thumbnail the start panel already uses. |
| Switch | Click a file to open/switch to it (with the usual unsaved-changes prompt). |
| Detach | Right-click a file → *Open in New Window*, or drag it out past the window edge. |
| Re-attach | Drag a file from one window's Files list onto another window's list. |
| Double-click | Preference *Open files on double-click*: **Current Window** (default) or **New Window**. `Shift+double-click` always forces the opposite. |
| Preference location | Settings → **Workspace → File Opening**, segmented control. |
| State scope | The recent-files list is **app-level state**, not part of the document or its undo history — same rule as the layout graph. |
| Session restore | The Files list and detached-window geometry persist with the layout snapshot, so restart reproduces the exact workspace. |
| Alternative | **C — breadcrumb stack + popout drawer**, documented below as the fallback. |

## Goal

Support several open `.pxref` scenes at once — switching between them inside
one window, and moving them between windows — without adding another
horizontal tab strip. The top and bottom edges already carry the workspace
buttons, the view tabs and the panel tabs; a third tab row would be visual
overload and navigation confusion. The right edge already hosts the panel tabs
(Tools / Outliner / Attribute Editor), so a new **Files** tab there is the
lowest-friction place to put file switching.

## What exists today

| Piece | Relevant fact |
| --- | --- |
| Document model | One document per window. `file.open()` / `open_scene(path)` *replace* the current document; there is no scene list. |
| Windows | `wm_output` nodes are top-level windows; `wm.tear_off(tile)` already moves a *view* into a window of its own. Scenes have no equivalent. |
| Thumbnails | Every save stores a small JPEG in the file; the start panel shows recent files as cards with them. The Files list reuses the same images. |
| Panel tabs | Tools / Outliner / Attribute Editor run down the window's right outer edge — the exact pattern the new **Files** tab reuses. |
| Keymap | `Ctrl+K` command search, `Ctrl+P` pack, `Ctrl+1`–`9` view markers, `Ctrl+W` window mode, `Ctrl+T` on top, `Ctrl+Shift+T` click-through, `` Ctrl+` `` Script Editor. `Ctrl+Shift+Tab` and `Ctrl+Shift+1`–`9` are free. |
| Session Resume | *File → Resume Last Session on Launch* reopens the workspace as left; the Files list and detached windows extend this to the scene list. |

## Chosen concept — right-side "Files" panel tab

```text
┌────────────────────────────────────────────────────────────┬──────────┐
│  workspace buttons · view tabs            (existing top)   │  Tools   │
├────────────────────────────────────────────────────────────┼──────────┤
│                                                        │  Outliner│
│                                                        │  Attr.   │
│              canvas / tiling area                      │  ┌──────┐ │
│                                                        │  │Files │ │
│                                                        │  └──────┘ │
├────────────────────────────────────────────────────────────┼──────────┤
│  status / bottom bar                      (existing bottom)│         │
└────────────────────────────────────────────────────────────┴──────────┘
  right edge = existing panel tabs + a new "Files" tab
  Files panel (when open) = vertical list of recent files
    ▣ character_refs.pxref        (active — accent highlight)
    ▣ lighting_study.pxref
    ▣ moodboard_v3.pxref          (orange border — unsaved changes)
    ▣ palette_test.pxref
```

- **The Files tab** is a new tab on the right edge, alongside Tools / Outliner /
  Attribute Editor. Clicking it opens a panel listing recent files, most recent
  first, each with its saved thumbnail.
- **Unsaved changes** are marked with an **orange border** around the file
  entry, so you can see at a glance which open files still need saving.
- **The active file** is highlighted with the accent color.
- **Switch** by clicking a file (the usual unsaved-changes prompt applies).
- **Detach** by right-click → *Open in New Window*, or by dragging the file out
  past the window edge. Drag it back onto another window's Files list to
  re-attach.

**Pros:** reuses the existing right-edge panel-tab pattern — zero new chrome
vocabulary; the panel hides when not needed, so it adds no permanent clutter;
the orange border is an unambiguous unsaved indicator; the list scrolls
vertically and scales to many files.

**Cons:** the panel takes horizontal space while open (mitigated by hiding it,
exactly like the other panels); reaching a file is one click further than an
always-visible rail.

## Concept C — breadcrumb stack + popout drawer (alternative)

The scene's name sits in the header as a breadcrumb segment; clicking it opens
a dropdown listing the other open scenes. A "background scenes" drawer slides
in from the left edge on hover or `Ctrl+Shift+Tab`.

**Pros:** nearly zero permanent chrome — everything is on-demand; cheapest to
build; good for users who work in one folder at a time.

**Cons:** weak discoverability of open scenes (hidden until hovered);
cross-folder switching is awkward; the UI never communicates *how many*
scenes are open.

**Why the Files tab wins:** it lives on the edge users already use for panels,
adds no new pattern, and the orange border makes unsaved state visible at a
glance. C remains the fallback if even the panel is judged too heavy.

## Multi-window and detach workflows

| Action | Gesture | Result |
| --- | --- | --- |
| Detach a file | right-click its entry → *Open in New Window*, or drag it out past the window edge | new top-level window holding that file |
| Pop out | right-click entry → *Open in New Window* | new window; file removed from the current one |
| Re-attach | drag a file from one window's Files list onto another's | file merges into the target window |
| Duplicate view | *Open in New Window* on an already-open file | same file in two windows — a **linked view**, never silently shared state |

A file open in two windows is explicitly a linked view: both windows show the
same document, and a save in either updates both. There is no snapshot mode in
v1.

## Configurable double-click behaviour

Setting: **Open files on double-click** → *Current Window* (default) or
*New Window*. Applies to double-clicking a file in the start panel, the
system file browser drop, and the Files list's *Open…* entry.

```text
double-click a file
      │
      ▼
  preference?
   ┌──┴───────────────┐
   │                  │
Current Window      New Window
   │                  │
   ▼                  ▼
switch in this      spawn a new
window (unsaved-    window, focused,
changes prompt)     file in its own panel
   └──┬───────────────┘
      │
      ▼
modifier overrides (always):
  Shift+double-click  → force the opposite of the preference
  Ctrl+double-click   → force Current Window
```

Modifier keys always act as the inverse of the preference, so power users
never need to visit Settings to do the other action.

## Preference controls

- **Location:** Settings → **Workspace → File Opening**, next to the other
  window and tab behaviours.
- **Control:** segmented button (*Current Window | New Window*), not a
  dropdown — two options deserve visible state.
- **Live hint:** one line beneath: *"Shift+double-click always opens a new
  window."*
- **Scope:** per-app, with an optional per-project override (a project
  `.pixelref` setting wins), matching how other workspace settings behave.

## Keys

| Key | Does |
| --- | --- |
| `Ctrl+Shift+Tab` | cycle to the next open file (current window) |
| `Ctrl+Shift+1`–`9` | jump to the n-th open file |
| `Ctrl+Shift+O` | open the highlighted file in a new window |
| `Shift+double-click` | force the opposite of the double-click preference |

All are rebindable through the normal keymap (`keymap.toml`, `pxcmd.bind_key`),
and none collide with the existing bindings listed above.

## Edge cases and clutter guards

1. **List overflow:** the Files list scrolls vertically; keep thumbnails
   legible — never shrink below a usable size.
2. **Orphan windows:** closing the last file in a detached window prompts
   *Close window / Re-attach to main* instead of leaving an empty shell.
3. **Duplicate opens:** clicking an already-open file focuses it (never opens a
   second entry) unless the user explicitly asks for a linked view.
4. **Unsaved indicator:** the orange border is the single source of truth for
   "needs saving" — it appears the moment a file is dirty and clears on save.
5. **Session restore:** the Files list order and detached-window geometry
   persist, so a restart reproduces the exact workspace.
6. **Modal conflicts:** any overlay dismisses on focus loss and never traps
   keyboard focus.
7. **Unsaved changes:** switching files or detaching a file with unsaved edits
   always asks first — never discard silently.

## Implementation notes

- The Files tab is a new panel on the right edge (like Tools / Outliner /
  Attribute Editor), so it appears in every workspace and never takes a tile's
  place.
- The recent-files list is app-level state, kept separate from the document and
  its undo history — the same rule as the `wm_*` layout graph.
- New commands (draft): `file.cycle()`, `file.jump(n)`,
  `file.open_in_new_window(path)`, `file.detach()`,
  `file.reattach()` — each undoable where it touches a document, and each
  appearing in `Ctrl+K` and the command reference.
- The Files list reuses the start panel's thumbnail cache.
- See [window-manager.md](window-manager.md) for the existing window and
  layout model this builds on, and [user-guide.md](user-guide.md#files-autosave-and-recovery)
  for the current single-file behaviour.
