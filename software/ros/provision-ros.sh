#!/usr/bin/env bash
# OpenDubs ROS 2 provisioning — run ON the Thor (needs sudo). Idempotent.
# Installs ROS 2 Jazzy base + the LIDAR / rosbridge / foxglove stack and
# installs+enables the opendubs ROS services. See ros/README.md.
#
# Usage:  sudo /opt/opendubs/ros/provision-ros.sh
set -euo pipefail

ROS_DISTRO=jazzy
ROSDIR=/opt/opendubs/ros
SERVICES="opendubs-rplidar opendubs-rplidar-rear opendubs-rosbridge opendubs-foxglove \
opendubs-scan-watchdog opendubs-scan-watchdog-rear opendubs-stamplc-bridge \
opendubs-drive-bridge opendubs-tablet-camera-demand opendubs-skeleton-relay \
opendubs-robot-state-publisher opendubs-slam opendubs-floor-frames opendubs-dance-tracker \
opendubs-base-selector opendubs-head-joint-relay"
# Units this stack no longer runs. Provisioning stops, disables and removes each one a robot still has,
# so an old install cannot start it again at boot. opendubs-camera drove the original Orbbec Astra,
# which has been removed from the robot (notes/history/astra-camera/).
RETIRED="opendubs-camera"

# The two LIDAR drivers cannot be started until each has been PINNED to a physical unit. Both A1
# adapters report CP2102 serial 0001, so /dev/serial/by-id holds one ambiguous entry and the
# rplidar.sh defaults (/dev/ttyUSB0, topic scan, frame laser) resolve to whichever unit enumerated
# first. Starting both unpinned means they fight for one device and publish over one topic.
#
# This is not hypothetical: on 2026-10-02 opendubs2 had no drop-ins at all, the rear units had never
# been installed (they were missing from this list), and the FRONT service was driving the REAR unit
# on /scan in frame laser. The dashboard had been rendering rear data as front.
#
# The pinning is per-robot — it depends on physical hub wiring — so it lives in a drop-in on the
# machine and never in the repo. See ros/README.md for the file and how to identify each unit.
LIDAR_UNITS="opendubs-rplidar opendubs-rplidar-rear"

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

# --- packages (idempotent: installs only what is missing) --------------------
echo "-- installing ROS packages"
# --no-upgrade: install what is missing, never move what is already there. A deploy is not the
# place to upgrade ROS packages by side effect (2026-10-09 it moved foxglove-bridge 3.5 -> 3.6).
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-upgrade \
  "ros-${ROS_DISTRO}-ros-base" \
  "ros-${ROS_DISTRO}-rplidar-ros" \
  "ros-${ROS_DISTRO}-rosbridge-suite" \
  "ros-${ROS_DISTRO}-foxglove-bridge" \
  "ros-${ROS_DISTRO}-diagnostic-msgs"
# The skeleton relay's WebSocket client to the dashboard. Its own line, and allowed to fail: an offline
# deploy must not stop the rest of ROS provisioning over a package only the relay needs.
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-upgrade python3-websockets >/dev/null \
  || echo "  !! python3-websockets not installed (offline?): the skeleton relay cannot start until it is"
# SLAM (opendubs-slam, off unless /etc/opendubs/slam.enabled) and the robot's frames. Their own line and
# allowed to fail, for the same reason.
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-upgrade \
  "ros-${ROS_DISTRO}-robot-state-publisher" "ros-${ROS_DISTRO}-slam-toolbox" >/dev/null \
  || echo "  !! slam_toolbox not installed (offline?): opendubs-slam cannot start until it is"

# --- the service user ---------------------------------------------------------
# The ROS units and the sudoers rule name the account the nodes run as with placeholders
# (@OPENDUBS_USER@, @OPENDUBS_HOME@): it is per robot and never in the repo. It is
# OPENDUBS_SERVICE_USER in /etc/opendubs/robot.env if set, otherwise the owner of /opt/opendubs,
# which is the account deploys run as. A unit installed WITHOUT rendering fails to start (no such
# user) rather than running as root.
SVC_USER=$(sed -n 's/^OPENDUBS_SERVICE_USER=//p' /etc/opendubs/robot.env 2>/dev/null | tail -1 | tr -d "\"'")
[ -n "$SVC_USER" ] || SVC_USER=$(stat -c %U /opt/opendubs)
if [ -z "$SVC_USER" ] || [ "$SVC_USER" = root ]; then
  echo "!! no service user (set OPENDUBS_SERVICE_USER in /etc/opendubs/robot.env); refusing to run ROS as root" >&2
  exit 2
fi
SVC_HOME=$(getent passwd "$SVC_USER" | cut -d: -f6)
[ -n "$SVC_HOME" ] || { echo "!! service user $SVC_USER does not exist" >&2; exit 2; }
render() { sed -e "s|@OPENDUBS_USER@|$SVC_USER|g" -e "s|@OPENDUBS_HOME@|$SVC_HOME|g" "$1"; }
echo "-- ROS services run as $SVC_USER"

# --- sudoers (scan watchdog → restart rplidar) ------------------------------
# Checked before it is installed: a malformed file in sudoers.d breaks sudo for everyone.
SUDOERS_TMP=$(mktemp)
render "$ROSDIR/opendubs-ros-sudoers" > "$SUDOERS_TMP"
visudo -cqf "$SUDOERS_TMP" || { rm -f "$SUDOERS_TMP"; echo "!! rendered sudoers rule failed visudo; not installed" >&2; exit 2; }
install -m0440 -o root -g root "$SUDOERS_TMP" /etc/sudoers.d/opendubs-ros
rm -f "$SUDOERS_TMP"

# --- services ---------------------------------------------------------------
for svc in $RETIRED; do
  if [ -e "/etc/systemd/system/$svc.service" ]; then
    echo "-- retiring $svc"
    systemctl disable --now "$svc" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$svc.service"
    rm -rf "/etc/systemd/system/$svc.service.d"
  fi
done
echo "-- installing + enabling ROS services"
chmod +x "$ROSDIR"/*.sh
# The robot's DDS settings and FastDDS profile, read by every unit below (and by any other lane's unit
# or container that names them). From the repo, refreshed on every provisioning, before any unit starts.
install -d -m 0755 /etc/opendubs
install -m 0644 -o root -g root "$ROSDIR/ros-dds.env" /etc/opendubs/ros-dds.env
install -m 0644 -o root -g root "$ROSDIR/fastdds-robot.xml" /etc/opendubs/fastdds-robot.xml
for svc in $SERVICES; do
  render "$ROSDIR/$svc.service" > "/etc/systemd/system/$svc.service"
  chmod 0644 "/etc/systemd/system/$svc.service"
done
# The UDP-only DDS lines were first placed by hand as a drop-in on opendubs2; the units carry them now.
rm -f /etc/systemd/system/opendubs-scan-watchdog.service.d/udp-only.conf \
      /etc/systemd/system/opendubs-scan-watchdog-rear.service.d/udp-only.conf
systemctl daemon-reload

# Which LIDAR units are pinned? An unpinned one is installed and enabled but NOT started, so a
# reboot picks it up once its drop-in exists, and provisioning stays idempotent either way.
UNPINNED=""
for svc in $LIDAR_UNITS; do
  if ! grep -rqs OPENDUBS_LIDAR_DEV "/etc/systemd/system/$svc.service.d/"; then
    UNPINNED="$UNPINNED $svc"
  fi
done

for svc in $SERVICES; do
  systemctl enable "$svc" >/dev/null
  case " $UNPINNED " in
    *" $svc "*) echo "  !! $svc: NOT started — no OPENDUBS_LIDAR_DEV drop-in (see below)" ;;
    *)          systemctl restart "$svc" ;;
  esac
done

echo "== ROS provisioning done =="
for svc in $SERVICES; do echo "  $svc: $(systemctl is-active "$svc")"; done

if [ -n "$UNPINNED" ]; then
  cat <<'WARN'

!! LIDAR NOT STARTED — each unit must be pinned to a device first.
!!
!! Both A1 adapters report serial 0001, so by-id is ambiguous and the defaults resolve to
!! whichever unit enumerated first. Pin each one by USB PORT PATH:
!!
!!   ls -l /dev/serial/by-path/          # one entry per connected A1
!!
!! Identify which physical unit is which before writing anything — unplug one, or hold a panel
!! in front of one and watch which topic changes. Do not infer it from plug order. Then, per unit:
!!
!!   sudo mkdir -p /etc/systemd/system/opendubs-rplidar.service.d
!!   sudo tee /etc/systemd/system/opendubs-rplidar.service.d/device.conf <<EOF
!!   [Service]
!!   Environment="OPENDUBS_LIDAR_DEV=/dev/serial/by-path/<the front unit's path>"
!!   Environment="OPENDUBS_LIDAR_TOPIC=scan"
!!   Environment="OPENDUBS_LIDAR_FRAME=laser"
!!   EOF
!!
!! The rear unit takes topic scan_rear and frame laser_rear in its own .service.d directory.
!! Then: sudo systemctl daemon-reload && sudo systemctl start <unit>
WARN
fi
