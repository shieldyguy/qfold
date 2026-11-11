import numpy as np
import pytest

from examples.synth_case import design
from qfold import kernel
from qfold.assembly import (Component, Design, align, frame, insert_step, mismatch, rigid_inv,
                            rot_x, rot_z, translate)


def test_align_puts_ref_on_target_with_offset_angle_flip():
    part = Component("part", refs={"pin": frame((5, 0, 2), (0, 1, 0), (1, 0, 0))})
    d = Design("t")
    occ = d.add(part, transform=translate((100, 0, 0)))
    target = frame((10, 20, 30), (0, 0, 1), (0, 1, 0))
    align(occ, "pin", onto=target, offset=(1, 2, 3), angle=30, flip=True)
    want = target @ translate((1, 2, 3)) @ rot_z(30) @ rot_x(180)
    np.testing.assert_allclose(occ.ref("pin"), want, atol=1e-12)
    np.testing.assert_allclose(rigid_inv(occ.world()) @ occ.world(), np.eye(4), atol=1e-12)


def test_align_by_a_child_occurrence_inside_a_moved_parent():
    leaf = Component("leaf")
    sub = Component("sub")
    sub.add(leaf, "L1", translate((3, 4, 0)))
    holder = Component("holder")
    d = Design("t")
    h = d.add(holder, transform=translate((50, 0, 0)))
    s = h.component.add(sub, "S", translate((0, 0, 7)))
    s_occ = h.find("S")
    target = frame((1, 1, 1), (1, 0, 0), (0, 1, 0))
    align(s_occ, "L1", onto=target)
    np.testing.assert_allclose(s_occ.ref("L1"), target, atol=1e-12)
    assert mismatch(d.find("holder/S/L1").ref(), target)["radial"] < 1e-9


@pytest.mark.skipif(not kernel.KERNEL.exists(), reason="sheetkernel not built")
def test_step_insert_round_trip(tmp_path):
    p = design.CaseParams()
    src = design.make_board(p)
    step = tmp_path / "board.step"
    design.write_board_step(p, step, tmp_path)
    d = Design("t")
    occ = insert_step(d, step, kernel.inspect_step(step, tmp_path), name="board")
    names = sorted(o.name for o in occ.children())
    assert names == sorted(o.name for o in src.top())
    for o in src.top():
        np.testing.assert_allclose(d.find(f"board/{o.name}").world(), o.world(), atol=1e-9)
    # Shared prototypes stay shared: ten pots, one component.
    pots = {d.find(f"board/RV{i}").component.key for i in range(1, 11)}
    assert len(pots) == 1


@pytest.mark.skipif(not kernel.KERNEL.exists(), reason="sheetkernel not built")
def test_misplaced_board_part_is_caught(tmp_path):
    p = design.CaseParams()
    step = tmp_path / "board.step"
    design.write_board_step(p, step, tmp_path, nudge={"RV5": (0.5, 0.0)})
    case = design.build(p, board_step=step, workdir=tmp_path)
    d = case.design
    off = {}
    for part, hole in case.mounts:
        h_path, _, h_ref = hole.partition(":")
        off[part.split("/")[-1]] = mismatch(d.find(part).ref(), d.find(h_path).ref(h_ref))["radial"]
    assert off["RV5"] == pytest.approx(0.5, abs=1e-9)
    assert max(v for k, v in off.items() if k != "RV5") < 1e-9


def test_extruded_profile_stand_in(tmp_path):
    from qfold.assembly import Design, extrude, prim_component
    d = Design("t")  # an L drawn in (y, z), 10 long in x
    d.add(prim_component("l", [extrude([(0, 0), (4, 0), (4, 1), (1, 1), (1, 3), (0, 3)], 10.0,
                                       x=(0, 1, 0), y=(0, 0, 1))]), "l")
    job = d.job()
    job["outputs"] = {"measure": [{"name": "l", "geom": d.group("l")}]}
    m = kernel.run(job, tmp_path)["measure"]["l"]
    assert m["volume"] == pytest.approx(60.0) and m["bbox"] == pytest.approx([0, 0, 0, 10, 4, 3])
