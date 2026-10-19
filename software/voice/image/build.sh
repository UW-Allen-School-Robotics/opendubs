#!/bin/bash
# build.sh — build the opendubs-voice image from pinned inputs. Plan: notes/workplan-voice.md 1.6.
#
#   build.sh          the CPU image, FROM ROS_BASE_IMAGE: Parakeet and Kokoro on the CPU, CPU
#                     PyTorch; the local language model runs in its own container (run-llm.sh)
#   build.sh --gpu    FROM ISAAC_ROS_BASE_IMAGE, PyTorch from that base, if gate L-4 needs the GPU.
#                     Refuses until the Thor stack lane has pinned that base.
#   build.sh --context DIR --tag opendubs-voice:NAME
#                     build from a prepared directory with the repository's layout (the deploy, on
#                     a robot with no .git). NAME should be the same tree-hash tag a checkout gets:
#                       $(git rev-parse --short=12 HEAD:software/voice)-$(git rev-parse --short=8 HEAD:software/head-protocol/head_protocol.py)
#   --dry-run         print the docker build command and stop
#
# Either way the image's last step is check_no_gpl.py, and it contains no model weights.
#
# REFUSES when the base in software/stack/versions.env is empty or tag-only (no @sha256:). There is
# no fallback to :latest: an image that cannot be rebuilt identically is not deployable.
# From a checkout it builds the COMMITTED tree only (git archive of HEAD), so an uncommitted edit
# cannot reach an image, and tags it with the hashes of its inputs (the software/voice tree and
# head_protocol.py), so an unrelated commit never forces a multi-GB rebuild.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
MANIFEST=${OPENDUBS_VERSIONS:-$ROOT/software/stack/versions.env}
die() { echo "build.sh: $*" >&2; exit 1; }

KEY=ROS_BASE_IMAGE; TORCH=cpu; CONTEXT=""; TAG=""; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --gpu)     KEY=ISAAC_ROS_BASE_IMAGE; TORCH=base ;;
    --context) CONTEXT=${2:?--context needs a directory}; shift ;;
    --tag)     TAG=${2:?--tag needs a name}; shift ;;
    --dry-run) DRY=1 ;;
    *)         die "usage: build.sh [--gpu] [--context DIR --tag opendubs-voice:NAME] [--dry-run]" ;;
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
  TAG=opendubs-voice:$VOICE-$PROTO
  if [ -n "$(git -C "$ROOT" status --porcelain -- software/voice software/head-protocol)" ]; then
    echo "build.sh: note: uncommitted changes under software/voice or software/head-protocol are NOT in this image" >&2
  fi
  CTX=$(mktemp -d)
  trap 'rm -rf "$CTX"' EXIT
  git -C "$ROOT" archive HEAD software/voice software/head-protocol/head_protocol.py | tar -x -C "$CTX"
fi
[ "$KEY" = ISAAC_ROS_BASE_IMAGE ] && TAG=$TAG-gpu
[[ "$TAG" =~ ^opendubs-voice:[A-Za-z0-9_.-]+$ ]] || die "tag must look like opendubs-voice:NAME, not '$TAG'"

CMD=(docker build --build-arg "BASE_IMAGE=$BASE" --build-arg "TORCH=$TORCH"
     -f "$CTX/software/voice/image/Dockerfile.voice" -t "$TAG" "$CTX")
if [ "$DRY" = 1 ]; then
  printf '%q ' "${CMD[@]}"; echo
  exit 0
fi
command -v docker >/dev/null || die "docker is not installed"
"${CMD[@]}"
echo "$TAG"
