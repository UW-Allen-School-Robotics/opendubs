#!/usr/bin/env bash
# openarm-bringup.sh — one-shot initial bring-up for the OpenArm payload on this robot.
#
# Run this after deploy-all.sh has synced /opt/opendubs (so openarm_robot-code, the
# pinned submodule, is already on disk at $WS below) and the Thor itself is set up
# per docs/thor-setup.md.
#
# This is SETUP, not an every-session command: it installs drivers, clones ssik, and
# builds/commits the workspace image — none of that should re-run just to start working.
# Once it's been run, day-to-day operation is openarm-up / openarm-up activate / openarm-up
# down, same as before. Re-running this script is only for a fresh robot or after wiping
# state; each step below skips itself if already done, so a partial failure can resume.
#
# What it does, in order:
#   1. provision-openarm.sh        — CAN driver, channel names, link service,
#                                     installs openarm-up / openarm-motors
#   2. openarm_robot-code/scripts/bake-image.sh — build + bake the workspace image
#   3. create the long-running `openarm` container that openarm-up expects (stopped;
#      openarm-up starts it)
#
# openarm_robot-code and ssik are both pinned submodules, synced here by deploy-all.sh —
# this script does not clone either of them.
#
# Usage:  sudo /opt/opendubs/openarm/openarm-bringup.sh
set -euo pipefail

DIR=/opt/opendubs/openarm
WS="$DIR/openarm_robot-code"
SSIK="$DIR/ssik"
STATE="$DIR/ws_state"
IMAGE="openarm-ros2:$(hostname)"
CONTAINER=openarm

die() { echo "openarm-bringup: $*" >&2; exit 1; }

[ -d "$WS" ] || die "$WS not found — run deploy-all.sh first so the openarm_robot-code submodule is synced here"
[ -x "$WS/scripts/bake-image.sh" ] || die "$WS/scripts/bake-image.sh missing — is openarm_robot-code up to date?"
[ -d "$SSIK" ] || die "$SSIK not found — run deploy-all.sh first so the ssik submodule is synced here"

echo "== 1. Host provisioning =="
"$DIR/provision-openarm.sh"

echo "== 2. Bake the workspace image =="
if sudo docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "-- $IMAGE already exists — skipping bake (sudo docker rmi $IMAGE to force a rebuild)"
else
  "$WS/scripts/bake-image.sh" "$IMAGE" "$STATE" "$SSIK"
fi

echo "== 3. Long-running container =="
if sudo docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "-- '$CONTAINER' container already exists — skipping (sudo docker rm -f $CONTAINER to recreate)"
else
  sudo docker run -d --name "$CONTAINER" --restart unless-stopped --privileged --network=host \
    -e DISPLAY -e RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    -v "$WS:/openarm_ws/src" \
    -v "$SSIK:/openarm_ws/ssik" \
    -v "$STATE/build:/openarm_ws/build" \
    -v "$STATE/install:/openarm_ws/install" \
    -v "$STATE/log:/openarm_ws/log" \
    -w /openarm_ws/src "$IMAGE" sleep infinity >/dev/null
  echo "-- '$CONTAINER' container created"
fi

echo "== Setup complete. Day-to-day operation is now: openarm-up =="