"""A pedal-style box: the template to copy for a new design.

    python -m examples.pedal_box [out_dir]

Tray: base, four walls, and an inward lip with PEM nuts on each side wall.
Every dimension is an outside dimension; Sheet does the bend math.
Lid: a flat plate resting on the lips, flush with the wall tops, screwed
down. Pots and an LED through the lid, jacks in the front wall, USB-C and DC
in the back. Coordinates: x right, y back, z up, origin at the tray's
front-left bottom outside corner.
"""

import sys
from dataclasses import dataclass

from qfold import parts, shops
from qfold.assembly import Design, align, frame
from qfold.fold import Hole
from qfold.project import Project
from qfold.sheet import Sheet


@dataclass
class Params:
    shop: str = "jlccnc"
    material: str = "Aluminum 5052"
    thickness: float = 1.5  # mm, a stock thickness of that shop
    W: float = 140.0  # outside width (x)
    D: float = 100.0  # outside depth (y)
    H: float = 40.0  # outside height (z); the lid's top is flush with the walls
    corner_gap: float = 0.4  # side wall ends to the front and back walls
    lid_clearance: float = 0.3  # lid edge to the walls, each side
    lip: float = 14.0  # lip width, from the side wall's outside face
    pot_body: float = 10.0  # pot body depth under the lid


def build_tray(p: Params, shop) -> Sheet:
    T = shop.rule.T
    tray = Sheet("tray", shop, corner_gap=p.corner_gap)
    base = tray.base(p.W, p.D)
    # Heights are outside dimensions. The front and back walls are made
    # first, so they run the full width and the side walls tuck in behind.
    base.flange("front", p.H, name="front_wall")
    base.flange("back", p.H, name="back_wall")
    for side in ("right", "left"):
        # The lid rests on the lips, so their top surface is T below the rim.
        wall = base.flange(side, p.H - T, name=f"{side}_wall")
        wall.flange("top", p.lip, name=f"{side}_lip")

    # Holes go where they are on the finished box, in x, y, z.
    tray.hole("front_wall", (40, 0, 20), Hole("circle", d=9.5, label='1/4" jack in', tag="J1"))
    tray.hole("front_wall", (100, 0, 20), Hole("circle", d=9.5, label='1/4" jack out', tag="J2"))
    tray.hole("back_wall", (50, p.D, 15), Hole("rrect", w=9.4, h=3.8, r=1.6, rot=90.0,
                                               label="USB-C", tag="J3"))
    tray.hole("back_wall", (90, p.D, 20), Hole("circle", d=8.2, label="DC barrel jack", tag="J4"))
    x_nut = p.lip / 2 + 1.5  # the middle of the lip's flat part
    for i, (x, y, face) in enumerate([(p.W - x_nut, 18, "right_lip"),
                                      (p.W - x_nut, p.D - 18, "right_lip"),
                                      (x_nut, 18, "left_lip"), (x_nut, p.D - 18, "left_lip")]):
        tray.hole(face, (x, y, p.H - T), Hole("circle", d=4.22, label="PEM CLS-M3 nut",
                                              tag=f"N{i + 1}", edge_cl=4.8))
    for i, (x, y) in enumerate([(15, 15), (p.W - 15, 15), (15, p.D - 15), (p.W - 15, p.D - 15)]):
        tray.hole("base", (x, y, 0), Hole("circle", d=3.4, label="foot screw M3", tag=f"F{i + 1}"))
    return tray


def build_lid(p: Params, shop, nuts) -> Sheet:
    """nuts: the tray's nut hole centres (x, y), which the lid's holes follow."""
    off = shop.rule.T + p.lid_clearance  # lid's front-left corner, in tray coordinates
    Wl, Dl = p.W - 2 * off, p.D - 2 * off
    lid = Sheet("lid", shop, up=False)  # its top surface is z = 0
    lid.base(Wl, Dl, name="lid")
    for i, (x, y) in enumerate(nuts):
        lid.hole("lid", (x - off, y - off, 0), Hole("circle", d=3.4, label="M3 clearance",
                                                    tag=f"C{i + 1}"))
    for i, dx in enumerate((-40.0, 0.0, 40.0)):
        lid.hole("lid", (Wl / 2 + dx, Dl / 2, 0), Hole("circle", d=7.2, label="pot (M7 bushing)",
                                                       tag=f"P{i + 1}"))
    lid.hole("lid", (Wl / 2, Dl - 15.0, 0), Hole("circle", d=3.2, label="3 mm LED", tag="L1"))
    return lid


def build(p: Params | None = None) -> Project:
    p = p or Params()
    shop = shops.spec(p.shop, p.material, p.thickness)
    m = shop.rule

    tray = build_tray(p, shop).component()
    nuts = [tuple(tray.refs[f"N{i}"][:2, 3]) for i in (1, 2, 3, 4)]
    lid = build_lid(p, shop, nuts).component(color=(70, 74, 82))
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
