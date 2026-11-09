"""A pedal-style box: the template to copy for a new design.

    python -m examples.pedal_box [out_dir]

Tray: base, four walls, and an inward lip with PEM nuts on each side wall.
Lid: a flat plate resting on the lips, flush with the wall tops, screwed
down. Pots and an LED through the lid, jacks in the front wall, USB-C and DC
in the back. Coordinates: x right, y back, z up, origin at the tray's
front-left bottom outside corner.
"""

import sys
from dataclasses import dataclass

from qfold import parts, shops
from qfold.assembly import Design, align, frame, sheet_component
from qfold.fold import Face, Flange, Hole, SheetMetalBody, hole_at, layout
from qfold.project import Project


@dataclass
class Params:
    shop: str = "jlccnc"
    material: str = "Aluminum 5052"
    thickness: float = 1.5  # mm, a stock thickness of that shop
    W: float = 140.0  # outside width (x)
    D: float = 100.0  # outside depth (y)
    H: float = 40.0  # outside height (z); the lid's top is flush with the walls
    corner_relief: float = 2.0  # bends stop this short of each base corner
    corner_gap: float = 0.4  # side wall ends to the front and back walls
    lid_clearance: float = 0.3  # lid edge to the walls, each side
    lip: float = 14.0  # lip width, from the side wall's outside face
    pot_body: float = 10.0  # pot body depth under the lid


def build_tray(p: Params, m) -> SheetMetalBody:
    T = m.T
    a = m.R + T  # how far a 90° bend's wall sits outside its bend line
    cr, g = p.corner_relief, p.corner_gap
    Lx, Ly = p.W - 2 * a, p.D - 2 * a  # base, between bend lines

    def wall(name: str, length: float, w0: float, w1: float, height: float, flanges=()) -> Face:
        """A wall on a bend that runs v = cr..length-cr. The wall itself spans
        v = w0..w1 and u = 0..height, notched `a` deep beside the bend ends."""
        return Face(name, [(0, cr), (0, length - cr), (a, length - cr), (a, w1), (height, w1),
                           (height, w0), (a, w0), (a, cr)], flanges=list(flanges))

    def side(name: str) -> Face:
        # Side walls stop corner_gap short of the front and back walls, and
        # carry the lip the lid rests on (its top surface at z = H - T).
        v0, v1 = T + g - a, p.D - T - g - a
        h = p.H - T - 2 * a
        lip = Face(f"{name}_lip", [(0, v0), (0, v1), (p.lip - a, v1 - 2), (p.lip - a, v0 + 2)])
        return wall(name, Ly, v0, v1, h, [Flange(f"{name}_lip", (h, 0), (0, 1), (1, 0), v0, v1,
                                                 90.0, lip)])

    # The base stops at the bend lines, with a square cut from each corner so
    # no two bends meet (corner relief).
    x0, x1, y0, y1 = a, p.W - a, a, p.D - a
    base = Face("base", [(x0 + cr, y0), (x1 - cr, y0), (x1 - cr, y0 + cr), (x1, y0 + cr),
                         (x1, y1 - cr), (x1 - cr, y1 - cr), (x1 - cr, y1), (x0 + cr, y1),
                         (x0 + cr, y1 - cr), (x0, y1 - cr), (x0, y0 + cr), (x0 + cr, y0 + cr)])
    # Flange(name, start point, direction along the edge, outward direction,
    #        bend from..to along the edge, angle, wall)
    base.flanges = [
        Flange("front", (a, a), (1, 0), (0, -1), cr, Lx - cr, 90.0,
               wall("front_wall", Lx, -a, Lx + a, p.H - a)),
        Flange("back", (a, p.D - a), (1, 0), (0, 1), cr, Lx - cr, 90.0,
               wall("back_wall", Lx, -a, Lx + a, p.H - a)),
        Flange("right", (p.W - a, a), (0, 1), (1, 0), cr, Ly - cr, 90.0, side("right_wall")),
        Flange("left", (a, a), (0, 1), (-1, 0), cr, Ly - cr, 90.0, side("left_wall")),
    ]
    tray = SheetMetalBody("tray", m, base)

    # Holes go where they are on the finished box, in x, y, z.
    hole_at(tray, "front_wall", (40, 0, 20), Hole("circle", d=9.5, label='1/4" jack in', tag="J1"))
    hole_at(tray, "front_wall", (100, 0, 20), Hole("circle", d=9.5, label='1/4" jack out', tag="J2"))
    hole_at(tray, "back_wall", (50, p.D, 15), Hole("rrect", w=9.4, h=3.8, r=1.6, rot=90.0,
                                                   label="USB-C", tag="J3"))
    hole_at(tray, "back_wall", (90, p.D, 20), Hole("circle", d=8.2, label="DC barrel jack",
                                                   tag="J4"))
    x_nut = p.lip / 2 + a / 2
    for i, (x, y, face) in enumerate([(p.W - x_nut, 18, "right_wall_lip"),
                                      (p.W - x_nut, p.D - 18, "right_wall_lip"),
                                      (x_nut, 18, "left_wall_lip"),
                                      (x_nut, p.D - 18, "left_wall_lip")]):
        hole_at(tray, face, (x, y, p.H - T), Hole("circle", d=4.22, label="PEM CLS-M3 nut",
                                                  tag=f"N{i + 1}", edge_cl=4.8))
    for i, (x, y) in enumerate([(15, 15), (p.W - 15, 15), (15, p.D - 15), (p.W - 15, p.D - 15)]):
        hole_at(tray, "base", (x, y, 0), Hole("circle", d=3.4, label="foot screw M3",
                                              tag=f"F{i + 1}"))
    return tray


def build_lid(p: Params, m, nuts) -> SheetMetalBody:
    """nuts: the tray's nut hole centres (x, y), which the lid's holes follow."""
    off = m.T + p.lid_clearance  # lid's front-left corner, in tray coordinates
    Wl, Dl = p.W - 2 * off, p.D - 2 * off
    holes = [Hole("circle", (x - off, y - off), d=3.4, label="M3 clearance", tag=f"C{i + 1}")
             for i, (x, y) in enumerate(nuts)]
    holes += [Hole("circle", (Wl / 2 + dx, Dl / 2), d=7.2, label="pot (M7 bushing)", tag=f"P{i + 1}")
              for i, dx in enumerate((-40.0, 0.0, 40.0))]
    holes.append(Hole("circle", (Wl / 2, Dl - 15.0), d=3.2, label="3 mm LED", tag="L1"))
    # N = -z: the face's own surface (z = 0) is the top, with material below.
    return SheetMetalBody("lid", m, Face("lid", [(0, 0), (Wl, 0), (Wl, Dl), (0, Dl)], holes),
                          N=(0.0, 0.0, -1.0))


def build(p: Params | None = None) -> Project:
    p = p or Params()
    shop = shops.spec(p.shop, p.material, p.thickness)
    m = shop.rule

    tray = sheet_component("tray", layout(build_tray(p, m)))
    nuts = [tuple(tray.refs[f"N{i}"][:2, 3]) for i in (1, 2, 3, 4)]
    lid = sheet_component("lid", layout(build_lid(p, m, nuts)), color=(70, 74, 82))
    off = m.T + p.lid_clearance
    tray.add_ref("lid_seat", (off, off, p.H))  # where the lid's origin goes

    d = Design("pedal_box")
    t = d.add(tray, "tray")
    lid_occ = d.add(lid, "lid")
    align(lid_occ, "origin", onto=t.ref("lid_seat"))

    # Hardware: each part's origin is its mounting point, z out of the case.
    # Hole refs: "C1" on the outside surface, "C1.inside" on the inside.
    screw, foot, pot = parts.screw_m3x6(), parts.rubber_foot(), parts.pot_9mm(p.pot_body)
    screws = [align(d.add(screw, f"screw_{i}"), "origin", onto=lid_occ.ref(f"C{i}"))
              for i in (1, 2, 3, 4)]
    feet = [align(d.add(foot, f"foot_{i}"), "origin", onto=t.ref(f"F{i}")) for i in (1, 2, 3, 4)]
    pots = [align(d.add(pot, f"pot_{i}"), "origin", onto=lid_occ.ref(f"P{i}.inside"))
            for i in (1, 2, 3)]
    led = align(d.add(parts.led_3mm(6.0), "led"), "origin", onto=lid_occ.ref("L1.inside"))
    pcb = d.add(parts.pcb(110.0, 60.0), "pcb")  # its top face under the pot bodies
    align(pcb, "origin", onto=frame((p.W / 2 - 55.0, p.D / 2 - 30.0, p.H - m.T - p.pot_body)))

    # What must hold. Everything else (solids, flat patterns, DFM rules, gaps,
    # overlaps, files) is checked without asking.
    proj = Project("pedal_box", d, shop=shop, params=p)
    proj.seated("lid", onto="tray", name="lid rests on the lips")
    proj.seated([s.path for s in screws], onto="lid", name="screw heads on the lid")
    proj.seated([o.path for o in pots], onto="lid", name="pots against the lid")
    proj.seated([o.path for o in pots], onto="pcb", name="pots on the PCB")
    proj.seated(led.path, onto="lid", name="LED in its hole", touch=False)
    proj.seated([f.path for f in feet], onto="tray", name="feet on the base")
    proj.clear("pcb", "tray", min=2.0)
    for i, s in enumerate(screws, 1):
        proj.on_hole(s.path, f"lid:C{i}", name="screws through lid and nut")
        proj.on_hole(s.path, f"tray:N{i}", name="screws through lid and nut", axial=False)
    return proj


if __name__ == "__main__":
    sys.exit(build().main())
