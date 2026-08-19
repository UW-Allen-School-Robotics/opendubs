#!/usr/bin/env bash
# OpenDubs firmware / PlatformIO provisioning — run ON the Thor (needs sudo).
# Idempotent. Sets up PlatformIO so the StampLC ESP32-S3 firmware can be flashed
# from the Thor without moving hardware (see rc_serial_probe/flash-from-thor.sh),
# plus the udev rules and flashing-user group membership.
#
# Usage:  sudo /opt/opendubs/rc_serial_probe/provision-firmware.sh
set -euo pipefail

ROOT=/opt/opendubs
PIOVENV=$ROOT/piovenv
# The user who runs flashing (owns the deploy; needs serial access).
FLASH_USER="${SUDO_USER:-emmar}"

echo "== OpenDubs firmware/PlatformIO provisioning =="

# --- PlatformIO venv --------------------------------------------------------
if [ ! -x "$PIOVENV/bin/pio" ]; then
  echo "-- creating PlatformIO venv at $PIOVENV"
  apt-get install -y -qq python3-venv curl >/dev/null || true
  python3 -m venv "$PIOVENV"
  "$PIOVENV/bin/pip" install --upgrade pip >/dev/null
  "$PIOVENV/bin/pip" install platformio
fi

# --- udev rules (canonical PlatformIO file) ---------------------------------
UDEV=/etc/udev/rules.d/99-platformio-udev.rules
if [ ! -f "$UDEV" ]; then
  echo "-- installing PlatformIO udev rules"
  curl -fsSL https://raw.githubusercontent.com/platformio/platformio-core/develop/platformio/assets/system/99-platformio-udev.rules -o "$UDEV"
  udevadm control --reload-rules && udevadm trigger || true
fi

# --- flashing-user serial access -------------------------------------------
echo "-- adding $FLASH_USER to dialout,plugdev"
usermod -aG dialout,plugdev "$FLASH_USER" || true

echo "== firmware provisioning done. pio: $("$PIOVENV/bin/pio" --version 2>/dev/null || echo '?') =="
echo "   (group changes take effect on next login for $FLASH_USER)"
