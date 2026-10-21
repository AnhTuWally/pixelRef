"""The documentation index: one searchable place for everything pixelRef can tell
about itself, for people (the Help browser) and programs (``pxcmd.docs.*``, the
``python -m pixelref docs`` command line and the MCP server for AI agents).

Entries are built from what is actually registered, so they are always current
and include third-party plugins:

* ``command:<name>``   every command (signature, docstring, alias, undo)
* ``node:<type>``      every node type, with its attribute table
* ``attr:<type>.<a>``  every attribute a type declares (or a plugin adds to it)
* ``plugin:<id>``      every discovered plugin
* ``guide:<page>#<s>`` each section of the Markdown guides (``docs/*.md`` and
  any ``*.md`` file in a plugin's folder)

The UI adds ``tool:``, ``action:`` (menu items, with their keys and where they
are in the menus) and annotates commands with the keys that run them. Plugins
add their own kinds by contributing a provider to the ``docs.providers``
extension point: a callable returning :class:`DocEntry` objects (or dicts with
the same fields). The effects plugin documents its effect types that way.

The index is rebuilt lazily whenever a registry it reads from changes.
"""

from __future__ import annotations

import inspect
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

from .commands import format_value
from .extensions import Disposable

if TYPE_CHECKING:
    from .session import Session

Provider = Callable[[], Iterable["DocEntry | dict[str, Any]"]]
Annotator = Callable[[dict[str, "DocEntry"]], None]

# Display order and search weight of each kind (unknown kinds come last, weight 1).
KINDS: dict[str, tuple[str, str, float]] = {
    "tool": ("Tools", "Tool", 1.15),
    "action": ("Menu items", "Menu item", 1.1),
    "command": ("Commands", "Command", 1.0),
    "node": ("Node types", "Node type", 1.05),
    "attr": ("Attributes", "Attribute", 0.9),
    "effect": ("Effects", "Effect", 1.0),
    "plugin": ("Plugins", "Plugin", 0.85),
    "guide": ("Guides", "Guide", 0.8),
}
# A bare name (no "kind:") is looked up in this order.
LOOKUP_ORDER = ("command", "tool", "node", "attr", "effect", "plugin", "action", "guide")
# Guides that aren't for users or duplicate generated entries.
SKIPPED_GUIDES = ("commands.md",)
SKIPPED_GUIDE_PREFIXES = ("design-", "perf-")


@dataclass
class DocEntry:
    """One documented thing. ``id`` is ``<kind>:<name>``."""

    kind: str
    name: str            # how code refers to it: "pen.combine", "image.opacity", "file.save"
    title: str           # how people call it: "Combine Drawings", "Opacity"
    summary: str = ""    # one line
    body: str = ""       # Markdown details
    plugin: str | None = None
    keywords: str = ""
    keys: list[str] = field(default_factory=list)       # shortcuts, as shown in the app
    usage: str = ""      # a Python snippet: how to use it from a script
    related: list[str] = field(default_factory=list)    # ids of other entries
    data: dict[str, Any] = field(default_factory=dict)  # structured facts (signature, type, default…)
    _haystack: tuple[str, ...] | None = field(default=None, repr=False, compare=False)

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.name}"

    @property
    def kind_title(self) -> str:
        return KINDS.get(self.kind, ("", self.kind.capitalize()))[1]

    def brief(self) -> dict[str, Any]:
        """What a search result needs."""
        out: dict[str, Any] = {"id": self.id, "kind": self.kind, "name": self.name, "title": self.title,
                               "summary": self.summary}
        if self.keys:
            out["keys"] = list(self.keys)
        if self.usage:
            out["usage"] = self.usage
        return out

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in asdict(self).items() if not k.startswith("_") and v not in ("", None, [], {})}
        out["id"] = self.id
        return out

    def markdown(self, titles: Callable[[str], str] | None = None) -> str:
        """The entry as one Markdown page. Related entries link as ``doc:<id>``."""
        meta = [f"*{self.kind_title}*", f"`{self.name}`"]
        if self.plugin:
            meta.append(f"plugin `{self.plugin}`")
        if self.keys:
            meta.append("keys " + ", ".join(f"`{k}`" for k in self.keys))
        lines = [f"# {self.title}", "", " · ".join(meta), ""]
        if self.summary and not self.body.lstrip().startswith(self.summary.rstrip(".…")):
            lines += [self.summary, ""]
        if self.usage:
            lines += ["```python", self.usage, "```", ""]
        if self.body:
            lines += [self.body.strip(), ""]
        if self.related:
            label = titles or (lambda doc_id: doc_id)
            lines += ["**See also:** " + " · ".join(f"[{label(r)}](doc:{r})" for r in self.related), ""]
        return "\n".join(lines).rstrip() + "\n"


def _entry(value: "DocEntry | dict[str, Any]") -> DocEntry:
    if isinstance(value, DocEntry):
        return value
    return DocEntry(**{k: v for k, v in value.items() if k != "id"})


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", text.lower()) if w]


class DocIndex:
    """Every :class:`DocEntry`, searchable. ``session.docs``."""

    def __init__(self, session: "Session") -> None:
        self.session = session
        self._providers: list[Provider] = []
        self._annotators: list[Annotator] = []
        self._entries: dict[str, DocEntry] | None = None
        self._listeners: list[Callable[[], None]] = []
        session.extensions.declare(
            "docs.providers", owner="kernel",
            doc="Callables returning DocEntry objects (or dicts): extra entries for the documentation index")
        session.extensions.watch("docs.providers", self.invalidate)
        session.commands.watch(self.invalidate)
        session.types.watch(lambda _name: self.invalidate())
        session.plugins.watch(self.invalidate)
        for provider in (self._commands, self._node_types, self._plugins, self._guides):
            self.add_provider(provider)

    # ---- Registration -------------------------------------------------------------------

    def add_provider(self, provider: Provider) -> Disposable:
        self._providers.append(provider)
        self.invalidate()
        return Disposable(lambda: provider in self._providers and (self._providers.remove(provider),
                                                                   self.invalidate()))

    def add_annotator(self, annotator: Annotator) -> Disposable:
        """``annotator(entries)`` may change entries after every provider ran (add keys, links…)."""
        self._annotators.append(annotator)
        self.invalidate()
        return Disposable(lambda: annotator in self._annotators and (self._annotators.remove(annotator),
                                                                     self.invalidate()))

    def invalidate(self) -> None:
        if self._entries is None:
            return
        self._entries = None
        for callback in list(self._listeners):
            callback()

    def watch(self, callback: Callable[[], None]) -> Disposable:
        """Called when the index is out of date (it rebuilds on the next read)."""
        self._listeners.append(callback)
        return Disposable(lambda: callback in self._listeners and self._listeners.remove(callback))

    # ---- Reading ----------------------------------------------------------------------------

    def entries(self) -> dict[str, DocEntry]:
        if self._entries is None:
            self._entries = self._build()
        return self._entries

    def _build(self) -> dict[str, DocEntry]:
        entries: dict[str, DocEntry] = {}
        providers = list(self._providers) + list(self.session.extensions.contributions("docs.providers"))
        for provider in providers:
            try:
                for value in provider():
                    entry = _entry(value)
                    entries.setdefault(entry.id, entry)
            except Exception:  # noqa: BLE001  (one broken provider mustn't hide the rest)
                import logging

                logging.getLogger(__name__).exception("Documentation provider %r failed", provider)
        for annotator in list(self._annotators):
            try:
                annotator(entries)
            except Exception:  # noqa: BLE001
                import logging

                logging.getLogger(__name__).exception("Documentation annotator %r failed", annotator)
        for entry in entries.values():
            entry.related = [r for r in dict.fromkeys(entry.related) if r in entries and r != entry.id]
        return entries

    def kinds(self) -> dict[str, int]:
        """How many entries of each kind, in display order."""
        counts: dict[str, int] = {}
        for entry in self.entries().values():
            counts[entry.kind] = counts.get(entry.kind, 0) + 1
        order = list(KINDS) + sorted(k for k in counts if k not in KINDS)
        return {kind: counts[kind] for kind in order if kind in counts}

    def get(self, name: str) -> DocEntry | None:
        """An entry by id (``command:pen.combine``) or by bare name (``pen.combine``, ``group``,
        ``image.opacity``, ``Opacity``): commands first, then tools, node types, attributes…"""
        entries = self.entries()
        name = name.strip()
        if name in entries:
            return entries[name]
        kind, sep, rest = name.partition(":")
        if sep and kind in self.kinds():
            lowered = rest.lower()
            return next((e for e in entries.values() if e.kind == kind and e.name.lower() == lowered), None)
        bare = name.removeprefix("pxcmd.").split("(", 1)[0].strip()
        command = self.session.commands.get(bare)
        if command is not None and f"command:{command.name}" in entries:
            return entries[f"command:{command.name}"]
        type_name, dot, attr = bare.rpartition(".")
        if dot and type_name in self.session.types:  # an inherited attribute: image.opacity
            for node_type in self.session.types.lineage(type_name):
                if f"attr:{node_type.name}.{attr}" in entries:
                    return entries[f"attr:{node_type.name}.{attr}"]
        for exact in (True, False):
            for kind in LOOKUP_ORDER:
                for entry in entries.values():
                    if entry.kind != kind:
                        continue
                    if exact and entry.name == bare:
                        return entry
                    if not exact and bare.lower() in (entry.name.lower(), entry.title.lower()):
                        return entry
        return None

    def title_of(self, doc_id: str) -> str:
        entry = self.entries().get(doc_id)
        return entry.title if entry else doc_id

    def markdown(self, entry: DocEntry) -> str:
        return entry.markdown(self.title_of)

    def search(self, query: str, kinds: Iterable[str] | str | None = None, limit: int | None = 50,
               plugin: str | None = None) -> list[DocEntry]:
        """Entries matching every word of ``query``, best first. An empty query lists everything
        (by kind, then title). ``kind:command`` or ``plugin:pen`` in the query filter too."""
        wanted = {kinds} if isinstance(kinds, str) else set(kinds or ())
        words: list[str] = []
        for token in query.split():
            key, sep, value = token.partition(":")
            if sep and value and key in ("kind", "plugin"):
                if key == "kind":
                    wanted.add(value)
                else:
                    plugin = value
            else:
                words.append(token.lower())
        pool = [e for e in self.entries().values()
                if (not wanted or e.kind in wanted) and (plugin is None or e.plugin == plugin)]
        order = {kind: i for i, kind in enumerate(KINDS)}
        if not words:
            pool.sort(key=lambda e: (order.get(e.kind, len(order)), e.title.lower()))
            return pool[:limit] if limit else pool
        phrase = " ".join(words)
        scored = []
        for entry in pool:
            score = self._score(entry, words, phrase)
            if score > 0:
                scored.append((-score, order.get(entry.kind, len(order)), entry.title.lower(), entry))
        scored.sort(key=lambda s: s[:3])
        results = [s[3] for s in scored]
        return results[:limit] if limit else results

    @staticmethod
    def _score(entry: DocEntry, words: list[str], phrase: str) -> float:
        if entry._haystack is None:
            name, title = entry.name.lower(), entry.title.lower()
            entry._haystack = (name, title, " ".join(_words(name) + _words(title)), entry.keywords.lower(),
                               " ".join(entry.keys).lower(), entry.summary.lower(), entry.body.lower())
        name, title, parts, keywords, keys, summary, body = entry._haystack
        part_list = parts.split()
        total = 0.0
        for word in words:
            if word == name or word == title:
                total += 60
            elif any(p == word for p in part_list):
                total += 40
            elif any(p.startswith(word) for p in part_list):
                total += 30
            elif word in name or word in title:
                total += 20
            elif word in keywords or word in keys:
                total += 12
            elif word in summary:
                total += 6
            elif word in body:
                total += 2
            else:
                return 0.0
        if phrase in (name, title):
            total += 120
        elif title.startswith(phrase) or name.startswith(phrase):
            total += 40
        elif phrase in title or phrase in summary:
            total += 10
        # Shorter names are more general: save_scene before effects.save_default.
        return max(total * KINDS.get(entry.kind, ("", "", 1.0))[2] - len(name) * 0.05, 0.01)

    # ---- Built-in providers ---------------------------------------------------------------------

    def _commands(self) -> Iterable[DocEntry]:
        for command in self.session.commands.commands():
            doc = inspect.cleandoc(command.doc or inspect.getdoc(command.fn) or "")
            summary, _sep, rest = doc.partition("\n")
            signature = command_signature(command.fn)
            call = command.display_name
            data: dict[str, Any] = {"signature": f"{command.name}{signature}", "undoable": command.undoable}
            if command.alias:
                data["alias"] = command.alias
            namespace = command.name.rsplit(".", 1)[0] if "." in command.name else ""
            related = [f"plugin:{command.plugin}"] if command.plugin else []
            if namespace and namespace != command.plugin:
                related.append(f"plugin:{namespace}")
            body = rest.strip()
            facts = [f"- **Signature:** `pxcmd.{command.name}{signature}`"]
            if command.alias:
                facts.append(f"- **Alias:** `pxcmd.{command.alias}`")
            facts.append("- **Undo:** " + ("one undo step" if command.undoable else "not undoable"))
            yield DocEntry(
                kind="command", name=command.name, title=_command_title(command.display_name),
                summary=summary.strip(), body="\n".join(facts) + ("\n\n" + body if body else ""),
                plugin=command.plugin, keywords=" ".join(filter(None, [command.alias or "", namespace])),
                usage=f"pxcmd.{call}{signature}", related=related, data=data)

    def _node_types(self) -> Iterable[DocEntry]:
        types = self.session.types
        declared: dict[str, list[tuple[Any, str | None]]] = {}
        for name in types.names(include_abstract=True):
            node_type = types.get(name)
            declared[name] = [(attr, node_type.plugin) for attr in node_type.attrs]
        for extension in types.extensions():
            declared.setdefault(extension.type_name, []).extend((a, extension.plugin) for a in extension.attrs)
        for name in types.names(include_abstract=True):
            node_type = types.get(name)
            lineage = [t.name for t in types.lineage(name)]
            attrs = types.attrs(name)
            facts = [f"- **Inherits:** {' → '.join(f'`{t}`' for t in lineage[1:])}" if len(lineage) > 1 else "",
                     f"- **Category:** {types.category(name)}" if types.category(name) else "",
                     "- **Abstract:** nodes of this exact type can't be created" if node_type.abstract else "",
                     f"- **Capabilities:** {', '.join(sorted(node_type.capabilities))}"
                     if node_type.capabilities else ""]
            rows = ["| Attribute | Type | Default | Description |", "| --- | --- | --- | --- |"]
            for attr in attrs.values():
                if attr.hidden:
                    continue
                default = "" if attr.default is None else f"`{_short(format_value(attr.default))}`"
                rows.append(f"| [{attr.name}](doc:attr:{_attr_owner(name, attr.name, declared, types)}."
                            f"{attr.name}) | {attr.type} | {default} | {_cell(attr.doc or attr.label)} |")
            body = "\n".join(f for f in facts if f) + "\n\n## Attributes\n\n" + "\n".join(rows)
            summary = node_type.doc.strip().split("\n", 1)[0] if node_type.doc else ""
            yield DocEntry(
                kind="node", name=name, title=node_type.nice_name,
                summary=summary or f"{node_type.nice_name} node" + (f" (a kind of {lineage[1]})"
                                                                     if len(lineage) > 1 else ""),
                body=body, plugin=node_type.plugin,
                usage="" if node_type.abstract else f'pxcmd.create_node("{name}")',
                related=[f"node:{t}" for t in lineage[1:]] + ([f"plugin:{node_type.plugin}"]
                                                             if node_type.plugin else []),
                data={"inherits": lineage[1:], "abstract": node_type.abstract,
                      "attributes": [a.name for a in attrs.values()]})
        for type_name, owned in declared.items():
            for attr, plugin in owned:
                info = attr.to_dict()
                facts = [f"- **Type:** {attr.type}"]
                if attr.default is not None:
                    facts.append(f"- **Default:** `{_short(format_value(attr.default), 120)}`")
                if attr.minimum is not None or attr.maximum is not None:
                    facts.append(f"- **Range:** {attr.minimum if attr.minimum is not None else '…'} to "
                                 f"{attr.maximum if attr.maximum is not None else '…'}")
                if attr.choices:
                    facts.append("- **Choices:** " + ", ".join(f"`{c}`" for c in attr.choices))
                if attr.unit:
                    facts.append(f"- **Unit:** {attr.unit}")
                flags = [flag for flag, on in (("computed (read-only)", attr.computed), ("read-only", attr.readonly),
                                               ("hidden", attr.hidden), ("not saved", not attr.storable)) if on]
                if flags:
                    facts.append("- **Notes:** " + ", ".join(flags))
                if attr.short:
                    facts.append(f"- **Short name:** `{attr.short}`")
                example = format_value(attr.default) if attr.default is not None else "value"
                usage = (f'pxcmd.get_attr("<node>.{attr.name}")' if attr.computed or attr.readonly
                         else f'pxcmd.set_attr("<node>.{attr.name}", {_short(example, 60)})')
                summary = (attr.doc or "").strip().split("\n", 1)[0]
                yield DocEntry(
                    kind="attr", name=f"{type_name}.{attr.name}", title=attr.label or _nice(attr.name),
                    summary=summary or f"The {attr.name} attribute ({attr.type}) of {type_name} nodes",
                    body="\n".join(facts), plugin=plugin, keywords=" ".join(filter(None, [attr.short, attr.group,
                                                                                         type_name])),
                    usage=usage, related=[f"node:{type_name}"], data=info)

    def _plugins(self) -> Iterable[DocEntry]:
        for info in self.session.plugins.list_plugins():
            facts = [f"- **Version:** {info['version']}", f"- **Status:** {info['status']}"
                     + ("" if info["enabled"] or info["status"] == "disabled" else " (disabled)"),
                     "- **Built in**" if info["builtin"] else f"- **Folder:** `{info['path']}`"]
            if not info["builtin"]:
                author = info["author"]
                who = author.get("name", "") + (f" ({author['organization']})" if author.get("organization") else "")
                facts.append(f"- **Author:** {who or 'not given'}")
                facts.append(f"- **Trust:** {info['trust_label']}" + (f" — {info['trust_reason']}"
                                                                     if info["trust_reason"] else ""))
                facts.append(f"- **Source:** {info['source']}")
                if info["license"]:
                    facts.append(f"- **License:** {info['license']}")
                for key, label in (("homepage", "Homepage"), ("repository", "Repository")):
                    if info[key]:
                        facts.append(f"- **{label}:** <{info[key]}>")
                facts.append("- **Permissions:** " + (", ".join(info["permissions"]) or "none declared"))
                if info["conflicts"]:
                    facts.append(f"- **Other copies:** {len(info['conflicts'])} (see Window → Plugins…)")
            if info["requires"]:
                facts.append("- **Requires:** " + ", ".join(f"[{r}](doc:plugin:{r})" for r in info["requires"]))
            if info["python_requires"]:
                facts.append("- **Python packages:** " + ", ".join(info["python_requires"]))
            if info["error"]:
                facts.append(f"- **Error:** {info['error']}")
            commands = [c.name for c in self.session.commands.commands() if c.plugin == info["id"]]
            if commands:
                facts += ["", "## Commands", ""] + [f"- [{c}](doc:command:{c})" for c in commands]
            if not info["builtin"]:
                readme = self.session.plugins.readme(info["id"])
                if readme:
                    facts += ["", "## About", "", _demote_headings(readme)]
            yield DocEntry(kind="plugin", name=info["id"], title=info["name"] or info["id"],
                           summary=info["description"], body="\n".join(facts), plugin=info["id"],
                           usage=f'pxcmd.plugin_info("{info["id"]}")',
                           related=[f"plugin:{r}" for r in info["requires"]],
                           keywords=" ".join([info["author"].get("name", ""), info["category"], *info["tags"]]),
                           data={k: info[k] for k in ("version", "status", "enabled", "builtin", "requires", "trust",
                                                      "author", "license", "permissions", "source")})

    def _guides(self) -> Iterable[DocEntry]:
        sources: list[tuple[Path, str | None]] = []
        folder = docs_folder()
        if folder is not None:
            sources += [(path, None) for path in sorted(folder.glob("*.md"))
                        if path.name not in SKIPPED_GUIDES and not path.name.startswith(SKIPPED_GUIDE_PREFIXES)]
        for info in self.session.plugins.list_plugins():
            if info["path"] and Path(info["path"]).is_dir():
                sources += [(path, info["id"]) for path in sorted(Path(info["path"]).glob("*.md"))]
        for path, plugin in sources:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            page = path.stem if plugin is None else f"{plugin}/{path.stem}"
            yield from guide_sections(text, page, plugin)


# ---- Helpers --------------------------------------------------------------------------------


def _demote_headings(markdown: str) -> str:
    """A plugin's README inside its entry: its headings go below the entry's own."""
    return re.sub(r"(?m)^(#{1,5}) ", lambda m: "#" * min(6, len(m.group(1)) + 2) + " ", markdown)


def docs_folder() -> Path | None:
    """The Markdown guides: ``docs/`` next to the sources, or bundled in the standalone build."""
    root = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    folder = root / "docs"
    return folder if folder.is_dir() else None


def command_signature(fn: Callable[..., Any]) -> str:
    """``(nodes: Any = None, name: str = 'Group')``: annotations as written, defaults as Python."""
    try:
        parameters = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return "(...)"
    parts: list[str] = []
    star_done = False
    for index, p in enumerate(parameters):
        if p.kind is p.KEYWORD_ONLY and not star_done:
            parts.append("*")
            star_done = True
        text = {p.VAR_POSITIONAL: "*", p.VAR_KEYWORD: "**"}.get(p.kind, "") + p.name
        star_done = star_done or p.kind is p.VAR_POSITIONAL
        if p.annotation is not p.empty:
            text += ": " + (p.annotation if isinstance(p.annotation, str) else inspect.formatannotation(p.annotation))
        if p.default is not p.empty:
            text += (" = " if p.annotation is not p.empty else "=") + repr(p.default)
        parts.append(text)
        if p.kind is p.POSITIONAL_ONLY and (index + 1 == len(parameters)
                                            or parameters[index + 1].kind is not p.POSITIONAL_ONLY):
            parts.append("/")
    return f"({', '.join(parts)})"


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def guide_sections(text: str, page: str, plugin: str | None = None) -> Iterable[DocEntry]:
    """One entry per heading (levels 1-3) of a Markdown page; the level-1 heading is the page."""
    sections: list[tuple[int, str, list[str]]] = []
    fenced = False
    current: tuple[int, str, list[str]] | None = None
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        match = None if fenced else _HEADING.match(line)
        if match:
            current = (len(match.group(1)), match.group(2).strip(), [])
            sections.append(current)
        elif current is not None:
            current[2].append(line)
        else:
            current = (1, page, [line])
            sections.append(current)
    page_title = next((title for level, title, _ in sections if level == 1), page)
    page_id = f"guide:{page}"
    for level, title, lines in sections:
        body = "\n".join(lines).strip()
        if level == 1:
            name, entry_title = page, title
        else:
            name, entry_title = f"{page}#{slug(title)}", title
        related = [] if level == 1 else [page_id]
        if plugin:
            related.append(f"plugin:{plugin}")
        yield DocEntry(kind="guide", name=name, title=entry_title, summary=_first_sentence(body),
                       body=body, plugin=plugin, keywords=page_title, related=related,
                       data={"page": page, "level": level})


def _first_sentence(markdown: str) -> str:
    for paragraph in re.split(r"\n\s*\n", markdown):
        text = paragraph.strip()
        if not text or text.startswith(("|", "```", "!", "<", "#", "- ", "* ", "1.")):
            continue
        text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", " ".join(text.split()))
        text = re.sub(r"[*_`]", "", text)
        end = re.search(r"(?<=[.!?])\s", text)
        return _short(text[:end.start()] if end else text, 200)
    return ""


def _attr_owner(type_name: str, attr: str, declared: dict[str, list[tuple[Any, str | None]]], types: Any) -> str:
    for node_type in types.lineage(type_name):
        if any(a.name == attr for a, _plugin in declared.get(node_type.name, ())):
            return node_type.name
    for owner, attrs in declared.items():  # an extension on an ancestor that isn't registered yet
        if any(a.name == attr for a, _plugin in attrs):
            return owner
    return type_name


def _command_title(name: str) -> str:
    return name.rsplit(".", 1)[-1].replace("_", " ").capitalize()


def _nice(name: str) -> str:
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).replace("_", " ").capitalize()


def _short(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _cell(text: str) -> str:
    return " ".join((text or "").split()).replace("|", "/")
