"""Fold engine for sheet metal bodies: flat faces joined by flanges.

A sheet metal body is a root face plus flanges hanging off its edges. Each
flange is a bend plus the wall face it carries, recursively. (Fusion's sheet
metal Flange: a bend and a wall.) From that one description the engine
produces both states of the body:

* folded: planar plates (face outline + holes, extruded by thickness) and bend
  sectors (annular sectors swept about each bend axis), in 3D;
* flat: the same plates laid out in the plane, joined by bend-allowance strips,
  plus the bend centre lines for the flat-pattern DXF.

Conventions
-----------
Every face has a frame (O, U, V, N). Face-local 2D coordinates (u, v) map to
O + u*U + v*V on the *reference surface*; material occupies s in [0, T] along N.
A flange is attached to a straight edge of its parent face, given in parent
coordinates by a start point p0, a unit direction d along the edge and a unit
in-plane direction o pointing out of the parent. Its wall face uses u along o
(away from the bend) and v along d, with its origin at the image of p0, so
wall coordinates (0, v) sit exactly where the bend ends.

A positive bend angle folds toward the material side (+N). Then the reference
surface is the outside of the bend and outer dimensions are reference
dimensions, which is how the synth case is specified. Negative angles fold the
other way. Bend allowance is the arc length of the neutral surface,
theta * (R + K*T), with R the inside radius. Thickness, radius and K come from
the body's SheetMetalRule (Fusion: Sheet Metal Rule).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class SheetMetalRule:
    """Fusion's Sheet Metal Rule: material, thickness, bend radius, K-factor."""
    material: str
    thickness: float  # mm
    bend_radius: float  # default inside bend radius, mm
    k_factor: float  # neutral axis position from the inside surface, 0..1

    @property
    def T(self) -> float:
        return self.thickness

    @property
    def R(self) -> float:
        return self.bend_radius

    @property
    def K(self) -> float:
        return self.k_factor


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


def outline_points(outline, step_deg: float = 6.0) -> np.ndarray:
    """An outline's vertices as plain (u, v) points, with arc edges sampled.

    An outline vertex is (u, v) or (u, v, bulge). A bulge makes the edge that
    starts there a circular arc whose middle sits bulge * chord / 2 to the
    right of the chord, so |bulge| = tan(angle / 4), as in a DXF polyline.
    """
    pts = []
    n = len(outline)
    for k, p in enumerate(outline):
        a = np.array(p[:2], float)
        pts.append(a)
        bulge = p[2] if len(p) > 2 else 0.0
        if abs(bulge) < 1e-12:
            continue
        b = np.array(outline[(k + 1) % n][:2], float)
        d = b - a
        c = float(np.linalg.norm(d))
        theta = 4 * math.atan(abs(bulge))  # the arc's angle
        r = c / (2 * math.sin(theta / 2))
        right = np.array([d[1], -d[0]]) / c
        sgn = 1.0 if bulge > 0 else -1.0
        centre = (a + b) / 2 - sgn * right * (r - abs(bulge) * c / 2)
        a0 = math.atan2(*(a - centre)[::-1])
        steps = max(2, int(math.ceil(math.degrees(theta) / step_deg)))
        for i in range(1, steps):
            t = a0 + sgn * theta * i / steps  # bulging right of the chord turns left (CCW)
            pts.append(centre + r * np.array([math.cos(t), math.sin(t)]))
    return np.array(pts)


def arc_bulge(p, q, centre) -> float:
    """The bulge of the (minor) arc from p to q about centre: see outline_points."""
    p, q, c = (np.array(x, float) for x in (p, q, centre))
    r = np.linalg.norm(p - c)
    mid = (p + q) / 2 - c
    m = c + r * mid / np.linalg.norm(mid)
    d = q - p
    return float(-2 * (d[0] * (m - p)[1] - d[1] * (m - p)[0]) / (d @ d))


def outline_area(outline) -> float:
    """Area enclosed by an outline, arcs included (positive if counter-clockwise)."""
    area, n = 0.0, len(outline)
    for k, p in enumerate(outline):
        q = outline[(k + 1) % n]
        area += (p[0] * q[1] - q[0] * p[1]) / 2
        bulge = p[2] if len(p) > 2 else 0.0
        if abs(bulge) > 1e-12:  # the segment between chord and arc
            c = math.hypot(q[0] - p[0], q[1] - p[1])
            theta = 4 * math.atan(abs(bulge))
            r = c / (2 * math.sin(theta / 2))
            # Bulging right of the chord adds area on a counter-clockwise outline.
            area += math.copysign(r * r * (theta - math.sin(theta)) / 2, bulge)
    return area


@dataclass
class Face:
    name: str
    outline: list[tuple]  # (u, v) or (u, v, bulge): see outline_points
    holes: list[Hole] = field(default_factory=list)
    flanges: list["Flange"] = field(default_factory=list)


@dataclass
class Flange:
    """A bend along a parent edge plus the wall it carries (Fusion: Flange)."""
    name: str
    p0: tuple[float, float]
    d: tuple[float, float]
    o: tuple[float, float]
    v0: float
    v1: float
    angle: float  # degrees; + folds toward the material side
    wall: Face
    radius: float | None = None  # inside radius override
    # The bend's own outline when its ends aren't square, in (s, v): s across
    # the bend from the parent's tangent line (0) to the wall's (the bend
    # allowance), v along the edge. Vertices may carry bulges. Default: the
    # rectangle 0..allowance by v0..v1. v0 and v1 must still span it.
    strip: list | None = None


@dataclass
class SheetMetalBody:
    name: str
    rule: SheetMetalRule
    root: Face
    # Root face frame, in the owning component's coordinates.
    origin: tuple = (0.0, 0.0, 0.0)
    U: tuple = (1.0, 0.0, 0.0)
    V: tuple = (0.0, 1.0, 0.0)
    N: tuple = (0.0, 0.0, 1.0)
    tear_corners: bool = False  # bends meant to meet at face corners with no relief cut


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
    bend: Flange
    parent: str
    R: float
    allowance: float
    line2d: tuple[np.ndarray, np.ndarray]  # centre line of the bend strip, flat pattern
    axis_point: np.ndarray  # a point on the bend axis (3D, part coords)
    axis: np.ndarray
    area: float = 0.0  # of the bend's flat strip


@dataclass
class SheetLayout:
    """Both states of a sheet metal body, plus every face's frame."""
    body: SheetMetalBody
    faces: dict[str, FaceState]
    bends: list[BendState]
    plates3d: list[dict]
    sectors: list[dict]
    plates_flat: list[dict]


def _frame(O, U, V, N) -> dict:
    return {"o": list(map(float, O)), "u": list(map(float, U)), "v": list(map(float, V)),
            "n": list(map(float, N))}


def layout(body: SheetMetalBody) -> SheetLayout:
    mat = body.rule
    T = mat.T
    faces: dict[str, FaceState] = {}
    bends: list[BendState] = []
    plates3d, sectors, plates_flat = [], [], []

    O = np.array(body.origin, float)
    U, V, N = (np.array(x, float) for x in (body.U, body.V, body.N))
    # The flat pattern must be a rigid (orientation-preserving) development of
    # the reference surface, so match the root frame's handedness.
    hand = 1.0 if np.dot(np.cross(U, V), N) > 0 else -1.0
    root = FaceState(body.root, O, U, V, N, np.zeros(2), np.array([1.0, 0.0]),
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
        for b in f.flanges:
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
            along = np.dot(w, D3) > 0  # does the axis run the way v does?
            base = A + D3 * (b.v0 if along else b.v1)
            sector = {"base": list(map(float, base)), "axis": list(map(float, w)),
                      "xdir": list(map(float, xdir)), "r_in": R, "r_out": R + T,
                      "angle": th, "length": b.v1 - b.v0}
            if b.strip:
                # The same outline on the inside cylinder: angle for s, and
                # distance along the axis for v, counter-clockwise.
                edges = []
                for k, q in enumerate(b.strip):
                    nxt = b.strip[(k + 1) % len(b.strip)]
                    pts = (outline_points([q, nxt[:2]], step_deg=2.0)[:-1].tolist() + [list(nxt[:2])]
                           if len(q) > 2 and abs(q[2]) > 1e-12 else [list(q[:2]), list(nxt[:2])])
                    edges.append([[s_ / BA * th, (v_ - b.v0) if along else (b.v1 - v_)]
                                  for s_, v_ in pts])
                flat = [pt for e in edges for pt in e[:-1]]
                if sum(a[0] * c[1] - c[0] * a[1] for a, c in zip(flat, flat[1:] + flat[:1])) < 0:
                    edges = [e[::-1] for e in edges[::-1]]
                sector["profile"] = edges
            sectors.append(sector)
            P2 = st.to2d(*p0)
            d2 = d[0] * st.U2 + d[1] * st.V2
            o2 = o[0] * st.U2 + o[1] * st.V2
            if b.strip:
                flip = -1.0 if o2[0] * d2[1] - o2[1] * d2[0] < 0 else 1.0  # a mirrored map
                strip = [[*map(float, P2 + o2 * q[0] + d2 * q[1])]
                         + ([flip * q[2]] if len(q) > 2 and q[2] else []) for q in b.strip]
                area = abs(outline_area(b.strip))
            else:
                strip = [list(map(float, p)) for p in (
                    P2 + d2 * b.v0, P2 + d2 * b.v1, P2 + d2 * b.v1 + o2 * BA,
                    P2 + d2 * b.v0 + o2 * BA)]
                area = BA * (b.v1 - b.v0)
            plates_flat.append({"frame": _frame([0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]),
                                "T": T, "outline": strip, "holes": []})
            mid = P2 + o2 * BA / 2
            bends.append(BendState(b, f.name, R, BA, (mid + d2 * b.v0, mid + d2 * b.v1), A, w,
                                   area))
            visit(FaceState(b.wall, Oc, Uc, Vc, Nc, P2 + o2 * BA, o2, d2))

    visit(root)
    return SheetLayout(body, faces, bends, plates3d, sectors, plates_flat)


def expected_volume_difference(lay: SheetLayout) -> float:
    """Folded minus flat volume implied by the bend allowance.

    A bend sector holds theta*L*T*(R + T/2) of material; its flat strip holds
    theta*L*T*(R + K*T). The difference is theta*L*T^2*(0.5 - K) per bend,
    with L the strip's area over its width when its ends aren't square.
    """
    T, K = lay.body.rule.T, lay.body.rule.K
    return sum(math.radians(abs(b.bend.angle)) * (b.area / b.allowance) * T * T * (0.5 - K)
               for b in lay.bends)


def hole_at(body: SheetMetalBody, face: str, xyz, hole: Hole) -> Hole:
    """Add `hole` to the named face, centred where the point xyz lies on it.

    xyz is in the body's own folded coordinates, so a jack on the back wall is
    placed by where it is on the finished part ("x = 90, z = 15"), not by the
    wall's local (u, v), which is easy to mirror. The point may be on the
    face's outside or inside surface, or anywhere between; a point off the
    face's plane is an error.
    """
    faces = layout(body).faces
    if face not in faces:
        raise KeyError(f"{body.name} has no face {face!r}; it has: {', '.join(faces)}")
    st = faces[face]
    rel = np.asarray(xyz, float) - st.O
    s = float(rel @ st.N)
    T = body.rule.T
    if not -1e-6 <= s <= T + 1e-6:
        raise ValueError(f"{body.name}/{face}: point {tuple(xyz)} is {s:.3f} mm from the face's "
                         f"outside surface (material spans 0..{T:g}); it isn't on this face")
    hole.c = (float(rel @ st.U), float(rel @ st.V))
    st.face.holes.append(hole)
    return hole
