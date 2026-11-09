"""Design-for-manufacture checks on fold layouts and kernel results.

Every rule threshold here is a placeholder rule of thumb. Replace them with the
numbers your fabricator publishes for the material, thickness and tooling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .fold import Layout


@dataclass
class Rules:
    hole_to_bend: float | None = None  # default 2T + R, measured to the bend tangent line
    hole_to_edge: float | None = None  # default T
    hole_web: float | None = None  # default T, between neighbouring holes
    min_hole: float | None = None  # default T
    min_flange: float | None = None  # default 4T, flat length past the bend
    max_sheet: tuple[float, float] = (1200.0, 600.0)
    min_clearance: float = 0.2  # between parts that should not touch
    pcb_clearance: float = 2.0

    def resolve(self, T: float, R: float) -> "Rules":
        return Rules(
            hole_to_bend=self.hole_to_bend if self.hole_to_bend is not None else 2 * T + R,
            hole_to_edge=self.hole_to_edge if self.hole_to_edge is not None else T,
            hole_web=self.hole_web if self.hole_web is not None else T,
            min_hole=self.min_hole if self.min_hole is not None else T,
            min_flange=self.min_flange if self.min_flange is not None else 4 * T,
            max_sheet=self.max_sheet, min_clearance=self.min_clearance,
            pcb_clearance=self.pcb_clearance)


@dataclass
class Check:
    name: str
    status: str  # "pass" | "warn" | "fail"
    detail: str
    value: float | None = None
    limit: float | None = None


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name, ok, detail, value=None, limit=None, warn_only=False):
        status = "pass" if ok else ("warn" if warn_only else "fail")
        self.checks.append(Check(name, status, detail, value, limit))

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    def summary(self) -> str:
        n = {s: sum(c.status == s for c in self.checks) for s in ("pass", "warn", "fail")}
        return f"{n['pass']} pass, {n['warn']} warn, {n['fail']} fail"


def _seg_dist(pts: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    t = np.clip(((pts - a) @ ab) / max(ab @ ab, 1e-12), 0, 1)
    proj = a + t[:, None] * ab
    return float(np.min(np.linalg.norm(pts - proj, axis=1)))


def _poly_segments(poly):
    p = np.array(poly, float)
    return [(p[i], p[(i + 1) % len(p)]) for i in range(len(p))]


def _extent_u(outline, v: float) -> float:
    """Largest u where the line v = const crosses the outline."""
    best = -math.inf
    for a, b in _poly_segments(outline):
        if (a[1] - v) * (b[1] - v) <= 0 and abs(b[1] - a[1]) > 1e-12:
            t = (v - a[1]) / (b[1] - a[1])
            best = max(best, a[0] + t * (b[0] - a[0]))
    return best


def check_layout(lay: Layout, rules: Rules, rep: Report) -> None:
    T, R = lay.part.material.T, lay.part.material.R
    r = rules.resolve(T, R)
    part = lay.part.name

    # Bend lines per face, in that face's coordinates.
    bend_lines: dict[str, list[tuple[str, np.ndarray, np.ndarray]]] = {n: [] for n in lay.faces}
    for bs in lay.bends:
        b = bs.bend
        p0, d = np.array(b.p0), np.array(b.d)
        bend_lines[bs.parent].append((b.name, p0 + d * b.v0, p0 + d * b.v1))
        bend_lines[b.child.name].append((b.name, np.array([0.0, b.v0]), np.array([0.0, b.v1])))

    worst = {"bend": math.inf, "edge": math.inf, "web": math.inf}
    where = {"bend": "", "edge": "", "web": ""}
    small, hw_bad, hw_n = [], [], 0
    for name, st in lay.faces.items():
        holes = st.face.holes
        bounds = [h.boundary() for h in holes]
        for h, pts in zip(holes, bounds):
            if h.edge_cl > 0:
                hw_n += 1
                c = np.array([h.c], float)
                segs = _poly_segments(st.face.outline) + [(a, b) for _, a, b in bend_lines[name]]
                dc = min(_seg_dist(c, a, b) for a, b in segs)
                if dc < h.edge_cl:
                    hw_bad.append(f"{name} {h.label} {dc:.2f} < {h.edge_cl:g}")
            size = h.d if h.kind == "circle" else min(h.w, h.h)
            if size < r.min_hole:
                small.append(f"{name}:{h.label} {size:g}")
            for bname, a, b in bend_lines[name]:
                dist = _seg_dist(pts, a, b)
                if dist < worst["bend"]:
                    worst["bend"], where["bend"] = dist, f"{name} {h.label} to bend {bname}"
            for a, b in _poly_segments(st.face.outline):
                dist = _seg_dist(pts, a, b)
                if dist < worst["edge"]:
                    worst["edge"], where["edge"] = dist, f"{name} {h.label}"
        for i in range(len(holes)):
            for j in range(i + 1, len(holes)):
                dist = float(np.min(np.linalg.norm(bounds[i][:, None] - bounds[j][None], axis=2)))
                if dist < worst["web"]:
                    worst["web"], where["web"] = dist, f"{name} {holes[i].label} / {holes[j].label}"

    if math.isfinite(worst["bend"]):
        rep.add(f"{part}: hole to bend", worst["bend"] >= r.hole_to_bend,
                f"closest {worst['bend']:.2f} mm ({where['bend']}), need ≥ {r.hole_to_bend:.2f}",
                worst["bend"], r.hole_to_bend)
    if math.isfinite(worst["edge"]):
        rep.add(f"{part}: hole to edge", worst["edge"] >= r.hole_to_edge,
                f"closest {worst['edge']:.2f} mm ({where['edge']}), need ≥ {r.hole_to_edge:.2f}",
                worst["edge"], r.hole_to_edge)
    if math.isfinite(worst["web"]):
        rep.add(f"{part}: web between holes", worst["web"] >= r.hole_web,
                f"narrowest {worst['web']:.2f} mm ({where['web']}), need ≥ {r.hole_web:.2f}",
                worst["web"], r.hole_web)
    rep.add(f"{part}: minimum hole size", not small,
            "all holes ≥ %.2f mm" % r.min_hole if not small else "too small: " + ", ".join(small))
    if hw_n:
        rep.add(f"{part}: hardware edge distance", not hw_bad,
                f"{hw_n} hardware holes meet their centre-to-edge minimum" if not hw_bad
                else "; ".join(hw_bad))

    # Flange length across the bend, ignoring end chamfers and reliefs (outer 5%).
    shortest, which = math.inf, ""
    for bs in lay.bends:
        b = bs.bend
        span = b.v1 - b.v0
        for v in np.linspace(b.v0 + 0.05 * span, b.v1 - 0.05 * span, 25):
            e = _extent_u(b.child.outline, float(v))
            if e < shortest:
                shortest, which = e, b.name
    rep.add(f"{part}: flange length", shortest >= r.min_flange,
            f"shortest flat flange {shortest:.2f} mm (bend {which}), need ≥ {r.min_flange:.2f}",
            shortest, r.min_flange)


def check_flat_size(name: str, size: tuple[float, float], rules: Rules, rep: Report) -> None:
    a, b = sorted(size, reverse=True)
    lim = sorted(rules.max_sheet, reverse=True)
    rep.add(f"{name}: fits sheet", a <= lim[0] and b <= lim[1],
            f"flat {size[0]:.1f} × {size[1]:.1f} mm, sheet limit {lim[0]:g} × {lim[1]:g}")
