#!/usr/bin/env bash
# OpenDubs dashboard — idempotent provisioning, run ON the Thor (needs sudo).
# Sets up: python venv + backend deps, the service account and its sudoers rule,
# the backend service, and the StamPLC Ethernet heartbeat.
#
# The robot has NO local display. The operator's Android tablet browses the
# dashboard over the network, so nothing here installs a browser, a display
# manager, an X session or a touch driver.
#
# Covers the DASHBOARD subsystem only — see ros/provision-ros.sh and
# rc_serial_probe/provision-firmware.sh, orchestrated by provision-all.sh.
#
# Usage:  sudo /opt/opendubs/kiosk-dashboard/deploy/provision.sh
set -euo pipefail

ROOT=/opt/opendubs
APP="$ROOT/kiosk-dashboard"
DEPLOY="$APP/deploy"
VENV="$ROOT/venv"
# Historical name. The backend runs as this account (opendubs-dash.service
# sets User=kiosk); it has not logged in to anything since the panel was retired.
SVC_USER=kiosk

echo "== OpenDubs dashboard provisioning =="

# --- 0) System packages ----------------------------------------------------
apt-get update -qq || true
apt-get install -y -qq python3.12-venv python3-dev || true

# --- 1) Python venv + backend deps -----------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  echo "-- creating venv at $VENV"
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install -r "$APP/backend/requirements.txt"

# --- 2) Service account -----------------------------------------------------
if ! getent passwd "$SVC_USER" >/dev/null; then
  echo "-- creating user $SVC_USER"
  useradd -m -s /bin/bash "$SVC_USER"
  passwd -l "$SVC_USER" >/dev/null   # no password login
fi
# video/render for the RealSense the tracker opens; input is vestigial and
# harmless. Left alone deliberately rather than pruned blind.
usermod -aG video,render,input "$SVC_USER" || true
# Code stays owned by the deploy user; world-readable so the service can read it.
chmod -R a+rX "$ROOT" || true
chmod +x "$DEPLOY"/*.sh
# sudoers: let the service user run poweroff/reboot from the dashboard.
install -m 0440 "$DEPLOY/opendubs-sudoers" /etc/sudoers.d/opendubs-kiosk

# --- 3) Backend service -----------------------------------------------------
echo "-- installing backend service"
install -m 0644 "$DEPLOY/opendubs-dash.service" /etc/systemd/system/opendubs-dash.service
# StamPLC Ethernet heartbeat — broadcasts "HB" so the firmware's RX watchdog can spot a wedged
# link (see stamplc_hb.py). Required for the USB-less (transport=udp) StamPLC link to self-heal.
install -m 0644 "$DEPLOY/opendubs-stamplc-hb.service" /etc/systemd/system/opendubs-stamplc-hb.service
systemctl daemon-reload
systemctl enable opendubs-dash.service
systemctl enable opendubs-stamplc-hb.service
systemctl restart opendubs-dash.service
systemctl restart opendubs-stamplc-hb.service

echo
echo "== done. backend: $(systemctl is-active opendubs-dash) on :8080 =="
echo "Open it from the tablet; there is no local display to launch."
