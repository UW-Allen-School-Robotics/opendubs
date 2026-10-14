#!/bin/bash
# build.sh — build the opendubs-voice image from pinned inputs. Plan: notes/workplan-voice.md 1.6.
#
#   build.sh          the CPU image, FROM ROS_BASE_IMAGE: Parakeet and Kokoro on the CPU, CPU
#                     PyTorch; the local language model runs in its own container (run-llm.sh)
#   build.sh --gpu    FROM ISAAC_ROS_BASE_IMAGE, PyTorch from that base, if gate L-4 needs the GPU.
#                     Refuses until the Thor stack lane has pinned that base.
#
# Either way the image's last step is check_no_gpl.py, and it contains no model weights.
#
# REFUSES when the base in software/stack/versions.env is empty or tag-only (no @sha256:). There is
# no fallback to :latest: an image that cannot be rebuilt identically is not deployable.
# Builds the COMMITTED tree only (git archive of HEAD), so an uncommitted edit cannot reach an
# image, and tags the image with the commit it came from.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
MANIFEST=${OPENDUBS_VERSIONS:-$ROOT/software/stack/versions.env}
die() { echo "build.sh: $*" >&2; exit 1; }

KEY=ROS_BASE_IMAGE; TORCH=cpu
case "${1:-}" in
  "")    ;;
  --gpu) KEY=ISAAC_ROS_BASE_IMAGE; TORCH=base ;;
  *)     die "usage: build.sh [--gpu]" ;;
esac

[ -f "$MANIFEST" ] || die "no version manifest at $MANIFEST"
BASE=$(sed -n "s/^$KEY=//p" "$MANIFEST" | tail -1)
[ -n "$BASE" ] || die "$KEY is empty in $MANIFEST (the Thor stack lane pins it)"
[[ "$BASE" =~ @sha256:[0-9a-f]{64}$ ]] || die "$KEY is not pinned by digest: $BASE"

command -v docker >/dev/null || die "docker is not installed"
SHA=$(git -C "$ROOT" rev-parse --short=12 HEAD)
if [ -n "$(git -C "$ROOT" status --porcelain -- software/voice software/head-protocol)" ]; then
  echo "build.sh: note: uncommitted changes under software/voice or software/head-protocol are NOT in this image" >&2
fi

CTX=$(mktemp -d)
trap 'rm -rf "$CTX"' EXIT
git -C "$ROOT" archive HEAD software/voice software/head-protocol/head_protocol.py | tar -x -C "$CTX"

TAG=opendubs-voice:$SHA$([ "$KEY" = ISAAC_ROS_BASE_IMAGE ] && echo -gpu || true)
docker build --build-arg "BASE_IMAGE=$BASE" --build-arg "TORCH=$TORCH" -f "$CTX/software/voice/image/Dockerfile.voice" -t "$TAG" "$CTX"
echo "$TAG"
