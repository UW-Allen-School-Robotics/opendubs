#!/usr/bin/env bash
# OpenDubs /scan watchdog wrapper (ROS 2 Jazzy). Restarts opendubs-rplidar when
# /scan goes stale. NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
exec python3 /opt/opendubs/ros/scan_watchdog.py
