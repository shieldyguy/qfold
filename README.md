# Prebuilt OpenCascade for qfold

Minimal OpenCascade Technology 7.9.3 (tag `V7_9_3` of
https://github.com/Open-Cascade-SAS/OCCT), built so qfold sessions don't have to
compile it. It contains the modelling toolkits, STEP/STL exchange and
hidden-line removal, with no OpenGL, Tcl or Draw.

- Platform: Linux x86_64, Ubuntu 24.04 (glibc 2.39), GCC 13.3, Release, shared libraries (stripped)
- Runtime needs system libX11, libfreetype and libfontconfig
- Built with `build_occt.sh` in this branch, the same as `scripts/build_occt.sh` on `main`
- Layout: `lib/` (shared libraries) and `include/opencascade/` (headers)

This branch holds build artifacts only and has no history in common with `main`.
qfold's `scripts/bootstrap.sh` clones it shallowly into `.occt/install`.

OpenCascade is distributed under the GNU LGPL 2.1 with the OCCT exception; see
`LICENSE_LGPL_21.txt` and `OCCT_LGPL_EXCEPTION.txt`. Source: the tag above.
