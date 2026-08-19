#!/usr/bin/env bash
# OpenDubs ROS 2 provisioning — run ON the Thor (needs sudo). Idempotent.
# Installs ROS 2 Jazzy base + the LIDAR / rosbridge / foxglove stack and
# installs+enables the opendubs ROS services. See ros/README.md.
#
# Usage:  sudo /opt/opendubs/ros/provision-ros.sh
set -euo pipefail

ROS_DISTRO=jazzy
ROSDIR=/opt/opendubs/ros
SERVICES="opendubs-rplidar opendubs-rosbridge opendubs-foxglove opendubs-scan-watchdog opendubs-stamplc-bridge opendubs-camera"

echo "== OpenDubs ROS provisioning =="

# --- ROS 2 apt repository ---------------------------------------------------
if [ ! -f /etc/apt/sources.list.d/ros2.list ]; then
  echo "-- adding ROS 2 apt repo + key"
  apt-get install -y -qq curl gnupg ca-certificates software-properties-common >/dev/null
  add-apt-repository -y universe >/dev/null 2>&1 || true
  curl -fsSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    -o /usr/share/keyrings/ros-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release; echo "$VERSION_CODENAME") main" \
    > /etc/apt/sources.list.d/ros2.list
  apt-get update -qq
fi

# --- packages (idempotent — apt no-ops if already current) ------------------
echo "-- installing ROS packages"
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  "ros-${ROS_DISTRO}-ros-base" \
  "ros-${ROS_DISTRO}-rplidar-ros" \
  "ros-${ROS_DISTRO}-rosbridge-suite" \
  "ros-${ROS_DISTRO}-foxglove-bridge" \
  "ros-${ROS_DISTRO}-diagnostic-msgs"

# --- sudoers (scan watchdog → restart rplidar) ------------------------------
install -m0440 "$ROSDIR/opendubs-ros-sudoers" /etc/sudoers.d/opendubs-ros

# --- services ---------------------------------------------------------------
echo "-- installing + enabling ROS services"
chmod +x "$ROSDIR"/*.sh
for svc in $SERVICES; do
  install -m0644 "$ROSDIR/$svc.service" "/etc/systemd/system/$svc.service"
done
systemctl daemon-reload
for svc in $SERVICES; do
  systemctl enable "$svc" >/dev/null
  systemctl restart "$svc"
done

echo "== ROS provisioning done =="
for svc in $SERVICES; do echo "  $svc: $(systemctl is-active "$svc")"; done
