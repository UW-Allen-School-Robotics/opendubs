#!/usr/bin/env bash
# Over-the-air flash the StampPLC from the Thor — NO USB. Builds the firmware, serves the .bin
# over a throwaway HTTP server, tells the StampPLC to pull+self-flash+reboot (via the `OTA`
# command over UDP), then watches it recover. This is the everyday reflash path once the
# ethernet-OTA firmware is on the board; keep a USB port reachable only as a recovery fallback
# (a complete-but-buggy image still needs USB — see the firmware OTA notes).
#
# Usage:  ./ota-from-thor.sh [stamp-ip]      (default stamp IP 192.168.88.253)
#         OTA_PORT=8099 ./ota-from-thor.sh
set -u

STAMP_IP="${1:-192.168.88.253}"
CMD_PORT=5005                       # firmware UDP command/telemetry port
HTTP_PORT="${OTA_PORT:-8099}"
PROJ="$(cd "$(dirname "$0")" && pwd)"
PIO=/opt/opendubs/piovenv/bin/pio
BIN="$PROJ/.pio/build/stamplc/firmware.bin"

# Thor's address on the robot subnet (the URL the StampPLC will fetch from).
THOR_IP="$(ip -4 addr show 2>/dev/null | grep -oE '192\.168\.88\.[0-9]+' | head -1)"
[ -z "$THOR_IP" ] && { echo "ERROR: could not find a 192.168.88.x address on this host."; exit 1; }

echo "== building firmware =="
cd "$PROJ" && "$PIO" run || { echo "ERROR: build failed"; exit 1; }
[ -f "$BIN" ] || { echo "ERROR: $BIN not found after build"; exit 1; }
echo "firmware: $(du -h "$BIN" | cut -f1)  ($BIN)"

echo "== serving it on http://$THOR_IP:$HTTP_PORT =="
pkill -f "http.server $HTTP_PORT" 2>/dev/null; sleep 1
( cd "$(dirname "$BIN")" && exec python3 -m http.server "$HTTP_PORT" ) >/tmp/ota-http.log 2>&1 &
HTTP_PID=$!
trap 'kill "$HTTP_PID" 2>/dev/null' EXIT
sleep 2

echo "== sending OTA command to $STAMP_IP:$CMD_PORT =="
python3 -c "
import socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.sendto(b'OTA http://$THOR_IP:$HTTP_PORT/firmware.bin\n',('$STAMP_IP',$CMD_PORT))
print('sent: OTA http://$THOR_IP:$HTTP_PORT/firmware.bin')
"

echo "== waiting for the board to pull the image =="
pulled=0
for i in $(seq 1 15); do
  if grep -q "GET /firmware.bin" /tmp/ota-http.log 2>/dev/null; then pulled=1; echo "  board fetched the image (t~${i}s)"; break; fi
  sleep 1
done
[ "$pulled" -eq 0 ] && { echo "WARNING: no HTTP GET from the board in 15s — command not received? check $STAMP_IP reachable + firmware has OTA support."; }

echo "== waiting for reboot + recovery =="
# Expect: reachable -> drops (reboot) -> reachable again.
sawdrop=0; back=0
for i in $(seq 1 40); do
  if ping -c1 -W1 "$STAMP_IP" >/dev/null 2>&1; then
    if [ "$sawdrop" -eq 1 ]; then back=1; echo "  board back up after reboot (t~${i}s)"; break; fi
  else
    sawdrop=1
  fi
  sleep 1
done

if [ "$back" -eq 1 ]; then
  echo "== OTA OK — board rebooted into the new firmware and is reachable =="
elif ping -c1 -W1 "$STAMP_IP" >/dev/null 2>&1; then
  echo "== board reachable (no clean reboot dip observed). Verify telemetry freshness in the dashboard. =="
else
  echo "== ERROR: board NOT reachable after OTA. It may need USB recovery (reflash over USB). =="
  exit 1
fi
