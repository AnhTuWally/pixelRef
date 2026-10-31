"""Finding elements on the board by what they are called, how they are tagged and what they say.

Headless (no Qt): batch mode, scripts and the Find panel use the same code.

**Queries.** Every term must match (AND); case never matters::

    sky                 any field contains "sky": name, tags, type, note, metadata, text
    "warm light"        a phrase (quotes keep the spaces)
    #hero  tag:hero     a tag, whole (``#sky*`` with wildcards: sky, skyline…)
    #sky,sea            either tag (commas separate tags that may match)
    type:image          the node type, a base type (``type:annotation_*``) or its label
    name:ref_           the name contains it
    note:fix            the note contains it
    shot:010  shot:     a metadata field contains it (``shot:`` alone: has the field)
    -#done  -sketch     leave out what matches

**Index.** :class:`SearchIndex` keeps one :class:`Entry` (the node's searchable text, folded to
lower case) per node and rebuilds only the entries of nodes changed since the last search
(``SceneGraph.node_revisions``), so it is derived data: never saved, always rebuildable.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field as dataclass_field
from pathlib import PurePath
from typing import Any

# Attribute types whose text is searched (besides tags / note / metadata), from the node type's schema.
TEXT_TYPES = frozenset({"string", "text", "path", "asset"})
# Fields a ``field:`` prefix names; any other prefix is a metadata key.
FIELDS = ("tag", "type", "name", "note")
# How much a term matching each field counts (results are sorted by the sum, then by scene order).
WEIGHTS = {"name": 100, "tags": 80, "type": 40, "metadata": 30, "note": 20, "text": 20}
SNIPPET = 48  # characters of note / text shown around a match

_TOKEN = re.compile(r'\s*(-)?(#|[A-Za-z_][\w.-]*:)?(?:"([^"]*)"?|(\S+))?')


def fold(text: str) -> str:
    return text.casefold()


@dataclass(frozen=True)
class Term:
    """One part of a query: ``field`` is None (anywhere), tag, type, name, note or meta."""

    value: str  # folded
    field: str | None = None
    key: str = ""  # the metadata key (folded) for field "meta"
    negate: bool = False
    glob: bool = dataclass_field(init=False, default=False)  # the value has wildcards (* ? [)
    # A tag term's tags (``#sky,sea``: either); other terms: just the value.
    options: tuple[str, ...] = dataclass_field(init=False, default=(), repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "glob", any(c in self.value for c in "*?["))
        options = tuple(p.strip() for p in self.value.split(",") if p.strip()) if self.field == "tag" else ()
        object.__setattr__(self, "options", options or (self.value,))


def parse_query(text: str) -> list[Term]:
    """The terms of a query (see the module docstring). Empty terms are dropped."""
    terms: list[Term] = []
    for match in _TOKEN.finditer(text or ""):
        negate, prefix, quoted, bare = match.groups()
        value = fold(quoted if quoted is not None else bare or "").strip()
        field_name: str | None = None
        key = ""
        if prefix == "#":
            field_name = "tag"
        elif prefix:
            name = fold(prefix[:-1])
            if name in FIELDS:
                field_name = name
            else:
                field_name, key = "meta", name
        if not value and field_name != "meta":
            continue
        terms.append(Term(value, field_name, key, bool(negate)))
    return terms


def tag_query(tags: list[str]) -> str:
    """A query finding anything with one of ``tags``: ``#sky,sea`` (quoted when a tag has spaces)."""
    joined = ",".join(t for t in tags if t)
    if not joined:
        return ""
    return f'#"{joined}"' if any(c in joined for c in ' "') else f"#{joined}"


@dataclass
class Entry:
    """A node's searchable text, folded to lower case."""

    node: Any  # the scene's Node (identity tells whether the entry is still current)
    revision: int
    name: str
    node_type: str
    on_canvas: bool
    types: tuple[str, ...]  # folded: the type, its bases and its label (``type:``)
    type_words: tuple[str, ...]  # folded: the type and its label (plain words; "node" would match all)
    tags: tuple[str, ...]  # as stored
    note: str
    metadata: dict[str, tuple[str, str]]  # folded key: (key, folded value text)
    texts: tuple[tuple[str, str], ...]  # (attribute, text) of the type's text attributes
    # Folded copies, made once per entry rather than once per search.
    folded_name: str = ""
    folded_tags: tuple[str, ...] = ()
    folded_note: str = ""
    folded_texts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.folded_name = fold(self.name)
        self.folded_tags = tuple(fold(t) for t in self.tags)
        self.folded_note = fold(self.note)
        self.folded_texts = tuple(fold(text) for _attr, text in self.texts)


@dataclass
class Match:
    name: str
    node_type: str
    score: int
    fields: tuple[str, ...]  # which fields matched: name, tags, type, note, metadata, text
    snippet: str = ""  # the note or text around the first match, when that's where it matched
    tags: tuple[str, ...] = ()
    # (text, needle) the snippet is cut from: done only for the results returned.
    snippet_source: tuple[str, str] | None = dataclass_field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.node_type, "score": self.score, "fields": list(self.fields),
                "snippet": self.snippet, "tags": list(self.tags)}


def _value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False)


def snippet(text: str, needle: str, width: int = SNIPPET) -> str:
    """One line of ``text`` around the first occurrence of ``needle`` (folded)."""
    flat = " ".join(text.split())
    at = fold(flat).find(needle) if needle else 0
    if at < 0:
        at = 0
    start = max(0, at - width // 3)
    if start:
        space = flat.find(" ", start, at)  # start on a whole word when one begins before the match
        start = space + 1 if space >= 0 else start
    end = min(len(flat), start + width)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


class SearchIndex:
    """Searchable text of every node in a scene, rebuilt only where the scene changed."""

    def __init__(self, scene: Any, types: Any) -> None:
        self.scene = scene
        self.types = types
        self._entries: dict[str, Entry] = {}
        self._order: list[Entry] = []
        self._revision: Any = None
        self.builds = 0  # entries built so far (tests and measurements)

    # ---- Index ---------------------------------------------------------------------------------

    def entries(self) -> list[Entry]:
        """Every node's entry, in scene order (parents first, back to front)."""
        scene = self.scene
        stamp = (scene.revision, self.types.generation)
        if stamp == self._revision:
            return self._order
        if self._revision is None or self._revision[1] != self.types.generation:
            self._entries.clear()  # labels and text attributes depend on the registered types
        revisions = scene.node_revisions
        old, fresh = self._entries, {}
        order = []
        for node in scene.iter_nodes():
            revision = revisions.get(node.name, -1)
            entry = old.get(node.name)
            if entry is None or entry.node is not node or entry.revision != revision:
                entry = self._build(node, revision)
            fresh[node.name] = entry
            order.append(entry)
        self._entries, self._order, self._revision = fresh, order, stamp
        return order

    def invalidate(self) -> None:
        """Forget everything; the next search rebuilds every entry."""
        self._entries.clear()
        self._order = []
        self._revision = None

    def _build(self, node: Any, revision: int) -> Entry:
        self.builds += 1
        types = self.types
        attrs = node.attrs
        known = node.node_type in types
        names = [t.name for t in types.lineage(node.node_type)] if known else [node.node_type]
        label = types.label(node.node_type) if known else ""
        meta = attrs.get("metadata")
        metadata = {fold(str(k)): (str(k), fold(_value_text(v))) for k, v in meta.items()} \
            if isinstance(meta, dict) else {}
        texts: list[tuple[str, str]] = []
        specs = types.attrs(node.node_type) if known else {}
        for key, value in attrs.items():
            if key in ("tags", "note", "metadata") or not isinstance(value, str) or not value:
                continue
            spec = specs.get(key)
            if spec is not None and (spec.type not in TEXT_TYPES or spec.hidden):
                continue
            if spec is not None and spec.type == "path":
                value = PurePath(value).name  # the file name: the folders would match everything
            elif spec is not None and spec.type == "asset":
                asset = self.scene.get_asset(value)
                value = asset.name if asset is not None else ""
            if value:
                texts.append((key, value))
        return Entry(
            node=node, revision=revision, name=node.name, node_type=node.node_type,
            on_canvas=types.is_canvas(node.node_type) if known else "translate" in attrs,
            types=tuple(fold(t) for t in [*names, label] if t),
            type_words=tuple(fold(t) for t in [node.node_type, label] if t),
            tags=tuple(str(t) for t in attrs.get("tags") or []),
            note=str(attrs.get("note") or ""), metadata=metadata, texts=tuple(texts),
        )

    # ---- Searching -------------------------------------------------------------------------------

    def search(self, query: str | list[Term], limit: int | None = None, canvas_only: bool = True) -> list[Match]:
        """Nodes matching every term, best first (then in scene order). An empty query finds nothing.
        ``canvas_only`` leaves out data nodes (cameras, display presets)."""
        terms = parse_query(query) if isinstance(query, str) else list(query)
        if not terms:
            return []
        found: list[tuple[int, int, Match]] = []
        for index, entry in enumerate(self.entries()):
            if canvas_only and not entry.on_canvas:
                continue
            match = self.match(entry, terms)
            if match is not None:
                found.append((-match.score, index, match))
        found.sort(key=lambda item: (item[0], item[1]))
        matches = [m for _s, _i, m in found]
        if limit:
            matches = matches[:limit]
        for found_match in matches:
            if found_match.snippet_source is not None:
                found_match.snippet = snippet(*found_match.snippet_source)
        return matches

    def match(self, entry: Entry, terms: list[Term]) -> Match | None:
        score, fields, source = 0, [], None
        for term in terms:
            hits = self._hits(entry, term)
            if term.negate:
                if hits:
                    return None
                continue
            if not hits:
                return None
            best = max(hits, key=lambda h: h[1])
            score += best[1]
            for field_name, _weight, text in hits:
                if field_name not in fields:
                    fields.append(field_name)
                if text and source is None:
                    source = (text, term.value)
        if all(t.negate for t in terms):
            score = 1  # only exclusions: everything else matches
        return Match(entry.name, entry.node_type, score, tuple(fields), "", entry.tags, source)

    def _hits(self, entry: Entry, term: Term) -> list[tuple[str, int, str]]:
        """(field, score, text for a snippet) for each field the term matches."""
        value = term.value
        kind = term.field
        hits: list[tuple[str, int, str]] = []
        if kind in (None, "name"):
            name = entry.folded_name
            if (fnmatch.fnmatchcase(name, value) if term.glob else value in name):
                bonus = 200 if name == value else 50 if name.startswith(value) else 0
                hits.append(("name", WEIGHTS["name"] + bonus, ""))
        if kind in (None, "tag"):
            tags = entry.folded_tags
            if kind == "tag":
                hit = any(fnmatch.fnmatchcase(t, option) for option in term.options for t in tags) \
                    if term.glob else any(option in tags for option in term.options)
            else:
                hit = any(value in t for t in tags)
            if hit:
                exact = any(option in tags for option in term.options)
                hits.append(("tags", WEIGHTS["tags"] + (40 if exact else 0), ""))
        if kind == "type":
            if any(fnmatch.fnmatchcase(t, value) for t in entry.types):
                hits.append(("type", WEIGHTS["type"], ""))
        elif kind is None and any(value in t for t in entry.type_words):
            hits.append(("type", WEIGHTS["type"], ""))
        if kind in (None, "note") and value in entry.folded_note:
            hits.append(("note", WEIGHTS["note"], entry.note))
        if kind is None:
            if any(value in k or value in v for k, (_key, v) in entry.metadata.items()):
                hits.append(("metadata", WEIGHTS["metadata"], ""))
            for (_attr, text), folded in zip(entry.texts, entry.folded_texts):
                if value in folded:
                    hits.append(("text", WEIGHTS["text"], text))
                    break
        elif kind == "meta":
            found = entry.metadata.get(term.key)
            if found is not None and (not value or value in found[1]):
                hits.append(("metadata", WEIGHTS["metadata"], ""))
        return hits
