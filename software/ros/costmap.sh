#!/usr/bin/env bash
# Nav2 costmap of the body-filtered lidars and the depth camera, plus the zero neck and lift joints.
# Not part of opendubs-slam: run this to publish /costmap, Ctrl-C to stop. Needs the map frame,
# which opendubs-slam provides.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
HERE=$(cd "$(dirname "$0")" && pwd)
if [ ! -x /opt/ros/jazzy/lib/nav2_costmap_2d/nav2_costmap_2d ] \
   || [ ! -f /opt/ros/jazzy/lib/libdepth_image_proc.so ] \
   || [ ! -f /opt/ros/jazzy/lib/libpointcloud_to_laserscan.so ]; then
  echo "need ros-jazzy-nav2-costmap-2d, ros-jazzy-depth-image-proc and ros-jazzy-pointcloud-to-laserscan" >&2
  exit 1
fi
ros2 launch "$HERE/costmap.launch.py" &
LAUNCH=$!
trap 'kill -INT "$LAUNCH" 2>/dev/null; wait "$LAUNCH"' TERM INT
# The executable starts unconfigured and stays there until told.
(
  for _ in $(seq 1 60); do
    sleep 5
    case "$(ros2 lifecycle get /costmap 2>/dev/null)" in
      active*) exit 0 ;;
      unconfigured*) echo "costmap is unconfigured: configuring and activating it"
                     ros2 lifecycle set /costmap configure && ros2 lifecycle set /costmap activate ;;
      inactive*) echo "costmap is configured but inactive: activating it"
                 ros2 lifecycle set /costmap activate ;;
    esac
  done
  echo "costmap did not become active within 5 min"
) &
wait "$LAUNCH"
