#!/usr/bin/env bash
# OpenDubs tablet camera-demand wrapper (ROS 2 Jazzy). Counts subscribers on the head tablet's camera
# topics and tells the dashboard, which streams a camera only while someone wants it.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
exec python3 /opt/opendubs/ros/tablet_camera_demand.py
