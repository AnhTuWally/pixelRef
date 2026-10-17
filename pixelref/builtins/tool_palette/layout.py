"""The user's arrangement of the Tools view: sections of buttons, in order, some hidden.

Every button is an action key: tools are ``tool.<id>``, the Add buttons are the
actions plugins contribute to ``palette.buttons``, and the user can add any other
action (a command, a script, a macro). Plugins only give the defaults: each
default button has a home section (``tools`` or ``add``) and an order.

The layout is kept in the user's settings once they change something. A default
button the layout doesn't mention yet (a plugin installed or enabled later)
shows up in its home section next to the neighbours it has by default, so a new
plugin never makes the user redo their palette. A button whose plugin is off
keeps its place for when it comes back. Hidden buttons keep their place too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

LAYOUT_KEY = "layout"
HOME_SECTIONS = {"tools": "Tools", "add": "Add"}


@dataclass
class Section:
    id: str
    title: str
    items: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "items": list(self.items)}


def default_sections(defaults: dict[str, list[str]]) -> list[Section]:
    return [Section(sid, HOME_SECTIONS.get(sid, sid.title()), list(keys)) for sid, keys in defaults.items()]


def parse(stored: Any) -> tuple[list[Section], set[str]] | None:
    """The stored layout, cleaned up (junk skipped, each key in one place); None when unset."""
    if not isinstance(stored, dict) or not isinstance(stored.get("sections"), list):
        return None
    sections: list[Section] = []
    seen_keys: set[str] = set()
    for entry in stored["sections"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"]:
            continue
        if any(s.id == entry["id"] for s in sections):
            continue
        items = []
        for key in entry.get("items") or []:
            if isinstance(key, str) and key and key not in seen_keys:
                seen_keys.add(key)
                items.append(key)
        title = entry.get("title")
        sections.append(Section(entry["id"], str(title) if title else HOME_SECTIONS.get(entry["id"], ""), items))
    hidden = {k for k in stored.get("hidden") or [] if isinstance(k, str)}
    return sections, hidden


def merge(sections: list[Section], defaults: dict[str, list[str]]) -> list[Section]:
    """Put the default keys the layout doesn't mention into their home sections, each after
    the nearest earlier default neighbour already there (else before the nearest later one)."""
    placed = {key for section in sections for key in section.items}
    for sid, keys in defaults.items():
        missing = [k for k in keys if k not in placed]
        if not missing:
            continue
        section = next((s for s in sections if s.id == sid), None)
        if section is None:
            section = Section(sid, HOME_SECTIONS.get(sid, sid.title()))
            sections.append(section)
        for index, key in enumerate(keys):
            if key in placed:
                continue
            position = next((section.items.index(k) + 1 for k in reversed(keys[:index]) if k in section.items), None)
            if position is None:
                position = next((section.items.index(k) for k in keys[index + 1:] if k in section.items),
                                len(section.items))
            section.items.insert(position, key)
            placed.add(key)
    return sections


class PaletteLayout:
    """Reads and writes the layout in ``store`` (the plugin's settings)."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def customized(self) -> bool:
        return parse(self.store.json_value(LAYOUT_KEY, None)) is not None

    def resolve(self, defaults: dict[str, list[str]]) -> tuple[list[Section], set[str]]:
        """(every section with every key, the hidden keys) for these defaults
        (home section id -> its default keys, in order)."""
        parsed = parse(self.store.json_value(LAYOUT_KEY, None))
        if parsed is None:
            return default_sections(defaults), set()
        sections, hidden = parsed
        return merge(sections, defaults), hidden

    def save(self, sections: list[Section], hidden: set[str]) -> None:
        placed = {key for section in sections for key in section.items}
        self.store.set_json(LAYOUT_KEY, {"sections": [s.as_dict() for s in sections],
                                         "hidden": sorted(k for k in hidden if k in placed)})

    def reset(self) -> None:
        self.store.remove(LAYOUT_KEY)
