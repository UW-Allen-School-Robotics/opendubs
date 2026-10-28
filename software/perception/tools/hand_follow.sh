#!/usr/bin/env bash
# The simulated right arm following a person's palm, on the Thor (as any user). See hand_follow.py for the frames.
#
#   hand_follow.sh up                   everything, in the background: the camera (if not running), the hand tracker
#                                       (hand_stack.sh), the simulated arm and its viewer (http://<thor>:8098), and the
#                                       follower with its services. Following stays OFF until `start`.
#   hand_follow.sh start [left|right]   /perception/hand_follow/start (RobotCommand): following on; returns at once
#   hand_follow.sh stop                 /perception/hand_follow/stop  (RobotCommand): following and tracking off, arm holds
#   hand_follow.sh status               what is running, and /perception/hand_follow/status (RobotQuery)
#   hand_follow.sh down [--camera]      the follower, the simulated arm and the tracker stopped (the camera too with
#                                       --camera; it is shared with the grasp stack)
#
#   hand_follow.sh head-down | head-release   the head tilted fully down and HELD (POST /api/head/hold; THE HEAD MOVES) | released
#   hand_follow.sh frames [--save F] [--palm x,y,z]   the camera -> arm transform now, without following
#   hand_follow.sh sim | run [options]  the simulated arm | the follower, in the foreground (what `up` starts)
#   hand_follow.sh reset                the simulated arm back to ready, following right/target again
set -eo pipefail   # not -u: ROS's setup.bash fails under nounset

HERE="$(cd "$(dirname "$0")" && pwd)"
export GRASP_SIM_DOMAIN="${HAND_SIM_DOMAIN:-78}"
export GRASP_SIM_DIR="${HAND_SIM_DIR:-${TMPDIR:-/tmp}/hand-sim-${USER}}"
DASH="${OPENDUBS_DASH:-http://127.0.0.1:8080}"
WS="${ROBOT_API_WS:-$HOME/robot_api_ws}"
FOLLOW_PID="${GRASP_SIM_DIR}/follow.pid"
ros() {
  # robot_api_interfaces: perception_call.sh builds it into ~/robot_api_ws the first time
  [[ -f "${WS}/install/setup.bash" ]] || "${HERE}/perception_call.sh" status >/dev/null 2>&1 || true
  set +u; source /opt/gafro/setup.bash; source "${WS}/install/setup.bash"
  export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}" FASTDDS_BUILTIN_TRANSPORTS=UDPv4
}
alive() { [[ -f "$1" ]] && kill -0 $(cat "$1") 2>/dev/null; }
wait_log() {   # wait_log <file> <pattern> <seconds>
  local i; for ((i = 0; i < $3 * 2; i++)); do grep -q "$2" "$1" 2>/dev/null && return 0; sleep 0.5; done; return 1
}

cmd="${1:-}"; shift || true
case "${cmd}" in
  up)
    mkdir -p "${GRASP_SIM_DIR}"
    "${HERE}/hand_stack.sh" up
    if alive "${GRASP_SIM_DIR}/pids"; then
      echo "sim: already running"
    else
      setsid nohup "$0" sim > "${GRASP_SIM_DIR}/sim.out" 2>&1 < /dev/null &
      wait_log "${GRASP_SIM_DIR}/sim.out" "following right/target" 90 \
        || { echo "the simulated arm did not start; see ${GRASP_SIM_DIR}/sim.out" >&2; tail -5 "${GRASP_SIM_DIR}/sim.out" >&2; exit 1; }
      echo "sim: up, viewer at http://$(hostname -I | cut -d' ' -f1):8098"
    fi
    if alive "${FOLLOW_PID}"; then
      echo "follower: already running"
    else
      setsid nohup "$0" run "$@" > "${GRASP_SIM_DIR}/follow.log" 2>&1 < /dev/null &
      echo $! > "${FOLLOW_PID}"
      wait_log "${GRASP_SIM_DIR}/follow.log" "services /perception/hand_follow" 60 \
        || { echo "the follower did not start; see ${GRASP_SIM_DIR}/follow.log" >&2; tail -5 "${GRASP_SIM_DIR}/follow.log" >&2; exit 1; }
      echo "follower: up, log ${GRASP_SIM_DIR}/follow.log"
    fi
    echo "ready, not following: $0 start"
    ;;
  start)
    req='{}'; [[ -n "${1:-}" ]] && req="{\"hand\": \"$1\"}"
    exec "${HERE}/perception_call.sh" hand_follow/start "${req}"
    ;;
  stop)
    exec "${HERE}/perception_call.sh" hand_follow/stop '{}'
    ;;
  status)
    "${HERE}/hand_stack.sh" status
    if alive "${GRASP_SIM_DIR}/pids"; then echo "sim: running"; else echo "sim: stopped"; fi
    if alive "${FOLLOW_PID}"; then echo "follower: running"; else echo "follower: stopped"; fi
    alive "${FOLLOW_PID}" && "${HERE}/perception_call.sh" hand_follow/status || true
    ;;
  down)
    # only what `up` started (its pids), never another session's simulation
    if alive "${FOLLOW_PID}"; then
      pkill -TERM -P "$(cat "${FOLLOW_PID}")" 2>/dev/null || true
      kill -TERM "$(cat "${FOLLOW_PID}")" 2>/dev/null || true
      echo "follower: stopped"
    fi
    rm -f "${FOLLOW_PID}"
    "${HERE}/grasp_to_sim.sh" stop && echo "sim: stopped"
    "${HERE}/hand_stack.sh" down
    if [[ "${1:-}" == --camera ]]; then
      if systemctl is-active -q archit-perception-camera.service 2>/dev/null; then
        sudo -n systemctl stop archit-perception-camera.service && echo "camera: stopped (systemd unit)"
      else
        "${HERE}/camera.sh" down
      fi
    fi
    ;;
  sim)
    args=("$@"); [[ " $* " == *" --port "* ]] || args+=(--port 8098)
    exec "${HERE}/grasp_to_sim.sh" sim "${args[@]}"
    ;;
  reset|ctl)
    exec "${HERE}/grasp_to_sim.sh" "${cmd}" "$@"
    ;;
  run|frames)
    ros
    exec python3 -u "${HERE}/hand_follow.py" "${cmd}" --sim-domain "${GRASP_SIM_DOMAIN}" "$@"
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
    sed -n '2,17p' "$0"; exit 1
    ;;
esac
