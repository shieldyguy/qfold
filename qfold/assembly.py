"""Assemblies in Fusion's terms: Design, Component, Occurrence, Body, and align.

A Design has a root Component. A Component holds Bodies and Occurrences of
other components (Fusion's sub-assemblies); an Occurrence is one placed
instance of a component, with a transform relative to its parent. Several
occurrences can share one component, as with six identical jacks.

Parts are positioned with `align`: move an occurrence so a named reference
frame on it lands on a frame somewhere else, with an optional offset, angle
and flip. There are no joints and no solver. The design is rebuilt from code on
every run, so an align written against named geometry ("hole P6") is re-applied
whenever that geometry moves.

A reference ("ref") is a frame: origin plus x/y/z axes, stored as a 4x4 matrix.
Components carry named refs. Sheet metal components get one per tagged hole,
automatically: `P6` sits on the outside surface and `P6.inside` on the inside
surface, both with z pointing out of the part's outside face and x along the
face's U direction.

The kernel never sees any of this. `Design.instances()` flattens the tree into
world-placed bodies before each kernel call.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .fold import SheetLayout


# ------------------------------------------------------------------- frames

def frame(origin=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0)) -> np.ndarray:
    """4x4 frame with the given origin and z axis; x is made orthogonal to z."""
    z = np.asarray(z, float)
    z = z / np.linalg.norm(z)
    x = np.asarray(x, float)
    x = x - z * (x @ z)
    nx = np.linalg.norm(x)
    if nx < 1e-9:
        raise ValueError("frame: x is parallel to z")
    x = x / nx
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, np.cross(z, x), z, origin
    return m


def translate(v) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = v
    return m


def rot_z(angle_deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    m = np.eye(4)
    m[:2, :2] = [[c, -s], [s, c]]
    return m


def rot_x(angle_deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    m = np.eye(4)
    m[1:3, 1:3] = [[c, -s], [s, c]]
    return m


def rigid_inv(m: np.ndarray) -> np.ndarray:
    r = m[:3, :3]
    out = np.eye(4)
    out[:3, :3] = r.T
    out[:3, 3] = -r.T @ m[:3, 3]
    return out


def as4(m) -> np.ndarray:
    m = np.asarray(m, float)
    if m.shape == (4, 4):
        return m
    out = np.eye(4)
    out[:3, :] = m
    return out


# ------------------------------------------------------------------ objects

@dataclass
class Body:
    """Geometry owned by a component, in the component's coordinates."""
    name: str
    sheet: SheetLayout | None = None  # a sheet metal body (folded state)
    step: tuple[str, list[int]] | None = None  # (file, product-tree path)
    prims: list[dict] | None = None  # boxes and cylinders
    color: tuple[int, int, int] = (180, 180, 180)

    def job(self, proto: str) -> dict:
        if self.sheet is not None:
            return {"name": proto, "plates": self.sheet.plates3d, "sectors": self.sheet.sectors}
        if self.step is not None:
            return {"name": proto, "step": self.step[0], "path": list(self.step[1])}
        return {"name": proto, "prims": self.prims or []}


class Component:
    def __init__(self, name: str, bodies=(), refs=None, source: str = "", key: str | None = None):
        self.name = name
        self.key = key or name  # unique within a design; shared by its occurrences
        self.bodies: list[Body] = list(bodies)
        self.occurrences: list[Occurrence] = []
        self.refs: dict[str, np.ndarray] = dict(refs or {})
        self.source = source  # "sheet metal", "STEP: file.step", "stand-in", ...

    def add(self, component: "Component", name: str | None = None, transform=None) -> "Occurrence":
        name = name or component.name
        if any(o.name == name for o in self.occurrences):
            raise ValueError(f"{self.name}: duplicate occurrence name {name!r}")
        occ = Occurrence(name, component, as4(transform if transform is not None else np.eye(4)))
        self.occurrences.append(occ)
        return occ

    def add_ref(self, name: str, origin, z=(0, 0, 1), x=(1, 0, 0)) -> None:
        self.refs[name] = frame(origin, z, x)

    def ref(self, name: str) -> np.ndarray:
        if name == "origin":
            return np.eye(4)
        if name not in self.refs:
            raise KeyError(f"component {self.name!r} has no ref {name!r}")
        return self.refs[name]


@dataclass(eq=False)
class Occurrence:
    name: str
    component: Component
    transform: np.ndarray = field(default_factory=lambda: np.eye(4))
    parent: "Occurrence | None" = None
    design: "Design | None" = None

    @property
    def path(self) -> str:
        return self.name if self.parent is None else f"{self.parent.path}/{self.name}"

    def world(self) -> np.ndarray:
        return self.transform if self.parent is None else self.parent.world() @ self.transform

    def children(self) -> list["Occurrence"]:
        return [self.design._wrap(o, self) for o in self.component.occurrences]

    def find(self, path: str) -> "Occurrence":
        occ = self
        for part in path.split("/"):
            match = [c for c in occ.children() if c.name == part]
            if not match:
                raise KeyError(f"{occ.path}: no occurrence {part!r}")
            occ = match[0]
        return occ

    def ref(self, spec: str = "origin") -> np.ndarray:
        """World frame of a ref: "name", "child/path" (its origin) or "child/path:name"."""
        if ":" in spec:
            path, name = spec.split(":", 1)
            return self.find(path).ref(name)
        if spec in self.component.refs or spec == "origin":
            return self.world() @ self.component.ref(spec)
        return self.find(spec).ref("origin")

    def walk(self):
        yield self
        for c in self.children():
            yield from c.walk()


class Design:
    """The top of the tree. Occurrence objects are views onto the component
    tree, so a component shared by several occurrences stays one object."""

    def __init__(self, name: str):
        self.root = Component(name)
        self._occ: dict[tuple[int, int], Occurrence] = {}

    def _wrap(self, child: Occurrence, parent: Occurrence | None) -> Occurrence:
        key = (id(parent), id(child))
        if key not in self._occ:
            self._occ[key] = Occurrence(child.name, child.component, child.transform, parent, self)
        occ = self._occ[key]
        occ.transform = child.transform  # stays in sync with the component tree
        occ._src = child
        return occ

    def add(self, component: Component, name: str | None = None, transform=None) -> Occurrence:
        src = self.root.add(component, name, transform)
        return self._wrap(src, None)

    def top(self) -> list[Occurrence]:
        return [self._wrap(o, None) for o in self.root.occurrences]

    def find(self, path: str) -> Occurrence:
        first, _, rest = path.partition("/")
        match = [o for o in self.top() if o.name == first]
        if not match:
            raise KeyError(f"design has no occurrence {first!r}")
        return match[0].find(rest) if rest else match[0]

    def walk(self):
        for o in self.top():
            yield from o.walk()

    # ------------------------------------------------------------ flattening

    def components(self) -> dict[str, Component]:
        out: dict[str, Component] = {}
        for occ in self.walk():
            out.setdefault(occ.component.key, occ.component)
        return out

    @staticmethod
    def proto_name(comp: Component, body: Body) -> str:
        return f"{comp.key}#{body.name}"

    @staticmethod
    def instance_name(occ: Occurrence, body: Body) -> str:
        return occ.path if len(occ.component.bodies) == 1 else f"{occ.path}#{body.name}"

    def instances(self, under: str | None = None):
        """(instance name, prototype name, world 4x4, body) for every placed body."""
        start = [self.find(under)] if under else self.top()
        for top in start:
            for occ in top.walk():
                w = occ.world()
                for b in occ.component.bodies:
                    yield self.instance_name(occ, b), self.proto_name(occ.component, b), w, b

    def group(self, *paths: str) -> list[str]:
        """Instance names under each occurrence path (an occurrence and its children)."""
        names = []
        for p in paths:
            names += [n for n, *_ in self.instances(p)]
        if not names:
            raise KeyError(f"no bodies under {paths}")
        return names

    def job(self) -> dict:
        bodies, seen = [], set()
        for comp in self.components().values():
            for b in comp.bodies:
                proto = self.proto_name(comp, b)
                if proto not in seen:
                    seen.add(proto)
                    bodies.append(b.job(proto))
        inst = [{"name": n, "body": p, "place": w[:3].tolist()} for n, p, w, _ in self.instances()]
        return {"bodies": bodies, "instances": inst}

    def step_tree(self) -> dict:
        """Nested tree for the kernel's named assembly STEP export."""
        def node(comp: Component, name: str, transform) -> dict:
            return {"name": name, "component": comp.key, "transform": transform[:3].tolist(),
                    "bodies": [self.proto_name(comp, b) for b in comp.bodies],
                    "children": [node(o.component, o.name, o.transform)
                                 for o in comp.occurrences]}
        return node(self.root, self.root.name, np.eye(4))

    def parts_list(self) -> list[dict]:
        """Fusion-style parts list: leaf components (those with bodies) and counts."""
        rows: dict[str, dict] = {}
        for occ in self.walk():
            c = occ.component
            if not c.bodies:
                continue
            r = rows.setdefault(c.key, {"component": c.name, "qty": 0, "source": c.source})
            r["qty"] += 1
        return list(rows.values())


# ---------------------------------------------------------------------- align

def align(occ: Occurrence, ref: str, onto: np.ndarray, offset=(0, 0, 0), angle: float = 0.0,
          flip: bool = False) -> Occurrence:
    """Move `occ` so its ref frame lands on the world frame `onto`.

    offset is in the target frame, angle turns about the target's z axis, and
    flip turns the moving frame half a turn about its x axis (z reversed).
    """
    local = rigid_inv(occ.world()) @ occ.ref(ref)
    adjust = translate(offset) @ rot_z(angle) @ (rot_x(180) if flip else np.eye(4))
    world = as4(onto) @ adjust @ rigid_inv(local)
    parent = occ.parent.world() if occ.parent is not None else np.eye(4)
    new = rigid_inv(parent) @ world
    occ.transform = new
    occ._src.transform = new
    return occ


def mismatch(a: np.ndarray, b: np.ndarray) -> dict:
    """How far frame a is from frame b: offset in b's plane, along b's z, and angle."""
    d = a[:3, 3] - b[:3, 3]
    z = b[:3, 2]
    axial = float(d @ z)
    radial = float(np.linalg.norm(d - axial * z))
    ang = math.degrees(math.acos(float(np.clip(a[:3, 2] @ z, -1, 1))))
    return {"radial": radial, "axial": axial, "angle_deg": ang}


# ------------------------------------------------------------------- builders

def sheet_component(name: str, lay: SheetLayout, color=(180, 180, 180)) -> Component:
    """Component with one sheet metal body and a ref on every tagged hole."""
    comp = Component(name, [Body(lay.body.name, sheet=lay, color=color)], source="sheet metal")
    T = lay.body.rule.T
    for st in lay.faces.values():
        out = -st.N  # out of the outside face
        for h in st.face.holes:
            if not h.tag:
                continue
            if h.tag in comp.refs:
                raise ValueError(f"{name}: duplicate hole tag {h.tag}")
            comp.refs[h.tag] = frame(st.to3d(*h.c, 0.0), out, st.U)
            comp.refs[f"{h.tag}.inside"] = frame(st.to3d(*h.c, T), out, st.U)
    return comp


def prim_component(name: str, prims: list[dict], color=(60, 60, 60), source="stand-in",
                   refs=None) -> Component:
    return Component(name, [Body(name, prims=prims, color=color)], refs=refs, source=source)


def box(o, dx, dy, dz, x=(1, 0, 0), z=(0, 0, 1)) -> dict:
    return {"kind": "box", "o": list(map(float, o)), "xdir": list(map(float, x)),
            "zdir": list(map(float, z)), "dx": dx, "dy": dy, "dz": dz}


def cyl(p, d, length, direction=(0, 0, 1), bore: float = 0.0) -> dict:
    """A cylinder from point p, diameter d, along direction. bore > 0 makes it
    a tube with that inside diameter (a nut, a standoff, a bushing)."""
    return {"kind": "cyl", "p": list(map(float, p)), "dir": list(map(float, direction)), "d": d,
            "len": length, "bore": float(bore)}


def insert_step(parent, path: str | Path, tree: list[dict] | None = None,
                name: str | None = None, color=(60, 60, 60),
                colors: dict[str, tuple] | None = None) -> Occurrence:
    """Insert a STEP file (Fusion: Insert into Current Design).

    Each product in the file becomes a Component and each instance an
    Occurrence, so names and sub-assemblies survive: a part named RV1 in the
    file is the occurrence "<name>/RV1", and `align(board, "RV1", onto=...)`
    places the whole board by that part's origin. `parent` is a Design or an
    Occurrence. `colors` maps component names to render colours; others get
    `color`. `tree` is the file's product tree (kernel.inspect_step); it is
    read from the file when not given.
    """
    if tree is None:
        from . import kernel
        tree = kernel.inspect_step(Path(path))
    path = str(Path(path).resolve())
    stem = Path(path).stem
    cache: dict[str, Component] = {}

    def build(node: dict) -> Component:
        key = f"{stem}:{node['component'] or node['name']}"
        if key in cache:
            return cache[key]
        comp = Component(node["component"] or node["name"], key=key, source=f"STEP: {stem}.step")
        if not node["assembly"]:
            comp.bodies.append(Body(comp.name, step=(path, node["path"]),
                                    color=(colors or {}).get(comp.name, color)))
        for ch in node["children"]:
            comp.add(build(ch), ch["name"], ch["transform"])
        cache[key] = comp
        return comp

    if len(tree) == 1:
        top, transform = build(tree[0]), as4(tree[0]["transform"])
    else:
        top = Component(stem, key=f"{stem}:", source=f"STEP: {stem}.step")
        for root in tree:
            top.add(build(root), root["name"], root["transform"])
        transform = np.eye(4)
    if isinstance(parent, Design):
        return parent.add(top, name or top.name, transform)
    src = parent.component.add(top, name or top.name, transform)
    return parent.design._wrap(src, parent)
