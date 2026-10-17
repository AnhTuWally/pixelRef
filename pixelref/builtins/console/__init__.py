"""The Script Editor: a Python console like Maya's.

* The upper pane is the output: results, ``print`` output, tracebacks and —
  with "Echo all commands" on — the ``pxcmd`` call of everything you do in
  the UI, so the API can be learned by using the app.
* The lower pane has tabs of Python. Ctrl+Enter (or keypad Enter) runs the
  selected text, or the whole tab. Each run is one undo step.
* ``pxcmd``, ``pxnode``, ``sel()``, ``session`` and ``app`` are predefined.
* ``userSetup.py`` in the user script folder (and in folders on
  ``$PIXELREF_SCRIPT_PATH``) runs at startup.
"""

from __future__ import annotations

import os
import traceback
from pathlib import Path
from typing import Any


class ConsoleEngine:
    """Executes Python for the console (no Qt here, so it is easy to test)."""

    def __init__(self, session: Any, app: Any = None) -> None:
        from pixelref import pxcmd, pxnode

        self.session = session
        self.app = app
        self.namespace: dict[str, Any] = {
            "pxcmd": pxcmd, "pxnode": pxnode, "session": session, "app": app,
            "sel": lambda: session.selection.names, "__name__": "__console__",
        }
        self.running = False
        self.writers: list[Any] = []

    def write(self, text: str, kind: str = "output") -> None:
        for writer in list(self.writers):
            writer(text, kind)

    def execute(self, source: str, label: str = "Script") -> Any:
        """Run code; returns the value of a single expression (else None)."""
        import contextlib

        session = self.session
        session.make_current()
        source = source.rstrip()
        if not source.strip():
            return None
        result: Any = None
        stdout = _Stream(self, "output")
        stderr = _Stream(self, "error")
        self.running = True
        canvas = getattr(self.app, "canvas", None)
        batch = canvas.batch() if canvas is not None else contextlib.nullcontext()
        try:
            with session.undo.chunk(label), batch, contextlib.redirect_stdout(stdout), \
                    contextlib.redirect_stderr(stderr):
                try:
                    code = compile(source, "<console>", "eval")
                except SyntaxError:
                    code = None
                try:
                    if code is not None:
                        result = eval(code, self.namespace)  # noqa: S307
                        if result is not None:
                            print(repr(result))
                        self.namespace["_"] = result
                    else:
                        exec(compile(source, "<console>", "exec"), self.namespace)  # noqa: S102
                # SystemExit too: exit() / sys.exit() in a script must not quit the app unprompted.
                except (Exception, SystemExit):  # noqa: BLE001  (keep what ran so far, like Maya)
                    lines = traceback.format_exc().splitlines()
                    # Drop the console's own frames: start at the user's code.
                    start = next((i for i, line in enumerate(lines) if '"<console>"' in line), 1)
                    stderr.write("\n".join(["Traceback (most recent call last):"] + lines[start:]) + "\n")
        finally:
            self.running = False
            stdout.flush()
            stderr.flush()
        session.notify_changed("script")
        return result

    def run_file(self, path: str | Path) -> None:
        path = Path(path)
        self.namespace["__file__"] = str(path)
        self.write(f"# Running {path}\n", "info")
        self.execute(path.read_text(encoding="utf-8"), label=f"Run {path.name}")


class _Stream:
    def __init__(self, engine: ConsoleEngine, kind: str) -> None:
        self.engine = engine
        self.kind = kind
        self.buffer = ""

    def write(self, text: str) -> int:
        self.buffer += text
        if "\n" in self.buffer:
            head, _sep, self.buffer = self.buffer.rpartition("\n")
            self.engine.write(head + "\n", self.kind)
        return len(text)

    def flush(self) -> None:
        if self.buffer:
            self.engine.write(self.buffer + "\n", self.kind)
            self.buffer = ""


def user_setup_paths(config_dir: Path) -> list[Path]:
    paths = [config_dir / "scripts" / "userSetup.py"]
    for entry in os.environ.get("PIXELREF_SCRIPT_PATH", "").split(os.pathsep):
        if entry.strip():
            paths.append(Path(entry.strip()) / "userSetup.py")
    return [p for p in paths if p.is_file()]


def activate_ui(ui: Any) -> None:
    from .ui import activate_console_ui

    activate_console_ui(ui)
