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
# A persistent system journal, so `journalctl -b -1` shows the previous boot after a hang or a
# power cut (software/stack/journald/). Restarting journald keeps the current boot's entries.
if ! cmp -s "$ROOT/stack/journald/50-opendubs-persistent.conf" /etc/systemd/journald.conf.d/50-opendubs-persistent.conf; then
  echo "== persistent journal =="
  install -d -m 0755 /etc/systemd/journald.conf.d
  install -m 0644 "$ROOT/stack/journald/50-opendubs-persistent.conf" /etc/systemd/journald.conf.d/
  systemctl restart systemd-journald
  journalctl --flush
fi
# Host leftovers this stack no longer uses, removed so every robot matches the repo (CLAUDE.md rule 5).
# Each is a no-op on a robot that never had it. Retired 2026-10-21, on the maintainers' word:
#   opendubs-telemetry-log  the 8Servo-wedge UDP recorder (its script, /opt/opendubs/telemetry-log.py,
#                           was never in the repo); it crash-looped once that work ended
#   opendubs-xorg-fix       re-installed and locked a forced-HDMI xorg.conf for the retired kiosk panel
#                           (the xorg.conf it left is not touched here)
#   opendubs-mavros         the retired Pixhawk link
#   x11vnc                  the retired kiosk panel's remote-desktop kit
#   the cups snap           nothing prints from the robot
#   iperf3's server         disabled only; the package stays for a manual network test
RETIRED_UNITS="opendubs-telemetry-log opendubs-xorg-fix opendubs-mavros"
for svc in $RETIRED_UNITS; do
  if [ -e "/etc/systemd/system/$svc.service" ]; then
    echo "-- retiring $svc"
    systemctl disable --now "$svc" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$svc.service"
    rm -rf "/etc/systemd/system/$svc.service.d"
  fi
done
systemctl daemon-reload
rm -f "$ROOT/telemetry-log.py"
if dpkg -s x11vnc >/dev/null 2>&1; then
  echo "-- removing x11vnc"
  DEBIAN_FRONTEND=noninteractive apt-get remove -y -qq x11vnc >/dev/null || echo "   !! could not remove x11vnc"
fi
if command -v snap >/dev/null && snap list cups >/dev/null 2>&1; then
  echo "-- removing the cups snap"
  snap remove --purge cups >/dev/null || echo "   !! could not remove the cups snap"
fi
if systemctl is-enabled --quiet iperf3 2>/dev/null; then
  echo "-- disabling the iperf3 server (the package stays)"
  systemctl disable --now iperf3 >/dev/null 2>&1 || true
fi

bash "$ROOT/kiosk-dashboard/deploy/provision.sh"
bash "$ROOT/ros/provision-ros.sh"
bash "$ROOT/rc_serial_probe/provision-firmware.sh"

# The robot network monitor (the network lane's tools/network). Installed and enabled on every robot;
# its unit has ConditionPathExists=/etc/opendubs/network.env, so it starts only once that file (the
# read-only router account, root 0600) has been created at the robot, and a start before then is a
# quiet no-op. The restart picks up a new netmon.py after a deploy.
if [ -f "$ROOT/network/opendubs-netmon.service" ]; then
  echo "== OpenDubs network monitor =="
  install -m0644 "$ROOT/network/opendubs-netmon.service" /etc/systemd/system/opendubs-netmon.service
  systemctl daemon-reload
  systemctl enable opendubs-netmon >/dev/null
  systemctl restart opendubs-netmon
  if [ -e /etc/opendubs/network.env ]; then
    echo "   opendubs-netmon: $(systemctl is-active opendubs-netmon)"
  else
    echo "   opendubs-netmon: idle until /etc/opendubs/network.env exists (tools/network/README.md)"
  fi
fi
echo "############ provision-all done ############"
echo "Reboot to bring up the kiosk + ROS services:  sudo reboot"
