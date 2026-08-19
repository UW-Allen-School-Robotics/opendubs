#!/usr/bin/env bash
# OpenDubs full host provisioning — run ON the Thor (needs sudo). Idempotent.
# Chains the subsystem provisioners: kiosk → ROS → firmware. Assumes the repo is
# deployed under /opt/opendubs (see deploy-all.sh) and the Thor is flashed with
# the pinned base (JetPack 7 / L4T R38.4 / Ubuntu 24.04 — see
# docs/portability-audit.md). Per-unit identity (hostname, secrets) is separate.
#
# Usage:  sudo /opt/opendubs/provision-all.sh
set -euo pipefail
ROOT=/opt/opendubs

echo "############ OpenDubs provision-all ############"
bash "$ROOT/kiosk-dashboard/deploy/provision.sh"
bash "$ROOT/ros/provision-ros.sh"
bash "$ROOT/rc_serial_probe/provision-firmware.sh"
echo "############ provision-all done ############"
echo "Reboot to bring up the kiosk + ROS services:  sudo reboot"
