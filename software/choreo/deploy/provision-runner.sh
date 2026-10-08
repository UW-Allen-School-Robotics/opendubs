#!/usr/bin/env bash
# The show runner's service — idempotent, run ON the Thor as root. Called by the dashboard's
# provision.sh (software/kiosk-dashboard/deploy), after deploy.sh has copied software/choreo to
# /opt/opendubs/choreo.
#
# The one robot-specific step: where the arm stack's joint limits live. They are read IN PLACE, never
# copied (a copy goes stale when the arm limits change). The arm container mounts its source checkout
# at /openarm_ws/src; that mount's host side is looked up here, so no path is written into the repo.
# The result is a drop-in that lets the runner's service — and nothing else — see that one directory,
# read-only. Re-running picks up a checkout that has moved.
set -euo pipefail

APP=/opt/opendubs/choreo
UNIT=opendubs-runner.service
DROPIN_DIR=/etc/systemd/system/$UNIT.d
MOUNT_POINT=/etc/opendubs/arm-limits
CONTAINER=openarm

echo "-- installing the show runner ($UNIT)"
# Live arm output (plan 5.6) sends FollowJointTrajectory goals from the host, which needs the action's
# message package; the arm container has it, the host did not.
if [ -d /opt/ros/jazzy ] && ! dpkg -s ros-jazzy-control-msgs >/dev/null 2>&1; then
  apt-get install -y -qq ros-jazzy-control-msgs || echo "   WARNING: control_msgs not installed — live arm output will be unavailable"
fi
install -m 0644 "$APP/deploy/$UNIT" "/etc/systemd/system/$UNIT"
install -d -m 0755 "$MOUNT_POINT"

src=""
if command -v docker >/dev/null 2>&1; then
  src=$(docker inspect "$CONTAINER" \
          --format '{{range .Mounts}}{{if eq .Destination "/openarm_ws/src"}}{{.Source}}{{end}}{{end}}' \
          2>/dev/null || true)
fi
limits_dir="${src:+$src/openarm/hw_validation}"
if [ -n "$limits_dir" ] && [ -f "$limits_dir/joint_limits.yaml" ]; then
  install -d -m 0755 "$DROPIN_DIR"
  # "-": if the checkout later disappears, the service still starts — and refuses every routine,
  # saying the limits cannot be read — rather than failing to start with no explanation.
  printf '[Service]\nBindReadOnlyPaths=-%s:%s\n' "$limits_dir" "$MOUNT_POINT" > "$DROPIN_DIR/arm-limits.conf"
  echo "   joint limits: read in place from the arm stack's checkout (robot-local drop-in)"
else
  echo "   WARNING: could not find the arm stack's joint limits (container '$CONTAINER', mount"
  echo "   /openarm_ws/src). The runner will list every routine as refused until this is re-run."
  echo "   An existing drop-in, if any, is left as it was."
fi

systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null
systemctl restart "$UNIT"
echo "   runner: $(systemctl is-active "$UNIT") on 127.0.0.1:8092"
