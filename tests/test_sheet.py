"""Sheet: parts described by outside dimensions, with generated reliefs."""

import math

import numpy as np
import pytest

from qfold import dfm, kernel, shops
from qfold.fold import Hole, SheetMetalRule, expected_volume_difference
from qfold.sheet import Sheet

RULE = SheetMetalRule("test", 1.5, 1.25, 0.42)
T, A = RULE.T, RULE.R + RULE.T


def solid(sheet: Sheet, tmp_path):
    """Kernel facts about the part, after checking it is one sound solid whose
    flat pattern is the same sheet and that it passes the DFM rules."""
    lay = sheet.layout()
    job = {"bodies": [{"name": "m", "plates": lay.plates3d, "sectors": lay.sectors},
                      kernel.flat_body(lay, "f")], "outputs": {}}
    res = kernel.run(job, tmp_path)["bodies"]
    m, f = res["m"], res["f"]
    tol = 1e-9 * m["volume"]
    assert m["valid"] and m["solids"] == 1
    assert abs(m["parts_volume"] - m["volume"]) <= tol, "folded part intersects itself"
    assert abs(f["parts_volume"] - f["volume"]) <= tol, "flat pattern overlaps itself"
    assert abs(m["parts_volume"] - f["parts_volume"] - expected_volume_difference(lay)) <= tol
    rep = dfm.Report()
    dfm.check_layout(lay, dfm.Rules(), rep)
    assert rep.ok, [c for c in rep.checks if c.status == "fail"]
    return lay, m


def box(**kw) -> Sheet:
    s = Sheet("box", RULE, **kw)
    base = s.base(120, 80)
    for e in ("front", "back", "right", "left"):
        base.flange(e, 30, name=e)
    return s


def test_outside_dimensions_are_what_you_asked_for(tmp_path):
    lay, m = solid(box(), tmp_path)
    assert m["bbox"] == pytest.approx([0, 0, 0, 120, 80, 30], abs=1e-6)
    # Walls made first (front, back) run the full width; the sides tuck in.
    front, right = lay.faces["front"], lay.faces["right"]
    xs = [front.to3d(u, v)[0] for u, v in front.face.outline]
    ys = [right.to3d(u, v)[1] for u, v in right.face.outline]
    gap = max(0.3, 0.25 * T)
    assert (min(xs), max(xs)) == pytest.approx((0, 120))
    assert (min(ys), max(ys)) == pytest.approx((T + gap, 80 - T - gap))


def test_reliefs_are_sized_from_the_rule_and_can_be_changed(tmp_path):
    def sizes(s):
        lay = s.layout()
        base = lay.faces["base"].face.outline
        corner = min(u for u, _ in base if u > A + 1e-9) - A  # square cut from the base corner
        notch = sorted({u for u, _ in lay.faces["front"].face.outline})[1]  # beside the bend
        return corner, notch
    ba = math.pi / 2 * (RULE.R + RULE.K * T)
    assert sizes(box()) == pytest.approx((T, RULE.R + T + 0.5 - ba / 2))
    wide = box(relief_width=3.0, relief_depth=4.0)
    assert sizes(wide) == pytest.approx((3.0, 4.0))
    solid(wide, tmp_path)
    scs = shops.spec("sendcutsend", "5052 Aluminum", 1.6)
    assert Sheet("x", scs).relief_width == pytest.approx(scs.rules.relief_width)


def test_over_and_under_can_be_chosen(tmp_path):
    s = Sheet("box", RULE, corner_gap=0.5)
    base = s.base(120, 80)
    base.flange("front", 30, name="front", over=False)
    base.flange("right", 30, name="right")
    lay, _ = solid(s, tmp_path)
    right, front = lay.faces["right"], lay.faces["front"]
    assert min(right.to3d(u, v)[1] for u, v in right.face.outline) == pytest.approx(0)
    assert max(front.to3d(u, v)[0] for u, v in front.face.outline) == pytest.approx(120 - T - 0.5)
    both = Sheet("box", RULE)
    b = both.base(120, 80)
    b.flange("front", 30, over=True)
    b.flange("right", 30, over=True)
    with pytest.raises(ValueError, match="can't both go over"):
        both.layout()


def test_lip_height_is_measured_to_its_outside(tmp_path):
    s = Sheet("u", RULE)
    wall = s.base(100, 60).flange("right", 40, name="wall")
    wall.flange("top", 12, name="lip")
    lay, m = solid(s, tmp_path)
    assert m["bbox"][5] == pytest.approx(40)  # the lip's top surface, not the wall's edge
    lip = lay.faces["lip"]
    assert min(lip.to3d(u, v)[0] for u, v in lip.face.outline) == pytest.approx(100 - 12)


def test_partial_flange_gets_relief_slots(tmp_path):
    s = Sheet("tab", RULE)
    s.base(100, 60).flange("back", 20, name="tab", start=30, end=70)
    lay, m = solid(s, tmp_path)
    assert m["bbox"] == pytest.approx([0, 0, 0, 100, 60, 20], abs=1e-6)
    us = sorted({round(u, 6) for u, _ in lay.faces["base"].face.outline})
    assert us == pytest.approx([0, 30 - T, 30, 70, 70 + T, 100])  # a T-wide slot each side
    tab = lay.faces["tab"]
    xs = [tab.to3d(u, v)[0] for u, v in tab.face.outline]
    assert (min(xs), max(xs)) == pytest.approx((30, 70))


def test_sloped_wall_and_folded_down_lid(tmp_path):
    s = Sheet("wedge", RULE)
    base = s.base(100, 60)
    base.flange("right", (20, 45), name="cheek")  # 20 high at the front, 45 at the back
    lay, m = solid(s, tmp_path)
    cheek = lay.faces["cheek"]
    top = sorted((cheek.to3d(u, v)[1], cheek.to3d(u, v)[2]) for u, v in cheek.face.outline)
    assert top[0] == pytest.approx((0, 20)) or top[1] == pytest.approx((0, 20))
    assert max(z for _, z in top) == pytest.approx(45)
    with pytest.raises(ValueError, match="sloped top"):
        s.panels["cheek"].flange("top", 10)

    lid = Sheet("lid", RULE, up=False)
    b = lid.base(100, 60, name="top")
    b.flange("left", 15, name="l")
    b.flange("right", 15, name="r")
    lid.hole("top", (50, 30, 0), Hole("circle", d=6, tag="H"))
    lid.hole("r", (100, 30, -10), Hole("circle", d=3, tag="S"))
    lay, m = solid(lid, tmp_path)
    assert m["bbox"] == pytest.approx([0, 0, -15, 100, 60, 0], abs=1e-6)
    c = lid.component()
    assert np.allclose(c.refs["S"][:3, 3], (100, 30, -10)) and np.allclose(c.refs["S"][:3, 2], (1, 0, 0))


def test_other_angles_meet_at_the_outside_corner(tmp_path):
    s = Sheet("vee", RULE)
    s.base(100, 60).flange("right", 30, name="wing", angle=45)
    lay, _ = solid(s, tmp_path)
    wing = lay.faces["wing"]
    far = max((wing.to3d(u, v) for u, v in wing.face.outline), key=lambda q: q[2])
    # 30 mm along the wing from where the two outside surfaces meet (x = 100, z = 0).
    assert far[[0, 2]] == pytest.approx([100 + 30 * math.cos(math.radians(45)),
                                         30 * math.sin(math.radians(45))])


def test_mistakes_are_named():
    s = Sheet("p", RULE)
    base = s.base(50, 40)
    with pytest.raises(ValueError, match="front, back|left, right"):
        base.flange("top", 10)
    base.flange("front", 10, name="w")
    with pytest.raises(ValueError, match="named 'w'"):
        base.flange("back", 10, name="w")
    with pytest.raises(KeyError, match="it has: base, w"):
        s.hole("lid", (0, 0, 0), Hole("circle", d=3))
    short = Sheet("q", RULE)
    short.base(50, 40).flange("front", 3.0)
    with pytest.raises(ValueError, match="too short"):
        short.layout()
    with pytest.raises(TypeError, match="bend_radius"):
        Sheet("o", shops.spec("oshcut", "5052-H32 Aluminum", 1.6))
