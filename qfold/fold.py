"""Fold engine: a tree of flat faces joined by bends.

A part is a root face plus bends hanging off its edges, each bend carrying a
child face, recursively. From that one description the engine produces both
states of the part:

* folded: planar plates (face outline + holes, extruded by thickness) and bend
  sectors (annular sectors swept about each bend axis), in 3D;
* flat: the same plates laid out in the plane, joined by bend-allowance strips,
  plus the bend centre lines for the flat-pattern DXF.

Conventions
-----------
Every face has a frame (O, U, V, N). Face-local 2D coordinates (u, v) map to
O + u*U + v*V on the *reference surface*; material occupies s in [0, T] along N.
A bend is attached to a straight edge of its parent face, given in parent
coordinates by a start point p0, a unit direction d along the edge and a unit
in-plane direction o pointing out of the parent. Its child face uses u along o
(away from the bend) and v along d, with its origin at the image of p0, so
child coordinates (0, v) sit exactly where the bend ends.

A positive bend angle folds toward the material side (+N). Then the reference
surface is the outside of the bend and outer dimensions are reference
dimensions, which is how the synth case is specified. Negative angles fold the
other way. Bend allowance is the arc length of the neutral surface,
theta * (R + K*T), with R the inside radius.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class Material:
    name: str
    T: float  # thickness, mm
    R: float  # default inside bend radius, mm
    K: float  # K-factor (neutral axis position from the inside surface, 0..1)


@dataclass
class Hole:
    kind: str  # "circle" | "rrect" | "poly"
    c: tuple[float, float] = (0.0, 0.0)
    d: float = 0.0  # circle diameter
    w: float = 0.0  # rrect width (along u before rotation)
    h: float = 0.0  # rrect height
    r: float = 0.0  # rrect corner radius
    rot: float = 0.0  # rrect rotation, degrees
    pts: list | None = None  # polygon points
    label: str = ""  # e.g. "3.5mm jack"
    tag: str = ""  # short id used on drawings, e.g. "B1"
    edge_cl: float = 0.0  # hardware rule: min distance hole centre to any edge or bend line

    def to_job(self) -> dict:
        if self.kind == "circle":
            return {"kind": "circle", "c": list(self.c), "d": self.d}
        if self.kind == "rrect":
            return {"kind": "rrect", "c": list(self.c), "w": self.w, "h": self.h, "r": self.r,
                    "rot": self.rot}
        return {"kind": "poly", "pts": [list(p) for p in self.pts]}

    def boundary(self, n: int = 48) -> np.ndarray:
        """Sampled boundary in face coordinates, for clearance checks and drawings."""
        if self.kind == "circle":
            a = np.linspace(0, 2 * math.pi, n, endpoint=False)
            return np.c_[self.c[0] + self.d / 2 * np.cos(a), self.c[1] + self.d / 2 * np.sin(a)]
        if self.kind == "rrect":
            hw, hh = self.w / 2, self.h / 2
            r = max(0.0, min(self.r, hw, hh))
            pts = []
            for cx, cy, a0 in ((hw - r, -hh + r, -90), (hw - r, hh - r, 0),
                               (-hw + r, hh - r, 90), (-hw + r, -hh + r, 180)):
                for a in np.linspace(a0, a0 + 90, max(2, n // 4)):
                    t = math.radians(a)
                    pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
            pts = np.array(pts)
            c, s = math.cos(math.radians(self.rot)), math.sin(math.radians(self.rot))
            rotm = np.array([[c, -s], [s, c]])
            return pts @ rotm.T + np.array(self.c)
        return np.array(self.pts, dtype=float)

    def size_text(self) -> str:
        if self.kind == "circle":
            return f"Ø{self.d:g}"
        if self.kind == "rrect":
            return f"{self.w:g}×{self.h:g}" + (f" R{self.r:g}" if self.r else "")
        return "profile"


@dataclass
class Face:
    name: str
    outline: list[tuple[float, float]]
    holes: list[Hole] = field(default_factory=list)
    bends: list["Bend"] = field(default_factory=list)


@dataclass
class Bend:
    name: str
    p0: tuple[float, float]
    d: tuple[float, float]
    o: tuple[float, float]
    v0: float
    v1: float
    angle: float  # degrees; + folds toward the material side
    child: Face
    radius: float | None = None  # inside radius override


@dataclass
class Part:
    name: str
    material: Material
    root: Face
    # Root frame in part coordinates.
    origin: tuple = (0.0, 0.0, 0.0)
    U: tuple = (1.0, 0.0, 0.0)
    V: tuple = (0.0, 1.0, 0.0)
    N: tuple = (0.0, 0.0, 1.0)
    # 3x4 matrix placing part coordinates into the assembly.
    placement: np.ndarray = field(default_factory=lambda: np.eye(4)[:3])


def rotation(axis: np.ndarray, angle: float) -> np.ndarray:
    a = axis / np.linalg.norm(axis)
    k = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(angle) * k + (1 - math.cos(angle)) * (k @ k)


@dataclass
class FaceState:
    face: Face
    O: np.ndarray
    U: np.ndarray
    V: np.ndarray
    N: np.ndarray
    O2: np.ndarray  # flat-pattern frame
    U2: np.ndarray
    V2: np.ndarray

    def to3d(self, u: float, v: float, s: float = 0.0) -> np.ndarray:
        return self.O + u * self.U + v * self.V + s * self.N

    def to2d(self, u: float, v: float) -> np.ndarray:
        return self.O2 + u * self.U2 + v * self.V2


@dataclass
class BendState:
    bend: Bend
    parent: str
    R: float
    allowance: float
    line2d: tuple[np.ndarray, np.ndarray]  # centre line of the bend strip, flat pattern
    axis_point: np.ndarray  # a point on the bend axis (3D, part coords)
    axis: np.ndarray


@dataclass
class Layout:
    part: Part
    faces: dict[str, FaceState]
    bends: list[BendState]
    plates3d: list[dict]
    sectors: list[dict]
    plates_flat: list[dict]

    def placed(self, p: np.ndarray) -> np.ndarray:
        m = self.part.placement
        return m[:, :3] @ p + m[:, 3]

    def placed_dir(self, d: np.ndarray) -> np.ndarray:
        return self.part.placement[:, :3] @ d


def _frame(O, U, V, N) -> dict:
    return {"o": list(map(float, O)), "u": list(map(float, U)), "v": list(map(float, V)),
            "n": list(map(float, N))}


def layout(part: Part) -> Layout:
    mat = part.material
    T = mat.T
    faces: dict[str, FaceState] = {}
    bends: list[BendState] = []
    plates3d, sectors, plates_flat = [], [], []

    O = np.array(part.origin, float)
    U, V, N = (np.array(x, float) for x in (part.U, part.V, part.N))
    # The flat pattern must be a rigid (orientation-preserving) development of
    # the reference surface, so match the root frame's handedness.
    hand = 1.0 if np.dot(np.cross(U, V), N) > 0 else -1.0
    root = FaceState(part.root, O, U, V, N, np.zeros(2), np.array([1.0, 0.0]),
                     np.array([0.0, hand]))

    def visit(st: FaceState) -> None:
        f = st.face
        if f.name in faces:
            raise ValueError(f"duplicate face name {f.name!r}")
        faces[f.name] = st
        holes = [h.to_job() for h in f.holes]
        plates3d.append({"frame": _frame(st.O, st.U, st.V, st.N), "T": T,
                         "outline": [list(p) for p in f.outline], "holes": holes})
        plates_flat.append({
            "frame": _frame([*st.O2, 0], [*st.U2, 0], [*st.V2, 0], [0, 0, 1]), "T": T,
            "outline": [list(p) for p in f.outline], "holes": holes})
        for b in f.bends:
            R = b.radius if b.radius is not None else mat.R
            th = math.radians(abs(b.angle))
            if th <= 0 or th >= math.pi:
                raise ValueError(f"bend {b.name}: angle must be in (0, 180) degrees")
            BA = th * (R + mat.K * T)
            p0, d, o = (np.array(x, float) for x in (b.p0, b.d, b.o))
            if abs(np.dot(d, o)) > 1e-9 or abs(np.linalg.norm(d) - 1) > 1e-9 \
                    or abs(np.linalg.norm(o) - 1) > 1e-9:
                raise ValueError(f"bend {b.name}: d and o must be orthonormal")
            P = st.to3d(*p0)
            D3 = d[0] * st.U + d[1] * st.V
            O3 = o[0] * st.U + o[1] * st.V
            if b.angle > 0:
                A = P + st.N * (R + T)
                w = np.cross(O3, st.N)
                xdir = -st.N
            else:
                A = P - st.N * R
                w = np.cross(st.N, O3)
                xdir = st.N
            w = w / np.linalg.norm(w)
            rot = rotation(w, th)
            Oc = A + rot @ (P - A)
            Uc, Vc, Nc = rot @ O3, D3, rot @ st.N
            base = A + D3 * (b.v0 if np.dot(w, D3) > 0 else b.v1)
            sectors.append({"base": list(map(float, base)), "axis": list(map(float, w)),
                            "xdir": list(map(float, xdir)), "r_in": R, "r_out": R + T,
                            "angle": th, "length": b.v1 - b.v0})
            P2 = st.to2d(*p0)
            d2 = d[0] * st.U2 + d[1] * st.V2
            o2 = o[0] * st.U2 + o[1] * st.V2
            strip = [P2 + d2 * b.v0, P2 + d2 * b.v1, P2 + d2 * b.v1 + o2 * BA,
                     P2 + d2 * b.v0 + o2 * BA]
            plates_flat.append({"frame": _frame([0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]),
                                "T": T, "outline": [list(map(float, p)) for p in strip],
                                "holes": []})
            mid = P2 + o2 * BA / 2
            bends.append(BendState(b, f.name, R, BA, (mid + d2 * b.v0, mid + d2 * b.v1), A, w))
            visit(FaceState(b.child, Oc, Uc, Vc, Nc, P2 + o2 * BA, o2, d2))

    visit(root)
    return Layout(part, faces, bends, plates3d, sectors, plates_flat)


def expected_volume_difference(lay: Layout) -> float:
    """Folded minus flat volume implied by the bend allowance.

    A bend sector holds theta*L*T*(R + T/2) of material; its flat strip holds
    theta*L*T*(R + K*T). The difference is theta*L*T^2*(0.5 - K) per bend.
    """
    T, K = lay.part.material.T, lay.part.material.K
    return sum(math.radians(abs(b.bend.angle)) * (b.bend.v1 - b.bend.v0) * T * T * (0.5 - K)
               for b in lay.bends)
