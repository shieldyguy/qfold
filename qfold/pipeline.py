"""End-to-end build of the synth case: model, assemble, check, export, render, draw.

    python -m qfold.pipeline [out_dir]

Writes STEP (each sheet metal part, plus the whole assembly with named
components and occurrences), flat-pattern DXFs, STL meshes, PNG renders, a
4-sheet PDF drawing with PNG previews, and report.json.
"""

from __future__ import annotations

import io
import json
import math
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np

from . import design, dfm, kernel
from .assembly import Design, mismatch
from .flat import cut_length, flat_pattern, write_dxf
from .fold import expected_volume_difference
from .render import read_stl, render
from .sheets import sheet_assembly, sheet_flat, sheet_panel, sheet_shell_views


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


ISO = _unit((1.0, -1.25, 0.95))
MOUNT_TOL = {"radial": 0.05, "axial": 0.05, "angle_deg": 0.1}
SELF_TOL = 1e-9  # relative; fused vs unfused volume, and the bend allowance identity


def design_ref(d: Design, spec: str) -> np.ndarray:
    """World frame for "occurrence/path:ref" (or just a path, for its origin)."""
    path, _, name = spec.partition(":")
    return d.find(path).ref(name or "origin")


def sheet_proto(case: design.Case, occurrence: str) -> str:
    comp = case.design.find(occurrence).component
    return Design.proto_name(comp, comp.bodies[0])


def color_groups(d: Design) -> dict[tuple, list[str]]:
    groups: dict[tuple, list[str]] = {}
    for name, _, _, body in d.instances():
        groups.setdefault(tuple(body.color), []).append(name)
    return groups


def build_job(case: design.Case, out: Path):
    d = case.design
    job = d.job()
    job["bodies"] += [kernel.flat_body(case.shell), kernel.flat_body(case.panel)]
    shell_p, panel_p = sheet_proto(case, "bottom_shell"), sheet_proto(case, "top_panel")
    everything = [n for n, *_ in d.instances()]
    board = d.group("control_board")

    views = []

    def view(name, geom, toward, right):
        views.append({"name": name, "geom": geom, "toward": list(map(float, toward)),
                      "right": list(map(float, right))})

    view("shell_top", shell_p, (0, 0, 1), (1, 0, 0))
    view("shell_front", shell_p, (0, -1, 0), (1, 0, 0))
    view("shell_right", shell_p, (1, 0, 0), (0, 1, 0))
    view("shell_back", shell_p, (0, 1, 0), (-1, 0, 0))
    view("panel_top", panel_p, (0, 0, 1), (1, 0, 0))
    view("panel_front", panel_p, (0, -1, 0), (1, 0, 0))
    view("panel_right", panel_p, (1, 0, 0), (0, 1, 0))

    stl_dir = out / "stl"
    stl_dir.mkdir(parents=True, exist_ok=True)
    groups = color_groups(d)
    stl = [{"path": str(stl_dir / f"group_{i}.stl"), "geom": names, "deflection": 0.04}
           for i, names in enumerate(groups.values())]

    # Interference and clearance questions, with what each one must satisfy:
    #   clear      no overlap and at least min_clearance apart
    #   seated     no overlap and touching (within rules.seated): parts that
    #              rest on each other, so a part that misses entirely fails
    #   pcb_clear  no overlap and at least pcb_clearance apart
    #   self_gap   two faces of one sheet metal body not joined by a bend
    pairs = [
        ("bottom_shell vs top_panel", d.group("bottom_shell"), d.group("top_panel"), "clear"),
        ("screw heads on cheeks", d.group(*case.screws), d.group("bottom_shell", "top_panel"),
         "seated"),
        ("feet on base", d.group(*case.feet), d.group("bottom_shell"), "seated"),
        ("board parts against panel", board, d.group("top_panel"), "seated"),
        ("control board vs bottom_shell", board, d.group("bottom_shell"), "pcb_clear"),
    ]
    # Each face of each sheet metal body as its own body, to measure the gaps
    # between faces that only meet at a corner (e.g. cheek to rear wall).
    for lay in (case.shell, case.panel):
        names = list(lay.faces)
        joined = {frozenset((bs.parent, bs.bend.wall.name)) for bs in lay.bends}
        for name, plate in zip(names, lay.plates3d):
            job["bodies"].append({"name": f"{lay.body.name}#face:{name}", "plates": [plate],
                                  "sectors": []})
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if frozenset((a, b)) not in joined:
                    pairs.append((f"{lay.body.name}: {a} vs {b}",
                                  f"{lay.body.name}#face:{a}", f"{lay.body.name}#face:{b}",
                                  "self_gap"))
    job["outputs"] = {
        "measure": [{"name": "assembly", "geom": everything}],
        "step": [
            {"path": str(out / "bottom_shell.step"), "geom": shell_p, "product": "bottom_shell"},
            {"path": str(out / "top_panel.step"), "geom": panel_p, "product": "top_panel"},
            {"path": str(out / "assembly.step"), "tree": d.step_tree()},
        ],
        "stl": stl,
        "flat_outline": [{"body": "bottom_shell#flat", "z": 0.0},
                         {"body": "top_panel#flat", "z": 0.0}],
        "views": views,
        "pairs": [{"a": a, "b": b} for _, a, b, _ in pairs],
    }
    return job, pairs, list(zip(groups.keys(), stl))


def kernel_checks(case: design.Case, res: dict, pairs, rules: dfm.Rules, rep: dfm.Report):
    d = case.design
    r = rules.resolve(case.params.rule.T, case.params.rule.R)
    for lay, occ in ((case.shell, "bottom_shell"), (case.panel, "top_panel")):
        m = res["bodies"][sheet_proto(case, occ)]
        f = res["bodies"][f"{lay.body.name}#flat"]
        rep.add(f"{occ}: valid solid", m["valid"] and m["solids"] == 1,
                f"valid={m['valid']}, solids={m['solids']}, faces={m['faces']}")
        # Self-intersection: fusing overlapping plates/sectors loses volume, so
        # the fused solid must equal the sum of its unfused parts, folded and flat.
        lost = [(st, b["parts_volume"] - b["volume"]) for st, b in (("folded", m), ("flat", f))]
        tol = SELF_TOL * m["volume"]
        rep.add(f"{occ}: no self-intersection", all(abs(x) <= tol for _, x in lost),
                ", ".join(f"{st} fuse loses {x:.2e} mm³" for st, x in lost)
                + f" (limit {tol:.1e})")
        # Bend allowance identity on the unfused parts: an exact relation.
        expect = expected_volume_difference(lay)
        got = m["parts_volume"] - f["parts_volume"]
        rep.add(f"{occ}: flat pattern matches fold", abs(got - expect) <= tol,
                f"folded−flat volume {got:.6f} mm³, bend allowance predicts {expect:.6f}",
                got, expect)

    vol = {k: v["volume"] for k, v in res["bodies"].items()}
    total = sum(vol[p] for _, p, _, _ in d.instances())
    want = {"bottom_shell.step": vol[sheet_proto(case, "bottom_shell")],
            "top_panel.step": vol[sheet_proto(case, "top_panel")], "assembly.step": total}
    n_inst = len(list(d.instances()))
    for s in res["step"]:
        name = Path(s["path"]).name
        ok = abs(s["readback_volume"] - want[name]) <= 1e-6 * want[name] and s["readback_solids"] >= 1
        if name == "assembly.step":
            ok = ok and s["readback_solids"] == n_inst
        rep.add(f"{name} reads back", ok,
                f"{s['readback_solids']} solid(s), volume {s['readback_volume']:.3f} mm³ "
                f"(model {want[name]:.3f})")

    gaps = []
    for (label, _, _, rule), pr in zip(pairs, res["pairs"]):
        overlap, dist = pr["interference_volume"], pr["min_distance"]
        if rule == "clear":
            rep.add(f"{label}: no interference", overlap < 1e-6, f"overlap {overlap:.6f} mm³")
            rep.add(f"{label}: clearance", dist >= r.min_clearance,
                    f"closest {dist:.3f} mm, need ≥ {r.min_clearance}", dist, r.min_clearance)
        elif rule == "pcb_clear":
            rep.add(f"{label}: clearance", overlap < 1e-6 and dist >= r.pcb_clearance,
                    f"overlap {overlap:.6f} mm³, closest {dist:.2f} mm, need ≥ {r.pcb_clearance}",
                    dist, r.pcb_clearance)
        elif rule == "seated":
            rep.add(f"{label}", overlap < 1e-6 and dist <= r.seated,
                    f"overlap {overlap:.6f} mm³, gap {dist:.4f} mm (must touch, ≤ {r.seated})",
                    dist, r.seated)
        elif rule == "self_gap":
            gaps.append((label, overlap, dist))
        else:
            raise ValueError(f"unknown pair rule {rule}")
    if gaps:
        bad = [f"{lb} {dist:.3f} mm" for lb, ov, dist in gaps if ov > 1e-6 or dist < r.min_clearance]
        tight = min(gaps, key=lambda g: g[2])
        rep.add("faces of one part keep their gaps", not bad,
                f"{len(gaps)} unjoined face pairs, tightest {tight[2]:.3f} mm ({tight[0]}), "
                f"need ≥ {r.min_clearance}" if not bad else "too close: " + ", ".join(bad),
                tight[2], r.min_clearance)

    # Each screw through its cheek hole and on into its lip's PEM nut hole.
    off = []
    for screw, cheek, lip in case.screw_holes:
        f = design_ref(d, screw)
        for hole in (cheek, lip):
            m = mismatch(f, design_ref(d, hole))
            off.append((m["radial"], m["angle_deg"], f"{screw}→{hole.split(':')[-1]}"))
    worst_r = max(off)
    worst_a = max(o[1] for o in off)
    bad = [w for rr, aa, w in off if rr > MOUNT_TOL["radial"] or aa > MOUNT_TOL["angle_deg"]]
    rep.add("screws on cheek and lip holes", not bad,
            f"{len(case.screw_holes)} screws, worst {worst_r[0]:.3f} mm off axis, {worst_a:.3f}°"
            if not bad else "off axis: " + ", ".join(bad))

    # Every board part on its panel hole: position along and across the hole
    # axis, and tilt. Only RV1 was aligned; the rest test the board layout.
    worst, bad = {k: 0.0 for k in MOUNT_TOL}, []
    for part, hole in case.mounts:
        m = mismatch(design_ref(d, part), design_ref(d, hole))
        for k, tol in MOUNT_TOL.items():
            worst[k] = max(worst[k], abs(m[k]))
        if any(abs(m[k]) > tol for k, tol in MOUNT_TOL.items()):
            bad.append(f"{part.split('/')[-1]}→{hole.split(':')[-1].removesuffix('.inside')} "
                       f"off {m['radial']:.2f} mm")
    rep.add("board parts on their panel holes", not bad,
            f"{len(case.mounts)} parts, worst {worst['radial']:.3f} mm across, "
            f"{worst['axial']:.3f} mm along, {worst['angle_deg']:.3f}°" if not bad
            else "misaligned: " + ", ".join(bad))

    panel_tags = {t for t in d.find("top_panel").component.refs
                  if not t.endswith(".inside") and t.startswith("P")}
    mounted = {h.split(":")[-1].removesuffix(".inside") for _, h in case.mounts}
    board_parts = {o.name for o in d.find("control_board").children()} - {"PCB"}
    used = {p.split("/")[-1] for p, _ in case.mounts}
    rep.add("every panel hole has a part, every part a hole",
            panel_tags == mounted and board_parts == used,
            f"{len(panel_tags)} panel holes, {len(board_parts)} board parts"
            + ("" if panel_tags == mounted else f"; empty holes {sorted(panel_tags - mounted)}")
            + ("" if board_parts == used else f"; parts without hole {sorted(board_parts - used)}"))


def check_dxf(path: Path, fp) -> dfm.Check:
    """Re-open the DXF and take a census of the cut layer against the flat
    pattern: extent, every circle by centre and diameter, line and arc counts,
    total cut length, and bend lines with their up/down labels."""
    import ezdxf
    from ezdxf import bbox

    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    cut = msp.query('*[layer=="CUT"]')
    lines = msp.query('LINE[layer=="CUT"]')
    arcs = msp.query('ARC[layer=="CUT"]')
    circles = msp.query('CIRCLE[layer=="CUT"]')
    bends = msp.query('LINE[layer=="BEND"]')
    notes = [t.dxf.text for t in msp.query('TEXT[layer=="NOTES"]')]
    ext = bbox.extents(cut)
    size = (ext.size.x, ext.size.y)
    problems = []
    if abs(size[0] - fp.size[0]) > 1e-6 or abs(size[1] - fp.size[1]) > 1e-6:
        problems.append(f"extent {size[0]:.3f}×{size[1]:.3f}")
    key = lambda x, y, r: (round(x, 4), round(y, 4), round(r, 4))
    got = sorted(key(c.dxf.center.x, c.dxf.center.y, c.dxf.radius) for c in circles)
    want = sorted(key(c[0], c[1], r) for c, r in fp.circles)
    if got != want:
        problems.append(f"circles {len(got)} vs {len(want)} or moved")
    if len(lines) != len(fp.lines) or len(arcs) != len(fp.arcs):
        problems.append(f"lines/arcs {len(lines)}/{len(arcs)} vs {len(fp.lines)}/{len(fp.arcs)}")
    length = sum(ln.dxf.start.distance(ln.dxf.end) for ln in lines)
    length += sum(2 * np.pi * c.dxf.radius for c in circles)
    length += sum(np.radians((a.dxf.end_angle - a.dxf.start_angle) % 360 or 360) * a.dxf.radius
                  for a in arcs)
    if abs(length - cut_length(fp)) > 1e-6 * cut_length(fp):
        problems.append(f"cut length {length:.3f} vs {cut_length(fp):.3f}")
    labels = [f"{b.name} {b.direction} " for b in fp.bends]
    if len(bends) != len(fp.bends) or not all(any(n.startswith(lb) for n in notes) for lb in labels):
        problems.append("bend lines or DN/UP labels differ")
    return dfm.Check(f"{path.name} reads back", "fail" if problems else "pass",
                     "; ".join(problems) if problems else
                     f"{len(circles)} circles, {len(lines)} lines, {len(arcs)} arcs, "
                     f"{len(bends)} bends, cut {length:.1f} mm, extent {size[0]:.3f} × {size[1]:.3f}")


def main(out_dir: str = "out") -> int:
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    case = design.build(workdir=out / "work")
    rules = dfm.Rules()
    rep = dfm.Report()

    job, pairs, stl_groups = build_job(case, out)
    res = kernel.run(job, out / "work")
    kernel_checks(case, res, pairs, rules, rep)
    for lay in (case.shell, case.panel):
        dfm.check_layout(lay, rules, rep)

    m = case.params.rule
    flats, tags = {}, {}
    for lay in (case.shell, case.panel):
        name = lay.body.name
        fp = flat_pattern(lay, res["flat_outline"][f"{name}#flat"])
        flats[name] = fp
        tags[name] = {b.bend.name: f"B{i + 1}" for i, b in enumerate(lay.bends)}
        dfm.check_flat_size(name, fp.size, rules, rep)
        dxf_path = out / f"{name}_flat.dxf"
        write_dxf(fp, dxf_path, m.material, m.T)
        rep.checks.append(check_dxf(dxf_path, fp))

    meshes = [(read_stl(s["path"]), color) for color, s in stl_groups]
    img = render(meshes, toward=ISO, size=(1500, 1080))
    img.save(out / "render_iso.png")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    # The inserted control board on its own, as placed in the case.
    job_b = {"bodies": job["bodies"], "instances": job["instances"], "outputs": {"stl": []}}
    board_groups: dict[tuple, list[str]] = {}
    for name, _, _, body in case.design.instances("control_board"):
        board_groups.setdefault(tuple(body.color), []).append(name)
    for i, names in enumerate(board_groups.values()):
        job_b["outputs"]["stl"].append({"path": str(out / "stl" / f"board_{i}.stl"),
                                        "geom": names, "deflection": 0.04})
    kernel.run(job_b, out / "work", tag="board")
    board_meshes = [(read_stl(s["path"]), color)
                    for color, s in zip(board_groups, job_b["outputs"]["stl"])]
    render(board_meshes, toward=ISO, size=(1500, 1080)).save(out / "render_board.png")

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
        "parts_list": case.design.parts_list(),
        "bodies": res["bodies"],
        "envelope": res["measure"]["assembly"]["bbox"],
        "pairs": [dict(label=p[0], **r) for p, r in zip(pairs, res["pairs"])],
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
