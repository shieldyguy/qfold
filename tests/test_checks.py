"""Each check should fail on a design that breaks it on purpose."""
import math

import numpy as np
import pytest

from examples.synth_case import design
from qfold import dfm, kernel
from qfold.assembly import align, translate
from qfold.flat import flat_pattern
from qfold.fold import Face, Flange, Hole, SheetMetalBody, SheetMetalRule, layout

needs_kernel = pytest.mark.skipif(not kernel.KERNEL.exists(), reason="sheetkernel not built")


@needs_kernel
def test_self_intersection_is_caught(tmp_path):
    # Negative corner gap: the cheeks run into the front lip and rear wall.
    case = design.build(design.CaseParams(corner_gap=-0.05), workdir=tmp_path)
    comp = case.design.find("bottom_shell").component
    res = kernel.run({"bodies": [comp.bodies[0].job("shell")]}, tmp_path)
    b = res["bodies"]["shell"]
    assert b["valid"] and b["solids"] == 1  # BRepCheck alone can't see it
    assert b["parts_volume"] - b["volume"] > 1e-3


@needs_kernel
def test_clean_shell_has_no_self_intersection(tmp_path):
    case = design.build(workdir=tmp_path)
    comp = case.design.find("bottom_shell").component
    res = kernel.run({"bodies": [comp.bodies[0].job("shell")]}, tmp_path)
    b = res["bodies"]["shell"]
    assert abs(b["parts_volume"] - b["volume"]) < 1e-9 * b["volume"]


def test_bends_meeting_without_relief_fail():
    rep = dfm.Report()
    dfm.check_layout(design.build_shell_only(design.CaseParams(corner_relief=0.0)), dfm.Rules(), rep)
    c = next(c for c in rep.checks if c.name.endswith("bend corner relief"))
    assert c.status == "fail"


def test_hole_crossing_face_edge_fails():
    rule = SheetMetalRule("t", 1.0, 1.0, 0.5)
    face = Face("f", [(0, 0), (20, 0), (20, 10), (0, 10)], [Hole("circle", (19.5, 5), d=3)])
    rep = dfm.Report()
    dfm.check_layout(layout(SheetMetalBody("p", rule, face)), dfm.Rules(), rep)
    assert next(c for c in rep.checks if "inside their face" in c.name).status == "fail"


@pytest.fixture(scope="module")
def fit_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("fit")


def _failures(case, workdir):
    rep = design.project(case).check_fit(workdir)
    return {c.name: c.detail for c in rep.checks if c.status != "pass"}


@needs_kernel
def test_assembled_case_fits(fit_dir):
    assert _failures(design.build(workdir=fit_dir), fit_dir) == {}


# One part out of place among several: the checks run per part, so the group
# can't pass on the strength of the others. (occurrence, move in its own
# frame, {check: words its detail must contain}).
MISPLACED = [
    ("foot_1", (0, 0, 5), {"feet on base": "foot_1 5.000 mm off",
                           "feet on their base holes": "5.00 mm along"}),
    ("foot_1", (100, 0, 0), {"feet on their base holes": "100.00 mm across"}),
    ("screw_1", (0, 0, 50), {"screw heads on cheeks": "screw_1",
                             "screws on cheek and lip holes": "50.00 mm along"}),
    # The shank still sits in its Ø3.4 hole, 0.2 mm all round; the head is off.
    ("screw_1", (0, 0, 1), {"screw heads on cheeks": "screw_1 0.200 mm off",
                            "screws on cheek and lip holes": "1.00 mm along"}),
    ("screw_1", (0, 0, -0.06), {"screw heads on cheeks": "screw_1 overlaps",
                                "screws on cheek and lip holes": "-0.06 mm along",
                                "no part overlaps another": "screw_1 overlaps the rest"}),
    ("control_board/RV5", (0, 0, -2), {"board parts against panel": "RV5 0.100 mm off",
                                       "board parts on their panel holes": "RV5→P10"}),
    ("control_board/D2", (0.5, 0, 0), {"board parts against panel": "D2 overlaps",
                                       "board parts on their panel holes": "D2→P3",
                                       "no part overlaps another": "control_board overlaps"}),
]


@needs_kernel
@pytest.mark.parametrize("path, move, expect", MISPLACED,
                         ids=[f"{p.split('/')[-1]}{m}" for p, m, _ in MISPLACED])
def test_one_misplaced_part_is_caught(fit_dir, path, move, expect):
    case = design.build(workdir=fit_dir)
    occ = case.design.find(path)
    align(occ, "origin", onto=occ.world() @ translate(move))
    failed = _failures(case, fit_dir)
    assert set(failed) == set(expect), failed
    for name, words in expect.items():
        assert words in failed[name], failed[name]


def _tab_with_hole(N):
    rule = SheetMetalRule("t", 1.0, 1.0, 0.5)
    wall = Face("wall", [(0, 0), (0, 20), (10, 20), (10, 0)])
    root = Face("root", [(0, 0), (40, 0), (40, 20), (0, 20)], [Hole("circle", (10, 15), d=4)],
                [Flange("b", (40, 0), (0, 1), (1, 0), 0, 20, 90.0, wall)])
    return layout(SheetMetalBody("p", rule, root, N=N))


@needs_kernel
@pytest.mark.parametrize("N, hole_x_from", [((0, 0, 1), "right"), ((0, 0, -1), "left")])
def test_flat_pattern_shows_the_outside_face(tmp_path, N, hole_x_from):
    """Outside face = viewed from -N. With N=+z that mirrors the layout, so the
    hole ends up 10 mm from the right edge; with N=-z it stays 10 mm from the
    left. Bends fold away from the viewer: DN."""
    lay = _tab_with_hole(N)
    res = kernel.run({"bodies": [kernel.flat_body(lay, "flat")],
                      "outputs": {"flat_outline": [{"body": "flat", "z": 0.0}]}}, tmp_path)
    fp = flat_pattern(lay, res["flat_outline"]["flat"])
    (c, r), = fp.circles
    width = 50 + math.pi / 2 * (1.0 + 0.5 * 1.0)
    assert fp.size[0] == pytest.approx(width)
    x = fp.size[0] - c[0] if hole_x_from == "right" else c[0]
    assert x == pytest.approx(10.0) and c[1] == pytest.approx(15.0) and r == pytest.approx(2.0)
    assert [b.direction for b in fp.bends] == ["DN"]


@needs_kernel
def test_kernel_errors_carry_their_message(tmp_path):
    with pytest.raises(RuntimeError, match="unknown primitive kind: nope"):
        kernel.run({"bodies": [{"name": "x", "prims": [{"kind": "nope"}]}]}, tmp_path)
    with pytest.raises(ValueError):
        kernel.run({"bodies": [{"name": "x", "prims": [], "v": float("nan")}]}, tmp_path)
