"""A U-channel chassis with a board from a STEP file on four standoffs: the
shortest example, and the one that inserts a STEP. SendCutSend 1.6 mm 5052.

    python -m examples.chassis [out_dir]
"""
import sys
from dataclasses import dataclass
from pathlib import Path

from qfold import shops
from qfold.assembly import Design, align, cyl, frame, insert_step, prim_component, sheet_component
from qfold.fold import Face, Flange, Hole, SheetMetalBody, hole_at, layout
from qfold.project import Project

BOARD = Path(__file__).resolve().parents[1] / "synth_case" / "control_board.step"


@dataclass
class Params:
    shop: str = "sendcutsend"
    material: str = "5052 Aluminum"
    thickness: float = 1.6
    W: float = 340.0   # outside width (x)
    D: float = 180.0   # depth (y)
    H: float = 45.0    # outside wall height (z)
    standoff: float = 8.0


def build() -> Project:
    p = Params()
    shop = shops.spec(p.shop, p.material, p.thickness)
    m = shop.rule
    a = m.R + m.T
    wall = lambda name: Face(name, [(0, 0), (0, p.D), (p.H - a, p.D), (p.H - a, 0)])
    base = Face("base", [(a, 0), (p.W - a, 0), (p.W - a, p.D), (a, p.D)], flanges=[
        Flange("right", (p.W - a, 0), (0, 1), (1, 0), 0, p.D, 90.0, wall("right_wall")),
        Flange("left", (a, 0), (0, 1), (-1, 0), 0, p.D, 90.0, wall("left_wall")),
    ])
    body = SheetMetalBody("chassis", m, base)
    posts = [(20, 20), (p.W - 20, 20), (20, p.D - 20), (p.W - 20, p.D - 20)]
    for i, (x, y) in enumerate(posts, 1):
        hole_at(body, "base", (x, y, 0), Hole("circle", d=3.4, label="standoff screw M3", tag=f"M{i}"))
    for i, y in enumerate((40, p.D - 40), 1):
        hole_at(body, "right_wall", (p.W, y, 25), Hole("circle", d=4.5, label="rack ear M4", tag=f"E{i}"))
        hole_at(body, "left_wall", (0, y, 25), Hole("circle", d=4.5, label="rack ear M4", tag=f"E{i + 2}"))

    d = Design("chassis")
    ch = d.add(sheet_component("chassis", layout(body)), "chassis")
    post = prim_component("standoff_M3x8", [cyl((0, 0, 0), 6.0, p.standoff)])
    standoffs = [align(d.add(post, f"standoff_{i}"), "origin", onto=ch.ref(f"M{i}.inside"), flip=True)
                 for i in (1, 2, 3, 4)]
    # The board's origin is wherever its STEP put it: here a corner of the PCB's
    # top face, so it sits one PCB thickness above the standoffs.
    board = insert_step(d, BOARD, name="board")
    align(board, "origin", onto=frame((14, 14, m.T + p.standoff + 1.6)))

    proj = Project("chassis", d, shop=shop, params=p)
    proj.seated([s.path for s in standoffs], onto="chassis", name="standoffs on the base")
    proj.seated("board/PCB", onto=[s.path for s in standoffs], name="board on its standoffs")
    proj.clear("board", "chassis", min=3.0)
    for i, s in enumerate(standoffs, 1):
        proj.on_hole(s.path, f"chassis:M{i}.inside", name="standoffs on their holes")
    return proj


if __name__ == "__main__":
    sys.exit(build().main())
