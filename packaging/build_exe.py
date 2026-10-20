"""Build the standalone pixelRef folder and check that it really works.

    python packaging/build_exe.py              build dist/pixelRef/, smoke-test it, zip it for release
    python packaging/build_exe.py --test-only  smoke-test (and zip) an existing build

Needs PyInstaller (``pip install -r packaging/requirements-build.txt``).

The smoke test runs the *built* executables, not this Python, because the
things most likely to break only break when frozen:

* the built-in plugins are found and activate (they're imported by name);
* the bundled pip runs (``pixelref-cli --run-pip``);
* a package installs into pixelRef's folder and imports (offline, from a
  wheel the test builds), the way the Plugins window does it;
* standard library modules pixelRef itself never imports are there for
  packages installed later.
"""

from __future__ import annotations

import base64
import hashlib
import os
import platform
import re
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "pixelRef"
EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""


def build() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("PyInstaller is missing: pip install -r packaging/requirements-build.txt") from None
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"),
                    str(ROOT / "packaging" / "pixelref.spec")], check=True, cwd=ROOT)


def package() -> Path:
    """Zip dist/pixelRef/ as dist/pixelRef-v<version>-<os>-<arch>.zip, ready for a GitHub release."""
    version = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "pixelref" / "__init__.py").read_text(encoding="utf-8"))[1]
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
    target = DIST.parent / f"pixelRef-v{version}-{system}-{arch}.zip"
    target.unlink(missing_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(DIST, DIST.name)
        for path in sorted(DIST.rglob("*")):
            archive.write(path, path.relative_to(DIST.parent).as_posix() + ("/" if path.is_dir() else ""))
    print(f"Release zip: {target} ({target.stat().st_size / 1e6:.0f} MB)")
    return target


def make_wheel(folder: Path, name: str, version: str, code: str) -> Path:
    module = name.replace("-", "_")
    info = f"{module}-{version}.dist-info"
    files = {
        f"{module}/__init__.py": code,
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: pixelref-build\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = []
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(text.encode())}")
    files[f"{info}/RECORD"] = "\n".join(record + [f"{info}/RECORD,,"]) + "\n"
    wheel = folder / f"{module}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return wheel


def smoke_test() -> None:
    cli = DIST / f"pixelref-cli{EXE_SUFFIX}"
    app = DIST / f"pixelRef{EXE_SUFFIX}"
    for exe in (cli, app):
        if not exe.is_file():
            raise SystemExit(f"Missing {exe}: build first")
    work = Path(tempfile.mkdtemp(prefix="pixelref-smoke-"))
    env = dict(os.environ, PIXELREF_PACKAGES_DIR=str(work / "packages"), PIP_NO_INDEX="1",
               QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8")
    env.pop("PYTHONPATH", None)

    def run(*args: str) -> str:
        result = subprocess.run([str(cli), *args], capture_output=True, text=True, env=env, cwd=work,
                                encoding="utf-8", errors="replace", timeout=600)
        if result.returncode != 0:
            raise SystemExit(f"FAILED: pixelref-cli {' '.join(args)}\n{result.stdout}\n{result.stderr}")
        return result.stdout

    print("1/4 bundled pip:", run("--run-pip", "--version").strip())

    wheel = make_wheel(work, "pixelref-smoke", "1.0.0", "MESSAGE = 'installed and imported'\n")
    script = work / "smoke.py"
    script.write_text(textwrap.dedent(f"""
        import importlib, sys
        active = [p["id"] for p in pxcmd.list_plugins() if p["status"] == "active"]
        assert len(active) > 20, active
        print("plugins active:", len(active))
        result = pxcmd.packages.install_file({str(wheel)!r})
        assert result["ok"], result
        import pixelref_smoke
        print("package:", pixelref_smoke.MESSAGE, "from", pixelref_smoke.__file__)
        for name in ("ctypes", "unittest", "xml.dom.minidom", "sqlite3", "concurrent.futures",
                     "multiprocessing", "email.mime.text", "decimal", "csv", "zoneinfo"):
            importlib.import_module(name)
        print("stdlib: ok")
        print("frozen:", getattr(sys, "frozen", False))
    """), encoding="utf-8")
    print("2/4 batch mode + package install:")
    print(textwrap.indent(run("--batch", str(script)).strip(), "    "))

    # The real UI kernel (offscreen): catches Qt libraries the spec left out by mistake.
    ui_script = work / "smoke_ui.py"
    ui_script.write_text(textwrap.dedent(f"""
        from pathlib import Path
        from PySide6.QtGui import QColor, QImage, QImageReader
        from PySide6.QtNetwork import QNetworkAccessManager  # images dragged in from browsers
        from pixelref.ui import create_app

        formats = {{bytes(f).decode() for f in QImageReader.supportedImageFormats()}}
        missing = {{"png", "jpg", "gif", "webp", "tiff", "bmp", "ico", "svg"}} - formats
        assert not missing, missing
        png = Path({str(work)!r}) / "red.png"
        image = QImage(40, 30, QImage.Format_ARGB32)
        image.fill(QColor(200, 0, 0))
        assert image.save(str(png))
        app = create_app([], persist_layout=False, load_user_files=False)
        app.session.run("import_image", str(png))
        app.canvas.refresh(force=True)
        rendered = app.canvas.render_items(app.canvas.content_items())
        assert rendered.pixelColor(10, 10) == QColor(200, 0, 0), rendered.pixelColor(10, 10)
        QNetworkAccessManager()
        from pixelref.builtins.help import guide_path
        assert guide_path() is not None, "Getting Started board (onboard.pxref) is not bundled"
        print("ui: canvas renders, image formats:", ", ".join(sorted(formats)))
    """), encoding="utf-8")
    print("3/4 UI kernel (offscreen):")
    print(textwrap.indent(run("--batch", str(ui_script)).strip(), "    "))

    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"4/4 build size: {size / 1e6:.0f} MB in {DIST}")
    print("Smoke test passed.")


def main(argv: list[str]) -> int:
    if "--test-only" not in argv:
        build()
    smoke_test()
    package()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
