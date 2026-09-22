#!/usr/bin/env bash
# OpenArm host provisioning — run ON the Thor (needs sudo). Idempotent.
# Sets up everything the arms need from the host, below the arm software itself:
#   1. the peak_usb CAN adapter driver, as a DKMS module (the Jetson kernel ships without it)
#   2. udev names arm_left / arm_right for the adapter's two channels (per-robot; never overwritten)
#   3. openarm-can@.service, which raises each channel at 1M/5M CAN-FD whenever it appears
#   4. the openarm-up and openarm-motors commands
# The arm software runs in its own container; see docs/openarm-setup.md. Not part of
# provision-all.sh, because the arms are an optional payload.
#
# Usage:  sudo /opt/opendubs/openarm/provision-openarm.sh
set -euo pipefail

DIR=/opt/opendubs/openarm
KREL=$(uname -r)                          # e.g. 6.8.12-tegra
KVER=${KREL%%-*}                          # upstream version, e.g. 6.8.12
DKMS_VER="${KVER}-opendubs1"
DKMS_SRC="/usr/src/peak_usb-${DKMS_VER}"
UPSTREAM="https://raw.githubusercontent.com/gregkh/linux/v${KVER}/drivers/net/can/usb/peak_usb"
PEAK_FILES="pcan_usb.c pcan_usb_core.c pcan_usb_core.h pcan_usb_fd.c pcan_usb_pro.c pcan_usb_pro.h"

echo "== OpenArm host provisioning (kernel $KREL) =="

# --- 1. peak_usb driver -----------------------------------------------------
existing=$(modinfo -n peak_usb 2>/dev/null || true)
if [ -n "$existing" ] && [[ "$existing" != */updates/dkms/* ]]; then
  echo "-- peak_usb already provided by the kernel ($existing); skipping DKMS"
elif dkms status "peak_usb/${DKMS_VER}" -k "$KREL" 2>/dev/null | grep -q installed; then
  echo "-- peak_usb/${DKMS_VER} already installed via DKMS"
else
  echo "-- building peak_usb/${DKMS_VER} with DKMS (source: Linux v${KVER}, unmodified)"
  [ -e "/lib/modules/$KREL/build/Makefile" ] || { echo "!! kernel headers missing at /lib/modules/$KREL/build"; exit 1; }
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends dkms curl >/dev/null
  mkdir -p "$DKMS_SRC"
  for f in $PEAK_FILES; do curl -fsSL "$UPSTREAM/$f" -o "$DKMS_SRC/$f"; done
  cat > "$DKMS_SRC/Makefile" <<'EOF'
obj-m += peak_usb.o
peak_usb-y := pcan_usb_core.o pcan_usb.o pcan_usb_pro.o pcan_usb_fd.o
EOF
  cat > "$DKMS_SRC/dkms.conf" <<EOF
# PEAK PCAN-USB family driver (peak_usb), unmodified from Linux v${KVER}
# drivers/net/can/usb/peak_usb/. Installed by opendubs provision-openarm.sh.
PACKAGE_NAME="peak_usb"
PACKAGE_VERSION="${DKMS_VER}"
BUILT_MODULE_NAME[0]="peak_usb"
DEST_MODULE_LOCATION[0]="/updates/dkms"
AUTOINSTALL="yes"
EOF
  dkms status "peak_usb/${DKMS_VER}" | grep -q . || dkms add "peak_usb/${DKMS_VER}"
  dkms build "peak_usb/${DKMS_VER}" -k "$KREL"
  dkms install "peak_usb/${DKMS_VER}" -k "$KREL"
fi

# --- 2 + 3. channel names and link service ---------------------------------
install -m0644 "$DIR/openarm-can@.service" /etc/systemd/system/openarm-can@.service
if [ -e /etc/udev/rules.d/70-openarm-can.rules ]; then
  echo "-- keeping this robot's /etc/udev/rules.d/70-openarm-can.rules (per-robot wiring)"
else
  echo "-- installing default channel names: dev_id 0x0 -> arm_left, 0x1 -> arm_right"
  install -m0644 "$DIR/70-openarm-can.rules" /etc/udev/rules.d/70-openarm-can.rules
fi
systemctl daemon-reload
udevadm control --reload

# --- 4. commands ------------------------------------------------------------
install -D -m0755 "$DIR/openarm-motors" /usr/local/lib/openarm/openarm-motors
ln -sfn /usr/local/lib/openarm/openarm-motors /usr/local/bin/openarm-motors
install -m0755 "$DIR/openarm-up" /usr/local/bin/openarm-up

# --- apply now, if the adapter is plugged in --------------------------------
if ip link show arm_left >/dev/null 2>&1 && ip link show arm_right >/dev/null 2>&1; then
  echo "-- arm_left / arm_right already present; not reloading the driver"
elif pgrep -x ros2_control_node >/dev/null; then
  # Reloading would drop both CAN buses under a running arm stack.
  echo "!! arm stack is running (openarm-up down first); names apply at the next reboot or replug"
elif lsusb -d 0c72:0011 >/dev/null 2>&1; then
  echo "-- adapter present; reloading peak_usb so the names and links apply"
  modprobe -r peak_usb 2>/dev/null || true
  modprobe peak_usb
  udevadm settle
else
  echo "-- adapter (0c72:0011) not plugged in; names and links apply when it is"
fi

echo "== OpenArm host provisioning done =="
for i in arm_left arm_right; do
  if ip link show "$i" >/dev/null 2>&1; then
    echo "  $i: $(ip -details link show "$i" | grep -oE 'state (ERROR-[A-Z]+|BUS-OFF|STOPPED)' | head -1), service $(systemctl is-active "openarm-can@$i")"
  else
    echo "  $i: not present"
  fi
done
