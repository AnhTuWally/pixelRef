# Design notes: backdrops and "hosting" (on-the-fly grouping by touching)

Status: **decisions made 2026-10-20; phases 0-3 implemented (see "Implementation status" at the end).** The sections
below are the original proposal; the "Decisions" section overrides them where
they differ (notably the naming: read "host" as "backdrop", "member" as
"contents").

## Decisions (2026-10-20)

| Topic | Decision |
| --- | --- |
| Representation | **Hierarchy adoption (option C).** Joining = reparent under the backdrop, leaving = reparent out, placement kept. One backdrop per node; backdrops nest. |
| Mental model | A stack of papers or pictures moved together as a group. Moving a backdrop moves its contents. |
| Stacking | A backdrop **always draws behind its non-backdrop siblings** (risk 1, option b). Applies to the `backdrop` node type only: an image or rectangle that merely *acts* as a backdrop keeps its place among its siblings, otherwise switching the toggle on would sink it behind everything. Contents draw above their backdrop (children draw above parent). Backdrops keep their relative order; a nested backdrop draws above its parent. The tree is reordered on create, join and restack so the outliner matches what is drawn. |
| Naming | UI and code say **backdrop**. Nodes inside one are its **contents**. Opt-in toggle on other nodes: "Act as backdrop" (`backdrop_mode`). Capability: `backdrop`. Plugin: `backdrops`. Extension point: `backdrop.policies`. Commands: `backdrops.settle / adopt / release / contents / wrap / create / set_mode` (the plugin id prefixes every command). |
| Strict vs free | One setting that changes the **default**, not two code paths. *Strict* (default): only backdrops and nodes with `backdrop_mode` set act as backdrops. *Free*: any rectangle or image defaults to `inside` (groups later: their extent is their members, so they would grow by capture). Per-node values always win. A backdrop can never be smaller than what it adopts; the preview always shows before release. |
| Backdrop-only move | **Not provided** (open question 3). Resize handles change `size` (not `scale`), and `backdrop.release` plus a move covers the rest. Revisit with Ctrl+Alt+drag on the title bar if real use asks for it. |
| `touch` semantics | Overlap, with a small zoom-scaled margin. |
| Key | `B` is taken by redact and `S` by split view. **`K`** wraps the selection in a backdrop, or arms the draw-backdrop tool when nothing is selected. Check against all registered shortcuts before release. |
| Links | Dropping onto a backdrop does **not** create a link. Membership and links stay separate. |
| Overlap of backdrops | Smallest (innermost) wins, nesting handles the rest (open question 1 accepted). |

### Visual indicators (non-intrusive)

- Outliner: dedicated backdrop type icon; a small badge on the icon for a node
  that only *acts* as a backdrop. Contents are already indented.
- Canvas, selected backdrop (or any node with `backdrop_mode`): faint dashed
  outline around its contents. Nothing extra when unselected.
- A small title tab on the backdrop's top-left edge (also the double-click
  target for editing the title).
- Opt-in nodes (image or rect acting as a backdrop): tiny corner glyph on hover
  or selection only.
- Hovering a content item lightly tints its backdrop's outline.
- While dragging: only the candidate backdrop under the pointer lights up, with
  the Join / Leave chip.
- Avoid: fills or borders on every backdrop-capable node, and colour-coding
  contents by backdrop.
- Phase 5 candidate: a View toggle "Show backdrop relationships" drawing faint
  lines or tints from each backdrop to its contents (off by default).

## Goal

Let items on the board join a group just by being dropped on top of something,
and leave it by being dragged off, with no dialogs and no commands to learn.
The reference is Toon Boom Harmony's Backdrop: a coloured, titled rectangle
behind a set of nodes. Move the backdrop and what sits on it moves too.

The extra requirement is that this is **not only a backdrop type**. Any node
type should be able to act as the thing others join (a "host"), and any
canvas node should be able to be joined (a "member").

(Harmony's behaviour here is from memory of its backdrop feature, not a fresh
read of the linked page. Worth a quick check against the docs before locking
details such as what happens at the backdrop's edge.)

## What exists today

| Piece | Relevant fact |
| --- | --- |
| Tree | The document is a node tree. Sibling order is draw order. Any `canvas_node` can have children (the per-frame design already relies on annotations as children of an image). |
| `group` | `builtins/grouping`. A hard bundle: a click picks the whole group, double-click enters it (`captures_children`, `opens_for_selection`). `pick_members` turns it into a plain folder whose members are clicked directly (`ContainerItem`). |
| `move_nodes` / `parent` | Reparent **keeping canvas placement** and restack, undoable. `group` and `ungroup` are built on it. |
| `link` | Ends follow elements, groups included. No effect on membership. |
| Bounds | `session.world_bounds(name)` works headless. The UI has exact polygons (`item.scene_polygon()`). |
| Drag | The select tool brackets a drag with `canvas.begin_interaction()` / `end_interaction("Move")`: one undo step. That is the natural place to settle membership. |
| Hooks | `ctx.extend_node_type("canvas_node", ...)` adds sparse attributes to every type. Extension points, `canvas.overlays` and tidy hooks (`commands.on_tidy`) exist. |
| Capabilities | `CAP_CONTENT`, `CAP_CONTAINER`, `CAP_COMPOUND`... are free-form strings that let generic features serve types they have never heard of. This is the right place for a "host" capability. |

## Three ways to represent "this belongs to that"

| | A. Computed (nothing stored) | B. Soft link attribute | C. Hierarchy (adoption) |
| --- | --- | --- | --- |
| Idea | At drag start, find what is under the host and move it too. | Member stores `host = <id>`; moving a host propagates its transform to members. | Joining = reparent the member under the host. Leaving = reparent back out. |
| Persisted / scriptable | No. Invisible in the file and the outliner. | Yes. | Yes, for free. |
| Host moves, members follow | Only during a UI drag. A script moving the host moves nothing. | Needs a new kernel propagation rule on transform changes. | Free (parent transform). |
| Undo, copy/paste, duplicate, hide, lock, opacity | Ad hoc. | Each needs handling. | All inherited from the tree. |
| Outliner | Nothing to show. | Needs a custom view. | Shows nesting already. |
| Overlapping hosts | Ambiguous, rederived each drag. | Possible (many-to-many) but confusing. | One host per member by construction; hosts can nest. |
| Cost | Cheap, fragile. | Highest: duplicates what the tree already does. | Lowest, but changes stacking (see risks). |

**Recommendation: C, with a deliberately thin layer on top.** The kernel
already has the right structure; the new work is the *rules for when to
reparent* and the *feedback while dragging*, not a new relation.

## Proposed model

### Vocabulary

- **Host**: a node that others can join. Declared by the `host` capability on
  a node type, and switched on per node (below).
- **Member**: a child of a host. Any `canvas_node` can be one.
- **Settle**: re-evaluating membership for some nodes. This is the one
  operation everything else calls.

### Pieces

1. **`backdrop` node type** (inherits `canvas_node`, capability `host`):
   `size`, `color`, `opacity`, `title`, `title_size`, optional `note`. Draws a
   filled rounded rectangle with a title bar. Its item does **not** capture
   clicks, so members are picked directly, like `pick_members` groups.
2. **Host capability on other types.** `annotation_rect` gets it (off by
   default). Text notes, images and groups can opt in later. A sparse
   attribute on `canvas_node`, `host_mode`, switches it per node:
   `off | inside | overlap | touch`, with the type's default used when unset.
   Making any rectangle a backdrop is then one toggle in the Attribute Editor.
3. **`HostPolicy` contributions** on a new extension point `host.policies`,
   one per host type, so each type defines what "touching" means:

   ```python
   HostPolicy(
       node_type="backdrop",
       default_mode="inside",
       join_threshold=0.5,     # share of the member inside the host to join
       leave_threshold=0.3,    # share to stay (lower: hysteresis, no flicker)
       accepts=lambda host, candidate: True,   # type filter
       priority=0,             # tie-break between overlapping hosts
   )
   ```

   Suggested defaults: backdrop `inside` and on; rectangle `inside` but off;
   a text note `touch` (a sticky note that rides along with the image under
   it), off.
4. **Commands** (headless, in a new `hosting` plugin, so batch scripts get the
   same behaviour):

   | Command | Does |
   | --- | --- |
   | `hosting.settle(nodes=None)` | Re-evaluate membership for these nodes (default: selection) and for hosts among them. |
   | `hosting.adopt(host, nodes)` / `hosting.release(nodes)` | Explicit join / leave, bypassing the rules. |
   | `hosting.members(host)` | The members. |
   | `hosting.wrap(nodes=None, padding=24, title="")` | Create a backdrop sized around the nodes and settle. |
5. **UI**: the select tool calls `settle` just before `end_interaction`, so
   the move and the membership change are **one undo step**. A new
   `canvas.overlays` painter gives live feedback while dragging.

### The settle rules

For each candidate node (top-level in its container, see below):

1. Find hosts whose mode accepts it, excluding the node itself, its own
   descendants (no cycles), and hosts smaller than the node (a big image does
   not get swallowed by a small backdrop).
2. Score each: `inside` uses the share of the node's area inside the host;
   `overlap` uses any overlap above a small epsilon; `touch` uses overlap or
   edge distance under a few pixels at the current zoom.
3. If the candidate is already a member, it stays while the score is at or
   above `leave_threshold`. Otherwise it joins only at `join_threshold`.
4. If several hosts qualify, pick the **smallest** by area (the innermost
   backdrop), then the highest `priority`, then the front-most.
5. Reparent with `move_nodes(..., place="into")`, which keeps placement. If no
   host qualifies and the node has a host parent, move it out to the host's
   parent, just above the host.

Settle is **never** triggered by plain `set_attr`. A script that sets
`translate` does not silently reparent things. Only UI gestures and explicit
commands settle. (A tidy hook would settle after every command, which is
exactly the surprise to avoid.)

### What counts as a candidate

- Selected nodes after a move, or the node just created or dropped (draw a
  rectangle, paste, drop an image).
- When a **host** is moved or resized, settle **everything that overlaps its
  new area**, so resizing a backdrop over loose nodes captures them, and
  shrinking it releases the ones it no longer covers.
- A node inside a regular `group` is not a candidate on its own. The group is.
  Same for pen strokes inside a drawing (`compound`).

## What the user sees (the intuitive part)

| Gesture | Result |
| --- | --- |
| Drag an item over a backdrop | The backdrop outline brightens and a small "+ Join" chip shows at the pointer. Release to join. No other feedback is needed. |
| Drag a member out | The backdrop dims, the chip reads "Leave". Release to leave. |
| Drag the backdrop | Members move with it. They are children, so this needs no extra code on the canvas. |
| Hold **Alt** while dragging | Move without joining or leaving. (Alt+press is already ignored by the select tool, so check for a clash first.) |
| Draw a rectangle or backdrop around things | Everything inside is captured on release. |
| Select several things, press **B** | `hosting.wrap`: a backdrop appears around them, padded, with the title field focused. Mirrors `G` for Group. |
| Select a host | Its members get a faint dashed outline. |
| Resize a backdrop | Members join or leave on release. |
| Double-click the title | Edit it in place. |
| Undo | One step for the move and the membership change together. |

Principles: nothing happens mid-drag except a preview, the preview is always
the exact thing release will do, and there is a one-key way out (Alt, or undo).

## How this sits next to `group`

They stay separate, because they answer different questions.

| | Group | Host (backdrop) |
| --- | --- | --- |
| Made by | Explicit command (`G`) | Placement: drop it on something |
| Click picks | The whole group | The member itself |
| Looks like | Nothing (invisible bundle) | The host itself is drawn |
| Membership changes | Never by moving | By moving, on release |

Both are tree parents, so they nest: a group can be a member of a backdrop and
a backdrop can contain groups. Provide *Backdrop to Group* and *Group to
Backdrop* as simple conversions later.

## Plugin layout

- `builtins/hosting/`: headless mechanism (capability, `HostPolicy`, commands,
  `host_mode` extension) and the drag-feedback overlay. Optional, not a
  foundation plugin.
- `builtins/backdrop/`: the node type, its item, title editing, the `B` action
  and palette button. Depends on `hosting`.
- The select tool needs a single hook: an extension point (for example
  `select.drop_handlers`) called before `end_interaction`, so it does not
  import `hosting`. The same hook lets other plugins react to a drop.

Disabling `hosting` leaves backdrops as plain rectangles that still contain
their children.

## Risks and things to verify first

1. **Stacking changes on join.** Members live in the host's child list, so
   their draw order relative to unrelated siblings can change. Example: a
   member that sat in front of an unrelated item `C` (which only brushes the
   backdrop) ends up behind it. Options: (a) accept it, since backdrops
   normally sit at the back; (b) make backdrops always draw behind
   non-backdrop siblings; (c) raise the host to just under its front-most
   member on join. (b) is the closest to Harmony and the simplest to explain.
2. **Item and click behaviour for children of non-container nodes.** The
   per-frame design assumes it, but a spike should confirm that children of a
   rect or image item draw, hit-test, select and move correctly, including
   with a rotated host.
3. **Rotated or scaled hosts.** Headless decisions use axis-aligned
   `world_bounds`, so they are approximate for rotated nodes. The UI can use
   `scene_polygon()` for the preview. The two must agree, or the preview lies.
   Suggest settle decisions in the UI use polygons and the headless version is
   documented as bounds-based.
4. **Scale on the host.** Scaling a backdrop with the gizmo scales its
   members. That is right for a group but may surprise for a backdrop. Resizing
   should change `size`, not `scale`, so members stay put.
5. **Old builds.** Older versions open the file with a placeholder for the
   unknown `backdrop` type. Verify that its children are kept and still shown,
   not hidden with the placeholder.
6. **Locked nodes.** Proposal: a locked host still adopts, a locked member
   never leaves. Needs a decision.
7. **Cost.** Settle runs on release only, with a cheap bounds pre-filter
   against the (few) hosts, so it should be fine at thousands of nodes. Measure
   on a large board.
8. **Surprise captures.** Large `touch` hosts would grab things constantly.
   That is why only backdrops are on by default and everything else is opt-in
   per node.

## Phases

| Phase | Deliverable | Notes |
| --- | --- | --- |
| 0 | Spike: items parented under a rect and an image, drag, rotate, z-order, placement kept. | Answers risks 1, 2 and 3 before any API is fixed. |
| 1 | `hosting` headless: capability, `HostPolicy`, `settle` / `adopt` / `release` / `wrap`, `host_mode`. Tests in the headless style. | No Qt. Covers overlap, nesting, hysteresis, cycles, undo. |
| 2 | `backdrop` type and item, resize handles, title editing, `B`. | Usable end to end, with settle called on release. |
| 3 | Drag feedback: preview overlay, Join / Leave chip, Alt bypass, member outlines. | This is where "intuitive" is won or lost. Expect iteration. |
| 4 | Opt-in on rectangles, text notes and images: `host_mode` in the Attribute Editor, policies, docs. | Proves the design is general. |
| 5 | Polish: conversions to and from Group, collapse, auto-fit to members, user-guide page. | Optional. |

## Open questions for review

*All resolved on 2026-10-20, see "Decisions" above: 1 accepted, 2 backdrops
behind non-backdrop siblings, 3 no backdrop-only move, 4 `K`, 5 overlap with
margin, 6 "backdrop" / "contents", 7 keep links separate.*

1. Is one host per member (a tree) enough? Harmony-style overlapping backdrops
   with a node in two of them would need option B. This design chooses the
   innermost host and nests instead.
2. Stacking: accept the change, force backdrops to the back, or raise the host?
3. Should a host-only move exist (move the backdrop and leave members behind)?
   If so, which modifier. Shift is taken by toggle-select.
4. Is `B` the right key, and should it wrap the selection or start a draw
   tool when nothing is selected?
5. Should `touch` mean edge contact or overlap? Proposed: overlap, with a small
   zoom-scaled margin.
6. Names: "backdrop" and "host" are used here. "Member" could read as
   "child". Is "host / guest" or "backdrop / rider" clearer in the UI?
7. Should dropping a member onto a host also offer a link (so hosting and
   links share one gesture), or stay separate?

## Implementation status (2026-10-20)

Built in `pixelref/builtins/backdrops/` (headless engine in `__init__.py`, Qt in
`ui.py`), tests in `tests/test_backdrops.py`.

| Phase | State |
| --- | --- |
| 0 Spike | Done by building it: children of a backdrop item draw, hit-test, select and move correctly; rotation not exercised yet. |
| 1 Headless | Done: `backdrop` type, `backdrop_mode`, `BackdropPolicy` / `backdrop.policies`, `settle`, `adopt`, `release`, `contents`, `wrap`, `create`, `set_mode`, stacking rule (tidy hook). |
| 2 Type and item | Done: item with title bar (title bar and border pick, the body does not), resize points that change `size`, double-click title to rename, `K`, palette button, draw tool. |
| 3 Feedback | Done: green Join / amber Leave outline and chip while dragging, Alt-on-release bypass, dashed outline of a selected backdrop's contents. Not done: hover tint of the backdrop. |
| 4 Opt-in | Rectangles and images have policies (free mode, or `backdrop_mode` per node). Text notes and the Attribute Editor badge are still to do. |
| 5 Polish | Not started: conversions to and from Group, collapse, auto-fit, "Show backdrop relationships", outliner badge for acting backdrops. |

Mechanism added to the kernel UI: `CanvasDocument.add_interaction_handler`
(`handler(label, names)`), called after a drag, nudge or resize is committed and
before its undo step closes. It replaces the `select.drop_handlers` idea: it
also covers scale, rotate and resize, and any plugin can react to a finished
canvas edit.

Known limits: decisions use axis-aligned bounds (rotated nodes are approximate,
preview and decision agree because both use bounds); drawing or pasting a node
inside a backdrop does not auto-join it yet (move it once, or `K`/`settle`);
the Alt bypass is read from the keyboard at release, because Alt+press is
already ignored by the select tool. Measured: settling one node on a
5,000-node board takes about 12 ms, settling a moved backdrop about 80 ms.
