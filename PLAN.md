# Plan: address the review findings

Source: review on PR #1, https://github.com/shieldyguy/qfold/pull/1#pullrequestreview-5821488287
(finding IDs H1, M1–M5, L1–L10 below refer to that review). Written 2026-11-08
against main 3cf80a2 and PR #1 a88e112. Line numbers are from those commits.

Goal: none of the three demonstrated ways a wrong part passes every check
(same-body interference, bends meeting at an unrelieved corner, a vacuous probe)
survives; the DXF gets an independent check; the bootstrap fails fast and
reproducibly. Ordered so each PR is small, testable and leaves main green.

Reading this from another session: `git fetch origin review-plan && git show
origin/review-plan:PLAN.md`, or `gh pr view 4`.

**Status note (2026-11-08, later):** issue #3 assigns owners — Builder takes
H1–M4 and L1–L9 on PR #2 (`assembly-align`), M5/L7/L10 on PR #1; Reviewer takes
the adversarial tests (PR A below). Treat #3 as authoritative for *who/where*;
this file is the reference for *how*. PR #2 renamed the API to Fusion terms
(`SheetMetalRule`, `Flange`, `SheetMetalBody`, `Design/Component/Occurrence`;
kernel job = `bodies` + `instances`), so names below (`Face`, `Bend`,
`CaseParams`, `plates3d`) are the pre-rename ones; the mechanics are unchanged.

---

## PR A — tests first (locks in what is right today)

No behaviour change. Tests that should pass now, plus `xfail` tests for the
false passes, so PRs B and C have something to flip.

- [ ] `tests/test_fold_kernel.py` (skip if `kernel.KERNEL` is missing). Port the
  adversarial set from the review: angles ±1, ±10, ±45, ±90, ±135, ±170, 179,
  179.9; radius 0 / 0.2 / 20; roots N=−z, yz-plane, left-handed, offset+rotated;
  nested 90/−90/60/−120; perpendicular flange off a wall's v-edge; 30° slanted
  bend edge (±); partial-span bend; child wider than its bend span. Assert per
  part: `valid`, `solids == 1` in both states; `|folded − flat − expected| <
  1e-4·V` (tightened in PR B); fused volume equals the analytic sum
  Σ(outline area − hole areas)·T + Σ sectors (resp. + Σ strips) within 1e-3.
  Analytic sum: shoelace on `face.outline`, circle/rrect areas for holes.
- [ ] `tests/test_flat_convention.py`, pure Python. Refactor `flat.py:50–59` so the
  outside-view transform is callable without the kernel:
  `outside_view(lay) -> (tp, ta)` (or `to_outside(lay, p2d)`), and have
  `flat_pattern` use it. Test on an L-bracket whose wall has a hole at v=40 of 50:
  - root N=+z (right-handed): the wall's hole maps to x *less than* every base
    point (wall appears left of the base), y ≈ 40 relative to the base's v=0;
  - root N=−z: the wall's hole maps to x *greater than* every base point;
  - `angle > 0 → "DN"`, `angle < 0 → "UP"` for both roots.
  These are the signs verified by hand on the synth case (shell mirrored, panel
  not).
- [ ] `tests/test_checks_catch_bad_parts.py` (skip without kernel). Run
  `kernel_checks + dfm.check_layout` on variants of `CaseParams`:
  - `corner_gap=-0.05` → expect a failing check (**xfail until PR B/C**);
  - `panel_clearance=-0.05` → interference fails (passes today);
  - a screw probe displaced 20 mm outside the case → expect a failure
    (**xfail until PR B**);
  - a DXF written with `fp.circles = []` → `check_dxf` fails (**xfail until PR B**).
- [ ] Factor the check-suite runner used by those tests out of `pipeline.main`
  (`run_checks(case, out) -> (res, rep)`), so tests and the pipeline share it.

Size: ~250 lines of tests, ~20 lines moved in pipeline.py.

## PR B — exact checks (H1, M2, M3, M4, L1, L2)

Kernel (`kernel/sheetkernel.cpp`):
- [ ] `fmt` → `%.17g` (line 267). All reported measurements, not just volume.
- [ ] Per body, before `fuseAll` (line 687): sum `BRepGProp::VolumeProperties`
  over `parts`; report as `"sum_parts_volume"` next to `volume` in
  `measureJson`'s caller (store it on `Body`).
- [ ] Per body, optional `"clearance_pairs": [[i, j], ...]` (indices into
  plates ++ sectors). For each pair report
  `BRepExtrema_DistShapeShape(parts[i], parts[j]).Value()` as
  `"part_clearances": [{"i":..,"j":..,"d":..}]`. Needed by PR C but cheap to
  land here.
- [ ] `quote()` (270–281): escape every char `< 0x20` as `\u00XX`.
- [ ] `main` returns `ok ? 0 : 1` (line 807).

Python:
- [ ] `kernel.py:41` `json.dumps(..., allow_nan=False)`; in `run`, if the result
  file is missing, raise `RuntimeError` carrying `proc.stdout`/`stderr`; message
  at `kernel.py:43` → "run scripts/bootstrap.sh".
- [ ] `pipeline.kernel_checks`:
  - new check `"{name}: no self-overlap"` for folded **and** flat bodies:
    `|sum_parts_volume − volume| ≤ 1e-6·V + 1e-6`. This is the detector for
    same-body interference and flat self-overlap (fails on `corner_gap=-0.05`,
    where today's check passes with an 11 mm³ discrepancy).
  - bend-allowance identity on the unfused sums:
    `|Σ_fold − Σ_flat − expected| ≤ 1e-9·V + 1e-6` (it is an exact identity);
    keep the fused-volume comparison but at the same tolerance.
  - screw probes: Python knows the hole on each body (cheek 3.4, lip 4.22); add
    `"must_pass": {"bottom_shell": 3.4, "top_panel": 4.22}` to each probe spec
    and assert `common_volume == 0` **and**
    `min_distance ≤ (hole_d − shank_d)/2 + 0.05` per body (0.25 / 0.66 mm), so
    a shank that misses the part is not "clear". For `pcb_keepout` assert the
    box's eight corners lie inside the shell's bbox.
- [ ] `pipeline.check_dxf` census, compared with the `FlatPattern`:
  `CIRCLE` centres + diameters (sorted, 1e-6), `ARC` count, `LINE` count on
  CUT, total CUT length (lines + arcs + circles, 1e-6 relative), `BEND` line
  endpoints (1e-6), and each NOTES text containing `f"{bend.name} {direction}"`.
- [ ] Flip the xfails from PR A. Regenerate `examples/synth_case` (report.json
  gains keys; check count changes) and the "26" in README.

Size: ~60 lines C++, ~90 lines Python.

## PR C — corner relief and same-body clearance (M1)

Design (`qfold/design.py`):
- [ ] `CaseParams.corner_relief: float = 2.0`. Square relief at each base
  corner: the base outline becomes the rectangle with a `cr × cr` square removed
  at each corner, and every bend span is shortened by `cr` at both ends
  (`front`/`back`: v ∈ [cr, L_x − cr]; `right`/`left`: v ∈ [cr, L_y − cr]).
  `_lip_outline` and `cheek` take the stem range `[cr, L − cr]` instead of
  `[0, L]`; the full-width extensions beyond `rd` are unchanged. Effect: in the
  flat, no two bend zones share a vertex (each corner is a `cr`-square notch past
  both bend ends); in 3D a `cr × cr` opening at each bottom corner, in the region
  that is already open up to z = 2a. Round reliefs (Ø ≥ T at the bend-line
  intersection) are the nicer standard but need holes cut through the whole
  body (L5); do that later if wanted.
- [ ] The panel's notches already clear the lip ends by `relief_width`; no change.

DFM (`qfold/dfm.py`):
- [ ] `Rules.min_corner_relief: float | None = None` → default `T`. Rule: for
  each face, for each pair of bends on that face, if any endpoint of one bend
  lies within `min_corner_relief` of any endpoint of the other, fail
  "bends X and Y meet at a corner without relief" — unless the bend endpoints
  are at least that far from the face outline's vertex nearest to them (i.e. a
  notch or hole exists). Simplest implementation that is right for both styles:
  compute `d = min distance between the two bends' endpoint sets`; pass if
  `d ≥ min_corner_relief`.
- [ ] Same-body clearance check using the kernel pairs from PR B. Python builds
  the pair list from the layout: all part pairs except (plate, sector of a bend
  on that plate), (sector, its child plate), (parent plate, child plate through
  one bend). Sector–sector pairs on the same parent **are** included — that is
  the unrelieved-corner case (today's shell measures 0 there). Check:
  `min d ≥ Rules.min_clearance`. Report the worst pair with face names.
- [ ] Regenerate `examples/synth_case` (DXF, STEP, drawings, report). Check the
  render: corner openings are expected to be `(a + cr)` square at the bottom
  corners.

Size: ~70 lines design/dfm, ~30 lines pipeline. Depends on PR B's kernel pairs.

## PR D — bootstrap hardening (M5, L7, L10) — follow-up to PR #1

`scripts/bootstrap.sh`:
- [ ] Fail fast: `case "$(uname -sm)" in "Linux x86_64") ;; *) echo "prebuilt
  OpenCascade is Linux x86_64 (Ubuntu 24.04, glibc ≥ 2.38) only; build from
  source with scripts/build_occt.sh" >&2; exit 1;; esac`.
- [ ] Prerequisites: `g++`, `rsvg-convert`, `qpdf`, `pdftoppm` on PATH;
  `python3 -c "import numpy, PIL, pytest"`. One line per missing item, then exit.
- [ ] Atomic fetch: clone into `.occt/install.tmp`, verify, then
  `mv` to `.occt/install`; `rm -rf .occt/install.tmp` first. Fixes the wedged
  state after an interrupted first run.
- [ ] Pin: `occt_commit=751a6aaf…` next to `occt_branch`; after cloning,
  `git rev-parse HEAD` must equal it. Add `scripts/occt-prebuilt.sha256`
  (sha256 of `lib/*.so.7.9.3`) and run `sha256sum -c` before the move.
- [ ] ezdxf: `--branch <validated release tag>` instead of master (today's
  master is `1.4.6b0`). Optionally `--filter=blob:none` + sparse-checkout of
  `src/` (40 MB → ~10 MB). Verify `python3 -c "import ezdxf"` after cloning;
  the tests do not import it.
- [ ] Smoke test after `kernel/build.sh`: `ldd kernel/build/sheetkernel | grep
  "not found"` → list them and exit; then run the binary on a one-plate job and
  require `"ok":true`. Turns a glibc/libX11 problem into a one-line message.
- [ ] `qfold/flat.py:22–27`: try `import ezdxf` first; only on `ImportError`
  insert `.deps/ezdxf/src` into `sys.path`.
- [ ] README: "about a minute" → "about 10 seconds"; list the runtime system
  libraries (libX11, libfreetype, libfontconfig, libpng, libexpat, libbz2,
  libbrotli) until the next prebuilt drops them.

Next prebuilt (separate, later):
- [ ] Rebuild with `-DUSE_FREETYPE=OFF -DUSE_XLIB=OFF` in `scripts/build_occt.sh`
  and check `ldd`; if the kernel still links, the prebuilt needs only glibc,
  libstdc++ and zlib.
- [ ] Decide: keep the orphan branch (every full clone of qfold fetches it;
  +~27 MB history per rebuild) or move to a `qfold-prebuilt` repo (clone stays
  small, SHA pin works the same way through the git proxy). Recommendation:
  separate repo.

Size: ~50 lines bash, ~5 lines Python, README.

## PR E — low items and docs (L3, L4, L5, L6, L8, L9)

- [ ] `dfm.check_layout`: use `bend.radius or material.R` per bend for the
  hole-to-bend threshold (L3).
- [ ] `_extent_u`: if no crossing, skip that sample instead of returning −inf;
  fail with a clear message if *all* samples miss (L4).
- [ ] Hole-crossing-plate guard: in `fold.layout`, raise if any hole boundary
  leaves its face outline (shoelace/segment test); keeps the kernel's per-plate
  cut honest even if DFM thresholds are relaxed (L5).
- [ ] `check_dxf`: compute `fp.size` from exact arc extents (angle-range
  aware), and compare with 1e-6 **relative** (L9).
- [ ] README: "proves the flat pattern and the folded part agree" → "proves the
  two generated states agree with each other and with the bend-allowance
  formula; R and K still come from your fabricator"; "dimensions … read from
  the model" → hole tables and the envelope come from the model, overall
  dimensions on sheets 2 and 4 are the design parameters (L8).
- [ ] Optional: assembly STEP as an XCAF document with two components (L6).
  Separate PR; touches the kernel's link line (TKXCAF/TKDESTEP already linked).

## Done when

- `CaseParams(corner_gap=-0.05)` fails exactly `bottom_shell: no self-overlap`
  and the same-body clearance check; `corner_gap=0.4` passes everything.
- Today's shell (no corner relief) fails the corner-relief DFM rule and the
  sector–sector clearance; with `corner_relief=2.0` both pass.
- A probe moved 20 mm outside the case fails the screw check; the real probes
  pass with `min_distance` 0.2 / 0.61.
- A DXF with a missing circle, a mirrored outline, or a swapped DN label fails
  `check_dxf`; the real DXFs pass.
- `bash -c 'scripts/bootstrap.sh & sleep 2; kill %1'` followed by
  `scripts/bootstrap.sh` succeeds; on a non-x86_64 Linux box the script exits
  within a second with one explanatory line.
- `python3 -m pytest tests` runs the convention test without the kernel and the
  rest with it; the example regenerates byte-identically from a clean checkout
  apart from the date in the title block.

## Open decisions for the owner

1. Square vs round corner relief (round needs through-body hole cutting).
2. Separate `qfold-prebuilt` repo vs orphan branch.
3. Whether `min_clearance` (0.2) is also the right floor for sector–sector
   pairs, or whether corner relief gets its own value.
4. XCAF assembly now or later.
