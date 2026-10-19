#!/usr/bin/env bash
# OpenDubs full deploy — run on the DEV machine. rsyncs the repo subsystems to
# /opt/opendubs on the Thor (preserving exec bits — the repo's *.sh are +x, so
# `rsync -a` carries them) and runs provision-all.sh. Re-run any time to push
# updates. See docs/portability-audit.md.
#
# Usage:  ./deploy-all.sh [ssh-host]      (default host: thor)
set -euo pipefail
HOST="${1:-thor}"
REPO="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/opendubs
EXC=(--exclude '__pycache__' --exclude '*.pyc' --exclude '.pio' --exclude '.git' --exclude '.venv')

echo "== deploying repo -> $HOST:$DEST =="
ssh -t "$HOST" "sudo mkdir -p $DEST && sudo chown -R \$(id -un):\$(id -gn) $DEST"

# The repo is grouped (firmware/ software/) but the Thor's /opt/opendubs layout stays FLAT — every
# systemd unit + provision-all.sh references /opt/opendubs/<sub>/... — so map grouped source -> flat
# dest. NOTE: path mapping authored offline (Thor down); verify on the first provision of the 2nd bot.
#
# PER-ROBOT FILES ARE NEVER OVERWRITTEN. Drive calibration lives in rc_serial_probe/src/main.cpp on
# each robot, and the repo's copy is a worked example, not any robot's: an unguarded `--delete` here
# followed by an OTA flashes the wrong wheel signs (notes/per-robot-calibration.md). The list of such
# files is tools/deploy-audit.py's PER_ROBOT entries, so the audit and the deploy cannot disagree.
deploy_sub() {  # $1=repo path  $2=flat dest
  local rules line protect=()
  rules=$(python3 "$REPO/tools/deploy-audit.py" --rsync-protect "$1") || exit 1
  # Into an array, one rule per element: unquoted, `*.bak*` would be glob-expanded by the shell.
  # (A read loop, not mapfile, so macOS's bash 3.2 runs it.)
  while IFS= read -r line; do [ -n "$line" ] && protect+=("$line"); done <<< "$rules"
  rsync -az --delete "${EXC[@]}" "${protect[@]}" "$REPO/$1/" "$HOST:$DEST/$2/"
}
# The dashboard deploys through its own script: it builds the offline docs, copies the arm view's
# model as real files (a plain rsync would carry a symlink that dangles in this flat layout and
# delete docs-site/), and ships the show runner. Provisioning runs once, below, via provision-all.sh.
OPENDUBS_SKIP_PROVISION=1 "$REPO/software/kiosk-dashboard/deploy/deploy.sh" "$HOST"
deploy_sub software/ros             ros
deploy_sub software/stack           stack     # version pins + the AirPlay receiver; provision with stack/airplay/provision-airplay.sh
deploy_sub software/voice           voice     # the voice service's scripts; built and run below, on robots that opt in
deploy_sub software/openarm         openarm   # copied only; provision with openarm/provision-openarm.sh
deploy_sub firmware/rc_serial_probe rc_serial_probe
rsync -az "$REPO/provision-all.sh" "$HOST:$DEST/provision-all.sh"

# The tablet's kiosk app, when one has been built: signed ON THE ROBOT with the project key and served
# at the dashboard's /api/kiosk/latest (software/stack/kiosk-signing). The unsigned APK and its
# kiosk-version.json come from the Mac's build, which is not in git, so OPENDUBS_KIOSK_APK can point
# at a build in another checkout. Absent: skipped quietly; not every deploy changes the app.
KIOSK_APK="${OPENDUBS_KIOSK_APK:-$REPO/software/tablet-kiosk/app/build/outputs/apk/release/app-release-unsigned.apk}"
KIOSK_VER="$(dirname "$KIOSK_APK")/kiosk-version.json"
# The voice service and the local language model, on a robot whose robot.env sets
# OPENDUBS_VOICE_DEPLOY=1 (software/stack/voice/provision-voice.sh). The image is tagged by the trees it
# is built from, the voice service and the head protocol, so an unrelated commit never rebuilds it;
# the build inputs are sent only when the robot has no image with that tag yet. Both come from this
# checkout's HEAD, which is what a deploy from a clean main checkout carries anyway.
deploy_voice() {
  ssh "$HOST" "grep -qs '^OPENDUBS_VOICE_DEPLOY=1' /etc/opendubs/robot.env" || return 0
  local tag tmp=""
  tag="opendubs-voice:$(git -C "$REPO" rev-parse --short=12 HEAD:software/voice)-$(git -C "$REPO" rev-parse --short=8 HEAD:software/head-protocol/head_protocol.py)"
  if ! ssh "$HOST" "sudo docker image inspect $tag >/dev/null 2>&1"; then
    tmp=$(ssh "$HOST" mktemp -d)
    git -C "$REPO" archive HEAD software/voice software/head-protocol/head_protocol.py | ssh "$HOST" "tar -x -C $tmp"
  fi
  echo "== voice on $HOST ($tag) =="
  ssh -t "$HOST" "sudo $DEST/stack/voice/provision-voice.sh $tag $tmp" \
    || echo "!! voice provisioning failed (see above); the rest of the deploy is unaffected"
  [ -z "$tmp" ] || ssh "$HOST" rm -rf "$tmp"
}

deploy_kiosk() {
  [ -f "$KIOSK_APK" ] && [ -f "$KIOSK_VER" ] || return 0
  echo "== signing and publishing the kiosk app on $HOST =="
  local tmp; tmp=$(ssh "$HOST" mktemp -d)
  rsync -az "$KIOSK_APK" "$KIOSK_VER" "$HOST:$tmp/"
  ssh -t "$HOST" "sudo $DEST/stack/kiosk-signing/sign-kiosk.sh $tmp/$(basename "$KIOSK_APK") $tmp/kiosk-version.json" \
    || echo "!! the kiosk app was NOT published (see above); the rest of the deploy is unaffected"
  ssh "$HOST" rm -rf "$tmp"
}

echo "== provisioning $HOST (idempotent) =="
ssh -t "$HOST" "sudo bash $DEST/provision-all.sh"
deploy_kiosk
deploy_voice

# Post-deploy health readout — every opendubs unit, including the StamPLC->ROS bridge.
SERVICES="opendubs-dash opendubs-rplidar opendubs-rosbridge opendubs-foxglove opendubs-scan-watchdog opendubs-stamplc-bridge opendubs-camera opendubs-yolo-tracker opendubs-drive-bridge opendubs-tablet-camera-demand"
echo "== service status on $HOST =="
ssh "$HOST" "for s in $SERVICES; do printf '  %-28s %s\n' \"\$s\" \"\$(systemctl is-active \$s 2>/dev/null)\"; done"

echo "== deployed. Reboot the Thor to (re)launch kiosk + ROS:  ssh $HOST sudo reboot =="
