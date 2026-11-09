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

You need Python 3.11+ with numpy and Pillow, plus `ezdxf`, `rsvg-convert`,
`qpdf` and `pdftoppm`. You also need an OpenCascade 7.9 install to build the
kernel.

```bash
scripts/build_occt.sh .occt              # minimal OCCT from source (~40 min on 2 cores)
OCCT_DIR=$PWD/.occt/install kernel/build.sh
python -m qfold.pipeline out
python -m pytest tests
```

On a machine with a package index, `pip install ezdxf` is enough for ezdxf. In
sandboxes without PyPI, everything comes from git:

```bash
git clone --depth 1 https://github.com/mozman/ezdxf .deps/ezdxf
export QFOLD_EZDXF_SRC=$PWD/.deps/ezdxf/src EZDXF_DISABLE_C_EXT=1
```

## Next

- Bend tables (R and K by material and thickness) instead of one K-factor
- Hems, jogs and non-90° bends in the example
- A component library: jack, pot and switch footprints with keep-outs
- PCB import (KiCad STEP) for clearance checks
- An MCP server so an agent can call build, measure, render and check as tools
