#!/usr/bin/env bash
# OpenDubs kiosk — deploy from the dev machine to the Thor over SSH.
# Syncs the repo's kiosk-dashboard/ to /opt/opendubs/kiosk-dashboard on the Thor (and the show
# runner, software/choreo, to /opt/opendubs/choreo),
# then runs provisioning. Re-run any time to push updates.
#
# Usage:  ./deploy.sh [ssh-host]      (default host: thor)
set -euo pipefail

HOST="${1:-thor}"
SRC="$(cd "$(dirname "$0")/.." && pwd)/"      # repo kiosk-dashboard/
DEST_ROOT=/opt/opendubs
DEST="$DEST_ROOT/kiosk-dashboard"

echo "== deploying kiosk-dashboard -> $HOST:$DEST =="

# ensure dest exists and is writable by us
ssh "$HOST" "sudo mkdir -p $DEST && sudo chown -R \$(id -un):\$(id -gn) $DEST_ROOT"

# frontend/assets/arm is a symlink into the repo's description/ in a checkout; it is excluded here
# and the files the arm view needs are copied as real files just below.
rsync -az --delete \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.venv' \
  --exclude 'frontend/assets/arm' \
  "$SRC" "$HOST:$DEST/"

# The arm view's model: chain.json plus the collision meshes it references, from the single robot
# description (description/openarm). Visual meshes and the MJCF are not needed on the robot.
ARM="$SRC../../description/openarm"
ssh "$HOST" "mkdir -p $DEST/frontend/assets/arm/meshes"
rsync -az "$ARM/chain.json" "$HOST:$DEST/frontend/assets/arm/"
rsync -az --delete "$ARM/meshes/collision" "$HOST:$DEST/frontend/assets/arm/meshes/"

# The show runner behind the Shows page (software/choreo, plan 4.4c): its own service, provisioned
# below by provision.sh. Laptop-only leftovers are not copied.
CHOREO="$SRC../choreo/"
ssh "$HOST" "mkdir -p $DEST_ROOT/choreo"
rsync -az --delete \
  --exclude '__pycache__' --exclude '*.pyc' --exclude 'MUJOCO_LOG.TXT' \
  "$CHOREO" "$HOST:$DEST_ROOT/choreo/"

echo "== running provisioning on $HOST =="
ssh "$HOST" "sudo bash $DEST/deploy/provision.sh"

echo "== deployed. Reload the dashboard on the tablet to pick up frontend changes. =="
