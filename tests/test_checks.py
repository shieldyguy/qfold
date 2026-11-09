"""Each check should fail on a design that breaks it on purpose."""
import math

import numpy as np
import pytest

from qfold import design, dfm, kernel
from qfold.assembly import translate
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


@needs_kernel
def test_screw_that_misses_its_cheek_is_caught(tmp_path):
    case = design.build(workdir=tmp_path)
    d = case.design
    screw = d.find("screw_1")
    screw.transform = screw.transform @ translate((0, 0, 1.0))  # lift off the cheek
    screw._src.transform = screw.transform
    job = d.job()
    job["outputs"] = {"pairs": [{"a": d.group("screw_1"), "b": d.group("bottom_shell")}]}
    pr = kernel.run(job, tmp_path)["pairs"][0]
    assert pr["interference_volume"] < 1e-9  # "no overlap" alone would pass
    # The shank still sits in the Ø3.4 hole (0.2 mm all round), but the head
    # no longer touches the cheek, so the seated rule fails it.
    assert pr["min_distance"] == pytest.approx(0.2, abs=1e-6)
    assert pr["min_distance"] > dfm.Rules().seated


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
