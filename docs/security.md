# Staying safe with plugins and scripts

**The short version:** plugins and scripts run with the same access as you.
They can read and change your files, use the network and start other
programs. Only install them from people you trust.

pixelRef helps: it won't run a new or changed plugin until you approve it,
it checks a plugin's files every time it starts, and it reports what
third-party plugins do. But once you approve a plugin, nothing sandboxes it.
This page explains what to look at before you trust something, what pixelRef
protects you from, and what it can't.

## What counts as code

Everything below runs Python inside pixelRef, with your permissions:

| What | Where it lives | What protects you |
|---|---|---|
| Third-party plugins | the user plugin folder, folders you add in *Window → Plugins… → Sources*, `PIXELREF_PLUGIN_PATH`, `--plugin-path` | approval, signatures, file checks, the activity monitor |
| Python packages | pixelRef's package folder (*Plugins → Python packages*) | only what you or an approved plugin asks for gets installed |
| Scripts | the scripts folder (*Scripts → Open Scripts Folder*) and `PIXELREF_SCRIPT_PATH` | nothing: a script runs when you run it |
| `userSetup.py` | the scripts folder and `PIXELREF_SCRIPT_PATH` | nothing: it runs at every start |
| `keymap.toml` entries with `python =` or `script =` | pixelRef's config folder | nothing: they run when you press the key |
| Code pasted into the Script Editor | — | nothing: you run it |

The **MCP server** (`python -m pixelref mcp`, for AI assistants) is off by
default: it refuses to start until you switch on the built-in plugin *AI
assistant connection (MCP)* in *Window → Plugins…*. Even then it runs only
when an AI app you configured starts it, and it can only read: pixelRef's
documentation, and the `.pxref` files the agent names (as JSON, without
loading plugins or running anything in them). It can't change or save
boards, or run commands. Switching the plugin off stops new servers from starting; one an
AI app already started keeps running until that app closes it.

Boards (`.pxref` files) aren't code: opening one never runs anything and
never switches a plugin on. A board that uses a plugin you don't have shows
its nodes as placeholders. Don't install a plugin just because a board asks
for it.

## Before you trust a plugin

*Window → Plugins…* lists every plugin; select one to see its page.

- **Who made it?** A plugin's page shows the author it names, but anyone can
  write any name in a manifest. A **✔ Verified** plugin is signed by a
  publisher on your trusted list, and its files match the signature: that
  is the only real proof of where it comes from.
- **Where did you get it?** Prefer your studio's plugin folder or the
  author's own site over a file someone sent you.
- **What does it say it does?** The *Permissions it declares* section lists
  what it asks for. A colour-palette plugin that wants the network, or to
  start programs, deserves a question.
- **Has it changed?** **⚠ Changed** means its files changed since you
  approved it. If you didn't just update it, don't approve it again.
- **Is the signature invalid?** **✖ Invalid signature** means the files don't
  match what the publisher signed: it may have been tampered with. pixelRef
  won't approve it unless you tick *I understand…*. Don't, unless you know why.

When you switch on a plugin that hasn't been approved, the approval dialog
shows all of this again. *Cancel* is the default; nothing runs unless you
choose *Approve and switch on*.

## What pixelRef protects you from

- **Nothing runs until you approve it.** A plugin's code isn't even imported
  before you approve it (or it's signed by a trusted publisher, or it's in a
  folder you told pixelRef to trust).
- **Changed files are caught.** An approval is for exactly those files.
  pixelRef checks them at every start, and again right before running the
  code: an edited plugin needs approving again.
- **Signatures are checked.** A plugin whose files don't match its signature
  is blocked.
- **Built-in plugins can't be impersonated.** A third-party plugin can't take
  a built-in plugin's name (unless it says it's a replacement and you pick
  it), and built-in plugins load first, so a plugin can't knock one out by
  taking its commands. Foundation plugins can't be replaced at all.
- **Plugins can't take each other's names.** A plugin that registers a command
  or service another plugin owns fails to load, unless its manifest declares
  the override (shown on its page).
- **Bundled packages stay with their plugin.** A plugin's own `wheels/` folder
  is only used to install the packages *that* plugin needs, and only once
  it's approved.
- **pixelRef's own packages can't be replaced** from the package manager.

- **Embedded scenes in PNGs are untrusted data.** A dropped PNG's embedded scene
  is size-limited while decompressing, parsed into a scratch scene, stripped of
  unknown node types, linked file paths and scene-level data, and added only if
  all of that succeeds. Nothing in it is executed (click actions only run
  commands that plugins have declared safe for them, as in any scene file).

## What it can't protect you from

- **An approved plugin isn't sandboxed.** It can do anything you can. Approve
  only what you trust.
- **The activity monitor is a warning light, not a lock.** It notices files
  written or deleted, network use, programs started, native code and code run
  from text, and lists anything the plugin didn't declare on its page
  (*Activity*). It never blocks anything, and code that goes straight to the
  operating system or deliberately hides can get past it. "Nothing noticed"
  doesn't mean "safe".
- **Scripts, `userSetup.py`, keymap Python and the Script Editor** aren't
  covered by plugin approval. Run only scripts you wrote or trust.
- **A folder set to "Trust everything"** runs whatever is put in it, by anyone
  who can write there.
- **The pixelRef program itself.** If someone can change pixelRef's own files,
  no check inside pixelRef helps. Install pixelRef from its official release
  and keep its folder writable only by administrators.

## If something looks wrong

1. Switch the plugin off (untick it in *Window → Plugins…*) and choose
   **Revoke approval** on its page.
2. Look at its *Activity* section. The violations are also written to
   `plugin-activity.jsonl` in pixelRef's data folder.
3. Remove its folder, or remove the source folder in the *Sources* tab.
4. Tell whoever gave it to you, or your studio's admin.

If you think a plugin stole something (passwords, keys, access tokens),
change those passwords and revoke those tokens.

## Plugin folders and their policy

*Window → Plugins… → Sources* lists every folder pixelRef looks in, in order:
built-in, your user plugin folder, folders you added (in the order you set),
`PIXELREF_PLUGIN_PATH`, then the command line. When two folders hold a
plugin with the same id, the earlier one is used; the plugin's page lists the
other copies and lets you pick one.

Each folder you add has a policy:

- **Ask before loading** (default): every new or changed plugin needs your approval.
- **Only trusted publishers**: only plugins signed by a publisher on your list
  can run; nothing else can even be approved.
- **Trust everything (developer)**: everything runs without asking. pixelRef
  makes you type the folder's name to choose it, and the Plugins window warns
  while one exists. Use it only for your own plugin development folder.

pixelRef warns about folders on a network share and, on macOS and Linux,
folders other users can write to.

`--trust-plugin-path DIR` on the command line trusts a folder for that run
only (batch scripts, CI, plugin development); `--plugin-path DIR` adds it with
the usual approval.

## For studios

- **Sign your plugins.** Make one key for the studio
  (`python -m pixelref plugin keygen studio.pem --name "Example Studio"`),
  keep the `.pem` file secret, and sign each release
  (`python -m pixelref plugin sign path/to/plugin --key studio.pem`).
- **Give artists the public key.** They add `studio.pub.toml` once under
  *Sources → Trusted publishers*; signed plugins then load without asking.
- **Use "Only trusted publishers"** for the shared plugin folder, so an
  unsigned file dropped there never runs.
- **Keep shared plugin folders read-only** for artists, and don't point
  `PIXELREF_PLUGIN_PATH` at folders everyone can write to.
- **Keep the monitor on** and look at the *Needs attention* filter now and then.

See [Writing plugins](plugins.md) for the manifest fields, permissions and
signing in detail.
