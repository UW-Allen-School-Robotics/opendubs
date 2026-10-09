#!/bin/bash
# build.sh — build the openarm image from pinned sources. Design: notes/design/arm-image.md.
#
#   build.sh                     check every pin, assemble the build context, build, print the image
#   build.sh --context-only DIR  assemble the context in DIR and stop (no Docker needed)
#
# REFUSES, before cloning anything, when:
#   - software/stack/versions.env has no ROS_BASE_IMAGE, or it is tag-only (no @sha256:)
#   - pins.env has an empty value
#   - any repos.lock entry has no URL, or a commit that is not a full 40-character hash
# There is no fallback to :latest or to a branch tip: an image that cannot be rebuilt identically
# is what this replaces.
#
# The org's private copy of the teleop bridge is cloned HERE, with this machine's own git
# credentials, so no secret reaches the Docker build or the image history.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
MANIFEST=${OPENDUBS_VERSIONS:-$ROOT/software/stack/versions.env}
LOCK=$HERE/repos.lock
PINS=$HERE/pins.env
die() { echo "build.sh: $*" >&2; exit 1; }

CONTEXT_ONLY=""
if [ "${1:-}" = --context-only ]; then CONTEXT_ONLY=${2:?usage: build.sh --context-only DIR}; fi

# ---- the pins ------------------------------------------------------------------------------
[ -f "$MANIFEST" ] || die "no version manifest at $MANIFEST (the Thor stack's software/stack/versions.env)"
ROS_BASE_IMAGE=$(sed -n 's/^ROS_BASE_IMAGE=//p' "$MANIFEST" | tail -1)
[ -n "$ROS_BASE_IMAGE" ] || die "ROS_BASE_IMAGE is empty in $MANIFEST"
[[ "$ROS_BASE_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || die "ROS_BASE_IMAGE is not pinned by digest: $ROS_BASE_IMAGE"

# shellcheck source=/dev/null
. "$PINS"
for v in OPENARM_CAN_VERSION UV_VERSION; do
  [ -n "${!v:-}" ] || die "$v is empty in pins.env (capture-pins.sh fills it from a robot)"
done

bad=0
while read -r name kind url commit; do
  if [ -z "${url:-}" ]; then echo "build.sh: $name has no URL in repos.lock" >&2; bad=1; continue; fi
  [[ "${commit:-}" =~ ^[0-9a-f]{40}$ ]] || { echo "build.sh: $name's commit is not a full hash: '${commit:-}'" >&2; bad=1; }
done < <(sed -n '/^[a-z]/p' "$LOCK")
[ "$bad" = 0 ] || die "repos.lock is not fully pinned (capture-pins.sh fills it from a robot)"

# ---- the build context ---------------------------------------------------------------------
CTX=${CONTEXT_ONLY:-$(mktemp -d)}
mkdir -p "$CTX"
[ -z "$(ls -A "$CTX")" ] || die "build context $CTX is not empty"

fetch() {  # fetch NAME URL COMMIT DEST
  git clone --quiet --filter=blob:none --no-checkout "$2" "$4"
  git -C "$4" checkout --quiet --detach "$3"
  [ "$(git -C "$4" rev-parse HEAD)" = "$3" ] || die "$1: checked out $(git -C "$4" rev-parse HEAD), not $3"
  echo "  $1 @ ${3:0:12}"
}
echo "== sources (repos.lock)"
while read -r name kind url commit; do
  case "$kind" in
    top)  fetch "$name" "$url" "$commit" "$CTX/src" ;;
  esac
done < <(sed -n '/^[a-z]/p' "$LOCK")
[ -d "$CTX/src" ] || die "repos.lock has no 'top' entry"
while read -r name kind url commit; do
  case "$kind" in
    top)  ;;
    ssik) fetch "$name" "$url" "$commit" "$CTX/ssik" ;;
    *)    rm -rf "${CTX:?}/src/$name"          # upstream tracks siblings as empty gitlink placeholders
          fetch "$name" "$url" "$commit" "$CTX/src/$name" ;;
  esac
done < <(sed -n '/^[a-z]/p' "$LOCK")

# What upstream's setup.sh does to the tree, minus its host-side `uv sync` (the image does that).
echo "== preparing the workspace (setup.sh's steps)"
while read -r name kind _; do
  case "$kind" in uv|asset) touch "$CTX/src/$name/COLCON_IGNORE" ;; esac
done < <(sed -n '/^[a-z]/p' "$LOCK")
find "$CTX/src" -mindepth 2 -maxdepth 2 -name .venv -type d -prune -exec rm -rf {} +
[ -f "$CTX/src/uv.lock" ] || die "the workspace has no uv.lock, so its Python environment cannot be pinned"

cp "$HERE/Dockerfile.openarm" "$HERE/entrypoint.sh" "$HERE/fastdds-udp-only.xml" "$CTX/"
chmod +x "$CTX/entrypoint.sh"

if [ -n "$CONTEXT_ONLY" ]; then
  echo "== context assembled in $CTX (not built)"; exit 0
fi

# ---- the image -----------------------------------------------------------------------------
PIN_HASH=$(cat "$MANIFEST" "$PINS" "$LOCK" "$HERE/Dockerfile.openarm" "$HERE/entrypoint.sh" \
           "$HERE/fastdds-udp-only.xml" | sha256sum | cut -c1-12)
TAG=openarm-ros2:$PIN_HASH
echo "== building $TAG on $ROS_BASE_IMAGE"
docker buildx build --load --network=host \
  -f "$CTX/Dockerfile.openarm" \
  --build-arg ROS_BASE_IMAGE="$ROS_BASE_IMAGE" \
  --build-arg OPENARM_CAN_VERSION="$OPENARM_CAN_VERSION" \
  --build-arg UV_VERSION="$UV_VERSION" \
  --label org.opendubs.pins="$PIN_HASH" \
  --label org.opendubs.ros-base="$ROS_BASE_IMAGE" \
  -t "$TAG" "$CTX"
rm -rf "$CTX"
echo "== built $TAG ($(docker image inspect --format '{{.Id}}' "$TAG"))"
echo "   run it with: $HERE/run.sh $TAG"
