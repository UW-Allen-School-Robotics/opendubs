#!/usr/bin/env bash
# The HallPass mission runner (hallpass_runner.py), run by opendubs-hallpass-runner.service.
# Needs opendubs-nav2 (navigate_to_pose), the dashboard's look-at and HallPass's stop check (OPENDUBS_SITEKIT).
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
if [ -r /etc/opendubs/ros-dds.env ]; then set -a; . /etc/opendubs/ros-dds.env; set +a; fi
exec python3 "$(dirname "$(readlink -f "$0")")/hallpass_runner.py"
