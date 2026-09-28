#!/usr/bin/env bash
# Over-the-air flash the Tab5 face from the Thor — NO USB. Builds the firmware, serves the .bin
# over a throwaway HTTP server, tells the head to pull+self-flash+reboot (the `OTA` command over
# UDP), then watches it come back.
#
# Usage:  ./ota-from-thor.sh [head-host-or-ip]     (default: opendubs-tab5.local)
#         OTA_PORT=8000 ./ota-from-thor.sh
#
# WHY THIS EXISTS. The head's README documents `pio run -e tab5_ota -t upload` (espota). That has
# NEVER worked on this board: ArduinoOTA's listener sits behind the ESP-Hosted co-processor and
# begin() returns void, so every failure is silent and espota just reports "no response". The
# StampPLC solved the same problem by PULLING its firmware over HTTP on command; the head does the
# same. See the comment above otaHttpPull() in src/main.cpp.
#
# Without this script the procedure gets re-derived from source every time, which has cost real
# hours. If it is broken, FIX IT — do not go back to improvising.
set -u

HEAD="${1:-opendubs-tab5.local}"
CMD_PORT=4242                       # firmware UDP command port — see UDP_PORT in src/main.cpp
HTTP_PORT="${OTA_PORT:-8000}"
PROJ="$(cd "$(dirname "$0")" && pwd)"
BIN="$PROJ/.pio/build/tab5/firmware.bin"

# PlatformIO needs Python >= 3.10 here: pioarduino is the only platform that builds the ESP32-P4,
# and it refuses 3.9. intelhex is needed by esptool's image packer and is not pulled in for you.
PIO="${PIO:-}"
if [ -z "$PIO" ]; then
  if [ -x /opt/opendubs/piovenv/bin/pio ]; then PIO=/opt/opendubs/piovenv/bin/pio
  elif command -v uv >/dev/null 2>&1;      then PIO="uv run --python 3.12 --with platformio --with intelhex pio"
  else echo "ERROR: no pio. Install PlatformIO on Python >= 3.10, or install uv." >&2; exit 1; fi
fi

# Resolve the head. mDNS needs avahi-daemon + libnss-mdns on this host; without them .local
# resolves nowhere and the dashboard cannot find the head either (that is a real failure we hit).
HEAD_IP="$(getent hosts "$HEAD" 2>/dev/null | awk '{print $1}' | head -1)"
[ -z "$HEAD_IP" ] && HEAD_IP="$HEAD"        # already an IP, or resolution is broken
# THE HEAD IS POWERED FROM THE E-STOP PROTECTED DRIVE BRANCH. With the contactor open it has
# no power at all -- it does not "drop off the network", it is off. So a StampPLC reflash, an
# e-stop, or anything that opens the contactor takes the head down with it, and the head cannot
# be flashed until the contactor is closed again. Check that FIRST: every other diagnosis of a
# silent head (mDNS, Wi-Fi association, a stale DHCP lease) is a wild goose chase if the reason
# is simply that the drive branch is dead.
CONTACTOR="$(curl -s --max-time 3 http://127.0.0.1:8080/api/stamplc 2>/dev/null \
             | python3 -c 'import sys,json; print(json.load(sys.stdin)["io"]["relay"][0])' 2>/dev/null)"
if [ "$CONTACTOR" = "False" ]; then
  echo "ERROR: the e-stop contactor is OPEN, so the head has no power."
  echo "       The Tab5 sits on the e-stop protected drive branch. Close the contactor first:"
  echo "           set CH5 to OFF, then:  curl -X POST http://127.0.0.1:8080/api/estop/clear"
  echo "       (A StampPLC reflash opens it. Flash the StampPLC first, clear, THEN the head.)"
  exit 1
fi

ping -c1 -W2 "$HEAD_IP" >/dev/null 2>&1 || {
  echo "ERROR: no reply from the head at $HEAD_IP."
  echo "       FIRST: is the e-stop contactor closed? The head is on the drive branch and has"
  echo "       no power when it is open. That is the usual answer and it looks like a network"
  echo "       fault. (This check runs above, but only when the dashboard API is reachable.)"
  echo "       Then: this host may be missing mDNS:"
  echo "           sudo apt install -y avahi-daemon libnss-mdns avahi-utils"
  echo "       Note this script must run on a host that can reach the head -- the Thor can;"
  echo "       a laptop on the same subnet may not."
  exit 1
}

# Our address ON THE PATH TO THE HEAD — that is the URL it will fetch from. Asking the routing
# table means this works on whatever subnet the robot happens to use, and picks the right
# interface on a dual-homed host.
SRC_IP="$(ip -4 route get "$HEAD_IP" 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}' | head -1)"
[ -z "$SRC_IP" ] && { echo "ERROR: no route from this host to $HEAD_IP." >&2; exit 1; }

echo "==> building"
( cd "$PROJ" && $PIO run ) || { echo "BUILD FAILED — nothing sent." >&2; exit 1; }
[ -f "$BIN" ] || { echo "ERROR: $BIN missing after a successful build." >&2; exit 1; }

SERVE="$(mktemp -d)"; cp "$BIN" "$SERVE/firmware.bin"
( cd "$SERVE" && python3 -m http.server "$HTTP_PORT" >"$SERVE/http.log" 2>&1 ) &
HTTP_PID=$!
trap 'kill $HTTP_PID 2>/dev/null; rm -rf "$SERVE"' EXIT
sleep 1

URL="http://$SRC_IP:$HTTP_PORT/firmware.bin"
echo "==> telling $HEAD_IP:$CMD_PORT to pull $URL"
printf 'OTA %s' "$URL" | nc -u -w1 "$HEAD_IP" "$CMD_PORT"

# The head shows its own progress (OTA: connecting / writing / NN% / ok, rebooting). Here we just
# confirm it fetched the file and came back, because a silent failure looks identical to success.
echo "==> waiting for the fetch"
for i in $(seq 1 20); do
  grep -q "GET /firmware.bin" "$SERVE/http.log" 2>/dev/null && { echo "    fetched"; break; }
  sleep 1
  [ "$i" = 20 ] && { echo "ERROR: the head never fetched it. Check its screen." >&2; exit 1; }
done

echo "==> waiting for it to come back"
sleep 10
for i in $(seq 1 24); do
  ping -c1 -W1 "$HEAD_IP" >/dev/null 2>&1 && { echo "    back after $((10 + i))s"; exit 0; }
  sleep 1
done
echo "WARNING: not answering yet. Look at its screen; if it is stuck, power-cycle it." >&2
exit 1
