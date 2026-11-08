"""Fully portable mode (PR-01): opt-in, relative to the application folder, never the working directory."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from harness import tmp_dir
from pixelref.core import paths, packages


@contextmanager
def app_folder(marker: str | None = None, name: str = "pixelRef"):
    """Pretend ``<tmp>/<name>`` is the standalone application folder (optionally with a portable.toml)."""
    root = tmp_dir() / name
    root.mkdir()
    if marker is not None:
        (root / "portable.toml").write_text(marker, encoding="utf-8")
    paths.set_application_root_for_tests(root)
    try:
        yield root
    finally:
        paths.set_application_root_for_tests(None)


ENABLED = "[portable]\nenabled = true\n"


def inside(path: Path, root: Path) -> bool:
    return Path(path).resolve().is_relative_to(root.resolve())


def test_default_is_not_portable():
    assert paths.portable() is None and paths.problem() == ""  # a source copy or no marker
    with app_folder() as root:
        assert paths.portable() is None
    with app_folder("[portable]\nenabled = false\n"):
        assert paths.portable() is None and paths.problem() == ""
    with app_folder("# only a comment\n") as root:
        assert paths.portable() is not None  # the marker's presence means portable unless disabled
        assert paths.portable().data == root / "user-data"


def test_marker_parsing_and_validation():
    with app_folder(ENABLED + 'data_directory = "my data/stuff"\n') as root:
        locations = paths.portable()
        assert locations.data == root / "my data" / "stuff" and locations.config == locations.data / "config"
        assert locations.settings_file.name == "pixelRef.ini" and locations.boards.name == "boards"
    bad = ['data_directory = "/etc"', 'data_directory = "../outside"', 'data_directory = "a/../../b"',
           'data_directory = "C:\\\\Users\\\\me"', 'data_directory = "\\\\\\\\server\\\\share"', 'data_directory = "."',
           'data_directory = ""', "data_directory = 5", 'data_directory = "_internal"', 'data_directory = ".pixelref-update"',
           'data_directory = "PixelRef.exe"', 'data_directory = "portable.toml"', 'data_directory = "d:data"',
           'data_directory = "release-inventory.json/x"', "enabled = \"yes\""]
    for line in bad:
        with app_folder("[portable]\n" + ("" if line.startswith("enabled") else "enabled = true\n") + line + "\n"):
            assert paths.portable() is None, line
            assert "portable.toml is not usable" in paths.problem(), line
    with app_folder("[portable\nbroken"):
        assert paths.portable() is None and "not usable" in paths.problem()
    with app_folder("portable = 3\n"):
        assert paths.portable() is None and "not usable" in paths.problem()


def test_locations_follow_the_application_folder_not_the_working_directory():
    with app_folder(ENABLED) as root:
        elsewhere = tmp_dir()
        before = Path.cwd()
        os.chdir(elsewhere)
        try:
            locations = paths.portable()
            assert locations.root == root and inside(locations.data, root) and not inside(locations.data, elsewhere)
        finally:
            os.chdir(before)
        assert list(elsewhere.iterdir()) == []


def test_every_provider_uses_the_portable_folder_when_enabled():
    from pixelref.builtins.effects.ui import cache_folder
    from pixelref.builtins.file import RecoveryService as Recovery, save_dir
    from pixelref.core.settings import MemorySettings
    from pixelref.ui import _data_dir, _user_plugin_dir
    from pixelref.ui.app import App

    saved = os.environ.pop("PIXELREF_PACKAGES_DIR", None)  # the tests' override would win otherwise
    try:
        with app_folder(ENABLED) as root:
            data = root / "user-data"
            assert App.config_dir() == data / "config"
            assert App.data_dir() == data / "data"
            assert _data_dir() == data / "data" and _user_plugin_dir() == data / "data" / "plugins"
            assert Recovery.recovery_dir() == data / "data" / "recovery"
            assert cache_folder() == data / "cache" / "effects"
            assert packages.default_packages_root() == data / "python"
            assert save_dir(MemorySettings()) == data / "boards" and (data / "boards").is_dir()
            custom = tmp_dir()
            chosen = MemorySettings()
            chosen.setValue("save_dir", str(custom))
            assert save_dir(chosen) == custom  # an explicit choice by the user is respected
        with app_folder() as root:  # not portable: nothing moves
            for path in (App.config_dir(), App.data_dir(), _data_dir(), Recovery.recovery_dir(), cache_folder()):
                assert not inside(path, root), path
    finally:
        if saved is not None:
            os.environ["PIXELREF_PACKAGES_DIR"] = saved


def test_settings_live_in_the_portable_folder_only():
    from PySide6.QtCore import QSettings

    from pixelref.ui.settings import QtSettings

    plain = QtSettings()
    plain.setValue("portable_test/outside", "yes")
    plain.sync()
    with app_folder(ENABLED) as root:
        settings = QtSettings()
        assert not settings.contains("portable_test/outside")  # a fresh, separate store
        settings.setValue("portable_test/inside", "mine")
        settings.sync()
        ini = root / "user-data" / "settings" / "pixelRef.ini"
        assert ini.is_file() and "inside=mine" in ini.read_text(encoding="utf-8")
        assert QtSettings().value("portable_test/inside") == "mine"
    assert not QtSettings().contains("portable_test/inside")  # never leaked into the per-user settings
    assert plain.value("portable_test/outside") == "yes"
    plain.remove("portable_test")
    plain.sync()


def test_unwritable_portable_settings_fall_back_instead_of_failing():
    from pixelref.ui.settings import QtSettings

    with app_folder(ENABLED) as root:
        (root / "user-data").write_text("a file where the folder should be")  # cannot create user-data/settings
        settings = QtSettings()
        settings.setValue("portable_test/fallback", "ok")  # the app still starts and works
        assert settings.value("portable_test/fallback") == "ok"
        settings.remove("portable_test")


def test_the_updater_and_portable_mode_share_one_application_root():
    from pixelref.core.updates import application_root

    assert application_root is paths.application_root and paths.application_root() is None  # source copy


def test_portable_files_are_never_part_of_a_release_or_touched_by_an_update():
    import test_update_install as ti
    from pixelref.core.updates.inventory import build_inventory

    root = ti.make_install()
    assert (root / "portable.toml").is_file() and (root / "user-data" / "settings.ini").is_file()
    inventory = build_inventory(root, "1.1.0")
    assert not any(f["path"].startswith(("user-data", "portable.toml")) for f in inventory["files"])
    before = {k: v for k, v in ti.tree(root).items() if k.startswith("user-data") or k == "portable.toml"}
    ti.run(root, ti.stage(root))
    after = {k: v for k, v in ti.tree(root).items() if k.startswith("user-data") or k == "portable.toml"}
    assert before == after and before
