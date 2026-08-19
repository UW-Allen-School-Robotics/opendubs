#!/usr/bin/env bash
# OpenDubs MAVROS launch wrapper (ROS 2 Jazzy, ArduPilot / apm).
#
# MAVROS owns the Pixhawk serial link and republishes it as /mavros/* ROS topics
# (visible in Foxglove + to any ROS node), AND forwards the raw MAVLink stream to
# a local UDP endpoint so the kiosk dashboard's pymavlink reader can still consume
# RC/GPS/flow — point the dashboard at udpin:127.0.0.1:14550
# (OPENDUBS_MAVLINK_DEV). This is what frees /dev/ttyACM0 from the dashboard's old
# exclusive read. NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash

# Pixhawk 6C primary MAVLink interface (if00), by stable by-id path (portable —
# matches any Holybro/Pixhawk unit; ttyACM numbers shuffle across reboots).
DEV=$(ls /dev/serial/by-id/*Holybro*if00 /dev/serial/by-id/*Pixhawk*if00 2>/dev/null | head -1)
FCU="${OPENDUBS_FCU_URL:-serial://${DEV}:115200}"
# Forward raw MAVLink to the dashboard's local UDP listener (port 14550).
GCS="${OPENDUBS_GCS_URL:-udp://@127.0.0.1:14550}"

echo "MAVROS fcu_url=$FCU gcs_url=$GCS"

# ArduPilot's USB (SR0) stream rates default low, so IMU/GPS/attitude wouldn't
# publish. Request all streams @10 Hz once the node is up — backgrounded, retried
# until the service exists, and re-asserted on every (re)start.
(
  for _ in $(seq 1 30); do
    sleep 2
    ros2 service call /mavros/set_stream_rate mavros_msgs/srv/StreamRate \
      "{stream_id: 0, message_rate: 10, on_off: true}" >/dev/null 2>&1 && break
  done
) &

exec ros2 launch mavros apm.launch fcu_url:="$FCU" gcs_url:="$GCS"
