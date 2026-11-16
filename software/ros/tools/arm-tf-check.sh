#!/usr/bin/env bash
# READ-ONLY, on opendubs2: does the arm tree reach the global TF, and does base_link -> hand_tcp resolve?
# Publishes nothing, moves nothing. From the repo root on the Mac:
#   ssh opendubs2 'bash -s' < software/ros/tools/arm-tf-check.sh
source /opt/ros/jazzy/setup.bash
set -a; [ -r /etc/opendubs/robot.env ] && . /etc/opendubs/robot.env >/dev/null 2>&1; set +a
echo "== who publishes /tf_static, /tf"; ros2 topic info -v /tf_static 2>/dev/null | grep -E 'Node name|Node namespace'; ros2 topic info /tf 2>/dev/null | grep -i count
echo "== robot_state_publisher nodes"; ros2 node list 2>/dev/null | grep -i robot_state_publisher
echo "== the arms' world -> openarm_body_link0 joint, as the container runs it"
ros2 param get /robot_state_publisher robot_description 2>/dev/null | sed 's/>/>\n/g' | grep -A3 'openarm_body_world_joint' | grep -E 'parent|child|origin' | head -3
for pair in "world openarm_left_hand_tcp" "base_link arm_mount" "base_link openarm_left_hand_tcp"; do
  echo "== tf2_echo $pair"; timeout 6 ros2 run tf2_ros tf2_echo $pair 2>&1 | grep -m3 -E 'Translation|Rotation: in RPY \(degree\)|Invalid|not exist|two|unconnected|Could not'
done
echo "== frames with parent world, or world as a child"; timeout 6 ros2 topic echo --once /tf_static 2>/dev/null | grep -B1 -A1 'frame_id: world\|child_frame_id: world' | head
