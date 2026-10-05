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
    # The dependency manifest is OURS and tracked -- it holds three version pins that were each
    # found the hard way and that used to exist only inside the clone.
    cp "$HERE/idf_component.yml" "$DEMO/platforms/tab5/main/idf_component.yml"

    # DROP THE 934 KB MP3 WE NEVER PLAY. The vendor embeds audio/canon_in_d.mp3 via
    # EMBED_TXTFILES for a music demo: 956,208 bytes, about 25% of our entire 3.94 MB image, and
    # almost exactly what esp-dl costs (~726 KB code + 196 KB model). Nothing in the face plays it.
    # Removing the embed alone breaks the link, because hal_audio.cpp declares the _binary_
    # symbols, so the music test's canon case is pointed at the short startup sfx instead. The two
    # small sfx (42 KB together) stay -- not worth a third vendor edit.
    #
    # Patched here rather than tracked as a whole vendor file copy, so a vendor change to
    # CMakeLists is inherited instead of silently overwritten. Idempotent, and it FAILS LOUDLY if
    # an anchor moves rather than quietly building the 934 KB version again.
    python3 - "$DEMO/platforms/tab5" <<'PATCH'
import pathlib, sys
root = pathlib.Path(sys.argv[1])

cm = root / "main/CMakeLists.txt"
t = cm.read_text()
if '"../audio/canon_in_d.mp3" ' in t:
    t = t.replace('"../audio/canon_in_d.mp3" ', '')
    cm.write_text(t)
    print("  patched: canon_in_d.mp3 un-embedded (-934 KB)")
elif 'canon_in_d.mp3' in t:
    sys.exit("REFUSING: canon_in_d.mp3 is embedded in a form this patch does not recognise")

ha = root / "main/hal/components/hal_audio.cpp"
t = ha.read_text()
old = """extern const uint8_t canon_in_d_mp3_start[] asm("_binary_canon_in_d_mp3_start");
extern const uint8_t canon_in_d_mp3_end[] asm("_binary_canon_in_d_mp3_end");"""
if old in t:
    t = t.replace(old, """// canon_in_d.mp3 is no longer embedded -- see build.sh. Aliased to the startup sfx so the
// vendor's music test still links and still makes a noise, without 934 KB of payload.
#define canon_in_d_mp3_start startup_sfx_mp3_start
#define canon_in_d_mp3_end   startup_sfx_mp3_end""")
    ha.write_text(t)
    print("  patched: hal_audio.cpp canon symbols aliased")
elif "canon_in_d_mp3_start[] asm" in t:
    sys.exit("REFUSING: hal_audio.cpp declares the canon symbols in an unrecognised form")
PATCH
    # esp-dl is appended ONLY for the probe: in the default dependency set it aborts at boot with
    # an i2c driver-generation conflict (see idf_component.yml). Keeping it out of the tracked
    # manifest means a normal build cannot inherit that.
    if [ "$PROBE_FACEDET" = "1" ]; then
        printf '  espressif/esp-dl: 3.1.3\n  espressif/human_face_detect: 0.2.3\n' \
            >> "$DEMO/platforms/tab5/main/idf_component.yml"
    fi

    # Generate the probe's reference image from the committed .jpg EVERY build. Generated, never
    # committed: a checked-in generated file is the thing that drifts (today's lesson, three times).
    # Wi-Fi credentials, if supplied, as a GENERATED HEADER in the ephemeral build tree.
    # NEVER written into this repo: per-robot secrets do not belong in git, and the generated
    # file lives only in the clone. Needed once per board -- esp_wifi persists to NVS after a
    # successful association, so later builds can omit them entirely.
    if [ -n "${OPENDUBS_WIFI_SSID:-}" ]; then
        printf '#pragma once\n#define OPENDUBS_WIFI_SSID "%s"\n#define OPENDUBS_WIFI_PASS "%s"\n' \
            "$OPENDUBS_WIFI_SSID" "${OPENDUBS_WIFI_PASS:-}" \
            > "$DEMO/app/apps/app_face/net_credentials.h"
        echo "wifi credentials: compiled in for SSID '$OPENDUBS_WIFI_SSID' (one-time provisioning)"
    else
        printf '#pragma once\n// no build-time credentials; NVS-stored ones will be used\n' \
            > "$DEMO/app/apps/app_face/net_credentials.h"
    fi

    # OPENDUBS_NO_MARK_VALID=1 builds the deliberately-bad image for the rollback test: it runs
    # normally but never declares itself good, so the next reboot must revert it.
    if [ "${OPENDUBS_NO_MARK_VALID:-0}" = "1" ]; then
        echo "#pragma once
#define OPENDUBS_NO_MARK_VALID 1" > "$DEMO/app/apps/app_face/rollback_test.h"
        echo "ROLLBACK TEST IMAGE: this build will NOT mark itself valid"
    else
        echo "#pragma once" > "$DEMO/app/apps/app_face/rollback_test.h"
    fi

    # The probe toggle is a GENERATED HEADER rather than a -D, because idf.py -D sets a CMake
    # variable, not a compile definition, and bridging it would mean editing the vendor's
    # CMakeLists -- a file we do not own and do not sync.
    if [ "$PROBE_FACEDET" = "1" ]; then
        echo "#pragma once
#define OPENDUBS_PROBE_FACEDET 1" > "$DEMO/app/apps/app_face/face_probe_config.h"
        echo "face-detection probe: ON"
    else
        echo "#pragma once
// probe off -- set OPENDUBS_PROBE_FACEDET=1" > "$DEMO/app/apps/app_face/face_probe_config.h"
    fi

    python3 - "$HERE/probe/human_face.jpg" > "$DEMO/app/apps/app_face/face_probe_image.h" <<'EMBED'
import sys, pathlib
b = pathlib.Path(sys.argv[1]).read_bytes()
print("// GENERATED by build.sh from firmware/tab5_idf/probe/human_face.jpg -- do not edit, do not commit.")
print("#pragma once")
print("#include <stdint.h>")
print(f"// {len(b)} bytes")
print("static const uint8_t kFaceProbeJpeg[] = {")
for i in range(0, len(b), 16):
    print("    " + ",".join(f"0x{c:02x}" for c in b[i:i+16]) + ",")
print("};")
EMBED

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

# Set OPENDUBS_PROBE_FACEDET=1 to compile in the face-detection self-test (app_face/face_probe.cpp).
# Off by default and it costs nothing when off: with esp-dl present but unreferenced the app image
# is byte-identical, because the linker drops it, model data included.
PROBE_FACEDET="${OPENDUBS_PROBE_FACEDET:-0}"

# PORT defaults to whichever /dev/cu.usbmodem* is present, because macOS numbers the physical USB
# SOCKET, not the device: the same Tab5 was usbmodem2101 in one port and usbmodem101 in another,
# and CLAUDE.md's documented number is only true for one socket.
#
# Auto-detecting is safe here for a specific reason, not by luck: idf.py passes --chip esp32p4, and
# esptool REFUSES to write to a chip that does not match. So if the port that happens to be present
# is the StamPLC (an ESP32-S3, and whose own platformio.ini names /dev/cu.usbmodem101), the flash
# aborts instead of writing face firmware onto the robot's safety controller. Do not remove the
# --chip argument to "make flashing more flexible"; it is the guard.
build_device() {
    # sdkconfig IS TRACKED BY THE VENDOR -- it is not a throwaway artifact, and deleting it to
    # "regenerate cleanly" is what broke the boot: 94 KB of vendor configuration was replaced by
    # whatever our 1.7 KB sdkconfig.defaults plus Kconfig defaults produced, and the result aborted
    # at 1307 ms with "i2c: CONFLICT! driver_ng is not allowed to be used with this old driver".
    #
    # Worse, it also revealed that sdkconfig.defaults had NEVER APPLIED to a device build: IDF only
    # consults it when sdkconfig is ABSENT, and sdkconfig has existed since the clone. Our settings
    # were inert -- which is why CONFIG_LV_USE_PERF_MONITOR appeared set in our tracked file while
    # the FPS counter was never on screen.
    #
    # So: regenerate from an ORDERED list of tracked files -- the vendor's config as the base, our
    # deltas applied on top. Deterministic, reproducible, and our overrides actually take effect.
    # FORCE THE BOOTLOADER TO REBUILD when our config changes. The bootloader is a separate CMake
    # subproject with its own generated config, and it does NOT reliably pick up a change to
    # sdkconfig.defaults. That is not cosmetic: CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is a
    # BOOTLOADER feature, and enabling it produced an app whose sdkconfig said `=y` while the
    # bootloader on the device was seven hours stale and had never heard of it. OTA rollback
    # therefore silently did nothing -- the image state stayed NEW instead of becoming
    # PENDING_VERIFY, because the transition is the bootloader's job.
    #
    # Caught on a bench. On the robot it would have presented as "rollback does not work", after
    # someone had already relied on it.
    if [ "$HERE/sdkconfig.defaults" -nt "$DEMO/platforms/tab5/build/bootloader/bootloader.bin" ]; then
        echo "config is newer than the bootloader -- full rebuild so the bootloader picks it up"
        # The WHOLE build dir, not just build/bootloader: the parent treats that as a configured
        # ExternalProject and will not re-configure it, so deleting only the bootloader gives
        # "not a CMake build directory (missing CMakeCache.txt)". Config changes are rare; a full
        # rebuild is the cheap, reliable option.
        rm -rf "$DEMO/platforms/tab5/build"
    fi
    rm -f "$DEMO/platforms/tab5/sdkconfig"
    SDKCFG="$HERE/sdkconfig.vendor;$HERE/sdkconfig.defaults"

    # ALWAYS reconfigure. Both build systems glob app/ at CONFIGURE time, so adding or removing a
    # source file silently builds the old file list -- which has produced a confusing link error
    # three separate times (a deleted app_launcher.cpp, and twice a newly added file).
    ( cd "$DEMO/platforms/tab5" && . "$IDF/export.sh" >/dev/null 2>&1 \
      && idf.py -DSDKCONFIG_DEFAULTS="$SDKCFG" reconfigure >/dev/null \
      && idf.py -DSDKCONFIG_DEFAULTS="$SDKCFG" build \
      && idf.py -p "${PORT:-$(ls -1 /dev/cu.usbmodem* 2>/dev/null | head -1)}" flash )
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
