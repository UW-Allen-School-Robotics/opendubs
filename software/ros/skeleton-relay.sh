#!/usr/bin/env bash
# OpenDubs skeleton feed relay (ROS 2 Jazzy): the tracker's topic -> the dashboard -> the stage page.
# Only skeleton points pass (skeleton_relay.py). NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
exec python3 /opt/opendubs/ros/skeleton_relay.py
