"""The update helper: activates a staged update after pixelRef has exited.

    helper_main.py activate <transaction-id> [--wait-pid PID ...]

It runs from ``<install>/.pixelref-update/helper/<transaction-id>/`` (a copy outside the files it
replaces; never from the app's own Python or DLLs) and *derives* the install root from its own location.
There are no source, destination or executable arguments: the transaction id names a folder the app
staged, and everything else is re-read and re-verified from disk. Exit codes: 0 done, 2 refused or failed
(see the JSON line on stdout), 3 assisted installation is disabled in this build.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pixelref.core.updates import current_platform
from pixelref.core.updates import install
from pixelref.core.updates.install import InstallError
from pixelref.core.updates.verification import RELEASE_TRUST, VerificationError


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[0] != "activate":
        print(json.dumps({"ok": False, "error": "usage: helper_main.py activate <transaction-id> [--wait-pid PID ...]"}))
        return 2
    pids: list[int] = []
    rest = argv[2:]
    while rest:
        if rest[0] != "--wait-pid" or len(rest) < 2 or not rest[1].isdigit():
            print(json.dumps({"ok": False, "error": "bad arguments"}))
            return 2
        pids.append(int(rest[1]))
        rest = rest[2:]
    if not install.ASSISTED_INSTALL_ENABLED:
        print(json.dumps({"ok": False, "error": "assisted installation is disabled in this build"}))
        return 3
    try:
        here = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
        root = install.derive_install_root(here)
        journal = install.run_activation(root, argv[1], trust=RELEASE_TRUST, platform=current_platform(), pids=pids)
    except (InstallError, VerificationError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps({"ok": True, "state": journal.state, "version": journal.new_version}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
