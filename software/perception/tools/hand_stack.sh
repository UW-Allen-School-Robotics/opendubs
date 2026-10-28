#!/usr/bin/env bash
# The hand tracker on opendubs2 under the archit account: MediaPipe on the running Femto Bolt driver, palms on ROS.
#
#   camera   archit-perception-camera   the Femto Bolt driver (tools/camera.sh), /camera/...; started if it is not running
#   hand     archit-perception-hand     hand_tracker/node.py: /perception/hand/{left,right}/palm and /perception/hand/state
#
# Both on the robot's ROS domain 0. The tracker only works while something subscribes (tools/hand_follow.sh run, or
# `hand_stack.sh echo`).
#
#   hand_stack.sh build              the image: archit-perception:dev plus MediaPipe (image/hand.Dockerfile)
#   hand_stack.sh models <dir>       copy hand_landmarker.task and pose_landmarker_full.task from <dir> into the data dir
#   hand_stack.sh up | down | status | logs
#   hand_stack.sh echo               one /perception/hand/state message (subscribing turns the tracker on)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NAME=archit-perception-hand
CAMERA=archit-perception-camera
IMAGE=archit-perception-hand:dev
REPO="${REPO:-$(cd "${HERE}/../../.." && pwd)}"
DATA="${DATA:-$HOME/perception-data}"
MODELS="${DATA}/models/mediapipe"
DOCKER="sudo -n docker"
running() { ${DOCKER} inspect -f '{{.State.Running}}' "$1" 2>/dev/null | grep -q true; }

case "${1:-}" in
  build)
    ${DOCKER} build -f "${REPO}/software/perception/image/hand.Dockerfile" -t "${IMAGE}" "${REPO}/software/perception"
    ;;
  models)
    src="${2:?usage: $0 models <dir with the .task files>}"
    mkdir -p "${MODELS}"
    for f in hand_landmarker.task pose_landmarker_full.task; do
      sudo -n install -m 0644 -o "$(id -u)" -g "$(id -g)" "${src}/${f}" "${MODELS}/${f}"
    done
    ls -l "${MODELS}"
    ;;
  up)
    [[ -f "${MODELS}/hand_landmarker.task" && -f "${MODELS}/pose_landmarker_full.task" ]] || { echo "no models in ${MODELS}: $0 models <dir>" >&2; exit 1; }
    if running "${NAME}"; then echo "hand: already running"; exit 0; fi
    if running "${CAMERA}"; then echo "camera: already running"; else "${HERE}/camera.sh" up; fi
    ${DOCKER} rm -f "${NAME}" >/dev/null 2>&1 || true
    # --ipc of the camera's container: the driver's shared memory, as the grasp server reads it
    ${DOCKER} run -d --rm --name "${NAME}" --network host --ipc "container:${CAMERA}" \
      -e ROS_DOMAIN_ID=0 -e ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET -e HOME=/data/home \
      -e PYTHONPATH=/work/software/perception \
      -v "${DATA}:/data" -v "${REPO}:/work:ro" \
      "${IMAGE}" python3 -u -m hand_tracker.node --models /data/models/mediapipe --ros-args -r __ns:=/perception >/dev/null
    for ((i = 0; i < 60; i++)); do
      ${DOCKER} logs "${NAME}" 2>&1 | grep -q "hand tracker up" && break
      running "${NAME}" || break
      sleep 1
    done
    ${DOCKER} logs "${NAME}" 2>&1 | grep -q "hand tracker up" || { echo "${NAME} did not start:" >&2; ${DOCKER} logs "${NAME}" 2>&1 | tail -20 >&2; exit 1; }
    echo "ready: /perception/hand/{left,right}/palm and /perception/hand/state on domain 0 (on demand)"
    ;;
  status)
    for c in "${CAMERA}" "${NAME}"; do if running "$c"; then echo "$c: running"; else echo "$c: stopped"; fi; done
    ;;
  logs)
    ${DOCKER} logs -f "${NAME}"
    ;;
  echo)
    ${DOCKER} exec "${NAME}" /opt/perception/entrypoint.sh timeout 15 ros2 topic echo --once /perception/hand/state
    ;;
  down)
    # only the tracker: the camera is shared with the grasp stack
    if running "${NAME}"; then ${DOCKER} stop -t 5 "${NAME}" >/dev/null && echo "stopped ${NAME}"; fi
    ;;
  *)
    sed -n '2,13p' "$0"; exit 1
    ;;
esac
