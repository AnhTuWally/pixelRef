"""Update discovery (M1): version ordering, release selection, error states, schedule. All offline."""

from __future__ import annotations

import json

from pixelref.core.settings import MemorySettings
from pixelref.core.updates import (Channel, InstallMode, Platform, UpdatePreferences, UpdateState, parse_releases,
                                   parse_version, check_for_updates, try_parse_version)
from pixelref.core.updates.models import FetchResponse

WIN = Platform("windows", "x64")


def release(tag: str, prerelease: bool = False, assets=None, draft: bool = False) -> dict:
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft, "body": "notes",
            "html_url": f"https://github.com/AnhTuWally/pixelRef/releases/tag/{tag}",
            "assets": [{"name": n, "size": 10, "browser_download_url": f"https://github.com/x/{n}"}
                       for n in (assets if assets is not None else [f"pixelRef-{tag}-windows-x64.zip"])]}


def fetcher(payload, status=200, headers=None):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return lambda url, timeout: FetchResponse(status, body, headers or {})


def check(payload, current="1.1.0", channel=Channel.STABLE, **kw):
    return check_for_updates(current, channel, fetch=fetcher(payload, **kw), target=WIN, mode=InstallMode.SOURCE)


def test_version_ordering_is_not_lexical():
    v = parse_version
    assert v("1.10.0") > v("1.9.0") and v("v2.0.0") > v("1.99.99")
    assert v("1.0.0-beta.2") < v("1.0.0-beta.11") < v("1.0.0-rc.1") < v("1.0.0")
    assert v("1.0.0-alpha") < v("1.0.0-alpha.1") < v("1.0.0-alpha.beta")
    assert v("1.0.0+a").same_precedence(v("1.0.0+b"))
    assert all(try_parse_version(x) is None for x in ("1.0", "latest", "1.0.0-01", "", None, "01.0.0"))


def test_newer_stable_is_offered_and_older_is_not():
    result = check([release("v1.2.0"), release("v1.1.0"), release("v1.0.0")])
    assert result.state is UpdateState.AVAILABLE and str(result.release.version) == "1.2.0"
    assert result.asset.name == "pixelRef-v1.2.0-windows-x64.zip" and not result.can_install
    assert check([release("v1.1.0"), release("v1.0.0")]).state is UpdateState.UP_TO_DATE


def test_channels_and_drafts():
    payload = [release("v1.3.0-beta.1", True), release("v1.2.0"), release("v9.0.0", draft=True)]
    assert str(check(payload).release.version) == "1.2.0"
    assert str(check(payload, channel=Channel.BETA).release.version) == "1.3.0-beta.1"
    assert check([release("v1.2.0-beta.1", True)], current="1.2.0", channel=Channel.BETA).state is UpdateState.UP_TO_DATE
    assert check([release("v1.2.0")], current="1.2.0-beta.3", channel=Channel.STABLE).state is UpdateState.AVAILABLE


def test_platform_filtering():
    other = [release("v1.2.0", assets=["pixelRef-v1.2.0-linux-x64.zip"]), release("v1.1.5")]
    assert str(check(other).release.version) == "1.1.5"  # 1.2.0 ships archives, none for Windows
    bare = check([release("v1.2.0", assets=[])])
    assert bare.state is UpdateState.AVAILABLE and bare.asset is None  # open the release page instead
    mismatch = check([release("v1.2.0", assets=["pixelRef-v9.9.9-windows-x64.zip"])])
    assert mismatch.state is UpdateState.UP_TO_DATE  # asset version must match the release
    result = check_for_updates("1.1.0", fetch=fetcher([release("v1.2.0")]), target=Platform("plan9", "x64"))
    assert result.state is UpdateState.UNSUPPORTED_PLATFORM


def test_failures_never_read_as_up_to_date():
    def offline(url, timeout):
        raise OSError("no route")

    assert check_for_updates("1.1.0", fetch=offline, target=WIN).state is UpdateState.OFFLINE
    limited = check([], status=403, headers={"x-ratelimit-remaining": "0"})
    assert limited.state is UpdateState.RATE_LIMITED and limited.is_failure
    assert check([], status=429, headers={"retry-after": "120"}).retry_after == 120
    for bad in (b"not json", b'{"a": 1}', b"\xff\xfe"):
        assert check(bad).state is UpdateState.INVALID_METADATA
    assert check([], status=500).state is UpdateState.INVALID_METADATA
    assert check([], status=404).state is UpdateState.INVALID_METADATA
    assert check([release("v1.2.0")], current="garbage").state is UpdateState.INVALID_METADATA
    assert check([release("v1.2.0")]).state is UpdateState.AVAILABLE


def test_hostile_release_entries_are_ignored():
    entry = release("v1.2.0")
    entry["html_url"] = "http://evil.example/x"
    assert parse_releases([entry, "junk", None, {"tag_name": "nightly"}]) == []
    entry = release("v1.2.0")
    entry["assets"].append({"name": "pixelRef-v1.2.0-windows-x64.zip", "size": -1, "browser_download_url": "https://evil.example/a"})
    assert len(parse_releases([entry])[0].assets) == 1


def test_schedule_and_preferences():
    now = [1_000_000.0]
    prefs = UpdatePreferences(MemorySettings(), clock=lambda: now[0], jitter=lambda a, b: 0.0)
    assert prefs.check_enabled and not prefs.auto_download and prefs.channel is Channel.STABLE
    assert prefs.check_due()                       # never checked
    prefs.record_attempt(True)
    assert not prefs.check_due()
    now[0] += 23 * 3600
    assert not prefs.check_due()
    now[0] += 2 * 3600
    assert prefs.check_due()
    prefs.record_attempt(False)                    # failed attempt: back off for an hour, success time unchanged
    assert not prefs.check_due() and prefs.last_successful_check == 1_000_000.0
    now[0] += 3601
    assert prefs.check_due()
    prefs.check_enabled = False
    assert not prefs.check_due()                   # manual checks don't consult check_due
    prefs.channel = Channel.BETA
    assert prefs.channel is Channel.BETA


# ---- The UI plugin ----------------------------------------------------------------------------

def _wait(h, cond, ms=3000):
    import time

    from harness import APP

    end = time.time() + ms / 1000
    while not cond() and time.time() < end:
        APP.processEvents()
        time.sleep(0.01)
    return cond()


def test_ui_indicator_only_when_update_exists():
    from harness import Harness

    h = Harness()
    try:
        service = h.service("updates")
        block = h.app.bar_block("updateIndicator")
        assert service is not None and block is not None and block.isHidden()
        service.fetch = fetcher([release("v99.0.0", assets=[])])
        assert h.app.run_command("updates.check") == "checking"
        assert _wait(h, lambda: service.state is UpdateState.AVAILABLE)
        assert not block.isHidden() and "99.0.0" in block.text()
        assert not service.result.can_install
        assert not h.service("updates") is None
        service.fetch = fetcher([release("v1.0.0")])
        service.check()
        assert _wait(h, lambda: service.state is UpdateState.UP_TO_DATE)
        assert block.isHidden()
    finally:
        h.close()


def test_ui_failed_check_is_not_up_to_date():
    from harness import Harness

    h = Harness()
    try:
        service = h.service("updates")

        def offline(url, timeout):
            raise OSError("down")

        service.fetch = offline
        service.check(manual=True)
        assert _wait(h, lambda: service.state is not UpdateState.CHECKING)
        assert service.state is UpdateState.OFFLINE
        assert h.app.bar_block("updateIndicator").isHidden()
    finally:
        h.close()
