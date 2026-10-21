"""``python -m pixelref mcp``: pixelRef's documentation as an MCP server, so an AI agent can look
up any tool, command, attribute or guide while it talks with you.

It speaks the Model Context Protocol over stdio (one JSON-RPC message per line), with no
dependencies beyond pixelRef itself. Add it to an MCP client, for example Claude Code::

    claude mcp add pixelref -- python -m pixelref mcp          # from a source checkout
    claude mcp add pixelref -- "C:/…/pixelRef/pixelref-cli.exe" mcp   # the standalone build

or in a client's JSON configuration::

    {"mcpServers": {"pixelref": {"command": "python", "args": ["-m", "pixelref", "mcp"]}}}

Tools: ``search_docs``, ``get_doc`` and ``list_doc_kinds``. The index is the same as the
Help browser's (see ``core.docs``) and is built on the first call. ``--no-user`` leaves out
your own plugins and keymap.toml.

**Off by default.** The server only starts while the built-in "AI assistant connection (MCP)"
plugin is switched on in Window → Plugins… (``pxcmd.load_plugin("mcp")``); otherwise it says
how to switch it on and exits. ``pxcmd.mcp.setup()`` then gives the lines above for this computer.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from . import __version__

PLUGIN_ID = "mcp"
SWITCHED_OFF = (
    "pixelRef's MCP server is switched off.\n"
    "To let AI assistants read pixelRef's documentation, open pixelRef, choose Window > Plugins..., "
    "switch on \"AI assistant connection (MCP)\" (or run pxcmd.load_plugin(\"mcp\") in the Script Editor), "
    "then restart your AI app."
)

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = """\
pixelRef is a desktop reference board (like PureRef) for images, GIFs, movies and annotations,
built like a DCC application: every user action is a Python command (pxcmd.<name>), every
element is a node with attributes, and every feature is a plugin.

Use search_docs to find what the user is asking about (a tool, menu item, command, node type,
attribute or guide section), then get_doc for the details. When you answer a user:
- give the quickest way first (a key or a menu item; entries list their keys and menus),
- give Python (the entry's `usage`) when they want to script or automate it,
- never guess a command's name or arguments: check them with get_doc."""

TOOLS = [
    {
        "name": "search_docs",
        "title": "Search pixelRef documentation",
        "description": (
            "Search pixelRef's documentation: tools (tool), menu items with their keyboard shortcuts (action), "
            "Python commands (command), node types (node), node attributes (attr), plugins (plugin), effects "
            "(effect) and sections of the user guides (guide). Every word of the query must match. An empty "
            "query with a kind lists that kind. Returns the best matches as JSON: id, kind, title, summary, "
            "keys and a Python usage snippet. Pass an id to get_doc for the full entry."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words to look for, e.g. 'blur faces' or 'pen combine'"},
                "kind": {"type": "array", "items": {"type": "string"},
                         "description": "Only these kinds: tool, action, command, node, attr, plugin, effect, guide"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 15},
            },
            "required": ["query"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_doc",
        "title": "Read one pixelRef documentation entry",
        "description": (
            "The full documentation of one thing, as Markdown: what it does, its keys and menus, its Python "
            "signature and usage, its attributes or options, and related entries. Accepts an id from "
            "search_docs (command:pen.combine, tool:blur, attr:canvas_node.opacity, guide:user-guide#links) "
            "or a plain name (pen.combine, group, image.opacity, blur)."),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "An entry id or a name"}},
            "required": ["name"],
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "list_doc_kinds",
        "title": "List the kinds of pixelRef documentation",
        "description": "The kinds of documentation entries and how many of each there are.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
]


class Server:
    def __init__(self, user: bool = True, plugin_paths: tuple[str, ...] = (), trusted_paths: tuple[str, ...] = ()
                 ) -> None:
        self.user = user
        self.plugin_paths = plugin_paths
        self.trusted_paths = trusted_paths
        self._app: Any = None

    @property
    def docs(self) -> Any:
        if self._app is None:
            from .docs_cli import build_app

            self._app = build_app(self.user, self.plugin_paths, self.trusted_paths)
        return self._app.session.docs

    def close(self) -> None:
        if self._app is not None:
            self._app.close(force=True)

    # ---- JSON-RPC ---------------------------------------------------------------------------

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        if method is None or "id" not in message:  # a notification (or a stray response): no reply
            return None
        try:
            result = self.dispatch(method, message.get("params") or {})
        except _RpcError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            requested = params.get("protocolVersion")
            return {
                "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "pixelref", "title": "pixelRef documentation", "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self.call_tool(params.get("name", ""), params.get("arguments") or {})
        raise _RpcError(-32601, f"Method not found: {method}")

    # ---- Tools ------------------------------------------------------------------------------

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "search_docs":
            kinds = arguments.get("kind") or None
            limit = max(1, min(int(arguments.get("limit") or 15), 200))
            results = [e.brief() for e in self.docs.search(str(arguments.get("query", "")), kinds, limit)]
            if not results:
                return _text("No matches. Try fewer or different words, or list_doc_kinds.")
            return _text(json.dumps(results, indent=1, ensure_ascii=False), {"results": results})
        if name == "get_doc":
            wanted = str(arguments.get("name", ""))
            entry = self.docs.get(wanted)
            if entry is None:
                hits = self.docs.search(wanted, limit=5)
                hint = f" Closest matches: {', '.join(e.id for e in hits)}." if hits else ""
                return _text(f"No documentation for '{wanted}'.{hint}", error=True)
            return _text(self.docs.markdown(entry))
        if name == "list_doc_kinds":
            counts = self.docs.kinds()
            return _text(json.dumps(counts), {"kinds": counts})
        raise _RpcError(-32602, f"Unknown tool: {name}")


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _text(text: str, structured: dict[str, Any] | None = None, error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": error}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def mcp_enabled(settings: Any = None) -> bool:
    """Is the "AI assistant connection (MCP)" plugin switched on? (``settings``: the user's, by default.)"""
    from .core.plugins import BUILTIN_DIR, PluginManifest, switched_on

    if settings is None:
        from PySide6.QtCore import QCoreApplication

        from .ui.settings import QtSettings

        QCoreApplication.setOrganizationName("pixelRef")  # the user's settings, as the app keeps them
        QCoreApplication.setApplicationName("pixelRef")
        settings = QtSettings()
    return switched_on(settings, PluginManifest.load(BUILTIN_DIR / PLUGIN_ID, builtin=True), "builtin")


def main(argv: list[str]) -> int:
    if not mcp_enabled():
        print(SWITCHED_OFF, file=sys.stderr)
        return 1
    plugin_paths: list[str] = []
    trusted_paths: list[str] = []
    args = iter(argv)
    for arg in args:
        if arg == "--plugin-path":
            plugin_paths.append(next(args, ""))
        elif arg == "--trust-plugin-path":
            trusted_paths.append(next(args, ""))
    server = Server(user="--no-user" not in argv, plugin_paths=tuple(p for p in plugin_paths if p),
                    trusted_paths=tuple(p for p in trusted_paths if p))
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    try:
        for line in stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                replies = [{"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}}]
            else:
                batch = message if isinstance(message, list) else [message]
                replies = [r for r in (server.handle(m) for m in batch if isinstance(m, dict)) if r is not None]
            for reply in replies:
                stdout.write(json.dumps(reply, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()
    finally:
        server.close()
    return 0
