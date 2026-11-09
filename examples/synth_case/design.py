"""Parametric desktop synth case, as a Design.

Two sheet metal components:

* bottom_shell: a base with a front lip, a tall rear wall and two wedge-shaped
  cheeks folded up from it. The front lip and rear wall run the full width, and
  the cheeks tuck in behind them so the corners close up.
* top_panel: sits flush with the cheek tops. Its two folded-down lips carry PEM
  nuts and are screwed through the cheeks.

These are assembled with align, plus hardware (screws, feet) and a control
board inserted from a STEP file. The board is aligned by one pot onto its panel
hole, and every other part on the board is then checked against its own hole.

All lengths are in millimetres and are outer dimensions unless noted. The
sheet metal rule values are placeholders for the fabricator's numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from qfold import kernel, parts
from qfold.assembly import (Design, Occurrence, align, frame, insert_step, sheet_component)
from qfold.fold import Face, Flange, Hole, SheetLayout, SheetMetalBody, SheetMetalRule, layout
from qfold.project import Project

HERE = Path(__file__).resolve().parent
BOARD_STEP = HERE / "control_board.step"


@dataclass
class CaseParams:
    W: float = 320.0  # outer width
    D: float = 160.0  # outer depth
    Hf: float = 26.0  # cheek height at the front
    Hb: float = 58.0  # cheek height at the back
    rule: SheetMetalRule = field(default_factory=lambda: SheetMetalRule(
        "Aluminium 5052-H32 (placeholder)", thickness=1.6, bend_radius=1.6, k_factor=0.42))
    corner_gap: float = 0.4  # cheek end to front lip / rear wall inner face
    panel_clearance: float = 0.3  # top panel lip to cheek inner face
    front_lip_clearance: float = 0.5  # front lip top to panel underside
    relief_depth: float | None = None  # defaults to R + T
    relief_width: float = 2.0
    corner_relief: float = 2.0  # square relief where two base bends meet (>= T)
    lip_depth: float = 18.0  # top panel lip, from panel top surface
    pem_edge_cl: float = 4.8  # PEM CLS-M3 centre-to-edge minimum (placeholder, check catalog)
    board_gap: float = 10.0  # board top to panel inside surface (part body height)
    board_inset: float = 8.0  # board edge to panel edge


@dataclass(frozen=True)
class PanelPart:
    """One panel hole and the board part that goes through it."""
    tag: str  # panel hole, e.g. "P6"
    ref: str  # board designator, e.g. "RV1"
    part: str  # key into parts.STANDIN
    u: float
    v: float
    label: str
    hole: dict


def panel_parts() -> list[PanelPart]:
    out = [PanelPart("P1", "U1", "oled_096", 42.0, 116.0, "OLED window",
                     dict(kind="rrect", w=36.0, h=20.0, r=1.0))]
    for i, v in enumerate((128.0, 120.0, 112.0, 104.0)):
        out.append(PanelPart(f"P{2 + i}", f"D{1 + i}", "led_3mm", 72.0, v, "3 mm LED",
                             dict(kind="circle", d=3.2)))
    n = 0
    for v in (116.0, 72.0):
        for u in (110.0, 155.0, 200.0, 245.0, 290.0):
            out.append(PanelPart(f"P{6 + n}", f"RV{1 + n}", "pot_9mm", u, v,
                                 "9 mm pot (M7 bushing)", dict(kind="circle", d=7.2)))
            n += 1
    for i, u in enumerate((30.0, 62.0)):
        out.append(PanelPart(f"P{16 + i}", f"SW{1 + i}", "toggle_m6", u, 72.0,
                             "toggle switch (M6)", dict(kind="circle", d=6.4)))
    for i in range(8):
        out.append(PanelPart(f"P{18 + i}", f"SW{3 + i}", "button_12mm", 40.0 + i * 34.0, 30.0,
                             "12 mm button", dict(kind="circle", d=12.2)))
    return out


def panel_size(p: CaseParams) -> tuple[float, float]:
    """Top panel width and slope length."""
    T, c = p.rule.T, p.panel_clearance
    alpha = math.atan2(p.Hb - p.Hf, p.D)
    # The back edge is square to the panel, so its lower corner sits
    # T*sin(alpha) further back than its top.
    return p.W - 2 * T - 2 * c, (p.D - T - c - T * math.sin(alpha)) / math.cos(alpha)


@dataclass
class Case:
    params: CaseParams
    design: Design
    shell: SheetLayout
    panel: SheetLayout
    slope: float  # radians
    mounts: list[tuple[str, str]]  # (board part occurrence path, panel ref)
    clamped: set[str]  # board parts held against the panel: they must touch it
    screws: list[str]
    screw_holes: list[tuple[str, str, str]]  # (screw path, cheek hole ref, lip hole ref)
    feet: list[str]
    foot_holes: list[tuple[str, str]]  # (foot path, base hole ref)


def _wall_outline(b0: float, b1: float, w0: float, w1: float, Lf: float, rd: float):
    """Wall standing on a bend that spans v in [b0, b1]. The wall spans
    [w0, w1]; where it reaches past the bend it is notched to depth rd, so the
    bend ends in free edges (bend relief)."""
    return [(0, b0), (0, b1), (rd, b1), (rd, w1), (Lf, w1), (Lf, w0), (rd, w0), (rd, b0)]


def build_panel(p: CaseParams) -> SheetLayout:
    m = p.rule
    T, R = m.T, m.R
    a = R + T  # set-back of a 90° bend, measured to the outer surface
    Wp, Sp = panel_size(p)
    s0 = s1 = 8.0  # lip ends to the panel's front and back edges
    rw = p.relief_width
    Ll = p.lip_depth - a
    ch = 3.0
    outline = [(0, 0), (Wp, 0), (Wp, s0 - rw), (Wp - a, s0 - rw), (Wp - a, Sp - s1 + rw),
               (Wp, Sp - s1 + rw), (Wp, Sp), (0, Sp), (0, Sp - s1 + rw), (a, Sp - s1 + rw),
               (a, s0 - rw), (0, s0 - rw)]
    uh = Ll / 2 + 0.5

    def lip(name: str, tags) -> Face:
        return Face(name, [(0, s0), (0, Sp - s1), (Ll, Sp - s1 - ch), (Ll, s0 + ch)],
                    [Hole("circle", (uh, v), d=4.22, label="PEM CLS-M3 nut", tag=t,
                          edge_cl=p.pem_edge_cl) for v, t in zip((s0 + 22.0, Sp - s1 - 22.0), tags)])

    holes = [Hole(pp.hole["kind"], (pp.u, pp.v), label=pp.label, tag=pp.tag,
                  **{k: v for k, v in pp.hole.items() if k != "kind"}) for pp in panel_parts()]
    root = Face("panel", outline, holes, [
        Flange("panel_right", (Wp - a, 0), (0, 1), (1, 0), s0, Sp - s1, 90.0,
               lip("lip_right", ("L1", "L2"))),
        Flange("panel_left", (a, 0), (0, 1), (-1, 0), s0, Sp - s1, 90.0,
               lip("lip_left", ("L3", "L4"))),
    ])
    # Part coordinates: top (outside) surface at z = 0, material below it.
    return layout(SheetMetalBody("top_panel", m, root, N=(0.0, 0.0, -1.0)))


def panel_seat(p: CaseParams) -> np.ndarray:
    """Where the panel's origin sits on the shell: front-left top corner,
    z along the panel's outward normal."""
    T, c = p.rule.T, p.panel_clearance
    alpha = math.atan2(p.Hb - p.Hf, p.D)
    return frame((T + c, 0.0, p.Hf), (0.0, -math.sin(alpha), math.cos(alpha)), (1.0, 0.0, 0.0))


def build_shell(p: CaseParams, lip_holes_world: dict[str, np.ndarray]) -> SheetLayout:
    """lip_holes_world: lip hole tag -> world frame (shell coordinates)."""
    m = p.rule
    T, R = m.T, m.R
    a = R + T
    rd = p.relief_depth if p.relief_depth is not None else a
    cr = p.corner_relief
    alpha = math.atan2(p.Hb - p.Hf, p.D)
    g = p.corner_gap
    L_x, L_y = p.W - 2 * a, p.D - 2 * a
    h_front = p.Hf - T / math.cos(alpha) - p.front_lip_clearance

    base_holes = [Hole("circle", (x, y), d=3.4, label="foot screw M3", tag=f"A{i + 1}")
                  for i, (x, y) in enumerate([(x, y) for x in (12.0, p.W - 12.0)
                                              for y in (12.0, p.D - 12.0)])]
    for i in range(6):
        base_holes.append(Hole("rrect", (50.0 + i * 44.0, p.D - 30.0), w=36.0, h=5.0, r=2.5,
                               label="vent slot", tag=f"A{5 + i}"))

    rear = [("circle", 30.0, dict(d=8.2), "DC barrel jack"),
            ("circle", 52.0, dict(d=6.4), "power switch (M6)"),
            ("rrect", 75.0, dict(w=9.4, h=3.8, r=1.6, rot=90.0), "USB-C"),
            ("circle", 105.0, dict(d=15.6), "MIDI DIN in"),
            ("circle", 135.0, dict(d=15.6), "MIDI DIN out")]
    rear += [("circle", x, dict(d=6.2), "3.5 mm jack") for x in
             (175.0, 195.0, 215.0, 235.0, 255.0, 275.0)]
    z_jacks = 26.0
    # Rear wall coordinates are (u, v) = (z - a, x - a); the USB-C slot is
    # turned to lie along x.
    rear_holes = [Hole(kind, (z_jacks - a, x - a), label=label, tag=f"R{i + 1}", **kw)
                  for i, (kind, x, kw, label) in enumerate(rear)]

    def cheek(name: str, lip_tags: tuple[str, str], tags: tuple[str, str], side: str) -> Face:
        vs, ve = T + g - a, p.D - T - g - a
        top = lambda v: p.Hf + (v + a) * math.tan(alpha) - a
        outline = [(0, cr), (0, L_y - cr), (rd, L_y - cr), (rd, ve), (top(ve), ve), (top(vs), vs),
                   (rd, vs), (rd, cr)]
        holes = []
        for lt, t in zip(lip_tags, tags):
            y, z = lip_holes_world[lt][1, 3], lip_holes_world[lt][2, 3]
            holes.append(Hole("circle", (z - a, y - a), d=3.4, label=f"M3 clearance ({side})",
                              tag=t))
        return Face(name, outline, holes)

    # The base sits inside the bend lines. Each bend stops `cr` short of the
    # corner, and a cr x cr square is cut from each base corner, so no two
    # bends end on the same sharp corner (corner relief).
    x0, x1, y0, y1 = a, p.W - a, a, p.D - a
    base_outline = [(x0 + cr, y0), (x1 - cr, y0), (x1 - cr, y0 + cr), (x1, y0 + cr),
                    (x1, y1 - cr), (x1 - cr, y1 - cr), (x1 - cr, y1), (x0 + cr, y1),
                    (x0 + cr, y1 - cr), (x0, y1 - cr), (x0, y0 + cr), (x0 + cr, y0 + cr)]
    base = Face("base", base_outline, base_holes, [
        Flange("front", (a, a), (1, 0), (0, -1), cr, L_x - cr, 90.0,
               Face("front_lip", _wall_outline(cr, L_x - cr, -a, L_x + a, h_front - a, rd))),
        Flange("back", (a, p.D - a), (1, 0), (0, 1), cr, L_x - cr, 90.0,
               Face("back_wall", _wall_outline(cr, L_x - cr, -a, L_x + a, p.Hb - a, rd),
                    rear_holes)),
        Flange("right", (p.W - a, a), (0, 1), (1, 0), cr, L_y - cr, 90.0,
               cheek("cheek_right", ("L1", "L2"), ("S1", "S2"), "right")),
        Flange("left", (a, a), (0, 1), (-1, 0), cr, L_y - cr, 90.0,
               cheek("cheek_left", ("L3", "L4"), ("S3", "S4"), "left")),
    ])
    return layout(SheetMetalBody("bottom_shell", m, base))


def build_shell_only(p: CaseParams | None = None) -> SheetLayout:
    """The shell layout on its own (no kernel), for layout-level checks."""
    p = p or CaseParams()
    panel = build_panel(p)
    seat = panel_seat(p)
    refs = sheet_component("top_panel", panel).refs
    return build_shell(p, {t: seat @ refs[t] for t in ("L1", "L2", "L3", "L4")})


def make_board(p: CaseParams, name: str = "control_board",
               nudge: dict[str, tuple[float, float]] | None = None) -> Design:
    """The stand-in control board as its own Design, laid out from the panel
    the way a PCB designer would. `nudge` moves named parts (for tests)."""
    Wp, Sp = panel_size(p)
    inset, gap = p.board_inset, p.board_gap
    d = Design(name)
    d.add(parts.pcb(Wp - 2 * inset, Sp - 2 * inset), "PCB")
    made = {}
    for pp in panel_parts():
        comp = made.setdefault(pp.part, parts.STANDIN[pp.part](gap))
        dx, dy = (nudge or {}).get(pp.ref, (0.0, 0.0))
        t = np.eye(4)
        t[:3, 3] = (pp.u - inset + dx, pp.v - inset + dy, gap)
        d.add(comp, pp.ref, t)
    return d


def write_board_step(p: CaseParams, path: Path, workdir: Path, **kw) -> dict:
    """Export the stand-in board as a named assembly STEP, like a KiCad export."""
    d = make_board(p, **kw)
    job = d.job()
    job["outputs"] = {"step": [{"path": str(path), "tree": d.step_tree()}]}
    path.parent.mkdir(parents=True, exist_ok=True)
    return kernel.run(job, workdir)


def build(p: CaseParams | None = None, board_step: Path = BOARD_STEP,
          workdir: Path | None = None) -> Case:
    p = p or CaseParams()
    workdir = workdir or (HERE / "out" / "work")
    alpha = math.atan2(p.Hb - p.Hf, p.D)

    panel = build_panel(p)
    panel_comp = sheet_component("top_panel", panel, color=(58, 62, 70))
    seat = panel_seat(p)
    # Cheek holes go where the lip holes land once the panel sits on its seat.
    lip_world = {t: seat @ panel_comp.refs[t] for t in ("L1", "L2", "L3", "L4")}
    shell = build_shell(p, lip_world)
    shell_comp = sheet_component("bottom_shell", shell, color=(176, 180, 186))
    shell_comp.refs["panel_seat"] = seat

    d = Design("synth_case")
    shell_occ = d.add(shell_comp)
    panel_occ = d.add(panel_comp)
    align(panel_occ, "origin", onto=shell_occ.ref("panel_seat"))

    screw = parts.screw_m3x6()
    screws, screw_holes = [], []
    for i, (tag, lip) in enumerate((("S1", "L1"), ("S2", "L2"), ("S3", "L3"), ("S4", "L4"))):
        occ = d.add(screw, f"screw_{i + 1}")
        align(occ, "origin", onto=shell_occ.ref(tag))
        screws.append(occ.path)
        screw_holes.append((occ.path, f"bottom_shell:{tag}", f"top_panel:{lip}"))
    foot = parts.rubber_foot()
    feet, foot_holes = [], []
    for i, tag in enumerate(("A1", "A2", "A3", "A4")):
        occ = d.add(foot, f"foot_{i + 1}")
        align(occ, "origin", onto=shell_occ.ref(tag))
        feet.append(occ.path)
        foot_holes.append((occ.path, f"bottom_shell:{tag}"))

    if not Path(board_step).exists():
        write_board_step(p, Path(board_step), workdir)
    tree = kernel.inspect_step(Path(board_step), workdir)
    board = insert_step(d, board_step, tree, name="control_board", colors=parts.COLORS)
    pp0 = next(pp for pp in panel_parts() if pp.ref == "RV1")
    align(board, pp0.ref, onto=panel_occ.ref(f"{pp0.tag}.inside"))
    mounts = [(f"control_board/{pp.ref}", f"top_panel:{pp.tag}.inside") for pp in panel_parts()]
    clamped = {f"control_board/{pp.ref}" for pp in panel_parts() if pp.part in parts.CLAMPED}

    return Case(p, d, shell, panel, alpha, mounts, clamped, screws, screw_holes, feet, foot_holes)


def project(case: Case, rules=None) -> Project:
    """The synth case as a Project: what must touch, clear and line up."""
    from . import sheets as synth_sheets

    d = case.design
    p = Project("synth_case", d, rules=rules, params=case.params, title="Synth case", notes=[
        "Material, bend radius and K-factor are PLACEHOLDERS: use your fab's values.",
        "Hole sizes for parts are typical values; confirm with datasheets."])
    p.clear("bottom_shell", "top_panel", name="bottom_shell vs top_panel")
    p.seated(case.screws, onto=["bottom_shell", "top_panel"], name="screw heads on cheeks")
    p.seated(case.feet, onto="bottom_shell", name="feet on base")
    for occ in d.find("control_board").children():
        # Pots, switches and buttons clamp to the panel. LEDs, the display and
        # the PCB sit clear of it; their place is pinned by the hole check.
        p.seated(occ.path, onto="top_panel", name="board parts against panel",
                 touch=occ.path in case.clamped)
    p.clear("control_board", "bottom_shell", min=2.0, name="control board vs bottom_shell")
    # A screw's depth is set by its cheek hole. The lip's PEM hole sits T plus
    # the panel clearance behind it, so for the lip only the axis is compared.
    for s, cheek, lip in case.screw_holes:
        p.on_hole(s, cheek, name="screws on cheek and lip holes")
        p.on_hole(s, lip, name="screws on cheek and lip holes", axial=False)
    for f, hole in case.foot_holes:
        p.on_hole(f, hole, name="feet on their base holes")
    for part, hole in case.mounts:  # only RV1 was aligned; the rest test the board layout
        p.on_hole(part, hole, name="board parts on their panel holes")

    def coverage(b):
        panel_tags = {t for t in d.find("top_panel").component.refs
                      if not t.endswith(".inside") and t.startswith("P")}
        mounted = {h.split(":")[-1].removesuffix(".inside") for _, h in case.mounts}
        board_parts = {o.name for o in d.find("control_board").children()} - {"PCB"}
        used = {m.split("/")[-1] for m, _ in case.mounts}
        b.rep.add("every panel hole has a part, every part a hole",
                  panel_tags == mounted and board_parts == used,
                  f"{len(panel_tags)} panel holes, {len(board_parts)} board parts"
                  + ("" if panel_tags == mounted else f"; empty holes {sorted(panel_tags - mounted)}")
                  + ("" if board_parts == used
                     else f"; parts without hole {sorted(board_parts - used)}"))
    p.check(coverage)

    p.facts.append(("Panel slope", f"{math.degrees(case.slope):.2f}°"))
    p.extra_parts.append({"component": "PEM CLS-M3-1 nut", "qty": 4,
                          "source": "clinched in panel lips"})
    p.renders["render_board"] = "control_board"
    p.part_sheets = lambda b: synth_sheets.part_sheets(case, b)
    return p
