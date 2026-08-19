#!/usr/bin/env bash
# OpenDubs kiosk — deploy from the dev machine to the Thor over SSH.
# Syncs the repo's kiosk-dashboard/ to /opt/opendubs/kiosk-dashboard on the Thor,
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

rsync -az --delete \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.venv' \
  "$SRC" "$HOST:$DEST/"

echo "== running provisioning on $HOST =="
ssh "$HOST" "sudo bash $DEST/deploy/provision.sh"

echo "== deployed. Restart the kiosk with:  ssh $HOST sudo systemctl restart gdm =="
