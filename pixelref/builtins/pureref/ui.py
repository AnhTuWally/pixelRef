"""PureRef import in the desktop app: File menu entries and dropping ``.pur`` files.

pixelRef never writes ``.pur`` files. Opening a board as a new scene says so:
a notice the first time (until the user ticks "Don't show this again") and a
reminder in the status bar every time. Importing into a board doesn't.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QFileDialog, QMessageBox

from pixelref.plugin_api import CommandError
from pixelref.plugin_api.contracts import IngestHandler

from . import PUR_EXT

PUR_FILTER = f"PureRef Board (*{PUR_EXT})"
DROP_GAP = 64.0
NOTICE_SETTING = "one_way_notice_hidden"
NOTICE_TITLE = "PureRef boards are import-only"
NOTICE_TEXT = (
    "pixelRef reads PureRef boards but can't save them back as PureRef files.\n\n"
    "Your work is saved as a pixelRef scene (.pxref). The original .pur file is left exactly as it was, "
    "so changes you make here won't show up in PureRef."
)
REMINDER = "import only: save as a pixelRef scene (.pxref)"


def activate_pureref_ui(ui: Any) -> None:
    session = ui.session
    app = ui.app
    importer = session.services.get("pureref")

    def report_text(verb: str) -> str:
        report = importer.last_report
        parts = [f"{report.get('images', 0) + report.get('linked', 0)} image(s)", f"{report.get('notes', 0)} note(s)"]
        if report.get("missing"):
            parts.append(f"{report['missing']} missing")
        return f"{verb} {Path(report.get('file', '')).name}: " + ", ".join(parts)

    def show_report(verb: str, one_way: bool = False) -> None:
        ui.status(f"{report_text(verb)} ({REMINDER})" if one_way else report_text(verb))
        warnings = importer.last_report.get("warnings") or []
        if warnings:
            ui.toast(warnings[0] if len(warnings) == 1 else f"{warnings[0]} (+{len(warnings) - 1} more)")
        if one_way:
            one_way_notice()

    def notice_hidden() -> bool:
        return str(ui.settings.value(NOTICE_SETTING, "false")).lower() in ("true", "1")

    notice: dict[str, Any] = {}

    def one_way_notice() -> None:
        """Say once (until hidden) that pixelRef can't write PureRef files. Not modal: the board stays usable."""
        if notice_hidden() or notice.get("box") is not None:
            return
        box = QMessageBox(QMessageBox.Information, NOTICE_TITLE, NOTICE_TEXT, QMessageBox.Ok, app.main_window())
        check = QCheckBox("Don't show this again")
        box.setCheckBox(check)

        def closed(_result: int) -> None:
            if check.isChecked():
                ui.settings.setValue(NOTICE_SETTING, True)
            notice.pop("box", None)
            box.deleteLater()

        box.finished.connect(closed)
        box.setWindowModality(Qt.NonModal)
        notice["box"] = box
        box.show()

    def close_notice() -> None:
        box = notice.pop("box", None)
        if box is not None:
            box.close()
            box.deleteLater()

    ui.track(close_notice)
    ui.provide_service("pureref.notice", type("Notice", (), {
        "show": staticmethod(one_way_notice), "box": staticmethod(lambda: notice.get("box")),
        "hidden": staticmethod(notice_hidden),
    })())

    def start_dir() -> str:
        return str(session.base_dir())

    def open_dialog() -> None:
        """Choose a PureRef board to open as a new scene (asks about unsaved changes first)."""
        files = session.services.get("files")
        if files is not None and not files.may_discard():
            return
        path, _ = QFileDialog.getOpenFileName(app.main_window(), "Open PureRef Board", start_dir(), PUR_FILTER)
        if path and app.run_command("pureref.open", path) is not None:
            def framed() -> None:
                app.run_command("navigation.frame_all")
                show_report("Opened", one_way=True)  # after framing, which sets its own status

            QTimer.singleShot(0, framed)

    def import_dialog() -> None:
        """Choose a PureRef board to add to this one, at the view centre."""
        path, _ = QFileDialog.getOpenFileName(app.main_window(), "Import PureRef Board", start_dir(), PUR_FILTER)
        if not path:
            return
        view = app.active_view()
        center = view.center() if view is not None else QPointF()
        if app.run_command("pureref.import_file", path, [center.x(), center.y()]) is not None:
            show_report("Imported")

    ui.command("open_dialog", open_dialog, undoable=False)
    ui.command("import_dialog", import_dialog, undoable=False)
    ui.action("pureref.open", "pureref.open_dialog", "Open PureRef Board (Import Only)…", icon="folder",
              keywords="pur pureref load board convert read-only one way", category="File")
    ui.action("pureref.import", "pureref.import_dialog", "Import PureRef Board…", icon="image",
              keywords="pur pureref add insert merge board convert read-only one way", category="File")
    ui.menu("canvas", "pureref.open", path="File", group="file", order=1.5)
    ui.menu("canvas", "pureref.import", path="File", group="file.import", order=5)

    # ---- Dropped .pur files ----------------------------------------------------------------------

    def pur_files(mime: Any) -> list[Path]:
        if not mime.hasUrls():
            return []
        return [Path(u.toLocalFile()) for u in mime.urls()
                if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() == PUR_EXT]

    def describe(mime: Any) -> str | None:
        boards = pur_files(mime)
        if not boards:
            return None
        what = f"the PureRef board {boards[0].name}" if len(boards) == 1 else f"{len(boards)} PureRef boards"
        return f"Drop to add {what}"

    def ingest(_view: Any, pos: QPointF, mime: Any) -> Any:
        """Each dropped board in its own group, the first centred on the drop, the others to its right."""
        boards = pur_files(mime)
        if not boards:
            return None
        x, images, notes, errors = pos.x(), 0, 0, []
        for board in boards:
            try:
                file, parsed = importer.read(board)
            except CommandError as exc:
                errors.append(str(exc))
                continue
            roots = importer.build(file, parsed, [x, pos.y()], group=bool(session.scene.nodes))
            images += importer.last_report["images"] + importer.last_report["linked"]
            notes += importer.last_report["notes"]
            bounds = [session.world_bounds(name) for name in roots]
            bounds = [b for b in bounds if b]
            if bounds:
                right = max(b[0] + b[2] for b in bounds)
                width = right - min(b[0] for b in bounds)
                x = right + DROP_GAP + width / 2.0  # roughly centres a board of the same width next
        if errors:
            ui.toast(errors[0])
        return (images, notes) if images or notes else "handled"

    ui.contribute("ingest.handlers", IngestHandler("pureref.board", 1, lambda m: bool(pur_files(m)), describe, ingest))
