"""Design-for-manufacture checks on sheet metal layouts and kernel results.

Every rule threshold here is a placeholder rule of thumb. Replace them with the
numbers your fabricator publishes for the material, thickness and tooling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .fold import SheetLayout


@dataclass
class Rules:
    hole_to_bend: float | None = None  # default 2T + R of that bend, to the bend tangent line
    hole_to_edge: float | None = None  # default T
    hole_web: float | None = None  # default T, between neighbouring holes
    min_hole: float | None = None  # default T
    min_flange: float | None = None  # default 4T, flat length past the bend
    bend_end_gap: float | None = None  # default T: bends on one face must end this far apart
    max_sheet: tuple[float, float] = (1200.0, 600.0)
    min_clearance: float = 0.2  # between parts (or faces of one part) that should not touch
    pcb_clearance: float = 2.0
    seated: float = 0.01  # parts that should rest on each other: at most this far apart

    def resolve(self, T: float, R: float) -> "Rules":
        return Rules(
            hole_to_bend=self.hole_to_bend if self.hole_to_bend is not None else 2 * T + R,
            hole_to_edge=self.hole_to_edge if self.hole_to_edge is not None else T,
            hole_web=self.hole_web if self.hole_web is not None else T,
            min_hole=self.min_hole if self.min_hole is not None else T,
            min_flange=self.min_flange if self.min_flange is not None else 4 * T,
            bend_end_gap=self.bend_end_gap if self.bend_end_gap is not None else T,
            max_sheet=self.max_sheet, min_clearance=self.min_clearance,
            pcb_clearance=self.pcb_clearance, seated=self.seated)


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


def _inside(pts: np.ndarray, poly) -> np.ndarray:
    """Even-odd point-in-polygon test for each point."""
    p = np.array(poly, float)
    x, y = pts[:, 0], pts[:, 1]
    inside = np.zeros(len(pts), bool)
    for (x0, y0), (x1, y1) in zip(p, np.roll(p, -1, axis=0)):
        crosses = (y0 > y) != (y1 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xs = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
        inside ^= crosses & (x < xs)
    return inside


def _extent_u(outline, v: float) -> float | None:
    """Largest u where the line v = const crosses the outline (None if it misses)."""
    best = None
    for a, b in _poly_segments(outline):
        if (a[1] - v) * (b[1] - v) <= 0 and abs(b[1] - a[1]) > 1e-12:
            t = (v - a[1]) / (b[1] - a[1])
            u = a[0] + t * (b[0] - a[0])
            best = u if best is None else max(best, u)
    return best


def check_layout(lay: SheetLayout, rules: Rules, rep: Report) -> None:
    T, R = lay.body.rule.T, lay.body.rule.R
    r = rules.resolve(T, R)
    part = lay.body.name

    # Bend tangent lines per face, in that face's coordinates, with the
    # hole-to-bend minimum for that bend's own radius.
    bend_lines: dict[str, list[tuple[str, np.ndarray, np.ndarray, float]]] = {
        n: [] for n in lay.faces}
    for bs in lay.bends:
        b = bs.bend
        p0, d = np.array(b.p0), np.array(b.d)
        need = rules.hole_to_bend if rules.hole_to_bend is not None else 2 * T + bs.R
        bend_lines[bs.parent].append((b.name, p0 + d * b.v0, p0 + d * b.v1, need))
        bend_lines[b.wall.name].append((b.name, np.array([0.0, b.v0]), np.array([0.0, b.v1]), need))

    worst_bend = (math.inf, 0.0, 0.0, "")  # margin, distance, needed, where
    worst = {"edge": (math.inf, ""), "web": (math.inf, "")}
    small, outside, hw_bad, hw_n = [], [], [], 0
    for name, st in lay.faces.items():
        holes = st.face.holes
        bounds = [h.boundary() for h in holes]
        for h, pts in zip(holes, bounds):
            size = h.d if h.kind == "circle" else min(h.w, h.h)
            if size < r.min_hole:
                small.append(f"{name}:{h.label} {size:g}")
            if not _inside(pts, st.face.outline).all():
                outside.append(f"{name}:{h.tag or h.label}")
            for bname, a, b, need in bend_lines[name]:
                dist = _seg_dist(pts, a, b)
                if dist - need < worst_bend[0]:
                    worst_bend = (dist - need, dist, need, f"{name} {h.label} to bend {bname}")
            for a, b in _poly_segments(st.face.outline):
                dist = _seg_dist(pts, a, b)
                if dist < worst["edge"][0]:
                    worst["edge"] = (dist, f"{name} {h.label}")
            if h.edge_cl > 0:
                hw_n += 1
                c = np.array([h.c], float)
                segs = _poly_segments(st.face.outline) + [(a, b) for _, a, b, _ in bend_lines[name]]
                dc = min(_seg_dist(c, a, b) for a, b in segs)
                if dc < h.edge_cl:
                    hw_bad.append(f"{name} {h.label} {dc:.2f} < {h.edge_cl:g}")
        for i in range(len(holes)):
            for j in range(i + 1, len(holes)):
                dist = float(np.min(np.linalg.norm(bounds[i][:, None] - bounds[j][None], axis=2)))
                if dist < worst["web"][0]:
                    worst["web"] = (dist, f"{name} {holes[i].label} / {holes[j].label}")

    if math.isfinite(worst_bend[0]):
        _, dist, need, where = worst_bend
        rep.add(f"{part}: hole to bend", worst_bend[0] >= 0,
                f"tightest {dist:.2f} mm ({where}), need ≥ {need:.2f}", dist, need)
    if math.isfinite(worst["edge"][0]):
        dist, where = worst["edge"]
        rep.add(f"{part}: hole to edge", dist >= r.hole_to_edge,
                f"closest {dist:.2f} mm ({where}), need ≥ {r.hole_to_edge:.2f}", dist,
                r.hole_to_edge)
    if math.isfinite(worst["web"][0]):
        dist, where = worst["web"]
        rep.add(f"{part}: web between holes", dist >= r.hole_web,
                f"narrowest {dist:.2f} mm ({where}), need ≥ {r.hole_web:.2f}", dist, r.hole_web)
    rep.add(f"{part}: holes inside their face", not outside,
            "every hole lies within its face" if not outside
            else "crosses the face edge: " + ", ".join(outside))
    rep.add(f"{part}: minimum hole size", not small,
            "all holes ≥ %.2f mm" % r.min_hole if not small else "too small: " + ", ".join(small))
    if hw_n:
        rep.add(f"{part}: hardware edge distance", not hw_bad,
                f"{hw_n} hardware holes meet their centre-to-edge minimum" if not hw_bad
                else "; ".join(hw_bad))

    # Bends that leave one face must not end on, or close to, each other:
    # two bends meeting at a sharp corner tear or wrinkle without a relief.
    tight, closest = [], math.inf
    for name in lay.faces:
        own = [(bn, a, b) for bn, a, b, _ in bend_lines[name]
               if any(bs.parent == name and bs.bend.name == bn for bs in lay.bends)]
        for i in range(len(own)):
            for j in range(i + 1, len(own)):
                (n1, a1, b1), (n2, a2, b2) = own[i], own[j]
                ends1, ends2 = np.array([a1, b1]), np.array([a2, b2])
                dist = min(_seg_dist(ends1, a2, b2), _seg_dist(ends2, a1, b1))
                closest = min(closest, dist)
                if dist < r.bend_end_gap:
                    tight.append(f"{n1}/{n2} {dist:.2f} mm")
    if math.isfinite(closest):
        rep.add(f"{part}: bend corner relief", not tight,
                f"bends end ≥ {closest:.2f} mm apart, need ≥ {r.bend_end_gap:.2f}" if not tight
                else "bends meet without relief: " + ", ".join(tight), closest, r.bend_end_gap)

    # Flange length across the bend, ignoring end chamfers and reliefs (outer 5%).
    shortest, which, missed = math.inf, "", []
    for bs in lay.bends:
        b = bs.bend
        span = b.v1 - b.v0
        for v in np.linspace(b.v0 + 0.05 * span, b.v1 - 0.05 * span, 25):
            e = _extent_u(b.wall.outline, float(v))
            if e is None:
                missed.append(b.name)
                continue
            if e < shortest:
                shortest, which = e, b.name
    if not lay.bends:
        return
    if missed:
        rep.add(f"{part}: flange length", False,
                "wall outline doesn't cover its bend: " + ", ".join(sorted(set(missed))))
    else:
        rep.add(f"{part}: flange length", shortest >= r.min_flange,
                f"shortest flat flange {shortest:.2f} mm (bend {which}), need ≥ {r.min_flange:.2f}",
                shortest, r.min_flange)


def check_flat_size(name: str, size: tuple[float, float], rules: Rules, rep: Report) -> None:
    a, b = sorted(size, reverse=True)
    lim = sorted(rules.max_sheet, reverse=True)
    rep.add(f"{name}: fits sheet", a <= lim[0] and b <= lim[1],
            f"flat {size[0]:.1f} × {size[1]:.1f} mm, sheet limit {lim[0]:g} × {lim[1]:g}")
