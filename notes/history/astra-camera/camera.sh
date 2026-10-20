#!/usr/bin/env bash
# OpenDubs camera driver wrapper (ROS 2 Jazzy).
#
# Drives the Orbbec Astra (original, USB 2bc5:0401) via the legacy
# orbbec/ros2_astra_camera driver, PORTED to ROS 2 Jazzy (the modern OrbbecSDK
# does NOT support the original Astra). The driver is built in ~/orbbec_ws —
# rebuild it with rc/astra-jazzy-build.sh. Publishes /camera/{color,depth,ir}/* +
# /camera/depth/points. NOTE: this Astra is a STOPGAP camera, not the final one.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
source "${OPENDUBS_CAMERA_WS:-$HOME/orbbec_ws}/install/setup.bash"
exec ros2 launch astra_camera astra.launch.xml
