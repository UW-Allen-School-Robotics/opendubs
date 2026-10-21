#!/usr/bin/env bash
# OpenDubs SLAM wrapper: the robot-body scan filter and slam_toolbox (mapping), see slam.launch.py.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
exec ros2 launch /opt/opendubs/ros/slam.launch.py
