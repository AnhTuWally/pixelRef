# Help and documentation

pixelRef documents itself. Every tool, menu item, command, node type,
attribute, effect, plugin and guide section is an entry in one index, built
from what is actually loaded, so it always matches your copy of pixelRef, including
third-party plugins and your own keys. You can reach the index in four ways:

| Who | Where | How |
| --- | --- | --- |
| you, in the app | the Help browser | `Shift+F1`, or *Help: Search Documentation…* in the right-click menu |
| you, in a script or the Script Editor | `pxcmd.docs.*` and `pxcmd.help` | `pxcmd.docs.search("blur")` |
| you or a program, in a terminal | `python -m pixelref docs` | `python -m pixelref docs show pen.combine` |
| an AI agent | the MCP server | `python -m pixelref mcp` |

## The Help browser

Press `Shift+F1`. The browser pops up next to the cursor. Type to search:
every word must match, and the best match is shown straight away. Up and
Down move through the results. The chips under the search field limit the
results to tools, menu items, commands, nodes (and their attributes) or guides.

- **Enter** runs the highlighted tool, menu item or argument-free command,
  and **Run** does the same. **Ctrl+Enter** or **Copy code** copies its
  Python, for example `pxcmd.tools.set("blur")`.
- Links between entries open in place. **Alt+Left** or the back arrow
  goes back.
- Each result shows its kind and its keys. A command lists the keys and
  menu items that run it.
- The browser is a panel like the Outliner. Its pin turns it into a
  floating card, and *Window › Panels* can dock it in the sidebar.

**Context help.** Point at something and press `Shift+F1` to open its
entry. This works on a tool button in the Tools panel, on a row of the
Attribute Editor (*Opacity*, *Rotation*, a text note's *Font size*…) or on
the transform buttons. Over the canvas it opens the selected item's node type,
or the active tool. In the command search (`Ctrl+K`), **F1** opens the
highlighted entry.

## Kinds of entries

Each entry has an id, `<kind>:<name>`:

| Kind | Example id | What it holds |
| --- | --- | --- |
| `tool` | `tool:blur` | what the tool does, its key, its options |
| `action` | `action:file.save` | a menu item or shortcut: its keys, menus and the command it runs |
| `command` | `command:pen.combine` | the Python signature, docstring, alias, whether it can be undone |
| `node` | `node:annotation_rect` | a node type: what it inherits and a table of its attributes |
| `attr` | `attr:canvas_node.opacity` | an attribute: type, default, range, choices, and how to set it |
| `effect` | `effect:blur` | an effect and its settings |
| `plugin` | `plugin:links` | a plugin: description, status, dependencies, commands |
| `guide` | `guide:user-guide#links` | a section of these guides |

Names work without the kind too. `pen.combine`, `group` (an alias),
`image.opacity` (inherited from `canvas_node`) and `Opacity` are all found.

## From Python

```python
pxcmd.docs.search("curved link")            # [{id, kind, name, title, summary, keys?, usage?}, …]
pxcmd.docs.search("", kind="tool")          # every tool
pxcmd.docs.search("opacity", kind=["attr"], limit=5)
pxcmd.docs.search("kind:command plugin:pen")
pxcmd.docs.get("pen.combine")               # everything, with "markdown"
pxcmd.docs.kinds()                          # {"tool": 13, "action": 224, …}
pxcmd.docs.export("docs.json")              # every entry (format="markdown" for one text)
print(pxcmd.help("image.opacity"))          # one entry as text
```

## From a terminal

```bash
python -m pixelref docs search blur faces          # id, title, summary and keys, best first
python -m pixelref docs search --kind attr opacity --json
python -m pixelref docs show tool:pen              # Markdown (or --json)
python -m pixelref docs kinds
python -m pixelref docs export -o pixelref-docs.json            # or --format markdown
```

The standalone build runs the same commands as `pixelref-cli docs …`. The
terminal tools start pixelRef offscreen with your plugins and `keymap.toml`,
which takes about a second. `--no-user` leaves those out. Nothing is written
to your settings.

## For AI agents (MCP)

**It is off by default.** Switch on *AI assistant connection (MCP)* in
*Window → Plugins…* first (or `pxcmd.load_plugin("mcp")`); until then the
server says how to do that and exits. Once it's on, *Copy AI Assistant (MCP)
Setup* (Ctrl+K) copies the Claude Code line for this computer, and
`pxcmd.mcp.setup()` also gives the JSON other apps use.

`python -m pixelref mcp` is a [Model Context Protocol](https://modelcontextprotocol.io)
server over stdio. Point an AI client at it and the agent can look things up
while it talks with you, for example "how do I blur a face?" or "write a
script that tiles every image tagged *lighting*". It needs nothing beyond
pixelRef itself.

```bash
claude mcp add pixelref -- python -m pixelref mcp                    # Claude Code, from a source checkout
claude mcp add pixelref -- "C:/Apps/pixelRef/pixelref-cli.exe" mcp   # the standalone build
```

Other clients take the same command in their JSON configuration:

```json
{"mcpServers": {"pixelref": {"command": "python", "args": ["-m", "pixelref", "mcp"],
                             "cwd": "D:/tools/pixelRef"}}}
```

It offers three read-only tools:

- **`search_docs`** `(query, kind?, limit?)` returns the best matches with
  their ids, summaries, keys and Python usage.
- **`get_doc`** `(name)` returns one entry as Markdown.
- **`list_doc_kinds`** `()` returns the kinds and their counts.

The server also tells the agent how to answer: a key or menu item first,
Python when you want automation, and never a guessed command name. To give
an agent everything at once instead, for example as a file in its context,
use `docs export`.

## Documenting your own plugin

A plugin is documented as soon as it's loaded:

- **Commands:** the first line of a command's docstring is its summary, and
  the rest is its details.
- **Node types and attributes:** `NodeType(doc=…)` and `Attr(doc=…)`.
  These also become the Attribute Editor's tooltips.
- **Tools:** `tooltip`, `hint`, `keywords` and the options' `doc`.
- **Menu items:** `ui.action(…, tip=…, keywords=…)`.
- **Guides:** every `*.md` file in the plugin's folder is split into
  sections (`guide:<plugin>/<file>#<section>`).
- **Anything else:** contribute a provider to `docs.providers`, a callable
  returning `DocEntry` objects. The effects plugin documents its effect
  types this way:

```python
from pixelref.plugin_api import DocEntry

def activate(ctx):
    def document():
        return [DocEntry(kind="recipe", name="contact_sheet", title="Contact sheet",
                         summary="Lay out every image in a grid", usage='pxcmd.arrange("grid")',
                         related=["command:arrange.arrange"])]
    ctx.contribute("docs.providers", document)
```

For context help on a widget of your own, set its `doc` property to an id
or a name: `button.setProperty("doc", "command:my_plugin.do_thing")`.
