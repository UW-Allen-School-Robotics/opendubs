#!/bin/sh
# The face twin: the head's own face code, built for the desktop, following a playhead through
# face_bridge.py — the composer's by default, or the show runner's.
#
#   ./face.sh                                     # follow the composer (:8091)
#   ./face.sh --composer http://127.0.0.1:8092    # follow the show runner instead
#
# The desktop face is built from the Tab5 tree (firmware/tab5_idf/README.md, `./build.sh desktop`)
# into $OPENDUBS_DEMO/build/desktop/app_desktop_build. It shares the head's command parser and
# listens on 127.0.0.1:4242 only. Without that build, face_stub.py stands in: it prints what the
# face would be told and draws nothing. If something already listens on 4242 it is used as it is.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
# The face twin shares its frames here, and the body twin (twin.sh) shows them on the robot's head.
export OPENDUBS_FACE_FRAMES="${OPENDUBS_FACE_FRAMES:-${TMPDIR:-/tmp}/opendubs-face-frames.bin}"
FACE="${OPENDUBS_DEMO:+$OPENDUBS_DEMO/build/desktop/app_desktop_build}"
child=""

if lsof -nP -iUDP@127.0.0.1:4242 >/dev/null 2>&1; then
  echo "face.sh: a face is already listening on 127.0.0.1:4242 — using it"
elif [ -n "$FACE" ] && [ -x "$FACE" ]; then
  echo "face.sh: starting the face twin ($FACE)"
  (cd "$OPENDUBS_DEMO" && exec "$FACE") &
  child=$!
else
  echo "face.sh: no desktop face build (set OPENDUBS_DEMO to the Tab5 demo tree and run"
  echo "         firmware/tab5_idf/build.sh desktop) — the stand-in prints instead of drawing"
  python3 "$HERE/face_stub.py" &
  child=$!
fi
trap '[ -n "$child" ] && kill "$child" 2>/dev/null' EXIT INT TERM
sleep 1
python3 "$HERE/face_bridge.py" "$@"
