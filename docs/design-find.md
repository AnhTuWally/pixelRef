# Design notes: element metadata and Find

Status: **implemented (2026-10-31).** Tags get a proper editor, and a Find panel searches the
board by name, tag, type, note, metadata and text. This page records what was reused, what
was added, and why.

## What metadata a node has

| Attribute | Type | Owner | Stored |
| --- | --- | --- | --- |
| `tags` | `string_list` | the kernel (`node` base type) | always (`[]` when empty) |
| `note` | `text` | the `inspector` plugin, an extension of `node` | only once set |
| `metadata` | `any` (a dict) | the `inspector` plugin, an extension of `node` | only once set |

Nothing new was added to the node model. The brief asked for a shared `description`
attribute, but `note` already is one: free text on every node, saved, undoable, carried
through import and editable PNGs, with its own Attribute Editor section. A second free-text
field would split descriptions between two places, so **the note is the description**.

## Decisions

| Topic | Decision | Why |
| --- | --- | --- |
| Bulk tag editing | Kernel commands `add_tags`, `remove_tags`, `set_tags`, `list_tags` (`core/kernel_commands.py`) | `tags` is the one attribute the kernel puts on every node and `ls(tags=)` is a kernel command; scripts, batch mode and every plugin can use them whatever plugins are loaded. `set_attr` replaces a whole list, which loses tags on a multi-selection. |
| Tag normalisation | Trimmed, empty ones dropped, kept once each in first-seen order, case kept (`clean_tags`, in the plugin API). Only the new commands normalise; `set_attr` is unchanged | Existing scripts behave as before. Case-sensitive storage matches `ls(tags=)`; search ignores case. |
| Only changed nodes are written | The commands skip nodes whose tags wouldn't change | Smaller undo snapshots and canvas refreshes on big selections. |
| Tag UI | A *Tags* section (`inspector/tags.py`, `generic=True`) for one item or many; partial tags show a count and a **+** | Reuses the `inspector.sections` extension point. The inspector is a foundation plugin, so tags are always editable. |
| Quick editor | Its `tags` field on a multi-selection applies the difference to the first item's tags (one undo step) | It used to write the first item's list onto every item. |
| Search location | A built-in plugin `find`: `search.py` (no Qt) + `ui.py` (panel) | Same headless/Qt split as every plugin; disabling it removes Find and leaves the data alone. |
| What is searched | Name, type (name + label; base types only with `type:`), tags, note, metadata keys and values, and each `string` / `text` / `path` / `asset` attribute of the node's schema (unknown types: every text value) | Third-party node types become searchable with no new API. Paths contribute their file name only: folder names would match everything. Base types in plain words would make "node" match every node. |
| The index | `SearchIndex`: one folded entry per node, rebuilt when the node's `SceneGraph.node_revisions` entry or `Node` object changes; a full rebuild when node types change | Derived, never saved, always rebuildable. Undo and file loads swap in new `Node` objects, so they rebuild everything, which is correct and was measured to be fast enough. |
| Query syntax | Terms AND together; `#tag`, `#a,b`, `type:`, `name:`, `note:`, `key:value`, `-term`, `"phrase"`; wildcards in tags and types | Covers the common cases with no query language to learn; there is no `OR` except between tags, which is what *Find Similar* needs. |
| Ranking | Sum over terms of the best field: exact name 300, name prefix 150, name 100, whole tag 120, tag 80, type 40, metadata 30, note / text 20; then scene order | Predictable; a typed name wins. |
| Going to a result | `find.locate` runs `links.go_to` when the links plugin is on (glide, select, `Alt+Left` history), else `navigation.frame` + select | Reuses the existing navigation instead of a third camera path; `links` is optional. |
| Panel placement | `default_mode="hidden"` with `Ctrl+F` popping it up | The default sidebar layout is unchanged. |
| Outliner | Unchanged (name filter only) | A requirement of this change. Its filter could later ask the `find` service when it is present. |

## Performance

`examples/scenes/huge_scene.pxref`, 5,376 image nodes, headless, on the development machine:

| Operation | Time |
| --- | --- |
| First search (builds every entry) | 86 ms |
| Search, one word, warm | 16 ms |
| Search, three terms matching every node | 61 ms |
| Search after editing one node (one entry rebuilt) | 9 ms |
| Search after undo (all entries rebuilt) | 93–110 ms |
| `add_tags` on all 5,376 nodes (one undo step) | 88–96 ms |

The Find panel on the same board (search plus filling the list, offscreen Qt):

| Operation | Time |
| --- | --- |
| Every node matches (5,376 found, 200 listed), first / warm | 80 / 63 ms |
| One match | 24 ms |
| Empty field (counting the board's tags) | 6 ms |
| After editing one node | 30 ms |

The panel waits 120 ms after the last key before searching and lists at most 200 results.
Two optimisations were made after measuring: folded text is stored in each entry instead of
being recomputed per search, and snippets are cut only for the results returned.

## Limitations

- Tags are case-sensitive when stored (*Sky* and *sky* are two tags); search ignores case.
- A tag containing a comma can't be searched with `#` (the comma separates alternatives);
  a plain word still finds it.
- No `OR` between different kinds of terms; no regular expressions.
- `type:` wants a whole type name, a label, or wildcards (`type:rect` finds nothing).
- Text inside a prefix-less word with a colon (`http://…`) is read as a metadata key; quote it.
- The search is a scan over cached entries, linear in the number of nodes; an inverted index
  would only pay off on boards far larger than the ones measured.
