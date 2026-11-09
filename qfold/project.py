"""Build, check, export and draw a design: the one entry point for any project.

    from qfold.project import Project

    p = Project("pedal_box", design, shop=shops.spec("jlccnc", "Aluminum 5052", 1.5),
                params=params)
    p.seated(["screw_1", "screw_2"], onto="lid", name="screws on the lid")
    p.clear("lid", "tray")
    p.on_hole("screw_1", "lid:C1")
    sys.exit(p.main())            # python -m examples.pedal_box [out_dir]

`build(out)` finds every sheet metal part in the design and, with no further
wiring, writes:

    <part>.step, assembly.step      exact solids, read back and compared
    <part>_flat.dxf                 CUT / BEND / NOTES layers, read back
    render_iso.png                  shaded render
    <name>_drawing.pdf, preview/    assembly, one sheet per part, holes, checks
    report.json                     parameters, checks, holes as assembled, sizes

and runs every standard check: valid solid, no self-intersection, flat pattern
matches the fold, the DFM rules, sheet size, gaps between faces of one part,
no top-level part overlapping another, plus the fits you declare. Occurrences
are named by path ("lid", "control_board/RV1"); holes by "path:tag".
"""

from __future__ import annotations

import dataclasses
import io
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from . import dfm, kernel, sheets
from .assembly import Body, Component, Design, mismatch
from .drawing import SHEET_NO
from .flat import cut_length, flat_pattern, write_dxf
from .fold import SheetLayout, SheetMetalRule, expected_volume_difference
from .render import read_stl, render


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


ISO = _unit((1.0, -1.25, 0.95))
MOUNT_TOL = {"radial": 0.05, "axial": 0.05, "angle_deg": 0.1}
OVERLAP_TOL = 1e-6  # mm³: below this two parts don't overlap
SELF_TOL = 1e-9  # relative; fused vs unfused volume, and the bend allowance identity
VIEWS = {"top": ((0, 0, 1), (1, 0, 0)), "front": ((0, -1, 0), (1, 0, 0)),
         "right": ((1, 0, 0), (0, 1, 0)), "back": ((0, 1, 0), (-1, 0, 0))}


def design_ref(d: Design, spec: str) -> np.ndarray:
    """World frame for "occurrence/path:ref" (or just a path, for its origin)."""
    path, _, name = spec.partition(":")
    return d.find(path).ref(name or "origin")


def _list(x) -> list[str]:
    return [x] if isinstance(x, str) else list(x)


@dataclass
class SheetPart:
    name: str  # the sheet metal body's name; names its files and views
    path: str  # its (first) occurrence
    comp: Component
    body: Body
    lay: SheetLayout
    proto: str  # kernel prototype name


@dataclass
class Build:
    """What the sheet builders and custom checks get to look at."""
    project: "Project"
    out: Path
    res: dict
    rep: dfm.Report
    parts: dict[str, SheetPart]
    flats: dict = field(default_factory=dict)
    tags: dict = field(default_factory=dict)  # part -> {bend name: "B1"}
    holes: list = field(default_factory=list)
    render_png: bytes = b""


class Project:
    def __init__(self, name: str, design: Design, *, shop=None, rule: SheetMetalRule | None = None,
                 rules: dfm.Rules | None = None, params=None, notes=(), title: str | None = None):
        """shop: a shops.ShopSpec; its limits become the DFM rules and its
        notes go on the drawings. rules overrides them. params: a dataclass
        or dict, recorded in report.json and on the drawing."""
        self.name, self.design = name, design
        self.title = title or name.replace("_", " ").capitalize()
        self.shop = shop
        self.rules = rules or (shop.rules if shop else dfm.Rules())
        self.params = params
        self.notes = list(notes) + (list(shop.notes) if shop else [])
        self.facts: list[tuple[str, str]] = []  # extra rows for the assembly sheet
        self.extra_parts: list[dict] = []  # parts list rows with no geometry (PEM nuts)
        self.part_sheets = None  # fn(build) -> [Sheet], replaces the default part sheets
        self.renders: dict[str, str] = {}  # extra render name -> occurrence path
        self._fits: list[tuple] = []
        self._holes: list[tuple] = []
        self._checks: list = []
        parts = self.sheet_parts()
        if not parts and rule is None:
            raise ValueError("no sheet metal body in the design; pass rule=")
        self.rule = rule or next(iter(parts.values())).lay.body.rule

    # ------------------------------------------------------------ declaring
    def seated(self, parts, onto, name: str | None = None, touch: bool = True) -> None:
        """Each part must rest on `onto`: no overlap, and touching it. One
        kernel question per part, reported as one row that names the parts
        that fail. touch=False keeps only the no-overlap half, for parts that
        sit in a hole with clearance (LEDs)."""
        onto = _list(onto)
        name = name or f"seated on {', '.join(onto)}"
        for part in _list(parts):
            self._fits.append((name, part.split("/")[-1], [part], onto,
                               "seated" if touch else "no_overlap", None))

    def clear(self, a, b, min: float | None = None, name: str | None = None) -> None:
        """a and b (occurrence paths, or lists of them) must not overlap and
        must stay at least `min` apart (default: rules.min_clearance)."""
        a, b = _list(a), _list(b)
        self._fits.append((name or f"{', '.join(a)} clear of {', '.join(b)}", "", a, b,
                           "clear", min))

    def on_hole(self, part: str, hole: str, name: str = "parts on their holes",
                axial: bool = True) -> None:
        """The part's origin must sit on the hole's axis ("path:tag"), at its
        depth (unless axial=False) and square to it."""
        self._holes.append((name, part, hole,
                            tuple(MOUNT_TOL) if axial else ("radial", "angle_deg")))

    def check(self, fn) -> None:
        """A custom check: fn(build) adds rows to build.rep."""
        self._checks.append(fn)

    # ------------------------------------------------------------- looking
    def sheet_parts(self) -> dict[str, SheetPart]:
        out: dict[str, SheetPart] = {}
        seen = set()
        for occ in self.design.walk():
            c = occ.component
            if c.key in seen:
                continue
            seen.add(c.key)
            for body in c.bodies:
                if body.sheet is None:
                    continue
                name = body.sheet.body.name
                if name in out:
                    raise ValueError(f"two sheet metal bodies are named {name!r}")
                out[name] = SheetPart(name, occ.path, c, body, body.sheet,
                                      Design.proto_name(c, body))
        return out

    def parts_list(self) -> list[dict]:
        return self.design.parts_list() + self.extra_parts

    def param_rows(self) -> list[tuple[str, str]]:
        """The parameter table, flattened to (name, value) rows."""
        p = self.params
        if p is None:
            return []
        if dataclasses.is_dataclass(p):
            p = asdict(p)
        rows = []

        def walk(prefix, v):
            if isinstance(v, dict):
                for k, x in v.items():
                    walk(f"{prefix}.{k}" if prefix else str(k), x)
            else:
                rows.append((prefix, f"{v:g}" if isinstance(v, float) else str(v)))
        walk("", p)
        return rows

    def holes(self) -> list[dict]:
        """Every hole of every sheet metal part where it ends up in the
        assembly: centre on the outside surface, and the way it faces."""
        axes = {(1, 0, 0): "+X right", (-1, 0, 0): "−X left", (0, 1, 0): "+Y back",
                (0, -1, 0): "−Y front", (0, 0, 1): "+Z up", (0, 0, -1): "−Z down"}
        rows = []
        for part in self.sheet_parts().values():
            w = self.design.find(part.path).world()
            for fname, st in part.lay.faces.items():
                for h in st.face.holes:
                    c = w[:3, :3] @ st.to3d(*h.c) + w[:3, 3]
                    out = w[:3, :3] @ (-st.N)
                    facing = next((t for a, t in axes.items() if np.dot(out, a) > 0.9999),
                                  ", ".join(f"{x + 0.0:.2f}" for x in np.round(out, 2)))
                    rows.append({"part": part.name, "tag": h.tag, "label": h.label,
                                 "size": h.size_text(), "face": fname, "x": round(float(c[0]), 6) + 0.0,
                                 "y": round(float(c[1]), 6) + 0.0,
                                 "z": round(float(c[2]), 6) + 0.0, "facing": facing})
        return rows

    # ------------------------------------------------------------- fitting
    def _pairs(self, job: dict) -> list[tuple]:
        """(check, item, a, b, rule, limit) with a, b as kernel instance names.
        Adds each face of each sheet part to the job as its own body."""
        d = self.design
        pairs = [(n, it, d.group(*a), d.group(*b), rule, lim)
                 for n, it, a, b, rule, lim in self._fits]
        # Faces of one part that aren't joined by a bend must keep a gap
        # (cheek to rear wall). Both faces are in the part's own coordinates.
        for part in self.sheet_parts().values():
            lay, names = part.lay, list(part.lay.faces)
            joined = {frozenset((bs.parent, bs.bend.wall.name)) for bs in lay.bends}
            for name, plate in zip(names, lay.plates3d):
                job["bodies"].append({"name": f"{part.name}#face:{name}", "plates": [plate],
                                      "sectors": []})
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    if frozenset((a, b)) not in joined:
                        pairs.append(("faces of one part keep their gaps",
                                      f"{part.name}: {a} vs {b}", f"{part.name}#face:{a}",
                                      f"{part.name}#face:{b}", "self_gap", None))
        # Safety net that needs no declaring: no top-level occurrence may
        # overlap the rest. (What's inside an inserted STEP is taken as given.)
        units = []
        for occ in d.top():
            try:
                units.append((occ.name, d.group(occ.path)))
            except KeyError:
                pass
        if len(units) > 1:
            for name, mine in units:
                rest = [n for other, names in units if other != name for n in names]
                pairs.append(("no part overlaps another", name, mine, rest, "apart", None))
        return pairs

    def _fit_checks(self, res_pairs: list[dict], pairs, rep: dfm.Report) -> None:
        r = self.rules.resolve(self.rule.T, self.rule.R)
        rows: dict[str, list] = {}
        for (check, item, _, _, rule, lim), pr in zip(pairs, res_pairs):
            rows.setdefault(check, []).append(
                (item, rule, pr["interference_volume"], pr["min_distance"], lim))
        for check, items in rows.items():
            kinds = {k for _, k, _, _, _ in items}
            if kinds == {"clear"}:
                for _, _, ov, dist, lim in items:
                    need = lim if lim is not None else r.min_clearance
                    rep.add(check, ov < OVERLAP_TOL and dist >= need,
                            f"overlap {ov:.6f} mm³, closest {dist:.3f} mm, need ≥ {need:g}",
                            dist, need)
            elif kinds <= {"seated", "no_overlap"}:
                bad = [f"{it} overlaps {ov:.4f} mm³" for it, _, ov, _, _ in items
                       if ov >= OVERLAP_TOL]
                bad += [f"{it} {dist:.3f} mm off" for it, k, ov, dist, _ in items
                        if k == "seated" and ov < OVERLAP_TOL and dist > r.seated]
                seated = [dist for _, k, _, dist, _ in items if k == "seated"]
                worst = max(seated, default=0.0)
                free = len(items) - len(seated)
                rep.add(check, not bad,
                        ", ".join(([f"{len(seated)} touching, widest gap {worst:.4f} mm "
                                    f"(≤ {r.seated})"] if seated else [])
                                  + ([f"{free} clear of it"] if free else []) + ["no overlap"])
                        if not bad else "not seated: " + "; ".join(bad), worst, r.seated)
            elif kinds == {"self_gap"}:
                bad = [f"{it} {dist:.3f} mm" for it, _, ov, dist, _ in items
                       if ov >= OVERLAP_TOL or dist < r.min_clearance]
                tight = min(items, key=lambda g: g[3])
                rep.add(check, not bad,
                        f"{len(items)} unjoined face pairs, tightest {tight[3]:.3f} mm "
                        f"({tight[0]}), need ≥ {r.min_clearance}" if not bad
                        else "too close: " + ", ".join(bad), tight[3], r.min_clearance)
            elif kinds == {"apart"}:
                bad = [f"{it} overlaps the rest by {ov:.4f} mm³" for it, _, ov, _, _ in items
                       if ov >= OVERLAP_TOL]
                rep.add(check, not bad, f"{len(items)} top-level parts, none overlapping"
                        if not bad else "; ".join(bad))
            else:
                raise ValueError(f"{check}: mixed fit rules {sorted(kinds)}")

    def _hole_checks(self, rep: dfm.Report) -> None:
        d = self.design
        rows: dict[str, list] = {}
        for name, part, hole, keys in self._holes:
            rows.setdefault(name, []).append((part, hole, keys))
        for name, entries in rows.items():
            worst, bad = dict.fromkeys(MOUNT_TOL, 0.0), []
            for part, hole, keys in entries:
                m = mismatch(design_ref(d, part), design_ref(d, hole))
                for k in keys:
                    worst[k] = max(worst[k], abs(m[k]))
                if any(abs(m[k]) > MOUNT_TOL[k] for k in keys):
                    bad.append(f"{part.split('/')[-1]}→"
                               f"{hole.split(':')[-1].removesuffix('.inside')} "
                               f"{m['radial']:.2f} mm across, {m['axial']:.2f} mm along, "
                               f"{m['angle_deg']:.2f}°")
            n = len({part for part, _, _ in entries})
            rep.add(name, not bad,
                    f"{n} parts, worst {worst['radial']:.3f} mm across, {worst['axial']:.3f} mm "
                    f"along, {worst['angle_deg']:.3f}°" if not bad
                    else "misaligned: " + "; ".join(bad))

    def check_fit(self, workdir) -> dfm.Report:
        """Only the fit checks (declared fits, gaps, overlaps, parts on their
        holes): the same ones build() runs, in about a second."""
        job = self.design.job()
        pairs = self._pairs(job)
        job["outputs"] = {"pairs": [{"a": p[2], "b": p[3], "distance": p[4] != "apart"} for p in pairs]}
        res = kernel.run(job, Path(workdir), "fit")
        rep = dfm.Report()
        self._fit_checks(res["pairs"], pairs, rep)
        self._hole_checks(rep)
        return rep

    # ------------------------------------------------------------ building
    def build(self, out="out", quiet: bool = False) -> dfm.Report:
        out = Path(out).resolve()
        work = out / "work"
        (out / "stl").mkdir(parents=True, exist_ok=True)
        d, parts, rep = self.design, self.sheet_parts(), dfm.Report()

        job = d.job()
        job["bodies"] += [kernel.flat_body(p.lay) for p in parts.values()]
        groups: dict[tuple, list[str]] = {}
        for name, _, _, body in d.instances():
            groups.setdefault(tuple(body.color), []).append(name)
        stl = [{"path": str(out / "stl" / f"group_{i}.stl"), "geom": names, "deflection": 0.04}
               for i, names in enumerate(groups.values())]
        pairs = self._pairs(job)
        job["outputs"] = {
            "measure": [{"name": "assembly", "geom": [n for n, *_ in d.instances()]}],
            "step": [{"path": str(out / f"{p.name}.step"), "geom": p.proto, "product": p.name}
                     for p in parts.values()]
                    + [{"path": str(out / "assembly.step"), "tree": d.step_tree()}],
            "stl": stl,
            "flat_outline": [{"body": f"{p.name}#flat", "z": 0.0} for p in parts.values()],
            "views": [{"name": f"{p.name}_{v}", "geom": p.proto, "toward": list(t),
                       "right": list(r)} for p in parts.values() for v, (t, r) in VIEWS.items()],
            "pairs": [{"a": p[2], "b": p[3], "distance": p[4] != "apart"} for p in pairs],
        }
        res = kernel.run(job, work)
        b = Build(self, out, res, rep, parts, holes=self.holes())

        # Each sheet metal part: the solid, both states, and the DFM rules.
        for p in parts.values():
            m, f = res["bodies"][p.proto], res["bodies"][f"{p.name}#flat"]
            rep.add(f"{p.name}: valid solid", m["valid"] and m["solids"] == 1,
                    f"valid={m['valid']}, solids={m['solids']}, faces={m['faces']}")
            # Fusing overlapping plates or bends loses volume, so the fused
            # solid must equal the sum of its unfused pieces, folded and flat.
            lost = [(st, x["parts_volume"] - x["volume"]) for st, x in (("folded", m), ("flat", f))]
            tol = SELF_TOL * m["volume"]
            rep.add(f"{p.name}: no self-intersection", all(abs(x) <= tol for _, x in lost),
                    ", ".join(f"{st} fuse loses {x:.2e} mm³" for st, x in lost)
                    + f" (limit {tol:.1e})")
            expect = expected_volume_difference(p.lay)
            got = m["parts_volume"] - f["parts_volume"]
            rep.add(f"{p.name}: flat pattern matches fold", abs(got - expect) <= tol,
                    f"folded−flat volume {got:.6f} mm³, bend allowance predicts {expect:.6f}",
                    got, expect)
            dfm.check_layout(p.lay, self.rules, rep)

        vol = {k: v["volume"] for k, v in res["bodies"].items()}
        want = {f"{p.name}.step": vol[p.proto] for p in parts.values()}
        want["assembly.step"] = sum(vol[proto] for _, proto, _, _ in d.instances())
        n_inst = len(list(d.instances()))
        for s in res["step"]:
            name = Path(s["path"]).name
            ok = (abs(s["readback_volume"] - want[name]) <= 1e-6 * want[name]
                  and s["readback_solids"] >= 1)
            if name == "assembly.step":
                ok = ok and s["readback_solids"] == n_inst
            rep.add(f"{name} reads back", ok,
                    f"{s['readback_solids']} solid(s), volume {s['readback_volume']:.3f} mm³ "
                    f"(model {want[name]:.3f})")

        self._fit_checks(res["pairs"], pairs, rep)
        self._hole_checks(rep)
        for fn in self._checks:
            fn(b)

        for p in parts.values():
            fp = flat_pattern(p.lay, res["flat_outline"][f"{p.name}#flat"])
            b.flats[p.name] = fp
            b.tags[p.name] = {bs.bend.name: f"B{i + 1}" for i, bs in enumerate(p.lay.bends)}
            dfm.check_flat_size(p.name, fp.size, self.rules, rep)
            path = out / f"{p.name}_flat.dxf"
            write_dxf(fp, path, p.lay.body.rule.material, p.lay.body.rule.T)
            rep.checks.append(check_dxf(path, fp))

        img = render([(read_stl(s["path"]), color) for color, s in zip(groups, stl)],
                     toward=ISO, size=(1500, 1080))
        img.save(out / "render_iso.png")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b.render_png = buf.getvalue()
        for rname, under in self.renders.items():
            sub: dict[tuple, list[str]] = {}
            for name, _, _, body in d.instances(under):
                sub.setdefault(tuple(body.color), []).append(name)
            jb = {"bodies": job["bodies"], "instances": job["instances"], "outputs": {"stl": [
                {"path": str(out / "stl" / f"{rname}_{i}.stl"), "geom": names, "deflection": 0.04}
                for i, names in enumerate(sub.values())]}}
            kernel.run(jb, work, tag=rname)
            render([(read_stl(s["path"]), c) for c, s in zip(sub, jb["outputs"]["stl"])],
                   toward=ISO, size=(1500, 1080)).save(out / f"{rname}.png")

        part_sheets = (self.part_sheets(b) if self.part_sheets
                       else [sheets.sheet_part(b, p) for p in parts.values()])
        all_sheets = [sheets.sheet_assembly(b), *part_sheets, sheets.sheet_holes(b),
                      sheets.sheet_checks(b)]
        pdfs = []
        for i, sh in enumerate(all_sheets, 1):
            svg = work / f"sheet{i}.svg"
            svg.write_text(sh.svg().replace(SHEET_NO, f"{i} of {len(all_sheets)}"))
            pdf = work / f"sheet{i}.pdf"
            subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(pdf), str(svg)], check=True)
            pdfs.append(str(pdf))
        drawing = out / f"{self.name}_drawing.pdf"
        subprocess.run(["qpdf", "--empty", "--pages", *pdfs, "--", str(drawing)], check=True)
        prev = out / "preview"
        prev.mkdir(exist_ok=True)
        for old in prev.glob("sheet-*.png"):
            old.unlink()
        subprocess.run(["pdftoppm", "-png", "-r", "110", str(drawing), str(prev / "sheet")],
                       check=True)

        report = {
            "project": self.name,
            "summary": rep.summary(),
            "ok": rep.ok,
            "parameters": dict(self.param_rows()),
            "sheet_metal_rules": {p.name: asdict(p.lay.body.rule) for p in parts.values()},
            "shop": ({"shop": self.shop.shop, "material": self.shop.material,
                      "notes": list(self.shop.notes), "sources": self.shop.sources}
                     if self.shop else None),
            "checks": [asdict(c) for c in rep.checks],
            "holes": b.holes,
            "parts_list": self.parts_list(),
            "bodies": res["bodies"],
            "envelope": res["measure"]["assembly"]["bbox"],
            "pairs": [dict(label=p[0], item=p[1], **r) for p, r in zip(pairs, res["pairs"])],
            "flat_sizes": {k: v.size for k, v in b.flats.items()},
            "facts": dict(self.facts),
        }
        (out / "report.json").write_text(json.dumps(report, indent=2, default=float))
        if not quiet:
            print(f"{self.name}: {rep.summary()}  ->  {out}")
            for c in rep.checks:
                print(f"[{c.status:4}] {c.name}: {c.detail}")
        return rep

    def main(self, argv=None) -> int:
        """Command line: [out_dir]. Exit status 1 if any check fails."""
        argv = sys.argv[1:] if argv is None else argv
        return 0 if self.build(argv[0] if argv else "out").ok else 1


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
