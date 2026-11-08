"""Code blocks: the ``annotation_code`` node type, a small syntax highlighter and the commands.

The highlighter is a plain tokenizer (no Qt): ``tokenize(code, language)`` returns
``(start, end, kind)`` spans for comments, strings, numbers, keywords and so on,
which the canvas item turns into colours.
"""

from __future__ import annotations

import re
from typing import Any

from pixelref.plugin_api import CAP_CONTENT, Attr, CommandError, NodeType, differs

DEFAULT_WIDTH = 480.0
HELLO_WORLD = 'print("Hello, World!")'  # what a new, empty block starts with
THEMES = ("dark", "light")

C_KEYWORDS = ("auto break case char const continue default do double else enum extern float for goto if inline int "
              "long register return short signed sizeof static struct switch typedef union unsigned void volatile "
              "while bool true false NULL nullptr")
CPP_KEYWORDS = C_KEYWORDS + (" class namespace template typename public private protected virtual new delete using "
                             "try catch throw this operator override final constexpr noexcept")
JS_KEYWORDS = ("async await break case catch class const continue debugger default delete do else export extends "
               "finally for from function if import in instanceof let new of return static super switch this throw "
               "try typeof var void while with yield true false null undefined")
TS_KEYWORDS = JS_KEYWORDS + " interface type enum implements namespace public private protected readonly abstract as any"

# language -> (label, keywords, line comments, block comment, string quotes)
LANGUAGES: dict[str, tuple[str, str, tuple[str, ...], tuple[str, str] | None, str]] = {
    "plain": ("Plain text", "", (), None, ""),
    "python": ("Python", "and as assert async await break class continue def del elif else except finally for from "
               "global if import in is lambda nonlocal not or pass raise return try while with yield True False None "
               "self", ("#",), None, "\"'"),
    "javascript": ("JavaScript", JS_KEYWORDS, ("//",), ("/*", "*/"), "\"'`"),
    "typescript": ("TypeScript", TS_KEYWORDS, ("//",), ("/*", "*/"), "\"'`"),
    "json": ("JSON", "true false null", (), None, "\""),
    "c": ("C", C_KEYWORDS, ("//",), ("/*", "*/"), "\"'"),
    "cpp": ("C++", CPP_KEYWORDS, ("//",), ("/*", "*/"), "\"'"),
    "csharp": ("C#", CPP_KEYWORDS + " string var foreach in out ref params get set async await namespace using "
               "readonly sealed", ("//",), ("/*", "*/"), "\"'"),
    "java": ("Java", CPP_KEYWORDS + " extends implements interface import package final static synchronized "
             "boolean byte String", ("//",), ("/*", "*/"), "\"'"),
    "go": ("Go", "break case chan const continue default defer else fallthrough for func go goto if import interface "
           "map package range return select struct switch type var true false nil", ("//",), ("/*", "*/"), "\"'`"),
    "rust": ("Rust", "as async await break const continue crate dyn else enum extern false fn for if impl in let loop "
             "match mod move mut pub ref return self Self static struct super trait true type unsafe use where while",
             ("//",), ("/*", "*/"), "\""),
    "html": ("HTML / XML", "", (), ("<!--", "-->"), "\"'"),
    "css": ("CSS", "important", (), ("/*", "*/"), "\"'"),
    "sql": ("SQL", "select from where and or not insert into values update set delete create table drop alter add "
            "join left right inner outer on group by order having limit as null is in like distinct union primary key",
            ("--",), ("/*", "*/"), "'\""),
    "shell": ("Shell", "if then else elif fi for while do done case esac function in return export local echo cd "
              "exit set unset source", ("#",), None, "\"'"),
}
LANGUAGE_CHOICES = tuple(LANGUAGES)

# Kinds a token can have; the canvas maps them to theme colours.
KINDS = ("comment", "string", "number", "keyword", "tag", "function")
_NUMBER = re.compile(r"\b(?:0[xX][0-9a-fA-F_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)\b")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TAG = re.compile(r"</?[A-Za-z][\w:.-]*")
_CALL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?=\()")
_CSS_PROP = re.compile(r"[a-z-]+(?=\s*:)")


def tokenize(code: str, language: str) -> list[tuple[int, int, str]]:
    """Spans ``(start, end, kind)`` of the highlighted parts of ``code`` (sorted, non-overlapping)."""
    spec = LANGUAGES.get(language)
    if spec is None or language == "plain" or not code:
        return []
    _label, keyword_text, line_comments, block, quotes = spec
    keywords = frozenset(keyword_text.split())
    ignore_case = language == "sql"
    if ignore_case:
        keywords = frozenset(k.lower() for k in keywords)
    spans: list[tuple[int, int, str]] = []
    i, n = 0, len(code)
    while i < n:
        ch = code[i]
        if block and code.startswith(block[0], i):
            end = code.find(block[1], i + len(block[0]))
            end = n if end < 0 else end + len(block[1])
            spans.append((i, end, "comment"))
            i = end
            continue
        comment = next((c for c in line_comments if code.startswith(c, i)), None)
        if comment is not None:
            end = code.find("\n", i)
            end = n if end < 0 else end
            spans.append((i, end, "comment"))
            i = end
            continue
        if ch in quotes:
            if language == "python" and code.startswith(ch * 3, i):
                close = code.find(ch * 3, i + 3)
                end = n if close < 0 else close + 3
            else:
                end = i + 1
                while end < n and code[end] != ch and code[end] != "\n":
                    end += 2 if code[end] == "\\" else 1
                end = min(n, end + 1) if end < n and code[end] == ch else min(n, end)
            spans.append((i, end, "string"))
            i = end
            continue
        if language == "html":
            match = _TAG.match(code, i)
            if match:
                spans.append((match.start(), match.end(), "tag"))
                i = match.end()
                continue
        if language == "css":
            match = _CSS_PROP.match(code, i)
            if match:
                spans.append((match.start(), match.end(), "keyword"))
                i = match.end()
                continue
        if language not in ("html",):
            match = _NUMBER.match(code, i)
            if match and (i == 0 or not (code[i - 1].isalnum() or code[i - 1] == "_")):
                spans.append((match.start(), match.end(), "number"))
                i = match.end()
                continue
        if ch.isalpha() or ch == "_":
            match = _WORD.match(code, i)
            word = match.group()
            kind = None
            if (word.lower() if ignore_case else word) in keywords:
                kind = "keyword"
            elif language not in ("json", "html", "css", "shell") and code.startswith("(", match.end()):
                kind = "function"
            if kind:
                spans.append((match.start(), match.end(), kind))
            i = match.end()
            continue
        i += 1
    return spans


def code_lines(attrs: dict[str, Any]) -> list[str]:
    return str(attrs.get("code", "")).replace("\r\n", "\n").replace("\r", "\n").split("\n")


def estimate_bounds(_session: Any, node: Any) -> list[float]:
    """Rough block size without Qt (headless scripts); the canvas knows better."""
    attrs = node.attrs
    size = float(attrs.get("font_size", 13) or 13)
    padding = float(attrs.get("padding", 12.0) or 0.0)
    lines = code_lines(attrs)
    gutter = (len(str(len(lines))) + 2) * size * 0.6 if attrs.get("line_numbers") else 0.0
    if attrs.get("wrap", True):
        width = float(attrs.get("width", DEFAULT_WIDTH) or DEFAULT_WIDTH)
        usable = max(size, width - 2 * padding - gutter)
        rows = sum(max(1, -(-int(len(line) * size * 0.6) // max(1, int(usable)))) for line in lines)
    else:
        width = max(len(line) for line in lines) * size * 0.6 + 2 * padding + gutter
        rows = len(lines)
    return [0.0, 0.0, max(width, 40.0), rows * size * 1.45 + 2 * padding]


CODE_TYPE = NodeType(
    name="annotation_code",
    inherits="canvas_node",
    label="Code block",
    capabilities=frozenset({CAP_CONTENT}),
    compute={"local_bounds": estimate_bounds},
    attrs=(
        Attr("code", "text", "", group="Code"),
        Attr("language", "enum", "python", choices=LANGUAGE_CHOICES, group="Code"),
        Attr("highlight", "bool", True, group="Code", label="Syntax highlight"),
        Attr("wrap", "bool", True, group="Code", label="Word wrap", doc="Wrap long lines at the block width"),
        Attr("line_numbers", "bool", False, group="Code"),
        Attr("theme", "enum", "dark", choices=THEMES, group="Code"),
        Attr("font_size", "float", 13.0, minimum=4.0, soft_min=8.0, soft_max=48.0, step=1.0, precision=0, unit="pt",
             group="Code"),
        Attr("width", "float", DEFAULT_WIDTH, minimum=60.0, soft_max=1600.0, step=10.0, precision=0, group="Box",
             doc="Block width when word wrap is on"),
        Attr("padding", "float", 12.0, minimum=0.0, soft_max=60.0, step=1.0, precision=0, group="Box"),
        Attr("corner_radius", "float", 6.0, minimum=0.0, soft_max=40.0, step=1.0, precision=0, group="Box"),
    ),
    quick_attrs=("language", "wrap", "line_numbers", "theme", "font_size"),
)


def activate(ctx: Any) -> None:
    ctx.register_node_type(CODE_TYPE)
    session = ctx.session
    scene = session.scene

    def code_names(nodes: Any) -> list[str]:
        names = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)]
        return [n for n in names if scene.get_node(n).node_type == "annotation_code"]

    def create_code(code: str = "", translate: Any = None, language: str = "python", **attrs: Any) -> str:
        """Create a code block at ``translate``; extra keyword arguments are attributes (wrap, theme…)."""
        if language not in LANGUAGES:
            raise CommandError(f"Unknown language '{language}' ({', '.join(LANGUAGES)})")
        x, y = (float(translate[0]), float(translate[1])) if translate is not None else (0.0, 0.0)
        name = scene.unique_name("Code")
        scene.create_node("annotation_code", name, attrs={
            "code": code, "language": language, "translate": [round(x, 3), round(y, 3)], **attrs})
        session.selection.set([name])
        return name

    def set_code_attrs(updates: dict[str, Any], nodes: Any = None, label: str = "Edit code") -> list[str]:
        names = code_names(nodes)
        if not names:
            raise CommandError("Select a code block")
        changed = []
        for name in names:
            attrs = scene.get_node(name).attrs
            change = {k: v for k, v in updates.items() if differs(attrs.get(k), v)}
            if change:
                scene.set_attrs(name, change)
                changed.append(name)
        return changed

    def set_code(name: str, code: str) -> None:
        set_code_attrs({"code": code}, name, "Edit code")

    def copy_code(nodes: Any = None) -> str:
        """Plain code of the selected blocks, for the clipboard (the UI puts it there)."""
        names = code_names(nodes)
        if not names:
            raise CommandError("Select a code block to copy")
        return "\n\n".join(str(scene.get_node(n).attrs.get("code", "")) for n in names)

    ctx.command("create", create_code, alias="create_code", label="Add code")
    ctx.command("set_attrs", set_code_attrs, label=lambda updates, nodes=None, label="Edit code": label)
    ctx.command("set_code", set_code, label="Edit code")
    ctx.command("get_code", copy_code, undoable=False, echo=False)


def activate_ui(ui: Any) -> None:
    from .ui import activate_code_ui

    activate_code_ui(ui)
