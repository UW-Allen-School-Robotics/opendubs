#!/usr/bin/env bash
# C1: a read-only look at Spot's Orin before its first capture.
# It prints what is installed, running and free; it starts, stops and changes nothing, and asks for
# no password. Run it on the Orin; send the whole output to the Spot lane.
#
#   bash c1-check.sh [ROS_SETUP]     # ROS_SETUP: a setup.bash to source if ROS isn't already sourced
#
# Run it while spot_ros2 and the Ouster driver are up, if they are started that day, so the topic and
# frame sections show real names. It works with them down too, and reports what it can.
set -u

section() { printf '\n== %s ==\n' "$1"; }

section "Machine"
uname -a
cat /etc/nv_tegra_release 2>/dev/null
date -u +%FT%T.%NZ
section "Disk (a 30-minute walk at full camera rate needs about 50 GB)"
df -h 2>/dev/null | grep -vE '^(tmpfs|devtmpfs|overlay|udev)'

section "ROS 2"
ls /opt/ros 2>/dev/null
if [ -n "${1:-}" ]; then
  # shellcheck disable=SC1090
  source "$1"
elif [ -z "${ROS_DISTRO:-}" ] && [ "$(ls /opt/ros 2>/dev/null | wc -l)" = 1 ]; then
  # shellcheck disable=SC1090
  source /opt/ros/"$(ls /opt/ros)"/setup.bash
fi
echo "ROS_DISTRO=${ROS_DISTRO:-unset}  ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-unset}  RMW=${RMW_IMPLEMENTATION:-default}"

section "Spot and Ouster code on disk (earlier recording setups included)"
find "$HOME" /opt /ws /workspace -maxdepth 5 -type d \
  \( -name spot_ros2 -o -name spot_driver -o -name ouster-ros -o -name ouster_ros -o -name ouster_sensor_msgs \) \
  2>/dev/null | head -40
find "$HOME" -maxdepth 6 \( -name '*.launch.py' -o -name '*.launch.xml' -o -name '*.yaml' -o -name '*.sh' \) 2>/dev/null \
  | grep -iE 'spot|ouster|record|bag' | grep -v '/\.' | head -60
section "Containers"
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || echo "no docker access"

command -v ros2 >/dev/null || { section "ros2 not on PATH: stopping here"; exit 0; }
section "rosbag2 storage plugins (mcap wanted)"
ros2 pkg list 2>/dev/null | grep -E '^rosbag2_storage|^ouster|^spot'

section "Running nodes"
ros2 node list 2>/dev/null
section "Topics with types"
ros2 topic list -t 2>/dev/null

section "Ouster driver parameters"
for n in $(ros2 node list 2>/dev/null | grep -iE 'ouster|os_'); do
  for p in sensor_hostname lidar_mode udp_profile_lidar timestamp_mode ptp_utc_tai_offset; do
    printf '%s %s: ' "$n" "$p"; ros2 param get "$n" "$p" 2>/dev/null | tr -d '\n'; echo
  done
done
section "Ouster metadata (channels = pixels_per_column)"
timeout 5 ros2 topic echo --once --field data /ouster/metadata 2>/dev/null \
  | grep -oE '"(lidar_mode|udp_profile_lidar|pixels_per_column|prod_line|columns_per_frame)": *"?[^,"]*' | sort -u

section "Frames: body -> flat_body (gravity-aligned) and body -> os_sensor"
timeout 6 ros2 run tf2_ros tf2_echo body flat_body 2>&1 | grep -m2 -E 'Translation|Rotation|exist|Invalid'
timeout 6 ros2 run tf2_ros tf2_echo body os_sensor 2>&1 | grep -m2 -E 'Translation|Rotation|exist|Invalid'

section "Topic rates (5 s each)"
for t in /odometry /ouster/lidar_packets /camera/frontleft/image /depth/frontleft/image; do
  printf '%s: ' "$t"; timeout 5 ros2 topic hz "$t" 2>/dev/null | grep -m1 'average rate' || echo "no messages"
done

section "PTP"
for i in $(ls /sys/class/net); do
  ethtool -T "$i" 2>/dev/null | grep -qE 'PTP Hardware Clock: [0-9]' && echo "$i: has a PTP hardware clock"
done
systemctl is-active ptp4l phc2sys 2>/dev/null | paste -sd' ' | sed 's/^/ptp4l phc2sys: /'
echo; echo "C1 done. Nothing was changed."
