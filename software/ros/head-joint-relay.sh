#!/usr/bin/env bash
# OpenDubs head joint relay (ROS 2 Jazzy): the neck's pan and tilt -> robot_state_publisher's input, so
# the head's and the Femto Bolt's frames are in TF (head_joint_relay.py). NOTE: do NOT `set -u` — ROS's
# setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
exec python3 /opt/opendubs/ros/head_joint_relay.py
