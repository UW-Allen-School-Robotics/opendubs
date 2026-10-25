#!/usr/bin/env bash
# OpenDubs robot_state_publisher: publishes the robot's own frames (base_link -> datum -> laser,
# laser_rear, the wheels, ...) from description/opendubs/opendubs.urdf.
#
# It runs in the /opendubs namespace because the arms' container already runs a
# robot_state_publisher on /robot_description, which the arms' controller_manager reads: this one
# must never publish there. Only TF is remapped to the global /tf and /tf_static. Moving joints
# (zaxis, head_pan, head_tilt) are read from /opendubs/joint_states: head_pan and head_tilt arrive from
# head_joint_relay.py while the neck answers; zaxis has no source yet (workplan L.2b), so base_link ->
# lift_platform is absent (the head and the arms both ride the lift, so frames between them resolve
# without it). The LIDARs hang off fixed joints.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
URDF="${OPENDUBS_URDF:-/opt/opendubs/description/opendubs.urdf}"
[ -s "$URDF" ] || { echo "no URDF at $URDF" >&2; exit 1; }
# The URDF goes in as a parameters file: XML on the command line is parsed as YAML and breaks.
PARAMS=$(mktemp /tmp/opendubs-rsp-XXXXXX.yaml)
trap 'rm -f "$PARAMS"' EXIT
python3 - "$URDF" "$PARAMS" <<'PY'
import sys, yaml
urdf = open(sys.argv[1]).read()
yaml.safe_dump({"/**": {"ros__parameters": {"robot_description": urdf}}}, open(sys.argv[2], "w"))
PY
ros2 run robot_state_publisher robot_state_publisher --ros-args \
  -r __ns:=/opendubs -r /tf:=/tf -r /tf_static:=/tf_static --params-file "$PARAMS"
