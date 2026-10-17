"""Keyboard Shortcuts (F1): every action's keys, editable (saved to keymap.toml), and the mouse reference.
Also About pixelRef: the version of the app, Python and Qt."""

from __future__ import annotations

import platform
from html import escape
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

    ui.command("show", show_help, undoable=False, echo=False)
    ui.command("about", about, undoable=False, echo=False)
    ui.command("show_about", show_about, undoable=False, echo=False)
    ui.command("text", html, undoable=False, echo=False)
    ui.action("help.shortcuts", "help.show", "Keyboard Shortcuts…",
              keywords="keyboard hotkeys shortcuts keys keymap edit customize rebind macro", category="Window",
              icon="keyboard")
    ui.bind("F1", "help.shortcuts")
    ui.bind("?", "help.shortcuts")
    ui.menu("canvas", "help.shortcuts", group="app", order=0)
    ui.action("help.about", "help.show_about", "About pixelRef…", keywords="version info credits", category="Window",
              icon="info")
    ui.menu("canvas", "help.about", group="app", order=1)
