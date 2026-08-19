#!/usr/bin/env bash
# OpenDubs StampLC → ROS bridge wrapper (ROS 2 Jazzy). Polls the kiosk dashboard's
# /api/stamplc endpoint and republishes StampLC IO/safety/encoder/PDH as ROS topics +
# diagnostics, and advertises std_srvs services that POST back to the dashboard.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
exec python3 /opt/opendubs/ros/stamplc_bridge.py
