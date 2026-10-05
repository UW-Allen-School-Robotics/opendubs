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
#   ./build.sh cost            desktop for ~95 s, then print the dirty-area verdict
#
# Point OPENDUBS_DEMO at the M5Tab5-UserDemo clone (see README.md for how to construct it).
set -euo pipefail

DEMO="${OPENDUBS_DEMO:?set OPENDUBS_DEMO to the M5Tab5-UserDemo clone}"
IDF="${OPENDUBS_IDF:-$(dirname "$DEMO")/esp-idf}"
HERE="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-both}"

sync_sources() {
    # Sync whole DIRECTORIES, not a hand-maintained file list. The list version copied three of
    # the nine files we own; cues.cpp, cost_gate.* and the startup anim were only ever in the
    # build tree because they had been copied by hand. That is the "I don't know what you
    # flashed" failure waiting to happen: edit cues.cpp, run build.sh, flash the previous image.
    for d in app_face app_startup_anim; do
        mkdir -p "$DEMO/app/apps/$d"
        cp "$HERE/$d/"*.cpp "$HERE/$d/"*.h "$DEMO/app/apps/$d/"
    done
    cp "$HERE/lv_conf.h" "$DEMO/lv_conf.h"
    cp "$HERE/partitions.csv" "$HERE/sdkconfig.defaults" "$DEMO/platforms/tab5/"

    # Then PROVE it synced. A silent cp failure looks exactly like a stale build.
    local drift=0
    for f in "$HERE"/app_face/*.cpp "$HERE"/app_face/*.h "$HERE"/app_startup_anim/*.cpp \
             "$HERE"/app_startup_anim/*.h; do
        local rel="${f#$HERE/}"
        cmp -s "$f" "$DEMO/app/apps/$rel" || { echo "NOT SYNCED: $rel"; drift=1; }
    done
    [ "$drift" -eq 0 ] || { echo "refusing to build from a tree that does not match the repo"; exit 1; }
    echo "synced and verified $(ls "$HERE"/app_face/*.cpp "$HERE"/app_face/*.h \
        "$HERE"/app_startup_anim/* | wc -l | tr -d ' ') files into the demo tree"
}

build_device() {
    # sdkconfig is GENERATED and ACCUMULATES. It is not regenerated when sdkconfig.defaults
    # changes, so it silently drifts from the file we actually track: a stale one made every
    # build today 64 KB larger than the repo describes, and the drift is invisible because
    # both builds succeed. If our defaults are newer, delete it and let it regenerate.
    if [ "$HERE/sdkconfig.defaults" -nt "$DEMO/platforms/tab5/sdkconfig" ]; then
        echo "sdkconfig.defaults is newer -- regenerating sdkconfig"
        rm -f "$DEMO/platforms/tab5/sdkconfig"
    fi

    # ALWAYS reconfigure. Both build systems glob app/ at CONFIGURE time, so adding or removing a
    # source file silently builds the old file list -- which has produced a confusing link error
    # three separate times (a deleted app_launcher.cpp, and twice a newly added file).
    ( cd "$DEMO/platforms/tab5" && . "$IDF/export.sh" >/dev/null 2>&1 \
      && idf.py reconfigure >/dev/null && idf.py build \
      && idf.py -p "${PORT:-/dev/cu.usbmodem2101}" flash )
}

build_desktop() {
    local extra="${1:-}"
    # Same reason as build_device: reconfigure so a new or deleted source file is picked up.
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
  # The cost gate: run long enough for the bench to walk every state, emote, activity and theme,
  # since the expensive frame is a particular combination rather than the average.
  cost)    build_desktop
           ( cd "$DEMO" && ./build/desktop/app_desktop_build > /tmp/opendubs-cost.log 2>&1 & )
           sleep 95; grep '\[cost\]' /tmp/opendubs-cost.log | tail -6
           pkill -f app_desktop_build 2>/dev/null || true ;;
  both)    build_device; build_desktop
           echo "both built; run the desktop with: (cd $DEMO && ./build/desktop/app_desktop_build)" ;;
  *)       echo "usage: $0 {both|device|desktop|asan}"; exit 2 ;;
esac
