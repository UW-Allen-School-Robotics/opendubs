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
#   dev.sh gafro                   the runner on TOBI'S ARM SERVICE (the gafro backend, gafro_robot.py): a fixed
#                                  program (PROGRAM, default examples/candy_gafro.py), approved on its page or by
#                                  voice ("yes"), run on each "trick or treat", one action goal per call. The arm
#                                  service (arm_service.py run, supervised and activated) must be up on the
#                                  robot's domain, and nothing else drive that arm meanwhile. THE ARM AND THE HEAD
#                                  MOVE. SIM=1 GAFRO_DOMAIN=<n>: a simulated arm service on its own domain (its
#                                  gripper and the head logged, the deadman counted as held). Start the listener
#                                  without SHOW and with DAY=1, and the coordinator with CANDY_RUNNER=1
#   dev.sh gafro-sim [stop]        a SIMULATED right arm for `SIM=1 dev.sh gafro`: tobi's arm service with --backend mujoco
#                                  (the robot's installed copy, /opt/opendubs/arm) on its own ROS domain (GAFRO_DOMAIN, 78) and
#                                  socket, taken to ready and left there, and the /joint_states merge beside it. Archit's recipe
#                                  (software/perception/tools/grasp_to_sim.sh sim, archit/treat-follow-ros), without his follow
#   dev.sh kws                     fetch the listener's keyword-spotter model into $DATA/models, once
#   dev.sh butler                  fetch the butler's voice (bm_george, British English) into $DATA/models, once
#   dev.sh listen                  the listener on the Jabra (archit-architect-listener), beside the runner;
#                                  SHOW=<routine>: "trick or treat" plays that show-palette routine through
#                                  the dashboard instead (the Shows page's gates; START=deadman, the default,
#                                  makes the supervisor's fresh deadman press the go; or countdown;
#                                  or held: a countdown if the deadman is already held, else the press)
#   dev.sh day                     the demo day's coordinator (archit-architect-day, demo_day.py) on 127.0.0.1:8096:
#                                  patrol, approach a visitor, candy, and the preprogrammed shows, one at a time.
#                                  SHOW=<the candy routine>, CHOREO="monster_mash ghostbusters them_bones" (3-5),
#                                  EVERY="240 480" (a random pick after this many seconds of patrolling); the nav
#                                  is a stub (moves nothing); NAV=hound: the robot's nav stack, told to hold on
#                                  /hound_nav/hold (its bridge must honour it: README); who is near comes from
#                                  the perception services. FAKE=1: no palette and nobody seen, for a desk.
#                                  Start the listener with DAY=1 so "trick or treat" asks it first
#   dev.sh install                 start camera, listen and day at boot: the systemd units in deploy/, filled in with
#                                  this checkout (REPO), DATA, SHOW and you; enabled and started now. TEST UNITS, like
#                                  archit-perception's: no deploy or provisioner installs or touches them. The
#                                  coordinator starts PAUSED (curl -X POST 127.0.0.1:8096/api/resume on the day)
#                                  PROGRAM: the program SHOW was recorded from, for the tab (default examples/bucket_offer.py).
#                                  The listener's unit plays shows' music on the Jabra (SHOW_AUDIO=1, show_music.py)
#   dev.sh uninstall               stop and remove them;   dev.sh status: whether they run
#   dev.sh camera                  the Orbbec's colour image as a small JPEG on 127.0.0.1:8097/frame.jpg, for the
#                                  dashboard's ARCHITECT tab (archit-architect-camera; read-only, subscribed only
#                                  while asked; joins archit-perception-camera's IPC, as the perception server does)
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
# VOICE (listen: butler, the default, bm_george in British English, fetched once by dev.sh butler; or
# heart, the voice service's American voice), JABRA_VOLUME (listen: the Jabra's playback volume, set
# on each start, default 100%),
# PROGRAM (for dry, a path inside the repository), DOCKER (default: sudo -n docker), PROFILE (arms,
# the default, or arms_see: the program may look with the perception services, which a recorded show
# then follows; the container joins the robot's ROS domain 0 for those read-only calls, as API.md
# says a caller must, with the robot's DDS profile like every container here).
#
# PLANNER (show path: dry, up): cbirrt routes a move the straight-line planner refuses as too close
# around the body (routing.py), with PLANNER_ROOT a checkout of feat/arm-motion-planner-ui, mounted
# read-only; every leg is still checked, and the published show swept, as without it.
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
CAMERA_NAME=archit-architect-camera
DAY_NAME=archit-architect-day
# FOREGROUND=1: camera, listen and day run in the foreground (the boot units' way: systemd keeps them)
DETACH=-d
[ "${FOREGROUND:-0}" = 1 ] && DETACH=""
UNITS=(archit-architect-camera archit-architect-listener archit-architect-day)
PERCEPTION_CAMERA=archit-perception-camera
# Every ROS container takes the robot's DDS profile (software/ros, provision-ros.sh; ros-dds.env):
# UDP over loopback only, so the traffic stays on the Thor and no discovery multicast reaches the
# Wi-Fi. Nothing may override its transports (no FASTDDS_BUILTIN_TRANSPORTS, no profile of our own),
# and the range stays SUBNET: in Jazzy LOCALHOST quietly adds shared memory on top of any profile.
ROBOT_DDS=/etc/opendubs/fastdds-robot.xml
DDS=(-v "${ROBOT_DDS}:${ROBOT_DDS}:ro" -e FASTRTPS_DEFAULT_PROFILES_FILE="${ROBOT_DDS}"
     -e FASTDDS_DEFAULT_PROFILES_FILE="${ROBOT_DDS}" -e ROS_AUTOMATIC_DISCOVERY_RANGE=SUBNET)
need_dds() { [ -f "${ROBOT_DDS}" ] || { echo "dev.sh: no ${ROBOT_DDS} (software/ros/provision-ros.sh installs it)" >&2; exit 1; }; }

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
  [ "${backend}" = gafro ] && profile=gafro
  case "${profile}" in
    arms) ;;
    arms_see) domain=0 ;;
    gafro) domain="${GAFRO_DOMAIN:-0}" ;;
    *) echo "dev.sh: PROFILE must be arms or arms_see, not ${profile}" >&2; exit 1 ;;
  esac
  if [ -n "${PLANNER:-}" ]; then
    [ -d "${PLANNER_ROOT:-}/sim/planner" ] || { echo "dev.sh: PLANNER needs PLANNER_ROOT, a checkout of feat/arm-motion-planner-ui" >&2; exit 1; }
    [ "${backend}" = show ] || { echo "dev.sh: PLANNER routes a recorded show only, not the live backend" >&2; exit 1; }
    env+=(-e OPENDUBS_ARCHITECT_PLANNER="${PLANNER}" -e OPENDUBS_PLANNER_ROOT=/planner)
    args+=(-v "${PLANNER_ROOT}:/planner:ro")
  fi
  local publish="--publish /data/shows"
  [ "${backend}" = gafro ] && publish=""
  if [ "${backend}" = live ]; then
    publish=""
    [ "${ARMS:-fake}" = ros ] && domain=0
  fi
  DOMAIN="${domain}"
  # The runner and the arm service share one IPC namespace (--ipc shareable here, container:NAME there).
  # Under ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST Fast DDS handed a reply over by shared memory, so across
  # two private IPC namespaces a call found the service and never heard back: a run waited 60 s on
  # reset_robot (2026-10-24). The robot's profile has no shared memory; the shared namespace stays as
  # cheap insurance.
  need_dds
  ${DOCKER} run -d --rm --name "${NAME}" --network host --ipc shareable \
    -e ROS_DOMAIN_ID="${domain}" "${DDS[@]}" \
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
  need_dds
  ${DOCKER} run -d --rm --name "${ARMS_NAME}" --network host --ipc "container:${NAME}" \
    -e ROS_DOMAIN_ID="${DOMAIN}" "${DDS[@]}" \
    -e HOME=/data/home -w /data/home -e PYTHONPATH=/work/software/architect \
    -v "${DATA}:/data" -v "${REPO}:/work:ro" "${vols[@]}" \
    "${IMAGE}" bash -c "python3 -u -m opendubs_architect.robot_node ${args[*]} 2>&1 | tee /data/arms.log"
  echo "started ${ARMS_NAME} (ARMS=${mode}, ROS domain ${DOMAIN})"
}

camera() {  # the colour image for the dashboard tab, from the perception stack's camera driver
  ${DOCKER} inspect -f '{{.State.Running}}' "${PERCEPTION_CAMERA}" 2>/dev/null | grep -q true \
    || { echo "dev.sh: ${PERCEPTION_CAMERA} is not running (software/perception: tools/grasp_stack.sh up)" >&2; exit 1; }
  if ${DOCKER} inspect "${CAMERA_NAME}" >/dev/null 2>&1; then
    echo "dev.sh: ${CAMERA_NAME} is already running; 'dev.sh down' first" >&2; exit 1
  fi
  # The driver's container shares its IPC namespace: before the robot's profile, the driver handed frames
  # over by shared memory and a container of its own discovered the topic and got none.
  need_dds
  ${DOCKER} run ${DETACH} --rm --name "${CAMERA_NAME}" --network host --ipc "container:${PERCEPTION_CAMERA}" \
    -e ROS_DOMAIN_ID=0 "${DDS[@]}" -e PYTHONPATH=/work/software/architect -v "${REPO}:/work:ro" \
    "${IMAGE}" python3 -u -m opendubs_architect.camera_feed
  echo "started ${CAMERA_NAME}: http://127.0.0.1:8097/frame.jpg"
}

day() {  # the coordinator: reads the palette and the perception services, drives nothing until the nav is real
  # CANDY_RUNNER=1: the candy run is the ARCHITECT runner's program on 127.0.0.1:${PORT} (dev.sh gafro), not
  # a show; SHOW is then only its name. The choreo shows stay the show runner's.
  [ "${CANDY_RUNNER:-0}" = 1 ] && SHOW="${SHOW:-architect-gafro}"
  [ -n "${SHOW:-}" ] || { echo "dev.sh: SHOW=<the candy routine> is needed (the listener's)" >&2; exit 1; }
  if ${DOCKER} inspect "${DAY_NAME}" >/dev/null 2>&1; then
    echo "dev.sh: ${DAY_NAME} is already running; 'dev.sh down' first" >&2; exit 1
  fi
  local args="--candy ${SHOW}"
  [ -n "${CHOREO:-}" ] && args="${args} --choreo ${CHOREO}"
  [ -n "${EVERY:-}" ] && args="${args} --every ${EVERY}"
  [ "${FAKE:-0}" = 1 ] && args="${args} --people fake --palette fake"
  [ "${PAUSED:-0}" = 1 ] && args="${args} --start-paused"
  [ -n "${NAV:-}" ] && args="${args} --nav ${NAV}"                  # hound: the robot's nav stack (HoundNav)
  [ "${CANDY_RUNNER:-0}" = 1 ] && args="${args} --candy-runner http://127.0.0.1:${PORT}"
  need_dds
  ${DOCKER} run ${DETACH} --rm --name "${DAY_NAME}" --network host \
    -e ROS_DOMAIN_ID=0 "${DDS[@]}" -e PYTHONPATH=/work/software/architect \
    -e OPENDUBS_ARCHITECT_ARMS=/data/architect-arms.yaml -v "${DATA}:/data" -v "${REPO}:/work:ro" \
    -v /var/lib/opendubs/shows:/shows:ro \
    "${IMAGE}" bash -c "python3 -u -m opendubs_architect.demo_day --shows /shows ${args} 2>&1 | tee /data/demo_day.log"
  echo "started ${DAY_NAME}: http://127.0.0.1:8096/api/state (start the listener with DAY=1 SHOW=${SHOW})"
}

listen() {  # the Jabra, the spotter, Parakeet and Kokoro; posts to the runner on 127.0.0.1:${PORT}
  local gid tts=() show="" ; gid=$(getent group audio | cut -d: -f3)
  [ -n "${SHOW:-}" ] && show="--show ${SHOW} --start ${START:-deadman}"
  [ "${LOG_HEARD:-0}" = 1 ] && show="${show} --log-heard"
  [ "${DAY:-0}" = 1 ] && show="${show} --day http://127.0.0.1:8096"   # the demo day's coordinator (dev.sh day)
  [ "${SHOW_AUDIO:-0}" = 1 ] && show="${show} --show-audio"         # shows' music on the Jabra (show_music.py)     # tuning: what Parakeet wrote for a refused hit
  [ -n "${SHOW:-}" ] && [ -n "${PROGRAM:-}" ] && show="${show} --program /work/${PROGRAM}"   # the tab shows its code
  [ -f "${DATA}/models/kws-gigaspeech-3.3m/tokens.txt" ] || { echo "dev.sh: no spotter model; run 'dev.sh kws' first" >&2; exit 1; }
  if ${DOCKER} inspect "${LISTENER}" >/dev/null 2>&1; then
    echo "dev.sh: ${LISTENER} is already running; 'dev.sh down' first" >&2; exit 1
  fi
  # A reboot brings the Jabra back at 55% (-12 dB), too quiet to hear the robot over a room (found
  # 2026-10-27): set its playback volume each start, JABRA_VOLUME (default 100%).
  local card
  card=$(awk '/Jabra/ {print $1; exit}' /proc/asound/cards 2>/dev/null)
  if [ -z "${card}" ]; then echo "dev.sh: no Jabra in /proc/asound/cards" >&2; exit 1; fi
  amixer -q -c "${card}" sset PCM "${JABRA_VOLUME:-100%}" unmute \
    || echo "dev.sh: could not set the Jabra's volume (card ${card}); check it by ear" >&2
  [ "${TTS_DEVICE:-cuda}" = cuda ] && tts=(--runtime nvidia -e VOICE_TTS_DEVICE=cuda)
  if [ "${VOICE:-butler}" = butler ]; then      # VOICE=heart: the voice service's American voice instead
    [ -d "${DATA}/models/butler" ] || { echo "dev.sh: no butler voice; run 'dev.sh butler' first (or VOICE=heart)" >&2; exit 1; }
    show="${show} --tts butler"
  fi
  ${DOCKER} run ${DETACH} --rm --name "${LISTENER}" --network host \
    --device /dev/snd --group-add "${gid}" "${tts[@]}" \
    -e HOME=/data/home -w /data/home -e PYTHONPATH=/work/software/architect \
    -e OPENDUBS_VOICE_MODELS=/models -e HF_HUB_OFFLINE=1 -e HF_HOME=/tmp/hf \
    -e HF_HUB_CACHE=/models/hf/hub -e HUGGINGFACE_HUB_CACHE=/models/hf/hub -e TRANSFORMERS_CACHE=/models/hf/hub \
    -v "${DATA}:/data" -v "${REPO}:/work:ro" -v "${MODELS}:/models:ro" \
    "${IMAGE}" bash -c "python3 -u -m opendubs_architect.listener --models /models --kws-models /data/models ${show} \
      2>&1 | tee /data/listener.log"
  if [ -n "${SHOW:-}" ]; then echo "started ${LISTENER}: the Jabra; trick or treat plays ${SHOW} (start ${START:-deadman})"
  else echo "started ${LISTENER}: the Jabra, say on 127.0.0.1:8099; start the runner with LISTEN=1"; fi
  echo "what it hears, live: ssh -L 8099:127.0.0.1:8099 <you>@opendubs2, then http://localhost:8099/"
}

install_units() {  # the boot units: deploy/*.service with this checkout, DATA, SHOW and the user filled in
  [ -n "${SHOW:-}" ] || { echo "dev.sh: SHOW=<the candy routine> is needed" >&2; exit 1; }
  local u
  for u in "${UNITS[@]}"; do
    sed -e "s|@REPO@|${REPO}|g" -e "s|@DATA@|${DATA}|g" -e "s|@SHOW@|${SHOW}|g" -e "s|@USER@|$(id -un)|g" \
      -e "s|@PROGRAM@|${PROGRAM:-software/architect/examples/bucket_offer.py}|g" \
      "${REPO}/software/architect/deploy/${u}.service" | sudo -n tee "/etc/systemd/system/${u}.service" >/dev/null
  done
  sudo -n systemctl daemon-reload
  for u in "${UNITS[@]}"; do   # a hand-started container of the same name would make the unit refuse
    ${DOCKER} stop -t 5 "${u}" >/dev/null 2>&1 || true
    sudo -n systemctl enable --now "${u}.service"
  done
  echo "installed and started: ${UNITS[*]} (from ${REPO}; the coordinator starts PAUSED)"
}

uninstall_units() {
  local u
  for u in "${UNITS[@]}"; do
    sudo -n systemctl disable --now "${u}.service" 2>/dev/null || true
    sudo -n rm -f "/etc/systemd/system/${u}.service"
  done
  sudo -n systemctl daemon-reload
  echo "removed: ${UNITS[*]}"
}

gafro_sim() {  # $1: start (default) or stop. Runs on the host: the gafro venv has MuJoCo; nothing reaches the robot's graph
  local dir="${DATA}/gafro-sim" domain="${GAFRO_DOMAIN:-78}" bundle="${GAFRO_BUNDLE:-/opt/opendubs/arm}"
  local sock="${dir}/arm-right.sock" service="${bundle}/software/openarm/gafro/arm_service.py"
  if [ "${1:-start}" = stop ]; then        # only what this started (its pids), never another session's
    [ -f "${dir}/pids" ] && kill $(cat "${dir}/pids") 2>/dev/null || true
    rm -f "${sock}" "${dir}/pids"; echo "stopped the simulated arm"; return
  fi
  [ "${domain}" != 0 ] || { echo "dev.sh: GAFRO_DOMAIN must not be 0, the robot's own" >&2; exit 1; }
  [ -f "${service}" ] || { echo "dev.sh: no arm service at ${service} (GAFRO_BUNDLE)" >&2; exit 1; }
  [ -S "${sock}" ] && { echo "dev.sh: a simulated arm already has ${sock}; dev.sh gafro-sim stop" >&2; exit 1; }
  mkdir -p "${dir}"
  # The robot's DDS profile, as the runner's container has it, on the simulation's own domain
  ( set +u; source /opt/gafro/setup.bash; source /opt/opendubs/ws/install/setup.bash   # ArmCommand: /right_arm/command
    set -a; . /etc/opendubs/ros-dds.env; set +a; export ROS_DOMAIN_ID="${domain}"
    model="${bundle}/description/openarm/gafro/openarm_bimanual.xml" light="${dir}/openarm_bimanual_weightless.xml"
    # MuJoCo's position servo has no gravity feed-forward: the model made weightless, as grasp_to_sim.sh and the
    # service's own simulation test do
    python3 - "${model}" "${light}" <<'PY'
import os, sys, mujoco
spec = mujoco.MjSpec.from_file(sys.argv[1])
for body in spec.bodies:
    if body.name != "world":
        body.gravcomp = 1.0
spec.meshdir = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), spec.meshdir))
open(sys.argv[2], "w").write(spec.to_xml())
PY
    python3 -u "${service}" run --side right --backend mujoco --model "${light}" --socket "${sock}" > "${dir}/service.log" 2>&1 &
    echo $! > "${dir}/pids"
    merge="${bundle}/software/openarm/gafro/joint_states_merge.py"   # the service publishes /right/joint_states
    if [ -f "${merge}" ]; then python3 -u "${merge}" --sides right > "${dir}/merge.log" 2>&1 & echo $! >> "${dir}/pids"; fi
    for _ in $(seq 60); do [ -S "${sock}" ] && break; sleep 0.5; done
    [ -S "${sock}" ] || { echo "dev.sh: the simulated arm service did not start; ${dir}/service.log" >&2; tail -5 "${dir}/service.log" >&2; exit 1; }
    python3 "${service}" ctl --side right --socket "${sock}" activate --allow-large
    for _ in $(seq 40); do python3 "${service}" ctl --side right --socket "${sock}" status | grep -q "activate done" && break; sleep 0.5; done
    python3 "${service}" ctl --side right --socket "${sock}" status | head -2 )
  echo "simulated right arm at ready on ROS domain ${domain}; logs in ${dir}. Next: SIM=1 GAFRO_DOMAIN=${domain} dev.sh gafro"
}

case "${1:-}" in
  build)
    ${DOCKER} build -f "${REPO}/software/architect/image/Dockerfile" -t "${IMAGE}" \
      --build-arg BASE_IMAGE="${BASE_IMAGE}" --build-context voice="${REPO}/software/voice" \
      --build-context arms="${REPO}/software/openarm/interfaces" "${REPO}/software/architect"
    ;;
  kws)
    mkdir -p "${DATA}/models"
    ${DOCKER} run --rm -v "${DATA}:/data" -v "${REPO}:/work:ro" -e PYTHONPATH=/work/software/architect \
      "${IMAGE}" python3 -m opendubs_architect.kws fetch /data/models
    ;;
  butler)
    mkdir -p "${DATA}/models"
    ${DOCKER} run --rm -v "${DATA}:/data" -v "${REPO}:/work:ro" -e PYTHONPATH=/work/software/architect \
      "${IMAGE}" python3 -m opendubs_architect.butler fetch /data/models
    ;;
  listen)
    listen
    ;;
  camera)
    camera
    ;;
  day)
    day
    ;;
  install)
    install_units
    ;;
  uninstall)
    uninstall_units
    ;;
  status)
    systemctl --no-pager --lines=0 status "${UNITS[@]/%/.service}" 2>/dev/null | grep -E "^. |Active:" || true
    ;;
  live)
    [ -n "${PROGRAM:-}" ] || echo "dev.sh: PROGRAM not set; the fixed program is examples/arms_wave.py" >&2
    run live --agent program --program "/work/${PROGRAM:-software/architect/examples/arms_wave.py}"
    arms
    ;;
  gafro-sim)
    gafro_sim "${2:-start}"
    ;;
  gafro)
    # tobi's arm service must already be up, supervised and active for the right arm (software/openarm/gafro).
    # SIM=1: a SIMULATED arm service on GAFRO_DOMAIN (not 0): the deadman counts as held, and the head and
    # the gripper, which a simulation does not have, are logged instead of moved.
    if [ "${SIM:-0}" = 1 ]; then
      [ "${GAFRO_DOMAIN:-0}" != 0 ] || { echo "dev.sh: SIM=1 needs GAFRO_DOMAIN, the simulation's own ROS domain (not 0)" >&2; exit 1; }
      sim="--sim-deadman --sim-head --sim-gripper"
    else
      sim=""
    fi
    # TREAT_DOMAIN=<n>: the goals go through Archit's trick-or-treat services on that domain (treat_calls.py)
    [ -n "${TREAT_DOMAIN:-}" ] && sim="${sim} --treat-domain ${TREAT_DOMAIN}"
    run gafro --agent program --program "/work/${PROGRAM:-software/architect/examples/candy_gafro.py}" ${sim}
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
    ${DOCKER} stop -t 5 "${CAMERA_NAME}" >/dev/null 2>&1 && echo "stopped ${CAMERA_NAME}"
    ${DOCKER} stop -t 5 "${DAY_NAME}" >/dev/null 2>&1 && echo "stopped ${DAY_NAME}"
    sudo -n chown -R "$(id -u):$(id -g)" "${DATA}" 2>/dev/null || true
    ;;
  *)
    sed -n '2,71p' "$0"; exit 1
    ;;
esac
