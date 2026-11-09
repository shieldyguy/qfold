"""End-to-end build of the synth case: model, check, export, render, draw.

    python -m qfold.pipeline [out_dir]

Writes STEP (each part and the assembly), flat-pattern DXFs, an STL per part,
a PNG render, a 4-sheet PDF drawing with PNG previews, and report.json.
"""

from __future__ import annotations

import io
import json
import math
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np

from . import design, dfm, kernel
from .flat import flat_pattern, write_dxf
from .fold import expected_volume_difference
from .render import read_stl, render
from .sheets import (sheet_assembly, sheet_flat, sheet_panel, sheet_shell_views)


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


ISO = _unit((1.0, -1.25, 0.95))
ISO_RIGHT = _unit(np.cross((0, 0, 1), ISO))


def build_job(case: design.Case, out: Path) -> dict:
    shell, panel = case.shell, case.panel
    bodies = [kernel.folded_body(shell), kernel.folded_body(panel),
              kernel.flat_body(shell), kernel.flat_body(panel)]
    views = []

    def view(name, bodies_, toward, right, placed=False):
        views.append({"name": name, "bodies": bodies_, "toward": list(map(float, toward)),
                      "right": list(map(float, right)), "placed": placed})

    view("shell_top", ["bottom_shell"], (0, 0, 1), (1, 0, 0))
    view("shell_front", ["bottom_shell"], (0, -1, 0), (1, 0, 0))
    view("shell_right", ["bottom_shell"], (1, 0, 0), (0, 1, 0))
    view("shell_back", ["bottom_shell"], (0, 1, 0), (-1, 0, 0))
    view("panel_top", ["top_panel"], (0, 0, 1), (1, 0, 0))
    view("panel_front", ["top_panel"], (0, -1, 0), (1, 0, 0))
    view("panel_right", ["top_panel"], (1, 0, 0), (0, 1, 0))

    probes = []
    T, c = case.params.material.T, case.params.panel_clearance
    for i, s in enumerate(case.screws):
        start = s["centre"] - s["inward"] * (T + c + 2.0)  # outside the cheek
        probes.append({"name": f"screw_{i + 1}_{s['side']}", "kind": "cyl", "p": start,
                       "dir": s["inward"], "d": 3.0, "len": 2 * T + c + 6.0,
                       "bodies": ["bottom_shell", "top_panel"]})
    b = case.pcb_box
    probes.append({"name": "pcb_keepout", "kind": "box", "o": b["o"], "xdir": b["xdir"],
                   "zdir": b["zdir"], "dx": b["dx"], "dy": b["dy"], "dz": b["dz"],
                   "bodies": ["bottom_shell", "top_panel"]})

    return {
        "bodies": bodies,
        "outputs": {
            "step": [
                {"path": str(out / "bottom_shell.step"), "bodies": ["bottom_shell"],
                 "product": "bottom_shell"},
                {"path": str(out / "top_panel.step"), "bodies": ["top_panel"],
                 "product": "top_panel"},
                {"path": str(out / "assembly.step"), "bodies": ["bottom_shell", "top_panel"],
                 "placed": True, "product": "synth_case"},
            ],
            "stl": [{"path": str(out / "bottom_shell.stl"), "body": "bottom_shell",
                     "placed": True, "deflection": 0.04},
                    {"path": str(out / "top_panel.stl"), "body": "top_panel", "placed": True,
                     "deflection": 0.04}],
            "flat_outline": [{"body": "bottom_shell_flat", "z": 0.0},
                             {"body": "top_panel_flat", "z": 0.0}],
            "views": views,
            "pairs": [{"a": "bottom_shell", "b": "top_panel"}],
            "probes": probes,
        },
    }


def kernel_checks(case: design.Case, res: dict, rules: dfm.Rules, rep: dfm.Report) -> None:
    r = rules.resolve(case.params.material.T, case.params.material.R)
    for lay in (case.shell, case.panel):
        name = lay.part.name
        m, f = res["bodies"][name], res["bodies"][f"{name}_flat"]
        rep.add(f"{name}: valid solid", m["valid"] and m["solids"] == 1,
                f"valid={m['valid']}, solids={m['solids']}, faces={m['faces']}")
        expect = expected_volume_difference(lay)
        got = m["volume"] - f["volume"]
        tol = 1e-4 * m["volume"] + 1e-3
        rep.add(f"{name}: flat pattern matches fold", abs(got - expect) <= tol,
                f"folded−flat volume {got:.4f} mm³, bend allowance predicts {expect:.4f}",
                got, expect)
    for pr in res["pairs"]:
        rep.add(f"{pr['a']} vs {pr['b']}: no interference", pr["interference_volume"] < 1e-6,
                f"overlap volume {pr['interference_volume']:.6f} mm³")
        rep.add(f"{pr['a']} vs {pr['b']}: clearance", pr["min_distance"] >= r.min_clearance,
                f"closest {pr['min_distance']:.3f} mm, need ≥ {r.min_clearance}",
                pr["min_distance"], r.min_clearance)
    vols = {k: v["volume"] for k, v in res["bodies"].items()}
    expect_step = {"bottom_shell.step": vols["bottom_shell"], "top_panel.step": vols["top_panel"],
                   "assembly.step": vols["bottom_shell"] + vols["top_panel"]}
    for s in res["step"]:
        name = Path(s["path"]).name
        want = expect_step[name]
        ok = abs(s["readback_volume"] - want) <= 1e-6 * want and s["readback_solids"] >= 1
        rep.add(f"{name} reads back", ok,
                f"{s['readback_solids']} solid(s), volume {s['readback_volume']:.3f} mm³ "
                f"(model {want:.3f})")
    screws = [p for p in res["probes"] if p["name"].startswith("screw_")]
    worst = min(min(h["common_volume"] for h in p["hits"].values()) for p in screws)
    blocked = [p["name"] for p in screws if any(h["common_volume"] > 1e-6
                                                for h in p["hits"].values())]
    rep.add("screws pass through cheek and lip holes", not blocked,
            f"{len(screws)} M3 shanks clear both parts" if not blocked else
            "blocked: " + ", ".join(blocked))
    pcb = next(p for p in res["probes"] if p["name"] == "pcb_keepout")
    dmin = min(h["min_distance"] for h in pcb["hits"].values())
    hit = any(h["common_volume"] > 1e-6 for h in pcb["hits"].values())
    rep.add("PCB keep-out clears the case", not hit and dmin >= r.pcb_clearance,
            f"closest {dmin:.2f} mm to the case, need ≥ {r.pcb_clearance}", dmin,
            r.pcb_clearance)


def check_dxf(path: Path, fp) -> dfm.Check:
    """Re-open the DXF and confirm the cut layer has the flat pattern's extent."""
    import ezdxf
    from ezdxf import bbox

    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    cut = msp.query('*[layer=="CUT"]')
    bends = msp.query('LINE[layer=="BEND"]')
    ext = bbox.extents(cut)
    size = (ext.size.x, ext.size.y)
    ok = (abs(size[0] - fp.size[0]) < 1e-6 and abs(size[1] - fp.size[1]) < 1e-6
          and len(bends) == len(fp.bends))
    return dfm.Check(f"{path.name} reads back", "pass" if ok else "fail",
                     f"{len(cut)} cut entities, {len(bends)} bend lines, extent "
                     f"{size[0]:.3f} × {size[1]:.3f} mm")


def main(out_dir: str = "out") -> int:
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    case = design.build()
    rules = dfm.Rules()
    rep = dfm.Report()

    res = kernel.run(build_job(case, out), out / "work")
    kernel_checks(case, res, rules, rep)
    for lay in (case.shell, case.panel):
        dfm.check_layout(lay, rules, rep)

    m = case.params.material
    flats = {}
    tags = {}
    for lay, label in ((case.shell, "Bottom shell"), (case.panel, "Top panel")):
        fp = flat_pattern(lay, res["flat_outline"][f"{lay.part.name}_flat"])
        flats[lay.part.name] = fp
        tags[lay.part.name] = {b.bend.name: f"B{i + 1}" for i, b in enumerate(lay.bends)}
        dfm.check_flat_size(lay.part.name, fp.size, rules, rep)
        dxf_path = out / f"{lay.part.name}_flat.dxf"
        write_dxf(fp, dxf_path, m.name, m.T)
        rep.checks.append(check_dxf(dxf_path, fp))

    meshes = [(read_stl(out / "bottom_shell.stl"), (176, 180, 186)),
              (read_stl(out / "top_panel.stl"), (58, 62, 70))]
    img = render(meshes, toward=ISO, size=(1500, 1080))
    img.save(out / "render_iso.png")
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    sheets = [
        sheet_assembly(case, res, rep, buf.getvalue()),
        sheet_shell_views(case, res),
        sheet_flat(case, flats["bottom_shell"], "Bottom shell", 3, tags["bottom_shell"], 0.5,
                   ["Corner reliefs and closed corners are in the DXF."]),
        sheet_panel(case, res, flats["top_panel"], tags["top_panel"]),
    ]
    pdfs = []
    for i, sh in enumerate(sheets, 1):
        svg = out / "work" / f"sheet{i}.svg"
        svg.write_text(sh.svg())
        pdf = out / "work" / f"sheet{i}.pdf"
        subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(pdf), str(svg)], check=True)
        pdfs.append(str(pdf))
    drawing = out / "synth_case_drawing.pdf"
    subprocess.run(["qpdf", "--empty", "--pages", *pdfs, "--", str(drawing)], check=True)
    prev = out / "preview"
    prev.mkdir(exist_ok=True)
    subprocess.run(["pdftoppm", "-png", "-r", "110", str(drawing), str(prev / "sheet")],
                   check=True)

    report = {
        "summary": rep.summary(),
        "ok": rep.ok,
        "checks": [asdict(c) for c in rep.checks],
        "bodies": res["bodies"],
        "pairs": res["pairs"],
        "probes": res["probes"],
        "flat_sizes": {k: v.size for k, v in flats.items()},
        "slope_deg": math.degrees(case.slope),
    }
    (out / "report.json").write_text(json.dumps(report, indent=2, default=float))
    print(rep.summary())
    for c in rep.checks:
        print(f"[{c.status:4}] {c.name}: {c.detail}")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
