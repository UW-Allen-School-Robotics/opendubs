#!/usr/bin/env bash
# Flash the StampS3 from the Thor via PlatformIO — canonical `pio run -t upload`,
# no need to move the hardware to the Mac.
#
# The dashboard backend (opendubs-dash) holds the stamp's serial port open, so we
# stop it to free the port, flash, then restart it (restart happens even if the
# flash fails). PlatformIO auto-detects the Espressif (303a) bootloader port, so
# it won't grab another connected board.
#
# Usage:  /opt/opendubs/rc_serial_probe/flash-from-thor.sh
set -u

PROJ="$(cd "$(dirname "$0")" && pwd)"
PIO=/opt/opendubs/piovenv/bin/pio

if ! ls /dev/serial/by-id/*Espressif*if00 >/dev/null 2>&1; then
  echo "ERROR: StampS3 (Espressif 303a) not found on USB. Is it plugged into the Thor?"
  exit 1
fi

echo "== stopping opendubs-dash to free the serial port =="
sudo systemctl stop opendubs-dash
trap 'echo "== restarting opendubs-dash =="; sudo systemctl start opendubs-dash' EXIT
sleep 1

# Compile the firmware-signing public key in, so the recovered board accepts signed OTA updates
# afterwards (notes/design/firmware-signing.md). USB is the recovery path, so a missing key does
# not stop it: the image is built without one and refuses every OTA until it is reflashed with one.
KS=/etc/opendubs/firmware-signing
mkdir -p "$PROJ/include"
if sudo test -r "$KS/ota.pub.der"; then
  sudo cat "$KS/ota.pub.der" | python3 "$PROJ/tools/pubkey_header.py" /dev/stdin > "$PROJ/include/ota_pubkey.h"
  echo "== this build trusts firmware-signing key $(sudo sha256sum "$KS/ota.pub.der" | cut -c1-16) =="
else
  rm -f "$PROJ/include/ota_pubkey.h"
  echo "WARNING: no firmware-signing key on this Thor: the flashed board will refuse every OTA until"
  echo "         it is reflashed with one (software/stack/firmware-signing/README.md)."
fi

echo "== flashing StampS3 via PlatformIO =="
cd "$PROJ"
"$PIO" run -t upload
