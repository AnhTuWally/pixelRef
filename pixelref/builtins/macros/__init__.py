"""Scripts & macros: run your own Python and command sequences from keys and menus.

* **Scripts.** Every ``.py`` file in the user scripts folder (and in folders on
  ``$PIXELREF_SCRIPT_PATH``) becomes an action ``script.<file name>``: it is in
  the *Scripts* menu and the command search (Ctrl+K), and Keyboard Shortcuts
  (F1) can give it a key. The first line of the file's docstring is its title.
  ``userSetup.py``, the console's ``console_tab_*.py`` and files starting with
  ``_`` are skipped. The folder is watched: new and edited files show up at once.
* **Macros.** *Record Macro* (Ctrl+Alt+R, or the record button in the window bar)
  captures what you do as ``pxcmd`` lines (the same lines the Script Editor
  echoes). While recording, the bar shows a REC pill (time, steps, pause, save,
  discard) and every canvas gets a red frame. Saving asks for a name and lets you
  untick steps; the macro is a script that works like any other: run it from the
  menu, bind it, edit it.
* **Hotkeys.** ``keymap.toml`` entries with ``commands = [...]``, ``python = "..."``
  or ``script = "file.py"`` run through ``macros.run``.

Every run is one undo step; if a step fails, everything the run did is undone and
the error is shown (the traceback goes to the Script Editor).
"""

from __future__ import annotations

import ast
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

SKIPPED = ("userSetup.py",)


def script_folders(config_dir: Path) -> list[Path]:
    folders = [config_dir / "scripts"]
    for entry in os.environ.get("PIXELREF_SCRIPT_PATH", "").split(os.pathsep):
        if entry.strip():
            folders.append(Path(entry.strip()))
    return folders


def is_script(path: Path) -> bool:
    return (path.suffix == ".py" and path.name not in SKIPPED and not path.name.startswith(("_", "."))
            and not path.name.startswith("console_tab_"))


def script_title(path: Path) -> str:
    """The first line of the module docstring, else the file name made readable."""
    try:
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
        doc = None
    first = (doc or "").strip().splitlines()[0].strip() if doc and doc.strip() else ""
    return first or path.stem.replace("_", " ").strip().capitalize()


def discover(folders: list[Path]) -> list[tuple[str, Path, str]]:
    """(action key, path, title) for every script; earlier folders win a name clash."""
    found: dict[str, tuple[str, Path, str]] = {}
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.py"), key=lambda p: p.name.lower()):
            if not is_script(path):
                continue
            key = "script." + re.sub(r"[^a-z0-9_]+", "_", path.stem.lower()).strip("_")
            if key != "script." and key not in found:
                found[key] = (key, path, script_title(path))
    return list(found.values())


def macro_source(name: str, lines: list[str]) -> str:
    body = "\n".join(lines)
    return f'"""{name}\n\nMacro recorded on {date.today().isoformat()}. Edit freely: it is a normal script.\n"""\n\n{body}\n'


class Recording:
    """A macro being recorded: its lines, and how long it has been recording (pauses excluded)."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.paused = False
        self.started = time.monotonic()
        self.paused_for = 0.0
        self.paused_at: float | None = None

    def elapsed(self) -> float:
        now = self.paused_at if self.paused_at is not None else time.monotonic()
        return max(0.0, now - self.started - self.paused_for)

    def set_paused(self, paused: bool) -> None:
        if paused == self.paused:
            return
        self.paused = paused
        if paused:
            self.paused_at = time.monotonic()
        elif self.paused_at is not None:
            self.paused_for += time.monotonic() - self.paused_at
            self.paused_at = None


def activate_ui(ui: Any) -> None:
    from PySide6.QtCore import QFileSystemWatcher, QTimer, QUrl
    from PySide6.QtGui import QColor, QDesktopServices
    from PySide6.QtWidgets import QDialog

    from pixelref.plugin_api.ui import widgets

    from .ui import RED, MacroBlock, SaveMacroDialog, paint_recording

    from pixelref.core.commands import CommandError
    from pixelref.core.extensions import Disposable

    app = ui.app
    session = ui.session
    scripts: dict[str, tuple[Path, str]] = {}  # action key -> (path, title)
    script_items: list[Any] = []
    # Scripts are scanned when the app loads user files (not in tests), or once asked to.
    state: dict[str, Any] = {"recording": None, "pending": None, "scan": getattr(app, "load_user_files", True)}

    def folders() -> list[Path]:
        return script_folders(app.config_dir())

    def user_folder() -> Path:
        return folders()[0]

    # ---- Running -----------------------------------------------------------------------------

    def find_script(name: str) -> Path:
        path = Path(name)
        if path.is_absolute():
            if path.is_file():
                return path
        else:
            for folder in folders():
                if (folder / path).is_file():
                    return folder / path
        raise CommandError(f"Script not found: {name} (looked in {', '.join(str(f) for f in folders())})")

    def run_python(source: str, label: str, path: Path | None = None) -> None:
        console = ui.service("console")
        if console is None:
            raise CommandError("Running Python needs the Script Editor plugin")
        engine = console.engine
        errors: list[str] = []

        def capture(text: str, kind: str) -> None:
            if kind == "error":
                errors.append(text)

        engine.writers.append(capture)
        try:
            if path is not None:
                engine.namespace["__file__"] = str(path)
                engine.write(f"# Running {path}\n", "info")
            engine.execute(source, label=label)
        finally:
            engine.writers.remove(capture)
        if errors:
            last = next((line for line in reversed("".join(errors).splitlines()) if line.strip()), "error")
            raise CommandError(f"{label} failed: {last.strip()} (traceback in the Script Editor, Ctrl+`)")

    def run_step(step: str) -> Any:
        spec = app.actions.specs.get(step)
        if spec is not None:
            return session.run(spec.command, *spec.args, **spec.kwargs)
        if session.commands.exists(step):
            return session.run(step)
        raise CommandError(f"Unknown action or command '{step}'")

    def run(commands: list[str] | None = None, python: str | None = None, script: str | None = None,
            label: str = "Macro") -> None:
        """Run a macro as one undo step: action/command names in order, Python code or a script file."""
        given = [v for v in (commands, python, script) if v]
        if len(given) != 1:
            raise CommandError("Give exactly one of commands, python or script")
        if commands:
            for index, step in enumerate([commands] if isinstance(commands, str) else commands, start=1):
                try:
                    run_step(str(step))
                except Exception as exc:  # noqa: BLE001
                    raise CommandError(f"{label}: step {index} ({step}) failed: {exc}") from exc
        elif python:
            run_python(python, label)
        else:
            path = find_script(str(script))
            run_python(path.read_text(encoding="utf-8"), label, path)
        ui.status(f"Ran {label}")

    def run_script(path: str) -> None:
        """Run a script file from the scripts folder (or a full path) as one undo step."""
        found = find_script(path)
        run(script=str(found), label=script_title(found))

    # ---- Script actions --------------------------------------------------------------------------

    def rescan(force: bool = False) -> list[str]:
        """Find the scripts again and update their actions; returns their action keys."""
        state["scan"] = state["scan"] or force
        found = discover(folders()) if state["scan"] else []
        wanted = {key: (path, title) for key, path, title in found}
        if wanted == scripts:
            return list(wanted)
        while script_items:
            script_items.pop().dispose()
        scripts.clear()
        scripts.update(wanted)
        for key, (path, title) in wanted.items():
            script_items.append(app.actions.define(key, "macros.run_script", title, args=(str(path),),
                                                   keywords=f"script macro run python {path.stem}",
                                                   category="Scripts", plugin=ui.plugin_id))
        watch()
        return list(wanted)

    watcher = QFileSystemWatcher()
    debounce = QTimer()
    debounce.setSingleShot(True)
    debounce.setInterval(250)
    debounce.timeout.connect(lambda: rescan())
    watcher.directoryChanged.connect(lambda _p: debounce.start())
    watcher.fileChanged.connect(lambda _p: debounce.start())

    def watch() -> None:
        paths = [str(f) for f in folders() if f.is_dir()] + [str(p) for p, _t in scripts.values()]
        old = watcher.directories() + watcher.files()
        if old:
            watcher.removePaths(old)
        if paths:
            watcher.addPaths(paths)

    def stop_watching() -> None:
        debounce.stop()
        old = watcher.directories() + watcher.files()
        if old:
            watcher.removePaths(old)
        while script_items:
            script_items.pop().dispose()
        scripts.clear()
        watcher.deleteLater()
        debounce.deleteLater()

    ui.track(stop_watching)

    def open_folder() -> str:
        """Open the user scripts folder (created if missing) in the file browser."""
        folder = user_folder()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        rescan(force=True)
        watch()
        return str(folder)

    # ---- Recording ---------------------------------------------------------------------------------

    def current() -> Recording | None:
        return state["recording"]

    def recording() -> bool:
        return state["recording"] is not None

    blocks: list[MacroBlock] = []
    ticker = QTimer()
    ticker.setInterval(500)

    def tick() -> None:
        for block in list(blocks):
            try:
                block.tick()
            except RuntimeError:
                blocks.remove(block)

    ticker.timeout.connect(tick)

    def changed() -> None:
        """The recording started, stopped, paused or got a step: update everything that shows it."""
        app.actions.refresh_states()
        for block in list(blocks):
            try:
                block.refresh()
            except RuntimeError:
                blocks.remove(block)
        if recording():
            ticker.start()
        else:
            ticker.stop()
        app.update_views()

    def on_echo(text: str) -> None:
        state["pending"] = text

    def on_after(command: Any, _result: Any, error: BaseException | None) -> None:
        pending, state["pending"] = state["pending"], None
        rec = current()
        if rec is None or rec.paused or pending is None or error is not None:
            return
        if command.name.startswith("macros."):
            return
        rec.lines.append(pending)
        changed()

    hooks: list[Any] = []

    def end_recording() -> None:
        state["recording"] = None
        for hook in hooks:
            hook.dispose()
        hooks.clear()
        changed()

    def save_macro(name: str, lines: list[str]) -> str:
        folder = user_folder()
        folder.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "macro"
        path = folder / f"{stem}.py"
        number = 2
        while path.exists():
            path, number = folder / f"{stem}_{number}.py", number + 1
        path.write_text(macro_source(name, lines), encoding="utf-8")
        return str(path)

    def start() -> None:
        state["recording"], state["pending"] = Recording(), None
        hooks[:] = [session.commands.on_echo(on_echo), session.commands.on_after(on_after)]
        changed()
        ui.status("Recording a macro: work as usual · Save in the bar's REC pill (or Ctrl+Alt+R) when done")
        ui.toast("Recording macro", QColor(RED))

    def ask_to_save(lines: list[str]) -> tuple[str, list[str], bool] | str | None:
        """The save dialog: (name, lines, wants a key), "keep" to go on recording, None to discard."""
        dialog = SaveMacroDialog(lines, "My macro", app.main_window())
        result = dialog.exec()
        if result == SaveMacroDialog.KEEP:
            return "keep"
        if result != QDialog.Accepted:
            return None
        return dialog.name().strip(), dialog.lines(), dialog.shortcut_box.isChecked()

    def record(name: str | None = None) -> str | None:
        """Start recording a macro, or stop and save it as a script (asks for a name)."""
        if not recording():
            start()
            return None
        rec = current()
        if not rec.lines:
            end_recording()
            ui.status("Macro recording stopped: nothing was recorded")
            return None
        lines, wants_key = rec.lines, False
        if name is None:
            was_paused = rec.paused
            rec.set_paused(True)  # the dialog is up: nothing is recorded meanwhile
            changed()
            answer = ask_to_save(list(lines))
            if answer == "keep":
                rec.set_paused(was_paused)
                changed()
                ui.status("Still recording the macro")
                return None
            if answer is None:
                end_recording()
                ui.status("Macro discarded")
                return None
            name, lines, wants_key = answer
        end_recording()
        if not str(name).strip() or not lines:
            ui.status("Macro discarded")
            return None
        name = str(name).strip()
        path = save_macro(name, lines)
        keys = rescan(force=True)
        key = next((k for k in keys if scripts[k][0] == Path(path)), None)
        ui.status(f"Saved macro '{name}' ({len(lines)} steps) · Scripts menu, Ctrl+K, or give it a key in "
                  "Keyboard Shortcuts (F1)")
        ui.toast(f"Saved macro '{name}'")
        if wants_key and key is not None:
            widgets.capture_shortcut(app, key, app.main_window(), label=name)
        return key

    def pause() -> bool:
        """Pause or resume the macro being recorded; returns True when now paused."""
        rec = current()
        if rec is None:
            raise CommandError("No macro is being recorded")
        rec.set_paused(not rec.paused)
        changed()
        ui.status("Macro recording paused: what you do now is not recorded" if rec.paused
                  else "Recording the macro again")
        return rec.paused

    def discard() -> None:
        """Stop recording and throw the recorded steps away."""
        if not recording():
            raise CommandError("No macro is being recorded")
        end_recording()
        ui.status("Macro discarded")

    def remove_last() -> str | None:
        """Take the last step out of the macro being recorded; returns it."""
        rec = current()
        if rec is None:
            raise CommandError("No macro is being recorded")
        if not rec.lines:
            return None
        line = rec.lines.pop()
        changed()
        ui.status(f"Removed from the macro: {line}")
        return line

    def drop_hooks() -> None:
        for hook in hooks:
            hook.dispose()
        hooks.clear()
        state["recording"] = None
        ticker.stop()
        ticker.deleteLater()

    ui.track(Disposable(drop_hooks))

    def make_block(_app: Any, _output: str) -> MacroBlock:
        block = MacroBlock(ui, current)
        blocks.append(block)
        block.destroyed.connect(lambda *_a, b=block: b in blocks and blocks.remove(b))
        return block

    ui.bar_block(make_block, order=80)
    ui.overlay(lambda view, painter: paint_recording(view, painter, current()), order=95)

    # ---- Commands, actions, menu ---------------------------------------------------------------------

    ui.command("run", run, alias="run_macro", label=lambda *a, label="Macro", **k: label)
    ui.command("run_script", run_script, alias="run_user_script", label="Run script")
    ui.command("record", record, alias="record_macro", undoable=False, echo=False)
    ui.command("pause", pause, alias="pause_macro", undoable=False, echo=False)
    ui.command("discard", discard, alias="discard_macro", undoable=False, echo=False)
    ui.command("remove_last", remove_last, undoable=False, echo=False)
    def reload() -> list[str]:
        """Find the scripts in the scripts folders again; returns their action keys."""
        return rescan(force=True)

    def list_scripts() -> dict[str, str]:
        """Every script action and its file."""
        return {key: str(path) for key, (path, _title) in scripts.items()}

    ui.command("reload", reload, alias="reload_scripts", undoable=False, echo=False)
    ui.command("open_folder", open_folder, undoable=False, echo=False)
    ui.command("list", list_scripts, alias="list_scripts", undoable=False, echo=False)
    a = ui.action
    a("macros.record", "macros.record", "Record Macro", keywords="macro record actions automate repeat",
      category="Scripts", checked=recording, icon="record",
      dynamic_text=lambda: "Stop Recording && Save Macro…" if recording() else "Record Macro")
    a("macros.pause", "macros.pause", "Pause Recording", keywords="macro pause resume recording",
      category="Scripts", icon="pause", enabled=recording,
      dynamic_text=lambda: "Resume Recording" if recording() and current().paused else "Pause Recording")
    a("macros.remove_last", "macros.remove_last", "Remove Last Recorded Step",
      keywords="macro undo step recording", category="Scripts",
      enabled=lambda: recording() and bool(current().lines))
    a("macros.discard", "macros.discard", "Discard Recording", keywords="macro cancel throw away recording",
      category="Scripts", enabled=recording)
    a("macros.reload", "macros.reload", "Reload Scripts", keywords="refresh rescan scripts", category="Scripts")
    a("macros.open_folder", "macros.open_folder", "Open Scripts Folder", keywords="scripts folder files explorer",
      category="Scripts")
    ui.bind("Ctrl+Alt+R", "macros.record")

    def scripts_menu(menu: Any) -> None:
        if not scripts:
            empty = menu.addAction("No scripts yet: record a macro or add .py files")
            empty.setEnabled(False)
            return
        for key in sorted(scripts, key=lambda k: scripts[k][1].lower()):
            action = app.actions.get(key)
            if action is not None:
                menu.addAction(action)

    ui.place_submenu("canvas", "Scripts", "menus", 3, icon="console")
    ui.menu("canvas", builder=scripts_menu, path="Scripts", group="scripts.list", order=0)
    ui.menu("canvas", "macros.record", path="Scripts", group="scripts.record", order=0)
    for order, key in enumerate(("macros.pause", "macros.remove_last", "macros.discard"), start=1):
        ui.menu("canvas", key, path="Scripts", group="scripts.record", order=order, when=recording)
    for order, key in enumerate(("macros.reload", "macros.open_folder", "help.shortcuts")):
        ui.menu("canvas", key, path="Scripts", group="scripts.manage", order=order)

    rescan()
