"""``python -m pixelref`` starts the app; ``python -m pixelref --batch script.py`` runs a script headless.

``--run-pip …`` runs the bundled pip (the package manager uses it in the
standalone executable, which has no ``python -m pip``).
"""

import sys


if sys.version_info < (3, 11):
    raise SystemExit(
        f"pixelRef requires Python 3.11 or newer; the current interpreter is {sys.version.split()[0]}."
    )


def main() -> None:
    if sys.argv[1:2] == ["--run-pip"]:  # the package manager, in the standalone executable
        from .pip_runner import run_pip

        raise SystemExit(run_pip(sys.argv[2:]))
    if "--batch" in sys.argv[1:]:
        from .batch import run_batch

        args = [a for a in sys.argv[1:] if a != "--batch"]
        raise SystemExit(run_batch(args))
    from .ui import run

    raise SystemExit(run())


if __name__ == "__main__":
    main()
