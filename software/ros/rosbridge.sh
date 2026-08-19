#!/usr/bin/env bash
# OpenDubs rosbridge launch wrapper (ROS 2 Jazzy).
#
# Runs rosbridge_server's websocket on 0.0.0.0:9090 so the dashboard frontend
# (browser JS) can subscribe to ROS topics (e.g. /scan) and call services
# (/start_motor, /stop_motor) directly — the "dashboard as thin ROS subscriber"
# path from docs/hardening-roadmap.md track 4.
#
# NOTE: do NOT `set -u` — ROS's setup.bash references unbound vars and aborts.
source /opt/ros/jazzy/setup.bash
exec ros2 launch rosbridge_server rosbridge_websocket_launch.xml
