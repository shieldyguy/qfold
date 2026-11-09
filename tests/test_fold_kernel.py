"""Adversarial fold tests: parts built to stress fold.py, run through sheetkernel.

Every part is checked three ways, in both states (folded and flat):

* the kernel returns one valid solid;
* folded minus flat volume equals the bend-allowance prediction
  theta*L*T^2*(0.5 - K) summed over the bends (fold.expected_volume_difference);
* the fused volume equals the analytic sum of the pieces the kernel was given:
  plates by shoelace area minus hole areas, times T; sectors theta*L*T*(R + T/2);
  flat strips BA*L*T. A fused body is smaller than the sum of its parts exactly
  when two of them intersect, so this is the self-overlap detector. It needs no
  tolerance beyond the kernel's print precision.

The frame tests at the end need no kernel: they check that every wall frame is
orthonormal, folds toward the material side for a positive angle and away from
it for a negative one, and sits R + T (or R) from the bend axis.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from qfold import design, kernel
from qfold.fold import (Face, Flange, Hole, SheetMetalBody, SheetMetalRule,
                        expected_volume_difference, layout)

RULE = SheetMetalRule("test", thickness=2.0, bend_radius=1.0, k_factor=0.4)
A = RULE.R + RULE.T  # set-back of a 90 degree bend, outer surface to bend axis

needs_kernel = pytest.mark.skipif(not kernel.KERNEL.exists(), reason="sheetkernel not built")


# ------------------------------------------------------------------ analytics

def shoelace(pts) -> float:
    p = np.asarray(pts, float)
    x, y = p[:, 0], p[:, 1]
    return 0.5 * abs(float(x @ np.roll(y, -1) - y @ np.roll(x, -1)))


def hole_area(h: Hole) -> float:
    if h.kind == "circle":
        return math.pi * (h.d / 2) ** 2
    if h.kind == "rrect":
        r = max(0.0, min(h.r, h.w / 2, h.h / 2))
        return h.w * h.h - (4 - math.pi) * r * r
    return shoelace(h.pts)


def analytic_volumes(lay) -> tuple[float, float]:
    """(folded, flat) volume as the sum of the pieces the kernel is asked to fuse."""
    T = lay.body.rule.T
    plates = sum((shoelace(st.face.outline) - sum(hole_area(h) for h in st.face.holes)) * T
                 for st in lay.faces.values())
    sectors = sum(s["angle"] * s["length"] * T * (s["r_in"] + T / 2) for s in lay.sectors)
    strips = sum(b.allowance * (b.bend.v1 - b.bend.v0) * T for b in lay.bends)
    return plates + sectors, plates + strips


def run_both_states(lay, workdir) -> tuple[dict, dict]:
    job = {"bodies": [{"name": "fold", "plates": lay.plates3d, "sectors": lay.sectors},
                      kernel.flat_body(lay, "flat")],
           "outputs": {}}
    res = kernel.run(job, workdir)
    return res["bodies"]["fold"], res["bodies"]["flat"]


# ---------------------------------------------------------------------- parts

def wall(name: str, length: float, width: float, v0: float = 0.0, holes=(), flanges=()) -> Face:
    return Face(name, [(0, v0), (0, v0 + length), (width, v0 + length), (width, v0)],
                list(holes), list(flanges))


def corner(angle: float, radius: float | None = None, root: dict | None = None,
           wall_holes=()) -> SheetMetalBody:
    """A base with one wall folded off its x = 100 - A edge."""
    base = Face("base", [(0, 0), (100 - A, 0), (100 - A, 50), (0, 50)], flanges=[
        Flange("b", (100 - A, 0), (0, 1), (1, 0), 0, 50, angle,
               wall("wall", 50, 10, holes=wall_holes), radius)])
    return SheetMetalBody("p", RULE, base, **(root or {}))


def nested(angle2: float) -> SheetMetalBody:
    """Wall off the base, then a return flange off the wall's far edge."""
    ret = wall("ret", 50, 8)
    w = Face("wall", [(0, 0), (0, 50), (20 - A, 50), (20 - A, 0)],
             flanges=[Flange("b2", (20 - A, 0), (0, 1), (1, 0), 0, 50, angle2, ret)])
    base = Face("base", [(0, 0), (100 - A, 0), (100 - A, 50), (0, 50)],
                flanges=[Flange("b", (100 - A, 0), (0, 1), (1, 0), 0, 50, 90.0, w)])
    return SheetMetalBody("p", RULE, base)


def perpendicular() -> SheetMetalBody:
    """A flange folded sideways off a wall's v-edge: two bends at right angles."""
    side = wall("side", 30, 8)
    w = Face("wall", [(0, 0), (0, 50 - A), (30, 50 - A), (30, 0)],
             flanges=[Flange("b2", (0, 50 - A), (1, 0), (0, 1), 0, 30, 90.0, side)])
    base = Face("base", [(0, 0), (100 - A, 0), (100 - A, 50), (0, 50)],
                flanges=[Flange("b", (100 - A, 0), (0, 1), (1, 0), 0, 50, 90.0, w)])
    return SheetMetalBody("p", RULE, base)


def slanted(angle: float) -> SheetMetalBody:
    """Bend along a parent edge at 30 degrees to the u axis."""
    th = math.radians(30)
    d = (math.cos(th), math.sin(th))
    o = (math.sin(th), -math.cos(th))
    L = 60.0
    p0 = (10.0, 0.0)
    p1 = (p0[0] + d[0] * L, p0[1] + d[1] * L)
    base = Face("base", [p0, p1, (p1[0], 80), (0, 80), (0, 0)],
                flanges=[Flange("b", p0, d, o, 0, L, angle, wall("w", L, 12))])
    return SheetMetalBody("p", RULE, base)


def partial_span() -> SheetMetalBody:
    """A tab on the middle 20 mm of a 50 mm edge."""
    base = Face("base", [(0, 0), (100 - A, 0), (100 - A, 50), (0, 50)],
                flanges=[Flange("b", (100 - A, 0), (0, 1), (1, 0), 15, 35, 90.0,
                                wall("tab", 20, 10, v0=15))])
    return SheetMetalBody("p", RULE, base)


def wider_than_bend() -> SheetMetalBody:
    """Wall 5 mm wider than its bend at both ends, no relief."""
    base = Face("base", [(0, 0), (100 - A, 0), (100 - A, 50), (0, 50)],
                flanges=[Flange("b", (100 - A, 0), (0, 1), (1, 0), 0, 50, 90.0,
                                wall("wide", 60, 10, v0=-5))])
    return SheetMetalBody("p", RULE, base)


def two_walls() -> SheetMetalBody:
    """Walls on two adjacent base edges, full length, meeting at a corner."""
    w1 = wall("w1", 50, 30)
    w2 = wall("w2", 100 - A, 30)
    base = Face("base", [(0, 0), (100 - A, 0), (100 - A, 50), (0, 50)], flanges=[
        Flange("b1", (100 - A, 0), (0, 1), (1, 0), 0, 50, 90.0, w1),
        Flange("b2", (0, 0), (1, 0), (0, -1), 0, 100 - A, 90.0, w2)])
    return SheetMetalBody("p", RULE, base)


def with_holes() -> SheetMetalBody:
    """Round, rotated rounded-rectangle and polygon holes on base and wall."""
    base_holes = [Hole("circle", (30, 25), d=6.0),
                  Hole("rrect", (60, 25), w=20, h=6, r=3, rot=30.0),
                  Hole("poly", pts=[(10, 10), (20, 10), (15, 18)])]
    base = Face("base", [(0, 0), (100 - A, 0), (100 - A, 50), (0, 50)], base_holes, [
        Flange("b", (100 - A, 0), (0, 1), (1, 0), 0, 50, 90.0,
               wall("wall", 50, 14, holes=[Hole("circle", (8, 25), d=4.0)]))])
    return SheetMetalBody("p", RULE, base)


CASES = {
    **{f"angle_{a:g}": (lambda a=a: corner(a))
       for a in (90, -90, 45, -45, 135, -135, 170, -170, 10, 1, 179, 179.9)},
    "radius_0": lambda: corner(90, radius=0.0),
    "radius_0.2": lambda: corner(90, radius=0.2),
    "radius_20": lambda: corner(90, radius=20.0),
    "root_N_down": lambda: corner(90, root=dict(N=(0, 0, -1))),
    "root_yz_plane": lambda: corner(90, root=dict(U=(0, 1, 0), V=(0, 0, 1), N=(1, 0, 0))),
    "root_left_handed": lambda: corner(90, root=dict(U=(0, 1, 0), V=(1, 0, 0), N=(0, 0, 1))),
    "root_offset_rotated": lambda: corner(
        -90, root=dict(origin=(10, 20, 30), U=(0, 0, 1), V=(1, 0, 0), N=(0, 1, 0))),
    "nested_90": lambda: nested(90), "nested_-90": lambda: nested(-90),
    "nested_60": lambda: nested(60), "nested_-120": lambda: nested(-120),
    "perpendicular_bends": perpendicular,
    "slanted_edge": lambda: slanted(90), "slanted_edge_neg": lambda: slanted(-90),
    "partial_span": partial_span,
    "wall_wider_than_bend": wider_than_bend,
    "two_walls_meeting": two_walls,
    "holes_all_kinds": with_holes,
}


@pytest.fixture(params=sorted(CASES), ids=sorted(CASES))
def body(request) -> SheetMetalBody:
    return CASES[request.param]()


# ------------------------------------------------------------------ kernel tests

@needs_kernel
def test_both_states_are_one_valid_solid(body, tmp_path):
    fold, flat = run_both_states(layout(body), tmp_path)
    assert fold["valid"] and fold["solids"] == 1, fold
    assert flat["valid"] and flat["solids"] == 1, flat


@needs_kernel
def test_fused_volume_equals_sum_of_pieces(body, tmp_path):
    """No piece of either state overlaps another, and nothing is lost in the fuse."""
    lay = layout(body)
    fold, flat = run_both_states(lay, tmp_path)
    exp_fold, exp_flat = analytic_volumes(lay)
    # 1e-3 mm^3 covers the kernel's current %.10g print precision.
    assert fold["volume"] == pytest.approx(exp_fold, abs=1e-3)
    assert flat["volume"] == pytest.approx(exp_flat, abs=1e-3)


@needs_kernel
def test_folded_minus_flat_matches_bend_allowance(body, tmp_path):
    lay = layout(body)
    fold, flat = run_both_states(lay, tmp_path)
    assert fold["volume"] - flat["volume"] == pytest.approx(
        expected_volume_difference(lay), abs=1e-3)


@needs_kernel
def test_self_overlap_shows_in_fused_volume(tmp_path):
    """The synth shell with its cheeks driven 0.05 mm into the lip and rear wall:
    a same-body interference of about 11 mm^3. The fused folded volume falls
    short of the sum of its plates and sectors by that amount, while the flat
    pattern (which does not overlap) still matches its sum. This is the signal
    a self-interference check must use; the folded-minus-flat tolerance alone
    (1e-4 * V, about 14 mm^3) does not see it."""
    good = design.build(design.CaseParams(), workdir=tmp_path).shell
    bad = design.build(design.CaseParams(corner_gap=-0.05), workdir=tmp_path).shell
    g_fold, g_flat = run_both_states(good, tmp_path / "good")
    b_fold, b_flat = run_both_states(bad, tmp_path / "bad")
    assert g_fold["volume"] == pytest.approx(analytic_volumes(good)[0], abs=1e-3)
    assert b_flat["volume"] == pytest.approx(analytic_volumes(bad)[1], abs=1e-3)
    missing = analytic_volumes(bad)[0] - b_fold["volume"]
    assert 5.0 < missing < 20.0, missing
    assert b_fold["valid"] and b_fold["solids"] == 1  # the fuse hides the overlap


# ----------------------------------------------------------------- input checks

@pytest.mark.parametrize("angle", [0.0, 180.0, -180.0, 360.0])
def test_degenerate_angles_are_rejected(angle):
    with pytest.raises(ValueError, match="angle"):
        layout(corner(angle))


def test_non_orthonormal_bend_axes_are_rejected():
    base = Face("base", [(0, 0), (10, 0), (10, 10), (0, 10)], flanges=[
        Flange("b", (10, 0), (0, 1), (1, 1), 0, 10, 90.0, wall("w", 10, 5))])
    with pytest.raises(ValueError, match="orthonormal"):
        layout(SheetMetalBody("p", RULE, base))


def test_duplicate_face_names_are_rejected():
    base = Face("base", [(0, 0), (10, 0), (10, 10), (0, 10)], flanges=[
        Flange("b", (10, 0), (0, 1), (1, 0), 0, 10, 90.0, wall("base", 10, 5))])
    with pytest.raises(ValueError, match="duplicate"):
        layout(SheetMetalBody("p", RULE, base))


# ------------------------------------------------------------------ frame tests

def test_wall_frames_are_rigid_and_fold_the_right_way(body):
    """For every bend: the wall frame is orthonormal and keeps the parent's
    handedness; the wall tilts toward +N (the material) for a positive angle and
    toward -N for a negative one, by exactly the bend angle; its origin lies on
    the bend's outer arc (R + T from the axis for a positive bend, where the
    reference surface is the outside, R for a negative one, where it is the
    inside); and the flat development is a rigid motion of each face."""
    lay = layout(body)
    T = body.rule.T
    for bs in lay.bends:
        parent, child = lay.faces[bs.parent], lay.faces[bs.bend.wall.name]
        th = math.radians(abs(bs.bend.angle))
        sign = 1.0 if bs.bend.angle > 0 else -1.0
        M = np.column_stack([child.U, child.V, child.N])
        np.testing.assert_allclose(M.T @ M, np.eye(3), atol=1e-12)
        hand_parent = np.sign(np.cross(parent.U, parent.V) @ parent.N)
        hand_child = np.sign(np.cross(child.U, child.V) @ child.N)
        d2 = np.array(bs.bend.d)
        o2 = np.array(bs.bend.o)
        det = o2[0] * d2[1] - o2[1] * d2[0]  # child (u, v) = (o, d) in parent coords
        assert hand_child == hand_parent * np.sign(det)
        assert child.N @ parent.N == pytest.approx(math.cos(th), abs=1e-12)
        assert child.U @ parent.N == pytest.approx(sign * math.sin(th), abs=1e-12)
        r_ref = bs.R + T if bs.bend.angle > 0 else bs.R
        assert np.linalg.norm(child.O - bs.axis_point) == pytest.approx(r_ref, abs=1e-9)
        # 2D: the development frame keeps the same handedness as the 3D frame,
        # with the root's orientation chosen so the view is from +N.
        hand_2d = np.sign(child.U2[0] * child.V2[1] - child.U2[1] * child.V2[0])
        assert hand_2d == hand_child
        assert bs.allowance == pytest.approx(th * (bs.R + body.rule.K * T))
