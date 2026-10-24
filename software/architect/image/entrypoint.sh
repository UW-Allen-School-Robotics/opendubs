#!/bin/bash
# Source ROS 2 and the robot API interfaces, then run the command.
source /opt/ros/jazzy/setup.bash
source /opt/architect_ws/install/setup.bash
exec "$@"
