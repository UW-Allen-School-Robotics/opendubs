#!/usr/bin/env bash
# OpenDubs base selector: in AUTO, one software owner of the base at a time. See base_selector.py.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
exec python3 /opt/opendubs/ros/base_selector.py
