#!/bin/bash
# run-llm.sh — the local brain: vLLM in its own container (opendubs-llm), serving one local model
# from its pinned serving profile. Plan: notes/workplan-voice.md 1.5.
#
#   run-llm.sh PROFILE [--replace] [--robot] [--dry-run]
#   run-llm.sh qwen3-30b-a3b-w4a16          the local brain (V-O3)
#   run-llm.sh qwen3-4b                     its fallback, if gate L-3 fails
#
# The profile (llm_profile.py, from models.lock.json) sets the served name, the tool-call parser,
# the context length and the sequences at once; the voice service reads the same profile
# (VOICE_LLM_PROFILE), so the two cannot disagree.
#
# The weights are served from a LOCAL, pinned copy, never fetched by vLLM: fetch_models.py puts
# them in $MODELS/llm/<profile> (`fetch_models.py --only PROFILE`), this script verifies every file
# against models.lock.json before it starts, and vLLM runs with HF_HUB_OFFLINE=1.
#
# The image is VOICE_LLM_IMAGE in software/stack/versions.env, pinned by digest (a request to the
# Thor stack lane: the key does not exist yet, so this refuses until it does).
#
# GPU memory share is REQUIRED, per robot, as VOICE_LLM_GPU_SHARE (e.g. 0.20 for about 24 GiB of
# the Thor's 122). vLLM's default of 0.9 would take the whole machine, perception included, so
# there is no default here.
#
# REFUSES on a robot (a host with /etc/opendubs/robot.env) unless --robot is passed. The local model
# goes onto a robot only after gates L-1 to L-3 have passed on the non-robot Thor; the working
# robot is never the test bed.
#
# --dry-run prints the docker command and starts nothing; it skips the weights check, docker and the
# port check, so it can run on a desk (the tests use it).
#
# The container carries labels (opendubs.llm.profile, .share, .image), so a deploy can tell whether
# the running copy already matches and leave it alone.
#
# Listens on 127.0.0.1:8000 only.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
VOICE=$(cd "$HERE/.." && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
MANIFEST=${OPENDUBS_VERSIONS:-$ROOT/software/stack/versions.env}
die() { echo "run-llm.sh: $*" >&2; exit 1; }
NAME=opendubs-llm
MODELS=${OPENDUBS_VOICE_MODELS:-/var/lib/opendubs/models}
PORT=${VOICE_LLM_PORT:-8000}

PROFILE=${1:?usage: run-llm.sh PROFILE [--replace] [--robot] [--dry-run]   (profiles: python3 $VOICE/llm_profile.py --list)}; shift
REPLACE=0; ROBOT=0; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --replace) REPLACE=1 ;;
    --robot)   ROBOT=1 ;;
    --dry-run) DRY=1 ;;
    *)         die "unknown option $1" ;;
  esac
  shift
done

if [ -e /etc/opendubs/robot.env ] && [ "$ROBOT" != 1 ]; then
  die "this host is a robot (/etc/opendubs/robot.env). The local model runs here only after gates L-1..L-3 pass on the non-robot Thor; then pass --robot"
fi

# The profile: plain KEY=value lines, every value validated by llm_profile.py as a plain token.
PROFILE_ENV=$(python3 -I "$VOICE/llm_profile.py" --shell "$PROFILE") || die "no such profile '$PROFILE'"
LLM_DEST="" LLM_SERVED_NAME="" LLM_TOOL_PARSER="" LLM_MAX_MODEL_LEN="" LLM_MAX_NUM_SEQS=""
while IFS='=' read -r k v; do
  case "$k" in
    LLM_DEST|LLM_SERVED_NAME|LLM_TOOL_PARSER|LLM_MAX_MODEL_LEN|LLM_MAX_NUM_SEQS) printf -v "$k" '%s' "$v" ;;
  esac
done <<< "$PROFILE_ENV"

IMAGE=$(sed -n 's/^VOICE_LLM_IMAGE=//p' "$MANIFEST" 2>/dev/null | tail -1)
[ -n "$IMAGE" ] || die "VOICE_LLM_IMAGE is not set in $MANIFEST (requested from the Thor stack lane)"
[[ "$IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || die "VOICE_LLM_IMAGE is not pinned by digest: $IMAGE"

SHARE=${VOICE_LLM_GPU_SHARE:-}
[[ "$SHARE" =~ ^0\.[0-9]+$ ]] || die "set VOICE_LLM_GPU_SHARE (a fraction like 0.20); there is deliberately no default"

CMD=(docker run -d --name "$NAME"
  --runtime nvidia --network host --ipc host
  --restart unless-stopped
  -e HF_HUB_OFFLINE=1
  -v "$MODELS/$LLM_DEST:/model:ro"
  --log-opt max-size=10m --log-opt max-file=3
  --label "opendubs.llm.profile=$PROFILE" --label "opendubs.llm.share=$SHARE" --label "opendubs.llm.image=$IMAGE"
  "$IMAGE"
  vllm serve /model --served-model-name "$LLM_SERVED_NAME" --host 127.0.0.1 --port "$PORT"
    --gpu-memory-utilization "$SHARE" --max-model-len "$LLM_MAX_MODEL_LEN"
    --max-num-seqs "$LLM_MAX_NUM_SEQS" --enable-prefix-caching
    --enable-auto-tool-choice --tool-call-parser "$LLM_TOOL_PARSER")
if [ "$DRY" = 1 ]; then
  printf '%q ' "${CMD[@]}"; echo
  exit 0
fi

python3 -I "$VOICE/fetch_models.py" --dest "$MODELS" --only "$PROFILE" --check \
  || die "the weights for $PROFILE are missing or do not match models.lock.json. Fetch them with:
  sudo python3 -I $VOICE/fetch_models.py --dest $MODELS --only $PROFILE"

if docker inspect "$NAME" >/dev/null 2>&1; then
  [ "$REPLACE" = 1 ] || die "a '$NAME' container already exists; pass --replace to remove it"
  docker rm -f "$NAME" >/dev/null
fi

# After any --replace removal: a port still in use now belongs to something else.
if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$PORT\$"; then
  die "port $PORT is already in use; not sharing it (set VOICE_LLM_PORT)"
fi

"${CMD[@]}"
echo "run-llm.sh: $NAME serving $PROFILE as '$LLM_SERVED_NAME' on 127.0.0.1:$PORT at a $SHARE GPU memory share"
echo "run-llm.sh: set VOICE_LLM_PROFILE=$PROFILE for the voice service"
