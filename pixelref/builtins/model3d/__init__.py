"""3D reference objects: the ``model3d`` node type, commands and storage management.

A ``model3d`` node points at a linked 3D file (``path``), an embedded asset
(``asset``), or a built-in reference primitive (``primitive``). New 3D files
are linked by file path by default so large meshes keep ``.pxref`` files small,
and can be embedded into the scene at any time with ``model3d.embed``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pixelref.plugin_api import CAP_CONTENT, Attr, CommandError, NodeType, differs

TYPE = "model3d"
SUPPORTED_MODEL_EXTS = {".obj", ".glb", ".gltf", ".stl", ".ply"}
SUPPORTED_TEXTURE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga"}
DEFAULT_SIZE = (400.0, 400.0)
ARRANGE_GAP = 12.0

PRIMITIVES = ("loomis_head", "sphere", "cube", "cylinder", "torus")
PRIMITIVE_LABELS: dict[str, str] = {
    "loomis_head": "Loomis Head",
    "sphere": "Sphere",
    "cube": "Cube",
    "cylinder": "Cylinder",
    "torus": "Torus",
}

SHADING_MODES = ("studio", "clay", "matcap", "flat", "normals", "wireframe", "silhouette")
SHADING_LABELS: dict[str, str] = {
    "studio": "Studio Lit",
    "clay": "Clay",
    "matcap": "MatCap",
    "flat": "Flat",
    "normals": "Normals",
    "wireframe": "Wireframe",
    "silhouette": "Silhouette",
}

PROJECTIONS = ("perspective", "orthographic")

PRESET_VIEWS: dict[str, dict[str, Any]] = {
    "front": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0},
    "back": {"yaw": 180.0, "pitch": 0.0, "roll": 0.0},
    "right": {"yaw": 90.0, "pitch": 0.0, "roll": 0.0},
    "left": {"yaw": -90.0, "pitch": 0.0, "roll": 0.0},
    "top": {"yaw": 0.0, "pitch": 89.0, "roll": 0.0},
    "bottom": {"yaw": 0.0, "pitch": -89.0, "roll": 0.0},
    "three_quarter": {"yaw": 35.0, "pitch": 20.0, "roll": 0.0},
    "reset": {"yaw": 35.0, "pitch": 20.0, "roll": 0.0, "distance": 2.6, "target": [0.0, 0.0]},
}


def compute_local_bounds(_session: Any, node: Any) -> list[float]:
    size = node.attrs.get("size", DEFAULT_SIZE)
    try:
        w = max(40.0, float(size[0]))
        h = max(40.0, float(size[1]))
    except (TypeError, ValueError, IndexError):
        w, h = DEFAULT_SIZE
    return [0.0, 0.0, w, h]


MODEL3D_TYPE = NodeType(
    name=TYPE,
    inherits="canvas_node",
    label="3D Object",
    capabilities=frozenset({CAP_CONTENT}),
    compute={"local_bounds": compute_local_bounds},
    attrs=(
        # Source & Viewport
        Attr("asset", "asset", "", group="Model", doc="Embedded 3D file asset id; wins over path when set"),
        Attr("path", "path", "", group="Model", doc="Linked 3D model file (.obj, .glb, .gltf, .stl, .ply)"),
        Attr("primitive", "string", "", group="Model", doc="Built-in primitive name when no file/asset is used"),
        Attr("size", "float2", [400.0, 400.0], minimum=60.0, soft_max=2048.0, step=10.0, precision=0, group="Model",
             label="Viewport size", doc="3D viewport width and height in scene pixels"),
        # 3D Camera
        Attr("yaw", "float", 35.0, soft_min=-180.0, soft_max=180.0, step=5.0, precision=1, unit="°", group="Camera",
             doc="Camera horizontal orbit angle in degrees"),
        Attr("pitch", "float", 20.0, minimum=-89.5, maximum=89.5, step=5.0, precision=1, unit="°", group="Camera",
             doc="Camera vertical elevation angle in degrees"),
        Attr("roll", "float", 0.0, minimum=-180.0, maximum=180.0, step=5.0, precision=1, unit="°", group="Camera",
             doc="Camera roll angle in degrees"),
        Attr("distance", "float", 2.6, minimum=0.25, maximum=30.0, soft_min=0.8, soft_max=8.0, step=0.1, precision=2,
             group="Camera", doc="Camera distance in normalized model radii"),
        Attr("target", "float2", [0.0, 0.0], step=0.05, precision=3, group="Camera",
             doc="Camera pan offset [x, y] in view space"),
        Attr("projection", "enum", "perspective", choices=PROJECTIONS, group="Camera",
             doc="Camera projection mode (perspective or orthographic)"),
        Attr("fov", "float", 45.0, minimum=5.0, maximum=120.0, soft_min=15.0, soft_max=90.0, step=1.0, precision=0,
             unit="°", group="Camera", label="Field of view", doc="Vertical field of view for perspective projection"),
        # Shading, Texture & Lighting
        Attr("shading", "enum", "studio", choices=SHADING_MODES, group="Shading",
             doc="Surface shading style for reference viewing"),
        Attr("show_texture", "bool", True, group="Shading", label="Show texture",
             doc="Display model texture when available (Studio and Flat shading)"),
        Attr("texture_path", "path", "", group="Shading", label="Texture file",
             doc="Optional texture image file override (.png, .jpg, .webp)"),
        Attr("texture_asset", "asset", "", group="Shading", hidden=True,
             doc="Embedded texture image asset id"),
        Attr("wireframe_overlay", "bool", False, group="Shading", label="Wireframe overlay",
             doc="Draw mesh topology edges over the shaded surface"),
        Attr("base_color", "color", [196, 200, 208], group="Shading", label="Base color",
             doc="Surface color for clay, silhouette and uncolored meshes"),
        Attr("background_color", "color4", [24, 27, 34, 0], group="Shading", label="Background",
             doc="Viewport background color and alpha (alpha 0 = transparent cutout)"),
        Attr("light_azimuth", "float", 45.0, soft_min=-180.0, soft_max=180.0, step=5.0, precision=1, unit="°",
             group="Lighting", label="Light azimuth", doc="Key light horizontal angle relative to the view"),
        Attr("light_elevation", "float", 35.0, minimum=-90.0, maximum=90.0, step=5.0, precision=1, unit="°",
             group="Lighting", label="Light elevation", doc="Key light vertical angle relative to the view"),
        Attr("light_intensity", "float", 1.0, minimum=0.0, maximum=3.0, soft_max=2.0, step=0.05, precision=2,
             group="Lighting", label="Light intensity", doc="Key light brightness"),
        Attr("ambient", "float", 0.25, minimum=0.0, maximum=1.0, step=0.05, precision=2, group="Lighting",
             label="Ambient fill", doc="Ambient fill light intensity"),
    ),
    quick_attrs=("shading", "show_texture", "wireframe_overlay", "projection", "fov"),
)


class Model3DApi:
    """Creating and managing 3D reference nodes (shared by headless commands and UI)."""

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.session = ctx.session

    @property
    def embed_new(self) -> bool:
        return bool(self.ctx.settings.value("embed_models", False, type=bool))

    @embed_new.setter
    def embed_new(self, value: bool) -> None:
        self.ctx.settings.setValue("embed_models", bool(value))

    def source_from_file(self, path: Path, embed: bool | None = None) -> dict[str, Any]:
        """Source attributes for a 3D file: linked by path by default, or embedded in scene assets."""
        should_embed = self.embed_new if embed is None else bool(embed)
        if should_embed:
            try:
                data = Path(path).read_bytes()
            except OSError:
                return {"path": self.session.to_scene_path(path), "asset": "", "primitive": ""}
            return {
                "asset": self.session.scene.add_asset(data, name=Path(path).name),
                "path": "",
                "primitive": "",
            }
        return {"path": self.session.to_scene_path(path), "asset": "", "primitive": ""}

    def create(self, source: dict[str, Any], name_hint: str, x: float, y: float, centered: bool = True,
               **extra_attrs: Any) -> str:
        scene = self.session.scene
        name = scene.unique_name(name_hint or "Model3D")
        size = extra_attrs.get("size", DEFAULT_SIZE)
        w, h = float(size[0]), float(size[1])
        if centered:
            x -= w / 2.0
            y -= h / 2.0
        attrs = {
            **source,
            "size": [round(w, 1), round(h, 1)],
            "translate": [round(x, 3), round(y, 3)],
            "scale": [1.0, 1.0],
            "rotate": 0.0,
            **{k: v for k, v in extra_attrs.items() if k != "size"},
        }
        scene.create_node(TYPE, name, attrs=attrs)
        scene.emit("media.added", {"name": name})
        return name

    def create_from_path(self, path: Path, x: float, y: float, centered: bool = True,
                         embed: bool | None = None) -> str:
        return self.create(self.source_from_file(path, embed=embed), Path(path).stem or "Model3D", x, y, centered)

    def create_row(self, paths: list[Path], x: float, y: float, embed: bool | None = None) -> list[str]:
        """Add multiple 3D models side by side, centred on (x, y)."""
        if not paths:
            return []
        w, h = DEFAULT_SIZE
        total = w * len(paths) + ARRANGE_GAP * max(0, len(paths) - 1)
        left = x - total / 2.0
        names: list[str] = []
        for path in paths:
            source = self.source_from_file(path, embed=embed)
            names.append(self.create(source, Path(path).stem or "Model3D", left + w / 2.0, y, centered=True))
            left += w + ARRANGE_GAP
        return names

    def create_primitive(self, primitive: str = "loomis_head", x: float = 0.0, y: float = 0.0,
                         name_hint: str | None = None, centered: bool = True, **extra_attrs: Any) -> str:
        key = primitive.lower().strip()
        if key not in PRIMITIVES:
            raise CommandError(f"Unknown 3D primitive '{primitive}' (choose from {', '.join(PRIMITIVES)})")
        hint = name_hint or PRIMITIVE_LABELS.get(key, "Model3D").replace(" ", "_")
        return self.create({"primitive": key, "path": "", "asset": ""}, hint, x, y, centered=centered, **extra_attrs)


def activate(ctx: Any) -> None:
    session = ctx.session
    scene = session.scene
    ctx.register_node_type(MODEL3D_TYPE)
    api = Model3DApi(ctx)
    ctx.provide_service("models3d", api)

    def model_names(nodes: Any = None) -> list[str]:
        names = session.selection.names if nodes is None else [
            scene.resolve(n) for n in ([nodes] if isinstance(nodes, str) else nodes)
        ]
        return [n for n in names if scene.has(n) and scene.get_node(n).node_type == TYPE]

    def require_models(nodes: Any = None) -> list[str]:
        names = model_names(nodes)
        if not names:
            raise CommandError("Select a 3D object")
        return names

    def import_model(path: str, translate: Any = None, name: str | None = None, embed: bool | None = None,
                     centered: bool = True) -> str:
        """Add a 3D model file (.obj, .glb, .gltf, .stl, .ply). Linked by default unless embed=True."""
        file = session.resolve_path(path)
        if not file.is_file():
            raise CommandError(f"No such 3D model file: {path}")
        if file.suffix.lower() not in SUPPORTED_MODEL_EXTS:
            raise CommandError(f"Not a supported 3D model file: {path}")
        x, y = (float(translate[0]), float(translate[1])) if translate is not None else (0.0, 0.0)
        created = api.create(api.source_from_file(file, embed=embed), name or file.stem, x, y, centered=centered)
        session.selection.set([created])
        return created

    def import_models(paths: list[str], translate: Any = None, embed: bool | None = None) -> list[str]:
        """Add several 3D model files side by side, centred on ``translate``."""
        files = [session.resolve_path(p) for p in paths]
        missing = [str(f) for f in files if not f.is_file()]
        if missing:
            raise CommandError(f"No such 3D model file: {missing[0]}")
        unsupported = [str(f) for f in files if f.suffix.lower() not in SUPPORTED_MODEL_EXTS]
        if unsupported:
            raise CommandError(f"Not a supported 3D model file: {unsupported[0]}")
        x, y = (float(translate[0]), float(translate[1])) if translate is not None else (0.0, 0.0)
        names = api.create_row(files, x, y, embed=embed)
        session.selection.set(names)
        return names

    def create_primitive(primitive: str = "loomis_head", translate: Any = None, name: str | None = None,
                         centered: bool = True, **attrs: Any) -> str:
        """Create a built-in 3D reference primitive (loomis_head, sphere, cube, cylinder, torus)."""
        x, y = (float(translate[0]), float(translate[1])) if translate is not None else (0.0, 0.0)
        created = api.create_primitive(primitive, x, y, name_hint=name, centered=centered, **attrs)
        session.selection.set([created])
        return created

    def set_model_attrs(updates: dict[str, Any], nodes: Any = None, label: str = "Edit 3D view") -> list[str]:
        """Update attributes on the selected (or specified) 3D objects."""
        names = require_models(nodes)
        changed: list[str] = []
        for name in names:
            attrs = scene.get_node(name).attrs
            change = {k: v for k, v in updates.items() if differs(attrs.get(k), v)}
            if change:
                scene.set_attrs(name, change)
                changed.append(name)
        return changed

    def set_preset_view(preset: str = "three_quarter", nodes: Any = None) -> list[str]:
        """Snap the 3D camera to a preset angle (front, back, left, right, top, bottom, three_quarter, reset)."""
        key = preset.lower().strip()
        if key not in PRESET_VIEWS:
            raise CommandError(f"Unknown 3D camera preset '{preset}' ({', '.join(PRESET_VIEWS)})")
        return set_model_attrs(dict(PRESET_VIEWS[key]), nodes, label=f"3D view: {key.replace('_', ' ')}")

    def set_shading(shading: str = "studio", nodes: Any = None) -> list[str]:
        """Set the shading mode of the selected 3D objects."""
        key = shading.lower().strip()
        if key not in SHADING_MODES:
            raise CommandError(f"Unknown shading mode '{shading}' ({', '.join(SHADING_MODES)})")
        return set_model_attrs({"shading": key}, nodes, label=f"3D shading: {SHADING_LABELS.get(key, key)}")

    def toggle_wireframe(nodes: Any = None) -> bool:
        """Toggle wireframe topology overlay on the selected 3D objects."""
        names = require_models(nodes)
        on = not all(bool(scene.get_node(n).attrs.get("wireframe_overlay", False)) for n in names)
        set_model_attrs({"wireframe_overlay": on}, names, label="Toggle 3D wireframe")
        return on

    def toggle_projection(nodes: Any = None) -> str:
        """Toggle between perspective and orthographic camera projection on the selected 3D objects."""
        names = require_models(nodes)
        all_ortho = all(scene.get_node(n).attrs.get("projection", "perspective") == "orthographic" for n in names)
        mode = "perspective" if all_ortho else "orthographic"
        set_model_attrs({"projection": mode}, names, label=f"3D projection: {mode}")
        return mode

    def toggle_texture(nodes: Any = None) -> bool:
        """Toggle texture display on the selected 3D objects."""
        names = require_models(nodes)
        on = not all(bool(scene.get_node(n).attrs.get("show_texture", True)) for n in names)
        set_model_attrs({"show_texture": on}, names, label="Toggle 3D texture")
        return on

    def set_texture(path: Any, nodes: Any = None) -> list[str]:
        """Assign an external texture image file (.png, .jpg, .webp) to the selected 3D objects."""
        if isinstance(path, (list, tuple)) and isinstance(nodes, (str, Path)):
            path, nodes = nodes, path
        file = session.resolve_path(str(path))
        if not file.is_file():
            raise CommandError(f"No such texture image file: {path}")
        names = require_models(nodes)
        rel = session.to_scene_path(file)
        return set_model_attrs(
            {"texture_path": rel, "texture_asset": "", "show_texture": True},
            names,
            label="Set 3D texture",
        )

    def clear_texture(nodes: Any = None) -> list[str]:
        """Clear any custom texture override on the selected 3D objects."""
        names = require_models(nodes)
        return set_model_attrs({"texture_path": "", "texture_asset": ""}, names, label="Clear 3D texture")

    # ---- Storage (linked by default, optional embedding in .pxref) ---------------------------

    def embed(nodes: Any = None) -> int:
        """Copy linked 3D model files (and companion textures) into the scene file."""
        from .loader import find_companion_texture

        embedded, missing = 0, []
        for name in model_names(nodes):
            attrs = scene.get_node(name).attrs
            if attrs.get("asset") or not attrs.get("path"):
                continue
            path = session.resolve_path(str(attrs["path"]))
            try:
                data = path.read_bytes()
            except OSError:
                missing.append(name)
                continue
            updates: dict[str, Any] = {
                "asset": scene.add_asset(data, name=path.name),
                "path": "",
                "primitive": "",
            }
            if not attrs.get("texture_asset"):
                tex_file: Path | None = None
                if attrs.get("texture_path"):
                    cand = session.resolve_path(str(attrs["texture_path"]))
                    if cand.is_file():
                        tex_file = cand
                else:
                    tex_file = find_companion_texture(path.parent, filename=path.name)
                if tex_file is not None:
                    try:
                        updates["texture_asset"] = scene.add_asset(tex_file.read_bytes(), name=tex_file.name)
                        updates["texture_path"] = ""
                    except OSError:
                        pass
            scene.set_attrs(name, updates)
            embedded += 1
        note = f" · {len(missing)} missing file(s) could not be embedded" if missing else ""
        session.events.emit("ui.status", {"text": f"Embedded {embedded} 3D model(s) in the scene{note}"})
        return embedded

    def embed_all() -> int:
        """Embed every linked 3D model in the scene."""
        return embed([n.name for n in scene.iter_nodes() if n.node_type == TYPE])

    def link(node: str, path: str) -> None:
        """Link a 3D node to a file on disk instead of embedded data."""
        name = scene.resolve(node)
        if scene.get_node(name).node_type != TYPE:
            raise CommandError(f"'{name}' is not a 3D object")
        scene.set_attrs(name, {"path": session.to_scene_path(Path(path)), "asset": "", "primitive": ""})

    def extract(node: str, path: str) -> str:
        """Write an embedded 3D model to ``path`` and link to that file."""
        name = scene.resolve(node)
        asset = scene.get_asset(str(scene.get_node(name).attrs.get("asset", "") or ""))
        if asset is None:
            raise CommandError(f"'{name}' has no embedded 3D model")
        Path(path).write_bytes(asset.data)
        scene.set_attrs(name, {"path": session.to_scene_path(Path(path)), "asset": "", "primitive": ""})
        return str(path)

    def set_embed_new(on: bool) -> bool:
        """Whether newly added 3D models are embedded (True) or linked by path (False)."""
        api.embed_new = bool(on)
        mode = "embedded in the scene file" if on else "linked by file path"
        session.events.emit("ui.status", {"text": f"New 3D models will be {mode}"})
        return api.embed_new

    def prepare_paths(sess: Any, scene_path: Path) -> None:
        """Make linked 3D model and texture paths portable on save: stored relative to the scene when possible."""
        new_dir = Path(scene_path).parent.resolve()
        for node in list(scene.iter_nodes()):
            attrs = node.attrs
            if node.node_type != TYPE:
                continue
            for path_key, asset_key in (("path", "asset"), ("texture_path", "texture_asset")):
                if attrs.get(asset_key) or not attrs.get(path_key):
                    continue
                source = sess.resolve_path(str(attrs[path_key]))
                try:
                    value = source.resolve().relative_to(new_dir).as_posix()
                except (ValueError, OSError):
                    value = source.as_posix()
                if value != attrs[path_key]:
                    scene.set_attr(node.name, path_key, value)

    ctx.command("import_model", import_model, alias="import_model", label="Insert 3D object")
    ctx.command("import_models", import_models, alias="import_models", label="Insert 3D objects")
    ctx.command("create_primitive", create_primitive, alias="create_primitive", label="Add 3D primitive")
    ctx.command("set_attrs", set_model_attrs, label=lambda updates, nodes=None, label="Edit 3D view": label)
    ctx.command("set_preset_view", set_preset_view, alias="set_3d_view", label="Set 3D camera preset")
    ctx.command("set_shading", set_shading, alias="set_3d_shading", label="Set 3D shading")
    ctx.command("toggle_wireframe", toggle_wireframe, alias="toggle_3d_wireframe", label="Toggle 3D wireframe")
    ctx.command("toggle_projection", toggle_projection, alias="toggle_3d_projection", label="Toggle 3D projection")
    ctx.command("toggle_texture", toggle_texture, alias="toggle_3d_texture", label="Toggle 3D texture")
    ctx.command("set_texture", set_texture, alias="set_3d_texture", label="Set 3D texture")
    ctx.command("clear_texture", clear_texture, alias="clear_3d_texture", label="Clear 3D texture")
    ctx.command("embed", embed, alias="embed_models", label="Embed 3D models")
    ctx.command("embed_all", embed_all, alias="embed_all_models", label="Embed all 3D models")
    ctx.command("link", link, alias="link_model", label="Link 3D model to file")
    ctx.command("extract", extract, alias="extract_model", label="Save 3D model as file")
    ctx.command("set_embed_new", set_embed_new, alias="set_embed_new_models", undoable=False)
    ctx.contribute("scene.before_save", prepare_paths)


def activate_ui(ui: Any) -> None:
    from .ui import activate_model3d_ui

    activate_model3d_ui(ui)
