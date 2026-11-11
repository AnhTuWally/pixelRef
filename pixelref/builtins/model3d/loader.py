"""Zero-dependency 3D mesh & texture loaders (.obj/.mtl, .stl, .ply, .gltf/.glb) and primitives.

Every loaded mesh is normalized into a unit bounding sphere centered at the
origin so camera distances and framing work identically regardless of whether
the source file was authored in millimetres, metres or centimetres.

For high-polygon meshes (such as 3D scans with ratusan of thousands of
triangles), binary blocks are unpacked in C via :func:`struct.iter_unpack` and
:mod:`array` directly into packed GPU vertex/index buffers, while CPU fallback
structures are capped so loading and rendering stay fast and responsive.
"""

from __future__ import annotations

import array
import base64
import itertools
import json
import math
import re
import struct
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

MAX_CACHE_ENTRIES = 32
DEFAULT_RGB = (196, 200, 208)
MAX_CPU_FACES = 16000
MAX_UNROLLED_GPU_FACES = 60000
MAX_WIREFRAME_EDGES = 10000
ASYNC_FILE_THRESHOLD = 1_000_000  # files > 1 MB decode on a background worker thread
TEXTURE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga")


@dataclass(frozen=True)
class MeshData:
    """Normalized triangle mesh ready for GPU OpenGL and CPU fallback rendering."""

    vertices: tuple[tuple[float, float, float], ...]
    faces: tuple[tuple[int, int, int], ...]
    face_normals: tuple[tuple[float, float, float], ...]
    vertex_normals: tuple[tuple[float, float, float], ...]
    face_colors: tuple[tuple[int, int, int], ...] | None
    edges: tuple[tuple[int, int], ...]
    format_name: str
    raw_bounds: tuple[float, float, float]  # original (dx, dy, dz) before normalization
    total_vertices: int = 0
    total_faces: int = 0
    texture_path: Path | None = None
    texture_bytes: bytes | None = None
    face_uvs: tuple[tuple[tuple[float, float], tuple[float, float], tuple[float, float]], ...] | None = None
    # Packed GPU buffers for hardware-accelerated OpenGL rendering
    gpu_positions: bytes | None = None
    gpu_normals: bytes | None = None
    gpu_uvs: bytes | None = None
    gpu_colors: bytes | None = None
    gpu_indices: bytes | None = None
    gpu_vertex_count: int = 0
    gpu_index_count: int = 0
    gpu_center: tuple[float, float, float] = (0.0, 0.0, 0.0)
    gpu_inv_radius: float = 1.0

    @property
    def vertex_count(self) -> int:
        return self.total_vertices or len(self.vertices)

    @property
    def face_count(self) -> int:
        return self.total_faces or len(self.faces)

    @property
    def has_texture(self) -> bool:
        return self.texture_path is not None or self.texture_bytes is not None

    @property
    def has_uvs(self) -> bool:
        return self.gpu_uvs is not None or self.face_uvs is not None


def _normalize_vec(x: float, y: float, z: float) -> tuple[float, float, float]:
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    inv = 1.0 / length
    return (x * inv, y * inv, z * inv)


def _clamp_byte(value: float) -> int:
    return max(0, min(255, round(value)))


def find_companion_texture(base_dir: Path | None, filename: str = "", hint: str = "") -> Path | None:
    """Locate a texture image file in ``base_dir`` matching ``hint`` or ``filename`` stem."""
    if base_dir is None or not base_dir.is_dir():
        return None
    resolved_dir = base_dir.resolve()

    def _safe_file(candidate: Path) -> Path | None:
        try:
            res = candidate.resolve()
            if res.is_file() and res.is_relative_to(resolved_dir):
                return res
        except OSError:
            pass
        return None

    if hint:
        clean_hint = hint.strip().strip('"').strip("'").replace("\\", "/")
        direct = _safe_file(resolved_dir / clean_hint)
        if direct is not None:
            return direct
        hint_name = Path(clean_hint).name.lower()
        try:
            for entry in resolved_dir.iterdir():
                if entry.name.lower() == hint_name and entry.is_file():
                    return entry.resolve()
        except OSError:
            pass

    if not filename:
        return None
    stem = Path(filename).stem
    if not stem:
        return None

    # Common naming conventions: <stem>.jpg, <stem>_0.jpg (Artec), <stem>_1.jpg, <stem>_diffuse.png, etc.
    candidates: list[str] = []
    for suffix in ("", "_0", "_1", "_diffuse", "_albedo", "_color", "_basecolor", "_tex"):
        for ext in TEXTURE_EXTS:
            candidates.append(f"{stem}{suffix}{ext}")
            candidates.append(f"{stem}{suffix}{ext.upper()}")

    for cand in candidates:
        found = _safe_file(resolved_dir / cand)
        if found is not None:
            return found

    # Case-insensitive stem prefix match in the same folder
    stem_lower = stem.lower()
    try:
        for entry in sorted(resolved_dir.iterdir()):
            if not entry.is_file() or entry.suffix.lower() not in TEXTURE_EXTS:
                continue
            entry_stem = entry.stem.lower()
            if entry_stem == stem_lower or entry_stem.startswith(stem_lower + "_"):
                return entry.resolve()
    except OSError:
        pass
    return None


def _compute_bounds_and_sphere(
    vertices: list[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], float, tuple[float, float, float]]:
    """Return ``((cx, cy, cz), inv_radius, raw_bounds)`` for ``vertices``, sampling if huge."""
    n_verts = len(vertices)
    step = max(1, n_verts // 25000)
    sampled = vertices if step == 1 else vertices[::step]
    xs = [v[0] for v in sampled]
    ys = [v[1] for v in sampled]
    zs = [v[2] for v in sampled]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    cz = (min_z + max_z) * 0.5
    raw_bounds = (round(max_x - min_x, 4), round(max_y - min_y, 4), round(max_z - min_z, 4))

    max_r_sq = 0.0
    for x, y, z in sampled:
        dx, dy, dz = x - cx, y - cy, z - cz
        r_sq = dx * dx + dy * dy + dz * dz
        if r_sq > max_r_sq:
            max_r_sq = r_sq
    radius = math.sqrt(max_r_sq) if max_r_sq > 1e-12 else 1.0
    return (cx, cy, cz), (1.0 / radius), raw_bounds


def build_mesh(
    vertices: list[tuple[float, float, float]],
    faces: list[tuple[int, int, int]],
    format_name: str,
    vertex_normals: list[tuple[float, float, float]] | None = None,
    face_colors: list[tuple[int, int, int]] | None = None,
    face_uvs: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] | None = None,
    vertex_uvs: list[tuple[float, float]] | None = None,
    texture_path: Path | None = None,
    texture_bytes: bytes | None = None,
    gpu_override: dict[str, Any] | None = None,
    crease_degrees: float = 55.0,
) -> MeshData:
    """Normalize ``vertices`` into a unit sphere and build GPU buffers + CPU fallback structures."""
    if not vertices or not faces:
        raise ValueError("3D file contains no valid triangles")

    _ = crease_degrees
    total_verts = len(vertices)
    total_faces = len(faces)
    (cx, cy, cz), inv_r, raw_bounds = _compute_bounds_and_sphere(vertices)

    # For huge meshes (> MAX_CPU_FACES), subsample the CPU fallback triangles and remap only
    # the vertices they reference so CPU normal/edge calculation stays under 30ms.
    if total_faces > MAX_CPU_FACES:
        cpu_step = max(1, total_faces // MAX_CPU_FACES)
        sub_faces_raw = faces[::cpu_step]
        sub_colors_raw = face_colors[::cpu_step] if face_colors is not None else None
        sub_uvs_raw = face_uvs[::cpu_step] if face_uvs is not None else None
        remap: dict[int, int] = {}
        cpu_verts: list[tuple[float, float, float]] = []
        cpu_v_normals_in: list[tuple[float, float, float]] | None = [] if vertex_normals else None
        cpu_faces_in: list[tuple[int, int, int]] = []
        for i0, i1, i2 in sub_faces_raw:
            if not (0 <= i0 < total_verts and 0 <= i1 < total_verts and 0 <= i2 < total_verts):
                continue
            mapped: list[int] = []
            for orig_i in (i0, i1, i2):
                new_i = remap.get(orig_i)
                if new_i is None:
                    new_i = len(cpu_verts)
                    remap[orig_i] = new_i
                    vx, vy, vz = vertices[orig_i]
                    cpu_verts.append(((vx - cx) * inv_r, (vy - cy) * inv_r, (vz - cz) * inv_r))
                    if cpu_v_normals_in is not None and vertex_normals is not None and orig_i < len(vertex_normals):
                        cpu_v_normals_in.append(vertex_normals[orig_i])
                mapped.append(new_i)
            cpu_faces_in.append((mapped[0], mapped[1], mapped[2]))
        norm_verts = tuple(cpu_verts)
        work_faces = cpu_faces_in
        work_v_normals = cpu_v_normals_in
        work_colors = sub_colors_raw
        work_uvs = sub_uvs_raw
    else:
        norm_verts = tuple(((x - cx) * inv_r, (y - cy) * inv_r, (z - cz) * inv_r) for x, y, z in vertices)
        work_faces = faces
        work_v_normals = vertex_normals
        work_colors = face_colors
        work_uvs = face_uvs

    n_cpu_verts = len(norm_verts)
    valid_faces: list[tuple[int, int, int]] = []
    valid_colors: list[tuple[int, int, int]] = []
    valid_uvs: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    f_normals: list[tuple[float, float, float]] = []

    for idx, (i0, i1, i2) in enumerate(work_faces):
        if not (0 <= i0 < n_cpu_verts and 0 <= i1 < n_cpu_verts and 0 <= i2 < n_cpu_verts):
            continue
        if i0 == i1 or i1 == i2 or i2 == i0:
            continue
        v0, v1, v2 = norm_verts[i0], norm_verts[i1], norm_verts[i2]
        ux, uy, uz = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
        vx, vy, vz = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        valid_faces.append((i0, i1, i2))
        f_normals.append(_normalize_vec(nx, ny, nz))
        if work_colors is not None and idx < len(work_colors):
            valid_colors.append(work_colors[idx])
        if work_uvs is not None and idx < len(work_uvs):
            valid_uvs.append(work_uvs[idx])
        elif vertex_uvs is not None and total_faces <= MAX_CPU_FACES:
            if i0 < len(vertex_uvs) and i1 < len(vertex_uvs) and i2 < len(vertex_uvs):
                valid_uvs.append((vertex_uvs[i0], vertex_uvs[i1], vertex_uvs[i2]))

    if not valid_faces:
        raise ValueError("3D mesh contains no non-degenerate triangles")

    if work_v_normals is not None and len(work_v_normals) == n_cpu_verts:
        v_normals = tuple(_normalize_vec(*n) for n in work_v_normals)
    else:
        acc_x = [0.0] * n_cpu_verts
        acc_y = [0.0] * n_cpu_verts
        acc_z = [0.0] * n_cpu_verts
        for (i0, i1, i2), (nx, ny, nz) in zip(valid_faces, f_normals):
            acc_x[i0] += nx
            acc_y[i0] += ny
            acc_z[i0] += nz
            acc_x[i1] += nx
            acc_y[i1] += ny
            acc_z[i1] += nz
            acc_x[i2] += nx
            acc_y[i2] += ny
            acc_z[i2] += nz
        v_normals = tuple(_normalize_vec(acc_x[i], acc_y[i], acc_z[i]) for i in range(n_cpu_verts))

    edge_step = max(1, len(valid_faces) // (MAX_WIREFRAME_EDGES // 2))
    edge_set: set[tuple[int, int]] = set()
    for i0, i1, i2 in valid_faces[::edge_step]:
        edge_set.add((min(i0, i1), max(i0, i1)))
        edge_set.add((min(i1, i2), max(i1, i2)))
        edge_set.add((min(i2, i0), max(i2, i0)))
        if len(edge_set) >= MAX_WIREFRAME_EDGES:
            break

    # ---- Build Packed GPU Buffers ---------------------------------------------------------------
    gpu_positions: bytes | None = None
    gpu_normals: bytes | None = None
    gpu_uvs: bytes | None = None
    gpu_colors: bytes | None = None
    gpu_indices: bytes | None = None
    gpu_vertex_count = 0
    gpu_index_count = 0
    gpu_center = (0.0, 0.0, 0.0)
    gpu_inv_radius = 1.0

    if gpu_override is not None:
        gpu_positions = gpu_override.get("positions")
        gpu_normals = gpu_override.get("normals")
        gpu_uvs = gpu_override.get("uvs")
        gpu_colors = gpu_override.get("colors")
        gpu_indices = gpu_override.get("indices")
        gpu_vertex_count = int(gpu_override.get("vertex_count", 0))
        gpu_index_count = int(gpu_override.get("index_count", 0))
        gpu_center = gpu_override.get("center", (cx, cy, cz))
        gpu_inv_radius = float(gpu_override.get("inv_radius", inv_r))
    elif total_faces <= MAX_CPU_FACES:
        # Unroll triangles so every corner gets blended smooth+face normal, face color, and corner UV
        n_tris = len(valid_faces)
        gpu_vertex_count = n_tris * 3
        pos_list: list[float] = []
        norm_list: list[float] = []
        has_colors = len(valid_colors) == n_tris
        has_uvs = len(valid_uvs) == n_tris
        col_list: list[float] = []
        uv_list: list[float] = []

        for idx, (i0, i1, i2) in enumerate(valid_faces):
            v0, v1, v2 = norm_verts[i0], norm_verts[i1], norm_verts[i2]
            pos_list.extend((*v0, *v1, *v2))
            fnx, fny, fnz = f_normals[idx]
            for vi in (i0, i1, i2):
                vnx, vny, vnz = v_normals[vi]
                # Blend smooth vertex normal with face normal (matching CPU studio look)
                bx = vnx * 0.66 + fnx * 0.34
                by = vny * 0.66 + fny * 0.34
                bz = vnz * 0.66 + fnz * 0.34
                norm_list.extend(_normalize_vec(bx, by, bz))
            if has_colors:
                cr, cg, cb = valid_colors[idx]
                rf, gf, bf = cr / 255.0, cg / 255.0, cb / 255.0
                col_list.extend((rf, gf, bf, rf, gf, bf, rf, gf, bf))
            if has_uvs:
                uv0, uv1, uv2 = valid_uvs[idx]
                uv_list.extend((*uv0, *uv1, *uv2))

        gpu_positions = array.array("f", pos_list).tobytes()
        gpu_normals = array.array("f", norm_list).tobytes()
        if has_colors:
            gpu_colors = array.array("f", col_list).tobytes()
        if has_uvs:
            gpu_uvs = array.array("f", uv_list).tobytes()
    else:
        # Large mesh: indexed GPU buffers directly from raw vertices and faces
        gpu_center = (cx, cy, cz)
        gpu_inv_radius = inv_r
        gpu_vertex_count = total_verts
        gpu_index_count = total_faces * 3
        gpu_positions = array.array("f", itertools.chain.from_iterable(vertices)).tobytes()
        gpu_indices = array.array("I", itertools.chain.from_iterable(faces)).tobytes()
        if vertex_normals is not None and len(vertex_normals) == total_verts:
            gpu_normals = array.array("f", itertools.chain.from_iterable(vertex_normals)).tobytes()
        if vertex_uvs is not None and len(vertex_uvs) == total_verts:
            gpu_uvs = array.array("f", itertools.chain.from_iterable(vertex_uvs)).tobytes()

    return MeshData(
        vertices=norm_verts,
        faces=tuple(valid_faces),
        face_normals=tuple(f_normals),
        vertex_normals=v_normals,
        face_colors=tuple(valid_colors) if len(valid_colors) == len(valid_faces) and valid_colors else None,
        edges=tuple(sorted(edge_set)),
        format_name=format_name,
        raw_bounds=raw_bounds,
        total_vertices=total_verts,
        total_faces=total_faces,
        texture_path=texture_path,
        texture_bytes=texture_bytes,
        face_uvs=tuple(valid_uvs) if len(valid_uvs) == len(valid_faces) and valid_uvs else None,
        gpu_positions=gpu_positions,
        gpu_normals=gpu_normals,
        gpu_uvs=gpu_uvs,
        gpu_colors=gpu_colors,
        gpu_indices=gpu_indices,
        gpu_vertex_count=gpu_vertex_count,
        gpu_index_count=gpu_index_count,
        gpu_center=gpu_center,
        gpu_inv_radius=gpu_inv_radius,
    )


# ---- Wavefront OBJ (+ MTL & Textures) -----------------------------------------------------------

_MTL_OPT_FLAGS_1 = {"-bm", "-blendu", "-blendv", "-boost", "-clamp", "-imfchan", "-texres"}
_MTL_OPT_FLAGS_2 = {"-mm"}
_MTL_OPT_FLAGS_3 = {"-o", "-s", "-t"}


def _extract_map_filename(tokens: list[str]) -> str:
    """Strip standard MTL texture options (-s, -o, -bm, etc.) and return the texture filename."""
    i = 0
    n = len(tokens)
    while i < n and tokens[i].startswith("-"):
        flag = tokens[i].lower()
        if flag in _MTL_OPT_FLAGS_3:
            i += 4
        elif flag in _MTL_OPT_FLAGS_2:
            i += 3
        elif flag in _MTL_OPT_FLAGS_1:
            i += 2
        else:
            i += 2
    return " ".join(tokens[i:]).strip() if i < n else ""


def _parse_mtl(mtl_text: str) -> tuple[dict[str, tuple[int, int, int]], dict[str, str]]:
    materials: dict[str, tuple[int, int, int]] = {}
    textures: dict[str, str] = {}
    current: str | None = None
    for raw_line in mtl_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        cmd = parts[0].lower()
        if cmd == "newmtl" and len(parts) >= 2:
            current = " ".join(parts[1:])
        elif cmd == "kd" and current is not None and len(parts) >= 4:
            try:
                r, g, b = float(parts[1]), float(parts[2]), float(parts[3])
                scale = 255.0 if max(r, g, b) <= 1.001 else 1.0
                materials[current] = (_clamp_byte(r * scale), _clamp_byte(g * scale), _clamp_byte(b * scale))
            except ValueError:
                pass
        elif cmd == "map_kd" and current is not None and len(parts) >= 2:
            tex_name = _extract_map_filename(parts[1:])
            if tex_name:
                textures[current] = tex_name
    return materials, textures


def parse_obj(data: bytes, base_dir: Path | None = None, filename: str = "") -> MeshData:
    text = data.decode("utf-8", errors="replace")
    vertices: list[tuple[float, float, float]] = []
    v_colors: list[tuple[int, int, int] | None] = []
    raw_uvs: list[tuple[float, float]] = []
    raw_normals: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    face_colors: list[tuple[int, int, int]] = []
    face_uvs: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    has_any_color = False
    has_any_uv = False
    materials: dict[str, tuple[int, int, int]] = {}
    mtl_textures: dict[str, str] = {}
    texture_hint = ""
    current_color: tuple[int, int, int] | None = None
    v_norm_acc: list[list[float]] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        head = parts[0]
        if head == "v" and len(parts) >= 4:
            try:
                x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
            except ValueError:
                continue
            vertices.append((x, y, z))
            v_norm_acc.append([0.0, 0.0, 0.0])
            if len(parts) >= 7:
                try:
                    cr, cg, cb = float(parts[4]), float(parts[5]), float(parts[6])
                    scale = 255.0 if max(cr, cg, cb) <= 1.001 else 1.0
                    v_colors.append((_clamp_byte(cr * scale), _clamp_byte(cg * scale), _clamp_byte(cb * scale)))
                    has_any_color = True
                except ValueError:
                    v_colors.append(None)
            else:
                v_colors.append(None)
        elif head == "vt" and len(parts) >= 3:
            try:
                # Wavefront OBJ vt has v=0 at bottom; flip to top-left image UV space (1.0 - v)
                raw_uvs.append((float(parts[1]), 1.0 - float(parts[2])))
            except ValueError:
                continue
        elif head == "vn" and len(parts) >= 4:
            try:
                raw_normals.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                continue
        elif head == "mtllib" and base_dir is not None and len(parts) >= 2:
            mtl_name = " ".join(parts[1:])
            mtl_path = (base_dir / mtl_name).resolve()
            try:
                if mtl_path.is_file() and mtl_path.is_relative_to(base_dir.resolve()):
                    m_cols, m_texs = _parse_mtl(mtl_path.read_text(encoding="utf-8", errors="replace"))
                    materials.update(m_cols)
                    mtl_textures.update(m_texs)
                    if not texture_hint and m_texs:
                        texture_hint = next(iter(m_texs.values()))
            except OSError:
                pass
        elif head == "usemtl" and len(parts) >= 2:
            mtl_key = " ".join(parts[1:])
            current_color = materials.get(mtl_key)
            if current_color is not None:
                has_any_color = True
            if mtl_key in mtl_textures:
                texture_hint = mtl_textures[mtl_key]
        elif head == "f" and len(parts) >= 4:
            poly_v: list[int] = []
            poly_vt: list[tuple[float, float] | None] = []
            poly_vn: list[int | None] = []
            n_v = len(vertices)
            n_vt = len(raw_uvs)
            n_vn = len(raw_normals)
            for token in parts[1:]:
                chunks = token.split("/")
                try:
                    vi = int(chunks[0])
                except ValueError:
                    continue
                vi = (n_v + vi) if vi < 0 else (vi - 1)
                if not (0 <= vi < n_v):
                    continue
                uv_val: tuple[float, float] | None = None
                if len(chunks) >= 2 and chunks[1]:
                    try:
                        raw_vti = int(chunks[1])
                        vti = (n_vt + raw_vti) if raw_vti < 0 else (raw_vti - 1)
                        if 0 <= vti < n_vt:
                            uv_val = raw_uvs[vti]
                            has_any_uv = True
                    except ValueError:
                        pass
                vni: int | None = None
                if len(chunks) >= 3 and chunks[2]:
                    try:
                        raw_vni = int(chunks[2])
                        vni = (n_vn + raw_vni) if raw_vni < 0 else (raw_vni - 1)
                        if not (0 <= vni < n_vn):
                            vni = None
                    except ValueError:
                        vni = None
                poly_v.append(vi)
                poly_vt.append(uv_val)
                poly_vn.append(vni)
            if len(poly_v) < 3:
                continue
            for k in range(1, len(poly_v) - 1):
                i0, i1, i2 = poly_v[0], poly_v[k], poly_v[k + 1]
                faces.append((i0, i1, i2))
                uv0 = poly_vt[0] or (0.0, 0.0)
                uv1 = poly_vt[k] or (0.0, 0.0)
                uv2 = poly_vt[k + 1] or (0.0, 0.0)
                face_uvs.append((uv0, uv1, uv2))
                for vi, vni in ((i0, poly_vn[0]), (i1, poly_vn[k]), (i2, poly_vn[k + 1])):
                    if vni is not None:
                        nx, ny, nz = raw_normals[vni]
                        v_norm_acc[vi][0] += nx
                        v_norm_acc[vi][1] += ny
                        v_norm_acc[vi][2] += nz
                if current_color is not None:
                    face_colors.append(current_color)
                elif v_colors[i0] is not None and v_colors[i1] is not None and v_colors[i2] is not None:
                    c0, c1, c2 = v_colors[i0], v_colors[i1], v_colors[i2]
                    assert c0 is not None and c1 is not None and c2 is not None
                    face_colors.append(((c0[0] + c1[0] + c2[0]) // 3,
                                        (c0[1] + c1[1] + c2[1]) // 3,
                                        (c0[2] + c1[2] + c2[2]) // 3))
                else:
                    face_colors.append(DEFAULT_RGB)

    has_explicit_vn = any(acc[0] or acc[1] or acc[2] for acc in v_norm_acc)
    v_normals = [_normalize_vec(a[0], a[1], a[2]) for a in v_norm_acc] if has_explicit_vn else None
    tex_path = find_companion_texture(base_dir, filename=filename, hint=texture_hint) if (texture_hint or has_any_uv) else None

    # For large indexed OBJ meshes with UVs (> MAX_CPU_FACES), build vertex_uvs for GPU indexed draw
    vertex_uvs: list[tuple[float, float]] | None = None
    if has_any_uv and len(faces) > MAX_CPU_FACES:
        vertex_uvs = [(0.0, 0.0)] * len(vertices)
        for (i0, i1, i2), (uv0, uv1, uv2) in zip(faces, face_uvs):
            vertex_uvs[i0] = uv0
            vertex_uvs[i1] = uv1
            vertex_uvs[i2] = uv2

    return build_mesh(
        vertices,
        faces,
        "OBJ",
        vertex_normals=v_normals,
        face_colors=face_colors if has_any_color else None,
        face_uvs=face_uvs if has_any_uv else None,
        vertex_uvs=vertex_uvs,
        texture_path=tex_path,
    )


# ---- STL (ASCII & Binary) -----------------------------------------------------------------------


def _is_binary_stl(data: bytes) -> bool:
    if len(data) < 84:
        return False
    tri_count = struct.unpack_from("<I", data, 80)[0]
    expected = 84 + tri_count * 50
    if expected == len(data):
        return True
    head = data[:256].lstrip()
    return not (head.startswith(b"solid") and b"facet" in head and b"vertex" in head)


def parse_stl(data: bytes) -> MeshData:
    vert_map: dict[tuple[float, float, float], int] = {}
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []

    def add_vert(x: float, y: float, z: float) -> int:
        key = (round(x, 5), round(y, 5), round(z, 5))
        idx = vert_map.get(key)
        if idx is None:
            idx = len(vertices)
            vert_map[key] = idx
            vertices.append((x, y, z))
        return idx

    if _is_binary_stl(data) and len(data) >= 84:
        tri_count = struct.unpack_from("<I", data, 80)[0]
        max_tris = min(tri_count, (len(data) - 84) // 50)
        mv = memoryview(data)[84 : 84 + max_tris * 50]
        for vals in struct.iter_unpack("<12fH", mv):
            i0 = add_vert(vals[3], vals[4], vals[5])
            i1 = add_vert(vals[6], vals[7], vals[8])
            i2 = add_vert(vals[9], vals[10], vals[11])
            faces.append((i0, i1, i2))
    else:
        text = data.decode("utf-8", errors="replace")
        tri: list[int] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.lower().startswith("vertex"):
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        idx = add_vert(float(parts[1]), float(parts[2]), float(parts[3]))
                        tri.append(idx)
                        if len(tri) == 3:
                            faces.append((tri[0], tri[1], tri[2]))
                            tri = []
                    except ValueError:
                        pass

    return build_mesh(vertices, faces, "STL")


# ---- PLY (ASCII & Binary, including Artec & MeshLab Textures) -----------------------------------

_PLY_TYPES: dict[str, tuple[str, int]] = {
    "char": ("b", 1), "int8": ("b", 1),
    "uchar": ("B", 1), "uint8": ("B", 1),
    "short": ("h", 2), "int16": ("h", 2),
    "ushort": ("H", 2), "uint16": ("H", 2),
    "int": ("i", 4), "int32": ("i", 4),
    "uint": ("I", 4), "uint32": ("I", 4),
    "float": ("f", 4), "float32": ("f", 4),
    "double": ("d", 8), "float64": ("d", 8),
}


def parse_ply(data: bytes, base_dir: Path | None = None, filename: str = "") -> MeshData:
    header_end = data.find(b"end_header\n")
    sep_len = len(b"end_header\n")
    if header_end < 0:
        header_end = data.find(b"end_header\r\n")
        sep_len = len(b"end_header\r\n")
    if header_end < 0:
        raise ValueError("Invalid PLY file: missing end_header")

    header_text = data[:header_end].decode("ascii", errors="replace")
    body = data[header_end + sep_len:]

    fmt = "ascii"
    texture_hint = ""
    elements: list[tuple[str, int, list[tuple[str, ...]]]] = []
    for raw_line in header_text.splitlines():
        line = raw_line.strip()
        parts = line.split()
        if not parts:
            continue
        low0 = parts[0].lower()
        if low0 == "format" and len(parts) >= 2:
            fmt = parts[1].lower()
        elif low0 == "comment" and len(parts) >= 3 and parts[1].lower() == "texturefile":
            texture_hint = " ".join(parts[2:])
        elif low0 == "element" and len(parts) >= 3:
            elements.append((parts[1], int(parts[2]), []))
        elif low0 == "property" and elements:
            elements[-1][2].append(tuple(parts[1:]))

    vertices: list[tuple[float, float, float]] = []
    v_normals: list[tuple[float, float, float]] = []
    v_colors: list[tuple[int, int, int]] = []
    v_uvs: list[tuple[float, float]] = []
    faces: list[tuple[int, int, int]] = []
    face_uvs: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    mtv_list: list[tuple[float, float]] = []
    mtf_list: list[tuple[int, int, int]] = []

    if fmt == "ascii":
        lines = [ln.strip() for ln in body.decode("utf-8", errors="replace").splitlines() if ln.strip()]
        cursor = 0
        for el_name, count, props in elements:
            prop_names = [p[-1] for p in props if p[0] != "list"]
            if el_name == "vertex":
                ix = prop_names.index("x") if "x" in prop_names else 0
                iy = prop_names.index("y") if "y" in prop_names else 1
                iz = prop_names.index("z") if "z" in prop_names else 2
                inx = prop_names.index("nx") if "nx" in prop_names else -1
                iny = prop_names.index("ny") if "ny" in prop_names else -1
                inz = prop_names.index("nz") if "nz" in prop_names else -1
                ir = next((prop_names.index(k) for k in ("red", "r") if k in prop_names), -1)
                ig = next((prop_names.index(k) for k in ("green", "g") if k in prop_names), -1)
                ib = next((prop_names.index(k) for k in ("blue", "b") if k in prop_names), -1)
                iu = next((prop_names.index(k) for k in ("u", "s", "texture_u") if k in prop_names), -1)
                iv = next((prop_names.index(k) for k in ("v", "t", "texture_v") if k in prop_names), -1)
                for _ in range(count):
                    if cursor >= len(lines):
                        break
                    vals = lines[cursor].split()
                    cursor += 1
                    vertices.append((float(vals[ix]), float(vals[iy]), float(vals[iz])))
                    if inx >= 0 and iny >= 0 and inz >= 0:
                        v_normals.append((float(vals[inx]), float(vals[iny]), float(vals[inz])))
                    if ir >= 0 and ig >= 0 and ib >= 0:
                        rf, gf, bf = float(vals[ir]), float(vals[ig]), float(vals[ib])
                        scale = 255.0 if max(rf, gf, bf) <= 1.0 and "." in vals[ir] else 1.0
                        v_colors.append((_clamp_byte(rf * scale), _clamp_byte(gf * scale), _clamp_byte(bf * scale)))
                    if iu >= 0 and iv >= 0:
                        v_uvs.append((float(vals[iu]), float(vals[iv])))
            elif el_name == "face":
                for _ in range(count):
                    if cursor >= len(lines):
                        break
                    vals = [int(v) for v in lines[cursor].split()]
                    cursor += 1
                    if not vals:
                        continue
                    n_idx = vals[0]
                    poly = vals[1 : 1 + n_idx]
                    for k in range(1, len(poly) - 1):
                        faces.append((poly[0], poly[k], poly[k + 1]))
            else:
                cursor += count
    else:
        endian = "<" if "little" in fmt else ">"
        offset = 0
        mv = memoryview(body)
        for el_name, count, props in elements:
            if el_name == "vertex" and all(p[0] != "list" for p in props):
                fmt_chars = endian + "".join(_PLY_TYPES[p[0]][0] for p in props)
                stride = struct.calcsize(fmt_chars)
                block_end = min(len(body), offset + count * stride)
                actual_count = (block_end - offset) // stride
                block_mv = mv[offset : offset + actual_count * stride]
                offset += actual_count * stride
                prop_names = [p[1] for p in props]

                if prop_names == ["x", "y", "z"] and fmt_chars in ("<fff", ">fff"):
                    vertices = list(struct.iter_unpack(fmt_chars, block_mv))
                else:
                    ix = prop_names.index("x") if "x" in prop_names else 0
                    iy = prop_names.index("y") if "y" in prop_names else 1
                    iz = prop_names.index("z") if "z" in prop_names else 2
                    inx = prop_names.index("nx") if "nx" in prop_names else -1
                    iny = prop_names.index("ny") if "ny" in prop_names else -1
                    inz = prop_names.index("nz") if "nz" in prop_names else -1
                    ir = next((prop_names.index(k) for k in ("red", "r") if k in prop_names), -1)
                    ig = next((prop_names.index(k) for k in ("green", "g") if k in prop_names), -1)
                    ib = next((prop_names.index(k) for k in ("blue", "b") if k in prop_names), -1)
                    iu = next((prop_names.index(k) for k in ("u", "s", "texture_u") if k in prop_names), -1)
                    iv = next((prop_names.index(k) for k in ("v", "t", "texture_v") if k in prop_names), -1)
                    is_float_color = ir >= 0 and props[ir][0] in ("float", "float32", "double", "float64")
                    for vals in struct.iter_unpack(fmt_chars, block_mv):
                        vertices.append((float(vals[ix]), float(vals[iy]), float(vals[iz])))
                        if inx >= 0 and iny >= 0 and inz >= 0:
                            v_normals.append((float(vals[inx]), float(vals[iny]), float(vals[inz])))
                        if ir >= 0 and ig >= 0 and ib >= 0:
                            scale = 255.0 if is_float_color else 1.0
                            v_colors.append((_clamp_byte(vals[ir] * scale),
                                             _clamp_byte(vals[ig] * scale),
                                             _clamp_byte(vals[ib] * scale)))
                        if iu >= 0 and iv >= 0:
                            v_uvs.append((float(vals[iu]), float(vals[iv])))

            elif el_name == "face":
                # Fast path for standard triangle PLY: single list property (uchar count=3, 3x int32/uint32)
                if (
                    len(props) == 1
                    and props[0][0] == "list"
                    and _PLY_TYPES[props[0][1]][1] == 1
                    and _PLY_TYPES[props[0][2]][1] == 4
                    and offset + count * 13 <= len(body)
                    and body[offset] == 3
                    and body[offset + (count - 1) * 13] == 3
                ):
                    idx_code = _PLY_TYPES[props[0][2]][0]
                    tri_fmt = f"{endian}x{idx_code}{idx_code}{idx_code}"
                    faces = list(struct.iter_unpack(tri_fmt, mv[offset : offset + count * 13]))
                    offset += count * 13
                else:
                    for _ in range(count):
                        for p in props:
                            if p[0] == "list":
                                cnt_code, cnt_sz = _PLY_TYPES[p[1]]
                                idx_code, idx_sz = _PLY_TYPES[p[2]]
                                n_idx = struct.unpack_from(endian + cnt_code, body, offset)[0]
                                offset += cnt_sz
                                poly = struct.unpack_from(f"{endian}{n_idx}{idx_code}", body, offset)
                                offset += n_idx * idx_sz
                                if p[-1] in ("vertex_indices", "vertex_index"):
                                    for k in range(1, len(poly) - 1):
                                        faces.append((int(poly[0]), int(poly[k]), int(poly[k + 1])))
                                elif p[-1] == "texcoord" and n_idx >= 6:
                                    # MeshLab per-face texcoord list (u0, v0, u1, v1, u2, v2)
                                    face_uvs.append((
                                        (float(poly[0]), 1.0 - float(poly[1])),
                                        (float(poly[2]), 1.0 - float(poly[3])),
                                        (float(poly[4]), 1.0 - float(poly[5])),
                                    ))
                            else:
                                _, sz = _PLY_TYPES[p[0]]
                                offset += sz

            elif el_name == "multi_texture_vertex" and all(p[0] != "list" for p in props):
                # Artec 3D scanner UV element: uchar tx, float u, float v (9 bytes)
                fmt_chars = endian + "".join(_PLY_TYPES[p[0]][0] for p in props)
                stride = struct.calcsize(fmt_chars)
                block_end = min(len(body), offset + count * stride)
                actual_count = (block_end - offset) // stride
                block_mv = mv[offset : offset + actual_count * stride]
                offset += actual_count * stride
                if stride == 9 and fmt_chars in ("<Bff", ">Bff"):
                    mtv_list = list(struct.iter_unpack(f"{endian}xff", block_mv))
                else:
                    prop_names = [p[1] for p in props]
                    iu = prop_names.index("u") if "u" in prop_names else 1
                    iv = prop_names.index("v") if "v" in prop_names else 2
                    mtv_list = [(float(vals[iu]), float(vals[iv])) for vals in struct.iter_unpack(fmt_chars, block_mv)]

            elif el_name == "multi_texture_face":
                # Artec 3D scanner UV face element: uchar tx, uint tn, list uchar int texture_vertex_indices (18 bytes)
                if (
                    offset + count * 18 <= len(body)
                    and body[offset + 5] == 3
                    and body[offset + (count - 1) * 18 + 5] == 3
                ):
                    mtf_list = list(struct.iter_unpack(f"{endian}6xiii", mv[offset : offset + count * 18]))
                    offset += count * 18
                else:
                    for _ in range(count):
                        for p in props:
                            if p[0] == "list":
                                cnt_code, cnt_sz = _PLY_TYPES[p[1]]
                                idx_code, idx_sz = _PLY_TYPES[p[2]]
                                n_idx = struct.unpack_from(endian + cnt_code, body, offset)[0]
                                offset += cnt_sz
                                poly = struct.unpack_from(f"{endian}{n_idx}{idx_code}", body, offset)
                                offset += n_idx * idx_sz
                                if n_idx >= 3:
                                    mtf_list.append((int(poly[0]), int(poly[1]), int(poly[2])))
                            else:
                                _, sz = _PLY_TYPES[p[0]]
                                offset += sz

            else:
                if all(p[0] != "list" for p in props):
                    stride = sum(_PLY_TYPES[p[0]][1] for p in props)
                    offset += count * stride
                else:
                    for _ in range(count):
                        for p in props:
                            if p[0] == "list":
                                cnt_code, cnt_sz = _PLY_TYPES[p[1]]
                                _, idx_sz = _PLY_TYPES[p[2]]
                                n_idx = struct.unpack_from(endian + cnt_code, body, offset)[0]
                                offset += cnt_sz + n_idx * idx_sz
                            else:
                                _, sz = _PLY_TYPES[p[0]]
                                offset += sz

    face_colors: list[tuple[int, int, int]] | None = None
    if len(v_colors) == len(vertices) and faces and len(faces) <= MAX_CPU_FACES:
        face_colors = [
            ((v_colors[i0][0] + v_colors[i1][0] + v_colors[i2][0]) // 3,
             (v_colors[i0][1] + v_colors[i1][1] + v_colors[i2][1]) // 3,
             (v_colors[i0][2] + v_colors[i1][2] + v_colors[i2][2]) // 3)
            for i0, i1, i2 in faces
        ]

    # Build seamless GPU buffers when Artec multi_texture_vertex + multi_texture_face are present
    gpu_override: dict[str, Any] | None = None
    has_uvs = bool(mtv_list and len(mtf_list) == len(faces)) or bool(v_uvs) or bool(face_uvs)
    if mtv_list and len(mtf_list) == len(faces) and vertices:
        n_mtv = len(mtv_list)
        (cx, cy, cz), inv_r, _ = _compute_bounds_and_sphere(vertices)
        mtv_pos = [vertices[0]] * n_mtv
        for (i0, i1, i2), (t0_i, t1_i, t2_i) in zip(faces, mtf_list):
            mtv_pos[t0_i] = vertices[i0]
            mtv_pos[t1_i] = vertices[i1]
            mtv_pos[t2_i] = vertices[i2]
        gpu_override = {
            "positions": array.array("f", itertools.chain.from_iterable(mtv_pos)).tobytes(),
            "normals": None,
            "uvs": array.array("f", itertools.chain.from_iterable(mtv_list)).tobytes(),
            "colors": None,
            "indices": array.array("I", itertools.chain.from_iterable(mtf_list)).tobytes(),
            "vertex_count": n_mtv,
            "index_count": len(mtf_list) * 3,
            "center": (cx, cy, cz),
            "inv_radius": inv_r,
        }
        # Populate CPU fallback face_uvs for the subsampled faces
        cpu_step = max(1, len(faces) // MAX_CPU_FACES) if len(faces) > MAX_CPU_FACES else 1
        face_uvs = [
            (mtv_list[t0_i], mtv_list[t1_i], mtv_list[t2_i])
            for t0_i, t1_i, t2_i in mtf_list[::cpu_step]
        ]

    tex_path = find_companion_texture(base_dir, filename=filename, hint=texture_hint) if (texture_hint or has_uvs) else None

    return build_mesh(
        vertices,
        faces,
        "PLY",
        vertex_normals=v_normals if len(v_normals) == len(vertices) else None,
        face_colors=face_colors,
        face_uvs=face_uvs if face_uvs else None,
        vertex_uvs=v_uvs if len(v_uvs) == len(vertices) else None,
        texture_path=tex_path,
        gpu_override=gpu_override,
    )


# ---- glTF 2.0 (.gltf & .glb, including Textures) ------------------------------------------------

_GLTF_COMP: dict[int, tuple[str, int]] = {
    5120: ("b", 1),
    5121: ("B", 1),
    5122: ("h", 2),
    5123: ("H", 2),
    5125: ("I", 4),
    5126: ("f", 4),
}

_GLTF_COUNTS: dict[str, int] = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT4": 16,
}


def _mat4_identity() -> list[float]:
    return [1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


def _mat4_mul(a: list[float], b: list[float]) -> list[float]:
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = (
                a[0 * 4 + row] * b[col * 4 + 0]
                + a[1 * 4 + row] * b[col * 4 + 1]
                + a[2 * 4 + row] * b[col * 4 + 2]
                + a[3 * 4 + row] * b[col * 4 + 3]
            )
    return out


def _node_local_matrix(node: dict[str, Any]) -> list[float]:
    if "matrix" in node and len(node["matrix"]) == 16:
        return [float(v) for v in node["matrix"]]
    tx, ty, tz = (float(v) for v in node.get("translation", (0.0, 0.0, 0.0)))
    qx, qy, qz, qw = (float(v) for v in node.get("rotation", (0.0, 0.0, 0.0, 1.0)))
    sx, sy, sz = (float(v) for v in node.get("scale", (1.0, 1.0, 1.0)))
    xx, yy, zz = qx * qx, qy * qy, qz * qz
    xy, xz, yz = qx * qy, qx * qz, qy * qz
    wx, wy, wz = qw * qx, qw * qy, qw * qz
    return [
        (1.0 - 2.0 * (yy + zz)) * sx, (2.0 * (xy + wz)) * sx, (2.0 * (xz - wy)) * sx, 0.0,
        (2.0 * (xy - wz)) * sy, (1.0 - 2.0 * (xx + zz)) * sy, (2.0 * (yz + wx)) * sy, 0.0,
        (2.0 * (xz + wy)) * sz, (2.0 * (yz - wx)) * sz, (1.0 - 2.0 * (xx + yy)) * sz, 0.0,
        tx, ty, tz, 1.0,
    ]


def _transform_point(m: list[float], x: float, y: float, z: float) -> tuple[float, float, float]:
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


def _transform_dir(m: list[float], x: float, y: float, z: float) -> tuple[float, float, float]:
    return _normalize_vec(
        m[0] * x + m[4] * y + m[8] * z,
        m[1] * x + m[5] * y + m[9] * z,
        m[2] * x + m[6] * y + m[10] * z,
    )


def _read_accessor(doc: dict[str, Any], buffers: list[bytes], accessor_idx: int) -> list[tuple[Any, ...]]:
    accessors = doc.get("accessors", [])
    views = doc.get("bufferViews", [])
    acc = accessors[accessor_idx]
    view_idx = acc.get("bufferView")
    if view_idx is None:
        return []
    view = views[view_idx]
    buf = buffers[int(view.get("buffer", 0))]
    comp_code, comp_size = _GLTF_COMP[int(acc["componentType"])]
    num_comp = _GLTF_COUNTS[str(acc["type"])]
    count = int(acc["count"])
    offset = int(view.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    elem_size = comp_size * num_comp
    stride = int(view.get("byteStride", 0)) or elem_size
    fmt = f"<{num_comp}{comp_code}"
    if stride == elem_size and offset + count * elem_size <= len(buf):
        return list(struct.iter_unpack(fmt, memoryview(buf)[offset : offset + count * elem_size]))
    return [struct.unpack_from(fmt, buf, offset + i * stride) for i in range(count)]


def _extract_gltf_texture(doc: dict[str, Any], buffers: list[bytes], base_dir: Path | None) -> tuple[Path | None, bytes | None]:
    textures = doc.get("textures", [])
    images = doc.get("images", [])
    views = doc.get("bufferViews", [])
    if not textures or not images:
        return None, None
    img_idx: int | None = None
    for mat in doc.get("materials", []):
        pbr = mat.get("pbrMetallicRoughness", {})
        base_tex = pbr.get("baseColorTexture")
        if isinstance(base_tex, dict) and "index" in base_tex:
            t_idx = int(base_tex["index"])
            if 0 <= t_idx < len(textures):
                src = textures[t_idx].get("source")
                if src is not None and 0 <= int(src) < len(images):
                    img_idx = int(src)
                    break
    if img_idx is None:
        src = textures[0].get("source")
        if src is not None and 0 <= int(src) < len(images):
            img_idx = int(src)
    if img_idx is None:
        return None, None

    img_spec = images[img_idx]
    if "bufferView" in img_spec:
        v_idx = int(img_spec["bufferView"])
        if 0 <= v_idx < len(views):
            view = views[v_idx]
            buf = buffers[int(view.get("buffer", 0))]
            off = int(view.get("byteOffset", 0))
            ln = int(view.get("byteLength", 0))
            return None, bytes(buf[off : off + ln])
    uri = str(img_spec.get("uri", "") or "")
    if uri.startswith("data:"):
        comma = uri.find(",")
        if comma >= 0:
            return None, base64.b64decode(uri[comma + 1:])
    elif uri and base_dir is not None:
        return find_companion_texture(base_dir, hint=uri), None
    return None, None


def parse_gltf(data: bytes, base_dir: Path | None = None, filename: str = "") -> MeshData:
    glb_bin = b""
    format_name = "glTF"
    if data[:4] == b"glTF" and len(data) >= 20:
        format_name = "GLB"
        _magic, _version, total_len = struct.unpack_from("<3I", data, 0)
        offset = 12
        json_bytes = b""
        while offset + 8 <= min(len(data), total_len):
            chunk_len, chunk_type = struct.unpack_from("<2I", data, offset)
            offset += 8
            chunk = data[offset : offset + chunk_len]
            offset += chunk_len
            if chunk_type == 0x4E4F534A:  # JSON
                json_bytes = chunk
            elif chunk_type == 0x004E4942:  # BIN
                glb_bin = chunk
        doc = json.loads(json_bytes.decode("utf-8"))
    else:
        doc = json.loads(data.decode("utf-8"))

    buffers: list[bytes] = []
    for i, buf_spec in enumerate(doc.get("buffers", [])):
        uri = buf_spec.get("uri")
        if not uri:
            buffers.append(glb_bin if i == 0 else b"")
        elif str(uri).startswith("data:"):
            comma = str(uri).find(",")
            buffers.append(base64.b64decode(str(uri)[comma + 1:]) if comma >= 0 else b"")
        elif base_dir is not None:
            bin_path = (base_dir / str(uri)).resolve()
            if bin_path.is_file() and bin_path.is_relative_to(base_dir.resolve()):
                buffers.append(bin_path.read_bytes())
            else:
                buffers.append(b"")
        else:
            buffers.append(b"")

    materials_rgb: list[tuple[int, int, int]] = []
    for mat in doc.get("materials", []):
        pbr = mat.get("pbrMetallicRoughness", {})
        factor = pbr.get("baseColorFactor", [0.8, 0.82, 0.85, 1.0])
        materials_rgb.append((
            _clamp_byte(float(factor[0]) * 255.0),
            _clamp_byte(float(factor[1]) * 255.0),
            _clamp_byte(float(factor[2]) * 255.0),
        ))

    tex_path, tex_bytes = _extract_gltf_texture(doc, buffers, base_dir)
    if tex_path is None and tex_bytes is None and filename:
        tex_path = find_companion_texture(base_dir, filename=filename)

    vertices: list[tuple[float, float, float]] = []
    v_normals: list[tuple[float, float, float]] = []
    v_uvs: list[tuple[float, float]] = []
    faces: list[tuple[int, int, int]] = []
    face_colors: list[tuple[int, int, int]] = []
    face_uvs: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    has_colors = False
    has_uvs = False
    all_have_normals = True
    all_have_uvs = True

    meshes = doc.get("meshes", [])
    nodes = doc.get("nodes", [])

    def add_mesh(mesh_idx: int, world_m: list[float]) -> None:
        nonlocal has_colors, has_uvs, all_have_normals, all_have_uvs
        if not (0 <= mesh_idx < len(meshes)):
            return
        for prim in meshes[mesh_idx].get("primitives", []):
            mode = int(prim.get("mode", 4))
            if mode not in (4, 5, 6):
                continue
            attrs = prim.get("attributes", {})
            if "POSITION" not in attrs:
                continue
            pos_list = _read_accessor(doc, buffers, int(attrs["POSITION"]))
            if not pos_list:
                continue
            base_v = len(vertices)
            for p in pos_list:
                vertices.append(_transform_point(world_m, float(p[0]), float(p[1]), float(p[2])))

            if "NORMAL" in attrs:
                norm_list = _read_accessor(doc, buffers, int(attrs["NORMAL"]))
                for n in norm_list:
                    v_normals.append(_transform_dir(world_m, float(n[0]), float(n[1]), float(n[2])))
            else:
                all_have_normals = False

            prim_uvs: list[tuple[float, float]] | None = None
            if "TEXCOORD_0" in attrs:
                uv_raw = _read_accessor(doc, buffers, int(attrs["TEXCOORD_0"]))
                prim_uvs = [(float(u[0]), float(u[1])) for u in uv_raw]
                v_uvs.extend(prim_uvs)
                has_uvs = True
            else:
                all_have_uvs = False

            prim_v_colors: list[tuple[int, int, int]] | None = None
            if "COLOR_0" in attrs:
                c_acc_idx = int(attrs["COLOR_0"])
                c_comp = int(doc["accessors"][c_acc_idx]["componentType"])
                c_raw = _read_accessor(doc, buffers, c_acc_idx)
                scale = 255.0 if c_comp == 5126 else (255.0 / 65535.0 if c_comp == 5123 else 1.0)
                prim_v_colors = [
                    (_clamp_byte(float(c[0]) * scale),
                     _clamp_byte(float(c[1]) * scale),
                     _clamp_byte(float(c[2]) * scale))
                    for c in c_raw
                ]
                has_colors = True

            mat_idx = prim.get("material")
            mat_color: tuple[int, int, int] | None = None
            if mat_idx is not None and 0 <= int(mat_idx) < len(materials_rgb):
                mat_color = materials_rgb[int(mat_idx)]
                has_colors = True

            if "indices" in prim:
                idx_list = [int(x[0]) for x in _read_accessor(doc, buffers, int(prim["indices"]))]
            else:
                idx_list = list(range(len(pos_list)))

            tris: list[tuple[int, int, int]] = []
            if mode == 4:
                for i in range(0, len(idx_list) - 2, 3):
                    tris.append((idx_list[i], idx_list[i + 1], idx_list[i + 2]))
            elif mode == 5:
                for i in range(len(idx_list) - 2):
                    if i % 2 == 0:
                        tris.append((idx_list[i], idx_list[i + 1], idx_list[i + 2]))
                    else:
                        tris.append((idx_list[i + 1], idx_list[i], idx_list[i + 2]))
            elif mode == 6:
                for i in range(1, len(idx_list) - 1):
                    tris.append((idx_list[0], idx_list[i], idx_list[i + 1]))

            for i0, i1, i2 in tris:
                faces.append((base_v + i0, base_v + i1, base_v + i2))
                if prim_uvs is not None and max(i0, i1, i2) < len(prim_uvs):
                    face_uvs.append((prim_uvs[i0], prim_uvs[i1], prim_uvs[i2]))
                else:
                    face_uvs.append(((0.0, 0.0), (0.0, 0.0), (0.0, 0.0)))
                if prim_v_colors is not None and max(i0, i1, i2) < len(prim_v_colors):
                    c0, c1, c2 = prim_v_colors[i0], prim_v_colors[i1], prim_v_colors[i2]
                    face_colors.append(((c0[0] + c1[0] + c2[0]) // 3,
                                        (c0[1] + c1[1] + c2[1]) // 3,
                                        (c0[2] + c1[2] + c2[2]) // 3))
                elif mat_color is not None:
                    face_colors.append(mat_color)
                else:
                    face_colors.append(DEFAULT_RGB)

    visited: set[int] = set()

    def walk_node(node_idx: int, parent_m: list[float]) -> None:
        if node_idx in visited or not (0 <= node_idx < len(nodes)):
            return
        visited.add(node_idx)
        node = nodes[node_idx]
        world_m = _mat4_mul(parent_m, _node_local_matrix(node))
        if "mesh" in node:
            add_mesh(int(node["mesh"]), world_m)
        for child_idx in node.get("children", []):
            walk_node(int(child_idx), world_m)

    if nodes:
        scenes = doc.get("scenes", [])
        scene_idx = int(doc.get("scene", 0))
        root_nodes = scenes[scene_idx].get("nodes", []) if 0 <= scene_idx < len(scenes) else list(range(len(nodes)))
        for r_idx in root_nodes:
            walk_node(int(r_idx), _mat4_identity())
    else:
        for m_idx in range(len(meshes)):
            add_mesh(m_idx, _mat4_identity())

    return build_mesh(
        vertices,
        faces,
        format_name,
        vertex_normals=v_normals if all_have_normals and len(v_normals) == len(vertices) else None,
        face_colors=face_colors if has_colors else None,
        face_uvs=face_uvs if has_uvs else None,
        vertex_uvs=v_uvs if has_uvs and all_have_uvs and len(v_uvs) == len(vertices) else None,
        texture_path=tex_path,
        texture_bytes=tex_bytes,
    )


# ---- Built-in Artist Reference Primitives -------------------------------------------------------


def _make_sphere(rings: int = 18, sectors: int = 28) -> MeshData:
    vertices: list[tuple[float, float, float]] = [(0.0, 1.0, 0.0)]
    v_uvs: list[tuple[float, float]] = [(0.5, 0.0)]
    for r in range(1, rings):
        phi = math.pi * r / rings
        y = math.cos(phi)
        sin_p = math.sin(phi)
        v_coord = r / rings
        for s in range(sectors):
            theta = 2.0 * math.pi * s / sectors
            vertices.append((sin_p * math.sin(theta), y, sin_p * math.cos(theta)))
            v_uvs.append((s / sectors, v_coord))
    vertices.append((0.0, -1.0, 0.0))
    v_uvs.append((0.5, 1.0))
    bottom = len(vertices) - 1

    faces: list[tuple[int, int, int]] = []
    for s in range(sectors):
        ns = (s + 1) % sectors
        faces.append((0, 1 + s, 1 + ns))
    for r in range(rings - 2):
        row = 1 + r * sectors
        next_row = row + sectors
        for s in range(sectors):
            ns = (s + 1) % sectors
            faces.append((row + s, next_row + s, next_row + ns))
            faces.append((row + s, next_row + ns, row + ns))
    last_row = 1 + (rings - 2) * sectors
    for s in range(sectors):
        ns = (s + 1) % sectors
        faces.append((last_row + s, bottom, last_row + ns))

    return build_mesh(vertices, faces, "Primitive (Sphere)", vertex_normals=list(vertices), vertex_uvs=v_uvs)


def _make_cube() -> MeshData:
    quads = (
        ((-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1), (0, 0, 1)),      # Front (+Z)
        ((1, -1, -1), (-1, -1, -1), (-1, 1, -1), (1, 1, -1), (0, 0, -1)), # Back (-Z)
        ((1, -1, 1), (1, -1, -1), (1, 1, -1), (1, 1, 1), (1, 0, 0)),      # Right (+X)
        ((-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1), (-1, 0, 0)), # Left (-X)
        ((-1, 1, 1), (1, 1, 1), (1, 1, -1), (-1, 1, -1), (0, 1, 0)),      # Top (+Y)
        ((-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1), (0, -1, 0)), # Bottom (-Y)
    )
    vertices: list[tuple[float, float, float]] = []
    v_normals: list[tuple[float, float, float]] = []
    v_uvs: list[tuple[float, float]] = []
    faces: list[tuple[int, int, int]] = []
    quad_uvs = ((0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0))
    for p0, p1, p2, p3, n in quads:
        base = len(vertices)
        for p, uv in zip((p0, p1, p2, p3), quad_uvs):
            vertices.append((float(p[0]), float(p[1]), float(p[2])))
            v_normals.append((float(n[0]), float(n[1]), float(n[2])))
            v_uvs.append(uv)
        faces.append((base, base + 1, base + 2))
        faces.append((base, base + 2, base + 3))
    return build_mesh(vertices, faces, "Primitive (Cube)", vertex_normals=v_normals, vertex_uvs=v_uvs)


def _make_cylinder(sectors: int = 28) -> MeshData:
    vertices: list[tuple[float, float, float]] = []
    v_normals: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    r, half_h = 0.75, 1.0
    for s in range(sectors):
        theta = 2.0 * math.pi * s / sectors
        nx, nz = math.sin(theta), math.cos(theta)
        vertices.append((r * nx, half_h, r * nz))
        v_normals.append((nx, 0.0, nz))
        vertices.append((r * nx, -half_h, r * nz))
        v_normals.append((nx, 0.0, nz))
    for s in range(sectors):
        ns = (s + 1) % sectors
        t0, b0 = 2 * s, 2 * s + 1
        t1, b1 = 2 * ns, 2 * ns + 1
        faces.append((t0, b0, b1))
        faces.append((t0, b1, t1))
    top_center = len(vertices)
    vertices.append((0.0, half_h, 0.0))
    v_normals.append((0.0, 1.0, 0.0))
    top_ring = len(vertices)
    for s in range(sectors):
        theta = 2.0 * math.pi * s / sectors
        vertices.append((r * math.sin(theta), half_h, r * math.cos(theta)))
        v_normals.append((0.0, 1.0, 0.0))
    for s in range(sectors):
        ns = (s + 1) % sectors
        faces.append((top_center, top_ring + s, top_ring + ns))
    bot_center = len(vertices)
    vertices.append((0.0, -half_h, 0.0))
    v_normals.append((0.0, -1.0, 0.0))
    bot_ring = len(vertices)
    for s in range(sectors):
        theta = 2.0 * math.pi * s / sectors
        vertices.append((r * math.sin(theta), -half_h, r * math.cos(theta)))
        v_normals.append((0.0, -1.0, 0.0))
    for s in range(sectors):
        ns = (s + 1) % sectors
        faces.append((bot_center, bot_ring + ns, bot_ring + s))

    return build_mesh(vertices, faces, "Primitive (Cylinder)", vertex_normals=v_normals)


def _make_torus(major_segments: int = 28, minor_segments: int = 16, R: float = 0.72, r: float = 0.28) -> MeshData:
    vertices: list[tuple[float, float, float]] = []
    v_normals: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for i in range(major_segments):
        u = 2.0 * math.pi * i / major_segments
        cu, su = math.cos(u), math.sin(u)
        for j in range(minor_segments):
            v = 2.0 * math.pi * j / minor_segments
            cv, sv = math.cos(v), math.sin(v)
            x = (R + r * cv) * su
            y = r * sv
            z = (R + r * cv) * cu
            vertices.append((x, y, z))
            v_normals.append(_normalize_vec(cv * su, sv, cv * cu))
    for i in range(major_segments):
        ni = (i + 1) % major_segments
        for j in range(minor_segments):
            nj = (j + 1) % minor_segments
            v00 = i * minor_segments + j
            v10 = ni * minor_segments + j
            v11 = ni * minor_segments + nj
            v01 = i * minor_segments + nj
            faces.append((v00, v01, v11))
            faces.append((v00, v11, v10))
    return build_mesh(vertices, faces, "Primitive (Torus)", vertex_normals=v_normals)


def _make_loomis_head() -> MeshData:
    """An artist's Loomis-style planar reference head for portrait angle & lighting study."""
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    face_colors: list[tuple[int, int, int]] = []

    def add_quad(p0: tuple[float, float, float], p1: tuple[float, float, float],
                 p2: tuple[float, float, float], p3: tuple[float, float, float],
                 color: tuple[int, int, int] = (204, 206, 212)) -> None:
        base = len(vertices)
        vertices.extend((p0, p1, p2, p3))
        faces.append((base, base + 1, base + 2))
        faces.append((base, base + 2, base + 3))
        face_colors.extend((color, color))

    def add_tri(p0: tuple[float, float, float], p1: tuple[float, float, float],
                p2: tuple[float, float, float], color: tuple[int, int, int] = (204, 206, 212)) -> None:
        base = len(vertices)
        vertices.extend((p0, p1, p2))
        faces.append((base, base + 1, base + 2))
        face_colors.append(color)

    rings, sectors = 12, 24
    cranium_base = len(vertices)
    vertices.append((0.0, 1.05, -0.05))
    for r in range(1, rings):
        phi = math.pi * r / rings
        y = 0.20 + 0.85 * math.cos(phi)
        sin_p = math.sin(phi)
        for s in range(sectors):
            theta = 2.0 * math.pi * s / sectors
            raw_x = 0.82 * sin_p * math.sin(theta)
            x = max(-0.64, min(0.64, raw_x))
            z = -0.05 + 0.88 * sin_p * math.cos(theta)
            if z > 0.62 and y < 0.55:
                z = 0.62 + (z - 0.62) * 0.4
            vertices.append((x, y, z))
    vertices.append((0.0, -0.65, -0.05))
    cranium_bottom = len(vertices) - 1

    skin_main = (208, 210, 216)
    skin_temple = (182, 188, 198)
    for s in range(sectors):
        ns = (s + 1) % sectors
        faces.append((cranium_base, cranium_base + 1 + s, cranium_base + 1 + ns))
        face_colors.append(skin_main)
    for r in range(rings - 2):
        row = cranium_base + 1 + r * sectors
        next_row = row + sectors
        for s in range(sectors):
            ns = (s + 1) % sectors
            vx = abs(vertices[row + s][0])
            col = skin_temple if vx >= 0.62 else skin_main
            faces.append((row + s, next_row + s, next_row + ns))
            faces.append((row + s, next_row + ns, row + ns))
            face_colors.extend((col, col))
    last_row = cranium_base + 1 + (rings - 2) * sectors
    for s in range(sectors):
        ns = (s + 1) % sectors
        faces.append((last_row + s, cranium_bottom, last_row + ns))
        face_colors.append(skin_main)

    brow_c = (0.0, 0.34, 0.78)
    brow_r, brow_l = (0.44, 0.33, 0.66), (-0.44, 0.33, 0.66)
    temple_r, temple_l = (0.62, 0.32, 0.32), (-0.62, 0.32, 0.32)
    cheek_r, cheek_l = (0.50, -0.04, 0.62), (-0.50, -0.04, 0.62)
    cheek_outer_r, cheek_outer_l = (0.62, -0.02, 0.24), (-0.62, -0.02, 0.24)
    mouth_r, mouth_l = (0.28, -0.42, 0.72), (-0.28, -0.42, 0.72)
    jaw_r, jaw_l = (0.48, -0.50, 0.18), (-0.48, -0.50, 0.18)
    chin_top_r, chin_top_l = (0.18, -0.62, 0.74), (-0.18, -0.62, 0.74)
    chin_bot_r, chin_bot_l = (0.15, -0.82, 0.68), (-0.15, -0.82, 0.68)
    under_jaw = (0.0, -0.78, 0.10)

    face_front = (214, 216, 222)
    face_side = (190, 194, 204)
    face_under = (165, 170, 182)

    eye_c = (0.0, 0.18, 0.70)
    eye_r, eye_l = (0.38, 0.16, 0.58), (-0.38, 0.16, 0.58)
    add_quad(brow_l, brow_c, eye_c, eye_l, face_under)
    add_quad(brow_c, brow_r, eye_r, eye_c, face_under)
    add_quad(eye_l, eye_c, (0.0, -0.04, 0.72), cheek_l, face_front)
    add_quad(eye_c, eye_r, cheek_r, (0.0, -0.04, 0.72), face_front)

    add_quad(temple_r, brow_r, cheek_r, cheek_outer_r, face_side)
    add_quad(brow_l, temple_l, cheek_outer_l, cheek_l, face_side)

    add_quad(cheek_l, (0.0, -0.04, 0.72), (0.0, -0.40, 0.78), mouth_l, face_front)
    add_quad((0.0, -0.04, 0.72), cheek_r, mouth_r, (0.0, -0.40, 0.78), face_front)
    add_quad(cheek_r, cheek_outer_r, jaw_r, mouth_r, face_side)
    add_quad(cheek_outer_l, cheek_l, mouth_l, jaw_l, face_side)

    add_quad(mouth_l, (0.0, -0.40, 0.78), chin_top_r, chin_top_l, face_front)
    add_tri((0.0, -0.40, 0.78), mouth_r, chin_top_r, face_front)
    add_quad(chin_top_l, chin_top_r, chin_bot_r, chin_bot_l, face_front)
    add_quad(mouth_r, jaw_r, chin_bot_r, chin_top_r, face_side)
    add_quad(jaw_l, mouth_l, chin_top_l, chin_bot_l, face_side)
    add_tri(chin_bot_l, chin_bot_r, under_jaw, face_under)
    add_tri(chin_bot_r, jaw_r, under_jaw, face_under)
    add_tri(jaw_l, chin_bot_l, under_jaw, face_under)

    n_root_l, n_root_r = (-0.07, 0.24, 0.72), (0.07, 0.24, 0.72)
    n_tip_l, n_tip_r = (-0.08, -0.10, 0.96), (0.08, -0.10, 0.96)
    n_wing_l, n_wing_r = (-0.17, -0.16, 0.74), (0.17, -0.16, 0.74)
    n_base_l, n_base_r = (-0.08, -0.18, 0.76), (0.08, -0.18, 0.76)
    nose_bridge = (224, 226, 230)
    add_quad(n_root_l, n_root_r, n_tip_r, n_tip_l, nose_bridge)
    add_quad(n_root_r, (0.15, 0.16, 0.66), n_wing_r, n_tip_r, face_side)
    add_quad((-0.15, 0.16, 0.66), n_root_l, n_tip_l, n_wing_l, face_side)
    add_quad(n_tip_l, n_tip_r, n_base_r, n_base_l, face_under)
    add_tri(n_tip_r, n_wing_r, n_base_r, face_under)
    add_tri(n_wing_l, n_tip_l, n_base_l, face_under)

    neck_sectors = 16
    neck_r = 0.36
    for s in range(neck_sectors):
        ns = (s + 1) % neck_sectors
        t0 = 2.0 * math.pi * s / neck_sectors
        t1 = 2.0 * math.pi * ns / neck_sectors
        x0, z0 = neck_r * math.sin(t0), -0.12 + neck_r * math.cos(t0)
        x1, z1 = neck_r * math.sin(t1), -0.12 + neck_r * math.cos(t1)
        add_quad((x0, -0.45, z0), (x0, -1.12, z0 - 0.06),
                 (x1, -1.12, z1 - 0.06), (x1, -0.45, z1), face_side)

    return build_mesh(vertices, faces, "Primitive (Loomis Head)", face_colors=face_colors)


_PRIMITIVE_BUILDERS = {
    "loomis_head": _make_loomis_head,
    "sphere": _make_sphere,
    "cube": _make_cube,
    "cylinder": _make_cylinder,
    "torus": _make_torus,
}


# ---- Thread-Safe Cache & Background Async Loader ------------------------------------------------


class MeshCache:
    """Thread-safe LRU cache of parsed 3D meshes."""

    def __init__(self, max_entries: int = MAX_CACHE_ENTRIES) -> None:
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: OrderedDict[Any, MeshData] = OrderedDict()

    def get(self, key: Any) -> MeshData | None:
        with self._lock:
            mesh = self._entries.get(key)
            if mesh is not None:
                self._entries.move_to_end(key)
            return mesh

    def put(self, key: Any, mesh: MeshData) -> MeshData:
        with self._lock:
            self._entries[key] = mesh
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            return mesh

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


CACHE = MeshCache()


class AsyncMeshLoader:
    """Background worker pool for parsing large 3D files without blocking the Qt UI thread."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pixelref-3d")
        self._lock = threading.Lock()
        self._pending: dict[Any, list[Callable[[MeshData | None, Exception | None], None]]] = {}

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def submit(
        self,
        key: Any,
        work: Callable[[], MeshData],
        callback: Callable[[MeshData | None, Exception | None], None],
    ) -> None:
        with self._lock:
            if key in self._pending:
                self._pending[key].append(callback)
                return
            self._pending[key] = [callback]

        def _run() -> None:
            mesh: MeshData | None = None
            err: Exception | None = None
            try:
                mesh = work()
            except Exception as exc:  # noqa: BLE001
                err = exc
            with self._lock:
                callbacks = self._pending.pop(key, [])
            for cb in callbacks:
                try:
                    cb(mesh, err)
                except Exception:  # noqa: BLE001
                    pass

        self._pool.submit(_run)


ASYNC_LOADER = AsyncMeshLoader()


def sniff_format(data: bytes, filename: str = "") -> str:
    ext = Path(filename).suffix.lower() if filename else ""
    if ext in (".obj", ".stl", ".ply", ".glb", ".gltf"):
        return ext
    head = data[:64].lstrip()
    if data[:4] == b"glTF":
        return ".glb"
    if head.startswith(b"ply"):
        return ".ply"
    if head.startswith(b"{") and b'"asset"' in data[:512]:
        return ".gltf"
    if head.startswith(b"solid ") or _is_binary_stl(data):
        return ".stl"
    return ".obj"


def load_mesh_bytes(data: bytes, filename: str = "", base_dir: Path | None = None,
                    cache_key: Any = None) -> MeshData:
    if cache_key is not None:
        cached = CACHE.get(cache_key)
        if cached is not None:
            return cached
    ext = sniff_format(data, filename)
    if ext == ".obj":
        mesh = parse_obj(data, base_dir=base_dir, filename=filename)
    elif ext == ".stl":
        mesh = parse_stl(data)
    elif ext == ".ply":
        mesh = parse_ply(data, base_dir=base_dir, filename=filename)
    elif ext in (".glb", ".gltf"):
        mesh = parse_gltf(data, base_dir=base_dir, filename=filename)
    else:
        raise ValueError(f"Unsupported 3D format: {ext}")
    if cache_key is not None:
        CACHE.put(cache_key, mesh)
    return mesh


def file_cache_key(path: Path) -> tuple[Any, ...]:
    resolved = path.resolve()
    stat = resolved.stat()
    return ("file", str(resolved), stat.st_mtime_ns, stat.st_size)


def load_mesh_file(path: Path) -> MeshData:
    resolved = path.resolve()
    key = file_cache_key(resolved)
    cached = CACHE.get(key)
    if cached is not None:
        return cached
    data = resolved.read_bytes()
    return load_mesh_bytes(data, filename=resolved.name, base_dir=resolved.parent, cache_key=key)


def load_primitive(name: str) -> MeshData:
    key_name = name.lower().strip() or "loomis_head"
    cache_key = ("primitive", key_name)
    cached = CACHE.get(cache_key)
    if cached is not None:
        return cached
    builder = _PRIMITIVE_BUILDERS.get(key_name, _make_loomis_head)
    return CACHE.put(cache_key, builder())
