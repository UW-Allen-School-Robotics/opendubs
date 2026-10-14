#!/bin/bash
# The choreography studio on this machine: the composer, the face twin and the MuJoCo body twin,
# started together. ./studio-down.sh stops them (only what this script started).
#
#   ./studio-up.sh                     # composer on :8091, the tablet face, the MuJoCo window
#
# Shows and library: ~/opendubs-shows and ~/opendubs-library when they exist (they live outside the
# repo and are never committed), else the repo's own shows/ and the default library. Override with
# OPENDUBS_STUDIO_SHOWS / OPENDUBS_LIBRARY. Other settings, all optional:
#   OPENDUBS_STUDIO_PORT=8091          the composer's port
#   OPENDUBS_STUDIO_HEAD=tablet        the face twin's head (face.sh --head: tablet | tab5 | auto)
#   OPENDUBS_STUDIO_BODY_HEAD=galaxy_tablet   the MuJoCo head (twin_view.py --head)
#   OPENDUBS_FACE_WEB_PORTS=8765,8766,4244,4245   the face door's ports (face.sh)
#
# Everything binds 127.0.0.1 and nothing here can move the robot. Logs and process ids are kept in
# $OPENDUBS_STUDIO_DIR (default ~/.cache/opendubs-studio). A port already in use is never taken over:
# the script stops and names what holds it.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="${OPENDUBS_STUDIO_DIR:-$HOME/.cache/opendubs-studio}"
PORT="${OPENDUBS_STUDIO_PORT:-8091}"
HEAD="${OPENDUBS_STUDIO_HEAD:-tablet}"
BODY_HEAD="${OPENDUBS_STUDIO_BODY_HEAD:-galaxy_tablet}"
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
mkdir -p "$DIR"

SHOWS="${OPENDUBS_STUDIO_SHOWS:-}"
[ -z "$SHOWS" ] && [ -d "$HOME/opendubs-shows" ] && SHOWS="$HOME/opendubs-shows"
[ -z "$SHOWS" ] && SHOWS="$HERE/shows"
if [ -z "${OPENDUBS_LIBRARY:-}" ] && [ -d "$HOME/opendubs-library" ]; then
    export OPENDUBS_LIBRARY="$HOME/opendubs-library"
fi

running() { [ -f "$DIR/$1.pid" ] && kill -0 "$(cat "$DIR/$1.pid")" 2>/dev/null; }
holder() { lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1" (pid "$2")"}'; }

for p in composer face body; do
    if running "$p"; then
        echo "studio: already running ($p, pid $(cat "$DIR/$p.pid")) — ./studio-down.sh first" >&2
        exit 1
    fi
done
if [ -n "$(holder "$PORT")" ]; then
    echo "studio: port $PORT is in use by $(holder "$PORT") — not ours; set OPENDUBS_STUDIO_PORT" >&2
    exit 1
fi

# Each part in its own process group, so studio-down.sh stops it with everything it started.
set -m
start() {   # name, command...
    local name="$1"; shift
    ( cd "$HERE" && exec "$@" ) > "$DIR/$name.log" 2>&1 &
    echo $! > "$DIR/$name.pid"
    echo "studio: $name started (pid $!, log $DIR/$name.log)"
}

start composer "$UV" run -q --python 3.12 --with pyyaml --with numpy --with mujoco \
    python composer/server.py --port "$PORT" --shows "$SHOWS"
URL="http://127.0.0.1:$PORT"
for _ in $(seq 1 60); do
    curl -s -o /dev/null -m 1 "$URL/" && break
    if ! running composer; then
        echo "studio: the composer did not start — see $DIR/composer.log:" >&2
        tail -5 "$DIR/composer.log" >&2
        rm -f "$DIR/composer.pid"
        exit 1
    fi
    sleep 0.5
done
echo "studio: composer at $URL (shows: $SHOWS; library: ${OPENDUBS_LIBRARY:-default})"

start face ./face.sh --head "$HEAD" --composer "$URL"
start body ./twin.sh --composer "$URL" --head "$BODY_HEAD"
sleep 3
for p in face body; do
    running "$p" || { echo "studio: $p stopped at once — see $DIR/$p.log:" >&2; tail -5 "$DIR/$p.log" >&2; }
done

command -v open >/dev/null && [ -z "${OPENDUBS_NO_BROWSER:-}" ] && open "$URL/"
echo "studio: up. Open a show in the composer; the face and the MuJoCo window follow it. Stop: ./studio-down.sh"
