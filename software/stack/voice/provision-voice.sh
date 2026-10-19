#!/usr/bin/env bash
# Provision the voice service (opendubs-voice) and the local language model (opendubs-llm) on a
# robot that opts in. Run as root on the Thor; deploy-all.sh calls it. Idempotent.
#
#   sudo provision-voice.sh opendubs-voice:<tag> [<build context>]
#
# The tag names the voice image for the voice tree and head_protocol.py that this deploy carries
# (deploy-all.sh computes it). <build context> is a copy of those inputs in the repository's layout,
# sent only when no image with that tag exists here yet.
#
# The voice lane's own scripts do the work (software/voice/image: build.sh, run.sh, run-llm.sh);
# this only decides when to call them:
#   - /etc/opendubs/voice.env (root, 0600) and /var/lib/opendubs/models are created if missing.
#   - The voice image is built only when its tag is missing. A failed build (no internet, say)
#     keeps the current container running.
#   - opendubs-voice is (re)created only when it runs another image. run.sh refuses when the
#     speech weights are missing or do not verify; the deploy carries on without it.
#   - opendubs-llm runs only when voice.env sets VOICE_LLM_PROFILE and VOICE_LLM_GPU_SHARE. It is
#     (re)created only when its labels (profile, share, image) differ from those, so a copy started
#     by hand with the same values is adopted as it is. A copy without labels is recreated once.
#   - Weights are never fetched here: that is a one-time step (software/voice/README.md).
# Restarts are Docker's (--restart unless-stopped, set by the run scripts), not systemd's.
#
# Nothing here prints voice.env, which may hold an API key: only the two LLM keys are read from it.
set -euo pipefail
die() { echo "!! $*" >&2; exit 2; }
[ "$(id -u)" = 0 ] || die "run as root"
TAG="${1:?usage: provision-voice.sh opendubs-voice:<tag> [<build context>]}"
CTX="${2:-}"

VOICE=/opt/opendubs/voice
ENVF=/etc/opendubs/voice.env
MODELS=/var/lib/opendubs/models
# The robot's tree is flat (/opt/opendubs/stack), not the repository's layout the scripts assume.
export OPENDUBS_VERSIONS=/opt/opendubs/stack/versions.env
export OPENDUBS_VOICE_ENV="$ENVF" OPENDUBS_VOICE_MODELS="$MODELS"
[ -x "$VOICE/image/run.sh" ] || die "no $VOICE/image/run.sh; deploy software/voice first"

echo "== OpenDubs voice =="
install -d -m 0755 -o root -g root /var/lib/opendubs
[ -d "$MODELS" ] || install -d -m 0755 -o root -g root "$MODELS"
if [ ! -e "$ENVF" ]; then
  ( umask 077; : > "$ENVF" )
  echo "-- created an empty $ENVF (root, 0600): the defaults apply"
fi
chown root:root "$ENVF"; chmod 0600 "$ENVF"

key() { sed -n "s/^$1=//p" "$ENVF" | tail -1 | tr -d "\"'"; }

# --- the voice service --------------------------------------------------------------------------
if docker image inspect "$TAG" >/dev/null 2>&1; then
  echo "-- image $TAG is already built"
elif [ -n "$CTX" ]; then
  echo "-- building $TAG (the first build takes 20-30 min and needs internet)"
  "$VOICE/image/build.sh" --context "$CTX" --tag "$TAG" \
    || echo "!! the voice image was NOT built (see above); the current container keeps running"
else
  echo "!! no image $TAG and no build context; the current container keeps running"
fi

if docker image inspect "$TAG" >/dev/null 2>&1; then
  running=$(docker inspect -f '{{.Config.Image}}' opendubs-voice 2>/dev/null || true)
  if [ "$running" = "$TAG" ]; then
    echo "-- opendubs-voice already runs $TAG"
  else
    echo "-- starting opendubs-voice from $TAG${running:+ (was $running)}"
    "$VOICE/image/run.sh" "$TAG" --replace \
      || echo "!! opendubs-voice was NOT started (see above)"
  fi
fi

# --- the local language model -------------------------------------------------------------------
PROFILE=$(key VOICE_LLM_PROFILE); SHARE=$(key VOICE_LLM_GPU_SHARE)
if [ -z "$PROFILE" ] || [ -z "$SHARE" ]; then
  echo "-- opendubs-llm: not configured (set VOICE_LLM_PROFILE and VOICE_LLM_GPU_SHARE in $ENVF)"
  docker inspect opendubs-llm >/dev/null 2>&1 \
    && echo "   a container exists and is left as it is: remove it by hand if it should not run"
else
  IMAGE=$(sed -n 's/^VOICE_LLM_IMAGE=//p' "$OPENDUBS_VERSIONS" | tail -1)
  have=$(docker inspect -f '{{index .Config.Labels "opendubs.llm.profile"}}|{{index .Config.Labels "opendubs.llm.share"}}|{{index .Config.Labels "opendubs.llm.image"}}' opendubs-llm 2>/dev/null || true)
  if [ "$have" = "$PROFILE|$SHARE|$IMAGE" ]; then
    echo "-- opendubs-llm already serves $PROFILE at a $SHARE share"
  else
    echo "-- (re)creating opendubs-llm: $PROFILE at a $SHARE share${have:+ (was ${have})}"
    VOICE_LLM_GPU_SHARE="$SHARE" "$VOICE/image/run-llm.sh" "$PROFILE" --robot --replace \
      || echo "!! opendubs-llm was NOT started (see above)"
  fi
fi

echo "== voice: $(docker inspect -f '{{.State.Status}}' opendubs-voice 2>/dev/null || echo absent), llm: $(docker inspect -f '{{.State.Status}}' opendubs-llm 2>/dev/null || echo absent) =="
