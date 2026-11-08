"""Numbered image sequences on disk: patterns, frame paths, scanning and grouping.

Pure Python (no Qt), so headless code and tests use it as is.

A sequence is stored as a *pattern*: the file name with its frame number
replaced by one ``#`` per digit of padding, ``shot_v003.####.exr`` for
``shot_v003.1001.exr`` … ``shot_v003.1096.exr``. Unpadded numbering
(``frame8.png``, ``frame9.png``, ``frame10.png``) is a single ``#``. Other
spellings are accepted on input and turned into this one: ``%04d``, ``%d``,
``@@@@`` and Houdini's ``$F4``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

_NUMBERED = re.compile(r"^(?P<prefix>.*?)(?P<digits>\d+)(?P<suffix>\.[A-Za-z0-9]+)$")
_HASHES = re.compile(r"#+")
_PRINTF = re.compile(r"%0?(\d*)d")
_ATS = re.compile(r"@+")
_HOUDINI = re.compile(r"\$F(\d?)")


@dataclass(frozen=True)
class Numbered:
    """A file name split around its frame number."""

    prefix: str
    frame: int
    digits: str
    suffix: str


def parse(name: str) -> Numbered | None:
    """``shot_v003.1001.exr`` -> ("shot_v003.", 1001, "1001", ".exr"): the last run of digits
    right before the extension; None when there is none."""
    match = _NUMBERED.match(name)
    if match is None:
        return None
    digits = match.group("digits")
    return Numbered(match.group("prefix"), int(digits), digits, match.group("suffix"))


def is_pattern(text: str) -> bool:
    name = Path(text).name
    return bool(_HASHES.search(name) or _PRINTF.search(name) or _ATS.search(name) or _HOUDINI.search(name))


def normalize(pattern: str) -> str:
    """Any accepted spelling of a pattern as ``#`` padding (only the file name is changed)."""
    path = Path(pattern)
    name = path.name
    name = _PRINTF.sub(lambda m: "#" * max(1, int(m.group(1) or 1)), name)
    name = _HOUDINI.sub(lambda m: "#" * max(1, int(m.group(1) or 1)), name)
    name = _ATS.sub(lambda m: "#" * len(m.group(0)), name)
    return str(path.with_name(name)) if str(path.parent) not in ("", ".") else name


def split(pattern: str) -> tuple[str, int, str]:
    """(directory and prefix, padding, suffix) of a ``#`` pattern; the last run of ``#`` is the frame."""
    pattern = normalize(pattern)
    runs = list(_HASHES.finditer(pattern))
    if not runs:
        raise ValueError(f"Not a sequence pattern (no #): {pattern}")
    last = runs[-1]
    return pattern[:last.start()], len(last.group(0)), pattern[last.end():]


def pattern_for(path: str | Path, padding: int | None = None) -> str:
    """The pattern of the sequence a numbered file belongs to (``shot.1001.exr`` -> ``shot.####.exr``)."""
    path = Path(path)
    numbered = parse(path.name)
    if numbered is None:
        raise ValueError(f"Not a numbered file: {path.name}")
    pad = padding if padding is not None else _padding_of(numbered.digits)
    return str(path.with_name(f"{numbered.prefix}{'#' * pad}{numbered.suffix}"))


def _padding_of(digits: str) -> int:
    """A frame number's padding: its width when it has leading zeros or is 4+ digits, else none."""
    return len(digits) if digits.startswith("0") or len(digits) >= 4 else 1


def frame_path(pattern: str, frame: int) -> str:
    head, pad, tail = split(pattern)
    number = f"-{abs(int(frame)):0{pad}d}" if frame < 0 else f"{int(frame):0{pad}d}"
    return f"{head}{number}{tail}"


def fits(digits: str, pad: int) -> bool:
    """Whether a frame number written as ``digits`` belongs to a sequence with padding ``pad``."""
    if pad <= 1:
        return digits == "0" or not digits.startswith("0")
    return len(digits) == pad or (len(digits) > pad and not digits.startswith("0"))


@dataclass
class SeqInfo:
    """The frames of a sequence found on disk."""

    pattern: str
    frames: list[int] = field(default_factory=list)  # sorted

    @property
    def first(self) -> int | None:
        return self.frames[0] if self.frames else None

    @property
    def last(self) -> int | None:
        return self.frames[-1] if self.frames else None

    def missing(self, first: int | None = None, last: int | None = None) -> list[int]:
        """Frame numbers between ``first`` and ``last`` (default: the range on disk) with no file."""
        if not self.frames:
            return []
        low = self.first if first is None else first
        high = self.last if last is None else last
        present = set(self.frames)
        return [n for n in range(low, high + 1) if n not in present]


def scan(pattern: str) -> SeqInfo:
    """The frames on disk of a ``#`` pattern (an absolute path)."""
    head, pad, tail = split(pattern)
    folder, prefix = os.path.split(head)
    frames: list[int] = []
    try:
        names = os.listdir(folder or ".")
    except OSError:
        return SeqInfo(normalize(pattern), [])
    low_prefix, low_tail = prefix.lower(), tail.lower()
    for name in names:
        low = name.lower()
        if not (low.startswith(low_prefix) and low.endswith(low_tail)) or len(name) <= len(prefix) + len(tail):
            continue
        digits = name[len(prefix):len(name) - len(tail)]
        if digits.isdigit() and fits(digits, pad):
            frames.append(int(digits))
    return SeqInfo(normalize(pattern), sorted(set(frames)))


@dataclass
class Group:
    """Files of one sequence among a set of paths."""

    pattern: str
    frames: list[int]
    paths: list[Path]


def group(paths: list[Path], minimum: int = 2) -> tuple[list[Group], list[Path]]:
    """Split ``paths`` into sequences (``minimum`` frames or more) and single files, in their order."""
    buckets: dict[tuple[str, str, str], list[tuple[Path, Numbered]]] = {}
    singles: list[Path] = []
    for path in paths:
        numbered = parse(path.name)
        if numbered is None:
            singles.append(path)
            continue
        key = (os.path.normcase(str(path.parent)), numbered.prefix, numbered.suffix.lower())
        buckets.setdefault(key, []).append((path, numbered))
    groups: list[Group] = []
    for members in buckets.values():
        for pad, chosen in _by_padding(members).items():
            if len(chosen) < minimum:
                singles.extend(p for p, _n in chosen)
                continue
            chosen.sort(key=lambda m: m[1].frame)
            first_path = chosen[0][0]
            groups.append(Group(pattern_for(first_path, pad), [n.frame for _p, n in chosen], [p for p, _n in chosen]))
    order = {str(p): i for i, p in enumerate(paths)}
    groups.sort(key=lambda g: min(order[str(p)] for p in g.paths))
    singles.sort(key=lambda p: order[str(p)])
    return groups, singles


def _by_padding(members: list[tuple[Path, Numbered]]) -> dict[int, list[tuple[Path, Numbered]]]:
    """Members split by padding: zero-led numbers fix a width; the others join the widest one
    they fit, else form an unpadded sequence (or a 4+ digit one: ``1001``…)."""
    widths = sorted({len(n.digits) for _p, n in members if n.digits.startswith("0") and len(n.digits) > 1})
    out: dict[int, list[tuple[Path, Numbered]]] = {}
    for path, numbered in members:
        digits = numbered.digits
        if digits.startswith("0") and len(digits) > 1:
            pad = len(digits)
        else:
            fitting = [w for w in widths if len(digits) >= w]
            pad = fitting[-1] if fitting else 1
        out.setdefault(pad, []).append((path, numbered))
    if 1 in out and not widths:
        lengths = {len(n.digits) for _p, n in out[1]}
        if len(lengths) == 1 and min(lengths) >= 4:  # 1001 … 1096: write it as ####
            out[min(lengths)] = out.pop(1)
    return out
