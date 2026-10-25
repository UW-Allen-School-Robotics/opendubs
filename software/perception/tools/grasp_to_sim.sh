#!/usr/bin/env bash
# Perception's grasp in the gafro OpenArm simulation, on the Thor (as any user):
#
#   grasp_to_sim.sh sim [--port 8093]                      # the simulated right arm and its viewer (http://<thor>:8093)
#   grasp_to_sim.sh run --label bottle                     # grasp_object -> TF into the arm's frame -> the simulated arm
#   grasp_to_sim.sh run --label bottle --dry-run           # the same, printing the targets without moving anything
#
# The simulation runs on its own ROS domain (GRASP_SIM_DOMAIN, 77), so its joint_states never reach the robot's
# graph; `run` reads the camera, TF and perception on the robot's (ROS_DOMAIN_ID, 0). See grasp_to_sim.py.
set -eo pipefail   # not -u: ROS's setup.bash fails under nounset

HERE="$(cd "$(dirname "$0")" && pwd)"
SIM_DOMAIN="${GRASP_SIM_DOMAIN:-77}"
[[ -f /opt/gafro/setup.bash ]] || { echo "no /opt/gafro: the gafro stack is not installed on this machine" >&2; exit 1; }

cmd="${1:-}"; shift || true
case "${cmd}" in
  sim)
    source /opt/gafro/setup.bash
    export ROS_DOMAIN_ID="${SIM_DOMAIN}" FASTDDS_BUILTIN_TRANSPORTS=UDPv4
    port=8093
    for ((i = 1; i <= $#; i++)); do [[ "${!i}" == "--port" ]] && { j=$((i + 1)); port="${!j}"; }; done
    if ss -ltn 2>/dev/null | grep -qE ":${port}\b"; then
      echo "port ${port} is in use; pick another with --port" >&2; exit 1
    fi
    if ros2 node list --no-daemon 2>/dev/null | grep -q openarm_bridge; then
      echo "a simulation is already running on domain ${SIM_DOMAIN}; use it, or set GRASP_SIM_DOMAIN for another" >&2; exit 1
    fi
    [[ " $* " == *" --port "* ]] || set -- "$@" --port "${port}"
    echo "simulation on ROS domain ${SIM_DOMAIN}; viewer at http://$(hostname -I | cut -d' ' -f1):${port}" >&2
    exec gafro-openarm-sim "$@"
    ;;
  run)
    # perception_call.sh builds robot_api_interfaces into ~/robot_api_ws the first time; reuse that.
    WS="${ROBOT_API_WS:-$HOME/robot_api_ws}"
    [[ -f "${WS}/install/setup.bash" ]] || "${HERE}/perception_call.sh" status >/dev/null || true
    source /opt/gafro/setup.bash
    source "${WS}/install/setup.bash"
    export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}" FASTDDS_BUILTIN_TRANSPORTS=UDPv4
    exec python3 "${HERE}/grasp_to_sim.py" --sim-domain "${SIM_DOMAIN}" "$@"
    ;;
  *)
    sed -n '2,9p' "$0"; exit 1
    ;;
esac
