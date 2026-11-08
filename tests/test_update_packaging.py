"""The update helper's build wiring and independence, checked statically (the real build is build_exe.py)."""

from __future__ import annotations

import ast
import re

from harness import ROOT

UPDATES = ROOT / "pixelref" / "core" / "updates"
FORBIDDEN = ("PySide6", "shiboken6", "pip", "pixelref.ui", "pixelref.builtins", "pixelref.plugin_api")


def imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append(("." * node.level) + (node.module or ""))
    return found


def test_the_helper_and_the_update_code_it_imports_stay_small_and_qt_free():
    files = [ROOT / "packaging" / "updater" / "helper_main.py", *sorted(UPDATES.glob("*.py"))]
    flow = UPDATES / "flow.py"
    for path in files:
        for name in imports(path):
            assert not name.startswith(FORBIDDEN), f"{path.name} imports {name}: the helper must stay Qt-free and independent"
    # Modules outside core/updates that the helper reaches: only the light shared ones.
    reached = {n for p in files for n in imports(p) if n.startswith("..") and not n.startswith("...")}
    assert reached <= {"..settings", "..paths", "..", "..errors"} | {".." + x for x in ("settings", "paths")}, reached
    assert flow.exists()


def test_the_specs_and_the_build_wire_the_helper_into_the_app():
    helper_spec = (ROOT / "packaging" / "updater" / "helper.spec").read_text(encoding="utf-8")
    assert 'name="pixelref-updater"' in helper_spec and "PySide6" in helper_spec.split("EXCLUDES")[1]
    assert "helper_main.py" in helper_spec
    app_spec = (ROOT / "packaging" / "pixelref.spec").read_text(encoding="utf-8")
    assert 'ROOT / "dist" / "pixelref-updater"' in app_spec and 'Path("updater")' in app_spec
    build = (ROOT / "packaging" / "build_exe.py").read_text(encoding="utf-8")
    first, second = build.index("updater/helper.spec"), build.index('"pixelref.spec"')
    assert first < second, "the helper must be built before the app bundles it"
    assert "smoke_test_helper(work, env)" in build and 'DIST / "_internal" / "updater"' in build
    from pixelref.core.updates import flow

    assert flow.HELPER_BUNDLE_NAME == "updater" and flow.HELPER_EXE["windows"] == "pixelref-updater.exe"
    assert re.search(r'name="pixelref-updater"', helper_spec)  # the name flow.HELPER_EXE expects (plus .exe)
