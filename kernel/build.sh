#!/usr/bin/env bash
# Build the sheetkernel binary against an OpenCascade install.
#   OCCT_DIR=/path/to/occt/install kernel/build.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
OCCT_DIR=${OCCT_DIR:-$here/../.occt/install}  # scripts/bootstrap.sh puts it here
[ -d "$OCCT_DIR/lib" ] || { echo "no OpenCascade at $OCCT_DIR; run scripts/bootstrap.sh" >&2; exit 1; }
inc="$OCCT_DIR/include/opencascade"
lib=$(cd "$OCCT_DIR/lib" && pwd)
mkdir -p "$here/build"
libs=(TKDESTL TKDESTEP TKDE TKXCAF TKVCAF TKV3d TKService TKCAF TKLCAF TKCDF TKXSBase
      TKHLR TKMesh TKOffset TKFillet TKFeat TKBool TKBO TKPrim TKShHealing TKTopAlgo
      TKGeomAlgo TKBRep TKGeomBase TKG3d TKG2d TKMath TKernel)
g++ -O2 -std=c++17 -Wall -Wextra -Wno-unused-parameter -I"$inc" \
    "$here/sheetkernel.cpp" -o "$here/build/sheetkernel" \
    -L"$lib" -Wl,--disable-new-dtags,-rpath,"$lib" "${libs[@]/#/-l}" -lpthread
echo "built $here/build/sheetkernel"
