#!/usr/bin/env bash
# OpenDubs RPLIDAR A1 driver launch wrapper (ROS 2 Jazzy) — env-driven so ONE
# script serves BOTH the front and rear units.
#
# The stock `rplidar.launch.py` hardcodes serial_port=/dev/ttyUSB0, topic /scan,
# and frame_id "laser" (its args aren't declared, so `key:=val` is ignored) — it
# can't drive two lidars. So we run the rplidar_composition node directly with an
# explicit device / topic / frame:
#   OPENDUBS_LIDAR_DEV   = serial device   (default /dev/ttyUSB0)
#   OPENDUBS_LIDAR_TOPIC = scan topic      (default scan)
#   OPENDUBS_LIDAR_FRAME = frame_id        (default laser)
#
# Pin OPENDUBS_LIDAR_DEV by USB PORT PATH (/dev/serial/by-path/...), NOT by-id:
# both A1 CP2102 adapters report the same serial "0001", so by-id is ambiguous.
#
# NOTE: do NOT use `set -u` — ROS's setup.bash references unbound vars and aborts.
source /opt/ros/jazzy/setup.bash

DEV="${OPENDUBS_LIDAR_DEV:-/dev/ttyUSB0}"
TOPIC="${OPENDUBS_LIDAR_TOPIC:-scan}"
FRAME="${OPENDUBS_LIDAR_FRAME:-laser}"

exec ros2 run rplidar_ros rplidar_composition --ros-args \
  -r __node:="rplidar_${FRAME}" \
  -p serial_port:="$DEV" \
  -p serial_baudrate:=115200 \
  -p frame_id:="$FRAME" \
  -p angle_compensate:=true \
  -p inverted:=false \
  -r /scan:="/${TOPIC}"
