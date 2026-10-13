#!/usr/bin/env bash
# Put the neck's Wi-Fi credentials into its NVS, once, over USB.
#
#   ./tools/provision.sh            # prompts; run it ON the machine the Dial is plugged into
#
# WHY A SCRIPT. The credentials are PER-ROBOT and never belong in the repo, in a commit, in a chat
# or in a shell history -- the rule that kept the Tab5's PSK out of this tree. So the password is
# typed here with the echo off, written straight to the port, and never stored anywhere.
#
# NOT `screen`, NOT pyserial's defaults. CLAUDE.md is explicit: anything that asserts DTR/RTS
# resets an ESP32, and on this robot that has reset the safety controller. This writes to the port
# with stty configured not to hang up, which does not.
set -euo pipefail

PORT="${1:-}"
if [ -z "$PORT" ]; then
    PORT=$(ls /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_* 2>/dev/null | head -1 || true)
fi
[ -n "$PORT" ] && [ -e "$PORT" ] || { echo "No Dial found. Pass the port as an argument." >&2; exit 1; }
echo "neck on: $PORT"

read -r -p "SSID: " SSID
read -r -s -p "Password (not shown, not stored): " PASS; echo
[ -n "$SSID" ] || { echo "no SSID given" >&2; exit 1; }

# -hupcl: do not drop DTR when we close, so the board is not reset out from under us.
stty -F "$PORT" 115200 raw -echo -hupcl

printf 'WIFI %s %s\n' "$SSID" "$PASS" > "$PORT"
unset PASS
echo "sent. listening for its verdict..."

timeout 8 cat "$PORT" | head -20 || true
echo
echo "If it said 'saved to NVS', power-cycle it and it will join. Nothing was written to disk here."
