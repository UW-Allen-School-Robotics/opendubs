#!/usr/bin/env bash
# The grasp pipeline on opendubs2 under the archit account, in one command
# (notes/workplan-perception.md, Phase 4): a label in, a grasp in the camera frame out.
#
#   camera   archit-perception-camera   the Femto Bolt driver (tools/camera.sh), /camera/... on domain 0
#   server   archit-perception-server   YOLO-World, NanoSAM and CLIPSeg; the segment action; no navigation
#   tasks    (in the server container)  the grasp_object action and the AnyGrasp client
#
# AnyGrasp itself runs on the workstation (anygrasp_server/). Its address comes from ANYGRASP_URL,
# else from ~/perception-data/anygrasp_url (one line, e.g. ws://HOST:8768/grasp), so it stays out
# of the repo.
#
#   grasp_stack.sh up            start whatever is not running, wait until it is ready
#   grasp_stack.sh grasp bottle  one grasp_object; results in ~/perception-data/results/grasp-<time>
#   grasp_stack.sh status        what runs, camera rates, the workstation's health
#   grasp_stack.sh logs          follow the server and tasks logs
#   grasp_stack.sh down          stop the server and the camera
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NAME=archit-perception-server
CAMERA=archit-perception-camera
IMAGE=archit-perception:dev
REPO="${REPO:-$HOME/opendubs-perception}"
DATA="${DATA:-$HOME/perception-data}"
CONFIG=/work/software/perception/config/perception_manip.yaml
TASKS_CONFIG=/work/software/perception/config/perception_tasks.yaml
DOCKER="sudo -n docker"
in_ctr() { local opts=(); while [[ "$1" == -* ]]; do opts+=("$1"); shift; done; ${DOCKER} exec "${opts[@]}" "$1" /opt/perception/entrypoint.sh "${@:2}"; }
running() { ${DOCKER} inspect -f '{{.State.Running}}' "$1" 2>/dev/null | grep -q true; }
url() {
  if [[ -n "${ANYGRASP_URL:-}" ]]; then echo "${ANYGRASP_URL}"
  elif [[ -s "${DATA}/anygrasp_url" ]]; then head -n1 "${DATA}/anygrasp_url"
  fi
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
  up)
    URL="$(url)"
    [[ -n "${URL}" ]] || { echo "no AnyGrasp address: set ANYGRASP_URL or write it to ${DATA}/anygrasp_url" >&2; exit 1; }
    if running "${CAMERA}"; then echo "camera: already running"; else "${HERE}/camera.sh" up; fi
    if running "${NAME}"; then
      echo "server: already running (stop it with 'grasp_stack.sh down' to change its config)"
    else
      mkdir -p "${DATA}/results"
      rm -f "${DATA}/results/server.log" "${DATA}/results/tasks.log"
      ${DOCKER} run -d --rm --name "${NAME}" --runtime nvidia --network host --ipc "container:${CAMERA}" \
        -e ROS_DOMAIN_ID=77 -e ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET \
        -e HOME=/data/home -w /data/home -e HF_HOME=/data/models/hf -e HF_HUB_OFFLINE=1 \
        -e ANYGRASP_URL="${URL}" \
        -e PYTHONPATH=/work/software/perception:/opt/nanosam:/opt/perception_models \
        -v "${DATA}:/data" -v "${REPO}:/work:ro" \
        "${IMAGE}" bash -c "python3 -u -m perception_server.server --config ${CONFIG} 2>&1 | tee /data/results/server.log" >/dev/null
      echo "server: starting (the models take about a minute)"
    fi
    if in_ctr "${NAME}" pgrep -f perception_tasks.node >/dev/null 2>&1; then
      echo "tasks: already running"
    else
      in_ctr -d "${NAME}" bash -c "python3 -u -m perception_tasks.node --config ${TASKS_CONFIG} > /data/results/tasks.log 2>&1"
    fi
    wait_for server.log "perception server up" 240 "server"
    wait_for tasks.log "perception tasks up" 30 "tasks"
    curl -s -m 5 "$(echo "${URL}" | sed -E 's#^ws(s?)://#http\1://#; s#/[^/]*$#/health#')" >/dev/null \
      || echo "warning: the AnyGrasp workstation at ${URL} does not answer /health" >&2
    echo "ready: grasp_object on domain 77, AnyGrasp at ${URL}"
    echo "try:   $0 grasp bottle"
    ;;
  grasp)
    shift
    [[ $# -ge 1 ]] || { echo "usage: $0 grasp <label> [grasp_client.py options]" >&2; exit 1; }
    label="$1"; shift
    out="/data/results/grasp-$(date +%H%M%S)"
    in_ctr "${NAME}" python3 /work/software/perception/tools/grasp_client.py --label "${label}" --camera femto --out "${out}" "$@"
    echo "saved to ${DATA}/results/$(basename "${out}")"
    ;;
  status)
    for c in "${CAMERA}" "${NAME}"; do
      if running "$c"; then echo "$c: running"; else echo "$c: stopped"; fi
    done
    if running "${NAME}"; then
      in_ctr "${NAME}" pgrep -f perception_tasks.node >/dev/null 2>&1 && echo "tasks: running" || echo "tasks: stopped"
    fi
    running "${CAMERA}" && "${HERE}/camera.sh" status 2>/dev/null | grep -E "average rate" || true
    URL="$(url)"
    if [[ -n "${URL}" ]]; then
      echo "anygrasp ${URL}: $(curl -s -m 5 "$(echo "${URL}" | sed -E 's#^ws(s?)://#http\1://#; s#/[^/]*$#/health#')" || echo unreachable)"
    fi
    ;;
  logs)
    tail -F "${DATA}/results/server.log" "${DATA}/results/tasks.log"
    ;;
  down)
    if running "${NAME}"; then ${DOCKER} stop -t 5 "${NAME}" >/dev/null && echo "stopped ${NAME}"; fi
    if running "${CAMERA}"; then "${HERE}/camera.sh" down; fi
    sudo -n chown -R "$(id -u):$(id -g)" "${DATA}"
    ;;
  *)
    sed -n '2,18p' "$0"; exit 1
    ;;
esac
