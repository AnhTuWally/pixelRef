"""Kernel groundwork for effects and the Python package manager.

* node type extensions (sparse attributes added by plugins to types they don't own)
* paint filters and ``canvas.item_hooks`` (any node type, children untouched)
* plugin Python requirements, the "unavailable" state and the managed package
  folder (installs run real pip, offline, from wheels built by the test)
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

from harness import APP, Harness, make_wheel, tmp_dir


def headless(folder: Path | None = None, packages_root: Path | None = None, **kwargs):
    from pixelref.core.packages import PackageManager
    from pixelref.core.session import Session

    paths = [folder] if folder is not None else []
    packages = PackageManager(root=packages_root or tmp_dir())
    return Session(plugin_paths=paths, packages=packages, make_current=False, **kwargs).start()


def write_plugin(folder: Path, plugin_id: str, manifest: str = "", code: str = "def activate(ctx):\n    pass\n"
                 ) -> Path:
    plugin = folder / plugin_id
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "plugin.toml").write_text(f'id = "{plugin_id}"\nname = "{plugin_id}"\n{manifest}\n', encoding="utf-8")
    (plugin / "__init__.py").write_text(code, encoding="utf-8")
    return plugin


@contextlib.contextmanager
def offline():
    """pip never reaches the internet: only wheel files and --find-links folders."""
    old = os.environ.get("PIP_NO_INDEX")
    os.environ["PIP_NO_INDEX"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("PIP_NO_INDEX", None)
        else:
            os.environ["PIP_NO_INDEX"] = old


def forget_module(name: str) -> None:
    for key in [k for k in sys.modules if k == name or k.startswith(name + ".")]:
        del sys.modules[key]


def close(session) -> None:
    """Shut down and take the session's package folder off sys.path (the next
    test must not find this test's packages)."""
    site_dir = str(session.packages.site)
    session.shutdown()
    sys.path[:] = [p for p in sys.path if p != site_dir]
    import importlib

    importlib.invalidate_caches()


# ---- Node type extensions --------------------------------------------------------------------------


EXTENDER = '''
from pixelref.plugin_api import Attr, SceneError


def check(value):
    for entry in value:
        if not isinstance(entry, dict) or "type" not in entry:
            raise SceneError("every effect needs a type")
    return value


def activate(ctx):
    ctx.extend_node_type("canvas_node", (Attr("fx_stack", "any", [], hidden=True, validator=check),))
'''


def test_extension_attrs_are_sparse_inherited_and_validated():
    from pixelref.core.errors import SceneError

    folder = tmp_dir()
    write_plugin(folder, "extender", code=EXTENDER)
    session = headless(folder)
    pxcmd = session.commands.proxy()
    try:
        assert session.plugins.get("extender").active, session.plugins.get("extender").error
        pxcmd.create_node("annotation_rect", "Box")
        pxcmd.create_node("annotation_text", "Note")
        # Inherited by every canvas type, but not stored until set (files don't change).
        assert "fx_stack" in session.types.attrs("image") and "fx_stack" in session.types.attrs("annotation_rect")
        assert "fx_stack" not in session.scene.nodes["Box"].attrs
        assert pxcmd.get_attr("Box.fx_stack") == []
        assert '"fx_stack"' not in session.dumps()
        pxcmd.set_attr("Box.fx_stack", [{"type": "grayscale"}])
        assert session.scene.nodes["Box"].attrs["fx_stack"] == [{"type": "grayscale"}]
        try:
            pxcmd.set_attr("Note.fx_stack", [{"strength": 1}])
        except SceneError:
            pass
        else:
            raise AssertionError("the extension's validator rejects bad values")
        assert "fx_stack" not in session.scene.nodes["Note"].attrs
        pxcmd.undo()
        assert "fx_stack" not in session.scene.nodes["Box"].attrs, "undo restores the unset state"
        pxcmd.redo()
        assert pxcmd.get_attr("Box.fx_stack") == [{"type": "grayscale"}]
    finally:
        close(session)


def test_extension_values_survive_while_their_plugin_is_off():
    folder = tmp_dir()
    write_plugin(folder, "extender", code=EXTENDER)
    session = headless(folder)
    pxcmd = session.commands.proxy()
    path = tmp_dir() / "board.pxref"
    try:
        pxcmd.create_node("annotation_rect", "Box")
        pxcmd.set_attr("Box.fx_stack", [{"type": "invert"}])
        pxcmd.unload_plugin("extender")
        assert "fx_stack" not in session.types.attrs("annotation_rect")
        pxcmd.save_scene(str(path))
        pxcmd.open_scene(str(path))
        node = session.scene.nodes["Box"]
        assert node.attrs["fx_stack"] == [{"type": "invert"}], "kept while nobody declares it"
        assert "fx_stack" in node.dynamic
        pxcmd.load_plugin("extender")
        assert session.scene.attr_spec(node, "fx_stack").validator is not None, "the extension's spec wins again"
        pxcmd.save_scene(str(path))
        saved = json.loads(path.read_text(encoding="utf-8"))
        box = next(n for n in saved["nodes"] if n["name"] == "Box")
        assert box["attrs"]["fx_stack"] == [{"type": "invert"}] and "fx_stack" not in box.get("dynamic", {})
    finally:
        session.settings.remove("plugins/disabled")
        session.settings.remove("plugins/enabled")
        close(session)


def test_extension_attr_names_cannot_clash():
    from pixelref.core.attributes import Attr
    from pixelref.core.errors import SceneError
    from pixelref.core.nodetypes import TypeExtension

    session = headless()
    try:
        types = session.types
        for target, name in (("canvas_node", "opacity"), ("canvas_node", "size"), ("image", "translate")):
            try:  # an attribute of the type, of its ancestors, or of a subtype ("size" on rects)
                types.extend(TypeExtension(target, (Attr(name, "float", 0.0),)))
            except SceneError:
                continue
            raise AssertionError(f"extending {target} with {name} must fail")
        first = types.extend(TypeExtension("canvas_node", (Attr("glow", "float", 0.0),)))
        try:
            types.extend(TypeExtension("canvas_node", (Attr("glow", "float", 1.0),)))
        except SceneError:
            pass
        else:
            raise AssertionError("two plugins can't add the same attribute")
        later = types.extend(TypeExtension("not_registered_yet", (Attr("x_extra", "int", 0),)))
        first.dispose()
        later.dispose()
        assert "glow" not in types.attrs("image")
    finally:
        close(session)


# ---- Paint filters and item hooks --------------------------------------------------------------------


def test_paint_filters_reach_any_node_type_but_not_child_items():
    from PySide6.QtGui import QColor, QImage, QPainter

    h = Harness()
    try:
        h.add_image("Img", 0, 0, w=100, h=60)
        h.create("annotation_rect", "Mark", parent="Img", translate=[10, 10], size=[30, 20],
                 fill_color=[0, 200, 0], fill_opacity=1.0, stroke_color=[0, 200, 0])
        h.create("annotation_rect", "Box", translate=[300, 0], size=[40, 30])
        seen = []

        def blue_out(item, painter, option, widget, draw):
            # Draw the content offscreen, then show a flat copy: what an effect does.
            rect = item.boundingRect()
            image = QImage(max(1, int(rect.width())), max(1, int(rect.height())), QImage.Format_ARGB32_Premultiplied)
            image.fill(0)
            offscreen = QPainter(image)
            offscreen.translate(-rect.x(), -rect.y())
            draw(offscreen)
            offscreen.end()
            image.fill(QColor(0, 0, 255))
            painter.drawImage(rect.topLeft(), image)

        def hook(item, node):
            seen.append((node["name"], type(item).__name__))
            if node["name"] in ("Img", "Box"):
                item.add_paint_filter(blue_out)

        disposable = h.session.extensions.contribute("canvas.item_hooks", hook)
        h.refresh()
        names = {name for name, _type in seen}
        assert {"Img", "Mark", "Box"} <= names, seen
        assert h.item("Img").paint_filters and not h.item("Mark").paint_filters
        image = h.app.canvas.render_items([h.item("Img")])
        assert image.pixelColor(5, 5) == QColor(0, 0, 255), "the image's content went through the filter"
        assert image.pixelColor(25, 20) == QColor(0, 200, 0), "its annotation is drawn as it is"
        box = h.app.canvas.render_items([h.item("Box")])
        assert box.pixelColor(box.width() // 2, box.height() // 2) == QColor(0, 0, 255), "any node type"
        disposable.dispose()
        h.refresh()
        assert not h.item("Img").paint_filters, "items are rebuilt without the hook"
        plain = h.app.canvas.render_items([h.item("Img")])
        assert plain.pixelColor(5, 5) != QColor(0, 0, 255)
    finally:
        h.close()


def test_text_editing_frame_is_a_decoration_outside_the_filters():
    h = Harness()
    try:
        h.create("annotation_text", "Note", translate=[0, 0], text="Hello")
        item = h.item("Note")
        calls = []
        item.add_paint_filter(lambda it, painter, option, widget, draw: calls.append("filter") or draw(painter))
        original = item.paint_decorations
        item.paint_decorations = lambda *a: calls.append("decorations") or original(*a)
        h.app.canvas.render_items([item])
        assert calls[:2] == ["filter", "decorations"], calls
    finally:
        h.close()


# ---- Python requirements of plugins ------------------------------------------------------------------


def test_requirements_parse_and_compare_without_importing():
    from pixelref.core.errors import PluginError
    from pixelref.core.packages import PackageManager, Requirement, parse_version

    req = Requirement.parse("NumPy>=1.26,<3")
    assert req.key == "numpy" and req.allows("2.1.0") and not req.allows("1.25.9") and not req.allows("3.0")
    assert Requirement.parse("pkg~=1.4.2").allows("1.4.9") and not Requirement.parse("pkg~=1.4.2").allows("1.5")
    assert Requirement.parse("pkg==2.*").allows("2.7.1") and not Requirement.parse("pkg!=2.*").allows("2.0")
    table = Requirement.parse({"name": "opencv-python", "import": "cv2", "version": ">=4.8"})
    assert table.module == "cv2" and str(table) == "opencv-python>=4.8"
    assert parse_version("2.1.0rc1") == (2, 1, 0) and parse_version("v3") == (3,)
    for bad in ("", ">=1.0", "pkg >= one two", 42):
        try:
            Requirement.parse(bad)
        except PluginError:
            continue
        raise AssertionError(f"{bad!r} is not a requirement")
    store = PackageManager(root=tmp_dir())
    status = store.status(Requirement.parse("PySide6>=6"))
    assert status.ok and status.location == "app"
    assert not store.status(Requirement.parse("pixelref-no-such-package")).ok
    assert "needs Python package pixelref-no-such-package>=1" in \
        store.status(Requirement.parse("pixelref-no-such-package>=1")).problem


def test_plugins_waiting_for_packages_are_unavailable_and_so_are_their_dependents():
    folder = tmp_dir()
    write_plugin(folder, "fx_numpy", '[python]\nrequires = ["pxt-missing>=1.0"]')
    write_plugin(folder, "fx_extra", 'requires = ["fx_numpy"]')
    write_plugin(folder, "fx_optional", '[python]\noptional = ["pxt-missing"]')
    write_plugin(folder, "fx_bad", '[python]\nrequires = [">=nonsense"]')
    session = headless(folder)
    try:
        plugins = session.plugins
        numpy_plugin = plugins.get("fx_numpy")
        assert numpy_plugin.state == "unavailable" and numpy_plugin.error == "needs Python package pxt-missing>=1.0"
        assert numpy_plugin.module is None, "the plugin's code isn't even imported"
        extra = plugins.get("fx_extra")
        assert extra.state == "unavailable" and extra.missing_packages == ["pxt-missing>=1.0"]
        assert "requires 'fx_numpy'" in extra.error
        assert plugins.get("fx_optional").active, "optional packages don't block a plugin"
        assert plugins.get("fx_bad").broken
        info = {p["id"]: p for p in session.run("list_plugins")}
        assert info["fx_numpy"]["enabled"] and info["fx_numpy"]["missing_packages"] == ["pxt-missing>=1.0"]
        assert session.run("packages.needed") == {"pxt-missing>=1.0": ["fx_numpy"]}
        session.run("unload_plugin", "fx_numpy")
        assert "pxt-missing>=1.0" not in session.run("packages.needed"), "only enabled plugins wait"
    finally:
        session.settings.remove("plugins/disabled")
        session.settings.remove("plugins/enabled")
        close(session)


USES_PACKAGE = '''
def activate(ctx):
    def answer():
        import pxt_alpha  # imported when used, never at startup

        return pxt_alpha.VALUE

    ctx.command("answer", answer, undoable=False)
'''


def test_installing_a_wheel_brings_plugins_back_and_removing_it_switches_them_off():
    folder = tmp_dir()
    write_plugin(folder, "uses_alpha", '[python]\nrequires = ["pxt-alpha>=1.0"]', code=USES_PACKAGE)
    write_plugin(folder, "after_alpha", 'requires = ["uses_alpha"]')
    wheel = make_wheel(tmp_dir(), "pxt-alpha", "1.2.0", code="VALUE = 42\n")
    session = headless(folder)
    try:
        assert session.plugins.get("uses_alpha").state == "unavailable"
        with offline():
            result = session.run("packages.install_file", str(wheel))
        assert result["ok"] and not result["restart"], result
        assert session.plugins.get("uses_alpha").active, session.plugins.get("uses_alpha").error
        assert session.plugins.get("after_alpha").active, "dependents come back too"
        listed = {p["name"]: p for p in session.run("packages.list")}
        assert listed["pxt-alpha"]["version"] == "1.2.0" and listed["pxt-alpha"]["chosen"]
        assert listed["pxt-alpha"]["needed_by"] == ["uses_alpha"]
        assert (session.packages.wheels / wheel.name).is_file(), "kept for rebuilds"
        # Not imported yet: removing it is immediate.
        result = session.run("packages.remove", "pxt-alpha")
        assert result["ok"] and not result["restart"], result
        assert session.plugins.get("uses_alpha").state == "unavailable"
        assert session.plugins.get("after_alpha").state == "unavailable"
        assert session.packages.installed() == {} and session.packages.wanted() == {}
    finally:
        forget_module("pxt_alpha")
        close(session)


def test_changing_a_loaded_package_waits_for_the_next_start():
    root = tmp_dir()
    folder = tmp_dir()
    write_plugin(folder, "uses_alpha", '[python]\nrequires = ["pxt-alpha>=1.0"]', code=USES_PACKAGE)
    wheels = tmp_dir()
    make_wheel(wheels, "pxt-alpha", "1.0.0", code="VALUE = 1\n")
    session = headless(folder, packages_root=root)
    try:
        session.packages.extra_find_links.append(wheels)  # like a plugin's bundled wheels/ folder
        with offline():
            assert session.run("packages.install_missing")["ok"]
        assert session.run("uses_alpha.answer") == 1, "the package was imported from pixelRef's folder"
        make_wheel(wheels, "pxt-alpha", "2.0.0", code="VALUE = 2\n")
        with offline():
            result = session.run("packages.update")
        assert result["ok"] and result["restart"], "a loaded module can't be swapped out"
        assert session.packages.restart_needed
        assert session.packages.installed() == {"pxt-alpha": "1.0.0"}, "the running app keeps the old one"
    finally:
        close(session)
        forget_module("pxt_alpha")
    # "Restart": a new process would start with an empty sys.modules.
    from pixelref.core.packages import PackageManager

    restarted = PackageManager(root=root)
    sys.path[:] = [p for p in sys.path if p != str(restarted.site)]
    restarted.activate()
    try:
        assert not restarted.restart_needed and restarted.installed() == {"pxt-alpha": "2.0.0"}
        import pxt_alpha

        assert pxt_alpha.VALUE == 2
    finally:
        forget_module("pxt_alpha")
        sys.path[:] = [p for p in sys.path if p != str(restarted.site)]


def test_bad_installs_explain_themselves_and_change_nothing():
    from pixelref.core.errors import CommandError

    session = headless()
    try:
        for args in (("packages.install", "PySide6"), ("packages.install", "pip"),
                     ("packages.remove", "never-installed"), ("packages.install_file", "nope.whl")):
            try:
                session.run(*args)
            except CommandError:
                continue
            raise AssertionError(f"{args} must fail")
        with offline():
            try:
                session.run("packages.install", "pxt-not-anywhere")
            except CommandError as exc:
                assert "no version" in str(exc), exc
            else:
                raise AssertionError("an unknown package can't install")
        assert session.packages.wanted() == {} and session.packages.installed() == {}
        assert not list(session.packages.root.glob("install-*")), "scratch folders are cleaned up"
    finally:
        close(session)


def test_a_new_python_version_adopts_the_wish_list():
    from pixelref.core.packages import PackageManager, WantedPackage

    root = tmp_dir()
    old = PackageManager(root=root, tag="py312-win-amd64")
    old.save_wanted({"pxt-alpha": WantedPackage("pxt-alpha>=1", plugins=["uses_alpha"]),
                     "local": WantedPackage("local", file="local-1.0-cp312-win_amd64.whl")})
    new = PackageManager(root=root, tag="py313-win-amd64")
    assert new.adopt_other_versions() == ["pxt-alpha>=1"], "wheels built for the old Python are left behind"
    assert set(new.wanted()) == {"pxt-alpha"} and new.adopt_other_versions() == []


# ---- The Plugins window --------------------------------------------------------------------------------


def test_plugins_window_installs_what_plugins_need_in_the_background():
    from PySide6.QtCore import Qt

    from pixelref.ui.package_jobs import package_runner
    from pixelref.ui.plugin_manager import PluginManagerDialog

    folder = tmp_dir()
    plugin = write_plugin(folder, "uses_beta", '[python]\nrequires = ["pxt-beta"]')
    make_wheel(plugin / "wheels", "pxt-beta", "0.3.0")  # a plugin shipping its own wheels: works offline
    h = Harness(plugin_paths=(folder,))
    dialog = PluginManagerDialog(h.app, h.window)
    try:
        assert h.session.plugins.get("uses_beta").state == "unavailable"
        row = next(dialog.tree.topLevelItem(i) for i in range(dialog.tree.topLevelItemCount())
                   if dialog.tree.topLevelItem(i).data(0, Qt.UserRole) == "uses_beta")
        assert row.text(1) == "unavailable" and "pxt-beta" in row.text(3)
        assert row.checkState(0) == Qt.Checked, "switched on: it runs once its package is there"
        assert not dialog.install_needed_button.isHidden() and "pxt-beta" in dialog.needed_label.text()
        with offline():
            dialog.install_needed_button.click()
            runner = package_runner(h.app)
            assert runner.busy and not dialog.progress.isHidden()
            assert not dialog.update_all_button.isEnabled(), "one job at a time"
            runner.wait()
            APP.processEvents()
        assert runner.last_result["ok"], runner.last_result
        assert h.session.plugins.get("uses_beta").active
        assert dialog.install_needed_button.isHidden() and dialog.progress.isHidden()
        assert "pxt-beta" in dialog.log.toPlainText(), "pip's log is kept for 'Details'"
        assert dialog.packages_tree.topLevelItemCount() == 1
        package = dialog.packages_tree.topLevelItem(0)
        assert (package.text(0), package.text(1), package.text(2)) == ("pxt-beta", "0.3.0", "uses_beta")
        package.setSelected(True)
        assert dialog.remove_button.isEnabled()
        dialog.remove_button.click()
        runner.wait()
        APP.processEvents()
        assert h.session.plugins.get("uses_beta").state == "unavailable"
        assert dialog.packages_tree.topLevelItemCount() == 0
    finally:
        dialog.reject()
        site_dir = str(h.session.packages.site)
        h.close()
        sys.path[:] = [p for p in sys.path if p != site_dir]
