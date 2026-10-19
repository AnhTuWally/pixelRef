"""Auto-Refresh: the open file changing, vanishing or being replaced on disk."""

from __future__ import annotations

import os
import time

from harness import make, tmp_dir
from PySide6.QtWidgets import QMessageBox


def _board(h, name: str = "board.pxref"):
    path = tmp_dir() / name
    h.add_image("n1", 0, 0)
    assert h.save_to(path)
    return path


def _external_edit(path, source_text: str) -> None:
    """Another program writes the file (mtime moved on so it's noticed even on coarse clocks)."""
    path.write_text(source_text, encoding="utf-8")
    later = time.time() + 5
    os.utime(path, (later, later))


def test_modes_and_external_change():
    h = make()
    try:
        path = _board(h)
        other = make()
        try:
            other.add_image("a", 0, 0)
            other.add_image("b", 10, 10)
            other_path = tmp_dir() / "other.pxref"
            assert other.save_to(other_path)
            changed_text = other_path.read_text(encoding="utf-8")
        finally:
            other.close() if hasattr(other, "close") else None
        assert h.run("file.check_external") is None  # our own save is not an external change
        h.run("file.set_watch_mode", "off")
        _external_edit(path, changed_text)
        assert h.run("file.check_external") is None and not h.has("a"), "off: nothing happens"
        h.run("file.set_watch_mode", "notify")
        # turning watching back on looks at the disk straight away: it's announced once, not reloaded
        assert h.run("file.check_external") is None and not h.has("a"), "notify: told once, not reloaded"
        view = h.view
        assert view._toast and view._toast["actions"][0][0] == "Reload", "the toast offers Reload"
        h.run("file.reload")
        assert h.has("a") and h.has("b") and not h.has("n1")
        # automatic mode reloads on its own
        h.run("file.set_watch_mode", "reload")
        _external_edit(path, changed_text.replace('"b"', '"c"'))
        assert h.run("file.check_external") == "changed"
        assert h.has("c") and not h.has("b")
    finally:
        h.close() if hasattr(h, "close") else None


def test_deleted_and_unsaved():
    h = make()
    try:
        path = _board(h)
        h.run("file.set_watch_mode", "notify")
        path.unlink()
        assert h.run("file.check_external") == "missing"
        assert h.has("n1"), "the open copy is kept"
        assert h.run("file.save") and path.exists(), "saving writes it back"
        # Unsaved edits: reload asks, and "Keep Mine" keeps them.
        h.add_image("mine", 5, 5)
        text = path.read_text(encoding="utf-8")
        _external_edit(path, text)
        answers = []
        orig = QMessageBox.exec
        QMessageBox.exec = lambda self: answers.append(self.text()) or 0
        try:
            h.run("file.reload")
        finally:
            QMessageBox.exec = orig
        assert answers and h.has("mine"), "declined: unsaved work survives"
    finally:
        h.close() if hasattr(h, "close") else None
