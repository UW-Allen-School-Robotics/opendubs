#!/usr/bin/env bash
# Scripted OTA rollback test — workplan Phase 5 item 3.
#
# Rollback has fired for real several times during development, which is reassuring but is not a
# test: nobody chose the moment and nothing recorded the outcome. This does, repeatably.
#
# IT IS A SAFE TEST, and that is by design. The image it flashes is an ORDINARY build that simply
# never calls esp_ota_mark_app_valid_cancel_rollback() (OPENDUBS_NO_MARK_VALID=1). So the worst case
# if rollback is broken is a head running a perfectly good image that has not self-certified -- not
# a brick. A deliberately crashing image would test the same mechanism and risk a USB recovery
# session on a head that lives inside the robot.
#
# What it asserts:
#   1. the unmarked image boots and REPORTS state=PENDING_VERIFY
#   2. it STAYS PENDING_VERIFY past the healthy window, rather than marking itself valid
#   3. after a reboot the head is back on the PREVIOUS slot and the PREVIOUS image hash
#
# Usage:  ./test-rollback.sh <head-ip> <this-host-ip> [http-port]
set -euo pipefail

HEAD="${1:?usage: test-rollback.sh <head-ip> <host-ip> [port]}"
HOST="${2:?usage: test-rollback.sh <head-ip> <host-ip> [port]}"
PORT="${3:-8099}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DEMO="${OPENDUBS_DEMO:?set OPENDUBS_DEMO to the demo tree}"
BUILD="$DEMO/platforms/tab5/build"

ping_head() {   # one PING, printed raw
    python3 - "$HEAD" <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(6)
for _ in range(10):
    s.sendto(b"PING", (sys.argv[1], 4242))
    try:
        print(s.recvfrom(1024)[0].decode(errors="replace")); break
    except socket.timeout: pass
else:
    print("NO REPLY")
PY
}
field() { sed -n "s/.*$1=\([^ |]*\).*/\1/p" <<<"$2"; }

echo "== 1. baseline =="
BEFORE="$(ping_head)"
echo "$BEFORE"
SLOT0="$(field slot "$BEFORE")"; SHA0="$(field sha "$BEFORE")"
[ -n "$SLOT0" ] && [ -n "$SHA0" ] || { echo "FAIL: no baseline from the head"; exit 1; }

echo "== 2. build the unmarked image =="
( cd "$HERE" && OPENDUBS_NO_MARK_VALID=1 OPENDUBS_PANTILT=1 OPENDUBS_FACEDET=1 ./build.sh image >/dev/null )

echo "== 3. serve it and trigger the OTA =="
( cd "$BUILD" && python3 -m http.server "$PORT" --bind "$HOST" >/dev/null 2>&1 & echo $! > /tmp/.rbsrv )
sleep 1
python3 - "$HEAD" "$HOST" "$PORT" <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(4)
s.sendto(f"OTA http://{sys.argv[2]}:{sys.argv[3]}/m5stack_tab5.bin".encode(), (sys.argv[1], 4242))
print(" ", s.recvfrom(256)[0].decode())
PY
sleep 95
kill "$(cat /tmp/.rbsrv)" 2>/dev/null || true

echo "== 4. the unmarked image must boot PENDING_VERIFY and stay there =="
MID="$(ping_head)"; echo "$MID"
SHA1="$(field sha "$MID")"; ST1="$(field state "$MID")"
[ "$SHA1" != "$SHA0" ] || { echo "FAIL: the test image never installed"; exit 1; }
[ "$ST1" = "PENDING_VERIFY" ] || { echo "FAIL: expected PENDING_VERIFY, got $ST1"; exit 1; }
sleep 20
LATE="$(ping_head)"
[ "$(field state "$LATE")" = "PENDING_VERIFY" ] \
    || { echo "FAIL: it marked itself valid; OPENDUBS_NO_MARK_VALID is not taking effect"; exit 1; }
echo "  still PENDING_VERIFY after the healthy window — correct"

echo "== 5. reboot; the bootloader must revert =="
python3 - "$HEAD" <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(4)
s.sendto(b"REBOOT", (sys.argv[1], 4242))
try: print(" ", s.recvfrom(256)[0].decode())
except socket.timeout: print("  (no ack; it may already be down)")
PY
sleep 30
AFTER="$(ping_head)"; echo "$AFTER"

if [ "$(field sha "$AFTER")" = "$SHA0" ] && [ "$(field slot "$AFTER")" = "$SLOT0" ]; then
    echo "PASS: rolled back to slot $SLOT0 / sha $SHA0"
else
    echo "FAIL: expected slot $SLOT0 sha $SHA0, got slot $(field slot "$AFTER") sha $(field sha "$AFTER")"
    exit 1
fi
