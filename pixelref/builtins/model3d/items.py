"""The 3D object canvas item: renders a 3D mesh offscreen to a cached QImage."""

from __future__ import annotations

import math
import time
import weakref
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsItem

from pixelref.plugin_api.ui import NodeItemMixin, theme

from . import DEFAULT_SIZE, TYPE
from .loader import (
    ASYNC_FILE_THRESHOLD,
    ASYNC_LOADER,
    CACHE,
    PROGRESS_TEXTURE,
    LoadProgress,
    MeshData,
    file_cache_key,
    load_mesh_bytes,
    load_mesh_file,
    load_primitive,
    report_progress,
)
from .renderer import RenderParams, paint_axes_gizmo, paint_light_gizmo, render_mesh, resolve_mesh_texture

# Items currently in interactive 3D View Mode (normally none or one).
EDITING_3D: "weakref.WeakSet[Model3DItem]" = weakref.WeakSet()

LOADING_FRAME_MS = 33  # repaint rate of the loading card's spinner and progress bar


class _MeshLoadedBridge(QObject):
    """Marshals background-thread mesh load completions onto the main Qt GUI thread,
    and ticks the loading card's animation while a load is in flight."""

    loaded = Signal(object, object, object)  # (cache_key, mesh, error)

    def __init__(self, item: "Model3DItem") -> None:
        super().__init__()
        self._item_ref = weakref.ref(item)
        self.loaded.connect(self._on_loaded, Qt.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setInterval(LOADING_FRAME_MS)
        self._timer.timeout.connect(self._tick)

    def _on_loaded(self, key: Any, mesh: Any, error: Any) -> None:
        item = self._item_ref()
        if item is not None:
            item._on_async_mesh_loaded(key, mesh, error)

    def start_animation(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        item = self._item_ref()
        if item is None or not item.is_loading():
            self._timer.stop()
            return
        item.update()


def _prepared_load(load: Callable[[], MeshData]) -> Callable[[], MeshData]:
    """Wrap a background mesh load so it also decodes the model's texture off the UI thread
    (otherwise the first paint after loading stalls on decoding a large JPEG/PNG)."""

    def work() -> MeshData:
        mesh = load()
        if mesh.has_texture:
            report_progress(PROGRESS_TEXTURE[0], "Decoding texture")
            try:
                resolve_mesh_texture(mesh)
            except Exception:  # noqa: BLE001 - the paint path decodes (and reports) it again
                pass
        report_progress(1.0, "Finishing")
        return mesh

    return work


def placeholder_card(size: QSize, title: str, subtitle: str = "") -> QImage:
    w = max(60, size.width())
    h = max(60, size.height())
    image = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("#1a1c21"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QPen(QColor("#3d4452"), 1.0, Qt.DashLine))
    painter.drawRoundedRect(QRectF(1.0, 1.0, w - 2.0, h - 2.0), 6.0, 6.0)
    painter.setPen(QColor("#8a93a6"))
    text = f"{title}\n{subtitle}".strip()
    painter.drawText(QRectF(8.0, 8.0, w - 16.0, h - 16.0), Qt.AlignCenter | Qt.TextWordWrap, text)
    painter.end()
    return image


def _rgb_tuple(val: Any, default: tuple[int, int, int] = (196, 200, 208)) -> tuple[int, int, int]:
    try:
        return (
            max(0, min(255, int(val[0]))),
            max(0, min(255, int(val[1]))),
            max(0, min(255, int(val[2]))),
        )
    except (TypeError, ValueError, IndexError):
        return default


def _rgba_tuple(val: Any, default: tuple[int, int, int, int] = (24, 27, 34, 0)) -> tuple[int, int, int, int]:
    try:
        a = int(val[3]) if len(val) >= 4 else 255
        return (
            max(0, min(255, int(val[0]))),
            max(0, min(255, int(val[1]))),
            max(0, min(255, int(val[2]))),
            max(0, min(255, a)),
        )
    except (TypeError, ValueError, IndexError):
        return default


class Model3DItem(NodeItemMixin, QGraphicsItem):
    """Canvas item displaying an interactive 3D reference mesh."""

    MIN_SIZE = 60.0

    def __init__(self, node_name: str, session: Any) -> None:
        super().__init__()
        self._init_node(node_name, TYPE, None)
        self.session = session
        self.cache_key: Any = None
        self._attrs: dict[str, Any] = {}
        self._size = QPointF(*DEFAULT_SIZE)
        self._mesh: MeshData | None = None
        self._missing = False
        self._loading = False
        self._loading_label = ""
        self._loading_name = ""
        self._loading_detail = ""
        self._load_progress: LoadProgress | None = None
        self._pending_load_key: Any = None
        self._bridge = _MeshLoadedBridge(self)
        self._error_text = ""
        self._editing = False
        self._interactive_drag = False
        self._render_key: Any = None
        self._rendered: QImage = QImage()
        self._rendered_pixmap: QPixmap | None = None
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.ItemDoesntPropagateOpacityToChildren, True)

    # ---- Source & Attributes -------------------------------------------------------------------

    def apply_attrs(self, attrs: dict[str, Any]) -> None:
        self._attrs = dict(attrs)
        raw_size = attrs.get("size", DEFAULT_SIZE)
        try:
            w = max(self.MIN_SIZE, float(raw_size[0]))
            h = max(self.MIN_SIZE, float(raw_size[1]))
        except (TypeError, ValueError, IndexError):
            w, h = DEFAULT_SIZE
        if QPointF(w, h) != self._size:
            self.prepareGeometryChange()
            self._size = QPointF(w, h)
        self._resolve_mesh(attrs)
        self._invalidate_render()
        self.update()

    def _resolve_mesh(self, attrs: dict[str, Any]) -> None:
        self._missing = False
        self._loading = False
        self._loading_label = ""
        self._loading_name = ""
        self._loading_detail = ""
        self._load_progress = None
        self._pending_load_key = None
        self._error_text = ""
        asset_id = str(attrs.get("asset", "") or "")
        path_str = str(attrs.get("path", "") or "")
        primitive = str(attrs.get("primitive", "") or "")

        try:
            if asset_id:
                asset = self.session.scene.get_asset(asset_id)
                if asset is None:
                    self._mesh = None
                    self._missing = True
                    self._error_text = f"Missing embedded 3D asset\n{asset_id[:8]}"
                    return
                ckey = ("asset", asset_id)
                cached = CACHE.get(ckey)
                if cached is not None:
                    self._mesh = cached
                    return
                if len(asset.data) > ASYNC_FILE_THRESHOLD:
                    base_dir = self.session.base_dir()
                    data = asset.data
                    fname = asset.name
                    self._start_async_load(
                        ckey,
                        lambda: load_mesh_bytes(data, filename=fname, base_dir=base_dir, cache_key=ckey),
                        asset.name or asset_id[:8],
                        f"embedded · {len(data) / (1024.0 * 1024.0):.1f} MB",
                    )
                    return
                self._mesh = load_mesh_bytes(
                    asset.data,
                    filename=asset.name,
                    base_dir=self.session.base_dir(),
                    cache_key=ckey,
                )
            elif path_str:
                path = self.session.resolve_path(path_str)
                if not path.is_file():
                    self._mesh = None
                    self._missing = True
                    self._error_text = f"Missing 3D file\n{Path(path_str).name}"
                    return
                ckey = file_cache_key(path)
                cached = CACHE.get(ckey) if ckey is not None else None
                if cached is not None:
                    self._mesh = cached
                    return
                file_size = path.stat().st_size
                if file_size > ASYNC_FILE_THRESHOLD and ckey is not None:
                    self._start_async_load(
                        ckey,
                        lambda p=path: load_mesh_file(p),
                        path.name,
                        f"{file_size / (1024.0 * 1024.0):.1f} MB",
                    )
                    return
                self._mesh = load_mesh_file(path)
            else:
                self._mesh = load_primitive(primitive or "loomis_head")
        except Exception as exc:  # noqa: BLE001
            self._mesh = None
            self._missing = True
            label = Path(path_str).name if path_str else (primitive or "3D model")
            self._error_text = f"Can't load {label}\n{exc}"

    def _start_async_load(self, ckey: Any, load: Callable[[], MeshData], name: str, detail: str) -> None:
        """Parse the mesh on a worker thread; the item shows a live progress card meanwhile."""
        self._loading = True
        self._loading_name = name
        self._loading_detail = detail
        self._loading_label = f"Loading 3D model…\n{name} ({detail})"
        self._pending_load_key = ckey
        bridge = self._bridge  # not ``self``: the worker must not keep the item alive

        def done(mesh: MeshData | None, error: Exception | None) -> None:
            bridge.loaded.emit(ckey, mesh, error)

        self._load_progress = ASYNC_LOADER.submit(ckey, _prepared_load(load), done)
        bridge.start_animation()

    def load_progress(self) -> tuple[float, str] | None:
        """``(fraction, stage)`` of the background load in flight, or None when not loading."""
        if not self._loading or self._load_progress is None:
            return None
        return self._load_progress.snapshot()

    def _on_async_mesh_loaded(self, key: Any, mesh: MeshData | None, error: Exception | None) -> None:
        if key != self._pending_load_key:
            return
        self._loading = False
        self._load_progress = None
        self._pending_load_key = None
        if error is not None or mesh is None:
            self._mesh = None
            self._missing = True
            path_str = str(self._attrs.get("path", "") or "")
            label = Path(path_str).name if path_str else "3D model"
            self._error_text = f"Can't load {label}\n{error}"
        else:
            self._mesh = mesh
            self._missing = False
            self._error_text = ""
        self._invalidate_render()
        self.update()
        scene = self.scene()
        if scene is not None:
            for view in scene.views():
                view.viewport().update()

    def wait_for_load(self, timeout: float = 30.0) -> MeshData | None:
        """Synchronously wait if an async load is in flight (useful for tests/CLI)."""
        if not self._loading:
            return self._mesh
        self._loading = False
        self._load_progress = None
        self._pending_load_key = None
        asset_id = str(self._attrs.get("asset", "") or "")
        path_str = str(self._attrs.get("path", "") or "")
        try:
            if asset_id:
                asset = self.session.scene.get_asset(asset_id)
                if asset is not None:
                    self._mesh = load_mesh_bytes(
                        asset.data,
                        filename=asset.name,
                        base_dir=self.session.base_dir(),
                        cache_key=("asset", asset_id),
                    )
            elif path_str:
                path = self.session.resolve_path(path_str)
                if path.is_file():
                    self._mesh = load_mesh_file(path)
        except Exception as exc:  # noqa: BLE001
            self._mesh = None
            self._missing = True
            self._error_text = str(exc)
        self._invalidate_render()
        self.update()
        return self._mesh

    def _invalidate_render(self) -> None:
        self._render_key = None
        self._rendered_pixmap = None

    # ---- Live 3D View Mode & Interactive Preview ----------------------------------------------

    @property
    def editing(self) -> bool:
        return self._editing

    def set_editing(self, on: bool) -> None:
        on = bool(on) and not self._locked
        if self._editing == on:
            return
        self._editing = on
        if on:
            EDITING_3D.add(self)
        else:
            EDITING_3D.discard(self)
            self._interactive_drag = False
            self._invalidate_render()
        self.update()

    @property
    def blocks_transform(self) -> bool:
        return self._editing

    def set_interactive_drag(self, active: bool) -> None:
        if self._interactive_drag != bool(active):
            self._interactive_drag = bool(active)
            self._invalidate_render()
            self.update()

    def preview_attrs(self, updates: dict[str, Any], interactive: bool = True) -> None:
        """Update camera/light/shading attributes in-place for live 60fps feedback during drag."""
        self._attrs.update(updates)
        self._interactive_drag = interactive
        self._invalidate_render()
        self.update()

    def current_attrs(self) -> dict[str, Any]:
        return dict(self._attrs)

    # ---- Viewport Box Resize (Gizmo handles) --------------------------------------------------

    def viewport_size(self) -> QPointF:
        return QPointF(self._size)

    def set_viewport_size(self, width: float, height: float) -> None:
        w = max(self.MIN_SIZE, float(width))
        h = max(self.MIN_SIZE, float(height))
        if QPointF(w, h) != self._size:
            self.prepareGeometryChange()
            self._size = QPointF(w, h)
            self._attrs["size"] = [round(w, 1), round(h, 1)]
            self._invalidate_render()
            self.update()

    def commit_values(self) -> dict[str, Any]:
        values = self.transform_attrs()
        values["size"] = [round(self._size.x(), 1), round(self._size.y(), 1)]
        return values

    # ---- Info & Effect Source ------------------------------------------------------------------

    @property
    def mesh(self) -> MeshData | None:
        return self._mesh

    def is_missing(self) -> bool:
        return self._missing

    def is_loading(self) -> bool:
        return self._loading

    @property
    def has_texture(self) -> bool:
        if self._attrs.get("texture_asset") or self._attrs.get("texture_path"):
            return True
        return bool(self._mesh is not None and self._mesh.has_texture)

    def _resolve_texture_override(self) -> tuple[Path | None, bytes | None, str]:
        tex_asset_id = str(self._attrs.get("texture_asset", "") or "")
        if tex_asset_id:
            asset = self.session.scene.get_asset(tex_asset_id)
            if asset is not None and asset.data:
                return None, asset.data, f"asset:{tex_asset_id}"
        tex_path_str = str(self._attrs.get("texture_path", "") or "")
        if tex_path_str:
            tex_path = self.session.resolve_path(tex_path_str)
            if tex_path.is_file():
                return tex_path, None, f"path:{tex_path}"
        return None, None, ""

    def _build_render_params(self) -> RenderParams:
        attrs = self._attrs
        target_raw = attrs.get("target", [0.0, 0.0])
        try:
            target = (round(float(target_raw[0]), 4), round(float(target_raw[1]), 4))
        except (TypeError, ValueError, IndexError):
            target = (0.0, 0.0)
        tex_key = (
            str(attrs.get("texture_asset", "") or ""),
            str(attrs.get("texture_path", "") or ""),
        )
        return RenderParams(
            width=max(60, round(self._size.x())),
            height=max(60, round(self._size.y())),
            dpr=1.0 if self._interactive_drag else 1.5,
            yaw=round(float(attrs.get("yaw", 35.0)), 2),
            pitch=round(float(attrs.get("pitch", 20.0)), 2),
            roll=round(float(attrs.get("roll", 0.0)), 2),
            distance=round(float(attrs.get("distance", 2.6)), 3),
            target=target,
            projection=str(attrs.get("projection", "perspective")),
            fov=round(float(attrs.get("fov", 45.0)), 1),
            shading=str(attrs.get("shading", "studio")),
            wireframe_overlay=bool(attrs.get("wireframe_overlay", False)),
            show_texture=bool(attrs.get("show_texture", True)),
            base_color=_rgb_tuple(attrs.get("base_color", [196, 200, 208])),
            background_color=_rgba_tuple(attrs.get("background_color", [24, 27, 34, 0])),
            light_azimuth=round(float(attrs.get("light_azimuth", 45.0)), 2),
            light_elevation=round(float(attrs.get("light_elevation", 35.0)), 2),
            light_intensity=round(float(attrs.get("light_intensity", 1.0)), 2),
            ambient=round(float(attrs.get("ambient", 0.25)), 2),
            interactive=self._interactive_drag,
            texture_key=tex_key,
        )

    def rendered_image(self) -> QImage:
        params = self._build_render_params()
        key = (id(self._mesh), self._loading, self._missing, self._error_text, params)
        if self._render_key == key and not self._rendered.isNull():
            return self._rendered
        if self._loading:
            self._rendered = placeholder_card(
                QSize(params.width, params.height),
                self._loading_label or "Loading 3D model…",
            )
        elif self._mesh is None or self._missing:
            self._rendered = placeholder_card(
                QSize(params.width, params.height),
                self._error_text or "Missing 3D model",
            )
        else:
            ov_path, ov_bytes, ov_id = self._resolve_texture_override()
            self._rendered = render_mesh(
                self._mesh,
                params,
                override_tex_path=ov_path,
                override_tex_bytes=ov_bytes,
                override_tex_id=ov_id,
            )
        self._render_key = key
        self._rendered_pixmap = None
        return self._rendered

    def effect_source(self) -> tuple[Any, Any] | None:
        if self._missing or self._loading or self._mesh is None:
            return None
        image = self.rendered_image()
        if self._rendered_pixmap is None:
            self._rendered_pixmap = QPixmap.fromImage(image)
        return self._rendered_pixmap, ("model3d", self.node_name, self._render_key)

    def paint_source(self, painter: QPainter, pixmap: Any) -> None:
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(self.boundingRect(), pixmap, QRectF(pixmap.rect()))

    # ---- QGraphicsItem -------------------------------------------------------------------------

    def content_rect(self) -> QRectF:
        return QRectF(0.0, 0.0, self._size.x(), self._size.y())

    def boundingRect(self) -> QRectF:
        return self.content_rect()

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.ItemPositionHasChanged:
            self._notify_moved()
        elif change == QGraphicsItem.ItemSelectedHasChanged and not value and self._editing:
            self.set_editing(False)
        return super().itemChange(change, value)

    def paint_content(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        if self._loading:
            self._paint_loading_card(painter)
            return
        image = self.rendered_image()
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawImage(self.boundingRect(), image)
        painter.restore()

    def _paint_loading_card(self, painter: QPainter) -> None:
        """Vector loading card: spinner, file name, current stage and a progress bar."""
        rect = self.content_rect()
        w, h = rect.width(), rect.height()
        fraction, stage = self.load_progress() or (0.0, "Loading")
        accent = QColor(theme.ACCENT)
        track = QColor("#2a2f3a")
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        border = QPen(QColor("#3d4452"), 1.0, Qt.DashLine)
        border.setCosmetic(True)
        painter.setPen(border)
        painter.setBrush(QColor("#1a1c21"))
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 6.0, 6.0)

        unit = min(w, h)
        show_text = h >= 120.0 and w >= 120.0
        radius = max(10.0, min(36.0, unit * 0.09))
        cy = rect.center().y() - (radius * 0.9 if show_text else radius * 0.4)
        spin = QRectF(rect.center().x() - radius, cy - radius, radius * 2.0, radius * 2.0)
        stroke = max(2.0, radius * 0.16)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(track, stroke, Qt.SolidLine, Qt.RoundCap))
        painter.drawEllipse(spin)
        angle = (time.monotonic() * 300.0) % 360.0
        sweep = 70.0 + 50.0 * (1.0 + math.sin(time.monotonic() * 3.0))  # breathing arc
        painter.setPen(QPen(accent, stroke, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(spin, round(-angle * 16), round(-sweep * 16))

        # Progress bar near the bottom.
        bar_w = w * 0.7
        bar_h = max(3.0, min(8.0, unit * 0.018))
        bar = QRectF(rect.center().x() - bar_w * 0.5, rect.bottom() - max(14.0, h * 0.12) - bar_h, bar_w, bar_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(bar, bar_h * 0.5, bar_h * 0.5)
        if fraction > 0.0:
            painter.setBrush(accent)
            painter.drawRoundedRect(QRectF(bar.left(), bar.top(), max(bar_h, bar_w * fraction), bar_h),
                                    bar_h * 0.5, bar_h * 0.5)

        if show_text:
            px = max(9, min(18, round(unit * 0.035)))
            title_font = QFont()
            title_font.setPixelSize(px + 1)
            title_font.setBold(True)
            info_font = QFont()
            info_font.setPixelSize(px)
            text_w = w - 24.0
            top = spin.bottom() + radius * 0.6
            title_m = QFontMetricsF(title_font)
            painter.setFont(title_font)
            painter.setPen(QColor("#d4d9e3"))
            name = title_m.elidedText(self._loading_name or "3D model", Qt.ElideMiddle, text_w)
            painter.drawText(QRectF(rect.left() + 12.0, top, text_w, title_m.height()), Qt.AlignCenter, name)
            top += title_m.height() + 2.0
            info_m = QFontMetricsF(info_font)
            painter.setFont(info_font)
            painter.setPen(QColor("#8a93a6"))
            info = f"{stage}… {round(fraction * 100)}%"
            if self._loading_detail:
                info = f"{self._loading_detail} · {info}"
            info = info_m.elidedText(info, Qt.ElideRight, text_w)
            painter.drawText(QRectF(rect.left() + 12.0, top, text_w, info_m.height()), Qt.AlignCenter, info)

        painter.restore()

    def paint_decorations(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        if not self._editing:
            return
        rect = self.content_rect()
        attrs = self._attrs
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        # Interactive 3D View Mode border
        pen = QPen(QColor(theme.ACCENT), 2.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 4.0, 4.0)

        # Orientation XYZ axes and Key Light direction gizmos
        paint_axes_gizmo(
            painter,
            rect,
            float(attrs.get("yaw", 35.0)),
            float(attrs.get("pitch", 20.0)),
            float(attrs.get("roll", 0.0)),
        )
        paint_light_gizmo(
            painter,
            rect,
            float(attrs.get("light_azimuth", 45.0)),
            float(attrs.get("light_elevation", 35.0)),
        )

        # Compact HUD hint banner at the top of the 3D viewport
        if rect.width() >= 180.0 and rect.height() >= 90.0:
            font = QFont()
            font.setPixelSize(11)
            painter.setFont(font)
            metrics = QFontMetricsF(font)
            hint = "3D View · Drag: Orbit · Shift/Mid: Pan · Alt: Light · Esc: Done"
            if metrics.horizontalAdvance(hint) + 24.0 > rect.width() - 96.0:
                hint = "3D View · Drag: Orbit · Esc: Done"
            tw = min(rect.width() - 16.0, metrics.horizontalAdvance(hint) + 18.0)
            pill = QRectF(rect.center().x() - tw * 0.5, rect.top() + 8.0, tw, 22.0)
            painter.setPen(QPen(QColor(255, 255, 255, 35), 1.0))
            painter.setBrush(QColor(18, 21, 28, 210))
            painter.drawRoundedRect(pill, 6.0, 6.0)
            painter.setPen(QColor("#e2e6f0"))
            painter.drawText(pill, Qt.AlignCenter, hint)

        painter.restore()
