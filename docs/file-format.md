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
| `links` | a fingerprint of every linked file, keyed by the stored path (see [Linked files](#linked-files)). |
| `package` | `{"id": "…"}` in a scene written by *Save Package*: the id of its files folder's manifest. |
| `assets` | embedded files, keyed by the SHA-1 of their bytes, so identical images are stored once. Only assets that nodes still use are written (every attribute of type `asset`: an image's `asset`, a 3D object's `texture_asset`). |

An image node shows its embedded `asset` when one is set, otherwise the file
at `path`. Embedded OpenEXR files have the MIME type `image/x-exr`.

## Linked files

Every attribute of type `path` (an image's or 3D object's `path`, a 3D
`texture_path`, a movie's `path`, a sequence's pattern) is stored in one of
three forms, chosen on every save:

- **relative** to the scene's folder when the file is inside it (`media/clip.mp4`);
- **`${NAME}/rest`** when it is under a *named root* (*File → Preferences →
  Path Roots…*; the longest match wins). When reading, `NAME` comes from the
  named roots, else from an environment variable, so pipeline variables such
  as `${PRISM_JOB}/assets/robot.gltf` work with no setup. A `${NAME}` written
  by hand or by a tool is kept as long as it resolves;
- **absolute** otherwise.

A board can define variables of its own in `plugins.path_search`:
`{"vars": {"STUDIO_DRIVE": "W:/", "LINUX_PATH": "/mnt/drive"}}`. They work like
named roots (a named root or environment variable of the same name wins), and
a path that is not found is retried under each of them: the part after
`${NAME}/`, the part of a full path below one of the folders, or a whole
relative path.

The `links` section records what each linked file *is*, so it can be found
again when it is not where the path says:

```json
"links": {
  "media/clip.mp4": {"size": 7340032, "qh": "9b1c…"},
  "${PRISM_JOB}/assets/robot/robot.gltf": {"size": 18213, "qh": "…"},
  "renders/shot.####.exr": {"frames": 96, "frame": 1001, "first": {"size": 8841220, "qh": "…"}}
}
```

`size` is the file size in bytes and `qh` the SHA-1 of its first and last
64 KB (quick even for a 4 GB movie). A sequence records its frame count, the
first frame number on disk and that frame's fingerprint. Records are
refreshed on every save; a record whose file is missing at save time is kept.

Opening a board never searches the disk. A stored path is resolved by a fixed
chain: (1) the path itself; (2) the scene's package folder (below), looked up
by fingerprint in its manifest; (3) this machine's memory of files found
earlier by *Find Missing Files* (fingerprint → path, in the user settings
`links/remembered`); (4) otherwise the node shows its "Missing" placeholder.
Steps 2 and 3 only accept a file with the recorded fingerprint, so a
different file with the same name is never used. A file still at its path is
used even if its content changed (the Attribute Editor's Storage row says
*(changed)*). A path found by step 2 or 3 is written into the scene on its
next save, never silently before.

### Packages

*Save Package* writes `<name>.pxref` and a `<name>.files/` folder beside it:

```
Board/
  Board.pxref            paths are plain relative: "Board.files/robot/robot.gltf"
  Board.files/
    manifest.json
    robot/robot.gltf  robot.bin  textures/robot_diffuse.png
    sh010/shot.0001.exr …
    Downloads/clip.mp4
```

Each source folder becomes one subfolder (a model's `.mtl`, `.bin` and
textures keep their place beside it). `manifest.json` lists every file:

```json
{"id": "7f3a9c…", "pixelref_version": "1.5.3",
 "files": [{"path": "robot/robot.gltf", "size": 18213, "qh": "…", "sha1": "…"}]}
```

`sha1` is the hash of the whole file (*Verify Package* checks it). The scene's
`package` section carries the same `id`. The files folder is found as
`<scene stem>.files` beside the scene, else any sibling folder whose manifest
has that id (so it can be renamed), else the folder this machine last saw with
that id (user setting `links/packages`). Because the paths inside are plain
relative ones, older pixelRef versions open a package too, as long as the
scene and its folder travel together.

A `sequence` node is always linked: `path` is the pattern of its frame files
with one `#` per digit of padding (`renders/shot.####.exr`; a single `#` for
unpadded numbers), `first` / `last` the frames played, plus `fps`, `loop`,
`autoplay` and `missing` (`"hold"` or `"black"`).

The exr plugin adds sparse attributes to image and sequence nodes, written
only once changed: `exr_layer` (`""` is the beauty; `"diffuse"`, or
`"part/layer"` in multi-part files), `exposure` (stops), `gamma`, `channel`
(`"rgb"`, `"r"`, `"g"`, `"b"`, `"a"`, `"luma"`) and `colorspace` (the OCIO
input colour space; `""` = the board default).

A board saved with its colour settings (*Save with this board*) has
`plugins.ocio`: `{"mode": "builtin" | "ocio", "config": "", "display": "",
"view": "", "look": "", "input": ""}`. `config` is a path, an `ocio://` URI
or `""` (`$OCIO`, else OCIO's built-in ACES config); empty names mean the
config's defaults. A config, display or view the opening machine doesn't have
falls back (your config, then the built-in one) with a warning: the board
still opens.

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

## Embedded in PNG exports

An exported PNG can hold a `.pxref` document (schema 1, with its `assets`) of the
exported nodes in a private ancillary chunk `pxRF`: the 4 bytes `PXR1`, then the
zlib-compressed JSON text. Roots are in world space and linked images are embedded.
Readers cap the inflated size (768 MB), nodes (50,000), nesting (64) and images.
See [architecture.md](architecture.md#files).

## Undo snapshots

Undo stores the same JSON without `assets`, in compact form. Embedded image
data stays in memory, so undo steps stay small even on boards with large
images.
