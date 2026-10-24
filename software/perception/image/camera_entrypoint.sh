#!/bin/bash
# Source ROS 2 and the Orbbec driver, then run the command.
source /opt/ros/jazzy/setup.bash
source /opt/orbbec_ws/install/setup.bash
exec "$@"
