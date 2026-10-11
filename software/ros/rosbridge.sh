#!/usr/bin/env bash
# OpenDubs rosbridge launch wrapper (ROS 2 Jazzy).
#
# Runs rosbridge_server's websocket on 127.0.0.1:9090 — THIS HOST ONLY. Nothing on the LAN talks to
# it directly: clients use the dashboard's /rosbridge (ws://<robot>:8080/rosbridge, or
# wss://<robot>:8443/rosbridge from an HTTPS page), which relays to this port behind the
# dashboard's Origin check. rosbridge itself has no authentication, so the raw port is not offered.
#
# OPENDUBS_ROSBRIDGE_ADDRESS overrides the bind address — only on an isolated bench network, as a
# per-robot systemd drop-in, never committed.
#
# NOTE: do NOT `set -u` — ROS's setup.bash references unbound vars and aborts.
source /opt/ros/jazzy/setup.bash
exec ros2 launch rosbridge_server rosbridge_websocket_launch.xml \
  address:="${OPENDUBS_ROSBRIDGE_ADDRESS:-127.0.0.1}"
