#!/usr/bin/env bash
# OpenDubs robot_state node: owns StamPLC link, publishes /robot/state, arbitrates /zaxis/*.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
export PYTHONPATH="/opt/opendubs/kiosk-dashboard/backend:/opt/opendubs/ros:${PYTHONPATH:-}"
exec python3 /opt/opendubs/ros/robot_state.py
