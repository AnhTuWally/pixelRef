"""GPU-drawn canvas views.

A canvas view draws through OpenGL when the machine has a real GPU driver:
images are uploaded once as textures and scaled, rotated and composited by
the GPU, so a pan or zoom costs almost nothing however large the window is.
Without one (remote desktops, headless tests, software-only drivers, which
are slower than Qt's own raster painting) views paint in software.

Grabbing an OpenGL viewport (``viewport().grab()``) reads its framebuffer
back unreliably (stray pixels on some drivers): use ``CanvasView.render_frame``
(or ``grab_clean``) for the canvas's pixels.

``PIXELREF_GL``: "1" always use OpenGL, "0" never, unset: when a hardware
driver is found.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtGui import QGuiApplication, QSurfaceFormat
from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

SAMPLES = 4  # multisampling: antialiased edges on the GPU
SOFTWARE_RENDERERS = ("llvmpipe", "softpipe", "swiftshader", "gdi generic", "basic render", "software")

_available: bool | None = None
renderer = ""  # the GL_RENDERER string once probed


def gl_mode() -> str:
    return os.environ.get("PIXELREF_GL", "").strip()


def gl_available() -> bool:
    """A hardware OpenGL context can be made (probed once)."""
    global _available, renderer
    if _available is not None:
        return _available
    mode = gl_mode()
    _available = False
    if mode == "0":
        return False
    if QGuiApplication.platformName() in ("offscreen", "minimal") and mode != "1":
        return False
    try:
        from PySide6.QtGui import QOffscreenSurface, QOpenGLContext

        context = QOpenGLContext()
        if not context.create():
            return False
        surface = QOffscreenSurface()
        surface.setFormat(context.format())
        surface.create()
        if not context.makeCurrent(surface):
            return False
        GL_RENDERER = 0x1F01  # noqa: N806
        renderer = str(context.functions().glGetString(GL_RENDERER) or "")
        context.doneCurrent()
    except Exception:  # noqa: BLE001  (no usable OpenGL: paint in software)
        logger.debug("OpenGL probe failed", exc_info=True)
        return False
    software = any(name in renderer.lower() for name in SOFTWARE_RENDERERS)
    _available = mode == "1" or not software
    logger.info("Canvas rendering: %s (%s)", "OpenGL" if _available else "software", renderer or "no renderer")
    return _available


def make_viewport() -> QWidget | None:
    """An OpenGL viewport for a canvas view, or None (keep the default one)."""
    if not gl_available():
        return None
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainter
    from PySide6.QtOpenGLWidgets import QOpenGLWidget

    class CanvasGLWidget(QOpenGLWidget):
        """The view paints this widget from its own paintEvent. Only ``grab()`` goes through
        ``paintGL``, and the view's painting there leaves stray pixels, so a grab draws the
        frame the view renders in software instead."""

        def paintGL(self) -> None:  # noqa: N802
            view = getattr(self, "_pixelref_view", None)
            if view is None:
                return
            painter = QPainter(self)
            try:
                painter.drawImage(QPointF(0, 0), view.render_frame())
            finally:
                painter.end()

    widget = CanvasGLWidget()
    fmt = QSurfaceFormat.defaultFormat()
    fmt.setSamples(SAMPLES)
    widget.setFormat(fmt)
    return widget
