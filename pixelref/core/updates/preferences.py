"""Update preferences and the check schedule, stored through the app's ``Settings`` (QSettings in the
UI). These are conveniences only: never authoritative security or transaction state."""

from __future__ import annotations

import random
import time

from ..settings import Settings
from .models import Channel

CHECK_ENABLED = "updates/check_enabled"
CHECK_INTERVAL = "updates/check_interval"      # hours
CHANNEL = "updates/channel"
AUTO_DOWNLOAD = "updates/auto_download"
LAST_SUCCESSFUL_CHECK = "updates/last_successful_check"  # epoch seconds
LAST_ATTEMPT = "updates/last_attempt"
DISMISSED_VERSION = "updates/dismissed_version"

DEFAULT_INTERVAL_HOURS = 24
MIN_INTERVAL_HOURS = 1
RETRY_AFTER_FAILURE_SECONDS = 3600


class UpdatePreferences:
    def __init__(self, settings: Settings, *, clock=time.time, jitter=random.uniform) -> None:
        self._s = settings
        self._clock = clock
        self._jitter = jitter

    @property
    def check_enabled(self) -> bool:
        return self._s.value(CHECK_ENABLED, True, type=bool)

    @check_enabled.setter
    def check_enabled(self, value: bool) -> None:
        self._s.setValue(CHECK_ENABLED, bool(value))

    @property
    def interval_hours(self) -> int:
        return max(MIN_INTERVAL_HOURS, self._s.value(CHECK_INTERVAL, DEFAULT_INTERVAL_HOURS, type=int) or DEFAULT_INTERVAL_HOURS)

    @interval_hours.setter
    def interval_hours(self, value: int) -> None:
        self._s.setValue(CHECK_INTERVAL, max(MIN_INTERVAL_HOURS, int(value)))

    @property
    def channel(self) -> Channel:
        return Channel.parse(self._s.value(CHANNEL, "stable", type=str))

    @channel.setter
    def channel(self, value: Channel) -> None:
        self._s.setValue(CHANNEL, value.value)

    @property
    def auto_download(self) -> bool:
        return self._s.value(AUTO_DOWNLOAD, False, type=bool)

    @auto_download.setter
    def auto_download(self, value: bool) -> None:
        self._s.setValue(AUTO_DOWNLOAD, bool(value))

    @property
    def last_successful_check(self) -> float | None:
        value = self._s.value(LAST_SUCCESSFUL_CHECK, 0.0, type=float)
        return value or None

    @property
    def dismissed_version(self) -> str:
        return self._s.value(DISMISSED_VERSION, "", type=str)

    @dismissed_version.setter
    def dismissed_version(self, value: str) -> None:
        self._s.setValue(DISMISSED_VERSION, value)

    def record_attempt(self, success: bool) -> None:
        """Attempts and successes are cached separately, so a failure never reads as a fresh check."""
        now = self._clock()
        self._s.setValue(LAST_ATTEMPT, now)
        if success:
            self._s.setValue(LAST_SUCCESSFUL_CHECK, now)

    def check_due(self) -> bool:
        """Whether a periodic check should run now: enabled, the last success is older than the interval
        (plus up to 10% jitter, so installs don't all call at once), and a failed attempt isn't recent."""
        if not self.check_enabled:
            return False
        now = self._clock()
        attempt = self._s.value(LAST_ATTEMPT, 0.0, type=float)
        success = self._s.value(LAST_SUCCESSFUL_CHECK, 0.0, type=float)
        if attempt > success and now - attempt < RETRY_AFTER_FAILURE_SECONDS:
            return False
        interval = self.interval_hours * 3600
        return now - success >= interval * (1 + self._jitter(0.0, 0.1))
