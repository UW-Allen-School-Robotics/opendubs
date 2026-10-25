#!/usr/bin/env bash
# Run ARCHITECT on opendubs2 under the archit account, the way archit-perception runs. One
# container, archit-architect-server: the demo runner on 127.0.0.1:8098, in the arms profile (no
# perception), writing each approved program as a stamped show to ~/architect-data/shows. It
# moves nothing: a show reaches the arms only through show_sync.py and the dashboard's Shows page
# (software/architect/README.md). ROS domain 77, localhost-only discovery. Nothing here changes the host.
#
#   dev.sh build                   build archit-architect:dev from this checkout
#   dev.sh dry                     the runner with a fixed program (examples/arms_wave.py), no model
#   dev.sh up                      the runner with ARCHITECT writing programs (needs architect.env)
#   dev.sh live                    the runner on the LIVE arm backend (no show): a fixed program (PROGRAM),
#                                  armed by voice, run on each "trick or treat" through the arm service
#                                  (archit-architect-arms, robot_node.py). FAKE ARMS by default: nothing
#                                  moves. ARMS=ros for the real arms (see below)
#   dev.sh kws                     fetch the listener's keyword-spotter model into $DATA/models, once
#   dev.sh listen                  the listener on the Jabra (archit-architect-listener), beside the runner;
#                                  SHOW=<routine>: "trick or treat" plays that show-palette routine through
#                                  the dashboard instead (the Shows page's gates; START=deadman, the default,
#                                  makes the supervisor's fresh deadman press the go; or countdown)
#   dev.sh logs                    follow the runner log
#   dev.sh shows                   the shows written so far
#   dev.sh down                    stop and remove the container
#
# The page: ssh -L 8098:127.0.0.1:8098 archit@opendubs2, then http://localhost:8098/ on the laptop.
#
# Environment: REPO (this repository's clone, default ~/opendubs-architect), SSA (the ssa checkout on
# its opendubs branch, default ~/ssa), DATA (default ~/architect-data), LIMITS (the arm stack's
# joint_limits.yaml, mounted read-only; by default found where the show runner's drop-in binds it from,
# as show_sync.py finds it; without one the test fixture is used and the push re-checks),
# BASE_IMAGE (default archit-perception:dev: Kokoro uses its PyTorch), MODELS (the voice service's
# models, mounted read-only, default /var/lib/opendubs/models), LISTEN=1 (the runner speaks through
# the listener), TTS_DEVICE (cuda, the default: Kokoro on the GPU, 0.14 s a line; or cpu, about 2 s),
# PROGRAM (for dry, a path inside the repository), DOCKER (default: sudo -n docker), PROFILE (arms,
# the default, or arms_see: the program may look with the perception services, which a recorded show
# then follows; the container joins the robot's ROS domain 0 for those read-only calls, as API.md
# says a caller must, with FASTDDS_BUILTIN_TRANSPORTS=UDPv4).
#
# ARMS (live only): fake, the default (every goal followed in memory, a fake deadman held in AUTO; the
# runner still needs the REAL deadman to start a run), or ros: goals to the arm controllers on ROS domain
# 0, the gates read from the dashboard. live_arms.py says why this is not yet allowed on the robot: no
# arm lease, so the show runner and this service must never both send goals. ARMS=ros refuses while
# opendubs-runner is active, and the Arms and Choreography lanes sign off before it is used.
#
# $DATA/architect.env holds ssa's API settings (the variables ssa's own .env has), owned by you
# and mode 600, or `up` refuses. It is passed to the container as its environment, never baked in.
# $DATA/architect-arms.yaml is this robot's arm config (live_arms.CONFIG_TEMPLATE); without it the
# arms take named poses only.
set -euo pipefail

NAME=archit-architect-server
IMAGE=archit-architect:dev
REPO="${REPO:-$HOME/opendubs-architect}"
SSA="${SSA:-$HOME/ssa}"
DATA="${DATA:-$HOME/architect-data}"
PORT=8098
DOCKER="${DOCKER:-sudo -n docker}"
BASE_IMAGE="${BASE_IMAGE:-archit-perception:dev}"
MODELS="${MODELS:-/var/lib/opendubs/models}"
LISTENER=archit-architect-listener
ARMS_NAME=archit-architect-arms

# The arm stack's joint_limits.yaml, the file the show runner validates against: its drop-in binds the
# arm stack's hw_validation/ directory to /etc/opendubs/arm-limits (provision-runner.sh). Read in
# place, never copied, so it cannot go stale.
DROPIN=/etc/systemd/system/opendubs-runner.service.d/arm-limits.conf
find_limits() {
  local src
  src=$(sed -n 's/^BindReadOnlyPaths=-\{0,1\}\([^:]*\):\/etc\/opendubs\/arm-limits\/\{0,1\}$/\1/p' "${DROPIN}" 2>/dev/null | tail -1)
  [ -n "${src}" ] && sudo -n test -f "${src}/joint_limits.yaml" && echo "${src}/joint_limits.yaml"
}

run() {  # $1: show or live; then the runner's arguments
  local backend="$1"; shift
  local args=() env=()
  # Docker would create a missing mount point as an empty, root-owned folder, and the runner would then
  # fail on its first import (config.py needs ssa, even for a fixed program).
  [ -d "${SSA}/ssa" ] || { echo "dev.sh: no ssa checkout at ${SSA} (its opendubs branch; set SSA)" >&2; exit 1; }
  mkdir -p "${DATA}/shows" "${DATA}/home"
  LIMITS="${LIMITS:-$(find_limits || true)}"
  if [ -n "${LIMITS}" ]; then echo "joint limits: ${LIMITS} (read-only)"
  else echo "dev.sh: WARNING: no arm joint limits found (${DROPIN}); recording against the TEST FIXTURE" >&2; fi
  [ -n "${LIMITS:-}" ] && args+=(-v "${LIMITS}:/limits/joint_limits.yaml:ro") \
    && env+=(-e OPENDUBS_ARCHITECT_LIMITS=/limits/joint_limits.yaml)
  [ -f "${DATA}/architect-arms.yaml" ] && env+=(-e OPENDUBS_ARCHITECT_ARMS=/data/architect-arms.yaml)
  if [ -f "${DATA}/architect.env" ]; then
    [ "$(stat -c %a "${DATA}/architect.env")" = 600 ] \
      || { echo "dev.sh: ${DATA}/architect.env must be mode 600 (it holds an API key): chmod 600 ${DATA}/architect.env" >&2; exit 1; }
    env+=(--env-file "${DATA}/architect.env")
  fi
  local say=""
  [ "${LISTEN:-0}" = 1 ] && say="--say-url http://127.0.0.1:8099/api/say"
  local profile="${PROFILE:-arms}" domain=77
  case "${profile}" in
    arms) ;;
    arms_see) domain=0; env+=(-e FASTDDS_BUILTIN_TRANSPORTS=UDPv4) ;;
    *) echo "dev.sh: PROFILE must be arms or arms_see, not ${profile}" >&2; exit 1 ;;
  esac
  local publish="--publish /data/shows"
  if [ "${backend}" = live ]; then
    publish=""
    [ "${ARMS:-fake}" = ros ] && domain=0
  fi
  DOMAIN="${domain}"
  # The runner and the arm service share one IPC namespace (--ipc shareable here, container:NAME there).
  # With localhost-only discovery, Fast DDS still hands a reply over by shared memory, so across two
  # private IPC namespaces a call finds the service and never hears back, even with UDPv4 only: a run
  # waited 60 s on reset_robot (2026-10-24). UDPv4 as software/perception/API.md asks of every caller.
  ${DOCKER} run -d --rm --name "${NAME}" --network host --ipc shareable \
    -e ROS_DOMAIN_ID="${domain}" -e ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST -e FASTDDS_BUILTIN_TRANSPORTS=UDPv4 \
    -e HOME=/data/home -w /data/home \
    -e OPENDUBS_ARCHITECT_PROFILE="${profile}" -e OPENDUBS_ARCHITECT_BACKEND="${backend}" -e OPENDUBS_ARCHITECT_DATA=/data \
    -e PYTHONPATH=/work/software/architect:/ssa -e SSA_ROOT=/ssa \
    "${env[@]}" "${args[@]}" \
    -v "${DATA}:/data" -v "${REPO}:/work:ro" -v "${SSA}:/ssa:ro" \
    "${IMAGE}" bash -c "python3 -u -m opendubs_architect.demo_runner --port ${PORT} ${publish} \
      --log /data/runner.jsonl ${say} $* 2>&1 | tee /data/runner.log"
  echo "started ${NAME} on 127.0.0.1:${PORT}; 'dev.sh logs' to follow"
}

arms() {  # the arm service for the live backend, on the runner's ROS domain ($DOMAIN, set by run's caller)
  local mode="${ARMS:-fake}" args=(--arms fake --robot fake) vols=()
  case "${mode}" in
    fake) ;;
    ros)
      if systemctl is-active --quiet opendubs-runner; then
        echo "dev.sh: opendubs-runner is active. With no arm lease, two motion sources must never run at once" >&2
        echo "        (live_arms.py). Stop the show runner first, with whoever runs it, or use ARMS=fake." >&2
        exit 1
      fi
      args=(--arms ros --robot dashboard) ;;
    *) echo "dev.sh: ARMS must be fake or ros, not ${mode}" >&2; exit 1 ;;
  esac
  LIMITS="${LIMITS:-$(find_limits || true)}"
  [ -n "${LIMITS}" ] && vols+=(-v "${LIMITS}:/limits/joint_limits.yaml:ro") && args+=(--limits /limits/joint_limits.yaml)
  [ "${mode}" = ros ] && [ -z "${LIMITS}" ] && { echo "dev.sh: ARMS=ros needs the arm stack's joint limits" >&2; exit 1; }
  [ -f "${DATA}/architect-arms.yaml" ] && args+=(--config /data/architect-arms.yaml)
  if ${DOCKER} inspect "${ARMS_NAME}" >/dev/null 2>&1; then
    echo "dev.sh: ${ARMS_NAME} is already running; 'dev.sh down' first" >&2; exit 1
  fi
  ${DOCKER} run -d --rm --name "${ARMS_NAME}" --network host --ipc "container:${NAME}" \
    -e ROS_DOMAIN_ID="${DOMAIN}" -e ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST -e FASTDDS_BUILTIN_TRANSPORTS=UDPv4 \
    -e HOME=/data/home -w /data/home -e PYTHONPATH=/work/software/architect \
    -v "${DATA}:/data" -v "${REPO}:/work:ro" "${vols[@]}" \
    "${IMAGE}" bash -c "python3 -u -m opendubs_architect.robot_node ${args[*]} 2>&1 | tee /data/arms.log"
  echo "started ${ARMS_NAME} (ARMS=${mode}, ROS domain ${DOMAIN})"
}

listen() {  # the Jabra, the spotter, Parakeet and Kokoro; posts to the runner on 127.0.0.1:${PORT}
  local gid tts=() show="" ; gid=$(getent group audio | cut -d: -f3)
  [ -n "${SHOW:-}" ] && show="--show ${SHOW} --start ${START:-deadman}"
  [ "${LOG_HEARD:-0}" = 1 ] && show="${show} --log-heard"     # tuning: what Parakeet wrote for a refused hit
  [ -f "${DATA}/models/kws-gigaspeech-3.3m/tokens.txt" ] || { echo "dev.sh: no spotter model; run 'dev.sh kws' first" >&2; exit 1; }
  if ${DOCKER} inspect "${LISTENER}" >/dev/null 2>&1; then
    echo "dev.sh: ${LISTENER} is already running; 'dev.sh down' first" >&2; exit 1
  fi
  [ "${TTS_DEVICE:-cuda}" = cuda ] && tts=(--runtime nvidia -e VOICE_TTS_DEVICE=cuda)
  ${DOCKER} run -d --rm --name "${LISTENER}" --network host \
    --device /dev/snd --group-add "${gid}" "${tts[@]}" \
    -e HOME=/data/home -w /data/home -e PYTHONPATH=/work/software/architect \
    -e OPENDUBS_VOICE_MODELS=/models -e HF_HUB_OFFLINE=1 -e HF_HOME=/tmp/hf \
    -e HF_HUB_CACHE=/models/hf/hub -e HUGGINGFACE_HUB_CACHE=/models/hf/hub -e TRANSFORMERS_CACHE=/models/hf/hub \
    -v "${DATA}:/data" -v "${REPO}:/work:ro" -v "${MODELS}:/models:ro" \
    "${IMAGE}" bash -c "python3 -u -m opendubs_architect.listener --models /models --kws-models /data/models ${show} \
      2>&1 | tee /data/listener.log"
  if [ -n "${SHOW:-}" ]; then echo "started ${LISTENER}: the Jabra; trick or treat plays ${SHOW} (start ${START:-deadman})"
  else echo "started ${LISTENER}: the Jabra, say on 127.0.0.1:8099; start the runner with LISTEN=1"; fi
}

case "${1:-}" in
  build)
    ${DOCKER} build -f "${REPO}/software/architect/image/Dockerfile" -t "${IMAGE}" \
      --build-arg BASE_IMAGE="${BASE_IMAGE}" --build-context voice="${REPO}/software/voice" \
      "${REPO}/software/architect"
    ;;
  kws)
    mkdir -p "${DATA}/models"
    ${DOCKER} run --rm -v "${DATA}:/data" -v "${REPO}:/work:ro" -e PYTHONPATH=/work/software/architect \
      "${IMAGE}" python3 -m opendubs_architect.kws fetch /data/models
    ;;
  listen)
    listen
    ;;
  live)
    [ -n "${PROGRAM:-}" ] || echo "dev.sh: PROGRAM not set; the fixed program is examples/arms_wave.py" >&2
    run live --agent program --program "/work/${PROGRAM:-software/architect/examples/arms_wave.py}"
    arms
    ;;
  dry)
    run show --agent program --program "/work/${PROGRAM:-software/architect/examples/arms_wave.py}"
    ;;
  up)
    [ -f "${DATA}/architect.env" ] || { echo "dev.sh: no ${DATA}/architect.env (ssa's API settings)" >&2; exit 1; }
    run show --agent ssa
    ;;
  logs)
    ${DOCKER} logs -f "${NAME}"
    ;;
  shows)
    ls -l "${DATA}/shows"
    ;;
  down)
    ${DOCKER} stop -t 5 "${NAME}" >/dev/null 2>&1 && echo "stopped ${NAME}"
    ${DOCKER} stop -t 5 "${ARMS_NAME}" >/dev/null 2>&1 && echo "stopped ${ARMS_NAME}"
    ${DOCKER} stop -t 5 "${LISTENER}" >/dev/null 2>&1 && echo "stopped ${LISTENER}"
    sudo -n chown -R "$(id -u):$(id -g)" "${DATA}" 2>/dev/null || true
    ;;
  *)
    sed -n '2,42p' "$0"; exit 1
    ;;
esac
