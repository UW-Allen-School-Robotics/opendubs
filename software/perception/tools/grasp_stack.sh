#!/usr/bin/env bash
# The grasp pipeline on opendubs2 under the archit account, in one command
# (notes/workplan-perception.md, Phases 4 and 5): a label in, a grasp in the camera frame out.
#
#   camera   archit-perception-camera   the Femto Bolt driver (tools/camera.sh), /camera/...
#   grasp    archit-perception-grasp    the perception server (YOLO-World, NanoSAM, CLIPSeg; no
#                                       navigation) and the grasp_object node (tools/serve_grasp.sh)
#
# Both on the robot's ROS domain 0; everything this publishes is under /perception
# (/perception/segment, /perception/grasp_object). AnyGrasp runs on the workstation
# (anygrasp_server/); its address comes from ANYGRASP_URL, else ~/perception-data/anygrasp_url.
#
#   grasp_stack.sh up             start whatever is not running, wait until it is ready
#   grasp_stack.sh grasp bottle   one grasp_object; results in ~/perception-data/results/grasp-<time>
#   grasp_stack.sh status | logs | down
#   grasp_stack.sh install        TEST: two systemd units (deploy/archit-perception*.service) so it
#                                 runs from boot; up/down/logs then go through systemd
#   grasp_stack.sh uninstall      remove them again
#   grasp_stack.sh serve          the grasp container in the foreground (the unit's ExecStart)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NAME=archit-perception-grasp
CAMERA=archit-perception-camera
IMAGE=archit-perception:dev
UNITS=(archit-perception-camera.service archit-perception.service)
REPO="${REPO:-$HOME/opendubs-perception}"
DATA="${DATA:-$HOME/perception-data}"
DOCKER="sudo -n docker"
NS=(--ros-args -r __ns:=/perception)
in_ctr() { local opts=(); while [[ "$1" == -* ]]; do opts+=("$1"); shift; done; ${DOCKER} exec "${opts[@]}" "$1" /opt/perception/entrypoint.sh "${@:2}"; }
running() { ${DOCKER} inspect -f '{{.State.Running}}' "$1" 2>/dev/null | grep -q true; }
installed() { [[ -f /etc/systemd/system/archit-perception.service ]]; }
url() {
  if [[ -n "${ANYGRASP_URL:-}" ]]; then echo "${ANYGRASP_URL}"
  elif [[ -s "${DATA}/anygrasp_url" ]]; then head -n1 "${DATA}/anygrasp_url"
  fi
}
health() { curl -s -m 5 "$(echo "$1" | sed -E 's#^ws(s?)://#http\1://#; s#/[^/]*$#/health#')"; }

# The grasp container: detached for `up`, in the foreground for the unit.
run_grasp() {
  local mode="$1" URL
  URL="$(url)"
  [[ -n "${URL}" ]] || { echo "no AnyGrasp address: set ANYGRASP_URL or write it to ${DATA}/anygrasp_url" >&2; exit 1; }
  # First, so `up` never reads the last run's "up" lines as this one's.
  mkdir -p "${DATA}/results"
  rm -f "${DATA}/results/server.log" "${DATA}/results/tasks.log"
  # The server joins the camera container's shared memory, so the camera must exist first.
  local i
  for ((i = 0; i < 60; i++)); do running "${CAMERA}" && break; sleep 1; done
  running "${CAMERA}" || { echo "${CAMERA} is not running" >&2; exit 1; }
  ${DOCKER} rm -f "${NAME}" >/dev/null 2>&1 || true
  local detach=()
  [[ "${mode}" == detached ]] && detach=(-d)
  ${DOCKER} run "${detach[@]}" --rm --name "${NAME}" --runtime nvidia --network host --ipc "container:${CAMERA}" \
    -e ROS_DOMAIN_ID=0 -e ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET \
    -e HOME=/data/home -w /data/home -e HF_HOME=/data/models/hf -e HF_HUB_OFFLINE=1 \
    -e ANYGRASP_URL="${URL}" \
    -e PYTHONPATH=/work/software/perception:/opt/nanosam:/opt/perception_models \
    -v "${DATA}:/data" -v "${REPO}:/work:ro" \
    "${IMAGE}" bash /work/software/perception/tools/serve_grasp.sh
}

# Wait for a line in a log under DATA/results: wait_for <file> <pattern> <seconds> <what>
wait_for() {
  local i
  for ((i = 0; i < $3; i++)); do
    grep -q "$2" "${DATA}/results/$1" 2>/dev/null && return 0
    sleep 1
  done
  echo "$4 not ready after $3 s; see ${DATA}/results/$1" >&2
  return 1
}

case "${1:-}" in
  serve)
    run_grasp foreground
    ;;
  up)
    if installed; then
      sudo -n systemctl start "${UNITS[@]}"
      echo "started ${UNITS[*]}"
    else
      if running "${CAMERA}"; then echo "camera: already running"; else "${HERE}/camera.sh" up; fi
      if running "${NAME}"; then echo "grasp: already running"; else run_grasp detached >/dev/null; echo "grasp: starting"; fi
    fi
    for ((i = 0; i < 120; i++)); do running "${NAME}" && break; sleep 1; done
    running "${NAME}" || { echo "${NAME} did not start; see '$0 logs'" >&2; exit 1; }
    wait_for server.log "perception server up" 240 "server"
    wait_for tasks.log "perception tasks up" 60 "tasks"
    URL="$(url)"
    health "${URL}" >/dev/null || echo "warning: the AnyGrasp workstation at ${URL} does not answer /health" >&2
    echo "ready: /perception/grasp_object on domain 0, AnyGrasp at ${URL}"
    echo "try:   $0 grasp bottle"
    ;;
  grasp)
    shift
    [[ $# -ge 1 ]] || { echo "usage: $0 grasp <label> [grasp_client.py options]" >&2; exit 1; }
    label="$1"; shift
    out="/data/results/grasp-$(date +%H%M%S)"
    in_ctr "${NAME}" python3 /work/software/perception/tools/grasp_client.py --label "${label}" --camera femto \
      --out "${out}" "$@" "${NS[@]}"
    echo "saved to ${DATA}/results/$(basename "${out}")"
    ;;
  status)
    if installed; then
      for u in "${UNITS[@]}"; do echo "${u}: $(systemctl is-active "${u}") ($(systemctl is-enabled "${u}") at boot)"; done
    fi
    for c in "${CAMERA}" "${NAME}"; do
      if running "$c"; then echo "$c: running"; else echo "$c: stopped"; fi
    done
    running "${CAMERA}" && "${HERE}/camera.sh" status 2>/dev/null | grep -E "average rate" || true
    URL="$(url)"
    if [[ -n "${URL}" ]]; then echo "anygrasp ${URL}: $(health "${URL}" || echo unreachable)"; fi
    ;;
  logs)
    if installed; then
      sudo -n journalctl -f -u archit-perception-camera -u archit-perception
    else
      tail -F "${DATA}/results/server.log" "${DATA}/results/tasks.log"
    fi
    ;;
  down)
    if installed; then
      sudo -n systemctl stop "${UNITS[@]}"
      echo "stopped ${UNITS[*]} (still enabled at boot; 'uninstall' removes them)"
    else
      if running "${NAME}"; then ${DOCKER} stop -t 10 "${NAME}" >/dev/null && echo "stopped ${NAME}"; fi
      if running "${CAMERA}"; then "${HERE}/camera.sh" down; fi
    fi
    sudo -n chown -R "$(id -u):$(id -g)" "${DATA}"
    ;;
  install)
    # Stop copies started by hand, so the units own the containers from here on.
    if running "${NAME}"; then ${DOCKER} stop -t 10 "${NAME}" >/dev/null; fi
    if running "${CAMERA}"; then ${DOCKER} stop -t 5 "${CAMERA}" >/dev/null; fi
    for u in "${UNITS[@]}"; do
      sudo -n install -m 0644 "${REPO}/software/perception/deploy/${u}" "/etc/systemd/system/${u}"
    done
    sudo -n systemctl daemon-reload
    sudo -n systemctl enable --now "${UNITS[@]}"
    echo "installed and started ${UNITS[*]}; they start at every boot ('uninstall' removes them)"
    ;;
  uninstall)
    sudo -n systemctl disable --now "${UNITS[@]}" 2>/dev/null || true
    for u in "${UNITS[@]}"; do sudo -n rm -f "/etc/systemd/system/${u}"; done
    sudo -n systemctl daemon-reload
    echo "removed ${UNITS[*]}"
    ;;
  *)
    sed -n '2,21p' "$0"; exit 1
    ;;
esac
