"""Scene thumbnails and the start panel with recent files."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt

from harness import APP, make, tmp_dir


def test_saving_stores_a_thumbnail_near_the_top_of_the_file():
    from pixelref.plugin_api import read_thumbnail

    h = make()
    try:
        h.add_image("Wide", 0, 0, 800, 200)
        folder = tmp_dir()
        path = folder / "thumb.pxref"
        assert h.save_to(path)
        text = path.read_text(encoding="utf-8")
        assert text.index('"thumbnail"') < text.index('"nodes"'), "readable without parsing the images"
        section = read_thumbnail(path)
        assert section["mime"] == "image/jpeg" and section["width"] == 320 and section["height"] == 80, section
        info = h.run("file.thumbnail", str(path), save_to=str(folder / "thumb.png"))
        assert info["width"] == 320 and "data" not in info
        assert (folder / "thumb.png").exists()
        # Reopening and saving again keeps exactly one thumbnail, updated.
        h.open(path)
        h.add_image("More", 0, 400, 100, 100)
        assert h.save_to(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["thumbnail"]["height"] > 80
        # An empty board saves without one.
        h.run("delete", ["Wide", "More"])
        assert h.save_to(path)
        assert read_thumbnail(path) is None
    finally:
        h.close()


def test_start_panel_shows_recent_files_with_thumbnails_and_opens_them():
    h = make()
    try:
        panels = h.service("start_panel").panels
        panel = panels["Main"]
        h.add_image("Img", 0, 0, 300, 200)
        h.refresh()
        assert not panel.isVisible(), "hidden while the board has content"
        path = tmp_dir() / "recent_one.pxref"
        assert h.save_to(path)
        h.run("file.new")
        h.refresh()
        APP.processEvents()
        panel = panels["Main"]  # a new scene builds new views (and their panels)
        assert panel.isVisible() and h.view.welcome_widget is panel
        cards = {card.path: card for card in panel.cards}
        key = str(path.resolve())
        assert key in cards, list(cards)
        card = cards[key]
        assert card.name.text() == "recent_one" and card.thumb.pixmap() is not None
        assert str(path.parent.name) in card.folder.text() or "…" in card.folder.text()
        assert not h.grab().isNull()
        from PySide6.QtTest import QTest

        QTest.mouseClick(card, Qt.LeftButton)
        APP.processEvents()
        assert h.has("Img"), "clicking a card opens the file"
        h.refresh()
        assert not panels["Main"].isVisible()
    finally:
        h.close()


def test_clicking_the_start_panel_leaves_no_button_held_on_the_canvas():
    """A click on a recent card (or the panel) must not reach the canvas: the canvas
    would see the press but not the release, and act as if the button stayed down."""
    from PySide6.QtTest import QTest

    h = make()
    try:
        h.add_image("Img", 0, 0, 300, 200)
        h.refresh()
        path = tmp_dir() / "held.pxref"
        assert h.save_to(path)
        h.run("file.new")
        h.refresh()
        APP.processEvents()
        panel = h.service("start_panel").panels["Main"]
        tool = h.app.tools.active
        QTest.mouseClick(panel, Qt.LeftButton, Qt.NoModifier, panel.rect().topLeft() + panel.rect().center() / 8)
        APP.processEvents()
        assert tool.op is None and not h.app.input._capture, "the panel's background swallows clicks"
        card = panel.cards[0]
        QTest.mouseClick(card, Qt.LeftButton)
        APP.processEvents()
        assert h.has("Img"), "the card opened the file"
        assert h.app.tools.active.op is None, "no rubber band or drag left running"
        assert not h.app.input._capture, "no handler still holds the mouse"
    finally:
        h.close()


def test_start_panel_hides_in_other_workspaces_once_the_board_has_content():
    h = make()
    try:
        panels = h.service("start_panel").panels
        review = [t for t, view in h.app.views.items()  # Present hides overlays, so no panel there
                  if h.app.wm.doc.workspace_of(t) != h.app.wm.focused_workspace() and view.display.show_overlays]
        assert review, "the Review workspace has canvas views"
        assert all(not panels[t].isHidden() for t in review), "an empty board shows the panel everywhere"
        h.add_image("Img", 0, 0, 300, 200)
        h.refresh()
        APP.processEvents()
        assert all(panels[t].isHidden() for t in panels), "every view's panel goes, shown or not"
        h.app.run_command("wm.workspace", "Review")
        APP.processEvents()
        assert all(not panels[t].isVisible() for t in review)
        h.run("delete", ["Img"])
        h.refresh()
        APP.processEvents()
        visible = [t for t in review if panels[t].isVisible()]
        assert visible, "and they come back on an empty board"
        panel = panels[visible[0]]
        viewport = h.app.views[visible[0]].viewport().rect()
        assert abs(panel.geometry().center().x() - viewport.center().x()) <= 2, "centred in its view"
    finally:
        h.close()


def _assert_fits(h, name):
    panel = h.service("start_panel").panels[name]
    viewport = h.app.views[name].viewport().rect()
    assert panel.size() == panel.sizeHint(), (name, panel.size(), panel.sizeHint())
    assert panel.title.height() >= panel.title.sizeHint().height(), "the title isn't squashed"
    for card in panel.cards:
        assert card.height() >= card.sizeHint().height(), "cards aren't cropped"
    assert viewport.contains(panel.geometry()) or panel.height() > viewport.height() - 16, (viewport, panel.geometry())
    return panel


def test_start_panel_is_never_clipped_in_other_workspaces_or_small_views():
    h = make()
    try:
        folder = tmp_dir()
        for index in range(4):
            h.run("file.new")
            h.add_image(f"Img{index}", 0, 0, 300, 200)
            assert h.save_to(folder / f"recent_{index}.pxref")
        h.run("file.new")
        h.refresh()
        APP.processEvents()
        shown = len(_assert_fits(h, "Main").cards)
        assert shown >= 4
        h.app.run_command("wm.workspace", "Review")
        APP.processEvents()
        for name in ("ReviewColor", "ReviewValue"):
            _assert_fits(h, name)
        # A short window: fewer rows of cards, never a cropped panel.
        h.app.run_command("wm.workspace", "Board")
        h.window.resize(1400, 420)
        APP.processEvents()
        panel = _assert_fits(h, "Main")
        viewport = h.view.viewport().rect()
        assert panel.height() <= viewport.height() - 16, (panel.height(), viewport.height())
        assert len(panel.cards) < shown
    finally:
        h.close()


def test_the_version_is_shown_on_the_start_panel_and_in_about():
    import pixelref

    h = make()
    try:
        assert h.run("help.about")["pixelref"] == pixelref.__version__
        assert APP.applicationVersion() in ("", pixelref.__version__)
        panel = h.service("start_panel").panels["Main"]
        assert panel.version.text() == f"pixelRef {pixelref.__version__}"
        h.run("help.show_about")
    finally:
        h.close()


def test_getting_started_opens_once_on_first_launch_and_again_from_help():
    from PySide6.QtCore import QSettings

    h = make()
    try:
        onboarding = h.session.services.get("onboarding")
        assert onboarding is not None
        # The first launch (no recent boards, flag unset) opens the guide, as an untitled clean copy.
        h.run("file.clear_recent")
        QSettings().remove("onboarding_shown")
        assert onboarding.show_first_run() is True
        assert h.session.scene.has("Welcome (card)")
        # Static content is locked; the sandbox pictures are not.
        assert h.session.compute("Welcome (card)", "world_locked") == 1
        # Never again on later launches...
        h.run("new_scene")
        assert onboarding.show_first_run() is False
        assert not h.session.scene.has("Welcome (card)")
        # ...but Help → Getting Started reopens it any time.
        h.run("help.getting_started")
        assert h.session.scene.has("Welcome (card)")
    finally:
        h.close()
