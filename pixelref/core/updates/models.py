"""Qt-free update models: install mode, release assets, and the outcome of a discovery."""

from __future__ import annotations

import enum
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .versions import Version

REPOSITORY = "AnhTuWally/pixelRef"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases"


class UpdateState(enum.Enum):
    """What the UI shows. A failed check is never ``UP_TO_DATE``."""

    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    AVAILABLE = "available"
    OFFLINE = "offline"
    RATE_LIMITED = "rate_limited"
    INVALID_METADATA = "invalid_metadata"
    UNSUPPORTED_PLATFORM = "unsupported_platform"


class Channel(enum.Enum):
    STABLE = "stable"
    BETA = "beta"

    @classmethod
    def parse(cls, text: object) -> "Channel":
        return cls.BETA if str(text).strip().lower() == "beta" else cls.STABLE


class InstallMode(enum.Enum):
    """How this copy of pixelRef is running. Only ``STANDALONE`` may ever be updated in place (later
    milestones); ``SOURCE`` is discovery-only and its files are never rewritten."""

    STANDALONE = "standalone"
    SOURCE = "source"


@dataclass(frozen=True)
class Platform:
    system: str  # "windows" | "macos" | "linux" | other
    arch: str    # "x64" | "arm64" | other

    def __str__(self) -> str:
        return f"{self.system}-{self.arch}"


def current_platform() -> Platform:
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else sys.platform)
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "x64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    return Platform(system, arch)


def detect_install_mode() -> InstallMode:
    return InstallMode.STANDALONE if getattr(sys, "frozen", False) else InstallMode.SOURCE


def application_root() -> Path | None:
    """The folder holding the standalone executable, or None when running from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    size: int
    url: str


@dataclass(frozen=True)
class Release:
    tag: str
    version: Version
    prerelease: bool
    html_url: str
    notes: str = ""
    published_at: str = ""
    assets: tuple[ReleaseAsset, ...] = ()

    @property
    def channel(self) -> Channel:
        return Channel.BETA if self.prerelease or self.version.is_prerelease else Channel.STABLE


@dataclass(frozen=True)
class DiscoveryResult:
    state: UpdateState
    current: Version | None = None
    release: Release | None = None        # set when a newer eligible release exists
    asset: ReleaseAsset | None = None     # the portable archive for this platform, when published
    message: str = ""
    retry_after: float | None = None      # seconds, for RATE_LIMITED when the server said so
    mode: InstallMode = InstallMode.SOURCE

    @property
    def is_failure(self) -> bool:
        return self.state in (UpdateState.OFFLINE, UpdateState.RATE_LIMITED, UpdateState.INVALID_METADATA)

    @property
    def can_install(self) -> bool:
        """Discovery alone never installs (M1): always False until a verified-update pipeline exists."""
        return False


@dataclass
class FetchResponse:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
