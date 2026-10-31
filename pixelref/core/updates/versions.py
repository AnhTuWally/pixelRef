"""Semantic version parsing and ordering (SemVer 2.0.0 precedence), never lexical string comparison."""

from __future__ import annotations

import re
from dataclasses import dataclass

_PATTERN = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class VersionError(ValueError):
    """Text that is not a semantic version."""


def _pre_key(identifiers: tuple[str, ...]) -> tuple:
    # Numeric identifiers sort below alphanumeric ones; a version without a pre-release sorts last.
    if not identifiers:
        return (1,)
    return (0, tuple((0, int(i), "") if i.isdigit() else (1, 0, i) for i in identifiers))


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> "Version":
        match = _PATTERN.match(text.strip()) if isinstance(text, str) else None
        if match is None:
            raise VersionError(f"Not a semantic version: {text!r}")
        pre = tuple(match["pre"].split(".")) if match["pre"] else ()
        if any(i.isdigit() and len(i) > 1 and i.startswith("0") for i in pre):
            raise VersionError(f"Not a semantic version: {text!r}")
        build = tuple(match["build"].split(".")) if match["build"] else ()
        return cls(int(match["major"]), int(match["minor"]), int(match["patch"]), pre, build)

    @property
    def is_prerelease(self) -> bool:
        return bool(self.pre)

    def _key(self) -> tuple:
        return (self.major, self.minor, self.patch, _pre_key(self.pre))

    def __lt__(self, other: "Version") -> bool:
        return self._key() < other._key()

    def __le__(self, other: "Version") -> bool:
        return self._key() <= other._key()

    def __gt__(self, other: "Version") -> bool:
        return self._key() > other._key()

    def __ge__(self, other: "Version") -> bool:
        return self._key() >= other._key()

    def same_precedence(self, other: "Version") -> bool:
        return self._key() == other._key()  # build metadata is ignored

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.pre:
            text += "-" + ".".join(self.pre)
        if self.build:
            text += "+" + ".".join(self.build)
        return text


def parse_version(text: str) -> Version:
    return Version.parse(text)


def try_parse_version(text: object) -> Version | None:
    try:
        return Version.parse(text)  # type: ignore[arg-type]
    except VersionError:
        return None
