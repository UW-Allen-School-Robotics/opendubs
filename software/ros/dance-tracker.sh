#!/usr/bin/env bash
# OpenDubs dance tracker: a show's DANCE targets + /odom -> /cmd_vel, closed loop. See dance_tracker.py.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
exec python3 /opt/opendubs/ros/dance_tracker.py
