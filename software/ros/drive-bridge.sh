#!/usr/bin/env bash
# OpenDubs drive bridge wrapper (ROS 2 Jazzy). /cmd_vel → the StamPLC's `DRIVE` command, and the
# StamPLC's `[ODO]` line → /odom + TF odom→base_link. Speaks UDP 5005 directly: the dashboard is
# not in the motion path.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
# Top speed is UNMEASURED — see punch list C2. Override once it is known:
#   OPENDUBS_MAX_LIN=0.8 OPENDUBS_MAX_ANG=1.5 ./drive-bridge.sh
exec python3 /opt/opendubs/ros/drive_bridge.py --ros-args \
  -p max_linear_mps:="${OPENDUBS_MAX_LIN:-0.5}" \
  -p max_angular_rps:="${OPENDUBS_MAX_ANG:-1.0}" \
  -p publish_tf:="${OPENDUBS_ODOM_TF:-true}"
