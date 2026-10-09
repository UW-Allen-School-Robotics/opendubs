#!/bin/bash
# run.sh — create the openarm container from a built image. Design: notes/design/arm-image.md.
#
#   run.sh IMAGE [--replace] [--unprivileged] [--dev NAME=PATH ...]
#
#   --replace        remove an existing 'openarm' container first. Refused unless every arm motor
#                    reads disabled: removing the container under an energized arm leaves it frozen
#                    at its last command with nothing to stop it.
#   --unprivileged   host networking WITHOUT --privileged. The target, once a vcan test on the
#                    non-robot Thor shows SocketCAN needs nothing more (Thor stack plan, T-3 / 4).
#                    Until then the default stays --privileged, as the hand-built container is.
#   --dev NAME=PATH  bind-mount a checkout of repository NAME (from repos.lock) over its place in the
#                    image, e.g. --dev openarm=~/src/openarm for the teleop bridge. One repository at
#                    a time: the Python environment lives inside /openarm_ws/src.
#
# Per-machine state is mounted from the host, never baked in: the teleop certificate
# (/etc/opendubs/openarm-certs) and openarm-up's logs (/var/log/opendubs-openarm).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
die() { echo "run.sh: $*" >&2; exit 1; }
NAME=openarm
CERTS=/etc/opendubs/openarm-certs
LOGS=/var/log/opendubs-openarm

IMAGE=${1:?usage: run.sh IMAGE [--replace] [--unprivileged] [--dev NAME=PATH ...]}; shift
REPLACE=0; PRIV=(--privileged); DEV=()
while [ $# -gt 0 ]; do
  case "$1" in
    --replace)      REPLACE=1 ;;
    --unprivileged) PRIV=() ;;
    --dev)          spec=${2:?--dev needs NAME=PATH}; shift
                    repo=${spec%%=*}; path=${spec#*=}
                    grep -Eq "^$repo +(uv|ros|asset) " "$HERE/repos.lock" || die "--dev: '$repo' is not a sibling in repos.lock"
                    [ -d "$path" ] || die "--dev: $path is not a directory"
                    DEV+=(-v "$(cd "$path" && pwd):/openarm_ws/src/$repo") ;;
    *)              die "unknown option $1" ;;
  esac
  shift
done

docker image inspect "$IMAGE" >/dev/null 2>&1 || die "no image $IMAGE (build it with build.sh)"

if docker inspect "$NAME" >/dev/null 2>&1; then
  [ "$REPLACE" = 1 ] || die "a '$NAME' container already exists; pass --replace to remove it"
  summary=$(/usr/local/lib/openarm/openarm-motors summary 2>/dev/null || true)
  case "$summary" in
    *"enabled=0 "*) ;;
    *) die "refusing to replace '$NAME': motors are not all disabled (${summary:-unreadable}). Park and 'openarm-up down' first." ;;
  esac
  docker rm -f "$NAME" >/dev/null
  echo "== removed the old '$NAME' container"
fi

sudo install -d -m 700 "$CERTS"
sudo install -d -m 755 "$LOGS"

# Dev mounts BEFORE the certificate mount: a checkout mounted over src/openarm must not shadow the
# certificate directory inside it.
docker run -d --name "$NAME" \
  --restart unless-stopped \
  --network=host \
  ${PRIV[@]+"${PRIV[@]}"} \
  ${DEV[@]+"${DEV[@]}"} \
  -v "$CERTS:/openarm_ws/src/openarm/certs" \
  -v "$LOGS:/openarm_ws/log" \
  "$IMAGE" >/dev/null
n_dev=$(( ${#DEV[@]} / 2 ))
echo "== '$NAME' is running $IMAGE ($([ ${#PRIV[@]} -gt 0 ] && echo privileged || echo unprivileged), $n_dev dev mount(s))"
echo "   next: openarm-up   (arms come up inactive)"
