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

# The keys the flashed board will trust are the repository's (lib/ota_verify/keys/). USB is the
# recovery path, so none does not stop it: the board then refuses every OTA until reflashed.
TRUSTED="$(ls "$PROJ/lib/ota_verify/keys/" 2>/dev/null | sed -n 's/\.der$//p' | tr '\n' ' ')"
if [ -n "$TRUSTED" ]; then
  echo "== the flashed board will trust firmware-signing key(s): $TRUSTED=="
else
  echo "WARNING: this build trusts no firmware-signing key: the flashed board will refuse every OTA"
  echo "         until it is reflashed with one (software/stack/firmware-signing/README.md)."
fi

echo "== flashing StampS3 via PlatformIO =="
cd "$PROJ"
"$PIO" run -t upload
