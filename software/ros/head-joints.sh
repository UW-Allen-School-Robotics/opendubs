#!/usr/bin/env bash
# OpenDubs head joints: /head/joint_states -> /opendubs/joint_states for robot_state_publisher (head_joints.py).
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
exec python3 /opt/opendubs/ros/head_joints.py
