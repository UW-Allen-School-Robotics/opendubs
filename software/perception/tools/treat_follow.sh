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
#   treat_follow.sh reach [options]     V1, one shot (treat_reach.py): the tracker and the simulated arm started if not
#                                       running (no follower), the head centres the person (THE HEAD MOVES), a raised and
#                                       still hand is sent once to the SIMULATED arm's reach_pose action; then it exits
#   treat_follow.sh reach-real [opts]   the same on THE REAL RIGHT ARM (treat_reach.py --real): the head still, slow, and
#                                       nothing moves until the deadman is held; letting go ends it. Needs the real arm
#                                       service started, supervised and activated, on a checkout with the command action
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
arm_interfaces() {   # opendubs_arm_interfaces (the ArmCommand action) into the same workspace, as perception_call.sh builds its own
  local pkg="${HERE}/../../openarm/interfaces/opendubs_arm_interfaces"
  if [[ ! -f "${WS}/install/opendubs_arm_interfaces/share/opendubs_arm_interfaces/package.xml" ]] \
     || ! cmp -s "${pkg}/action/ArmCommand.action" "${WS}/src/opendubs_arm_interfaces/action/ArmCommand.action"; then
    echo "building opendubs_arm_interfaces in ${WS} ..." >&2
    mkdir -p "${WS}/src"; rm -rf "${WS}/src/opendubs_arm_interfaces"; cp -r "${pkg}" "${WS}/src/opendubs_arm_interfaces"
    (set +u; source /opt/ros/jazzy/setup.bash; cd "${WS}" && colcon build --packages-select opendubs_arm_interfaces >/dev/null) \
      || { echo "build failed; run colcon in ${WS} to see why" >&2; exit 1; }
  fi
}
stack_up() {   # the tracker and the simulated arm, not the follower
  mkdir -p "${GRASP_SIM_DIR}"
  "${HERE}/hand_stack.sh" up
  if alive "${GRASP_SIM_DIR}/pids"; then
    echo "sim: already running"
  else
    setsid nohup "${HERE}/grasp_to_sim.sh" sim --port 8098 > "${GRASP_SIM_DIR}/sim.out" 2>&1 < /dev/null &
    wait_log "${GRASP_SIM_DIR}/sim.out" "following right/target" 90 \
      || { echo "the simulated arm did not start; see ${GRASP_SIM_DIR}/sim.out" >&2; tail -5 "${GRASP_SIM_DIR}/sim.out" >&2; exit 1; }
    echo "sim: up, viewer at http://$(hostname -I | cut -d' ' -f1):8098"
  fi
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
  reach)
    if alive "${PID}" || alive "${GRASP_SIM_DIR}/follow.pid"; then
      echo "a follower is running: stop it first ($0 down, or TERM ${PID})" >&2; exit 1
    fi
    arm_interfaces
    stack_up
    ros
    exec python3 -u "${HERE}/treat_reach.py" --sim-domain "${GRASP_SIM_DOMAIN}" "$@"
    ;;
  reach-real)
    if alive "${PID}" || alive "${GRASP_SIM_DIR}/follow.pid"; then
      echo "a follower is running: stop it first ($0 down, or TERM ${PID})" >&2; exit 1
    fi
    arm_interfaces
    "${HERE}/hand_stack.sh" up
    ros
    exec python3 -u "${HERE}/treat_reach.py" --real "$@"
    ;;
  run)
    ros
    exec python3 -u "${HERE}/treat_follow.py" --sim-domain "${GRASP_SIM_DOMAIN}" "$@"
    ;;
  *)
    sed -n '2,23p' "$0"; exit 1
    ;;
esac
