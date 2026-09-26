#!/usr/bin/env bash
# OpenDubs kiosk — idempotent provisioning, run ON the Thor (needs sudo).
# Sets up: python venv + deps, backend service, kiosk user + sudoers, gdm
# autologin, GNOME autostart wrappers (dashboard/touch/VNC), chromium snap,
# forced+immutable panel xorg (+ restore oneshot), and touch rotation.
# Covers the KIOSK subsystem only — see ros/provision-ros.sh and
# rc_serial_probe/provision-firmware.sh, orchestrated by provision-all.sh.
#
# Usage:  sudo /opt/opendubs/kiosk-dashboard/deploy/provision.sh
set -euo pipefail

ROOT=/opt/opendubs
APP="$ROOT/kiosk-dashboard"
DEPLOY="$APP/deploy"
VENV="$ROOT/venv"
KIOSK_USER=kiosk

echo "== OpenDubs kiosk provisioning =="

# --- 0) System packages ----------------------------------------------------
# python venv; touch bridge (evdev + Xlib XTEST); fullscreen forcing
# (wmctrl/xdotool); remote view (x11vnc); diagnostics (libinput-tools, pygame);
# xprop for the fullscreen watchdog.
apt-get update -qq || true
apt-get install -y -qq \
  python3.12-venv python3-dev \
  python3-evdev python3-xlib python3-pygame \
  wmctrl xdotool x11vnc libinput-tools x11-utils || true

# Chromium (kiosk browser) is a snap on Ubuntu 24.04.
if ! snap list chromium >/dev/null 2>&1; then
  echo "-- installing chromium snap"; snap install chromium || true
fi

# --- 1) Python venv + backend deps -----------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
  echo "-- creating venv at $VENV"
  python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip >/dev/null
"$VENV/bin/pip" install -r "$APP/backend/requirements.txt"

# --- 2) Kiosk user ----------------------------------------------------------
if ! getent passwd "$KIOSK_USER" >/dev/null; then
  echo "-- creating user $KIOSK_USER"
  useradd -m -s /bin/bash "$KIOSK_USER"
  # no password login; autologin only
  passwd -l "$KIOSK_USER" >/dev/null
fi
usermod -aG video,render,input "$KIOSK_USER" || true
# Code stays owned by the deploy user; world-readable so the kiosk service can
# read it. (The kiosk user writes only to its own HOME for the browser profile.)
chmod -R a+rX "$ROOT" || true
chmod +x "$DEPLOY"/*.sh
# sudoers: let the kiosk user run poweroff/reboot from the dashboard.
install -m 0440 "$DEPLOY/opendubs-sudoers" /etc/sudoers.d/opendubs-kiosk

# --- 3) Panel xorg config + touch rotation ---------------------------------
# Forced-HDMI config for the no-EDID panel. The Jetson reverts xorg.conf late in
# boot → lock it immutable and install a oneshot that re-asserts it before the
# display manager. (See thor-kiosk-display notes.)
echo "-- installing xorg config (forced HDMI, immutable)"
chattr -i /etc/X11/xorg.conf 2>/dev/null || true
install -m 0644 "$DEPLOY/xorg-hdmi.conf" /etc/X11/xorg.conf
install -d /etc/X11/xorg.conf.d
install -m 0644 "$DEPLOY/99-opendubs-touch.conf" /etc/X11/xorg.conf.d/99-opendubs-touch.conf
install -m 0644 "$DEPLOY/opendubs-xorg-fix.service" /etc/systemd/system/opendubs-xorg-fix.service
chattr +i /etc/X11/xorg.conf 2>/dev/null || true
systemctl enable opendubs-xorg-fix.service >/dev/null 2>&1 || true

# --- 4) Kiosk session: stock GNOME (Xorg) + autostart wrappers --------------
# The kiosk runs the stock GNOME Xorg session (gdm autologin, WaylandEnable=false
# below) with our launchers in the kiosk user's GNOME autostart: dashboard
# (Chromium), touch bridge, VNC.
echo "-- installing kiosk autostart wrappers"
KHOME=$(getent passwd "$KIOSK_USER" | cut -d: -f6)
install -d -o "$KIOSK_USER" -g "$KIOSK_USER" "$KHOME/.config/autostart"
for d in opendubs-dashboard opendubs-touch opendubs-vnc; do
  install -m 0644 -o "$KIOSK_USER" -g "$KIOSK_USER" "$DEPLOY/$d.desktop" "$KHOME/.config/autostart/$d.desktop"
done
# Remove any stale custom-Xsession override from earlier provisioning so the
# user defaults to the stock GNOME session (runs on Xorg via WaylandEnable=false).
rm -f /var/lib/AccountsService/users/"$KIOSK_USER"

# --- 5) GDM autologin -------------------------------------------------------
echo "-- configuring gdm autologin for $KIOSK_USER"
GDM=/etc/gdm3/custom.conf
[ -f "$GDM" ] || GDM=/etc/gdm/custom.conf
cp "$GDM" "$GDM.opendubs.bak" 2>/dev/null || true
python3 - "$GDM" "$KIOSK_USER" <<'PY'
import configparser, sys
path, user = sys.argv[1], sys.argv[2]
cp = configparser.ConfigParser(); cp.optionxform = str
cp.read(path)
if 'daemon' not in cp: cp['daemon'] = {}
cp['daemon']['WaylandEnable'] = 'false'
cp['daemon']['AutomaticLoginEnable'] = 'true'
cp['daemon']['AutomaticLogin'] = user
with open(path, 'w') as f: cp.write(f)
print("updated", path)
PY

# --- 6) Backend service -----------------------------------------------------
echo "-- installing backend service"
install -m 0644 "$DEPLOY/opendubs-dash.service" /etc/systemd/system/opendubs-dash.service
# StampPLC Ethernet heartbeat — broadcasts "HB" so the firmware's RX watchdog can spot a wedged
# link (see stamplc_hb.py). Required for the USB-less (transport=udp) StampPLC link to self-heal.
install -m 0644 "$DEPLOY/opendubs-stamplc-hb.service" /etc/systemd/system/opendubs-stamplc-hb.service
systemctl daemon-reload
systemctl enable opendubs-dash.service
systemctl enable opendubs-stamplc-hb.service
systemctl disable --now opendubs-yolo-tracker.service || true
rm -f /etc/systemd/system/opendubs-yolo-tracker.service
systemctl restart opendubs-dash.service
systemctl restart opendubs-stamplc-hb.service

echo
echo "== done. backend: $(systemctl is-active opendubs-dash) =="
echo "Reboot (or restart gdm) to launch the kiosk:  sudo systemctl restart gdm"
