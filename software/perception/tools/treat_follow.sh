#!/usr/bin/env bash
# Trick or treat on the Thor (as any user): the head finds and centres a person, the simulated right arm follows a
# hand they hold out, and goes home when it drops. See treat_follow.py and treat_brain.py.
#
#   treat_follow.sh up [options]        everything, in the background: the camera (if not running), the hand tracker,
#                                       the simulated arm and its viewer (http://<thor>:8098), and the follower with its
#                                       services. Nothing moves until `start`.
#   treat_follow.sh start               /perception/treat_follow/start: THE HEAD STARTS MOVING (searching, centring)
#   treat_follow.sh stop                /perception/treat_follow/stop: the head still, the arm holds where it is
#   treat_follow.sh status              what is running, and /perception/treat_follow/status
#   treat_follow.sh down [--camera]     the follower, the simulated arm and the tracker stopped
#   treat_follow.sh run [options]       the follower in the foreground (what `up` starts); options: treat_follow.py -h
#   treat_follow.sh reset | ctl ...     the simulated arm (grasp_to_sim.sh's)
#
# It shares the camera, the tracker and the simulated arm with hand_follow.sh, and refuses to start while that
# follower runs: two followers would fight over right/target and the head.
set -eo pipefail   # not -u: ROS's setup.bash fails under nounset

HERE="$(cd "$(dirname "$0")" && pwd)"
export GRASP_SIM_DOMAIN="${HAND_SIM_DOMAIN:-78}"
export GRASP_SIM_DIR="${HAND_SIM_DIR:-${TMPDIR:-/tmp}/hand-sim-${USER}}"
WS="${ROBOT_API_WS:-$HOME/robot_api_ws}"
PID="${GRASP_SIM_DIR}/treat.pid"
LOG="${GRASP_SIM_DIR}/treat.log"
export HAND_ARM_SOCKET="${GRASP_SIM_DIR}/arm-right.sock"
ros() {
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
    if alive "${GRASP_SIM_DIR}/follow.pid"; then
      echo "hand_follow's follower is running (${GRASP_SIM_DIR}/follow.pid): stop it first (TERM that pid)" >&2; exit 1
    fi
    mkdir -p "${GRASP_SIM_DIR}"
    "${HERE}/hand_stack.sh" up
    if alive "${GRASP_SIM_DIR}/pids"; then
      echo "sim: already running; $("${HERE}/grasp_to_sim.sh" ctl follow 2>&1 | tail -1)"
    else
      setsid nohup "${HERE}/grasp_to_sim.sh" sim --port 8098 > "${GRASP_SIM_DIR}/sim.out" 2>&1 < /dev/null &
      wait_log "${GRASP_SIM_DIR}/sim.out" "following right/target" 90 \
        || { echo "the simulated arm did not start; see ${GRASP_SIM_DIR}/sim.out" >&2; tail -5 "${GRASP_SIM_DIR}/sim.out" >&2; exit 1; }
      echo "sim: up, viewer at http://$(hostname -I | cut -d' ' -f1):8098"
    fi
    if alive "${PID}"; then
      echo "treat follower: already running"
    else
      setsid nohup "$0" run "$@" > "${LOG}" 2>&1 < /dev/null &
      echo $! > "${PID}"
      wait_log "${LOG}" "services /perception/treat_follow" 60 \
        || { echo "the follower did not start; see ${LOG}" >&2; tail -5 "${LOG}" >&2; exit 1; }
      echo "treat follower: up, log ${LOG}"
    fi
    echo "ready, nothing moving: $0 start (the head will move)"
    ;;
  start)
    echo "the head starts moving: searching for a person" >&2
    exec "${HERE}/perception_call.sh" treat_follow/start '{}'
    ;;
  stop)
    exec "${HERE}/perception_call.sh" treat_follow/stop '{}'
    ;;
  status)
    "${HERE}/hand_stack.sh" status
    if alive "${GRASP_SIM_DIR}/pids"; then echo "sim: running"; else echo "sim: stopped"; fi
    if alive "${PID}"; then echo "treat follower: running"; else echo "treat follower: stopped"; fi
    alive "${PID}" && "${HERE}/perception_call.sh" treat_follow/status || true
    ;;
  down)
    if alive "${PID}"; then
      pkill -TERM -P "$(cat "${PID}")" 2>/dev/null || true
      kill -TERM "$(cat "${PID}")" 2>/dev/null || true
      echo "treat follower: stopped"
    fi
    rm -f "${PID}"
    "${HERE}/grasp_to_sim.sh" stop && echo "sim: stopped"
    "${HERE}/hand_stack.sh" down
    if [[ "${1:-}" == --camera ]]; then "${HERE}/camera.sh" down; fi
    ;;
  reset|ctl)
    exec "${HERE}/grasp_to_sim.sh" "${cmd}" "$@"
    ;;
  run)
    ros
    exec python3 -u "${HERE}/treat_follow.py" --sim-domain "${GRASP_SIM_DOMAIN}" "$@"
    ;;
  *)
    sed -n '2,17p' "$0"; exit 1
    ;;
esac
