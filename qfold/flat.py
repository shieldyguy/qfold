"""Flat pattern geometry in the outside-face view, and DXF export.

The kernel returns the flat pattern's boundary (exact lines, arcs and circles)
in the development plane. Here it is turned to show the *outside* face, the
side a person sees on the finished part, and shifted so its lower-left corner
sits at the origin. Bends that fold toward the material side then fold away
from the viewer, marked DN.
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .fold import Layout

_EZDXF = os.environ.get("QFOLD_EZDXF_SRC")
if _EZDXF and _EZDXF not in sys.path:
    sys.path.insert(0, _EZDXF)


@dataclass
class FlatBend:
    name: str
    a: np.ndarray
    b: np.ndarray
    direction: str  # "DN" (away from viewer) or "UP"
    angle: float
    radius: float
    allowance: float


@dataclass
class FlatPattern:
    name: str
    lines: list[tuple[np.ndarray, np.ndarray]] = field(default_factory=list)
    arcs: list[tuple[np.ndarray, float, float, float]] = field(default_factory=list)  # c, r, a0, a1
    circles: list[tuple[np.ndarray, float]] = field(default_factory=list)
    polys: list[np.ndarray] = field(default_factory=list)
    bends: list[FlatBend] = field(default_factory=list)
    size: tuple[float, float] = (0.0, 0.0)
    area: float = 0.0


def flat_pattern(lay: Layout, outline: dict) -> FlatPattern:
    U, V, N = (np.array(x, float) for x in (lay.part.U, lay.part.V, lay.part.N))
    right_handed = np.dot(np.cross(U, V), N) > 0

    def tp(p):  # development plane -> outside view
        x, y = float(p[0]), float(p[1])
        return np.array([-x, y]) if right_handed else np.array([x, -y])

    def ta(a0, a1):  # CCW arc angles under that reflection
        return (180.0 - a1, 180.0 - a0) if right_handed else (-a1, -a0)

    fp = FlatPattern(lay.part.name, area=outline["area"])
    for e in outline["edges"]:
        if e["type"] == "line":
            fp.lines.append((tp(e["a"]), tp(e["b"])))
        elif e["type"] == "circle":
            fp.circles.append((tp(e["c"]), e["r"]))
        elif e["type"] == "arc":
            a0, a1 = ta(e["a0"], e["a1"])
            fp.arcs.append((tp(e["c"]), e["r"], a0, a1))
        else:
            fp.polys.append(np.array([tp(p) for p in e["pts"]]))
    for bs in lay.bends:
        b = bs.bend
        fp.bends.append(FlatBend(b.name, tp(bs.line2d[0]), tp(bs.line2d[1]),
                                 "DN" if b.angle > 0 else "UP", abs(b.angle), bs.R, bs.allowance))

    # Bounding box from all boundary geometry (arcs sampled densely).
    pts = [p for ln in fp.lines for p in ln]
    for c, r in fp.circles:
        pts += [c - r, c + r]
    for c, r, a0, a1 in fp.arcs:
        span = (a1 - a0) % 360.0 or 360.0
        for t in np.linspace(0, span, 37):
            ang = math.radians(a0 + t)
            pts.append(c + r * np.array([math.cos(ang), math.sin(ang)]))
    for p in fp.polys:
        pts += list(p)
    allp = np.array(pts)
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    shift = -lo
    fp.lines = [(a + shift, b + shift) for a, b in fp.lines]
    fp.arcs = [(c + shift, r, a0, a1) for c, r, a0, a1 in fp.arcs]
    fp.circles = [(c + shift, r) for c, r in fp.circles]
    fp.polys = [p + shift for p in fp.polys]
    for b in fp.bends:
        b.a, b.b = b.a + shift, b.b + shift
    fp.size = (float(hi[0] - lo[0]), float(hi[1] - lo[1]))
    return fp


def write_dxf(fp: FlatPattern, path: Path, material: str, thickness: float) -> None:
    """R2010 DXF: cut geometry on CUT, bend centre lines on BEND, notes on NOTES."""
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    doc.units = ezdxf.units.MM
    doc.layers.add("CUT", color=7)
    doc.layers.add("BEND", color=1, linetype="DASHDOT")
    doc.layers.add("NOTES", color=3)
    msp = doc.modelspace()
    for a, b in fp.lines:
        msp.add_line(tuple(a), tuple(b), dxfattribs={"layer": "CUT"})
    for c, r in fp.circles:
        msp.add_circle(tuple(c), r, dxfattribs={"layer": "CUT"})
    for c, r, a0, a1 in fp.arcs:
        msp.add_arc(tuple(c), r, a0, a1, dxfattribs={"layer": "CUT"})
    for p in fp.polys:
        msp.add_lwpolyline([tuple(q) for q in p], dxfattribs={"layer": "CUT"})
    for b in fp.bends:
        msp.add_line(tuple(b.a), tuple(b.b), dxfattribs={"layer": "BEND"})
        mid = (b.a + b.b) / 2
        d = b.b - b.a
        ang = math.degrees(math.atan2(d[1], d[0]))
        if ang > 90 or ang <= -90:
            ang -= 180 if ang > 90 else -180
        msp.add_text(f"{b.name} {b.direction} {b.angle:g}° R{b.radius:g}", height=2.5,
                     dxfattribs={"layer": "NOTES", "rotation": ang}).set_placement(
            tuple(mid + np.array([0.0, 1.0])))
    msp.add_text(f"{fp.name}  {material}  t={thickness:g} mm  outside face shown; "
                 f"DN = bend away from viewer", height=3.0,
                 dxfattribs={"layer": "NOTES"}).set_placement((0, -8))
    doc.saveas(path)
