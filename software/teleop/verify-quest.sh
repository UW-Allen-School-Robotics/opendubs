#!/usr/bin/env bash
# Robot-side proof-of-life check for Quest teleop. Run ON the Thor while the Quest
# app is running + connected to rosbridge. Reports whether each topic is live + its
# rate. NOTE: do NOT `set -u` — ROS's setup.bash aborts under nounset.
source /opt/ros/jazzy/setup.bash

TOPICS=(
  /quest/hmd/pose
  /quest/controller/left/pose
  /quest/controller/right/pose
  /quest/controller/left/joy
  /quest/controller/right/joy
)

echo "== Quest teleop topics (rosbridge :9090) =="
echo "-- advertised topics --"
ros2 topic list 2>/dev/null | grep '^/quest/' || echo "  (none yet — is the Quest app running + connected?)"
echo "-- live rate per topic (3 s sample) --"
for t in "${TOPICS[@]}"; do
  printf "  %-34s " "$t"
  timeout 4 ros2 topic hz "$t" 2>/dev/null | grep -m1 "average rate" || echo "(no data)"
done
echo
echo "Tip: 'ros2 topic echo /quest/hmd/pose' to see live pose, or open Foxglove"
echo "(ws://opendubs2.local:8765) and add a 3D panel to watch the frames move."
