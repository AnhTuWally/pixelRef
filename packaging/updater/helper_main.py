"""The update helper: activates a staged update after pixelRef has exited.

    helper_main.py activate <transaction-id> [--wait-pid PID ...]
    helper_main.py recover  [--wait-pid PID ...]     restore the previous version after a failed update
    helper_main.py rollback [--wait-pid PID ...]     the user's rollback to the previous version

It runs from ``<install>/.pixelref-update/helper/<transaction-id>/`` (a copy outside the files it
replaces; never from the app's own Python or DLLs) and *derives* the install root from its own location.
There are no source, destination or executable arguments: the transaction id names a folder the app
staged, and everything else is re-read and re-verified from disk. Exit codes: 0 done, 2 refused or failed
(see the JSON line on stdout), 3 assisted installation is disabled in this build. ``recover`` only
undoes moves, so it works even while installation is disabled.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pixelref.core.updates import current_platform
from pixelref.core.updates import install, recovery
from pixelref.core.updates.install import InstallError
from pixelref.core.updates.verification import RELEASE_TRUST, VerificationError


def _parse_pids(rest: list[str]) -> list[int] | None:
    pids: list[int] = []
    while rest:
        if rest[0] != "--wait-pid" or len(rest) < 2 or not rest[1].isdigit():
            return None
        pids.append(int(rest[1]))
        rest = rest[2:]
    return pids


def main(argv: list[str]) -> int:
    usage = {"ok": False, "error": "usage: helper_main.py activate <transaction-id> | recover | rollback  [--wait-pid PID ...]"}
    command = argv[0] if argv else ""
    if command not in ("activate", "recover", "rollback") or (command == "activate" and len(argv) < 2):
        print(json.dumps(usage))
        return 2
    pids = _parse_pids(argv[2:] if command == "activate" else argv[1:])
    if pids is None:
        print(json.dumps({"ok": False, "error": "bad arguments"}))
        return 2
    if command != "recover" and not install.ASSISTED_INSTALL_ENABLED:
        print(json.dumps({"ok": False, "error": "assisted installation is disabled in this build"}))
        return 3
    try:
        here = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
        root = install.derive_install_root(here)
        if command == "activate":
            journal = install.run_activation(root, argv[1], trust=RELEASE_TRUST, platform=current_platform(), pids=pids)
        elif command == "rollback":
            journal = recovery.rollback_previous(root, platform=current_platform(), pids=pids)
        else:
            journal = recovery.recover(root, pids=pids)
    except (InstallError, VerificationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": journal is None or journal.state in ("COMMITTED", "ROLLED_BACK", "PENDING_HEALTH_CHECK"),
                      "state": journal.state if journal else "IDLE", "version": journal.new_version if journal else ""}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
