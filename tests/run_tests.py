"""Run every test without pytest: ``python tests/run_tests.py [name-filter ...]``.

(``python -m pytest tests`` works too.)
"""

from __future__ import annotations

import importlib
import os
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import harness  # noqa: E402,F401  (configures Qt before the test modules import it)


def main(argv: list[str]) -> int:
    filters = [a for a in argv if not a.startswith("-")]
    start_dir = os.getcwd()
    tests = []
    for path in sorted(HERE.glob("test_*.py")):
        module = importlib.import_module(path.stem)
        for name in sorted(vars(module)):
            fn = getattr(module, name)
            if name.startswith("test_") and callable(fn):
                if not filters or any(f in f"{path.stem}.{name}" for f in filters):
                    tests.append((f"{path.stem}.{name}", fn))
    failed = []
    began = time.perf_counter()
    for label, fn in tests:
        try:
            fn()
            print(f"PASS  {label}", flush=True)
        except Exception:  # noqa: BLE001
            failed.append(label)
            print(f"FAIL  {label}", flush=True)
            traceback.print_exc()
        finally:
            os.chdir(start_dir)
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed in {time.perf_counter() - began:.1f}s")
    for label in failed:
        print(f"  failed: {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
