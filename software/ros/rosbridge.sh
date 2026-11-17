#!/usr/bin/env bash
# OpenDubs rosbridge launch wrapper (ROS 2 Jazzy).
#
# rosbridge has no authentication and gives full access to the ROS graph, so it never listens on
# every interface. By default it listens on 127.0.0.1:9090, THIS HOST ONLY, and LAN clients use the
# dashboard's /rosbridge (ws://<robot>:8080/rosbridge, or wss://<robot>:8443/rosbridge from an HTTPS
# page), which relays to this port behind the dashboard's Origin check.
#
# A robot whose LAN clients need the raw port (HallPass's live viewer) sets, in
# /etc/opendubs/robot.env, the Thor's own address on the robot's network, and points the dashboard
# at it, since 127.0.0.1 then no longer answers:
#   OPENDUBS_ROSBRIDGE_ADDRESS=<the Thor's robot-network address>
#   OPENDUBS_ROSBRIDGE_URL=ws://<that address>:9090
# deploy-all.sh checks the listener against exactly this after every deploy.
#
# NOTE: do NOT `set -u` — ROS's setup.bash references unbound vars and aborts.
addr="${OPENDUBS_ROSBRIDGE_ADDRESS:-127.0.0.1}"
case "$addr" in
  0.0.0.0|::|'[::]'|'*'|'')
    echo "rosbridge: refusing OPENDUBS_ROSBRIDGE_ADDRESS='$addr', which listens on every interface;" \
         "set the Thor's robot-network address instead (software/ros/README.md)" >&2
    exit 1 ;;
esac
source /opt/ros/jazzy/setup.bash
exec ros2 launch rosbridge_server rosbridge_websocket_launch.xml address:="$addr"
