"""A clamshell case for the Huaxin 37-key keybed.

    python -m designs.keybed_case [out_dir]

Top, folding down: the control panel behind the keys, with a cheek at each
end folded down to the table. The cheeks run forward past the panel,
alongside the keys, sloping down toward the front. Bottom, folding up: the
floor with a low front wall and a tall back wall, sitting between the
cheeks. The keybed stands on its eight bosses on the floor.

The bottom is recessed inside the top: the cheeks and panel reach `recess`
past it at the back, the front and underneath. Corners nest concentrically
with a `fit` gap: the back wall's top corners inside the top's bends, and
the cheeks' lower corners around the bottom's bends.

The front wall's top stays below the lowest point a pressed key reaches.
The screws that join the halves go through the cheeks into tabs on the
floor, placed under the key overhang and behind the keybed, never beside
the keybed frame.

Coordinates: x right, y back, z up; x = 0 is the left cheek's outside face,
y = 0 the cheeks' front tips, z = 0 the floor's underside. The
keybed STEP's own origin is the front-left corner of the lowest white key,
at white key top height.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

from qfold import parts, shops
from qfold.assembly import Design, align, box, cyl, frame, insert_step, prim_component
from qfold.fold import Hole
from qfold.project import Project
from qfold.sheet import Sheet

KEYBED = Path(__file__).resolve().parent / "keybed.step"
# Read from the keybed STEP (its solids are named): overall size, where the
# keys and frame end, and the eight mounting bosses.
KB_W, KB_D = 511.4, 161.4  # width; depth to the back of the frame
KB_KEYS = 160.0  # depth to the back ends of the keys
KB_FRAME_FRONT = 47.5  # the keys overhang the frame's front by this much
KB_SKIRT = 14.0  # a white key's front skirt, below its top
KB_FLOOR = 48.5  # boss bottoms below the white key tops
KB_BLACK_REAR = ((136.1, 7.0), (160.0, 0.0))  # black keys slope down to their back ends
KB_BOSS_X = (20.4, 156.0, 332.8, 495.9)
KB_BOSS_Y = (70.7, 146.2)


@dataclass
class Params:
    shop: str = "jlccnc"
    material: str = "Aluminum 5052"
    thickness: float = 2.0
    key_side: float = 1.5  # end keys to the cheeks' inside faces
    key_front: float = 1.5  # key fronts to the front wall's inside face
    key_travel: float = 10.0  # how far a key goes down when pressed
    travel_clear: float = 2.0  # front wall top below a fully pressed key
    rear_zone: float = 85.0  # room behind the keybed for electronics
    key_clear: float = 2.0  # panel underside above the highest key beneath it
    panel_overhang: float = 10.0  # panel front edge, forward of the keys' back ends
    cheek_front_rise: float = 1.0  # cheek tops above the white key tops, at the front
    recess: float = 2.5  # cheeks and panel reach this far past the bottom
    tab: float = 18.0  # height of the floor's side tabs, outside
    fit: float = 0.3  # between the cheeks' inside faces and the bottom
    corner: str = "overlap"
    corner_relief: str = "tear"
    bend_relief: str = "shear"
    pots: int = 8


def build(p: Params | None = None) -> Project:
    p = p or Params()
    shop = shops.spec(p.shop, p.material, p.thickness)
    T, g = shop.rule.T, p.fit
    style = dict(corner=p.corner, corner_relief=p.corner_relief, bend_relief=p.bend_relief,
                 corner_gap=p.fit)

    R, rc = shop.rule.R, p.recess
    w_in = KB_W + 2 * p.key_side  # between the cheeks' inside faces
    W = w_in + 2 * T  # overall width
    Db = T + p.key_front + KB_D + p.rear_zone + T  # the bottom, front wall to back wall
    D = Db + 2 * rc  # the top, cheek tips to the panel's back edge
    kx, ky, kz = T + p.key_side, rc + T + p.key_front, T + KB_FLOOR  # the keybed's origin
    # The panel sits key_clear above the highest key under its front edge:
    # there, the black keys are partway down their rear slope.
    (ya, za), (yb, zb) = KB_BLACK_REAR
    key_high = max(0.0, za + (zb - za) * (KB_KEYS - p.panel_overhang - ya) / (yb - ya))
    panel_rise = key_high + p.key_clear + T
    z_top = kz + panel_rise  # panel's top surface
    y_panel = ky + KB_KEYS - p.panel_overhang  # panel's front edge
    y_kb_back = ky + KB_D  # back of the keybed frame

    # ---- top: the panel, with a cheek folded down at each end
    top = Sheet("top", shop, dims="inside", up=False, **style)
    panel = top.base(w_in, D - y_panel, name="panel")
    for side in ("left", "right"):
        # Down past the floor, forward past the front wall, sloping toward the
        # front. The lower corners are rounded about the same centres as the
        # bottom's bends (outside radius R + T), `recess` further out.
        panel.flange(side, z_top + rc, name=f"{side}_cheek", inside=False, extend=(y_panel, 0),
                     extend_drop=(panel_rise - p.cheek_front_rise, None),
                     round=(R + T + rc, R + T + rc))

    def on_top(x, y, z):  # case coordinates -> the top's own (origin: panel's front-left corner)
        return (x, y - y_panel, z - z_top)

    # ---- bottom: floor, low front wall, tall back wall, and four side tabs
    wb = w_in - 2 * g  # between the cheeks, with the fit gap each side
    x0 = T + g  # the bottom's own origin in case coordinates
    key_low = kz - KB_SKIRT - p.key_travel  # underside of a fully pressed white key's front
    bottom = Sheet("bottom", shop, **style)
    base = bottom.base(wb, Db)
    base.flange("front", key_low - p.travel_clear, name="front_wall", round=(R - g, R - g))
    # Up to `fit` under the panel. Its top corners are rounded to nest inside
    # the top's bends: the same centres, `fit` smaller than their inside radius.
    base.flange("back", z_top - T - g, name="back_wall", round=(R - g, R - g))
    # Tabs for the cheek screws: under the key overhang, and behind the keybed.
    tabs = {"front": (rc + 8.0, ky + KB_FRAME_FRONT - 6.0),
            "rear": (y_kb_back + 6.0, rc + Db - 8.0)}  # in case coordinates
    for side in ("left", "right"):
        for name, (a, b) in tabs.items():
            base.flange(side, p.tab, name=f"{side}_tab_{name}", start=a - rc, end=b - rc)

    def on_bottom(x, y, z):  # case coordinates -> the bottom's own
        return (x - x0, y - rc, z)

    z_screw = 11.0
    screw_at = [(x, y, side, name) for x, side in ((0.0, "left"), (W, "right"))
                for name, y in (("front", sum(tabs["front"]) / 2), ("rear", tabs["rear"][0] + 15.0),
                                ("rear", tabs["rear"][1] - 15.0))]
    for i, (x, y, side, name) in enumerate(screw_at, 1):
        x_tab = x0 if side == "left" else x0 + wb  # the tab's outside face
        bottom.hole(f"{side}_tab_{name}", on_bottom(x_tab, y, z_screw),
                    Hole("circle", d=4.22, tag=f"N{i}", label="PEM CLS-M3 nut", edge_cl=4.8))
        top.hole(f"{side}_cheek", on_top(x, y, z_screw),
                 Hole("circle", d=3.4, tag=f"S{i}", label="M3 clearance"))
    for i, (bx, by) in enumerate([(x, y) for y in KB_BOSS_Y for x in KB_BOSS_X], 1):
        bottom.hole("base", on_bottom(kx + bx, ky + by, 0),
                    Hole("circle", d=3.4, tag=f"K{i}", label="keybed boss screw M3"))
    for i, (x, y) in enumerate([(30, 25), (W - 30, 25), (30, D - 25), (W - 30, D - 25)], 1):
        bottom.hole("base", on_bottom(x, y, 0), Hole("circle", d=3.4, tag=f"F{i}",
                                                     label="foot screw M3"))
    # Controls on the panel, connectors in the back wall.
    y_pots = y_panel + 0.55 * (D - y_panel)
    for i in range(p.pots):
        x = W / 2 + (i - (p.pots - 1) / 2) * 52.0
        top.hole("panel", on_top(x, y_pots, z_top), Hole("circle", d=7.2, tag=f"P{i + 1}",
                                                         label="pot (M7 bushing)"))
    top.hole("panel", on_top(W - 40, y_pots, z_top), Hole("circle", d=3.2, tag="L1",
                                                          label="3 mm LED"))
    for tag, x, hole in (("J1", 60, Hole("circle", d=9.5, label='1/4" jack')),
                         ("J2", 90, Hole("circle", d=9.5, label='1/4" jack')),
                         ("J3", 130, Hole("rrect", w=9.4, h=3.8, r=1.6, label="USB-C")),
                         ("J4", 160, Hole("circle", d=8.2, label="DC barrel jack"))):
        hole.tag = tag
        bottom.hole("back_wall", on_bottom(W - x, rc + Db, z_top - 26.0), hole)

    # ---- assembly
    d = Design("keybed_case")
    b = d.add(bottom.component(color=(72, 76, 84)), "bottom")
    align(b, "origin", onto=frame((x0, rc, 0)))
    t = d.add(top.component(color=(196, 198, 204)), "top")
    align(t, "origin", onto=frame((0, y_panel, z_top)))
    keybed = insert_step(d, KEYBED, name="keybed", color=(235, 235, 230))
    align(keybed, "origin", onto=frame((kx, ky, kz)))
    # The space the white keys' fronts sweep through when pressed. Not a part:
    # it is here so the checks can keep the case out of it.
    sweep = d.add(prim_component("key_travel_space", [box((0, 0, -KB_SKIRT - p.key_travel),
                                                          KB_W, KB_FRAME_FRONT, p.key_travel)],
                                 color=(235, 235, 230)), "key_travel")
    align(sweep, "origin", onto=frame((kx, ky, kz)))

    screw, foot, pot = parts.screw_m3x6(), parts.rubber_foot(), parts.pot_9mm(10.0)
    # What a clinch nut leaves standing proud on the inside: a 5.6 mm ring, 2 mm tall.
    nut = prim_component("PEM_CLS_M3", [cyl((0, 0, 0), 5.6, 2.0, bore=3.0)])
    n = len(screw_at)
    screws = [align(d.add(screw, f"screw_{i}"), "origin", onto=t.ref(f"S{i}"))
              for i in range(1, n + 1)]
    nuts = [align(d.add(nut, f"nut_{i}"), "origin", onto=b.ref(f"N{i}.inside"), flip=True)
            for i in range(1, n + 1)]
    feet = [align(d.add(foot, f"foot_{i}"), "origin", onto=b.ref(f"F{i}")) for i in range(1, 5)]
    pots = [align(d.add(pot, f"pot_{i}"), "origin", onto=t.ref(f"P{i}.inside"))
            for i in range(1, p.pots + 1)]
    led = align(d.add(parts.led_3mm(6.0), "led"), "origin", onto=t.ref("L1.inside"))

    # ---- what must hold
    proj = Project("keybed_case", d, shop=shop, params=p, title="Keybed case")
    case = ["top", "bottom"] + [x.path for x in nuts]
    proj.seated("keybed", onto="bottom", name="keybed stands on the floor")
    proj.clear("keybed", ["top"] + [x.path for x in nuts], min=1.0,
               name="keys and frame clear the top and the nuts")
    proj.ghost("key_travel")
    proj.clear("key_travel", case, min=1.0, name="pressed keys clear the case")
    proj.clear("top", "bottom", min=0.2, name="top slides over the bottom")
    proj.seated([x.path for x in nuts], onto="bottom", name="PEM nuts in the tabs")
    proj.seated([x.path for x in screws], onto="top", name="screw heads on the cheeks")
    proj.seated([x.path for x in feet], onto="bottom", name="feet on the floor")
    proj.seated([x.path for x in pots], onto="top", name="pots against the panel")
    proj.seated(led.path, onto="top", name="LED in its hole", touch=False)
    for i, x in enumerate(screws, 1):
        proj.on_hole(x.path, f"top:S{i}", name="screws through the cheeks into their nuts")
        proj.on_hole(x.path, f"bottom:N{i}", name="screws through the cheeks into their nuts",
                     axial=False)
    proj.facts += [("Between the cheeks", f"{w_in:g} (keybed {KB_W:g} + {p.key_side:g} each side)"),
                   ("White key tops", f"z = {kz:g}"),
                   ("Pressed key underside", f"z = {key_low:g}"),
                   ("Front wall top", f"z = {key_low - p.travel_clear:g}"),
                   ("Panel top", f"z = {z_top:.2f}"),
                   ("Highest key under the panel", f"z = {kz + key_high:.2f}")]
    proj.views = {"render_back": (-0.9, 1.3, 0.75), "render_side": (1.0, 0.0, 0.02),
                  "render_front": (0.25, -1.3, 0.35)}
    return proj


if __name__ == "__main__":
    sys.exit(build().main())
