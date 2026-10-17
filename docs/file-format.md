# The `.pxref` scene file

Scenes are readable, diff-friendly JSON in `.pxref` files (files ending in
`.json` open as scenes too). One file holds everything: the node tree,
scene-level plugin data and, by default, the images themselves. This page
describes schema 1, written by pixelRef 1.0.0.

```json
{
  "pixelref_version": "1.0.0",
  "schema": 1,
  "thumbnail": {"mime": "image/jpeg", "width": 320, "height": 180, "encoding": "base64", "data": "/9j/4AAQ…",
                "updated": "2026-10-16T21:47:03"},
  "types": {"annotation_text": 1, "group": 1, "image": 1},
  "plugins": {
    "canvas_background": {"background": [23, 25, 29], "pattern": "lines", "grid_size": 64, "pattern_color": null}
  },
  "nodes": [
    {"id": "6f0c…", "name": "Foreground", "type": "group", "parent": null,
     "attrs": {"tags": [], "translate": [0.0, 0.0], "rotate": 0.0, "scale": [1.0, 1.0], "pivot": [0.0, 0.0],
               "visibility": 1, "opacity": 1.0, "locked": 0, "pick_members": 1}},
    {"id": "a81d…", "name": "ConceptArt_01", "type": "image", "parent": "6f0c…",
     "attrs": {"asset": "3f2a…", "path": "", "translate": [250.0, -120.5], "rotate": 15.0, "…": "…"},
     "dynamic": {"rating": {"type": "int", "default": 0, "min": 0, "max": 5}}},
    {"id": "c2e4…", "name": "Note_01", "type": "annotation_text", "parent": "a81d…",
     "attrs": {"text": "Fix the lighting on this edge", "color": [255, 0, 0], "translate": [10.0, 50.0], "…": "…"}}
  ],
  "workspaces": {"version": 1, "current": "1: Board", "workspaces": [
    {"id": "1: Board", "title": "Board", "number": 1, "window_style": null,
     "root": {"tile": "canvas", "name": "Main", "title": "Main", "state": {"camera": {"…": "…"}}, "header": 1},
     "floating": []}
  ]},
  "assets": {
    "3f2a…": {"name": "concept.jpg", "mime": "image/jpeg", "size": 48213, "encoding": "base64", "data": "/9j/4AAQ…"}
  }
}
```

| Key | Meaning |
| --- | --- |
| `pixelref_version` | the version of pixelRef that saved the file. |
| `schema` | the file format version (1). A file with a newer schema is refused (*saved by a newer pixelRef*) rather than half-read. |
| `thumbnail` | a small JPEG of the board (longest side 320 px) written by the app on every save, and left out when the board is empty. It comes right after `schema`, so `pixelref.plugin_api.read_thumbnail(path)` reads it from the start of the file without parsing the images. The start panel shows it next to recent files; `pxcmd.file.thumbnail(path, save_to="thumb.png")` writes it out. Batch-mode saves don't render one. |
| `types` | the schema version of each node type used. When a type's plugin raises its `schema_version`, its `upgrade(attrs, from_version)` converts nodes saved with the earlier version on load. |
| `plugins` | scene-level data owned by plugins, keyed by plugin id (`canvas_background`…). |
| `nodes` | every node, parents before children. **Sibling order is draw order**, back first. `parent` is the parent's `id`. `attrs` holds every stored attribute (computed ones are not saved). `dynamic` holds the specifications of attributes added with `pxcmd.add_attr`. |
| `workspaces` | the scene's workspaces: `current` is the one shown, each entry has its title, number, window style, tiling `root` (nested `split` / `tabbed` / `stacked` / `tile` specs) and `floating` tiles (see [window-manager.md](window-manager.md#workspaces)). |
| `effect_cache` | processed effect results (lossless WebP, base64) saved by the effects plugin, so heavy effects don't have to be recomputed and still show without the plugin. Only written when the scene uses such effects; see [effects.md](effects.md#speed-and-caching). |
| `assets` | embedded files, keyed by the SHA-1 of their bytes, so identical images are stored once. Only assets that nodes still use are written. |

An image node shows its embedded `asset` when one is set, otherwise the file
at `path`. Linked paths are stored relative to the scene file when possible.

Other top-level keys are kept as they are, so tools can add their own
sections without pixelRef dropping them.

**Unknown node types.** Nodes whose type no active plugin provides keep their
attributes and type version and are written back unchanged. Disabling or
uninstalling a plugin never loses data.

**Atomic loading.** A file is parsed into a scratch scene first. If anything is
wrong, the open scene stays as it was and the error is reported (a
`SceneError`):

- a file that isn't JSON: *Not a pixelRef scene file*;
- a file with a `schema` newer than 1: *saved by a newer pixelRef*;
- a parent cycle, invalid attributes or a malformed `nodes` list.

## Undo snapshots

Undo stores the same JSON without `assets`, in compact form. Embedded image
data stays in memory, so undo steps stay small even on boards with large
images.
