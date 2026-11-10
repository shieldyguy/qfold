# Direction

What qfold is for, what we're building next, and what we decided not to
build. Read this before adding anything. Update it when a decision changes.

## Goal

Design sheet metal enclosures (synth cases, electronics boxes) by agent, and
order them from a fab shop with nothing left for the shop to guess.

The first real design is the next version of the keybed case. The previous
order (made outside qfold, from a STEP and projected 2D views) came back
about 1 mm too narrow inside: the highest and lowest keys of the 37-key
keybed touch the PEM nuts. Two failures qfold must prevent:

1. **Fit:** a part colliding with or crowding another. Caught by clearance
   checks against real part models (the keybed's STEP in the assembly).
2. **Interpretation:** the shop filling in details the drawing left out.
   Prevented by a flat pattern and tables that call out every hole and bend.

## Principles

- **The checks are the product.** Geometry is only useful if it is verified:
  exact volume identities, read-back of every file, per-part fit rules.
- **Checks prove consistency, not intent.** A hole in the wrong place passes
  every check. Intent is verified by showing it: final hole positions in a
  table, labelled renders, drawings a person can read.
- **Don't build a tool where the LLM will do.** No formula language, no
  constraint solver, no parameter ranges, no fuzzy matching. Designs are
  Python; the agent does the thinking.
- **But the tool does the bend math.** An agent gives outside dimensions
  and names edges. Set-backs, corner treatment and reliefs are the tool's
  job, because an agent redoing them by hand each time gets them wrong.
- **A real design drives every feature.** Nothing is added for a
  hypothetical user. If the keybed case doesn't need it, it waits.
- **Shop data is data.** Bend radius, K and limits come from the shop's
  published tables (`qfold/shop_data`), never from memory.
- **Failing loudly is fine.** A bad parameter that breaks the build in ten
  seconds is useful information; don't guard against it in advance.

## Done

- **Generic runner** (`qfold/project.py`). One `Project` takes any design and
  produces every standard check, declared fits, STEP, DXF, renders, default
  drawing sheets, the holes-as-assembled table and the parameter table. The
  synth case and the pedal box both run on it; `qfold/` holds only the tool
  and each design lives under `examples/`.
- **`Sheet`** (`qfold/sheet.py`): parts described by outside dimensions.
  It works out bend set-backs, bend extents, which wall goes over at a
  corner, and the reliefs (corner squares, wall notches, slots beside
  partial flanges), sized from the shop's rule. Holes are placed by their
  position on the finished part. The pedal box and chassis use it.

- **Corner and relief options** on `Sheet`, named as in Fusion: seam
  (overlap, butt, open), corner relief (square, round, tear) and bend relief
  (straight, round, tear/shear), with sizes from the shop's rule or set by
  hand. Inside or outside dimensions, per sheet or per feature. Outlines
  can carry arcs. Principle: no limits beyond what a shop will make.

## Next, in order

1. **Keybed case v2**, with the keybed and boards as STEP. Add what it
   needs as it needs it: likely candidates are overlap ratio, mitered lips
   where two lips meet at a corner, hems, countersinks.
2. **Synth case onto `Sheet`**, or leave it as the example of the layer
   beneath.
3. **Fresh-agent test.** A new agent, given the README and a spec, builds a
   correct box without reading qfold's internals.

## Known limits

- qfold cannot unfold someone else's STEP. A design made elsewhere is
  re-described in qfold; its STEP can still be inserted to measure against.
- Flat faces and straight bends only. No hems, jogs, countersinks, louvers,
  or panel text yet. Add when a real design needs one.
- K-factor for JLCCNC and bend data for OSH Cut are not public. JLCCNC
  unfolds the STEP itself; if we ever send them flats, ask their support
  for K first.
- Drawings beyond the default sheet are per-design code.

## Process

Commits go straight to `main` (no PRs; it's one owner and one agent).
Every change keeps `scripts/bootstrap.sh` and both examples green on a fresh
clone, and regenerates their committed `out/` folders.

## Open questions

- Keybed case: where did the 1 mm go, model or build? Needs the previous
  STEP, the drawing sent to JLC, and measurements of the received part.
- Which shop and material for v2?
