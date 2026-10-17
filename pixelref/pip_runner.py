"""``pixelref --run-pip …``: run the bundled pip in its own process.

The standalone executable has no ``python -m pip``, so the package manager
starts the executable itself with ``--run-pip``; pip then runs on the exact
Python that pixelRef was built with and picks wheels that match it. pip is
never run inside the app's own process (a failing install can't take the
window down, and pip's internals aren't a public API).
"""

from __future__ import annotations

import runpy
import sys


def run_pip(args: list[str]) -> int:
    import os

    # A windowed executable has no stdout: pip must not crash writing to it.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115
    sys.argv = ["pip", *args]
    try:
        runpy.run_module("pip", run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    except ImportError:
        print("pip is not available in this build of pixelRef", file=sys.stderr)
        return 1
    return 0
