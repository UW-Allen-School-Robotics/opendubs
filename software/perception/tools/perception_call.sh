#!/usr/bin/env bash
# Call a /perception service from a terminal on the Thor (host side, as any user), e.g.
#
#   perception_call.sh grasp_object '{"label": "bottle"}'
#   perception_call.sh locate '{"label": "bottle"}'
#   perception_call.sh detect '{"labels": ["bottle", "cup"]}'
#   perception_call.sh segment '{"labels": ["bottle"], "include_masks": true}'
#   perception_call.sh status
#
# The first run builds robot_api_interfaces into ~/robot_api_ws with the host's ROS 2 Jazzy (about
# a minute; a build in your home, nothing installed on the system). The plain ROS CLI works too
# once that is sourced:
#
#   source /opt/ros/jazzy/setup.bash && source ~/robot_api_ws/install/setup.bash
#   export ROS_DOMAIN_ID=0 FASTDDS_BUILTIN_TRANSPORTS=UDPv4
#   ros2 service call /perception/grasp_object robot_api_interfaces/srv/RobotCommand '{req: "{\"label\": \"bottle\"}"}'
#   ros2 service call /perception/status robot_api_interfaces/srv/RobotQuery
#
# FASTDDS_BUILTIN_TRANSPORTS=UDPv4: the perception container shares memory only with the camera's,
# so a node outside it that tries Fast DDS shared memory finds the service and never hears back.
set -eo pipefail   # not -u: ROS's setup.bash fails under nounset

HERE="$(cd "$(dirname "$0")" && pwd)"
WS="${ROBOT_API_WS:-$HOME/robot_api_ws}"
PKG="${HERE}/../robot_api_interfaces"

source /opt/ros/jazzy/setup.bash
if [[ ! -f "${WS}/install/robot_api_interfaces/share/robot_api_interfaces/package.xml" ]] \
   || ! cmp -s "${PKG}/msg/ResultCode.msg" "${WS}/src/robot_api_interfaces/msg/ResultCode.msg" \
   || ! cmp -s "${PKG}/srv/RobotCommand.srv" "${WS}/src/robot_api_interfaces/srv/RobotCommand.srv"; then
  echo "building robot_api_interfaces in ${WS} ..." >&2
  mkdir -p "${WS}/src"
  rm -rf "${WS}/src/robot_api_interfaces"
  cp -r "${PKG}" "${WS}/src/robot_api_interfaces"
  (cd "${WS}" && colcon build --packages-select robot_api_interfaces >/dev/null) || { echo "build failed; run colcon in ${WS} to see why" >&2; exit 1; }
fi
source "${WS}/install/setup.bash"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}" FASTDDS_BUILTIN_TRANSPORTS=UDPv4

[[ $# -ge 1 ]] || { sed -n '2,9p' "$0"; exit 1; }
exec python3 "${HERE}/perception_call.py" "$@"
