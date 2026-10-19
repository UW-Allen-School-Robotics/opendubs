#!/bin/bash
# build.sh — build the opendubs-voice image from pinned inputs. Plan: notes/workplan-voice.md 1.6.
#
#   build.sh          the GPU variant (default): FROM VOICE_LLM_IMAGE, the pinned vLLM image, whose
#                     CUDA PyTorch runs Kokoro on the Thor's GPU. Thor only (sbsa, CUDA 13)
#   build.sh --cpu    FROM ROS_BASE_IMAGE with CPU PyTorch; runs on any JetPack line or Orin, but
#                     Kokoro on the CPU misses gate L-4 (1 to 2 s a sentence)
#   build.sh --context DIR --tag opendubs-voice:NAME
#                     build from a prepared directory with the repository's layout (the deploy, on
#                     a robot with no .git). NAME must be the same tag a checkout gets (below)
#   --dry-run         print the docker build command and stop
#
# THE TAG names exactly what the image is built from, so a change to any input, and only that,
# rebuilds it:
#   opendubs-voice:<software/voice tree, 12>-<head_protocol.py blob, 8>-<base digest, 8>
#     voice tree   git rev-parse --short=12 HEAD:software/voice
#     protocol     git rev-parse --short=8 HEAD:software/head-protocol/head_protocol.py
#     base         the first 8 hex of the base image's sha256 digest: VOICE_LLM_IMAGE by default,
#                  ROS_BASE_IMAGE with --cpu (so the two variants never share a tag)
#
# NEVER PUSH THE IMAGE. The GPU variant contains NVIDIA's CUDA libraries from the vLLM base, under
# NVIDIA's terms: it is built on each robot and never pushed to a registry, bundled or shipped.
#
# Either way the image's last step is check_no_gpl.py, and it contains no model weights.
#
# REFUSES when the base in software/stack/versions.env is empty or tag-only (no @sha256:). There is
# no fallback to :latest: an image that cannot be rebuilt identically is not deployable.
# From a checkout it builds the COMMITTED tree only (git archive of HEAD), so an uncommitted edit
# cannot reach an image.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
MANIFEST=${OPENDUBS_VERSIONS:-$ROOT/software/stack/versions.env}
die() { echo "build.sh: $*" >&2; exit 1; }

KEY=VOICE_LLM_IMAGE; VARIANT=gpu; TTS_DEVICE=cuda; CONTEXT=""; TAG=""; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --cpu)     KEY=ROS_BASE_IMAGE; VARIANT=cpu; TTS_DEVICE=cpu ;;
    --context) CONTEXT=${2:?--context needs a directory}; shift ;;
    --tag)     TAG=${2:?--tag needs a name}; shift ;;
    --dry-run) DRY=1 ;;
    *)         die "usage: build.sh [--cpu] [--context DIR --tag opendubs-voice:NAME] [--dry-run]" ;;
  esac
  shift
done

[ -f "$MANIFEST" ] || die "no version manifest at $MANIFEST"
BASE=$(sed -n "s/^$KEY=//p" "$MANIFEST" | tail -1)
[ -n "$BASE" ] || die "$KEY is empty in $MANIFEST (the Thor stack lane pins it)"
[[ "$BASE" =~ @sha256:[0-9a-f]{64}$ ]] || die "$KEY is not pinned by digest: $BASE"

if [ -n "$CONTEXT" ]; then
  # A prepared context (the deploy, on a robot with no .git): it must have the repository's layout,
  # and the caller names the tag, which should come from voice_tag() on the machine that has git.
  [ -n "$TAG" ] || die "--context needs --tag"
  for f in software/voice/image/Dockerfile.voice software/voice/server.py software/head-protocol/head_protocol.py; do
    [ -f "$CONTEXT/$f" ] || die "--context $CONTEXT has no $f (it needs the repository's layout)"
  done
  CTX=$CONTEXT
else
  [ -z "$TAG" ] || die "--tag is only for --context; from a checkout the tag is the inputs' tree hashes"
  # The tag names exactly what the image is built from: the voice tree and the protocol file it
  # copies. An unrelated commit leaves both hashes, and so the image, unchanged.
  VOICE=$(git -C "$ROOT" rev-parse --short=12 HEAD:software/voice)
  PROTO=$(git -C "$ROOT" rev-parse --short=8 HEAD:software/head-protocol/head_protocol.py)
  BASE8=${BASE##*@sha256:}; BASE8=${BASE8:0:8}
  TAG=opendubs-voice:$VOICE-$PROTO-$BASE8
  if [ -n "$(git -C "$ROOT" status --porcelain -- software/voice software/head-protocol)" ]; then
    echo "build.sh: note: uncommitted changes under software/voice or software/head-protocol are NOT in this image" >&2
  fi
  CTX=$(mktemp -d)
  trap 'rm -rf "$CTX"' EXIT
  git -C "$ROOT" archive HEAD software/voice software/head-protocol/head_protocol.py | tar -x -C "$CTX"
fi
[[ "$TAG" =~ ^opendubs-voice:[A-Za-z0-9_.-]+$ ]] || die "tag must look like opendubs-voice:NAME, not '$TAG'"

CMD=(docker build --build-arg "BASE_IMAGE=$BASE" --build-arg "VARIANT=$VARIANT" --build-arg "TTS_DEVICE=$TTS_DEVICE"
     -f "$CTX/software/voice/image/Dockerfile.voice" -t "$TAG" "$CTX")
if [ "$DRY" = 1 ]; then
  printf '%q ' "${CMD[@]}"; echo
  exit 0
fi
command -v docker >/dev/null || die "docker is not installed"
"${CMD[@]}"
echo "$TAG"
