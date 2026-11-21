#!/usr/bin/env bash
# Host test of the StamPLC's SBUS framer (src/sbus_framer.h), on a laptop: no board, no radio.
# Built as C++11 with warnings as errors, because the StamPLC's Arduino core compiles as gnu++11.
#
#   firmware/rc_serial_probe/lib/sbus_framer/tests/test_sbus_framer.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
W="$(mktemp -d)"; trap 'rm -rf "$W"' EXIT
"${CXX:-c++}" -std=c++11 -O2 -Wall -Wextra -Werror -I"$HERE/../src" "$HERE/test_sbus_framer.cpp" -o "$W/test"
"$W/test"
