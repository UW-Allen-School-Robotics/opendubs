#!/usr/bin/env bash
# Build the Tab5 face for the device, the desktop, or both.
#
# This exists because the two drifted: the desktop binary went 45 minutes and several features
# stale while the device kept being flashed, so the simulator was showing old code and could not
# have validated anything. If both are built from one command they cannot diverge.
#
#   ./build.sh both            device + desktop
#   ./build.sh device          idf.py build and flash
#   ./build.sh desktop         SDL build and run
#   ./build.sh asan            desktop with AddressSanitizer + UndefinedBehaviorSanitizer
#
# Point OPENDUBS_DEMO at the M5Tab5-UserDemo clone (see README.md for how to construct it).
set -euo pipefail

DEMO="${OPENDUBS_DEMO:?set OPENDUBS_DEMO to the M5Tab5-UserDemo clone}"
IDF="${OPENDUBS_IDF:-$(dirname "$DEMO")/esp-idf}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-both}"

sync_sources() {
    cp "$HERE/app_face/app_face.cpp" "$HERE/app_face/app_face.h" "$DEMO/app/apps/app_face/"
    cp "$HERE/lv_conf.h" "$DEMO/lv_conf.h"
    echo "synced our files into the demo tree"
}

build_device() {
    ( cd "$DEMO/platforms/tab5" && . "$IDF/export.sh" >/dev/null 2>&1 \
      && idf.py build && idf.py -p "${PORT:-/dev/cu.usbmodem2101}" flash )
}

build_desktop() {
    local extra="${1:-}"
    cmake -S "$DEMO" -B "$DEMO/build-desktop" -DCMAKE_BUILD_TYPE=RelWithDebInfo \
          ${extra:+-DCMAKE_CXX_FLAGS="$extra" -DCMAKE_C_FLAGS="$extra" -DCMAKE_EXE_LINKER_FLAGS="$extra"} \
          >/dev/null
    cmake --build "$DEMO/build-desktop" --target app_desktop_build -j8
}

sync_sources
case "$MODE" in
  device)  build_device ;;
  desktop) build_desktop; ( cd "$DEMO" && ./build/desktop/app_desktop_build ) ;;
  # The sanitizers are the diagnostic the simulator is uniquely good for: a native binary gets
  # AddressSanitizer and UndefinedBehaviorSanitizer, which catch the classes that are invisible on
  # the device -- array overruns (the cobweb point pools), use-after-free, and the integer and
  # float UB that turns into a NaN in a style value.
  asan)    build_desktop "-fsanitize=address,undefined -fno-omit-frame-pointer -g"
           ( cd "$DEMO" && ASAN_OPTIONS=detect_leaks=0 ./build/desktop/app_desktop_build ) ;;
  both)    build_device; build_desktop
           echo "both built; run the desktop with: (cd $DEMO && ./build/desktop/app_desktop_build)" ;;
  *)       echo "usage: $0 {both|device|desktop|asan}"; exit 2 ;;
esac
