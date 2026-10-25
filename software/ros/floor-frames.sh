#!/usr/bin/env bash
# OpenDubs floor frames: <floor> -> map static TF and the room labels, from the site file.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
exec python3 /opt/opendubs/ros/floor_frames.py
