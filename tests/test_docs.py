"""The documentation index (core.docs), its commands, the Help browser, context help and the MCP server."""

from __future__ import annotations

import json

from harness import APP, make
from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QCursor
from PySide6.QtTest import QTest

from pixelref.builtins.help.browser import DocsBrowser


def headless(**kwargs):
    from pixelref.core.session import Session

    return Session(**kwargs).start()


# ---- The index (headless) ---------------------------------------------------------------------


def test_index_covers_commands_nodes_attrs_plugins_guides_and_effects():
    session = headless()
    try:
        kinds = session.docs.kinds()
        for kind in ("command", "node", "attr", "plugin", "guide", "effect"):
            assert kinds.get(kind, 0) > 0, kind
        assert len(session.docs.entries()) == sum(kinds.values())
        assert session.docs.get("command:pen.combine").kind == "command"
        # Bare names: aliases, inherited attributes, titles.
        assert session.docs.get("group").id == "command:grouping.group"
        assert session.docs.get("pxcmd.group(").id == "command:grouping.group"
        assert session.docs.get("image.opacity").id == "attr:canvas_node.opacity"
        assert session.docs.get("node:IMAGE").id == "node:image"
        assert session.docs.get("effect:blur").usage.startswith('pxcmd.effects.add("blur"')
        assert session.docs.get("no such thing anywhere") is None
        # Guides are split into sections with GitHub anchors.
        links = session.docs.get("guide:user-guide#links")
        assert links is not None and links.title == "Links" and "guide:user-guide" in links.related
    finally:
        session.shutdown()


def test_search_ranks_names_first_and_filters():
    session = headless()
    try:
        docs = session.docs
        assert docs.search("pen combine")[0].id == "command:pen.combine"
        assert docs.search("opacity", "attr")[0].id == "attr:canvas_node.opacity"
        assert all(e.kind == "node" for e in docs.search("kind:node"))
        assert all(e.plugin == "pen" for e in docs.search("plugin:pen"))
        assert docs.search("zzqx nothing matches") == []
        # Every word has to match somewhere.
        assert all("blur" in (e.name + e.title + e.summary + e.body + e.keywords).lower()
                   for e in docs.search("blur radius"))
    finally:
        session.shutdown()


def test_index_follows_registries_and_providers():
    from pixelref.core.docs import DocEntry

    session = headless()
    try:
        docs = session.docs
        before = len(docs.entries())

        def flip_widgets(count: int = 2) -> int:
            """Flip some widgets.

            More about flipping."""
            return count

        handle = session.commands.register("test.flip_widgets", flip_widgets, undoable=False)
        entry = docs.get("test.flip_widgets")
        assert entry.summary == "Flip some widgets." and "More about flipping." in entry.body
        assert entry.usage == "pxcmd.test.flip_widgets(count: int = 2)"
        handle.dispose()
        assert docs.get("command:test.flip_widgets") is None
        contribution = session.extensions.contribute(
            "docs.providers", lambda: [DocEntry("recipe", "toast", "Toast", "Bread, but warm")], plugin=None)
        assert docs.get("recipe:toast").summary == "Bread, but warm"
        assert docs.search("warm bread")[0].id == "recipe:toast"
        contribution.dispose()
        assert len(docs.entries()) == before
    finally:
        session.shutdown()


def test_docs_commands_and_help():
    from pixelref.core.docs import command_signature
    from pixelref.core.errors import CommandError

    session = headless()
    try:
        results = session.run("docs.search", "save scene", limit=3)
        assert results[0]["id"] == "command:save_scene" and results[0]["usage"].startswith("pxcmd.save_scene(")
        page = session.run("docs.get", "set_attr")
        assert page["kind"] == "command" and page["data"]["undoable"] and "```python" in page["markdown"]
        try:
            session.run("docs.get", "pen combin")
            raise AssertionError("expected an error")
        except CommandError as exc:
            assert "command:pen.combine" in str(exc)  # suggestions
        assert session.run("docs.kinds")["command"] > 100
        exported = session.run("docs.export", kind="node")
        assert {e["kind"] for e in exported} == {"node"}
        assert "# Opacity" in session.run("help", "image.opacity")
        assert session.run("help", "set_attr").startswith("# Set attr")

        def sample(node, name: str = "Group", *values, flag: bool = False, **rest):  # noqa: ANN001, ANN002, ANN003
            pass

        assert command_signature(sample) == "(node, name: str = 'Group', *values, flag: bool = False, **rest)"
    finally:
        session.shutdown()


# ---- The UI: tools, menu items, keys, the Help browser ----------------------------------------------


def test_ui_adds_tools_actions_and_keys():
    h = make()
    try:
        docs = h.session.docs
        blur = docs.get("tool:blur")
        assert blur.keys == ["B"] and blur.usage == 'pxcmd.tools.set("blur")'
        assert "node:annotation_blur" in blur.related
        save = docs.get("action:file.save")
        assert save.keys == ["Ctrl+S"] and save.data["command"] == "file.save"
        assert "G" in docs.get("command:grouping.group").keys  # the command learns its menu item's keys
        assert "action:grouping.group" in docs.get("command:grouping.group").related
        assert docs.search("blur")[0].id == "tool:blur"
        # Changing a key updates the documentation.
        h.app.keymap.assign("grouping.group", ["Ctrl+Alt+9"])
        APP.processEvents()
        assert docs.get("action:grouping.group").keys == ["Ctrl+Alt+9"]
    finally:
        h.close()


def test_help_browser_search_open_links_and_run():
    h = make()
    try:
        shown = h.run("help.docs", "pen.combine")
        APP.processEvents()
        assert shown == "command:pen.combine"
        popup = h.app.panels.popups["docs"]
        assert popup.isVisible()
        browser = popup.findChild(DocsBrowser)
        assert browser.current == "command:pen.combine" and "Combine" in browser.view.toPlainText()
        browser.search.setText("rect")
        APP.processEvents()
        assert browser.current == "tool:rect"  # the best match shows at once
        browser._on_link(QUrl("doc:node:annotation_rect"))
        assert browser.current == "node:annotation_rect"
        browser.back()
        assert browser.current == "tool:rect"
        browser.run_current()  # runs the tool (and closes the pop-up)
        APP.processEvents()
        assert h.app.tools.active_id == "rect" and not popup.isVisible()
        # A guide's link to another guide opens in place.
        h.run("help.docs", "guide:reference")
        browser._on_link(QUrl("user-guide.md#links"))
        assert browser.current == "guide:user-guide#links"
    finally:
        h.close()


def test_context_help_and_command_search_f1():
    from PySide6.QtWidgets import QWidget

    h = make()
    try:
        # Widgets name their documentation: tool buttons and Attribute Editor rows.
        docs = {w.property("doc") for w in h.window.findChildren(QWidget) if w.property("doc")}
        assert "tool:pen" in docs
        h.add_image("Photo", 0, 0, w=100, h=80)
        h.select(["Photo"])
        APP.processEvents()
        rows = {w.property("doc") for w in h.inspector.findChildren(QWidget) if w.property("doc")}
        assert {"canvas_node.opacity", "canvas_node.rotate", "action:transform.flip_h"} <= rows
        assert all(h.session.docs.get(doc) is not None for doc in rows)
        # With no such widget under the cursor: the selection's node type.
        QCursor.setPos(-10000, -10000)
        assert h.run("help.docs") == "node:image"
        # F1 in the command search opens the highlighted entry.
        menu = h.app.menus.build("canvas", h.view)
        menu.field.setText("keyboard shortcuts")
        APP.processEvents()
        QTest.keyClick(menu.field, Qt.Key_F1)
        APP.processEvents()
        browser = h.app.panels.popups["docs"].findChild(DocsBrowser)
        assert browser.current == "action:help.shortcuts"
    finally:
        h.close()


# ---- The MCP server ------------------------------------------------------------------------------


def test_mcp_server_protocol():
    from pixelref.mcp_server import Server

    h = make()
    try:
        server = Server()
        server._app = h.app  # the index of this app (the real server builds its own offscreen)

        def call(method, params=None, rid=1):  # noqa: ANN001, ANN202
            return server.handle({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})

        init = call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                   "clientInfo": {"name": "test", "version": "0"}})["result"]
        assert init["protocolVersion"] == "2025-03-26" and init["capabilities"]["tools"]
        assert "pxcmd" in init["instructions"]
        assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
        assert call("ping")["result"] == {}
        names = [t["name"] for t in call("tools/list")["result"]["tools"]]
        assert names == ["search_docs", "get_doc", "list_doc_kinds"]
        found = call("tools/call", {"name": "search_docs", "arguments": {"query": "blur", "limit": 3}})["result"]
        assert not found["isError"] and json.loads(found["content"][0]["text"])[0]["id"] == "tool:blur"
        assert found["structuredContent"]["results"][0]["keys"] == ["B"]
        page = call("tools/call", {"name": "get_doc", "arguments": {"name": "pen.combine"}})["result"]
        assert page["content"][0]["text"].startswith("# Combine")
        missing = call("tools/call", {"name": "get_doc", "arguments": {"name": "pen combin"}})["result"]
        assert missing["isError"] and "command:pen.combine" in missing["content"][0]["text"]
        assert call("tools/call", {"name": "nope"})["error"]["code"] == -32602
        assert call("resources/list")["error"]["code"] == -32601
    finally:
        h.close()


def test_mcp_server_is_off_until_its_plugin_is_switched_on():
    import contextlib
    import io

    from pixelref.core.settings import MemorySettings
    from pixelref.mcp_server import main, mcp_enabled

    settings = MemorySettings()
    assert not mcp_enabled(settings), "off by default"
    settings.setValue("plugins/enabled", ["mcp"])
    assert mcp_enabled(settings)
    settings.setValue("plugins/disabled", ["mcp"])
    assert not mcp_enabled(settings)
    h = make()
    try:
        record = h.session.plugins.get("mcp")
        assert record is not None and not record.active and not h.session.commands.exists("mcp.setup")
        # The command line reads the user's settings: switched off, it explains and exits without serving.
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert main([]) == 1
        assert "Window > Plugins" in err.getvalue() and "AI assistant connection (MCP)" in err.getvalue()
        h.session.plugins.set_enabled("mcp", True)
        assert mcp_enabled(), "the switch in the Plugins window is the switch for the server"
        setup = h.session.run("mcp.setup")
        assert setup["claude_code"].startswith("claude mcp add pixelref -- ") and setup["command"][-1] == "mcp"
        assert setup["json"]["mcpServers"]["pixelref"]["args"][-1] == "mcp"
        assert h.app.actions.get("mcp.copy_setup") is not None
        h.session.plugins.set_enabled("mcp", False)
        assert not mcp_enabled()
    finally:
        h.session.settings.remove("plugins/enabled")
        h.session.settings.remove("plugins/disabled")
        h.close()
