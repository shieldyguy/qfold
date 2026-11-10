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


@pytest.mark.parametrize("corner", ["overlap", "butt", "open"])
@pytest.mark.parametrize("corner_relief", ["square", "round", "tear"])
@pytest.mark.parametrize("bend_relief", ["straight", "round", "tear"])
def test_every_seam_and_relief_style_makes_a_sound_box(tmp_path, corner, corner_relief,
                                                       bend_relief):
    s = box(corner=corner, corner_relief=corner_relief, bend_relief=bend_relief)
    lay = s.layout()
    job = {"bodies": [{"name": "m", "plates": lay.plates3d, "sectors": lay.sectors},
                      kernel.flat_body(lay, "f")], "outputs": {}}
    res = kernel.run(job, tmp_path)["bodies"]
    m, f = res["m"], res["f"]
    tol = 1e-9 * m["volume"]
    assert m["valid"] and m["solids"] == 1
    assert m["bbox"] == pytest.approx([0, 0, 0, 120, 80, 30], abs=1e-6)
    assert abs(m["parts_volume"] - m["volume"]) <= tol and abs(f["parts_volume"] - f["volume"]) <= tol
    assert abs(m["parts_volume"] - f["parts_volume"] - expected_volume_difference(lay)) <= tol
    rep = dfm.Report()
    dfm.check_layout(lay, dfm.Rules(), rep)
    assert rep.ok
    relief = next(c for c in rep.checks if c.name == "box: bend corner relief")
    assert relief.status == ("warn" if corner_relief == "tear" else "pass")


def _span(st, axis):
    vals = [st.to3d(u, v)[axis] for u, v, *_ in st.face.outline]
    return min(vals), max(vals)


def test_seams():
    gap = 0.5
    butt = box(corner="butt", corner_gap=gap).layout()
    for name, axis, full in (("front", 0, 120), ("right", 1, 80)):
        lo, hi = _span(butt.faces[name], axis)  # both stop at the inside corner
        assert (lo, full - hi) == pytest.approx((T + gap / math.sqrt(2),) * 2)
    opened = box(corner="open").layout()
    lo, hi = _span(opened.faces["front"], 0)
    assert (lo, 120 - hi) == pytest.approx((A + T,) * 2)  # as wide as its bend: set-back + relief


def test_relief_styles_shape_the_outline():
    arcs = lambda lay, face: [q for q in lay.faces[face].face.outline if len(q) > 2]
    rnd = box(corner_relief="round", bend_relief="round").layout()
    assert len(arcs(rnd, "base")) == 4 and len(arcs(rnd, "front")) == 2
    assert all(abs(q[2]) == pytest.approx(math.tan(math.pi / 8)) for q in arcs(rnd, "base"))
    assert not arcs(box().layout(), "base")
    tear = box(corner_relief="tear", bend_relief="tear", kerf=0.15).layout()
    assert len(tear.faces["base"].face.outline) == 4  # nothing cut from the corners
    assert sorted({u for u, *_ in tear.faces["front"].face.outline})[1] == pytest.approx(0.15)
    # A tab with shear reliefs: kerf-wide slits that stop just past the bend.
    s = Sheet("tab", RULE, bend_relief="shear", kerf=0.15)
    s.base(100, 60).flange("back", 20, name="tab", start=30, end=70)
    base = s.layout().faces["base"].face.outline
    assert sorted({round(u, 6) for u, _ in base}) == pytest.approx([0, 29.85, 30, 70, 70.15, 100])
    assert min(v for _, v in base if v > 1) == pytest.approx(60 - A - 0.15)
    # One flange can differ from the rest.
    s = Sheet("tab", RULE)
    s.base(100, 60).flange("back", 20, name="tab", start=30, end=70, relief="round")
    assert len(arcs(s.layout(), "base")) == 4  # two rounded slot ends
    with pytest.raises(ValueError, match="choose from straight, round, tear, shear"):
        Sheet("x", RULE, bend_relief="oval")


def test_inside_dimensions(tmp_path):
    s = Sheet("in", RULE, dims="inside")
    base = s.base(100, 60)
    for e in ("front", "back", "right"):  # no left wall: nothing to add there
        base.flange(e, 30, name=e)
    lay, m = solid(s, tmp_path)
    assert m["bbox"] == pytest.approx([0, 0, 0, 100 + T, 60 + 2 * T, 30 + T], abs=1e-6)
    # Mixed: an outside-sized base, one wall's height given inside, to under its lip.
    s = Sheet("mix", RULE)
    wall = s.base(100, 60).flange("right", 30, name="wall", inside=True)
    wall.flange("top", 12, name="lip")
    lay, m = solid(s, tmp_path)
    lip = lay.faces["lip"]
    assert lip.to3d(5, 5, T)[2] == pytest.approx(T + 30)  # the lip's underside: 30 clear inside
    assert m["bbox"][3:] == pytest.approx([100, 60, 30 + 2 * T], abs=1e-6)


def test_wall_extends_past_its_edge_with_a_sloped_edge(tmp_path):
    s = Sheet("top", RULE, up=False, bend_relief="shear")
    panel = s.base(100, 40, name="panel")
    panel.flange("left", 60, name="cheek", extend=(80, 0), extend_drop=(11, None))
    lay, m = solid(s, tmp_path)
    assert m["bbox"] == pytest.approx([0, -80, -60, 100, 40, 0], abs=1e-6)
    cheek = lay.faces["cheek"]
    pts = np.array([cheek.to3d(*q[:2]) for q in cheek.face.outline])
    tip = pts[np.isclose(pts[:, 1], -80)]
    assert sorted(tip[:, 2]) == pytest.approx([-60, -11])  # 11 below the panel's top at its tip
    bad = Sheet("top", RULE, up=False)
    b = bad.base(100, 40)
    b.flange("front", 20)
    b.flange("left", 60, extend=(10, 0))
    with pytest.raises(ValueError, match="can't extend past an end that meets another flange"):
        bad.layout()


def test_rounded_far_corners(tmp_path):
    s = Sheet("u", RULE)
    s.base(100, 60).flange("back", 30, name="wall", round=(1.4, 6.0))
    lay, m = solid(s, tmp_path)
    arcs = [q for q in lay.faces["wall"].face.outline if len(q) > 2]
    assert len(arcs) == 2 and all(abs(q[2]) == pytest.approx(math.tan(math.pi / 8)) for q in arcs)
    # The corner itself is gone: the wall's outline now turns 1.4 and 6 mm short of it.
    pts = np.array([lay.faces["wall"].to3d(*q[:2]) for q in lay.faces["wall"].face.outline])
    top = sorted(pts[np.isclose(pts[:, 2], 30)][:, 0])
    assert top == pytest.approx([1.4, 94.0])
    too_big = Sheet("u", RULE)
    too_big.base(100, 60).flange("back", 30, round=(40.0, 0))
    with pytest.raises(ValueError, match="doesn't fit"):
        too_big.layout()
