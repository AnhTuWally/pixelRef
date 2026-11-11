"""3D mesh & texture rendering to QImage (hardware OpenGL FBO with CPU fallback).

Supports perspective and orthographic cameras, adjustable key-light direction,
model textures (.png, .jpg, .webp, embedded GLB textures), and seven reference
shading modes:

* ``studio``     — 3-point key/fill/rim lighting with texture, material or base colours
* ``clay``       — uniform sculpting clay with soft wrap lighting for form study
* ``matcap``     — view-space sculpting MatCap (warm terracotta/studio sphere)
* ``flat``       — unlit diffuse / texture colour
* ``normals``    — view-space RGB surface normal map
* ``wireframe``  — topology edges only
* ``silhouette`` — solid flat silhouette for negative-space & readability study
"""

from __future__ import annotations

import atexit
import ctypes
import ctypes.util
import math
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication, QIODevice, QLineF, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QImage, QImageReader, QOffscreenSurface,
    QOpenGLContext, QPainter, QPen, QPolygonF, QSurfaceFormat,
)
from PySide6.QtOpenGL import (
    QOpenGLBuffer, QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat,
    QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture, QOpenGLVertexArrayObject,
)

from .loader import MeshData

INTERACTIVE_MAX_FACES = 12000
MAX_RENDER_DIM = 1280
MAX_TEXTURE_DIM = 2048
MAX_GPU_MESH_CACHE = 16
MAX_TEXTURE_CACHE = 16

_SHADING_TO_INT = {
    "studio": 0,
    "clay": 1,
    "matcap": 2,
    "flat": 3,
    "normals": 4,
    "wireframe": 5,
    "silhouette": 6,
}


@dataclass(frozen=True)
class RenderParams:
    width: int = 400
    height: int = 400
    dpr: float = 1.0
    yaw: float = 35.0
    pitch: float = 20.0
    roll: float = 0.0
    distance: float = 2.6
    target: tuple[float, float] = (0.0, 0.0)
    projection: str = "perspective"
    fov: float = 45.0
    shading: str = "studio"
    wireframe_overlay: bool = False
    show_texture: bool = True
    texture_key: Any = None
    base_color: tuple[int, int, int] = (196, 200, 208)
    background_color: tuple[int, int, int, int] = (24, 27, 34, 0)
    light_azimuth: float = 45.0
    light_elevation: float = 35.0
    light_intensity: float = 1.0
    ambient: float = 0.25
    interactive: bool = False


def camera_basis(yaw_deg: float, pitch_deg: float, roll_deg: float = 0.0) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]:
    """Orthonormal view-space basis ``(right, up, forward)`` for orbit angles."""
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)

    fx, fy, fz = cp * sy, sp, cp * cy
    rx, ry, rz = cy, 0.0, -sy
    ux = fy * rz - fz * ry
    uy = fz * rx - fx * rz
    uz = fx * ry - fy * rx

    if abs(roll_deg) > 1e-4:
        roll = math.radians(roll_deg)
        cr, sr = math.cos(roll), math.sin(roll)
        nrx = rx * cr + ux * sr
        nry = ry * cr + uy * sr
        nrz = rz * cr + uz * sr
        ux = ux * cr - rx * sr
        uy = uy * cr - ry * sr
        uz = uz * cr - rz * sr
        rx, ry, rz = nrx, nry, nrz

    return (rx, ry, rz), (ux, uy, uz), (fx, fy, fz)


def light_vector(azimuth_deg: float, elevation_deg: float) -> tuple[float, float, float]:
    """Unit direction toward the key light in view space (+X right, +Y up, +Z viewer)."""
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    ce = math.cos(el)
    return (ce * math.sin(az), math.sin(el), ce * math.cos(az))


# ---- Texture Decoding & Cache -------------------------------------------------------------------


class TextureStore:
    """LRU cache of decoded QImages for 3D model textures (scaled to MAX_TEXTURE_DIM)."""

    def __init__(self, max_entries: int = MAX_TEXTURE_CACHE) -> None:
        self.max_entries = max_entries
        self._images: OrderedDict[Any, QImage] = OrderedDict()

    def _put(self, key: Any, image: QImage) -> QImage:
        self._images[key] = image
        self._images.move_to_end(key)
        while len(self._images) > self.max_entries:
            self._images.popitem(last=False)
        return image

    def load_path(self, path: Path) -> tuple[Any, QImage | None]:
        try:
            resolved = path.resolve()
            stat = resolved.stat()
        except OSError:
            return None, None
        key = ("tex_file", str(resolved), stat.st_mtime_ns, stat.st_size)
        cached = self._images.get(key)
        if cached is not None:
            self._images.move_to_end(key)
            return key, cached

        reader = QImageReader(str(resolved))
        reader.setAutoTransform(True)
        sz = reader.size()
        if sz.isValid() and (sz.width() > MAX_TEXTURE_DIM or sz.height() > MAX_TEXTURE_DIM):
            reader.setScaledSize(sz.scaled(QSize(MAX_TEXTURE_DIM, MAX_TEXTURE_DIM), Qt.KeepAspectRatio))
        img = reader.read()
        if img.isNull():
            return None, None
        if img.format() != QImage.Format_RGBA8888:
            img = img.convertToFormat(QImage.Format_RGBA8888)
        return key, self._put(key, img)

    def load_bytes(self, data: bytes, cache_id: Any) -> tuple[Any, QImage | None]:
        key = ("tex_bytes", cache_id, len(data))
        cached = self._images.get(key)
        if cached is not None:
            self._images.move_to_end(key)
            return key, cached

        ba = QByteArray(data)
        buf = QBuffer(ba)
        buf.open(QIODevice.ReadOnly)
        reader = QImageReader(buf)
        reader.setAutoTransform(True)
        sz = reader.size()
        if sz.isValid() and (sz.width() > MAX_TEXTURE_DIM or sz.height() > MAX_TEXTURE_DIM):
            reader.setScaledSize(sz.scaled(QSize(MAX_TEXTURE_DIM, MAX_TEXTURE_DIM), Qt.KeepAspectRatio))
        img = reader.read()
        buf.close()
        if img.isNull():
            return None, None
        if img.format() != QImage.Format_RGBA8888:
            img = img.convertToFormat(QImage.Format_RGBA8888)
        return key, self._put(key, img)


TEXTURE_STORE = TextureStore()


def resolve_mesh_texture(
    mesh: MeshData,
    override_path: Path | None = None,
    override_bytes: bytes | None = None,
    override_id: Any = None,
) -> tuple[Any, QImage | None]:
    """Return ``(texture_key, QImage)`` for ``mesh`` (or the user's override texture)."""
    if override_bytes:
        return TEXTURE_STORE.load_bytes(override_bytes, override_id or id(override_bytes))
    if override_path is not None:
        return TEXTURE_STORE.load_path(override_path)
    if mesh.texture_bytes:
        return TEXTURE_STORE.load_bytes(mesh.texture_bytes, id(mesh))
    if mesh.texture_path is not None:
        return TEXTURE_STORE.load_path(mesh.texture_path)
    return None, None


# ---- Hardware-Accelerated OpenGL Offscreen Renderer ---------------------------------------------

_VERTEX_SHADER = """#version 330 core
layout(location = 0) in vec3 a_pos;
layout(location = 1) in vec3 a_normal;
layout(location = 2) in vec2 a_uv;
layout(location = 3) in vec3 a_color;

uniform vec3 u_center;
uniform float u_inv_radius;
uniform vec3 u_right;
uniform vec3 u_up;
uniform vec3 u_forward;
uniform vec2 u_pan;
uniform float u_dist;
uniform int u_is_persp;
uniform float u_focal;
uniform vec2 u_aspect_scale;

out vec3 v_view_pos;
out vec3 v_normal;
out vec2 v_uv;
out vec3 v_color;

void main() {
    vec3 p = (a_pos - u_center) * u_inv_radius;
    float vx = dot(p, u_right) + u_pan.x;
    float vy = dot(p, u_up) + u_pan.y;
    float vz = dot(p, u_forward);
    v_view_pos = vec3(vx, vy, vz);

    v_normal = vec3(
        dot(a_normal, u_right),
        dot(a_normal, u_up),
        dot(a_normal, u_forward)
    );
    v_uv = a_uv;
    v_color = a_color;

    if (u_is_persp == 1) {
        float near_z = 0.08;
        float far_z = max(25.0, u_dist + 12.0);
        float cam_d = u_dist - vz;
        float clip_x = vx * u_focal * u_aspect_scale.x;
        float clip_y = vy * u_focal * u_aspect_scale.y;
        float clip_z = ((far_z + near_z) / (far_z - near_z)) * cam_d - (2.0 * far_z * near_z) / (far_z - near_z);
        gl_Position = vec4(clip_x, clip_y, clip_z, cam_d);
    } else {
        float clip_x = vx * u_focal * u_aspect_scale.x;
        float clip_y = vy * u_focal * u_aspect_scale.y;
        float clip_z = -vz * 0.1;
        gl_Position = vec4(clip_x, clip_y, clip_z, 1.0);
    }
}
"""

_FRAGMENT_SHADER = """#version 330 core
in vec3 v_view_pos;
in vec3 v_normal;
in vec2 v_uv;
in vec3 v_color;

uniform int u_mode;
uniform int u_has_normals;
uniform int u_has_colors;
uniform int u_use_texture;
uniform sampler2D u_tex;
uniform vec3 u_base_color;
uniform vec3 u_light_dir;
uniform vec3 u_half_dir;
uniform float u_light_int;
uniform float u_ambient;
uniform vec4 u_wire_color;

out vec4 fragColor;

void main() {
    if (u_mode == 7) {
        fragColor = u_wire_color;
        return;
    }
    if (u_mode == 6) {
        fragColor = vec4(u_base_color, 1.0);
        return;
    }
    if (u_mode == 5) {
        fragColor = vec4(0.75, 0.80, 0.90, 0.90);
        return;
    }

    vec3 n;
    if (u_has_normals == 1 && dot(v_normal, v_normal) > 1e-6) {
        n = normalize(v_normal);
    } else {
        vec3 dx = dFdx(v_view_pos);
        vec3 dy = dFdy(v_view_pos);
        vec3 fn = cross(dx, dy);
        float len = length(fn);
        n = (len > 1e-9) ? (fn / len) : vec3(0.0, 0.0, 1.0);
    }
    if (n.z < 0.0) {
        n = -n;
    }

    if (u_mode == 4) {
        fragColor = vec4(n * 0.5 + 0.5, 1.0);
        return;
    }

    if (u_mode == 2) {
        float spec_dot = max(0.0, dot(n, vec3(-0.38, 0.48, 0.79)));
        float spec = pow(spec_dot, 24.0) * (95.0 / 255.0);
        float core = max(0.0, n.z);
        float warm = 0.22 + 0.78 * pow(core, 0.75);
        float cavity = pow(1.0 - core, 2.2);
        float hemi = 0.5 + 0.5 * n.y;
        float r = (198.0 * warm + 28.0 * hemi - 42.0 * cavity) / 255.0 + spec;
        float g = (112.0 * warm + 22.0 * hemi - 35.0 * cavity) / 255.0 + spec * 0.92;
        float b = (94.0 * warm + 26.0 * hemi - 25.0 * cavity) / 255.0 + spec * 0.88;
        fragColor = vec4(clamp(vec3(r, g, b), 0.0, 1.0), 1.0);
        return;
    }

    vec3 albedo = u_base_color;
    if (u_mode != 1) {
        if (u_use_texture == 1) {
            albedo = texture(u_tex, v_uv).rgb;
        } else if (u_has_colors == 1) {
            albedo = v_color;
        }
    }

    if (u_mode == 3) {
        fragColor = vec4(clamp(albedo, 0.0, 1.0), 1.0);
        return;
    }

    float ndotl = dot(n, u_light_dir);
    float wrap;
    if (u_mode == 1) {
        wrap = max(0.0, (ndotl + 0.22) / 1.22) * u_light_int;
    } else {
        wrap = max(0.0, ndotl) * u_light_int;
    }

    float fill_dot = max(0.0, dot(n, vec3(-0.55, 0.60, 0.58)));
    float fill = fill_dot * (0.22 + 0.25 * u_ambient);
    float rim = pow(1.0 - max(0.0, n.z), 2.6) * 0.24 * max(0.3, u_light_int);

    float ndoth = max(0.0, dot(n, u_half_dir));
    float spec_power = (u_mode == 1) ? 18.0 : 28.0;
    float spec_strength = ((u_mode == 1) ? 26.0 : ((u_use_texture == 1) ? 18.0 : 38.0)) / 255.0;
    float spec = (ndotl > 0.0) ? (pow(ndoth, spec_power) * spec_strength * u_light_int) : 0.0;

    vec3 mult = vec3(
        u_ambient + wrap * 1.02 + fill * 0.82 + rim * 0.95,
        u_ambient + wrap * 0.99 + fill * 0.90 + rim * 0.98,
        u_ambient + wrap * 0.95 + fill * 1.05 + rim * 1.06
    );
    vec3 col = albedo * mult + vec3(spec);
    fragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}
"""


def _load_native_gl() -> Any:
    """Load the native OpenGL library for glDrawElements and glPolygonMode."""
    candidates = []
    if sys.platform.startswith("linux"):
        candidates = ["libGL.so.1", "libGL.so"]
    elif sys.platform == "win32":
        candidates = ["opengl32"]
    elif sys.platform == "darwin":
        candidates = ["/System/Library/Frameworks/OpenGL.framework/OpenGL"]
    found = ctypes.util.find_library("GL")
    if found and found not in candidates:
        candidates.append(found)
    for name in candidates:
        try:
            lib = ctypes.windll.LoadLibrary(name) if sys.platform == "win32" else ctypes.cdll.LoadLibrary(name)
            lib.glDrawElements.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
            lib.glPolygonMode.argtypes = [ctypes.c_uint, ctypes.c_uint]
            return lib
        except Exception:  # noqa: BLE001
            continue
    return None


class _GPUMeshBuffers:
    """OpenGL VBOs/IBO for a single MeshData instance."""

    def __init__(self, mesh: MeshData) -> None:
        self.vertex_count = mesh.gpu_vertex_count
        self.index_count = mesh.gpu_index_count
        self.center = mesh.gpu_center
        self.inv_radius = mesh.gpu_inv_radius
        self.has_normals = mesh.gpu_normals is not None
        self.has_uvs = mesh.gpu_uvs is not None
        self.has_colors = mesh.gpu_colors is not None
        self.indexed = mesh.gpu_indices is not None and self.index_count > 0

        self.pos_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self.pos_vbo.create()
        self.pos_vbo.bind()
        if mesh.gpu_positions:
            self.pos_vbo.allocate(mesh.gpu_positions, len(mesh.gpu_positions))

        self.norm_vbo: QOpenGLBuffer | None = None
        if mesh.gpu_normals:
            self.norm_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self.norm_vbo.create()
            self.norm_vbo.bind()
            self.norm_vbo.allocate(mesh.gpu_normals, len(mesh.gpu_normals))

        self.uv_vbo: QOpenGLBuffer | None = None
        if mesh.gpu_uvs:
            self.uv_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self.uv_vbo.create()
            self.uv_vbo.bind()
            self.uv_vbo.allocate(mesh.gpu_uvs, len(mesh.gpu_uvs))

        self.col_vbo: QOpenGLBuffer | None = None
        if mesh.gpu_colors:
            self.col_vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
            self.col_vbo.create()
            self.col_vbo.bind()
            self.col_vbo.allocate(mesh.gpu_colors, len(mesh.gpu_colors))

        self.ibo: QOpenGLBuffer | None = None
        if self.indexed and mesh.gpu_indices:
            self.ibo = QOpenGLBuffer(QOpenGLBuffer.IndexBuffer)
            self.ibo.create()
            self.ibo.bind()
            self.ibo.allocate(mesh.gpu_indices, len(mesh.gpu_indices))

    def destroy(self) -> None:
        for buf in (self.pos_vbo, self.norm_vbo, self.uv_vbo, self.col_vbo, self.ibo):
            if buf is not None and buf.isCreated():
                buf.destroy()


class _GLRenderer:
    """Shared offscreen OpenGL renderer for all 3D reference items."""

    GL_TRIANGLES = 0x0004
    GL_FLOAT = 0x1406
    GL_UNSIGNED_INT = 0x1405
    GL_DEPTH_TEST = 0x0B71
    GL_BLEND = 0x0BE2
    GL_SRC_ALPHA = 0x0302
    GL_ONE_MINUS_SRC_ALPHA = 0x0303
    GL_COLOR_BUFFER_BIT = 0x00004000
    GL_DEPTH_BUFFER_BIT = 0x00000100
    GL_FRONT_AND_BACK = 0x0408
    GL_LINE = 0x1B01
    GL_FILL = 0x1B02
    GL_POLYGON_OFFSET_LINE = 0x2A02

    def __init__(self) -> None:
        self._initialized = False
        self._failed = False
        self.ctx: QOpenGLContext | None = None
        self.surface: QOffscreenSurface | None = None
        self.vao: QOpenGLVertexArrayObject | None = None
        self.prog: QOpenGLShaderProgram | None = None
        self.native_gl: Any = None
        self._mesh_buffers: OrderedDict[int, _GPUMeshBuffers] = OrderedDict()
        self._textures: OrderedDict[Any, QOpenGLTexture] = OrderedDict()
        self._white_tex: QOpenGLTexture | None = None

    def _ensure_init(self) -> bool:
        if self._initialized:
            return not self._failed
        self._initialized = True
        try:
            fmt = QSurfaceFormat()
            fmt.setVersion(3, 3)
            fmt.setProfile(QSurfaceFormat.CoreProfile)
            fmt.setDepthBufferSize(24)
            ctx = QOpenGLContext()
            ctx.setFormat(fmt)
            if not ctx.create():
                self._failed = True
                return False
            surface = QOffscreenSurface()
            surface.setFormat(ctx.format())
            surface.create()
            if not ctx.makeCurrent(surface):
                self._failed = True
                return False

            vao = QOpenGLVertexArrayObject()
            vao.create()
            vao.bind()

            prog = QOpenGLShaderProgram()
            if not prog.addShaderFromSourceCode(QOpenGLShader.Vertex, _VERTEX_SHADER):
                self._failed = True
                ctx.doneCurrent()
                return False
            if not prog.addShaderFromSourceCode(QOpenGLShader.Fragment, _FRAGMENT_SHADER):
                self._failed = True
                ctx.doneCurrent()
                return False
            if not prog.link():
                self._failed = True
                ctx.doneCurrent()
                return False

            white = QImage(2, 2, QImage.Format_RGBA8888)
            white.fill(Qt.white)
            self._white_tex = QOpenGLTexture(white)
            self.native_gl = _load_native_gl()
            self.ctx = ctx
            self.surface = surface
            self.vao = vao
            self.prog = prog
            app = QCoreApplication.instance()
            if app is not None:
                app.aboutToQuit.connect(self.cleanup)
            ctx.doneCurrent()
            return True
        except Exception:  # noqa: BLE001
            self._failed = True
            return False

    def cleanup(self) -> None:
        if self.ctx is None or self.surface is None:
            return
        try:
            if self.ctx.makeCurrent(self.surface):
                for bufs in self._mesh_buffers.values():
                    bufs.destroy()
                self._mesh_buffers.clear()
                for tex in self._textures.values():
                    if tex.isCreated():
                        tex.destroy()
                self._textures.clear()
                if self._white_tex is not None and self._white_tex.isCreated():
                    self._white_tex.destroy()
                self._white_tex = None
                if self.vao is not None and self.vao.isCreated():
                    self.vao.destroy()
                self.vao = None
                self.prog = None
                self.ctx.doneCurrent()
        except Exception:  # noqa: BLE001
            pass
        self.ctx = None
        self.surface = None
        self._initialized = False

    def _get_mesh_buffers(self, mesh: MeshData) -> _GPUMeshBuffers:
        key = (id(mesh), mesh.vertex_count, mesh.face_count)
        bufs = self._mesh_buffers.get(key)
        if bufs is not None:
            self._mesh_buffers.move_to_end(key)
            return bufs
        bufs = _GPUMeshBuffers(mesh)
        self._mesh_buffers[key] = bufs
        while len(self._mesh_buffers) > MAX_GPU_MESH_CACHE:
            _, old = self._mesh_buffers.popitem(last=False)
            old.destroy()
        return bufs

    def _get_texture(self, tex_key: Any, tex_image: QImage | None) -> QOpenGLTexture | None:
        if tex_key is None or tex_image is None or tex_image.isNull():
            return None
        tex = self._textures.get(tex_key)
        if tex is not None:
            self._textures.move_to_end(tex_key)
            return tex
        tex = QOpenGLTexture(tex_image)
        tex.setMinMagFilters(QOpenGLTexture.LinearMipMapLinear, QOpenGLTexture.Linear)
        tex.setWrapMode(QOpenGLTexture.Repeat)
        self._textures[tex_key] = tex
        while len(self._textures) > MAX_TEXTURE_CACHE:
            _, old_tex = self._textures.popitem(last=False)
            if old_tex.isCreated():
                old_tex.destroy()
        return tex

    def render(self, mesh: MeshData, params: RenderParams, tex_key: Any = None,
               tex_image: QImage | None = None) -> QImage | None:
        if not mesh.gpu_positions or not self._ensure_init():
            return None
        assert self.ctx is not None and self.surface is not None and self.prog is not None and self.vao is not None
        if not self.ctx.makeCurrent(self.surface):
            return None

        fbo: QOpenGLFramebufferObject | None = None
        try:
            bufs = self._get_mesh_buffers(mesh)
            if bufs.indexed and self.native_gl is None:
                return None

            dpr = max(1.0, min(2.0, float(params.dpr)))
            px_w = max(16, min(MAX_RENDER_DIM, round(params.width * dpr)))
            px_h = max(16, min(MAX_RENDER_DIM, round(params.height * dpr)))

            fbo_fmt = QOpenGLFramebufferObjectFormat()
            fbo_fmt.setAttachment(QOpenGLFramebufferObject.CombinedDepthStencil)
            if not params.interactive:
                fbo_fmt.setSamples(4)
            fbo = QOpenGLFramebufferObject(px_w, px_h, fbo_fmt)
            if not fbo.isValid():
                fbo = None
                fbo_fmt.setSamples(0)
                fbo = QOpenGLFramebufferObject(px_w, px_h, fbo_fmt)
                if not fbo.isValid():
                    return None

            fbo.bind()
            f = self.ctx.functions()
            f.glViewport(0, 0, px_w, px_h)
            f.glEnable(self.GL_DEPTH_TEST)
            f.glEnable(self.GL_BLEND)
            f.glBlendFunc(self.GL_SRC_ALPHA, self.GL_ONE_MINUS_SRC_ALPHA)

            bg_r, bg_g, bg_b, bg_a = params.background_color
            if bg_a <= 0:
                f.glClearColor(0.0, 0.0, 0.0, 0.0)
            else:
                a_f = bg_a / 255.0
                f.glClearColor((bg_r / 255.0) * a_f, (bg_g / 255.0) * a_f, (bg_b / 255.0) * a_f, a_f)
            f.glClear(self.GL_COLOR_BUFFER_BIT | self.GL_DEPTH_BUFFER_BIT)

            self.vao.bind()
            prog = self.prog
            prog.bind()

            # Bind attributes
            bufs.pos_vbo.bind()
            prog.enableAttributeArray(0)
            prog.setAttributeBuffer(0, self.GL_FLOAT, 0, 3, 12)

            if bufs.norm_vbo is not None:
                bufs.norm_vbo.bind()
                prog.enableAttributeArray(1)
                prog.setAttributeBuffer(1, self.GL_FLOAT, 0, 3, 12)
            else:
                prog.disableAttributeArray(1)
                f.glVertexAttrib3f(1, 0.0, 0.0, 1.0)

            if bufs.uv_vbo is not None:
                bufs.uv_vbo.bind()
                prog.enableAttributeArray(2)
                prog.setAttributeBuffer(2, self.GL_FLOAT, 0, 2, 8)
            else:
                prog.disableAttributeArray(2)
                f.glVertexAttrib2f(2, 0.0, 0.0)

            if bufs.col_vbo is not None:
                bufs.col_vbo.bind()
                prog.enableAttributeArray(3)
                prog.setAttributeBuffer(3, self.GL_FLOAT, 0, 3, 12)
            else:
                prog.disableAttributeArray(3)
                br, bg, bb = params.base_color
                f.glVertexAttrib3f(3, br / 255.0, bg / 255.0, bb / 255.0)

            if bufs.indexed and bufs.ibo is not None:
                bufs.ibo.bind()

            # Camera & lighting uniforms
            right, up, forward = camera_basis(params.yaw, params.pitch, params.roll)
            dist = max(0.25, float(params.distance))
            is_persp = 1 if params.projection == "perspective" else 0
            fov_rad = math.radians(max(5.0, min(120.0, float(params.fov))))
            focal = (1.0 / math.tan(fov_rad * 0.5)) if is_persp else (2.0 / max(0.2, dist * 0.85))
            min_side = float(min(px_w, px_h))
            aspect_x = (min_side * 0.90) / float(px_w)
            aspect_y = (min_side * 0.90) / float(px_h)

            lx, ly, lz = light_vector(params.light_azimuth, params.light_elevation)
            h_len = math.sqrt(lx * lx + ly * ly + (lz + 1.0) * (lz + 1.0))
            hx, hy, hz = (lx / h_len, ly / h_len, (lz + 1.0) / h_len) if h_len > 1e-9 else (0.0, 0.0, 1.0)

            cx, cy, cz = bufs.center
            f.glUniform3f(prog.uniformLocation("u_center"), cx, cy, cz)
            f.glUniform1f(prog.uniformLocation("u_inv_radius"), bufs.inv_radius)
            f.glUniform3f(prog.uniformLocation("u_right"), *right)
            f.glUniform3f(prog.uniformLocation("u_up"), *up)
            f.glUniform3f(prog.uniformLocation("u_forward"), *forward)
            f.glUniform2f(prog.uniformLocation("u_pan"), float(params.target[0]), float(params.target[1]))
            f.glUniform1f(prog.uniformLocation("u_dist"), dist)
            f.glUniform1i(prog.uniformLocation("u_is_persp"), is_persp)
            f.glUniform1f(prog.uniformLocation("u_focal"), focal)
            f.glUniform2f(prog.uniformLocation("u_aspect_scale"), aspect_x, aspect_y)

            mode_int = _SHADING_TO_INT.get(params.shading, 0)
            f.glUniform1i(prog.uniformLocation("u_mode"), mode_int)
            f.glUniform1i(prog.uniformLocation("u_has_normals"), 1 if bufs.has_normals else 0)
            f.glUniform1i(prog.uniformLocation("u_has_colors"), 1 if bufs.has_colors else 0)

            gl_tex = self._get_texture(tex_key, tex_image) if (params.show_texture and bufs.has_uvs) else None
            if gl_tex is not None:
                gl_tex.bind(0)
                f.glUniform1i(prog.uniformLocation("u_use_texture"), 1)
            else:
                if self._white_tex is not None:
                    self._white_tex.bind(0)
                f.glUniform1i(prog.uniformLocation("u_use_texture"), 0)
            f.glUniform1i(prog.uniformLocation("u_tex"), 0)

            br, bg, bb = params.base_color
            f.glUniform3f(prog.uniformLocation("u_base_color"), br / 255.0, bg / 255.0, bb / 255.0)
            f.glUniform3f(prog.uniformLocation("u_light_dir"), lx, ly, lz)
            f.glUniform3f(prog.uniformLocation("u_half_dir"), hx, hy, hz)
            f.glUniform1f(prog.uniformLocation("u_light_int"), float(params.light_intensity))
            f.glUniform1f(prog.uniformLocation("u_ambient"), float(params.ambient))

            # Draw surface or wireframe
            if params.shading == "wireframe":
                if self.native_gl is not None:
                    self.native_gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_LINE)
                self._draw_geom(f, bufs, cap_wire=True)
                if self.native_gl is not None:
                    self.native_gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_FILL)
            else:
                self._draw_geom(f, bufs, cap_wire=False)
                if params.wireframe_overlay and self.native_gl is not None and mesh.face_count <= 40000:
                    f.glEnable(self.GL_POLYGON_OFFSET_LINE)
                    f.glPolygonOffset(-1.0, -1.0)
                    self.native_gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_LINE)
                    f.glUniform1i(prog.uniformLocation("u_mode"), 7)
                    f.glUniform4f(prog.uniformLocation("u_wire_color"), 0.08, 0.10, 0.14, 0.45)
                    self._draw_geom(f, bufs, cap_wire=True)
                    self.native_gl.glPolygonMode(self.GL_FRONT_AND_BACK, self.GL_FILL)
                    f.glDisable(self.GL_POLYGON_OFFSET_LINE)

            prog.release()
            self.vao.release()
            fbo.release()

            raw_img = fbo.toImage()
            del fbo
            fbo = None
            result = raw_img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
            result.setDevicePixelRatio(dpr)

            # For ultra-dense meshes (> 40,000 faces) with wireframe_overlay, draw the capped
            # edge subset cleanly via QPainter so the mesh doesn't turn into a solid dark block.
            if params.wireframe_overlay and params.shading != "wireframe" and mesh.face_count > 40000:
                _paint_wireframe_edges_qpainter(result, mesh, params, dpr)

            return result
        except Exception:  # noqa: BLE001
            return None
        finally:
            if fbo is not None:
                del fbo
            self.ctx.doneCurrent()

    def _draw_geom(self, f: Any, bufs: _GPUMeshBuffers, cap_wire: bool = False) -> None:
        if bufs.indexed:
            count = min(bufs.index_count, 90000) if cap_wire else bufs.index_count
            self.native_gl.glDrawElements(self.GL_TRIANGLES, count, self.GL_UNSIGNED_INT, ctypes.c_void_p(0))
        else:
            count = min(bufs.vertex_count, 90000) if cap_wire else bufs.vertex_count
            f.glDrawArrays(self.GL_TRIANGLES, 0, count)


_GL_RENDERER = _GLRenderer()
atexit.register(_GL_RENDERER.cleanup)


# ---- Software Fallback & Wireframe Helpers ------------------------------------------------------


def _clamp_u8(val: float) -> int:
    if val <= 0.0:
        return 0
    if val >= 255.0:
        return 255
    return int(val + 0.5)


def _shade_color(
    shading: str,
    nx: float,
    ny: float,
    nz: float,
    base_rgb: tuple[int, int, int],
    lx: float,
    ly: float,
    lz: float,
    hx: float,
    hy: float,
    hz: float,
    light_intensity: float,
    ambient: float,
) -> QColor:
    if shading in ("silhouette", "flat"):
        return QColor(base_rgb[0], base_rgb[1], base_rgb[2])

    if shading == "normals":
        return QColor(
            _clamp_u8((nx * 0.5 + 0.5) * 255.0),
            _clamp_u8((ny * 0.5 + 0.5) * 255.0),
            _clamp_u8((nz * 0.5 + 0.5) * 255.0),
        )

    if shading == "matcap":
        spec_dot = max(0.0, nx * -0.38 + ny * 0.48 + nz * 0.79)
        spec = (spec_dot ** 24) * 95.0
        core = max(0.0, nz)
        warm = 0.22 + 0.78 * (core ** 0.75)
        cavity = (1.0 - core) ** 2.2
        hemi = 0.5 + 0.5 * ny
        r = 198.0 * warm + 28.0 * hemi - 42.0 * cavity + spec
        g = 112.0 * warm + 22.0 * hemi - 35.0 * cavity + spec * 0.92
        b = 94.0 * warm + 26.0 * hemi - 25.0 * cavity + spec * 0.88
        return QColor(_clamp_u8(r), _clamp_u8(g), _clamp_u8(b))

    ndotl = nx * lx + ny * ly + nz * lz
    if shading == "clay":
        wrap = max(0.0, (ndotl + 0.22) / 1.22) * light_intensity
    else:
        wrap = max(0.0, ndotl) * light_intensity

    fill_dot = max(0.0, nx * -0.55 + ny * 0.60 + nz * 0.58)
    fill = fill_dot * (0.22 + 0.25 * ambient)
    rim = ((1.0 - max(0.0, nz)) ** 2.6) * 0.24 * max(0.3, light_intensity)

    ndoth = max(0.0, nx * hx + ny * hy + nz * hz)
    spec_power = 18.0 if shading == "clay" else 28.0
    spec_strength = 26.0 if shading == "clay" else 38.0
    spec = (ndoth ** spec_power) * spec_strength * light_intensity if ndotl > 0.0 else 0.0

    br, bg, bb = base_rgb
    r_mult = ambient + wrap * 1.02 + fill * 0.82 + rim * 0.95
    g_mult = ambient + wrap * 0.99 + fill * 0.90 + rim * 0.98
    b_mult = ambient + wrap * 0.95 + fill * 1.05 + rim * 1.06

    return QColor(
        _clamp_u8(br * r_mult + spec),
        _clamp_u8(bg * g_mult + spec),
        _clamp_u8(bb * b_mult + spec),
    )


def _paint_wireframe_edges_qpainter(image: QImage, mesh: MeshData, params: RenderParams, dpr: float) -> None:
    log_w = image.width() / dpr
    log_h = image.height() / dpr
    cx, cy = log_w * 0.5, log_h * 0.5
    half_min = min(log_w, log_h) * 0.45
    right, up, forward = camera_basis(params.yaw, params.pitch, params.roll)
    rx, ry, rz = right
    ux, uy, uz = up
    fx, fy, fz = forward
    dist = max(0.25, float(params.distance))
    pan_x, pan_y = float(params.target[0]), float(params.target[1])
    is_persp = params.projection == "perspective"
    fov_rad = math.radians(max(5.0, min(120.0, float(params.fov))))
    focal = 1.0 / math.tan(fov_rad * 0.5) if is_persp else (2.0 / max(0.2, dist * 0.85))

    verts = mesh.vertices
    n_verts = len(verts)
    sx_arr = [0.0] * n_verts
    sy_arr = [0.0] * n_verts
    clipped = [False] * n_verts
    near_z = 0.08
    for i, (x, y, z) in enumerate(verts):
        vx = x * rx + y * ry + z * rz + pan_x
        vy = x * ux + y * uy + z * uz + pan_y
        vz = x * fx + y * fy + z * fz
        if is_persp:
            cam_d = dist - vz
            if cam_d < near_z:
                clipped[i] = True
                cam_d = near_z
            scale = (focal / cam_d) * half_min
        else:
            scale = focal * half_min
        sx_arr[i] = cx + vx * scale
        sy_arr[i] = cy - vy * scale

    edges = mesh.edges
    edge_step = max(1, len(edges) // 4500)
    lines = [
        QLineF(sx_arr[a], sy_arr[a], sx_arr[b], sy_arr[b])
        for a, b in edges[::edge_step]
        if not (clipped[a] or clipped[b])
    ]
    if lines:
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor(20, 24, 32, 115), 0.7))
        painter.drawLines(lines)
        painter.end()


def render_mesh(
    mesh: MeshData,
    params: RenderParams,
    override_tex_path: Path | None = None,
    override_tex_bytes: bytes | None = None,
    override_tex_id: Any = None,
) -> QImage:
    """Render ``mesh`` into a ``QImage`` using hardware OpenGL FBO (or software fallback)."""
    tex_key, tex_img = (
        resolve_mesh_texture(mesh, override_tex_path, override_tex_bytes, override_tex_id)
        if params.show_texture
        else (None, None)
    )

    gl_img = _GL_RENDERER.render(mesh, params, tex_key=tex_key, tex_image=tex_img)
    if gl_img is not None:
        return gl_img

    # ---- CPU QPainter Fallback ------------------------------------------------------------------
    dpr = max(1.0, min(2.0, float(params.dpr)))
    px_w = max(16, min(MAX_RENDER_DIM, round(params.width * dpr)))
    px_h = max(16, min(MAX_RENDER_DIM, round(params.height * dpr)))

    image = QImage(px_w, px_h, QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(dpr)
    bg_r, bg_g, bg_b, bg_a = params.background_color
    if bg_a <= 0:
        image.fill(Qt.transparent)
    else:
        image.fill(QColor(bg_r, bg_g, bg_b, bg_a))

    log_w = px_w / dpr
    log_h = px_h / dpr
    cx = log_w * 0.5
    cy = log_h * 0.5
    half_min = min(log_w, log_h) * 0.45

    right, up, forward = camera_basis(params.yaw, params.pitch, params.roll)
    rx, ry, rz = right
    ux, uy, uz = up
    fx, fy, fz = forward

    dist = max(0.25, float(params.distance))
    pan_x, pan_y = float(params.target[0]), float(params.target[1])
    is_persp = params.projection == "perspective"
    fov_rad = math.radians(max(5.0, min(120.0, float(params.fov))))
    focal = 1.0 / math.tan(fov_rad * 0.5) if is_persp else (2.0 / max(0.2, dist * 0.85))

    verts = mesh.vertices
    n_verts = len(verts)
    vx_arr = [0.0] * n_verts
    vy_arr = [0.0] * n_verts
    vz_arr = [0.0] * n_verts
    sx_arr = [0.0] * n_verts
    sy_arr = [0.0] * n_verts
    clipped = [False] * n_verts

    near_z = 0.08
    for i, (x, y, z) in enumerate(verts):
        vx = x * rx + y * ry + z * rz + pan_x
        vy = x * ux + y * uy + z * uz + pan_y
        vz = x * fx + y * fy + z * fz
        vx_arr[i] = vx
        vy_arr[i] = vy
        vz_arr[i] = vz
        if is_persp:
            cam_d = dist - vz
            if cam_d < near_z:
                clipped[i] = True
                cam_d = near_z
            scale = (focal / cam_d) * half_min
        else:
            scale = focal * half_min
        sx_arr[i] = cx + vx * scale
        sy_arr[i] = cy - vy * scale

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    shading = params.shading
    base_rgb = params.base_color
    lx, ly, lz = light_vector(params.light_azimuth, params.light_elevation)
    h_len = math.sqrt(lx * lx + ly * ly + (lz + 1.0) * (lz + 1.0))
    hx, hy, hz = (lx / h_len, ly / h_len, (lz + 1.0) / h_len) if h_len > 1e-9 else (0.0, 0.0, 1.0)

    light_int = float(params.light_intensity)
    amb = float(params.ambient)

    if shading != "wireframe":
        faces = mesh.faces
        f_normals = mesh.face_normals
        v_normals = mesh.vertex_normals
        f_colors = mesh.face_colors if shading not in ("clay", "silhouette") else None
        f_uvs = mesh.face_uvs if (tex_img is not None and shading not in ("clay", "silhouette")) else None
        tex_w = tex_img.width() if tex_img is not None else 0
        tex_h = tex_img.height() if tex_img is not None else 0

        n_faces = len(faces)
        step = max(1, n_faces // INTERACTIVE_MAX_FACES) if (params.interactive and n_faces > INTERACTIVE_MAX_FACES) else 1

        vn_view: list[tuple[float, float, float]] = [
            (nx * rx + ny * ry + nz * rz,
             nx * ux + ny * uy + nz * uz,
             nx * fx + ny * fy + nz * fz)
            for nx, ny, nz in v_normals
        ]

        draw_list: list[tuple[float, int, int, int, QColor]] = []
        for idx in range(0, n_faces, step):
            i0, i1, i2 = faces[idx]
            if clipped[i0] or clipped[i1] or clipped[i2]:
                continue
            x0, y0 = sx_arr[i0], sy_arr[i0]
            x1, y1 = sx_arr[i1], sy_arr[i1]
            x2, y2 = sx_arr[i2], sy_arr[i2]

            cross_z = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
            fnx, fny, fnz = f_normals[idx]
            v_fnz = fnx * fx + fny * fy + fnz * fz

            if cross_z > 0.0 and v_fnz < -0.05:
                continue

            vn0, vn1, vn2 = vn_view[i0], vn_view[i1], vn_view[i2]
            v_fnx = fnx * rx + fny * ry + fnz * rz
            v_fny = fnx * ux + fny * uy + fnz * uz
            nx = (vn0[0] + vn1[0] + vn2[0]) * 0.22 + v_fnx * 0.34
            ny = (vn0[1] + vn1[1] + vn2[1]) * 0.22 + v_fny * 0.34
            nz = (vn0[2] + vn1[2] + vn2[2]) * 0.22 + v_fnz * 0.34
            n_len = math.sqrt(nx * nx + ny * ny + nz * nz)
            if n_len > 1e-9:
                nx, ny, nz = nx / n_len, ny / n_len, nz / n_len
            else:
                nx, ny, nz = 0.0, 0.0, 1.0
            if nz < 0.0:
                nx, ny, nz = -nx, -ny, -nz

            if f_uvs is not None and tex_img is not None and idx < len(f_uvs):
                (u0, v0), (u1, v1), (u2, v2) = f_uvs[idx]
                cu = ((u0 + u1 + u2) / 3.0) % 1.0
                cv = ((v0 + v1 + v2) / 3.0) % 1.0
                tx = min(tex_w - 1, max(0, int(cu * tex_w)))
                ty = min(tex_h - 1, max(0, int(cv * tex_h)))
                tc = tex_img.pixelColor(tx, ty)
                tri_rgb = (tc.red(), tc.green(), tc.blue())
            elif f_colors is not None:
                tri_rgb = f_colors[idx]
            else:
                tri_rgb = base_rgb

            color = _shade_color(
                shading, nx, ny, nz, tri_rgb, lx, ly, lz, hx, hy, hz, light_int, amb
            )
            depth = vz_arr[i0] + vz_arr[i1] + vz_arr[i2]
            draw_list.append((depth, i0, i1, i2, color))

        draw_list.sort(key=lambda item: item[0])

        seam_pen = QPen(Qt.SolidLine)
        seam_pen.setWidthF(0.85)
        seam_pen.setJoinStyle(Qt.RoundJoin)
        for _depth, i0, i1, i2, color in draw_list:
            seam_pen.setColor(color)
            painter.setPen(seam_pen)
            painter.setBrush(QBrush(color))
            poly = QPolygonF([
                QPointF(sx_arr[i0], sy_arr[i0]),
                QPointF(sx_arr[i1], sy_arr[i1]),
                QPointF(sx_arr[i2], sy_arr[i2]),
            ])
            painter.drawPolygon(poly)

    if shading == "wireframe" or params.wireframe_overlay:
        edges = mesh.edges
        n_edges = len(edges)
        edge_step = max(1, n_edges // 5000) if (params.interactive and n_edges > 5000) else 1
        if shading == "wireframe":
            wire_color = QColor(192, 204, 230, 225)
            wire_width = 1.05
        else:
            wire_color = QColor(20, 24, 32, 115)
            wire_width = 0.7
        painter.setPen(QPen(wire_color, wire_width))
        painter.setBrush(Qt.NoBrush)
        lines: list[QLineF] = []
        for idx in range(0, n_edges, edge_step):
            a, b = edges[idx]
            if not (clipped[a] or clipped[b]):
                lines.append(QLineF(sx_arr[a], sy_arr[a], sx_arr[b], sy_arr[b]))
        if lines:
            painter.drawLines(lines)

    painter.end()
    return image


# ---- Orientation & Light Gizmo Overlays (drawn in 3D View Mode) ---------------------------------


def paint_axes_gizmo(painter: QPainter, rect: QRectF,
                     yaw: float, pitch: float, roll: float = 0.0) -> None:
    """Paint an XYZ orientation gizmo in the top-right of ``rect`` showing the current 3D axes."""
    if rect.width() < 110.0 or rect.height() < 110.0:
        return
    radius = 22.0
    center = QPointF(rect.right() - radius - 14.0, rect.top() + radius + 14.0)
    right, up, forward = camera_basis(yaw, pitch, roll)
    rx, ry, rz = right
    ux, uy, uz = up
    fx, fy, fz = forward

    axes = [
        ("X", (rx, ux, fx), QColor("#ef5350")),
        ("Y", (ry, uy, fy), QColor("#66bb6a")),
        ("Z", (rz, uz, fz), QColor("#42a5f5")),
    ]
    axes.sort(key=lambda item: item[1][2])

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    bg_r = radius + 8.0
    painter.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
    painter.setBrush(QColor(16, 18, 24, 175))
    painter.drawEllipse(center, bg_r, bg_r)

    font = QFont(painter.font())
    font.setPixelSize(9)
    font.setBold(True)
    painter.setFont(font)

    for label, (ax, ay, _az), color in axes:
        end = QPointF(center.x() + ax * radius, center.y() - ay * radius)
        painter.setPen(QPen(color, 1.8, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(center, end)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(end, 3.0, 3.0)
        lbl_rect = QRectF(end.x() - 7.0, end.y() - 7.0, 14.0, 14.0)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(lbl_rect, Qt.AlignCenter, label)

    painter.restore()


def paint_light_gizmo(painter: QPainter, rect: QRectF,
                      azimuth: float, elevation: float) -> None:
    """Paint a key-light direction indicator sphere in the bottom-right of ``rect``."""
    if rect.width() < 110.0 or rect.height() < 110.0:
        return
    radius = 18.0
    center = QPointF(rect.right() - radius - 16.0, rect.bottom() - radius - 16.0)
    lx, ly, _lz = light_vector(azimuth, elevation)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    bg_r = radius + 6.0
    painter.setPen(QPen(QColor(255, 215, 110, 75), 1.0))
    painter.setBrush(QColor(16, 18, 24, 175))
    painter.drawEllipse(center, bg_r, bg_r)

    painter.setPen(QPen(QColor(120, 130, 150, 110), 1.0))
    painter.setBrush(QColor(52, 56, 68, 200))
    painter.drawEllipse(center, radius * 0.55, radius * 0.55)

    sun_pt = QPointF(center.x() + lx * radius * 0.78, center.y() - ly * radius * 0.78)
    painter.setPen(QPen(QColor("#ffd54f"), 1.5, Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(sun_pt, center)
    painter.setPen(QPen(QColor("#fff8e1"), 1.0))
    painter.setBrush(QColor("#ffca28"))
    painter.drawEllipse(sun_pt, 4.2, 4.2)
    painter.restore()
