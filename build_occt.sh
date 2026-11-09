#!/usr/bin/env bash
# Build a minimal OpenCascade (modelling, STEP/STL, hidden-line removal) from
# source. Needs only git, cmake, ninja, a C++17 compiler, FreeType and Xlib
# headers; no package index. About 40 minutes on 2 cores.
#
#   scripts/build_occt.sh [prefix]    # default prefix: ./.occt
set -euo pipefail
prefix=${1:-$(pwd)/.occt}
mkdir -p "$prefix"
prefix=$(cd "$prefix" && pwd)
src="$prefix/src"
[ -d "$src/.git" ] || git clone --depth 1 --branch V7_9_3 \
    https://github.com/Open-Cascade-SAS/OCCT "$src"
cmake -G Ninja -S "$src" -B "$prefix/build" -DCMAKE_BUILD_TYPE=Release \
  -DINSTALL_DIR="$prefix/install" \
  -DBUILD_MODULE_FoundationClasses=OFF -DBUILD_MODULE_ModelingData=OFF \
  -DBUILD_MODULE_ModelingAlgorithms=OFF -DBUILD_MODULE_Visualization=OFF \
  -DBUILD_MODULE_ApplicationFramework=OFF -DBUILD_MODULE_DataExchange=OFF \
  -DBUILD_MODULE_DETools=OFF -DBUILD_MODULE_Draw=OFF \
  -DBUILD_ADDITIONAL_TOOLKITS="TKDESTEP TKDESTL TKHLR TKOffset TKFillet TKBool TKMesh TKFeat TKPrim TKBO TKShHealing" \
  -DUSE_TCL=OFF -DUSE_TK=OFF -DUSE_OPENGL=OFF -DUSE_GLES2=OFF -DUSE_FREETYPE=ON -DUSE_XLIB=ON \
  -DUSE_FREEIMAGE=OFF -DUSE_FFMPEG=OFF -DUSE_VTK=OFF -DUSE_TBB=OFF -DUSE_RAPIDJSON=OFF \
  -DUSE_DRACO=OFF -DBUILD_DOC_Overview=OFF -DBUILD_SAMPLES_QT=OFF
ninja -C "$prefix/build" -j"$(nproc)" install
echo "OpenCascade installed in $prefix/install"
