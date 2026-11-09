"""Parametric desktop synth case: a folded bottom shell and a sloped top panel.

The bottom shell is one sheet: a base with a front lip, a tall back wall and two
wedge-shaped side cheeks folded up from it. The front lip and back wall run the
full width and the cheeks tuck in behind them, so the corners close up. The top
panel is a second sheet that sits flush with the cheek tops; its two folded-down
lips carry PEM nuts and are screwed through the cheeks.

All lengths are in millimetres and are outer dimensions unless noted. The
material values are placeholders to be replaced with the fabricator's numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .fold import Bend, Face, Hole, Layout, Material, Part, layout


@dataclass
class CaseParams:
    W: float = 320.0  # outer width
    D: float = 160.0  # outer depth
    Hf: float = 26.0  # cheek height at the front
    Hb: float = 58.0  # cheek height at the back
    material: Material = field(default_factory=lambda: Material(
        "Aluminium 5052-H32 (placeholder)", T=1.6, R=1.6, K=0.42))
    corner_gap: float = 0.4  # cheek end to front lip / back wall inner face
    panel_clearance: float = 0.3  # top panel lip to cheek inner face
    front_lip_clearance: float = 0.5  # front lip top to panel underside
    relief_depth: float | None = None  # defaults to R + T
    relief_width: float = 2.0
    lip_depth: float = 18.0  # top panel lip, from panel top surface
    pem_edge_cl: float = 4.8  # PEM CLS-M3 centre-to-edge minimum (placeholder, check catalog)
    lip_inset: float = 8.0  # lip ends to panel front/back edges
    pcb_drop: float = 10.0  # panel underside to PCB top
    pcb_keepout: float = 9.6  # PCB thickness + tallest part below it


@dataclass
class Case:
    params: CaseParams
    shell: Layout
    panel: Layout
    slope: float  # radians
    screws: list[dict]  # screw axes in assembly coordinates
    pcb_box: dict  # PCB + component keep-out, assembly coordinates


def _lip_outline(L: float, Lf: float, ext0: float, ext1: float, rd: float):
    """Wall standing on a bend that spans v in [0, L]; the wall extends ext0
    before and ext1 after the bend, with relief notches of depth rd under the
    extensions."""
    return [(0, 0), (0, L), (rd, L), (rd, L + ext1), (Lf, L + ext1), (Lf, -ext0), (rd, -ext0),
            (rd, 0)]


def build(p: CaseParams | None = None) -> Case:
    p = p or CaseParams()
    m = p.material
    T, R = m.T, m.R
    a = R + T  # set-back of a 90° bend measured to the outer surface
    rd = p.relief_depth if p.relief_depth is not None else a
    alpha = math.atan2(p.Hb - p.Hf, p.D)
    ca, sa = math.cos(alpha), math.sin(alpha)

    # ---------------------------------------------------------------- top panel
    c = p.panel_clearance
    Wp = p.W - 2 * T - 2 * c
    # Along the slope, front edge to back edge. The back edge is square to the
    # panel, so its lower corner sits T*sin(alpha) further back than its top.
    Sp = (p.D - T - c - T * sa) / ca
    s0 = s1 = p.lip_inset
    rw = p.relief_width
    Ll = p.lip_depth - a
    ch = 3.0
    panel_outline = [(0, 0), (Wp, 0), (Wp, s0 - rw), (Wp - a, s0 - rw), (Wp - a, Sp - s1 + rw),
                     (Wp, Sp - s1 + rw), (Wp, Sp), (0, Sp), (0, Sp - s1 + rw),
                     (a, Sp - s1 + rw), (a, s0 - rw), (0, s0 - rw)]
    lip_holes_v = (s0 + 22.0, Sp - s1 - 22.0)
    uh = Ll / 2 + 0.5

    def lip(name: str) -> Face:
        return Face(name, [(0, s0), (0, Sp - s1), (Ll, Sp - s1 - ch), (Ll, s0 + ch)],
                    [Hole("circle", (uh, v), d=4.22, label="PEM CLS-M3 nut",
                          edge_cl=p.pem_edge_cl) for v in lip_holes_v])

    panel_holes: list[Hole] = []
    panel_holes.append(Hole("rrect", (42.0, 116.0), w=36.0, h=20.0, r=1.0, label="OLED window"))
    for i, v in enumerate((128.0, 120.0, 112.0, 104.0)):
        panel_holes.append(Hole("circle", (72.0, v), d=3.2, label="3 mm LED"))
    for v in (116.0, 72.0):
        for u in (110.0, 155.0, 200.0, 245.0, 290.0):
            panel_holes.append(Hole("circle", (u, v), d=7.2, label="9 mm pot (M7 bushing)"))
    for u in (30.0, 62.0):
        panel_holes.append(Hole("circle", (u, 72.0), d=6.4, label="toggle switch (M6)"))
    for i in range(8):
        panel_holes.append(Hole("circle", (40.0 + i * 34.0, 30.0), d=12.2, label="12 mm button"))

    panel_root = Face("panel", panel_outline, panel_holes, [
        Bend("panel_right", (Wp - a, 0), (0, 1), (1, 0), s0, Sp - s1, 90.0, lip("lip_right")),
        Bend("panel_left", (a, 0), (0, 1), (-1, 0), s0, Sp - s1, 90.0, lip("lip_left")),
    ])
    place = np.array([[1, 0, 0, T + c], [0, ca, -sa, 0.0], [0, sa, ca, p.Hf]], float)
    panel = Part("top_panel", m, panel_root, N=(0.0, 0.0, -1.0), placement=place)
    panel_lay = layout(panel)

    # Screw axes: through each lip hole, perpendicular to the lip.
    screws = []
    for side in ("lip_right", "lip_left"):
        st = panel_lay.faces[side]
        for h in st.face.holes:
            centre = panel_lay.placed(st.to3d(*h.c))
            inward = panel_lay.placed_dir(st.N)  # into the lip material, toward the case centre
            screws.append({"side": side, "centre": centre, "inward": inward})

    # ------------------------------------------------------------- bottom shell
    g = p.corner_gap
    L_x = p.W - 2 * a
    L_y = p.D - 2 * a
    h_front = p.Hf - T / ca - p.front_lip_clearance

    base_holes = [Hole("circle", (x, y), d=3.4, label="foot screw M3")
                  for x in (12.0, p.W - 12.0) for y in (12.0, p.D - 12.0)]
    for i in range(6):
        base_holes.append(Hole("rrect", (50.0 + i * 44.0, p.D - 30.0), w=36.0, h=5.0, r=2.5,
                               label="vent slot"))

    back_spec = [("circle", 30.0, dict(d=8.2), "DC barrel jack"),
                 ("circle", 52.0, dict(d=6.4), "power switch (M6)"),
                 ("rrect", 75.0, dict(w=9.4, h=3.8, r=1.6), "USB-C"),
                 ("circle", 105.0, dict(d=15.6), "MIDI DIN in"),
                 ("circle", 135.0, dict(d=15.6), "MIDI DIN out")]
    back_spec += [("circle", x, dict(d=6.2), "3.5 mm jack") for x in
                  (175.0, 195.0, 215.0, 235.0, 255.0, 275.0)]
    z_jacks = 26.0
    back_holes = [Hole(kind, (z_jacks - a, x - a), label=label, **kw)
                  for kind, x, kw, label in back_spec]
    # Back-wall child coordinates are (u, v) = (z - a, x - a); rounded
    # rectangles are given in (u, v) too, so rotate the USB-C slot to lie along x.
    for h in back_holes:
        if h.kind == "rrect":
            h.rot = 90.0

    def cheek(name: str, side: str) -> Face:
        vs, ve = T + g - a, p.D - T - g - a
        top = lambda v: p.Hf + (v + a) * math.tan(alpha) - a
        outline = [(0, 0), (0, L_y), (rd, L_y), (rd, ve), (top(ve), ve), (top(vs), vs), (rd, vs),
                   (rd, 0)]
        holes = []
        for s in screws:
            if s["side"] != side:
                continue
            y, z = s["centre"][1], s["centre"][2]
            holes.append(Hole("circle", (z - a, y - a), d=3.4, label="M3 clearance"))
        return Face(name, outline, holes)

    base = Face("base", [(a, a), (p.W - a, a), (p.W - a, p.D - a), (a, p.D - a)], base_holes, [
        Bend("front", (a, a), (1, 0), (0, -1), 0, L_x, 90.0,
             Face("front_lip", _lip_outline(L_x, h_front - a, a, a, rd))),
        Bend("back", (a, p.D - a), (1, 0), (0, 1), 0, L_x, 90.0,
             Face("back_wall", _lip_outline(L_x, p.Hb - a, a, a, rd), back_holes)),
        Bend("right", (p.W - a, a), (0, 1), (1, 0), 0, L_y, 90.0, cheek("cheek_right", "lip_right")),
        Bend("left", (a, a), (0, 1), (-1, 0), 0, L_y, 90.0, cheek("cheek_left", "lip_left")),
    ])
    shell = Part("bottom_shell", m, base)
    shell_lay = layout(shell)

    # PCB plus bottom-side components, hanging below the panel, parallel to it.
    st = panel_lay.faces["panel"]
    u0, u1, v0, v1 = 8.0, Wp - 8.0, 8.0, Sp - 8.0
    z_top = -T - p.pcb_drop  # panel part coordinates (top surface at z = 0)
    origin = panel_lay.placed(np.array([u0, v0, z_top - p.pcb_keepout]))
    xdir = panel_lay.placed_dir(np.array([1.0, 0, 0]))
    zdir = panel_lay.placed_dir(np.array([0, 0, 1.0]))
    pcb_box = {"o": origin, "xdir": xdir, "zdir": zdir, "dx": u1 - u0, "dy": v1 - v0,
               "dz": p.pcb_keepout}

    return Case(p, shell_lay, panel_lay, alpha, screws, pcb_box)
