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
# Projector video by casting (video_out.py, notes/design/choreo-video.md): the Cast protocol client,
# for the system Python the runner runs under. Without it the runner still runs, and the projector
# card says this robot can't cast yet.
if ! python3 -c 'import pychromecast' >/dev/null 2>&1; then
  apt-get install -y -qq --no-upgrade python3-pychromecast || echo "   WARNING: python3-pychromecast not installed — no casting to the projector"
fi
install -m 0644 "$APP/deploy/$UNIT" "/etc/systemd/system/$UNIT"
# The runner talks to the arm container over ROS 2 from the HOST, as the kiosk user. FastDDS's default
# transports include shared memory, which does not cross users: the runner's node sees NO topics. The
# UDP-only profile the unit names fixes that (measured on the robot, 2026-10-12), but it was only ever
# baked INTO the container. Install it on the host from the arm stack's own tracked file (Arms lane:
# software/openarm/image/fastdds-udp-only.xml, deployed to /opt/opendubs/openarm) — copied, never
# edited here, and refreshed when it changes. The teleop unit names the same file.
DDS_SRC=/opt/opendubs/openarm/image/fastdds-udp-only.xml
DDS_DST=/etc/opendubs-fastdds-udp-only.xml
if [ -f "$DDS_SRC" ]; then
  if ! cmp -s "$DDS_SRC" "$DDS_DST"; then
    install -m 0644 "$DDS_SRC" "$DDS_DST" && echo "   DDS: the UDP-only FastDDS profile installed at $DDS_DST"
  fi
else
  echo "   WARNING: $DDS_SRC is missing (deploy software/openarm first) — the runner's ROS node may see"
  echo "   no arm topics, so live arm output would not reach the arms."
fi
# The demo shows' music is generated, never committed (shows/audio/.gitignore), so a deploy may not
# carry it: make it here. Deterministic, standard library only, a few seconds.
python3 "$APP/shows/audio/make_test_audio.py" >/dev/null && echo "   demo show music: generated"
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
