"""The Qt side of the image plugin."""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QImageReader, QMovie, QPainter, QPen, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QFileDialog

from pixelref.plugin_api.contracts import IngestHandler, InspectorSection, PaletteButton, SelectionInfo
from pixelref.plugin_api.ui import widgets as w

from . import MIME_EXTENSIONS, SUPPORTED_IMAGE_EXTS
from .items import PixmapItem, prune_frame_cache


def decode_image(data: bytes) -> QImage:
    """Decode image bytes, honouring EXIF orientation like files on disk."""
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    return reader.read()


def png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


class PixmapCache:
    def __init__(self, session: Any) -> None:
        self.session = session
        self.pixmaps: dict[tuple[str, Any], QPixmap] = {}

    def load(self, attrs: dict[str, Any]) -> tuple[QPixmap, tuple[str, Any] | None]:
        """Pixmap for an image node: its embedded asset, or else its linked file."""
        asset_id = str(attrs.get("asset", "") or "")
        if not asset_id:
            return self._load_file(str(attrs.get("path", "") or ""))
        key = ("asset", asset_id)
        cached = self.pixmaps.get(key)
        if cached is not None:
            return cached, key
        asset = self.session.scene.get_asset(asset_id)
        image = decode_image(asset.data) if asset is not None else QImage()
        if image.isNull():
            return self.placeholder("embedded image"), None
        pixmap = QPixmap.fromImage(image)
        self.pixmaps[key] = pixmap
        return pixmap, key

    def _load_file(self, path_value: str) -> tuple[QPixmap, tuple[str, Any] | None]:
        if not path_value:
            return self.placeholder(""), None
        path = self.session.resolve_path(path_value)
        try:
            key = (str(path.resolve()), path.stat().st_mtime_ns)
        except OSError:
            return self.placeholder(path_value), None
        cached = self.pixmaps.get(key)
        if cached is not None:
            return cached, key
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)  # honour EXIF orientation of photos
        image = reader.read()
        if image.isNull():
            return self.placeholder(path_value), None
        pixmap = QPixmap.fromImage(image)
        self.pixmaps[key] = pixmap
        return pixmap, key

    @staticmethod
    def placeholder(path_value: str) -> QPixmap:
        ph = QPixmap(300, 200)
        ph.fill(QColor("#1a1c21"))
        painter = QPainter(ph)
        painter.setPen(QPen(QColor("#3d4452"), 1))
        painter.drawRect(ph.rect().adjusted(1, 1, -2, -2))
        painter.setPen(QPen(QColor("#606878")))
        name = Path(path_value).name if path_value else "No file"
        painter.drawText(ph.rect(), Qt.AlignCenter, f"Missing\n{name}")
        painter.end()
        return ph

    ANIMATED = {"image/gif": b"gif", "image/webp": b"webp"}

    def attach_gif(self, item: PixmapItem, attrs: dict[str, Any]) -> None:
        """Animate GIFs and animated WebPs, whether embedded or linked."""
        asset = self.session.scene.get_asset(str(attrs.get("asset", "") or ""))
        if asset is not None:
            fmt = self.ANIMATED.get(asset.mime)
            if fmt is None:
                return
            buffer = QBuffer()
            buffer.setData(QByteArray(asset.data))
            buffer.open(QIODevice.ReadOnly)
            movie = QMovie(buffer, QByteArray(fmt))
            data = buffer.data()

            def open_reader(data: QByteArray = data, fmt: bytes = fmt) -> QImageReader:
                source = QBuffer()
                source.setData(data)
                source.open(QIODevice.ReadOnly)
                reader = QImageReader(source, QByteArray(fmt))
                reader._source = source  # keep the buffer alive while reading
                return reader
        else:
            path = self.session.resolve_path(str(attrs.get("path", "")))
            if path.suffix.lower() not in (".gif", ".webp") or not path.exists():
                return
            movie = QMovie(str(path))

            def open_reader(path: str = str(path)) -> QImageReader:
                return QImageReader(path)
        if movie.isValid() and movie.frameCount() != 1:
            if asset is not None:
                item._movie_buffer = buffer  # the movie reads from it lazily; keep it alive
            item.set_frame_source(open_reader)
            item.set_movie(movie)
            for hook in self.movie_hooks():
                try:
                    hook(item)
                except Exception:  # noqa: BLE001
                    logging.getLogger(__name__).exception("image.movie_hooks contribution failed")

    def movie_hooks(self) -> list[Any]:
        return list(self.session.extensions.contributions("image.movie_hooks"))

    def prune(self, used: set[Any]) -> None:
        for key in list(self.pixmaps):
            if key not in used:
                del self.pixmaps[key]


class ImageSection(w.InspectorSection):
    def __init__(self, ui: Any, parent: Any) -> None:
        super().__init__("image", "Image", parent)
        self.ui = ui
        self._name: str | None = None

    def refresh(self, info: SelectionInfo) -> None:
        # Its rows come from other plugins (inspector.rows: crop, storage…);
        # grayscale and the other effects have their own section.
        self._name = info.names[0] if info.single else None


MIN_ALLOCATION_LIMIT_MB = 2048


def activate_image_ui(ui: Any) -> None:
    session = ui.session
    canvas = ui.canvas
    # Qt refuses to decode images over 256 MB (e.g. 8200 × 8200 RGBA) by default;
    # a reference board must show big scans and panoramas, not "Missing".
    limit = QImageReader.allocationLimit()
    if 0 < limit < MIN_ALLOCATION_LIMIT_MB:
        QImageReader.setAllocationLimit(MIN_ALLOCATION_LIMIT_MB)  # process-wide; other windows share it
    cache = PixmapCache(session)
    api = session.services.require("images")
    headless_sizer = api.sizer

    def pixmap_size(attrs: dict[str, Any]) -> tuple[int, int] | None:
        pixmap, key = cache.load(attrs)
        return (pixmap.width(), pixmap.height()) if key is not None else None

    api.sizer = pixmap_size
    ui.track(lambda: setattr(api, "sizer", headless_sizer))
    ui.provide_service("image_cache", cache)
    ui.declare_extension_point("image.movie_hooks",
                               "callable(item): called when an animated image's item starts its movie")

    def factory(_canvas: Any, node: dict[str, Any]) -> PixmapItem:
        attrs = node.get("attrs", {})
        pixmap, key = cache.load(attrs)
        item = PixmapItem(node["name"], pixmap)
        item.cache_key = key
        item.set_crop(attrs.get("crop"))
        cache.attach_gif(item, attrs)
        return item

    def describe(name: str, node: dict[str, Any]) -> list[str]:
        attrs = node.get("attrs", {})
        parts = []
        item = canvas.item(name)
        if isinstance(item, PixmapItem):
            rect = item.crop_rect()
            parts.append(f"{rect.width():.0f} × {rect.height():.0f} px")
        asset = session.scene.get_asset(str(attrs.get("asset", "") or ""))
        if asset is not None:
            kind = MIME_EXTENSIONS.get(asset.mime, "").lstrip(".").upper() or "image"
            parts.append(f"{kind} · embedded {w.format_size(asset.size)}")
        elif attrs.get("path"):
            parts.append("linked file")
        return parts

    def double_click(view: Any, item: Any) -> bool:
        bounds = canvas.scene_bounds([item])
        if not bounds.isNull():
            view.fit_rect(bounds)
        return True

    ui.node_ui("image", factory=factory, label="Image", icon="image", badge=("#c9a96e", "I"),
               describe=describe, double_click=double_click)

    def prune(_event: Any) -> None:
        used = {getattr(item, "cache_key", None) for item in canvas.items.values()}
        used.discard(None)
        cache.prune(used)
        prune_frame_cache(used)

    # After every refresh (not just full rebuilds), so deleted images free their pixmaps.
    ui.on_ui("canvas.refreshed", prune)

    # ---- Inspector -------------------------------------------------------------------------

    ui.contribute("inspector.sections", InspectorSection(
        key="image", title="Image", order=30,
        applies=lambda info: info.single and info.node["type"] == "image",
        build=lambda parent, _inspector: ImageSection(ui, parent),
    ))

    # ---- Ingest ------------------------------------------------------------------------------

    network = QNetworkAccessManager()
    ui.track(network.deleteLater)

    def local_paths(mime: Any) -> list[Path]:
        paths: list[Path] = []
        for url in mime.urls() if mime.hasUrls() else []:
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.is_dir():
                paths.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in SUPPORTED_IMAGE_EXTS))
            elif path.suffix.lower() in SUPPORTED_IMAGE_EXTS:
                paths.append(path)
        return paths

    def describe_files(mime: Any) -> str | None:
        if not mime.hasUrls():
            return None
        local = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        images = [f for f in local if Path(f).suffix.lower() in SUPPORTED_IMAGE_EXTS]
        folders = [f for f in local if "." not in f.replace("\\", "/").rsplit("/", 1)[-1]]
        if images:
            return f"Drop to add {len(images)} image{'s' if len(images) != 1 else ''}"
        if folders:
            return "Drop to add the images in this folder"
        if any(not u.isLocalFile() for u in mime.urls()):
            return "Drop to download and add the image"
        return None

    def ingest_files(_view: Any, pos: QPointF, mime: Any) -> Any:
        paths = local_paths(mime)
        if not paths:
            return None
        return len(api.create_row(paths, pos.x(), pos.y())), 0

    def ingest_raw(_view: Any, pos: QPointF, mime: Any) -> Any:
        for raw_format, ext in (("image/gif", ".gif"), ("image/webp", ".webp")):
            if mime.hasFormat(raw_format):
                raw = bytes(mime.data(raw_format))
                if raw:
                    api.create_from_bytes(raw, "Pasted", pos.x(), pos.y(), f"pasted{ext}")
                    return 1, 0
        return None

    def ingest_bitmap(_view: Any, pos: QPointF, mime: Any) -> Any:
        if not mime.hasImage():
            return None
        image_data = mime.imageData()
        if image_data is None:
            return None
        image = image_data if isinstance(image_data, QImage) else QPixmap(image_data).toImage()
        if image.isNull():
            return None
        api.create_from_bytes(png_bytes(image), "Pasted", pos.x(), pos.y(), "pasted.png")
        return 1, 0

    def html_sources(mime: Any) -> list[str]:
        html = mime.html() if mime.hasHtml() else ""
        return re.findall(r"""<img[^>]+src\s*=\s*["']([^"']+)["']""", html or "", flags=re.IGNORECASE)

    def decode_data_url(url: str) -> tuple[bytes, str] | None:
        match = re.match(r"data:image/([\w.+-]+);base64,(.*)", url, flags=re.DOTALL)
        if not match:
            return None
        ext = {"jpeg": ".jpg", "svg+xml": ".svg"}.get(match.group(1).lower(), f".{match.group(1).lower()}")
        try:
            return base64.b64decode(match.group(2)), ext
        except ValueError:
            return None

    def ingest_inline(_view: Any, pos: QPointF, mime: Any) -> Any:
        inline = [src for src in html_sources(mime) if src.startswith("data:image/")]
        added = 0
        for offset, src in enumerate(inline):
            data = decode_data_url(src)
            if data:
                api.create_from_bytes(data[0], "Web", pos.x() + offset * 30, pos.y() + offset * 30, f"web{data[1]}")
                added += 1
        return (added, 0) if added else None

    def remote_urls(mime: Any) -> list[QUrl]:
        urls = [QUrl(src) for src in html_sources(mime) if src.startswith("http")]
        if not urls and mime.hasUrls():
            urls = [u for u in mime.urls() if u.scheme() in ("http", "https")]
        text = mime.text().strip() if mime.hasText() else ""
        if not urls and re.fullmatch(r"https?://\S+", text):
            urls = [QUrl(text)]
        return urls

    def ingest_remote(_view: Any, pos: QPointF, mime: Any) -> Any:
        urls = remote_urls(mime)
        if not urls:
            return None
        text = mime.text().strip() if mime.hasText() else ""
        download(urls[:20], pos, fallback_text=text)
        return "handled"

    def download(urls: list[QUrl], pos: QPointF, fallback_text: str = "") -> None:
        for index, url in enumerate(urls):
            request = QNetworkRequest(url)
            request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "Mozilla/5.0 (pixelRef)")
            request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                                 QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
            reply = network.get(request)
            position = pos + QPointF(index * 30.0, index * 30.0)
            reply.finished.connect(
                lambda r=reply, p=position, t=fallback_text if len(urls) == 1 else "": downloaded(r, p, t)
            )
        ui.status(f"Downloading {len(urls)} image(s)…")

    def fallback_note(text: str, pos: QPointF) -> None:
        notes = session.services.get("notes")
        if notes is not None:
            with session.undo.chunk("Paste text"):
                notes.create(text, pos.x(), pos.y())
            session.notify_changed("paste")

    def downloaded(reply: QNetworkReply, pos: QPointF, fallback_text: str) -> None:
        reply.deleteLater()
        url = reply.url().toString()
        if reply.error() != QNetworkReply.NetworkError.NoError:
            ui.status(f"Download failed: {reply.errorString()}")
            if fallback_text:
                fallback_note(fallback_text, pos)
            return
        data = bytes(reply.readAll())
        image = QImage()
        if not image.loadFromData(data):
            if fallback_text:
                fallback_note(fallback_text, pos)
            else:
                ui.status(f"Not an image: {url}")
            return
        content_type = str(reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader) or "").lower()
        known = {"png": ".png", "jpeg": ".jpg", "jpg": ".jpg", "gif": ".gif", "webp": ".webp", "bmp": ".bmp"}
        ext = next((value for key, value in known.items() if key in content_type), None)
        if ext is None:
            suffix = Path(reply.url().path()).suffix.lower()
            ext = suffix if suffix in SUPPORTED_IMAGE_EXTS else None
        if ext is None:
            data, ext = png_bytes(image), ".png"  # unknown container (e.g. AVIF): re-encode
        url_name = Path(reply.url().path()).name
        stem = Path(url_name).stem or "Web"
        filename = url_name if Path(url_name).suffix else f"{stem}{ext}"
        with session.undo.chunk("Add web image"):
            name = api.create_from_bytes(data, stem, pos.x(), pos.y(), filename)
        session.notify_changed("download")
        if session.scene.has(name):
            ui.app.run_command("select", [name])
        ui.status(f"Downloaded {Path(reply.url().path()).name or url}")

    for handler in (
        IngestHandler("image.files", 10, lambda m: bool(m.hasUrls()), describe_files, ingest_files),
        IngestHandler("image.raw", 20, lambda m: m.hasFormat("image/gif") or m.hasFormat("image/webp"),
                      lambda m: "Drop to add the image", ingest_raw),
        IngestHandler("image.bitmap", 30, lambda m: m.hasImage(), lambda m: "Drop to add the image", ingest_bitmap),
        IngestHandler("image.inline", 40, lambda m: m.hasHtml(), lambda m: "Drop to add the image", ingest_inline),
        IngestHandler("image.remote", 50, lambda m: True, lambda m: None, ingest_remote),
    ):
        ui.contribute("ingest.handlers", handler, order=handler.order)

    # ---- Clipboard export --------------------------------------------------------------------

    def export_to_clipboard(names: list[str], mime: Any) -> None:
        images = [canvas.item(n) for n in names if isinstance(canvas.item(n), PixmapItem)]
        if len(images) == 1:
            item = images[0]
            mime.setImageData(item.pixmap().copy(item.crop_rect().toRect()).toImage())
        linked = [session.resolve_path(str(canvas.nodes[i.node_name]["attrs"].get("path", "")))
                  for i in images if not canvas.nodes[i.node_name]["attrs"].get("asset")]
        if linked:
            mime.setUrls([QUrl.fromLocalFile(str(path)) for path in linked])

    ui.contribute("clipboard.exporters", export_to_clipboard)

    # ---- Actions -----------------------------------------------------------------------------

    def insert_images_dialog() -> None:
        """Choose image files and add them in a row at the view centre."""
        paths, _ = QFileDialog.getOpenFileNames(ui.app.main_window(), "Insert Images", "",
                                                "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.tif *.tiff)")
        if not paths:
            return
        view = ui.app.active_view()
        center = view.center() if view is not None else QPointF()
        names = ui.app.run_command("image.import_images", paths, [center.x(), center.y()])
        if names:
            ui.status(f"Added {len(names)} image(s)")

    ui.command("insert_dialog", insert_images_dialog, undoable=False)
    ui.action("image.insert", "image.insert_dialog", "Insert Images…", icon="image",
              keywords="add import picture photo file", category="File")
    ui.action("image.grayscale", "image.toggle_grayscale", "Toggle Grayscale",
              keywords="greyscale black white monochrome desaturate", category="Transform")
    ui.bind("Ctrl+I", "image.insert")
    ui.bind("Shift+G", "image.grayscale")

    def selection_has_image() -> bool:
        return any(canvas.nodes.get(n, {}).get("type") == "image" for n in session.selection.names)

    ui.menu("canvas", "image.insert", group="create", order=10, when="selection.count == 0")
    ui.menu("canvas", "image.insert", path="File", group="file.import", order=0)
    ui.menu("canvas", "image.grayscale", path="Image", group="image", order=20, when=selection_has_image)
    ui.place_submenu("canvas", "Image", "transform", 1, icon="image")
    ui.contribute("palette.buttons", PaletteButton("image.insert", "Image", "image", order=10))
