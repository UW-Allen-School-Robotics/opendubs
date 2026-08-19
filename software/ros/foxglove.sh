#!/usr/bin/env bash
# OpenDubs Foxglove bridge launch wrapper (ROS 2 Jazzy).
#
# Runs foxglove_bridge's websocket on 0.0.0.0:8765 so the Foxglove app (desktop/
# web, on a dev laptop — NOT the Thor) can visualize the ROS graph: /scan, TF,
# point clouds, plots, diagnostics, MCAP recording. Engineering/observability
# console — complements (does not replace) the kiosk operator HMI.
#
# Keep this on the robot's onboard network / behind the router — it exposes the
# full ROS graph. NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
exec ros2 launch foxglove_bridge foxglove_bridge_launch.xml
