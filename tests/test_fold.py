import math

import numpy as np
import pytest

from qfold import design, kernel
from qfold.fold import Bend, Face, Material, Part, expected_volume_difference, layout

MAT = Material("test", T=2.0, R=1.0, K=0.4)


def box_corner(angle=90.0):
    a = MAT.R + MAT.T
    wall = Face("wall", [(0, 0), (0, 50), (10, 50), (10, 0)])
    base = Face("base", [(0, 0), (100 - a, 0), (100 - a, 50), (0, 50)],
                bends=[Bend("b", (100 - a, 0), (0, 1), (1, 0), 0, 50, angle, wall)])
    return layout(Part("p", MAT, base))


def test_up_bend_puts_outer_face_at_set_back():
    lay = box_corner(90)
    w = lay.faces["wall"]
    # Outer (reference) surface of the wall at x = 100, starting at z = R + T.
    np.testing.assert_allclose(w.O, [100, 0, MAT.R + MAT.T], atol=1e-12)
    np.testing.assert_allclose(w.U, [0, 0, 1], atol=1e-12)
    np.testing.assert_allclose(w.N, [-1, 0, 0], atol=1e-12)


def test_down_bend_folds_away_from_material():
    lay = box_corner(-90)
    w = lay.faces["wall"]
    a = 100 - MAT.R - MAT.T
    np.testing.assert_allclose(w.O, [a + MAT.R, 0, -MAT.R], atol=1e-12)
    np.testing.assert_allclose(w.U, [0, 0, -1], atol=1e-12)
    np.testing.assert_allclose(w.N, [1, 0, 0], atol=1e-12)


def test_flat_strip_is_bend_allowance_wide():
    lay = box_corner(90)
    ba = math.pi / 2 * (MAT.R + MAT.K * MAT.T)
    assert lay.bends[0].allowance == pytest.approx(ba)
    wall2d = lay.faces["wall"]
    np.testing.assert_allclose(wall2d.O2, [100 - MAT.R - MAT.T + ba, 0], atol=1e-12)


def test_expected_volume_difference():
    lay = box_corner(90)
    assert expected_volume_difference(lay) == pytest.approx(
        math.pi / 2 * 50 * MAT.T ** 2 * (0.5 - MAT.K))


@pytest.mark.skipif(not kernel.KERNEL.exists(), reason="sheetkernel not built")
def test_kernel_flat_matches_fold(tmp_path):
    case = design.build()
    job = {"bodies": [kernel.folded_body(case.shell), kernel.flat_body(case.shell)],
           "outputs": {}}
    res = kernel.run(job, tmp_path)
    fold, flat = res["bodies"]["bottom_shell"], res["bodies"]["bottom_shell_flat"]
    assert fold["valid"] and fold["solids"] == 1
    assert fold["volume"] - flat["volume"] == pytest.approx(
        expected_volume_difference(case.shell), rel=1e-4)
