"""Shop data: the tables hold together, and qfold bends the way the shop does."""

import math

import pytest

from examples.synth_case import design
from qfold import dfm, shops
from qfold.fold import Face, Flange, SheetMetalBody, layout

IN = shops.IN


def _scs_rows():
    data = shops.load("sendcutsend")
    for name in data["materials"]:
        for row in shops._rows(data, name):
            yield name, row


SCS = list(_scs_rows())
# Rows where the printed K (two decimals) doesn't reproduce the printed bend
# deduction; re-read from the page on 2026-11-09. Data edits that add to this
# set are probably transcription errors.
K_DISAGREES = {("5052 Aluminum", 0.063), ("Brass", 0.187), ("Brass", 0.250),
               ("Titanium Grade 2", 0.040)}


def test_sendcutsend_rows_are_consistent():
    off = set()
    for name, r in SCS:
        T, R, K = r["T"], r["R"], r["K"]
        bd = 2 * (R + T) - math.pi / 2 * (R + K * T)
        if abs(bd - r["BD90"]) > 0.003:
            off.add((name, T))
        # Relief depth is R + T + 0.020 on every row.
        assert r["relief_depth"] == pytest.approx(R + T + 0.020, abs=0.0015), (name, T)
        assert 0.25 < K < 0.5 and R > 0 and r["die"] >= 4 * T, (name, T)
    assert off == K_DISAGREES


# SendCutSend publishes the minimum flange both formed (outside to edge) and
# flat (bend centre line to edge) for these; the formed one is converted.
FLAT_FLANGE = {("5052 Aluminum", 0.063): 0.255, ("5052 Aluminum", 0.250): 1.150,
               ("Mild Steel", 0.104): 0.368, ("304 Stainless Steel", 0.100): 0.620}


@pytest.mark.parametrize("key", FLAT_FLANGE, ids=lambda k: f"{k[0]} {k[1]}")
def test_formed_flange_converts_to_the_published_flat_one(key):
    s = shops.spec("sendcutsend", key[0], key[1] * IN)
    ba90 = 2 * (s.row["R"] + s.row["T"]) - s.row["BD90"]
    # rules.min_flange is the flat wall past the bend tangent line.
    assert s.rules.min_flange / IN + ba90 / 2 == pytest.approx(FLAT_FLANGE[key], abs=0.002)


@pytest.mark.parametrize("name, row", SCS, ids=[f"{n} {r['T']}" for n, r in SCS])
def test_flat_pattern_uses_the_shops_bend_deduction(name, row):
    s = shops.spec("sendcutsend", name, row["T"] * IN)
    wall = Face("wall", [(0, 0), (30, 0), (30, 40), (0, 40)])
    root = Face("root", [(0, 0), (50, 0), (50, 40), (0, 40)],
                flanges=[Flange("b", (50, 0), (0, 1), (1, 0), 0, 40, 90, wall)])
    bend = layout(SheetMetalBody("p", s.rule, root)).bends[0]
    # Outside legs A = 50 + R + T and B = 30 + R + T flatten to A + B - BD.
    A = B = s.rule.R + s.rule.T
    flat = 50 + bend.allowance + 30
    assert flat == pytest.approx(50 + A + 30 + B - row["BD90"] * IN, abs=1e-9)
    assert bend.R == pytest.approx(row["R"] * IN)


@pytest.mark.parametrize("shop, material, T", [("sendcutsend", "5052 Aluminum", 1.6),
                                               ("jlccnc", "Aluminum 5052", 1.5)])
def test_synth_case_meets_shop_limits(shop, material, T):
    s = shops.spec(shop, material, T)
    p = design.CaseParams(rule=s.rule)
    for lay in (design.build_shell_only(p), design.build_panel(p)):
        rep = dfm.Report()
        dfm.check_layout(lay, s.rules, rep)
        assert rep.ok, [c for c in rep.checks if c.status == "fail"]


def test_sendcutsend_limits_in_mm():
    s = shops.spec("sendcutsend", "5052 Aluminum", 1.6)
    assert s.thickness == pytest.approx(1.6002)
    assert s.rule.R == pytest.approx(0.889)
    assert s.rule.K == pytest.approx(0.455, abs=0.001)  # from BD90, printed 0.42
    assert any("printed K 0.42" in n for n in s.notes)
    # Half the 0.472 in die from the bend centre line, less half the bend.
    assert s.rules.hole_to_bend == pytest.approx((0.472 - 0.100) / 2 * IN)
    assert s.rules.min_flange == pytest.approx((0.303 - 0.098) * IN)
    assert s.rules.min_hole == pytest.approx(0.5 * 0.063 * IN)
    assert s.rules.max_sheet == pytest.approx((44 * IN, 30 * IN))


def test_unpublished_bend_data_needs_to_be_supplied():
    o = shops.spec("oshcut", "5052-H32 Aluminum", 1.6)
    assert o.rule is None and "app.oshcut.com" in o.notes[0]
    assert o.rules.min_hole == pytest.approx(0.032 * IN)
    assert o.rules.hole_web == pytest.approx(0.021 * IN)
    o = shops.spec("oshcut", "5052-H32 Aluminum", 1.6, bend_radius=1.0, k_factor=0.4)
    assert (o.rule.T, o.rule.R, o.rule.K) == (pytest.approx(1.6002), 1.0, 0.4)


def test_jlccnc_radius_from_outer_corner_table():
    j = shops.spec("jlccnc", "Aluminum 5052", 2.0)
    # Outer corner R3.7 at 2 mm; the inside radius is T less.
    assert (j.rule.T, j.rule.R) == (2.0, pytest.approx(1.7))
    assert j.rule.K == 0.415 and any("only affects qfold's own flat" in n for n in j.notes)
    assert any("actual sheet thickness 1.95" in n for n in j.notes)
    # Published flange 10 mm, outside to edge: the flat wall past the bend is R + T less.
    assert j.rules.min_flange == pytest.approx(10 - 3.7)
    assert (j.rules.hole_to_bend, j.rules.hole_to_edge, j.rules.min_hole) == (4.0, 3.0, 1.0)
    # 1 mm: the 3 mm-from-bend rule beats 2 T.
    assert shops.spec("jlccnc", "Aluminum 5052", 1.0).rules.hole_to_bend == 3.0
    # 4 mm has no flange table entry: V/2 + 2 with V = 6 T, outside to edge.
    assert shops.spec("jlccnc", "Cold Rolled Steel", 4.0).rules.min_flange == \
        pytest.approx(6 * 4 / 2 + 2 - (4 + 4))
    # 0.8 mm is stocked but its radius isn't published.
    assert shops.spec("jlccnc", "Aluminum 5052", 0.8).rule is None
    k = shops.spec("jlccnc", "Aluminum 5052", 0.8, bend_radius=0.8, k_factor=0.4).rule
    assert (k.R, k.K) == (0.8, 0.4)


@pytest.mark.parametrize("args, kw, words", [
    (("acme", "x", 1.0), {}, "known: sendcutsend"),
    (("sendcutsend", "5052", 1.6), {}, "'5052 Aluminum'"),
    (("sendcutsend", "5052 Aluminum", 1.5), {}, "1.60 mm (0.063 in)"),
    (("sendcutsend", "5052 Aluminum", 1.6), {"bend_radius": 1, "k_factor": 0.4}, "fixed radius"),
    (("oshcut", "5052-H32 Aluminum", 12.7), {}, "doesn't bend"),
    (("oshcut", "5052-H32 Aluminum", 1.6), {"bend_radius": 1}, "together"),
    (("jlccnc", "Aluminum 5052", 1.6), {}, "1.5 mm, 2 mm"),
    (("jlccnc", "Aluminum 6061", 1.5), {}, "'Aluminum 5052'"),
])
def test_lookup_errors_name_the_options(args, kw, words):
    with pytest.raises(ValueError) as e:
        shops.spec(*args, **kw)
    assert words in str(e.value)
