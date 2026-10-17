"""Playback controls for animated images (GIF, animated WebP).

* ``K`` plays / pauses, ``,`` and ``.`` step one frame back / forward,
  ``Ctrl+,`` / ``Ctrl+.`` jump to the first / last frame — for the selected
  animated images.
* The Attribute Editor gets a *Playback* section: transport buttons, the
  frame number, a scrub slider and a go-to-frame field.
* The quick editor bar under the selection gets previous / play-pause / next
  and the frame number (a ``quick_edit.sections`` contribution). While the
  quick editor is off, each selected animation gets a painted bar of its own.
* ``pxcmd.playback.play / pause / toggle / next_frame / prev_frame /
  go_to_frame / first_frame / last_frame / info``.

Frames are numbered from 1. Like camera moves, playback is not undoable; a
paused image remembers its frame in the scene file (plugin data
``playback``), so a board can hold a GIF on a chosen frame.
"""

from __future__ import annotations

from typing import Any


def activate_ui(ui: Any) -> None:
    from .ui import activate_playback_ui

    activate_playback_ui(ui)
