"""Keyboard Shortcuts (F1): every action's keys, editable (saved to keymap.toml), and the mouse reference.
Also About pixelRef (the version of the app, Python and Qt) and Getting Started, the onboarding board
that opens by itself the first time pixelRef starts."""

from __future__ import annotations

import platform
import sys
from html import escape
from pathlib import Path
from typing import Any

from pixelref.plugin_api import APP_VERSION

CATEGORY_ORDER = ("File", "Edit", "Create", "Tools", "Transform", "Arrange", "View", "Window", "Scripts",
                  "User Hotkeys")

MOUSE_HELP = (
    "Wheel: zoom · Middle/right-drag or Space+drag: pan · Ctrl+wheel: scale selection · tap Space: maximise view",
    "Drag empty space: rubber-band select (Ctrl/Shift adds) · Shift+click: toggle",
    "Corner handles: scale (Shift: free, Alt: from centre) · Edge handles: stretch",
    "Text notes: side handles set the box width (text reflows) · double-click one for auto width",
    "Lines/arrows: drag either end point (Shift: 45° steps) · drag the line to move it",
    "Rectangles: drag the round corner/edge points to reshape (Shift: square, Alt: from centre) · blue dot sets the "
    "corner radius",
    "Rect (R) / Arrow (A) / Pen (P) stay active until Esc/V · Eraser (E) removes strokes, rects, arrows",
    "Pen: each session makes a new Drawing · Ctrl+Shift+G separates strokes, Ctrl+J combines · double-click a "
    "drawing (Shift+P) to edit it again",
    "Drag the top knob or just outside a corner: rotate (Shift: 15° steps)",
    "Double-click image: zoom to it · Double-click text: edit · Alt+drag: move window",
    "Frameless window (F11): drag an edge or corner to resize · drag the bar or a view header to move · "
    "double-click them to maximise",
    "Outliner: drag rows to reparent/reorder · double-click or F2 to rename · 👁/🔒 columns toggle",
    "Panels: the tabs on the window edge show/hide Tools, Outliner and Attribute Editor (right-click: docked, "
    "floating, pop-up only, other side) · Alt+T, Alt+O, Alt+A (Alt+Enter) pop them up anywhere · Tab hides the "
    "sidebar and floating panels · a panel's header buttons float it over the canvas or dock it",
    "Window manager: Ctrl+W then H/J/K/L focus, Shift+H/J/K/L move, S/V split, N new view, T tabbed, F fullscreen, "
    "1–9 workspaces, - scratchpad (console), Esc leaves",
)


GUIDE_FILE = "onboard.pxref"
GUIDE_MARKER = "Welcome"           # the view marker the guide opens looking through
ONBOARDING_SHOWN_SETTING = "onboarding_shown"


def guide_path() -> Path | None:
    """The Getting Started board: examples/scenes/onboard.pxref next to the sources, or beside the
    bundled app's libraries in the standalone build. None when the file isn't there."""
    roots = [Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]]
    for root in roots:
        path = root / "examples" / "scenes" / GUIDE_FILE
        if path.is_file():
            return path
    return None


def activate_ui(ui: Any) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence
    from PySide6.QtWidgets import QDialog, QTabWidget, QTextBrowser, QVBoxLayout

    from pixelref.plugin_api.ui import logo_pixmap, theme

    app = ui.app

    def html() -> str:
        """The keyboard shortcut reference as HTML (built from every action and binding)."""
        rows: list[str] = []
        by_category: dict[str, list[tuple[str, str]]] = {}
        for key, spec in app.actions.specs.items():
            if spec.category in ("", "hidden"):
                continue
            action = app.actions.actions[key]
            shortcuts = ", ".join(s.toString(QKeySequence.SequenceFormat.NativeText) for s in action.shortcuts())
            by_category.setdefault(spec.category, []).append((action.text().replace("&&", "&"), shortcuts))
        order = list(CATEGORY_ORDER) + sorted(c for c in by_category if c not in CATEGORY_ORDER)
        for category in order:
            entries = by_category.get(category)
            if not entries:
                continue
            rows.append(f"<tr><td colspan=2 style='padding-top:8px;color:{theme.ACCENT_HEX}'>"
                        f"<b>{escape(category)}</b></td></tr>")
            for text, shortcut in entries:
                rows.append(f"<tr><td>{escape(text)}</td><td style='padding-left:16px;color:#aeb4bf'>"
                            f"{escape(shortcut)}</td></tr>")
        leader = app.keymap.leader_sequence
        mouse = "<br>".join(escape(line.replace("Ctrl+W", leader)) for line in MOUSE_HELP)
        return (f"<table>{''.join(rows)}</table><p><b style='color:{theme.ACCENT_HEX}'>Mouse & windows</b><br>"
                f"{mouse}</p>")

    def show_help(search: str = "") -> None:
        """Show Keyboard Shortcuts (F1): every action's keys, editable, and the mouse reference."""
        from .editor import ShortcutEditor

        dialog = QDialog(app.main_window())
        dialog.setWindowTitle("pixelRef — Keyboard Shortcuts")
        dialog.resize(760, 760)
        dialog.setStyleSheet(app.stylesheet)
        layout = QVBoxLayout(dialog)
        tabs = QTabWidget(dialog)
        editor = ShortcutEditor(app, tabs)
        if search:
            editor.search.setText(search)
        tabs.addTab(editor, "Shortcuts")
        browser = QTextBrowser(tabs)
        browser.setHtml(mouse_html())
        tabs.addTab(browser, "Mouse && Windows")
        layout.addWidget(tabs)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.editor = editor
        dialog.show()
        editor.search.setFocus()
        state["dialog"] = dialog

    state: dict[str, Any] = {}

    def mouse_html() -> str:
        leader = app.keymap.leader_sequence
        return "<br>".join(escape(line.replace("Ctrl+W", leader)) for line in MOUSE_HELP)

    def about() -> dict[str, str]:
        """The pixelRef, Python and Qt versions."""
        import PySide6

        return {"pixelref": APP_VERSION, "python": platform.python_version(), "pyside6": PySide6.__version__}

    def show_about() -> None:
        """Show the About pixelRef dialog (version information)."""
        from PySide6.QtWidgets import QMessageBox

        info = about()
        box = QMessageBox(app.main_window())
        box.setWindowTitle("About pixelRef")
        box.setIconPixmap(logo_pixmap(64, box.devicePixelRatioF()))
        box.setTextFormat(Qt.RichText)
        box.setText(f"<b style='font-size:15px'>pixelRef {escape(info['pixelref'])}</b>"
                    "<br>A plugin-first, node-based reference board.")
        box.setInformativeText(f"Python {escape(info['python'])} · PySide6 {escape(info['pyside6'])}")
        box.setStyleSheet(app.stylesheet)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.show()
        state["about"] = box

    def open_guide(confirm: bool = True) -> bool:
        """Open the Getting Started board: a tour of the basic and advanced features, with screenshots.
        It opens as an untitled copy (Save asks for a name), so the shipped file is never overwritten."""
        from PySide6.QtWidgets import QMessageBox

        path = guide_path()
        files = ui.service("files")
        if path is None or files is None:
            QMessageBox.warning(app.main_window(), "Getting Started", f"The guide ({GUIDE_FILE}) wasn't found.")
            return False
        if confirm and not files.may_discard():
            return False
        session = ui.session
        try:
            session.load_text(session.format.read_text(path), None)
        except Exception as exc:  # noqa: BLE001  (loading is atomic: the open board is untouched)
            QMessageBox.critical(app.main_window(), "Getting Started", f"{path}\n\n{exc}")
            return False
        session.revision = session.saved_revision = 0  # the guide opens clean: closing it never asks to save
        cache = ui.service("image_cache")
        if cache is not None:
            cache.clear()
            ui.canvas.request_full_refresh()
        recovery = ui.service("recovery")
        if recovery is not None:
            recovery.clear()
        if session.scene.has(GUIDE_MARKER):
            app.run_command("views.look_through", GUIDE_MARKER, animate=False)
        else:
            app.run_command("navigation.frame_all")
        ui.status("Getting Started")
        return True

    def show_first_run() -> bool:
        """At startup: open the guide the very first time pixelRef runs (once, ever).
        Someone who already has recent boards is an existing user and isn't shown it."""
        if ui.settings.value(ONBOARDING_SHOWN_SETTING, False, type=bool):
            return False
        ui.settings.setValue(ONBOARDING_SHOWN_SETTING, True)  # first: a crash while opening must not repeat it
        files = ui.service("files")
        if files is None or files.recent_files():
            return False
        return open_guide(confirm=False)

    ui.provide_service("onboarding", type("Onboarding", (), {
        "show_first_run": staticmethod(show_first_run), "open_guide": staticmethod(open_guide),
    })())
    ui.command("getting_started", open_guide, undoable=False, echo=False)
    ui.command("show", show_help, undoable=False, echo=False)
    ui.command("about", about, undoable=False, echo=False)
    ui.command("show_about", show_about, undoable=False, echo=False)
    ui.command("text", html, undoable=False, echo=False)
    ui.action("help.shortcuts", "help.show", "Keyboard Shortcuts…",
              keywords="keyboard hotkeys shortcuts keys keymap edit customize rebind macro", category="Window",
              icon="keyboard")
    ui.bind("F1", "help.shortcuts")
    ui.bind("?", "help.shortcuts")
    ui.action("help.getting_started", "help.getting_started", "Getting Started…",
              keywords="tutorial onboarding welcome guide tour learn introduction help basics", category="Window",
              icon="info")
    ui.menu("canvas", "help.getting_started", group="app", order=-1)
    ui.menu("canvas", "help.shortcuts", group="app", order=0)
    ui.action("help.about", "help.show_about", "About pixelRef…", keywords="version info credits", category="Window",
              icon="info")
    ui.menu("canvas", "help.about", group="app", order=1)
