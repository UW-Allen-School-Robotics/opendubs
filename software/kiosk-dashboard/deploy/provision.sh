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
# avahi + libnss-mdns so `.local` names resolve on this host. Without them nothing on the robot
# can find the Tab5 or the StamPLC by name and everyone falls back to hardcoded IPs, which is how
# a wrong address gets written down and then believed.
apt-get install -y -qq python3.12-venv python3-dev avahi-daemon libnss-mdns || true

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
# Face tracker. It used to be installed by hand and an older revision of THIS script deleted it
# on every run, so a provision silently removed a working demo and nothing could put it back.
install -m 0644 "$DEPLOY/opendubs-yolo-tracker.service" /etc/systemd/system/opendubs-yolo-tracker.service
install -d -o "$SVC_USER" -g "$SVC_USER" -m 0755 /opt/opendubs/models   # persistent YOLO weights
systemctl daemon-reload
systemctl enable opendubs-dash.service
systemctl enable opendubs-stamplc-hb.service
systemctl enable opendubs-yolo-tracker.service
systemctl restart opendubs-dash.service
systemctl restart opendubs-stamplc-hb.service

# --- 3b) The show runner (the Shows page's routines) --------------------------
# Its own service, localhost only; deploy.sh copies software/choreo alongside this app.
if [ -x "$ROOT/choreo/deploy/provision-runner.sh" ]; then
  "$ROOT/choreo/deploy/provision-runner.sh"
else
  echo "-- show runner not deployed (no $ROOT/choreo) — the Shows page will say so"
fi

# --- 4) Name the StamPLC ----------------------------------------------------
# Best-effort: it reads the address from the dashboard, so it only works once the backend is up
# and hearing the board. Re-runnable at any time.
echo "-- registering opendubs-stamplc.local"
sleep 3
"$DEPLOY/register-stamplc-mdns.sh" || echo "   (skipped — re-run it once the StamPLC is reporting)"

echo
echo "== done. backend: $(systemctl is-active opendubs-dash) on :8080 =="
if [ -f /etc/opendubs/tls/dashboard.crt ]; then
  echo "   and HTTPS on :8443 for the tablet"
else
  echo "   no HTTPS yet: run deploy/make-robot-ca.sh on the robot to give the tablet :8443"
fi
# Which robot this is, on every screen that controls it. Per-robot, so it lives on the robot.
if grep -qs '^OPENDUBS_ROBOT_NAME=' /etc/opendubs/robot.env; then
  echo "   robot name: $(sed -n 's/^OPENDUBS_ROBOT_NAME=//p' /etc/opendubs/robot.env | tr -d '"')"
else
  echo "   robot name: $(hostname -s) (the hostname). To name it, e.g.:"
  echo "       echo 'OPENDUBS_ROBOT_NAME=\"OpenDubs 2\"' | sudo tee -a /etc/opendubs/robot.env && sudo systemctl restart opendubs-dash"
fi
# Where the head is, by role (backend/head.py). Unset = the Tab5 at opendubs-tab5.local, both halves.
face=$(sed -n 's/^OPENDUBS_HEAD_FACE_HOST=//p' /etc/opendubs/robot.env 2>/dev/null | tr -d '"')
neck=$(sed -n 's/^OPENDUBS_HEAD_NECK_HOST=//p' /etc/opendubs/robot.env 2>/dev/null | tr -d '"')
echo "   head: face ${face:-opendubs-tab5.local (default)}, neck ${neck:-same as the face}"
echo "Open it from the tablet; there is no local display to launch."
