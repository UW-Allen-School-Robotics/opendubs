#!/usr/bin/env bash
# Over-the-air flash the StamPLC from the Thor — NO USB. Builds the firmware, serves the .bin
# over a throwaway HTTP server, tells the StamPLC to pull+self-flash+reboot (via the `OTA`
# command over UDP), then watches it recover. This is the everyday reflash path once the
# ethernet-OTA firmware is on the board; keep a USB port reachable only as a recovery fallback
# (a complete-but-buggy image still needs USB — see the firmware OTA notes).
#
# Usage:  ./ota-from-thor.sh                 <- normal: finds the StamPLC itself
#         ./ota-from-thor.sh 10.42.2.20      <- override, if discovery cannot
#         OTA_PORT=8099 ./ota-from-thor.sh
#
# You should never have to know the StamPLC's IP. It is a per-robot DHCP address, and hunting for
# it by hand has twice sent someone to the wrong one. Resolution order below; every step prints
# what it found, so a wrong answer is visible before anything is flashed.
set -u

CMD_PORT=5005                       # firmware UDP command/telemetry port

# --- find the StamPLC ------------------------------------------------------------------------
# 1) explicit argument, 2) $OPENDUBS_STAMP_IP, 3) the dashboard's learned peer — it is already
# receiving telemetry from the board, so it knows the address better than any default, 4) mDNS.
discover_stamp_ip() {
  [ -n "${1:-}" ]                   && { echo "$1";                   return 0; }
  [ -n "${OPENDUBS_STAMP_IP:-}" ]   && { echo "$OPENDUBS_STAMP_IP";   return 0; }

  # The dashboard reports the peer it is actually hearing from, as "<ip>:<port>".
  local peer
  peer="$(curl -s --max-time 3 http://127.0.0.1:8080/api/snapshot 2>/dev/null \
          | tr ',{}' '\n\n\n' | grep -oE '"port": *"[0-9.]+:[0-9]+"' \
          | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
  if [ -n "$peer" ]; then
    # Refuse an address that belongs to THIS host. The dashboard once reported 192.168.55.1 —
    # the Thor's own l4tbr0 — and serving the image at a local address cannot work.
    if ip -4 addr show 2>/dev/null | grep -qE "inet $peer/"; then
      echo "WARN: dashboard reports $peer, which is one of this host's own addresses — ignoring." >&2
    else
      echo "$peer"; return 0
    fi
  fi

  local m
  m="$(avahi-resolve -n opendubs-stamplc.local 2>/dev/null | awk '{print $2}')"
  [ -n "$m" ] && { echo "$m"; return 0; }
  return 1
}

STAMP_IP="$(discover_stamp_ip "${1:-}")" || {
  echo "ERROR: could not find the StamPLC."
  echo "       Tried: argument, \$OPENDUBS_STAMP_IP, the dashboard's learned peer"
  echo "       (http://127.0.0.1:8080/api/snapshot -> status.stamplc.port), and"
  echo "       opendubs-stamplc.local over mDNS."
  echo "       Is opendubs-dash running and hearing the board? Pass the address to override."
  exit 1
}
echo "== StamPLC at $STAMP_IP =="
HTTP_PORT="${OTA_PORT:-8099}"
PROJ="$(cd "$(dirname "$0")" && pwd)"
PIO=/opt/opendubs/piovenv/bin/pio
BIN="$PROJ/.pio/build/stamplc/firmware.bin"

# Thor's address ON THE PATH TO THIS StamPLC — that is the URL it will fetch from.
# Asking the routing table means this works on whatever subnet the robot happens to use and
# picks the right interface on a dual-homed host. It used to grep the host's addresses for
# 192.168.88.x, which is one robot's LAN: on a robot numbered anything else the script aborted
# before it built anything, and the everyday reflash path had to be done by hand.
THOR_IP="$(ip -4 route get "$STAMP_IP" 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}' | head -1)"
[ -z "$THOR_IP" ] && {
  echo "ERROR: no route from this host to the StamPLC at $STAMP_IP."
  echo "       Check the address (arg 1) and that this host is on the robot LAN."
  exit 1
}

echo "== building firmware =="
cd "$PROJ" && "$PIO" run || { echo "ERROR: build failed"; exit 1; }
[ -f "$BIN" ] || { echo "ERROR: $BIN not found after build"; exit 1; }
echo "firmware: $(du -h "$BIN" | cut -f1)  ($BIN)"

echo "== serving it on http://$THOR_IP:$HTTP_PORT =="
# Bracketed so the pattern cannot match the command running it (it does, over ssh).
pkill -f "http[.]server $HTTP_PORT" 2>/dev/null; sleep 1
# Bound to the interface the StamPLC will reach us on, rather than every interface.
( cd "$(dirname "$BIN")" && exec python3 -m http.server "$HTTP_PORT" --bind "$THOR_IP" ) >/tmp/ota-http.log 2>&1 &
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
