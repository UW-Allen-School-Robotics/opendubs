#!/usr/bin/env bash
# The simulated right arm following a person's palm, on the Thor (as any user). See hand_follow.py for the frames.
#
#   hand_follow.sh head-down            the head tilted fully down and HELD there (the dashboard's POST /api/head/hold:
#                                       pan centred, tilt clamped to this robot's band). THE HEAD MOVES.
#   hand_follow.sh sim [--port 8098]    the arm service on MuJoCo and its viewer (grasp_to_sim.sh sim, on this tool's own
#                                       domain, socket and port, so it never meets another session's simulation)
#   hand_follow.sh run [options]        palm -> arm_mount -> right/target; disabled until `enable`
#   hand_follow.sh enable | disable     switch following on or off (/hand_follow/enable on the robot's domain)
#   hand_follow.sh frames [--save F] [--palm x,y,z]   the camera -> arm transform now, without following
#   hand_follow.sh reset | stop         the simulated arm back to ready and following | the simulation ended
#   hand_follow.sh head-release         release the hold (announced for 3 s, then tracking may move the head again)
#
# The palms come from the hand tracker (hand_stack.sh up). `run --dummy` needs no tracker.
set -eo pipefail   # not -u: ROS's setup.bash fails under nounset

HERE="$(cd "$(dirname "$0")" && pwd)"
export GRASP_SIM_DOMAIN="${HAND_SIM_DOMAIN:-78}"
export GRASP_SIM_DIR="${HAND_SIM_DIR:-${TMPDIR:-/tmp}/hand-sim-${USER}}"
DASH="${OPENDUBS_DASH:-http://127.0.0.1:8080}"
ros() {
  set +u; source /opt/gafro/setup.bash
  export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}" FASTDDS_BUILTIN_TRANSPORTS=UDPv4
}

cmd="${1:-}"; shift || true
case "${cmd}" in
  sim)
    args=("$@"); [[ " $* " == *" --port "* ]] || args+=(--port 8098)
    exec "${HERE}/grasp_to_sim.sh" sim "${args[@]}"
    ;;
  reset|stop|ctl)
    exec "${HERE}/grasp_to_sim.sh" "${cmd}" "$@"
    ;;
  run|frames)
    ros
    exec python3 -u "${HERE}/hand_follow.py" "${cmd}" --sim-domain "${GRASP_SIM_DOMAIN}" "$@"
    ;;
  enable|disable)
    ros
    [[ "${cmd}" == enable ]] && v=true || v=false
    exec ros2 service call /hand_follow/enable std_srvs/srv/SetBool "{data: ${v}}"
    ;;
  head-down)
    echo "the head turns to centre and tilts fully down" >&2
    curl -sS -m 5 -X POST "${DASH}/api/head/hold" -H 'Content-Type: application/json' -d '{"pan": 90, "tilt": 0}'; echo
    sleep 2
    curl -sS -m 5 "${DASH}/api/head/hold"; echo
    ;;
  head-release)
    curl -sS -m 5 -X POST "${DASH}/api/head/hold" -H 'Content-Type: application/json' -d '{"op": "off"}'; echo
    ;;
  *)
    sed -n '2,14p' "$0"; exit 1
    ;;
esac
