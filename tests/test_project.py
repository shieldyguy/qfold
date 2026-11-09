"""The generic runner, on the pedal box template."""

import json

import numpy as np
import pytest

from examples.pedal_box.__main__ import Params, build, build_tray
from qfold import shops
from qfold.assembly import align, translate
from qfold.fold import Hole, hole_at, layout


def _failed(proj, workdir):
    return {c.name: c.detail for c in proj.check_fit(workdir).checks if c.status != "pass"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("pedal")
    proj = build()
    return proj, proj.build(out, quiet=True), out


def test_pedal_box_builds_and_passes(built):
    proj, rep, out = built
    assert rep.ok, [c for c in rep.checks if c.status == "fail"]
    for f in ("tray.step", "lid.step", "assembly.step", "tray_flat.dxf", "lid_flat.dxf",
              "render_iso.png", "pedal_box_drawing.pdf", "report.json"):
        assert (out / f).stat().st_size > 0, f
    assert len(list((out / "preview").glob("sheet-*.png"))) == 5
    names = {c.name for c in rep.checks}
    assert {"tray: flat pattern matches fold", "no part overlaps another",
            "faces of one part keep their gaps", "tray_flat.dxf reads back"} <= names


def test_report_records_parameters_shop_and_holes(built):
    _, _, out = built
    r = json.loads((out / "report.json").read_text())
    assert r["parameters"]["W"] == "140" and r["parameters"]["shop"] == "jlccnc"
    assert r["sheet_metal_rules"]["tray"]["bend_radius"] == pytest.approx(1.25)
    assert r["shop"]["shop"] == "JLCCNC"
    holes = {h["tag"]: h for h in r["holes"]}
    # Placed with hole_at by finished position: they come back where asked.
    assert (holes["J3"]["x"], holes["J3"]["y"], holes["J3"]["z"]) == (50, 100, 15)
    assert holes["J3"]["facing"] == "+Y back" and holes["J1"]["facing"] == "−Y front"
    assert holes["F1"]["facing"] == "−Z down" and holes["P1"]["facing"] == "+Z up"
    assert (holes["P2"]["x"], holes["P2"]["y"], holes["P2"]["z"]) == (70, 50, 40)


def test_hole_at_places_by_finished_position_and_rejects_points_off_the_face():
    p = Params()
    m = shops.spec(p.shop, p.material, p.thickness).rule
    tray = build_tray(p, m)
    h = hole_at(tray, "right_wall", (p.W, 30, 12), Hole("circle", d=3, tag="X1"))
    st = layout(tray).faces["right_wall"]
    assert np.allclose(st.to3d(*h.c), (p.W, 30, 12))
    hole_at(tray, "right_wall", (p.W - m.T, 60, 12), Hole("circle", d=3, tag="X2"))  # inside face
    with pytest.raises(ValueError, match="isn't on this face"):
        hole_at(tray, "right_wall", (p.W - 5, 30, 12), Hole("circle", d=3))
    with pytest.raises(KeyError, match="front_wall"):
        hole_at(tray, "rear", (0, 0, 0), Hole("circle", d=3))


@pytest.mark.parametrize("path, move, expect", [
    # Lifted: off the lips, and up into the screw heads that stayed put.
    ("lid", (0, 0, 0.5), {"lid rests on the lips", "screws through lid and nut",
                           "screw heads on the lid", "pots against the lid",
                           "no part overlaps another"}),
    # Slid sideways: still on the lips, but its holes cut into every part in them.
    ("lid", (1.0, 0, 0), {"no part overlaps another", "screws through lid and nut",
                           "screw heads on the lid", "pots against the lid", "LED in its hole"}),
    ("foot_3", (0, 0, 2), {"feet on the base"}),
    ("pcb", (0, 0, -1), {"pots on the PCB"}),
])
def test_moving_one_part_fails_the_fits_it_breaks(tmp_path, path, move, expect):
    proj = build()
    assert _failed(proj, tmp_path) == {}
    occ = proj.design.find(path)
    align(occ, "origin", onto=occ.world() @ translate(move))
    assert set(_failed(proj, tmp_path)) == expect


def test_chassis_with_inserted_step_fits(tmp_path):
    from examples.chassis.__main__ import build as chassis
    proj = chassis()
    assert _failed(proj, tmp_path) == {}  # standoffs point inward: on_hole takes either way
    assert proj.design.find("board/RV1").component.name == "pot_9mm"
    occ = proj.design.find("standoff_2")
    align(occ, "origin", onto=occ.world() @ translate((3, 0, 0)))
    assert set(_failed(proj, tmp_path)) == {"standoffs on their holes"}
