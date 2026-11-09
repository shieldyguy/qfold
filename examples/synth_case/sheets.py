"""The synth case's own part sheets: shell views with hole tables per wall,
the shell's flat pattern, and the panel. The assembly, holes and checks sheets
are qfold's defaults (qfold/sheets.py)."""

from __future__ import annotations

from qfold.drawing import Sheet, View, dim_h, dim_v, fmt_len, frame, table, tag
from qfold.flat import FlatPattern
from qfold.sheets import BEND_COLS, bend_rows, common_notes, draw_flat, hole_cols, material_text

from .design import Case


def part_sheets(case: Case, b) -> list[Sheet]:
    return [sheet_shell_views(case, b),
            sheet_flat(case, b, b.flats["bottom_shell"], "Bottom shell", b.tags["bottom_shell"],
                       0.5, ["Corner reliefs and closed corners are in the DXF."]),
            sheet_panel(case, b, b.flats["top_panel"], b.tags["top_panel"])]


def _items(lay, *faces):
    """(tag, face state, hole) for every hole on the named faces."""
    return [(h.tag, lay.faces[f], h) for f in faces for h in lay.faces[f].face.holes]


def _hole_rows(view: View, items, coord):
    """items: list of (tag, face_state, hole). coord(p3) -> (a, b) table coordinates."""
    rows = []
    for t, st, h in items:
        a, b = coord(st.to3d(*h.c))
        rows.append([t, h.label, fmt_len(round(a, 2)), fmt_len(round(b, 2)), h.size_text()])
    return rows


def _tag_holes(sh: Sheet, view: View, items):
    for t, st, h in items:
        x, y = view.P3(st.to3d(*h.c))
        r = (h.d / 2 if h.kind == "circle" else max(h.w, h.h) / 2) * view.scale
        tag(sh, x, y, r, t)


# ---------------------------------------------------------------------- sheets

def _sheet(case: Case, occurrence: str):
    """(component, body) of a sheet metal occurrence."""
    comp = case.design.find(occurrence).component
    return comp, comp.bodies[0]


def _shell_dims(case: Case, res: dict) -> dict:
    """Overall shell dimensions measured by the kernel, and the cheek height at
    the front taken from the cheek's own top edge, extended to y = 0."""
    bb = res["bodies"][case.design.proto_name(*_sheet(case, "bottom_shell"))]["bbox"]
    st = case.shell.faces["cheek_right"]
    (y1, z1), (y2, z2) = ((p[1], p[2]) for p in (st.to3d(*st.face.outline[4]),
                                                 st.to3d(*st.face.outline[5])))
    hf = z2 + (bb[1] - y2) * (z1 - z2) / (y1 - y2) - bb[2]
    return {"x0": bb[0], "y0": bb[1], "z0": bb[2], "W": bb[3] - bb[0], "D": bb[4] - bb[1],
            "Hb": bb[5] - bb[2], "Hf": hf}


def sheet_shell_views(case: Case, b) -> Sheet:
    res = b.res
    lay = case.shell
    s = 0.5
    sh = Sheet()
    frame(sh, "Bottom shell: views", "BOTTOM SHELL", None, None, material_text(case.params.rule),
          common_notes(b) + ["Third-angle projection. Hole positions from outer faces."],
          "1:2")
    vw = res["views"]
    top = View(vw["bottom_shell_top"], (0, 0, 1), (1, 0, 0), s)
    front = View(vw["bottom_shell_front"], (0, -1, 0), (1, 0, 0), s)
    right = View(vw["bottom_shell_right"], (1, 0, 0), (0, 1, 0), s)
    back = View(vw["bottom_shell_back"], (0, 1, 0), (-1, 0, 0), s)

    top.x, top.y = 32, 24
    front.x, front.y = 32, top.y + top.size[1] + 16
    right.x, right.y = front.x + front.size[0] + 14, front.y + front.size[1] - right.size[1]
    back.x, back.y = 32, front.y + front.size[1] + 34
    top.draw(sh, label="TOP")
    front.draw(sh, label="FRONT")
    right.draw(sh, label="RIGHT")
    back.draw(sh, label="REAR (viewed from behind)")

    # Overall sizes come from the kernel's bounding box of the solid.
    sd = _shell_dims(case, res)
    W, D, Hb, Hf = sd["W"], sd["D"], sd["Hb"], sd["Hf"]
    dim_h(sh, top.P3((0, D, 0)), top.P3((W, D, 0)), top.y - 5, W)
    dim_v(sh, top.P3((0, 0, 0)), top.P3((0, D, 0)), top.x - 7, D)
    h_front = lay.faces["front_lip"].to3d(lay.faces["front_lip"].face.outline[4][0], 0)[2]
    dim_v(sh, front.P3((0, 0, 0)), front.P3((0, 0, h_front)), front.x - 7, h_front)
    dim_v(sh, right.P3((W, 0, 0)), right.P3((W, 0, Hf)), right.x - 6, Hf)
    dim_v(sh, right.P3((W, D, 0)), right.P3((W, D, Hb)), right.x + right.size[0] + 7, Hb)
    dim_h(sh, right.P3((W, 0, 0)), right.P3((W, D, 0)), right.y + right.size[1] + 12, D,
          above=False)
    dim_v(sh, back.P3((W, D, 0)), back.P3((W, D, Hb)), back.x - 7, Hb)

    base_items = _items(lay, "base")
    back_items = _items(lay, "back_wall")
    side_items = _items(lay, "cheek_right", "cheek_left")
    _tag_holes(sh, top, base_items)
    _tag_holes(sh, back, back_items)
    _tag_holes(sh, right, _items(lay, "cheek_right"))

    x = 302
    y = table(sh, x, 24, hole_cols("X", "Y"),
              _hole_rows(top, base_items, lambda q: (q[0], q[1])),
              title="Base holes (top view, from front-left outer corner)")
    y = table(sh, x, y + 9, hole_cols("X", "Z"),
              _hole_rows(back, back_items, lambda q: (W - q[0], q[2])),
              title="Rear wall holes (from rear view lower-left)")
    table(sh, x, y + 9, hole_cols("Y", "Z"),
          _hole_rows(right, side_items, lambda q: (q[1], q[2])),
          title="Cheek holes (from front, base)")
    return sh


def sheet_flat(case: Case, b, fp: FlatPattern, part_label: str, tags: dict[str, str],
               scale: float, extra_notes: list[str]) -> Sheet:
    sh = Sheet()
    frame(sh, f"{part_label}: flat pattern", part_label.upper(), None, None,
          material_text(case.params.rule),
          common_notes(b) + extra_notes + [
              "Outside face shown. DN = bend away from viewer. DXF is the cutting master."],
          "1:2" if scale == 0.5 else f"1:{1 / scale:g}")
    w, h = fp.size[0] * scale, fp.size[1] * scale
    x0 = max(30.0, (300 - w) / 2 + 14)
    draw_flat(sh, fp, x0, 26, scale, f"FLAT PATTERN  {fmt_len(round(fp.size[0], 2))} × "
                                   f"{fmt_len(round(fp.size[1], 2))}", tags)
    table(sh, 24, 26 + h + 30, BEND_COLS, bend_rows(fp, tags), title="Bends")
    return sh


def sheet_panel(case: Case, b, fp: FlatPattern, tags: dict[str, str]) -> Sheet:
    res = b.res
    lay = case.panel
    s = 0.5
    sh = Sheet()
    frame(sh, "Top panel", "TOP PANEL", None, None, material_text(case.params.rule),
          common_notes(b) + ["Third-angle projection. Press PEM nuts from inside face."],
          "1:2")
    vw = res["views"]
    top = View(vw["top_panel_top"], (0, 0, 1), (1, 0, 0), s)
    front = View(vw["top_panel_front"], (0, -1, 0), (1, 0, 0), s)
    right = View(vw["top_panel_right"], (1, 0, 0), (0, 1, 0), s)
    top.x, top.y = 30, 22
    front.x, front.y = 30, top.y + top.size[1] + 12
    right.x, right.y = top.x + top.size[0] + 18, top.y
    top.draw(sh, label="TOP (outside face)")
    front.draw(sh, label="FRONT EDGE")
    right.draw(sh, label="RIGHT")

    st = lay.faces["panel"]
    Wp = max(p[0] for p in st.face.outline)
    Sp = max(p[1] for p in st.face.outline)
    dim_h(sh, top.P3((0, Sp, 0)), top.P3((Wp, Sp, 0)), top.y - 4.5, Wp)
    dim_v(sh, top.P3((0, 0, 0)), top.P3((0, Sp, 0)), top.x - 7, Sp)
    lip = lay.faces["lip_right"]
    depth = -lip.to3d(lip.face.outline[2][0], 0)[2]
    dim_v(sh, front.P3((Wp, 0, 0)), front.P3((Wp, 0, -depth)), front.x + front.size[0] + 6, depth)

    items = _items(lay, "panel")
    lip_items = _items(lay, "lip_right", "lip_left")
    _tag_holes(sh, top, items)
    _tag_holes(sh, right, _items(lay, "lip_right"))

    draw_flat(sh, fp, 30, front.y + front.size[1] + 22, s,
          f"FLAT PATTERN  {fmt_len(round(fp.size[0], 2))} × {fmt_len(round(fp.size[1], 2))}", tags)
    y = table(sh, 302, 22, hole_cols("X", "Y"),
              _hole_rows(top, items, lambda q: (q[0], q[1])),
              row_h=3.9, size=2.15, title="Panel holes (from front-left corner)")
    y = table(sh, 302, y + 8, hole_cols("Y", "Z↓"),
              _hole_rows(right, lip_items, lambda q: (q[1], -q[2])),
              row_h=3.9, size=2.15, title="Lip holes (Z below top face)")
    table(sh, 302, y + 8, [(c[0], c[1] * 0.9, c[2]) for c in BEND_COLS[:6]],
          [r[:6] for r in bend_rows(fp, tags)], row_h=3.9, size=2.15, title="Bends")
    return sh
