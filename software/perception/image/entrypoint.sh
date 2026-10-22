#!/bin/bash
# Source ROS 2 and the perception interfaces, then run the command.
source /opt/ros/jazzy/setup.bash
source /opt/perception_ws/install/setup.bash
exec "$@"
