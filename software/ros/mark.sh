#!/bin/bash
# mark.sh "label": one line in the current capture's marks.txt (time, label, /odom and SLAM pose, head joints).
HERE=$(dirname "$(readlink -f "$0")")
set -a; . /etc/opendubs/ros-dds.env; set +a; source /opt/ros/jazzy/setup.bash
exec python3 "$HERE/mark.py" "$@"
