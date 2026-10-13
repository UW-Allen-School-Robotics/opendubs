#!/bin/bash
# run-llm.sh — the local brain: an OpenAI-compatible model server (vLLM) in its own container,
# the opendubs-llm container of the voice note (§3.5). Plan: notes/workplan-voice.md 1.2 and 2.x.
#
#   run-llm.sh MODEL_ID [--replace] [--robot]
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
# Listens on 127.0.0.1:8000 only. Model weights are cached on the host at
# /var/lib/opendubs/models, never in the repo or the image.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
MANIFEST=${OPENDUBS_VERSIONS:-$ROOT/software/stack/versions.env}
die() { echo "run-llm.sh: $*" >&2; exit 1; }
NAME=opendubs-llm
CACHE=/var/lib/opendubs/models
PORT=${VOICE_LLM_PORT:-8000}

MODEL=${1:?usage: run-llm.sh MODEL_ID [--replace] [--robot]}; shift
REPLACE=0; ROBOT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --replace) REPLACE=1 ;;
    --robot)   ROBOT=1 ;;
    *)         die "unknown option $1" ;;
  esac
  shift
done

if [ -e /etc/opendubs/robot.env ] && [ "$ROBOT" != 1 ]; then
  die "this host is a robot (/etc/opendubs/robot.env). The local model runs here only after gates L-1..L-3 pass on the non-robot Thor; then pass --robot"
fi

IMAGE=$(sed -n 's/^VOICE_LLM_IMAGE=//p' "$MANIFEST" 2>/dev/null | tail -1)
[ -n "$IMAGE" ] || die "VOICE_LLM_IMAGE is not set in $MANIFEST (requested from the Thor stack lane)"
[[ "$IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || die "VOICE_LLM_IMAGE is not pinned by digest: $IMAGE"

SHARE=${VOICE_LLM_GPU_SHARE:-}
[[ "$SHARE" =~ ^0\.[0-9]+$ ]] || die "set VOICE_LLM_GPU_SHARE (a fraction like 0.20); there is deliberately no default"

if ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$PORT\$"; then
  die "port $PORT is already in use; not sharing it (set VOICE_LLM_PORT)"
fi

if docker inspect "$NAME" >/dev/null 2>&1; then
  [ "$REPLACE" = 1 ] || die "a '$NAME' container already exists; pass --replace to remove it"
  docker rm -f "$NAME" >/dev/null
fi

mkdir -p "$CACHE"
docker run -d --name "$NAME" \
  --runtime nvidia --network host --ipc host \
  --restart unless-stopped \
  -v "$CACHE:/root/.cache/huggingface" \
  --log-opt max-size=10m --log-opt max-file=3 \
  "$IMAGE" \
  vllm serve "$MODEL" --host 127.0.0.1 --port "$PORT" \
    --gpu-memory-utilization "$SHARE" --max-model-len 8192 \
    --enable-auto-tool-choice --tool-call-parser hermes
echo "run-llm.sh: $NAME serving $MODEL on 127.0.0.1:$PORT at a $SHARE GPU memory share"
