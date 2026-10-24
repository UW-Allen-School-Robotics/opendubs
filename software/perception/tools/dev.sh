#!/usr/bin/env bash
# Run the perception server on opendubs2 under the archit account (notes/workplan-perception.md).
# Everything runs in one container, archit-perception-server, publishing only on ROS domain 77.
# Camera "femto" is read, subscribe-only, from the robot's graph (domain 0), where tools/camera.sh
# runs the driver; the server joins that container's shared memory, so frames arrive without UDP
# losses. Start the camera first. Discovery is on the subnet so the domain-0 reader finds the driver (localhost
# never did); domain-77 hosts on the LAN can see the server. Nothing here changes the host.
#
#   dev.sh up                      server + dummy camera (DUMMY_ARGS="--switch-every 0" holds one image)
#   dev.sh query bus person        one Segment query; results in ~/perception-data/results/query-<time>
#   dev.sh query wall --camera femto
#   dev.sh grasp mug --camera femto  one GraspObject (Segment, then AnyGrasp); needs ANYGRASP_URL at 'up'
#   dev.sh topics                  navigation output rates
#   dev.sh logs                    follow the server log
#   dev.sh down                    stop and remove the container
set -euo pipefail

NAME=archit-perception-server
CAMERA=archit-perception-camera
IMAGE=archit-perception:dev
REPO="${REPO:-$HOME/opendubs-perception}"
DATA="${DATA:-$HOME/perception-data}"
CONFIG=/work/software/perception/config/perception.yaml
DOCKER="sudo -n docker"
# docker exec bypasses the image ENTRYPOINT, so run through it to source ROS and perception_msgs.
in_ctr() { local opts=(); while [[ "$1" == -* ]]; do opts+=("$1"); shift; done; ${DOCKER} exec "${opts[@]}" "$1" /opt/perception/entrypoint.sh "${@:2}"; }

case "${1:-}" in
  up)
    ipc=()
    if ${DOCKER} inspect -f '{{.State.Running}}' "${CAMERA}" 2>/dev/null | grep -q true; then
      ipc=(--ipc "container:${CAMERA}")
    else
      echo "${CAMERA} is not running: camera femto will have no frames"
    fi
    ${DOCKER} run -d --rm --name "${NAME}" --runtime nvidia --network host "${ipc[@]}" \
      -e ROS_DOMAIN_ID=77 -e ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET \
      -e HOME=/data/home -w /data/home -e HF_HOME=/data/models/hf -e HF_HUB_OFFLINE=1 \
      -e ANYGRASP_URL="${ANYGRASP_URL:-}" \
      -e PYTHONPATH=/work/software/perception:/opt/nanosam:/opt/perception_models \
      -v "${DATA}:/data" -v "${REPO}:/work:ro" \
      "${IMAGE}" bash -c "python3 -u -m perception_server.server --config ${CONFIG} 2>&1 | tee /data/results/server.log"
    in_ctr -d "${NAME}" bash -c "python3 -u /work/software/perception/tools/dummy_camera.py ${DUMMY_ARGS:-} > /data/results/dummy_camera.log 2>&1"
    # The tasks node is its own process, so a slow AnyGrasp workstation never stalls the models.
    if [[ -n "${ANYGRASP_URL:-}" ]]; then
      in_ctr -d "${NAME}" bash -c "python3 -u -m perception_tasks.node --config /work/software/perception/config/perception_tasks.yaml > /data/results/tasks.log 2>&1"
    else
      echo "ANYGRASP_URL is not set: grasp_object will not run (e.g. ANYGRASP_URL=ws://HOST:8768/grasp dev.sh up)"
    fi
    echo "started ${NAME}; 'dev.sh logs' to follow"
    ;;
  query)
    shift
    out="/data/results/query-$(date +%H%M%S)"
    in_ctr "${NAME}" python3 /work/software/perception/tools/segment_client.py --labels "$@" --out "${out}"
    echo "saved to ${DATA}/results/$(basename "${out}")"
    ;;
  grasp)
    shift
    out="/data/results/grasp-$(date +%H%M%S)"
    in_ctr "${NAME}" python3 /work/software/perception/tools/grasp_client.py --label "$@" --out "${out}"
    echo "saved to ${DATA}/results/$(basename "${out}")"
    ;;
  topics)
    in_ctr "${NAME}" bash -c "timeout 6 ros2 topic hz /perception/dummy/traversability & timeout 6 ros2 topic hz /perception/dummy/obstacles; wait" | grep -E "average rate|topic" || true
    ;;
  logs)
    ${DOCKER} logs -f "${NAME}"
    ;;
  down)
    ${DOCKER} stop -t 5 "${NAME}" >/dev/null && echo "stopped ${NAME}"
    sudo -n chown -R "$(id -u):$(id -g)" "${DATA}"
    ;;
  *)
    sed -n '2,17p' "$0"; exit 1
    ;;
esac
