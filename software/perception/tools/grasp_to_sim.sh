#!/usr/bin/env bash
# Perception's grasp on the simulated right arm, on the Thor (as any user):
#
#   grasp_to_sim.sh sim [--port 8097]                      # the arm service on MuJoCo, and its viewer (http://<thor>:8097)
#   grasp_to_sim.sh run --label bottle                     # grasp_object -> TF into the arm's frame -> the simulated arm
#   grasp_to_sim.sh run --label bottle --dry-run           # the same, printing the targets without moving anything
#
# `sim` runs software/openarm/gafro/arm_service.py with --backend mujoco: the real arm's own service and its `follow_pose`
# position controller, on the arm's model made weightless (MuJoCo's position servo has none of the driver's gravity
# feed-forward; tests/test_arm_service_sim.py does the same). It takes the arm to `ready` and starts `follow`, which
# tracks right/target, the topic `run` publishes. The gripper does not close in MuJoCo (gafro/README.md).
#
# Everything simulated is on its own ROS domain (GRASP_SIM_DOMAIN, 77) and its own command socket, so its joint_states
# never reach the robot's graph and it cannot be mistaken for the real arm's service. `run` reads the camera, TF and
# perception on the robot's domain (ROS_DOMAIN_ID, 0). See grasp_to_sim.py.
set -eo pipefail   # not -u: ROS's setup.bash fails under nounset

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${HERE}/../../.." && pwd)"
SIM_DOMAIN="${GRASP_SIM_DOMAIN:-77}"
SIM_DIR="${GRASP_SIM_DIR:-${TMPDIR:-/tmp}/grasp-sim-${USER}}"
SOCKET="${SIM_DIR}/arm-right.sock"
[[ -f /opt/gafro/setup.bash ]] || { echo "no /opt/gafro: the gafro stack is not installed on this machine" >&2; exit 1; }

cmd="${1:-}"; shift || true
case "${cmd}" in
  sim)
    port=8097
    while [[ $# -gt 0 ]]; do case "$1" in --port) port="$2"; shift 2 ;; *) echo "unknown: $1" >&2; exit 1 ;; esac; done
    set +u; source /opt/gafro/setup.bash
    # opendubs_arm_interfaces, if built (treat_follow.sh reach builds it): the service then serves /right_arm/command
    ARM_WS="${ROBOT_API_WS:-$HOME/robot_api_ws}"
    [[ -f "${ARM_WS}/install/setup.bash" ]] && source "${ARM_WS}/install/setup.bash"
    export ROS_DOMAIN_ID="${SIM_DOMAIN}" FASTDDS_BUILTIN_TRANSPORTS=UDPv4
    if ss -ltn 2>/dev/null | grep -qE ":${port}\b"; then
      echo "port ${port} is in use; pick another with --port" >&2; exit 1
    fi
    if [[ -S "${SOCKET}" ]]; then
      echo "a simulated arm service already has ${SOCKET}; stop it first (grasp_to_sim.sh stop)" >&2; exit 1
    fi
    mkdir -p "${SIM_DIR}"
    MODEL="${REPO}/description/openarm/gafro/openarm_bimanual.xml"
    WEIGHTLESS="${SIM_DIR}/openarm_bimanual_weightless.xml"
    python3 - "${MODEL}" "${WEIGHTLESS}" <<'PY'
import os, sys, mujoco
spec = mujoco.MjSpec.from_file(sys.argv[1])
for body in spec.bodies:
    if body.name != "world":
        body.gravcomp = 1.0
spec.meshdir = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), spec.meshdir))
open(sys.argv[2], "w").write(spec.to_xml())
PY
    SERVICE="${REPO}/software/openarm/gafro/arm_service.py"
    python3 -u "${SERVICE}" run --side right --backend mujoco --model "${WEIGHTLESS}" --socket "${SOCKET}" > "${SIM_DIR}/service.log" 2>&1 &
    service=$!
    python3 -u -m gafro_ri_ros2_bridge.viewer --model "${MODEL}" --port "${port}" > "${SIM_DIR}/viewer.log" 2>&1 &
    viewer=$!
    echo "${service} ${viewer}" > "${SIM_DIR}/pids"
    trap 'kill "${service}" "${viewer}" 2>/dev/null; rm -f "${SOCKET}" "${SIM_DIR}/pids"' INT TERM EXIT
    for _ in $(seq 60); do [[ -S "${SOCKET}" ]] && break; sleep 0.5; done
    [[ -S "${SOCKET}" ]] || { echo "the arm service did not start; see ${SIM_DIR}/service.log" >&2; tail -5 "${SIM_DIR}/service.log" >&2; exit 1; }
    ctl() { python3 "${SERVICE}" ctl --side right --socket "${SOCKET}" "$@"; }
    ctl activate --allow-large                                   # to `ready`, from wherever the simulator starts the arm
    for _ in $(seq 40); do ctl status | grep -q "activate done" && break; sleep 0.5; done
    ctl follow
    echo "simulated right arm following right/target on ROS domain ${SIM_DOMAIN}; viewer at http://$(hostname -I | cut -d' ' -f1):${port}" >&2
    echo "logs in ${SIM_DIR}; Ctrl-C (or grasp_to_sim.sh stop) ends it" >&2
    wait "${service}"
    ;;
  reset)
    # the simulated arm back to `ready`, then following again. `status` keeps "last: activate done" from the previous
    # activate, so wait for this one to start and then to leave "running", not for that text
    set +u; source /opt/gafro/setup.bash
    ctl() { python3 "${REPO}/software/openarm/gafro/arm_service.py" ctl --side right --socket "${SOCKET}" "$@"; }
    ctl hold >/dev/null
    ctl activate --allow-large
    for _ in $(seq 40); do sleep 0.5; ctl status | head -1 | grep -q "activate running" || break; done
    ctl follow
    ;;
  ctl)
    set +u; source /opt/gafro/setup.bash
    exec python3 "${REPO}/software/openarm/gafro/arm_service.py" ctl --side right --socket "${SOCKET}" "$@"
    ;;
  stop)
    # only the processes this script started (their pids), never another session's service or viewer
    [[ -f "${SIM_DIR}/pids" ]] && kill $(cat "${SIM_DIR}/pids") 2>/dev/null || true
    rm -f "${SOCKET}" "${SIM_DIR}/pids"
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
    sed -n '2,6p' "$0"; exit 1
    ;;
esac
