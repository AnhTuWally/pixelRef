"""Application update support (Qt-free). M1 discovery, M2 release verification. Nothing in this package modifies the
installation: see docs/update-architecture.md."""

from .discovery import check_for_updates, default_fetch, parse_releases, select_update
from .models import (Channel, DiscoveryResult, InstallMode, Platform, Release, ReleaseAsset, UpdateState,
                     application_root, current_platform, detect_install_mode)
from .preferences import UpdatePreferences
from .versions import Version, VersionError, parse_version, try_parse_version

__all__ = [
    "Channel", "DiscoveryResult", "InstallMode", "Platform", "Release", "ReleaseAsset", "UpdatePreferences",
    "UpdateState", "Version", "VersionError", "application_root", "check_for_updates", "current_platform",
    "default_fetch", "detect_install_mode", "parse_releases", "parse_version", "select_update", "try_parse_version",
]
