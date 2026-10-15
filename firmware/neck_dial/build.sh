#!/usr/bin/env bash
# Build the neck. The device image needs PlatformIO; the control law does not.
#
#   ./build.sh pantilt     compile and RUN the control law's host test -- no board, no toolchain
#   ./build.sh image       build the Dial's firmware (PlatformIO)
#
# The split exists for the reason the Tab5's does: the control law is pure compute, so the identical
# translation unit that runs on the Dial runs on a laptop, and its arithmetic can be checked with
# the board unplugged. Flashing is CLAUDE.md's rote M5Stack procedure and is NOT done here.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

case "${1:-pantilt}" in
  pantilt)
    out="$(mktemp -d)/pantilt_host_test"
    c++ -std=c++17 -O1 -Wall -Wextra -Werror \
        "$HERE/tools/pantilt_host_test.cpp" "$HERE/neck/pantilt_core.cpp" "$HERE/neck/rc_frame.cpp" \
        "$HERE/neck/tilt_confirm.cpp" -o "$out"
    exec "$out"
    ;;
  image)
    cd "$HERE"
    exec uv run --python 3.12 --with platformio --with intelhex pio run -e neck_dial
    ;;
  *) echo "usage: $0 [pantilt|image]" >&2; exit 2 ;;
esac
