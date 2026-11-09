# qfold

Lightweight, agent-friendly sheet metal CAD. You describe a part as a tree of
flat faces joined by bends; qfold turns that into exact solids, flat patterns,
manufacturing checks, renders and drawings. The geometry kernel is
[OpenCascade](https://github.com/Open-Cascade-SAS/OCCT), the same one under
FreeCAD and build123d, driven by a small C++ tool.

The first worked example is a desktop synth case: a folded bottom shell (base,
front lip, tall rear wall, wedge cheeks) and a sloped top panel with folded lips
that carry PEM nuts.

![Synth case render](examples/synth_case/render_iso.png)

## What one run produces

`python -m qfold.pipeline out` builds the synth case in about 15 seconds and
writes:

| File | What it is |
| --- | --- |
| `bottom_shell.step`, `top_panel.step`, `assembly.step` | Exact B-rep solids, mm |
| `*_flat.dxf` | Flat patterns: `CUT` layer, `BEND` centre lines, `NOTES` |
| `synth_case_drawing.pdf` | 4 A3 sheets: assembly, shell views, shell flat, panel |
| `preview/sheet-*.png` | PNG of each sheet |
| `render_iso.png`, `*.stl` | Shaded render and meshes |
| `report.json` | Every measurement and check result |

See [`examples/synth_case`](examples/synth_case) for a committed run.

## Checks

Every run checks its own output. The example passes all 26:

- **Kernel:** each part is one valid solid. Folded minus flat volume equals
  what the bend allowance predicts (θ·L·T²·(0.5 − K) per bend), which proves
  the flat pattern and the folded part agree.
- **Assembly:** no interference between parts, minimum clearance, M3 screw
  shanks pass through the cheek and lip holes, PCB keep-out clears the case.
- **DFM:** hole to bend, hole to edge, web between holes, minimum hole size,
  hardware centre-to-edge distance (PEM), minimum flange length, sheet size.
- **Files:** every STEP is read back and its volume compared with the model.
  Every DXF is re-opened and its extent and bend count compared.

These caught two real problems while the example was being built. The first
was a PEM nut too close to the lip bend, fixed by deepening the lip. The second
was the panel's back edge poking 0.01 mm into the rear wall, fixed by allowing
for the edge's tilt.

## How it works

```
design.py   parameters -> tree of Face/Bend (+ holes, hardware)
fold.py     tree -> folded plates + bend sectors (3D) and flat plates + strips
kernel/     sheetkernel (C++/OCCT): fuse, STEP/STL, measure, HLR, probes
dfm.py      manufacturing rules on the layout
flat.py     flat pattern in outside-face view, DXF export
render.py   numpy z-buffer renderer (no GPU or display)
drawing.py  SVG sheets: views from hidden-line removal, dims, tables
sheets.py   the synth case's four sheets
pipeline.py runs it all, writes report.json
```

Python owns the design and fold math; the kernel owns geometry truth. Both
states of the part are generated from the same tree, so there is no unfold step
that can fail. Dimensions and hole tables on drawings are read from the model.

### Conventions

- Each face has a frame (O, U, V, N). Its 2D (u, v) coordinates lie on the
  *reference surface*, and material spans 0..T along N.
- A bend hangs off a straight parent edge (start p0, direction d, outward o).
  A positive angle folds toward the material side, so the reference surface is
  the outside of the bend and outer dimensions are reference dimensions.
- Bend allowance is θ·(R + K·T), with R the inside radius.
- Flat patterns are shown from the outside face. `DN` means the bend folds away
  from the viewer.

## Placeholders to replace before cutting

These are marked on every drawing:

- Material, inside radius (R1.6) and K-factor (0.42): use your fabricator's
  values or bend table.
- DFM thresholds in `dfm.Rules`.
- Hole sizes for jacks, pots, switches and the PEM centre-to-edge minimum:
  check the part datasheets.

## Setup

On a fresh checkout, one command gets everything and runs the tests. It takes
about a minute and needs only git, so it works in sandboxes without PyPI:

```bash
scripts/bootstrap.sh
python -m qfold.pipeline out
```

The bootstrap script:

- **Checks the platform and tools.** It stops early, with a clear message, if
  the machine isn't Linux x86_64 or is missing git, g++, `rsvg-convert`,
  `qpdf`, `pdftoppm`, numpy, Pillow or pytest.
- **Fetches OpenCascade.** It shallow-clones the `occt-7.9.3-linux-x86_64`
  branch of [qfold-prebuilt](https://github.com/shieldyguy/qfold-prebuilt) into
  `.occt/install`. That repo holds compiled libraries and headers only, for
  Linux x86_64 on Ubuntu 24.04 (glibc 2.38+), so cloning qfold itself stays
  small. The clone must match the commit pinned in the script, and the
  libraries must match `scripts/occt-prebuilt.sha256`. A moved branch or an
  altered library stops the bootstrap instead of being linked silently. An
  interrupted clone is cleared and retried.
- **Gets ezdxf.** If `ezdxf` isn't importable, it sparse-clones the pinned
  release (`src/` only) into `.deps/`. An installed ezdxf takes precedence.
- **Builds sheetkernel and smoke-tests it.** Compiling takes seconds. It then
  checks that every shared library resolves and that a tiny job returns the
  right volume, so glibc or library problems show up here in one line.

On another platform, or to change OpenCascade's build options, build it from
source once and point the kernel at it. This takes about 40 minutes on 2 cores:

```bash
scripts/build_occt.sh .occt
kernel/build.sh                # uses .occt/install by default, or set OCCT_DIR
```

To publish a new prebuilt build:

1. In qfold-prebuilt, commit the stripped `lib/` and `include/opencascade/`,
   with the OCCT licence files, on a new orphan branch named for the version
   and platform.
2. Regenerate the manifest: `(cd <install> && sha256sum lib/*.so.*.*.*) >
   scripts/occt-prebuilt.sha256`.
3. Update `occt_branch` and `occt_sha` in `scripts/bootstrap.sh`.

Don't move an existing prebuilt branch; publish a new one. The SHA pin and
the manifest stop a moved branch from being used anyway.

## Next

- Bend tables (R and K by material and thickness) instead of one K-factor
- Hems, jogs and non-90° bends in the example
- A component library: jack, pot and switch footprints with keep-outs
- PCB import (KiCad STEP) for clearance checks
- An MCP server so an agent can call build, measure, render and check as tools
