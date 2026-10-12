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
  --exclude 'frontend/assets/arm' --exclude 'docs-site' \
  "$SRC" "$HOST:$DEST/"

# The docs, served by the dashboard at /docs/ so the "?" beside a refusal opens its troubleshooting
# entry with no internet, at the version being deployed. Built here so the robot never needs MkDocs.
# A failed build warns and the deploy carries on: the docs must never hold back a dashboard fix.
REPO="$SRC../.."
if command -v mkdocs >/dev/null 2>&1; then
  DOCS_TMP="$(mktemp -d)"
  if (cd "$REPO" && mkdocs build -q -d "$DOCS_TMP/site"); then
    rsync -az --delete "$DOCS_TMP/site/" "$HOST:$DEST/docs-site/"
  else
    echo "!! docs build failed — deploying without updating the robot's /docs/" >&2
  fi
  rm -rf "$DOCS_TMP"
else
  echo "!! mkdocs not installed (pip install mkdocs-material) — deploying without updating the robot's /docs/" >&2
fi

# The arm view's model: chain.json plus the collision meshes it references, from the single robot
# description (description/openarm). Visual meshes and the MJCF are not needed on the robot.
ARM="$SRC../../description/openarm"
ssh "$HOST" "mkdir -p $DEST/frontend/assets/arm/meshes"
rsync -az "$ARM/chain.json" "$HOST:$DEST/frontend/assets/arm/"
rsync -az --delete "$ARM/meshes/collision" "$HOST:$DEST/frontend/assets/arm/meshes/"

# The show runner behind the Shows page (software/choreo, plan 4.4c): its own service, provisioned
# below by provision.sh. Laptop-only leftovers are not copied. Show music is never committed
# (shows/audio/.gitignore), so a song copied to the robot exists only there: protected from --delete,
# while one in this checkout is still copied over.
CHOREO="$SRC../choreo/"
ssh "$HOST" "mkdir -p $DEST_ROOT/choreo"
rsync -az --delete --filter 'P shows/audio/*' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude 'MUJOCO_LOG.TXT' \
  "$CHOREO" "$HOST:$DEST_ROOT/choreo/"

# deploy-all.sh calls this script and provisions everything itself afterwards.
if [ -n "${OPENDUBS_SKIP_PROVISION:-}" ]; then
  echo "== dashboard copied; provisioning left to the caller =="
  exit 0
fi
echo "== running provisioning on $HOST =="
ssh "$HOST" "sudo bash $DEST/deploy/provision.sh"

echo "== deployed. Reload the dashboard on the tablet to pick up frontend changes. =="
