#!/usr/bin/env bash
# Flash the robot's head (the Tab5, ESP-IDF firmware) over the air — THE procedure. No USB.
#
#   On a machine with ESP-IDF and the demo tree (firmware/tab5_idf/README.md):
#       ./ota-from-thor.sh --build              # builds THE SHIPPING IMAGE, prints its path and hash
#   Copy that .bin to the Thor, then ON THE THOR:
#       ./ota-from-thor.sh <image.bin> [head]   # head: default opendubs-tab5.local
#
# The head PULLS its firmware: this serves the .bin over a throwaway HTTP server and sends
# `OTA <url>` on UDP 4242; the head downloads it into its other slot, reboots into it, and marks it
# valid once it has run healthily — otherwise the bootloader rolls back to the previous image on the
# next boot (proven on the robot by test-rollback.sh). This script does not trust "it downloaded":
# it identifies the image by the hash of its ELF, read from the .bin, and waits until the head's
# PING reports THAT hash, running, VALID.
#
# Run it on the Thor: a laptop on the same subnet may not reach the head. It replaces the Arduino
# firmware's firmware/tab5_face/ota-from-thor.sh, which is retired (the head runs ESP-IDF).
# If this is broken, FIX IT — do not go back to improvising.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
CMD_PORT=4242
HTTP_PORT="${OTA_PORT:-8000}"
# THE SHIPPING CONFIGURATION, in one place. Pan/tilt and the face detector are what the robot's head
# does; every diagnostic instrument is off, and the image marks itself valid. The same set the
# rollback test builds its (otherwise unmarked) image from.
SHIP_FLAGS="OPENDUBS_PANTILT=1 OPENDUBS_FACEDET=1 OPENDUBS_DBG_UI=0 OPENDUBS_DBG_PERCEPT=0 \
OPENDUBS_DBG_SERIAL=0 OPENDUBS_WALKIN=0 OPENDUBS_NO_MARK_VALID=0"
# Strings that only diagnostic builds contain (build.sh `release` checks the same list).
MARKERS=("ROLLBACK TEST" "selftest" "walkin" "gate:noimu")
SLOT_BYTES=$((0x500000))            # partitions.csv: ota_0 / ota_1

image_sha() {   # the first 8 bytes of the app's ELF SHA-256, as the head's PING reports it
    python3 - "$1" <<'PY'
import struct, sys
b = open(sys.argv[1], "rb").read(0x100)
if len(b) < 0xB8 or b[0] != 0xE9 or struct.unpack_from("<I", b, 0x20)[0] != 0xABCD5432:
    sys.exit("not an ESP-IDF app image")
print(b[0xB0:0xB8].hex())
PY
}

check_image() {
    local bin="$1" sha
    [ -f "$bin" ] || { echo "ERROR: no such image: $bin" >&2; exit 1; }
    sha="$(image_sha "$bin")" || { echo "ERROR: $bin is not an ESP-IDF app image." >&2; exit 1; }
    [ "$(wc -c < "$bin")" -le "$SLOT_BYTES" ] || { echo "ERROR: $bin is larger than an OTA slot." >&2; exit 1; }
    for m in "${MARKERS[@]}"; do
        if strings "$bin" 2>/dev/null | grep -qF "$m"; then
            echo "ERROR: '$m' is in $bin — a diagnostic or test build, not the shipping image." >&2
            echo "       Build the shipping image with:  $0 --build" >&2
            exit 1
        fi
    done
    echo "$sha"
}

# ---- --build: the shipping image, anywhere ESP-IDF and the demo tree are ----------------------
if [ "${1:-}" = "--build" ]; then
    : "${OPENDUBS_DEMO:?set OPENDUBS_DEMO to the demo tree (firmware/tab5_idf/README.md)}"
    echo "==> building the shipping image ($SHIP_FLAGS)"
    ( cd "$HERE" && env $SHIP_FLAGS ./build.sh image ) || { echo "BUILD FAILED." >&2; exit 1; }
    BIN="$OPENDUBS_DEMO/platforms/tab5/build/m5stack_tab5.bin"
    SHA="$(check_image "$BIN")" || exit 1
    echo
    echo "shipping image: $BIN"
    echo "          sha:  $SHA"
    echo "Copy it to the Thor and run there:  firmware/tab5_idf/ota-from-thor.sh <that .bin>"
    exit 0
fi

# ---- flash, on the Thor ----------------------------------------------------------------------
BIN="${1:?usage: $0 --build   |   $0 <image.bin> [head]}"
HEAD="${2:-opendubs-tab5.local}"
SHA="$(check_image "$BIN")" || exit 1
echo "==> image $BIN, sha $SHA"

HEAD_IP="$(getent hosts "$HEAD" 2>/dev/null | awk '{print $1}' | head -1)"
[ -z "$HEAD_IP" ] && HEAD_IP="$HEAD"

# THE HEAD IS POWERED FROM THE E-STOP PROTECTED DRIVE BRANCH. With the contactor open it has no
# power: it is off, not offline. Check that before anything else looks like a network fault.
CONTACTOR="$(curl -s --max-time 3 http://127.0.0.1:8080/api/stamplc 2>/dev/null \
             | python3 -c 'import sys,json; print(json.load(sys.stdin)["io"]["relay"][0])' 2>/dev/null)"
if [ "$CONTACTOR" = "False" ]; then
    echo "ERROR: the e-stop contactor is OPEN, so the head has no power."
    echo "       Close it first: set CH5 to its low position, then"
    echo "           curl -X POST http://127.0.0.1:8080/api/estop/clear"
    echo "       (A StamPLC reflash opens it. Flash the StamPLC first, clear, THEN the head.)"
    exit 1
fi

ping_head() {   # one PING reply, or NO REPLY
    python3 - "$HEAD_IP" "$CMD_PORT" <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
for _ in range(5):
    s.sendto(b"PING", (sys.argv[1], int(sys.argv[2])))
    try:
        print(s.recvfrom(1024)[0].decode(errors="replace")); break
    except socket.timeout:
        pass
else:
    print("NO REPLY")
PY
}
field() { sed -n "s/.*$1=\([^ |]*\).*/\1/p" <<<"$2"; }

BEFORE="$(ping_head)"
if [ "$BEFORE" = "NO REPLY" ]; then
    echo "ERROR: no reply from the head at $HEAD_IP:$CMD_PORT."
    echo "       FIRST: is the e-stop contactor closed? The head has no power when it is open, and"
    echo "       that looks exactly like a network fault. Then: does this host resolve .local names"
    echo "       (avahi-daemon + libnss-mdns)? Run this on the Thor, not a laptop."
    exit 1
fi
SLOT0="$(field slot "$BEFORE")"; SHA0="$(field sha "$BEFORE")"
echo "==> the head runs slot $SLOT0, sha $SHA0, $(field state "$BEFORE")"
if [ "$SHA0" = "$SHA" ] && [ "${FORCE:-0}" != "1" ]; then
    echo "The head already runs this image. Nothing to do (FORCE=1 to flash it again)."
    exit 0
fi

SRC_IP="$(ip -4 route get "$HEAD_IP" 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}' | head -1)"
[ -z "$SRC_IP" ] && { echo "ERROR: no route from this host to $HEAD_IP." >&2; exit 1; }

# A free port, and a server proven to be serving OUR file: a stale server on the port makes the head
# fetch something else, which reads as "the head never fetched it" and blames a blameless head.
while ss -tln 2>/dev/null | grep -q ":$HTTP_PORT "; do
    HTTP_PORT=$((HTTP_PORT + 1))
    [ "$HTTP_PORT" -gt 8020 ] && { echo "ERROR: no free port in 8000-8020." >&2; exit 1; }
done
SERVE="$(mktemp -d)"; cp "$BIN" "$SERVE/m5stack_tab5.bin"
( cd "$SERVE" && exec python3 -m http.server "$HTTP_PORT" --bind "$SRC_IP" >"$SERVE/http.log" 2>&1 ) &
HTTP_PID=$!
trap 'kill $HTTP_PID 2>/dev/null; wait $HTTP_PID 2>/dev/null; rm -rf "$SERVE"' EXIT
sleep 1
URL="http://$SRC_IP:$HTTP_PORT/m5stack_tab5.bin"
if ! kill -0 "$HTTP_PID" 2>/dev/null || ! curl -sf --max-time 3 "$URL" -o "$SERVE/check.bin" \
   || ! cmp -s "$SERVE/check.bin" "$BIN"; then
    echo "ERROR: cannot fetch our own URL ($URL) — the head will not manage it either." >&2
    sed 's/^/       /' "$SERVE/http.log" >&2
    exit 1
fi
: > "$SERVE/http.log"

echo "==> telling $HEAD_IP:$CMD_PORT to pull $URL"
python3 - "$HEAD_IP" "$CMD_PORT" "$URL" <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(4)
s.sendto(f"OTA {sys.argv[3]}".encode(), (sys.argv[1], int(sys.argv[2])))
try: print("    head:", s.recvfrom(256)[0].decode(errors="replace"))
except socket.timeout: print("    (no acknowledgement; watching for the download anyway)")
PY

for i in $(seq 1 20); do
    grep -q "GET /m5stack_tab5.bin" "$SERVE/http.log" 2>/dev/null && { echo "    fetching"; break; }
    sleep 1
    [ "$i" = 20 ] && { echo "ERROR: the head never fetched the image. Check its screen." >&2; exit 1; }
done

# A download takes about a minute and the head answers PING throughout, but the slot only changes
# after the reboot — so wait, then poll until the new hash is RUNNING and has marked itself VALID.
echo "==> waiting for the download, the reboot and the health check (about 75-120 s)"
sleep "${OTA_SETTLE_S:-75}"      # PING before ~75 s only reports the old image mid-download
for i in $(seq 1 30); do
    NOW="$(ping_head)"
    if [ "$(field sha "$NOW")" = "$SHA" ] && [ "$(field state "$NOW")" = "VALID" ]; then
        echo "PASS: the head runs sha $SHA in slot $(field slot "$NOW"), VALID."
        exit 0
    fi
    sleep 5
done
echo "FAIL: after ~4 minutes the head reports: $NOW" >&2
if [ "$(field sha "$NOW")" = "$SHA0" ]; then
    echo "      It is still (or again) on the previous image, $SHA0: the new one did not install," >&2
    echo "      or it failed its health check and the bootloader rolled back. Check its screen." >&2
elif [ "$(field sha "$NOW")" = "$SHA" ]; then
    echo "      The new image runs but has not marked itself VALID; it will roll back on the next" >&2
    echo "      reboot. Check its screen." >&2
fi
exit 1
