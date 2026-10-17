"""Commands the kernel provides on every session (flat names in ``pxcmd``).

Node references accept a node name or its UUID. Commands that take
``nodes`` accept one name or a list; ``None`` means the current selection.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable

from . import xform
from .attributes import Attr
from .errors import CommandError, SceneError
from .values import fast_copy

if TYPE_CHECKING:
    from .session import Session


def register_kernel_commands(session: "Session") -> None:
    scene = session.scene
    reg = session.commands.register

    def names_of(nodes: Any, default_selection: bool = True) -> list[str]:
        if nodes is None:
            return session.selection.names if default_selection else []
        if isinstance(nodes, str):
            nodes = [nodes]
        return [scene.resolve(str(node)) for node in nodes]

    def one(node: Any) -> str:
        if node is None:
            selected = session.selection.names
            if not selected:
                raise CommandError("Nothing selected")
            return selected[0]
        return scene.resolve(str(node))

    def split_target(target: str, attr: str | None) -> tuple[str, str]:
        if attr is None:
            if "." not in target:
                raise CommandError(f"Expected 'node.attribute', got {target!r}")
            # Attribute names never contain dots; node names may ("ref.v2").
            node, _dot, attr = target.rpartition(".")
            return scene.resolve(node), attr
        return scene.resolve(target), attr

    def default_name(node_type: str) -> str:
        spec = session.types.get(node_type)
        label = (spec.label if spec is not None and spec.label else node_type).title().replace(" ", "")
        return label.replace("_", "")

    # ---- Scene -------------------------------------------------------------------------------

    def new_scene() -> None:
        """Clear the document and start a new scene."""
        session.new_document()

    def open_scene(path: str) -> str:
        """Open a scene file, replacing the current document."""
        session.open_file(path)
        return Path(path).as_posix()

    def save_scene(path: str | None = None) -> str:
        """Save the scene (to its current file when ``path`` is omitted)."""
        return session.save_file(path).as_posix()

    def import_scene(path: str) -> list[str]:
        """Add the nodes of another scene file to this one; returns the new root names."""
        from .scene import SceneGraph

        other = SceneGraph(session.types)
        session.format.load_into_scene(other, session.format.read_text(path))
        renamed: dict[str, str] = {}
        roots: list[str] = []
        for asset_id, asset in other.assets.items():
            scene.assets.setdefault(asset_id, asset)
        for node in other.iter_nodes():
            new_name = scene.unique_name(node.name)
            renamed[node.name] = new_name
            parent = renamed.get(node.parent) if node.parent else None
            # Keep the ids (node_ref attributes point at them) unless already in use.
            node_id = node.id if node.id and node.id not in scene.by_id else None
            scene.create_node(node.node_type, new_name, parent=parent, attrs=fast_copy(node.attrs),
                              node_id=node_id, strict=False, dynamic=dict(node.dynamic))
            if parent is None:
                roots.append(new_name)
        return roots

    def scene_info() -> dict[str, Any]:
        """Summary of the open scene."""
        counts: dict[str, int] = {}
        for node in scene.nodes.values():
            counts[node.node_type] = counts.get(node.node_type, 0) + 1
        return {
            "path": session.scene_path.as_posix() if session.scene_path else None,
            "nodes": len(scene.nodes),
            "types": dict(sorted(counts.items())),
            "assets": len(scene.assets),
            "unsaved": session.unsaved,
            "plugin_data": sorted(scene.plugin_data),
        }

    reg("new_scene", new_scene, undoable=False)
    reg("open_scene", open_scene, undoable=False)
    reg("save_scene", save_scene, undoable=False)
    reg("import_scene", import_scene, label="Import scene")
    reg("scene_info", scene_info, undoable=False, echo=False)

    # ---- Nodes ---------------------------------------------------------------------------------

    def create_node(node_type: str, name: str | None = None, parent: str | None = None,
                    attrs: dict[str, Any] | None = None, index: int | None = None, **attr_values: Any) -> str:
        """Create a node; taken names get a numbered suffix. Returns the new name."""
        values = dict(attrs or {})
        values.update(attr_values)
        final = scene.unique_name(name or default_name(node_type))
        scene.create_node(node_type, final, parent=scene.resolve(parent) if parent else None,
                          attrs=values, index=index)
        return final

    def delete(nodes: Any = None) -> list[str]:
        """Delete nodes (and everything below them)."""
        deleted: list[str] = []
        for name in names_of(nodes):
            if scene.has(name):
                deleted.extend(scene.delete_node(name))
        return deleted

    def rename(node: str, new_name: str) -> str:
        """Rename a node. Names are unique: renaming to a taken name is an error."""
        old = one(node)
        scene.rename_node(old, new_name)
        session.selection.rename(old, new_name)
        return new_name

    def duplicate(nodes: Any = None, offset: tuple[float, float] = (0.0, 0.0)) -> list[str]:
        """Copy nodes with their children; copies go on top. ``offset`` moves them (world units)."""
        roots: list[str] = []
        taken: set[str] = set()
        for root in names_of(nodes):
            node = scene.get_node(root)

            def copy_tree(name: str, parent: str | None) -> str:
                source = scene.get_node(name)
                new_name = scene.unique_name(name, taken)
                taken.add(new_name)
                scene.create_node(source.node_type, new_name, parent=parent, attrs=fast_copy(source.attrs),
                                  strict=False, dynamic=dict(source.dynamic))
                for child in source.children:
                    copy_tree(child, new_name)
                return new_name

            copy = copy_tree(root, node.parent)
            if offset and (offset[0] or offset[1]) and session.types.is_canvas(node.node_type):
                _move_world(copy, float(offset[0]), float(offset[1]))
            roots.append(copy)
        return roots

    def _move_world(name: str, dx: float, dy: float) -> None:
        node = scene.get_node(name)
        parent_m = xform.parent_world_matrix(scene, node.parent)
        inverse = parent_m.inverted()
        if inverse is None:
            return
        lx = inverse.m11 * dx + inverse.m21 * dy
        ly = inverse.m12 * dx + inverse.m22 * dy
        tx, ty = node.attrs.get("translate", [0.0, 0.0])
        scene.set_attr(name, "translate", [round(float(tx) + lx, 3), round(float(ty) + ly, 3)])

    def parent(nodes: Any = None, parent: str | None = None, index: int | None = None,
               preserve_transform: bool = True) -> list[str]:
        """Parent nodes under ``parent`` (None = scene root), keeping them in place on the canvas."""
        new_parent = scene.resolve(parent) if parent else None
        moved = []
        for name in names_of(nodes):
            node = scene.get_node(name)
            if node.parent == new_parent and index is None:
                continue
            canvas = session.types.is_canvas(node.node_type) and "translate" in node.attrs
            world = xform.world_matrix(scene, name) if canvas and preserve_transform else None
            scene.reparent(name, new_parent, index)
            if world is not None:
                inverse = xform.parent_world_matrix(scene, new_parent).inverted()
                if inverse is not None:
                    scene.set_attrs(name, xform.attrs_for_matrix(world @ inverse, node.attrs))
            moved.append(name)
        return moved

    def move_nodes(nodes: Any, parent: str | None = None, anchor: str | None = None, place: str = "end") -> None:
        """Reparent (keeping canvas placement) and position among the new siblings.

        ``place`` counts front-most first, like the outliner: "into" = in front of
        every sibling, "above"/"below" = just in front of / behind ``anchor``,
        "end" = behind every sibling.
        """
        names = names_of(nodes)
        new_parent = scene.resolve(parent) if parent else None
        for name in names:
            if scene.get_node(name).parent != new_parent:
                parent_cmd(name, new_parent)
        siblings = [n for n in reversed(scene.children_of(new_parent)) if n not in names]
        if place == "end":
            index = len(siblings)
        elif place in ("above", "below") and anchor in siblings:
            index = siblings.index(anchor) + (1 if place == "below" else 0)
        else:
            index = 0
        ordered = siblings[:index] + names + siblings[index:]
        scene.restack(list(reversed(ordered)))

    parent_cmd = parent

    def reorder(nodes: Any = None, mode: str = "front") -> None:
        """Change stacking: front, back, forward or backward (within each parent)."""
        scene.reorder(names_of(nodes), mode)

    def restack(names: list[str]) -> None:
        """Set the order of siblings, back-most first."""
        scene.restack([scene.resolve(n) for n in names])

    reg("create_node", create_node, label=lambda node_type, *a, **k: f"Create {node_type}")
    reg("delete", delete, label="Delete")
    reg("rename", rename, label=lambda node, *a, **k: f"Rename {node}")
    reg("duplicate", duplicate, label="Duplicate")
    reg("parent", parent, label="Reparent")
    reg("move_nodes", move_nodes, label=lambda nodes, parent=None, anchor=None, place="end":
        "Reparent" if place == "into" else "Reorder")
    reg("reorder", reorder, label=lambda nodes=None, mode="front": {
        "front": "Bring to front", "back": "Send to back", "forward": "Bring forward", "backward": "Send backward",
    }.get(mode, "Reorder"))
    reg("restack", restack, label="Reorder")

    # ---- Queries ---------------------------------------------------------------------------------

    def ls(type: str | list[str] | None = None, tags: str | list[str] | None = None,  # noqa: A002
           selection: bool = False, visible: bool | None = None, pattern: str | None = None,
           roots: bool = False) -> list[str]:
        """List nodes in scene order, optionally filtered."""
        wanted_types = [type] if isinstance(type, str) else list(type or [])
        wanted_tags = [tags] if isinstance(tags, str) else list(tags or [])
        chosen = set(session.selection.names) if selection else None
        out = []
        for node in scene.iter_nodes():
            if chosen is not None and node.name not in chosen:
                continue
            if roots and node.parent is not None:
                continue
            if wanted_types and not any(
                node.node_type == t or session.types.inherits(node.node_type, t) for t in wanted_types
            ):
                continue
            if wanted_tags and not set(wanted_tags) & set(node.attrs.get("tags") or []):
                continue
            if pattern and not fnmatch.fnmatchcase(node.name, pattern):
                continue
            if visible is not None:
                shown = bool(session.compute(node.name, "world_visibility")) if "visibility" in node.attrs else True
                if shown != visible:
                    continue
            out.append(node.name)
        return out

    def exists(node: str) -> bool:
        """Whether a node with this name (or id) exists."""
        try:
            scene.resolve(node)
        except SceneError:
            return False
        return True

    def node_type(node: str) -> str:
        """The type of a node."""
        return scene.get_node(one(node)).node_type

    def inherits(node: str, base: str) -> bool:
        """Whether a node (or a node type, by name) is ``base`` or derives from it."""
        if not scene.has(str(node)) and str(node) in session.types:
            return session.types.inherits(str(node), base)
        return session.types.inherits(scene.get_node(one(node)).node_type, base)

    def children(node: str, all_descendants: bool = False, type: str | None = None) -> list[str]:  # noqa: A002
        """The children (or all descendants) of a node, optionally of one type."""
        name = one(node)
        result = scene.descendants(name) if all_descendants else scene.children_of(name)
        if type:
            result = [n for n in result if session.types.inherits(scene.get_node(n).node_type, type)]
        return result

    def parent_of(node: str) -> str | None:
        """The parent of a node, or None for a top-level node."""
        return scene.get_node(one(node)).parent

    def node_id(node: str) -> str:
        """A node's permanent unique id (names can change, ids don't)."""
        return scene.get_node(one(node)).id

    for name, fn in (("ls", ls), ("exists", exists), ("node_type", node_type),
                     ("inherits", inherits), ("children", children), ("parent_of", parent_of),
                     ("node_id", node_id)):
        reg(name, fn, undoable=False, echo=False)

    # ---- Selection ----------------------------------------------------------------------------

    def select(nodes: Any = None, add: bool = False, remove: bool = False, toggle: bool = False,
               clear: bool = False) -> list[str]:
        """Change the selection (replace by default). Returns the new selection."""
        if clear or (nodes is None and not (add or remove or toggle)):
            session.selection.clear()
            return []
        names = names_of(nodes, default_selection=False)
        if add:
            session.selection.add(names)
        elif remove:
            session.selection.remove(names)
        elif toggle:
            session.selection.toggle(names)
        else:
            session.selection.set(names)
        return session.selection.names

    def selected() -> list[str]:
        """The selected node names."""
        return session.selection.names

    reg("select", select, undoable=False)
    reg("selected", selected, undoable=False, echo=False)

    # ---- Attributes -----------------------------------------------------------------------------

    def get_attr(target: str, attr: str | None = None) -> Any:
        """``get_attr("Node.attr")`` or ``get_attr("Node", "attr")`` (computed attributes too)."""
        name, attr = split_target(target, attr)
        return session.get_attr(name, attr)

    def set_attr(target: str, *values: Any) -> None:
        """``set_attr("Node.attr", value)`` or ``set_attr("Node", "attr", value)``."""
        if "." in target and not scene.has(target):
            name, attr = split_target(target, None)
            if not values:
                raise CommandError("set_attr needs a value")
            value = values[0] if len(values) == 1 else list(values)
        else:
            if len(values) < 2:
                raise CommandError("Use set_attr('node.attr', value) or set_attr('node', 'attr', value)")
            name, attr = scene.resolve(target), str(values[0])
            value = values[1] if len(values) == 2 else list(values[1:])
        scene.set_attr(name, attr, value)

    def set_attrs(node: str, attrs: dict[str, Any]) -> None:
        """Set several attributes of one node."""
        scene.set_attrs(scene.resolve(node), attrs)

    def add_attr(node: str, name: Any, type: str = "any", default: Any = None, value: Any = None,  # noqa: A002
                 **spec: Any) -> None:
        """Add a dynamic attribute to one node (saved with the scene); ``name`` may also be an ``Attr``."""
        if isinstance(name, Attr):
            scene.add_attr(scene.resolve(node), name, value)
            return
        allowed = {"short", "minimum", "maximum", "choices", "hidden", "label", "group", "doc"}
        unknown = set(spec) - allowed
        if unknown:
            raise CommandError(f"Unknown attribute options: {', '.join(sorted(unknown))}")
        if "choices" in spec:
            spec["choices"] = tuple(spec["choices"])
        scene.add_attr(scene.resolve(node), Attr(name, type, default, **spec), value)

    def delete_attr(node: str, name: str) -> None:
        """Remove a dynamic attribute."""
        scene.delete_attr(scene.resolve(node), name)

    def attrs(node: str, computed: bool = True, hidden: bool = True) -> list[str]:
        """Attribute names of a node."""
        name = one(node)
        n = scene.get_node(name)
        out = []
        specs = session.types.attrs(n.node_type) if n.node_type in session.types else {}
        for attr in specs.values():
            if (attr.computed and not computed) or (attr.hidden and not hidden):
                continue
            out.append(attr.name)
        out += [a for a in n.dynamic if a not in out]
        out += [a for a in n.attrs if a not in out]
        return out

    def attr_info(node: str, attr: str) -> dict[str, Any]:
        """The specification of one attribute."""
        name = one(node)
        n = scene.get_node(name)
        spec = scene.attr_spec(n, attr)
        if spec is None:
            if attr in n.attrs:
                return {"name": attr, "type": "any", "dynamic": False, "declared": False}
            raise SceneError(f"'{name}' has no attribute '{attr}'")
        info = spec.to_dict()
        info.update({"name": spec.name, "computed": spec.computed, "storable": spec.storable,
                     "dynamic": spec.name in scene.dynamic_attrs(n), "declared": True})
        return info

    reg("get_attr", get_attr, undoable=False, echo=False)
    reg("set_attr", set_attr, label=lambda target, *v: f"Set {target if '.' in target else target + '.' + str(v[0])}")
    reg("set_attrs", set_attrs, label=lambda node, *a, **k: f"Edit {node}")
    reg("add_attr", add_attr, label="Add attribute")
    reg("delete_attr", delete_attr, label="Delete attribute")
    reg("attrs", attrs, undoable=False, echo=False)
    reg("attr_info", attr_info, undoable=False, echo=False)

    # ---- Transforms ---------------------------------------------------------------------------

    def get_transform(nodes: Any = None, world_space: bool = False) -> Any:
        """The core transform of nodes (one dict, or a list for several nodes).

        ``world_space`` gives the transform as seen on the canvas.
        """
        targets = names_of(nodes)
        results = []
        for name in targets:
            node = scene.get_node(name)
            if world_space:
                world = xform.world_matrix(scene, name)
                tx, ty, sx, sy, rot = xform.decompose(world)
                entry = {"translate": [tx, ty], "rotate": rot, "scale": [sx, sy]}
            else:
                tx, ty, sx, sy, rot, px, py = xform.transform_values(node.attrs)
                entry = {"translate": [tx, ty], "rotate": rot, "scale": [sx, sy], "pivot": [px, py]}
            entry["world_bounds"] = session.world_bounds(name)
            results.append(entry)
        return results[0] if len(results) == 1 else results

    def set_transform(nodes: Any = None, translate: Iterable[float] | None = None, rotate: float | None = None,
                      scale: Iterable[float] | float | None = None, pivot: Iterable[float] | None = None,
                      relative: bool = False, world_space: bool = False) -> None:
        """Set the core transform attributes.

        ``relative`` adds to translate/rotate and multiplies scale.
        ``world_space`` writes the transform as seen on the canvas.
        """
        targets = names_of(nodes)
        if isinstance(scale, (int, float)):
            scale = [float(scale), float(scale)]
        for name in targets:
            node = scene.get_node(name)
            if not session.types.is_canvas(node.node_type):
                continue
            if world_space:
                world = xform.world_matrix(scene, name)
                tx, ty, sx, sy, rot = xform.decompose(world)
                if translate is not None:
                    t = list(translate)
                    tx, ty = (tx + t[0], ty + t[1]) if relative else (t[0], t[1])
                if rotate is not None:
                    rot = rot + float(rotate) if relative else float(rotate)
                if scale is not None:
                    s = list(scale)
                    sx, sy = (sx * s[0], sy * s[1]) if relative else (s[0], s[1])
                new_world = xform.compose(tx, ty, sx, sy, rot)
                inverse = xform.parent_world_matrix(scene, node.parent).inverted()
                if inverse is not None:
                    scene.set_attrs(name, xform.attrs_for_matrix(new_world @ inverse, node.attrs))
                continue
            updates: dict[str, Any] = {}
            tx, ty, sx, sy, rot, _px, _py = xform.transform_values(node.attrs)
            if translate is not None:
                t = list(translate)
                updates["translate"] = [tx + t[0], ty + t[1]] if relative else [t[0], t[1]]
            if rotate is not None:
                updates["rotate"] = xform.normalize_angle(rot + float(rotate) if relative else float(rotate))
            if scale is not None:
                s = list(scale)
                updates["scale"] = [sx * s[0], sy * s[1]] if relative else [s[0], s[1]]
            if pivot is not None:
                updates["pivot"] = list(pivot)
            if updates:
                scene.set_attrs(name, updates)

    reg("get_transform", get_transform, undoable=False, echo=False)
    reg("set_transform", set_transform, label="Transform")

    # ---- Undo ---------------------------------------------------------------------------------

    def undo() -> str | None:
        """Undo the last change; returns its label."""
        return session.undo.undo()

    def redo() -> str | None:
        """Redo the last undone change; returns its label."""
        return session.undo.redo()

    def undo_info() -> dict[str, Any]:
        """The undo and redo labels, oldest first."""
        stack = session.undo.stack
        return {"undo": stack.labels(), "next_undo": stack.undo_label(), "next_redo": stack.redo_label(),
                "can_undo": stack.can_undo(), "can_redo": stack.can_redo()}

    reg("undo", undo, undoable=False)
    reg("redo", redo, undoable=False)
    reg("undo_info", undo_info, undoable=False, echo=False)

    # ---- Plugins ----------------------------------------------------------------------------------

    def list_plugins() -> list[dict[str, Any]]:
        """Every discovered plugin with its status, version and dependencies."""
        return session.plugins.list_plugins()

    def plugin_info(plugin_id: str) -> dict[str, Any]:
        """Details of one plugin (manifest, status, error)."""
        for info in session.plugins.list_plugins():
            if info["id"] == plugin_id:
                return info
        raise CommandError(f"Unknown plugin '{plugin_id}'")

    def load_plugin(plugin_id: str) -> bool:
        """Enable a plugin (remembered between sessions)."""
        session.plugins.set_enabled(plugin_id, True)
        return session.plugins.is_active(plugin_id)

    def unload_plugin(plugin_id: str) -> None:
        """Disable a plugin (remembered between sessions); its UI disappears, nodes stay."""
        session.plugins.set_enabled(plugin_id, False)

    def reload_plugin(plugin_id: str) -> bool:
        """Re-import a plugin's code (for plugin development)."""
        return session.plugins.reload(plugin_id)

    reg("list_plugins", list_plugins, undoable=False, echo=False)
    reg("plugin_info", plugin_info, undoable=False, echo=False)
    reg("load_plugin", load_plugin, undoable=False)
    reg("unload_plugin", unload_plugin, undoable=False)
    reg("reload_plugin", reload_plugin, undoable=False)

    # ---- Python packages (pixelRef's managed folder) -------------------------------------------
    #
    # These run pip and wait for it; the Plugins window runs the same jobs in the
    # background. They are commands so scripts and batch mode can set up a machine.

    from .packages import InstallError

    def run_job(plan: Callable[[], Any]) -> dict[str, Any]:
        try:
            job = plan()
        except InstallError as exc:
            raise CommandError(str(exc)) from None
        result = job.run()
        if not result["ok"]:
            raise CommandError(result["message"])
        result.pop("output", None)
        return result

    def packages_list() -> list[dict[str, Any]]:
        """Packages in pixelRef's folder, with their version, why they're there and what needs them."""
        packages = session.packages
        installed = packages.installed()
        wanted = packages.wanted()
        needed_by: dict[str, set[str]] = {}
        for plugin_id, record in session.plugins.records.items():
            for req in record.manifest.python_requires + record.manifest.python_optional:
                needed_by.setdefault(req.key, set()).add(plugin_id)
        out = []
        for key in sorted(set(installed) | set(wanted)):
            entry = wanted.get(key)
            out.append({
                "name": key,
                "version": installed.get(key, ""),
                "requirement": entry.requirement if entry else "",
                "chosen": entry is not None,  # False: installed only as another package's dependency
                "file": entry.file if entry else "",
                "needed_by": sorted(needed_by.get(key, set()) | set(entry.plugins if entry else [])),
            })
        return out

    def packages_needed() -> dict[str, list[str]]:
        """Python packages enabled plugins are waiting for: {requirement: [plugin ids]}."""
        return session.plugins.packages_needed()

    def packages_install(requirements: Any) -> dict[str, Any]:
        """Install packages (``"numpy"``, ``["numpy>=1.26", "scipy"]``) into pixelRef's folder."""
        reqs = [requirements] if isinstance(requirements, str) else [str(r) for r in requirements]
        return run_job(lambda: session.packages.plan_install(reqs, user=True))

    def packages_install_missing(plugin_id: str | None = None) -> dict[str, Any]:
        """Install what enabled plugins (or one plugin) need to become available."""
        needed = session.plugins.packages_needed()
        if plugin_id is not None:
            record = session.plugins.get(plugin_id)
            if record is None:
                raise CommandError(f"Unknown plugin '{plugin_id}'")
            needed = {req: [plugin_id] for req in record.missing_packages or session.plugins.missing_packages(plugin_id)}
        if not needed:
            return {"ok": True, "message": "Nothing is missing.", "restart": False}
        plugins = sorted({p for ids in needed.values() for p in ids})
        return run_job(lambda: session.packages.plan_install(list(needed), plugins=plugins))

    def packages_install_file(path: str) -> dict[str, Any]:
        """Install a downloaded wheel (``.whl``) file, e.g. on a computer without internet."""
        return run_job(lambda: session.packages.plan_install_file(session.resolve_path(path)))

    def packages_remove(names: Any) -> dict[str, Any]:
        """Remove packages from pixelRef's folder (may need a restart when they're in use)."""
        return run_job(lambda: session.packages.plan_remove([names] if isinstance(names, str) else list(names)))

    def packages_update(names: Any = None) -> dict[str, Any]:
        """Update packages to the newest versions their requirements allow (all when no names)."""
        chosen = [names] if isinstance(names, str) else (list(names) if names else None)
        return run_job(lambda: session.packages.plan_update(chosen))

    def packages_info() -> dict[str, Any]:
        """Where pixelRef keeps its packages and whether a restart is pending."""
        packages = session.packages
        return {"folder": str(packages.site), "python": packages.tag, "restart_needed": packages.restart_needed,
                "wheels": str(packages.wheels), "plugin_wheel_folders": [str(p) for p in packages.extra_find_links]}

    for name, fn, echo in (
        ("packages.list", packages_list, False),
        ("packages.needed", packages_needed, False),
        ("packages.info", packages_info, False),
        ("packages.install", packages_install, True),
        ("packages.install_missing", packages_install_missing, True),
        ("packages.install_file", packages_install_file, True),
        ("packages.remove", packages_remove, True),
        ("packages.update", packages_update, True),
    ):
        reg(name, fn, undoable=False, echo=echo)

    # ---- Introspection ---------------------------------------------------------------------------

    def list_commands(pattern: str | None = None) -> list[str]:
        """Command names, optionally filtered by a glob pattern such as ``pen.*``."""
        names = session.commands.names()
        return [n for n in names if not pattern or fnmatch.fnmatchcase(n, pattern)]

    def help_cmd(command: str) -> str:
        """One-line description of a command."""
        found = session.commands.resolve(command)
        return f"pxcmd.{found.display_name} — {found.doc or 'no description'}"

    def node_types(include_abstract: bool = False) -> list[str]:
        """Registered node type names."""
        return session.types.names(include_abstract=include_abstract)

    reg("list_commands", list_commands, undoable=False, echo=False)
    reg("help", help_cmd, undoable=False, echo=False)
    reg("node_types", node_types, undoable=False, echo=False)
