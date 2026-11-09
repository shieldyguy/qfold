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
  constraint solver, no parameter ranges, no fuzzy matching, no high-level
  design language. Designs are Python; the agent does the thinking.
- **A real design drives every feature.** Nothing is added for a
  hypothetical user. If the keybed case doesn't need it, it waits.
- **Shop data is data.** Bend radius, K and limits come from the shop's
  published tables (`qfold/shop_data`), never from memory.
- **Failing loudly is fine.** A bad parameter that breaks the build in ten
  seconds is useful information; don't guard against it in advance.

## Next, in order

1. **Generic runner.** One call takes any design and produces: all standard
   checks on every sheet part, declared fit rules, STEP, DXF, renders, a
   default drawing sheet per part (flat pattern, hole table, bend table),
   a table of every hole's final position, and the parameter table in
   `report.json` and on the drawing. The synth case moves onto it and must
   still pass its 39 checks.
2. **Generated reliefs.** The fold engine cuts bend and corner reliefs,
   sized from the shop's rules, closed corners by default. Today they are
   hand-drawn into face outlines, oversized and inconsistent.
3. **Keybed case v2**, built on 1 and 2, with the keybed and boards as STEP.
4. **Fresh-agent test.** A new agent, given the README and a spec, builds a
   correct box without reading qfold's internals.

Small helper allowed alongside 1: place a wall hole "as seen from outside"
(x from the left, z from the bottom), since wall coordinates are easy to
mirror.

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
Every change keeps `scripts/bootstrap.sh` and the example pipeline green on
a fresh clone.

## Open questions

- Keybed case: where did the 1 mm go, model or build? Needs the previous
  STEP, the drawing sent to JLC, and measurements of the received part.
- Which shop and material for v2?
