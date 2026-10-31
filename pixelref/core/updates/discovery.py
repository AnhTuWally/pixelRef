"""Release discovery from GitHub Releases. Discovery only: nothing here downloads or installs.

The network call is a plain GET of the public release list. It carries no user identity, document
name or board content, only a generic User-Agent. ``fetch`` is injectable so tests run offline.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
from typing import Any, Callable
from urllib.parse import urlparse

from .models import (Channel, DiscoveryResult, FetchResponse, InstallMode, Platform, Release, ReleaseAsset,
                     RELEASES_URL, UpdateState, current_platform, detect_install_mode)
from .versions import Version, try_parse_version

TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_NOTES_CHARS = 4000
ASSET_PATTERN = re.compile(r"^pixelRef-v(?P<version>[^/\\]+)-(?P<system>[a-z]+)-(?P<arch>[a-z0-9_]+)\.zip$")
ALLOWED_HOSTS = ("github.com", "api.github.com")

Fetcher = Callable[[str, float], FetchResponse]


def default_fetch(url: str, timeout: float = TIMEOUT_SECONDS) -> FetchResponse:
    """GET ``url`` (https only). Network errors raise OSError; HTTP errors return their status."""
    if urlparse(url).scheme != "https":
        raise OSError(f"Refusing non-HTTPS URL: {url}")
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "pixelRef-update-check"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310  (https enforced above)
            body = response.read(MAX_RESPONSE_BYTES + 1)
            return FetchResponse(response.status, body, {k.lower(): v for k, v in response.headers.items()})
    except urllib.error.HTTPError as exc:
        return FetchResponse(exc.code, b"", {k.lower(): v for k, v in (exc.headers or {}).items()})
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        raise OSError(str(exc)) from exc


def _safe_https(url: Any, hosts: tuple[str, ...] = ALLOWED_HOSTS) -> str:
    if not isinstance(url, str):
        return ""
    parsed = urlparse(url)
    return url if parsed.scheme == "https" and parsed.hostname in hosts else ""


def parse_releases(payload: Any) -> list[Release]:
    """Releases from the GitHub JSON list. Drafts and entries without a semantic version are skipped;
    a payload that isn't a list raises ValueError."""
    if not isinstance(payload, list):
        raise ValueError("release list is not a JSON array")
    releases: list[Release] = []
    for entry in payload:
        if not isinstance(entry, dict) or entry.get("draft") is True:
            continue
        version = try_parse_version(entry.get("tag_name"))
        page = _safe_https(entry.get("html_url"))
        if version is None or not page:
            continue
        assets = []
        for raw in entry.get("assets") or []:
            if not isinstance(raw, dict):
                continue
            url = _safe_https(raw.get("browser_download_url"), ALLOWED_HOSTS + ("objects.githubusercontent.com",))
            size = raw.get("size")
            if isinstance(raw.get("name"), str) and url and isinstance(size, int) and not isinstance(size, bool) and size >= 0:
                assets.append(ReleaseAsset(raw["name"], size, url))
        notes = entry.get("body") if isinstance(entry.get("body"), str) else ""
        releases.append(Release(str(entry["tag_name"]), version, bool(entry.get("prerelease")), page,
                                notes[:MAX_NOTES_CHARS], str(entry.get("published_at") or ""), tuple(assets)))
    return releases


def eligible(release: Release, channel: Channel) -> bool:
    """Stable users see only stable releases; beta users see stable and pre-releases."""
    return channel is Channel.BETA or release.channel is Channel.STABLE


def find_asset(release: Release, target: Platform) -> ReleaseAsset | None:
    """The portable archive for ``target``: its name must carry the release's own version."""
    for asset in release.assets:
        match = ASSET_PATTERN.match(asset.name)
        if (match and match["system"] == target.system and match["arch"] == target.arch
                and try_parse_version(match["version"]) is not None
                and try_parse_version(match["version"]).same_precedence(release.version)):
            return asset
    return None


def select_update(releases: list[Release], current: Version, channel: Channel, target: Platform
                  ) -> tuple[Release, ReleaseAsset | None] | None:
    """The newest eligible release newer than ``current`` that this platform can use, or None.

    A release that publishes archives, but none for ``target``, is skipped; one that publishes no
    archives at all is still offered (the user opens the release page)."""
    best: tuple[Release, ReleaseAsset | None] | None = None
    for release in releases:
        if not eligible(release, channel) or not release.version > current:
            continue
        asset = find_asset(release, target)
        if asset is None and any(ASSET_PATTERN.match(a.name) for a in release.assets):
            continue
        if best is None or release.version > best[0].version:
            best = (release, asset)
    return best


def check_for_updates(current: str | Version, channel: Channel = Channel.STABLE, *, fetch: Fetcher = default_fetch,
                      target: Platform | None = None, mode: InstallMode | None = None, url: str = RELEASES_URL,
                      timeout: float = TIMEOUT_SECONDS) -> DiscoveryResult:
    """Ask GitHub for releases. Never raises: every failure is a state, and none reads as 'up to date'."""
    mode = mode or detect_install_mode()
    current_version = current if isinstance(current, Version) else try_parse_version(current)
    if current_version is None:
        return DiscoveryResult(UpdateState.INVALID_METADATA, message=f"Installed version {current!r} is not valid.", mode=mode)
    target = target or current_platform()
    try:
        response = fetch(url, timeout)
    except OSError as exc:
        return DiscoveryResult(UpdateState.OFFLINE, current_version, message=f"Could not reach GitHub: {exc}", mode=mode)
    if response.status in (403, 429):
        remaining = response.headers.get("x-ratelimit-remaining")
        retry = response.headers.get("retry-after")
        if response.status == 429 or remaining == "0" or retry:
            return DiscoveryResult(UpdateState.RATE_LIMITED, current_version, message="GitHub's rate limit was reached.",
                                   retry_after=float(retry) if retry and retry.isdigit() else None, mode=mode)
    if response.status != 200:
        return DiscoveryResult(UpdateState.INVALID_METADATA, current_version,
                               message=f"GitHub answered HTTP {response.status}.", mode=mode)
    if len(response.body) > MAX_RESPONSE_BYTES:
        return DiscoveryResult(UpdateState.INVALID_METADATA, current_version, message="The release list is too large.", mode=mode)
    try:
        releases = parse_releases(json.loads(response.body.decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        return DiscoveryResult(UpdateState.INVALID_METADATA, current_version, message=f"Unreadable release list: {exc}", mode=mode)
    if target.system not in ("windows", "macos", "linux"):
        return DiscoveryResult(UpdateState.UNSUPPORTED_PLATFORM, current_version, message=f"Unsupported platform {target}.", mode=mode)
    found = select_update(releases, current_version, channel, target)
    if found is None:
        return DiscoveryResult(UpdateState.UP_TO_DATE, current_version, mode=mode)
    return DiscoveryResult(UpdateState.AVAILABLE, current_version, release=found[0], asset=found[1], mode=mode)
