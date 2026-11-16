#!/usr/bin/env bash
# Nav2 behind the base lease (nav2.launch.py), run by opendubs-nav2.service. Needs opendubs-slam.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
if [ -r /etc/opendubs/ros-dds.env ]; then set -a; . /etc/opendubs/ros-dds.env; set +a; fi
for p in nav2_bt_navigator nav2_controller nav2_planner nav2_behaviors nav2_velocity_smoother \
         nav2_lifecycle_manager nav2_regulated_pure_pursuit_controller nav2_navfn_planner \
         depth_image_proc pointcloud_to_laserscan; do
  [ -d "/opt/ros/jazzy/share/$p" ] || { echo "nav2: package $p is not installed (provision-ros.sh)" >&2; exit 1; }
done
exec ros2 launch "$(dirname "$(readlink -f "$0")")/nav2.launch.py"
