#!/usr/bin/env bash
# OpenDubs drive bridge wrapper (ROS 2 Jazzy). /cmd_vel → the StamPLC's `DRIVE` command, and the
# StamPLC's `[ODO]` line → /odom + TF odom→base_link. Speaks UDP 5005 directly: the dashboard is
# not in the motion path.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
# The software speed cap and the full scales at it are per robot, in /etc/opendubs/base.env (the unit
# reads it): OPENDUBS_AUTO_CAP (fraction of full command), OPENDUBS_MAX_LIN / _STRAFE / _ANG (the speeds
# at the cap). See drive_scale.py.
exec python3 /opt/opendubs/ros/drive_bridge.py --ros-args \
  -p max_linear_mps:="${OPENDUBS_MAX_LIN:-0.5}" \
  -p max_strafe_mps:="${OPENDUBS_MAX_STRAFE:-0.0}" \
  -p max_angular_rps:="${OPENDUBS_MAX_ANG:-1.0}" \
  -p auto_cap:="${OPENDUBS_AUTO_CAP:-0.25}" \
  -p fw_auto_max_unit:="${OPENDUBS_FW_AUTO_MAX:-0.35}" \
  -p publish_tf:="${OPENDUBS_ODOM_TF:-true}"
