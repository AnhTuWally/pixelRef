"""What the UI adds to the documentation index (``core.docs``): every tool, every
menu item with its keys and where it is in the menus, and on each command the
keys and menu items that run it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

from ..core.commands import format_call, format_value
from ..core.docs import DocEntry
from ..core.extensions import Disposable

if TYPE_CHECKING:
    from .app import App

MENU_TITLES = {"canvas": "Right-click menu"}


def _clean(text: str) -> str:
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&").rstrip(".…").strip()


def attach(app: "App") -> Disposable:
    """Register the UI's documentation with ``app.session.docs``."""
    docs = app.session.docs

    def keys(target: str) -> list[str]:
        return app.keymap.shortcuts(target)

    def menu_paths(key: str) -> list[str]:
        paths = []
        for item in app.menus.items:
            if item.action == key:
                paths.append(" › ".join([MENU_TITLES.get(item.menu, item.menu.capitalize()), *item.path]))
        return paths

    def command_id(name: str) -> str | None:
        command = app.session.commands.get(name)
        return f"command:{command.name}" if command else None

    def tools() -> Iterable[DocEntry]:
        for tool in app.tools.ordered():
            facts = []
            if tool.hint:
                facts.append(f"While it's active: {tool.hint}")
            if tool.creates:
                facts.append(f"Creates [{tool.creates}](doc:node:{tool.creates}) nodes.")
            if tool.kind == "sticky":
                facts.append("Stays active until you press Esc or V.")
            options = [o for o in tool.option_specs if not o.hidden]
            if options:
                facts += ["", f"## {tool.options_title or 'Options'}", "",
                          "| Option | Type | Default | Description |", "| --- | --- | --- | --- |"]
                facts += [f"| {o.label or o.name} | {o.type} | "
                          f"{'' if o.default is None else '`' + format_value(o.default) + '`'} | "
                          f"{' '.join((o.doc or '').split())} |" for o in options]
            plugin = getattr(tool.ui, "plugin_id", None)
            related = [r for r in ("command:tools.set", f"node:{tool.creates}" if tool.creates else "",
                                   f"plugin:{plugin}" if plugin else "") if r]
            yield DocEntry(
                kind="tool", name=tool.id, title=_clean(tool.title or tool.id),
                summary=tool.tooltip or tool.hint or f"The {tool.title or tool.id} tool",
                body="\n".join(facts), plugin=plugin, keywords=" ".join(filter(None, [tool.keywords,
                                                                                    tool.action_text])),
                keys=keys(f"tool.{tool.id}"), usage=f'pxcmd.tools.set("{tool.id}")', related=related,
                data={"creates": tool.creates, "hidden": tool.hidden,
                      "options": {o.name: o.default for o in options}})

    def actions() -> Iterable[DocEntry]:
        for key, spec in app.actions.specs.items():
            if spec.category == "hidden" or key.startswith("tool."):
                continue
            action = app.actions.actions.get(key)
            title = _clean(action.text() if action is not None else spec.text) or key
            target = command_id(spec.command)
            paths = menu_paths(key)
            facts = [f"- **Runs:** `{format_call(spec.command, spec.args, spec.kwargs)}`"]
            if paths:
                facts.append("- **Menu:** " + "; ".join(paths))
            if spec.category:
                facts.append(f"- **Shortcut category:** {spec.category}")
            yield DocEntry(
                kind="action", name=key, title=title, summary=spec.tip, body="\n".join(facts),
                plugin=spec.plugin, keywords=" ".join(filter(None, [spec.keywords, spec.category, *paths])),
                keys=keys(key), usage=format_call(spec.command, spec.args, spec.kwargs),
                related=[target] if target else [],
                data={"command": spec.command, "args": list(spec.args), "menus": paths, "category": spec.category})

    def annotate(entries: dict[str, DocEntry]) -> None:
        """Commands learn their keys and menu items; menu items without a tip borrow the command's summary."""
        for entry in entries.values():
            if entry.kind != "action":
                continue
            target = entries.get(entry.related[0]) if entry.related else None
            if target is None:
                continue
            if not entry.summary:
                entry.summary = target.summary
            target.related.append(entry.id)
            if not app.actions.specs[entry.name].args:
                target.keys += [k for k in entry.keys if k not in target.keys]
        for binding in app.keymap.live_bindings():
            target = entries.get(command_id(binding.target) or "")
            if target is not None:
                key = keys(binding.target)
                target.keys += [k for k in key if k not in target.keys]

    return Disposable.combine([
        docs.add_provider(tools),
        docs.add_provider(actions),
        docs.add_annotator(annotate),
        app.actions.watch(docs.invalidate),
        app.keymap.watch(docs.invalidate),
        app.tools.watch_registry(docs.invalidate),
    ])
