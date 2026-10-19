"""Set up the virtual environment (if needed) and launch pixelRef.

    python launch_pixelRef.py [scene.pxref]

First run creates .venv next to this script and installs requirements.txt
into it. Later runs just detect the venv already has what it needs and
launch the GUI straight away. Any arguments are forwarded to
``python -m pixelref``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"

if sys.platform == "win32":
    VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
else:
    VENV_PYTHON = VENV_DIR / "bin" / "python"


def venv_python() -> Path:
    if not VENV_DIR.exists():
        print(f"Creating virtual environment in {VENV_DIR} ...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
    return VENV_PYTHON


def deps_satisfied(python: Path) -> bool:
    result = subprocess.run(
        [str(python), "-c", "import PySide6"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def install_deps(python: Path) -> None:
    print("Installing requirements ...")
    subprocess.run(
        [str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)], check=True
    )


def main() -> int:
    python = venv_python()
    if not deps_satisfied(python):
        install_deps(python)

    result = subprocess.run([str(python), "-m", "pixelref", *sys.argv[1:]], cwd=ROOT)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
