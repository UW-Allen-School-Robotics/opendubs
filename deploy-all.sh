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

# What this deploy carries, said plainly at the start and the end and recorded on the robot in
# $DEST/DEPLOYED (tools/deploy-audit.py prints it). The robot runs what is on main (CLAUDE.md rule 5),
# so a branch or a dirty checkout is called out: rsync ships the WORKING TREE, not the commit.
SRC_SHA=$(git -C "$REPO" rev-parse --short=12 HEAD)
SRC_BRANCH=$(git -C "$REPO" symbolic-ref -q --short HEAD || echo detached)
MAIN_SHA=$(git -C "$REPO" rev-parse --short=12 origin/main 2>/dev/null || echo unknown)
if [ "$SRC_SHA" = "$MAIN_SHA" ]; then
  SOURCE="main @ $SRC_SHA"
elif git -C "$REPO" merge-base --is-ancestor HEAD origin/main 2>/dev/null; then
  SOURCE="an OLDER main commit @ $SRC_SHA (main is $MAIN_SHA)"
else
  SOURCE="branch $SRC_BRANCH @ $SRC_SHA, NOT main (main is $MAIN_SHA)"
fi
[ -z "$(git -C "$REPO" status --porcelain --untracked-files=no)" ] || SOURCE="$SOURCE, WITH UNCOMMITTED CHANGES"

echo "== deploying repo -> $HOST:$DEST =="
echo "== source: $SOURCE =="
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
deploy_sub description/opendubs     description   # opendubs.urdf, read by opendubs-robot-state-publisher
deploy_sub software/stack           stack     # version pins + the AirPlay receiver; provision with stack/airplay/provision-airplay.sh
deploy_sub software/voice           voice     # the voice service's scripts; built and run below, on robots that opt in
deploy_sub software/head-protocol   head-protocol  # the head's protocol and looks.py, read by the dashboard and the show runner
# The network lane's router health monitor and diagnostics (tools/network/README.md); its unit is
# installed by provision-all.sh and stays idle until /etc/opendubs/network.env exists on the robot.
[ -d "$REPO/tools/network" ] && deploy_sub tools/network network
deploy_sub software/openarm         openarm   # copied only; provision with openarm/provision-openarm.sh
# The arm service's bundle, /opt/opendubs/arm: this repository's own layout for what opendubs-arm@.service needs, so that the model (description/openarm/gafro) and the
# clearance twin (sim/) are found by the same relative paths as in a checkout, and no checkout is needed on the robot. software/openarm is the copy above, linked in
# (python reads __file__ without resolving the link, so the four-directories-up rule still lands on /opt/opendubs/arm).
ssh "$HOST" "mkdir -p $DEST/arm/software $DEST/arm/description && ln -sfn $DEST/openarm $DEST/arm/software/openarm"
deploy_sub description/openarm      arm/description/openarm
deploy_sub sim                      arm/sim
deploy_sub firmware/common         common    # shared firmware code; rc_serial_probe/lib/ota_verify links here
# main.cpp used to carry each robot's calibration inline, and deploys skipped it; now it is an
# ordinary deployed file and the values are in the robot's src/robot_config.h. Before the first such
# deploy replaces a robot's old main.cpp (one that still defines the values inline), keep that copy,
# once, so its values can still be moved
# (firmware/rc_serial_probe/tools/make_robot_config.py). The .pre- name keeps it out of deploys and audits.
ssh "$HOST" 'd=/opt/opendubs/rc_serial_probe/src
  if [ -f "$d/main.cpp" ] && grep -q "kDriveInvert\[4\] *=" "$d/main.cpp" \
     && [ ! -f "$d/robot_config.h" ] && [ ! -f "$d/main.cpp.pre-robot-config" ]; then
    cp -p "$d/main.cpp" "$d/main.cpp.pre-robot-config" && echo "-- kept the robot s own main.cpp as $d/main.cpp.pre-robot-config"
  fi'
deploy_sub firmware/rc_serial_probe rc_serial_probe
rsync -az "$REPO/provision-all.sh" "$HOST:$DEST/provision-all.sh"

# The tablet's kiosk app: signed ON THE ROBOT with the project key and served at the dashboard's
# /api/kiosk/latest (software/stack/kiosk-signing). The unsigned APK and its kiosk-version.json come
# from a build on the Mac (software/tablet-kiosk: ./gradlew assembleRelease), which is not in git, so
# OPENDUBS_KIOSK_APK can point at a build in another checkout. The version this checkout's source says
# (app/build.gradle.kts) is compared with what the robot serves: a robot already on it is left alone; a
# robot behind it with no build here is reported LOUDLY (a quiet skip once left the tablet three
# versions behind for days); a build of a different version than the source is refused as stale.
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
  # The third part is the image the voice image is built FROM (its GPU variant: VOICE_LLM_IMAGE), so a
  # bump of that pin rebuilds it too. The same formula as build.sh's from a checkout.
  local base
  base=$(git -C "$REPO" show HEAD:software/stack/versions.env | sed -n 's/^VOICE_LLM_IMAGE=.*@sha256://p' | tail -1 | cut -c1-8)
  [ ${#base} = 8 ] || { echo "!! VOICE_LLM_IMAGE is not pinned by digest in versions.env; voice skipped"; return 0; }
  tag="opendubs-voice:$(git -C "$REPO" rev-parse --short=12 HEAD:software/voice)-$(git -C "$REPO" rev-parse --short=8 HEAD:software/head-protocol/head_protocol.py)-$base"
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
  local want wname served sname have
  want=$(sed -n 's/^ *versionCode *= *\([0-9][0-9]*\).*/\1/p' "$REPO/software/tablet-kiosk/app/build.gradle.kts" | head -1)
  wname=$(sed -n 's/^ *versionName *= *"\([^"]*\)".*/\1/p' "$REPO/software/tablet-kiosk/app/build.gradle.kts" | head -1)
  [ -n "$want" ] || { echo "!! kiosk: no versionCode in software/tablet-kiosk/app/build.gradle.kts; kiosk step skipped"; return 0; }
  read -r served sname < <(ssh "$HOST" "cat /var/lib/opendubs/kiosk/latest.json 2>/dev/null" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin); print(int(d["versionCode"]), d.get("versionName", "?"))
except Exception:
    print(0, "nothing")') || { served=0; sname=nothing; }
  if [ "$served" -ge "$want" ]; then
    echo "== kiosk: $HOST already serves $sname ($served); this checkout is $wname ($want), nothing to publish =="
    return 0
  fi
  if ! { [ -f "$KIOSK_APK" ] && [ -f "$KIOSK_VER" ]; }; then
    echo "!! kiosk NOT published: $HOST serves $sname ($served), this checkout is $wname ($want), and there is"
    echo "!!   no build at $KIOSK_APK. The tablet stays on $sname. Build it"
    echo "!!   (cd software/tablet-kiosk && ./gradlew assembleRelease) or set OPENDUBS_KIOSK_APK, and deploy again."
    return 0
  fi
  have=$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["versionCode"]))' "$KIOSK_VER" 2>/dev/null || echo 0)
  if [ "$have" != "$want" ]; then
    echo "!! kiosk NOT published: the build at $KIOSK_APK is versionCode $have, this checkout is $wname ($want):"
    echo "!!   a stale build. Rebuild it (cd software/tablet-kiosk && ./gradlew assembleRelease) and deploy again."
    return 0
  fi
  echo "== signing and publishing the kiosk app on $HOST: $sname ($served) -> $wname ($want) =="
  local tmp; tmp=$(ssh "$HOST" mktemp -d)
  rsync -az "$KIOSK_APK" "$KIOSK_VER" "$HOST:$tmp/"
  ssh -t "$HOST" "sudo $DEST/stack/kiosk-signing/sign-kiosk.sh $tmp/$(basename "$KIOSK_APK") $tmp/kiosk-version.json" \
    || echo "!! the kiosk app was NOT published (see above); the rest of the deploy is unaffected"
  ssh "$HOST" rm -rf "$tmp"
}

# The stage box's software (software/stage-pi), which the box pulls and installs itself between shows,
# REBOOTING to do so. Its version is the folder's git tree hash, not the deploy's commit, so a deploy that
# does not change the folder offers the same version and the box is left alone; one the robot already
# offers is not republished. publish-bundle.sh (the Tablet lane's) signs the manifest with
# stack/firmware-signing/sign-firmware.sh stage and publishes it to /var/lib/opendubs/stage/.
deploy_stage() {
  git -C "$REPO" cat-file -e HEAD:software/stage-pi/release/publish-bundle.sh 2>/dev/null || return 0
  local ver served tmp
  ver=$(git -C "$REPO" rev-parse --short=12 HEAD:software/stage-pi)
  served=$(ssh "$HOST" "cat /var/lib/opendubs/stage/manifest.json 2>/dev/null" | python3 -c '
import json, sys
try:
    print(json.load(sys.stdin).get("version") or "")
except Exception:
    print("")') || served=""
  if [ "$served" = "$ver" ]; then
    echo "== stage box: $HOST already offers its software $ver, nothing to publish =="
    return 0
  fi
  echo "== publishing the stage box's software on $HOST: ${served:-nothing} -> $ver =="
  tmp=$(ssh "$HOST" mktemp -d)
  git -C "$REPO" archive HEAD software/stage-pi | ssh "$HOST" "tar -x -C $tmp"
  ssh -t "$HOST" "sudo bash $tmp/software/stage-pi/release/publish-bundle.sh $tmp/software/stage-pi $ver" \
    || echo "!! the stage box's software was NOT published (see above); the rest of the deploy is unaffected"
  ssh "$HOST" rm -rf "$tmp"
}

echo "== provisioning $HOST (idempotent) =="
ssh -t "$HOST" "sudo bash $DEST/provision-all.sh"
deploy_kiosk
deploy_stage
deploy_voice

# Post-deploy health readout — every opendubs unit, including the StamPLC->ROS bridge.
SERVICES="opendubs-dash opendubs-rplidar opendubs-rosbridge opendubs-foxglove opendubs-scan-watchdog opendubs-stamplc-bridge opendubs-yolo-tracker opendubs-drive-bridge opendubs-tablet-camera-demand opendubs-netmon"
echo "== service status on $HOST =="
ssh "$HOST" "for s in $SERVICES; do printf '  %-28s %s\n' \"\$s\" \"\$(systemctl is-active \$s 2>/dev/null)\"; done"

printf 'source=%s\nsha=%s\nbranch=%s\nmain=%s\nwhen=%s\nfrom=%s\n' "$SOURCE" "$SRC_SHA" "$SRC_BRANCH" "$MAIN_SHA" \
  "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$(hostname -s)" | ssh "$HOST" "cat > $DEST/DEPLOYED"
echo "== deployed from $SOURCE =="
echo "== deployed. Reboot the Thor to (re)launch kiosk + ROS:  ssh $HOST sudo reboot =="
