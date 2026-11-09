#!/usr/bin/env bash
# Set up a checkout in about a minute without compiling OpenCascade:
#   0. check the platform and the tools the pipeline needs
#   1. fetch prebuilt OpenCascade from a pinned commit, verify its libraries
#   2. fetch a pinned ezdxf release from git if ezdxf isn't importable
#   3. build sheetkernel against the prebuilt libraries and smoke-test it
#   4. run the tests
# Safe to re-run, and it recovers from an interrupted earlier run.
set -euo pipefail
cd "$(dirname "$0")/.."
root=$(pwd)
die() { echo "bootstrap: $*" >&2; exit 1; }

# Pins. A new prebuilt build means updating occt_* here and
# scripts/occt-prebuilt.sha256 together (see README).
occt_repo=https://github.com/shieldyguy/qfold-prebuilt
occt_branch=occt-7.9.3-linux-x86_64
occt_sha=751a6aaf04624f9572a05a021e3c9cf163f209a5
ezdxf_tag=v1.4.5

# 0. Platform and tools ---------------------------------------------------
[ "$(uname -sm)" = "Linux x86_64" ] ||
  die "the prebuilt OpenCascade is for Linux x86_64 (Ubuntu 24.04, glibc 2.38+); on $(uname -sm), build it with scripts/build_occt.sh"
missing=()
for c in git g++ python3 sha256sum ldd rsvg-convert qpdf pdftoppm; do
  command -v "$c" >/dev/null 2>&1 || missing+=("$c")
done
for m in numpy PIL pytest; do
  python3 -c "import $m" 2>/dev/null || missing+=("python3 module $m")
done
[ ${#missing[@]} -eq 0 ] || die "missing: ${missing[*]}"

# 1. Prebuilt OpenCascade -------------------------------------------------
if [ ! -d .occt/install/lib ] || [ "$(git -C .occt/install rev-parse HEAD 2>/dev/null)" != "$occt_sha" ]; then
  echo "== fetching prebuilt OpenCascade (qfold-prebuilt $occt_branch @ ${occt_sha:0:12})"
  rm -rf .occt/install .occt/install.tmp  # also clears a half-finished earlier clone
  mkdir -p .occt
  GIT_LFS_SKIP_SMUDGE=1 git clone -q --depth 1 --branch "$occt_branch" "$occt_repo" .occt/install.tmp
  got=$(git -C .occt/install.tmp rev-parse HEAD)
  [ "$got" = "$occt_sha" ] || die "$occt_branch is at $got, expected $occt_sha. If that change was intended, verify it, then update occt_sha and scripts/occt-prebuilt.sha256."
  mv .occt/install.tmp .occt/install
fi
(cd .occt/install && sha256sum --quiet -c "$root/scripts/occt-prebuilt.sha256") ||
  die "prebuilt libraries don't match scripts/occt-prebuilt.sha256"

# 2. ezdxf ------------------------------------------------------------------
if ! python3 -c "import ezdxf" 2>/dev/null &&
   [ "$(git -C .deps/ezdxf describe --tags 2>/dev/null)" != "$ezdxf_tag" ]; then
  echo "== fetching ezdxf $ezdxf_tag from git"
  rm -rf .deps/ezdxf .deps/ezdxf.tmp
  mkdir -p .deps
  if git clone -q --depth 1 --branch "$ezdxf_tag" --filter=blob:none --sparse \
       https://github.com/mozman/ezdxf .deps/ezdxf.tmp 2>/dev/null &&
     git -C .deps/ezdxf.tmp sparse-checkout set src; then
    :
  else  # servers without partial clone: plain shallow clone
    rm -rf .deps/ezdxf.tmp
    git clone -q --depth 1 --branch "$ezdxf_tag" https://github.com/mozman/ezdxf .deps/ezdxf.tmp
  fi
  mv .deps/ezdxf.tmp .deps/ezdxf
fi

# 3. Kernel -----------------------------------------------------------------
echo "== building sheetkernel"
OCCT_DIR="$root/.occt/install" kernel/build.sh
if ldd kernel/build/sheetkernel | grep -q "not found"; then
  die "sheetkernel can't find these shared libraries:
$(ldd kernel/build/sheetkernel | grep 'not found')"
fi
python3 - <<'PY' || die "sheetkernel smoke test failed (see the error above)"
import pathlib, tempfile
from qfold import kernel
plate = {"frame": {"o": [0, 0, 0], "u": [1, 0, 0], "v": [0, 1, 0], "n": [0, 0, 1]}, "T": 3.0,
         "outline": [[0, 0], [1, 0], [1, 2], [0, 2]], "holes": []}
with tempfile.TemporaryDirectory() as d:
    res = kernel.run({"bodies": [{"name": "b", "plates": [plate], "sectors": []}],
                      "outputs": {}}, pathlib.Path(d))
    vol = res["bodies"]["b"]["volume"]
    assert abs(vol - 6.0) < 1e-9, vol
print("== sheetkernel smoke test ok")
PY

# 4. Tests ------------------------------------------------------------------
echo "== running tests"
python3 -m pytest -q tests
