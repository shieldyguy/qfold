#!/usr/bin/env bash
# Set up a fresh checkout in about a minute, without compiling OpenCascade:
#   1. shallow-clone the prebuilt OpenCascade branch into .occt/install
#   2. get ezdxf from git if it isn't importable (works without PyPI)
#   3. build sheetkernel against the prebuilt libraries (seconds)
#   4. run the tests
# Safe to re-run; steps that are already done are skipped.
set -euo pipefail
cd "$(dirname "$0")/.."
root=$(pwd)
occt_branch=prebuilt/occt-7.9.3-linux-x86_64
remote=$(git remote get-url origin 2>/dev/null || echo https://github.com/shieldyguy/qfold)

if [ ! -d .occt/install/lib ]; then
  echo "== fetching prebuilt OpenCascade ($occt_branch)"
  mkdir -p .occt
  GIT_LFS_SKIP_SMUDGE=1 git clone -q --depth 1 --branch "$occt_branch" "$remote" .occt/install
fi

if ! python3 -c "import ezdxf" 2>/dev/null && [ ! -d .deps/ezdxf/src ]; then
  echo "== fetching ezdxf from git"
  mkdir -p .deps
  git clone -q --depth 1 https://github.com/mozman/ezdxf .deps/ezdxf
fi

echo "== building sheetkernel"
OCCT_DIR="$root/.occt/install" kernel/build.sh

echo "== running tests"
python3 -m pytest -q tests
