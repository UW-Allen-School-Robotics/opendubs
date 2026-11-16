#!/usr/bin/env bash
# OpenDubs robot_state_publisher: publishes the robot's own frames (base_link -> datum -> laser,
# laser_rear, the wheels, ...) from description/opendubs/opendubs.urdf.
#
# It runs in the /opendubs namespace because the arms' container already runs a
# robot_state_publisher on /robot_description, which the arms' controller_manager reads: this one
# must never publish there. Only TF is remapped to the global /tf and /tf_static. Moving joints
# (zaxis, head_pan, head_tilt) are read from /opendubs/joint_states, published by opendubs-head-joints
# (head_joints.py: the neck's angles as reported, the lift's travel from /zaxis/travel_m). While the neck is silent
# the frames below the head are absent; the LIDARs hang off fixed joints.
# NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash
URDF="${OPENDUBS_URDF:-/opt/opendubs/description/opendubs.urdf}"
[ -s "$URDF" ] || { echo "no URDF at $URDF" >&2; exit 1; }
# The URDF goes in as a parameters file: XML on the command line is parsed as YAML and breaks.
PARAMS=$(mktemp /tmp/opendubs-rsp-XXXXXX.yaml)
trap 'rm -f "$PARAMS"' EXIT
# The robot's head calibration (/etc/opendubs/head-calibration.yaml, per robot; head_joints.py reads the same
# file for the joint offsets and says on /opendubs/head_calibration that TF carries it): its camera_link
# correction is composed onto the joint whose child is camera_link, origin := origin * correction.
python3 - "$URDF" "$PARAMS" "${OPENDUBS_HEAD_CALIBRATION:-/etc/opendubs/head-calibration.yaml}" <<'PY'
import math, os, sys, yaml
import xml.etree.ElementTree as ET
urdf = open(sys.argv[1]).read()

def rot(r, p, y):     # URDF rpy: R = Rz(y) Ry(p) Rx(r)
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr]]

def rpy(R):
    return (math.atan2(R[2][1], R[2][2]), math.atan2(-R[2][0], math.hypot(R[2][1], R[2][2])), math.atan2(R[1][0], R[0][0]))

cal = yaml.safe_load(open(sys.argv[3])) if os.path.exists(sys.argv[3]) else None
e = ((cal or {}).get("extrinsics") or {}).get("camera_link")
if e:
    root = ET.fromstring(urdf)
    j = next(j for j in root.iter("joint") if j.find("child").get("link") == "camera_link")
    o = j.find("origin")
    xyz = [float(v) for v in o.get("xyz", "0 0 0").split()]
    A = rot(*[float(v) for v in o.get("rpy", "0 0 0").split()])
    E, t = rot(*e.get("rpy_rad", [0, 0, 0])), e.get("xyz_m", [0, 0, 0])
    R = [[sum(A[i][k] * E[k][j_] for k in range(3)) for j_ in range(3)] for i in range(3)]
    xyz = [xyz[i] + sum(A[i][k] * t[k] for k in range(3)) for i in range(3)]
    o.set("xyz", " ".join(f"{v:.6f}" for v in xyz))
    o.set("rpy", " ".join(f"{v:.6f}" for v in rpy(R)))
    urdf = ET.tostring(root, encoding="unicode")
    print(f"head calibration {cal.get('robot')} v{cal.get('version')}: camera_link corrected by {e}", file=sys.stderr)
yaml.safe_dump({"/**": {"ros__parameters": {"robot_description": urdf}}}, open(sys.argv[2], "w"))
PY
ros2 run robot_state_publisher robot_state_publisher --ros-args \
  -r __ns:=/opendubs -r /tf:=/tf -r /tf_static:=/tf_static --params-file "$PARAMS"
