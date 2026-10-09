#!/bin/bash
# The face twin: the head's own face code, on the desktop, following a playhead through
# face_bridge.py — the composer's by default, or the show runner's.
#
#   ./face.sh                                     # follow the composer (:8091), the fitted head's face
#   ./face.sh --composer http://127.0.0.1:8092    # follow the show runner instead
#   ./face.sh --head tablet                       # the tablet's web face engine (software/face-web)
#   ./face.sh --head tab5                         # the Tab5's desktop face
#
# --head auto (the default) takes the robot description's fitted head (description/opendubs/
# dimensions.yaml, head_assembly.fitted). Nothing is decided between the heads: both twins work.
#
# THE TAB5: the desktop face is built from the Tab5 tree (firmware/tab5_idf/README.md,
# `./build.sh desktop`) into $OPENDUBS_DEMO/build/desktop/app_desktop_build. It shares the head's
# command parser and listens on 127.0.0.1:4242 only. Without that build, face_stub.py stands in: it
# prints what the face would be told and draws nothing. A face already on 4242 is used as it is.
#
# THE TABLET: the web face engine IS the tablet's face, run in a laptop browser through its own door
# (software/face-web/door_dev.py: the page, its websocket, the face half and a reference neck). The
# bridge sends the face its lines and the HEAD track to the neck, so the page shows the head turning.
# Everything binds 127.0.0.1 only. Ports: OPENDUBS_FACE_WEB_PORTS="http,ws,face,neck" (default
# 8765,8766,4244,4245). Several sessions may each run one, so face.sh NEVER reuses a door it did not
# start: if the face port answers already it stops and says so — set other ports, or pass --reuse
# to drive a door you started yourself.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
export OPENDUBS_FACE_FRAMES="${OPENDUBS_FACE_FRAMES:-${TMPDIR:-/tmp}/opendubs-face-frames.bin}"

HEAD=auto
REUSE=""
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --head) HEAD="$2"; shift 2 ;;
    --head=*) HEAD="${1#--head=}"; shift ;;
    --reuse) REUSE=1; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

kind_on() {   # PING a local face twin; print its kind (tab5 when it gives none), or nothing
  python3 - "$1" <<'PY'
import re, socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(0.3)
try:
    s.sendto(b"PING\n", ("127.0.0.1", int(sys.argv[1])))
    r = s.recv(2048).decode(errors="replace")
    m = re.search(r"\bkind=([\w-]+)", r)
    print("tablet" if m and m.group(1).startswith("tablet") else (m.group(1) if m else "tab5"))
except OSError:
    pass
PY
}

if [ "$HEAD" = auto ]; then
  fitted="$(awk '/^head_assembly:/{a=1} a && /^  fitted:/{print $2; exit}' "$REPO/description/opendubs/dimensions.yaml" 2>/dev/null)"
  case "$fitted" in galaxy_tablet*) HEAD=tablet ;; *) HEAD=tab5 ;; esac
  echo "face.sh: the robot description's fitted head is '${fitted:-unknown}' -> the $HEAD face"
fi

child=""
trap '[ -n "$child" ] && kill $child 2>/dev/null' EXIT INT TERM

if [ "$HEAD" = tablet ]; then
  IFS=, read -r P_HTTP P_WS P_FACE P_NECK <<< "${OPENDUBS_FACE_WEB_PORTS:-8765,8766,4244,4245}"
  PAGE="http://127.0.0.1:$P_HTTP/?door=ws://127.0.0.1:$P_WS&hud=1"
  if [ -n "$(kind_on "$P_FACE")" ]; then
    if [ -z "$REUSE" ]; then
      owner="$(lsof -t -nP -iUDP@127.0.0.1:"$P_FACE" 2>/dev/null | head -1)"
      echo "face.sh: a face already answers on udp $P_FACE${owner:+ (pid $owner, $(lsof -a -p "$owner" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p'))}."
      echo "         It may be another session's twin, so it is left alone. Use other ports, e.g."
      echo "         OPENDUBS_FACE_WEB_PORTS=8775,8776,4254,4255 ./face.sh --head tablet"
      echo "         or pass --reuse to drive a door you started yourself."
      exit 1
    fi
    echo "face.sh: reusing the web face's door on udp $P_FACE (--reuse)"
  else
    echo "face.sh: starting the web face's door (software/face-web/door_dev.py) on $P_HTTP/$P_WS/$P_FACE/$P_NECK"
    (cd "$REPO" && exec ~/.local/bin/uv run -q --with websockets python3 software/face-web/door_dev.py \
        --http "$P_HTTP" --ws "$P_WS" --face "$P_FACE" --neck "$P_NECK") &
    child=$!
    for _ in $(seq 1 50); do [ -n "$(kind_on "$P_FACE")" ] && break; sleep 0.2; done
    if [ -z "$OPENDUBS_NO_BROWSER" ] && command -v open >/dev/null; then open "$PAGE"
    else echo "face.sh: the face page: $PAGE"; fi
  fi
  python3 "$HERE/face_bridge.py" --face "127.0.0.1:$P_FACE" --neck "127.0.0.1:$P_NECK" "${ARGS[@]}"
else
  FACE="${OPENDUBS_DEMO:+$OPENDUBS_DEMO/build/desktop/app_desktop_build}"
  if [ -n "$(kind_on 4242)" ]; then
    echo "face.sh: a face is already listening on 127.0.0.1:4242 — using it"
  elif [ -n "$FACE" ] && [ -x "$FACE" ]; then
    echo "face.sh: starting the Tab5 face twin ($FACE)"
    (cd "$OPENDUBS_DEMO" && exec "$FACE") &
    child=$!
  else
    echo "face.sh: no desktop face build (set OPENDUBS_DEMO to the Tab5 demo tree and run"
    echo "         firmware/tab5_idf/build.sh desktop) — the stand-in prints instead of drawing"
    python3 "$HERE/face_stub.py" &
    child=$!
  fi
  sleep 1
  python3 "$HERE/face_bridge.py" --face 127.0.0.1:4242 "${ARGS[@]}"
fi
