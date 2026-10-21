"""AI assistant connection (MCP): the switch for ``python -m pixelref mcp``.

The MCP server (:mod:`pixelref.mcp_server`) is a separate process an AI app starts; it lets
the assistant search and read pixelRef's documentation and read ``.pxref`` files, nothing else
(it never writes or runs anything). This plugin is **off by default**, and while it is off
the server refuses to start
(:func:`pixelref.mcp_server.mcp_enabled`). Switching it on in *Window → Plugins…* allows it,
and adds ways to set an AI app up:

* ``pxcmd.mcp.setup()``: the Claude Code command line and the JSON other MCP apps use;
* *Copy AI Assistant (MCP) Setup* (Ctrl+K): copies the Claude Code command line.

Switching it off again stops new servers from starting; one an AI app already started keeps
running until that app closes it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pixelref.core.packages import is_frozen


def server_command() -> list[str]:
    """How an AI app starts the server on this computer."""
    if is_frozen():
        cli = Path(sys.executable).with_name("pixelref-cli" + (".exe" if sys.platform == "win32" else ""))
        return [str(cli), "mcp"]
    return [sys.executable, "-m", "pixelref", "mcp"]


def _quote(part: str) -> str:
    return f'"{part}"' if any(c in part for c in ' \t"') else part


def setup_info() -> dict[str, Any]:
    command = server_command()
    return {
        "command": command,
        "claude_code": "claude mcp add pixelref -- " + " ".join(_quote(p) for p in command),
        "json": {"mcpServers": {"pixelref": {"command": command[0], "args": command[1:]}}},
    }


def activate(ctx: Any) -> None:
    def setup() -> dict[str, Any]:
        """How to add pixelRef to an AI app: the Claude Code command line and the JSON other MCP apps use."""
        return setup_info()

    ctx.command("setup", setup, undoable=False, echo=False)


def activate_ui(ui: Any) -> None:
    from PySide6.QtWidgets import QApplication

    def copy_setup() -> str:
        """Copy the command that adds pixelRef to Claude Code (other AI apps: pxcmd.mcp.setup())."""
        text = setup_info()["claude_code"]
        QApplication.clipboard().setText(text)
        ui.toast("Copied. Run it in a terminal to connect Claude Code to pixelRef's documentation.")
        return text

    ui.command("copy_setup", copy_setup, undoable=False, echo=False)
    ui.action("mcp.copy_setup", "mcp.copy_setup", "Copy AI Assistant (MCP) Setup",
              keywords="mcp ai assistant claude agent model context protocol documentation connect",
              category="Window")
