# Design notes: per-frame annotations and frame links

Status: **build steps 1-3 done** in the `frames` plugin (`frame` attribute, visibility rule, pin / unpin /
copy-to, drawing on a paused frame, `Alt+F`, badges, filmstrip ticks, `source_frame` / `target_frame` on links,
click-to-jump). Not done: dragging from a tick to make a link, drawing a frame link from the tick instead of the
element edge, and step 4. Compiled 2026-10-20 from a design discussion.

Findings while building step 1:

- The `playback` plugin already is the frames service for GIFs and movies (`animated`, `frame_count()`,
  `current_frame()`, `go_to_frame()`) and already owns the `,` / `.` step keys, so no new service was needed.
- Drawing tools create top-level nodes, never children of an image. So a tidy hook adopts what a drawing
  command made into the paused animation under it and stamps the frame. While it plays, drawing is unchanged.
- Decided: default-valued sparse attributes are omitted from saved files (`Attr(omit_default=True)`, set back to
  default removes the value). Link frames are sparse attributes on `link`, so the links plugin was not touched.
- Pinned children hide through a `visible_rule` on the item that `CanvasDocument._finish` honours, so hidden
  notes also stop taking clicks.

## Goal

Annotate individual frames of a GIF or movie so each annotation has per-frame
context. Those annotations, and the frames themselves, can be linked to other
nodes on the board (for example, frame 3 linked to a note about frame 3). The
model should leave room for more creative uses later.

## Decisions so far

| Topic | Decision |
| --- | --- |
| Frame identity | Plain **frame index, 1-based**, matching the existing GIF code (`image/items.py`, "frames are numbered from 1"). No timestamps, no handling of re-cut files. |
| Dangling links | Fine. If a link's frame no longer exists, keep the value and show the end as unresolved (same treatment as missing images). Never delete data. |
| Where annotations live | Ordinary annotation nodes (text, rect, arrow, pen) as **children of the animated node**, with an optional `frame` attribute. Reuses every existing tool. |
| Is `frame` optional? | Yes. A sparse attribute declared with `ctx.extend_node_type("canvas_node", ...)`, default `0` = "all frames". Not set by hand and not a dynamic attribute (`pxcmd.add_attr`). |
| GIFs | GIFs are `image` nodes with playback already in `image/items.py:285`. Movies are a separate `movie` plugin. Both must support frames. |
| Cross-type support | A small `frames` **service** that animated node types implement (see below). |

## Core idea: a frame is an address

Today a link end holds a node id (`source` / `target`). Add an optional
sub-address to it:

```
node only          -> the whole element (today's behaviour)
node + frame: 3    -> frame 3
node + range       -> a span of frames (later)
node + spot        -> a point within the frame (later)
```

Links, notes and scripts deal only with "node plus optional address", so new
address kinds (ranges, markers, regions) can be added later without reworking
links.

For the `link` type this means two new attributes, `source_frame` and
`target_frame`, default `0` (whole element). Old files stay valid.

## The `frames` service

One shared contract so annotation and link code never cares what kind of node
it is:

- `frame_count(node)`
- `current_frame(node)`
- `set_frame(node, n)`

The image plugin (GIF / animated WebP) and the movie plugin each register an
implementation. A node is **frame-aware** automatically when its type
implements the service and `frame_count > 1`. Stills and one-frame GIFs get
none of the frame UI.

**Build order risk:** this touches both plugins. Either build the service
interface first with GIF as the only implementation (preferred, avoids baking in
GIF assumptions), or do GIFs end to end first and bolt movies on after.

## Per-frame annotations

- Annotations are children of the animated node, in its local space, so they
  stay glued to the picture when it is moved, scaled or rotated.
- **Visibility rule:** a child annotation is drawn only when its `frame` is `0`
  or equals the parent's current frame. Implement as a paint filter through the
  `canvas.item_hooks` contribution.
- **Drawing while paused on a frame stamps that frame automatically.** The user
  never types a frame number. Drawing during playback creates an all-frames
  annotation (or the tool asks the user to pause).

### Setting and clearing a frame (user workflow)

The user never edits the attribute directly. It is set by ordinary actions:

| Intent | Action | Result |
| --- | --- | --- |
| Annotate one frame | Pause or step to frame 3, draw with any tool | New node gets `frame = 3` |
| Annotate all frames | Draw during playback, or toggle **All frames** in tool options | `frame` stays `0` |
| Move a note to the current frame | Right-click -> *Pin to current frame* (or `Shift+F`) | `frame` = playhead frame |
| Make a note global again | Right-click -> *Show on all frames* | `frame` back to `0` |
| Copy to other frames | *Copy to frame...* (number or range) | Duplicates with other `frame` values |
| Bulk | Select several in the Outliner, same menu | Applies to the selection |

A small badge on each annotation (`f3`, or a globe for all frames) shows its
state. Each change is one `set_attrs` command, so undo, console echo and
scripting work without extra code.

## UI moments

1. **Frame strip:** a thin timeline under a frame-aware node. Frames that have
   annotations get a tick, with the count on hover. It is also the link anchor.
2. **Step keys:** `,` and `.` step one frame (extend the playback controls,
   input priority 700).
3. **Onion skin:** faint ghosts of neighbouring frames' annotations. This is the
   same visibility rule with a frame offset.
4. **Link from a frame:** drag from a strip tick to another node, or with the
   Link tool (`W`) while paused, drag from the element to anchor at the current
   frame. The link end gets a small `f3` badge. Existing link grabber (priority
   610) does the dragging.
5. **Link to a frame:** drag from a note onto a tick, or onto the element while
   paused.
6. A frame link is drawn from the tick, not the element edge, and stays visible
   whichever frame is showing.
7. **Navigation:** clicking a link end jumps the animation to that frame, and
   clicking a note jumps to its frame. Links work as navigation in both
   directions.
8. A "Clear frame" entry on the link-end handle sets the end back to the whole
   element (`0`).

## Extension ideas (all built on the frame address)

- **Markers:** named frames ("hit frame", "anticipation") stored as nodes; links
  target the marker.
- **Ranges:** `range: [3, 9]` highlights a span on the strip; good for notes
  about an action rather than a pose.
- **Compare and sync:** link frame 3 of clip A to frame 5 of clip B; a "sync"
  action scrubs both to their linked frames.
- **Extract frame:** create an image node from frame 3 and auto-link it back to
  its source frame. Pairs with the contact sheet plugin.
- **Link kinds:** a `kind` attribute on links (`note`, `reference`, `sync`) so
  tools act on meaning instead of colour.
- **Scripting:** for example `pxnode.node("clip").annotations(frame=3)` and
  `pxcmd.links_from(node, frame=3)`, consistent with the existing command model
  and batch mode.
- **Outliner:** group a node's annotations by frame.

## Suggested build order

1. `frames` service interface (GIF implementation first), the sparse `frame`
   attribute on annotations, the visibility rule, and step keys. Usable
   per-frame notes on their own.
2. Frame strip with annotation ticks.
3. `source_frame` / `target_frame` on links, anchoring to ticks, click to jump.
4. Ranges, markers, extract-frame, sync, each as a small separate plugin.

## Open items to verify before coding

- Whether default-valued attributes are omitted from saved `.pxref` files. I did
  not confirm this in the code; the design works either way (files just grow
  slightly if they are written).
- How the movie plugin exposes frame count and stepping. Only its manifest and
  attribute list were read, not `movie/items.py`.
- Whether the link grabber and `links/ui.py` (about 1300 lines, not yet read)
  can take a frame address without restructuring.
- Where drawing tools create nodes, so the "stamp the current frame" step can
  hook in once for all tools rather than per tool.

## Where this builds on existing code

- Links attach by node id: `pixelref/builtins/links/__init__.py`
- GIF / animated WebP playback, 1-based frames: `pixelref/builtins/image/items.py`
- Movie nodes: `pixelref/builtins/movie/`
- Extension mechanisms (sparse attributes, paint filters, services, extension
  points): `docs/architecture.md`
