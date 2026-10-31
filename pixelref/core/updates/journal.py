"""The durable transaction journal (``.pixelref-update/journal.json``) and its state machine.

The journal never stores a filesystem path: the staging and backup folders are derived from the
transaction id, and operations name single top-level entries of the installation. A forged or damaged
journal therefore cannot point recovery at an arbitrary path; it is also strictly validated on load.
Writes go through a temporary file, ``fsync`` and an atomic replace (plus a directory sync on POSIX).
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .versions import try_parse_version

SCHEMA_VERSION = 1
JOURNAL_NAME = "journal.json"
TRANSACTION_ID = re.compile(r"^[0-9a-f]{32}$")
NAME = re.compile(r"^[^/\\:\x00-\x1f<>\"|?*]{1,200}$")
STATES = ("IDLE", "DOWNLOADING", "VERIFIED", "STAGED", "ACTIVATING", "PENDING_HEALTH_CHECK", "COMMITTED",
          "ROLLING_BACK", "ROLLED_BACK", "RECOVERY_REQUIRED", "ROLLBACK_FAILED", "CANCELLED", "FAILED")
TERMINAL = frozenset({"IDLE", "COMMITTED", "ROLLED_BACK", "CANCELLED", "FAILED", "RECOVERY_REQUIRED", "ROLLBACK_FAILED"})
# Any pre-activation state may also go to CANCELLED or FAILED (nothing live was altered).
TRANSITIONS = {
    "IDLE": {"DOWNLOADING"},
    "DOWNLOADING": {"VERIFIED"},
    "VERIFIED": {"STAGED"},
    "STAGED": {"ACTIVATING"},
    "ACTIVATING": {"PENDING_HEALTH_CHECK", "ROLLING_BACK"},
    "PENDING_HEALTH_CHECK": {"COMMITTED", "ROLLING_BACK"},
    "ROLLING_BACK": {"ROLLED_BACK", "ROLLBACK_FAILED"},
    "ROLLBACK_FAILED": {"ROLLING_BACK", "RECOVERY_REQUIRED"},
    "COMMITTED": set(), "ROLLED_BACK": set(), "CANCELLED": set(), "FAILED": set(), "RECOVERY_REQUIRED": set(),
}
for _state in ("DOWNLOADING", "VERIFIED", "STAGED"):
    TRANSITIONS[_state] |= {"CANCELLED", "FAILED"}
ACTIONS = ("replace", "add", "remove")
OP_STATUS = ("pending", "moved_out", "in_place", "restored")


class JournalError(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Journal:
    transaction_id: str
    installation_id: str
    old_version: str
    new_version: str
    manifest_digest: str
    state: str = "STAGED"
    history: list[dict[str, str]] = field(default_factory=list)
    operations: list[dict[str, str]] = field(default_factory=list)   # {"name", "action", "status"}
    startup_attempts: int = 0
    startup_token: str = ""
    failure_reason: str = ""
    schema_version: int = SCHEMA_VERSION
    kind: str = "install"           # "install" (a staged update) or "rollback" (restore a backup)
    source_transaction: str = ""    # for a rollback: the transaction whose backup is restored
    sequence: int = 0               # the signed metadata sequence recorded when the update commits

    def __post_init__(self) -> None:
        if not self.history:
            self.history = [{"state": self.state, "at": _now()}]

    def transition(self, state: str, reason: str = "") -> None:
        if state not in TRANSITIONS.get(self.state, ()):
            raise JournalError(f"illegal transition {self.state} -> {state}")
        self.state = state
        self.history.append({"state": state, "at": _now()})
        if reason:
            self.failure_reason = reason[:500]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def validate(data: Any) -> Journal:
    """Strictly check a decoded journal; anything unexpected raises JournalError."""
    keys = {"schema_version", "transaction_id", "installation_id", "old_version", "new_version", "manifest_digest",
            "state", "history", "operations", "startup_attempts", "startup_token", "failure_reason", "kind",
            "source_transaction", "sequence"}
    try:
        if not isinstance(data, dict) or set(data) != keys or data["schema_version"] != SCHEMA_VERSION:
            raise ValueError("unexpected fields")
        if not TRANSACTION_ID.match(data["transaction_id"]) or not TRANSACTION_ID.match(data["installation_id"]):
            raise ValueError("bad id")
        if data["state"] not in STATES or not isinstance(data["manifest_digest"], str) or len(data["manifest_digest"]) != 64:
            raise ValueError("bad state or digest")
        if try_parse_version(data["old_version"]) is None or try_parse_version(data["new_version"]) is None:
            raise ValueError("bad version")
        if (not isinstance(data["startup_attempts"], int) or isinstance(data["startup_attempts"], bool)
                or not 0 <= data["startup_attempts"] <= 100):
            raise ValueError("bad attempts")
        if not isinstance(data["startup_token"], str) or len(data["startup_token"]) > 64 or not isinstance(data["failure_reason"], str):
            raise ValueError("bad text")
        if data["kind"] not in ("install", "rollback"):
            raise ValueError("bad kind")
        if (data["source_transaction"] == "") != (data["kind"] == "install") or (
                data["source_transaction"] and not TRANSACTION_ID.match(str(data["source_transaction"]))):
            raise ValueError("bad source transaction")
        if isinstance(data["sequence"], bool) or not isinstance(data["sequence"], int) or not 0 <= data["sequence"] <= 2**53:
            raise ValueError("bad sequence")
        history = data["history"]
        if (not isinstance(history, list) or not 0 < len(history) <= 200
                or any(not isinstance(h, dict) or set(h) != {"state", "at"} or h["state"] not in STATES
                       or not isinstance(h["at"], str) for h in history)):
            raise ValueError("bad history")
        operations = data["operations"]
        seen: set[str] = set()
        if not isinstance(operations, list) or len(operations) > 500:
            raise ValueError("bad operations")
        for op in operations:
            if (not isinstance(op, dict) or set(op) != {"name", "action", "status"} or not isinstance(op["name"], str)
                    or not NAME.match(op["name"]) or op["name"] in (".", "..") or op["name"].casefold() in seen
                    or op["action"] not in ACTIONS or op["status"] not in OP_STATUS):
                raise ValueError(f"bad operation {op!r}")
            seen.add(op["name"].casefold())
    except (ValueError, KeyError, TypeError) as exc:
        raise JournalError(f"invalid journal: {exc}") from exc
    return Journal(**data)


def journal_path(update_dir: Path) -> Path:
    return Path(update_dir) / JOURNAL_NAME


def write_journal(update_dir: Path, journal: Journal) -> None:
    """Durably replace the journal: temp file, fsync, atomic replace, directory sync."""
    validate(journal.to_dict())
    update_dir = Path(update_dir)
    temp = update_dir / f"journal.{uuid.uuid4().hex}.tmp"
    with temp.open("wb") as handle:
        handle.write(json.dumps(journal.to_dict(), indent=1).encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, journal_path(update_dir))
    if os.name == "posix":
        fd = os.open(update_dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def read_journal(update_dir: Path) -> Journal | None:
    path = journal_path(update_dir)
    if not path.exists():
        return None
    try:
        return validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise JournalError(f"unreadable journal: {exc}") from exc


def installation_id(update_dir: Path) -> str:
    path = Path(update_dir) / "installation-id"
    try:
        value = path.read_text(encoding="ascii").strip()
        if TRANSACTION_ID.match(value):
            return value
    except OSError:
        pass
    value = uuid.uuid4().hex
    path.write_text(value, encoding="ascii")
    return value


@contextlib.contextmanager
def journal_guard(update_dir: Path, timeout: float = 5.0, stale: float = 30.0, sleep=time.sleep):
    """Serialize read-modify-write of the journal between the helper and the app (which commits it)."""
    path = Path(update_dir) / "journal.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > stale:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            if time.monotonic() >= deadline:
                raise JournalError("the update journal is busy") from None
            sleep(0.02)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
