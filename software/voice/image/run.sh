#!/bin/bash
# run.sh — create the opendubs-voice container from a built image. Plan: notes/workplan-voice.md 1.6.
#
#   run.sh IMAGE [--replace]
#
# Host networking, because everything it talks to is on this Thor's loopback: the dashboard's
# /ws/voice relay connects to 127.0.0.1:8093, the face door is UDP 127.0.0.1:4242, the dashboard is
# :8080 and the local model :8000. The service binds 127.0.0.1 only.
#
# Per-robot configuration comes from /etc/opendubs/voice.env, which holds the API key when the cloud
# brain is enabled. That file must be owned by root and mode 0600, or this refuses: a key readable
# by other accounts on the robot has already leaked. Without the file the service starts with the
# defaults (local brain, voice on).
#
# Hardened: read-only root filesystem, no capabilities, no new privileges, a non-root user.
set -euo pipefail
die() { echo "run.sh: $*" >&2; exit 1; }
NAME=opendubs-voice
ENVFILE=${OPENDUBS_VOICE_ENV:-/etc/opendubs/voice.env}

IMAGE=${1:?usage: run.sh IMAGE [--replace]}; shift
REPLACE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --replace) REPLACE=1 ;;
    *)         die "unknown option $1" ;;
  esac
  shift
done

docker image inspect "$IMAGE" >/dev/null 2>&1 || die "no image $IMAGE (build it with build.sh)"

ENVARGS=()
if [ -e "$ENVFILE" ]; then
  owner=$(stat -c %U "$ENVFILE"); mode=$(stat -c %a "$ENVFILE")
  [ "$owner" = root ] && [ "$mode" = 600 ] \
    || die "$ENVFILE is $owner:$mode; it must be root:600 (it may hold the API key). Fix it with: sudo chown root:root $ENVFILE && sudo chmod 600 $ENVFILE"
  ENVARGS=(--env-file "$ENVFILE")
fi

if docker inspect "$NAME" >/dev/null 2>&1; then
  [ "$REPLACE" = 1 ] || die "a '$NAME' container already exists; pass --replace to remove it"
  docker rm -f "$NAME" >/dev/null
fi

docker run -d --name "$NAME" \
  --network host \
  --restart unless-stopped \
  --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  --log-opt max-size=10m --log-opt max-file=3 \
  "${ENVARGS[@]}" \
  "$IMAGE"
echo "run.sh: $NAME started from $IMAGE"
