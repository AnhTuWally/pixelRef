"""``python -m pixelref docs …``: search and read pixelRef's documentation from a terminal or a script.

    python -m pixelref docs search blur              # best matches, one per line
    python -m pixelref docs search "link curved" --kind attr --json
    python -m pixelref docs show pen.combine         # one entry as Markdown (or --json)
    python -m pixelref docs kinds
    python -m pixelref docs export -o pixelref-docs.json        # everything (--format markdown)

The entries come from an offscreen pixelRef with every plugin, so they include
tools, menu items and keys. Your own plugins and ``keymap.toml`` are included
unless you pass ``--no-user``; nothing is written to your settings. The
standalone build runs the same as ``pixelref-cli docs …``.

``python -m pixelref mcp`` serves the same index to AI agents (see ``mcp_server``).
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import json
import os
import sys
from pathlib import Path
from typing import Any


def build_app(user: bool = True, plugin_paths: tuple[str, ...] = (), trusted_paths: tuple[str, ...] = ()) -> Any:
    """An offscreen App whose ``session.docs`` holds every entry. In-memory settings, so the
    user's real settings are never written (their plugin choices and approvals are copied in,
    so the same plugins load as in the app); plugins' prints go to stderr (stdout may be a protocol)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    qapp = QApplication.instance() or QApplication([sys.argv[0]])
    qapp.setApplicationName("pixelRef")  # the user's config folder (keymap.toml, plugins) is found by name
    qapp.setOrganizationName("pixelRef")
    from .core.session import Session
    from .ui import _user_plugin_dir
    from .ui.app import App

    from .core.settings import MemorySettings
    from .ui.settings import plugin_choices

    settings = plugin_choices() if user else MemorySettings()
    with contextlib.redirect_stdout(sys.stderr):
        session = Session(settings=settings, plugin_paths=tuple(Path(p) for p in plugin_paths),
                          trusted_plugin_paths=tuple(Path(p) for p in trusted_paths),
                          user_plugin_dir=_user_plugin_dir() if user else None)
        session.start()
        return App(session, persist_layout=False, load_user_files=user)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pixelref docs", description="Search pixelRef's documentation.")
    parser.add_argument("--no-user", action="store_true", help="leave out your own plugins and keymap.toml")
    parser.add_argument("--plugin-path", action="append", default=[], metavar="DIR",
                        help="also load plugins from DIR (the ones you approved in the app)")
    parser.add_argument("--trust-plugin-path", action="append", default=[], metavar="DIR",
                        help="also load every plugin in DIR, trusting its code for this run")
    # The same options after the action too (`docs search blur --no-user`); SUPPRESS keeps the ones given before it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--no-user", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--plugin-path", action="append", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--trust-plugin-path", action="append", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser = functools.partial(sub.add_parser, parents=[common])
    search = sub.add_parser("search", help="find entries (every word must match)")
    search.add_argument("query", nargs="*")
    search.add_argument("--kind", action="append", help="only this kind (repeatable): tool, action, command, "
                                                        "node, attr, plugin, guide…")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--json", action="store_true")
    show = sub.add_parser("show", help="one entry, by id or name")
    show.add_argument("name")
    show.add_argument("--json", action="store_true")
    kinds = sub.add_parser("kinds", help="the kinds of entries and their counts")
    kinds.add_argument("--json", action="store_true")
    export = sub.add_parser("export", help="every entry, for an AI's context or a static site")
    export.add_argument("--format", choices=("json", "markdown"), default="json")
    export.add_argument("--kind", action="append")
    export.add_argument("-o", "--output", help="file to write (default: standard output)")
    args = parser.parse_args(argv)

    app = build_app(user=not args.no_user, plugin_paths=tuple(args.plugin_path),
                    trusted_paths=tuple(args.trust_plugin_path))
    docs = app.session.docs
    try:
        if args.action == "search":
            results = docs.search(" ".join(args.query), args.kind, args.limit or None)
            if args.json:
                _print(json.dumps([e.brief() for e in results], indent=1, ensure_ascii=False))
            else:
                width = max((len(e.id) for e in results), default=0)
                for entry in results:
                    keys = f"  [{', '.join(entry.keys)}]" if entry.keys else ""
                    _print(f"{entry.id:<{width}}  {entry.title} — {entry.summary}{keys}")
                if not results:
                    _print("No matches.")
            return 0 if results else 1
        if args.action == "show":
            entry = docs.get(args.name)
            if entry is None:
                hits = docs.search(args.name, limit=5)
                print(f"No documentation for '{args.name}'."
                      + (f" Did you mean: {', '.join(e.id for e in hits)}?" if hits else ""), file=sys.stderr)
                return 1
            if args.json:
                _print(json.dumps(entry.to_dict() | {"markdown": docs.markdown(entry)}, indent=1,
                                  ensure_ascii=False, default=str))
            else:
                _print(docs.markdown(entry))
            return 0
        if args.action == "kinds":
            counts = docs.kinds()
            _print(json.dumps(counts, indent=1) if args.json else "\n".join(f"{k:<10} {n}" for k, n in counts.items()))
            return 0
        entries = docs.search("", args.kind, None)
        if args.format == "json":
            text = json.dumps([e.to_dict() for e in entries], indent=1, ensure_ascii=False, default=str)
        else:
            text = "\n\n---\n\n".join(docs.markdown(e) for e in entries)
        if args.output:
            Path(args.output).write_text(text, encoding="utf-8")
            print(f"Wrote {len(entries)} entries to {args.output}", file=sys.stderr)
        else:
            _print(text)
        return 0
    finally:
        app.close(force=True)


def _print(text: str) -> None:
    """Print UTF-8 whatever the console's code page (Windows consoles default to cp1252)."""
    sys.stdout.buffer.write((text + "\n").encode("utf-8"))
    sys.stdout.flush()
