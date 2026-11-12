#!/usr/bin/env bash
# Map one floor, attended: `floor-mapping.sh check`, then `floor-mapping.sh floor <frame>` per floor.
# See floor_mapping.py. Sources ROS and the robot's DDS profile, so it sees what the robot's units see.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
if [ -r /etc/opendubs/ros-dds.env ]; then set -a; . /etc/opendubs/ros-dds.env; set +a; fi
exec python3 "$(dirname "$(readlink -f "$0")")/floor_mapping.py" "$@"
